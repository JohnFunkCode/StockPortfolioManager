"""
ohlcv_cache.py — Shared SQLite-backed OHLCV bar cache.

All MCP servers route yfinance ticker.history() calls through this module so
that expensive network fetches are performed at most once per bar per interval.

Public API
----------
    get_history(symbol, interval, days) -> pd.DataFrame
    period_to_days(period)             -> int
"""

from __future__ import annotations

import datetime
import enum
import logging
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import sqlite3

import pandas as pd
import pytz
import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_DB = Path(__file__).parent / "ohlcv_cache.db"

_ET = pytz.timezone("America/New_York")

# How many days of history to pre-populate on a cold start, per interval
_WARM_DAYS: dict[str, int] = {
    "1d":  730,
    "1wk": 1825,
    "1mo": 3650,
    "1h":  59,
    "30m": 59,
    "15m": 59,
}

# Hard cap on a single yfinance fetch window
_MAX_FETCH_DAYS = 730

# Mapping from yfinance period strings to calendar days
_PERIOD_DAYS: dict[str, int] = {
    "1d":   1,
    "5d":   5,
    "30d":  30,
    "60d":  60,
    "90d":  91,
    "1mo":  31,
    "3mo":  91,
    "6mo":  182,
    "1y":   365,
    "2y":   730,
    "3y":   1095,
    "5y":   1825,
    "10y":  3650,
}

_VALID_INTERVALS = {"1d", "1wk", "1mo", "1h", "30m", "15m"}


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


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    ts       INTEGER NOT NULL,
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   INTEGER NOT NULL,
    status   TEXT    NOT NULL CHECK(status IN ('OPEN','CLOSED','GAP','CORRECTED')),
    PRIMARY KEY (symbol, interval, ts)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
    ON ohlcv (symbol, interval, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_needs_action
    ON ohlcv (symbol, interval)
    WHERE status IN ('OPEN','CORRECTED');

CREATE TABLE IF NOT EXISTS fetch_log (
    symbol     TEXT    NOT NULL,
    interval   TEXT    NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval)
);
"""

_db_initialised = False
_db_init_lock = threading.Lock()

_SQLITE_TIMEOUT = 30  # seconds to wait for a write lock before raising


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB, timeout=_SQLITE_TIMEOUT)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _init_db() -> None:
    global _db_initialised
    if _db_initialised:
        return
    with _db_init_lock:
        if _db_initialised:
            return
        with closing(_connect()) as conn:
            conn.executescript(_DDL)
        _db_initialised = True


# ---------------------------------------------------------------------------
# Market-hours helpers
# ---------------------------------------------------------------------------

def _is_market_open() -> bool:
    """Approximate check — not holiday-aware."""
    now = datetime.datetime.now(tz=_ET)
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now < market_close


def _bar_status_for(bar_date: datetime.date, interval: str) -> BarStatus:
    """
    Classify a bar as OPEN or CLOSED based on interval and current time.
    Intraday bars on the current date while the market is open are OPEN.
    All other bars are CLOSED.
    """
    today = datetime.datetime.now(tz=_ET).date()
    if bar_date == today and interval not in ("1d", "1wk", "1mo") and _is_market_open():
        return BarStatus.OPEN
    if bar_date == today and interval == "1d" and _is_market_open():
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
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol=? AND interval=?",
            (symbol, interval),
        ).fetchone()
    return row[0] if row else 0


def _latest_closed_ts(symbol: str, interval: str) -> Optional[int]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT MAX(ts) FROM ohlcv WHERE symbol=? AND interval=? AND status='CLOSED'",
            (symbol, interval),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def _has_open_bar(symbol: str, interval: str) -> bool:
    with closing(_connect()) as conn:
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

    with closing(_connect()) as conn:
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
                INSERT OR REPLACE INTO ohlcv
                    (symbol, interval, ts, open, high, low, close, volume, status)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol, interval, ts_int,
                    float(row["Open"]), float(row["High"]),
                    float(row["Low"]),  new_close,
                    int(row["Volume"]) if row["Volume"] is not None else 0,
                    status.value,
                ),
            )

        conn.execute(
            "INSERT OR REPLACE INTO fetch_log (symbol, interval, fetched_at) VALUES (?,?,?)",
            (symbol, interval, int(datetime.datetime.utcnow().timestamp())),
        )


def _query_cache(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """
    Return cached bars as a DataFrame (columns: Open, High, Low, Close, Volume).
    GAP bars are excluded; CORRECTED bars are included.
    """
    cutoff = int(
        (datetime.datetime.utcnow() - datetime.timedelta(days=days)).timestamp()
    )
    with closing(_connect()) as conn:
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
# yfinance fetch
# ---------------------------------------------------------------------------

def _fetch_yfinance(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Download from yfinance, capped at _MAX_FETCH_DAYS."""
    fetch_days = min(days, _MAX_FETCH_DAYS)
    end   = datetime.datetime.utcnow()
    start = end - datetime.timedelta(days=fetch_days)
    logger.debug("yfinance fetch: %s %s start=%s end=%s", symbol, interval, start.date(), end.date())
    df = yf.download(
        symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if df.empty:
        logger.warning("yfinance returned no data for %s/%s", symbol, interval)
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    # yf.download returns MultiIndex columns when downloading a single symbol in
    # newer versions; flatten if needed.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def period_to_days(period: str) -> int:
    """Convert a yfinance period string to calendar days."""
    return _PERIOD_DAYS.get(period.lower(), 182)


def get_history(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """
    Return OHLCV history as a DataFrame with columns Open, High, Low, Close, Volume
    and a UTC DatetimeIndex — identical in structure to yfinance ticker.history().

    Serves from the SQLite cache when possible; fetches from yfinance only when:
      - No cached data exists (cold start)
      - The cache contains an OPEN bar that needs refreshing
      - The most recent CLOSED bar is ≥1 trading day old on a weekday
    """
    if interval not in _VALID_INTERVALS:
        raise ValueError(f"Invalid interval '{interval}'. Valid: {_VALID_INTERVALS}")

    symbol = symbol.upper()
    _init_db()

    needs_fetch = False
    if _count_cached(symbol, interval) == 0:
        logger.info("Cold start: %s/%s — fetching %d warm days", symbol, interval,
                    _WARM_DAYS.get(interval, 730))
        needs_fetch = True
        days = max(days, _WARM_DAYS.get(interval, 730))
    elif _has_open_bar(symbol, interval):
        logger.debug("OPEN bar detected for %s/%s — refreshing", symbol, interval)
        needs_fetch = True
    else:
        latest_ts = _latest_closed_ts(symbol, interval)
        if latest_ts is not None:
            last_date = datetime.datetime.utcfromtimestamp(latest_ts).date()
            today     = datetime.datetime.now(tz=_ET).date()
            days_gap  = (today - last_date).days
            if days_gap >= 1 and datetime.datetime.now(tz=_ET).weekday() < 5:
                logger.debug(
                    "Stale cache for %s/%s (gap=%d days) — refreshing", symbol, interval, days_gap
                )
                needs_fetch = True

    if needs_fetch:
        fresh = _fetch_yfinance(symbol, interval, days)
        if not fresh.empty:
            _store_bars(symbol, interval, fresh)

    return _query_cache(symbol, interval, days)
