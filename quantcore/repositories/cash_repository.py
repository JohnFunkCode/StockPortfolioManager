"""CashRepository — a book's cash ledger, the `cash_entries` table (#431 step 3.1).

One signed USD amount per row, positive when cash comes in. The owner enters
the opening balance, deposits and withdrawals here. The `buy` and `sell` rows
are written by `PortfolioRepository`, in the transaction of the lot or sale
they belong to (`cash_entry_sql`).

Rules this module keeps (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument, bound in every statement.
- `book_id` is keyword-only and defaults to None, which means the owner's real
  book. A book that isn't the owner's reads as empty and takes no writes.
- A read never creates a book. The first write to the real book does.

SQL only — no analytics. The running balance is `analytics.cash.cash_ledger`.
"""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any, Dict, List, Optional

from quantcore.db import get_connection
from quantcore.repositories import cash_entry_sql
from quantcore.repositories.books_repository import resolve_book_id

# Each buy or sell row with the lot or sale it came from, for its description.
# The two positions joins are restricted to the owner and the book like the
# ledger rows themselves, so a row can only ever describe this book's lot.
SQL_LIST_ENTRIES = """
SELECT
    ce.entry_id   AS entry_id,
    ce.kind       AS kind,
    ce.entry_date AS entry_date,
    ce.amount     AS amount,
    ce.lot_id     AS lot_id,
    ce.sale_id    AS sale_id,
    ce.notes      AS notes,
    s.ticker      AS symbol,
    COALESCE(ls.shares_sold, bp.quantity) AS shares,
    COALESCE(ls.sale_price, CAST(CAST(bp.purchase_price AS TEXT) AS NUMERIC)) AS price,
    COALESCE(bp.currency, sp.currency) AS currency
FROM cash_entries ce
LEFT JOIN lot_sales ls ON ls.sale_id = ce.sale_id
LEFT JOIN positions bp
       ON bp.position_id = ce.lot_id
      AND bp.owner = :owner
      AND bp.book_id IN (
          SELECT b.book_id FROM books b
          WHERE b.owner = :owner
            AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                     THEN b.kind = 'real'
                     ELSE b.book_id = CAST(:book_id AS INTEGER) END)
LEFT JOIN positions sp
       ON sp.position_id = ls.lot_id
      AND sp.owner = :owner
      AND sp.book_id IN (
          SELECT b.book_id FROM books b
          WHERE b.owner = :owner
            AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                     THEN b.kind = 'real'
                     ELSE b.book_id = CAST(:book_id AS INTEGER) END)
LEFT JOIN symbols s ON s.symbol_id = COALESCE(bp.symbol_id, sp.symbol_id)
WHERE ce.owner = :owner
  AND ce.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY ce.entry_date, ce.entry_id;
"""

# `:book_id` is a book `resolve_book_id` has just returned as this owner's.
SQL_INSERT_ENTRY = """
INSERT INTO cash_entries (owner, book_id, kind, entry_date, amount, notes)
VALUES (:owner, :book_id, :kind, :entry_date, :amount, :notes)
RETURNING entry_id;
"""

# Only what the owner typed can be deleted. A buy or sell row goes with its
# lot or sale, and an opening balance is replaced, never removed.
SQL_DELETE_MANUAL_ENTRY = """
DELETE FROM cash_entries
WHERE owner = :owner
  AND entry_id = :entry_id
  AND kind IN ('deposit', 'withdrawal')
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

# A paper book made before the ledger existed has no opening row. Its opening
# balance is its starting cash on the day it was made (New York time, the
# market's date). A real book never gets one this way: its owner enters it.
SQL_INSERT_PAPER_OPENING = """
INSERT INTO cash_entries (owner, book_id, kind, entry_date, amount)
SELECT
    b.owner,
    b.book_id,
    'opening_balance',
    CAST(b.created_at AT TIME ZONE 'America/New_York' AS DATE),
    b.starting_cash
FROM books b
WHERE b.owner = :owner
  AND b.kind = 'paper'
  AND b.book_id = CAST(:book_id AS INTEGER)
