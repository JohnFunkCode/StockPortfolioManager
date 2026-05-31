#!/usr/bin/env python3
"""
Monitor a hypothetical WMT 120/125 bull call spread.

Position:
    Buy  to open 25x WMT 120 Call expiring 2026-06-12
    Sell to open 25x WMT 125 Call expiring 2026-06-12

Data source:
    yfinance / Yahoo Finance

State:
    Writes a local pickle file in the same directory as this script.

Educational use only. Not trading advice.
"""

from __future__ import annotations

import math
import pickle
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yfinance as yf


# ---------------------------------------------------------------------
# Trade configuration
# ---------------------------------------------------------------------

SYMBOL = "WMT"
EXPIRATION = "2026-06-12"

LONG_CALL_STRIKE = 120.0
SHORT_CALL_STRIKE = 125.0
CONTRACTS = 25
MULTIPLIER = 100

# Fixed simulated debit per share for the hypothetical trade.
# 0.58 means the spread cost $58 per contract, or $1,450 for 25 contracts.
HYPOTHETICAL_ENTRY_DEBIT = 0.58

PLANNED_LONG_CALL_MID = 0.87
PLANNED_SHORT_CALL_MID = 0.29

STATE_FILE = "wmt_bull_call_spread_state.pkl"


# ---------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------

# Bull call: want stock to rise. Below long strike means less profitable.
# Above short strike means max profit achieved.
WARNING_DISTANCE_BELOW_LONG_STRIKE = 1.00   # Alert when price is within $1 below long strike
COMFORT_DISTANCE_ABOVE_LONG_STRIKE = 2.50   # Comfortably above long strike

# Entry debit validation thresholds
TARGET_DEBIT_LOW = 0.55
TARGET_DEBIT_HIGH = 0.65
DO_NOT_CHASE_ABOVE = 0.75

PROFIT_TAKE_PERCENT = 0.60        # Alert when 60% of max profit is available.
LOSS_ALERT_PERCENT_OF_MAX = 0.50  # Alert when loss reaches 50% of max loss.

WIDE_MARKET_THRESHOLD = 0.50      # Alert if bid/ask spread on either leg > $0.50.
LOW_EXTRINSIC_THRESHOLD = 0.25    # Risk alert if long call extrinsic < $0.25 (worthless).


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------

@dataclass
class OptionQuote:
    strike: float
    bid: float
    ask: float
    last: float
    mid: float
    volume: Optional[float]
    open_interest: Optional[float]
    implied_volatility: Optional[float]
    in_the_money: Optional[bool]
    last_trade_date: Any


@dataclass
class SpreadSnapshot:
    timestamp_utc: str
    symbol: str
    expiration: str
    stock_price: float

    long_strike: float
    short_strike: float
    entry_debit: float
    opening_cash_required: float

    long_call: Dict[str, Any]
    short_call: Dict[str, Any]

    mark_to_close_mid: float
    mark_to_close_conservative: float

    current_pnl_mid: float
    current_pnl_conservative: float

    max_profit: float
    max_loss: float
    breakeven: float

    percent_max_profit_captured_mid: float
    percent_max_loss_used_mid: float

    distance_to_long_strike: float
    distance_to_short_strike: float
    intrinsic_value_long_call: float
    extrinsic_value_long_call_mid: float

    status: str
    alerts: list[str]


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------

def safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        if math.isnan(result):
            return default
        return result
    except Exception:
        return default


def safe_optional_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        result = float(value)
        if math.isnan(result):
            return None
        return result
    except Exception:
        return None


def format_money(value: float) -> str:
    """
    Format dollar amounts with the minus sign before the dollar sign.
    Example: -23.0 becomes -$23.00 instead of $-23.00.
    """
    if abs(value) < 0.005:
        value = 0.0
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def get_script_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"snapshots": []}

    try:
        with path.open("rb") as f:
            state = pickle.load(f)

        if not isinstance(state, dict):
            return {"snapshots": []}

        state.setdefault("snapshots", [])
        return state

    except Exception as exc:
        print(f"WARNING: Could not read state file {path}: {exc}")
        return {"snapshots": []}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    with path.open("wb") as f:
        pickle.dump(state, f)


def days_to_expiration(expiration: str) -> int:
    """
    Return calendar days from today to the option expiration date.
    Uses the local system date.
    """
    expiration_date = date.fromisoformat(expiration)
    return (expiration_date - date.today()).days


