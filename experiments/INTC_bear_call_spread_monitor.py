#!/usr/bin/env python3
"""
Monitor a hypothetical INTC 125/135 bear call spread.

Position:
    Sell to open 1x INTC 125 Call expiring 2026-05-29
    Buy  to open 1x INTC 135 Call expiring 2026-05-29

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

SYMBOL = "INTC"
EXPIRATION = "2026-05-29"

SHORT_CALL_STRIKE = 125.0
LONG_CALL_STRIKE = 135.0
CONTRACTS = 1
MULTIPLIER = 100

# Fixed simulated credit per share for the hypothetical trade.
# Example: 2.25 means the spread generated $225 when opened.
HYPOTHETICAL_ENTRY_CREDIT = 2.25

STATE_FILE = "intc_bear_call_spread_state.pkl"


# ---------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------

WARNING_DISTANCE_TO_SHORT_STRIKE = 2.00
DANGER_DISTANCE_TO_SHORT_STRIKE = 0.00

PROFIT_TAKE_PERCENT = 0.60        # Alert when 60% of max profit is available.
LOSS_ALERT_PERCENT_OF_MAX = 0.50  # Alert when loss reaches 50% of max loss.

WIDE_MARKET_THRESHOLD = 0.50      # Alert if bid/ask spread on either leg > $0.50.
LOW_EXTRINSIC_THRESHOLD = 0.25    # Assignment-risk alert if short call extrinsic < $0.25.


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

    short_strike: float
    long_strike: float
    entry_credit: float
    opening_cash_generated: float

    short_call: Dict[str, Any]
    long_call: Dict[str, Any]

    mark_to_close_mid: float
    mark_to_close_conservative: float

    current_pnl_mid: float
    current_pnl_conservative: float

    max_profit: float
    max_loss: float
    breakeven: float

    percent_max_profit_captured_mid: float
    percent_max_loss_used_mid: float

    distance_to_short_strike: float
    intrinsic_value_short_call: float
    extrinsic_value_short_call_mid: float

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
        nearby = [s for s in available if abs(s - strike) <= 10]
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
    if stock_price < SHORT_CALL_STRIKE - 5:
        return "COMFORTABLE"
    if stock_price < SHORT_CALL_STRIKE - WARNING_DISTANCE_TO_SHORT_STRIKE:
        return "WATCH"
    if stock_price < SHORT_CALL_STRIKE:
        return "WARNING"
    if stock_price < LONG_CALL_STRIKE:
        return "DEFENSIVE"
    return "MAX-LOSS ZONE"


def build_alerts(
    stock_price: float,
    short_call: OptionQuote,
    long_call: OptionQuote,
    mark_to_close_mid: float,
    current_pnl_mid: float,
    max_profit: float,
    max_loss: float,
    extrinsic_short_mid: float,
) -> list[str]:
    alerts: list[str] = []

    distance = SHORT_CALL_STRIKE - stock_price

    if distance <= DANGER_DISTANCE_TO_SHORT_STRIKE:
        alerts.append(
            f"SHORT STRIKE BREACHED: INTC is at ${stock_price:.2f}, "
            f"above/equal to the ${SHORT_CALL_STRIKE:.2f} short call."
        )
    elif distance <= WARNING_DISTANCE_TO_SHORT_STRIKE:
        alerts.append(
            f"WARNING ZONE: INTC is ${distance:.2f} below the short strike."
        )

    if stock_price >= LONG_CALL_STRIKE:
        alerts.append(
            f"MAX-LOSS ZONE: INTC is above/equal to the long strike "
            f"(${LONG_CALL_STRIKE:.2f})."
        )

    if max_profit > 0:
        captured = current_pnl_mid / max_profit
        if captured >= PROFIT_TAKE_PERCENT:
            alerts.append(
                f"PROFIT TARGET: About {captured:.0%} of max profit is available "
                f"using midpoint marks."
            )

    if max_loss > 0 and current_pnl_mid < 0:
        loss_used = abs(current_pnl_mid) / max_loss
        if loss_used >= LOSS_ALERT_PERCENT_OF_MAX:
            alerts.append(
                f"LOSS ALERT: About {loss_used:.0%} of max loss is currently marked."
            )

    short_width = short_call.ask - short_call.bid
    long_width = long_call.ask - long_call.bid

    if short_width > WIDE_MARKET_THRESHOLD:
        alerts.append(
            f"WIDE SHORT-CALL MARKET: 125 call bid/ask width is ${short_width:.2f}."
        )

    if long_width > WIDE_MARKET_THRESHOLD:
        alerts.append(
            f"WIDE LONG-CALL MARKET: 135 call bid/ask width is ${long_width:.2f}."
        )

    if stock_price > SHORT_CALL_STRIKE and extrinsic_short_mid <= LOW_EXTRINSIC_THRESHOLD:
        alerts.append(
            f"ASSIGNMENT RISK: Short call is ITM and estimated extrinsic value is "
            f"only ${extrinsic_short_mid:.2f}."
        )

    if mark_to_close_mid >= (LONG_CALL_STRIKE - SHORT_CALL_STRIKE) * 0.90:
        alerts.append(
            "SPREAD NEAR MAX VALUE: The spread is marked close to full width."
        )

    return alerts


def build_historical_summary(
    state: Dict[str, Any],
    snapshot: SpreadSnapshot,
) -> Dict[str, Any]:
    """
    Summarize prior and current snapshot history from the local pickle state.
    """
    snapshots = list(state.get("snapshots", []))
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
            max_profit = safe_float(snapshot.max_profit)

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
        "days_to_expiration": days_to_expiration(snapshot.expiration),
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

    short_call = get_call_quote(calls, SHORT_CALL_STRIKE)
    long_call = get_call_quote(calls, LONG_CALL_STRIKE)

    spread_width = LONG_CALL_STRIKE - SHORT_CALL_STRIKE
    entry_credit = HYPOTHETICAL_ENTRY_CREDIT
    opening_cash_generated = entry_credit * MULTIPLIER * CONTRACTS

    # To close a bear call spread:
    #   Buy back the short call.
    #   Sell the long call.
    mark_to_close_mid = max(0.0, short_call.mid - long_call.mid)

    # Conservative estimate:
    #   Buy short at ask, sell long at bid.
    mark_to_close_conservative = max(0.0, short_call.ask - long_call.bid)

    max_profit = entry_credit * MULTIPLIER * CONTRACTS
    max_loss = (spread_width - entry_credit) * MULTIPLIER * CONTRACTS
    breakeven = SHORT_CALL_STRIKE + entry_credit

    current_pnl_mid = (entry_credit - mark_to_close_mid) * MULTIPLIER * CONTRACTS
    current_pnl_conservative = (
        entry_credit - mark_to_close_conservative
    ) * MULTIPLIER * CONTRACTS

    percent_max_profit_captured_mid = (
        current_pnl_mid / max_profit if max_profit > 0 else float("nan")
    )

    percent_max_loss_used_mid = (
        abs(current_pnl_mid) / max_loss
        if current_pnl_mid < 0 and max_loss > 0
        else 0.0
    )

    distance_to_short_strike = SHORT_CALL_STRIKE - stock_price

    intrinsic_short = max(0.0, stock_price - SHORT_CALL_STRIKE)
    extrinsic_short_mid = max(0.0, short_call.mid - intrinsic_short)

    status = classify_status(stock_price)

    alerts = build_alerts(
        stock_price=stock_price,
        short_call=short_call,
        long_call=long_call,
        mark_to_close_mid=mark_to_close_mid,
        current_pnl_mid=current_pnl_mid,
        max_profit=max_profit,
        max_loss=max_loss,
        extrinsic_short_mid=extrinsic_short_mid,
    )

    return SpreadSnapshot(
        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        symbol=SYMBOL,
        expiration=EXPIRATION,
        stock_price=stock_price,

        short_strike=SHORT_CALL_STRIKE,
        long_strike=LONG_CALL_STRIKE,
        entry_credit=entry_credit,
        opening_cash_generated=opening_cash_generated,

        short_call=quote_to_dict(short_call),
        long_call=quote_to_dict(long_call),

        mark_to_close_mid=mark_to_close_mid,
        mark_to_close_conservative=mark_to_close_conservative,

        current_pnl_mid=current_pnl_mid,
        current_pnl_conservative=current_pnl_conservative,

        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=breakeven,

        percent_max_profit_captured_mid=percent_max_profit_captured_mid,
        percent_max_loss_used_mid=percent_max_loss_used_mid,

        distance_to_short_strike=distance_to_short_strike,
        intrinsic_value_short_call=intrinsic_short,
        extrinsic_value_short_call_mid=extrinsic_short_mid,

        status=status,
        alerts=alerts,
    )


def print_snapshot(
    snapshot: SpreadSnapshot,
    prior: Optional[Dict[str, Any]],
    historical_summary: Dict[str, Any],
) -> None:
    print()
    print("=" * 72)
    print(f"{snapshot.symbol} BEAR CALL SPREAD MONITOR")
    print("=" * 72)

    print(f"Timestamp UTC:       {snapshot.timestamp_utc}")
    print(f"Expiration:          {snapshot.expiration}")
    print(f"Stock price:         ${snapshot.stock_price:,.2f}")
    print(f"Status:              {snapshot.status}")
    print()

    print("Order setup")
    print("-" * 72)
    print(f"Strategy:            Bear call spread / short call vertical")
    print(f"Action:              Sell 1 {snapshot.symbol} {SHORT_CALL_STRIKE:.0f}C, buy 1 {snapshot.symbol} {LONG_CALL_STRIKE:.0f}C")
    print(f"Expiration:          {snapshot.expiration}")
    print(f"Spread width:        ${LONG_CALL_STRIKE - SHORT_CALL_STRIKE:.2f}")
    print(f"Initial credit:      ${snapshot.entry_credit:.2f} per share / {format_money(snapshot.opening_cash_generated)} total")
    print(f"Basic thesis:        Profit if {snapshot.symbol} stays below ${SHORT_CALL_STRIKE:.2f} through expiration")
    print()

    print("Plain-English summary")
    print("-" * 72)
    print(
        f"You collected {format_money(snapshot.opening_cash_generated)} "
        f"to open this hypothetical spread."
    )
    print(
        f"To close it now, the midpoint estimate is "
        f"{format_money(snapshot.mark_to_close_mid * MULTIPLIER * CONTRACTS)}."
    )
    print(
        f"Estimated P/L is {format_money(snapshot.current_pnl_mid)}; "
        f"conservative P/L is {format_money(snapshot.current_pnl_conservative)}."
    )
    print(
        f"INTC is ${snapshot.distance_to_short_strike:,.2f} below the short strike "
        f"(${snapshot.short_strike:.2f})."
    )
    print()

    print("Position")
    print("-" * 72)
    print(f"Short call:          {CONTRACTS}x {snapshot.symbol} {SHORT_CALL_STRIKE:.0f}C")
    print(f"Long call:           {CONTRACTS}x {snapshot.symbol} {LONG_CALL_STRIKE:.0f}C")
    print(f"Entry credit:        ${snapshot.entry_credit:.2f} per share")
    print(f"Opening cash:        {format_money(snapshot.opening_cash_generated)}")
    print(f"Max profit:          {format_money(snapshot.max_profit)}")
    print(f"Max loss:            {format_money(snapshot.max_loss)}")
    print(f"Breakeven:           ${snapshot.breakeven:,.2f}")
    print()

    print("Current option quotes, for reference")
    print("-" * 72)
    sc = snapshot.short_call
    lc = snapshot.long_call

    print(
        f"125C short leg:      bid ${sc['bid']:.2f} / ask ${sc['ask']:.2f} / "
        f"mid ${sc['mid']:.2f} / last ${sc['last']:.2f}"
    )
    print(
        f"135C long leg:       bid ${lc['bid']:.2f} / ask ${lc['ask']:.2f} / "
        f"mid ${lc['mid']:.2f} / last ${lc['last']:.2f}"
    )
    print(
        "Learner note: the spread close cost is roughly short-call value minus "
        "long-call value."
    )
    print()

    print("Current spread value and P/L")
    print("-" * 72)
    print(f"Estimated cost to close:    ${snapshot.mark_to_close_mid:.2f} per share")
    print(
        f"Estimated close cost:       "
        f"{format_money(snapshot.mark_to_close_mid * MULTIPLIER * CONTRACTS)} total"
    )
    print(f"Conservative close cost:    ${snapshot.mark_to_close_conservative:.2f} per share")
    print(
        f"Conservative close cost:    "
        f"{format_money(snapshot.mark_to_close_conservative * MULTIPLIER * CONTRACTS)} total"
    )
    print(f"Estimated current P/L:      {format_money(snapshot.current_pnl_mid)}")
    print(f"Conservative current P/L:   {format_money(snapshot.current_pnl_conservative)}")

    if snapshot.max_profit > 0:
        print(
            f"Max profit captured:        "
            f"{snapshot.percent_max_profit_captured_mid:.1%}"
        )

    if snapshot.current_pnl_mid < 0:
        print(
            f"Max loss currently used:    "
            f"{snapshot.percent_max_loss_used_mid:.1%}"
        )

    print()

    profit_target_50 = snapshot.entry_credit * (1 - 0.50)
    profit_target_80 = snapshot.entry_credit * (1 - 0.80)

    print("Learning milestones")
    print("-" * 72)
    print(f"50% profit target:          close cost near ${profit_target_50:.2f} per share")
    print(f"80% profit target:          close cost near ${profit_target_80:.2f} per share")
    print(
        f"Warning zone starts near:   "
        f"${SHORT_CALL_STRIKE - WARNING_DISTANCE_TO_SHORT_STRIKE:.2f} stock price"
    )
    print(f"Defensive zone starts at:   ${SHORT_CALL_STRIKE:.2f} stock price")
    print()

    print("Risk checks")
    print("-" * 72)
    print(f"Distance to short strike:   ${snapshot.distance_to_short_strike:,.2f}")
    print(f"Short-call intrinsic:       ${snapshot.intrinsic_value_short_call:,.2f}")
    print(f"Short-call extrinsic est.:  ${snapshot.extrinsic_value_short_call_mid:,.2f}")
    print()

    print("Historical summary")
    print("-" * 72)
    print(f"Runs recorded:              {historical_summary['run_count']}")
    print(f"Days to expiration:         {historical_summary['days_to_expiration']}")
    print(f"Best midpoint P/L seen:     {format_money(historical_summary['best_pnl_mid'])}")
    print(f"Worst midpoint P/L seen:    {format_money(historical_summary['worst_pnl_mid'])}")
    print(f"Highest stock price seen:   ${historical_summary['highest_stock_price']:,.2f}")
    print(f"Lowest stock price seen:    ${historical_summary['lowest_stock_price']:,.2f}")
    print(f"Lowest close debit seen:    ${historical_summary['lowest_close_debit_mid']:,.2f}")
    print(f"Highest close debit seen:   ${historical_summary['highest_close_debit_mid']:,.2f}")
    print()

    daily_rows = historical_summary.get("daily_rows", [])
    if daily_rows:
        print("Historical profit table")
        print("-" * 112)
        print(
            f"{'Date':<12} {'Runs':>4} {'Stock':>9} {'Close':>8} "
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

    if prior:
        prior_price = safe_float(prior.get("stock_price"))
        prior_pnl = safe_float(prior.get("current_pnl_mid"))

        if not math.isnan(prior_price):
            print("Change since previous run")
            print("-" * 72)
            print(
                f"Stock change:              "
                f"{format_money(snapshot.stock_price - prior_price)}"
            )

            if not math.isnan(prior_pnl):
                print(
                    f"P/L change, midpoint:      "
                    f"{format_money(snapshot.current_pnl_mid - prior_pnl)}"
                )

            print()

    print("Alerts")
    print("-" * 72)

    if snapshot.alerts:
        for alert in snapshot.alerts:
            print(f"!! {alert}")
    else:
        print("No major alerts from the configured rules.")

    print()
    print("Suggested interpretation")
    print("-" * 72)

    if snapshot.status == "COMFORTABLE":
        print("Stock is comfortably below the short strike. Main question: take profit or hold.")
    elif snapshot.status == "WATCH":
        print("Stock is below the short strike but close enough to monitor actively.")
    elif snapshot.status == "WARNING":
        print("Stock is near the short strike. Gamma and assignment risk are becoming more relevant.")
    elif snapshot.status == "DEFENSIVE":
        print("Short strike is breached. Decide whether to close, roll, or accept defined loss.")
    else:
        print("Stock is at or above the long strike. The spread is near its max-loss region.")

    print("=" * 72)
    print()


def main() -> int:
    state_path = get_script_dir() / STATE_FILE
    state = load_state(state_path)

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
