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

# The bulk counterpart of SQL_GET_SYMBOL_ID: one round trip for the whole
# import instead of one per row. psycopg2 adapts the Python list to a
# PostgreSQL array for ANY().
SQL_GET_SYMBOL_IDS = """
SELECT symbol_id, ticker FROM symbols WHERE ticker = ANY(:tickers);
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

# Same insert without RETURNING, for the batched import path: execute_batch
# pipelines the statements and discards result sets, so the caller can't read
# per-row conflicts back out of it.
SQL_INSERT_ENTRY_BULK = """
INSERT INTO watchlist (symbol_id, name, currency, tags, added_by)
VALUES (:symbol_id, :name, :currency, :tags, :added_by)
ON CONFLICT (symbol_id) DO NOTHING;
"""

SQL_UPDATE_CURRENCY = """
UPDATE watchlist
SET currency = :currency
WHERE symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker);
"""

SQL_UPDATE_TAGS = """
UPDATE watchlist
SET tags = :tags
WHERE symbol_id = (SELECT symbol_id FROM symbols WHERE ticker = :ticker);
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

    def set_currency(self, symbol: str, currency: str) -> int:
        """Set `symbol`'s currency. Returns rows updated — 0 means unwatched.

        Narrow on purpose: the repair script fixes currencies on entries seeded
        from watchlist.yaml, and a general `update_entry` would invite a caller
        to overwrite tags or a name it never read.
        """
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(SQL_UPDATE_CURRENCY, {
                    "ticker": symbol.strip().upper(),
                    "currency": str(currency or "").strip().upper(),
                })
                updated = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(updated)

    def set_tags(self, symbol: str, tags: List[str]) -> int:
        """Replace `symbol`'s tags wholesale. Returns rows updated — 0 means
        unwatched.

        Narrow for the same reason as `set_currency`: the caller edits tags and
        nothing else, so there is no way to blank a name or a currency it never
        read. Replace rather than merge — the UI sends the full chip set it is
        displaying, and a merge could never remove one.
        """
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(SQL_UPDATE_TAGS, {
                    "ticker": symbol.strip().upper(),
                    "tags": list(tags),
                })
                updated = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(updated)

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
        `len(rows)` if the input repeats a symbol — the duplicates are
        collapsed instead of failing the import.

        Batched in three round trips (symbols, ids, entries) rather than the
        obvious loop over `_insert_entry`, which costs three trips *per row*:
        at the Cloud SQL proxy's ~90ms RTT that put the 227-entry seed at ~60
        seconds, and paid it again after every test that restores the table.
        """
        # Deduped here rather than left to ON CONFLICT, because the batched
        # insert can't report which rows conflicted — the count has to be
        # settled before the write. First occurrence wins, matching the
        # per-row path where the second insert is the one that no-ops.
        deduped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            ticker = (row.get("symbol") or "").strip().upper()
            if ticker and ticker not in deduped:
                deduped[ticker] = row

        now = _utc_now_iso()
        with closing(get_connection()) as conn:
            try:
                conn.execute(SQL_DELETE_ALL)
                if deduped:
                    tickers = list(deduped)
                    conn.executemany(
                        SQL_INSERT_SYMBOL,
                        [{"ticker": t, "created_at": now} for t in tickers],
                    )
                    ids = {
                        r["ticker"]: int(r["symbol_id"])
                        for r in conn.execute(
                            SQL_GET_SYMBOL_IDS, {"tickers": tickers}
                        ).fetchall()
                    }
                    conn.executemany(SQL_INSERT_ENTRY_BULK, [
                        {
                            "symbol_id": ids[t],
                            "name": row.get("name"),
                            "currency": row.get("currency") or "USD",
                            "tags": list(row.get("tags") or []),
                            "added_by": row.get("added_by"),
                        }
                        for t, row in deduped.items()
                    ])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(deduped)
