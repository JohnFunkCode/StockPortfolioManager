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
- **A stop that held is close when the bar closed within a number of ATRs
  of its level** (`approach`; plan step 4.4). The ATR is a 14-session Wilder
  ATR as of that bar, and the number is `STOP_APPROACH_ATR_MULTIPLE`, read
  by the daily job. Too little history to measure an ATR is no answer, not
  a "far".
- **The read-only stops tool lists what each holding shows** (`book_stops`,
  #579): the stop the Portfolio page draws, how far the last stored close is
  above its level, and whether the daily job last found it close. It
  measures nothing new: "close" is the job's record, not a second rule.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from quantcore.analytics import market_time
from quantcore.analytics.indicators import atr_series
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

# The order a stop at an outside broker was placed as (plan step 4.5). A
# trailing stop is placed as a trailing stop; a fixed one as a stop (a market
# order once triggered) or a stop limit. `stops.broker_order_type` has no
# CHECK, for the reason `method` has none.
STOP_ORDER = "stop"
STOP_LIMIT_ORDER = "stop_limit"
TRAILING_STOP_ORDER = "trailing_stop"
BROKER_ORDER_TYPES = (STOP_ORDER, STOP_LIMIT_ORDER, TRAILING_STOP_ORDER)
_ORDER_TYPES_FOR_KIND = {
    FIXED: (STOP_ORDER, STOP_LIMIT_ORDER),
    TRAILING: (TRAILING_STOP_ORDER,),
}
MAX_BROKERAGE_LENGTH = 60
# `broker_quantity` is NUMERIC(18,6), as a lot's quantity is.
_QUANTITY_STEP = Decimal("0.000001")


@dataclass(frozen=True)
class BrokerOrder:
    """What is stored about a stop's order at an outside broker. Every field
    is None for a stop that exists only here."""

    at_broker: bool = False
    quantity: Optional[Decimal] = None
    order_type: Optional[str] = None
    brokerage: Optional[str] = None


NOT_AT_BROKER = BrokerOrder()


def broker_order(
    kind: str,
    *,
    at_broker: bool,
    quantity: Optional[Decimal] = None,
    order_type: Optional[str] = None,
    brokerage: Optional[str] = None,
) -> BrokerOrder:
    """The broker details of a stop of `kind`, checked, or a `ValueError`
    saying what is wrong with them.

    A stop that isn't at a broker takes no details: a quantity, order type
    or brokerage with `at_broker` false is refused, not dropped, since the
    caller meant something by it. At a broker each detail is optional. The
    quantity is a positive number of shares (whether the book holds that many
    is the caller's to check), the order type must suit the kind, and the
    brokerage is trimmed text, empty read as none.
    """
    name = (brokerage or "").strip() or None
    if not at_broker:
        if any(detail is not None for detail in (quantity, order_type, name)):
            raise ValueError(
                "a quantity, order type or brokerage describes an order at a broker: "
                "set at_broker to give them"
            )
        return NOT_AT_BROKER
    return BrokerOrder(
        True, _broker_quantity(quantity), _broker_order_type(kind, order_type),
        _brokerage(name),
    )


def _broker_quantity(quantity: Optional[Decimal]) -> Optional[Decimal]:
    """A broker order's share count, checked: positive, at a lot's precision."""
    if quantity is None:
        return None
    if quantity <= _ZERO:
        raise ValueError("broker_quantity must be more than zero")
    if quantity != quantity.quantize(_QUANTITY_STEP):
        raise ValueError("broker_quantity can have at most six decimal places")
    return quantity


def _broker_order_type(kind: str, order_type: Optional[str]) -> Optional[str]:
    """A broker order's type, checked against the kinds of order that suit
    a stop of `kind`."""
    allowed = _ORDER_TYPES_FOR_KIND.get(kind, ())
    if order_type is not None and order_type not in allowed:
        raise ValueError(f"a {kind} stop's broker_order_type must be one of {', '.join(allowed)}")
    return order_type


def _brokerage(name: Optional[str]) -> Optional[str]:
    """A brokerage's name, already trimmed, checked for length."""
    if name is not None and len(name) > MAX_BROKERAGE_LENGTH:
        raise ValueError(f"brokerage can be at most {MAX_BROKERAGE_LENGTH} characters")
    return name


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


# --------------------------------------------------------------------------
# Close to the stop: the approach alert (plan step 4.4)
# --------------------------------------------------------------------------

# The ATR the approach distance is measured in: Wilder's, over 14 sessions,
# the stop-loss card's period. It needs one bar more than its period to have
# a true range for each, and it is seeded from the first one, so the read
# behind it covers about three months of sessions.
APPROACH_ATR_PERIOD = 14
APPROACH_MIN_BARS = APPROACH_ATR_PERIOD + 1
APPROACH_LOOKBACK_DAYS = 90
# How many ATRs above its level a close may be and count as close to the
# stop, when STOP_APPROACH_ATR_MULTIPLE is not set (or not a positive number).
DEFAULT_APPROACH_ATR_MULTIPLE = Decimal("1")

_ATR_STEP = Decimal("0.000001")
_DISTANCE_STEP = Decimal("0.1")


@dataclass(frozen=True)
class Approach:
    """How a held stop's newest bar closed against its level. `near` is
    whether the close was within the approach distance; `atr` is the one the
    distance was measured in, as of `session`."""

    session: datetime.date
    close: Decimal
    atr: Decimal
    near: bool


def _usable(bar: Mapping[str, Any]) -> bool:
    return all(_price(bar.get(k)) is not None for k in ("open", "high", "low", "close"))


def _atr(window: Iterable[Mapping[str, Any]]) -> Optional[Decimal]:
    """The `APPROACH_ATR_PERIOD` Wilder ATR as of the last of `window`'s
    usable bars, or None when it comes out zero (a symbol that hasn't moved)."""
    frame = pd.DataFrame({k: [float(b[k]) for b in window] for k in ("high", "low", "close")})
    atr = Decimal(str(float(
        atr_series(frame["high"], frame["low"], frame["close"], APPROACH_ATR_PERIOD).iloc[-1]
    ))).quantize(_ATR_STEP, rounding=ROUND_HALF_UP)
    return atr if atr > _ZERO else None


def approach(
    *,
    bars: Iterable[Mapping[str, Any]],
    last_bar: Optional[datetime.date],
    stop_level: Decimal,
    multiple: Decimal = DEFAULT_APPROACH_ATR_MULTIPLE,
) -> Optional[Approach]:
    """Whether a stop that held through `last_bar` closed close to its level.

    `bars` are closed daily bars, oldest first, reaching back far enough for
    the ATR; only those up to `last_bar` count. The newest usable one must be
    `last_bar` itself: the approach is about the bar the stop was just tested
    against. `stop_level` is the level after that bar (a trailing stop's
    raised one).

    A close is near when it is no more than `multiple` ATRs above the level.
    None when it can't be judged: no bar was tested, `last_bar` is missing or
    has a bad price, or there are fewer than `APPROACH_MIN_BARS` usable bars.
    A caller leaves what it recorded before as it was.
    """
    if last_bar is None:
        return None
    window = [b for b in bars if b["session"] <= last_bar and _usable(b)]
    if len(window) < APPROACH_MIN_BARS or window[-1]["session"] != last_bar:
        return None
    atr = _atr(window)
    if atr is None:
        return None
    close = fill_price(_price(window[-1]["close"]))
    return Approach(
        session=last_bar, close=close, atr=atr, near=close - stop_level <= multiple * atr
    )


def atr_distance(close: Decimal, stop_level: Decimal, atr: Decimal) -> Decimal:
    """How many ATRs `close` was above `stop_level`, to one decimal place:
    what the approach alert tells the owner."""
    return ((close - stop_level) / atr).quantize(_DISTANCE_STEP, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# A book's stops, as the read-only stops tool gives them (#579)
# --------------------------------------------------------------------------

_FRACTION_STEP = Decimal("0.0001")

# The stop fields the tool passes through as stored. Everything else on the
# row (where the level came from, alert bookkeeping, the raw approach close
# and ATR) is left out: the model can't use it, and the Portfolio page has it.
_TOOL_FIELDS = (
    "stop_id", "symbol", "status", "kind", "stop_level", "trail_pct",
    "high_water_mark", "method", "at_broker", "broker_quantity",
    "broker_order_type", "brokerage", "created_at", "last_bar_checked",
    "triggered_session", "trigger_price",
)


def stop_distance(close: Any, stop_level: Decimal) -> dict:
    """How far a close sits above a stop's level: `distance` in price, to
    the tick, and `distance_fraction` as a share of the close (0.05 is 5%),
    to four places. Negative when the close is under the level. Both None
    with no usable close."""
    price = _price(close)
    if price is None:
        return {"distance": None, "distance_fraction": None}
    price = fill_price(price)
    distance = price - stop_level
    return {
        "distance": distance,
        "distance_fraction": (distance / price).quantize(_FRACTION_STEP, rounding=ROUND_HALF_UP),
    }


def _near(stop: Mapping[str, Any]) -> dict:
    """What the daily job last recorded about a live stop being close
    (plan step 4.4): whether it is, the session it was judged on, and how
    many ATRs above the level that session closed."""
    recorded = stop["status"] == ACTIVE and stop.get("approached_session") is not None
    atr = stop.get("approach_atr") if recorded else None
    usable = atr is not None and atr > _ZERO and stop.get("approach_close") is not None
    return {
        "near": recorded,
        "approached_session": stop["approached_session"] if recorded else None,
        "atr_distance": (
            atr_distance(stop["approach_close"], stop["stop_level"], atr) if usable else None
        ),
    }


def tool_stop(stop: Mapping[str, Any], last_close: Mapping[str, Any]) -> dict:
    """One stop a holding shows, as the stops tool lists it: its terms, the
    symbol's last stored close (`last_close`, `last_close_date`), how far
    that close is above the level, and whether the daily job found the
    newest bar it tested close to the level."""
    close = last_close.get("close")
    return {
        **{field: stop.get(field) for field in _TOOL_FIELDS},
        "last_close": fill_price(close) if _price(close) is not None else None,
        "last_close_date": last_close.get("as_of"),
        **stop_distance(close, stop["stop_level"]),
        **_near(stop),
    }


def book_stops(
    stops: Iterable[Mapping[str, Any]],
    opened_by_symbol: Mapping[str, Iterable[datetime.date]],
    closes: Mapping[str, Mapping[str, Any]],
) -> dict:
    """One book's stops for the read-only stops tool (#579).

    `stops` and `opened_by_symbol` are what `holding_stops` takes: every
    symbol the book holds is a key of `opened_by_symbol`, even one whose lots
    have no date. `closes` maps a symbol to its last stored close
    (`{"close", "as_of"}`); a symbol missing from it has none.

    - `stops`: the stop each holding shows (`holding_stops`), in symbol
      order, with `tool_stop`'s fields: a live one, or one a bar reached
      while the holding was open.
    - `unprotected`: the held symbols with no live stop, in order. A symbol
      whose stop was reached and is still held is in both lists.
    """
    shown = holding_stops(stops, opened_by_symbol)
    live = {stop["symbol"] for stop in shown if stop["status"] == ACTIVE}
    return {
        "stops": [tool_stop(stop, closes.get(stop["symbol"], {})) for stop in shown],
        "unprotected": sorted(symbol for symbol in opened_by_symbol if symbol not in live),
    }
