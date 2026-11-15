"""A book's cash ledger: which entries count, and the running balance (#431 step 3.1).

Pure functions, no I/O. The rows come from `CashRepository.list_entries`.

The rules:

- **A ledger starts at its opening balance.** That is the cash the book held
  at the start of the opening date. An entry dated on or after it counts. One
  dated before it is from before the book's history starts: it is left out
  and only counted (`earlier_entry_count`).
- **With no opening balance there is no balance.** A book's lots only ever
  take cash out, so a balance summed from them alone would be a large negative
  number that means nothing. Nothing is listed until the owner says what cash
  they started with.
- **An entry with no amount is left out of the balance and flagged.** It is a
  buy or a sell of a non-USD lot with no FX rate for its trade date. It is
  never counted as zero silently: `fx_missing_count` says how many there are,
  and the balance is then known to be incomplete.
- **The balance is the sum of the amounts shown.** Each amount is already
  rounded to the cent where it is stored, so the total reconciles with the
  rows on screen.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional

OPENING_BALANCE = "opening_balance"
DEPOSIT = "deposit"
WITHDRAWAL = "withdrawal"
BUY = "buy"
SELL = "sell"

ZERO = Decimal("0")
CENT = Decimal("0.01")


def to_cents(amount: Decimal) -> Decimal:
    """`amount` to the cent, a half rounding away from zero: what Postgres's
    ROUND does to the buy and sell amounts, so every row rounds one way."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)

# The per-kind totals in the payload, by entry kind.
_TOTAL_KEYS = {DEPOSIT: "deposits", WITHDRAWAL: "withdrawals", BUY: "bought", SELL: "sold"}


def _in_ledger_order(entry: Dict[str, Any]):
    """Oldest first. On the opening date the opening balance comes first, so
    every other entry of that day is applied to it."""
    return (entry["entry_date"], entry["kind"] != OPENING_BALANCE, entry["entry_id"])


def _opening(entries: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((e for e in entries if e["kind"] == OPENING_BALANCE), None)


def _empty(earlier: int) -> Dict[str, Any]:
    return {
        "opening_date": None,
        "opening_balance": None,
        "balance": None,
        "negative": False,
        "entry_count": 0,
        "earlier_entry_count": earlier,
        "fx_missing_count": 0,
        **{key: None for key in _TOTAL_KEYS.values()},
        "entries": [],
    }


def _with_running_balance(counted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each entry, in the order given, with `fx_missing` and the balance once
    it is applied. An entry with no amount leaves the balance where it was."""
    balance = ZERO
    shown = []
    for row in counted:
        amount = row["amount"]
        if amount is not None:
            balance += amount
        shown.append({**row, "fx_missing": amount is None, "balance_after": balance})
    return shown


def _totals_by_kind(counted: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    """`deposits`, `withdrawals`, `bought` and `sold`: the signed sum of each
    kind's amounts, zero for a kind with none. An opening balance is in none."""
    totals = {key: ZERO for key in _TOTAL_KEYS.values()}
    for row in counted:
        key = _TOTAL_KEYS.get(row["kind"])
        if key is not None and row["amount"] is not None:
            totals[key] += row["amount"]
    return totals


def cash_ledger(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The ledger of one book, from all of its cash rows in any order.

    Each row needs `entry_id`, `kind`, `entry_date` (an ISO string) and
    `amount` (a Decimal, or None when it could not be converted); any other
    key is passed through.

    Returns the opening date and balance, the current `balance`, whether it
    is `negative`, the totals by kind since the opening (`deposits`,
    `withdrawals`, `bought`, `sold`, each signed as stored), the counts
    described in the module docstring, and `entries` newest first. Each entry
    gains `fx_missing` and `balance_after`, the balance once it is applied
    (unchanged by an entry with no amount).

    With no opening balance every money field is None and `entries` is empty.
    """
    rows = sorted(entries, key=_in_ledger_order)
    opening = _opening(rows)
    if opening is None:
        return _empty(len(rows))

    counted = [row for row in rows if row["entry_date"] >= opening["entry_date"]]
    shown = _with_running_balance(counted)
    balance = shown[-1]["balance_after"]

    return {
        "opening_date": opening["entry_date"],
        "opening_balance": opening["amount"],
        "balance": balance,
        "negative": balance < ZERO,
        "entry_count": len(shown),
        "earlier_entry_count": len(rows) - len(counted),
        "fx_missing_count": sum(1 for row in shown if row["fx_missing"]),
        **_totals_by_kind(counted),
        "entries": shown[::-1],
    }


def signed_amount(kind: str, amount: Decimal) -> Decimal:
    """The stored amount of a deposit or withdrawal the owner typed as a
    positive number: a deposit adds cash and a withdrawal takes it out.

    Rounded to the cent. Raises `ValueError` for another kind, or an amount
    that is not above zero once rounded.
    """
    if kind not in (DEPOSIT, WITHDRAWAL):
        raise ValueError(f"a cash entry can be a deposit or a withdrawal, not {kind!r}")
    cents = to_cents(amount)
    if cents <= ZERO:
        raise ValueError("amount must be more than zero")
    return cents if kind == DEPOSIT else -cents
