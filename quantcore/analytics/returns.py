"""Trailing/calendar price returns, one convention (issue #147 Part B1).

Not new math — **one correct copy replacing three inconsistent ones**. The same
five columns already exist in ``portfolio/metrics.py`` (``get_historical_metrics``)
and again as ``pct_return()`` in ``scripts/generate_watchlist_fundamentals_report.py``,
and the two disagree. Everything here is **close-to-close**, which is the report
script's convention and the correct one; three defects in ``metrics.py`` are the
reason to consolidate rather than reuse in place:

* it computes **open-to-close** (``Open.iloc[-6]`` → ``Close.iloc[-1]``), so a
  5-day return silently includes the sixth day's overnight gap;
* it anchors YTD by counting business days from Jan 1 with ``bdate_range`` and
  indexing positionally. ``bdate_range`` counts market holidays as business
  days, so the anchor drifts off the actual first trading bar of the year;
* it takes ``iloc[0]`` of a **two-year** frame and labels the result
  ``one_year_return``. It is a two-year return.

``portfolio/metrics.py`` is deliberately left alone — it is the legacy domain
layer feeding the preserved report script — so both live until that script goes.

Pure functions: dates and closes in, ``Decimal`` percent out. No I/O, no
network, no DataFrame required (Rule 8.4). ``as_of`` is always explicit rather
than read off the clock, so a caller can ask what YTD looked like in March.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Sequence, Tuple

from quantcore.analytics import portfolio_math

# (bar date, close). Chronological, oldest first — the order
# OhlcvRepository.daily_bars_for_symbols already returns.
Bars = Sequence[Tuple[date, float]]

_ONE_YEAR = timedelta(days=365)


def _dec(value: Any) -> Optional[Decimal]:
    """Coerce a close to Decimal, or None if it isn't a usable number.

    Goes through ``str`` rather than ``Decimal(float)`` so a price arrives as
    the number that was printed, not its binary expansion.
    """
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return None if not out.is_finite() else out


def _pct_change(end: Any, start: Any) -> Optional[Decimal]:
    """The shared last step: delegate the percentage itself to
    ``portfolio_math.period_return`` so this module and the Portfolio page's
    return columns cannot drift apart. ``None`` on a missing or zero anchor —
    never divide by zero, never report 0.00% for "unknown"."""
    end_d, start_d = _dec(end), _dec(start)
    if end_d is None or start_d is None or start_d == 0:
        return None
    return portfolio_math.period_return(end_d, start_d)


def trailing_return(closes: Sequence[float], days: int) -> Optional[Decimal]:
    """Close-to-close return over the last ``days`` **bars** (not calendar days).

    ``closes`` is chronological. Needs ``days + 1`` bars to have both ends of
    the window; a shorter history returns ``None`` rather than silently
    measuring a shorter period under a longer label.
    """
    if days <= 0 or len(closes) <= days:
        return None
    return _pct_change(closes[-1], closes[-1 - days])


def ytd_return(bars: Bars, as_of: date) -> Optional[Decimal]:
    """Year-to-date, anchored to the **first close on or after Jan 1** of
    ``as_of``'s year.

    Calendar-aware by construction: the anchor is found by date, so it lands on
    whatever the first actual trading bar of the year was. That is the holiday
    drift ``metrics.py`` gets wrong — it counts business days, and
    ``bdate_range`` counts New Year's Day and every market holiday as business
    days, walking the positional index off the real first bar.

    ``None`` when the frame does not reach back into January, or when the
    anchor bar *is* the latest bar (a one-bar window is not a return).
    """
    window = [(d, c) for d, c in bars if d <= as_of]
    if not window:
        return None

    jan_first = date(as_of.year, 1, 1)
    anchored = [(d, c) for d, c in window if d >= jan_first]
    if len(anchored) < 2:
        return None
    return _pct_change(anchored[-1][1], anchored[0][1])


def one_year_return(bars: Bars, as_of: date) -> Optional[Decimal]:
    """Trailing one year, anchored to the first bar on or after
    ``as_of - 365 days``.

    Two guards, both of which ``metrics.py`` is missing:

    * the anchor is found **by date**, so handing this a two-year frame yields
      a one-year return rather than a two-year one;
    * the frame must actually *span* a year — its oldest bar on or before the
      lookback date — otherwise ``None``. A six-month-old listing has no
      one-year return, and reporting its six-month return in a column headed
      "1y" is the same mislabelling from the other direction.
    """
    window = [(d, c) for d, c in bars if d <= as_of]
    if len(window) < 2:
        return None

    target = as_of - _ONE_YEAR
    if window[0][0] > target:
        return None

    anchored = [(d, c) for d, c in window if d >= target]
    if len(anchored) < 2:
        return None
    return _pct_change(anchored[-1][1], anchored[0][1])


def normalize_market_cap(
    value: Any, currency: Optional[str], fx_rate: Optional[float] = None
) -> Dict[str, Any]:
    """Split a market cap into "the number", "the unit", and "USD, if we know".

    The bug this exists for: the report script's ``fmt_market_cap`` divides by
    1e12 and appends "T" with no regard for currency, so SK hynix's ₩1,456 trillion
    renders as ``1456.62T`` in a column of dollars — a company that looks 60×
    larger than it is, sorted to the top of the table.

    Returning all three fields (rather than one converted number) is deliberate:
    the native figure is what the exchange actually reports, and a caller with
    no rate for the currency can still render ``1456.62T KRW`` honestly. Only
    ``market_cap_usd`` is comparable across securities, so that — not
    ``market_cap`` — is what a cross-currency sort must use.

    ``fx_rate`` is **USD per one unit of ``currency``**. USD needs none; any
    other currency without a rate yields ``market_cap_usd: None`` rather than a
    number that would be wrong by a factor of a thousand.
    """
    ccy = str(currency or "USD").strip().upper() or "USD"
    try:
        native = None if value is None else float(value)
    except (TypeError, ValueError):
        native = None

    if native is None:
        usd = None
    elif ccy == "USD":
        usd = native
    elif fx_rate is None:
        usd = None
    else:
        usd = native * float(fx_rate)

    return {"market_cap": native, "market_cap_currency": ccy, "market_cap_usd": usd}
