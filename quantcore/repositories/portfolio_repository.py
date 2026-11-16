"""PortfolioRepository — owner-scoped CRUD over the `positions` table.

Phase 1 Step 6 (docs/proposals/phase1-migration-plan.md): the `positions`
table becomes the source of truth for portfolio holdings, replacing direct
reads of portfolio.csv. CSV becomes an import format (one file per owner,
full-sync/replace semantics).

This repository contains SQL only — no analytics. Rows are returned as plain
dicts matching the shape the REST layer previously produced from the CSV
(`name`, `symbol`, `purchase_price`, `quantity`, `purchase_date`, `currency`,
`sale_price`, `sale_date`, `source="portfolio"`, `tags=[]`) so adapters and the
WebUI are unaffected.

Every statement on `positions` is restricted to one **book** as well as one
owner (#431). `book_id` is a keyword-only argument that defaults to None, and
None means the owner's REAL book: the restriction is the same literal subquery
in every statement, so a caller that says nothing can never read or change a
paper book, and a `book_id` that isn't this owner's matches no rows.
`tests/test_book_isolation_coverage.py` fails on a statement without it.

Every write that can change what a lot cost or a sale brought in rewrites
that lot's rows in the cash ledger before it commits (#431 step 3.1,
`cash_entry_sql.sync_lot_cash`). A deleted lot takes its rows with it through
the foreign keys.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

from quantcore.db import get_connection
from quantcore.repositories.books_repository import resolve_book_id
from quantcore.repositories.cash_entry_sql import insert_trade_entries, sync_lot_cash
from quantcore.repositories.stop_sql import close_flat_stops_on

# What prompted a sale (`lot_sales.source`, #431 step 3.3). A sale the owner
# records is manual. A paper order passes its own source through.
MANUAL_SOURCE = "manual"

MUTABLE_LOT_COLUMNS = {
    "name", "purchase_price", "quantity", "purchase_date", "currency",
    "sale_price", "sale_date", "status", "parent_lot_id", "trade_date",
    "fees", "acquisition_type", "account", "notes", "fx_rate_at_purchase",
}

SQL_INSERT_SYMBOL = """
INSERT INTO symbols (ticker, created_at)
VALUES (:ticker, :created_at)
ON CONFLICT(ticker) DO NOTHING;
"""

SQL_GET_SYMBOL_ID = """
SELECT symbol_id FROM symbols WHERE ticker = :ticker;
"""

SQL_LIST_POSITIONS = """
SELECT
    p.position_id    AS position_id,
    p.name           AS name,
    s.ticker         AS symbol,
    p.purchase_price AS purchase_price,
    p.quantity       AS quantity,
    p.purchase_date  AS purchase_date,
    p.currency       AS currency,
    p.sale_price     AS sale_price,
    p.sale_date      AS sale_date,
    p.status         AS status,
    p.parent_lot_id  AS parent_lot_id,
    p.trade_date     AS trade_date,
    p.fees           AS fees,
    p.acquisition_type AS acquisition_type,
    p.account        AS account,
    p.notes          AS notes
FROM positions p
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND p.status = :status
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY s.ticker, p.purchase_date;
"""

# Cross-owner on purpose (the daily job's walk). Real books only: an owner who
# holds nothing but paper positions has no symbols the job should capture.
SQL_LIST_OWNERS = """
SELECT DISTINCT p.owner AS owner
FROM positions p
JOIN books b ON b.book_id = p.book_id
WHERE b.kind = 'real'
ORDER BY p.owner;
"""

SQL_DELETE_OWNER_POSITIONS = """
DELETE FROM positions
WHERE owner = :owner
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_DELETE_OWNER_SYMBOL = """
DELETE FROM positions
WHERE owner = :owner
  AND symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker)
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_COUNT_OWNER_SYMBOL = """
SELECT COUNT(*) AS n
FROM positions
WHERE owner = :owner
  AND symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker)
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_INSERT_POSITION = """
INSERT INTO positions (
    owner, book_id, symbol_id, name,
    purchase_price, quantity, purchase_date, currency, sale_price, sale_date,
    opened_at, entry_price, shares, cost_basis_total,
    status, parent_lot_id, trade_date, fees, acquisition_type, account, notes,
    fx_rate_at_purchase, order_id
) VALUES (
    :owner, :book_id, :symbol_id, :name,
    :purchase_price, :quantity, :purchase_date, :currency, :sale_price, :sale_date,
    :opened_at, :entry_price, :shares, :cost_basis_total,
    :status, :parent_lot_id, :trade_date, :fees, :acquisition_type, :account, :notes,
    :fx_rate_at_purchase, :order_id
)
RETURNING position_id;
"""

