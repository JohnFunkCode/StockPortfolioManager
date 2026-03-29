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

import yaml
import yfinance as yf


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

# Portfolio ranking: blended conviction + ROI score
# ROI is capped before normalising so extreme outliers (e.g. 776%) don't
# swamp the conviction signal from the put score.
ROI_CAP_FOR_RANKING = 200.0   # cap ROI% at this value before normalising
ROI_WEIGHT = 0.40             # weight given to (capped, normalised) ROI
CONVICTION_WEIGHT = 0.60      # weight given to normalised put_score
MAX_PUT_SCORE = 8             # theoretical max from scoring rules


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
class OptionsSummary:
    expiration: str
    put_call_ratio: Optional[float]
    total_call_oi: int
    total_put_oi: int
    total_call_volume: int
    total_put_volume: int
    avg_call_iv: float
    avg_put_iv: float
    atm_puts: list = field(default_factory=list)   # list of dicts from yfinance


@dataclass
class SecurityAnalysis:
    symbol: str
    name: str
    tags: list
    price: float
    bands: BollingerBands
    options: Optional[OptionsSummary]

    # Derived scores (set by score())
    bb_pos: float = 0.0          # 0 = lower band, 1 = upper band
    long_score: float = 0.0      # Higher = stronger long/bounce signal
    put_score: float = 0.0       # Higher = stronger bearish/put signal
    long_reason: str = ""
    put_reason: str = ""


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
        hist = ticker.history(period=HISTORY_PERIOD)
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

        # ATM puts: 5 strikes nearest to current price
        puts_df = puts_df[puts_df["strike"] > 0].copy()
        puts_df["moneyness"] = abs(puts_df["strike"] - price)
        atm_puts = []
        for _, row in puts_df.nsmallest(5, "moneyness").iterrows():
            atm_puts.append({
                "strike": round(float(row["strike"]), 2),
                "last": round(_safe_float(row.get("lastPrice")), 2),
                "bid": round(_safe_float(row.get("bid")), 2),
                "ask": round(_safe_float(row.get("ask")), 2),
                "iv": round(_safe_float(row.get("impliedVolatility")) * 100, 1),
                "volume": _safe_int(row.get("volume")),
                "open_interest": _safe_int(row.get("openInterest")),
                "in_the_money": bool(row.get("inTheMoney", False)),
            })

        return OptionsSummary(
            expiration=nearest_exp,
            put_call_ratio=put_call_ratio,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            total_call_volume=total_call_vol,
            total_put_volume=total_put_vol,
            avg_call_iv=avg_call_iv,
            avg_put_iv=avg_put_iv,
            atm_puts=sorted(atm_puts, key=lambda x: x["strike"]),
        )
    except Exception:
        return None


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

        options = fetch_options(ticker, price)

        return SecurityAnalysis(
            symbol=symbol.upper(),
            name=name,
            tags=tags,
            price=price,
            bands=bands,
            options=options,
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

    PUT score drivers (higher = stronger bearish / put trade signal):
      +3  price above upper BB (technically overbought)
      +2  price within 2% of upper BB
      +3  put/call ratio > 2.0 (very bearish options positioning)
      +2  put/call ratio 1.5–2.0
      +1  put OI > call OI by a significant margin
      +1  large total put OI (institutional hedging conviction)
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


def print_long_candidates(results: list[SecurityAnalysis], top_n: int = 10) -> None:
    print_section("LONG / BOUNCE CANDIDATES  (oversold + bullish options)")
    candidates = sorted(
        [s for s in results if s.long_score > 0],
        key=lambda s: s.long_score,
        reverse=True,
    )[:top_n]

    if not candidates:
        print("  No candidates met the scoring threshold.")
        return

    for rank, sec in enumerate(candidates, 1):
        pc = sec.options.put_call_ratio if sec.options else None
        call_vol = sec.options.total_call_volume if sec.options else 0
        print(f"\n  #{rank}  {sec.symbol:<6}  ${sec.price:<8.2f}  score={sec.long_score}")
        print(f"       BB   lower={sec.bands.lower}  mid={sec.bands.middle}  upper={sec.bands.upper}")
        print(f"            {_bb_bar(sec.bb_pos)}  pos={sec.bb_pos:.2f}")
        print(f"       P/C  {_pc_label(pc)}")
        print(f"       Vol  call_volume={call_vol:,}")
        print(f"       Why  {sec.long_reason}")


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
        print(f"\n  #{rank}  {sec.symbol:<6}  ${sec.price:<8.2f}  score={sec.put_score}")
        print(f"       BB   lower={sec.bands.lower}  mid={sec.bands.middle}  upper={sec.bands.upper}")
        print(f"            {_bb_bar(sec.bb_pos)}  pos={sec.bb_pos:.2f}")
        print(f"       P/C  {_pc_label(pc)}")
        print(f"       OI   put_oi={put_oi:,}")
        print(f"       Why  {sec.put_reason}")

        trade = build_put_trade(
            sec,
            budget_per_trade=budget / max(len(candidates), 1),
            total_budget=budget,
        )
        if trade:
            trades.append(trade)
            spread_note = "  *** high IV — consider debit spread ***" if trade["suggest_spread"] else ""
            print(f"       PUT  strike={trade['strike']}  exp={trade['expiration']}"
                  f"  ask=${trade['ask']:.2f}/share  IV={trade['iv']:.0f}%{spread_note}")
            print(f"            target=${trade['target_price']}  "
                  f"est. P&L/contract=${trade['profit_at_target_per_contract']:.0f}  "
                  f"ROI={trade['roi_at_target_pct']:.0f}%")
            print(f"            cost={trade['contracts']}x @ ${trade['total_cost']:.0f} total")

    print_put_portfolio_summary(trades, budget)


def _combined_rank_score(t: dict) -> float:
    """
    Blended ranking score used to order trades in the portfolio summary.

    Combines two normalised components:
      - ROI component   : min(roi, ROI_CAP_FOR_RANKING) / ROI_CAP_FOR_RANKING
      - Conviction component: put_score / MAX_PUT_SCORE

    Capping ROI prevents a single high-ROI / low-conviction outlier (e.g. a
    score-3 stock with 776% theoretical ROI) from monopolising the budget ahead
    of high-conviction names that have a more reliable directional thesis.
    """
    roi_norm = min(t["roi_at_target_pct"], ROI_CAP_FOR_RANKING) / ROI_CAP_FOR_RANKING
    conv_norm = t.get("put_score", 0) / MAX_PUT_SCORE
    return ROI_WEIGHT * roi_norm + CONVICTION_WEIGHT * conv_norm


def print_put_portfolio_summary(trades: list[dict], total_budget: float) -> None:
    """Greedy budget fill: add trades ranked by blended conviction + ROI score."""
    if not trades:
        return
    print_section(f"PUT PORTFOLIO SUMMARY  (budget ${total_budget:,.0f})")
    print(f"  Ranking: {int(CONVICTION_WEIGHT*100)}% conviction (put score) + "
          f"{int(ROI_WEIGHT*100)}% ROI (capped at {ROI_CAP_FOR_RANKING:.0f}%)\n")

    remaining = total_budget
    selected = []
    for t in sorted(trades, key=_combined_rank_score, reverse=True):
        cost = t["ask"] * 100  # cost per single contract
        if cost <= remaining:
            affordable_contracts = max(1, int(remaining // cost))
            t = dict(t)  # copy to avoid mutation
            t["contracts"] = affordable_contracts
            t["total_cost"] = round(cost * affordable_contracts, 2)
            t["rank_score"] = _combined_rank_score(t)
            selected.append(t)
            remaining -= t["total_cost"]
        if remaining < 50:  # no budget left for even one cheap contract
            break

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
    args = parser.parse_args()

    if args.symbol:
        entries = [{"symbol": args.symbol.upper(), "name": args.symbol.upper(), "tags": []}]
    else:
        if not args.watchlist.exists():
            print(f"ERROR: watchlist not found at {args.watchlist}", file=sys.stderr)
            sys.exit(1)
        entries = load_watchlist(args.watchlist)
        entries = [e for e in entries if is_us_listed(e["symbol"])]

    print(f"\nOptions Analysis Engine")
    print(f"Watchlist : {args.watchlist}")
    print(f"Symbols   : {len(entries)} (US-listed)")
    print(f"Put Budget: ${args.puts_budget:,.0f}")
    print(f"\nFetching data", end="", flush=True)

    results: list[SecurityAnalysis] = []
    failed: list[str] = []

    for entry in entries:
        sym = entry["symbol"]
        print(".", end="", flush=True)
        sec = fetch_security(sym, entry["name"], entry["tags"])
        if sec is None:
            failed.append(sym)
            continue
        score(sec)
        results.append(sec)

    print(f" done ({len(results)} fetched, {len(failed)} failed)")
    if failed:
        print(f"Failed    : {', '.join(failed)}")

    if not results:
        print("No data retrieved. Check network / symbols.")
        sys.exit(1)

    print_long_candidates(results, top_n=args.top_n)
    print_put_candidates(results, budget=args.puts_budget, top_n=args.top_n)
    print_skip_list(results)
    print(f"\n{SEPARATOR}\n")


if __name__ == "__main__":
    main()
