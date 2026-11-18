"""Seeding a paper book, closing a position that can't be quoted, and the
day's `[PAPER]` fills message (#431 step 3.5).

Runs `OrdersService` against the real repositories and the test database,
with a stub in place of `YFinanceGateway` (the suite never reaches Yahoo), and
the two new routes through TestClient with two owners.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest import mock

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from quantcore.analytics import orders as om  # noqa: E402
from quantcore.analytics.market_time import ET  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.ohlcv_repository import OhlcvRepository  # noqa: E402
from quantcore.repositories.orders_repository import OrdersRepository  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.cash import CashService  # noqa: E402
from quantcore.services.orders import MANUAL_CLOSE_NOTE, OrdersService  # noqa: E402
from quantcore.services.prices import PricesService  # noqa: E402

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

from tests.test_orders_api import StubGateway, quote  # noqa: E402
from tests.test_orders_repository import purge  # noqa: E402
from tests.test_queued_fills import daily_bars  # noqa: E402

OWNER = "zz_paper_trading_owner"
OTHER = "zz_paper_trading_other"
IDENTITY = "zz_paper_trading_owner@example.com"
OTHER_IDENTITY = "zz_paper_trading_other@example.com"
FIRST = "ZZPAPERA"
SECOND = "ZZPAPERB"
FOREIGN = "ZZPAPERF"
SYMBOLS = (FIRST, SECOND, FOREIGN)
D = Decimal

# Monday 2026-06-15, 11:00 ET is in the session; 20:00 is after the close, and
# an order placed then belongs to Tuesday's session.
OPEN_NOW = ET.localize(datetime.datetime(2026, 6, 15, 11, 0))
EVENING = ET.localize(datetime.datetime(2026, 6, 15, 20, 0))
MONDAY = datetime.date(2026, 6, 15)
TUESDAY = datetime.date(2026, 6, 16)
LAST_BAR = datetime.date(2026, 6, 5)
OPENED_ON = datetime.date(2026, 6, 1)


def _purge():
    purge((OWNER, OTHER), SYMBOLS)
    with closing(get_connection()) as conn:
        for symbol in SYMBOLS:
            conn.execute("DELETE FROM ohlcv WHERE symbol = %s", (symbol,))
            conn.execute("DELETE FROM fetch_log WHERE symbol = %s", (symbol,))
        for identity in (IDENTITY, OTHER_IDENTITY):
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (identity,))
        conn.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.cash = CashService(CashRepository(), self.books)
        self.repo = OrdersRepository(self.lots)
        self.gateway = StubGateway()
        self.gateway.quotes = {FIRST: quote(50.0), SECOND: quote(20.0)}
        # Real prices over the real `ohlcv` table. `last_close` never fetches,
        # so a call to the gateway mock would be a bug.
        self.history = mock.Mock()
        self.prices = PricesService(
            ohlcv_repository=OhlcvRepository(), yfinance_gateway=self.history,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())
        self.service = OrdersService(self.repo, self.books, self.gateway, self.prices)
        self.book = self.books.create_paper_book(
            owner=OWNER, name="Paper", starting_cash=1000, opened_on=OPENED_ON
        )

    def hold_for_real(self, symbol, quantity, owner=OWNER, currency="USD"):
        """A lot in `owner`'s real book."""
        return self.lots.add_position(owner, {
            "symbol": symbol, "name": symbol, "purchase_price": D("10"), "quantity": D(quantity),
            "purchase_date": "2026-01-05", "trade_date": "2026-01-05", "currency": currency,
        })

    def balance(self):
        return self.cash.ledger(owner=OWNER, book_id=self.book)["balance"]

    def paper_lots(self, symbol, status="OPEN"):
        return self.lots.list_lots_for_symbol(OWNER, symbol, status, book_id=self.book)

    def stored(self, order_id):
        return self.repo.get_order(owner=OWNER, order_id=order_id, book_id=self.book)