SQL_GET_LOT = """
SELECT
    p.position_id    AS position_id,
    p.name           AS name,
    s.ticker         AS symbol,
    p.purchase_price AS purchase_price,
    p.quantity       AS quantity,
    p.purchase_date  AS purchase_date,
    p.currency       AS currency,
    p.sale_price     AS sale_price,
    p.sale_date      AS sale_date,
    p.status         AS status,
    p.parent_lot_id  AS parent_lot_id,
    p.trade_date     AS trade_date,
    p.fees           AS fees,
    p.acquisition_type AS acquisition_type,
    p.account        AS account,
    p.notes          AS notes,
    p.book_id        AS book_id,
    p.fx_rate_at_purchase AS fx_rate_at_purchase,
    p.order_id       AS order_id
FROM positions p
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND p.position_id = :lot_id
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_DELETE_LOT = """
DELETE FROM positions
WHERE owner = :owner
  AND position_id = :lot_id
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END);
"""

SQL_LIST_LOTS_FOR_SYMBOL = """
SELECT
    p.position_id    AS position_id,
    p.name           AS name,
    s.ticker         AS symbol,
    p.purchase_price AS purchase_price,
    p.quantity       AS quantity,
    p.purchase_date  AS purchase_date,
    p.currency       AS currency,
    p.sale_price     AS sale_price,
    p.sale_date      AS sale_date,
    p.status         AS status,
    p.parent_lot_id  AS parent_lot_id,
    p.trade_date     AS trade_date,
    p.fees           AS fees,
    p.acquisition_type AS acquisition_type,
    p.account        AS account,
    p.notes          AS notes
FROM positions p
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND s.ticker = :ticker{status_clause}
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY p.trade_date, p.position_id;
"""

SQL_INSERT_SALE = """
INSERT INTO lot_sales (
    lot_id, shares_sold, sale_price, sale_trade_date, fees, allocation_method, notes,
    fx_rate_at_sale, order_id, source, source_ref
) VALUES (
    :lot_id, :shares_sold, :sale_price, :sale_trade_date, :fees, :allocation_method, :notes,
    :fx_rate_at_sale, :order_id, :source, :source_ref
)
RETURNING sale_id;
"""

# lot_sales has no owner or book column: both are always decided through the
# lot's positions row (issues #266, #431).
SQL_LIST_SALES = """
SELECT
    ls.sale_id           AS sale_id,
    ls.lot_id            AS lot_id,
    s.ticker             AS symbol,
    ls.shares_sold       AS shares_sold,
    ls.sale_price        AS sale_price,
    ls.sale_trade_date   AS sale_trade_date,
    ls.fees              AS fees,
    ls.allocation_method AS allocation_method,
    ls.notes             AS notes
FROM lot_sales ls
JOIN positions p ON p.position_id = ls.lot_id
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner{symbol_clause}
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY ls.sale_trade_date, ls.sale_id;
"""

# Realized P/L (#235): each sale with the basis side of the lot it closed. The
# window is two bound dates, both optional, so the period filter is in the
# query and nothing is formatted into the SQL. A missing bound falls back to
# the row's own date through COALESCE (the column is NOT NULL); an OR would
# do the same, but the isolation scans refuse one in a WHERE clause. A sale
# belongs to a period by its sale_trade_date, never by when the row was written.
SQL_LIST_REALIZED_SALES = """
SELECT
    ls.sale_id            AS sale_id,
    ls.lot_id             AS lot_id,
    s.ticker              AS symbol,
    ls.shares_sold        AS shares_sold,
    ls.sale_price         AS sale_price,
    ls.sale_trade_date    AS sale_trade_date,
    ls.allocation_method  AS allocation_method,
    ls.notes              AS notes,
    p.purchase_price      AS purchase_price,
    p.trade_date          AS trade_date,
    p.currency            AS currency,
    p.account             AS account,
    p.fx_rate_at_purchase AS fx_rate_at_purchase,
    ls.fx_rate_at_sale    AS fx_rate_at_sale
FROM lot_sales ls
JOIN positions p ON p.position_id = ls.lot_id
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
  AND ls.sale_trade_date >= COALESCE(CAST(:start_date AS DATE), ls.sale_trade_date)
  AND ls.sale_trade_date <= COALESCE(CAST(:end_date AS DATE), ls.sale_trade_date)
ORDER BY ls.sale_trade_date, ls.sale_id;
"""

SQL_UPDATE_SALE_NOTES = """
UPDATE lot_sales SET notes = :notes
WHERE sale_id = :sale_id
  AND lot_id IN (
      SELECT position_id FROM positions
      WHERE owner = :owner
        AND book_id IN (
            SELECT b.book_id FROM books b
            WHERE b.owner = :owner
              AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                       THEN b.kind = 'real'
                       ELSE b.book_id = CAST(:book_id AS INTEGER) END));
