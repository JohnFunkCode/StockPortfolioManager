"""StopsService — stored stops (#431 phase 2, #221; plan steps 4.1, 4.2 and 4.4).

A stop is the owner's decision to get out of a holding at a level. This
service records it, lists it, takes it off, and checks it: the daily job
tests every active stop against the closed daily bars it has not seen
(`due_stops`, `check_stop`).

- **The checker reads closed bars only**, starting with the first session
  that opens after the stop was set, and works through every bar since the
  last one it tested. A night the job missed is made up on the next run.
  The stop of a buy that filled at a session's open starts with that
  session's own bar (`stops.first_session`).
- **A trailing stop's mark rises to a bar's high and never falls.**
- **A bar whose low reached the level triggers the stop.** In a real book
  that is a record and an alert: nothing is sold. In a paper book the whole
  position is sold at the level, or at the open when the stock opened under
  it, in the transaction that marks the stop `triggered`
  (`OrdersService.sell_for_stop`).
- **A stop is closed when its position goes flat** (`close_flat_stops`, and
  the sale's own transaction).
- **An archived book's stops are not checked.**
- **A stop that held is checked for being close**: its newest bar's close
  within a number of ATRs of the level is recorded on the stop
  (`stops.approached_session`), and cleared when a later close is further
  away. The daily job tells the owner once per approach, for the real book
  (`unalerted_approaches`, plan step 4.4). The bars the ATR needs come from
  the same read as the bars the stop is tested against.

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
from typing import Any, Dict, Iterator, List, Optional, Tuple

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

# What `check_stop` says became of a stop.
TRIGGERED = "triggered"   # a bar reached it
CHECKED = "checked"       # tested against new bars, and it held
WAITING = "waiting"       # no new closed bar to test it against
SKIPPED = "skipped"       # no longer active, or its holding is gone
CHECK_OUTCOMES = (TRIGGERED, CHECKED, WAITING, SKIPPED)

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


def _opened_on(lot: Dict[str, Any]) -> Optional[datetime.date]:
    """The date a lot was opened, or None when it has none on record. A lot's
    `trade_date` is a date and its `purchase_date` text, so both are read as
    the first ten characters of an ISO date."""
    text = str(lot.get("trade_date") or lot.get("purchase_date") or "")[:10]
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


class StopsService:
    def __init__(
        self,
        stops_repository: StopsRepository,
        books_repository: BooksRepository,
        prices,
        orders,
    ) -> None:
        self._repo = stops_repository
        self._books_repo = books_repository
        # PricesService.last_close: the newest stored closed daily bar. Zero
        # network, so setting a stop never waits on Yahoo. The checker reads
        # `closed_bars_since`, which fetches when a symbol's bars are behind.
        self._prices = prices
        # OrdersService.sell_for_stop: the sale a paper book's stop causes.
        # Required, so no service exists that can check a stop and not act on it.
        self._orders = orders

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

    def holding_stops(self, *, owner: str, book_id: Optional[int] = None) -> Dict[str, Any]:
        """The stop each of a book's holdings shows, one a symbol at most, in
        symbol order (`analytics.stored_stops.holding_stops`): its live stop,
        or one a bar reached while the holding was already open.

        What the Portfolio page draws. Two queries, no network call, and no
        book is created. Raises `BookNotFoundError` for a book that isn't
        the owner's.
        """
        book = self._book(owner, book_id)
        opened: Dict[str, List[datetime.date]] = {}
        for lot in self._repo.open_lots(owner=owner, book_id=book_id):
            day = _opened_on(lot)
            if day is not None:
                opened.setdefault(lot["symbol"], []).append(day)
        return {
            "book_kind": REAL if book is None else book["kind"],
            "stops": stops_math.holding_stops(
                self._repo.list_stops(owner=owner, book_id=book_id), opened
            ),
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
            # Read again: the status that refused the cancel may have been
            # written after the read above.
            current = self._repo.get_stop(owner=owner, stop_id=stop_id, book_id=book_id) or stop
            raise ValueError(
                f"only an active stop can be cancelled: this one is {current['status']}"
            )
        return self._repo.get_stop(owner=owner, stop_id=stop_id, book_id=book_id)

    # ------------------------------------------------------------------
    # The bar-close checker (the daily job, plan step 4.2)
    # ------------------------------------------------------------------
    def _live_books(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Every owner's books that are not archived, as `(owner, book)`.
        Walked owner by owner, so every statement after it is bound to one
        owner and one book."""
        for owner in self._books_repo.list_owners():
            for book in self._books_repo.list_books(owner=owner):
                if book["archived_at"] is None:
                    yield owner, book

    def close_flat_stops(self, *, now: Optional[datetime.datetime] = None) -> int:
        """Close every active stop whose book no longer holds its symbol, in
        every book that isn't archived. Returns how many were closed.

        A sale closes a flat symbol's stop in its own transaction. This is
        for the ways a holding can go that are not a sale (a deleted lot, an
        import that replaces the book), and for a stop set in the moment its
        last lot was sold. Zero network calls.
        """
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        return sum(
            self._repo.close_flat_stops(owner=owner, resolved_at=now, book_id=book["book_id"])
            for owner, book in self._live_books()
        )

    def due_stops(self, *, today: datetime.date) -> List[Dict[str, Any]]:
        """Every owner's active stops that have not been tested against the
        bar of the market date `today`: what the daily job has to check, and
        what its alarm counts.

        Each is `owner`, `book_id`, `stop_id`, `symbol` and
        `last_bar_checked`. A stop whose first session is still to come is
        not due, and nor is one in an archived book. Zero network calls.
        """
        due: List[Dict[str, Any]] = []
        for owner, book in self._live_books():
            for stop in self._repo.list_stops(owner=owner, book_id=book["book_id"]):
                if stop["status"] != stops_math.ACTIVE:
                    continue
                if not stops_math.is_due(
                    stop["created_at"], stop["last_bar_checked"], today,
                    live_from=stop["first_session"],
                ):
                    continue
                due.append({
                    "owner": owner,
                    "book_id": stop["book_id"],
                    "stop_id": stop["stop_id"],
                    "symbol": stop["symbol"],
                    "last_bar_checked": stop["last_bar_checked"],
                })
        return sorted(due, key=lambda s: (s["symbol"], s["stop_id"]))

    def check_stop(
        self,
        *,
        owner: str,
        stop_id: int,
        book_id: Optional[int] = None,
        today: Optional[datetime.date] = None,
        now: Optional[datetime.datetime] = None,
        approach_atr_multiple: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Test one stop against the closed daily bars it has not seen, and
        return it as it now is, with `outcome` (one of `CHECK_OUTCOMES`).

        - No new closed bar: `waiting`, and nothing is written.
        - The bars held: the newest one is recorded, with a trailing stop's
          raised mark and level, and whether it closed within
          `approach_atr_multiple` ATRs of the level (the default when None).
          `checked`.
        - A bar's low reached the level: `triggered`. A real book's stop is
          marked and nothing is sold. A paper book's position is sold at the
          trigger price and the stop marked in the same transaction.
        - A stop that is not active, or stopped being while this ran:
          `skipped`. Running the job twice triggers nothing twice.

        Raises `StopNotFoundError` for a stop that isn't in this owner's book
        and `BookNotFoundError` for a book that isn't the owner's. An error
        from the price fetch propagates, and the stop is left as it was.
        """
        book = self._book(owner, book_id)
        stop = self._repo.get_stop(owner=owner, stop_id=stop_id, book_id=book_id)
        if stop is None:
            raise StopNotFoundError(f"stop {stop_id} not found")
        if stop["status"] != stops_math.ACTIVE:
            return {**stop, "outcome": SKIPPED}
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        today = today or market_time.market_date(now)

        # The bars are read, and fetched if they have to be, before any lock.
        history, new_bars = self._bars(stop, today)
        check = stops_math.check_stop(
            kind=stop["kind"], stop_level=stop["stop_level"], trail_pct=stop["trail_pct"],
            high_water_mark=stop["high_water_mark"], bars=new_bars,
        )
        if check.last_bar is None:
            return {**stop, "outcome": WAITING}
        if check.trigger is None:
            done, outcome = self._repo.record_check(
                owner=owner, stop_id=stop_id, last_bar_checked=check.last_bar,
                stop_level=check.stop_level, high_water_mark=check.high_water_mark,
                book_id=book_id, **self._approach(history, check, approach_atr_multiple),
            ), CHECKED
        else:
            paper = book is not None and book["kind"] != REAL
            done, outcome = self._trigger(stop, check, owner, book_id, now, paper=paper), TRIGGERED
        current = self._repo.get_stop(owner=owner, stop_id=stop_id, book_id=book_id)
        return {**current, "outcome": outcome if done else SKIPPED}

    def _bars(
        self, stop: Dict[str, Any], today: datetime.date
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """`stop`'s closed daily bars through `today`, oldest first, as
        `(history, new)`: `new` are the ones it has not been tested against,
        and `history` reaches back far enough for the approach's ATR too. One
        read. No read at all when no new bar can exist yet."""
        since = stops_math.next_bar_date(
            stop["created_at"], stop["last_bar_checked"], live_from=stop["first_session"]
        )
        if since > today:
            return [], []
        start = min(since, today - datetime.timedelta(days=stops_math.APPROACH_LOOKBACK_DAYS))
        history = [
            bar for bar in self._prices.closed_bars_since(stop["symbol"], start)
            if bar["session"] <= today
        ]
        return history, [bar for bar in history if bar["session"] >= since]

    @staticmethod
    def _approach(
        history: List[Dict[str, Any]],
        check: stops_math.StopCheck,
        multiple: Optional[Decimal],
    ) -> Dict[str, Any]:
        """The approach columns `record_check` takes for a stop that held:
        unknown (left as they were) when the ATR can't be measured, else the
        newest bar's session, close and ATR when it closed near the level,
        and all None when it didn't."""
        found = stops_math.approach(
            bars=history, last_bar=check.last_bar, stop_level=check.stop_level,
            multiple=multiple if multiple is not None
            else stops_math.DEFAULT_APPROACH_ATR_MULTIPLE,
        )
        if found is None:
            return {"approach_known": False}
        if not found.near:
            return {"approach_known": True}
        return {"approach_known": True, "approached_session": found.session,
                "approach_close": found.close, "approach_atr": found.atr}

    def _trigger(
        self, stop: Dict[str, Any], check: stops_math.StopCheck, owner: str,
        book_id: Optional[int], now: datetime.datetime, *, paper: bool,
    ) -> bool:
        """Carry out a stop that a bar reached: sell the position in a paper
        book, mark the stop in a real one. False when the stop was no longer
        active, or (a paper book) nothing was left to sell."""
        trigger = check.trigger
        if paper:
            return self._orders.sell_for_stop(
                owner=owner, book_id=book_id, stop_id=stop["stop_id"], symbol=stop["symbol"],
                session=trigger.session, price=trigger.price,
                stop_level=check.stop_level, high_water_mark=check.high_water_mark, now=now,
            ) is not None
        return self._repo.trigger_stop(
            owner=owner, stop_id=stop["stop_id"], stop_level=check.stop_level,
            high_water_mark=check.high_water_mark, triggered_session=trigger.session,
            trigger_price=trigger.price, resolved_at=now, book_id=book_id,
        )

    # ------------------------------------------------------------------
    # The breach alert for a real book (plan step 4.2)
    # ------------------------------------------------------------------
    def unalerted_triggers(self, *, owner: str) -> List[Dict[str, Any]]:
        """The triggered stops of `owner`'s real book whose breach alert has
        not gone out, oldest trigger first. A paper book's triggered stop is
        not here: its sale is reported with the day's paper fills. One query,
        zero network calls, and no book is created."""
        owed = [
            stop for stop in self._repo.list_stops(owner=owner)
            if stop["status"] == stops_math.TRIGGERED and stop["alerted_at"] is None
        ]
        return sorted(owed, key=lambda s: (s["triggered_session"], s["stop_id"]))

    def mark_alerted(
        self, *, owner: str, stop_ids: List[int], now: Optional[datetime.datetime] = None
    ) -> int:
        """Record that the breach alert went out for these stops of `owner`'s
        real book. Returns how many were marked: one already marked, or not a
        triggered stop of that book, is not counted."""
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        return sum(
            1 for stop_id in stop_ids
            if self._repo.mark_alerted(owner=owner, stop_id=stop_id, alerted_at=now)
        )

    # ------------------------------------------------------------------
    # The approach alert for a real book (plan step 4.4)
    # ------------------------------------------------------------------
    def unalerted_approaches(self, *, owner: str) -> List[Dict[str, Any]]:
        """The active stops of `owner`'s real book whose newest checked bar
        closed near the level and whose approach alert has not gone out,
        nearest first, each with `atr_distance` (how many ATRs above its level
        that close was). A paper book's stop is not here: it sells by itself.
        One query, zero network calls, and no book is created."""
        owed = [
            {**stop, "atr_distance": stops_math.atr_distance(
                stop["approach_close"], stop["stop_level"], stop["approach_atr"])}
            for stop in self._repo.list_stops(owner=owner)
            if stop["status"] == stops_math.ACTIVE
            and stop["approached_session"] is not None
            and stop["approach_alerted_at"] is None
        ]
        return sorted(owed, key=lambda s: (s["atr_distance"], s["symbol"], s["stop_id"]))

    def mark_approach_alerted(
        self, *, owner: str, stop_ids: List[int], now: Optional[datetime.datetime] = None
    ) -> int:
        """Record that the approach alert went out for these stops of
        `owner`'s real book. Returns how many were marked: one already
        marked since it came near, or not near, is not counted."""
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        return sum(
            1 for stop_id in stop_ids
            if self._repo.mark_approach_alerted(owner=owner, stop_id=stop_id, alerted_at=now)
        )