class SeedBookTest(_Base):
    def test_in_market_hours_each_holding_is_copied_at_its_quote_and_costs_no_cash(self):
        self.hold_for_real(FIRST, "3")
        self.hold_for_real(FIRST, "1.5")
        self.hold_for_real(SECOND, "10")

        seeded = self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)

        self.assertEqual(
            (seeded["filled"], seeded["pending"], seeded["rejected"], seeded["skipped"]),
            (2, 0, 0, []))
        self.assertEqual(
            [(o["symbol"], o["kind"], o["side"], o["filled_shares"], o["fill_price"],
              o["price_source"]) for o in seeded["orders"]],
            [(FIRST, "seed", "buy", D("4.5"), D("50"), "quote"),
             (SECOND, "seed", "buy", D("10"), D("20"), "quote")])
        # One lot a symbol, at the quote, dated the session, and not a BUY.
        (lot,) = self.paper_lots(FIRST)
        self.assertEqual(
            (lot["quantity"], lot["purchase_price"], lot["trade_date"], lot["acquisition_type"]),
            (D("4.5"), D("50"), MONDAY, "SEED"))
        # On top of the starting cash: the ledger is untouched.
        self.assertEqual(self.balance(), D("1000.00"))
        entries = self.cash.ledger(owner=OWNER, book_id=self.book)["entries"]
        self.assertEqual([e["kind"] for e in entries], ["opening_balance"])
        self.assertEqual(self.gateway.calls, 1)  # one batch for every symbol

    def test_the_real_book_is_read_and_left_alone(self):
        self.hold_for_real(FIRST, "3")
        self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)
        real = self.lots.list_positions(OWNER)
        self.assertEqual([(lot["symbol"], lot["quantity"]) for lot in real], [(FIRST, D("3"))])

    def test_a_seeded_lot_can_be_sold_and_then_the_sale_moves_cash(self):
        self.hold_for_real(FIRST, "4")
        self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)

        sold = self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="sell", shares=1,
            idempotency_key="sell-1", now=OPEN_NOW)

        self.assertEqual(sold["status"], "filled")
        self.assertEqual(self.balance(), D("1050.00"))

    def test_outside_market_hours_every_seed_queues_and_fills_at_its_sessions_open(self):
        self.hold_for_real(FIRST, "4")
        self.gateway.quotes[FIRST] = quote(50.0, now=EVENING, age=4 * 3600, state="POST")

        seeded = self.service.seed_book(owner=OWNER, book_id=self.book, now=EVENING)

        (order,) = seeded["orders"]
        self.assertEqual((seeded["pending"], order["status"], order["session_date"]),
                         (1, "pending", "2026-06-16"))
        self.assertEqual(self.paper_lots(FIRST), [])

        OhlcvRepository().store_bars(FIRST, "1d", daily_bars({MONDAY: 49.5, TUESDAY: 51.2}))
        filled = self.service.fill_queued_order(
            owner=OWNER, order_id=order["order_id"], book_id=self.book, today=TUESDAY)

        self.assertEqual((filled["status"], filled["fill_price"], filled["price_source"]),
                         ("filled", D("51.2"), "session_open"))
        (lot,) = self.paper_lots(FIRST)
        self.assertEqual((lot["acquisition_type"], lot["trade_date"]), ("SEED", TUESDAY))
        self.assertEqual(self.balance(), D("1000.00"))

    def test_a_seed_larger_than_the_cash_still_fills(self):
        self.hold_for_real(FIRST, "1000")  # $50,000 of it against $1,000 of cash
        seeded = self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)
        self.assertEqual((seeded["filled"], seeded["rejected"]), (1, 0))

    def test_a_holding_that_cannot_be_priced_is_a_rejected_order_with_its_reason(self):
        self.hold_for_real(FIRST, "1")
        self.hold_for_real(SECOND, "1")
        self.hold_for_real(FOREIGN, "1", currency="SEK")
        del self.gateway.quotes[SECOND]
        self.gateway.quotes[FOREIGN] = quote(9.0, currency="SEK")

        seeded = self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)

        self.assertEqual((seeded["filled"], seeded["rejected"]), (1, 2))
        self.assertEqual(
            {o["symbol"]: o["reject_reason"] for o in seeded["orders"]},
            {FIRST: None, SECOND: "no_quote", FOREIGN: "non_usd"})
        self.assertEqual(self.paper_lots(SECOND), [])

    def test_a_symbol_an_order_cannot_name_is_skipped_and_reported(self):
        self.hold_for_real(FIRST, "1")
        with mock.patch.object(self.repo, "open_lots", return_value=[
            {"symbol": FIRST, "quantity": D("1")},
            {"symbol": "NOT A TICKER", "quantity": D("2")},
        ]):
            seeded = self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)
        self.assertEqual(seeded["skipped"], ["NOT A TICKER"])
        self.assertEqual([o["symbol"] for o in seeded["orders"]], [FIRST])

    def test_only_a_book_with_no_orders_can_be_seeded(self):
        self.hold_for_real(FIRST, "1")
        self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)

        with self.assertRaisesRegex(ValueError, "no orders"):
            self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)
        self.assertEqual(len(self.service.list_orders(owner=OWNER, book_id=self.book)["orders"]), 1)

        # A cancelled order still counts: the book has been used.
        used = self.books.create_paper_book(
            owner=OWNER, name="Used", starting_cash=100, opened_on=OPENED_ON)
        self.gateway.quotes[FIRST] = quote(50.0, now=EVENING, age=4 * 3600, state="POST")
        queued = self.service.place_order(
            owner=OWNER, book_id=used, symbol=FIRST, side="buy", shares=1,
            idempotency_key="q1", now=EVENING)
        self.service.cancel_order(owner=OWNER, order_id=queued["order_id"], book_id=used)
        with self.assertRaisesRegex(ValueError, "no orders"):
            self.service.seed_book(owner=OWNER, book_id=used, now=OPEN_NOW)

    def test_an_empty_real_book_seeds_nothing_and_says_so(self):
        with self.assertRaisesRegex(ValueError, "nothing to copy"):
            self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)
        self.assertEqual(self.gateway.calls, 0)

    def test_the_real_book_an_archived_book_and_a_strangers_book_are_refused(self):
        self.hold_for_real(FIRST, "1")
        real = self.books.ensure_real_book(owner=OWNER)
        for book_id in (None, real):
            with self.assertRaisesRegex(ValueError, "paper books"):
                self.service.seed_book(owner=OWNER, book_id=book_id, now=OPEN_NOW)
        self.books.archive_book(owner=OWNER, book_id=self.book)
        with self.assertRaisesRegex(ValueError, "archived"):
            self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)

    def test_another_owner_cannot_seed_this_book_and_only_their_own_holdings_are_copied(self):
        self.hold_for_real(FIRST, "7")
        self.hold_for_real(SECOND, "2", owner=OTHER)

        with self.assertRaises(BookNotFoundError):
            self.service.seed_book(owner=OTHER, book_id=self.book, now=OPEN_NOW)
        self.assertEqual(self.service.list_orders(owner=OWNER, book_id=self.book)["orders"], [])

        theirs = self.books.create_paper_book(
            owner=OTHER, name="Theirs", starting_cash=0, opened_on=OPENED_ON)
        seeded = self.service.seed_book(owner=OTHER, book_id=theirs, now=OPEN_NOW)
        self.assertEqual([(o["symbol"], o["filled_shares"]) for o in seeded["orders"]],
                         [(SECOND, D("2"))])

    def test_a_failure_part_way_leaves_the_book_unseeded(self):
        self.hold_for_real(FIRST, "1")
        self.hold_for_real(SECOND, "1")
        real_act = self.service._act
        calls = []

        def failing(tx, order, plan, now):
            calls.append(order["symbol"])
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real_act(tx, order, plan, now)

        with mock.patch.object(self.service, "_act", side_effect=failing):
            with self.assertRaises(RuntimeError):
                self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)

        self.assertEqual(self.service.list_orders(owner=OWNER, book_id=self.book)["orders"], [])
        self.assertEqual(self.paper_lots(FIRST), [])
        # Nothing was kept, so the seed can be asked for again.
        again = self.service.seed_book(owner=OWNER, book_id=self.book, now=OPEN_NOW)
        self.assertEqual(again["filled"], 2)


