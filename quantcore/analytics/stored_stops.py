"""Stored stops: what a stop the owner sets is made of (#431 phase 2, #221;
plan step 4.1).

Pure functions, no I/O. `StopsService` gathers the last close and asks here
what the stop's terms are. Where to *place* a stop is `analytics.stops`, the
stop-loss card's module; this one is about a stop once it has been chosen.

The rules:

- **A stop is fixed or trailing.** A fixed stop is a price. A trailing stop
  is a percentage below a high-water mark, and its level is worked out from
  the two and stored, so a reader never has to.
- **A trailing stop starts from the last close.** That close is its first
  high-water mark. The bar-close checker raises the mark and never lowers it
  (plan step 4.2).
- **A stop sits below the last close.** One at or above it would be broken
  by the bar it was set against. With no close on record a fixed stop is
  taken as typed, and a trailing stop is refused: it has nothing to start
  from.
- **A level is rounded to the market's tick**, the same rule a fill price
  uses, so a stop and the fill it later causes can be compared exactly.
- **Only an active stop is live.** Every other status is final.
- **The stop of a buy that filled at a session's open is live from that
  session** (`first_session`'s `live_from`; plan decision 63). The whole bar
  came after the fill, so there is no low from before the stop existed.
- **A buy order can ask for a stop** (`requested_stop`, `order_stop_terms`;
  plan step 4.3). What it asks for is checked when the order is placed and
  turned into terms when the buy fills, against the fill price: that price
  is the trailing stop's first high-water mark, and a fixed level at or
  above it is no stop at all.
- **A stop is tested against closed daily bars, starting with the first
  session that opens after it was set** (`first_session`, `check_stop`; plan
  step 4.2). A bar that was already trading when the stop was set is not
  tested: its low may be from before the stop existed.
- **A bar reaches a stop when its low is at or under the level.** The stop
  is then reached at its level, or at the bar's open when the stock opened
  under the level (plan decision 5).
- **A trailing stop's mark rises to a bar's high, after that bar is
  tested.** A daily bar doesn't say whether its high or its low came first,
  so a bar is tested against the level it started with.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Optional

from quantcore.analytics import market_time
from quantcore.analytics.orders import fill_price

FIXED = "fixed"
TRAILING = "trailing"
KINDS = (FIXED, TRAILING)

ACTIVE = "active"
TRIGGERED = "triggered"   # a closed bar reached the level (plan step 4.2)
CANCELLED = "cancelled"   # the owner took it off
REPLACED = "replaced"     # the owner set another stop on the symbol
CLOSED = "closed"         # the position went flat (plan step 4.2)
STATUSES = (ACTIVE, TRIGGERED, CANCELLED, REPLACED, CLOSED)
# A stop in one of these never changes again.
TERMINAL_STATUSES = (TRIGGERED, CANCELLED, REPLACED, CLOSED)

# How the level was chosen: typed by the owner, or one of the stop-loss
# card's three candidates. Not a database CHECK, so a new method to compare
# on paper needs no hand-applied migration.
MANUAL = "manual"
METHODS = (MANUAL, "technical", "trailing", "confluence")

# What set the stop. Only `manual` has a writer today. A trade's `source` has
# one more value, `stop`: a stop causes trades, nothing is caused by itself.
SOURCES = (MANUAL, "harvester", "evaluator", "watch", "ai")

_PCT_STEP = Decimal("0.0001")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class StopTerms:
    """What is stored for a stop: its level, and for a trailing stop the
    percentage and the high-water mark the level came from."""

    kind: str
    stop_level: Decimal
    trail_pct: Optional[Decimal] = None
    high_water_mark: Optional[Decimal] = None


def trail_percent(value: Decimal) -> Decimal:
    """A trailing percentage to the four decimals the database stores. More
    than 0 and less than 100, or `ValueError`."""
    pct = value.quantize(_PCT_STEP, rounding=ROUND_HALF_UP)
    if not _ZERO < pct < _HUNDRED:
        raise ValueError("trail_pct must be more than 0 and less than 100")
    return pct


def trailing_level(high_water_mark: Decimal, trail_pct: Decimal) -> Decimal:
    """The level of a trailing stop: `trail_pct` percent below the mark,
    rounded to the tick."""
    return fill_price(high_water_mark * (_HUNDRED - trail_pct) / _HUNDRED)


def _fixed_terms(stop_level: Optional[Decimal], reference: Optional[Decimal]) -> StopTerms:
    if stop_level is None:
        raise ValueError("a fixed stop needs a stop_level")
    if stop_level <= _ZERO:
        raise ValueError("stop_level must be more than zero")
    level = fill_price(stop_level)
    if level <= _ZERO:
        raise ValueError("stop_level is too small to be a price")
    if reference is not None and level >= reference:
        raise ValueError(f"a stop must be below the last close, which is {reference}")
    return StopTerms(kind=FIXED, stop_level=level)


def _trailing_terms(trail_pct: Optional[Decimal], reference: Optional[Decimal]) -> StopTerms:
    if trail_pct is None:
        raise ValueError("a trailing stop needs a trail_pct")
    pct = trail_percent(trail_pct)
    if reference is None:
        raise ValueError("a trailing stop starts from the last close, and none is on record")
    level = trailing_level(reference, pct)
    if not _ZERO < level < reference:
        raise ValueError("the trailing level works out to no price below the last close")
    return StopTerms(kind=TRAILING, stop_level=level, trail_pct=pct, high_water_mark=reference)


def stop_terms(
    kind: str,
    *,
    stop_level: Optional[Decimal] = None,
    trail_pct: Optional[Decimal] = None,
    reference: Optional[Decimal] = None,
) -> StopTerms:
    """The terms of a new stop, or `ValueError` saying what is wrong with it.

    `reference` is the symbol's last close, already rounded to the tick, or
    None when none is on record. A fixed stop gives `stop_level` and no
    `trail_pct`; a trailing stop gives `trail_pct` and no `stop_level`.
    """
    if kind not in KINDS:
        raise ValueError("kind must be fixed or trailing")
    if kind == FIXED:
        if trail_pct is not None:
            raise ValueError("a fixed stop takes a stop_level, not a trail_pct")
        return _fixed_terms(stop_level, reference)
    if stop_level is not None:
        raise ValueError("a trailing stop takes a trail_pct, not a stop_level: its level is worked out")
    return _trailing_terms(trail_pct, reference)


# --------------------------------------------------------------------------
# The stop a buy order asks for (plan step 4.3)
# --------------------------------------------------------------------------

# The level columns are NUMERIC(18,6). A larger figure would overflow the
# column and surface as a database error rather than as bad input.
MAX_LEVEL = Decimal("1000000000")


@dataclass(frozen=True)
class RequestedStop:
    """The stop a buy order asks for, as the order stores it until it fills.
    A fixed stop has a `stop_level` and a trailing stop a `trail_pct`, never
    both. `proposed_level` is what an analysis suggested, when one did."""

    kind: str
    method: str
    stop_level: Optional[Decimal] = None
    trail_pct: Optional[Decimal] = None
    proposed_level: Optional[Decimal] = None


def _asked_level(value: Decimal, name: str) -> Decimal:
    """A level an order asked for, to the tick. More than zero and small
    enough to store, or `ValueError`."""
    if value <= _ZERO:
        raise ValueError(f"{name} must be more than zero")
    if value > MAX_LEVEL:
        raise ValueError(f"{name} is too large")
    level = fill_price(value)
    if level <= _ZERO:
        raise ValueError(f"{name} is too small to be a price")
    return level


def requested_stop(
    kind: str,
    *,
    stop_level: Optional[Decimal] = None,
    trail_pct: Optional[Decimal] = None,
    method: str = MANUAL,
    proposed_level: Optional[Decimal] = None,
) -> RequestedStop:
    """The stop a buy order asks for, or `ValueError` saying what is wrong
    with it. Checked with no price: the buy has not filled yet, so nothing
    here can say whether a fixed level is under the fill price
    (`order_stop_terms` does, once there is one)."""
    if kind not in KINDS:
        raise ValueError("a stop's kind must be fixed or trailing")
    if method not in METHODS:
        raise ValueError(f"a stop's method must be one of {', '.join(METHODS)}")
    proposed = None if proposed_level is None else _asked_level(proposed_level, "proposed_level")
    if kind == FIXED:
        if trail_pct is not None:
            raise ValueError("a fixed stop takes a stop_level, not a trail_pct")
        if stop_level is None:
            raise ValueError("a fixed stop needs a stop_level")
        return RequestedStop(
            kind=FIXED, method=method, stop_level=_asked_level(stop_level, "stop_level"),
            proposed_level=proposed,
        )
    if stop_level is not None:
        raise ValueError("a trailing stop takes a trail_pct, not a stop_level: its level is worked out")
    if trail_pct is None:
        raise ValueError("a trailing stop needs a trail_pct")
    return RequestedStop(
        kind=TRAILING, method=method, trail_pct=trail_percent(trail_pct), proposed_level=proposed,
    )


def order_stop_terms(
    kind: str,
    *,
    stop_level: Optional[Decimal],
    trail_pct: Optional[Decimal],
    fill: Decimal,
) -> Optional[StopTerms]:
    """The terms of the stop a buy asked for, now that it filled at `fill`,
    or None when no stop can be set: a fixed level at or above the fill
    price, or a trailing level that works out to no price below it.

    The fill price stands where the last close does for a stop set by hand
    (`stop_terms`): the level must be under it, and it is a trailing stop's
    first high-water mark.
    """
    try:
        return stop_terms(kind, stop_level=stop_level, trail_pct=trail_pct, reference=fill)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# The stop a holding shows (plan decision 66)
# --------------------------------------------------------------------------

def _shown(stop: Mapping[str, Any], opened: Iterable[datetime.date]) -> bool:
    """Whether a symbol's newest stop is the one its holding shows: it is
    live, or a bar reached it while one of the lots was already open."""
    if stop["status"] == ACTIVE:
        return True
    if stop["status"] != TRIGGERED or stop["triggered_session"] is None:
        return False
    return any(day <= stop["triggered_session"] for day in opened)


def holding_stops(
    stops: Iterable[Mapping[str, Any]],
    opened_by_symbol: Mapping[str, Iterable[datetime.date]],
) -> list:
    """The one stop each held symbol shows, in symbol order.

    `stops` is a book's stops, newest first. `opened_by_symbol` gives, for
    each symbol the book holds, the dates its OPEN lots were opened.

    - Only a symbol's newest stop is looked at. One that was cancelled,
      replaced or closed shows nothing, whatever came before it.
    - An active stop is shown.
    - A stop that a bar reached is shown only when a lot was opened on or
      before that bar's session: the holding it was reached on is still
      there. A position bought back later was never under that stop.
    - A symbol the book doesn't hold shows nothing.
    """
    shown, seen = [], set()
    for stop in stops:
        symbol = stop["symbol"]
        if symbol in seen:
            continue
        seen.add(symbol)
        if symbol in opened_by_symbol and _shown(stop, opened_by_symbol[symbol]):
            shown.append(stop)
    return sorted(shown, key=lambda s: s["symbol"])


# --------------------------------------------------------------------------
# Checking a stop against closed daily bars (plan step 4.2)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Trigger:
    """The bar that reached a stop. `price` is what it was reached at: the
    `level`, or the bar's open when the stock opened under it (`gapped`)."""

    session: datetime.date
    price: Decimal
    level: Decimal
    gapped: bool


