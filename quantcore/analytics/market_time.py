"""Pure market-calendar helpers (no I/O) — analytics-layer utilities shared by

the OHLCV persistence layer (bar OPEN/CLOSED classification at write time) and
PricesService (fetch-when-stale policy). All functions accept an injectable
``now`` for deterministic tests. US equities regular session only; not
holiday-aware (same approximation the system has always used).
"""
from __future__ import annotations

import datetime

import pytz

ET = pytz.timezone("America/New_York")

# Mapping from yfinance period strings to calendar days.
PERIOD_DAYS: dict[str, int] = {
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

_OPEN = datetime.time(9, 30)
_CLOSE = datetime.time(16, 0)


def _now_et(now: datetime.datetime | None) -> datetime.datetime:
    if now is None:
        return datetime.datetime.now(tz=ET)
    return now.astimezone(ET)


def period_to_days(period: str) -> int:
    """Convert a yfinance period string to calendar days (default 182)."""
    return PERIOD_DAYS.get(period.lower(), 182)


def is_market_open(now: datetime.datetime | None = None) -> bool:
    """Approximate regular-hours check — weekdays 9:30–16:00 ET, not holiday-aware."""
    current = _now_et(now)
    if current.weekday() >= 5:
        return False
    return _OPEN <= current.time() < _CLOSE


def latest_completed_session(now: datetime.datetime | None = None) -> datetime.date:
    """The most recent trading session that has *started*.

    Overnight (midnight–9:30 ET) and on weekends no newer daily bar can exist,
    so staleness checks compare against this date rather than calendar today.
    """
    current = _now_et(now)
    session = current.date()
    if current.weekday() >= 5 or current.time() < _OPEN:
        session -= datetime.timedelta(days=1)
    while session.weekday() >= 5:
        session -= datetime.timedelta(days=1)
    return session
