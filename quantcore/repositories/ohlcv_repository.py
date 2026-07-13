"""
OHLCV bar persistence — SQL only (architectural-standard-v2 §5.1, issue #74).

Stores and queries daily/intraday bars with OPEN/CLOSED/GAP/CORRECTED status
semantics. Fetching lives in YFinanceGateway.fetch_history; the fetch-when-
stale policy lives in PricesService.get_history. This module never touches
the network.
"""

from __future__ import annotations

import datetime
import enum
import logging
from contextlib import closing
from dataclasses import dataclass
from typing import Optional
from time import time

import pandas as pd

from quantcore.analytics.market_time import ET, is_market_open
from quantcore.db import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class BarStatus(enum.Enum):
    OPEN      = "OPEN"       # bar is still forming (current session)
    CLOSED    = "CLOSED"     # bar is final
    GAP       = "GAP"        # no trades; excluded from query results
    CORRECTED = "CORRECTED"  # close diverged ≥0.1% on re-fetch (split adj.)


@dataclass
class OHLCV:
    timestamp: datetime.datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    status:    BarStatus

    def is_final(self) -> bool:
        return self.status in (BarStatus.CLOSED, BarStatus.CORRECTED)

    def is_tradeable(self) -> bool:
        return self.status != BarStatus.GAP


# Schema is now managed by quantcore.db
# ohlcv and fetch_log tables are in the unified database


# ---------------------------------------------------------------------------
# Bar classification (persistence semantics; clock logic in analytics.market_time)
# ---------------------------------------------------------------------------

def _bar_status_for(bar_date: datetime.date, interval: str) -> BarStatus:
    """
    Classify a bar as OPEN or CLOSED based on interval and current time.
    Intraday bars on the current date while the market is open are OPEN.
    All other bars are CLOSED.
    """
    today = datetime.datetime.now(tz=ET).date()
    if bar_date == today and interval not in ("1d", "1wk", "1mo") and is_market_open():
        return BarStatus.OPEN
    if bar_date == today and interval == "1d" and is_market_open():
        return BarStatus.OPEN
    return BarStatus.CLOSED


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _ts_to_int(ts: object) -> int:
    """Convert a pandas Timestamp (possibly tz-aware) to UTC Unix seconds."""
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp())
    return int(pd.Timestamp(ts).timestamp())


def _count_cached(symbol: str, interval: str) -> int:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol=? AND interval=?",
            (symbol, interval),
        ).fetchone()
    return row[0] if row else 0


def _latest_closed_ts(symbol: str, interval: str) -> Optional[int]:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT MAX(ts) FROM ohlcv WHERE symbol=? AND interval=? AND status='CLOSED'",
            (symbol, interval),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def _has_open_bar(symbol: str, interval: str) -> bool:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT 1 FROM ohlcv WHERE symbol=? AND interval=? AND status='OPEN' LIMIT 1",
            (symbol, interval),
        ).fetchone()
    return row is not None


