"""
ohlcv_cache.py — Shared OHLCV bar cache backed by SQLite.

Eliminates redundant yfinance API calls when multiple MCP tools analyse the
same symbol in the same session (e.g. get_rsi, get_macd, get_stochastic all
fetching 6 months of daily bars for the same ticker).

Persists across sessions: only incremental updates are fetched after the
initial warm-up, so a watchlist scan that previously fired 800+ HTTP requests
fires ~110 on the second run (one per symbol to check for new bars).

Public API
----------
    from ohlcv_cache import get_history, period_to_days

    hist = get_history("AAPL", "1d", days=180)   # pd.DataFrame

Bar statuses
------------
    OPEN      — bar is currently forming (today's bar during market hours)
    CLOSED    — bar is final; will not change (all completed historical bars)
    GAP       — no trades during this slot; synthetic placeholder, excluded from results
    CORRECTED — a previously-CLOSED bar was re-fetched with a different close price,
                indicating a split adjustment or exchange correction
"""

from __future__ import annotations

import datetime
import sqlite3
import zoneinfo
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CACHE_DB = Path(__file__).parent / "ohlcv_cache.db"

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class BarStatus(Enum):
    OPEN      = "OPEN"       # Bar is currently forming; data is live and changing.
    CLOSED    = "CLOSED"     # Bar interval has ended; data is finalized/static.
    GAP       = "GAP"        # No trades occurred during this time slot.
    CORRECTED = "CORRECTED"  # Exchange issued a correction after the bar closed.


@dataclass
class OHLCV:
    timestamp: datetime.datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    status:    BarStatus

    @property
    def is_final(self) -> bool:
        """Bar data is finalised — safe to serve from cache without re-fetching."""
        return self.status in (BarStatus.CLOSED, BarStatus.GAP, BarStatus.CORRECTED)

    @property
    def is_tradeable(self) -> bool:
        """Bar has real price data (GAP bars are synthetic placeholders)."""
        return self.status != BarStatus.GAP


# ---------------------------------------------------------------------------
# Interval limits and warm-up windows
# ---------------------------------------------------------------------------

# Maximum calendar days yfinance will return for each interval
_MAX_FETCH_DAYS: dict[str, int] = {
    "1d":  730,
    "1wk": 3650,
    "1mo": 7300,
    "1h":  59,
    "30m": 59,
    "15m": 59,
}

# How far back to populate on a cold start (more than any single tool needs,
# so subsequent calls for the same symbol are served entirely from cache)
_WARM_DAYS: dict[str, int] = {
    "1d":  730,   # 2 years — covers get_higher_lows("1d") which requests "2y"
    "1wk": 1825,
    "1mo": 3650,
    "1h":  59,
    "30m": 59,
    "15m": 59,
}

# Mapping from yfinance period strings to calendar days
_PERIOD_DAYS: dict[str, int] = {
    "1d":  1,
    "5d":  5,
    "30d": 30,
    "60d": 60,
    "90d": 91,
    "1mo": 31,
    "3mo": 91,
    "6mo": 182,
    "1y":  365,
    "2y":  730,
    "3y":  1095,
    "5y":  1825,
    "10y": 3650,
}

_ET = zoneinfo.ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Market hours helpers
# ---------------------------------------------------------------------------

def _is_market_open() -> bool:
    """Approximate US market-hours check.  Does not account for holidays."""
    now = datetime.datetime.now(tz=_ET)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


