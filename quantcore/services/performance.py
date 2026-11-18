"""PerformanceService — what each book is worth at the close, kept as a daily
series (#431 phase 3, plan step 5.1).

The daily job asks for every book a snapshot is owed for (`due_snapshots`)
and takes each (`snapshot_book`). Step 5.3 reads the series back as a
time-weighted return and a drawdown.

Rules kept here (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument on every method that touches a
  book. `book_id` is keyword-only and defaults to None, the real book.
- A snapshot never creates a book, and a read never writes.
- **Only the daily job writes a snapshot**, for the session that has just
  closed. What a row holds is `analytics.performance.book_value`.

The service only gathers: the book's cash rows and open lots (zero network),
each held symbol's close (`PricesService.close_on`, which fetches only a
symbol whose closed bars are behind) and an FX rate for each non-USD currency
held (one gateway call a currency). A missing close or rate is counted on the
row, never guessed.
"""

from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from quantcore.analytics import fx
from quantcore.analytics import performance as performance_math
from quantcore.repositories.book_snapshots_repository import BookSnapshotsRepository
from quantcore.repositories.books_repository import BookNotFoundError, BooksRepository
from quantcore.repositories.cash_repository import CashRepository

logger = logging.getLogger(__name__)


def _complete(snapshot: Optional[Dict[str, Any]]) -> bool:
    """True for a snapshot that values every holding at its own session's close."""
    return (
        snapshot is not None
        and snapshot["book_value"] is not None
        and snapshot["stale_count"] == 0
    )


class PerformanceService:
    def __init__(
        self,
        snapshots_repository: BookSnapshotsRepository,
        books_repository: BooksRepository,
        cash_repository: CashRepository,
        portfolio_repository,
        prices,
        fx_gateway=None,
    ) -> None:
        self._repo = snapshots_repository
        self._books_repo = books_repository
        self._cash_repo = cash_repository
        # PortfolioRepository.list_positions: a book's open lots.
        self._lots = portfolio_repository
        # PricesService.close_on: a holding's close.
        self._prices = prices
        # YFinanceGateway.fx_daily_closes: a non-USD holding's rate.
        self._fx_gateway = fx_gateway

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------
    def _require_book(self, owner: str, book_id: Optional[int]) -> None:
        """Raises `BookNotFoundError` for a named book that isn't `owner`'s.
        No book named is the real book, and looks nothing up."""
        if book_id is not None and self._books_repo.get_book(owner=owner, book_id=book_id) is None:
            raise BookNotFoundError(f"book {book_id} not found")

    def _live_books(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Every owner's books that are not archived, as `(owner, book)`.
        Walked owner by owner, so every statement after it is bound to one
        owner and one book."""
        for owner in self._books_repo.list_owners():
            for book in self._books_repo.list_books(owner=owner):
                if book["archived_at"] is None:
                    yield owner, book

    # ------------------------------------------------------------------
    # The daily job
    # ------------------------------------------------------------------
    def due_snapshots(self, *, today: datetime.date) -> List[Dict[str, Any]]:
        """Every book that is owed a complete snapshot for `today`: what the
        daily job has to take, and what its alarm counts.

        A book is owed one when it isn't archived, its ledger has an opening
        balance on or before `today`, and its row for `today` is missing, has
        no value, or priced a holding from an earlier session. A real book
        with no opening balance yet owes nothing (plan decision 9). Each is
        `owner`, `book_id`, `kind` and `reason` (`missing`, `incomplete` or
        `stale`). Zero network calls.
        """
        iso = today.isoformat()
        due: List[Dict[str, Any]] = []
        for owner, book in self._live_books():
            book_id = book["book_id"]
            entries = self._cash_repo.list_entries(owner=owner, book_id=book_id)
            if not any(e["kind"] == "opening_balance" and e["entry_date"] <= iso for e in entries):
                continue
            rows = self._repo.list_snapshots(owner=owner, start=today, end=today, book_id=book_id)
            row = rows[0] if rows else None
            if _complete(row):
                continue
            if row is None:
                reason = "missing"
            else:
                reason = "incomplete" if row["book_value"] is None else "stale"
            due.append({"owner": owner, "book_id": book_id, "kind": book["kind"], "reason": reason})
        return due

    def snapshot_book(
        self, *, owner: str, session: datetime.date, book_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Value one of `owner`'s books at the close of `session` and store it,
        replacing the row that session has. Returns the row as written, or
        None when the book had no opening balance by then (nothing is
        written).

        Run by the daily job after the close, once the day's fills and stop
        sales are in, so the open lots are the ones held at the close. Raises
        `BookNotFoundError` for a book that isn't the owner's. An error from a
        price fetch propagates, and nothing is written.
        """
        self._require_book(owner, book_id)
        entries = self._cash_repo.list_entries(owner=owner, book_id=book_id)
        lots = self._lots.list_positions(owner, "OPEN", book_id=book_id)
        snapshot = performance_math.book_value(
            session=session,
            entries=entries,
            lots=lots,
            closes=self._closes({lot["symbol"] for lot in lots}, session),
            rates=self._rates(lots, session),
            previous=self._repo.previous_snapshot(owner=owner, session=session, book_id=book_id),
        )
        if snapshot is None:
            return None
        snapshot_id = self._repo.upsert_snapshot(owner=owner, snapshot=snapshot, book_id=book_id)
        if snapshot_id is None:
            return None
        return {**snapshot, "snapshot_id": snapshot_id}

    def _closes(
        self, symbols: Sequence[str], session: datetime.date
    ) -> Dict[str, Tuple[Decimal, datetime.date]]:
        """`{symbol: (close, bar session)}` for the held symbols that have a
        close in the lookback window; a symbol with none is absent."""
        closes: Dict[str, Tuple[Decimal, datetime.date]] = {}
        for symbol in sorted(symbols):
            found = self._prices.close_on(symbol, session)
            if found["close"] is not None:
                closes[symbol] = (Decimal(str(found["close"])), found["session"])
        return closes

    def _rates(self, lots: Sequence[Dict[str, Any]], session: datetime.date) -> Dict[str, Decimal]:
        """`{currency: USD per unit}` at `session` for the non-USD currencies
        held. A currency whose lookup fails or finds no close within a week is
        absent: the row counts its holdings as `fx_missing`, never at 1.0."""
        currencies = {fx.normalize_currency(lot["currency"]) for lot in lots if fx.needs_rate(lot["currency"])}
        rates: Dict[str, Decimal] = {}
        if self._fx_gateway is None:
            return rates
        start, end = fx.fetch_window([session])
        for currency in sorted(currencies):
            try:
                closes = self._fx_gateway.fx_daily_closes(currency, start, end)
            except Exception as exc:  # noqa: BLE001 — the row says the rate is missing
                logger.warning("fx rates for %s unavailable (%s)", currency, type(exc).__name__)
                continue
            rate = fx.prior_close_rate(closes, session)
            if rate is not None:
                rates[currency] = rate
        return rates

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_snapshots(
        self,
        *,
        owner: str,
        start: Any = None,
        end: Any = None,
        book_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """One of `owner`'s books' snapshots, oldest first, from `start` to
        `end` (either None for an open end). One query, no network call.
        Raises `BookNotFoundError` for a book that isn't the owner's."""
        self._require_book(owner, book_id)
        return self._repo.list_snapshots(owner=owner, start=start, end=end, book_id=book_id)
