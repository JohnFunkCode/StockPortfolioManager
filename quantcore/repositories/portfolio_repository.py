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
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from quantcore.db import get_connection

MUTABLE_LOT_COLUMNS = {
    "name", "purchase_price", "quantity", "purchase_date", "currency",
    "sale_price", "sale_date", "status", "parent_lot_id", "trade_date",
    "fees", "acquisition_type", "account", "notes",
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
ORDER BY s.ticker, p.purchase_date;
"""

SQL_LIST_OWNERS = """
SELECT DISTINCT owner FROM positions ORDER BY owner;
"""

SQL_DELETE_OWNER_POSITIONS = """
DELETE FROM positions WHERE owner = :owner;
"""

SQL_DELETE_OWNER_SYMBOL = """
DELETE FROM positions
WHERE owner = :owner
  AND symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker);
"""

SQL_COUNT_OWNER_SYMBOL = """
SELECT COUNT(*) AS n
FROM positions
WHERE owner = :owner
  AND symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker);
"""

SQL_INSERT_POSITION = """
INSERT INTO positions (
    owner, symbol_id, name,
    purchase_price, quantity, purchase_date, currency, sale_price, sale_date,
    opened_at, entry_price, shares, cost_basis_total,
    status, parent_lot_id, trade_date, fees, acquisition_type, account, notes
) VALUES (
    :owner, :symbol_id, :name,
    :purchase_price, :quantity, :purchase_date, :currency, :sale_price, :sale_date,
    :opened_at, :entry_price, :shares, :cost_basis_total,
    :status, :parent_lot_id, :trade_date, :fees, :acquisition_type, :account, :notes
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
    p.notes          AS notes
FROM positions p
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
  AND p.position_id = :lot_id;
"""

SQL_DELETE_LOT = """
DELETE FROM positions WHERE owner = :owner AND position_id = :lot_id;
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
ORDER BY p.trade_date, p.position_id;
"""

SQL_INSERT_SALE = """
INSERT INTO lot_sales (
    lot_id, shares_sold, sale_price, sale_trade_date, fees, allocation_method, notes
) VALUES (
    :lot_id, :shares_sold, :sale_price, :sale_trade_date, :fees, :allocation_method, :notes
)
RETURNING sale_id;
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dec(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    """Map a positions row to the CSV-parity dict shape the REST layer expects.

    Keeps every historical key (CSV parity, frontend dependency) and adds the
    lot-identity fields from V4 (issue #126 Step 3.2).
    """
    return {
        "name": (row["name"] or "").strip() if row["name"] is not None else "",
        "symbol": (row["symbol"] or "").strip().upper(),
        "purchase_price": _dec(row["purchase_price"]),
        "quantity": _dec(row["quantity"]),
        "purchase_date": row["purchase_date"] or None,
        "currency": (row["currency"] or "USD").strip().upper(),
        "sale_price": _dec(row["sale_price"]),
        "sale_date": row["sale_date"] or None,
        "source": "portfolio",
        "tags": [],
        "lot_id": int(row["position_id"]) if row["position_id"] is not None else None,
        "status": row["status"] or None,
        "parent_lot_id": int(row["parent_lot_id"]) if row["parent_lot_id"] is not None else None,
        "trade_date": row["trade_date"] or None,
        "fees": _dec(row["fees"]),
        "acquisition_type": row["acquisition_type"] or None,
        "account": row["account"] or None,
        "notes": row["notes"] or None,
    }


class PortfolioRepository:
    """SQL persistence for portfolio positions, scoped by owner."""

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

    def _insert_position(self, conn, owner: str, row: Dict[str, Any]) -> int:
        """Insert one lot. Returns the new `position_id` (lot_id)."""
        symbol_id = self._resolve_symbol_id(conn, row["symbol"])
        legacy = self._legacy_values(row)
        cur = conn.execute(SQL_INSERT_POSITION, {
            "owner": owner,
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
            **legacy,
        })
        return int(cur.fetchone()["position_id"])

    def _apply_lot_fields(self, conn, owner: str, lot_id: int, fields: Dict[str, Any]) -> int:
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
            "WHERE owner = :owner AND position_id = :lot_id;"
        )
        params = {**fields, "owner": owner, "lot_id": lot_id}
        cur = conn.execute(sql, params)
        return cur.rowcount

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(self, owner: str, status: str = "OPEN") -> List[Dict[str, Any]]:
        """List `owner`'s lots. Defaults to open lots only (decision #12)."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                SQL_LIST_POSITIONS, {"owner": owner, "status": status}
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_owners(self) -> List[str]:
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_OWNERS).fetchall()
        return [r["owner"] for r in rows]

    def count_for_symbol(self, owner: str, ticker: str) -> int:
        with closing(get_connection()) as conn:
            row = conn.execute(
                SQL_COUNT_OWNER_SYMBOL, {"owner": owner, "ticker": ticker.strip().upper()}
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_lot(self, owner: str, lot_id: int) -> Optional[Dict[str, Any]]:
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_GET_LOT, {"owner": owner, "lot_id": lot_id}).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list_lots_for_symbol(
        self, owner: str, ticker: str, status: Optional[str] = "OPEN"
    ) -> List[Dict[str, Any]]:
        """List every lot of `ticker` for `owner`. `status=None` returns all statuses."""
        sql = SQL_LIST_LOTS_FOR_SYMBOL.format(
            status_clause="" if status is None else "\n  AND p.status = :status"
        )
        params = {"owner": owner, "ticker": ticker.strip().upper()}
        if status is not None:
            params["status"] = status
        with closing(get_connection()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def replace_owner_positions(self, owner: str, rows: List[Dict[str, Any]]) -> int:
        """Full-sync: delete all of `owner`'s positions and insert `rows`, atomically.

        Returns the number of rows inserted.
        """
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_DELETE_OWNER_POSITIONS, {"owner": owner})
                for row in rows:
                    self._insert_position(conn, owner, row)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(rows)

    def add_position(self, owner: str, row: Dict[str, Any]) -> int:
        """Insert a single lot. Every call creates a new lot (issue #126 Step 3.2)
        — the old owner/symbol/date upsert is gone. Returns the new `lot_id`.
        """
        with closing(get_connection()) as conn:
            try:
                lot_id = self._insert_position(conn, owner, row)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return lot_id

    def remove_position(self, owner: str, ticker: str) -> int:
        """Delete every lot of `ticker` for `owner`. Returns rows removed."""
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(
                    SQL_DELETE_OWNER_SYMBOL, {"owner": owner, "ticker": ticker.strip().upper()}
                )
                removed = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(removed)

    def update_lot(self, owner: str, lot_id: int, fields: Dict[str, Any]) -> bool:
        """Patch a single lot's mutable fields. Returns True if a row was updated.

        `fields` keys must be a subset of the mutable lot columns; unknown keys
        raise rather than being silently dropped.
        """
        with closing(get_connection()) as conn:
            try:
                updated = self._apply_lot_fields(conn, owner, lot_id, fields)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return updated > 0

    def delete_lot(self, owner: str, lot_id: int) -> bool:
        """Delete a single lot. Returns True if a row was deleted."""
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(SQL_DELETE_LOT, {"owner": owner, "lot_id": lot_id})
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
    ) -> int:
        """Record a partial or full sale against `lot_id`. Returns the new `sale_id`.

        Owner-scoped via an existence check — `lot_sales` itself has no owner
        column, so a lot id alone must never be sufficient to write a row.
        """
        with closing(get_connection()) as conn:
            try:
                lot = conn.execute(SQL_GET_LOT, {"owner": owner, "lot_id": lot_id}).fetchone()
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
                })
                sale_id = int(cur.fetchone()["sale_id"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return sale_id

    def close_lots(
        self,
        owner: str,
        allocation: List[Tuple[int, Any]],
        sale_price: Any,
        sale_trade_date: str,
        fees: Any = None,
        allocation_method: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Atomically record a sale that may span multiple lots (issue #126
        Step 4.3). `allocation` is `[(lot_id, shares), ...]` — the output of
        `portfolio_math.allocate`.

        For each pair: writes a `lot_sales` row, then either marks the lot
        CLOSED (full close) or splits it — the lot's own `quantity` is set to
        the shares actually sold and it is marked CLOSED, and a new child lot
        is inserted for the remainder, carrying the original `trade_date` and
        `purchase_price` with `parent_lot_id` set (partial close). Every lot
        must belong to `owner` and be OPEN. One transaction: any failure
        leaves every lot and `lot_sales` row exactly as it was.
        """
        results: List[Dict[str, Any]] = []
        with closing(get_connection()) as conn:
            try:
                for lot_id, shares in allocation:
                    row = conn.execute(SQL_GET_LOT, {"owner": owner, "lot_id": lot_id}).fetchone()
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

                    cur = conn.execute(SQL_INSERT_SALE, {
                        "lot_id": lot_id,
                        "shares_sold": shares,
                        "sale_price": sale_price,
                        "sale_trade_date": sale_trade_date,
                        "fees": fees,
                        "allocation_method": allocation_method,
                        "notes": notes,
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
                        })

                    self._apply_lot_fields(conn, owner, lot_id, {
                        "quantity": shares,
                        "status": "CLOSED",
                    })

                    results.append({
                        "lot_id": lot_id,
                        "sale_id": sale_id,
                        "shares_sold": shares,
                        "child_lot_id": child_lot_id,
                    })

                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return results
