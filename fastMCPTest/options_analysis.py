"""
Options analysis tool — reads watchlist.yaml, fetches live data via yfinance,
scores each security using Bollinger Band position and options Put/Call ratio,
and prints ranked long candidates and put trade setups.

No LLM required — all scoring logic is rule-based.

Usage:
    python options_analysis.py
    python options_analysis.py --watchlist /path/to/watchlist.yaml
    python options_analysis.py --puts-budget 1000
    python options_analysis.py --watchlist /path/to/watchlist.yaml --puts-budget 1000 --top-n 15
"""

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import yaml
import yfinance as yf
from ohlcv_cache import get_history, period_to_days

from options_store import OptionsStore


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BB_PERIOD = 20
BB_STD_DEV = 2
HISTORY_PERIOD = "3mo"

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


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

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


def fetch_bollinger_bands(ticker: yf.Ticker) -> Optional[BollingerBands]:
    try:
        hist = get_history(ticker.ticker, "1d", period_to_days(HISTORY_PERIOD))
        if hist.empty or len(hist) < BB_PERIOD:
            return None
        close = hist["Close"]
        sma = close.rolling(window=BB_PERIOD).mean().iloc[-1]
        std = close.rolling(window=BB_PERIOD).std().iloc[-1]
        return BollingerBands(
            upper=round(sma + BB_STD_DEV * std, 2),
            middle=round(sma, 2),
            lower=round(sma - BB_STD_DEV * std, 2),
        )
    except Exception:
        return None


def fetch_options(ticker: yf.Ticker, price: float) -> Optional[OptionsSummary]:
    try:
        expirations = ticker.options
        if not expirations:
            return None

        nearest_exp = expirations[0]
        chain = ticker.option_chain(nearest_exp)
        calls_df = chain.calls.copy()
        puts_df = chain.puts.copy()

        if calls_df.empty or puts_df.empty:
            return None

        total_call_oi = _safe_int(calls_df["openInterest"].fillna(0).sum())
        total_put_oi = _safe_int(puts_df["openInterest"].fillna(0).sum())
        total_call_vol = _safe_int(calls_df["volume"].fillna(0).sum())
        total_put_vol = _safe_int(puts_df["volume"].fillna(0).sum())
        avg_call_iv = round(_safe_float(calls_df["impliedVolatility"].fillna(0).mean()) * 100, 1)
        avg_put_iv = round(_safe_float(puts_df["impliedVolatility"].fillna(0).mean()) * 100, 1)

        put_call_ratio = (
            round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None
        )

        def _atm_contracts(df, price):
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

        return OptionsSummary(
            expiration=nearest_exp,
            put_call_ratio=put_call_ratio,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            total_call_volume=total_call_vol,
            total_put_volume=total_put_vol,
            avg_call_iv=avg_call_iv,
            avg_put_iv=avg_put_iv,
            atm_calls=_atm_contracts(calls_df, price),
            atm_puts=_atm_contracts(puts_df, price),
        )
    except Exception:
        return None


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


