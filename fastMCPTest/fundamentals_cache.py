"""
SQLite-backed append-only cache for fundamental data.

Stores snapshots of earnings calendar, fundamental scores, revenue growth,
and EPS acceleration indexed by (symbol, data_type, fetched_at).

TTL-based freshness checking (default 24h, configurable via FUNDAMENTALS_CACHE_TTL_HOURS).
Every cache miss appends a new row, building a time series for trend analysis.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_DB = Path(__file__).parent / "fundamentals_history.db"
_SQLITE_TIMEOUT = 30
_db_initialised = False
_db_init_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol      TEXT    NOT NULL,
    data_type   TEXT    NOT NULL,
    fetched_at  INTEGER NOT NULL,
    payload     TEXT    NOT NULL,
    PRIMARY KEY (symbol, data_type, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_latest
    ON fundamentals_history (symbol, data_type, fetched_at DESC);
"""


def _get_ttl_seconds() -> float:
    """Read TTL from env var on every call so changes take effect without restart."""
    raw = os.getenv("FUNDAMENTALS_CACHE_TTL_HOURS", "24")
    try:
        ttl_hours = float(raw)
        return max(0.0, ttl_hours) * 3600.0
    except (ValueError, TypeError):
        logger.warning(f"Invalid FUNDAMENTALS_CACHE_TTL_HOURS={raw}, using default 24h")
        return 86400.0


def _connect() -> sqlite3.Connection:
    """Establish SQLite connection with WAL and NORMAL pragmas."""
    try:
        conn = sqlite3.connect(CACHE_DB, timeout=_SQLITE_TIMEOUT)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Failed to connect to cache DB at {CACHE_DB}: {e}")
        raise


def _init_db() -> None:
    """Initialize DB schema with double-checked locking pattern."""
    global _db_initialised
    if _db_initialised:
        return

    with _db_init_lock:
        if _db_initialised:
            return
        try:
            with closing(_connect()) as conn:
                conn.executescript(_DDL)
                conn.commit()
            _db_initialised = True
            logger.info(f"Initialized fundamentals cache at {CACHE_DB}")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize fundamentals cache: {e}")
            raise


