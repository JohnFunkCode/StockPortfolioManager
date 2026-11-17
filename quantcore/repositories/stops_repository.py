"""StopsRepository — stored stops, the `stops` table (#431 phase 2, #221; plan
step 4.1).

A stop is set on a symbol in one of an owner's books. It is `active` while it
is live; `triggered`, `cancelled`, `replaced` and `closed` are final, and no
statement here moves a stop out of one: every change of status is
`UPDATE ... WHERE status = 'active'`. The statements a trade's transaction
also runs (triggering a stop with its sale, closing the stop of a symbol that
went flat) are in `stop_sql`.

A book has at most one active stop per symbol (a unique partial index).
Setting a stop on a symbol that has one replaces it: the old row becomes
`replaced` and the new one is inserted in the same transaction, with the
book's row locked, so two requests can't both find no active stop.

Rules this module keeps (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument, bound in every statement.
- `book_id` is keyword-only and defaults to None, which means the owner's real
  book. Every statement restricts the book with the same literal subquery
  `PortfolioRepository` uses, so a stop in another book matches no rows.
- A read never creates a book.
- **A stop is only written for a symbol the book holds.** `set_stop` reads the
  book's OPEN lots of the symbol on its own connection first.

SQL only — no analytics. What a stop's terms are is `analytics.stored_stops`.
"""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any, Dict, List, Optional

from quantcore.db import get_connection
from quantcore.repositories.books_repository import resolve_book_id
from quantcore.repositories.orders_repository import SQL_LOCK_BOOK
from quantcore.repositories.portfolio_repository import PortfolioRepository
from quantcore.repositories.stop_sql import (  # noqa: F401  (the SQL constants are re-exported)
    SQL_INSERT_STOP,
    SQL_REPLACE_ACTIVE_STOP,
    SQL_RESOLVE_STOP,
    close_flat_stops_on,
    resolve_stop_on,
    set_stop_on,
    trigger_stop_on,
)

SQL_LIST_STOPS = """
SELECT
    st.stop_id         AS stop_id,
    st.book_id         AS book_id,
    s.ticker           AS symbol,
    st.kind            AS kind,
    st.stop_level      AS stop_level,
    st.trail_pct       AS trail_pct,
    st.high_water_mark AS high_water_mark,
    st.method          AS method,
    st.proposed_level  AS proposed_level,
    st.reference_price AS reference_price,
    st.as_of           AS as_of,
    st.source          AS source,
    st.source_ref      AS source_ref,
    st.status          AS status,
    st.created_at      AS created_at,
    st.resolved_at     AS resolved_at,
    st.last_bar_checked  AS last_bar_checked,
    st.triggered_session AS triggered_session,
    st.trigger_price     AS trigger_price,
    st.alerted_at        AS alerted_at,
    st.first_session     AS first_session
FROM stops st
JOIN symbols s ON s.symbol_id = st.symbol_id
WHERE st.owner = :owner
  AND st.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY st.created_at DESC, st.stop_id DESC;
"""

SQL_GET_STOP = """
SELECT
    st.stop_id         AS stop_id,
    st.book_id         AS book_id,
    s.ticker           AS symbol,
    st.kind            AS kind,
    st.stop_level      AS stop_level,
    st.trail_pct       AS trail_pct,
    st.high_water_mark AS high_water_mark,
    st.method          AS method,
    st.proposed_level  AS proposed_level,
    st.reference_price AS reference_price,
    st.as_of           AS as_of,
    st.source          AS source,
    st.source_ref      AS source_ref,
    st.status          AS status,
    st.created_at      AS created_at,
    st.resolved_at     AS resolved_at,
    st.last_bar_checked  AS last_bar_checked,
    st.triggered_session AS triggered_session,
    st.trigger_price     AS trigger_price,
    st.alerted_at        AS alerted_at,
    st.first_session     AS first_session
FROM stops st
JOIN symbols s ON s.symbol_id = st.symbol_id
WHERE st.owner = :owner
  AND st.stop_id = :stop_id
  AND st.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# The checker tested the stop against closed bars and it held. The stop stays
# active: this records the newest bar tested and, for a trailing stop, the
# mark and level those bars raised it to.
SQL_RECORD_CHECK = """
UPDATE stops
SET last_bar_checked = :last_bar_checked,
    stop_level = :stop_level,
    high_water_mark = :high_water_mark
