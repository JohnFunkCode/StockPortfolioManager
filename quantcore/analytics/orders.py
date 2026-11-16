"""Paper orders: what happens to an order when it is placed, and the numbers
of a fill (#431 step 3.3).

Pure functions, no I/O. `OrdersService` gathers the quote and the clock and
asks here what to do with them.

The rules (plan decisions 25 to 29):

- **An order placed while the market is open fills at the current quote, or
  is rejected.** It is never left pending.
- **An order placed outside the session is queued for one session**: the next
  one to open. The daily job fills it at that session's opening price.
- **"Open" is three tests, not one.** The clock says 9:30 to 16:00 ET, the day
  is an NYSE trading day, and the quote's own market state doesn't say the
  session is over. The clock alone is wrong on a holiday, and on a half day
  after 13:00.
- **A quote is too old when its last regular-session trade is**, whatever the
  time it was fetched. Yahoo has no halted flag, so a halted symbol is refused
  the same way: its last trade is old while the market is open.
- **USD-listed symbols only.** The test is the quote's own currency.
- **A rejection carries a reason from `REJECT_REASONS`**, never free text.
- **A dollar buy never spends more than it asked for**: its share count is
  rounded down to the six decimals the database stores.
- **A queued order fills at the open of its session's closed daily bar**, on
  whatever night the daily job gets to it (`queued_fill`, plan step 3.4). With
  no such bar it is rejected once that is known to be final, and waits a
  night otherwise.
- **A seed is priced like a buy and costs no cash** (`seed_quantities`, plan
  step 3.5). It copies a real holding into a new paper book.
- **A position is closed by hand only when its symbol can't be quoted**
  (`unquotable`), and then at the last good price on record
  (`last_good_price`), never at one the owner types.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Optional

from quantcore.analytics import market_time
from quantcore.analytics.cash import to_cents

BUY = "buy"
SELL = "sell"
SIDES = (BUY, SELL)

PENDING = "pending"
FILLED = "filled"
CANCELLED = "cancelled"
REJECTED = "rejected"
# An order in one of these never changes again.
TERMINAL_STATUSES = (FILLED, CANCELLED, REJECTED)

# What prompted a trade. `manual` is the owner's own order and `stop` a sale
# the bar-close checker made (plan step 4.2). The rest have no writer yet;
# they are here so the first such trade needs no schema change.
MANUAL = "manual"
STOP = "stop"
SOURCES = (MANUAL, STOP, "harvester", "evaluator", "watch", "ai")

STALE_QUOTE = "stale_quote"
NO_QUOTE = "no_quote"
NON_USD = "non_usd"
INSUFFICIENT_CASH = "insufficient_cash"
INSUFFICIENT_SHARES = "insufficient_shares"
NO_OPEN_PRICE = "no_open_price"
REJECT_REASONS = (
    STALE_QUOTE, NO_QUOTE, NON_USD, INSUFFICIENT_CASH, INSUFFICIENT_SHARES, NO_OPEN_PRICE,
)

# Where a fill's price came from. A manual close has neither: its price is the
# last good one on record, and the order's kind says so. Nor has a stop's sale
# at its level, whose order has the source `stop`; one that filled at the open
# because the stock opened under the stop is `session_open`.
PRICE_FROM_QUOTE = "quote"
PRICE_FROM_SESSION_OPEN = "session_open"

# What kind of order it is. A market order is a buy or a sell that moves cash.
# A seed copies one real holding into a new paper book and moves none. A
# manual close sells a whole position whose symbol can no longer be quoted.
MARKET = "market"
SEED = "seed"
MANUAL_CLOSE = "manual_close"
KINDS = (MARKET, SEED, MANUAL_CLOSE)

# `positions.acquisition_type` of the lot each kind of buy fills. Only a BUY
# lot writes a cash row, which is what makes a seed free.
LOT_BOUGHT = "BUY"
LOT_SEEDED = "SEED"

# A symbol whose last regular-session trade is this many sessions old, or
# older, can be closed by hand. The same wait a queued order gives a missing
# bar before it is rejected.
UNQUOTABLE_AFTER_SESSIONS = 3

FILL = "fill"
QUEUE = "queue"
REJECT = "reject"
# A queued order the daily job leaves pending for another night.
WAIT = "wait"

# How many sessions after its own a queued order still waits for an opening
# price that hasn't arrived. On the run this many sessions later it is
# rejected `no_open_price`, so no order stays pending for good.
QUEUED_FILL_GIVE_UP_SESSIONS = 3

# A quote whose last regular-session trade is older than this is not filled
# against. The same five minutes the price cache allows a forming bar.
MAX_QUOTE_AGE_SECONDS = 300

# Yahoo's `marketState` values that mean the regular session is over, and the
# ones that mean it has not started.
_AFTER_SESSION = ("POST", "POSTPOST", "CLOSED")
_BEFORE_SESSION = ("PRE", "PREPRE")

_SHARE_STEP = Decimal("0.000001")
_SUB_DOLLAR_STEP = Decimal("0.0001")
_ONE = Decimal("1")


@dataclass(frozen=True)
class Placement:
    """What to do with a new order. `action` is FILL, QUEUE or REJECT.

    `session_date` is always set: the session a filled or rejected order
    belonged to, or the one a queued order waits for. `price` and `quote_time`
    are set for FILL only, `reason` for REJECT only.
    """

    action: str
    session_date: datetime.date
    reason: Optional[str] = None
    price: Optional[Decimal] = None
    quote_time: Optional[datetime.datetime] = None


def fill_price(price) -> Decimal:
    """A quote's price as a fill price: to the cent at a dollar or more, to a
    hundredth of a cent below it. Those are the market's own tick sizes, and
    they drop the float noise a quote arrives with."""
    value = Decimal(str(price))
    step = _SUB_DOLLAR_STEP if value < _ONE else Decimal("0.01")
    return value.quantize(step, rounding=ROUND_HALF_UP)


def shares_for_amount(amount: Decimal, price: Decimal) -> Decimal:
    """The shares a dollar amount buys at `price`, rounded down to six
    decimals so the cost is never more than the amount."""
    return (amount / price).quantize(_SHARE_STEP, rounding=ROUND_DOWN)


def trade_value(shares: Decimal, price: Decimal) -> Decimal:
    """What `shares` cost or bring in at `price`, to the cent. Gross of fees."""
    return to_cents(shares * price)


def _session_today_or_next(now: datetime.datetime) -> datetime.date:
    """The session a rejected order is recorded against: today's when today is
    a trading day, otherwise the next one."""
    today = market_time.market_date(now)
    return today if market_time.is_trading_day(today) else market_time.next_trading_day(today)


def _unusable_quote(has_quote: bool, currency: Optional[str]) -> Optional[str]:
    """Why the quote can't back an order at any hour, or None."""
    if not has_quote or not (currency or "").strip():
        return NO_QUOTE
    if currency.strip().upper() != "USD":
        return NON_USD
    return None