def _bar_status_for(bar_date: datetime.date, interval: str) -> BarStatus:
    """
    Assign OPEN or CLOSED to a bar based on whether its period is still forming.
    Intraday (1h/30m/15m): caller marks the last bar OPEN when market is open.
    """
    today = datetime.datetime.now(tz=_ET).date()

    if interval == "1d":
        if bar_date == today and _is_market_open():
            return BarStatus.OPEN
        return BarStatus.CLOSED

    if interval == "1wk":
        week_start = today - datetime.timedelta(days=today.weekday())
        if bar_date >= week_start and datetime.datetime.now(tz=_ET).weekday() < 5:
            return BarStatus.OPEN
        return BarStatus.CLOSED

    if interval == "1mo":
        if bar_date.year == today.year and bar_date.month == today.month:
            return BarStatus.OPEN
        return BarStatus.CLOSED

    # Intraday intervals: handled per-row in _store_bars
    return BarStatus.CLOSED


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _init_db() -> None:
    """Create tables if they do not already exist.  Safe to call repeatedly."""
    with sqlite3.connect(CACHE_DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol    TEXT    NOT NULL,
                interval  TEXT    NOT NULL,
                ts        INTEGER NOT NULL,
                open      REAL    NOT NULL,
                high      REAL    NOT NULL,
                low       REAL    NOT NULL,
                close     REAL    NOT NULL,
                volume    INTEGER NOT NULL,
                status    TEXT    NOT NULL
                    CHECK(status IN ('OPEN','CLOSED','GAP','CORRECTED')),
                PRIMARY KEY (symbol, interval, ts)
            );

            CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
                ON ohlcv (symbol, interval, ts DESC);

            CREATE INDEX IF NOT EXISTS idx_ohlcv_needs_action
                ON ohlcv (symbol, interval)
                WHERE status IN ('OPEN', 'CORRECTED');

            CREATE TABLE IF NOT EXISTS fetch_log (
                symbol     TEXT    NOT NULL,
                interval   TEXT    NOT NULL,
                fetched_at INTEGER NOT NULL,
                PRIMARY KEY (symbol, interval)
            );
        """)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ts_to_int(ts) -> int:
    """Convert a pandas Timestamp or datetime-like to UTC Unix seconds."""
    return int(pd.Timestamp(ts).timestamp())


def _count_cached(symbol: str, interval: str) -> int:
    with sqlite3.connect(CACHE_DB) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol=? AND interval=?",
            (symbol, interval),
        ).fetchone()
    return row[0] if row else 0


def _latest_closed_ts(symbol: str, interval: str) -> Optional[int]:
    with sqlite3.connect(CACHE_DB) as conn:
        row = conn.execute(
            "SELECT MAX(ts) FROM ohlcv WHERE symbol=? AND interval=? AND status='CLOSED'",
            (symbol, interval),
        ).fetchone()
    return row[0] if (row and row[0] is not None) else None


def _has_open_bar(symbol: str, interval: str) -> bool:
    with sqlite3.connect(CACHE_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM ohlcv WHERE symbol=? AND interval=? AND status='OPEN' LIMIT 1",
            (symbol, interval),
        ).fetchone()
    return row is not None


def _store_bars(symbol: str, interval: str, df: pd.DataFrame) -> None:
    """
    Upsert all bars from a yfinance DataFrame into the cache.

    Assigns OPEN/CLOSED status based on bar date and interval.

    Detects CORRECTED bars: if a previously-CLOSED bar is re-fetched with a
    materially different close price (>0.1%), status is changed to CORRECTED,
    flagging that a split, dividend adjustment, or data correction occurred.
    """
    if df is None or df.empty:
        return

    last_idx = len(df) - 1

    with sqlite3.connect(CACHE_DB) as conn:
        for i, (ts, row) in enumerate(df.iterrows()):
            ts_int   = _ts_to_int(ts)
            bar_date = pd.Timestamp(ts).date()
            new_close = float(row["Close"])

            if interval in ("1h", "30m", "15m"):
                # Only the last bar in an intraday fetch can be OPEN
                status = BarStatus.OPEN if (i == last_idx and _is_market_open()) else BarStatus.CLOSED
            else:
                status = _bar_status_for(bar_date, interval)

            # Correction detection: existing CLOSED bar with a different close price
            if status == BarStatus.CLOSED:
                existing = conn.execute(
                    "SELECT close, status FROM ohlcv "
                    "WHERE symbol=? AND interval=? AND ts=?",
                    (symbol, interval, ts_int),
                ).fetchone()
                if existing and existing[1] == "CLOSED":
                    cached_close = float(existing[0])
                    if cached_close != 0 and abs(cached_close - new_close) / abs(cached_close) > 0.001:
                        status = BarStatus.CORRECTED

            conn.execute(
                "INSERT OR REPLACE INTO ohlcv "
                "(symbol, interval, ts, open, high, low, close, volume, status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    interval,
                    ts_int,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    new_close,
                    int(row["Volume"]),
                    status.value,
                ),
            )

        conn.execute(
            "INSERT OR REPLACE INTO fetch_log (symbol, interval, fetched_at) "
            "VALUES (?,?,?)",
            (symbol, interval, int(datetime.datetime.utcnow().timestamp())),
        )


def _fetch_yfinance(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch OHLCV bars from yfinance using a start-date window."""
    max_days = _MAX_FETCH_DAYS.get(interval, 365)
    days     = min(days, max_days)
    start    = datetime.datetime.utcnow() - datetime.timedelta(days=days + 5)
    try:
        df = yf.Ticker(symbol).history(
            start=start.strftime("%Y-%m-%d"),
            interval=interval,
        )
        return df
    except Exception:
        return pd.DataFrame()