def play_is_over(expiration: str = EXPIRATION) -> bool:
    """
    Stop fetching current option-chain marks after expiration.
    Expired options often disappear from Yahoo, but the saved history is still useful.
    """
    return days_to_expiration(expiration) < 0


def spread_width() -> float:
    return SHORT_CALL_STRIKE - LONG_CALL_STRIKE


def opening_cash_required() -> float:
    return HYPOTHETICAL_ENTRY_DEBIT * MULTIPLIER * CONTRACTS


def max_profit_amount() -> float:
    return (spread_width() - HYPOTHETICAL_ENTRY_DEBIT) * MULTIPLIER * CONTRACTS


def max_loss_amount() -> float:
    return opening_cash_required()


def breakeven_price() -> float:
    return LONG_CALL_STRIKE + HYPOTHETICAL_ENTRY_DEBIT


def max_profit_roi() -> float:
    max_loss = max_loss_amount()
    return max_profit_amount() / max_loss if max_loss > 0 else float("nan")


def latest_stock_price(ticker: yf.Ticker) -> float:
    """
    Try several yfinance fields because availability varies.
    """
    try:
        fast_info = ticker.fast_info
        for key in ("last_price", "regular_market_price", "lastPrice"):
            try:
                price = safe_float(fast_info.get(key))
                if not math.isnan(price) and price > 0:
                    return price
            except Exception:
                pass
    except Exception:
        pass

    hist = ticker.history(period="1d", interval="1m")
    if not hist.empty and "Close" in hist.columns:
        price = safe_float(hist["Close"].dropna().iloc[-1])
        if not math.isnan(price) and price > 0:
            return price

    hist = ticker.history(period="5d", interval="1d")
    if not hist.empty and "Close" in hist.columns:
        price = safe_float(hist["Close"].dropna().iloc[-1])
        if not math.isnan(price) and price > 0:
            return price

    raise RuntimeError("Could not retrieve a usable stock price.")


def get_call_quote(calls: Any, strike: float) -> OptionQuote:
    if calls.empty:
        raise RuntimeError("Options chain returned no calls.")

    matches = calls[calls["strike"].astype(float) == float(strike)]

    if matches.empty:
        available = sorted(calls["strike"].astype(float).unique().tolist())
        nearby = [s for s in available if abs(s - strike) <= 50]
        raise RuntimeError(
            f"Could not find call strike {strike} for expiration {EXPIRATION}. "
            f"Nearby strikes: {nearby}"
        )

    row = matches.iloc[0]

    bid = safe_float(row.get("bid"), 0.0)
    ask = safe_float(row.get("ask"), 0.0)
    last = safe_float(row.get("lastPrice"), 0.0)

    if bid > 0 and ask > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
    elif last > 0:
        mid = last
    else:
        mid = 0.0

    return OptionQuote(
        strike=float(strike),
        bid=bid,
        ask=ask,
        last=last,
        mid=mid,
        volume=safe_optional_float(row.get("volume")),
        open_interest=safe_optional_float(row.get("openInterest")),
        implied_volatility=safe_optional_float(row.get("impliedVolatility")),
        in_the_money=bool(row.get("inTheMoney")) if "inTheMoney" in row else None,
        last_trade_date=row.get("lastTradeDate"),
    )


def quote_to_dict(q: OptionQuote) -> Dict[str, Any]:
    d = asdict(q)
    if q.last_trade_date is not None:
        d["last_trade_date"] = str(q.last_trade_date)
    return d


def classify_status(stock_price: float) -> str:
    """Bull call status: want price above long strike."""
    if stock_price >= SHORT_CALL_STRIKE:
        return "MAX-PROFIT ZONE"
    if stock_price >= LONG_CALL_STRIKE + COMFORT_DISTANCE_ABOVE_LONG_STRIKE:
        return "COMFORTABLE"
    if stock_price >= LONG_CALL_STRIKE:
        return "WATCH"
    if stock_price >= LONG_CALL_STRIKE - WARNING_DISTANCE_BELOW_LONG_STRIKE:
        return "WARNING"
    return "DEFENSIVE"