WHERE owner = :owner
  AND stop_id = :stop_id
  AND status = 'active'
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# The breach alert for a triggered stop went out. Only once: a stop that has
# been alerted for matches no row.
SQL_MARK_ALERTED = """
UPDATE stops
SET alerted_at = :alerted_at
WHERE owner = :owner
  AND stop_id = :stop_id
  AND status = 'triggered'
  AND alerted_at IS NULL
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""


class SymbolNotHeldError(ValueError):
    """A stop asked for on a symbol the book holds no OPEN lot of. A
    `ValueError`, so a route answers 422 for it."""


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "stop_id": int(row["stop_id"]),
        "book_id": int(row["book_id"]),
        "symbol": (row["symbol"] or "").strip().upper(),
        "kind": row["kind"],
        "stop_level": _dec(row["stop_level"]),
        "trail_pct": _dec(row["trail_pct"]),
        "high_water_mark": _dec(row["high_water_mark"]),
        "method": row["method"],
        "proposed_level": _dec(row["proposed_level"]),
        "reference_price": _dec(row["reference_price"]),
        "as_of": str(row["as_of"]),
        "source": row["source"],
        "source_ref": row["source_ref"] or None,
        "status": row["status"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "last_bar_checked": row["last_bar_checked"],
        "triggered_session": row["triggered_session"],
        "trigger_price": _dec(row["trigger_price"]),
        "alerted_at": row["alerted_at"],
        "first_session": row["first_session"],
    }


class StopsRepository:
    """SQL persistence for stored stops, scoped by owner and by book.

    `book_id=None` on any method means the owner's real book.
    """

    def __init__(self, portfolio_repository: Optional[PortfolioRepository] = None) -> None:
        self._lots = portfolio_repository or PortfolioRepository()

    def list_stops(self, *, owner: str, book_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every stop of one book, of any status, newest first."""
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_STOPS, {"owner": owner, "book_id": book_id}).fetchall()
        return [_row_to_dict(r) for r in rows]

    def open_lots(self, *, owner: str, book_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every OPEN lot of one book: what its stops are on. Creates no book."""
        return self._lots.list_positions(owner, book_id=book_id)

    def get_stop(
        self, *, owner: str, stop_id: int, book_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """One stop, or None when it isn't in this owner's book."""
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_GET_STOP, {"owner": owner, "stop_id": stop_id, "book_id": book_id}
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def set_stop(
        self, *, owner: str, stop: Dict[str, Any], book_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Store `stop` as its symbol's active stop in one book, replacing the
        one the symbol has. Returns the new stop, with `replaced_stop_id`: the
        id of the stop it took over from, or None.

        `stop` carries `symbol`, `kind`, `stop_level`, `trail_pct`,
        `high_water_mark`, `method`, `proposed_level`, `reference_price`,
        `as_of`, `source`, `source_ref` and `created_at`.

        Raises `SymbolNotHeldError` when the book holds no OPEN lot of the
        symbol, and `BookNotFoundError` for a book that isn't this owner's.
        Either way nothing is written.
        """
        ticker = stop["symbol"]
        with closing(get_connection()) as conn:
            try:
                book = resolve_book_id(conn, owner=owner, book_id=book_id)
                conn.execute(SQL_LOCK_BOOK, {"owner": owner, "book_id": book})
                if not self._lots.open_lots_on(conn, owner, ticker, book_id=book):
                    raise SymbolNotHeldError(
                        f"a stop needs a holding: this book has no open lot of {ticker}"
                    )
                stop_id, replaced = set_stop_on(conn, owner=owner, book_id=book, stop=stop)
                row = conn.execute(
                    SQL_GET_STOP, {"owner": owner, "stop_id": stop_id, "book_id": book}
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            **_row_to_dict(row),
            "replaced_stop_id": replaced,
        }

    def resolve_stop(
        self,
        *,
        owner: str,
        stop_id: int,
        status: str,
        resolved_at: Any,
        book_id: Optional[int] = None,
    ) -> bool:
        """Move an active stop to a final status. Returns False, and changes
        nothing, when the stop is not active or is not in this owner's book."""
        with closing(get_connection()) as conn:
            try:
                resolved = resolve_stop_on(
                    conn, owner=owner, stop_id=stop_id, status=status,
                    resolved_at=resolved_at, book_id=book_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return resolved

    # ------------------------------------------------------------------
    # The bar-close checker (plan step 4.2)
    # ------------------------------------------------------------------
    @staticmethod
    def _write(sql: str, params: Dict[str, Any]) -> int:
        """Run one statement in its own transaction. Returns the row count."""
        with closing(get_connection()) as conn:
            try:
                changed = conn.execute(sql, params).rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return changed

    def record_check(
        self,
        *,
        owner: str,
        stop_id: int,
        last_bar_checked: Any,
        stop_level: Any,
        high_water_mark: Any,
        book_id: Optional[int] = None,
    ) -> bool:
        """Record that an active stop held through the bars up to
        `last_bar_checked`, with the level and mark they left it at. Returns
        False, and changes nothing, when the stop is not active or is not in
        this owner's book."""
        return self._write(SQL_RECORD_CHECK, {
            "owner": owner, "stop_id": stop_id, "last_bar_checked": last_bar_checked,
            "stop_level": stop_level, "high_water_mark": high_water_mark, "book_id": book_id,
        }) == 1

    def trigger_stop(
        self,
        *,
        owner: str,
        stop_id: int,
        stop_level: Any,
        high_water_mark: Any,
        triggered_session: Any,
        trigger_price: Any,
        resolved_at: Any,
        book_id: Optional[int] = None,
    ) -> bool:
        """Mark an active stop `triggered`, on its own: what a real book's
        stop does, where nothing is sold. A paper book's stop is triggered in
        its sale's transaction (`OrderTransaction.trigger_stop`). Returns
        False, and changes nothing, when the stop is not active or is not in
        this owner's book."""
        with closing(get_connection()) as conn:
            try:
                triggered = trigger_stop_on(
                    conn, owner=owner, stop_id=stop_id, stop_level=stop_level,
                    high_water_mark=high_water_mark, triggered_session=triggered_session,
                    trigger_price=trigger_price, resolved_at=resolved_at, book_id=book_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return triggered

    def close_flat_stops(
        self, *, owner: str, resolved_at: Any, book_id: Optional[int] = None
    ) -> int:
        """Close every active stop of one book whose symbol the book no
        longer holds an OPEN lot of. Returns how many were closed."""
        with closing(get_connection()) as conn:
            try:
                closed = close_flat_stops_on(
                    conn, owner=owner, resolved_at=resolved_at, book_id=book_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return closed

    def mark_alerted(
        self, *, owner: str, stop_id: int, alerted_at: Any, book_id: Optional[int] = None
    ) -> bool:
        """Record that a triggered stop's breach alert went out. Returns
        False, and changes nothing, for a stop that isn't triggered, has
        already been alerted for, or is not in this owner's book."""
        return self._write(SQL_MARK_ALERTED, {
            "owner": owner, "stop_id": stop_id, "alerted_at": alerted_at, "book_id": book_id,
        }) == 1