class CloseUnquotedTest(_Base):
    def setUp(self):
        super().setUp()
        # Two lots bought on paper, then the symbol stops being quoted.
        for key, shares in (("b1", 2), ("b2", 3)):
            self.service.place_order(
                owner=OWNER, book_id=self.book, symbol=FIRST, side="buy", shares=shares,
                idempotency_key=key, now=OPEN_NOW)
        del self.gateway.quotes[FIRST]
        self.gateway.calls = 0

    def close(self, key="close-1", now=OPEN_NOW, **kwargs):
        kwargs.setdefault("owner", OWNER)
        kwargs.setdefault("book_id", self.book)
        kwargs.setdefault("symbol", FIRST)
        return self.service.close_unquoted(idempotency_key=key, now=now, **kwargs)

    def store_last_bar(self, close=12.3456):
        OhlcvRepository().store_bars(FIRST, "1d", daily_bars({LAST_BAR: close}))

    def test_the_whole_position_is_sold_at_the_last_stored_close_and_marked(self):
        self.store_last_bar()

        order = self.close()

        self.assertEqual(
            (order["kind"], order["side"], order["status"], order["filled_shares"],
             order["fill_price"], order["price_source"], order["session_date"]),
            ("manual_close", "sell", "filled", D("5"), D("12.35"), None, "2026-06-15"))
        # A daily bar is stamped midnight UTC of its session's date.
        self.assertEqual(order["quote_time"].astimezone(datetime.timezone.utc).date(), LAST_BAR)
        self.assertEqual(self.paper_lots(FIRST), [])
        # 1000 - 5 x 50 bought + 5 x 12.35 sold.
        self.assertEqual(self.balance(), D("811.75"))
        with closing(get_connection()) as conn:
            sales = conn.execute(
                "SELECT ls.notes, ls.order_id, ls.sale_price, ls.sale_trade_date FROM lot_sales ls "
                "JOIN positions p ON p.position_id = ls.lot_id WHERE p.owner = %s", (OWNER,),
            ).fetchall()
        self.assertEqual(len(sales), 2)
        for sale in sales:
            self.assertEqual(sale["notes"], MANUAL_CLOSE_NOTE.format(as_of="2026-06-05"))
            self.assertEqual(sale["order_id"], order["order_id"])
            self.assertEqual(str(sale["sale_trade_date"]), "2026-06-15")
        self.history.fetch_history.assert_not_called()

    def test_with_no_stored_bar_it_falls_back_to_the_quotes_last_trade(self):
        # Still listed, last traded on Monday 8 June: five sessions before.
        traded = ET.localize(datetime.datetime(2026, 6, 8, 15, 0))
        self.gateway.quotes[FIRST] = quote(7.5, now=traded, age=0)

        order = self.close()

        self.assertEqual((order["status"], order["fill_price"]), ("filled", D("7.5")))
        self.assertEqual(order["quote_time"], traded.astimezone(datetime.timezone.utc))

    def test_a_symbol_with_a_quote_is_refused(self):
        self.store_last_bar()
        self.gateway.quotes[FIRST] = quote(50.0)
        with self.assertRaisesRegex(ValueError, "has a quote"):
            self.close()
        # One trade yesterday is still a quote: an order can sell it.
        friday = ET.localize(datetime.datetime(2026, 6, 12, 15, 59))
        self.gateway.quotes[FIRST] = quote(50.0, now=friday, age=0, state="CLOSED")
        with self.assertRaisesRegex(ValueError, "has a quote"):
            self.close()
        self.assertEqual(len(self.paper_lots(FIRST)), 2)

    def test_no_price_on_record_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no price on record"):
            self.close()
        self.assertEqual(len(self.paper_lots(FIRST)), 2)

    def test_a_symbol_the_book_does_not_hold_is_refused_and_stores_no_order(self):
        OhlcvRepository().store_bars(SECOND, "1d", daily_bars({LAST_BAR: 5.0}))
        del self.gateway.quotes[SECOND]
        with self.assertRaisesRegex(ValueError, "holds no"):
            self.close(symbol=SECOND)
        orders = self.service.list_orders(owner=OWNER, book_id=self.book)["orders"]
        self.assertEqual([o["kind"] for o in orders], ["market", "market"])

    def test_a_queued_sell_blocks_the_close_until_it_is_cancelled_or_fills(self):
        """A manual close sells every share, so it would leave a queued sell
        to be rejected at its session with nothing saying why."""
        self.store_last_bar()
        self.gateway.quotes[FIRST] = quote(50.0, now=EVENING, age=4 * 3600, state="POST")
        queued = self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="sell", shares=2,
            idempotency_key="queued-sell", now=EVENING)
        self.assertEqual(queued["status"], "pending")
        del self.gateway.quotes[FIRST]

        with self.assertRaisesRegex(ValueError, "waiting for its session"):
            self.close()
        # Nothing was stored or sold: the refusal undid the order it had made.
        orders = self.service.list_orders(owner=OWNER, book_id=self.book)["orders"]
        self.assertNotIn("manual_close", [o["kind"] for o in orders])
        self.assertEqual(sum(lot["quantity"] for lot in self.paper_lots(FIRST)), D("5"))

        # The queued sell is untouched and still fills at its session's open.
        OhlcvRepository().store_bars(FIRST, "1d", daily_bars({MONDAY: 49.5, TUESDAY: 51.2}))
        filled = self.service.fill_queued_order(
            owner=OWNER, order_id=queued["order_id"], book_id=self.book, today=TUESDAY)
        self.assertEqual((filled["status"], filled["filled_shares"]), ("filled", D("2")))

        # With no sell waiting, the rest of the position can be closed.
        closed = self.close(key="close-after")
        self.assertEqual((closed["status"], closed["filled_shares"]), ("filled", D("3")))

    def test_a_repeated_key_closes_nothing_twice(self):
        self.store_last_bar()
        first = self.close()
        calls = self.gateway.calls

        again = self.close()

        self.assertEqual((again["order_id"], again["replayed"]), (first["order_id"], True))
        self.assertEqual(self.gateway.calls, calls)  # answered before the quote
        self.assertEqual(self.balance(), D("811.75"))

    def test_the_real_book_and_a_strangers_book_are_refused(self):
        self.store_last_bar()
        with self.assertRaisesRegex(ValueError, "paper books"):
            self.close(book_id=None)
        with self.assertRaises(BookNotFoundError):
            self.close(owner=OTHER)
        self.assertEqual(len(self.paper_lots(FIRST)), 2)

    def test_bad_input_is_refused_before_anything_is_read(self):
        with self.assertRaisesRegex(ValueError, "valid ticker"):
            self.close(symbol="NOT A TICKER")
        with self.assertRaisesRegex(ValueError, "idempotency key"):
            self.close(key="")
        self.assertEqual(self.gateway.calls, 0)


