"""Pure position-level math for the Portfolio page (issue #126 PR 4 Step 4.1),
and realized P/L on closed lots (#235).

Decimal in, Decimal out. No I/O, no database, no network — every number here
is a function of numbers already in hand. The Portfolio page never computes
gain/loss, percentages, days held, dollars-per-day, or totals itself (Rule
8.4); the service layer calls through to these functions and the UI only
formats what comes back.

``days_held`` and ``summary_totals`` take ``as_of`` explicitly rather than
reading the clock, so a caller (e.g. issue #145) can stop the clock at a sale
date instead of "today".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MONEY_QUANT = Decimal("0.01")
PCT_QUANT = Decimal("0.01")

FIFO = "FIFO"
LIFO = "LIFO"
HIFO = "HIFO"
MANUAL = "MANUAL"
ALLOCATION_METHODS = (FIFO, LIFO, HIFO, MANUAL)


class LotAllocationError(ValueError):
    """Raised when a sale cannot be resolved against the caller's open lots —
    an over-sell, a reference to a closed/foreign lot, or a malformed manual
    allocation. Never silently clamp; the caller made a data-entry error and
    must see it (Step 4.2 "Don't")."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _pct(value: Decimal) -> Decimal:
    return value.quantize(PCT_QUANT, rounding=ROUND_HALF_UP)


def gain_loss(current_price: Decimal, cost_basis_per_share: Decimal, quantity: Decimal) -> Decimal:
    """Dollar gain/loss for a position: (current price - cost basis) * shares."""
    return _money((current_price - cost_basis_per_share) * quantity)


def gain_loss_pct(current_price: Decimal, cost_basis_per_share: Decimal, quantity: Decimal) -> Optional[Decimal]:
    """Gain/loss as a percentage of total cost basis (quantity cancels, but we
    route through the same dollar figures ``gain_loss``/``summary_totals`` use
    so the two stay consistent by construction). ``None`` when the cost basis
    is zero — never divide by zero."""
    total_cost = cost_basis_per_share * quantity
    if not total_cost:
        return None
    total_gain_loss = (current_price - cost_basis_per_share) * quantity
    return _pct(total_gain_loss / total_cost * 100)


def days_held(trade_date: date, as_of: date) -> int:
    """Calendar days between a lot's trade date and ``as_of``."""
    return (as_of - trade_date).days


def dollars_per_day(gain_loss_amount: Decimal, days: int) -> Optional[Decimal]:
    """Average dollar gain/loss per calendar day. ``None`` when ``days`` is 0
    (the presentation layer renders that as "—") — never divide by zero."""
    if not days:
        return None
    return _money(gain_loss_amount / Decimal(days))


def period_return(current_price: Decimal, price_n_days_ago: Decimal) -> Decimal:
    """The security's own price return over the period (decision #13) — NOT
    the position's value change. A 30-day column shows what the stock did,
    independent of when or how much of it was bought."""
    return _pct((current_price - price_n_days_ago) / price_n_days_ago * 100)


def allocation_segments(
    invested: Optional[Decimal], gain_loss_amount: Optional[Decimal]
) -> Dict[str, Optional[Decimal]]:
    """The three segments of one symbol's stacked allocation bar (issue #147).

    Reproduces the legacy report's left-hand matplotlib chart exactly: an
    orange cost-basis block from zero, with the gain stacked *above* it in
    green, or the loss drawn *down* from it in red.

    ``bar_loss`` is deliberately negative — matplotlib's ``bottom=`` bar with a
    negative height — so a caller draws it between ``bar_base + bar_loss`` and
    ``bar_base`` without deciding a sign for itself. Splitting the signed
    gain/loss into two same-signed segments is the only "math" the chart needs,
    and it lives here rather than in the front end (Rule 8.4).

    Either input may be ``None`` (an unpriced symbol), in which case there is
    no bar to draw and all three segments come back ``None``.
    """
    if invested is None or gain_loss_amount is None:
        return {"bar_base": None, "bar_gain": None, "bar_loss": None}

    zero = Decimal("0")
    return {
        "bar_base": _money(invested),
        "bar_gain": _money(max(gain_loss_amount, zero)),
        "bar_loss": _money(min(gain_loss_amount, zero)),
    }