def build_alerts(
    stock_price: float,
    long_call: OptionQuote,
    short_call: OptionQuote,
    mark_to_close_mid: float,
    current_pnl_mid: float,
    max_profit: float,
    max_loss: float,
    entry_debit: float,
    extrinsic_long_mid: float,
) -> list[str]:
    alerts: list[str] = []

    distance_to_long = stock_price - LONG_CALL_STRIKE
    distance_to_short = SHORT_CALL_STRIKE - stock_price

    # Entry debit quality check
    if entry_debit > DO_NOT_CHASE_ABOVE:
        alerts.append(
            f"ENTRY DEBIT TOO HIGH: Entry debit of ${entry_debit:.2f} exceeds "
            f"do-not-chase threshold of ${DO_NOT_CHASE_ABOVE:.2f}."
        )
    elif entry_debit > TARGET_DEBIT_HIGH:
        alerts.append(
            f"ENTRY DEBIT ABOVE TARGET: Entry debit of ${entry_debit:.2f} is above "
            f"target range ${TARGET_DEBIT_LOW:.2f}-${TARGET_DEBIT_HIGH:.2f}."
        )

    # Price vs long strike
    if stock_price < LONG_CALL_STRIKE:
        alerts.append(
            f"BELOW LONG STRIKE: {SYMBOL} is at ${stock_price:.2f}, "
            f"${abs(distance_to_long):.2f} below the ${LONG_CALL_STRIKE:.2f} long call."
        )
    elif distance_to_long < WARNING_DISTANCE_BELOW_LONG_STRIKE:
        alerts.append(
            f"NEAR LONG STRIKE: {SYMBOL} is ${distance_to_long:.2f} above the long strike "
            f"(${LONG_CALL_STRIKE:.2f}). Limited profit potential."
        )

    # Price vs short strike (max profit zone)
    if stock_price >= SHORT_CALL_STRIKE:
        alerts.append(
            f"MAX-PROFIT ZONE: {SYMBOL} is at or above the short strike "
            f"(${SHORT_CALL_STRIKE:.2f}). Max profit is achieved."
        )

    # Profit target
    if max_profit > 0:
        captured = current_pnl_mid / max_profit
        if captured >= PROFIT_TAKE_PERCENT:
            alerts.append(
                f"PROFIT TARGET: About {captured:.0%} of max profit is available "
                f"using midpoint marks."
            )

    # Loss alert
    if max_loss > 0 and current_pnl_mid < 0:
        loss_used = abs(current_pnl_mid) / max_loss
        if loss_used >= LOSS_ALERT_PERCENT_OF_MAX:
            alerts.append(
                f"LOSS ALERT: About {loss_used:.0%} of max loss is currently marked."
            )

    # Wide markets
    long_width = long_call.ask - long_call.bid
    short_width = short_call.ask - short_call.bid

    if long_width > WIDE_MARKET_THRESHOLD:
        alerts.append(
            f"WIDE LONG-CALL MARKET: {LONG_CALL_STRIKE:.0f} call bid/ask width is ${long_width:.2f}."
        )

    if short_width > WIDE_MARKET_THRESHOLD:
        alerts.append(
            f"WIDE SHORT-CALL MARKET: {SHORT_CALL_STRIKE:.0f} call bid/ask width is ${short_width:.2f}."
        )

    # Long call extrinsic value (if sinking toward zero, spread approaches worthlessness)
    if stock_price < LONG_CALL_STRIKE and extrinsic_long_mid <= LOW_EXTRINSIC_THRESHOLD:
        alerts.append(
            f"LONG CALL LOSING VALUE: Long call is OTM and estimated extrinsic value is "
            f"only ${extrinsic_long_mid:.2f}."
        )

    # Spread near zero value means the long call is not carrying enough value.
    if mark_to_close_mid <= 0.10:
        alerts.append(
            "SPREAD NEAR ZERO: The spread is marked close to worthless. This is near the max-loss area."
        )

    return alerts


