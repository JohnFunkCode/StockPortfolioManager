"""PerformanceService — what each book is worth at the close, kept as a daily
series (#431 phase 3, plan step 5.1).

The daily job asks for every book a snapshot is owed for (`due_snapshots`)
and takes each (`snapshot_book`). Step 5.3 reads the series back as a
time-weighted return and a drawdown against SPY (`book_performance`, and
`compare_books` for every open book side by side). Step 5.4 gives Sidekick
and MCP the same figures, with the series sampled by period (`books_overview`).

Rules kept here (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument on every method that touches a
  book. `book_id` is keyword-only and defaults to None, the real book.
- A snapshot never creates a book, and a read never writes.
- **Only the daily job writes a snapshot**, for the session that has just
  closed, and the history backfill script rebuilds past ones. What a row
  holds is `analytics.performance.book_value`.
- **A change to history restates the rows it affects** (step 5.2, decisions
  85 and 88 as John revised them). A write dated on or before a book's newest
  row marks it (`restate_sql`); `restate_book` rebuilds the rows from that
  date, under the book's row lock, and clears the mark in the same
  transaction. It restates only from the book's first stored row: extending
  history further back is the backfill's job.

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
from quantcore.analytics import market_time
from quantcore.analytics.market_time import rolling_window
from quantcore.analytics import performance as performance_math
from quantcore.analytics import portfolio_math
from quantcore.repositories.book_snapshots_repository import BookSnapshotsRepository
from quantcore.repositories.books_repository import BookNotFoundError, BooksRepository
from quantcore.repositories.cash_repository import CashRepository

logger = logging.getLogger(__name__)


def _trading_sessions(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    """The NYSE sessions from `start` to `end` inclusive, oldest first."""
    sessions = []
    day = start
    while day <= end:
        if market_time.is_trading_day(day):
            sessions.append(day)
        day += datetime.timedelta(days=1)
    return sessions


def _summary(
    rebuilt: Sequence[Tuple[datetime.date, Optional[Dict[str, Any]]]],
    counts: Dict[str, int],
    deleted: int,
    lots: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """What a rebuild did, as `PerformanceService._rebuild_in` reports it."""
    rows = [row for _, row in rebuilt if row is not None]
    return {
        "sessions": len(rebuilt),
        **counts,
        "deleted": deleted,
        "unvalued": [r["session_date"] for r in rows if r["book_value"] is None],
        "stale": [r["session_date"] for r in rows
                  if r["book_value"] is not None and r["stale_count"]],
        "undated_lots": len(performance_math.undated_lots(lots)),
    }


def _complete(snapshot: Optional[Dict[str, Any]]) -> bool:
    """True for a snapshot that values every holding at its own session's close."""
    return (
        snapshot is not None
        and snapshot["book_value"] is not None
        and snapshot["stale_count"] == 0
    )


# The real book of an owner who has never written anything: a read must not
# create one (CLAUDE.md, "A read never creates a book"), so it is described
# rather than looked up, and every read of it passes no book id.
_REAL_STAND_IN: Dict[str, Any] = {
    "book_id": None, "name": "Real", "kind": "real", "archived_at": None,
}


