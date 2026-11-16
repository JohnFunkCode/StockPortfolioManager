"""StopsRepository — stored stops, the `stops` table (#431 phase 2, #221; plan
step 4.1).

A stop is set on a symbol in one of an owner's books. It is `active` while it
is live; `triggered`, `cancelled`, `replaced` and `closed` are final, and no
statement here moves a stop out of one: every change of status is
`UPDATE ... WHERE status = 'active'`.

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
    st.resolved_at     AS resolved_at
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
    st.resolved_at     AS resolved_at
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

# `:book_id` is the book `set_stop` has resolved as this owner's and locked.
SQL_INSERT_STOP = """
INSERT INTO stops (
    owner, book_id, symbol_id, kind, stop_level, trail_pct, high_water_mark,
    method, proposed_level, reference_price, as_of, source, source_ref, created_at
) VALUES (
    :owner, :book_id, (SELECT symbol_id FROM symbols WHERE ticker = :ticker), :kind,
    :stop_level, :trail_pct, :high_water_mark,
    :method, :proposed_level, :reference_price, :as_of, :source, :source_ref, :created_at
)
RETURNING stop_id;
"""

# The symbol's live stop gives way to the one about to be inserted. It
# matches at most one row: the unique index allows one active stop.
SQL_REPLACE_ACTIVE_STOP = """
UPDATE stops
SET status = 'replaced',
    resolved_at = :resolved_at
WHERE owner = :owner
  AND status = 'active'
  AND symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker)
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
RETURNING stop_id;
"""

# The one statement that takes a stop out of `active` by its id. It matches
# no row for a stop that is already final.
SQL_RESOLVE_STOP = """
UPDATE stops
SET status = :status,
    resolved_at = :resolved_at
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
                scope = {"owner": owner, "book_id": book, "ticker": ticker}
                replaced = conn.execute(
                    SQL_REPLACE_ACTIVE_STOP, {**scope, "resolved_at": stop["created_at"]}
                ).fetchone()
                stop_id = int(conn.execute(SQL_INSERT_STOP, {
                    **scope,
                    "kind": stop["kind"],
                    "stop_level": stop["stop_level"],
                    "trail_pct": stop.get("trail_pct"),
                    "high_water_mark": stop.get("high_water_mark"),
                    "method": stop["method"],
                    "proposed_level": stop.get("proposed_level"),
                    "reference_price": stop.get("reference_price"),
                    "as_of": stop["as_of"],
                    "source": stop["source"],
                    "source_ref": stop.get("source_ref"),
                    "created_at": stop["created_at"],
                }).fetchone()["stop_id"])
                row = conn.execute(
                    SQL_GET_STOP, {"owner": owner, "stop_id": stop_id, "book_id": book}
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            **_row_to_dict(row),
            "replaced_stop_id": int(replaced["stop_id"]) if replaced is not None else None,
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
                cur = conn.execute(SQL_RESOLVE_STOP, {
                    "owner": owner, "stop_id": stop_id, "status": status,
                    "resolved_at": resolved_at, "book_id": book_id,
                })
                resolved = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return resolved == 1