def _query_cache(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """
    Read bars from SQLite and return a pandas DataFrame matching yfinance format.

    GAP bars are excluded — tools see a continuous time series.
    CORRECTED bars are included with their updated price data.
    Index is a UTC DatetimeIndex named 'Datetime'.
    """
    cutoff = int(
        (datetime.datetime.utcnow() - datetime.timedelta(days=days + 1)).timestamp()
    )
    with sqlite3.connect(CACHE_DB) as conn:
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume "
            "FROM ohlcv "
            "WHERE symbol=? AND interval=? AND ts>=? AND status!='GAP' "
            "ORDER BY ts",
            (symbol, interval, cutoff),
        ).fetchall()

    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    idx = pd.DatetimeIndex(
        [pd.Timestamp(r[0], unit="s", tz="UTC") for r in rows],
        name="Datetime",
    )
    return pd.DataFrame(
        {
            "Open":   [r[1] for r in rows],
            "High":   [r[2] for r in rows],
            "Low":    [r[3] for r in rows],
            "Close":  [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def period_to_days(period: str) -> int:
    """Convert a yfinance period string ('6mo', '90d', '2y', …) to calendar days."""
    return _PERIOD_DAYS.get(period.lower(), 182)


def get_history(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """
    Return OHLCV history as a pandas DataFrame, pulling from SQLite cache when
    possible and fetching from yfinance only when necessary.

    Fetches from yfinance when:
      • No cached data exists for (symbol, interval)          — cold start
      • The cache has an OPEN bar that needs refreshing       — intraday or today's bar
      • The most recent CLOSED bar is at least 1 trading day old

    On a cold start the cache is pre-populated with the maximum useful history
    (_WARM_DAYS) so subsequent calls for the same symbol never need a full fetch.

    The returned DataFrame is identical in structure to yfinance ticker.history():
      columns  Open, High, Low, Close, Volume
      index    DatetimeIndex (UTC)

    Args:
        symbol:   Ticker symbol, e.g. 'AAPL'  (case-insensitive)
        interval: Bar interval: '1d', '1h', '30m', '15m', '1wk', '1mo'
        days:     Minimum calendar days of history the caller needs
    """
    symbol = symbol.upper()
    _init_db()

    needs_fetch = False

    if _count_cached(symbol, interval) == 0:
        needs_fetch = True                          # cold start

    elif _has_open_bar(symbol, interval):
        needs_fetch = True                          # refresh the forming bar

    else:
        latest_ts = _latest_closed_ts(symbol, interval)
        if latest_ts is not None:
            last_date = datetime.datetime.utcfromtimestamp(latest_ts).date()
            today     = datetime.datetime.now(tz=_ET).date()
            days_gap  = (today - last_date).days
            # Any weekday gap means at least one new bar exists
            if days_gap >= 1 and datetime.datetime.now(tz=_ET).weekday() < 5:
                needs_fetch = True

    if needs_fetch:
        warm_days  = _WARM_DAYS.get(interval, 365)
        fetch_days = max(days + 10, warm_days)
        _store_bars(symbol, interval, _fetch_yfinance(symbol, interval, fetch_days))

    return _query_cache(symbol, interval, days)