def summary_totals(lots: Iterable[Dict[str, Any]], as_of: date) -> Dict[str, Optional[Decimal]]:
    """Aggregate Total Investment, Total Current Value, Total Gain/Loss $/%,
    and Dollars Per Day across a set of open lots. USD only (decision #16).

    Each lot dict needs ``quantity``, ``purchase_price``, ``current_price``
    (all ``Decimal``), and ``trade_date`` (``date``) — the shape the
    repository/service layer already produces, plus a live price merged in.

    Dollars Per Day is the sum of each lot's own dollars-per-day (lots bought
    on ``as_of`` contribute nothing — they have not had a day to move yet).

    A lot missing ``purchase_price``, ``quantity``, or ``trade_date`` (the API
    currently allows this for legacy/CSV-imported rows) is skipped rather than
    crashing the whole rollup — it contributes nothing to the totals.
    """
    lots = [
        lot for lot in lots
        if lot["purchase_price"] is not None and lot["quantity"] is not None
        and lot["trade_date"] is not None
    ]
    if not lots:
        return {
            "total_investment": _money(Decimal("0")),
            "total_current_value": _money(Decimal("0")),
            "total_gain_loss": _money(Decimal("0")),
            "total_gain_loss_pct": None,
            "total_dollars_per_day": None,
        }

    total_investment = Decimal("0")
    total_current_value = Decimal("0")
    total_dollars_per_day = Decimal("0")
    any_dollars_per_day = False

    for lot in lots:
        quantity = lot["quantity"]
        purchase_price = lot["purchase_price"]
        current_price = lot["current_price"]
        trade_date = lot["trade_date"]

        total_investment += purchase_price * quantity
        total_current_value += current_price * quantity

        lot_gain_loss = gain_loss(current_price, purchase_price, quantity)
        lot_days = days_held(trade_date, as_of)
        lot_dpd = dollars_per_day(lot_gain_loss, lot_days)
        if lot_dpd is not None:
            total_dollars_per_day += lot_dpd
            any_dollars_per_day = True

    total_gain_loss = _money(total_current_value - total_investment)
    total_gain_loss_pct = (
        _pct(total_gain_loss / total_investment * 100) if total_investment else None
    )

    return {
        "total_investment": _money(total_investment),
        "total_current_value": _money(total_current_value),
        "total_gain_loss": total_gain_loss,
        "total_gain_loss_pct": total_gain_loss_pct,
        "total_dollars_per_day": _money(total_dollars_per_day) if any_dollars_per_day else None,
    }