def fetch_put_call_analysis(ticker: yf.Ticker, price: float) -> Optional[PutCallAnalysis]:
    """
    Fetch the nearest two option expirations and compute:
      - OI and volume P/C ratios for each
      - ATM-only OI P/C for the nearest expiry
      - Term structure skew (near minus mid)
      - Vol/OI divergence ratio (put unwinding vs fresh buying)
    """
    try:
        expirations = ticker.options
        if not expirations:
            return None

        # --- Nearest expiry ---
        near_exp   = expirations[0]
        near_chain = ticker.option_chain(near_exp)
        nc, np_   = near_chain.calls.copy(), near_chain.puts.copy()

        near_oi_pc, near_vol_pc, _ = _chain_pc(nc, np_, price)

        # ATM-only P/C for nearest expiry
        atm_call_oi = atm_put_oi = 0
        if not nc.empty and not np_.empty:
            nc_atm = nc[abs(nc["strike"] - price) <= abs(nc["strike"] - price).nsmallest(PC_ATM_STRIKES).iloc[-1]]
            np_atm = np_[abs(np_["strike"] - price) <= abs(np_["strike"] - price).nsmallest(PC_ATM_STRIKES).iloc[-1]]
            atm_call_oi = _safe_int(nc_atm["openInterest"].fillna(0).sum())
            atm_put_oi  = _safe_int(np_atm["openInterest"].fillna(0).sum())
        near_atm_pc = round(atm_put_oi / atm_call_oi, 2) if atm_call_oi > 0 else None

        # --- Mid expiry (second available, skip if same week) ---
        mid_exp    = None
        mid_oi_pc  = None
        for exp in expirations[1:]:
            try:
                mid_chain  = ticker.option_chain(exp)
                mc, mp     = mid_chain.calls.copy(), mid_chain.puts.copy()
                oi_pc, _, _ = _chain_pc(mc, mp, price)
                if oi_pc is not None:
                    mid_exp   = exp
                    mid_oi_pc = oi_pc
                    break
            except Exception:
                continue

        # --- Derived signals ---
        term_skew = None
        if near_oi_pc is not None and mid_oi_pc is not None:
            term_skew = round(near_oi_pc - mid_oi_pc, 2)

        vol_oi_ratio = None
        if near_vol_pc is not None and near_oi_pc is not None and near_oi_pc > 0:
            vol_oi_ratio = round(near_vol_pc / near_oi_pc, 2)

        put_unwinding    = vol_oi_ratio is not None and vol_oi_ratio <= PC_UNWIND_THRESHOLD
        fresh_put_buying = vol_oi_ratio is not None and vol_oi_ratio >= PC_FRESH_BUY_THRESH
        near_term_fear   = term_skew is not None and term_skew >= PC_TERM_SKEW_MIN

        return PutCallAnalysis(
            near_expiry=near_exp,
            near_oi_pc=near_oi_pc,
            near_vol_pc=near_vol_pc,
            near_atm_pc=near_atm_pc,
            mid_expiry=mid_exp,
            mid_oi_pc=mid_oi_pc,
            term_skew=term_skew,
            vol_oi_ratio=vol_oi_ratio,
            put_unwinding=put_unwinding,
            fresh_put_buying=fresh_put_buying,
            near_term_fear=near_term_fear,
        )
    except Exception:
        return None


