"""Pydantic models for /api/portfolio/cash (#431 step 3.1).

A book's cash ledger: one signed USD amount per entry, positive when cash
comes in. As elsewhere the handlers return ``QuantCoreJSONResponse`` verbatim;
these models document the surface and validate requests.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel


class CashEntry(BaseModel):
    entry_id: int
    # opening_balance, deposit, withdrawal, buy or sell.
    kind: str
    entry_date: str
    # USD, signed. None for a buy or sell of a non-USD lot with no FX rate:
    # the entry is flagged and left out of the balance.
    amount: Optional[Decimal] = None
    fx_missing: bool = False
    # The balance once this entry is applied.
    balance_after: Decimal
    # A buy or sell names the lot or sale it came from and what was traded,
    # in the lot's own currency.
    lot_id: Optional[int] = None
    sale_id: Optional[int] = None
    symbol: Optional[str] = None
    shares: Optional[Decimal] = None
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    notes: Optional[str] = None


class CashLedgerResponse(BaseModel):
    """GET /api/portfolio/cash. With no opening balance every money field is
    None and `entries` is empty: a ledger starts at its opening balance, and
    `earlier_entry_count` says how many entries are waiting for one."""

    # "real" takes an opening balance, deposits and withdrawals. "paper" does not.
    book_kind: str
    opening_date: Optional[str] = None
    opening_balance: Optional[Decimal] = None
    balance: Optional[Decimal] = None
    negative: bool
    entry_count: int
    # Entries dated before the opening date. They are not listed or counted.
    earlier_entry_count: int
    fx_missing_count: int
    # Totals since the opening date, signed as stored.
    deposits: Optional[Decimal] = None
    withdrawals: Optional[Decimal] = None
    bought: Optional[Decimal] = None
    sold: Optional[Decimal] = None
    # Newest first.
    entries: List[CashEntry]


class SetOpeningBalanceRequest(BaseModel):
    """The cash the real book held at the start of `date`. May be negative."""

    date: str
    amount: Decimal
    notes: Optional[str] = None


class AddCashEntryRequest(BaseModel):
    """A deposit or withdrawal. `amount` is positive; the kind gives its sign."""

    kind: Literal["deposit", "withdrawal"]
    date: str
    amount: Decimal
    notes: Optional[str] = None