@dataclass(frozen=True)
class StopCheck:
    """A stop after a run of bars. `last_bar` is the session of the newest
    bar tested, or None when none was. `stop_level` and `high_water_mark` are
    the terms after those bars: for a triggered stop, the ones it was reached
    with. `trigger` is None while the stop holds."""

    stop_level: Decimal
    high_water_mark: Optional[Decimal]
    last_bar: Optional[datetime.date]
    trigger: Optional[Trigger] = None


def first_session(
    created_at: datetime.datetime, *, live_from: Optional[datetime.date] = None
) -> datetime.date:
    """The first session a stop is tested against. `created_at` is
    timezone-aware.

    With no `live_from`, the first session to open after the stop was set: a
    stop set before 9:30 ET on a trading day is tested against that day's
    bar, and one set during the session or after it starts with the next
    session's bar.

    `live_from` is the session a buy filled in at the opening price, for the
    stop that buy asked for (plan decision 63). The whole of that bar came
    after the fill, so the stop is tested against it, whenever the fill was
    recorded.
    """
    return live_from if live_from is not None else market_time.upcoming_session(created_at)


def next_bar_date(
    created_at: datetime.datetime,
    last_bar_checked: Optional[datetime.date],
    *,
    live_from: Optional[datetime.date] = None,
) -> datetime.date:
    """The earliest session whose bar a stop still has to be tested against."""
    first = first_session(created_at, live_from=live_from)
    if last_bar_checked is None:
        return first
    return max(first, last_bar_checked + datetime.timedelta(days=1))


