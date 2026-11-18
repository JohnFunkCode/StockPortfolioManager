"""A book's value at a close, its time-weighted return and its drawdown
(#431 phase 3, plan step 5.1).

Pure functions, no I/O. The daily job gathers the inputs (`PerformanceService`)
and stores what `book_value` returns as one `book_value_snapshots` row a book
and session. Step 5.3 reads the stored rows back through
`time_weighted_return` and `max_drawdown`.

The rules:

- **A book's value is its cash plus its holdings at the session's close.** The
  cash is the ledger's balance counting every entry dated on or before the
  session (`analytics.cash`). A holding is its share count times the close of
  the newest closed daily bar on or before the session, in USD.
- **A book with no opening balance on or before the session has no value**
  (plan decision 9: real-book history starts at the opening-balance date).
- **A figure that can't be complete is None, never a guess.** A holding with
  no close at all, or a non-USD holding with no FX rate, leaves the holdings
  and the book value None, and is counted. A missing rate is never 1.0. A
  holding priced from an earlier session's close is valued, and counted as
  `stale_count`.
- **A flow is value that came in or went out without being earned.** The
  opening balance, deposits and withdrawals, and a holding that arrived
  without cash (any lot whose `acquisition_type` isn't `BUY`, a seeded paper
  lot included), valued at the session's close. A buy or a sale only moves
  value between cash and holdings, so it is not a flow. A row stores what the
  ledger counted as contributed (`contributed`), and its cash flow is the
  change since the previous row: a deposit typed in after a row was taken,
  but dated before it, is then a flow of the next row and not a gain. A lot
  that arrived without cash counts against the first row after the previous
  row's session, so one dated on a weekend lands on the Monday. With no
  previous row only a lot dated on the session counts, which is harmless:
  the first row is the base and its flow is never used.
- **A late change is placed on its real date by restating the rows from
  it** (step 5.2, decisions 85 and 88 as revised). A write dated on or before
  the book's newest row marks the book, and `rebuild_snapshots` recomputes
  each session from the book's whole history: the lots held at that
  session's close (`held_lots`), the ledger as dated, and each held symbol's
  close as of that session (`close_as_of`). Rebuilt in order, each row's
  flow is measured from the rebuilt row before it, so a deposit or a lot that
  arrived without cash counts on the session it is dated on, however late it
  was typed in. Until the rebuild has run, the row after a late lot that cost
  no cash still reads it as a gain: its value is in that row's holdings and
  in no flow.
- **The return is time-weighted, with a flow invested from the start of its
  session**: a session's return is `value / (previous value + flow) - 1`, and
  the sessions chain. Deposits and withdrawals then don't read as gains or
  losses. The first valued session is the base and has no return of its own.
- **A drawdown is measured on the return index, not on the value**, so a
  withdrawal is not a drawdown.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from quantcore.analytics import cash as cash_math
from quantcore.analytics.fx import as_date, needs_rate, normalize_currency, prior_close_rate

BUY = "BUY"
ZERO = Decimal("0")
ONE = Decimal("1")
# Returns and drawdowns are reported to this many places of a fraction.
RETURN_QUANTUM = Decimal("0.00000001")

# How far back a holding's close may come from (#431 step 5.1). A week
# covers a holiday weekend. Past that the symbol has stopped trading, and the
# holding is unpriced rather than valued at an old close.
CLOSE_LOOKBACK_DAYS = 7

# Ledger kinds that are flows: value in or out that the book didn't earn.
FLOW_KINDS = (cash_math.OPENING_BALANCE, cash_math.DEPOSIT, cash_math.WITHDRAWAL)


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _lot_day(lot: Mapping[str, Any]) -> Optional[datetime.date]:
    """The date a lot entered the book: its trade date, else its purchase date."""
    return as_date(lot.get("trade_date")) or as_date(lot.get("purchase_date"))


def _in_window(day: Optional[datetime.date], after: Optional[datetime.date],
               session: datetime.date) -> bool:
    """True when `day` is after `after` (when given) and on or before `session`.
    With no `after`, only the session itself counts."""
    if day is None or day > session:
        return False
    return day > after if after is not None else day == session


def _cash_at(entries: Sequence[Mapping[str, Any]], session: datetime.date
             ) -> Optional[Dict[str, Any]]:
    """The ledger as it stood at the close of `session`, or None when it had
    no opening balance by then."""
    iso = session.isoformat()
    ledger = cash_math.cash_ledger(e for e in entries if e["entry_date"] <= iso)
    return ledger if ledger["opening_date"] is not None else None


def _holding_value(
    lot: Mapping[str, Any],
    closes: Mapping[str, Tuple[Decimal, datetime.date]],
    rates: Mapping[str, Decimal],
) -> Tuple[Optional[Decimal], str]:
    """One lot's value in USD and what kind of price it had: `fresh`,
    `stale`, `unpriced` or `fx_missing` (the last two have no value)."""
    priced = closes.get(lot["symbol"])
    if priced is None:
        return None, "unpriced"
    close, _ = priced
    value = _dec(lot["quantity"]) * _dec(close)
    if needs_rate(lot.get("currency")):
        rate = rates.get(normalize_currency(lot.get("currency")))
        if rate is None:
            return None, "fx_missing"
        value *= _dec(rate)
    return value, "fresh"


def _holdings(
    lots: Sequence[Mapping[str, Any]],
    closes: Mapping[str, Tuple[Decimal, datetime.date]],
    rates: Mapping[str, Decimal],
    session: datetime.date,
) -> Dict[str, Any]:
    """Every lot valued, with the counts of those that couldn't be."""
    counts = {"unpriced": 0, "fx_missing": 0, "stale": 0}
    total = ZERO
    values: List[Optional[Decimal]] = []
    for lot in lots:
        value, how = _holding_value(lot, closes, rates)
        values.append(value)
        if value is None:
            counts[how] += 1
            continue
        total += value
        if closes[lot["symbol"]][1] < session:
            counts["stale"] += 1
    complete = counts["unpriced"] == 0 and counts["fx_missing"] == 0
    return {"total": total if complete else None, "values": values, **counts}


