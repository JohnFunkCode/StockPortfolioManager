"""CashService — a book's cash ledger (#431 step 3.1).

Reads the ledger of any of the owner's books, and records what the owner
enters for the real book: the opening balance, deposits and withdrawals. A
book's buy and sell entries are written by `PortfolioRepository` with the lot
or sale they belong to; nothing here writes them, except the backfill for
rows that predate the ledger.

Rules kept here (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument on every method.
- `book_id` is keyword-only and defaults to None, the real book. A named book
  is checked first: one that isn't the owner's raises `BookNotFoundError`.
- A read never creates a book.
- **Only the real book takes what the owner types.** A paper book's cash
  moves only through what the system fills, so its opening balance is its
  starting cash and it has no deposits or withdrawals.
- **The real book is never refused a fact.** A withdrawal or a buy larger
  than the recorded cash is saved, and the ledger says the balance is
  negative (plan decision 8).

The running balance and what counts towards it are `analytics.cash`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from quantcore.analytics import cash as cash_math
from quantcore.analytics.market_time import market_date
from quantcore.repositories.books_repository import (
    PAPER,
    REAL,
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository

# `cash_entries.amount` is NUMERIC(18,2). A larger figure would overflow the
# column and surface as a database error rather than as bad input.
MAX_CASH_AMOUNT = Decimal("1000000000000")
MAX_NOTE_LENGTH = 500


class CashEntryNotFoundError(ValueError):
    """An entry id that isn't a deposit or withdrawal in this owner's book. A
    `ValueError`, so a caller that treats bad input alike still does; a route
    answers 404 for it."""


def _amount(value: Any, name: str = "amount") -> Decimal:
    """`value` as a finite amount of money, rounded to the cent."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{name} is not a valid number: {value!r}") from None
    # NaN and Infinity pass every comparison, and NUMERIC stores NaN.
    if not amount.is_finite():
        raise ValueError(f"{name} must be a finite number")
    if abs(amount) > MAX_CASH_AMOUNT:
        raise ValueError(f"{name} is too large")
    return cash_math.to_cents(amount)


def _entry_date(value: Any, today: date) -> date:
    """`value` as a date that is not after today in market time."""
    try:
        day = date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"date is not a valid date: {value!r}") from None
    if day > today:
        raise ValueError("date cannot be in the future")
    return day


def _note(value: Any) -> Optional[str]:
    """A note trimmed, None when blank."""
    note = (str(value).strip() if value is not None else "") or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"a note can be at most {MAX_NOTE_LENGTH} characters")
    return note


class CashService:
    def __init__(self, cash_repository: CashRepository, books_repository: BooksRepository) -> None:
        self._repo = cash_repository
        self._books_repo = books_repository

    def _book_kind(self, owner: str, book_id: Optional[int]) -> str:
        """`real` or `paper`. No book named is the real book, and looks
        nothing up. Raises `BookNotFoundError` for a book that isn't `owner`'s."""
        if book_id is None:
            return REAL
        book = self._books_repo.get_book(owner=owner, book_id=book_id)
        if book is None:
            raise BookNotFoundError(f"book {book_id} not found")
        return book["kind"]

    def _require_real(self, owner: str, book_id: Optional[int]) -> None:
        if self._book_kind(owner, book_id) == PAPER:
            raise ValueError(
                "a paper book's cash changes only through its orders: it takes no "
                "opening balance, deposit or withdrawal"
            )

    def ledger(self, *, owner: str, book_id: Optional[int] = None) -> Dict[str, Any]:
        """The cash ledger of one of `owner`'s books: opening balance, current
        balance, totals by kind and the entries, newest first.

        `book_kind` says whether the book takes entries from the owner
        (`real`) or not (`paper`). One read, no network call. The shape is
        `analytics.cash.cash_ledger`.
        """
        kind = self._book_kind(owner, book_id)
        entries = self._repo.list_entries(owner=owner, book_id=book_id)
        return {"book_kind": kind, **cash_math.cash_ledger(entries)}

    def set_opening_balance(
        self,
        *,
        owner: str,
        entry_date: Any,
        amount: Any,
        notes: Any = None,
        book_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Say what cash the real book held at the start of `entry_date`,
        replacing any earlier answer. Returns the ledger.

        The amount may be negative (a margin debit is a fact too). Entries
        dated before `entry_date` stop counting. Raises `ValueError` for a
        paper book, a date in the future, or an amount that is not a finite
        number.
        """
        self._require_real(owner, book_id)
        self._repo.set_opening_balance(
            owner=owner,
            entry_date=_entry_date(entry_date, market_date()),
            amount=_amount(amount),
            notes=_note(notes),
            book_id=book_id,
        )
        return self.ledger(owner=owner, book_id=book_id)

    def add_entry(
        self,
        *,
        owner: str,
        kind: str,
        entry_date: Any,
        amount: Any,
        notes: Any = None,
        book_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record a deposit into or a withdrawal from the real book. `amount`
        is typed as a positive number; the kind gives it its sign. Returns the
        ledger.

        A withdrawal larger than the balance is saved: the ledger then reports
        a negative balance. Raises `ValueError` for a paper book, another
        kind, a date in the future, or an amount that is not above zero.
        """
        signed = cash_math.signed_amount(kind, _amount(amount))
        self._require_real(owner, book_id)
        self._repo.add_entry(
            owner=owner,
            kind=kind,
            entry_date=_entry_date(entry_date, market_date()),
            amount=signed,
            notes=_note(notes),
            book_id=book_id,
        )
        return self.ledger(owner=owner, book_id=book_id)

    def delete_entry(
        self, *, owner: str, entry_id: int, book_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Remove a deposit or withdrawal entered by mistake. Returns the ledger.

        Raises `CashEntryNotFoundError` for an entry that isn't a deposit or
        withdrawal in this book: a buy or sell goes with its lot or sale, and
        an opening balance is replaced, never removed.
        """
        self._require_real(owner, book_id)
        if not self._repo.delete_entry(owner=owner, entry_id=entry_id, book_id=book_id):
            raise CashEntryNotFoundError(f"cash entry {entry_id} not found")
        return self.ledger(owner=owner, book_id=book_id)

    def list_owners(self) -> List[str]:
        """Every owner who has a book: the backfill's walk."""
        return self._books_repo.list_owners()

    def backfill(self, *, owner: str, apply: bool = False) -> List[Dict[str, Any]]:
        """Write the cash rows missing from every one of `owner`'s books: a
        buy row per lot, a sell row per sale, and a paper book's opening
        balance. Writes nothing unless `apply`.

        For lots, sales and paper books that predate the ledger. A row that
        exists is never changed, so it is safe to run again. Returns one row
        per book: `book_id`, `name`, `kind` and how many `buys`, `sells` and
        `openings` were (or would be) written.
        """
        return [
            {
                "book_id": book["book_id"],
                "name": book["name"],
                "kind": book["kind"],
                **self._repo.backfill_entries(owner=owner, apply=apply, book_id=book["book_id"]),
            }
            for book in self._books_repo.list_books(owner=owner)
        ]
