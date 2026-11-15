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

# What prompted a trade. Only `manual` has a writer today; the rest are here
# so the first automated trade needs no schema change.
MANUAL = "manual"
SOURCES = (MANUAL, "stop", "harvester", "evaluator", "watch", "ai")

STALE_QUOTE = "stale_quote"
NO_QUOTE = "no_quote"
NON_USD = "non_usd"
INSUFFICIENT_CASH = "insufficient_cash"
INSUFFICIENT_SHARES = "insufficient_shares"
NO_OPEN_PRICE = "no_open_price"
REJECT_REASONS = (
    STALE_QUOTE, NO_QUOTE, NON_USD, INSUFFICIENT_CASH, INSUFFICIENT_SHARES, NO_OPEN_PRICE,
)

# Where a fill's price came from.
PRICE_FROM_QUOTE = "quote"
PRICE_FROM_SESSION_OPEN = "session_open"

FILL = "fill"
QUEUE = "queue"
REJECT = "reject"

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


def sellable_shares(held: Decimal, queued_sells: Decimal) -> Decimal:
    """The shares a new sell may ask for: those held less those already
    promised to queued sells of the same symbol."""
    return max(held - queued_sells, Decimal("0"))
