"""OptionsScreeningService — rule-based options screening from a watchlist.

Architectural standard v2 §5: this service holds the analytics extracted from
``fastMCPTest/options_analysis.py`` (the CLI/MCP hybrid). It scores each
security using Bollinger Band position, options Put/Call ratios, IV rank, and
news sentiment, then builds ranked long/put candidates and concrete call/put
trade specs.

No LLM required — all scoring logic is rule-based.

Two data sources (``source=``):
  - ``"cache"`` (the watchlist screen's default) reads only the database: the
    daily Job's 17:00 ET full-chain capture, cached daily bars, the cached
    earnings calendar and the news Job's sentiment — a handful of set-based
    queries for the whole watchlist and **no Yahoo call at all**. A symbol with
    no capture in the last ``SCREEN_MAX_STALENESS_TRADING_DAYS`` trading days
    is listed under ``stale`` rather than fetched live: the per-symbol live
    path is what outran the MCP wrapper's 60 s timeout.
  - ``"live"`` (``analyze_symbol``'s default) fetches from Yahoo per symbol.

Collaborators are injected (constructor injection; wiring lives only in
``registry.py``):
  - ``yfinance_gateway``        — live quotes, option chains, calendar, news
  - ``ohlcv_repository``        — cached daily history for Bollinger/HV computations
  - ``prices``                  — the live path's history seam (refreshes the cache)
  - ``options_repository``      — the cached full-chain captures
  - ``fundamentals_repository`` — the cached earnings calendar
  - ``news_store``              — FinBERT-scored news sentiment

The ``fastMCPTest/options_analysis.py`` module is now a thin adapter: the
FastMCP server (5 tools), the CLI ``main()``, and the ``print_*`` presentation
helpers all delegate the analytics here.
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from quantcore.analytics.market_time import market_date, period_to_days, trading_days_after
import yaml

# Project root (…/StockPortfolioManager) — for resolving the default watchlist.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BB_PERIOD = 20
BB_STD_DEV = 2
HISTORY_PERIOD = "3mo"

# Symbols fetched at once by the live path (#331). Kept modest for Yahoo's
# rate limits; refresh_options_snapshots runs 4 for the same reason. The
# watchlist screen no longer uses it — even at 8 workers a live screen of the
# whole watchlist took ~190 s, so it reads the cache instead (source="cache").
SCREEN_MAX_WORKERS = 8

# A cached capture older than this many trading days is reported as stale and
# not scored. 1 accepts the previous 17:00 ET capture all through the next
# session (and over a weekend or holiday), and no older.
SCREEN_MAX_STALENESS_TRADING_DAYS = 1

# History read for the cached screen: enough for the 252-day HV range; the
# Bollinger Bands use only the last BB_PERIOD bars of it.
CACHED_HISTORY_DAYS = 365

SOURCES = ("cache", "live")

# yfinance logs a failed calendar fetch (e.g. a 401 "Invalid Crumb" while
# concurrent workers refresh the shared crumb) and returns an empty calendar,
# which would silently disarm the earnings blackout. An empty answer is
# retried once after this pause (#331).
CALENDAR_RETRY_PAUSE_SECONDS = 1.0

# Scoring thresholds
PC_VERY_BULLISH = 0.5
PC_BULLISH = 0.8
PC_NEUTRAL_HIGH = 1.2
PC_BEARISH = 1.5
PC_VERY_BEARISH = 2.0

BB_OVERSOLD_THRESHOLD = 0.0   # price <= lower band
BB_OVERBOUGHT_THRESHOLD = 1.0  # price >= upper band

# Minimum OI on the put side to trust the P/C signal
MIN_PUT_OI_FOR_SIGNAL = 500

# Put/Call analysis thresholds
PC_ATM_STRIKES       = 5     # strikes each side of ATM for ATM P/C calculation
PC_UNWIND_THRESHOLD  = 0.75  # vol_pc / oi_pc ≤ this → puts being sold (unwinding)
PC_FRESH_BUY_THRESH  = 1.50  # vol_pc / oi_pc ≥ this → fresh put buying today
PC_TERM_SKEW_MIN     = 0.30  # near_pc - mid_pc ≥ this → near-term fear elevated

# IV Rank / Percentile thresholds
# High IV signals fear/capitulation → long bounce signal
# Low IV signals complacency → cheap puts, bearish signal
IV_RANK_EXTREME_FEAR  = 80   # +3 long score
IV_RANK_HIGH_FEAR     = 60   # +2 long score
IV_RANK_ELEVATED      = 40   # +1 long score
IV_RANK_COMPLACENT    = 20   # +2 put score (cheap puts)
IV_RANK_VERY_CHEAP    = 10   # +1 additional put score

# Portfolio ranking: blended conviction + ROI score
# ROI is capped before normalising so extreme outliers (e.g. 776%) don't
# swamp the conviction signal from the put score.
ROI_CAP_FOR_RANKING = 200.0   # cap ROI% at this value before normalising
ROI_WEIGHT = 0.40             # weight given to (capped, normalised) ROI
CONVICTION_WEIGHT = 0.60      # weight given to normalised put_score
MAX_PUT_SCORE = 15            # theoretical max from scoring rules (IV rank + P/C signals)

# ---------------------------------------------------------------------------
# Guardrail configuration
# ---------------------------------------------------------------------------

# 1. Earnings proximity: skip put trades when earnings are this close
EARNINGS_BLACKOUT_DAYS = 14

# 2. Catalyst cooldown: scan recent news this many days back for positive catalysts
CATALYST_LOOKBACK_DAYS = 5

POSITIVE_CATALYST_KEYWORDS = {
    "upgrade", "upgraded", "outperform", "overweight", "buy rating",
    "price target raised", "target raised", "raised target", "raised price target",
    "strong buy", "partnership", "deal", "contract awarded",
    "beats", "beat expectations", "revenue growth",
    "guidance raised", "raised guidance", "raised outlook",
}

# 3. Contradiction guard: suppress put trade when both scores are this high
CONTRADICTION_LONG_MIN = 3
CONTRADICTION_PUT_MIN  = 3

# Non-US listing suffixes skipped by default in watchlist scans
SKIP_SUFFIXES = (".PA", ".OL", ".AS", ".SG", ".KS", ".ST", ".DE")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BollingerBands:
    upper: float
    middle: float
    lower: float

    def position(self, price: float) -> float:
        """
        Returns a normalised 0–1 value:
          0.0 = at lower band (oversold)
          0.5 = at middle (20-day SMA)
          1.0 = at upper band (overbought)
        Values outside 0–1 mean the price has broken out of the bands.
        """
        band_width = self.upper - self.lower
        if band_width == 0:
            return 0.5
        return (price - self.lower) / band_width

    def pct_from_lower(self, price: float) -> float:
        return (price - self.lower) / self.lower * 100

    def pct_from_upper(self, price: float) -> float:
        return (price - self.upper) / self.upper * 100


@dataclass
class PutCallAnalysis:
    """
    Rich Put/Call analysis across multiple expirations and signal types.

    near_oi_pc    — OI-based P/C for nearest expiry (accumulated positioning)
    near_vol_pc   — Volume-based P/C for nearest expiry (today's trading sentiment)
    near_atm_pc   — ATM-only OI P/C (±PC_ATM_STRIKES strikes around current price)
                    Most directional signal — targeted hedging right at current price
    mid_oi_pc     — OI-based P/C for next expiry (~30–60 days out); None if unavailable
    term_skew     — near_oi_pc − mid_oi_pc (positive = near-term fear > longer-term)
    vol_oi_ratio  — near_vol_pc / near_oi_pc
                    < PC_UNWIND_THRESHOLD  → puts being sold today (fear unwinding → bounce)
                    > PC_FRESH_BUY_THRESH  → fresh put buying today (new bearish positioning)
    put_unwinding     — True when vol/OI ratio signals active put selling
    fresh_put_buying  — True when vol/OI ratio signals aggressive new put buying
    near_term_fear    — True when near expiry P/C is meaningfully > mid expiry P/C
    """
    near_expiry:      str
    near_oi_pc:       Optional[float]
    near_vol_pc:      Optional[float]
    near_atm_pc:      Optional[float]
    mid_expiry:       Optional[str]
    mid_oi_pc:        Optional[float]
    term_skew:        Optional[float]
    vol_oi_ratio:     Optional[float]
    put_unwinding:    bool
    fresh_put_buying: bool
    near_term_fear:   bool


@dataclass
class IVAnalysis:
    """
    IV Rank and IV Percentile computed from 252 days of rolling 30-day
    historical volatility (HV30) as a proxy for historical implied volatility.

    current_iv     — ATM implied volatility from the live options chain (%)
    hv_30          — current 30-day realised volatility, annualised (%)
    iv_vs_hv       — current_iv / hv_30 ratio (>1.0 = IV premium over realised vol)
    hv_52w_low     — lowest HV30 value over the past 252 trading days (%)
    hv_52w_high    — highest HV30 value over the past 252 trading days (%)
    iv_rank        — (current_iv - hv_52w_low) / (hv_52w_high - hv_52w_low) × 100
                     0 = at 52-week low, 100 = at 52-week high
    iv_percentile  — % of trading days in past year where HV30 < current_iv
                     90th percentile = IV higher than 90% of the past year
    label          — plain-English summary of the IV environment
    """
    current_iv:    float
    hv_30:         float
    iv_vs_hv:      float
    hv_52w_low:    float
    hv_52w_high:   float
    iv_rank:       float
    iv_percentile: float
    label:         str


@dataclass
class OptionsSummary:
    expiration: str
    put_call_ratio: Optional[float]
    total_call_oi: int
    total_put_oi: int
    total_call_volume: int
    total_put_volume: int
    avg_call_iv: float
    avg_put_iv: float
    atm_calls: list = field(default_factory=list)  # 5 nearest call contracts
    atm_puts: list = field(default_factory=list)   # 5 nearest put contracts


@dataclass
class SecurityAnalysis:
    symbol: str
    name: str
    tags: list
    price: float
    bands: BollingerBands
    options: Optional[OptionsSummary]
    iv: Optional[IVAnalysis] = None
    pc: Optional[PutCallAnalysis] = None

    # Derived scores (set by score())
    bb_pos: float = 0.0          # 0 = lower band, 1 = upper band
    long_score: float = 0.0      # Higher = stronger long/bounce signal
    put_score: float = 0.0       # Higher = stronger bearish/put signal
    long_reason: str = ""
    put_reason: str = ""

    # Guardrail data (set by fetch_security())
    days_to_earnings: Optional[int] = None       # None = unknown; <14 triggers blackout
    recent_positive_catalyst: bool = False        # True = upgrade/deal in last 5 days
    catalyst_headline: str = ""                   # The headline that triggered the flag

    # News sentiment (set by fetch_security() when news_store is available)
    news_signal: str = ""                         # BULLISH/BEARISH/MIXED/NEUTRAL/INSUFFICIENT_DATA
    news_top_headline: str = ""                   # Representative headline for display

    # When the inputs were captured (cache mode only; None = fetched live now)
    as_of: Optional[str] = None


# ---------------------------------------------------------------------------
# Pure numeric helpers
# ---------------------------------------------------------------------------

def _is_empty_calendar(cal) -> bool:
    if cal is None:
        return True
    if hasattr(cal, "empty"):
        return bool(cal.empty)
    return isinstance(cal, dict) and not cal


def _calendar_dates(cal) -> Optional[list]:
    """The candidate earnings dates in a yfinance calendar, or None."""
    if cal is None:
        return None
    # yfinance ≥ 0.2 returns a DataFrame; older versions return a dict.
    if hasattr(cal, "index"):
        # DataFrame: rows are field names, columns are dates
        for label in ("Earnings Date", "Earnings High", "Earnings Low"):
            if label in cal.index:
                return cal.loc[label].tolist()
        # Fallback: use any column values that look like dates
        return list(cal.columns)
    if isinstance(cal, dict):
        return list(cal.values())
    return None


def _as_date(d) -> Optional[date]:
    if hasattr(d, "date"):
        return d.date()
    if isinstance(d, str):
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return d if isinstance(d, date) else None


def _top_by(results: list, score: str, top_n: int) -> list:
    """The `top_n` results with a positive `score`, highest first (stable)."""
    ranked = [r for r in results if getattr(r, score) > 0]
    return sorted(ranked, key=lambda r: getattr(r, score), reverse=True)[:top_n]


def _days_until_next(raw_dates: list, today: date) -> Optional[int]:
    """Days from `today` to the nearest date on or after it, or None."""
    days = [(d - today).days for d in map(_as_date, raw_dates) if d is not None and d >= today]
    return min(days) if days else None


def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val) if val is not None else default
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        f = float(val) if val is not None else 0.0
        return default if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return default


def _chain_pc(calls_df, puts_df, price: float, atm_only: bool = False):
    """Return (oi_pc, vol_pc, atm_oi_pc) for a single expiration chain."""
    if calls_df is None or puts_df is None:
        return None, None, None
    if calls_df.empty or puts_df.empty:
        return None, None, None

    if atm_only:
        atm_range  = sorted(abs(calls_df["strike"] - price))[:PC_ATM_STRIKES * 2] if len(calls_df) else []
        threshold  = atm_range[-1] if atm_range else float("inf")
        calls_df   = calls_df[abs(calls_df["strike"] - price) <= threshold]
        puts_df    = puts_df[abs(puts_df["strike"] - price) <= threshold]

    call_oi  = _safe_int(calls_df["openInterest"].fillna(0).sum())
    put_oi   = _safe_int(puts_df["openInterest"].fillna(0).sum())
    call_vol = _safe_int(calls_df["volume"].fillna(0).sum())
    put_vol  = _safe_int(puts_df["volume"].fillna(0).sum())

    oi_pc  = round(put_oi  / call_oi,  2) if call_oi  > 0 else None
    vol_pc = round(put_vol / call_vol, 2) if call_vol > 0 else None
    return oi_pc, vol_pc, None  # third slot unused in this helper


# ---------------------------------------------------------------------------
# Shared by the live and cached paths
# ---------------------------------------------------------------------------

def _bands_from_history(hist) -> Optional[BollingerBands]:
    if hist is None or hist.empty or len(hist) < BB_PERIOD:
        return None
    close = hist["Close"]
    sma = close.rolling(window=BB_PERIOD).mean().iloc[-1]
    std = close.rolling(window=BB_PERIOD).std().iloc[-1]
    return BollingerBands(
        upper=round(sma + BB_STD_DEV * std, 2),
        middle=round(sma, 2),
        lower=round(sma - BB_STD_DEV * std, 2),
    )


def _iv_label(iv_rank: float) -> str:
    if iv_rank >= IV_RANK_EXTREME_FEAR:
        return f"extreme fear (rank {iv_rank:.0f}%) — capitulation signal, IV expensive"
    if iv_rank >= IV_RANK_HIGH_FEAR:
        return f"elevated fear (rank {iv_rank:.0f}%) — potential bounce zone"
    if iv_rank >= IV_RANK_ELEVATED:
        return f"above average (rank {iv_rank:.0f}%) — some fear priced in"
    if iv_rank <= IV_RANK_VERY_CHEAP:
        return f"very cheap IV (rank {iv_rank:.0f}%) — complacency, puts are cheap"
    if iv_rank <= IV_RANK_COMPLACENT:
        return f"low IV (rank {iv_rank:.0f}%) — complacency, consider cheap puts"
    return f"neutral IV (rank {iv_rank:.0f}%)"


def _hv_series(hist) -> Optional[pd.Series]:
    """Rolling 30-day HV (annualised, %), or None when history is too short."""
    if hist is None or hist.empty or len(hist) < 31:
        return None
    log_returns = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
    hv_series = (log_returns.rolling(window=30).std() * math.sqrt(252) * 100).dropna()
    return hv_series if len(hv_series) >= 20 else None


def _current_iv(options: Optional[OptionsSummary], fallback: float) -> float:
    """Mean of the chain's call/put IV; ``fallback`` (HV30) when it has none."""
    valid_ivs = [v for v in (options.avg_call_iv, options.avg_put_iv) if v > 0] if options else []
    return round(sum(valid_ivs) / len(valid_ivs), 1) if valid_ivs else fallback


def _iv_from_history(hist, options: Optional[OptionsSummary]) -> Optional[IVAnalysis]:
    """IV Rank / Percentile against a year of rolling HV30 (see IVAnalysis)."""
    hv_series = _hv_series(hist)
    if hv_series is None:
        return None

    hv_30       = round(float(hv_series.iloc[-1]), 1)
    hv_52w_low  = round(float(hv_series.min()), 1)
    hv_52w_high = round(float(hv_series.max()), 1)
    current_iv = _current_iv(options, hv_30)

    # IV Rank: position of current IV within the 52-week HV range
    hv_range = hv_52w_high - hv_52w_low
    iv_rank = round((current_iv - hv_52w_low) / hv_range * 100, 1) if hv_range > 0 else 50.0
    iv_rank = max(0.0, min(100.0, iv_rank))

    return IVAnalysis(
        current_iv=current_iv,
        hv_30=hv_30,
        iv_vs_hv=round(current_iv / hv_30, 2) if hv_30 > 0 else None,
        hv_52w_low=hv_52w_low,
        hv_52w_high=hv_52w_high,
        iv_rank=iv_rank,
        # IV Percentile: % of days where HV30 < current IV
        iv_percentile=round(float((hv_series < current_iv).mean() * 100), 1),
        label=_iv_label(iv_rank),
    )


def _atm_contracts(df, price: float) -> list:
    """The 5 contracts nearest `price`, sorted by strike."""
    df = df[df["strike"] > 0].copy()
    df["moneyness"] = abs(df["strike"] - price)
    contracts = []
    for _, row in df.nsmallest(5, "moneyness").iterrows():
        contracts.append({
            "strike": round(float(row["strike"]), 2),
            "last": round(_safe_float(row.get("lastPrice")), 2),
            "bid": round(_safe_float(row.get("bid")), 2),
            "ask": round(_safe_float(row.get("ask")), 2),
            "iv": round(_safe_float(row.get("impliedVolatility")) * 100, 1),
            "volume": _safe_int(row.get("volume")),
            "open_interest": _safe_int(row.get("openInterest")),
            "in_the_money": bool(row.get("inTheMoney", False)),
        })
    return sorted(contracts, key=lambda x: x["strike"])


def _summary_from_chain(expiration: str, calls_df, puts_df, price: float) -> Optional[OptionsSummary]:
    """OptionsSummary from one live expiration's calls/puts frames."""
    if calls_df.empty or puts_df.empty:
        return None
    total_call_oi = _safe_int(calls_df["openInterest"].fillna(0).sum())
    total_put_oi = _safe_int(puts_df["openInterest"].fillna(0).sum())
    return OptionsSummary(
        expiration=expiration,
        put_call_ratio=round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None,
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        total_call_volume=_safe_int(calls_df["volume"].fillna(0).sum()),
        total_put_volume=_safe_int(puts_df["volume"].fillna(0).sum()),
        avg_call_iv=round(_safe_float(calls_df["impliedVolatility"].fillna(0).mean()) * 100, 1),
        avg_put_iv=round(_safe_float(puts_df["impliedVolatility"].fillna(0).mean()) * 100, 1),
        atm_calls=_atm_contracts(calls_df, price),
        atm_puts=_atm_contracts(puts_df, price),
    )


def _atm_pc(calls_df, puts_df, price: float) -> Optional[float]:
    """OI P/C over the PC_ATM_STRIKES strikes nearest `price` on each side."""
    if calls_df.empty or puts_df.empty:
        return None
    c_dist = abs(calls_df["strike"] - price)
    p_dist = abs(puts_df["strike"] - price)
    c_atm = calls_df[c_dist <= c_dist.nsmallest(PC_ATM_STRIKES).iloc[-1]]
    p_atm = puts_df[p_dist <= p_dist.nsmallest(PC_ATM_STRIKES).iloc[-1]]
    call_oi = _safe_int(c_atm["openInterest"].fillna(0).sum())
    put_oi = _safe_int(p_atm["openInterest"].fillna(0).sum())
    return round(put_oi / call_oi, 2) if call_oi > 0 else None


def _pc_analysis(near_exp, near_oi_pc, near_vol_pc, near_atm_pc, mid_exp, mid_oi_pc) -> PutCallAnalysis:
    """Derive the term-skew and vol/OI signals from the per-expiry ratios."""
    term_skew = None
    if near_oi_pc is not None and mid_oi_pc is not None:
        term_skew = round(near_oi_pc - mid_oi_pc, 2)

    vol_oi_ratio = None
    if near_vol_pc is not None and near_oi_pc is not None and near_oi_pc > 0:
        vol_oi_ratio = round(near_vol_pc / near_oi_pc, 2)

    return PutCallAnalysis(
        near_expiry=near_exp,
        near_oi_pc=near_oi_pc,
        near_vol_pc=near_vol_pc,
        near_atm_pc=near_atm_pc,
        mid_expiry=mid_exp,
        mid_oi_pc=mid_oi_pc,
        term_skew=term_skew,
        vol_oi_ratio=vol_oi_ratio,
        put_unwinding=vol_oi_ratio is not None and vol_oi_ratio <= PC_UNWIND_THRESHOLD,
        fresh_put_buying=vol_oi_ratio is not None and vol_oi_ratio >= PC_FRESH_BUY_THRESH,
        near_term_fear=term_skew is not None and term_skew >= PC_TERM_SKEW_MIN,
    )


def _news_fields(summary: dict) -> tuple[str, str, bool]:
    """(signal, top headline, bullish) from a NewsStore sentiment summary.

    BULLISH news blocks puts (a positive catalyst undermines the bearish
    thesis), but only when it rests on scored articles.
    """
    signal = summary.get("signal", "")
    headline = ""
    if summary.get("top_positive"):
        headline = summary["top_positive"][0]
    elif summary.get("top_negative"):
        headline = summary["top_negative"][0]
    bullish = signal == "BULLISH" and summary.get("scored_articles", 0) > 0
    return signal, headline, bullish


# ---------------------------------------------------------------------------
# The cached path: stored captures → the same dataclasses the live path builds
# ---------------------------------------------------------------------------

_CHAIN_COLUMNS = ["strike", "openInterest", "volume", "impliedVolatility",
                  "lastPrice", "bid", "ask", "inTheMoney"]


def _chain_frames(contracts: list[dict]):
    """Stored ``options_contracts`` rows → yfinance-shaped (calls, puts) frames.

    Stored IV is a percentage; yfinance's is a fraction. A stored ask of 0
    (common in an after-hours capture) falls back to the last trade, so the
    indicative trade specs aren't silently dropped.
    """
    rows = {"call": [], "put": []}
    for c in contracts:
        ask = _safe_float(c.get("ask"))
        last = _safe_float(c.get("last_price"))
        rows.setdefault(c.get("kind"), []).append({
            "strike": _safe_float(c.get("strike")),
            "openInterest": _safe_int(c.get("open_interest")),
            "volume": _safe_int(c.get("volume")),
            "impliedVolatility": _safe_float(c.get("implied_vol")) / 100,
            "lastPrice": last,
            "bid": _safe_float(c.get("bid")),
            "ask": ask if ask > 0 else last,
            "inTheMoney": bool(c.get("in_the_money")),
        })
    return (pd.DataFrame(rows["call"], columns=_CHAIN_COLUMNS),
            pd.DataFrame(rows["put"], columns=_CHAIN_COLUMNS))


def _summary_from_cache(exp: dict, price: float) -> Optional[OptionsSummary]:
    """OptionsSummary from a stored expiration: its all-strike aggregates plus
    the ATM contracts read from its (strike-banded) contracts."""
    calls_df, puts_df = _chain_frames(exp.get("contracts") or [])
    if calls_df.empty or puts_df.empty:
        return None
    pc = exp.get("put_call_ratio")
    return OptionsSummary(
        expiration=exp["expiration"],
        put_call_ratio=round(float(pc), 2) if pc is not None else None,
        total_call_oi=_safe_int(exp.get("total_call_oi")),
        total_put_oi=_safe_int(exp.get("total_put_oi")),
        total_call_volume=_safe_int(exp.get("total_call_vol")),
        total_put_volume=_safe_int(exp.get("total_put_vol")),
        avg_call_iv=round(_safe_float(exp.get("avg_call_iv")), 1),
        avg_put_iv=round(_safe_float(exp.get("avg_put_iv")), 1),
        atm_calls=_atm_contracts(calls_df, price),
        atm_puts=_atm_contracts(puts_df, price),
    )


def _pc_from_cache(exps: list[dict], price: float) -> Optional[PutCallAnalysis]:
    """PutCallAnalysis from stored expirations (nearest first)."""
    if not exps:
        return None
    near = exps[0]
    near_oi_pc = near.get("put_call_ratio")
    near_oi_pc = round(float(near_oi_pc), 2) if near_oi_pc is not None else None
    call_vol = _safe_int(near.get("total_call_vol"))
    near_vol_pc = round(_safe_int(near.get("total_put_vol")) / call_vol, 2) if call_vol > 0 else None
    calls_df, puts_df = _chain_frames(near.get("contracts") or [])
    mid = next((e for e in exps[1:] if e.get("put_call_ratio") is not None), None)
    return _pc_analysis(
        near["expiration"], near_oi_pc, near_vol_pc, _atm_pc(calls_df, puts_df, price),
        mid["expiration"] if mid else None,
        round(float(mid["put_call_ratio"]), 2) if mid else None,
    )


def _captured_date(captured_at: str) -> date:
    return market_date(datetime.fromisoformat(captured_at.replace("Z", "+00:00")))


def _is_stale(snap: Optional[dict], today: date) -> bool:
    """No capture, nothing unexpired in it, or older than the staleness limit."""
    if not snap or not snap.get("expirations"):
        return True
    age = trading_days_after(_captured_date(snap["captured_at"]), today)
    return age > SCREEN_MAX_STALENESS_TRADING_DAYS


def _security_from_cache(entry: dict, snap: dict, hist, days_to_earnings, news) -> Optional[SecurityAnalysis]:
    """A SecurityAnalysis built only from stored data (no Yahoo call)."""
    price = _safe_float(snap.get("price"))
    if price <= 0 and hist is not None and not hist.empty:
        price = _safe_float(hist["Close"].iloc[-1])
    if price <= 0:
        return None
    price = round(price, 2)
    bands = _bands_from_history(hist)
    if bands is None:
        return None

    options = _summary_from_cache(snap["expirations"][0], price)
    signal, headline, bullish = _news_fields(news or {})
    return SecurityAnalysis(
        symbol=entry["symbol"].upper(),
        name=entry["name"],
        tags=entry["tags"],
        price=price,
        bands=bands,
        options=options,
        iv=_iv_from_history(hist, options),
        pc=_pc_from_cache(snap["expirations"], price),
        days_to_earnings=days_to_earnings,
        # Cache mode has no live keyword scan: only scored BULLISH news flags it.
        recent_positive_catalyst=bullish,
        catalyst_headline=headline if bullish else "",
        news_signal=signal,
        news_top_headline=headline,
        as_of=snap["captured_at"],
    )


@dataclass
class _CachedInputs:
    """The four bulk reads the cached screen scores from, keyed by symbol."""
    chains: dict
    history: dict
    earnings: dict
    news: dict


def _try_security_from_cache(entry: dict, sym: str, inputs: _CachedInputs) -> Optional[SecurityAnalysis]:
    try:
        return _security_from_cache(entry, inputs.chains[sym], inputs.history.get(sym),
                                    inputs.earnings.get(sym), inputs.news.get(sym))
    except Exception:
        return None


def _score_from_cache(entries: list[dict], inputs: _CachedInputs, today: date):
    """Split the entries into (scored, failed symbols, stale rows)."""
    results: list[SecurityAnalysis] = []
    failed: list[str] = []
    stale: list[dict] = []
    for entry in entries:
        sym = entry["symbol"].upper()
        snap = inputs.chains.get(sym)
        if _is_stale(snap, today):
            stale.append({"symbol": sym, "last_captured": snap["captured_at"] if snap else None})
            continue
        sec = _try_security_from_cache(entry, sym, inputs)
        if sec is None:
            failed.append(entry["symbol"])
        else:
            results.append(sec)
    return results, failed, stale


def _mark_cached_response(response: dict, chains: dict, results: list, stale: list) -> dict:
    """Stamp a ranked response with where its numbers came from and when."""
    for trade in response["put_trades"]:
        trade["pricing"] = f"indicative, as of {chains[trade['symbol']]['captured_at']}"
    response.update({
        "source": "cache",
        # The oldest capture any scored symbol rests on.
        "as_of": min((s.as_of for s in results), default=None),
        "stale": stale,
    })
    if stale:
        response["stale_hint"] = (
            "No options capture within the last "
            f"{SCREEN_MAX_STALENESS_TRADING_DAYS} trading day(s); "
            "run analyze_options_symbol on these for a live read."
        )
    return response


class OptionsScreeningService:
    """Rule-based options screener: fetch → score → build trades → rank."""

    def __init__(
        self,
        ohlcv_repository,
        yfinance_gateway,
        prices=None,
        *,
        options_repository=None,
        fundamentals_repository=None,
        news_store=None,
    ):
        self._ohlcv = ohlcv_repository
        self._yf = yfinance_gateway
        # History via PricesService — the single fetch seam (issue #74).
        self._prices = prices
        # The cached path's readers (source="cache"); see the module docstring.
        self._options_repo = options_repository
        self._fundamentals_repo = fundamentals_repository
        self._news_store = news_store

    # ------------------------------------------------------------------
    # Data fetching (the live path)
    # ------------------------------------------------------------------

    def fetch_bollinger_bands(self, symbol: str) -> Optional[BollingerBands]:
        try:
            return _bands_from_history(
                self._prices.get_history(symbol, "1d", period_to_days(HISTORY_PERIOD)))
        except Exception:
            return None

    def _prefetch_chain(self, symbol: str):
        """(expirations, nearest chain), fetched once for both options readers.

        A failure reads as no expirations, so neither reader refetches it.
        """
        try:
            expirations = self._yf.expirations(symbol)
            if not expirations:
                return [], None
            return expirations, self._yf.option_chain(symbol, expirations[0])
        except Exception:
            return [], None

    def fetch_options(
        self, symbol: str, price: float, expirations=None, near_chain=None,
    ) -> Optional[OptionsSummary]:
        try:
            if expirations is None:
                expirations = self._yf.expirations(symbol)
            if not expirations:
                return None
            if near_chain is None:
                near_chain = self._yf.option_chain(symbol, expirations[0])
            return _summary_from_chain(
                expirations[0], near_chain.calls.copy(), near_chain.puts.copy(), price)
        except Exception:
            return None

    def fetch_put_call_analysis(
        self, symbol: str, price: float, expirations=None, near_chain=None,
    ) -> Optional[PutCallAnalysis]:
        """
        Fetch the nearest two option expirations and compute:
          - OI and volume P/C ratios for each
          - ATM-only OI P/C for the nearest expiry
          - Term structure skew (near minus mid)
          - Vol/OI divergence ratio (put unwinding vs fresh buying)
        """
        try:
            if expirations is None:
                expirations = self._yf.expirations(symbol)
            if not expirations:
                return None

            # --- Nearest expiry ---
            near_exp = expirations[0]
            if near_chain is None:
                near_chain = self._yf.option_chain(symbol, near_exp)
            nc, np_ = near_chain.calls.copy(), near_chain.puts.copy()
            near_oi_pc, near_vol_pc, _ = _chain_pc(nc, np_, price)

            # --- Mid expiry (first later one with a usable OI P/C) ---
            mid_exp, mid_oi_pc = self._first_mid_pc(symbol, expirations[1:], price)

            return _pc_analysis(near_exp, near_oi_pc, near_vol_pc, _atm_pc(nc, np_, price),
                                mid_exp, mid_oi_pc)
        except Exception:
            return None

    def _first_mid_pc(self, symbol: str, expirations, price: float):
        for exp in expirations:
            try:
                mid_chain = self._yf.option_chain(symbol, exp)
                oi_pc, _, _ = _chain_pc(mid_chain.calls.copy(), mid_chain.puts.copy(), price)
                if oi_pc is not None:
                    return exp, oi_pc
            except Exception:
                continue
        return None, None

    def fetch_iv_analysis(self, symbol: str, options: Optional[OptionsSummary]) -> Optional[IVAnalysis]:
        """
        Compute IV Rank and IV Percentile using 252 days of rolling 30-day
        historical volatility as a proxy for historical implied volatility.

        Current IV is taken from the live options chain (average of ATM put and
        call IV).  If no options data is available, current IV falls back to the
        most recent HV30 value so rank/percentile still reflect the vol environment.
        """
        try:
            return _iv_from_history(self._prices.get_history(symbol, "1d", 365), options)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Guardrail data helpers
    # ------------------------------------------------------------------

    def fetch_earnings_proximity(
        self, symbol: str, *, now: datetime | None = None
    ) -> Optional[int]:
        """
        Return the number of calendar days until the next earnings date,
        or None if the information is unavailable.

        Values < EARNINGS_BLACKOUT_DAYS (14) trigger the earnings blackout guardrail.
        """
        try:
            raw_dates = _calendar_dates(self._fetch_calendar(symbol))
            if raw_dates is None:
                return None
            # yfinance supplies date-only earnings labels; compare them with
            # the US market date rather than the host's UTC date.
            return _days_until_next(raw_dates, market_date(now))
        except Exception:
            return None

    def _fetch_calendar(self, symbol: str):
        """yfinance's calendar, asked twice if the first answer is empty.

        yfinance logs a failed fetch (e.g. a 401 "Invalid Crumb") and returns
        an empty calendar, which would read as "no earnings date" (#331).
        """
        cal = self._yf.calendar(symbol)
        if _is_empty_calendar(cal):
            time.sleep(CALENDAR_RETRY_PAUSE_SECONDS)
            cal = self._yf.calendar(symbol)
        return cal

    def fetch_recent_positive_catalyst(self, symbol: str) -> tuple[bool, str]:
        """
        Scan the last CATALYST_LOOKBACK_DAYS of news headlines for analyst upgrades
        or positive catalyst keywords.

        Returns (triggered: bool, headline: str).
        The headline is the first matching title found, for display in the report.
        """
        try:
            from datetime import datetime as _datetime, timezone as _timezone, timedelta as _timedelta
            cutoff = _datetime.now(_timezone.utc) - _timedelta(days=CATALYST_LOOKBACK_DAYS)

            news = self._yf.news(symbol)
            if not news:
                return False, ""

            for item in news:
                # yfinance news items have providerPublishTime (Unix epoch int)
                ts = item.get("providerPublishTime") or item.get("published") or 0
                try:
                    pub_dt = _datetime.fromtimestamp(float(ts), tz=_timezone.utc)
                except (TypeError, ValueError, OSError):
                    continue

                if pub_dt < cutoff:
                    continue

                title = (item.get("title") or "").lower()
                for kw in POSITIVE_CATALYST_KEYWORDS:
                    if kw in title:
                        return True, item.get("title", "")

            return False, ""
        except Exception:
            return False, ""

    def _live_news(self, sym: str, news_store) -> tuple[str, str, bool, str]:
        """(signal, top headline, catalyst hit, catalyst headline) for the live path.

        Scored news decides when there is some; otherwise the yfinance keyword
        scan does.
        """
        if news_store is None:
            return ("", "") + self.fetch_recent_positive_catalyst(sym)
        try:
            summary = news_store.get_sentiment_summary(sym, days=CATALYST_LOOKBACK_DAYS)
        except Exception:
            return ("", "") + self.fetch_recent_positive_catalyst(sym)
        signal, headline, bullish = _news_fields(summary)
        if bullish:
            return signal, headline, True, headline
        if signal in ("INSUFFICIENT_DATA", ""):
            # Fall back to keyword scan if no scored articles yet
            return (signal, headline) + self.fetch_recent_positive_catalyst(sym)
        return signal, headline, False, ""

    def fetch_security(
        self,
        symbol: str,
        name: str,
        tags: list,
        news_store=None,
    ) -> Optional[SecurityAnalysis]:
        try:
            sym = symbol.upper()
            info = self._yf.fast_info(sym)
            price = getattr(info, "last_price", None)
            if price is None or math.isnan(float(price)):
                return None

            price = round(float(price), 2)
            bands = self.fetch_bollinger_bands(sym)
            if bands is None:
                return None

            # One expirations call and one nearest-chain call serve both readers.
            expirations, near_chain = self._prefetch_chain(sym)
            options     = self.fetch_options(sym, price, expirations, near_chain)
            iv_analysis = self.fetch_iv_analysis(sym, options)
            pc_analysis = self.fetch_put_call_analysis(sym, price, expirations, near_chain)
            days_to_earnings = self.fetch_earnings_proximity(sym)

            news_signal, news_top_headline, catalyst_hit, catalyst_headline = self._live_news(
                sym, news_store if news_store is not None else self._news_store)

            return SecurityAnalysis(
                symbol=sym,
                name=name,
                tags=tags,
                price=price,
                bands=bands,
                options=options,
                iv=iv_analysis,
                pc=pc_analysis,
                days_to_earnings=days_to_earnings,
                recent_positive_catalyst=catalyst_hit,
                catalyst_headline=catalyst_headline,
                news_signal=news_signal,
                news_top_headline=news_top_headline,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Scoring — pure rule-based, no LLM
    # ------------------------------------------------------------------

    def score(self, sec: SecurityAnalysis) -> None:
        """
        Populate long_score, put_score, long_reason, put_reason on the object.

        Scoring rules:
        ─────────────
        LONG score drivers (higher = stronger bounce / accumulation signal):
          +3  price below lower BB (technically oversold)
          +2  price within 2% of lower BB (near oversold)
          +3  put/call ratio < 0.5 (very bullish options positioning)
          +2  put/call ratio 0.5–0.8
          +1  large total call volume (top-tier institutional attention)
          +3  IV rank ≥ 80% (extreme fear / capitulation — IV expensive, mean-reversion likely)
          +2  IV rank 60–80% (elevated fear — potential bounce zone)
          +1  IV rank 40–60% (above average — some fear priced in)
          +2  put unwinding (vol P/C < OI P/C — today's traders selling puts = fear fading)
          +1  near-term fear > mid-term (near P/C > mid P/C by ≥ 0.30 — acute short-term capitulation)
          +1  ATM P/C lower than total P/C (near-money calls being bought — targeted bullish positioning)

        PUT score drivers (higher = stronger bearish / put trade signal):
          +3  price above upper BB (technically overbought)
          +2  price within 2% of upper BB
          +3  put/call ratio > 2.0 (very bearish options positioning)
          +2  put/call ratio 1.5–2.0
          +1  put OI > call OI by a significant margin
          +1  large total put OI (institutional hedging conviction)
          +2  IV rank ≤ 20% (complacency — puts are cheap, ideal entry for bearish trades)
          +1  IV rank ≤ 10% (very cheap IV — maximum complacency)
          +2  fresh put buying (vol P/C ≥ 1.5× OI P/C — aggressive new bearish positioning today)
          +1  ATM P/C higher than total P/C (targeted hedging right at current price)
        """
        bb_pos = sec.bands.position(sec.price)
        sec.bb_pos = bb_pos

        long_points = []
        put_points = []

        # --- Bollinger Band position ---
        pct_from_lower = sec.bands.pct_from_lower(sec.price)
        pct_from_upper = sec.bands.pct_from_upper(sec.price)

        if bb_pos <= 0.0:  # at or below lower band
            long_points.append((3, f"below lower BB ({sec.bands.lower})"))
        elif pct_from_lower <= 2.0:
            long_points.append((2, f"within 2% of lower BB ({sec.bands.lower})"))

        if bb_pos >= 1.0:  # at or above upper band
            put_points.append((3, f"above upper BB ({sec.bands.upper}) by {pct_from_upper:+.1f}%"))
        elif pct_from_upper >= -2.0:  # within 2% below upper
            put_points.append((2, f"within 2% of upper BB ({sec.bands.upper})"))

        # --- Options signals ---
        if sec.options is not None:
            pc = sec.options.put_call_ratio

            if pc is not None:
                if pc < PC_VERY_BULLISH:
                    long_points.append((3, f"P/C {pc:.2f} (very bullish)"))
                elif pc < PC_BULLISH:
                    long_points.append((2, f"P/C {pc:.2f} (bullish)"))

                if pc > PC_VERY_BEARISH:
                    put_points.append((3, f"P/C {pc:.2f} (very bearish)"))
                elif pc > PC_BEARISH:
                    put_points.append((2, f"P/C {pc:.2f} (bearish)"))

            # Large call volume = institutional attention (long signal)
            if sec.options.total_call_volume > 50_000:
                long_points.append((1, f"huge call volume ({sec.options.total_call_volume:,})"))
            elif sec.options.total_call_volume > 10_000:
                long_points.append((1, f"large call volume ({sec.options.total_call_volume:,})"))

            # Large put OI with put > call OI = institutional hedging (put signal)
            if (
                sec.options.total_put_oi > MIN_PUT_OI_FOR_SIGNAL
                and sec.options.total_put_oi > sec.options.total_call_oi
            ):
                ratio = sec.options.total_put_oi / max(sec.options.total_call_oi, 1)
                if ratio > 2.0:
                    put_points.append((1, f"put OI {sec.options.total_put_oi:,} >> call OI {sec.options.total_call_oi:,} ({ratio:.1f}x)"))
                else:
                    put_points.append((1, f"put OI ({sec.options.total_put_oi:,}) > call OI ({sec.options.total_call_oi:,})"))

            if sec.options.total_put_oi > 50_000:
                put_points.append((1, f"massive put OI ({sec.options.total_put_oi:,})"))

        # --- IV Rank signals ---
        if sec.iv is not None:
            ivr = sec.iv.iv_rank
            if ivr >= IV_RANK_EXTREME_FEAR:
                long_points.append((3, f"IV rank {ivr:.0f}% (extreme fear — capitulation signal)"))
            elif ivr >= IV_RANK_HIGH_FEAR:
                long_points.append((2, f"IV rank {ivr:.0f}% (elevated fear — potential bounce zone)"))
            elif ivr >= IV_RANK_ELEVATED:
                long_points.append((1, f"IV rank {ivr:.0f}% (above-average fear)"))

            if ivr <= IV_RANK_VERY_CHEAP:
                put_points.append((3, f"IV rank {ivr:.0f}% (very cheap IV — maximum complacency, puts cheap)"))
            elif ivr <= IV_RANK_COMPLACENT:
                put_points.append((2, f"IV rank {ivr:.0f}% (low IV — complacency, puts cheap)"))

        # --- Rich P/C signals ---
        if sec.pc is not None:
            pc = sec.pc

            # Long signals
            if pc.put_unwinding:
                long_points.append((2, f"put unwinding (vol P/C {pc.near_vol_pc:.2f} < OI P/C {pc.near_oi_pc:.2f} — fear fading)"))
            if pc.near_term_fear:
                long_points.append((1, f"near-term fear spike (near P/C {pc.near_oi_pc:.2f} vs mid {pc.mid_oi_pc:.2f}, skew +{pc.term_skew:.2f})"))
            if (pc.near_atm_pc is not None and pc.near_oi_pc is not None
                    and pc.near_atm_pc < pc.near_oi_pc * 0.85):
                long_points.append((1, f"ATM P/C {pc.near_atm_pc:.2f} < total P/C {pc.near_oi_pc:.2f} (near-money calls bought)"))

            # Put signals
            if pc.fresh_put_buying:
                put_points.append((2, f"fresh put buying (vol P/C {pc.near_vol_pc:.2f} ≥ {PC_FRESH_BUY_THRESH}× OI P/C {pc.near_oi_pc:.2f})"))
            if (pc.near_atm_pc is not None and pc.near_oi_pc is not None
                    and pc.near_atm_pc > pc.near_oi_pc * 1.20):
                put_points.append((1, f"ATM P/C {pc.near_atm_pc:.2f} > total P/C {pc.near_oi_pc:.2f} (targeted hedging at current price)"))

        # --- News sentiment boost ---
        if sec.news_signal == "BULLISH":
            long_points.append((2, "FinBERT news signal: BULLISH"))
        elif sec.news_signal == "BEARISH":
            put_points.append((2, "FinBERT news signal: BEARISH"))
        elif sec.news_signal == "MIXED":
            # Mixed news adds a modest point to the directionally dominant side
            long_points.append((1, "FinBERT news signal: MIXED (slight long bias)"))

        sec.long_score = sum(pts for pts, _ in long_points)
        sec.put_score = sum(pts for pts, _ in put_points)
        sec.long_reason = "; ".join(desc for _, desc in long_points) if long_points else "no signal"
        sec.put_reason = "; ".join(desc for _, desc in put_points) if put_points else "no signal"

    # ------------------------------------------------------------------
    # Trade builders
    # ------------------------------------------------------------------

    def build_put_trade(
        self,
        sec: SecurityAnalysis,
        budget_per_trade: float = 500.0,
        total_budget: float = 1000.0,
    ) -> Optional[dict]:
        """
        Given a bearish security, select the best ATM/near-ATM put contract and
        return a trade spec with cost, target, and risk/reward estimate.

        budget_per_trade  — ideal allocation per position (used for contract sizing)
        total_budget      — hard cap: skip if even 1 contract exceeds total budget
        """
        if sec.options is None or not sec.options.atm_puts:
            return None

        # --- Guardrail 1: Earnings proximity blackout ---
        # Earnings events can gap the stock in either direction; a pure put thesis
        # becomes a coin-flip inside the blackout window.
        if (
            sec.days_to_earnings is not None
            and sec.days_to_earnings < EARNINGS_BLACKOUT_DAYS
        ):
            return None

        # --- Guardrail 2: Catalyst cooldown ---
        # A recent analyst upgrade or positive catalyst undermines the bearish thesis.
        # The upgrade may be exactly what caused the put-heavy positioning (hedging longs),
        # making the P/C signal a false bearish read.
        if sec.recent_positive_catalyst:
            return None

        # --- Guardrail 3: Contradiction guard ---
        # When both the long and put scores are elevated, the signals are contradicting
        # each other (e.g. oversold bounce potential AND heavy put protection).
        # Ambiguous setups have poor risk/reward as the true direction is unclear.
        if sec.long_score >= CONTRADICTION_LONG_MIN and sec.put_score >= CONTRADICTION_PUT_MIN:
            return None

        # Prefer the put closest to ATM (first one after sorting by moneyness)
        best_put = min(sec.options.atm_puts, key=lambda p: abs(p["strike"] - sec.price))
        ask = best_put["ask"]
        if ask <= 0:
            return None

        cost_per_contract = ask * 100

        # Skip if even a single contract exceeds the total available budget
        if cost_per_contract > total_budget:
            return None

        contracts = max(1, int(budget_per_trade // cost_per_contract))

        # For put trades the target is always the lower Bollinger Band.
        # If the price is already below the lower BB (breakdown confirmed),
        # target 5% below the lower BB.
        if sec.price < sec.bands.lower:
            target_price = round(sec.bands.lower * 0.95, 2)
        else:
            target_price = sec.bands.lower

        # Put value at target (intrinsic only — ignores remaining time value)
        intrinsic_at_target = max(0.0, best_put["strike"] - target_price)
        profit_per_contract = (intrinsic_at_target - ask) * 100
        roi_pct = (profit_per_contract / cost_per_contract * 100) if cost_per_contract > 0 else 0

        # Skip trades with no positive return potential at the target price
        if roi_pct <= 0:
            return None

        # Spread suggestion when ATM put IV is high (> 65%) — sell a lower strike to offset cost
        suggest_spread = best_put["iv"] > 65.0

        return {
            "symbol": sec.symbol,
            "strike": best_put["strike"],
            "expiration": sec.options.expiration,
            "ask": ask,
            "contracts": contracts,
            "total_cost": round(cost_per_contract * contracts, 2),
            "iv": best_put["iv"],
            "target_price": round(target_price, 2),
            "profit_at_target_per_contract": round(profit_per_contract, 2),
            "roi_at_target_pct": round(roi_pct, 1),
            "suggest_spread": suggest_spread,
            "put_call_ratio": sec.options.put_call_ratio,
            "put_oi": sec.options.total_put_oi,
            "bb_pos": round(sec.bb_pos, 3),
            "put_score": sec.put_score,
        }

    def build_call_trade(
        self,
        sec: SecurityAnalysis,
        budget_per_trade: float = 500.0,
        total_budget: float = 1000.0,
    ) -> Optional[dict]:
        """
        Given a bullish security, select the best ATM/near-ATM call contract and
        return a trade spec with cost, target, and risk/reward estimate.

        Target price for calls is always the upper Bollinger Band.
        If the price is already above the upper BB (breakout confirmed),
        target 5% above the upper BB.

        Guardrails applied:
          - Earnings blackout (<14 days): earnings gap risk is direction-agnostic.
        Catalyst cooldown and contradiction guard are NOT applied to calls —
        a positive catalyst supports the call thesis.
        """
        if sec.options is None or not sec.options.atm_calls:
            return None

        # Earnings blackout applies to both directions
        if (
            sec.days_to_earnings is not None
            and sec.days_to_earnings < EARNINGS_BLACKOUT_DAYS
        ):
            return None

        best_call = min(sec.options.atm_calls, key=lambda c: abs(c["strike"] - sec.price))
        ask = best_call["ask"]
        if ask <= 0:
            return None

        cost_per_contract = ask * 100

        if cost_per_contract > total_budget:
            return None

        contracts = max(1, int(budget_per_trade // cost_per_contract))

        # Target is always the upper Bollinger Band.
        # If the price is already above the upper BB, target 5% above it.
        if sec.price > sec.bands.upper:
            target_price = round(sec.bands.upper * 1.05, 2)
        else:
            target_price = sec.bands.upper

        intrinsic_at_target = max(0.0, target_price - best_call["strike"])
        profit_per_contract = (intrinsic_at_target - ask) * 100
        roi_pct = (profit_per_contract / cost_per_contract * 100) if cost_per_contract > 0 else 0

        if roi_pct <= 0:
            return None

        # Spread suggestion when ATM call IV is high — sell a higher strike to offset cost
        suggest_spread = best_call["iv"] > 65.0

        return {
            "symbol": sec.symbol,
            "strike": best_call["strike"],
            "expiration": sec.options.expiration,
            "ask": ask,
            "contracts": contracts,
            "total_cost": round(cost_per_contract * contracts, 2),
            "iv": best_call["iv"],
            "target_price": round(target_price, 2),
            "profit_at_target_per_contract": round(profit_per_contract, 2),
            "roi_at_target_pct": round(roi_pct, 1),
            "suggest_spread": suggest_spread,
            "put_call_ratio": sec.options.put_call_ratio,
            "call_oi": sec.options.total_call_oi,
            "bb_pos": round(sec.bb_pos, 3),
            "long_score": sec.long_score,
        }

    # ------------------------------------------------------------------
    # Guardrail descriptions (consumed by the CLI presentation layer)
    # ------------------------------------------------------------------

    def put_guardrail_reason(self, sec: SecurityAnalysis) -> str:
        """
        Return a human-readable description of the first active put guardrail,
        or an empty string if no guardrail is triggered.
        Mirrors the logic in build_put_trade() so the display is always consistent.
        """
        if (
            sec.days_to_earnings is not None
            and sec.days_to_earnings < EARNINGS_BLACKOUT_DAYS
        ):
            return f"earnings in {sec.days_to_earnings}d (blackout <{EARNINGS_BLACKOUT_DAYS}d)"
        if sec.recent_positive_catalyst:
            headline = sec.catalyst_headline[:60] + "…" if len(sec.catalyst_headline) > 60 else sec.catalyst_headline
            return f"positive catalyst within {CATALYST_LOOKBACK_DAYS}d: \"{headline}\""
        if sec.long_score >= CONTRADICTION_LONG_MIN and sec.put_score >= CONTRADICTION_PUT_MIN:
            return (
                f"ambiguous signal (long_score={sec.long_score:.0f} AND put_score={sec.put_score:.0f} "
                f"both ≥ {CONTRADICTION_PUT_MIN})"
            )
        return ""

    def call_guardrail_reason(self, sec: SecurityAnalysis) -> str:
        """
        Return a human-readable description of the first active call guardrail,
        or an empty string if no guardrail is triggered.
        Earnings blackout and BEARISH news signal block calls.
        Positive catalysts support the call thesis; contradiction guard not applied.
        """
        if (
            sec.days_to_earnings is not None
            and sec.days_to_earnings < EARNINGS_BLACKOUT_DAYS
        ):
            return f"earnings in {sec.days_to_earnings}d (blackout <{EARNINGS_BLACKOUT_DAYS}d)"
        if sec.news_signal == "BEARISH":
            headline = sec.news_top_headline[:60] + "…" if len(sec.news_top_headline) > 60 else sec.news_top_headline
            note = f': "{headline}"' if headline else ""
            return f"FinBERT BEARISH news signal within {CATALYST_LOOKBACK_DAYS}d{note}"
        return ""

    # ------------------------------------------------------------------
    # Portfolio ranking / budget allocation
    # ------------------------------------------------------------------

    def combined_put_rank_score(self, t: dict) -> float:
        """Blended rank: conviction (put_score) × CONVICTION_WEIGHT + capped ROI × ROI_WEIGHT."""
        roi_norm  = min(t["roi_at_target_pct"], ROI_CAP_FOR_RANKING) / ROI_CAP_FOR_RANKING
        conv_norm = t.get("put_score", 0) / MAX_PUT_SCORE
        return CONVICTION_WEIGHT * conv_norm + ROI_WEIGHT * roi_norm

    def combined_call_rank_score(self, t: dict) -> float:
        """Blended rank: conviction (long_score) × CONVICTION_WEIGHT + capped ROI × ROI_WEIGHT."""
        roi_norm  = min(t["roi_at_target_pct"], ROI_CAP_FOR_RANKING) / ROI_CAP_FOR_RANKING
        conv_norm = t.get("long_score", 0) / MAX_PUT_SCORE
        return CONVICTION_WEIGHT * conv_norm + ROI_WEIGHT * roi_norm

    def greedy_fill(self, trades: list[dict], total_budget: float, rank_fn) -> list[dict]:
        """Greedy budget fill sorted by rank_fn descending. Returns selected trades."""
        remaining = total_budget
        selected = []
        for t in sorted(trades, key=rank_fn, reverse=True):
            cost = t["ask"] * 100
            if cost <= remaining:
                affordable_contracts = max(1, int(remaining // cost))
                t = dict(t)
                t["contracts"] = affordable_contracts
                t["total_cost"] = round(cost * affordable_contracts, 2)
                t["rank_score"] = rank_fn(t)
                selected.append(t)
                remaining -= t["total_cost"]
            if remaining < 50:
                break
        return selected

    # ------------------------------------------------------------------
    # Watchlist helpers
    # ------------------------------------------------------------------

    @staticmethod
    def load_watchlist(path) -> list[dict]:
        with open(path) as f:
            entries = yaml.safe_load(f)
        result = []
        for entry in entries:
            symbol = entry.get("symbol", "").strip()
            name = entry.get("name", symbol).strip()
            tags = [t for t in (entry.get("tags") or []) if t]
            if symbol:
                result.append({"symbol": symbol, "name": name, "tags": tags})
        return result

    @staticmethod
    def is_us_listed(symbol: str) -> bool:
        return not any(symbol.upper().endswith(sfx) for sfx in SKIP_SUFFIXES)

    # ------------------------------------------------------------------
    # MCP-facing analysis (verbatim response shapes — behavioral parity)
    # ------------------------------------------------------------------

    def _build_candidate_summary(self, sec: SecurityAnalysis) -> dict:
        pc = sec.options.put_call_ratio if sec.options else None
        return {
            "symbol": sec.symbol,
            "name": sec.name,
            "tags": sec.tags,
            "price": sec.price,
            "as_of": sec.as_of,
            "bb_pos": round(sec.bb_pos, 3),
            "bands": {
                "lower": sec.bands.lower,
                "middle": sec.bands.middle,
                "upper": sec.bands.upper,
            },
            "put_call_ratio": pc,
            "long_score": sec.long_score,
            "put_score": sec.put_score,
            "long_reason": sec.long_reason,
            "put_reason": sec.put_reason,
            "iv_label": sec.iv.label if sec.iv else None,
            "pc_analysis": {
                "near_oi_pc": sec.pc.near_oi_pc if sec.pc else None,
                "near_vol_pc": sec.pc.near_vol_pc if sec.pc else None,
                "near_atm_pc": sec.pc.near_atm_pc if sec.pc else None,
                "mid_oi_pc": sec.pc.mid_oi_pc if sec.pc else None,
                "term_skew": sec.pc.term_skew if sec.pc else None,
                "put_unwinding": sec.pc.put_unwinding if sec.pc else None,
                "fresh_put_buying": sec.pc.fresh_put_buying if sec.pc else None,
                "near_term_fear": sec.pc.near_term_fear if sec.pc else None,
            },
        }

    def _fetch_one(self, entry: dict) -> Optional[SecurityAnalysis]:
        try:
            return self.fetch_security(entry["symbol"], entry["name"], entry["tags"])
        finally:
            # yfinance's sqlite cache connections are per thread (peewee keeps
            # them thread-local), so only the thread that fetched can close its
            # own; closing from the caller afterwards leaves the workers' open.
            self._yf.close_thread_caches()

    def _fetch_all(self, entries: list[dict]) -> list[Optional[SecurityAnalysis]]:
        """Fetch every entry, in watchlist order, on a bounded pool (#331).

        The first symbol is fetched alone: yfinance shares one cookie/crumb
        across threads, and workers that all start without one flip its
        strategy on each other's 401s ("Invalid Crumb"). One fetch on this
        thread settles it before the pool starts. map() keeps watchlist order,
        so equal scores still rank as before.
        """
        fetched = [self._fetch_one(e) for e in entries[:1]]
        rest = entries[1:]
        if rest:
            with ThreadPoolExecutor(max_workers=min(SCREEN_MAX_WORKERS, len(rest))) as pool:
                fetched += pool.map(self._fetch_one, rest)
        return fetched

    def _build_put_trades(self, put_candidates: list[SecurityAnalysis], puts_budget: float) -> list:
        trades = []
        for sec in put_candidates:
            trade = self.build_put_trade(
                sec,
                budget_per_trade=puts_budget / max(len(put_candidates), 1),
                total_budget=puts_budget,
            )
            if trade:
                trades.append(trade)
        return trades

    def _ranked_response(self, entries: list[dict], results: list[SecurityAnalysis],
                         failed: list[str], puts_budget: float, top_n: int) -> dict:
        for sec in results:
            self.score(sec)
        long_candidates = _top_by(results, "long_score", top_n)
        put_candidates = _top_by(results, "put_score", top_n)
        trades = self._build_put_trades(put_candidates, puts_budget)
        return {
            "symbols_scanned": len(entries),
            "fetched": len(results),
            "failed": failed,
            "long_candidates": [self._build_candidate_summary(s) for s in long_candidates],
            "put_candidates": [self._build_candidate_summary(s) for s in put_candidates],
            "put_trades": trades,
        }

    def _run_analysis(self, entries: list[dict], puts_budget: float, top_n: int) -> dict:
        """The live path: every input fetched from Yahoo, per symbol."""
        results: list[SecurityAnalysis] = []
        failed: list[str] = []
        for entry, sec in zip(entries, self._fetch_all(entries)):
            if sec is None:
                failed.append(entry["symbol"])
            else:
                results.append(sec)
        response = self._ranked_response(entries, results, failed, puts_budget, top_n)
        response["source"] = "live"
        return response

    # ------------------------------------------------------------------
    # The cached path (source="cache"): database reads only, never Yahoo
    # ------------------------------------------------------------------

    def _cached_earnings(self, today: date) -> dict[str, Optional[int]]:
        """{SYMBOL: days to next earnings} from the cached earnings calendar.

        Repository only: ``FundamentalsService.get_earnings_calendar`` computes
        live on a miss, which is a Yahoo call. A miss reads as unknown (None),
        the same as the live path's empty calendar.
        """
        days: dict[str, Optional[int]] = {}
        for row in self._fundamentals_repo.get_all_latest("earnings_calendar"):
            ed = row.get("earnings_date")
            if row.get("symbol") and ed:
                days[row["symbol"].upper()] = _days_until_next([str(ed)[:10]], today)
        return days

    def _run_cached(self, entries: list[dict], puts_budget: float, top_n: int,
                    *, now: datetime | None = None) -> dict:
        if self._options_repo is None or self._fundamentals_repo is None or self._news_store is None:
            raise RuntimeError(
                "source='cache' needs options_repository, fundamentals_repository "
                "and news_store; wire them in registry.py or pass source='live'")
        today = market_date(now)
        symbols = [e["symbol"].upper() for e in entries]
        chains = self._options_repo.get_latest_full_chains(symbols, today.isoformat())
        inputs = _CachedInputs(
            chains=chains,
            history=self._ohlcv.daily_history_for_symbols(symbols, CACHED_HISTORY_DAYS),
            earnings=self._cached_earnings(today),
            news=self._news_store.get_sentiment_summaries(symbols, days=CATALYST_LOOKBACK_DAYS),
        )
        results, failed, stale = _score_from_cache(entries, inputs, today)
        response = self._ranked_response(entries, results, failed, puts_budget, top_n)
        return _mark_cached_response(response, chains, results, stale)

    @staticmethod
    def _normalize_entries(entries: list[dict]) -> list[dict]:
        """Reduce watchlist rows to the three fields the screener uses.

        Rows from the database carry the full securities shape (currency, the
        None purchase/sale placeholders, source); rows from the YAML carry only
        some of it. Both arrive here.
        """
        result = []
        for entry in entries:
            symbol = (entry.get("symbol") or "").strip()
            if not symbol:
                continue
            name = (entry.get("name") or symbol).strip()
            result.append({
                "symbol": symbol,
                "name": name,
                "tags": [t for t in (entry.get("tags") or []) if t],
            })
        return result

    def _run(self, entries: list[dict], puts_budget: float, top_n: int, source: str) -> dict:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
        if source == "cache":
            return self._run_cached(entries, puts_budget=puts_budget, top_n=top_n)
        return self._run_analysis(entries, puts_budget=puts_budget, top_n=top_n)

    def analyze_watchlist(
        self,
        entries: list[dict] | None = None,
        watchlist_path: str | None = None,
        puts_budget: float = 1000.0,
        top_n: int = 10,
        include_non_us: bool = False,
        source: str = "cache",
    ) -> dict:
        """Screen a watchlist. Callers supply the rows; the file is the fallback.

        Issue #83: the watchlist lives in the database now, and a service does
        not reach for files or repositories it does not own — the route passes
        ``entries`` from ``deps.load_watchlist()``. ``watchlist_path`` stays for
        the standalone CLI path, which still screens a YAML file directly.

        ``source="cache"`` (the default) reads only the database and lists
        symbols without a recent capture under ``stale``; ``"live"`` fetches
        every symbol from Yahoo, which for a whole watchlist takes minutes.
        """
        if entries is not None:
            entries = self._normalize_entries(entries)
        else:
            path = Path(watchlist_path) if watchlist_path else (_PROJECT_ROOT / "watchlist.yaml")
            if not path.exists():
                raise FileNotFoundError(f"watchlist not found at {path}")
            entries = self.load_watchlist(path)
        if not include_non_us:
            entries = [e for e in entries if self.is_us_listed(e["symbol"])]
        return self._run(entries, puts_budget, top_n, source)

    def analyze_symbol(self, symbol: str, puts_budget: float = 1000.0, top_n: int = 10,
                       source: str = "live") -> dict:
        entry = {"symbol": symbol.upper(), "name": symbol.upper(), "tags": []}
        return self._run([entry], puts_budget, top_n, source)
