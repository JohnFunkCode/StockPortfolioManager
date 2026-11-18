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
- **A rebuild runs under the book's row lock** (`restating`, #431 step 5.2).
  A write that marks the book for restatement updates the same row in its own
  transaction, so it either committed before the rebuild read anything or
  waits for the rebuild to commit and marks the book again.
- A rebuilt row that differs from the stored one is replaced and stamped
  `restated_at`; one that comes out the same is left alone, `taken_at` and all.

SQL only — no analytics.
"""

from __future__ import annotations

import datetime
from contextlib import closing, contextmanager
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Sequence

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
       contributed, holding_count, unpriced_count, stale_count, fx_missing_count, taken_at,
       restated_at
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
       contributed, holding_count, unpriced_count, stale_count, fx_missing_count, taken_at,
       restated_at
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


# A rebuilt row (#431 step 5.2). A new session is inserted with no
# restated_at: nothing was corrected. A stored row is replaced only when a
# value differs, and then says when. The WHERE on DO UPDATE leaves an
# unchanged row untouched, and RETURNING gives nothing for it.
SQL_WRITE_RESTATED = """
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
    restated_at      = now()
WHERE (book_value_snapshots.cash, book_value_snapshots.holdings_value,
       book_value_snapshots.book_value, book_value_snapshots.flow,
       book_value_snapshots.contributed, book_value_snapshots.holding_count,
       book_value_snapshots.unpriced_count, book_value_snapshots.stale_count,
       book_value_snapshots.fx_missing_count)
      IS DISTINCT FROM
      (EXCLUDED.cash, EXCLUDED.holdings_value, EXCLUDED.book_value, EXCLUDED.flow,
       EXCLUDED.contributed, EXCLUDED.holding_count, EXCLUDED.unpriced_count,
       EXCLUDED.stale_count, EXCLUDED.fx_missing_count)
RETURNING snapshot_id, restated_at;
"""

# A session the book has no value for any more (its opening balance moved
# later) loses its row.
SQL_DELETE_SNAPSHOT = """
DELETE FROM book_value_snapshots
WHERE owner = :owner
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND session_date = :session;
"""

# The book's row, locked for the rebuild, with its mark.
SQL_LOCK_RESTATE_MARK = """
SELECT book_id, restate_from
FROM books
WHERE owner = :owner
  AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
           THEN kind = 'real'
           ELSE book_id = CAST(:book_id AS INTEGER) END
FOR UPDATE;
"""

SQL_GET_RESTATE_MARK = """
SELECT restate_from
FROM books
WHERE owner = :owner
  AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
           THEN kind = 'real'
           ELSE book_id = CAST(:book_id AS INTEGER) END;
"""

# Cleared only when it still holds the date the rebuild started from. Under
# the row lock nothing can move it, so this is the plan's belt to that brace.
SQL_CLEAR_RESTATE_MARK = """
UPDATE books
SET restate_from = NULL
WHERE owner = :owner
  AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
           THEN kind = 'real'
           ELSE book_id = CAST(:book_id AS INTEGER) END
  AND restate_from = CAST(:marked_from AS DATE);
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
        "restated_at": row["restated_at"],
    }


class RestateTransaction:
    """One book's rebuild, on a connection holding the book's row lock.
    `book_id` is the resolved id (None when the book isn't the owner's) and
    `mark` the book's `restate_from` as the lock found it."""

    def __init__(self, conn, owner: str, book_id: Optional[int],
                 resolved: Optional[int], mark: Optional[datetime.date]) -> None:
        self._conn = conn
        self._owner = owner
        self._book_id = book_id
        self.resolved_book_id = resolved
        self.mark = mark

    def _params(self, **extra: Any) -> Dict[str, Any]:
        return {"owner": self._owner, "book_id": self._book_id, **extra}

    def write(self, snapshots: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        """Write rebuilt rows. Returns how many were `inserted` (a session
        with no row), `restated` (a row that changed) and `unchanged`."""
        counts = {"inserted": 0, "restated": 0, "unchanged": 0}
        for snapshot in snapshots:
            params = self._params(**{key: snapshot[key] for key in _COLUMNS})
            params["session_date"] = snapshot["session_date"]
            row = self._conn.execute(SQL_WRITE_RESTATED, params).fetchone()
            if row is None:
                counts["unchanged"] += 1
            elif row["restated_at"] is None:
                counts["inserted"] += 1
            else:
                counts["restated"] += 1
        return counts

    def delete(self, sessions: Sequence[datetime.date]) -> int:
        """Remove the rows of sessions the book has no value for. Returns how
        many there were."""
        return sum(
            self._conn.execute(SQL_DELETE_SNAPSHOT, self._params(session=s)).rowcount
            for s in sessions
        )

    def clear_mark(self) -> bool:
        """Clear the mark the rebuild started from. True when it was."""
        if self.mark is None:
            return False
        cur = self._conn.execute(SQL_CLEAR_RESTATE_MARK, self._params(marked_from=self.mark))
        return cur.rowcount > 0


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

    def restate_mark(
        self, *, owner: str, book_id: Optional[int] = None
    ) -> Optional[datetime.date]:
        """The session a book is marked to restate from, or None."""
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_GET_RESTATE_MARK, {
                "owner": owner, "book_id": book_id,
            }).fetchone()
        return row["restate_from"] if row is not None else None

    @contextmanager
    def restating(
        self, *, owner: str, book_id: Optional[int] = None, apply: bool = True
    ) -> Iterator[RestateTransaction]:
        """Hold one of `owner`'s books' row lock while its snapshots are
        rebuilt, and commit what the rebuild wrote when the block ends. Without
        `apply`, everything is rolled back, so a dry run reports what an
        applied one would write. An error rolls back too."""
        with closing(get_connection()) as conn:
            try:
                row = conn.execute(SQL_LOCK_RESTATE_MARK, {
                    "owner": owner, "book_id": book_id,
                }).fetchone()
                yield RestateTransaction(
                    conn, owner, book_id,
                    int(row["book_id"]) if row is not None else None,
                    row["restate_from"] if row is not None else None,
                )
                if apply:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback()
                raise

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
