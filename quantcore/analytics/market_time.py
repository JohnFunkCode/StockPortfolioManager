"""Pure market-calendar helpers (no I/O) — analytics-layer utilities shared by

the OHLCV persistence layer (bar OPEN/CLOSED classification at write time) and
PricesService (fetch-when-stale policy). All functions accept an injectable
``now`` for deterministic tests. US equities regular session only; not
holiday-aware for the *regular-hours* helpers (same approximation the system
has always used); ``is_trading_day`` / ``nyse_holidays`` are the full-day
holiday calendar, used by the daily job to skip closed days.
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


def market_date(now: datetime.datetime | None = None) -> datetime.date:
    """Today's calendar date in market (Eastern) time.

    The date a *holding period* is measured against. ``datetime.date.today()``
    is the host's date, which on Cloud Run is UTC — so from roughly 5pm ET
    until midnight ET it is already tomorrow, and every calendar-day count
    anchored to it comes out one day long. That is not cosmetic: a lot bought
    eight days ago reports nine, and any per-day rate divided by it is
    understated by the same ratio (~11% on a nine-day hold).

    Deliberately *not* ``latest_completed_session()`` — that rolls back to the
    prior trading day overnight and on weekends, which is right for "is there a
    newer bar?" and wrong here, since a holding period keeps accruing over a
    weekend.
    """
    return _now_et(now).date()


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


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    """The n-th (1-based) given weekday of a month; n=-1 means the last one."""
    if n > 0:
        first = datetime.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + datetime.timedelta(days=offset + 7 * (n - 1))
    nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - datetime.timedelta(days=1)
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> datetime.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return datetime.date(year, month, day)


def _observed(day: datetime.date) -> datetime.date | None:
    """NYSE observance: Saturday -> Friday, Sunday -> Monday."""
    if day.weekday() == 5:
        return day - datetime.timedelta(days=1)
    if day.weekday() == 6:
        return day + datetime.timedelta(days=1)
    return day


def nyse_holidays(year: int) -> set[datetime.date]:
    """Full-day NYSE closures falling inside ``year``.

    Computed from the published rules rather than a vendored table so the job
    cannot silently run out of calendar. Not covered: one-off closures (national
    days of mourning, weather); a run on such a day just captures what Yahoo
    serves, which is the pre-existing behaviour.
    """
    days: set[datetime.date] = {
        _nth_weekday(year, 1, 0, 3),                           # MLK Day
        _nth_weekday(year, 2, 0, 3),                           # Presidents Day
        _easter(year) - datetime.timedelta(days=2),            # Good Friday
        _nth_weekday(year, 5, 0, -1),                          # Memorial Day
        _observed(datetime.date(year, 7, 4)),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),                           # Labor Day
        _nth_weekday(year, 11, 3, 4),                          # Thanksgiving
        _observed(datetime.date(year, 12, 25)),                # Christmas
    }
    if year >= 2022:
        days.add(_observed(datetime.date(year, 6, 19)))        # Juneteenth
    # New Year's Day: a Saturday Jan 1 is NOT observed on the prior Friday
    # (that Friday falls in the previous year and the NYSE stays open).
    jan1 = datetime.date(year, 1, 1)
    if jan1.weekday() == 6:
        days.add(datetime.date(year, 1, 2))
    elif jan1.weekday() != 5:
        days.add(jan1)
    return {d for d in days if d.year == year}


def is_trading_day(day: datetime.date | None = None) -> bool:
    """True when the NYSE holds a regular session on ``day`` (default: today ET)."""
    day = day or market_date()
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def trading_days_after(start: datetime.date, end: datetime.date) -> int:
    """How many NYSE trading days fall in ``(start, end]`` — 0 when ``end <= start``.

    The age of a once-a-day capture: a Friday capture read on Monday is 1
    (Monday's session), read on Tuesday it is 2. Holidays and weekends never
    count, so the day after a holiday doesn't make yesterday's data look old.
    """
    count = 0
    day = start + datetime.timedelta(days=1)
    while day <= end:
        if is_trading_day(day):
            count += 1
        day += datetime.timedelta(days=1)
    return count