def cache_get(symbol: str, data_type: str) -> dict | None:
    """
    Retrieve most recent cached entry if fresh (within TTL).

    Args:
        symbol: Stock ticker (e.g. 'NVDA')
        data_type: One of: earnings_calendar, fundamental_score, revenue_growth, earnings_acceleration

    Returns:
        Cached payload dict if fresh, None otherwise
    """
    _init_db()
    ttl_seconds = _get_ttl_seconds()

    # TTL=0 disables the cache
    if ttl_seconds <= 0:
        logger.debug(f"Cache disabled (TTL=0) for {symbol}/{data_type}")
        return None

    try:
        with closing(_connect()) as conn:
            now = time.time()
            cutoff_ts = int(now - ttl_seconds)

            cursor = conn.execute(
                """
                SELECT payload, fetched_at FROM fundamentals_history
                WHERE symbol = ? AND data_type = ? AND fetched_at >= ?
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (symbol.upper(), data_type, cutoff_ts)
            )
            row = cursor.fetchone()

            if row is None:
                logger.debug(f"Cache miss: {symbol}/{data_type}")
                return None

            payload_json, fetched_ts = row
            try:
                payload = json.loads(payload_json)
                logger.debug(f"Cache hit: {symbol}/{data_type} (age: {int(now - fetched_ts)}s)")
                return payload
            except json.JSONDecodeError as e:
                logger.warning(f"Corrupt cache entry for {symbol}/{data_type}: {e}")
                return None

    except sqlite3.Error as e:
        logger.error(f"DB error reading cache for {symbol}/{data_type}: {e}")
        return None


def cache_set(symbol: str, data_type: str, payload: dict) -> None:
    """
    Write new cache entry (append-only).

    Args:
        symbol: Stock ticker
        data_type: Data type string
        payload: Dict to cache (must be JSON-serializable)
    """
    _init_db()

    if payload is None:
        logger.debug(f"Skipping cache_set for {symbol}/{data_type}: payload is None")
        return

    try:
        payload_json = json.dumps(payload)
    except (TypeError, ValueError) as e:
        logger.error(f"Cannot serialize payload for {symbol}/{data_type}: {e}")
        return

    try:
        fetched_at = int(time.time())
        with closing(_connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO fundamentals_history (symbol, data_type, fetched_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (symbol.upper(), data_type, fetched_at, payload_json)
            )
            conn.commit()
            logger.debug(f"Cached {symbol}/{data_type} at ts={fetched_at}")
    except sqlite3.Error as e:
        logger.error(f"DB error writing cache for {symbol}/{data_type}: {e}")


def cache_history(symbol: str, data_type: str, since_days: int = 365) -> list[dict]:
    """
    Retrieve all historical snapshots within lookback window (oldest first).

    Args:
        symbol: Stock ticker
        data_type: Data type string
        since_days: How many days back to look (default 365)

    Returns:
        List of dicts, each with fetched_at (ISO string) + payload fields, oldest first
    """
    _init_db()

    try:
        cutoff_ts = int(time.time()) - (since_days * 86400)
        with closing(_connect()) as conn:
            cursor = conn.execute(
                """
                SELECT fetched_at, payload FROM fundamentals_history
                WHERE symbol = ? AND data_type = ? AND fetched_at >= ?
                ORDER BY fetched_at ASC
                """,
                (symbol.upper(), data_type, cutoff_ts)
            )

            results = []
            for fetched_ts, payload_json in cursor.fetchall():
                try:
                    payload = json.loads(payload_json)
                    payload["fetched_at"] = datetime.fromtimestamp(
                        fetched_ts, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    payload["_fetched_at_ts"] = fetched_ts  # raw integer for freshness checks
                    results.append(payload)
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping corrupt entry for {symbol}/{data_type} at ts={fetched_ts}: {e}")
                    continue

            logger.debug(f"Retrieved {len(results)} history entries for {symbol}/{data_type}")
            return results

    except sqlite3.Error as e:
        logger.error(f"DB error reading history for {symbol}/{data_type}: {e}")
        return []


def cache_invalidate(symbol: str, data_type: str | None = None) -> None:
    """
    Delete cache entries for a symbol (and optionally a specific data_type).

    Args:
        symbol: Stock ticker
        data_type: Specific data_type to delete, or None to delete all for this symbol
    """
    _init_db()

    try:
        with closing(_connect()) as conn:
            if data_type is None:
                conn.execute(
                    "DELETE FROM fundamentals_history WHERE symbol = ?",
                    (symbol.upper(),)
                )
                logger.info(f"Invalidated all cache entries for {symbol}")
            else:
                conn.execute(
                    "DELETE FROM fundamentals_history WHERE symbol = ? AND data_type = ?",
                    (symbol.upper(), data_type)
                )
                logger.info(f"Invalidated cache entries for {symbol}/{data_type}")
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"DB error invalidating cache for {symbol}/{data_type}: {e}")


def cache_get_all_latest(data_type: str) -> list[dict]:
    """
    Retrieve most recent entry per symbol for a given data_type (no TTL filtering).

    Used by ranking, sector, and earnings tools to get a full inventory of cached symbols.

    Args:
        data_type: Data type string

    Returns:
        List of dicts, each with symbol, fetched_at (ISO), _fetched_at_ts (integer), + payload fields
    """
    _init_db()

    try:
        with closing(_connect()) as conn:
            # GROUP BY symbol, keep MAX(fetched_at) row for each
            cursor = conn.execute(
                """
                SELECT symbol, payload, MAX(fetched_at) as fetched_at
                FROM fundamentals_history
                WHERE data_type = ?
                GROUP BY symbol
                ORDER BY fetched_at DESC
                """,
                (data_type,)
            )

            results = []
            for symbol, payload_json, fetched_ts in cursor.fetchall():
                try:
                    payload = json.loads(payload_json)
                    payload["symbol"] = symbol
                    payload["fetched_at"] = datetime.fromtimestamp(
                        fetched_ts, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    payload["_fetched_at_ts"] = fetched_ts  # raw integer for freshness checks
                    results.append(payload)
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping corrupt entry for {symbol}/{data_type} at ts={fetched_ts}: {e}")
                    continue

            logger.debug(f"Retrieved {len(results)} latest entries for data_type={data_type}")
            return results

    except sqlite3.Error as e:
        logger.error(f"DB error reading latest entries for data_type={data_type}: {e}")
        return []


def cache_stats() -> dict:
    """
    Return cache inventory: symbol counts, date ranges, and DB size per data_type.

    Returns:
        Dict with data_types list and db_size_bytes
    """
    _init_db()

    try:
        with closing(_connect()) as conn:
            cursor = conn.execute(
                """
                SELECT data_type, COUNT(DISTINCT symbol) as symbol_count,
                       MIN(fetched_at) as oldest, MAX(fetched_at) as newest
                FROM fundamentals_history
                GROUP BY data_type
                ORDER BY data_type
                """
            )

            data_types = []
            for data_type, symbol_count, oldest_ts, newest_ts in cursor.fetchall():
                oldest_iso = datetime.fromtimestamp(
                    oldest_ts, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ") if oldest_ts else None
                newest_iso = datetime.fromtimestamp(
                    newest_ts, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ") if newest_ts else None

                data_types.append({
                    "data_type": data_type,
                    "symbol_count": symbol_count,
                    "oldest": oldest_iso,
                    "newest": newest_iso,
                })

            db_size_bytes = CACHE_DB.stat().st_size if CACHE_DB.exists() else 0

            return {
                "db_path": str(CACHE_DB),
                "db_size_bytes": db_size_bytes,
                "data_types": data_types,
            }

    except (sqlite3.Error, OSError) as e:
        logger.error(f"Error reading cache stats: {e}")
        return {
            "db_path": str(CACHE_DB),
            "db_size_bytes": 0,
            "data_types": [],
            "error": str(e),
        }
