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
from typing import Any, Dict, List, Optional

from quantcore.db import get_connection

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
    p.name           AS name,
    s.ticker         AS symbol,
    p.purchase_price AS purchase_price,
    p.quantity       AS quantity,
    p.purchase_date  AS purchase_date,
    p.currency       AS currency,
    p.sale_price     AS sale_price,
    p.sale_date      AS sale_date
FROM positions p
JOIN symbols s ON s.symbol_id = p.symbol_id
WHERE p.owner = :owner
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
    opened_at, entry_price, shares, cost_basis_total
) VALUES (
    :owner, :symbol_id, :name,
    :purchase_price, :quantity, :purchase_date, :currency, :sale_price, :sale_date,
    :opened_at, :entry_price, :shares, :cost_basis_total
)
ON CONFLICT(owner, symbol_id, purchase_date) DO UPDATE SET
    name           = excluded.name,
    purchase_price = excluded.purchase_price,
    quantity       = excluded.quantity,
    currency       = excluded.currency,
    sale_price     = excluded.sale_price,
    sale_date      = excluded.sale_date,
    opened_at      = excluded.opened_at,
    entry_price    = excluded.entry_price,
    shares         = excluded.shares,
    cost_basis_total = excluded.cost_basis_total;
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> Dict[str, Any]:
    """Map a positions row to the CSV-parity dict shape the REST layer expects."""
    return {
        "name": (row["name"] or "").strip() if row["name"] is not None else "",
        "symbol": (row["symbol"] or "").strip().upper(),
        "purchase_price": float(row["purchase_price"]) if row["purchase_price"] is not None else None,
        "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
        "purchase_date": row["purchase_date"] or None,
        "currency": (row["currency"] or "USD").strip().upper(),
        "sale_price": float(row["sale_price"]) if row["sale_price"] is not None else None,
        "sale_date": row["sale_date"] or None,
        "source": "portfolio",
        "tags": [],
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
        """Derive the (now-nullable) legacy columns from CSV-parity fields."""
        pp = row.get("purchase_price")
        qty = row.get("quantity")
        cost = (pp * qty) if (pp is not None and qty is not None) else None
        return {
            "opened_at": row.get("purchase_date"),
            "entry_price": pp,
            "shares": qty,
            "cost_basis_total": cost,
        }

    def _insert_position(self, conn, owner: str, row: Dict[str, Any]) -> None:
        symbol_id = self._resolve_symbol_id(conn, row["symbol"])
        legacy = self._legacy_values(row)
        conn.execute(SQL_INSERT_POSITION, {
            "owner": owner,
            "symbol_id": symbol_id,
            "name": row.get("name"),
            "purchase_price": row.get("purchase_price"),
            "quantity": row.get("quantity"),
            "purchase_date": row.get("purchase_date"),
            "currency": row.get("currency"),
            "sale_price": row.get("sale_price"),
            "sale_date": row.get("sale_date"),
            **legacy,
        })

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_positions(self, owner: str) -> List[Dict[str, Any]]:
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_POSITIONS, {"owner": owner}).fetchall()
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

    def add_position(self, owner: str, row: Dict[str, Any]) -> None:
        """Insert a single position (upsert on the owner/symbol/date key)."""
        with closing(get_connection()) as conn:
            try:
                self._insert_position(conn, owner, row)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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