ON CONFLICT (book_id) WHERE kind = 'opening_balance' DO NOTHING;
"""

MANUAL_KINDS = (cash_entry_sql.DEPOSIT, cash_entry_sql.WITHDRAWAL)


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "entry_id": int(row["entry_id"]),
        "kind": row["kind"],
        "entry_date": str(row["entry_date"]),
        "amount": _dec(row["amount"]),
        "lot_id": int(row["lot_id"]) if row["lot_id"] is not None else None,
        "sale_id": int(row["sale_id"]) if row["sale_id"] is not None else None,
        "notes": row["notes"] or None,
        "symbol": (row["symbol"] or "").strip().upper() or None,
        "shares": _dec(row["shares"]),
        "price": _dec(row["price"]),
        "currency": (row["currency"] or "").strip().upper() or None,
    }


def list_entries_on(conn, *, owner: str, book_id: Optional[int]) -> List[Dict[str, Any]]:
    """Every cash row of one book as `conn`'s transaction sees it, oldest
    first. A paper order's fill reads the ledger this way, with its own buy
    row already written, before it decides whether the book can afford it."""
    rows = conn.execute(SQL_LIST_ENTRIES, {"owner": owner, "book_id": book_id}).fetchall()
    return [_row_to_dict(r) for r in rows]


class CashRepository:
    """SQL persistence for a book's cash ledger, scoped by owner and by book.

    `book_id=None` on any method means the owner's real book.
    """

    def list_entries(self, *, owner: str, book_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every cash row of one book, oldest first. A buy or sell row carries
        the `symbol`, `shares`, `price` and `currency` of its lot or sale."""
        with closing(get_connection()) as conn:
            return list_entries_on(conn, owner=owner, book_id=book_id)

    def set_opening_balance(
        self,
        *,
        owner: str,
        entry_date: Any,
        amount: Any,
        notes: Optional[str] = None,
        book_id: Optional[int] = None,
    ) -> int:
        """Set the book's opening balance, replacing the one it has. Returns
        the row's `entry_id`. Raises `BookNotFoundError` for a book that isn't
        this owner's."""
        with closing(get_connection()) as conn:
            try:
                book = resolve_book_id(conn, owner=owner, book_id=book_id)
                entry_id = cash_entry_sql.write_opening_balance(
                    conn, owner=owner, book_id=book, entry_date=entry_date,
                    amount=amount, notes=notes,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return entry_id

    def add_entry(
        self,
        *,
        owner: str,
        kind: str,
        entry_date: Any,
        amount: Any,
        notes: Optional[str] = None,
        book_id: Optional[int] = None,
    ) -> int:
        """Record a deposit or a withdrawal. `amount` is signed: positive for a
        deposit, negative for a withdrawal. Returns the new `entry_id`.

        Raises `ValueError` for any other kind, and `BookNotFoundError` for a
        book that isn't this owner's.
        """
        if kind not in MANUAL_KINDS:
            raise ValueError(f"a cash entry can be a deposit or a withdrawal, not {kind!r}")
        with closing(get_connection()) as conn:
            try:
                book = resolve_book_id(conn, owner=owner, book_id=book_id)
                cur = conn.execute(SQL_INSERT_ENTRY, {
                    "owner": owner,
                    "book_id": book,
                    "kind": kind,
                    "entry_date": entry_date,
                    "amount": amount,
                    "notes": notes,
                })
                entry_id = int(cur.fetchone()["entry_id"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return entry_id

    def delete_entry(self, *, owner: str, entry_id: int, book_id: Optional[int] = None) -> bool:
        """Delete a deposit or a withdrawal. Returns True if a row was deleted.

        False for another owner's or another book's row, and for a buy, a sell
        or an opening balance.
        """
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(
                    SQL_DELETE_MANUAL_ENTRY,
                    {"owner": owner, "entry_id": entry_id, "book_id": book_id},
                )
                deleted = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return deleted > 0

    def backfill_entries(
        self, *, owner: str, apply: bool = False, book_id: Optional[int] = None
    ) -> Dict[str, int]:
        """Write the cash rows one book is missing: a buy row per lot, a sell
        row per sale and, for a paper book, its opening balance.

        Returns how many of each were written (`buys`, `sells`, `openings`).
        Without `apply` the same statements run and are rolled back, so a dry
        run reports exactly what an applied one would write. A row that is
        already there is never changed.
        """
        with closing(get_connection()) as conn:
            try:
                written = cash_entry_sql.insert_trade_entries(conn, owner=owner, book_id=book_id)
                written["openings"] = conn.execute(
                    SQL_INSERT_PAPER_OPENING, {"owner": owner, "book_id": book_id}
                ).rowcount
                if apply:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback()
                raise
        return written
