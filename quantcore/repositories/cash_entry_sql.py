"""The cash rows a lot, a sale or a new paper book writes (#431 step 3.1).

Module-level helpers that run on the caller's connection and never commit, so
a lot and its cash row land in one transaction. They live apart from
`CashRepository` because `PortfolioRepository` and `BooksRepository` both need
them, and `CashRepository` needs `BooksRepository`.

A `buy` row is derived from its lot and a `sell` row from its sale, in SQL,
by the two INSERT ... SELECT statements below. Those two statements are the
only definition of what a trade's cash amount is: the write path, the CSV
import and the backfill script all run them, so they cannot disagree.

- A buy is `-(purchase_price * quantity)` and a sell `sale_price *
  shares_sold`, in USD, to the cent. Gross of fees, like realized P/L.
- A non-USD lot is converted at the rate stored with the trade
  (`fx_rate_at_purchase`, `fx_rate_at_sale`). With no rate the amount is NULL:
  the row is flagged and left out of the balance, never converted at 1.0.
- Only a lot whose `acquisition_type` is BUY writes a buy row. A lot that
  arrived some other way (a transfer, a later seeded paper lot) cost no cash.
- A lot with no trade date, price or quantity writes no row.

`purchase_price` is a REAL column. Cast straight to NUMERIC it keeps six
significant digits (1234.567 becomes 1234.57); cast through TEXT it keeps the
value Python reads.

Every statement binds the owner and restricts the book with the same literal
subquery `PortfolioRepository` uses: `book_id` None is the owner's real book.
"""

from __future__ import annotations

from typing import Any, Optional

OPENING_BALANCE = "opening_balance"
DEPOSIT = "deposit"
WITHDRAWAL = "withdrawal"
BUY = "buy"
SELL = "sell"

SQL_DELETE_LOT_BUY_ENTRY = """
DELETE FROM cash_entries
WHERE owner = :owner
  AND kind = 'buy'
  AND lot_id = :lot_id
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_DELETE_LOT_SELL_ENTRIES = """
DELETE FROM cash_entries
WHERE owner = :owner
  AND kind = 'sell'
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND sale_id IN (
      SELECT ls.sale_id
      FROM lot_sales ls
      JOIN positions p ON p.position_id = ls.lot_id
      WHERE p.owner = :owner
        AND ls.lot_id = :lot_id
        AND p.book_id IN (
            SELECT b.book_id FROM books b
            WHERE b.owner = :owner
              AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                       THEN b.kind = 'real'
                       ELSE b.book_id = CAST(:book_id AS INTEGER) END));
"""

# `:lot_id` None means every lot in the book, which is the backfill and the
# CSV import. A row that is already there is left as it is.
SQL_INSERT_BUY_ENTRIES = """
INSERT INTO cash_entries (owner, book_id, kind, entry_date, amount, lot_id)
SELECT
    p.owner,
    p.book_id,
    'buy',
    p.trade_date,
    -ROUND(
        CAST(CAST(p.purchase_price AS TEXT) AS NUMERIC) * p.quantity
        * CASE WHEN UPPER(TRIM(COALESCE(p.currency, ''))) IN ('', 'USD') THEN 1
               ELSE p.fx_rate_at_purchase END,
        2),
    p.position_id
FROM positions p
WHERE p.owner = :owner
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND p.position_id = COALESCE(CAST(:lot_id AS INTEGER), p.position_id)
  AND p.trade_date IS NOT NULL
  AND p.purchase_price IS NOT NULL
  AND p.quantity IS NOT NULL
  AND UPPER(TRIM(p.acquisition_type)) = 'BUY'
ON CONFLICT (lot_id) WHERE kind = 'buy' DO NOTHING;
"""

SQL_INSERT_SELL_ENTRIES = """
INSERT INTO cash_entries (owner, book_id, kind, entry_date, amount, sale_id)
SELECT
    p.owner,
    p.book_id,
    'sell',
    ls.sale_trade_date,
    ROUND(
        ls.sale_price * ls.shares_sold
        * CASE WHEN UPPER(TRIM(COALESCE(p.currency, ''))) IN ('', 'USD') THEN 1
               ELSE ls.fx_rate_at_sale END,
        2),
    ls.sale_id
FROM lot_sales ls
JOIN positions p ON p.position_id = ls.lot_id
WHERE p.owner = :owner
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND ls.lot_id = COALESCE(CAST(:lot_id AS INTEGER), ls.lot_id)
ON CONFLICT (sale_id) WHERE kind = 'sell' DO NOTHING;
"""

# `:book_id` here is a book the caller has already resolved as this owner's
# (`resolve_book_id`, or the paper book it has just created).
SQL_UPSERT_OPENING_BALANCE = """
INSERT INTO cash_entries (owner, book_id, kind, entry_date, amount, notes)
VALUES (:owner, :book_id, 'opening_balance', :entry_date, :amount, :notes)
ON CONFLICT (book_id) WHERE kind = 'opening_balance'
DO UPDATE SET entry_date = EXCLUDED.entry_date,
              amount = EXCLUDED.amount,
              notes = EXCLUDED.notes
RETURNING entry_id;
"""


def insert_trade_entries(
    conn, *, owner: str, book_id: Optional[int], lot_id: Optional[int] = None
) -> dict:
    """Write the buy and sell rows that are missing, for one lot or (`lot_id`
    None) for every lot in the book. Returns how many of each were written."""
    params = {"owner": owner, "book_id": book_id, "lot_id": lot_id}
    return {
        "buys": conn.execute(SQL_INSERT_BUY_ENTRIES, params).rowcount,
        "sells": conn.execute(SQL_INSERT_SELL_ENTRIES, params).rowcount,
    }


def sync_lot_cash(conn, *, owner: str, book_id: Optional[int], lot_id: int) -> None:
    """Rewrite one lot's buy row and the sell row of each of its sales, from
    the lot and sale rows as they are now on `conn`.

    Called after every write that can change them: a new lot, an edit, a sale,
    a split, a filled FX rate. Deleting first is what lets a lot that no longer
    qualifies (its type is no longer BUY) lose its row.
    """
    params = {"owner": owner, "book_id": book_id, "lot_id": lot_id}
    conn.execute(SQL_DELETE_LOT_BUY_ENTRY, params)
    conn.execute(SQL_DELETE_LOT_SELL_ENTRIES, params)
    insert_trade_entries(conn, owner=owner, book_id=book_id, lot_id=lot_id)


def write_opening_balance(
    conn, *, owner: str, book_id: int, entry_date: Any, amount: Any, notes: Optional[str] = None
) -> int:
    """Set a book's opening balance, replacing the one it has. `book_id` must
    already be resolved as `owner`'s. Returns the row's `entry_id`."""
    cur = conn.execute(SQL_UPSERT_OPENING_BALANCE, {
        "owner": owner,
        "book_id": book_id,
        "entry_date": entry_date,
        "amount": amount,
        "notes": notes,
    })
    return int(cur.fetchone()["entry_id"])