def position_rollup(lots: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll one symbol's open lots up to a single held position (issue #224).

    ``quantity`` is the sum across lots and ``avg_price`` the quantity-weighted
    average purchase price — what the Watchlist page shows as "Avg Price".
    Every lot counts, so a multi-lot holding is never reduced to its last lot
    (the bug in the retired ``GET /api/securities``).

    A lot missing ``quantity`` or ``purchase_price`` is skipped, as in
    ``summary_totals``. With nothing usable (or a zero total quantity) both
    figures are ``None`` rather than a misleading 0.
    """
    lots = [
        lot for lot in lots
        if lot.get("purchase_price") is not None and lot.get("quantity") is not None
    ]
    total_quantity = sum((lot["quantity"] for lot in lots), Decimal("0"))
    if not lots or not total_quantity:
        return {"quantity": None, "avg_price": None, "lot_count": len(lots)}
    total_cost = sum((lot["purchase_price"] * lot["quantity"] for lot in lots), Decimal("0"))
    return {
        "quantity": total_quantity,
        "avg_price": _money(total_cost / total_quantity),
        "lot_count": len(lots),
    }


def allocate(
    lots: Sequence[Dict[str, Any]],
    shares_to_sell: Decimal,
    method: str,
    manual_allocation: Optional[Sequence[Tuple[Any, Decimal]]] = None,
) -> List[Tuple[Any, Decimal]]:
    """Decide which open lots a sale of ``shares_to_sell`` draws from.

    Each lot dict needs ``lot_id``, ``quantity`` (remaining open shares),
    ``trade_date``, ``purchase_price``, and ``status``. Returns
    ``[(lot_id, shares), ...]`` — a sale may span multiple lots (decision
    #9). This function only decides *which lots and how many shares*; it
    performs no DB effects (the service layer does the split/write).

    ``method`` is one of FIFO (default caller semantics), LIFO, HIFO, or
    MANUAL. For MANUAL, ``manual_allocation`` supplies the ``(lot_id,
    shares)`` pairs and this function only validates them.
    """
    if method not in ALLOCATION_METHODS:
        raise LotAllocationError(f"Unknown allocation method: {method!r}")
    if shares_to_sell <= 0:
        raise LotAllocationError("shares_to_sell must be positive")

    open_lots = {lot["lot_id"]: lot for lot in lots if lot.get("status", "OPEN") == "OPEN"}

    if method == MANUAL:
        return _allocate_manual(open_lots, shares_to_sell, manual_allocation)

    return _take_in_order(_ordered_lots(open_lots.values(), method), shares_to_sell)


# method -> (the lot field a sale is ordered by, newest/highest first?). The
# lot id breaks a tie, so the order never depends on how the lots arrived.
_SALE_ORDER = {
    FIFO: ("trade_date", False),
    LIFO: ("trade_date", True),
    HIFO: ("purchase_price", True),
}


def _ordered_lots(lots: Iterable[Dict[str, Any]], method: str) -> List[Dict[str, Any]]:
    """`lots` in the order a FIFO, LIFO or HIFO sale draws from them."""
    field, descending = _SALE_ORDER[method]
    return sorted(lots, key=lambda lot: (lot[field], lot["lot_id"]), reverse=descending)


def _take_in_order(
    ordered: Sequence[Dict[str, Any]], shares_to_sell: Decimal
) -> List[Tuple[Any, Decimal]]:
    """Draw `shares_to_sell` from `ordered`, front first, skipping an empty
    lot. Raises when the lots hold fewer shares than that."""
    allocation: List[Tuple[Any, Decimal]] = []
    remaining = shares_to_sell
    for lot in ordered:
        take = min(remaining, lot["quantity"])
        if take > 0:
            allocation.append((lot["lot_id"], take))
            remaining -= take

    if remaining > 0:
        raise LotAllocationError(
            f"Cannot allocate {shares_to_sell} shares: only "
            f"{shares_to_sell - remaining} available across open lots"
        )
    return allocation


def _allocate_manual(
    open_lots: Dict[Any, Dict[str, Any]],
    shares_to_sell: Decimal,
    manual_allocation: Optional[Sequence[Tuple[Any, Decimal]]],
) -> List[Tuple[Any, Decimal]]:
    if not manual_allocation:
        raise LotAllocationError("MANUAL allocation requires manual_allocation pairs")

    seen = set()
    total = Decimal("0")
    for lot_id, shares in manual_allocation:
        if lot_id in seen:
            raise LotAllocationError(f"Lot {lot_id} allocated more than once")
        seen.add(lot_id)

        lot = open_lots.get(lot_id)
        if lot is None:
            raise LotAllocationError(f"Lot {lot_id} is not an open lot the caller owns")
        if shares <= 0:
            raise LotAllocationError(f"Lot {lot_id}: allocated shares must be positive")
        if shares > lot["quantity"]:
            raise LotAllocationError(
                f"Lot {lot_id}: allocated {shares} shares exceeds its {lot['quantity']} available"
            )
        total += shares

    if total != shares_to_sell:
        raise LotAllocationError(
            f"Manual allocation totals {total} shares, expected {shares_to_sell}"
        )
    return list(manual_allocation)


# ---------------------------------------------------------------------------
# Realized P/L (#235)
# ---------------------------------------------------------------------------
USD = "USD"


def _fx_rates(sale: Dict[str, Any]) -> Optional[Tuple[Decimal, Decimal]]:
    """``(purchase, sale)`` USD-per-unit rates for one sale, or None when a
    non-USD sale is missing either. A USD sale is ``(1, 1)``; a missing rate is
    never read as 1.0."""
    if (sale.get("currency") or USD) == USD:
        return Decimal("1"), Decimal("1")
    purchase_rate = sale.get("fx_rate_at_purchase")
    sale_rate = sale.get("fx_rate_at_sale")
    if purchase_rate is None or sale_rate is None:
        return None
    return purchase_rate, sale_rate


def realized_sale(sale: Dict[str, Any]) -> Dict[str, Any]:
    """One recorded sale with its realized P/L, gross of fees (#235).

    ``sale`` needs ``shares_sold`` and ``sale_price`` from its ``lot_sales``
    row, and ``purchase_price`` and ``currency`` from the lot it closed.

    The ``*_native`` figures are in the lot's currency. ``cost_basis``,
    ``proceeds`` and ``realized_pnl`` are USD: basis at the purchase-date rate,
    proceeds at the sale-date rate, so a currency move during the hold is part
    of the result. Each is rounded to the cent, and ``realized_pnl`` is the
    difference of the two rounded figures, so a row always adds up on screen.

    Two kinds of sale get no USD figures and are left out of every total by
    the callers below: ``basis_missing`` (the lot has no purchase price) and
    ``fx_missing`` (a non-USD sale without both rates).
    """
    row = dict(sale)
    shares = sale["shares_sold"]
    purchase_price = sale.get("purchase_price")
    rates = _fx_rates(sale)
    row["basis_missing"] = purchase_price is None
    row["fx_missing"] = rates is None
    for key in ("cost_basis_native", "proceeds_native", "realized_pnl_native",
                "cost_basis", "proceeds", "realized_pnl", "realized_pnl_pct"):
        row[key] = None

    row["proceeds_native"] = _money(shares * sale["sale_price"])
    if purchase_price is None:
        return row
    row["cost_basis_native"] = _money(shares * purchase_price)
    row["realized_pnl_native"] = row["proceeds_native"] - row["cost_basis_native"]
    if rates is None:
        return row

    purchase_rate, sale_rate = rates
    row["cost_basis"] = _money(shares * purchase_price * purchase_rate)
    row["proceeds"] = _money(shares * sale["sale_price"] * sale_rate)
    row["realized_pnl"] = row["proceeds"] - row["cost_basis"]
    row["realized_pnl_pct"] = _realized_pct(row["realized_pnl"], row["cost_basis"])
    return row


def _realized_pct(realized: Decimal, cost_basis: Decimal) -> Optional[Decimal]:
    return _pct(realized / cost_basis * 100) if cost_basis else None


def _realized_totals(sales: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum already-rounded sale rows, so a total always equals the rows under
    it. A sale without USD figures is counted, never summed. With nothing to
    sum the money figures are None, not a zero that reads as "broke even"."""
    counted = [s for s in sales if s["realized_pnl"] is not None]
    totals: Dict[str, Any] = {
        "sale_count": len(sales),
        "fx_missing_count": sum(1 for s in sales if s["fx_missing"]),
        "basis_missing_count": sum(1 for s in sales if s["basis_missing"]),
        "cost_basis": None, "proceeds": None,
        "realized_pnl": None, "realized_pnl_pct": None,
    }
    if counted:
        for key in ("cost_basis", "proceeds", "realized_pnl"):
            totals[key] = sum((s[key] for s in counted), Decimal("0"))
        totals["realized_pnl_pct"] = _realized_pct(totals["realized_pnl"], totals["cost_basis"])
    return totals


def _best_first(row: Dict[str, Any]) -> Tuple[bool, Decimal, str]:
    """Sort key: highest realized P/L first, a symbol with no figure last."""
    realized = row["realized_pnl"]
    return (realized is None, -(realized or Decimal("0")), row["symbol"])


def realized_pnl(
    sales: Iterable[Dict[str, Any]], held_symbols: Iterable[str] = (),
) -> Dict[str, Any]:
    """Realized P/L by symbol for a set of recorded sales (#235).

    ``sales`` is whatever the caller already filtered to (one owner, one book,
    one period); this function reads no clock. ``held_symbols`` are the
    symbols that still have an open lot, so a row can say it was a partial
    exit (``still_held``).

    Returns ``{"symbols": [...], "totals": {...}}``. Each symbol row has its
    totals, the three bar segments from ``allocation_segments`` (base = cost
    basis of the shares closed, gain or loss = realized P/L, so the top of the
    bar is the proceeds), and its ``sales`` in the order given. Symbols are
    ordered best realized P/L first. ``shares_closed`` counts every sale of
    the symbol, including one left out of the money totals.
    """
    held = set(held_symbols)
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for sale in sales:
        by_symbol.setdefault(sale["symbol"], []).append(realized_sale(sale))

    rows = []
    for symbol, symbol_sales in by_symbol.items():
        totals = _realized_totals(symbol_sales)
        rows.append({
            "symbol": symbol,
            "still_held": symbol in held,
            "shares_closed": sum((s["shares_sold"] for s in symbol_sales), Decimal("0")),
            **totals,
            **allocation_segments(totals["cost_basis"], totals["realized_pnl"]),
            "sales": symbol_sales,
        })
    rows.sort(key=_best_first)

    all_sales = [sale for row in rows for sale in row["sales"]]
    return {
        "symbols": rows,
        "totals": {"symbol_count": len(rows), **_realized_totals(all_sales)},
    }


def total_quantity(lots: Iterable[Dict[str, Any]]) -> Decimal:
    """The shares held across `lots`, priced or not. A lot with no quantity
    counts as none."""
    return sum(
        (lot["quantity"] for lot in lots if lot.get("quantity") is not None), Decimal("0")
    )