class LastCloseTest(_Base):
    def test_reads_the_newest_closed_bar_and_never_fetches(self):
        OhlcvRepository().store_bars(
            FIRST, "1d", daily_bars({datetime.date(2026, 6, 4): 11.0, LAST_BAR: 12.5}))
        last = self.prices.last_close(FIRST.lower())
        self.assertEqual((last["close"], last["as_of"].date()), (12.5, LAST_BAR))
        self.history.fetch_history.assert_not_called()

    def test_a_symbol_with_no_bars_has_no_last_close(self):
        self.assertEqual(self.prices.last_close(FIRST), {"close": None, "as_of": None})
        self.history.fetch_history.assert_not_called()

    def test_last_closes_reads_every_symbol_at_once_and_leaves_out_one_with_none(self):
        OhlcvRepository().store_bars(
            FIRST, "1d", daily_bars({datetime.date(2026, 6, 4): 11.0, LAST_BAR: 12.5}))
        closes = self.prices.last_closes([FIRST.lower(), SECOND])
        self.assertEqual({s: (c["close"], c["as_of"].date()) for s, c in closes.items()},
                         {FIRST: (12.5, LAST_BAR)})
        self.history.fetch_history.assert_not_called()


class FillsOnTest(_Base):
    def test_lists_the_days_fills_of_the_owners_paper_books_with_their_book(self):
        self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="buy", shares=2,
            idempotency_key="b1", now=OPEN_NOW)
        # Rejected, and so not a fill.
        self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="sell", shares=99,
            idempotency_key="s1", now=OPEN_NOW)
        # Another owner's fill, the same day.
        theirs = self.books.create_paper_book(
            owner=OTHER, name="Theirs", starting_cash=1000, opened_on=OPENED_ON)
        self.service.place_order(
            owner=OTHER, book_id=theirs, symbol=FIRST, side="buy", shares=1,
            idempotency_key="b1", now=OPEN_NOW)

        fills = self.service.fills_on(owner=OWNER, day=MONDAY)

        self.assertEqual(
            [(f["book_name"], f["symbol"], f["side"], f["filled_shares"], f["kind"])
             for f in fills],
            [("Paper", FIRST, "buy", D("2"), "market")])
        self.assertNotIn("idempotency_key", fills[0])
        self.assertEqual(self.service.fills_on(owner=OWNER, day=TUESDAY), [])
        self.assertEqual(self.service.fills_on(owner=OWNER, day=MONDAY - datetime.timedelta(1)), [])

    def test_the_day_is_the_market_date_and_an_archived_book_still_counts(self):
        # 23:30 ET on Monday is Tuesday in UTC, and still Monday's fill.
        late = ET.localize(datetime.datetime(2026, 6, 15, 23, 30))
        self.gateway.quotes[FIRST] = quote(50.0, now=EVENING, age=4 * 3600, state="POST")
        queued = self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="buy", shares=1,
            idempotency_key="q1", now=ET.localize(datetime.datetime(2026, 6, 12, 20, 0)))
        OhlcvRepository().store_bars(FIRST, "1d", daily_bars({MONDAY: 48.0}))
        self.books.archive_book(owner=OWNER, book_id=self.book)
        self.service.fill_queued_order(
            owner=OWNER, order_id=queued["order_id"], book_id=self.book, today=MONDAY, now=late)

        self.assertEqual(len(self.service.fills_on(owner=OWNER, day=MONDAY)), 1)
        self.assertEqual(self.service.fills_on(owner=OWNER, day=TUESDAY), [])