def is_due(
    created_at: datetime.datetime,
    last_bar_checked: Optional[datetime.date],
    today: datetime.date,
    *,
    live_from: Optional[datetime.date] = None,
) -> bool:
    """Whether a stop is owed a check on the market date `today`: its first
    session has come and today's bar hasn't been tested."""
    return next_bar_date(created_at, last_bar_checked, live_from=live_from) <= today


def _price(value: Any) -> Optional[Decimal]:
    """A bar's price as a Decimal, or None when it is missing, not a number
    or not above zero."""
    if value is None:
        return None
    number = Decimal(str(value))
    return number if number.is_finite() and number > _ZERO else None


def trigger_for(session: datetime.date, level: Decimal, open_price: Decimal) -> Trigger:
    """What a bar that reached `level` did to the stop. The stop is reached
    at its level, or at the open when the stock opened under it."""
    opened = fill_price(open_price)
    if opened < level:
        return Trigger(session=session, price=opened, level=level, gapped=True)
    return Trigger(session=session, price=level, level=level, gapped=False)


def check_stop(
    *,
    kind: str,
    stop_level: Decimal,
    trail_pct: Optional[Decimal],
    high_water_mark: Optional[Decimal],
    bars: Iterable[Mapping[str, Any]],
) -> StopCheck:
    """Test a stop against closed daily bars, oldest first.

    Each bar has `session`, `open`, `high` and `low`. The caller passes only
    the bars the stop has not been tested against. A bar with a missing or
    non-positive price is left out.

    - A bar whose low is at or under the level reaches the stop, and the walk
      ends there (`trigger_for` gives the price).
    - Otherwise, for a trailing stop, a high above the mark raises the mark
      and the level with it. The level never falls.
    """
    level, mark, last_bar = stop_level, high_water_mark, None
    for bar in bars:
        opened, high, low = _price(bar["open"]), _price(bar["high"]), _price(bar["low"])
        if opened is None or high is None or low is None:
            continue
        last_bar = bar["session"]
        if low <= level:
            return StopCheck(level, mark, last_bar, trigger_for(last_bar, level, opened))
        if kind == TRAILING and fill_price(high) > mark:
            mark = fill_price(high)
            level = max(level, trailing_level(mark, trail_pct))
    return StopCheck(level, mark, last_bar)