def build_historical_summary(
    state: Dict[str, Any],
    snapshot: Optional[SpreadSnapshot] = None,
) -> Dict[str, Any]:
    """
    Summarize saved snapshot history. If a fresh snapshot is supplied, include it
    in the summary before it is persisted.
    """
    snapshots = list(state.get("snapshots", []))
    if snapshot is not None:
        snapshots.append(asdict(snapshot))

    pnl_values = [
        safe_float(s.get("current_pnl_mid"))
        for s in snapshots
        if not math.isnan(safe_float(s.get("current_pnl_mid")))
    ]

    stock_values = [
        safe_float(s.get("stock_price"))
        for s in snapshots
        if not math.isnan(safe_float(s.get("stock_price")))
    ]

    close_debit_values = [
        safe_float(s.get("mark_to_close_mid"))
        for s in snapshots
        if not math.isnan(safe_float(s.get("mark_to_close_mid")))
    ]

    snapshots_by_day: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for item in snapshots:
        timestamp = str(item.get("timestamp_utc", ""))
        try:
            day = datetime.fromisoformat(timestamp).date().isoformat()
        except Exception:
            day = timestamp[:10] or "unknown"
        snapshots_by_day[day].append(item)

    daily_rows = []
    for day in sorted(snapshots_by_day):
        day_snapshots = sorted(
            snapshots_by_day[day],
            key=lambda item: str(item.get("timestamp_utc", "")),
        )
        last = day_snapshots[-1]
        max_profit = safe_float(last.get("max_profit"))
        if math.isnan(max_profit) or max_profit <= 0:
            max_profit = max_profit_amount()

        def profit_capture(item: Dict[str, Any]) -> float:
            pnl = safe_float(item.get("current_pnl_mid"))
            if math.isnan(pnl) or math.isnan(max_profit) or max_profit <= 0:
                return float("-inf")
            return pnl / max_profit

        best = max(day_snapshots, key=profit_capture)
        worst = min(day_snapshots, key=profit_capture)
        best_capture = profit_capture(best)
        last_capture = profit_capture(last)

        if best_capture >= 0.60:
            flag = ">>> 60%+ PROFIT"
        elif best_capture >= 0.50:
            flag = "!! 50%+ PROFIT"
        else:
            flag = ""

        daily_rows.append(
            {
                "date": day,
                "runs": len(day_snapshots),
                "last_time_utc": str(last.get("timestamp_utc", ""))[11:19],
                "last_stock_price": safe_float(last.get("stock_price")),
                "last_close_debit_mid": safe_float(last.get("mark_to_close_mid")),
                "last_pnl_mid": safe_float(last.get("current_pnl_mid")),
                "last_profit_capture": last_capture,
                "best_pnl_mid": safe_float(best.get("current_pnl_mid")),
                "best_profit_capture": best_capture,
                "worst_pnl_mid": safe_float(worst.get("current_pnl_mid")),
                "worst_profit_capture": profit_capture(worst),
                "best_close_debit_mid": safe_float(best.get("mark_to_close_mid")),
                "status": str(last.get("status", "")),
                "flag": flag,
            }
        )

    return {
        "run_count": len(snapshots),
        "days_to_expiration": days_to_expiration(EXPIRATION),
        "play_is_over": play_is_over(),
        "best_pnl_mid": max(pnl_values) if pnl_values else float("nan"),
        "worst_pnl_mid": min(pnl_values) if pnl_values else float("nan"),
        "highest_stock_price": max(stock_values) if stock_values else float("nan"),
        "lowest_stock_price": min(stock_values) if stock_values else float("nan"),
        "lowest_close_debit_mid": min(close_debit_values) if close_debit_values else float("nan"),
        "highest_close_debit_mid": max(close_debit_values) if close_debit_values else float("nan"),
        "daily_rows": daily_rows,
    }


