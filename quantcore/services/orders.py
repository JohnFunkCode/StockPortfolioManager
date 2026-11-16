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


def _public(order: Dict[str, Any], *, replayed: bool = False) -> Dict[str, Any]:
    """The order as a caller sees it: everything but the idempotency key,
    plus whether this answer is a repeat of an earlier request."""
    shown = {key: value for key, value in order.items() if key != "idempotency_key"}
    return {**shown, "replayed": replayed}


def _held(lots: List[Dict[str, Any]]) -> Decimal:
    return sum((lot["quantity"] for lot in lots), _ZERO)


class OrdersService:
    def __init__(
        self,
        orders_repository: OrdersRepository,
        books_repository: BooksRepository,
        yfinance_gateway,
        prices=None,
    ) -> None:
        self._repo = orders_repository
        self._books_repo = books_repository
        # PricesService.session_open: the opening price a queued order fills
        # at. Only the daily job's fill needs it.
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
        book_id: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Place a market order in a paper book and return it as it now is:
        `filled`, `rejected` (with `reject_reason`) or `pending`.

        A buy is for `shares` or for a dollar `amount`; a sell is for
        `shares`. `replayed` is True when `idempotency_key` had already been
        used: nothing new happens and the first order is returned.

        Raises `ValueError` for bad input (a dollar amount that would buy
        more shares than an order can hold included), the real book or an
        archived one, and `BookNotFoundError` for a book that isn't the
        owner's. A refusal
        that depends on the market or the book's holdings is not an error: it
        is a stored order with status `rejected`.
        """
        symbol = _symbol(symbol)
        request = _request(side, shares, amount)
        key = _key(idempotency_key)
        if source not in orders_math.SOURCES:
            raise ValueError(f"source must be one of {', '.join(orders_math.SOURCES)}")
        ref = _source_ref(source_ref)
        self._paper_book(owner, book_id)
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)

        # A repeat is answered before the quote is asked for.
        earlier = self._repo.get_order_by_key(owner=owner, idempotency_key=key, book_id=book_id)
        if earlier is not None:
            return _public(earlier, replayed=True)

        plan = orders_math.placement(now=now, **self._quote_facts(symbol))

        with self._repo.transaction(owner=owner, book_id=book_id) as tx:
            order, created = tx.insert({
                **request,
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
        quote = self._gateway.batch_quotes([symbol]).get(symbol)
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
        price_source: str,
        quote_time: Optional[datetime.datetime],
        now: datetime.datetime,
    ) -> None:
        """Fill a pending order at `price` on its transaction, or reject it
        when the book can't cover it. The trade's date is the order's session.

        The trade's rows are written first and the order is marked last, so
        the cash check reads the ledger as it will be. A trade the book can't
        cover is undone to the savepoint and the order is rejected instead.
        """
        tx.savepoint()
        if order["side"] == orders_math.BUY:
            shares, reason = self._buy(tx, order, price)
        else:
            shares, reason = self._sell(tx, order, price)
        if reason is not None:
            tx.undo_trade()
            self._reject(tx, order, reason, now)
            return
        resolved = tx.resolve(
            order["order_id"], orders_math.FILLED, resolved_at=now, filled_shares=shares,
            fill_price=price, price_source=price_source, quote_time=quote_time,
        )
        if not resolved:
            # The trade's rows are already written. Raising rolls them back
            # with the transaction: an order that wasn't pending fills nothing.
            raise RuntimeError("order is no longer pending")

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
            "acquisition_type": "BUY",
            "order_id": order["order_id"],
        })
        balance = cash_math.cash_ledger(tx.cash_entries())["balance"]
        if not orders_math.can_afford(balance):
            return None, orders_math.INSUFFICIENT_CASH
        return shares, None

    def _sell(self, tx: OrderTransaction, order: Dict[str, Any], price: Decimal):
        """Write a sell's sale rows, oldest lots first. Returns
        `(shares, None)`, or `(None, reason)` when the book doesn't hold them."""
        shares = order["requested_shares"]
        try:
            allocation = portfolio_math.allocate(tx.open_lots(order["symbol"]), shares, SELL_METHOD)
        except portfolio_math.LotAllocationError:
            return None, orders_math.INSUFFICIENT_SHARES
        tx.sell(
            allocation, price, order["session_date"], allocation_method=SELL_METHOD,
            source=order["source"], source_ref=order["source_ref"], order_id=order["order_id"],
        )
        return shares, None

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