def fill(**overrides):
    base = {
        "book_name": "Momentum", "symbol": "AAA", "side": "buy", "kind": "market",
        "filled_shares": D("10.000000"), "fill_price": D("190.120000"), "price_source": "quote",
    }
    return {**base, **overrides}


class ReportPaperFillsTest(unittest.TestCase):
    def test_sends_one_message_for_the_owners_fills_and_caps_the_lines(self):
        orders, notifier = mock.Mock(), mock.Mock()
        orders.fills_on.return_value = [fill(symbol=f"S{i}") for i in range(25)]

        sent = main.report_paper_fills(orders, notifier, "john", today=MONDAY)

        self.assertEqual(sent, 25)
        orders.fills_on.assert_called_once_with(owner="john", day=MONDAY)
        lines, count, day = notifier.send_paper_fills.call_args.args
        self.assertEqual((len(lines), count, day),
                         (main.PAPER_FILLS_MESSAGE_LINES, 25, "2026-06-15"))

    def test_a_day_with_no_fills_sends_nothing(self):
        orders, notifier = mock.Mock(), mock.Mock()
        orders.fills_on.return_value = []
        self.assertEqual(main.report_paper_fills(orders, notifier, "john", today=MONDAY), 0)
        notifier.send_paper_fills.assert_not_called()

    def test_never_raises_and_logs_the_class_only(self):
        for broken in ("fills_on", "send"):
            orders, notifier = mock.Mock(), mock.Mock()
            orders.fills_on.return_value = [fill()]
            if broken == "fills_on":
                orders.fills_on.side_effect = RuntimeError("secret detail")
            else:
                notifier.send_paper_fills.side_effect = RuntimeError("secret detail")
            with mock.patch("builtins.print") as printed:
                self.assertEqual(
                    main.report_paper_fills(orders, notifier, "john", today=MONDAY), 0)
            output = " ".join(str(c) for c in printed.call_args_list)
            self.assertIn("paper fills report failed: RuntimeError", output)
            self.assertNotIn("secret detail", output)