def make_snapshot() -> SpreadSnapshot:
    ticker = yf.Ticker(SYMBOL)

    expirations = list(ticker.options)
    if EXPIRATION not in expirations:
        raise RuntimeError(
            f"Expiration {EXPIRATION} not found for {SYMBOL}. "
            f"Available expirations: {expirations}"
        )

    stock_price = latest_stock_price(ticker)

    chain = ticker.option_chain(EXPIRATION)
    calls = chain.calls

    long_call = get_call_quote(calls, LONG_CALL_STRIKE)
    short_call = get_call_quote(calls, SHORT_CALL_STRIKE)

    entry_debit = HYPOTHETICAL_ENTRY_DEBIT
    opening_cash = opening_cash_required()

    # A bull call spread's value is the long call value minus the short call value.
    # If closed, you sell the long call and buy back the short call.
    mark_to_close_mid = max(0.0, long_call.mid - short_call.mid)

    # Conservative estimate:
    #   Sell long at bid, buy short at ask.
    mark_to_close_conservative = max(0.0, long_call.bid - short_call.ask)

    max_profit = max_profit_amount()
    max_loss = max_loss_amount()
    breakeven = breakeven_price()

    current_pnl_mid = (mark_to_close_mid - entry_debit) * MULTIPLIER * CONTRACTS
    current_pnl_conservative = (
        mark_to_close_conservative - entry_debit
    ) * MULTIPLIER * CONTRACTS

    percent_max_profit_captured_mid = (
        current_pnl_mid / max_profit if max_profit > 0 else float("nan")
    )

    percent_max_loss_used_mid = (
        abs(current_pnl_mid) / max_loss
        if current_pnl_mid < 0 and max_loss > 0
        else 0.0
    )

    distance_to_long_strike = stock_price - LONG_CALL_STRIKE
    distance_to_short_strike = SHORT_CALL_STRIKE - stock_price

    intrinsic_long = max(0.0, stock_price - LONG_CALL_STRIKE)
    extrinsic_long_mid = max(0.0, long_call.mid - intrinsic_long)

    status = classify_status(stock_price)

    alerts = build_alerts(
        stock_price=stock_price,
        long_call=long_call,
        short_call=short_call,
        mark_to_close_mid=mark_to_close_mid,
        current_pnl_mid=current_pnl_mid,
        max_profit=max_profit,
        max_loss=max_loss,
        entry_debit=entry_debit,
        extrinsic_long_mid=extrinsic_long_mid,
    )

    return SpreadSnapshot(
        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        symbol=SYMBOL,
        expiration=EXPIRATION,
        stock_price=stock_price,

        long_strike=LONG_CALL_STRIKE,
        short_strike=SHORT_CALL_STRIKE,
        entry_debit=entry_debit,
        opening_cash_required=opening_cash,

        long_call=quote_to_dict(long_call),
        short_call=quote_to_dict(short_call),

        mark_to_close_mid=mark_to_close_mid,
        mark_to_close_conservative=mark_to_close_conservative,

        current_pnl_mid=current_pnl_mid,
        current_pnl_conservative=current_pnl_conservative,

        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=breakeven,

        percent_max_profit_captured_mid=percent_max_profit_captured_mid,
        percent_max_loss_used_mid=percent_max_loss_used_mid,

        distance_to_long_strike=distance_to_long_strike,
        distance_to_short_strike=distance_to_short_strike,
        intrinsic_value_long_call=intrinsic_long,
        extrinsic_value_long_call_mid=extrinsic_long_mid,

        status=status,
        alerts=alerts,
    )


def build_comparison_table(snapshot: SpreadSnapshot) -> Dict[str, Any]:
    """
    Compare bull call spread P/L vs. buy-and-hold shares at various price targets.
    """
    stock_entry = snapshot.stock_price
    spread_entry = snapshot.entry_debit
    spread_capital = spread_entry * MULTIPLIER * CONTRACTS

    # How many shares could you buy with the same capital?
    shares_bought = spread_capital / stock_entry

    # Define price targets: current, long strike, breakeven, short strike, and above
    price_targets = [
        stock_entry,
        LONG_CALL_STRIKE - 2,
        LONG_CALL_STRIKE,
        snapshot.breakeven,
        SHORT_CALL_STRIKE,
        SHORT_CALL_STRIKE + 2,
    ]

    rows = []
    for target_price in price_targets:
        # Bull call spread P/L
        if target_price < LONG_CALL_STRIKE:
            spread_pnl = -spread_capital
        elif target_price >= SHORT_CALL_STRIKE:
            spread_pnl = snapshot.max_profit
        else:
            # Between long and short strikes: long call ITM, short call OTM
            spread_pnl = ((target_price - LONG_CALL_STRIKE) * MULTIPLIER * CONTRACTS) - spread_capital

        # Buy-and-hold stock P/L
        stock_pnl = shares_bought * (target_price - stock_entry)

        # Compare: which is better? Calculate difference and winner
        pnl_diff = spread_pnl - stock_pnl
        winner = "SPREAD" if spread_pnl > stock_pnl else ("STOCK" if stock_pnl > spread_pnl else "TIE")

        rows.append({
            "target_price": target_price,
            "spread_pnl": spread_pnl,
            "stock_pnl": stock_pnl,
            "pnl_diff": pnl_diff,
            "winner": winner,
        })

    return {
        "shares_bought": shares_bought,
        "spread_capital": spread_capital,
        "stock_entry": stock_entry,
        "rows": rows,
    }