def _contributed(ledger: Mapping[str, Any]) -> Decimal:
    """The opening balance plus deposits less withdrawals the ledger counts."""
    return ledger["opening_balance"] + ledger["deposits"] + ledger["withdrawals"]


def _ledger_flow(
    ledger: Mapping[str, Any],
    session: datetime.date,
    previous: Optional[Mapping[str, Any]],
) -> Decimal:
    """The cash that came in or went out since the previous row: the change
    in what was contributed. With no previous row, what is dated `session`."""
    if previous is not None:
        return _contributed(ledger) - _dec(previous["contributed"])
    iso = session.isoformat()
    return sum(
        (e["amount"] for e in ledger["entries"]
         if e["kind"] in FLOW_KINDS and e["amount"] is not None and e["entry_date"] == iso),
        ZERO,
    )


def _lot_flow(
    lots: Sequence[Mapping[str, Any]],
    lot_values: Sequence[Optional[Decimal]],
    session: datetime.date,
    after: Optional[datetime.date],
) -> Optional[Decimal]:
    """The value of the lots that arrived without cash since `after` (only
    on `session` when None). None when one of them has no value."""
    total = ZERO
    for lot, value in zip(lots, lot_values):
        if (lot.get("acquisition_type") or BUY).upper() == BUY:
            continue
        if not _in_window(_lot_day(lot), after, session):
            continue
        if value is None:
            return None
        total += value
    return total