class NotifierPaperFillsTest(unittest.TestCase):
    def _notifier(self):
        from notifier import Notifier

        notifier = Notifier.__new__(Notifier)
        notifier.discord_webhook_url = "https://example.invalid/main"
        return notifier

    def test_embed_is_marked_paper_and_puts_each_fill_in_words(self):
        notifier = self._notifier()
        notifier.send_notifications = mock.Mock()
        fills = [
            fill(),
            fill(side="sell", symbol="BBB", filled_shares=D("0.500000"),
                 fill_price=D("0.123400"), price_source="session_open"),
            fill(kind="seed", symbol="CCC"),
            fill(kind="manual_close", side="sell", symbol="DDD", price_source=None,
                 fill_price=D("1.200000")),
        ]

        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("PAPER_DISCORD_WEBHOOK_URL", None)
            notifier.send_paper_fills(fills, 6, "2026-06-15")

        embed = notifier.send_notifications.call_args.args[0]
        self.assertTrue(embed["content"].startswith("[PAPER]"))
        self.assertEqual(embed["embeds"][0]["title"], "[PAPER] 6 paper fill(s) on 2026-06-15")
        self.assertEqual(embed["embeds"][0]["description"].split("\n"), [
            "- Momentum: bought 10 AAA at $190.12",
            "- Momentum: sold 0.5 BBB at $0.1234, the opening price",
            "- Momentum: seeded 10 CCC at $190.12",
            "- Momentum: closed by hand 10 DDD at $1.20, the last good price",
            "- and 2 more",
            "Simulated trades in paper books. No real order was placed.",
        ])
        self.assertIsNone(notifier.send_notifications.call_args.kwargs["webhook_url"])

    def test_its_own_webhook_is_used_when_one_is_set(self):
        notifier = self._notifier()
        with mock.patch.dict("os.environ", {"PAPER_DISCORD_WEBHOOK_URL": "https://example.invalid/paper"}), \
                mock.patch("notifier.requests.post") as post, \
                mock.patch("notifier.os.path.exists", return_value=False), \
                mock.patch("builtins.open", mock.mock_open()):
            post.return_value.status_code = 204
            notifier.send_paper_fills([fill()], 1, "2026-06-15")
        self.assertEqual(post.call_args.args[0], "https://example.invalid/paper")