def fetch_iv_analysis(ticker: yf.Ticker, options: Optional[OptionsSummary]) -> Optional[IVAnalysis]:
    """
    Compute IV Rank and IV Percentile using 252 days of rolling 30-day
    historical volatility as a proxy for historical implied volatility.

    Current IV is taken from the live options chain (average of ATM put and
    call IV).  If no options data is available, current IV falls back to the
    most recent HV30 value so rank/percentile still reflect the vol environment.
    """
    try:
        hist = get_history(ticker.ticker, "1d", 365)
        if hist.empty or len(hist) < 31:
            return None

        log_returns = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()

        # Rolling 30-day HV, annualised
        hv_series = log_returns.rolling(window=30).std() * math.sqrt(252) * 100
        hv_series = hv_series.dropna()

        if len(hv_series) < 20:
            return None

        hv_30       = round(float(hv_series.iloc[-1]), 1)
        hv_52w_low  = round(float(hv_series.min()), 1)
        hv_52w_high = round(float(hv_series.max()), 1)

        # Current IV: prefer live options chain; fall back to hv_30
        if options is not None and (options.avg_call_iv > 0 or options.avg_put_iv > 0):
            valid_ivs = [v for v in [options.avg_call_iv, options.avg_put_iv] if v > 0]
            current_iv = round(sum(valid_ivs) / len(valid_ivs), 1)
        else:
            current_iv = hv_30

        iv_vs_hv = round(current_iv / hv_30, 2) if hv_30 > 0 else None

        # IV Rank: position of current IV within the 52-week HV range
        hv_range = hv_52w_high - hv_52w_low
        if hv_range > 0:
            iv_rank = round((current_iv - hv_52w_low) / hv_range * 100, 1)
        else:
            iv_rank = 50.0
        iv_rank = max(0.0, min(100.0, iv_rank))

        # IV Percentile: % of days where HV30 < current IV
        iv_percentile = round(float((hv_series < current_iv).mean() * 100), 1)

        # Label
        if iv_rank >= IV_RANK_EXTREME_FEAR:
            label = f"extreme fear (rank {iv_rank:.0f}%) — capitulation signal, IV expensive"
        elif iv_rank >= IV_RANK_HIGH_FEAR:
            label = f"elevated fear (rank {iv_rank:.0f}%) — potential bounce zone"
        elif iv_rank >= IV_RANK_ELEVATED:
            label = f"above average (rank {iv_rank:.0f}%) — some fear priced in"
        elif iv_rank <= IV_RANK_VERY_CHEAP:
            label = f"very cheap IV (rank {iv_rank:.0f}%) — complacency, puts are cheap"
        elif iv_rank <= IV_RANK_COMPLACENT:
            label = f"low IV (rank {iv_rank:.0f}%) — complacency, consider cheap puts"
        else:
            label = f"neutral IV (rank {iv_rank:.0f}%)"

        return IVAnalysis(
            current_iv=current_iv,
            hv_30=hv_30,
            iv_vs_hv=iv_vs_hv,
            hv_52w_low=hv_52w_low,
            hv_52w_high=hv_52w_high,
            iv_rank=iv_rank,
            iv_percentile=iv_percentile,
            label=label,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Guardrail helpers
# ---------------------------------------------------------------------------

def fetch_earnings_proximity(ticker: yf.Ticker) -> Optional[int]:
    """
    Return the number of calendar days until the next earnings date,
    or None if the information is unavailable.

    Values < EARNINGS_BLACKOUT_DAYS (14) trigger the earnings blackout guardrail.
    """
    try:
        from datetime import date as _date, datetime as _datetime
        cal = ticker.calendar
        if cal is None:
            return None

        # yfinance ≥ 0.2 returns a DataFrame; older versions return a dict.
        if hasattr(cal, "index"):
            # DataFrame: rows are field names, columns are dates
            # Earnings Date row may appear as "Earnings Date" or similar
            for label in ("Earnings Date", "Earnings High", "Earnings Low"):
                if label in cal.index:
                    raw_dates = cal.loc[label].tolist()
                    break
            else:
                # Fallback: use any column values that look like dates
                raw_dates = list(cal.columns)
        elif isinstance(cal, dict):
            raw_dates = list(cal.values())
        else:
            return None

        today = _date.today()
        future_days = []
        for d in raw_dates:
            if d is None:
                continue
            if hasattr(d, "date"):
                d = d.date()
            elif isinstance(d, str):
                try:
                    d = _datetime.strptime(d[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
            if isinstance(d, _date) and d >= today:
                future_days.append((d - today).days)

        return min(future_days) if future_days else None
    except Exception:
        return None


def fetch_recent_positive_catalyst(ticker: yf.Ticker) -> tuple[bool, str]:
    """
    Scan the last CATALYST_LOOKBACK_DAYS of news headlines for analyst upgrades
    or positive catalyst keywords.

    Returns (triggered: bool, headline: str).
    The headline is the first matching title found, for display in the report.
    """
    try:
        from datetime import datetime as _datetime, timezone as _timezone, timedelta as _timedelta
        cutoff = _datetime.now(_timezone.utc) - _timedelta(days=CATALYST_LOOKBACK_DAYS)

        news = ticker.news
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


def fetch_security(symbol: str, name: str, tags: list) -> Optional[SecurityAnalysis]:
    try:
        ticker = yf.Ticker(symbol.upper())
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        if price is None or math.isnan(float(price)):
            return None

        price = round(float(price), 2)
        bands = fetch_bollinger_bands(ticker)
        if bands is None:
            return None

        options     = fetch_options(ticker, price)
        iv_analysis = fetch_iv_analysis(ticker, options)
        pc_analysis = fetch_put_call_analysis(ticker, price)
        days_to_earnings = fetch_earnings_proximity(ticker)
        catalyst_hit, catalyst_headline = fetch_recent_positive_catalyst(ticker)

        return SecurityAnalysis(
            symbol=symbol.upper(),
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
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scoring — pure rule-based, no LLM
# ---------------------------------------------------------------------------

def score(sec: SecurityAnalysis) -> None:
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

    sec.long_score = sum(pts for pts, _ in long_points)
    sec.put_score = sum(pts for pts, _ in put_points)
    sec.long_reason = "; ".join(desc for _, desc in long_points) if long_points else "no signal"
    sec.put_reason = "; ".join(desc for _, desc in put_points) if put_points else "no signal"


# ---------------------------------------------------------------------------
# Put trade builder
# ---------------------------------------------------------------------------

def build_put_trade(
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


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

SEPARATOR = "─" * 72


def _bb_bar(pos: float, width: int = 20) -> str:
    """Visual ASCII bar: [====|====] where | is the price position."""
    pos = max(-0.2, min(1.2, pos))
    idx = int(pos * width)
    idx = max(0, min(width - 1, idx))
    bar = ["-"] * width
    bar[idx] = "█"
    return "[" + "".join(bar) + "]"


def _pc_label(pc: Optional[float]) -> str:
    if pc is None:
        return "n/a"
    if pc < PC_VERY_BULLISH:
        return f"{pc:.2f} ▲▲ very bullish"
    if pc < PC_BULLISH:
        return f"{pc:.2f} ▲  bullish"
    if pc < PC_NEUTRAL_HIGH:
        return f"{pc:.2f} ─  neutral"
    if pc < PC_VERY_BEARISH:
        return f"{pc:.2f} ▼  bearish"
    return f"{pc:.2f} ▼▼ very bearish"


def print_section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def print_long_candidates(
    results: list[SecurityAnalysis],
    top_n: int = 10,
    budget: float = 1000.0,
) -> None:
    print_section("LONG / BOUNCE CANDIDATES  (oversold + bullish options)")
    candidates = sorted(
        [s for s in results if s.long_score > 0],
        key=lambda s: s.long_score,
        reverse=True,
    )[:top_n]

    if not candidates:
        print("  No candidates met the scoring threshold.")
        return

    call_trades = []
    for rank, sec in enumerate(candidates, 1):
        pc = sec.options.put_call_ratio if sec.options else None
        call_vol = sec.options.total_call_volume if sec.options else 0
        iv_label = sec.iv.label if sec.iv else "n/a"
        print(f"\n  #{rank}  {sec.symbol:<6}  ${sec.price:<8.2f}  long_score={sec.long_score}")
        print(f"       BB   lower={sec.bands.lower}  mid={sec.bands.middle}  upper={sec.bands.upper}")
        print(f"            {_bb_bar(sec.bb_pos)}  pos={sec.bb_pos:.2f}")
        print(f"       P/C  {_pc_label(pc)}", end="")
        if sec.pc is not None:
            atm = f"  atm={sec.pc.near_atm_pc:.2f}" if sec.pc.near_atm_pc else ""
            vol = f"  vol={sec.pc.near_vol_pc:.2f}" if sec.pc.near_vol_pc else ""
            mid = f"  mid={sec.pc.mid_oi_pc:.2f}" if sec.pc.mid_oi_pc else ""
            flags = ""
            if sec.pc.put_unwinding:  flags += " [UNWINDING]"
            if sec.pc.near_term_fear: flags += " [NEAR-TERM-FEAR]"
            print(f"{atm}{vol}{mid}{flags}")
        else:
            print()
        print(f"       IV   {iv_label}")
        print(f"       Vol  call_volume={call_vol:,}")
        if sec.days_to_earnings is not None:
            print(f"       ERN  {sec.days_to_earnings}d to earnings")
        print(f"       Why  {sec.long_reason}")

        # Primary recommendation: call trade
        call_guardrail = _call_guardrail_reason(sec)
        if call_guardrail:
            print(f"       *** GUARDRAIL (CALL): {call_guardrail} — call trade skipped ***")
        call_trade = build_call_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if call_trade:
            call_trades.append(call_trade)
            _print_call_trade(call_trade)

        # Comparison: put trade for the same security
        put_guardrail = _guardrail_reason(sec)
        if put_guardrail:
            print(f"       *** GUARDRAIL (PUT):  {put_guardrail} — put trade skipped ***")
        put_trade = build_put_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if put_trade:
            _print_put_trade(put_trade)

    print_call_portfolio_summary(call_trades, budget)


def _guardrail_reason(sec: SecurityAnalysis) -> str:
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


def _call_guardrail_reason(sec: SecurityAnalysis) -> str:
    """
    Return a human-readable description of the first active call guardrail,
    or an empty string if no guardrail is triggered.
    Only earnings blackout applies to calls — positive catalysts support the
    call thesis and the contradiction guard is not used for directional long trades.
    """
    if (
        sec.days_to_earnings is not None
        and sec.days_to_earnings < EARNINGS_BLACKOUT_DAYS
    ):
        return f"earnings in {sec.days_to_earnings}d (blackout <{EARNINGS_BLACKOUT_DAYS}d)"
    return ""


def _print_call_trade(trade: dict) -> None:
    """Print a single call trade spec in a consistent format."""
    spread_note = "  *** high IV — consider bull call spread ***" if trade["suggest_spread"] else ""
    print(f"       CALL strike={trade['strike']}  exp={trade['expiration']}"
          f"  ask=${trade['ask']:.2f}/share  IV={trade['iv']:.0f}%{spread_note}")
    print(f"            target=${trade['target_price']}  "
          f"est. P&L/contract=${trade['profit_at_target_per_contract']:.0f}  "
          f"ROI={trade['roi_at_target_pct']:.0f}%")
    print(f"            cost={trade['contracts']}x @ ${trade['total_cost']:.0f} total")


def _print_put_trade(trade: dict) -> None:
    """Print a single put trade spec in a consistent format."""
    spread_note = "  *** high IV — consider debit spread ***" if trade["suggest_spread"] else ""
    print(f"       PUT  strike={trade['strike']}  exp={trade['expiration']}"
          f"  ask=${trade['ask']:.2f}/share  IV={trade['iv']:.0f}%{spread_note}")
    print(f"            target=${trade['target_price']}  "
          f"est. P&L/contract=${trade['profit_at_target_per_contract']:.0f}  "
          f"ROI={trade['roi_at_target_pct']:.0f}%")
    print(f"            cost={trade['contracts']}x @ ${trade['total_cost']:.0f} total")


def print_put_candidates(results: list[SecurityAnalysis], budget: float, top_n: int = 7) -> None:
    print_section("PUT / BEARISH CANDIDATES  (overbought or heavy put positioning)")
    candidates = sorted(
        [s for s in results if s.put_score > 0],
        key=lambda s: s.put_score,
        reverse=True,
    )[:top_n]

    if not candidates:
        print("  No candidates met the scoring threshold.")
        return

    trades = []
    for rank, sec in enumerate(candidates, 1):
        pc = sec.options.put_call_ratio if sec.options else None
        put_oi = sec.options.total_put_oi if sec.options else 0
        iv_label = sec.iv.label if sec.iv else "n/a"
        print(f"\n  #{rank}  {sec.symbol:<6}  ${sec.price:<8.2f}  put_score={sec.put_score}  long_score={sec.long_score}")
        print(f"       BB   lower={sec.bands.lower}  mid={sec.bands.middle}  upper={sec.bands.upper}")
        print(f"            {_bb_bar(sec.bb_pos)}  pos={sec.bb_pos:.2f}")
        print(f"       P/C  {_pc_label(pc)}", end="")
        if sec.pc is not None:
            atm = f"  atm={sec.pc.near_atm_pc:.2f}" if sec.pc.near_atm_pc else ""
            vol = f"  vol={sec.pc.near_vol_pc:.2f}" if sec.pc.near_vol_pc else ""
            mid = f"  mid={sec.pc.mid_oi_pc:.2f}" if sec.pc.mid_oi_pc else ""
            flags = ""
            if sec.pc.fresh_put_buying: flags += " [FRESH-PUTS]"
            if sec.pc.near_term_fear:   flags += " [NEAR-TERM-FEAR]"
            print(f"{atm}{vol}{mid}{flags}")
        else:
            print()
        print(f"       IV   {iv_label}")
        print(f"       OI   put_oi={put_oi:,}")
        if sec.days_to_earnings is not None:
            print(f"       ERN  {sec.days_to_earnings}d to earnings")
        print(f"       Why  {sec.put_reason}")

        # Primary recommendation: put trade
        guardrail_reason = _guardrail_reason(sec)
        if guardrail_reason:
            print(f"       *** GUARDRAIL (PUT):  {guardrail_reason} — put trade skipped ***")
        trade = build_put_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if trade:
            trades.append(trade)
            _print_put_trade(trade)

        # Comparison: call trade for the same security
        call_guardrail = _call_guardrail_reason(sec)
        if call_guardrail:
            print(f"       *** GUARDRAIL (CALL): {call_guardrail} — call trade skipped ***")
        call_trade = build_call_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if call_trade:
            _print_call_trade(call_trade)

    print_put_portfolio_summary(trades, budget)


def _combined_put_rank_score(t: dict) -> float:
    """Blended rank: conviction (put_score) × CONVICTION_WEIGHT + capped ROI × ROI_WEIGHT."""
    roi_norm  = min(t["roi_at_target_pct"], ROI_CAP_FOR_RANKING) / ROI_CAP_FOR_RANKING
    conv_norm = t.get("put_score", 0) / MAX_PUT_SCORE
    return CONVICTION_WEIGHT * conv_norm + ROI_WEIGHT * roi_norm


def _combined_call_rank_score(t: dict) -> float:
    """Blended rank: conviction (long_score) × CONVICTION_WEIGHT + capped ROI × ROI_WEIGHT."""
    roi_norm  = min(t["roi_at_target_pct"], ROI_CAP_FOR_RANKING) / ROI_CAP_FOR_RANKING
    conv_norm = t.get("long_score", 0) / MAX_PUT_SCORE
    return CONVICTION_WEIGHT * conv_norm + ROI_WEIGHT * roi_norm


def _greedy_fill(trades: list[dict], total_budget: float, rank_fn) -> list[dict]:
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


def print_put_portfolio_summary(trades: list[dict], total_budget: float) -> None:
    """Greedy budget fill sorted by blended conviction + ROI score."""
    if not trades:
        return
    print_section(f"PUT PORTFOLIO SUMMARY  (budget ${total_budget:,.0f})")
    print(f"  Ranking: {int(CONVICTION_WEIGHT*100)}% conviction (put score) + "
          f"{int(ROI_WEIGHT*100)}% ROI (capped at {ROI_CAP_FOR_RANKING:.0f}%)\n")

    selected = _greedy_fill(trades, total_budget, _combined_put_rank_score)
    if not selected:
        print("  No trades fit within budget.")
        return

    total_spent = sum(t["total_cost"] for t in selected)
    for t in selected:
        print(
            f"  {t['symbol']:<6}  ${t['strike']} put  exp={t['expiration']}"
            f"  {t['contracts']}x  cost=${t['total_cost']:.0f}"
            f"  target=${t['target_price']}  est ROI={t['roi_at_target_pct']:.0f}%"
            f"  conviction={t.get('put_score', 0):.0f}  rank={t['rank_score']:.2f}"
        )
    print(f"\n  Total deployed: ${total_spent:,.0f} / ${total_budget:,.0f}")
    leftover = total_budget - total_spent
    if leftover > 0:
        print(f"  Remaining:      ${leftover:,.0f}  (consider longer-dated puts or debit spreads)")


def print_call_portfolio_summary(trades: list[dict], total_budget: float) -> None:
    """Greedy budget fill for call trades sorted by blended conviction + ROI score."""
    if not trades:
        return
    print_section(f"CALL PORTFOLIO SUMMARY  (budget ${total_budget:,.0f})")
    print(f"  Ranking: {int(CONVICTION_WEIGHT*100)}% conviction (long score) + "
          f"{int(ROI_WEIGHT*100)}% ROI (capped at {ROI_CAP_FOR_RANKING:.0f}%)\n")

    selected = _greedy_fill(trades, total_budget, _combined_call_rank_score)
    if not selected:
        print("  No trades fit within budget.")
        return

    total_spent = sum(t["total_cost"] for t in selected)
    for t in selected:
        print(
            f"  {t['symbol']:<6}  ${t['strike']} call  exp={t['expiration']}"
            f"  {t['contracts']}x  cost=${t['total_cost']:.0f}"
            f"  target=${t['target_price']}  est ROI={t['roi_at_target_pct']:.0f}%"
            f"  conviction={t.get('long_score', 0):.0f}  rank={t['rank_score']:.2f}"
        )
    print(f"\n  Total deployed: ${total_spent:,.0f} / ${total_budget:,.0f}")
    leftover = total_budget - total_spent
    if leftover > 0:
        print(f"  Remaining:      ${leftover:,.0f}  (consider longer-dated calls or bull call spreads)")


def print_skip_list(results: list[SecurityAnalysis]) -> None:
    """Print securities with no actionable signal in either direction."""
    skipped = [s for s in results if s.long_score == 0 and s.put_score == 0]
    if skipped:
        symbols = ", ".join(s.symbol for s in skipped)
        print(f"\n  Skipped (no signal): {symbols}")


# ---------------------------------------------------------------------------
# Watchlist loader
# ---------------------------------------------------------------------------

def load_watchlist(path: Path) -> list[dict]:
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


SKIP_SUFFIXES = (".PA", ".OL", ".AS", ".SG", ".KS", ".ST", ".DE")


def is_us_listed(symbol: str) -> bool:
    return not any(symbol.upper().endswith(sfx) for sfx in SKIP_SUFFIXES)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Options analysis from watchlist.yaml")
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=Path(__file__).parent / "watchlist.yaml",
        help="Path to watchlist YAML file",
    )
    parser.add_argument(
        "--puts-budget",
        type=float,
        default=1000.0,
        help="Total budget for put trades ($)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Max candidates to show per section",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Analyse a single symbol instead of full watchlist",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to options SQLite database (default: options_chain.db next to this script)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip saving snapshots to the database (useful for quick one-off runs)",
    )
    args = parser.parse_args()

    if args.symbol:
        entries = [{"symbol": args.symbol.upper(), "name": args.symbol.upper(), "tags": []}]
    else:
        if not args.watchlist.exists():
            print(f"ERROR: watchlist not found at {args.watchlist}", file=sys.stderr)
            sys.exit(1)
        entries = load_watchlist(args.watchlist)
        entries = [e for e in entries if is_us_listed(e["symbol"])]

    # Set up persistence (unless suppressed)
    store: Optional[OptionsStore] = None
    if not args.no_persist:
        store = OptionsStore(args.db) if args.db else OptionsStore()

    print(f"\nOptions Analysis Engine")
    print(f"Watchlist : {args.watchlist}")
    print(f"Symbols   : {len(entries)} (US-listed)")
    print(f"Put Budget: ${args.puts_budget:,.0f}")
    if store:
        print(f"Database  : {store.db_path}")
    print(f"\nFetching data", end="", flush=True)

    results: list[SecurityAnalysis] = []
    failed: list[str] = []
    saved = 0

    for entry in entries:
        sym = entry["symbol"]
        print(".", end="", flush=True)
        sec = fetch_security(sym, entry["name"], entry["tags"])
        if sec is None:
            failed.append(sym)
            continue
        score(sec)
        results.append(sec)

        # Persist snapshot for backtesting (issue #10)
        if store and sec.options:
            bb_dict = {
                "upper": sec.bands.upper,
                "middle": sec.bands.middle,
                "lower": sec.bands.lower,
                "period": 20,
            }
            opts_dict = {
                "expiration": sec.options.expiration,
                "put_call_ratio": sec.options.put_call_ratio,
                "calls": {
                    "total_open_interest": sec.options.total_call_oi,
                    "total_volume": sec.options.total_call_volume,
                    "avg_iv_pct": sec.options.avg_call_iv,
                    "atm_contracts": sec.options.atm_calls,
                },
                "puts": {
                    "total_open_interest": sec.options.total_put_oi,
                    "total_volume": sec.options.total_put_volume,
                    "avg_iv_pct": sec.options.avg_put_iv,
                    "atm_contracts": sec.options.atm_puts,
                },
            }
            if store.save_snapshot(sym, sec.price, bb_dict, opts_dict) is not None:
                saved += 1

    print(f" done ({len(results)} fetched, {len(failed)} failed)")
    if store:
        print(f"Persisted : {saved} new snapshots → {store.db_path.name}")
    if failed:
        print(f"Failed    : {', '.join(failed)}")

    if not results:
        print("No data retrieved. Check network / symbols.")
        sys.exit(1)

    print_long_candidates(results, top_n=args.top_n, budget=args.puts_budget)
    print_put_candidates(results, budget=args.puts_budget, top_n=args.top_n)
    print_skip_list(results)
    print(f"\n{SEPARATOR}\n")


if __name__ == "__main__":
    main()
