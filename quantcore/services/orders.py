"""OrdersService — paper orders and their fills (#431 step 3.3).

An order is a request to buy or sell in one of the owner's paper books. The
system sets every fill; the owner never types a price.

- Placed while the market is open, an order fills at the current quote or is
  rejected, in the transaction that creates it. It is never left pending.
- Placed outside the session, it is queued for the next one. The daily job
  fills it at that session's opening price (`queued_orders` and
  `fill_queued_order`, plan step 3.4): the open of the session's closed daily
  bar, on whatever night the job gets to it.
- A fill is all or nothing, and happens exactly once: the order leaves
  `pending` through one `UPDATE ... WHERE status = 'pending'`, in the same
  transaction as its lot or sale rows and their cash rows.
- Placing the same `idempotency_key` twice returns the first order.
- **A seed is an order that costs no cash** (`seed_book`, plan step 3.5): one
  per symbol the real book holds, priced, queued, filled and rejected by the
  rules above. Only a paper book that has never had an order can be seeded.
- **A position whose symbol can't be quoted is closed by hand**
  (`close_unquoted`), in one filled order at the last good price on record.
  The owner still types no price, and a symbol that has a quote is refused.
- **A stop that a closed bar reached sells the whole position** (`sell_for_stop`,
  plan step 4.2), at the price the checker worked out: the stop's level, or
  the session's open when the stock opened under it. The stop is marked
  `triggered` in the sale's transaction, so neither exists without the other.
- **A buy can ask for a stop** (`place_order`'s `stop_*` arguments, plan step
  4.3). The stop row is written when the buy fills, in the fill's
  transaction, against the fill price. A queued buy has no stop until it
  fills, and a rejected or cancelled one never gets one. The stop of a buy
  that filled at its session's open is live from that session's own bar,
  as a broker's attached stop is live once the entry fills. A fixed level at or
  above the fill price can't be a stop: in market hours the order is refused
  as bad input before anything is stored, and a queued buy that opens at or
  under its level fills with no stop and says so (`stop_set` False).

Rules kept here (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument on every method.
- `book_id` is keyword-only and defaults to None, the real book. **An order
  for the real book is refused**: a real trade is recorded with Add Lot, at
  the price and date the broker gave. A named book is checked first, and one
  that isn't the owner's raises `BookNotFoundError`.
- **A paper book has no margin.** A buy the book's cash can't cover is
  rejected. The check reads the ledger inside the fill's transaction, with
  the buy's own cash row written, so it can't disagree with what the ledger
  then shows.
- **Cash and shares are checked when the order fills.** What is checked when
  a queued order is placed is a courtesy.
- **The quote is fetched before the transaction opens**, so no connection and
  no book lock waits on the network. So is a queued order's opening price,
  and the order is read again once the lock is held.

What to do with an order is `analytics.orders`. The rows are
`OrdersRepository`'s.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from quantcore.analytics import cash as cash_math
from quantcore.analytics import market_time
from quantcore.analytics import orders as orders_math
from quantcore.analytics import portfolio_math
from quantcore.analytics import stored_stops as stops_math
from quantcore.repositories.books_repository import (  # noqa: F401  (BookNotFoundError re-exported for routes)
    REAL,
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.orders_repository import OrdersRepository, OrderTransaction

# The share columns are NUMERIC(18,6): twelve digits before the point, so the
# largest count they hold is just under 1e12. A larger one would overflow the
# column and surface as a database error rather than as bad input. The cap is
# the largest whole number that fits. `orders.requested_amount` is
# NUMERIC(18,2) and has room to spare.
MAX_ORDER_AMOUNT = Decimal("1000000000000")
MAX_ORDER_SHARES = Decimal("999999999999")
MAX_KEY_LENGTH = 100
MAX_SOURCE_REF_LENGTH = 200

# What a manual close writes on its sale rows, with the date the price is from.
MANUAL_CLOSE_NOTE = "Closed by hand at the last good price, from {as_of}: no quote"

# What a stop's sale writes on its sale rows.
STOP_SALE_NOTE = "Sold by its stop at {level}"
STOP_GAP_SALE_NOTE = "Sold by its stop at {level}: opened below it, so filled at the open"

# A paper sale takes the oldest lots first. The owner picks no lots: the
# system sets every part of a paper fill.
SELL_METHOD = "FIFO"

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_SHARE_STEP = Decimal("0.000001")
_ZERO = Decimal("0")


class OrderNotFoundError(ValueError):
    """An order id that isn't in this owner's book. A `ValueError`, so a
    caller that treats bad input alike still does; a route answers 404."""


def _number(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{name} is not a valid number: {value!r}") from None
    # NaN and Infinity pass every comparison, and NUMERIC stores NaN.
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return number


def _shares(value: Any) -> Decimal:
    """A share count rounded once, to the six decimals the database stores."""
    shares = _number(value, "shares").quantize(_SHARE_STEP)
    if shares <= _ZERO:
        raise ValueError("shares must be more than zero")
    if shares > MAX_ORDER_SHARES:
        raise ValueError("shares is too large")
    return shares


def _amount(value: Any) -> Decimal:
    amount = cash_math.to_cents(_number(value, "amount"))
    if amount <= _ZERO:
        raise ValueError("amount must be more than zero")
    if amount > MAX_ORDER_AMOUNT:
        raise ValueError("amount is too large")
    return amount


def _symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not _SYMBOL.match(symbol):
        raise ValueError("symbol is not a valid ticker")
    return symbol


def _key(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError("an order needs an idempotency key")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"an idempotency key can be at most {MAX_KEY_LENGTH} characters")
    return key


def _source_ref(value: Any) -> Optional[str]:
    ref = (str(value).strip() if value is not None else "") or None
    if ref is not None and len(ref) > MAX_SOURCE_REF_LENGTH:
        raise ValueError(f"source_ref can be at most {MAX_SOURCE_REF_LENGTH} characters")
    return ref


def _request(side: Any, shares: Any, amount: Any) -> Dict[str, Any]:
    """The validated side and size of an order: shares, or (a buy only) a
    dollar amount, never both."""
    if side not in orders_math.SIDES:
        raise ValueError("side must be buy or sell")
    if (shares is None) == (amount is None):
        raise ValueError("an order is for a number of shares or a dollar amount, not both")
    if side == orders_math.SELL and shares is None:
        raise ValueError("a sell is for a number of shares")
    return {
        "side": side,
        "requested_shares": _shares(shares) if shares is not None else None,
        "requested_amount": _amount(amount) if amount is not None else None,
    }


_NO_STOP: Dict[str, Any] = {
    "stop_kind": None, "stop_level": None, "stop_trail_pct": None,
    "stop_method": None, "stop_proposed_level": None,
}


def _stop_request(
    side: str, kind: Any, level: Any, trail_pct: Any, method: Any, proposed_level: Any
) -> Dict[str, Any]:
    """The stop a buy asks for, as the order's five `stop_*` columns: all
    None when no `kind` is given. Raises `ValueError` for a stop on a sell,
    for stop terms sent with no kind, and for terms that are wrong."""
    if kind is None:
        if any(v is not None for v in (level, trail_pct, method, proposed_level)):
            raise ValueError("a stop needs a kind: fixed or trailing")
        return dict(_NO_STOP)
    if side != orders_math.BUY:
        raise ValueError("only a buy can carry a stop")
    asked = stops_math.requested_stop(
        kind,
        stop_level=None if level is None else _number(level, "stop_level"),
        trail_pct=None if trail_pct is None else _number(trail_pct, "trail_pct"),
        method=stops_math.MANUAL if method is None else method,
        proposed_level=(
            None if proposed_level is None else _number(proposed_level, "proposed_level")
        ),
    )
    return {
        "stop_kind": asked.kind,
        "stop_level": asked.stop_level,
        "stop_trail_pct": asked.trail_pct,
        "stop_method": asked.method,
        "stop_proposed_level": asked.proposed_level,
    }


def _stop_terms(order: Dict[str, Any], price: Decimal) -> Optional[stops_math.StopTerms]:
    """The terms of the stop `order` asked for, were it to fill at `price`.
    None when it asked for none, or none can be set at that price."""
    if order["stop_kind"] is None:
        return None
    return stops_math.order_stop_terms(
        order["stop_kind"], stop_level=order["stop_level"],
        trail_pct=order["stop_trail_pct"], fill=price,
    )


def _no_stop_at(kind: str, price: Decimal) -> str:
    """Why a stop of `kind` can't be set on a buy that fills at `price`. A
    fixed stop gave a level that isn't under it. A trailing stop gave no
    level: its percentage is too small to reach the next price down."""
    if kind == stops_math.TRAILING:
        return (
            f"the trail is too small: it works out to no price below the one the order "
            f"fills at, which is {price}"
        )
    return f"the stop must be below the price the order fills at, which is {price}"


def _public(order: Dict[str, Any], *, replayed: bool = False) -> Dict[str, Any]:
    """The order as a caller sees it: everything but the idempotency key,
    plus whether this answer is a repeat of an earlier request."""
    shown = {key: value for key, value in order.items() if key != "idempotency_key"}
    return {**shown, "replayed": replayed}


def _held(lots: List[Dict[str, Any]]) -> Decimal:
    return sum((lot["quantity"] for lot in lots), _ZERO)


def _quote_facts(quote) -> Dict[str, Any]:
    """A live quote, or None, as the facts `analytics.orders` asks for."""
    if quote is None:
        return {
            "has_quote": False, "currency": None, "market_state": None,
            "regular_price": None, "regular_time": None,
        }
    return {
        "has_quote": True,
        "currency": quote.currency,
        "market_state": quote.market_state,
        "regular_price": quote.regular_price,
        "regular_time": quote.regular_time,
    }


def _opened_by(lots: List[Dict[str, Any]], session: datetime.date) -> List[Dict[str, Any]]:
    """The lots opened on or before `session`. A lot's `trade_date` is a date
    and its `purchase_date` text, so both are compared as ISO dates."""
    through = session.isoformat()
    return [
        lot for lot in lots
        if str(lot["trade_date"] or lot["purchase_date"] or "")[:10] <= through
    ]


def _stop_sale_terms(price: Decimal, stop_level: Decimal):
    """A stop sale's `price_source` and the note on its sale rows. A price
    under the level means the stock opened below it and filled at the open."""
    if price < stop_level:
        return orders_math.PRICE_FROM_SESSION_OPEN, STOP_GAP_SALE_NOTE.format(level=stop_level)
    return None, STOP_SALE_NOTE.format(level=stop_level)


def _tally(orders: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many of `orders` are in each of the three states a new order can be in."""
    return {
        status: sum(1 for order in orders if order["status"] == status)
        for status in (orders_math.FILLED, orders_math.PENDING, orders_math.REJECTED)
    }