class RoutesTest(_Base):
    def setUp(self):
        super().setUp()
        identities = OwnerIdentityRepository()
        identities.upsert(identity=IDENTITY, owner=OWNER)
        identities.upsert(identity=OTHER_IDENTITY, owner=OTHER)
        self.app = create_app()
        self.client = TestClient(self.app)
        self.as_identity(IDENTITY)
        services = mock.patch("api.routers.orders.services")
        services.start().return_value.orders = self.service
        self.addCleanup(services.stop)
        clock = mock.patch("quantcore.services.orders.datetime")
        frozen = clock.start()
        frozen.datetime.now.return_value = OPEN_NOW
        frozen.datetime.combine = datetime.datetime.combine
        frozen.timezone = datetime.timezone
        self.addCleanup(clock.stop)

    def as_identity(self, identity):
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal

    def seed(self, **body):
        return self.client.post("/api/portfolio/orders/seed", json={"book_id": self.book, **body})

    def manual_close(self, **body):
        body = {"book_id": self.book, "symbol": FIRST, "idempotency_key": "mc1", **body}
        return self.client.post("/api/portfolio/orders/manual-close", json=body)

    def test_seed_answers_201_with_one_order_a_symbol_then_422(self):
        self.hold_for_real(FIRST, "2")
        seeded = self.seed()
        self.assertEqual(seeded.status_code, 201)
        body = seeded.json()
        self.assertEqual((body["filled"], body["pending"], body["rejected"], body["skipped"]),
                         (1, 0, 0, []))
        self.assertEqual((body["orders"][0]["kind"], body["orders"][0]["filled_shares"]),
                         ("seed", 2))
        self.assertNotIn("idempotency_key", body["orders"][0])

        again = self.seed()
        self.assertEqual(again.status_code, 422)
        self.assertIn("no orders", again.json()["error"])

    def test_seed_refuses_the_real_book_and_takes_no_owner_or_price(self):
        self.hold_for_real(FIRST, "2")
        self.hold_for_real(SECOND, "9", owner=OTHER)
        real = self.books.ensure_real_book(owner=OWNER)
        self.assertEqual(self.seed(book_id=real).status_code, 422)
        self.assertEqual(self.client.post("/api/portfolio/orders/seed", json={}).status_code, 422)

        seeded = self.seed(owner=OTHER, price="1", symbols=[SECOND])
        self.assertEqual([o["symbol"] for o in seeded.json()["orders"]], [FIRST])
        self.assertEqual(seeded.json()["orders"][0]["fill_price"], 50)

    def test_manual_close_answers_201_then_200_for_the_same_key(self):
        self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="buy", shares=2,
            idempotency_key="b1", now=OPEN_NOW)
        del self.gateway.quotes[FIRST]
        OhlcvRepository().store_bars(FIRST, "1d", daily_bars({LAST_BAR: 12.0}))

        closed = self.manual_close()
        self.assertEqual(closed.status_code, 201)
        body = closed.json()
        self.assertEqual(
            (body["kind"], body["status"], body["filled_shares"], body["fill_price"],
             body["price_source"]),
            ("manual_close", "filled", 2, 12, None))

        again = self.manual_close()
        self.assertEqual((again.status_code, again.json()["replayed"]), (200, True))

    def test_manual_close_takes_no_price_and_refuses_a_quoted_symbol(self):
        self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="buy", shares=2,
            idempotency_key="b1", now=OPEN_NOW)
        refused = self.manual_close(price="0.01", fill_price="0.01")
        self.assertEqual(refused.status_code, 422)
        self.assertIn("has a quote", refused.json()["error"])
        self.assertEqual(self.manual_close(symbol="NOT A TICKER").status_code, 422)
        self.assertEqual(self.manual_close(idempotency_key=None).status_code, 422)

    def test_another_owner_gets_a_404_for_both_routes(self):
        self.hold_for_real(FIRST, "2", owner=OTHER)
        self.service.place_order(
            owner=OWNER, book_id=self.book, symbol=FIRST, side="buy", shares=2,
            idempotency_key="b1", now=OPEN_NOW)
        self.as_identity(OTHER_IDENTITY)

        self.assertEqual(self.seed().status_code, 404)
        self.assertEqual(self.manual_close().status_code, 404)
        self.assertEqual(len(self.paper_lots(FIRST)), 1)
        self.assertEqual(self.balance(), D("900.00"))


class RuleTablesTest(unittest.TestCase):
    def test_the_kinds_are_the_columns_check(self):
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT pg_get_constraintdef(oid) AS rule FROM pg_constraint "
                "WHERE conname = 'orders_kind_rule'"
            ).fetchone()
        for kind in om.KINDS:
            self.assertIn(f"'{kind}'", row["rule"])

    def test_the_database_refuses_a_seed_that_is_a_sell(self):
        _purge()
        self.addCleanup(_purge)
        book = BooksRepository().create_paper_book(
            owner=OWNER, name="Paper", starting_cash=0, opened_on=OPENED_ON)
        repo = OrdersRepository(PortfolioRepository())
        with self.assertRaises(Exception) as raised:
            with repo.transaction(owner=OWNER, book_id=book) as tx:
                tx.insert({
                    "kind": "seed", "side": "sell", "requested_shares": D("1"),
                    "requested_amount": None, "symbol": FIRST, "source": "manual",
                    "source_ref": None, "idempotency_key": "bad", "session_date": MONDAY,
                    "placed_at": OPEN_NOW,
                })
        self.assertIn("orders_kind_rule", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
