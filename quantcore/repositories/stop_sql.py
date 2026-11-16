"""Stop statements that run on the caller's connection (#431 phase 2, #221;
plan step 4.2).

Module-level helpers that never commit, so a stop's change of status lands in
the same transaction as the trade that causes it. They live apart from
`StopsRepository` because `OrdersRepository` and `PortfolioRepository` both
need them, and `StopsRepository` needs both of those.

- A paper book's stop is marked `triggered` in the transaction that writes
  its sale (`trigger_stop_on`).
- A sale that leaves a book with no OPEN lot of a symbol closes the symbol's
  stop in the sale's own transaction (`close_flat_stops_on`).

Every statement binds the owner and restricts the book with the same literal
subquery `PortfolioRepository` uses: `book_id` None is the owner's real book.
A stop leaves `active` only through `UPDATE ... WHERE status = 'active'`.
"""

from __future__ import annotations

from typing import Any, Optional

# The one statement that takes a stop out of `active` by its id, for every
# status but `triggered`. It matches no row for a stop that is already final.
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

# A closed bar reached the stop. The level and the mark are the ones it was
# reached with: a trailing stop may have risen on the bars before.
SQL_TRIGGER_STOP = """
UPDATE stops
SET status = 'triggered',
    resolved_at = :resolved_at,
    stop_level = :stop_level,
    high_water_mark = :high_water_mark,
    last_bar_checked = :triggered_session,
    triggered_session = :triggered_session,
    trigger_price = :trigger_price
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

# Every active stop of one book whose symbol the book no longer holds an OPEN
# lot of. The lots are restricted to the same owner and the same book.
SQL_CLOSE_FLAT_STOPS = """
UPDATE stops
SET status = 'closed',
    resolved_at = :resolved_at
WHERE owner = :owner
  AND status = 'active'
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND symbol_id NOT IN (
      SELECT p.symbol_id FROM positions p
      WHERE p.owner = :owner
        AND p.status = 'OPEN'
        AND p.symbol_id IS NOT NULL
        AND p.book_id IN (
            SELECT b.book_id FROM books b
            WHERE b.owner = :owner
              AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                       THEN b.kind = 'real'
                       ELSE b.book_id = CAST(:book_id AS INTEGER) END));
"""


def resolve_stop_on(
    conn, *, owner: str, stop_id: int, status: str, resolved_at: Any,
    book_id: Optional[int] = None,
) -> bool:
    """Move an active stop to `cancelled`, `replaced` or `closed`. False, and
    nothing changed, when it is not active or not in this owner's book."""
    cur = conn.execute(SQL_RESOLVE_STOP, {
        "owner": owner, "stop_id": stop_id, "status": status,
        "resolved_at": resolved_at, "book_id": book_id,
    })
    return cur.rowcount == 1


def trigger_stop_on(
    conn,
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
    """Mark an active stop `triggered`, with the bar and the price it was
    reached at. False, and nothing changed, when it is not active or not in
    this owner's book."""
    cur = conn.execute(SQL_TRIGGER_STOP, {
        "owner": owner, "stop_id": stop_id, "stop_level": stop_level,
        "high_water_mark": high_water_mark, "triggered_session": triggered_session,
        "trigger_price": trigger_price, "resolved_at": resolved_at, "book_id": book_id,
    })
    return cur.rowcount == 1


def close_flat_stops_on(
    conn, *, owner: str, resolved_at: Any, book_id: Optional[int] = None
) -> int:
    """Close every active stop of one book whose symbol the book holds no
    OPEN lot of, as `conn`'s transaction sees the lots. Returns how many."""
    cur = conn.execute(SQL_CLOSE_FLAT_STOPS, {
        "owner": owner, "resolved_at": resolved_at, "book_id": book_id,
    })
    return cur.rowcount