"""

# FX backfill (#235 step 2.3): the non-USD rows that still have no rate, and
# the writes that fill one in. A blank currency is USD, and `<>` is not true
# of a NULL, so a lot with no currency is never listed. Each write is
# restricted to rows whose rate is still NULL: a backfill fills a gap and
# never replaces a rate captured when the trade was recorded.
SQL_LIST_LOTS_MISSING_FX = """
SELECT
    p.position_id AS lot_id,
    s.ticker      AS symbol,
    p.currency    AS currency,
    p.trade_date  AS trade_date
FROM positions p
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND p.fx_rate_at_purchase IS NULL
  AND UPPER(TRIM(p.currency)) <> 'USD'
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY p.position_id;
"""

SQL_LIST_SALES_MISSING_FX = """
SELECT
    ls.sale_id         AS sale_id,
    ls.lot_id          AS lot_id,
    s.ticker           AS symbol,
    p.currency         AS currency,
    ls.sale_trade_date AS sale_trade_date
FROM lot_sales ls
JOIN positions p ON p.position_id = ls.lot_id
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND ls.fx_rate_at_sale IS NULL
  AND UPPER(TRIM(p.currency)) <> 'USD'
  AND p.book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
ORDER BY ls.sale_id;
"""

SQL_FILL_LOT_FX_RATE = """
UPDATE positions SET fx_rate_at_purchase = :rate
WHERE owner = :owner
  AND position_id = :lot_id
  AND fx_rate_at_purchase IS NULL
  AND book_id IN (
      SELECT b.book_id FROM books b
      WHERE b.owner = :owner
        AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                 THEN b.kind = 'real'
                 ELSE b.book_id = CAST(:book_id AS INTEGER) END)
RETURNING position_id AS lot_id;
"""

SQL_FILL_SALE_FX_RATE = """
UPDATE lot_sales SET fx_rate_at_sale = :rate
WHERE sale_id = :sale_id
  AND fx_rate_at_sale IS NULL
  AND lot_id IN (
      SELECT position_id FROM positions
      WHERE owner = :owner
        AND book_id IN (
            SELECT b.book_id FROM books b
            WHERE b.owner = :owner
              AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                       THEN b.kind = 'real'
                       ELSE b.book_id = CAST(:book_id AS INTEGER) END))
RETURNING lot_id;
"""

# A lot whose currency was corrected: its sales' rates were for the old
# currency, so they are cleared with the edit and looked up again.
SQL_CLEAR_LOT_SALE_FX_RATES = """
UPDATE lot_sales SET fx_rate_at_sale = NULL
WHERE lot_id = :lot_id
  AND lot_id IN (
      SELECT position_id FROM positions
      WHERE owner = :owner
        AND book_id IN (
            SELECT b.book_id FROM books b
            WHERE b.owner = :owner
              AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL
                       THEN b.kind = 'real'
                       ELSE b.book_id = CAST(:book_id AS INTEGER) END));
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _text(v, default: str = "") -> str:
    """A text column trimmed, with `default` standing in for NULL or ''."""
    return (v or default).strip()


def _or_none(v):
    """A column whose empty value ('' as well as NULL) is reported as None."""
    return v or None


def _int_or_none(v) -> Optional[int]:
    return int(v) if v is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    """Map a positions row to the CSV-parity dict shape the REST layer expects.

    Keeps every historical key (CSV parity, frontend dependency) and adds the
    lot-identity fields from V4 (issue #126 Step 3.2).
    """
    return {
        "name": _text(row["name"]),
        "symbol": _text(row["symbol"]).upper(),
        "purchase_price": _dec(row["purchase_price"]),
        "quantity": _dec(row["quantity"]),
        "purchase_date": _or_none(row["purchase_date"]),
        "currency": _text(row["currency"], "USD").upper(),
        "sale_price": _dec(row["sale_price"]),
        "sale_date": _or_none(row["sale_date"]),
        "source": "portfolio",
        "tags": [],
        "lot_id": _int_or_none(row["position_id"]),
        "status": _or_none(row["status"]),
        "parent_lot_id": _int_or_none(row["parent_lot_id"]),
        "trade_date": _or_none(row["trade_date"]),
        "fees": _dec(row["fees"]),
        "acquisition_type": _or_none(row["acquisition_type"]),
        "account": _or_none(row["account"]),
        "notes": _or_none(row["notes"]),
    }