def _queued_for(now: datetime.datetime, state: str) -> Optional[datetime.date]:
    """The session an order placed at `now` waits for, or None when the
    market is open and it should be dealt with at once."""
    today = market_time.market_date(now)
    if not (market_time.is_trading_day(today) and market_time.is_market_open(now)):
        return market_time.upcoming_session(now)
    if state in _AFTER_SESSION:
        # A half day: the clock says open and the exchange says closed.
        return market_time.next_trading_day(today)
    return None


def _in_hours_refusal(
    now: datetime.datetime, state: str, regular_price: Optional[float],
    regular_time: Optional[int], max_quote_age_seconds: int,
) -> Optional[str]:
    """Why an order can't fill against this quote right now, or None."""
    if state in _BEFORE_SESSION:
        return STALE_QUOTE
    if regular_price is None or regular_price <= 0:
        return NO_QUOTE
    if regular_time is None or now.timestamp() - regular_time > max_quote_age_seconds:
        return STALE_QUOTE
    return None


def placement(
    *,
    now: datetime.datetime,
    has_quote: bool,
    currency: Optional[str],
    market_state: Optional[str],
    regular_price: Optional[float],
    regular_time: Optional[int],
    max_quote_age_seconds: int = MAX_QUOTE_AGE_SECONDS,
) -> Placement:
    """Fill, queue or reject an order placed at `now`, from its symbol's quote.

    `now` is timezone-aware. `regular_price` and `regular_time` (epoch
    seconds) are the last regular-session trade.

    - No quote, or one with no currency: rejected `no_quote`. The symbol
      can't be shown to exist or to be in USD.
    - A currency other than USD: rejected `non_usd`.
    - Outside market hours (by the clock, the holiday calendar, or a market
      state that says the session is over): queued for the next session.
    - In market hours with no price, a market state that says the session
      hasn't started, or a last trade older than the limit: rejected
      `no_quote` or `stale_quote`.
    - Otherwise filled at the last trade, rounded by `fill_price`.
    """
    today = _session_today_or_next(now)
    state = (market_state or "").strip().upper()

    reason = _unusable_quote(has_quote, currency)
    if reason is not None:
        return Placement(REJECT, today, reason=reason)
    queued_for = _queued_for(now, state)
    if queued_for is not None:
        return Placement(QUEUE, queued_for)
    reason = _in_hours_refusal(now, state, regular_price, regular_time, max_quote_age_seconds)
    if reason is not None:
        return Placement(REJECT, today, reason=reason)
    return Placement(
        FILL,
        today,
        price=fill_price(regular_price),
        quote_time=datetime.datetime.fromtimestamp(regular_time, tz=datetime.timezone.utc),
    )


