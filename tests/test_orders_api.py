"""`OrdersService` and the /api/portfolio/orders routes (#431 step 3.3).

Runs the service against the real repositories and the test database, with a
stub in place of `YFinanceGateway` (the suite never reaches Yahoo), and the
routes through TestClient with two owners reached through `owner_identities`.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.analytics.market_time import ET  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.gateways.yfinance_gateway import Quote  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.orders_repository import OrdersRepository  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.cash import CashService  # noqa: E402
from quantcore.services.orders import (  # noqa: E402
    MAX_KEY_LENGTH,
    MAX_ORDER_SHARES,
    OrderNotFoundError,
    OrdersService,
)

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

from tests.test_orders_repository import purge  # noqa: E402

OWNER = "zz_orders_api_owner"
OTHER = "zz_orders_api_other"
IDENTITY = "zz_orders_api_owner@example.com"
OTHER_IDENTITY = "zz_orders_api_other@example.com"
SYMBOL = "ZZORDAPI"
D = Decimal

# Monday 2026-06-15, 11:00 ET: an ordinary session. 20:00 the same day is after
# the close, and the order belongs to Tuesday's session.
OPEN_NOW = ET.localize(datetime.datetime(2026, 6, 15, 11, 0))
EVENING = ET.localize(datetime.datetime(2026, 6, 15, 20, 0))
OPENED_ON = datetime.date(2026, 6, 1)


def quote(price=50.0, *, now=OPEN_NOW, age=10, state="REGULAR", currency="USD"):
    return Quote(
        regular_open=price, regular_high=price, regular_low=price, regular_price=price,
        regular_volume=1000, regular_time=int(now.timestamp()) - age, previous_close=price,
        regular_change_pct=0.0, pre_price=None, pre_change_pct=None, pre_time=None,
        post_price=None, post_change_pct=None, post_time=None, market_state=state,
        currency=currency, exchange_tz="America/New_York",
    )


class StubGateway:
    """`batch_quotes` from a dict, counting the calls."""

    def __init__(self):
        self.quotes = {SYMBOL: quote()}
        self.calls = 0

    def batch_quotes(self, symbols):
        self.calls += 1
        return {s: self.quotes.get(s) for s in symbols}


def _purge():
    purge((OWNER, OTHER), (SYMBOL, "ZZNOTHELD"))
    with closing(get_connection()) as conn:
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
        self.service = OrdersService(self.repo, self.books, self.gateway)
        self.book = self.books.create_paper_book(
            owner=OWNER, name="Paper", starting_cash=1000, opened_on=OPENED_ON
        )
        self._keys = 0

    def place(self, side="buy", now=OPEN_NOW, **kwargs):
        self._keys += 1
        kwargs.setdefault("idempotency_key", f"key-{self._keys}")
        kwargs.setdefault("book_id", self.book)
        kwargs.setdefault("owner", OWNER)
        kwargs.setdefault("symbol", SYMBOL)
        if "shares" not in kwargs and "amount" not in kwargs:
            kwargs["shares"] = 2
        return self.service.place_order(side=side, now=now, **kwargs)

    def balance(self, book_id=None):
        return self.cash.ledger(owner=OWNER, book_id=book_id or self.book)["balance"]

    def open_lots(self):
        return self.lots.list_lots_for_symbol(OWNER, SYMBOL, book_id=self.book)

    def sales(self):
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ls.lot_id, ls.shares_sold, ls.sale_price, ls.sale_trade_date, ls.source, "
                "ls.source_ref, ls.order_id, ls.allocation_method FROM lot_sales ls "
                "JOIN positions p ON p.position_id = ls.lot_id WHERE p.owner = %s "
                "ORDER BY ls.sale_id",
                (OWNER,),
            ).fetchall()
        return [dict(r) for r in rows]


class BuyTest(_Base):
    def test_a_buy_in_market_hours_fills_at_the_quote(self):
        order = self.place(shares=2)

        self.assertEqual(
            (order["status"], order["filled_shares"], order["fill_price"], order["price_source"]),
            ("filled", D("2"), D("50"), "quote"),
        )
        self.assertEqual((order["session_date"], order["source"], order["replayed"]),
                         ("2026-06-15", "manual", False))
        self.assertEqual(order["resolved_at"], OPEN_NOW)
        self.assertEqual(order["quote_time"].timestamp(), OPEN_NOW.timestamp() - 10)
        self.assertNotIn("idempotency_key", order)

        (lot,) = self.open_lots()
        self.assertEqual((lot["quantity"], lot["purchase_date"], lot["currency"]),
                         (D("2"), "2026-06-15", "USD"))
        self.assertEqual(D(str(lot["purchase_price"])), D("50"))
        self.assertEqual(self.balance(), D("900.00"))

    def test_the_lot_and_its_cash_row_carry_the_order(self):
        order = self.place(shares=2)

        with closing(get_connection()) as conn:
            lot = conn.execute(
                "SELECT position_id, order_id, acquisition_type FROM positions WHERE owner = %s",
                (OWNER,),
            ).fetchone()
        self.assertEqual((lot["order_id"], lot["acquisition_type"]), (order["order_id"], "BUY"))
        entry = self.cash.ledger(owner=OWNER, book_id=self.book)["entries"][0]
        self.assertEqual((entry["kind"], entry["lot_id"], entry["amount"]),
                         ("buy", lot["position_id"], D("-100.00")))

    def test_a_dollar_buy_fills_the_shares_the_amount_buys_and_no_more(self):
        self.gateway.quotes[SYMBOL] = quote(123.45)

        order = self.place(amount="1000")

        self.assertEqual((order["status"], order["requested_amount"], order["requested_shares"]),
                         ("filled", D("1000.00"), None))
        self.assertEqual(order["filled_shares"], D("8.100445"))
        # 8.100445 x 123.45 = 999.9999..., which is 1000.00 to the cent.
        self.assertEqual(self.balance(), D("0.00"))

    def test_a_buy_that_takes_every_cent_fills(self):
        order = self.place(shares=20)
        self.assertEqual((order["status"], self.balance()), ("filled", D("0.00")))

    def test_a_buy_the_cash_cannot_cover_is_rejected_and_leaves_nothing(self):
        order = self.place(shares="20.01")   # 1000.50 of the 1000.00 there is

        self.assertEqual((order["status"], order["reject_reason"]), ("rejected", "insufficient_cash"))
        self.assertIsNone(order["fill_price"])
        self.assertEqual(order["resolved_at"], OPEN_NOW)
        self.assertEqual(self.open_lots(), [])
        self.assertEqual(self.balance(), D("1000.00"))

    def test_a_dollar_buy_for_more_than_the_cash_is_rejected(self):
        order = self.place(amount="1000.01")
        self.assertEqual((order["status"], order["reject_reason"]), ("rejected", "insufficient_cash"))

    def test_a_dollar_amount_too_small_for_any_share_is_rejected(self):
        self.gateway.quotes[SYMBOL] = quote(700000.0)
        order = self.place(amount="0.01")
        self.assertEqual((order["status"], order["reject_reason"]), ("rejected", "insufficient_cash"))
        self.assertEqual(self.open_lots(), [])

    def test_cash_spent_by_one_buy_is_gone_for_the_next(self):
        self.assertEqual(self.place(shares=12)["status"], "filled")      # 600
        second = self.place(shares=12)                                   # 600 of the 400 left
        third = self.place(shares=8)                                     # exactly 400

        self.assertEqual(second["reject_reason"], "insufficient_cash")
        self.assertEqual((third["status"], self.balance()), ("filled", D("0.00")))

    def test_a_fractional_share_count_is_rounded_once(self):
        order = self.place(shares="0.3333335")
        self.assertEqual((order["requested_shares"], order["filled_shares"]),
                         (D("0.333334"), D("0.333334")))


class SellTest(_Base):
    def setUp(self):
        super().setUp()
        self.first = self.place(shares=4)                       # 4 @ 50
        self.gateway.quotes[SYMBOL] = quote(60.0)
        self.second = self.place(shares=6)                      # 6 @ 60
        self.gateway.quotes[SYMBOL] = quote(70.0)

    def test_a_sell_takes_the_oldest_lots_first_and_records_its_source(self):
        order = self.place("sell", shares=5, source="stop", source_ref="stop:17")

        self.assertEqual((order["status"], order["filled_shares"], order["fill_price"]),
                         ("filled", D("5"), D("70")))
        sales = self.sales()
        self.assertEqual([(s["shares_sold"], s["sale_price"]) for s in sales],
                         [(D("4"), D("70")), (D("1"), D("70"))])
        for sale in sales:
            self.assertEqual(
                (sale["source"], sale["source_ref"], sale["order_id"], sale["allocation_method"]),
                ("stop", "stop:17", order["order_id"], "FIFO"),
            )
            self.assertEqual(str(sale["sale_trade_date"]), "2026-06-15")
        self.assertEqual(sum(lot["quantity"] for lot in self.open_lots()), D("5"))
        # 1000 - 200 - 360 + 350
        self.assertEqual(self.balance(), D("790.00"))

    def test_the_remainder_of_a_split_lot_keeps_the_order_that_bought_it(self):
        self.place("sell", shares=5)

        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT order_id FROM positions WHERE owner = %s AND status = 'OPEN'", (OWNER,)
            ).fetchall()
        self.assertEqual([r["order_id"] for r in rows], [self.second["order_id"]])

    def test_a_sell_of_everything_leaves_no_open_lot(self):
        order = self.place("sell", shares=10)
        self.assertEqual((order["status"], self.open_lots()), ("filled", []))

    def test_a_sell_for_more_than_is_held_is_rejected_and_leaves_nothing(self):
        order = self.place("sell", shares="10.000001")

        self.assertEqual((order["status"], order["reject_reason"]), ("rejected", "insufficient_shares"))
        self.assertEqual(self.sales(), [])
        self.assertEqual(sum(lot["quantity"] for lot in self.open_lots()), D("10"))

    def test_a_sell_of_a_symbol_the_book_does_not_hold_is_rejected(self):
        self.gateway.quotes["ZZNOTHELD"] = quote()
        order = self.place("sell", symbol="ZZNOTHELD", shares=1)
        self.assertEqual(order["reject_reason"], "insufficient_shares")

    def test_shares_promised_to_a_queued_sell_cannot_be_sold_twice(self):
        queued = self.place("sell", shares=8, now=EVENING)
        too_many = self.place("sell", shares=3, now=EVENING)
        fits = self.place("sell", shares=2)

        self.assertEqual(queued["status"], "pending")
        self.assertEqual((too_many["status"], too_many["reject_reason"]),
                         ("rejected", "insufficient_shares"))
        self.assertEqual(fits["status"], "filled")

    def test_a_sale_the_owner_records_is_manual_with_no_order(self):
        (lot, *_rest) = self.open_lots()
        self.lots.close_lots(OWNER, [(lot["lot_id"], D("1"))], D("65"), "2026-06-15", book_id=self.book)

        (sale,) = self.sales()
        self.assertEqual((sale["source"], sale["source_ref"], sale["order_id"]), ("manual", None, None))


class RefusalTest(_Base):
    def _assert_rejected(self, order, reason):
        self.assertEqual((order["status"], order["reject_reason"]), ("rejected", reason))
        self.assertEqual(self.open_lots(), [])
        self.assertEqual(self.balance(), D("1000.00"))

    def test_no_quote(self):
        self.gateway.quotes[SYMBOL] = None
        self._assert_rejected(self.place(), "no_quote")

    def test_a_stale_quote(self):
        self.gateway.quotes[SYMBOL] = quote(age=301)
        self._assert_rejected(self.place(), "stale_quote")

    def test_a_non_usd_symbol(self):
        self.gateway.quotes[SYMBOL] = quote(currency="SEK")
        self._assert_rejected(self.place(), "non_usd")
        self._assert_rejected(self.place(now=EVENING), "non_usd")

    def test_a_rejected_order_is_listed_with_its_reason(self):
        self.gateway.quotes[SYMBOL] = None
        order = self.place()

        listed = self.service.list_orders(owner=OWNER, book_id=self.book)
        self.assertEqual((listed["book_kind"], listed["pending_count"]), ("paper", 0))
        self.assertEqual([(o["order_id"], o["reject_reason"]) for o in listed["orders"]],
                         [(order["order_id"], "no_quote")])


class QueueTest(_Base):
    def test_an_order_after_the_close_waits_for_the_next_session(self):
        self.gateway.quotes[SYMBOL] = quote(state="POST", age=4 * 3600)

        order = self.place(shares=2, now=EVENING)

        self.assertEqual((order["status"], order["session_date"]), ("pending", "2026-06-16"))
        self.assertIsNone(order["resolved_at"])
        self.assertIsNone(order["fill_price"])
        self.assertEqual(self.open_lots(), [])
        self.assertEqual(self.balance(), D("1000.00"))
        self.assertEqual(self.service.list_orders(owner=OWNER, book_id=self.book)["pending_count"], 1)

    def test_a_queued_share_buy_is_not_checked_against_the_cash(self):
        # Its cost isn't known until the open (plan decision 28).
        order = self.place(shares=10_000, now=EVENING)
        self.assertEqual(order["status"], "pending")

    def test_a_queued_dollar_buy_for_more_than_the_cash_is_rejected_now(self):
        order = self.place(amount="1000.01", now=EVENING)
        self.assertEqual((order["status"], order["reject_reason"]), ("rejected", "insufficient_cash"))

    def test_a_holiday_queues_though_the_clock_says_open(self):
        holiday = ET.localize(datetime.datetime(2026, 7, 3, 11, 0))
        self.gateway.quotes[SYMBOL] = quote(state="CLOSED", now=holiday, age=19 * 3600)

        order = self.place(now=holiday)

        self.assertEqual((order["status"], order["session_date"]), ("pending", "2026-07-06"))

    def test_a_queued_order_can_be_cancelled_once(self):
        order = self.place(now=EVENING)

        cancelled = self.service.cancel_order(
            owner=OWNER, order_id=order["order_id"], book_id=self.book, now=EVENING
        )

        self.assertEqual((cancelled["status"], cancelled["resolved_at"]), ("cancelled", EVENING))
        with self.assertRaisesRegex(ValueError, "only a pending order"):
            self.service.cancel_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)

    def test_a_filled_order_cannot_be_cancelled(self):
        order = self.place()

        with self.assertRaisesRegex(ValueError, "this one is filled"):
            self.service.cancel_order(owner=OWNER, order_id=order["order_id"], book_id=self.book)
        self.assertEqual(len(self.open_lots()), 1)

    def test_cancelling_an_order_that_is_not_in_the_book_is_not_found(self):
        order = self.place(now=EVENING)
        second = self.books.create_paper_book(owner=OWNER, name="Second", starting_cash=0)

        for book_id in (second, None):
            with self.subTest(book_id=book_id), self.assertRaises(OrderNotFoundError):
                self.service.cancel_order(owner=OWNER, order_id=order["order_id"], book_id=book_id)
        with self.assertRaises(OrderNotFoundError):
            self.service.cancel_order(owner=OWNER, order_id=999_999_999, book_id=self.book)


class IdempotencyTest(_Base):
    def test_the_same_key_twice_fills_once(self):
        first = self.place(shares=2, idempotency_key="click")
        again = self.place(shares=2, idempotency_key="click")

        self.assertEqual((again["order_id"], again["replayed"]), (first["order_id"], True))
        self.assertEqual(again["status"], "filled")
        self.assertEqual(len(self.open_lots()), 1)
        self.assertEqual(self.balance(), D("900.00"))

    def test_a_repeat_is_answered_without_asking_for_a_quote(self):
        self.place(idempotency_key="click")
        calls = self.gateway.calls
        self.place(idempotency_key="click")
        self.assertEqual(self.gateway.calls, calls)

    def test_a_repeat_that_slips_past_the_first_look_still_fills_once(self):
        # Two requests that both passed the look-up before either had written:
        # the insert's own conflict check is what decides.
        self.place(shares=2, idempotency_key="click")
        with patch.object(self.repo, "get_order_by_key", return_value=None):
            again = self.place(shares=2, idempotency_key="click")

        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.open_lots()), 1)

    def test_a_key_from_another_book_is_bad_input(self):
        second = self.books.create_paper_book(owner=OWNER, name="Second", starting_cash=500,
                                              opened_on=OPENED_ON)
        self.place(idempotency_key="click")

        with self.assertRaisesRegex(ValueError, "another book"):
            self.place(idempotency_key="click", book_id=second)
        self.assertEqual(self.balance(second), D("500.00"))


class ValidationTest(_Base):
    def _assert_refused(self, pattern, **kwargs):
        with self.assertRaisesRegex(ValueError, pattern):
            self.place(**kwargs)
        self.assertEqual(self.service.list_orders(owner=OWNER, book_id=self.book)["orders"], [])
        self.assertEqual(self.gateway.calls, 0)

    def test_bad_input_is_refused_before_anything_is_stored_or_fetched(self):
        cases = [
            ("not both", {"shares": 1, "amount": 5}),
            ("not both", {"shares": None, "amount": None}),
            ("a sell is for a number of shares", {"side": "sell", "shares": None, "amount": 5}),
            ("side must be", {"side": "short"}),
            ("more than zero", {"shares": 0}),
            ("more than zero", {"shares": "0.0000004"}),
            ("more than zero", {"amount": "0.004"}),
            ("more than zero", {"shares": -1}),
            ("finite", {"shares": "NaN"}),
            ("finite", {"amount": "Infinity"}),
            ("not a valid number", {"shares": "ten"}),
            ("too large", {"shares": "1e13"}),
            # The first whole count NUMERIC(18,6) can't hold.
            ("too large", {"shares": "1000000000000"}),
            ("too large", {"amount": "1e13"}),
            ("valid ticker", {"symbol": ""}),
            ("valid ticker", {"symbol": "DROP TABLE"}),
            ("valid ticker", {"symbol": "A" * 16}),
            ("idempotency key", {"idempotency_key": "  "}),
            ("at most", {"idempotency_key": "k" * (MAX_KEY_LENGTH + 1)}),
            ("source must be", {"source": "whim"}),
            ("source_ref", {"source_ref": "r" * 201}),
        ]
        for pattern, kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self._assert_refused(pattern, **kwargs)

    def test_the_largest_share_count_the_column_holds_is_stored(self):
        order = self.place(shares=MAX_ORDER_SHARES, now=EVENING)
        self.assertEqual((order["status"], order["requested_shares"]), ("pending", MAX_ORDER_SHARES))

    def test_a_dollar_buy_for_more_shares_than_fit_is_bad_input_and_stores_nothing(self):
        big = self.books.create_paper_book(
            owner=OWNER, name="Big", starting_cash="1000000000000", opened_on=OPENED_ON
        )
        self.gateway.quotes[SYMBOL] = quote(0.5)

        with self.assertRaisesRegex(ValueError, "more shares than an order can hold"):
            self.place(amount="1000000000000", book_id=big)

        self.assertEqual(self.service.list_orders(owner=OWNER, book_id=big)["orders"], [])
        self.assertEqual(self.lots.list_positions(OWNER, book_id=big), [])
        self.assertEqual(self.balance(big), D("1000000000000.00"))

    def test_the_real_book_takes_no_orders(self):
        real = self.books.ensure_real_book(owner=OWNER)
        for book_id in (None, real):
            with self.subTest(book_id=book_id), self.assertRaisesRegex(ValueError, "paper books"):
                self.service.place_order(
                    owner=OWNER, symbol=SYMBOL, side="buy", shares=1, idempotency_key="r",
                    book_id=book_id, now=OPEN_NOW,
                )
        self.assertEqual(self.lots.list_positions(OWNER), [])

    def test_an_archived_book_takes_no_orders(self):
        self.books.archive_book(owner=OWNER, book_id=self.book)
        self._assert_refused("archived")

    def test_another_owners_book_is_not_found(self):
        with self.assertRaises(BookNotFoundError):
            self.place(owner=OTHER)
        with self.assertRaises(BookNotFoundError):
            self.service.list_orders(owner=OTHER, book_id=self.book)
        with self.assertRaises(BookNotFoundError):
            self.service.cancel_order(owner=OTHER, order_id=1, book_id=self.book)

    def test_listing_the_real_book_reads_nothing_and_creates_no_book(self):
        listed = self.service.list_orders(owner=OTHER)

        self.assertEqual(listed, {"book_kind": "real", "pending_count": 0, "orders": []})
        self.assertEqual(self.books.list_books(owner=OTHER), [])

    def test_a_symbol_is_upper_cased(self):
        self.assertEqual(self.place(symbol=SYMBOL.lower())["symbol"], SYMBOL)


class OrdersRoutesTest(_Base):
    def setUp(self):
        super().setUp()
        identities = OwnerIdentityRepository()
        identities.upsert(identity=IDENTITY, owner=OWNER)
        identities.upsert(identity=OTHER_IDENTITY, owner=OTHER)
        self.app = create_app()
        self.client = TestClient(self.app)
        self.as_identity(IDENTITY)
        services = patch("api.routers.orders.services")
        services.start().return_value.orders = self.service
        self.addCleanup(services.stop)
        clock = patch("quantcore.services.orders.datetime")
        frozen = clock.start()
        frozen.datetime.now.return_value = OPEN_NOW
        frozen.timezone = datetime.timezone
        self.frozen = frozen
        self.addCleanup(clock.stop)

    def as_identity(self, identity):
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal

    def post(self, **body):
        body = {"book_id": self.book, "symbol": SYMBOL, "side": "buy", "idempotency_key": "k1", **body}
        return self.client.post("/api/portfolio/orders", json=body)

    def test_place_list_and_replay(self):
        placed = self.post(shares="2")
        self.assertEqual(placed.status_code, 201)
        body = placed.json()
        self.assertEqual((body["status"], body["filled_shares"], body["fill_price"]), ("filled", 2, 50))
        self.assertNotIn("idempotency_key", body)

        again = self.post(shares="2")
        self.assertEqual((again.status_code, again.json()["replayed"]), (200, True))
        self.assertEqual(again.json()["order_id"], body["order_id"])

        listed = self.client.get("/api/portfolio/orders", params={"book_id": self.book})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([o["order_id"] for o in listed.json()["orders"]], [body["order_id"]])

    def test_a_rejected_order_is_a_201_with_its_reason(self):
        self.gateway.quotes[SYMBOL] = quote(currency="SEK")
        placed = self.post(amount="100")
        self.assertEqual(placed.status_code, 201)
        self.assertEqual((placed.json()["status"], placed.json()["reject_reason"]),
                         ("rejected", "non_usd"))

    def test_cancel(self):
        self.frozen.datetime.now.return_value = EVENING
        order = self.post(shares="1").json()
        self.assertEqual(order["status"], "pending")

        url = f"/api/portfolio/orders/{order['order_id']}/cancel"
        cancelled = self.client.post(url, params={"book_id": self.book})
        self.assertEqual((cancelled.status_code, cancelled.json()["status"]), (200, "cancelled"))

        again = self.client.post(url, params={"book_id": self.book})
        self.assertEqual(again.status_code, 422)
        missing = self.client.post("/api/portfolio/orders/999999999/cancel", params={"book_id": self.book})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 422)  # book_id is required

    def test_bad_input_is_a_422(self):
        self.assertEqual(self.post(shares="1", amount="5").status_code, 422)
        self.assertEqual(self.post().status_code, 422)
        self.assertEqual(self.post(side="short", shares="1").status_code, 422)
        self.assertEqual(self.post(shares="1", symbol="NOT A TICKER").status_code, 422)
        real = self.books.ensure_real_book(owner=OWNER)
        refused = self.post(shares="1", book_id=real)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("paper books", refused.json()["error"])

    def test_the_route_takes_no_price_source_or_owner(self):
        placed = self.post(shares="1", source="ai", fill_price="1", owner=OTHER, price="1")
        self.assertEqual(placed.status_code, 201)
        body = placed.json()
        self.assertEqual((body["source"], body["fill_price"]), ("manual", 50))
        self.assertEqual(self.service.list_orders(owner=OTHER)["orders"], [])

    def test_another_owner_gets_a_404_for_every_route(self):
        order = self.post(shares="1").json()
        self.as_identity(OTHER_IDENTITY)

        self.assertEqual(
            self.client.get("/api/portfolio/orders", params={"book_id": self.book}).status_code, 404
        )
        self.assertEqual(self.post(shares="1", idempotency_key="theirs").status_code, 404)
        cancel = self.client.post(
            f"/api/portfolio/orders/{order['order_id']}/cancel", params={"book_id": self.book}
        )
        self.assertEqual(cancel.status_code, 404)
        self.assertEqual(self.balance(), D("950.00"))

    def test_listing_with_no_book_is_the_real_book(self):
        listed = self.client.get("/api/portfolio/orders")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), {"book_kind": "real", "pending_count": 0, "orders": []})


if __name__ == "__main__":
    unittest.main()