def print_trade_setup() -> None:
    print("Trade setup")
    print("-" * 72)
    print("Strategy:            Bull call spread, also called a long call vertical")
    print(f"Action:              Buy {CONTRACTS} {SYMBOL} {LONG_CALL_STRIKE:.0f}C and sell {CONTRACTS} {SYMBOL} {SHORT_CALL_STRIKE:.0f}C")
    print(f"Expiration:          {EXPIRATION}")
    print(f"Planned leg mids:    Buy ${PLANNED_LONG_CALL_MID:.2f}, sell ${PLANNED_SHORT_CALL_MID:.2f}")
    print(f"Net debit paid:      ${HYPOTHETICAL_ENTRY_DEBIT:.2f} per share / {format_money(opening_cash_required())} total")
    print(f"Spread width:        ${spread_width():.2f} between the two strikes")
    print(f"Max loss:            {format_money(max_loss_amount())}, the debit paid if the spread expires worthless")
    print(f"Max profit:          {format_money(max_profit_amount())}, if {SYMBOL} closes at or above ${SHORT_CALL_STRIKE:.2f}")
    print(f"Breakeven:           ${breakeven_price():.2f}, the long strike plus the ${HYPOTHETICAL_ENTRY_DEBIT:.2f} debit")
    print(f"Max profit ROI:      {max_profit_roi():.1%} on the {format_money(max_loss_amount())} at risk")
    print()
    print("Why the trade is structured this way:")
    print(f"  - Buying the ${LONG_CALL_STRIKE:.0f} call gives upside exposure if {SYMBOL} rises.")
    print(f"  - Selling the ${SHORT_CALL_STRIKE:.0f} call lowers the cost, but caps profit above ${SHORT_CALL_STRIKE:.0f}.")
    print("  - This is a defined-risk trade: the most you can lose is the debit paid.")
    print()


def print_payoff_guide() -> None:
    print("How this trade wins or loses")
    print("-" * 72)
    print(f"If {SYMBOL} closes below ${LONG_CALL_STRIKE:.2f}: both calls expire worthless; loss is {format_money(max_loss_amount())}.")
    print(f"If {SYMBOL} closes at ${breakeven_price():.2f}: the spread value equals the debit; P/L is about $0.")
    print(f"If {SYMBOL} closes between ${LONG_CALL_STRIKE:.2f} and ${SHORT_CALL_STRIKE:.2f}: P/L rises dollar-for-dollar after breakeven.")
    print(f"If {SYMBOL} closes at or above ${SHORT_CALL_STRIKE:.2f}: profit is capped at {format_money(max_profit_amount())}.")
    print()
    print("Expiration scenario table")
    print("-" * 72)
    print(f"{'WMT close':<22} {'Outcome':<24} {'P/L':>12}")
    print("-" * 72)
    print(f"{'Below $120.00':<22} {'Spread expires worthless':<24} {format_money(-max_loss_amount()):>12}")
    print(f"{'$120.58 breakeven':<22} {'No gain or loss':<24} {format_money(0):>12}")
    print(f"{'$125.00 or higher':<22} {'Maximum profit':<24} {format_money(max_profit_amount()):>12}")
    print()


def print_probability_notes() -> None:
    print("Probability notes")
    print("-" * 72)
    print("These are rough setup estimates, not live model outputs from this script.")
    print(f"Chance {SYMBOL} closes above breakeven (${breakeven_price():.2f}): about 50-55%.")
    print(f"Chance {SYMBOL} closes at or above ${SHORT_CALL_STRIKE:.2f}: about 35-40%.")
    print("Chance of max profit: about 35-40%.")
    print("Chance of total loss: about 45-50%.")
    print()


def print_live_option_quotes(snapshot: SpreadSnapshot) -> None:
    print("Current option quotes")
    print("-" * 72)
    lc = snapshot.long_call
    sc = snapshot.short_call
    print("Bid is what buyers currently offer; ask is what sellers want; mid is halfway between them.")
    print(
        f"{snapshot.long_strike:.0f}C long leg:       bid ${lc['bid']:.2f} / ask ${lc['ask']:.2f} / "
        f"mid ${lc['mid']:.2f} / last ${lc['last']:.2f}"
    )
    print(
        f"{snapshot.short_strike:.0f}C short leg:      bid ${sc['bid']:.2f} / ask ${sc['ask']:.2f} / "
        f"mid ${sc['mid']:.2f} / last ${sc['last']:.2f}"
    )
    print(
        f"Planned entry mids were ${PLANNED_LONG_CALL_MID:.2f} for the long call and "
        f"${PLANNED_SHORT_CALL_MID:.2f} for the short call."
    )
    print()


