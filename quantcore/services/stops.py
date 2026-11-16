"""StopsService — stored stops (#431 phase 2, #221; plan step 4.1).

A stop is the owner's decision to get out of a holding at a level. This
service records it, lists it and takes it off. Nothing here watches a price:
the bar-close checker that tests a stop against closed bars, alerts for a
real book and sells for a paper one is plan step 4.2.

- **A stop is set on a symbol in one book, not on a lot.** Lots bought later
  are covered by the same stop.
- **One active stop per symbol per book.** Setting another replaces it, and
  the old one is kept with status `replaced`. A stop is never deleted.
- **A stop needs a holding.** A symbol the book has no OPEN lot of is refused.
- **Fixed or trailing.** A trailing stop starts from the symbol's last stored
  close, which is its first high-water mark. The level is worked out here and
  stored.
- **A stop sits below the last close.** The close is the newest stored
  closed daily bar's, read with no network call. It is the same price the
  checker will test the stop against.
- **Every stop records where its level came from**: the method, the level
  the analysis proposed, the level saved, the close it was set against and
  that close's date.

Rules kept here (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument on every method.
- `book_id` is keyword-only and defaults to None, the real book. A named book
  is checked first: one that isn't the owner's raises `BookNotFoundError`.
- A read never creates a book.
- An archived book takes no new stop. A stop it already has can still be
  cancelled.

What a stop's terms are is `analytics.stored_stops`. The rows are
`StopsRepository`'s.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from quantcore.analytics import market_time
from quantcore.analytics import stored_stops as stops_math
from quantcore.analytics.orders import fill_price
from quantcore.repositories.books_repository import (  # noqa: F401  (BookNotFoundError re-exported for routes)
    REAL,
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.stops_repository import StopsRepository

# The level columns are NUMERIC(18,6). A larger figure would overflow the
# column and surface as a database error rather than as bad input.
MAX_STOP_LEVEL = Decimal("1000000000")
MAX_SOURCE_REF_LENGTH = 200

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_ZERO = Decimal("0")


class StopNotFoundError(ValueError):
    """A stop id that isn't in this owner's book. A `ValueError`, so a caller
    that treats bad input alike still does; a route answers 404."""


def _number(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{name} is not a valid number: {value!r}") from None
    # NaN and Infinity pass every comparison, and NUMERIC stores NaN.
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return number


def _optional_number(value: Any, name: str) -> Optional[Decimal]:
    return None if value is None else _number(value, name)


def _level(value: Any, name: str) -> Optional[Decimal]:
    """A price the owner or an analysis gave, or None. Not yet rounded."""
    level = _optional_number(value, name)
    if level is not None and level > MAX_STOP_LEVEL:
        raise ValueError(f"{name} is too large")
    return level


def _proposed(value: Any) -> Optional[Decimal]:
    """The level an analysis suggested, to the tick, or None."""
    level = _level(value, "proposed_level")
    if level is None:
        return None
    if level <= _ZERO:
        raise ValueError("proposed_level must be more than zero")
    return fill_price(level)


def _symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not _SYMBOL.match(symbol):
        raise ValueError("symbol is not a valid ticker")
    return symbol


def _source_ref(value: Any) -> Optional[str]:
    ref = (str(value).strip() if value is not None else "") or None
    if ref is not None and len(ref) > MAX_SOURCE_REF_LENGTH:
        raise ValueError(f"source_ref can be at most {MAX_SOURCE_REF_LENGTH} characters")
    return ref


def _one_of(value: Any, allowed, name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}")
    return value


class StopsService:
    def __init__(
        self, stops_repository: StopsRepository, books_repository: BooksRepository, prices
    ) -> None:
        self._repo = stops_repository
        self._books_repo = books_repository
        # PricesService.last_close: the newest stored closed daily bar. Zero
        # network, so setting a stop never waits on Yahoo.
        self._prices = prices

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------
    def _book(self, owner: str, book_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """The named book, or None when none is named (the real book, which
        is not looked up). Raises `BookNotFoundError` for one that isn't
        `owner`'s."""
        if book_id is None:
            return None
        book = self._books_repo.get_book(owner=owner, book_id=book_id)
        if book is None:
            raise BookNotFoundError(f"book {book_id} not found")
        return book

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_stops(
        self, *, owner: str, book_id: Optional[int] = None, include_inactive: bool = False
    ) -> Dict[str, Any]:
        """One book's stops, newest first: the active ones, or with
        `include_inactive` every stop the book has had.

        One query, no network call, and no book is created.
        """
        book = self._book(owner, book_id)
        stops = self._repo.list_stops(owner=owner, book_id=book_id)
        active = [stop for stop in stops if stop["status"] == stops_math.ACTIVE]
        return {
            "book_kind": REAL if book is None else book["kind"],
            "active_count": len(active),
            "stops": stops if include_inactive else active,
        }

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def _reference(self, symbol: str, now: datetime.datetime) -> Dict[str, Any]:
        """The last stored close a new stop is set against, to the tick, and
        its session date. With none on record: no price, and today's market
        date."""
        last = self._prices.last_close(symbol)
        if last["close"] is None:
            return {"price": None, "as_of": market_time.market_date(now)}
        # A daily bar's timestamp is midnight UTC of its session date.
        return {"price": fill_price(last["close"]), "as_of": last["as_of"].date()}

    def set_stop(
        self,
        *,
        owner: str,
        symbol: Any,
        kind: Any,
        stop_level: Any = None,
        trail_pct: Any = None,
        method: Any = stops_math.MANUAL,
        proposed_level: Any = None,
        source: str = stops_math.MANUAL,
        source_ref: Any = None,
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Set the stop on one of a book's holdings and return it.

        A fixed stop gives `stop_level`; a trailing stop gives `trail_pct`
        (percent below the high-water mark, which starts at the last close).
        `method` says how the level was chosen and `proposed_level` what the
        analysis suggested, when one did. `replaced_stop_id` on the answer is
        the stop this one took over from, or None.

        Raises `ValueError` for bad input, a level at or above the last
        close, a symbol the book doesn't hold, or an archived book, and
        `BookNotFoundError` for a book that isn't the owner's.
        """
        symbol = _symbol(symbol)
        method = _one_of(method, stops_math.METHODS, "method")
        source = _one_of(source, stops_math.SOURCES, "source")
        proposed = _proposed(proposed_level)
        ref = _source_ref(source_ref)
        book = self._book(owner, book_id)
        if book is not None and book["archived_at"] is not None:
            raise ValueError("an archived book takes no new stop")
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)

        reference = self._reference(symbol, now)
        terms = stops_math.stop_terms(
            kind,
            stop_level=_level(stop_level, "stop_level"),
            trail_pct=_optional_number(trail_pct, "trail_pct"),
            reference=reference["price"],
        )
        return self._repo.set_stop(owner=owner, book_id=book_id, stop={
            "symbol": symbol,
            "kind": terms.kind,
            "stop_level": terms.stop_level,
            "trail_pct": terms.trail_pct,
            "high_water_mark": terms.high_water_mark,
            "method": method,
            "proposed_level": proposed,
            "reference_price": reference["price"],
            "as_of": reference["as_of"],
            "source": source,
            "source_ref": ref,
            "created_at": now,
        })

    def cancel_stop(
        self,
        *,
        owner: str,
        stop_id: int,
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Take an active stop off and return it.

        Raises `StopNotFoundError` for a stop that isn't in this owner's
        book, and `ValueError` for one that is no longer active: what became
        of a stop is a record, and it is not rewritten.
        """
        self._book(owner, book_id)
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        stop = self._repo.get_stop(owner=owner, stop_id=stop_id, book_id=book_id)
        if stop is None:
            raise StopNotFoundError(f"stop {stop_id} not found")
        if not self._repo.resolve_stop(
            owner=owner, stop_id=stop_id, status=stops_math.CANCELLED,
            resolved_at=now, book_id=book_id,
        ):
            raise ValueError(f"only an active stop can be cancelled: this one is {stop['status']}")
        return self._repo.get_stop(owner=owner, stop_id=stop_id, book_id=book_id)
