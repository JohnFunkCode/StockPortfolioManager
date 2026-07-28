"""WatchlistRepository — CRUD over the `watchlist` table (issue #83).

Replaces the read-and-append of `watchlist.yaml`, which on Cloud Run wrote to
a per-instance container filesystem that resets on deploy.

SQL only, no analytics and no policy: `add_entry` reports that the insert
conflicted, it does not decide that a conflict means HTTP 409. That call is
`WatchlistService`'s.

Rows come back in the exact shape `api/deps.load_watchlist()` produced from the
YAML — every key, including the `None` placeholders the front end,
`PricesService.screen_securities`, `SentimentService` and `GET /api/securities`
all read.

The list is global (plan decision 1): no `owner` column, no owner parameter.
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

SQL_LIST_ENTRIES = """
SELECT
    w.watchlist_id AS watchlist_id,
    w.name         AS name,
    s.ticker       AS symbol,
    w.currency     AS currency,
    w.tags         AS tags
FROM watchlist w
JOIN symbols s ON s.symbol_id = w.symbol_id
ORDER BY s.ticker;
"""

# ON CONFLICT DO NOTHING means RETURNING yields no row when the symbol is
# already watched, so a duplicate is a None return rather than an exception.
SQL_INSERT_ENTRY = """
INSERT INTO watchlist (symbol_id, name, currency, tags, added_by)
VALUES (:symbol_id, :name, :currency, :tags, :added_by)
ON CONFLICT (symbol_id) DO NOTHING
RETURNING watchlist_id;
"""

SQL_DELETE_ENTRY = """
DELETE FROM watchlist
WHERE symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker);
"""

SQL_DELETE_ALL = """
DELETE FROM watchlist;
"""

SQL_COUNT = """
SELECT COUNT(*) AS n FROM watchlist;
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> Dict[str, Any]:
    """Map a watchlist row to the YAML-parity dict the REST layer expects.

    The four `None` placeholders are not padding: `GET /api/securities` merges
    these rows with portfolio rows into one list, so both sides must carry the
    same keys.
    """
    return {
        "name": (row["name"] or "").strip() if row["name"] is not None else "",
        "symbol": (row["symbol"] or "").strip().upper(),
        "currency": (row["currency"] or "USD").strip().upper(),
        "purchase_price": None,
        "quantity": None,
        "purchase_date": None,
        "sale_price": None,
        "sale_date": None,
        "source": "watchlist",
        "tags": list(row["tags"] or []),
    }


class WatchlistRepository:
    """SQL persistence for the global watchlist."""

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------
    def _resolve_symbol_id(self, conn, ticker: str) -> int:
        ticker = ticker.strip().upper()
        conn.execute(SQL_INSERT_SYMBOL, {"ticker": ticker, "created_at": _utc_now_iso()})
        row = conn.execute(SQL_GET_SYMBOL_ID, {"ticker": ticker}).fetchone()
        return int(row["symbol_id"])

    def _insert_entry(self, conn, row: Dict[str, Any]) -> Optional[int]:
        symbol_id = self._resolve_symbol_id(conn, row["symbol"])
        cur = conn.execute(SQL_INSERT_ENTRY, {
            "symbol_id": symbol_id,
            "name": row.get("name"),
            "currency": row.get("currency") or "USD",
            "tags": list(row.get("tags") or []),
            "added_by": row.get("added_by"),
        })
        inserted = cur.fetchone()
        return int(inserted["watchlist_id"]) if inserted is not None else None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_entries(self) -> List[Dict[str, Any]]:
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_ENTRIES).fetchall()
        return [_row_to_dict(r) for r in rows]

    def count(self) -> int:
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_COUNT).fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def add_entry(
        self,
        symbol: str,
        name: Optional[str] = None,
        currency: str = "USD",
        tags: Optional[List[str]] = None,
        added_by: Optional[str] = None,
    ) -> Optional[int]:
        """Insert one entry. Returns the new id, or None if already watched."""
        with closing(get_connection()) as conn:
            try:
                entry_id = self._insert_entry(conn, {
                    "symbol": symbol,
                    "name": name,
                    "currency": currency,
                    "tags": tags,
                    "added_by": added_by,
                })
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return entry_id

    def remove_entry(self, symbol: str) -> int:
        """Delete `symbol` from the watchlist. Returns rows removed (0 or 1)."""
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(SQL_DELETE_ENTRY, {"ticker": symbol.strip().upper()})
                removed = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(removed)

    def replace_all(self, rows: List[Dict[str, Any]]) -> int:
        """Full-sync: delete every entry and insert `rows`, atomically.

        Returns the number of rows actually inserted, which is less than
        `len(rows)` if the input repeats a symbol — the UNIQUE constraint
        collapses the duplicates instead of failing the import.
        """
        inserted = 0
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_DELETE_ALL)
                for row in rows:
                    if self._insert_entry(conn, row) is not None:
                        inserted += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return inserted