def _read_id(book: Dict[str, Any]) -> Optional[int]:
    """The `book_id` a read of `book` passes: None for the real book, which
    every repository reads by default."""
    return None if book["kind"] == "real" else book["book_id"]


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
        balance on or before `today`, and it is marked for restatement, or its
        row for `today` is missing, has no value, or priced a holding from an
        earlier session. A real book with no opening balance yet owes nothing
        (plan decision 9). Each is `owner`, `book_id`, `kind`, `reason`
        (`restate`, `missing`, `incomplete` or `stale`) and `restate_from`
        (the mark, or None). A marked book's reason is `restate` whatever its
        row for today says, because the rebuild takes that row too. Zero
        network calls.
        """
        iso = today.isoformat()
        due: List[Dict[str, Any]] = []
        for owner, book in self._live_books():
            book_id = book["book_id"]
            entries = self._cash_repo.list_entries(owner=owner, book_id=book_id)
            if not any(e["kind"] == "opening_balance" and e["entry_date"] <= iso for e in entries):
                continue
            mark = self._repo.restate_mark(owner=owner, book_id=book_id)
            rows = self._repo.list_snapshots(owner=owner, start=today, end=today, book_id=book_id)
            row = rows[0] if rows else None
            if mark is not None:
                reason = "restate"
            elif _complete(row):
                continue
            elif row is None:
                reason = "missing"
            else:
                reason = "incomplete" if row["book_value"] is None else "stale"
            due.append({"owner": owner, "book_id": book_id, "kind": book["kind"],
                        "reason": reason, "restate_from": mark})
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

    # ------------------------------------------------------------------
    # Restatement and the history backfill (step 5.2)
    # ------------------------------------------------------------------
    def restate_book(
        self, *, owner: str, through: datetime.date, book_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Rebuild a marked book's snapshots from its mark through `through`
        and clear the mark, in one transaction under the book's row lock.

        The rebuild starts at the later of the mark and the book's first
        stored row, so it corrects the series the book has and fills any hole
        in it, but never starts one earlier. Every trading session in the
        range is rebuilt, `through` included, so the daily job's own row for
        the session comes out of it too.

        Returns the summary `_rebuild_in` gives, with `restated_from` (the
        mark, or None when the book wasn't marked: nothing is done). Raises
        `BookNotFoundError` for a book that isn't the owner's. An error from a
        price or FX fetch propagates, nothing is written, and the book stays
        marked for the next run.
        """
        self._require_book(owner, book_id)
        with self._repo.restating(owner=owner, book_id=book_id) as tx:
            if tx.mark is None:
                return {"restated_from": None, "sessions": 0}
            stored = self._repo.list_snapshots(owner=owner, book_id=book_id)
            start = max([tx.mark, *(r["session_date"] for r in stored[:1])])
            summary = self._rebuild_in(tx, owner=owner, book_id=book_id,
                                       start=start, end=through)
            summary["cleared"] = tx.clear_mark()
        return {"restated_from": tx.mark, **summary}

    def backfill_book(
        self,
        *,
        owner: str,
        through: datetime.date,
        start: Optional[datetime.date] = None,
        book_id: Optional[int] = None,
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Rebuild a book's snapshots from `start` (by default its opening
        balance) through `through`: the history backfill script.

        Without `apply` the same writes run and are rolled back, so a dry run
        reports exactly what an applied one would do. An applied run that
        covers the book's mark, from on or before it through its newest row,
        also clears it. Returns `_rebuild_in`'s summary with `start` and
        `cleared`; `start` is None, and nothing is done, when the book has no
        opening balance. Raises `BookNotFoundError` for a book that isn't the
        owner's.
        """
        self._require_book(owner, book_id)
        if start is None:
            start = self._opening_date(owner, book_id)
            if start is None:
                return {"start": None, "sessions": 0}
        with self._repo.restating(owner=owner, book_id=book_id, apply=apply) as tx:
            stored = self._repo.list_snapshots(owner=owner, book_id=book_id)
            summary = self._rebuild_in(tx, owner=owner, book_id=book_id, start=start,
                                       end=through)
            newest = stored[-1]["session_date"] if stored else None
            covers = (tx.mark is not None and start <= tx.mark
                      and (newest is None or through >= newest))
            summary["cleared"] = tx.clear_mark() if covers else False
        return {"start": start, **summary}

    def _opening_date(self, owner: str, book_id: Optional[int]) -> Optional[datetime.date]:
        entries = self._cash_repo.list_entries(owner=owner, book_id=book_id)
        dates = [e["entry_date"] for e in entries if e["kind"] == "opening_balance"]
        return fx.as_date(dates[0]) if dates else None

    def _rebuild_in(
        self, tx, *, owner: str, book_id: Optional[int],
        start: datetime.date, end: datetime.date,
    ) -> Dict[str, Any]:
        """Rebuild every trading session from `start` to `end` on `tx`.

        Gathers the book's whole cash ledger and lot history, each symbol it
        held in the range's closed bars (one read a symbol) and each non-USD
        currency's daily rates (one fetch a currency), then writes what
        `analytics.performance.rebuild_snapshots` gives. A session with no
        value any more loses its row.

        Returns `sessions` (how many were rebuilt), the write counts
        (`inserted`, `restated`, `unchanged`), `deleted`, `unvalued` and
        `stale` (the sessions with no book value, or a holding priced from an
        earlier close) and `undated_lots` (lots that couldn't be placed in
        time, left out of every session).
        """
        sessions = _trading_sessions(start, end)
        if not sessions:
            return _summary([], {"inserted": 0, "restated": 0, "unchanged": 0}, 0, [])
        entries = self._cash_repo.list_entries(owner=owner, book_id=book_id)
        lots = self._lots.list_lot_history(owner, book_id=book_id)
        held = {lot["symbol"] for s in sessions for lot in performance_math.held_lots(lots, s)}
        rebuilt = performance_math.rebuild_snapshots(
            sessions=sessions,
            entries=entries,
            lots=lots,
            bars=self._bars(held, sessions[0]),
            fx_closes=self._fx_closes(lots, sessions),
            previous=self._repo.previous_snapshot(owner=owner, session=sessions[0],
                                                  book_id=book_id),
            lookback_days=performance_math.CLOSE_LOOKBACK_DAYS,
        )
        counts = tx.write([row for _, row in rebuilt if row is not None])
        deleted = tx.delete([session for session, row in rebuilt if row is None])
        return _summary(rebuilt, counts, deleted, lots)

    def _bars(
        self, symbols: Sequence[str], first: datetime.date
    ) -> Dict[str, List[Tuple[datetime.date, float]]]:
        """`{symbol: [(session, close), ...]}` of each symbol's closed daily
        bars from the lookback window before `first` on. One read a symbol,
        which fetches only a symbol whose closed bars are behind. A fetch
        error propagates: a rebuild must not write a hole a retry would fill."""
        since = first - datetime.timedelta(days=performance_math.CLOSE_LOOKBACK_DAYS)
        return {
            symbol: [(bar["session"], bar["close"])
                     for bar in self._prices.closed_bars_since(symbol, since)]
            for symbol in sorted(symbols)
        }

    def _fx_closes(
        self, lots: Sequence[Dict[str, Any]], sessions: Sequence[datetime.date]
    ) -> Dict[str, Dict[datetime.date, Any]]:
        """`{currency: {date: USD per unit}}` over the sessions, for every
        non-USD currency the book has held. One fetch a currency. Unlike the
        nightly row, a failed fetch propagates: a rebuild that wrote rows with
        no rate would clear the mark and leave them wrong."""
        currencies = {fx.normalize_currency(lot["currency"]) for lot in lots
                      if fx.needs_rate(lot["currency"])}
        if not currencies or self._fx_gateway is None:
            return {}
        start, end = fx.fetch_window(sessions)
        return {c: self._fx_gateway.fx_daily_closes(c, start, end) for c in sorted(currencies)}

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

    # ------------------------------------------------------------------
    # Performance (step 5.3)
    # ------------------------------------------------------------------
    def book_performance(
        self, *, owner: str, period: str = "year", book_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """One of `owner`'s books' performance over a rolling `period`
        (`month`, `quarter`, `year` or `all`, the Realized view's windows),
        against SPY, with realized P/L by source over the same window.

        Reads the stored snapshots only: the row before the window is the
        base, so the return covers the whole window. The one possible network
        call is SPY's closed bars, through `PricesService.closed_bars_since`,
        which fetches only when they are behind; a failure leaves the
        benchmark out (`benchmark_available` false) and never fails the read.
        Raises `BookNotFoundError` for a book that isn't the owner's, and
        `ValueError` for an unknown period.
        """
        start, end = rolling_window(period)
        book = self._book(owner, book_id)
        rows = self._base_and_rows(owner, start, end, _read_id(book))
        bars = self._benchmark_bars(rows[0]["session_date"] if rows else None)
        return self._performance(owner, book, rows, start, end, period, bars)

    def compare_books(self, *, owner: str, period: str = "year") -> Dict[str, Any]:
        """Every one of `owner`'s books that isn't archived, side by side over
        the same `period`: what the Compare books view shows (plan decision
        15). Each book is read on its own, bound to its owner and its id, as
        `book_performance` reads it; SPY's bars are read once for them all.
        The real book is first. An owner with no book yet gets the real book's
        stand-in, with no rows."""
        start, end = rolling_window(period)
        books = [b for b in self._books_repo.list_books(owner=owner) if b["archived_at"] is None]
        if not any(b["kind"] == "real" for b in books):
            books.insert(0, _REAL_STAND_IN)
        read = [(b, self._base_and_rows(owner, start, end, _read_id(b))) for b in books]
        firsts = [rows[0]["session_date"] for _, rows in read if rows]
        bars = self._benchmark_bars(min(firsts) if firsts else None)
        return {
            "period": period,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "books": [self._performance(owner, b, rows, start, end, period, bars)
                      for b, rows in read],
        }

    def books_overview(self, *, owner: str, period: str = "year") -> Dict[str, Any]:
        """Every one of `owner`'s open books in a few lines each: what the
        read-only book tool hands Sidekick and MCP (#431 step 5.4, plan
        decision 18).

        The Compare books payload for `period`, with each book's daily
        `series` sampled to a point a day, week or month by the period
        (`performance_math.sampled_series`, decision 107 as revised) and its
        `gaps` counted rather than listed, so a year of sessions doesn't
        flood a model's context, and with the book's
        open holdings (`portfolio_math.book_holdings`, one query a book, no
        network). The real book is first; an owner with no book yet gets the
        real book's stand-in. It takes no book: every open book is listed,
        and only `owner`'s.
        """
        compared = self.compare_books(owner=owner, period=period)
        books = []
        for book in compared["books"]:
            summary = {k: v for k, v in book.items() if k not in ("series", "gaps")}
            lots = self._lots.list_positions(owner, "OPEN", book_id=_read_id(book))
            sample = performance_math.sampled_series(book["series"], period)
            books.append({
                **summary,
                "series_interval": sample["interval"],
                "series": sample["points"],
                "gap_count": len(book["gaps"]),
                "holdings": portfolio_math.book_holdings(lots),
            })
        return {**{k: compared[k] for k in ("period", "start_date", "end_date")}, "books": books}

    def _book(self, owner: str, book_id: Optional[int]) -> Dict[str, Any]:
        """The book a read is about. With no `book_id` it is the real book,
        found in the owner's list so a read never creates one; an owner with
        no real book yet gets a stand-in with no id."""
        if book_id is not None:
            book = self._books_repo.get_book(owner=owner, book_id=book_id)
            if book is None:
                raise BookNotFoundError(f"book {book_id} not found")
            return book
        for book in self._books_repo.list_books(owner=owner):
            if book["kind"] == "real":
                return book
        return _REAL_STAND_IN

    def _base_and_rows(
        self, owner: str, start: Optional[datetime.date], end: Optional[datetime.date],
        book_id: Optional[int],
    ) -> List[Dict[str, Any]]:
        """The window's rows, after the row before the window when there is
        one: the base its return is measured from."""
        rows = self._repo.list_snapshots(owner=owner, start=start, end=end, book_id=book_id)
        if start is None:
            return rows
        base = self._repo.previous_snapshot(owner=owner, session=start, book_id=book_id)
        return ([base] if base is not None else []) + rows

    def _benchmark_bars(
        self, since: Optional[datetime.date]
    ) -> Optional[List[Tuple[datetime.date, float]]]:
        """SPY's closed daily bars from the lookback before `since`, as
        `(session, close)`; an empty list when there is nothing to compare,
        and None when they could not be read. The error is logged by class
        only."""
        if since is None:
            return []
        first = since - datetime.timedelta(days=performance_math.CLOSE_LOOKBACK_DAYS)
        try:
            bars = self._prices.closed_bars_since(performance_math.BENCHMARK_SYMBOL, first)
        except Exception as exc:  # noqa: BLE001 — the book's figures don't need it
            logger.warning("benchmark bars unavailable (%s)", type(exc).__name__)
            return None
        return [(bar["session"], bar["close"]) for bar in bars]

    def _performance(
        self, owner: str, book: Dict[str, Any], rows: List[Dict[str, Any]],
        start: Optional[datetime.date], end: Optional[datetime.date], period: str,
        bars: Optional[List[Tuple[datetime.date, float]]],
    ) -> Dict[str, Any]:
        """One book's payload from its rows, the benchmark's bars and its
        sales in the window (one read)."""
        book_id = _read_id(book)
        sales = self._lots.list_realized_sales(
            owner, start_date=start, end_date=end, book_id=book_id
        )
        return {
            "book_id": book["book_id"],
            "name": book["name"],
            "kind": book["kind"],
            "period": period,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            **performance_math.book_performance(rows, bars, window_start=start),
            "attribution": portfolio_math.realized_by_source(sales),
        }
