"""Pure position-level math for the Portfolio page (issue #126 PR 4 Step 4.1).

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


def gain_loss_pct(current_price: Decimal, cost_basis_per_share: Decimal, quantity: Decimal) -> Decimal:
    """Gain/loss as a percentage of total cost basis (quantity cancels, but we
    route through the same dollar figures ``gain_loss``/``summary_totals`` use
    so the two stay consistent by construction)."""
    total_cost = cost_basis_per_share * quantity
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


def summary_totals(lots: Iterable[Dict[str, Any]], as_of: date) -> Dict[str, Optional[Decimal]]:
    """Aggregate Total Investment, Total Current Value, Total Gain/Loss $/%,
    and Dollars Per Day across a set of open lots. USD only (decision #16).

    Each lot dict needs ``quantity``, ``purchase_price``, ``current_price``
    (all ``Decimal``), and ``trade_date`` (``date``) — the shape the
    repository/service layer already produces, plus a live price merged in.

    Dollars Per Day is the sum of each lot's own dollars-per-day (lots bought
    on ``as_of`` contribute nothing — they have not had a day to move yet).
    """
    lots = list(lots)
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

    if method == FIFO:
        ordered = sorted(open_lots.values(), key=lambda lot: (lot["trade_date"], lot["lot_id"]))
    elif method == LIFO:
        ordered = sorted(open_lots.values(), key=lambda lot: (lot["trade_date"], lot["lot_id"]), reverse=True)
    else:  # HIFO
        ordered = sorted(open_lots.values(), key=lambda lot: (lot["purchase_price"], lot["lot_id"]), reverse=True)

    allocation: List[Tuple[Any, Decimal]] = []
    remaining = shares_to_sell
    for lot in ordered:
        if remaining <= 0:
            break
        take = min(remaining, lot["quantity"])
        if take <= 0:
            continue
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
