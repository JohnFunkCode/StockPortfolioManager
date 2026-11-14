"""FX date maths for trade-date conversion (#235, plan step 2.3).

Pure functions, no I/O. Realized P/L converts each side of a sale to USD at
the rate on that side's trade date. The rates come from a daily series
(`YFinanceGateway.fx_daily_closes`); which close answers for which date is
decided here.

A rate is always **USD per one unit of the currency**, the convention
`returns.normalize_market_cap` already uses.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Tuple

USD = "USD"

# How far back a trade date may reach for a close. A weekend is two days and a
# holiday weekend four; a week covers both. Past that the series has a hole,
# and a rate from the far side of a hole is a guess, so there is no rate.
MAX_GAP_DAYS = 7

# The stored precision of `positions.fx_rate_at_purchase` and
# `lot_sales.fx_rate_at_sale`: NUMERIC(18,8).
RATE_QUANTUM = Decimal("0.00000001")


def normalize_currency(currency: Optional[str]) -> str:
    """The currency code as stored on a lot: upper case, USD when blank."""
    return (currency or USD).strip().upper() or USD


def needs_rate(currency: Optional[str]) -> bool:
    """True for a currency that has to be converted. USD never is."""
    return normalize_currency(currency) != USD


def as_date(value: Any) -> Optional[datetime.date]:
    """A trade date from a `date`, a `datetime` or an ISO string; None when it
    is missing or not a date."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fetch_window(dates: Iterable[datetime.date]) -> Tuple[datetime.date, datetime.date]:
    """The inclusive ``(start, end)`` range of closes needed to rate ``dates``.

    It starts ``MAX_GAP_DAYS`` before the earliest date, so that date can
    still find its prior close. Raises ``ValueError`` for no dates.
    """
    wanted = sorted(dates)
    if not wanted:
        raise ValueError("no dates to fetch FX closes for")
    return wanted[0] - datetime.timedelta(days=MAX_GAP_DAYS), wanted[-1]


def _valid_rate(value: Any) -> Optional[Decimal]:
    """``value`` as a stored rate, or None unless it is a positive finite
    number that survives rounding to the stored precision."""
    if value is None:
        return None
    try:
        rate = Decimal(str(value))
    except InvalidOperation:
        return None
    if not rate.is_finite() or rate <= 0:
        return None
    rate = rate.quantize(RATE_QUANTUM)
    return rate if rate > 0 else None


def prior_close_rate(
    closes: Mapping[datetime.date, Any],
    on: datetime.date,
    max_gap_days: int = MAX_GAP_DAYS,
) -> Optional[Decimal]:
    """The rate for a trade on ``on``: that day's close, or the most recent
    close before it when ``on`` had none (a weekend or a holiday).

    Never a later close: a trade can't be converted at a rate that didn't
    exist yet. None when no usable close lies within ``max_gap_days`` before
    ``on``. A close that isn't a positive finite number is skipped, so a bad
    print falls through to the close before it. A missing rate is None and
    never 1.0.
    """
    for back in range(max_gap_days + 1):
        rate = _valid_rate(closes.get(on - datetime.timedelta(days=back)))
        if rate is not None:
            return rate
    return None