def print_current_value(snapshot: SpreadSnapshot) -> None:
    print("Current spread value and P/L")
    print("-" * 72)
    print("For a bull call spread, current value is long-call value minus short-call value.")
    print(f"Estimated spread value:     ${snapshot.mark_to_close_mid:.2f} per share")
    print(
        f"Estimated value if closed:  "
        f"{format_money(snapshot.mark_to_close_mid * MULTIPLIER * CONTRACTS)} total"
    )
    print(f"Conservative value:         ${snapshot.mark_to_close_conservative:.2f} per share")
    print(
        f"Conservative close value:   "
        f"{format_money(snapshot.mark_to_close_conservative * MULTIPLIER * CONTRACTS)} total"
    )
    print(f"Estimated current P/L:      {format_money(snapshot.current_pnl_mid)}")
    print(f"Conservative current P/L:   {format_money(snapshot.current_pnl_conservative)}")

    if snapshot.max_profit > 0:
        print(f"Max profit captured:        {snapshot.percent_max_profit_captured_mid:.1%}")

    if snapshot.current_pnl_mid < 0:
        print(f"Max loss currently used:    {snapshot.percent_max_loss_used_mid:.1%}")

    print()


def print_risk_checks(snapshot: SpreadSnapshot) -> None:
    print("Risk checks")
    print("-" * 72)
    print(f"Current {SYMBOL} price:       ${snapshot.stock_price:,.2f}")
    print(f"Distance from long strike:  {format_money(snapshot.distance_to_long_strike)}")
    print(f"Distance from short strike: {format_money(snapshot.distance_to_short_strike)}")
    print(f"Long-call intrinsic value:  ${snapshot.intrinsic_value_long_call:,.2f}")
    print(f"Long-call time value est.:  ${snapshot.extrinsic_value_long_call_mid:,.2f}")
    print("Intrinsic value is what the option is worth if exercised now; time value is the extra market premium.")
    print()


def print_comparison(snapshot: SpreadSnapshot) -> None:
    print("Comparison: spread vs. buying WMT stock")
    print("-" * 72)
    comparison = build_comparison_table(snapshot)
    print(f"Capital in spread:          {format_money(comparison['spread_capital'])}")
    print(f"Current stock price:        ${comparison['stock_entry']:,.2f}")
    print(f"Same cash could buy:        {comparison['shares_bought']:.2f} shares")
    print()
    print("Profit/loss at selected expiration prices:")
    print("-" * 112)
    print(f"{'WMT Price':>12} {'Spread P/L':>15} {'Stock P/L':>15} {'Difference':>15} {'Winner':>12}")
    print("-" * 112)

    for row in comparison["rows"]:
        print(
            f"${row['target_price']:>11,.2f} "
            f"{format_money(row['spread_pnl']):>15} "
            f"{format_money(row['stock_pnl']):>15} "
            f"{format_money(row['pnl_diff']):>15} "
            f"{row['winner']:>12}"
        )

    print()
    print("Beginner note:")
    print("  - The spread uses less cash and has defined risk, but upside stops at the short strike.")
    print("  - Stock has uncapped upside, but it requires more cash for similar exposure.")
    print()


def print_historical_summary(historical_summary: Dict[str, Any]) -> None:
    print("Historical summary")
    print("-" * 72)
    print("These are local snapshots from previous script runs, not brokerage records.")
    print(f"Runs recorded:              {historical_summary['run_count']}")
    print(f"Days to expiration:         {historical_summary['days_to_expiration']}")
    print(f"Best P/L recorded:          {format_money(historical_summary['best_pnl_mid'])}")
    print(f"Worst P/L recorded:         {format_money(historical_summary['worst_pnl_mid'])}")
    print(f"Highest stock price seen:   ${historical_summary['highest_stock_price']:,.2f}")
    print(f"Lowest stock price seen:    ${historical_summary['lowest_stock_price']:,.2f}")
    print(f"Lowest spread value seen:   ${historical_summary['lowest_close_debit_mid']:,.2f}")
    print(f"Highest spread value seen:  ${historical_summary['highest_close_debit_mid']:,.2f}")
    print()

    daily_rows = historical_summary.get("daily_rows", [])
    if daily_rows:
        print("Historical profit table")
        print("-" * 112)
        print(
            f"{'Date':<12} {'Runs':>4} {'Stock':>9} {'Value':>8} "
            f"{'Last P/L':>11} {'Last %':>8} {'Best P/L':>11} {'Best %':>8}  Flag"
        )
        print("-" * 112)

        for row in daily_rows:
            flag = row.get("flag", "")
            print(
                f"{row['date']:<12} "
                f"{row['runs']:>4} "
                f"${row['last_stock_price']:>8.2f} "
                f"${row['last_close_debit_mid']:>7.2f} "
                f"{format_money(row['last_pnl_mid']):>11} "
                f"{row['last_profit_capture']:>7.1%} "
                f"{format_money(row['best_pnl_mid']):>11} "
                f"{row['best_profit_capture']:>7.1%}  "
                f"{flag}"
            )

        print()
        print("Flag legend: !! = best daily profit exceeded 50%; >>> = best daily profit exceeded 60%.")
        print()