def _store_bars(symbol: str, interval: str, df: pd.DataFrame) -> None:
    """
    Upsert OHLCV rows.  If an existing CLOSED bar's close diverges ≥0.1% from
    the freshly-fetched value the row is upgraded to CORRECTED (split detection).
    """
    if df.empty:
        return

    with closing(get_connection()) as conn:
        for ts_idx, row in df.iterrows():
            ts_int     = _ts_to_int(ts_idx)
            bar_date   = pd.Timestamp(ts_idx).date()
            new_close  = float(row["Close"])
            status     = _bar_status_for(bar_date, interval)

            if status == BarStatus.CLOSED:
                existing = conn.execute(
                    "SELECT close, status FROM ohlcv WHERE symbol=? AND interval=? AND ts=?",
                    (symbol, interval, ts_int),
                ).fetchone()
                if existing and existing[1] == "CLOSED":
                    cached_close = float(existing[0])
                    if (
                        cached_close != 0
                        and abs(cached_close - new_close) / abs(cached_close) > 0.001
                    ):
                        status = BarStatus.CORRECTED
                        logger.warning(
                            "CORRECTED bar detected: %s %s ts=%s  cached=%.4f  new=%.4f",
                            symbol, interval, ts_int, cached_close, new_close,
                        )

            conn.execute(
                """
                INSERT INTO ohlcv
                    (symbol, interval, ts, open, high, low, close, volume, status, adj_close, data_vendor, ingested_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (symbol, interval, ts) DO UPDATE SET
                    open        = EXCLUDED.open,
                    high        = EXCLUDED.high,
                    low         = EXCLUDED.low,
                    close       = EXCLUDED.close,
                    volume      = EXCLUDED.volume,
                    status      = EXCLUDED.status,
                    adj_close   = EXCLUDED.adj_close,
                    data_vendor = EXCLUDED.data_vendor,
                    ingested_at = EXCLUDED.ingested_at
                """,
                (
                    symbol, interval, ts_int,
                    float(row["Open"]), float(row["High"]),
                    float(row["Low"]),  new_close,
                    int(row["Volume"]) if row["Volume"] is not None else 0,
                    status.value,
                    None,  # adj_close: not available from yfinance with auto_adjust=True
                    "yfinance",
                    int(time()),
                ),
            )

        conn.execute(
            """
            INSERT INTO fetch_log (symbol, interval, fetched_at) VALUES (%s,%s,%s)
            ON CONFLICT (symbol, interval) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
            """,
            (symbol, interval, int(datetime.datetime.utcnow().timestamp())),
        )
        conn.commit()


def _query_cache(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """
    Return cached bars as a DataFrame (columns: Open, High, Low, Close, Volume).
    GAP bars are excluded; CORRECTED bars are included.
    """
    cutoff = int(
        (datetime.datetime.utcnow() - datetime.timedelta(days=days)).timestamp()
    )
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM   ohlcv
            WHERE  symbol=? AND interval=? AND ts>=? AND status != 'GAP'
            ORDER  BY ts ASC
            """,
            (symbol, interval, cutoff),
        ).fetchall()

    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    timestamps = [
        pd.Timestamp(r[0], unit="s", tz="UTC") for r in rows
    ]
    df = pd.DataFrame(
        {
            "Open":   [r[1] for r in rows],
            "High":   [r[2] for r in rows],
            "Low":    [r[3] for r in rows],
            "Close":  [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=pd.DatetimeIndex(timestamps),
    )
    return df


# ---------------------------------------------------------------------------
# Public API (SQL only — fetching lives in YFinanceGateway.fetch_history and
# the fetch-when-stale policy in PricesService.get_history, per issue #74)
# ---------------------------------------------------------------------------

count_cached = _count_cached
latest_closed_ts = _latest_closed_ts
has_open_bar = _has_open_bar
store_bars = _store_bars
get_bars = _query_cache


class OhlcvRepository:
    """OO facade over the module-level cache functions.

    Services depend on this class (constructor-injected via
    quantcore.services.registry) rather than on the module functions, so the
    cache internals can evolve without touching callers. SQL-only: history
    fetching is orchestrated by PricesService via YFinanceGateway.
    """

    def count_cached(self, symbol: str, interval: str) -> int:
        return _count_cached(symbol, interval)

    def latest_closed_ts(self, symbol: str, interval: str):
        return _latest_closed_ts(symbol, interval)

    def has_open_bar(self, symbol: str, interval: str) -> bool:
        return _has_open_bar(symbol, interval)

    def store_bars(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        _store_bars(symbol, interval, df)

    def get_bars(self, symbol: str, interval: str, days: int) -> pd.DataFrame:
        return _query_cache(symbol, interval, days)

    def daily_bars_for_symbols(self, symbols: list[str]) -> list:
        """All cached daily bars for the given symbols, ordered by symbol, ts."""
        if not symbols:
            return []
        with closing(get_connection()) as conn:
            placeholders = ",".join("?" for _ in symbols)
            return conn.execute(
                f"""
                SELECT symbol, ts, close, volume, high, low, open
                FROM ohlcv
                WHERE interval = '1d'
                  AND symbol IN ({placeholders})
                  AND status != 'GAP'
                ORDER BY symbol, ts ASC
                """,
                symbols,
            ).fetchall()
