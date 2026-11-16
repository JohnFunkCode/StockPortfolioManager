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
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

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