def status_interpretation(status: str) -> str:
    if status == "MAX-PROFIT ZONE":
        return "WMT is at or above the short strike; the spread is in its maximum-profit area."
    if status == "COMFORTABLE":
        return "WMT is above the long strike with some cushion, but it still needs to hold up."
    if status == "WATCH":
        return "WMT is above the long strike, but not yet near the max-profit strike."
    if status == "WARNING":
        return "WMT is close to the long strike; a small drop could leave the spread losing money."
    return "WMT is below the long strike; the spread needs a recovery before expiration."


def print_snapshot(
    snapshot: SpreadSnapshot,
    prior: Optional[Dict[str, Any]],
    historical_summary: Dict[str, Any],
) -> None:
    print()
    print("=" * 72)
    print(f"{snapshot.symbol} BULL CALL SPREAD MONITOR")
    print("=" * 72)
    print(f"Timestamp UTC:       {snapshot.timestamp_utc}")
    print(f"Expiration:          {snapshot.expiration}")
    print(f"Stock price:         ${snapshot.stock_price:,.2f}")
    print(f"Status:              {snapshot.status}")
    print(f"Meaning:             {status_interpretation(snapshot.status)}")
    print()

    print_trade_setup()
    print_payoff_guide()
    print_probability_notes()
    print_live_option_quotes(snapshot)
    print_current_value(snapshot)
    print_risk_checks(snapshot)
    print_comparison(snapshot)
    print_historical_summary(historical_summary)

    if prior:
        prior_price = safe_float(prior.get("stock_price"))
        prior_pnl = safe_float(prior.get("current_pnl_mid"))

        if not math.isnan(prior_price):
            print("Change since previous run")
            print("-" * 72)
            print(f"Stock change:              {format_money(snapshot.stock_price - prior_price)}")

            if not math.isnan(prior_pnl):
                print(f"P/L change, midpoint:      {format_money(snapshot.current_pnl_mid - prior_pnl)}")

            print()

    print("Alerts")
    print("-" * 72)
    if snapshot.alerts:
        for alert in snapshot.alerts:
            print(f"!! {alert}")
    else:
        print("No major alerts from the configured rules.")
    print()

    print("=" * 72)
    print()


def print_completed_play_report(historical_summary: Dict[str, Any], state_path: Path) -> None:
    print()
    print("=" * 72)
    print(f"{SYMBOL} BULL CALL SPREAD MONITOR")
    print("=" * 72)
    print(f"Expiration:          {EXPIRATION}")
    print("Play status:         OVER")
    print()
    print("Current calculations skipped.")
    print(
        f"The {EXPIRATION} option chain has expired, so this run reports the "
        "trade setup and saved historical findings only."
    )
    print()

    print_trade_setup()
    print_payoff_guide()
    print_probability_notes()
    print_historical_summary(historical_summary)
    print(f"Historical state:    {state_path}")
    print("=" * 72)
    print()


def main() -> int:
    state_path = get_script_dir() / STATE_FILE
    state = load_state(state_path)

    if play_is_over():
        historical_summary = build_historical_summary(state)
        print_completed_play_report(historical_summary, state_path)
        return 0

    try:
        snapshot = make_snapshot()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    prior = state["snapshots"][-1] if state["snapshots"] else None
    historical_summary = build_historical_summary(state, snapshot)

    print_snapshot(snapshot, prior, historical_summary)

    state["snapshots"].append(asdict(snapshot))

    # Keep the file small: retain last 250 runs.
    state["snapshots"] = state["snapshots"][-250:]

    save_state(state_path, state)

    print(f"Saved snapshot to: {state_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