@dataclass(frozen=True)
class QueuedFill:
    """What the daily job does with a queued order. `action` is FILL, REJECT
    or WAIT; `price` is set for FILL only, `reason` for REJECT only."""

    action: str
    price: Optional[Decimal] = None
    reason: Optional[str] = None


def _usable_open(open_price) -> Optional[Decimal]:
    """A stored or quoted price as a fill price, or None when it is missing,
    not a number, or not above zero."""
    if open_price is None:
        return None
    value = Decimal(str(open_price))
    if not value.is_finite() or value <= 0:
        return None
    price = fill_price(value)
    return price if price > 0 else None


def queued_fill(
    *,
    session: datetime.date,
    today: datetime.date,
    open_price,
    latest_bar: Optional[datetime.date],
    give_up_sessions: int = QUEUED_FILL_GIVE_UP_SESSIONS,
) -> QueuedFill:
    """Fill, reject or keep waiting on an order queued for `session`.

    `open_price` is the open of the symbol's closed daily bar for `session`,
    or None when there is no such bar. `latest_bar` is the date of its newest
    closed daily bar, or None. `today` is the market date of the run.

    - A session still to come: wait. Its bar can't exist yet.
    - A usable open: fill at it, rounded by `fill_price`. The price is the
      same on whatever night the order is reached.
    - No open, and a closed bar dated after the session: rejected
      `no_open_price`. The symbol's history has moved past that day without
      it (a symbol halted all day), so no bar is coming.
    - No open and no later bar: the history hasn't arrived, which a failed or
      empty fetch looks like too. Wait, until the session is
      `give_up_sessions` sessions old, then reject `no_open_price`.

    An order never rolls to a later session's open (plan decision 27).
    """
    if session > today:
        return QueuedFill(WAIT)
    price = _usable_open(open_price)
    if price is not None:
        return QueuedFill(FILL, price=price)
    if latest_bar is not None and latest_bar > session:
        return QueuedFill(REJECT, reason=NO_OPEN_PRICE)
    if market_time.trading_days_after(session, today) >= give_up_sessions:
        return QueuedFill(REJECT, reason=NO_OPEN_PRICE)
    return QueuedFill(WAIT)


def buy_shares(
    requested_shares: Optional[Decimal], requested_amount: Optional[Decimal], price: Decimal
) -> Decimal:
    """The shares a buy fills at `price`: the count asked for, or what the
    dollar amount buys."""
    if requested_shares is not None:
        return requested_shares
    return shares_for_amount(requested_amount, price)