def book_value(
    *,
    session: datetime.date,
    entries: Sequence[Mapping[str, Any]],
    lots: Sequence[Mapping[str, Any]],
    closes: Mapping[str, Tuple[Any, datetime.date]],
    rates: Mapping[str, Decimal],
    previous: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """One book's value at the close of `session`, as a snapshot row.

    `entries` are the book's cash rows (`CashRepository.list_entries`), `lots`
    its open lots (`symbol`, `quantity`, `currency`, `acquisition_type`,
    `trade_date` or `purchase_date`), `closes` `{symbol: (close, bar
    session)}` for the newest closed bar on or before `session` (a symbol
    with none is absent), and `rates` `{currency: USD per unit}` for the
    session (USD is never looked up).

    `previous` is the book's previous snapshot (`session_date` and
    `contributed`), or None when it has none. The flow is what changed since
    it; with None, what is dated `session` itself.

    Returns None when the ledger had no opening balance by `session`.
    Otherwise `cash`, `contributed`, `holdings_value`, `book_value` and
    `flow` (each to the cent; the last three None when incomplete),
    `holding_count`, `unpriced_count`, `stale_count`, `fx_missing_count`
    (holdings and cash rows with no rate).
    """
    ledger = _cash_at(entries, session)
    if ledger is None:
        return None
    priced = {sym: (_dec(close), day) for sym, (close, day) in closes.items()}
    holdings = _holdings(lots, priced, rates, session)
    cash = ledger["balance"]
    after = previous["session_date"] if previous is not None else None
    lot_flow = _lot_flow(lots, holdings["values"], session, after)
    flow = None if lot_flow is None else _ledger_flow(ledger, session, previous) + lot_flow
    complete = holdings["total"] is not None and ledger["fx_missing_count"] == 0
    held = cash_math.to_cents(holdings["total"]) if holdings["total"] is not None else None
    return {
        "session_date": session,
        "cash": cash,
        "contributed": _contributed(ledger),
        "holdings_value": held,
        "book_value": cash + held if complete else None,
        "flow": cash_math.to_cents(flow) if flow is not None else None,
        "holding_count": len(lots),
        "unpriced_count": holdings["unpriced"],
        "stale_count": holdings["stale"],
        "fx_missing_count": holdings["fx_missing"] + ledger["fx_missing_count"],
    }


def _left_on(lot: Mapping[str, Any]) -> Optional[datetime.date]:
    """The session a closed lot left the book: the date of its sale (a
    closed lot holds exactly the shares its one sale sold), else the legacy
    `sale_date`. None for an open lot, and for a closed lot with no date."""
    if lot.get("sold_on") is not None:
        return as_date(lot["sold_on"])
    if (lot.get("status") or "OPEN").upper() == "OPEN":
        return None
    return as_date(lot.get("sale_date"))


def undated_lots(lots: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The lots a rebuild can't place in time: one with no trade or purchase
    date, or a closed one with no sale date. They are left out of every
    session, and reported."""
    return [
        lot for lot in lots
        if _lot_day(lot) is None
        or ((lot.get("status") or "OPEN").upper() != "OPEN" and _left_on(lot) is None)
    ]


def held_lots(lots: Sequence[Mapping[str, Any]], session: datetime.date
              ) -> List[Mapping[str, Any]]:
    """The lots a book held at the close of `session`, from its whole history
    (`PortfolioRepository.list_lot_history`): entered on or before it, and
    not sold by then. A lot sold on the session itself is gone at its close.

    A partial sale splits a lot: the closed half holds the shares sold and
    the open half the rest, both with the original trade date, so before the
    sale the two add up to the lot as bought."""
    undated = {id(lot) for lot in undated_lots(lots)}
    held = []
    for lot in lots:
        if id(lot) in undated or _lot_day(lot) > session:
            continue
        left = _left_on(lot)
        if left is None or left > session:
            held.append(lot)
    return held


def close_as_of(
    bars: Sequence[Tuple[datetime.date, Any]],
    session: datetime.date,
    lookback_days: int,
) -> Optional[Tuple[Decimal, datetime.date]]:
    """The close of the newest bar on or before `session` and no more than
    `lookback_days` before it, as `(close, bar session)`; None when there is
    none. `bars` are `(session, close)` in any order. The same choice
    `PricesService.close_on` makes for the nightly row."""
    earliest = session - datetime.timedelta(days=lookback_days)
    best: Optional[Tuple[datetime.date, Any]] = None
    for day, close in bars:
        if earliest <= day <= session and (best is None or day > best[0]):
            best = (day, close)
    return (_dec(best[1]), best[0]) if best is not None else None


def rebuild_snapshots(
    *,
    sessions: Sequence[datetime.date],
    entries: Sequence[Mapping[str, Any]],
    lots: Sequence[Mapping[str, Any]],
    bars: Mapping[str, Sequence[Tuple[datetime.date, Any]]],
    fx_closes: Mapping[str, Mapping[datetime.date, Any]],
    previous: Optional[Mapping[str, Any]],
    lookback_days: int,
) -> List[Tuple[datetime.date, Optional[Dict[str, Any]]]]:
    """A book's snapshot rows for `sessions`, rebuilt from its whole history
    (plan step 5.2).

    `entries` is the whole cash ledger, `lots` every lot open or closed
    (`held_lots`), `bars` `{symbol: [(session, close), ...]}` of closed daily
    bars, `fx_closes` `{currency: {date: USD per unit}}`, and `previous` the
    stored row before the first session (or None). Each session is valued
    with `book_value`, its flow measured from the row rebuilt before it.

    Returns `(session, row)` in session order. `row` is None for a session
    the book had no opening balance by: it has no row.
    """
    rebuilt: List[Tuple[datetime.date, Optional[Dict[str, Any]]]] = []
    for session in sorted(sessions):
        held = held_lots(lots, session)
        closes = {}
        for symbol in {lot["symbol"] for lot in held}:
            found = close_as_of(bars.get(symbol, ()), session, lookback_days)
            if found is not None:
                closes[symbol] = found
        rates = {}
        for currency, daily in fx_closes.items():
            rate = prior_close_rate(daily, session)
            if rate is not None:
                rates[currency] = rate
        row = book_value(session=session, entries=entries, lots=held, closes=closes,
                         rates=rates, previous=previous)
        rebuilt.append((session, row))
        if row is not None:
            previous = row
    return rebuilt


def return_index(points: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """The growth of 1 through a book's snapshots, oldest first.

    Each point needs `session_date`, `book_value` and `flow` (either may be
    None). Returns `index` (a list of `(session, index)` for the valued
    sessions, starting at 1 on the first) and `gaps`, the sessions the chain
    couldn't be measured across:

    - a session with no value: its flow is carried to the next valued one;
    - a session whose flow, or one carried to it, is unknown;
    - a session where the previous value plus the flow is zero or less,
      when there is nothing to earn a return on.

    At a gap of the last two kinds the index carries on flat and the session
    becomes the new base.
    """
    index: List[Tuple[datetime.date, Decimal]] = []
    gaps: List[datetime.date] = []
    level = ONE
    base: Optional[Decimal] = None
    pending: Optional[Decimal] = ZERO
    for point in sorted(points, key=lambda p: p["session_date"]):
        flow = point.get("flow")
        pending = None if pending is None or flow is None else pending + _dec(flow)
        value = point.get("book_value")
        if value is None:
            gaps.append(point["session_date"])
            continue
        value = _dec(value)
        if base is not None:
            invested = None if pending is None else base + pending
            if invested is None or invested <= 0:
                gaps.append(point["session_date"])
            else:
                level *= value / invested
        index.append((point["session_date"], level))
        base, pending = value, ZERO
    return {"index": index, "gaps": gaps}


def time_weighted_return(points: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """The time-weighted return over a book's snapshots, as a fraction
    (0.05 is 5%), from the first valued session to the last.

    Returns `twr` (None with fewer than two valued sessions), `start` and
    `end` (the first and last valued sessions) and `gaps` (`return_index`).
    """
    chain = return_index(points)
    index = chain["index"]
    if len(index) < 2:
        twr = None
    else:
        twr = (index[-1][1] - ONE).quantize(RETURN_QUANTUM)
    return {
        "twr": twr,
        "start": index[0][0] if index else None,
        "end": index[-1][0] if index else None,
        "gaps": chain["gaps"],
    }


def max_drawdown(index: Sequence[Tuple[datetime.date, Decimal]]) -> Dict[str, Any]:
    """The largest fall from a peak of a return index (`return_index`), as a
    fraction at or below zero, with the sessions of that peak and trough.

    None with fewer than two points. Zero, with no peak or trough, when the
    index never fell below an earlier peak.
    """
    if len(index) < 2:
        return {"max_drawdown": None, "peak": None, "trough": None}
    peak_day, peak = index[0]
    worst, worst_peak, worst_trough = ZERO, None, None
    for day, level in index[1:]:
        if level > peak:
            peak_day, peak = day, level
            continue
        fall = level / peak - ONE
        if fall < worst:
            worst, worst_peak, worst_trough = fall, peak_day, day
    return {
        "max_drawdown": worst.quantize(RETURN_QUANTUM),
        "peak": worst_peak,
        "trough": worst_trough,
    }


# The benchmark every book is measured against (#431 step 5.3).
BENCHMARK_SYMBOL = "SPY"


def benchmark_index(
    sessions: Sequence[datetime.date],
    bars: Sequence[Tuple[datetime.date, Any]],
    lookback_days: int = CLOSE_LOOKBACK_DAYS,
) -> List[Tuple[datetime.date, Optional[Decimal]]]:
    """The growth of 1 in the benchmark over a book's valued `sessions`.

    `bars` are the benchmark's `(session, close)` closed daily bars. Each
    session's close is chosen as a holding's is (`close_as_of`), and the index
    is that close over the first session's. A price return: dividends are in
    neither the book's return nor this one. Every point is None when the first
    session has no close to start from; a later session with none is None on
    its own.
    """
    closes = [close_as_of(bars, session, lookback_days) for session in sessions]
    if not closes or closes[0] is None:
        return [(session, None) for session in sessions]
    base = closes[0][0]
    return [
        (session, (found[0] / base) if found is not None else None)
        for session, found in zip(sessions, closes)
    ]


def _restated(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """How many rows were rebuilt after they were first taken, and when the
    newest of them was: the disclosure GIPS asks for (step 5.2)."""
    stamps = [r["restated_at"] for r in rows if r.get("restated_at") is not None]
    return {"restated_count": len(stamps), "last_restated_at": max(stamps) if stamps else None}


def _cash_negative(rows: Sequence[Mapping[str, Any]]) -> int:
    """Sessions whose cash was below zero (plan decision 8)."""
    return sum(1 for r in rows if r.get("cash") is not None and _dec(r["cash"]) < 0)


def _latest(rows: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The newest row's figures, as the summary strip and the table show them."""
    if not rows:
        return None
    keys = ("session_date", "book_value", "cash", "holdings_value", "unpriced_count",
            "stale_count", "fx_missing_count")
    return {key: rows[-1].get(key) for key in keys}


def _quantized(value: Optional[Decimal]) -> Optional[Decimal]:
    return value.quantize(RETURN_QUANTUM) if value is not None else None


def _benchmark(
    sessions: Sequence[datetime.date],
    bars: Optional[Sequence[Tuple[datetime.date, Any]]],
) -> Tuple[Dict[datetime.date, Optional[Decimal]], Optional[Decimal]]:
    """The benchmark's index by session, and its return from the first
    session to the last (None with fewer than two, or no close to end on)."""
    if bars is None or not sessions:
        return {}, None
    bench = benchmark_index(sessions, bars)
    last = bench[-1][1]
    ret = _quantized(last - ONE) if last is not None and len(bench) >= 2 else None
    return dict(bench), ret


def _difference(a: Optional[Decimal], b: Optional[Decimal]) -> Optional[Decimal]:
    """`a - b`, or None unless both are known."""
    return a - b if a is not None and b is not None else None


def book_performance(
    points: Sequence[Mapping[str, Any]],
    benchmark_bars: Optional[Sequence[Tuple[datetime.date, Any]]],
    *,
    window_start: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """A book's performance over its snapshot rows, against the benchmark
    (#431 step 5.3).

    `points` are the stored rows oldest first. When the window has a start,
    the first of them may be the row before it: the base the window's return
    is measured from, so the return covers the whole window. Only the rows
    from `window_start` on are counted for gaps, restatement and negative
    cash. `benchmark_bars` are the benchmark's closed bars, or None when they
    could not be read.

    Returns the time-weighted return (`twr`), the benchmark's return over the
    same sessions (`benchmark_return`), their difference in fractions
    (`vs_benchmark`, None unless both are known), the drawdown, a `series` of
    `{session_date, book_value, index, benchmark_index}` for the valued
    sessions, the `gaps`, the restatement disclosure, `cash_negative_count`
    (sessions whose cash was below zero: plan decision 8 makes such a return
    unreliable) and the newest row (`latest`).
    """
    rows = sorted(points, key=lambda p: p["session_date"])
    after = window_start or datetime.date.min
    in_window = [r for r in rows if r["session_date"] >= after]
    chain = return_index(rows)
    index = chain["index"]
    bench, benchmark_return = _benchmark([day for day, _ in index], benchmark_bars)
    values = {r["session_date"]: r["book_value"] for r in rows}
    twr = time_weighted_return(rows)["twr"]
    drawdown = max_drawdown(index)
    return {
        "first_session": index[0][0] if index else None,
        "last_session": index[-1][0] if index else None,
        "twr": twr,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "benchmark_available": benchmark_bars is not None,
        "benchmark_return": benchmark_return,
        "vs_benchmark": _difference(twr, benchmark_return),
        "max_drawdown": drawdown["max_drawdown"],
        "drawdown_peak": drawdown["peak"],
        "drawdown_trough": drawdown["trough"],
        "series": [
            {"session_date": day, "book_value": values.get(day),
             "index": _quantized(level), "benchmark_index": _quantized(bench.get(day))}
            for day, level in index
        ],
        "gaps": [day for day in chain["gaps"] if day >= after],
        **_restated(in_window),
        "cash_negative_count": _cash_negative(in_window),
        "latest": _latest(in_window),
    }
