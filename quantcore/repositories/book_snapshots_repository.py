"""BookSnapshotsRepository — what each book was worth at a session's close,
the `book_value_snapshots` table (#431 phase 3, plan step 5.1).

One row a book and session, written by the daily job. What goes in a row is
`analytics.performance.book_value`.

Rules this module keeps (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument, bound in every statement.
- `book_id` is keyword-only and defaults to None, which means the owner's real
  book. A book that isn't the owner's reads as empty and takes no writes.
- A write never creates a book: a snapshot of a book that doesn't exist
  writes nothing.

SQL only — no analytics.
"""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any, Dict, List, Optional

from quantcore.db import get_connection

_COLUMNS = (
    "cash", "holdings_value", "book_value", "flow", "contributed", "holding_count",
    "unpriced_count", "stale_count", "fx_missing_count",
)

# The book is picked by the same literal subquery every book-scoped statement
# carries, so a book that isn't this owner's inserts nothing. A second run on
# the same session replaces that session's row.
SQL_UPSERT_SNAPSHOT = """
INSERT INTO book_value_snapshots (
    owner, book_id, session_date, cash, holdings_value, book_value, flow,
    contributed, holding_count, unpriced_count, stale_count, fx_missing_count, taken_at)
SELECT
    b.owner, b.book_id, :session_date, :cash, :holdings_value, :book_value, :flow,
    :contributed, :holding_count, :unpriced_count, :stale_count, :fx_missing_count, now()
FROM books b
WHERE b.owner = :owner
  AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
           THEN b.kind = 'real'
           ELSE b.book_id = CAST(:book_id AS INTEGER) END
ON CONFLICT (book_id, session_date) DO UPDATE SET
    cash             = EXCLUDED.cash,
    holdings_value   = EXCLUDED.holdings_value,
    book_value       = EXCLUDED.book_value,
    flow             = EXCLUDED.flow,
    contributed      = EXCLUDED.contributed,
    holding_count    = EXCLUDED.holding_count,
    unpriced_count   = EXCLUDED.unpriced_count,
    stale_count      = EXCLUDED.stale_count,
    fx_missing_count = EXCLUDED.fx_missing_count,
    taken_at         = EXCLUDED.taken_at
RETURNING snapshot_id;
"""

# A bound of NULL leaves that end of the range open. COALESCE rather than an
# OR, which the isolation scans refuse (plan section 7, 2.1). It relies on
# session_date being NOT NULL.
SQL_LIST_SNAPSHOTS = """
SELECT snapshot_id, book_id, session_date, cash, holdings_value, book_value, flow,
       contributed, holding_count, unpriced_count, stale_count, fx_missing_count, taken_at
FROM book_value_snapshots
WHERE owner = :owner
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND session_date >= COALESCE(CAST(:start AS DATE), session_date)
  AND session_date <= COALESCE(CAST(:end AS DATE), session_date)
ORDER BY session_date;
"""

SQL_PREVIOUS_SNAPSHOT = """
SELECT snapshot_id, book_id, session_date, cash, holdings_value, book_value, flow,
       contributed, holding_count, unpriced_count, stale_count, fx_missing_count, taken_at
FROM book_value_snapshots
WHERE owner = :owner
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND session_date < :session
ORDER BY session_date DESC
LIMIT 1;
"""


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "snapshot_id": int(row["snapshot_id"]),
        "book_id": int(row["book_id"]),
        "session_date": row["session_date"],
        "cash": _dec(row["cash"]),
        "holdings_value": _dec(row["holdings_value"]),
        "book_value": _dec(row["book_value"]),
        "flow": _dec(row["flow"]),
        "contributed": _dec(row["contributed"]),
        "holding_count": int(row["holding_count"]),
        "unpriced_count": int(row["unpriced_count"]),
        "stale_count": int(row["stale_count"]),
        "fx_missing_count": int(row["fx_missing_count"]),
        "taken_at": row["taken_at"],
    }


class BookSnapshotsRepository:
    """SQL persistence for book-value snapshots, scoped by owner and by book.

    `book_id=None` on any method means the owner's real book.
    """

    def upsert_snapshot(
        self, *, owner: str, snapshot: Dict[str, Any], book_id: Optional[int] = None
    ) -> Optional[int]:
        """Write one session's snapshot of a book, replacing the one that
        session has. `snapshot` carries `session_date` and the columns
        `analytics.performance.book_value` returns. Returns the row's id, or
        None when the book isn't this owner's (or, for the real book, doesn't
        exist yet): nothing is written then."""
        params = {key: snapshot[key] for key in _COLUMNS}
        params.update(owner=owner, book_id=book_id, session_date=snapshot["session_date"])
        with closing(get_connection()) as conn:
            try:
                row = conn.execute(SQL_UPSERT_SNAPSHOT, params).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(row["snapshot_id"]) if row is not None else None

    def list_snapshots(
        self,
        *,
        owner: str,
        start: Any = None,
        end: Any = None,
        book_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """A book's snapshots from `start` to `end` inclusive (either may be
        None for an open end), oldest first."""
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_SNAPSHOTS, {
                "owner": owner, "book_id": book_id, "start": start, "end": end,
            }).fetchall()
        return [_row_to_dict(r) for r in rows]

    def previous_snapshot(
        self, *, owner: str, session: Any, book_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """The book's newest snapshot before `session`, or None when it has
        none: what the next row's flow is measured from."""
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_PREVIOUS_SNAPSHOT, {
                "owner": owner, "book_id": book_id, "session": session,
            }).fetchone()
        return _row_to_dict(row) if row is not None else None