class OrdersService:
    def __init__(
        self,
        orders_repository: OrdersRepository,
        books_repository: BooksRepository,
        yfinance_gateway,
        prices,
    ) -> None:
        self._repo = orders_repository
        self._books_repo = books_repository
        # PricesService.session_open: the opening price a queued order fills
        # at. Only the daily job's fill uses it, but it is required, so no
        # service exists that can place a queued order and not fill one.
        self._prices = prices
        # YFinanceGateway.batch_quotes: one live quote for the order's symbol,
        # asked for at the moment the order is placed. Never the price cache,
        # whose entries can be minutes old.
        self._gateway = yfinance_gateway

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

    def _paper_book(self, owner: str, book_id: Optional[int]) -> Dict[str, Any]:
        """The paper book an order is for. Raises `ValueError` for the real
        book and for an archived one."""
        book = self._book(owner, book_id)
        if book is None or book["kind"] == REAL:
            raise ValueError(
                "orders are for paper books: record a real trade with Add Lot, at the "
                "price and date the broker gave"
            )
        if book["archived_at"] is not None:
            raise ValueError("an archived book takes no orders")
        return book

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_orders(self, *, owner: str, book_id: Optional[int] = None) -> Dict[str, Any]:
        """One book's orders, newest first, with how many are still queued.

        The real book has none and is not looked up, so the read creates no
        book. One query, no network call.
        """
        book = self._book(owner, book_id)
        orders = [] if book is None else self._repo.list_orders(owner=owner, book_id=book_id)
        return {
            "book_kind": REAL if book is None else book["kind"],
            "pending_count": sum(1 for o in orders if o["status"] == orders_math.PENDING),
            "orders": [_public(o) for o in orders],
        }

    # ------------------------------------------------------------------
    # Placing
    # ------------------------------------------------------------------
    def place_order(
        self,
        *,
        owner: str,
        symbol: Any,
        side: Any,
        idempotency_key: Any,
        shares: Any = None,
        amount: Any = None,
        source: str = orders_math.MANUAL,
        source_ref: Any = None,
        stop_kind: Any = None,
        stop_level: Any = None,
        stop_trail_pct: Any = None,
        stop_method: Any = None,
        stop_proposed_level: Any = None,
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Place a market order in a paper book and return it as it now is:
        `filled`, `rejected` (with `reject_reason`) or `pending`.

        A buy is for `shares` or for a dollar `amount`; a sell is for
        `shares`. `replayed` is True when `idempotency_key` had already been
        used: nothing new happens and the first order is returned.

        A buy may ask for a stop: `stop_kind` with a `stop_level` (fixed) or
        a `stop_trail_pct` (trailing, percent below a mark that starts at the
        fill price), and how the level was chosen (`stop_method`,
        `stop_proposed_level`). The stop is written when the buy fills, and
        `stop_set` on the order then says whether it was: False means the buy
        filled at or under the fixed level it asked for, so it has no stop.

        Raises `ValueError` for bad input (a dollar amount that would buy
        more shares than an order can hold included, and a fixed stop at or
        above the price an order placed in market hours fills at), the real
        book or an archived one, and `BookNotFoundError` for a book that isn't
        the owner's. A refusal
        that depends on the market or the book's holdings is not an error: it
        is a stored order with status `rejected`.
        """
        symbol = _symbol(symbol)
        request = _request(side, shares, amount)
        key = _key(idempotency_key)
        if source not in orders_math.SOURCES:
            raise ValueError(f"source must be one of {', '.join(orders_math.SOURCES)}")
        ref = _source_ref(source_ref)
        stop = _stop_request(
            request["side"], stop_kind, stop_level, stop_trail_pct, stop_method,
            stop_proposed_level,
        )
        self._paper_book(owner, book_id)
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)

        # A repeat is answered before the quote is asked for.
        earlier = self._repo.get_order_by_key(owner=owner, idempotency_key=key, book_id=book_id)
        if earlier is not None:
            return _public(earlier, replayed=True)

        plan = orders_math.placement(now=now, **self._quote_facts(symbol))
        # The price is already known for an order that fills now, so a stop
        # that can't sit under it is bad input: nothing is stored.
        if (
            plan.action == orders_math.FILL and stop["stop_kind"] is not None
            and _stop_terms(stop, plan.price) is None
        ):
            raise ValueError(_no_stop_at(stop["stop_kind"], plan.price))

        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            order, created = tx.insert({
                **request,
                **stop,
                "symbol": symbol,
                "source": source,
                "source_ref": ref,
                "idempotency_key": key,
                "session_date": plan.session_date,
                "placed_at": now,
            })
            if not created:
                return _public(order, replayed=True)
            self._act(tx, order, plan, now)
            return _public(tx.get(order["order_id"]))

    def _quote_facts(self, symbol: str) -> Dict[str, Any]:
        """One live quote for `symbol`, as the facts `placement` asks for."""
        return _quote_facts(self._gateway.batch_quotes([symbol]).get(symbol))

    # ------------------------------------------------------------------
    # Seeding (plan step 3.5)
    # ------------------------------------------------------------------
    def seed_book(
        self, *, owner: str, book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Copy the real book's open holdings into a new paper book, on top of
        its starting cash, and return what became of each.

        One `seed` order per symbol the real book holds, for the shares held.
        Each is priced by the rule a buy is: the live quote in market hours,
        the next session's opening price otherwise (it queues, and the daily
        job fills it). A seed costs no cash. A holding that can't be priced or
        isn't in US dollars is a rejected order with its reason, and a symbol
        an order can't name is listed under `skipped`.

        The whole seed is one transaction. Raises `ValueError` for the real
        book, an archived book, a book that has ever had an order, and a real
        book with nothing to copy; `BookNotFoundError` for a book that isn't
        the owner's.
        """
        self._paper_book(owner, book_id)
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        # The real book: no book is named, which is what `None` reads.
        holdings = orders_math.seed_quantities(self._repo.open_lots(owner=owner))
        wanted = {
            symbol: shares for symbol, shares in holdings.items()
            if _SYMBOL.match(symbol) and shares <= MAX_ORDER_SHARES
        }
        if not wanted:
            raise ValueError("the real book holds nothing to copy")
        # Every quote is asked for before the book is locked.
        quotes = self._gateway.batch_quotes(list(wanted))

        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            if tx.order_count() > 0:
                raise ValueError("only a book that has had no orders can be seeded")
            placed = []
            for symbol, shares in wanted.items():
                plan = orders_math.placement(now=now, **_quote_facts(quotes.get(symbol)))
                order, _ = tx.insert({
                    "kind": orders_math.SEED,
                    "side": orders_math.BUY,
                    "requested_shares": shares,
                    "requested_amount": None,
                    "symbol": symbol,
                    "source": orders_math.MANUAL,
                    "source_ref": None,
                    "idempotency_key": f"seed:{book_id}:{symbol}",
                    "session_date": plan.session_date,
                    "placed_at": now,
                })
                self._act(tx, order, plan, now)
                placed.append(_public(tx.get(order["order_id"])))
        return {
            "book_id": book_id,
            **_tally(placed),
            "skipped": sorted(set(holdings) - set(wanted)),
            "orders": placed,
        }

    # ------------------------------------------------------------------
    # Closing a position that can't be quoted (plan step 3.5)
    # ------------------------------------------------------------------
    def close_unquoted(
        self,
        *,
        owner: str,
        symbol: Any,
        idempotency_key: Any,
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Sell a paper book's whole position in a symbol that can no longer
        be quoted, at the last good price on record, and return the order.

        The order is a `manual_close`, filled as it is made. Its price is the
        close of the symbol's newest stored daily bar, or failing that the
        last trade its quote still carries; `quote_time` says when that price
        is from, and every sale row carries a note saying so. The owner types
        no price. `replayed` is True when `idempotency_key` had been used.

        Raises `ValueError` for bad input, the real book or an archived one, a
        symbol that still has a quote (sell it with an order), a symbol the
        book doesn't hold, one with no price on record, and one with a sell
        still queued (cancel it first);
        `BookNotFoundError` for a book that isn't the owner's.
        """
        symbol = _symbol(symbol)
        key = _key(idempotency_key)
        self._paper_book(owner, book_id)
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        today = market_time.market_date(now)

        earlier = self._repo.get_order_by_key(owner=owner, idempotency_key=key, book_id=book_id)
        if earlier is not None:
            return _public(earlier, replayed=True)

        # The quote and the stored bar are read before the book is locked.
        quote = _quote_facts(self._gateway.batch_quotes([symbol]).get(symbol))
        if not orders_math.unquotable(
            today=today, has_quote=quote["has_quote"], currency=quote["currency"],
            regular_price=quote["regular_price"], regular_time=quote["regular_time"],
        ):
            raise ValueError(f"{symbol} has a quote: sell it with an order")
        bar = self._prices.last_close(symbol)
        last = orders_math.last_good_price(
            bar_close=bar["close"], bar_time=bar["as_of"],
            quote_price=quote["regular_price"], quote_time=quote["regular_time"],
        )
        if last is None:
            raise ValueError(f"there is no price on record for {symbol}")

        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            shares = _held(tx.open_lots(symbol))
            if shares <= _ZERO:
                raise ValueError(f"this book holds no {symbol}")
            order, created = tx.insert({
                "kind": orders_math.MANUAL_CLOSE,
                "side": orders_math.SELL,
                "requested_shares": shares,
                "requested_amount": None,
                "symbol": symbol,
                "source": orders_math.MANUAL,
                "source_ref": None,
                "idempotency_key": key,
                "session_date": today,
                "placed_at": now,
            })
            if not created:
                return _public(order, replayed=True)
            # A queued sell has been promised some of these shares. Closing
            # them all would leave it to be rejected at its session, with
            # nothing saying why. Raising here undoes the insert above.
            if tx.queued_sell_shares(symbol, excluding_order_id=order["order_id"]) > _ZERO:
                raise ValueError(
                    f"a sell of {symbol} is waiting for its session: cancel it before "
                    "closing the position"
                )
            self._fill(
                tx, order, price=last.price, price_source=None, quote_time=last.as_of, now=now,
                sale_notes=MANUAL_CLOSE_NOTE.format(as_of=last.session.isoformat()),
            )
            return _public(tx.get(order["order_id"]))

    # ------------------------------------------------------------------
    # What filled (the daily job's Discord message, plan step 3.5)
    # ------------------------------------------------------------------
    def fills_on(self, *, owner: str, day: datetime.date) -> List[Dict[str, Any]]:
        """Every order filled in one of `owner`'s paper books on the market
        date `day`, in the order they filled, each with its `book_name`.

        Archived books are included: a queued order can fill in one. One
        query a book, zero network calls.
        """
        start = market_time.ET.localize(datetime.datetime.combine(day, datetime.time.min))
        end = market_time.ET.localize(
            datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min)
        )
        fills: List[Dict[str, Any]] = []
        for book in self._books_repo.list_books(owner=owner):
            if book["kind"] == REAL:
                continue
            fills.extend(
                {**_public(order), "book_name": book["name"]}
                for order in self._repo.list_filled_orders(
                    owner=owner, start=start, end=end, book_id=book["book_id"]
                )
            )
        return sorted(fills, key=lambda o: (o["resolved_at"], o["order_id"]))

    def _act(
        self, tx: OrderTransaction, order: Dict[str, Any], plan: orders_math.Placement,
        now: datetime.datetime,
    ) -> None:
        """Carry out a new order's placement on its transaction."""
        reason = plan.reason or self._placement_refusal(tx, order)
        if reason is not None:
            self._reject(tx, order, reason, now)
        elif plan.action == orders_math.FILL:
            self._fill(
                tx, order, price=plan.price, price_source=orders_math.PRICE_FROM_QUOTE,
                quote_time=plan.quote_time, now=now,
            )
        # QUEUE: the order stays pending for `session_date`.

    def _placement_refusal(self, tx: OrderTransaction, order: Dict[str, Any]) -> Optional[str]:
        """What can already be seen to be wrong when an order is placed.

        A sell for more than the shares held less those promised to queued
        sells, and a dollar buy for more than the book's cash. A buy in
        shares is not checked here: its cost isn't known until it fills.
        """
        if order["side"] == orders_math.SELL:
            free = orders_math.sellable_shares(
                _held(tx.open_lots(order["symbol"])),
                tx.queued_sell_shares(order["symbol"], excluding_order_id=order["order_id"]),
            )
            return orders_math.INSUFFICIENT_SHARES if order["requested_shares"] > free else None
        if order["requested_amount"] is not None:
            balance = cash_math.cash_ledger(tx.cash_entries())["balance"]
            if balance is None or order["requested_amount"] > balance:
                return orders_math.INSUFFICIENT_CASH
        return None

    # ------------------------------------------------------------------
    # Filling
    # ------------------------------------------------------------------
    def _reject(
        self, tx: OrderTransaction, order: Dict[str, Any], reason: str, now: datetime.datetime
    ) -> None:
        tx.resolve(order["order_id"], orders_math.REJECTED, resolved_at=now, reject_reason=reason)

    def _fill(
        self,
        tx: OrderTransaction,
        order: Dict[str, Any],
        *,
        price: Decimal,
        price_source: Optional[str],
        quote_time: Optional[datetime.datetime],
        now: datetime.datetime,
        sale_notes: Optional[str] = None,
        lots: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Fill a pending order at `price` on its transaction, or reject it
        when the book can't cover it. The trade's date is the order's session.
        `price_source` is None for a manual close and for a stop's sale at its
        level, which are also the only fills that write `sale_notes` on their
        sale rows. `lots` limits a sell to those lots (a stop's sale); without
        it a sell draws on every OPEN lot of the symbol.

        The trade's rows are written first and the order is marked last, so
        the cash check reads the ledger as it will be. A trade the book can't
        cover is undone to the savepoint and the order is rejected instead.
        A buy that asked for a stop gets it here, once the buy is known to
        stand, so the lot and its stop land together or not at all.
        """
        tx.savepoint()
        if order["side"] == orders_math.BUY:
            shares, reason = self._buy(tx, order, price)
        else:
            shares, reason = self._sell(tx, order, price, sale_notes, lots)
        if reason is not None:
            tx.undo_trade()
            self._reject(tx, order, reason, now)
            return
        resolved = tx.resolve(
            order["order_id"], orders_math.FILLED, resolved_at=now, filled_shares=shares,
            fill_price=price, price_source=price_source, quote_time=quote_time,
            stop_set=self._attach_stop(tx, order, price, price_source, now),
        )
        if not resolved:
            # The trade's rows are already written. Raising rolls them back
            # with the transaction: an order that wasn't pending fills nothing.
            raise RuntimeError("order is no longer pending")

    def _attach_stop(
        self, tx: OrderTransaction, order: Dict[str, Any], price: Decimal,
        price_source: Optional[str], now: datetime.datetime,
    ) -> Optional[bool]:
        """Write the stop a filled buy asked for (plan step 4.3). None when
        it asked for none, True when the stop was written, and False when no
        stop can be set at this fill price: a queued buy that opened at or
        under its fixed level. The buy still stands, with no stop.

        The stop is set against the fill: `price` is its reference and a
        trailing stop's first high-water mark, and `now` is when it was set.
        It replaces the stop the symbol already has in this book, as any new
        stop does.

        **A buy that filled at its session's open has a stop that is live
        from that session** (plan decision 63): the whole of that day's bar
        came after the fill, so the checker tests it, on whatever night the
        fill was recorded. A buy filled at a live quote keeps the general
        rule, the next session to open: a daily bar can't say whether its
        low came before the fill or after it.
        """
        if order["stop_kind"] is None:
            return None
        terms = _stop_terms(order, price)
        if terms is None:
            return False
        tx.set_stop({
            "symbol": order["symbol"],
            "kind": terms.kind,
            "stop_level": terms.stop_level,
            "trail_pct": terms.trail_pct,
            "high_water_mark": terms.high_water_mark,
            "method": order["stop_method"],
            "proposed_level": order["stop_proposed_level"],
            "reference_price": price,
            "as_of": order["session_date"],
            "source": stops_math.MANUAL,
            "source_ref": f"order:{order['order_id']}",
            "created_at": now,
            "first_session": (
                order["session_date"]
                if price_source == orders_math.PRICE_FROM_SESSION_OPEN else None
            ),
        })
        return True

    def _buy(self, tx: OrderTransaction, order: Dict[str, Any], price: Decimal):
        """Write a buy's lot. Returns `(shares, None)`, or `(None, reason)`
        when the book's cash doesn't cover it."""
        shares = orders_math.buy_shares(order["requested_shares"], order["requested_amount"], price)
        if shares <= _ZERO:
            # A dollar amount too small to buy a millionth of a share.
            return None, orders_math.INSUFFICIENT_CASH
        if shares > MAX_ORDER_SHARES:
            # A large dollar amount at a sub-dollar price. Bad input, like a
            # share count over the cap: nothing is stored, the order included.
            raise ValueError("amount buys more shares than an order can hold")
        tx.buy({
            "symbol": order["symbol"],
            "name": order["symbol"],
            "purchase_price": price,
            "quantity": shares,
            "purchase_date": order["session_date"],
            "trade_date": order["session_date"],
            "currency": "USD",
            # A seeded lot is not a BUY, so it writes no cash row.
            "acquisition_type": orders_math.lot_acquisition_type(order["kind"]),
            "order_id": order["order_id"],
        })
        if not orders_math.moves_cash(order["kind"]):
            return shares, None
        balance = cash_math.cash_ledger(tx.cash_entries())["balance"]
        if not orders_math.can_afford(balance):
            return None, orders_math.INSUFFICIENT_CASH
        return shares, None

    def _sell(
        self, tx: OrderTransaction, order: Dict[str, Any], price: Decimal,
        notes: Optional[str] = None,
        lots: Optional[List[Dict[str, Any]]] = None,
    ):
        """Write a sell's sale rows, oldest lots first. Returns
        `(shares, None)`, or `(None, reason)` when the book doesn't hold them."""
        shares = order["requested_shares"]
        if lots is None:
            lots = tx.open_lots(order["symbol"])
        try:
            allocation = portfolio_math.allocate(lots, shares, SELL_METHOD)
        except portfolio_math.LotAllocationError:
            return None, orders_math.INSUFFICIENT_SHARES
        tx.sell(
            allocation, price, order["session_date"], allocation_method=SELL_METHOD,
            source=order["source"], source_ref=order["source_ref"], order_id=order["order_id"],
            notes=notes,
        )
        return shares, None

    # ------------------------------------------------------------------
    # A stop's sale (the bar-close checker, plan step 4.2)
    # ------------------------------------------------------------------
    def sell_for_stop(
        self,
        *,
        owner: str,
        stop_id: int,
        symbol: str,
        session: datetime.date,
        price: Decimal,
        stop_level: Decimal,
        high_water_mark: Optional[Decimal],
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Sell a paper book's whole position in `symbol` because a closed
        bar reached its stop, and mark the stop `triggered`, in one
        transaction. Returns the filled order, or None when nothing was sold.

        `session` is the bar that reached the stop and `price` what it was
        reached at (`analytics.stored_stops.trigger_for`): the sale is dated
        that session and fills at that price. `stop_level` and
        `high_water_mark` are the terms the stop was reached with.

        Only lots opened on or before `session` are sold. A lot bought after
        it (a late run that fills a later session's queued buy first) was
        never under this stop, and stays. With no such lot the stop is
        marked `closed` and nothing is sold. A stop that is no longer active
        once the book is locked changes nothing.

        The book may be archived by the time this runs: the checker walks
        only books that are not, and a stop reached before the book was
        archived is still carried out. Raises `ValueError` for the real book
        and `BookNotFoundError` for a book that isn't the owner's.
        """
        book = self._book(owner, book_id)
        if book is None or book["kind"] == REAL:
            raise ValueError("a stop sells only in a paper book: a real book's stop is an alert")
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)

        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            lots = _opened_by(tx.open_lots(symbol), session)
            if not lots:
                tx.close_stop(stop_id, resolved_at=now)
                return None
            # The stop first: the sale below leaves the book flat, and a flat
            # symbol's active stop is closed by the sale's own transaction.
            if not tx.trigger_stop(
                stop_id, stop_level=stop_level, high_water_mark=high_water_mark,
                triggered_session=session, trigger_price=price, resolved_at=now,
            ):
                return None
            order, created = tx.insert({
                "kind": orders_math.MARKET,
                "side": orders_math.SELL,
                "requested_shares": _held(lots),
                "requested_amount": None,
                "symbol": symbol,
                "source": orders_math.STOP,
                "source_ref": f"stop:{stop_id}",
                "idempotency_key": f"stop:{stop_id}",
                "session_date": session,
                "placed_at": now,
            })
            if not created:
                raise RuntimeError("this stop already has an order")
            price_source, note = _stop_sale_terms(price, stop_level)
            self._fill(
                tx, order, price=price, price_source=price_source, quote_time=None, now=now,
                sale_notes=note, lots=lots,
            )
            sale = tx.get(order["order_id"])
            if sale["status"] != orders_math.FILLED:
                # Raising undoes the stop's change with the rest: a stop is
                # never triggered in a paper book without its sale.
                raise RuntimeError("the stop's sale did not fill")
            return _public(sale)

    # ------------------------------------------------------------------
    # Queued fills (the daily job, plan step 3.4)
    # ------------------------------------------------------------------
    def queued_orders(self, *, today: datetime.date) -> List[Dict[str, Any]]:
        """Every owner's pending orders queued for a session on or before
        `today`: what the daily job has to fill, and what its alarm counts.

        Each is `owner`, `book_id`, `order_id`, `symbol` and `session_date`.
        Walked owner by owner and book by book, so every statement is bound
        to one owner and one book. An archived book is included: an order
        accepted before the book was archived is still carried out, or it
        would stay pending for good. Zero network calls.
        """
        due: List[Dict[str, Any]] = []
        for owner in self._books_repo.list_owners():
            for book in self._books_repo.list_books(owner=owner):
                if book["kind"] == REAL:
                    continue
                due.extend(
                    {
                        "owner": owner,
                        "book_id": order["book_id"],
                        "order_id": order["order_id"],
                        "symbol": order["symbol"],
                        "session_date": order["session_date"],
                    }
                    for order in self._repo.list_due_orders(
                        owner=owner, through=today, book_id=book["book_id"]
                    )
                )
        return sorted(due, key=lambda o: (o["session_date"], o["order_id"]))

    def _order_in_book(self, owner: str, order_id: int, book_id: Optional[int]) -> Dict[str, Any]:
        """One order of a named book. Raises `OrderNotFoundError` when no book
        is named (the real book holds no orders) or the order isn't in it,
        and `BookNotFoundError` for a book that isn't `owner`'s."""
        if self._book(owner, book_id) is None:
            raise OrderNotFoundError(f"order {order_id} not found")
        order = self._repo.get_order(owner=owner, order_id=order_id, book_id=book_id)
        if order is None:
            raise OrderNotFoundError(f"order {order_id} not found")
        return order

    def fill_queued_order(
        self,
        *,
        owner: str,
        order_id: int,
        book_id: Optional[int] = None,
        today: Optional[datetime.date] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Carry out one queued order and return it as it now is: `filled` at
        its session's opening price, `rejected` (no opening price, or the
        book can't cover it), or still `pending` when the price hasn't
        arrived and may yet (`analytics.orders.queued_fill`).

        An order that is no longer pending is returned unchanged, so running
        the job twice fills nothing twice. Raises `OrderNotFoundError` for an
        order that isn't in this owner's book and `BookNotFoundError` for a
        book that isn't the owner's. An error from the price fetch propagates,
        and the order stays pending.
        """
        order = self._order_in_book(owner, order_id, book_id)
        if order["status"] != orders_math.PENDING:
            return _public(order)
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        session = datetime.date.fromisoformat(order["session_date"])
        today = today or market_time.market_date(now)
        if session > today:
            return _public(order)

        # The price is read, and fetched if it has to be, before the book is locked.
        bar = self._prices.session_open(order["symbol"], session)
        decision = orders_math.queued_fill(
            session=session, today=today, open_price=bar["open"], latest_bar=bar["latest_bar"],
        )
        if decision.action == orders_math.WAIT:
            return _public(order)

        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            # Read again under the lock: it may have been cancelled meanwhile.
            current = tx.get(order_id)
            if current["status"] != orders_math.PENDING:
                return _public(current)
            if decision.action == orders_math.REJECT:
                self._reject(tx, current, decision.reason, now)
            else:
                self._fill(
                    tx, current, price=decision.price,
                    price_source=orders_math.PRICE_FROM_SESSION_OPEN, quote_time=None, now=now,
                )
            return _public(tx.get(order_id))

    # ------------------------------------------------------------------
    # Cancelling
    # ------------------------------------------------------------------
    def cancel_order(
        self,
        *,
        owner: str,
        order_id: int,
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Withdraw a queued order and return it.

        Raises `OrderNotFoundError` for an order that isn't in this owner's
        book, and `ValueError` for one that is no longer pending: a fill is
        permanent, and a mistake is put right with an order the other way.
        """
        if self._book(owner, book_id) is None:
            raise OrderNotFoundError(f"order {order_id} not found")
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            order = tx.get(order_id)
            if order is None:
                raise OrderNotFoundError(f"order {order_id} not found")
            if not tx.resolve(order_id, orders_math.CANCELLED, resolved_at=now):
                raise ValueError(
                    f"only a pending order can be cancelled: this one is {order['status']}"
                )
            return _public(tx.get(order_id))
