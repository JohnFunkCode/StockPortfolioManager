"""Queued paper orders and the daily job step that fills them (#431 step 3.4).

Four layers, bottom up: `PricesService.session_open` against the real `ohlcv`
table with the Yahoo fetch stubbed; `OrdersService.queued_orders` and
`fill_queued_order` against the real repositories with a stub in place of the
price read; the walk, the alarm and the outer guard in `main.py` with a mock
service; and one run of the whole step on real services.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest import mock

import pandas as pd

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import main  # noqa: E402
from quantcore.analytics import orders as om  # noqa: E402
from quantcore.analytics.market_time import ET  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import BooksRepository  # noqa: E402
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.ohlcv_repository import OhlcvRepository  # noqa: E402
from quantcore.repositories.orders_repository import OrdersRepository  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.cash import CashService  # noqa: E402
from quantcore.services.orders import OrderNotFoundError, OrdersService  # noqa: E402
from quantcore.services.prices import PricesService  # noqa: E402

from tests.test_orders_api import StubGateway, quote  # noqa: E402
from tests.test_orders_repository import purge  # noqa: E402

OWNER = "zz_queued_fills_owner"
OTHER = "zz_queued_fills_other"
SYMBOL = "ZZQFILL"
SECOND = "ZZQFILL2"
D = Decimal

# Monday 2026-06-15, 20:00 ET: after the close, so an order placed then is for
# Tuesday's session. Friday 2026-06-19 is a holiday (Juneteenth).
EVENING = ET.localize(datetime.datetime(2026, 6, 15, 20, 0))
MONDAY = datetime.date(2026, 6, 15)
SESSION = datetime.date(2026, 6, 16)
WEDNESDAY = datetime.date(2026, 6, 17)
JOB_NOW = ET.localize(datetime.datetime(2026, 6, 16, 17, 5))
OPENED_ON = datetime.date(2026, 6, 1)


def ts_of(day: datetime.date) -> int:
    return int(datetime.datetime.combine(
        day, datetime.time(0), tzinfo=datetime.timezone.utc).timestamp())


def daily_bars(opens: dict) -> pd.DataFrame:
    """Daily bars dated at midnight UTC, one per `{date: open}`."""
    days = sorted(opens)
    values = [opens[day] for day in days]
    return pd.DataFrame(
        {"Open": values, "High": [v + 1 for v in values], "Low": [v - 1 for v in values],
         "Close": values, "Volume": [1_000_000] * len(days)},
        index=pd.DatetimeIndex([pd.Timestamp(ts_of(day), unit="s", tz="UTC") for day in days]),
    )


def _purge():
    purge((OWNER, OTHER), (SYMBOL, SECOND))
    with closing(get_connection()) as conn:
        for symbol in (SYMBOL, SECOND):
            conn.execute("DELETE FROM ohlcv WHERE symbol = %s", (symbol,))
            conn.execute("DELETE FROM fetch_log WHERE symbol = %s", (symbol,))
        conn.commit()


class StubPrices:
    """`session_open` from a dict of `{(symbol, session): open}`, with the
    newest bar date it should report."""

    def __init__(self):
        self.opens = {(SYMBOL, SESSION): 51.2372}
        self.latest_bar = SESSION
        self.error = None
        self.before_answer = None
        self.calls = []

    def session_open(self, symbol, session):
        self.calls.append((symbol, session))
        if self.before_answer is not None:
            self.before_answer()
        if self.error is not None:
            raise self.error
        return {"open": self.opens.get((symbol, session)), "latest_bar": self.latest_bar}


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.cash = CashService(CashRepository(), self.books)
        self.repo = OrdersRepository(self.lots)
        self.gateway = StubGateway()
        self.gateway.quotes = {
            SYMBOL: quote(state="POST", now=EVENING, age=4 * 3600),
            SECOND: quote(state="POST", now=EVENING, age=4 * 3600),
        }
        self.prices = StubPrices()
        self.service = OrdersService(self.repo, self.books, self.gateway, self.prices)
        self.book = self.books.create_paper_book(
            owner=OWNER, name="Paper", starting_cash=1000, opened_on=OPENED_ON
        )
        self._keys = 0

    def queue(self, side="buy", now=EVENING, **kwargs):
        """Place an order after the close, so it is queued."""
        self._keys += 1
        kwargs.setdefault("idempotency_key", f"queued-{self._keys}")
        kwargs.setdefault("book_id", self.book)
        kwargs.setdefault("owner", OWNER)
        kwargs.setdefault("symbol", SYMBOL)
        if "shares" not in kwargs and "amount" not in kwargs:
            kwargs["shares"] = 2
        order = self.service.place_order(side=side, now=now, **kwargs)
        self.assertEqual(order["status"], "pending")
        return order

    def fill(self, order, today=SESSION, **kwargs):
        kwargs.setdefault("owner", OWNER)
        kwargs.setdefault("book_id", order["book_id"])
        return self.service.fill_queued_order(
            order_id=order["order_id"], today=today, now=JOB_NOW, **kwargs)

    def balance(self, book_id=None):
        return self.cash.ledger(owner=OWNER, book_id=book_id or self.book)["balance"]

    def open_lots(self, symbol=SYMBOL):
        return self.lots.list_lots_for_symbol(OWNER, symbol, book_id=self.book)

    def hold(self, shares, price=40.0):
        """Give the book an open lot to sell, bought in market hours."""
        self._keys += 1
        open_now = ET.localize(datetime.datetime(2026, 6, 15, 11, 0))
        self.gateway.quotes[SYMBOL] = quote(price, now=open_now)
        order = self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=SYMBOL, side="buy", shares=shares,
            idempotency_key=f"hold-{self._keys}", now=open_now)
        self.assertEqual(order["status"], "filled")
        self.gateway.quotes[SYMBOL] = quote(state="POST", now=EVENING, age=4 * 3600)


class FillQueuedOrderTest(_Base):
    def test_a_queued_buy_fills_at_its_sessions_open(self):
        order = self.queue(shares=2)

        filled = self.fill(order)

        self.assertEqual(
            (filled["status"], filled["fill_price"], filled["filled_shares"]),
            ("filled", D("51.240000"), D("2.000000")))
        self.assertEqual((filled["price_source"], filled["quote_time"]), ("session_open", None))
        self.assertEqual(filled["resolved_at"], JOB_NOW)
        self.assertEqual(self.prices.calls, [(SYMBOL, SESSION)])
        self.assertEqual(self.balance(), D("897.52"))

    def test_the_lot_is_dated_the_session_not_the_night_it_was_filled(self):
        order = self.queue(shares=2)
        self.prices.latest_bar = datetime.date(2026, 6, 24)

        self.fill(order, today=datetime.date(2026, 6, 24))

        with closing(get_connection()) as conn:
            lot = conn.execute(
                "SELECT trade_date, order_id, purchase_price FROM positions "
                "WHERE owner = %s AND status = 'OPEN'", (OWNER,)).fetchone()
        self.assertEqual(str(lot["trade_date"]), "2026-06-16")
        self.assertEqual(lot["order_id"], order["order_id"])
        self.assertEqual(D(str(lot["purchase_price"])), D("51.24"))

    def test_a_queued_dollar_buy_gets_the_shares_the_open_buys(self):
        order = self.queue(amount="500")

        filled = self.fill(order)

        self.assertEqual(filled["filled_shares"], D("9.758001"))
        self.assertEqual(self.balance(), D("500.00"))

    def test_a_buy_the_cash_cannot_cover_at_the_open_is_rejected(self):
        # Accepted in the evening, when its cost wasn't known (decision 28).
        order = self.queue(shares=20)

        rejected = self.fill(order)

        self.assertEqual((rejected["status"], rejected["reject_reason"]),
                         ("rejected", "insufficient_cash"))
        self.assertEqual(self.open_lots(), [])
        self.assertEqual(self.balance(), D("1000.00"))

    def test_a_queued_sell_takes_the_oldest_lot_at_the_open(self):
        self.hold(5)
        order = self.queue(side="sell", shares=3)

        filled = self.fill(order)

        self.assertEqual((filled["status"], filled["fill_price"]), ("filled", D("51.240000")))
        self.assertEqual([D(str(lot["quantity"])) for lot in self.open_lots()], [D("2")])
        self.assertEqual(self.balance(), D("1000.00") - D("200.00") + D("153.72"))

    def test_a_sell_whose_shares_have_gone_is_rejected(self):
        self.hold(5)
        order = self.queue(side="sell", shares=5)
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE positions SET quantity = 1 WHERE owner = %s AND status = 'OPEN'",
                (OWNER,))
            conn.commit()

        rejected = self.fill(order)

        self.assertEqual((rejected["status"], rejected["reject_reason"]),
                         ("rejected", "insufficient_shares"))

    def test_no_bar_for_the_session_waits_a_night(self):
        order = self.queue()
        self.prices.opens = {}
        self.prices.latest_bar = MONDAY

        waiting = self.fill(order)

        self.assertEqual((waiting["status"], waiting["resolved_at"]), ("pending", None))
        self.assertEqual(self.open_lots(), [])

    def test_a_later_bar_without_the_sessions_rejects_no_open_price(self):
        order = self.queue()
        self.prices.opens = {}
        self.prices.latest_bar = WEDNESDAY

        rejected = self.fill(order, today=WEDNESDAY)

        self.assertEqual((rejected["status"], rejected["reject_reason"]),
                         ("rejected", "no_open_price"))
        self.assertEqual(self.balance(), D("1000.00"))

    def test_an_order_gives_up_on_the_third_session_without_a_bar(self):
        order = self.queue()
        self.prices.opens = {}
        self.prices.latest_bar = MONDAY

        self.assertEqual(self.fill(order, today=datetime.date(2026, 6, 18))["status"], "pending")
        rejected = self.fill(order, today=datetime.date(2026, 6, 22))

        self.assertEqual(rejected["reject_reason"], "no_open_price")

    def test_an_order_for_a_later_session_is_left_alone_and_asks_for_no_price(self):
        order = self.queue()

        untouched = self.fill(order, today=MONDAY)

        self.assertEqual(untouched["status"], "pending")
        self.assertEqual(self.prices.calls, [])

    def test_filling_twice_fills_once(self):
        order = self.queue(shares=2)
        self.fill(order)

        again = self.fill(order)

        self.assertEqual(again["status"], "filled")
        self.assertEqual(len(self.open_lots()), 1)
        self.assertEqual(self.balance(), D("897.52"))
        self.assertEqual(len(self.prices.calls), 1)

    def test_a_cancelled_order_is_returned_as_it_is(self):
        order = self.queue()
        self.service.cancel_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)

        self.assertEqual(self.fill(order)["status"], "cancelled")
        self.assertEqual(self.prices.calls, [])

    def test_an_order_cancelled_while_the_price_was_fetched_is_not_filled(self):
        order = self.queue()
        self.prices.before_answer = lambda: self.service.cancel_order(
            owner=OWNER, order_id=order["order_id"], book_id=self.book)

        result = self.fill(order)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.open_lots(), [])
        self.assertEqual(self.balance(), D("1000.00"))

    def test_a_price_fetch_that_raises_leaves_the_order_pending(self):
        order = self.queue()
        self.prices.error = RuntimeError("yahoo is down")

        with self.assertRaises(RuntimeError):
            self.fill(order)

        still = self.repo.get_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)
        self.assertEqual(still["status"], "pending")

    def test_another_owner_cannot_fill_it(self):
        order = self.queue()
        other_book = self.books.create_paper_book(
            owner=OTHER, name="Theirs", starting_cash=1000, opened_on=OPENED_ON)

        for kwargs in ({"owner": OTHER, "book_id": other_book},
                       {"owner": OWNER, "book_id": None}):
            with self.subTest(**kwargs), self.assertRaises(OrderNotFoundError):
                self.fill(order, **kwargs)
        with self.assertRaises(ValueError):
            self.fill(order, owner=OTHER, book_id=self.book)

        self.assertEqual(
            self.repo.get_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)["status"],
            "pending")

    def test_a_fill_of_an_order_that_is_not_pending_raises_and_writes_nothing(self):
        # The guard behind `fill_queued_order`'s own check under the lock.
        order = self.queue()
        self.service.cancel_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)

        with self.assertRaises(RuntimeError):
            with self.repo.transaction(owner=OWNER, book_id=self.book) as tx:
                self.service._fill(
                    tx, tx.get(order["order_id"]), price=D("50"),
                    price_source=om.PRICE_FROM_SESSION_OPEN, quote_time=None, now=JOB_NOW)

        self.assertEqual(self.open_lots(), [])
        self.assertEqual(self.balance(), D("1000.00"))


class QueuedOrdersTest(_Base):
    def test_lists_what_is_due_oldest_session_first(self):
        tuesday = self.queue(symbol=SECOND)
        # Placed on Tuesday evening, so it is for Wednesday.
        later = ET.localize(datetime.datetime(2026, 6, 16, 20, 0))
        self.gateway.quotes[SYMBOL] = quote(state="POST", now=later, age=4 * 3600)
        wednesday = self.queue(now=later)

        self.assertEqual(self.service.queued_orders(today=MONDAY), [])
        self.assertEqual(
            [o["order_id"] for o in self.service.queued_orders(today=SESSION)],
            [tuesday["order_id"]])
        due = self.service.queued_orders(today=WEDNESDAY)
        self.assertEqual([o["order_id"] for o in due],
                         [tuesday["order_id"], wednesday["order_id"]])
        self.assertEqual(due[0], {
            "owner": OWNER, "book_id": self.book, "order_id": tuesday["order_id"],
            "symbol": SECOND, "session_date": "2026-06-16",
        })

    def test_only_pending_orders_are_due(self):
        filled, cancelled, waiting = self.queue(), self.queue(), self.queue()
        self.fill(filled)
        self.service.cancel_order(owner=OWNER, order_id=cancelled["order_id"], book_id=self.book)

        self.assertEqual(
            [o["order_id"] for o in self.service.queued_orders(today=SESSION)],
            [waiting["order_id"]])

    def test_every_owner_and_every_paper_book_is_walked(self):
        mine = self.queue()
        second_book = self.books.create_paper_book(
            owner=OWNER, name="Second", starting_cash=1000, opened_on=OPENED_ON)
        second = self.queue(book_id=second_book)
        other_book = self.books.create_paper_book(
            owner=OTHER, name="Theirs", starting_cash=1000, opened_on=OPENED_ON)
        theirs = self.queue(owner=OTHER, book_id=other_book)
        # A real book for the owner: it holds no orders and must not break the walk.
        self.books.ensure_real_book(owner=OWNER)

        due = [o for o in self.service.queued_orders(today=SESSION)
               if o["owner"] in (OWNER, OTHER)]

        self.assertEqual(
            {(o["owner"], o["book_id"], o["order_id"]) for o in due},
            {(OWNER, self.book, mine["order_id"]), (OWNER, second_book, second["order_id"]),
             (OTHER, other_book, theirs["order_id"])})

    def test_an_order_in_a_book_archived_since_is_still_due_and_fills(self):
        order = self.queue()
        self.assertTrue(self.books.archive_book(owner=OWNER, book_id=self.book))

        self.assertEqual(
            [o["order_id"] for o in self.service.queued_orders(today=SESSION)],
            [order["order_id"]])
        self.assertEqual(self.fill(order)["status"], "filled")

    def test_the_repository_read_is_bound_to_its_owner_and_book(self):
        order = self.queue()
        other_book = self.books.create_paper_book(
            owner=OTHER, name="Theirs", starting_cash=1000, opened_on=OPENED_ON)

        mine = self.repo.list_due_orders(owner=OWNER, through=SESSION, book_id=self.book)
        self.assertEqual([o["order_id"] for o in mine], [order["order_id"]])
        for owner, book_id in ((OTHER, self.book), (OTHER, other_book), (OWNER, other_book),
                               (OWNER, None), (OTHER, None)):
            with self.subTest(owner=owner, book_id=book_id):
                self.assertEqual(
                    self.repo.list_due_orders(owner=owner, through=SESSION, book_id=book_id), [])

    def test_a_sell_placed_before_a_buy_pays_for_it(self):
        self.hold(20)  # $800 of the $1,000, so $200 of cash is left
        sell = self.queue(side="sell", shares=20)
        buy = self.queue(symbol=SECOND, shares=20)
        self.prices.opens[(SECOND, SESSION)] = 50.0

        results = [
            self.fill(order) for order in (
                self.repo.get_order(owner=OWNER, order_id=o["order_id"], book_id=self.book)
                for o in self.service.queued_orders(today=SESSION))
        ]

        self.assertEqual([r["order_id"] for r in results], [sell["order_id"], buy["order_id"]])
        self.assertEqual([r["status"] for r in results], ["filled", "filled"])
        self.assertEqual(self.balance(), D("200.00") + D("1024.80") - D("1000.00"))


class SessionOpenTest(unittest.TestCase):
    """`PricesService.session_open` on the real `ohlcv` table; Yahoo is a mock."""

    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.repo = OhlcvRepository()
        self.gateway = mock.Mock()
        self.gateway.fetch_history.return_value = pd.DataFrame()
        self.prices = PricesService(
            ohlcv_repository=self.repo, yfinance_gateway=self.gateway,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())

    def test_reads_the_open_of_the_sessions_closed_bar(self):
        self.repo.store_bars(SYMBOL, "1d", daily_bars({MONDAY: 49.5, SESSION: 51.2372}))

        self.assertEqual(self.prices.session_open(SYMBOL, SESSION),
                         {"open": 51.2372, "latest_bar": SESSION})
        self.assertEqual(self.prices.session_open(SYMBOL.lower(), MONDAY),
                         {"open": 49.5, "latest_bar": SESSION})

    def test_a_day_with_no_bar_has_no_open_and_reports_the_newest_bar(self):
        self.repo.store_bars(SYMBOL, "1d", daily_bars({MONDAY: 49.5, WEDNESDAY: 52.0}))

        self.assertEqual(self.prices.session_open(SYMBOL, SESSION),
                         {"open": None, "latest_bar": WEDNESDAY})

    def test_a_bar_still_open_is_not_a_price(self):
        self.repo.store_bars(SYMBOL, "1d", daily_bars({MONDAY: 49.5, SESSION: 51.0}))
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE ohlcv SET status = 'OPEN' WHERE symbol = %s AND interval = '1d' "
                "AND ts = %s", (SYMBOL, ts_of(SESSION)))
            conn.commit()

        self.assertEqual(self.prices.session_open(SYMBOL, SESSION),
                         {"open": None, "latest_bar": MONDAY})

    def test_a_symbol_with_no_history_is_fetched_with_its_full_warm_window(self):
        from quantcore.services.prices import WARM_DAYS

        self.assertEqual(self.prices.session_open(SYMBOL, SESSION),
                         {"open": None, "latest_bar": None})
        self.gateway.fetch_history.assert_called_once_with(SYMBOL, "1d", WARM_DAYS["1d"])

    def test_a_missing_bar_is_fetched_and_then_read(self):
        self.repo.store_bars(SYMBOL, "1d", daily_bars({MONDAY: 49.5}))
        self.gateway.fetch_history.return_value = daily_bars({MONDAY: 49.5, SESSION: 51.0})

        self.assertEqual(self.prices.session_open(SYMBOL, SESSION),
                         {"open": 51.0, "latest_bar": SESSION})
        symbol, interval, days = self.gateway.fetch_history.call_args.args
        self.assertEqual((symbol, interval), (SYMBOL, "1d"))
        age = (datetime.datetime.now(datetime.timezone.utc).date() - SESSION).days
        self.assertGreaterEqual(days, age)

    def test_a_fetch_that_raises_reaches_the_caller(self):
        self.gateway.fetch_history.side_effect = RuntimeError("429")
        with self.assertRaises(RuntimeError):
            self.prices.session_open(SYMBOL, SESSION)


# ---------------------------------------------------------------------------
# main.py: the walk, the alarm, the outer guard
# ---------------------------------------------------------------------------


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_queued_orders_alert(self, order_count, symbols, symbol_count, oldest_session):
        self.sent.append((order_count, symbols, symbol_count, oldest_session))


class _Clock:
    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def due(n, symbol="AAA", session="2026-06-16"):
    return [{"owner": f"secret-owner-{i}", "book_id": 7, "order_id": i + 1,
             "symbol": symbol, "session_date": session} for i in range(n)]


def fake_orders(queue=None, statuses=(), left=None, fail=()):
    """A mock `OrdersService`: `queue` is what is due before the fills,
    `left` what is still due after them."""
    orders = mock.Mock()
    queue = queue if queue is not None else due(len(statuses) or 2)
    orders.queued_orders.side_effect = [queue, left if left is not None else []]
    outcomes = iter(statuses or ["filled"] * len(queue))

    def fill(*, owner, book_id, order_id, today):
        if order_id in fail:
            raise RuntimeError("secret detail")
        return {"order_id": order_id, "status": next(outcomes)}

    orders.fill_queued_order.side_effect = fill
    return orders


class FillQueuedOrdersTest(unittest.TestCase):
    def test_walks_what_is_due_and_counts_each_outcome(self):
        orders = fake_orders(
            queue=due(5), statuses=["filled", "rejected", "pending", "cancelled"], fail={5})

        with mock.patch("builtins.print") as printed:
            s = main.fill_queued_orders(orders, budget_seconds=100, clock=_Clock(0), today=SESSION)

        self.assertEqual(
            {k: s[k] for k in ("due", "attempted", "filled", "rejected", "waiting",
                               "skipped", "failed", "budget_exhausted")},
            {"due": 5, "attempted": 5, "filled": 1, "rejected": 1, "waiting": 1,
             "skipped": 1, "failed": 1, "budget_exhausted": False})
        orders.queued_orders.assert_called_once_with(today=SESSION)
        self.assertEqual(
            orders.fill_queued_order.call_args_list[0],
            mock.call(owner="secret-owner-0", book_id=7, order_id=1, today=SESSION))
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("order 5 (AAA): fill failed (RuntimeError)", output)
        self.assertNotIn("secret", output)

    def test_budget_stops_the_walk(self):
        orders = fake_orders(queue=due(10))
        s = main.fill_queued_orders(orders, budget_seconds=4, clock=_Clock(1.0), today=SESSION)
        self.assertEqual((s["attempted"], s["filled"]), (3, 3))
        self.assertTrue(s["budget_exhausted"])

    def test_nothing_due_is_quiet(self):
        orders = fake_orders(queue=[])
        s = main.fill_queued_orders(orders, budget_seconds=10, today=SESSION)
        self.assertEqual((s["due"], s["attempted"]), (0, 0))
        orders.fill_queued_order.assert_not_called()

    def test_budget_comes_from_the_environment(self):
        with mock.patch.dict("os.environ", {main.QUEUED_FILLS_BUDGET_SECONDS_ENV: "3"}):
            s = main.fill_queued_orders(fake_orders(queue=due(10)), clock=_Clock(1.0),
                                        today=SESSION)
        self.assertEqual(s["attempted"], 2)

    def test_unparseable_budget_falls_back_to_the_default(self):
        with mock.patch.dict("os.environ", {main.QUEUED_FILLS_BUDGET_SECONDS_ENV: "soon"}), \
                mock.patch("builtins.print") as printed:
            main.fill_queued_orders(fake_orders(queue=[]), today=SESSION)
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn(f"budget {main.DEFAULT_QUEUED_FILLS_BUDGET_SECONDS:.0f}s", output)

    def test_budget_is_clamped_to_what_is_left_of_the_job(self):
        left = main._remaining_budget(
            main.DEFAULT_QUEUED_FILLS_BUDGET_SECONDS, job_started=0.0, clock=lambda: 1735.0)
        self.assertEqual(left, 5.0)


class AlertIfOrdersStillQueuedTest(unittest.TestCase):
    def test_quiet_when_nothing_is_left(self):
        orders, n = mock.Mock(), FakeNotifier()
        orders.queued_orders.return_value = []
        self.assertFalse(main.alert_if_orders_still_queued(orders, n, today=SESSION))
        self.assertEqual(n.sent, [])
        orders.queued_orders.assert_called_once_with(today=SESSION)

    def test_alarm_carries_the_count_a_symbol_sample_and_the_oldest_session(self):
        left = [o for i in range(12) for o in due(1, symbol=f"S{i:02d}")]
        left += due(1, symbol="S00", session="2026-06-12")
        orders, n = mock.Mock(), FakeNotifier()
        orders.queued_orders.return_value = left

        with mock.patch("builtins.print") as printed:
            self.assertTrue(main.alert_if_orders_still_queued(orders, n, today=SESSION))

        count, symbols, symbol_count, oldest = n.sent[0]
        self.assertEqual((count, symbol_count, oldest), (13, 12, "2026-06-12"))
        self.assertEqual(symbols, [f"S{i:02d}" for i in range(10)])
        self.assertNotIn("secret-owner", " ".join(str(c) for c in printed.call_args_list))

    def test_dead_webhook_does_not_raise(self):
        orders, n = mock.Mock(), FakeNotifier()
        orders.queued_orders.return_value = due(1)
        n.send_queued_orders_alert = mock.Mock(side_effect=RuntimeError("webhook"))
        self.assertTrue(main.alert_if_orders_still_queued(orders, n, today=SESSION))


class RunQueuedFillsTest(unittest.TestCase):
    def test_fills_then_alarms_on_what_the_database_still_holds(self):
        orders = fake_orders(queue=due(2), statuses=["filled", "pending"], left=due(1))
        n = FakeNotifier()

        main.run_queued_fills(orders, n, budget_seconds=100, today=SESSION)

        self.assertEqual(
            [c[0] for c in orders.method_calls],
            ["queued_orders", "fill_queued_order", "fill_queued_order", "queued_orders"])
        self.assertEqual(n.sent, [(1, ["AAA"], 1, "2026-06-16")])

    def test_the_alarm_still_runs_when_the_fills_raise(self):
        orders, n = mock.Mock(), FakeNotifier()
        orders.queued_orders.side_effect = [RuntimeError("secret detail"), due(3)]

        with mock.patch("builtins.print") as printed:
            main.run_queued_fills(orders, n, today=SESSION)

        self.assertEqual(n.sent, [(3, ["AAA"], 1, "2026-06-16")])
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("secret detail", output)

    def test_never_raises(self):
        orders = mock.Mock()
        orders.queued_orders.side_effect = RuntimeError("secret detail")
        with mock.patch("builtins.print") as printed:
            main.run_queued_fills(orders, FakeNotifier(), today=SESSION)
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("queued-orders check failed: RuntimeError", output)
        self.assertNotIn("secret detail", output)


class NotifierQueuedOrdersAlertTest(unittest.TestCase):
    def test_embed_is_marked_paper_and_names_the_count_sample_and_session(self):
        from notifier import Notifier

        notifier = Notifier.__new__(Notifier)
        notifier.send_notifications = mock.Mock()
        notifier.send_queued_orders_alert(13, ["AAA", "BBB"], 12, "2026-06-12")
        embed = notifier.send_notifications.call_args.args[0]
        description = embed["embeds"][0]["description"]
        self.assertTrue(embed["content"].startswith("[PAPER]"))
        self.assertIn("13 queued paper order(s)", description)
        self.assertIn("12 symbol(s): AAA, BBB and 10 more", description)
        self.assertIn("2026-06-12 session", description)
        # The message spells the limit out; this fails if the constant moves.
        self.assertIn("after three sessions", description)
        self.assertEqual(om.QUEUED_FILL_GIVE_UP_SESSIONS, 3)


class StepOnRealServicesTest(_Base):
    """The whole step: real `OrdersService`, real `PricesService` over the
    real `ohlcv` table. Only the Yahoo calls are stubbed."""

    def setUp(self):
        super().setUp()
        self.history = mock.Mock()
        self.history.fetch_history.return_value = pd.DataFrame()
        real_prices = PricesService(
            ohlcv_repository=OhlcvRepository(), yfinance_gateway=self.history,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())
        self.service = OrdersService(self.repo, self.books, self.gateway, real_prices)

    def _mine(self, orders):
        return [o for o in orders if o["owner"] == OWNER]

    def test_a_queued_order_fills_from_the_stored_bar_and_the_alarm_stays_quiet(self):
        order = self.queue(shares=2)
        OhlcvRepository().store_bars(
            SYMBOL, "1d", daily_bars({MONDAY: 49.5, SESSION: 51.2372}))
        n = FakeNotifier()

        with mock.patch.object(
                self.service, "queued_orders",
                side_effect=lambda today: self._mine(
                    OrdersService.queued_orders(self.service, today=today))):
            main.run_queued_fills(self.service, n, budget_seconds=60, today=SESSION)

        stored = self.repo.get_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)
        self.assertEqual((stored["status"], stored["fill_price"], stored["price_source"]),
                         ("filled", D("51.240000"), "session_open"))
        self.assertEqual(self.balance(), D("897.52"))
        self.assertEqual(n.sent, [])

    def test_an_order_with_no_bar_stays_queued_and_the_alarm_says_so(self):
        order = self.queue(shares=2)
        OhlcvRepository().store_bars(SYMBOL, "1d", daily_bars({MONDAY: 49.5}))
        n = FakeNotifier()

        with mock.patch.object(
                self.service, "queued_orders",
                side_effect=lambda today: self._mine(
                    OrdersService.queued_orders(self.service, today=today))):
            main.run_queued_fills(self.service, n, budget_seconds=60, today=SESSION)

        stored = self.repo.get_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(n.sent, [(1, [SYMBOL], 1, "2026-06-16")])


if __name__ == "__main__":
    unittest.main()