def can_afford(balance: Optional[Decimal]) -> bool:
    """Whether a paper book's cash covers the buy just applied to it.

    `balance` is the ledger's balance with the buy's own row counted. A paper
    book has no margin: below zero is refused. None (a book with no opening
    balance) can't afford anything.
    """
    return balance is not None and balance >= 0


def lot_acquisition_type(kind: str) -> str:
    """The `acquisition_type` of the lot a buy of this kind fills."""
    return LOT_SEEDED if kind == SEED else LOT_BOUGHT


def moves_cash(kind: str) -> bool:
    """Whether a buy of this kind is paid for from the book's cash. A seed is
    not: it puts a holding in the book on top of its starting cash."""
    return kind != SEED


def seed_quantities(lots) -> dict:
    """The shares of each symbol a seed copies: the open lots' quantities
    added up by symbol, rounded once to six decimals. A symbol whose total is
    not above zero is left out.

    `lots` are dicts with `symbol` and `quantity`.
    """
    totals: dict = {}
    for lot in lots:
        symbol = str(lot["symbol"]).strip().upper()
        totals[symbol] = totals.get(symbol, Decimal("0")) + Decimal(str(lot["quantity"]))
    rounded = {symbol: total.quantize(_SHARE_STEP) for symbol, total in totals.items()}
    return {symbol: shares for symbol, shares in sorted(rounded.items()) if shares > 0}


def unquotable(
    *,
    today: datetime.date,
    has_quote: bool,
    currency: Optional[str],
    regular_price: Optional[float],
    regular_time: Optional[int],
    after_sessions: int = UNQUOTABLE_AFTER_SESSIONS,
) -> bool:
    """Whether a symbol can no longer be sold with an order, so its position
    may be closed by hand.

    True when there is no quote, or one with no currency, no price or no trade
    time (a delisted symbol), and when the last regular-session trade is
    `after_sessions` or more NYSE sessions before `today` (a symbol that has
    stopped trading but is still listed). `regular_time` is epoch seconds.
    """
    if not has_quote or not (currency or "").strip():
        return True
    if regular_price is None or regular_price <= 0 or regular_time is None:
        return True
    traded = datetime.datetime.fromtimestamp(regular_time, tz=datetime.timezone.utc)
    return market_time.trading_days_after(market_time.market_date(traded), today) >= after_sessions


@dataclass(frozen=True)
class LastGoodPrice:
    """The price a manual close fills at, the time it is from, and the
    session that time belongs to."""

    price: Decimal
    as_of: datetime.datetime
    session: datetime.date


def last_good_price(
    *,
    bar_close,
    bar_time: Optional[datetime.datetime],
    quote_price: Optional[float],
    quote_time: Optional[int],
) -> Optional[LastGoodPrice]:
    """The last good price on record for a symbol that can't be quoted, or
    None when there is none.

    The close of its newest stored closed daily bar when there is one.
    Otherwise the last regular-session trade the quote still carries
    (`quote_time` is epoch seconds). Rounded by `fill_price`. `bar_time` is
    timezone-aware.
    """
    close = _usable_open(bar_close)
    if close is not None and bar_time is not None:
        # A daily bar is stamped midnight UTC of its session's date, so the
        # session is the UTC date, not the Eastern one.
        return LastGoodPrice(close, bar_time, bar_time.astimezone(datetime.timezone.utc).date())
    traded = _usable_open(quote_price)
    if traded is not None and quote_time is not None:
        as_of = datetime.datetime.fromtimestamp(quote_time, tz=datetime.timezone.utc)
        return LastGoodPrice(traded, as_of, market_time.market_date(as_of))
    return None


def sellable_shares(held: Decimal, queued_sells: Decimal) -> Decimal:
    """The shares a new sell may ask for: those held less those already
    promised to queued sells of the same symbol."""
    return max(held - queued_sells, Decimal("0"))
