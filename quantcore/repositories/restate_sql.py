"""Marking a book for restatement (#431 phase 3, plan step 5.2).

A book's daily snapshots are rebuilt from the session a later write made
wrong: GIPS corrects history and restates the returns, with a flow on its real
date (plan decisions 85 and 88, as John revised them on 2026-11-17). Every
write the owner makes to a book's cash or lots calls `mark_restate` on its own
connection, before it commits, with the dates the write touched: so the mark
lands with the change, or not at all.

- **A mark is set only when it is needed**: a date on or before the book's
  newest snapshot. A write dated after it changes nothing stored, and a book
  with no snapshots has nothing to restate.
- **A mark only moves earlier.** `restate_from` becomes the earlier of itself
  and the date, so two writes before a rebuild restate from the first.
- **An order's fill never marks** (it is dated today, after every stored
  row), so `OrdersService` doesn't call this.

The daily job's snapshot step rebuilds a marked book and clears the mark
(`BookSnapshotsRepository.clear_restate_mark`).

Every statement binds the owner and restricts the book with the same literal
subquery `PortfolioRepository` uses: `book_id` None is the owner's real book.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable, Optional

from quantcore.analytics.fx import as_date

# The book's newest snapshot is read through the same book subquery as the
# book itself, so it is this owner's and this book's. MAX over no rows is
# NULL, the comparison is NULL, and the book isn't marked.
SQL_MARK_RESTATE = """
UPDATE books
SET restate_from = LEAST(COALESCE(restate_from, CAST(:on AS DATE)), CAST(:on AS DATE))
WHERE owner = :owner
  AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
           THEN kind = 'real'
           ELSE book_id = CAST(:book_id AS INTEGER) END
  AND CAST(:on AS DATE) <= (
      SELECT MAX(s.session_date)
      FROM book_value_snapshots s
      WHERE s.owner = :owner
        AND s.book_id IN (
            SELECT b.book_id FROM books b
            WHERE b.owner = :owner
              AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                       THEN b.kind = 'real'
                       ELSE b.book_id = CAST(:book_id AS INTEGER) END));
"""


def earliest(days: Iterable[Any]) -> Optional[datetime.date]:
    """The earliest of `days` that is a date (a `date`, a `datetime` or an ISO
    string); None when none is."""
    found = [day for day in (as_date(d) for d in days) if day is not None]
    return min(found) if found else None


def lot_day(lot: Any) -> Optional[datetime.date]:
    """The date a lot entered its book: its trade date, else its purchase
    date. None for no lot, or a lot with neither."""
    if not lot:
        return None
    return as_date(lot.get("trade_date")) or as_date(lot.get("purchase_date"))


def mark_restate(
    conn, *, owner: str, book_id: Optional[int], days: Iterable[Any]
) -> bool:
    """Mark one of `owner`'s books for restatement from the earliest of
    `days`, when that is on or before the book's newest snapshot. Runs on the
    caller's connection and never commits. Returns True when the book was
    marked (or already marked from that date or earlier)."""
    on = earliest(days)
    if on is None:
        return False
    cur = conn.execute(SQL_MARK_RESTATE, {"owner": owner, "book_id": book_id, "on": on})
    return cur.rowcount > 0