class PortfolioRepository:
    """SQL persistence for portfolio positions, scoped by owner and by book.

    `book_id=None` on any method means the owner's real book.
    """

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------
    def _resolve_symbol_id(self, conn, ticker: str) -> int:
        ticker = ticker.strip().upper()
        conn.execute(SQL_INSERT_SYMBOL, {"ticker": ticker, "created_at": _utc_now_iso()})
        row = conn.execute(SQL_GET_SYMBOL_ID, {"ticker": ticker}).fetchone()
        return int(row["symbol_id"])

    @staticmethod
    def _legacy_values(row: Dict[str, Any]) -> Dict[str, Any]:
        """Derive the (now-nullable) legacy columns from CSV-parity fields.

        `shares` is a legacy INTEGER column with no reader left in the
        codebase (grep-confirmed) — it cannot hold the fractional quantities
        Step 3.3 introduces, so it's left NULL for those rather than raising.
        """
        pp = row.get("purchase_price")
        qty = row.get("quantity")
        cost = (pp * qty) if (pp is not None and qty is not None) else None
        whole_qty = qty if (qty is not None and qty == int(qty)) else None
        return {
            "opened_at": row.get("purchase_date"),
            "entry_price": pp,
            "shares": whole_qty,
            "cost_basis_total": cost,
        }

    def _insert_position(self, conn, owner: str, row: Dict[str, Any], *, book_id: int) -> int:
        """Insert one lot into `book_id`, which the caller has already resolved
        as this owner's (`resolve_book_id`, or the book of the owner-scoped lot
        it is splitting). Returns the new `position_id` (lot_id)."""
        symbol_id = self._resolve_symbol_id(conn, row["symbol"])
        legacy = self._legacy_values(row)
        cur = conn.execute(SQL_INSERT_POSITION, {
            "owner": owner,
            "book_id": book_id,
            "symbol_id": symbol_id,
            "name": row.get("name"),
            "purchase_price": row.get("purchase_price"),
            "quantity": row.get("quantity"),
            "purchase_date": row.get("purchase_date"),
            "currency": row.get("currency"),
            "sale_price": row.get("sale_price"),
            "sale_date": row.get("sale_date"),
            "status": row.get("status") or "OPEN",
            "parent_lot_id": row.get("parent_lot_id"),
            "trade_date": row.get("trade_date") or row.get("purchase_date"),
            "fees": row.get("fees"),
            "acquisition_type": row.get("acquisition_type") or "BUY",
            "account": row.get("account"),
            "notes": row.get("notes"),
            "fx_rate_at_purchase": row.get("fx_rate_at_purchase"),
            # The paper order that bought this lot (#431 step 3.3). None for a
            # lot the owner recorded.
            "order_id": row.get("order_id"),
            **legacy,
        })
        return int(cur.fetchone()["position_id"])

    @staticmethod
    def _sync_cash(conn, owner: str, lot_ids, *, book_id: Optional[int]) -> None:
        """Rewrite the cash rows of each lot in `lot_ids` on `conn`. None is
        skipped: a full close has no remainder lot."""
        for lot_id in lot_ids:
            if lot_id is not None:
                sync_lot_cash(conn, owner=owner, book_id=book_id, lot_id=lot_id)

    def _apply_lot_fields(
        self, conn, owner: str, lot_id: int, fields: Dict[str, Any], *, book_id: Optional[int]
    ) -> int:
        """Patch a lot's mutable columns on an already-open `conn` (no commit —
        the caller controls the transaction). Returns the rowcount affected.
        Shared by `update_lot` and `close_lots` so the allowlist/validation
        logic isn't duplicated.
        """
        unknown = set(fields) - MUTABLE_LOT_COLUMNS
        if unknown:
            raise ValueError(f"unknown lot field(s): {sorted(unknown)}")
        if not fields:
            return 0
        set_clause = ", ".join(f"{col} = :{col}" for col in fields)
        sql = (
            f"UPDATE positions SET {set_clause} "
            "WHERE owner = :owner AND position_id = :lot_id "
            "AND book_id IN ("
            "SELECT b.book_id FROM books b WHERE b.owner = :owner "
            "AND CASE WHEN CAST(:book_id AS INTEGER) IS NULL THEN b.kind = 'real' "
            "ELSE b.book_id = CAST(:book_id AS INTEGER) END);"
        )
        params = {**fields, "owner": owner, "lot_id": lot_id, "book_id": book_id}
        cur = conn.execute(sql, params)
        return cur.rowcount

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(
        self, owner: str, status: str = "OPEN", *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List `owner`'s lots in one book. Defaults to open lots only (decision #12)."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                SQL_LIST_POSITIONS, {"owner": owner, "status": status, "book_id": book_id}
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_owners(self) -> List[str]:
        """Every owner holding a position in a real book."""
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_OWNERS).fetchall()
        return [r["owner"] for r in rows]

    def count_for_symbol(
        self, owner: str, ticker: str, *, book_id: Optional[int] = None
    ) -> int:
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_COUNT_OWNER_SYMBOL,
                {"owner": owner, "ticker": ticker.strip().upper(), "book_id": book_id},
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_lot(
        self, owner: str, lot_id: int, *, book_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_GET_LOT, {"owner": owner, "lot_id": lot_id, "book_id": book_id}
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list_lots_for_symbol(
        self, owner: str, ticker: str, status: Optional[str] = "OPEN",
        *, book_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List every lot of `ticker` for `owner`. `status=None` returns all statuses."""
        with closing(get_connection()) as conn:
            return self._lots_for_symbol(conn, owner, ticker, status, book_id=book_id)

    @staticmethod
    def _lots_for_symbol(
        conn, owner: str, ticker: str, status: Optional[str], *, book_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        """`list_lots_for_symbol` on an open `conn`."""
        sql = SQL_LIST_LOTS_FOR_SYMBOL.format(
            status_clause="" if status is None else "\n  AND p.status = :status"
        )
        params = {"owner": owner, "ticker": ticker.strip().upper(), "book_id": book_id}
        if status is not None:
            params["status"] = status
        return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def replace_owner_positions(
        self, owner: str, rows: List[Dict[str, Any]], *, book_id: Optional[int] = None
    ) -> int:
        """Full-sync: delete all of `owner`'s positions in one book and insert
        `rows` into it, atomically. The owner's other books are untouched, so a
        CSV import (the real book) never wipes a paper book.

        The book's buy and sell cash rows are replaced with the lots: the old
        ones go with their lots, and each new lot writes its own.

        Returns the number of rows inserted.
        """
        with closing(get_connection()) as conn:
            try:
                book = resolve_book_id(conn, owner=owner, book_id=book_id)
                conn.execute(SQL_DELETE_OWNER_POSITIONS, {"owner": owner, "book_id": book})
                for row in rows:
                    self._insert_position(conn, owner, row, book_id=book)
                # The old lots took their cash rows with them. One pass
                # writes the new ones.
                insert_trade_entries(conn, owner=owner, book_id=book)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(rows)

    def add_position(
        self, owner: str, row: Dict[str, Any], *, book_id: Optional[int] = None
    ) -> int:
        """Insert a single lot. Every call creates a new lot (issue #126 Step 3.2)
        — the old owner/symbol/date upsert is gone. Returns the new `lot_id`.
        The lot's buy row goes into the book's cash ledger with it.

        Raises `ValueError` for a `book_id` that isn't this owner's.
        """
        with closing(get_connection()) as conn:
            try:
                lot_id = self.add_lot_on(conn, owner, row, book_id=book_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return lot_id

    def add_lot_on(
        self, conn, owner: str, row: Dict[str, Any], *, book_id: Optional[int] = None
    ) -> int:
        """`add_position` on the caller's connection, with no commit: the lot
        and its buy row join whatever transaction `conn` is in. A paper order's
        fill uses it so the order, the lot and the cash row land together
        (#431 step 3.3). Returns the new `lot_id`."""
        book = resolve_book_id(conn, owner=owner, book_id=book_id)
        lot_id = self._insert_position(conn, owner, row, book_id=book)
        sync_lot_cash(conn, owner=owner, book_id=book, lot_id=lot_id)
        return lot_id

    def open_lots_on(
        self, conn, owner: str, ticker: str, *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """The OPEN lots of `ticker` as `conn`'s transaction sees them, oldest
        first. What a fill allocates a sale across."""
        return self._lots_for_symbol(conn, owner, ticker, "OPEN", book_id=book_id)

    def remove_position(
        self, owner: str, ticker: str, *, book_id: Optional[int] = None
    ) -> int:
        """Delete every lot of `ticker` for `owner` in one book. Returns rows removed."""
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(
                    SQL_DELETE_OWNER_SYMBOL,
                    {"owner": owner, "ticker": ticker.strip().upper(), "book_id": book_id},
                )
                removed = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(removed)

    def update_lot(
        self, owner: str, lot_id: int, fields: Dict[str, Any], *, book_id: Optional[int] = None
    ) -> bool:
        """Patch a single lot's mutable fields. Returns True if a row was updated.

        `fields` keys must be a subset of the mutable lot columns; unknown keys
        raise rather than being silently dropped.

        A change of `currency` also clears the sale-date FX rate of every sale
        of the lot, in the same transaction: those rates were for the old
        currency (#235). The lot's cash rows are rewritten to match the edit.
        """
        with closing(get_connection()) as conn:
            try:
                updated = self._apply_lot_fields(conn, owner, lot_id, fields, book_id=book_id)
                if updated and "currency" in fields:
                    conn.execute(
                        SQL_CLEAR_LOT_SALE_FX_RATES,
                        {"owner": owner, "lot_id": lot_id, "book_id": book_id},
                    )
                if updated:
                    sync_lot_cash(conn, owner=owner, book_id=book_id, lot_id=lot_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return updated > 0

    def delete_lot(self, owner: str, lot_id: int, *, book_id: Optional[int] = None) -> bool:
        """Delete a single lot. Returns True if a row was deleted."""
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(
                    SQL_DELETE_LOT, {"owner": owner, "lot_id": lot_id, "book_id": book_id}
                )
                deleted = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return deleted > 0

    def insert_sale(
        self,
        owner: str,
        lot_id: int,
        shares_sold: Any,
        sale_price: Any,
        sale_trade_date: str,
        fees: Any = None,
        allocation_method: Optional[str] = None,
        notes: Optional[str] = None,
        *,
        fx_rate_at_sale: Any = None,
        book_id: Optional[int] = None,
    ) -> int:
        """Record a partial or full sale against `lot_id`. Returns the new `sale_id`.

        Owner- and book-scoped via an existence check — `lot_sales` itself has
        neither column, so a lot id alone must never be sufficient to write a row.
        """
        with closing(get_connection()) as conn:
            try:
                lot = conn.execute(
                    SQL_GET_LOT, {"owner": owner, "lot_id": lot_id, "book_id": book_id}
                ).fetchone()
                if lot is None:
                    raise ValueError(f"lot {lot_id} not found for owner {owner!r}")
                cur = conn.execute(SQL_INSERT_SALE, {
                    "lot_id": lot_id,
                    "shares_sold": shares_sold,
                    "sale_price": sale_price,
                    "sale_trade_date": sale_trade_date,
                    "fees": fees,
                    "allocation_method": allocation_method,
                    "notes": notes,
                    "fx_rate_at_sale": fx_rate_at_sale,
                    "order_id": None,
                    "source": MANUAL_SOURCE,
                    "source_ref": None,
                })
                sale_id = int(cur.fetchone()["sale_id"])
                sync_lot_cash(conn, owner=owner, book_id=book_id, lot_id=lot_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return sale_id

    def list_sales(
        self, owner: str, ticker: Optional[str] = None, *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List `owner`'s recorded sales in one book, oldest first; `ticker`
        narrows to one symbol."""
        sql = SQL_LIST_SALES.format(
            symbol_clause="" if ticker is None else "\n  AND s.ticker = :ticker"
        )
        params: Dict[str, Any] = {"owner": owner, "book_id": book_id}
        if ticker is not None:
            params["ticker"] = ticker.strip().upper()
        with closing(get_connection()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "sale_id": int(r["sale_id"]),
                "lot_id": int(r["lot_id"]),
                "symbol": (r["symbol"] or "").strip().upper(),
                "shares_sold": _dec(r["shares_sold"]),
                "sale_price": _dec(r["sale_price"]),
                "sale_trade_date": str(r["sale_trade_date"]),
                "fees": _dec(r["fees"]),
                "allocation_method": r["allocation_method"] or None,
                "notes": r["notes"] or None,
            }
            for r in rows
        ]

    def list_realized_sales(
        self,
        owner: str,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        book_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """`owner`'s sales in one book, each with the purchase price, trade
        date and currency of the lot it closed, oldest first (#235).

        `start_date` and `end_date` bound `sale_trade_date`, both inclusive;
        None leaves that side open. Only `lot_sales` rows count: the legacy
        `positions.sale_price` / `sale_date` columns are not a source.

        Both FX rates are whatever was recorded with the trade, None when
        nothing was. The maths flags a non-USD sale that lacks either and
        leaves it out of the totals.
        """
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_REALIZED_SALES, {
                "owner": owner,
                "book_id": book_id,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            }).fetchall()
        return [
            {
                "sale_id": int(r["sale_id"]),
                "lot_id": int(r["lot_id"]),
                "symbol": (r["symbol"] or "").strip().upper(),
                "shares_sold": _dec(r["shares_sold"]),
                "sale_price": _dec(r["sale_price"]),
                "sale_trade_date": str(r["sale_trade_date"]),
                "allocation_method": r["allocation_method"] or None,
                "notes": r["notes"] or None,
                "purchase_price": _dec(r["purchase_price"]),
                "trade_date": str(r["trade_date"]) if r["trade_date"] else None,
                "currency": (r["currency"] or "USD").strip().upper(),
                "account": r["account"] or None,
                "fx_rate_at_purchase": _dec(r["fx_rate_at_purchase"]),
                "fx_rate_at_sale": _dec(r["fx_rate_at_sale"]),
            }
            for r in rows
        ]

    def list_lots_missing_fx(
        self, owner: str, *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """`owner`'s non-USD lots in one book, open or closed, that have no
        purchase-date FX rate (#235)."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                SQL_LIST_LOTS_MISSING_FX, {"owner": owner, "book_id": book_id}
            ).fetchall()
        return [
            {
                "lot_id": int(r["lot_id"]),
                "symbol": (r["symbol"] or "").strip().upper(),
                "currency": (r["currency"] or "").strip().upper(),
                "trade_date": str(r["trade_date"]) if r["trade_date"] else None,
            }
            for r in rows
        ]

    def list_sales_missing_fx(
        self, owner: str, *, book_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """`owner`'s sales of non-USD lots in one book that have no sale-date
        FX rate (#235)."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                SQL_LIST_SALES_MISSING_FX, {"owner": owner, "book_id": book_id}
            ).fetchall()
        return [
            {
                "sale_id": int(r["sale_id"]),
                "lot_id": int(r["lot_id"]),
                "symbol": (r["symbol"] or "").strip().upper(),
                "currency": (r["currency"] or "").strip().upper(),
                "sale_trade_date": str(r["sale_trade_date"]),
            }
            for r in rows
        ]

    def fill_lot_fx_rate(
        self, owner: str, lot_id: int, rate: Any, *, book_id: Optional[int] = None
    ) -> bool:
        """Store a lot's purchase-date FX rate if it has none. Returns True if
        a row was filled; a lot that already has a rate is left as it is."""
        return self._fill_fx_rate(
            SQL_FILL_LOT_FX_RATE,
            {"owner": owner, "lot_id": lot_id, "rate": rate, "book_id": book_id},
        )

    def fill_sale_fx_rate(
        self, owner: str, sale_id: int, rate: Any, *, book_id: Optional[int] = None
    ) -> bool:
        """Store a sale's sale-date FX rate if it has none. Owner- and
        book-scoped through the sale's lot. Returns True if a row was filled."""
        return self._fill_fx_rate(
            SQL_FILL_SALE_FX_RATE,
            {"owner": owner, "sale_id": sale_id, "rate": rate, "book_id": book_id},
        )

    @staticmethod
    def _fill_fx_rate(sql: str, params: Dict[str, Any]) -> bool:
        """Run one of the two fill statements, which return the lot they
        touched. A filled rate gives that lot's cash rows an amount."""
        with closing(get_connection()) as conn:
            try:
                row = conn.execute(sql, params).fetchone()
                if row is not None:
                    sync_lot_cash(
                        conn, owner=params["owner"], book_id=params["book_id"],
                        lot_id=int(row["lot_id"]),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return row is not None

    def update_sale_notes(
        self, owner: str, sale_id: int, notes: Optional[str], *, book_id: Optional[int] = None
    ) -> bool:
        """Set or clear a sale's note. Returns True if a row was updated.

        Owner-scoped through the sale's lot — another owner's `sale_id` matches
        nothing, so it reads as "not found" rather than leaking existence.
        """
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(
                    SQL_UPDATE_SALE_NOTES,
                    {"owner": owner, "sale_id": sale_id, "notes": notes, "book_id": book_id},
                )
                updated = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return updated > 0

    def close_lots(
        self,
        owner: str,
        allocation: List[Tuple[int, Any]],
        sale_price: Any,
        sale_trade_date: str,
        fees: Any = None,
        allocation_method: Optional[str] = None,
        notes: Optional[str] = None,
        lot_notes: Optional[Mapping[int, str]] = None,
        *,
        fx_rates_at_sale: Optional[Mapping[str, Any]] = None,
        book_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Atomically record a sale that may span multiple lots (issue #126
        Step 4.3). `allocation` is `[(lot_id, shares), ...]` — the output of
        `portfolio_math.allocate`.

        For each pair: writes a `lot_sales` row, then either marks the lot
        CLOSED (full close) or splits it — the lot's own `quantity` is set to
        the shares actually sold and it is marked CLOSED, and a new child lot
        is inserted for the remainder, carrying the original `trade_date` and
        `purchase_price` with `parent_lot_id` set (partial close). Every lot
        must belong to `owner`, be in the one book, and be OPEN; the remainder
        lot of a split goes into its parent's book. One transaction: any failure
        leaves every lot and `lot_sales` row exactly as it was.

        `notes` is the default sale note written to every `lot_sales` row;
        `lot_notes` (`{lot_id: note}`) overrides it for individual lots
        (issue #266). The remainder lot of a split keeps the *purchase* note.

        `fx_rates_at_sale` is `{currency: USD per unit on the sale date}`
        (#235). Each `lot_sales` row stores the rate for its lot's currency,
        or NULL when the mapping has none (always the case for a USD lot). The
        remainder lot of a split keeps its parent's purchase-date rate.

        Each sale writes its sell row to the book's cash ledger, and a split
        rewrites the buy row of each half, all in the same transaction.
        """
        with closing(get_connection()) as conn:
            try:
                results = self.close_lots_on(
                    conn, owner, allocation, sale_price, sale_trade_date,
                    fees=fees, allocation_method=allocation_method, notes=notes,
                    lot_notes=lot_notes, fx_rates_at_sale=fx_rates_at_sale, book_id=book_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return results

    def close_lots_on(
        self,
        conn,
        owner: str,
        allocation: List[Tuple[int, Any]],
        sale_price: Any,
        sale_trade_date: str,
        fees: Any = None,
        allocation_method: Optional[str] = None,
        notes: Optional[str] = None,
        lot_notes: Optional[Mapping[int, str]] = None,
        *,
        fx_rates_at_sale: Optional[Mapping[str, Any]] = None,
        book_id: Optional[int] = None,
        source: str = MANUAL_SOURCE,
        source_ref: Optional[str] = None,
        order_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """`close_lots` on the caller's connection, with no commit. A paper
        order's fill uses it so the order, its sale rows and their cash rows
        land together (#431 step 3.3).

        `source`, `source_ref` and `order_id` are written to every `lot_sales`
        row: what prompted the sale, a pointer to it, and the order that
        filled it. A sale the owner records is `manual` with neither. The
        remainder lot of a split keeps the order that bought its parent.
        """
        results: List[Dict[str, Any]] = []
        for lot_id, shares in allocation:
            row = conn.execute(
                SQL_GET_LOT, {"owner": owner, "lot_id": lot_id, "book_id": book_id}
            ).fetchone()
            if row is None:
                raise ValueError(f"lot {lot_id} not found for owner {owner!r}")
            lot = _row_to_dict(row)
            if lot["status"] != "OPEN":
                raise ValueError(f"lot {lot_id} is not open")
            if shares > lot["quantity"]:
                raise ValueError(
                    f"lot {lot_id}: cannot sell {shares} shares, only "
                    f"{lot['quantity']} open"
                )

            sale_notes = (lot_notes or {}).get(lot_id, notes)
            cur = conn.execute(SQL_INSERT_SALE, {
                "lot_id": lot_id,
                "shares_sold": shares,
                "sale_price": sale_price,
                "sale_trade_date": sale_trade_date,
                "fees": fees,
                "allocation_method": allocation_method,
                "notes": sale_notes,
                "fx_rate_at_sale": (fx_rates_at_sale or {}).get(lot["currency"]),
                "order_id": order_id,
                "source": source,
                "source_ref": source_ref,
            })
            sale_id = int(cur.fetchone()["sale_id"])

            child_lot_id = None
            if shares < lot["quantity"]:
                remainder = lot["quantity"] - shares
                child_lot_id = self._insert_position(conn, owner, {
                    "symbol": lot["symbol"],
                    "name": lot["name"],
                    "purchase_price": lot["purchase_price"],
                    "quantity": remainder,
                    "purchase_date": lot["purchase_date"],
                    "trade_date": lot["trade_date"],
                    "currency": lot["currency"],
                    "status": "OPEN",
                    "parent_lot_id": lot_id,
                    "fees": lot["fees"],
                    "acquisition_type": lot["acquisition_type"],
                    "account": lot["account"],
                    "notes": lot["notes"],
                    "fx_rate_at_purchase": row["fx_rate_at_purchase"],
                    "order_id": row["order_id"],
                }, book_id=int(row["book_id"]))

            self._apply_lot_fields(conn, owner, lot_id, {
                "quantity": shares,
                "status": "CLOSED",
            }, book_id=book_id)

            # The sale's sell row, and the buy rows of both halves of
            # a split: the parent now holds only the shares sold.
            self._sync_cash(conn, owner, (lot_id, child_lot_id), book_id=book_id)

            results.append({
                "lot_id": lot_id,
                "sale_id": sale_id,
                "shares_sold": shares,
                "child_lot_id": child_lot_id,
                "notes": sale_notes,
            })
        if results:
            # A sale that leaves the book with no OPEN lot of a symbol closes
            # that symbol's stop, in the sale's own transaction (#431 step 4.2).
            close_flat_stops_on(
                conn, owner=owner, resolved_at=datetime.now(timezone.utc), book_id=book_id
            )
        return results
