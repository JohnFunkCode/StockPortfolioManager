"""The stop a paper buy asks for (#431 plan step 4.3).

`analytics.stored_stops.requested_stop` and `order_stop_terms` as pure
functions, then `OrdersService` against the real repositories and the test
database with a stub in place of `YFinanceGateway` (the suite never reaches
Yahoo), the order route through TestClient, and the realized read's `source`.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import psycopg2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from quantcore.analytics import stored_stops as stops_math  # noqa: E402
from quantcore.analytics.market_time import ET  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import BooksRepository  # noqa: E402
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.orders_repository import OrdersRepository  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.repositories.stops_repository import StopsRepository  # noqa: E402
from quantcore.services.cash import CashService  # noqa: E402
from quantcore.services.orders import OrdersService  # noqa: E402
from quantcore.services.stops import StopsService  # noqa: E402

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

from tests.test_orders_api import StubGateway, quote  # noqa: E402
from tests.test_stops_repository import purge  # noqa: E402

OWNER = "zz_order_stops_owner"
OTHER = "zz_order_stops_other"
IDENTITY = "zz_order_stops_owner@example.com"
SYMBOL = "ZZORDSTOP"
D = Decimal

# Monday 2026-06-15, 11:00 ET is in the session; 20:00 is after the close, and
# an order placed then belongs to Tuesday's session.
OPEN_NOW = ET.localize(datetime.datetime(2026, 6, 15, 11, 0))
EVENING = ET.localize(datetime.datetime(2026, 6, 15, 20, 0))
MONDAY = datetime.date(2026, 6, 15)
SESSION = datetime.date(2026, 6, 16)
JOB_NOW = ET.localize(datetime.datetime(2026, 6, 16, 18, 0))
OPENED_ON = datetime.date(2026, 6, 1)


class RequestedStopTest(unittest.TestCase):
    def test_a_fixed_stop_keeps_its_level_to_the_tick(self):
        asked = stops_math.requested_stop("fixed", stop_level=D("45.126"))
        self.assertEqual(
            (asked.kind, asked.stop_level, asked.trail_pct, asked.method, asked.proposed_level),
            ("fixed", D("45.13"), None, "manual", None),
        )

    def test_a_trailing_stop_keeps_its_percentage_and_has_no_level(self):
        asked = stops_math.requested_stop(
            "trailing", trail_pct=D("8.12345"), method="trailing", proposed_level=D("46.004"))
        self.assertEqual(
            (asked.kind, asked.stop_level, asked.trail_pct, asked.method, asked.proposed_level),
            ("trailing", None, D("8.1235"), "trailing", D("46.00")),
        )

    def test_what_is_wrong_with_a_request_is_said(self):
        cases = [
            ("kind must be fixed or trailing", dict(kind="limit", stop_level=D("1"))),
            ("method must be one of", dict(kind="fixed", stop_level=D("1"), method="hunch")),
            ("needs a stop_level", dict(kind="fixed")),
            ("not a trail_pct", dict(kind="fixed", stop_level=D("1"), trail_pct=D("5"))),
            ("stop_level must be more than zero", dict(kind="fixed", stop_level=D("0"))),
            ("stop_level is too large", dict(kind="fixed", stop_level=D("1e12"))),
            ("too small to be a price", dict(kind="fixed", stop_level=D("0.00001"))),
            ("needs a trail_pct", dict(kind="trailing")),
            ("not a stop_level", dict(kind="trailing", stop_level=D("1"), trail_pct=D("5"))),
            ("more than 0 and less than 100", dict(kind="trailing", trail_pct=D("100"))),
            ("proposed_level must be more than zero",
             dict(kind="fixed", stop_level=D("1"), proposed_level=D("-1"))),
        ]
        for pattern, kwargs in cases:
            kind = kwargs.pop("kind")
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                stops_math.requested_stop(kind, **kwargs)


class OrderStopTermsTest(unittest.TestCase):
    def test_a_fixed_level_under_the_fill_is_the_stop(self):
        terms = stops_math.order_stop_terms(
            "fixed", stop_level=D("45"), trail_pct=None, fill=D("50"))
        self.assertEqual((terms.kind, terms.stop_level, terms.high_water_mark),
                         ("fixed", D("45.00"), None))

    def test_a_fixed_level_at_or_above_the_fill_is_no_stop(self):
        for fill in ("45", "44.99", "12"):
            with self.subTest(fill=fill):
                self.assertIsNone(stops_math.order_stop_terms(
                    "fixed", stop_level=D("45"), trail_pct=None, fill=D(fill)))

    def test_a_trailing_stop_starts_from_the_fill(self):
        terms = stops_math.order_stop_terms(
            "trailing", stop_level=None, trail_pct=D("10"), fill=D("50"))
        self.assertEqual((terms.stop_level, terms.trail_pct, terms.high_water_mark),
                         (D("45.00"), D("10"), D("50")))

    def test_a_trailing_level_that_rounds_up_to_the_fill_is_no_stop(self):
        self.assertIsNone(stops_math.order_stop_terms(
            "trailing", stop_level=None, trail_pct=D("0.0001"), fill=D("50")))


class StubPrices:
    """`session_open` from one figure: what the queued-fill step reads."""

    def __init__(self):
        self.open = 51.0

    def session_open(self, symbol, session):
        return {"open": self.open, "latest_bar": session}


def _purge():
    purge((OWNER, OTHER), (SYMBOL,))
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM owner_identities WHERE identity = %s", (IDENTITY,))
        conn.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.repo = OrdersRepository(self.lots)
        self.stops = StopsRepository(self.lots)
        self.gateway = StubGateway()
        self.gateway.quotes = {SYMBOL: quote(50.0)}
        self.prices = StubPrices()
        self.service = OrdersService(self.repo, self.books, self.gateway, self.prices)
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

    def queue(self, **kwargs):
        self.gateway.quotes[SYMBOL] = quote(state="POST", now=EVENING, age=4 * 3600)
        order = self.place(now=EVENING, **kwargs)
        self.assertEqual(order["status"], "pending")
        return order

    def fill(self, order):
        return self.service.fill_queued_order(
            owner=OWNER, book_id=self.book, order_id=order["order_id"],
            today=SESSION, now=JOB_NOW)

    def all_stops(self, owner=OWNER, book_id="paper"):
        return self.stops.list_stops(
            owner=owner, book_id=self.book if book_id == "paper" else book_id)

    def order_count(self):
        return len(self.repo.list_orders(owner=OWNER, book_id=self.book))


class StopOnAFilledBuyTest(_Base):
    def test_a_fixed_stop_is_written_with_the_fill(self):
        order = self.place(stop_kind="fixed", stop_level="45", stop_method="technical",
                           stop_proposed_level="44.5")

        self.assertEqual(
            (order["status"], order["stop_set"], order["stop_kind"], order["stop_level"],
             order["stop_trail_pct"], order["stop_method"], order["stop_proposed_level"]),
            ("filled", True, "fixed", D("45"), None, "technical", D("44.5")),
        )
        (stop,) = self.all_stops()
        self.assertEqual(
            (stop["symbol"], stop["status"], stop["kind"], stop["stop_level"], stop["method"],
             stop["proposed_level"], stop["reference_price"], stop["as_of"], stop["source"],
             stop["source_ref"], stop["created_at"]),
            (SYMBOL, "active", "fixed", D("45"), "technical", D("44.5"), D("50"), "2026-06-15",
             "manual", f"order:{order['order_id']}", OPEN_NOW),
        )
        self.assertIsNone(stop["last_bar_checked"])

    def test_a_trailing_stop_starts_from_the_fill_price(self):
        order = self.place(stop_kind="trailing", stop_trail_pct="10")

        self.assertEqual((order["stop_set"], order["stop_method"]), (True, "manual"))
        (stop,) = self.all_stops()
        self.assertEqual(
            (stop["kind"], stop["trail_pct"], stop["high_water_mark"], stop["stop_level"]),
            ("trailing", D("10"), D("50"), D("45")),
        )

    def test_a_stop_from_a_fill_in_market_hours_is_first_checked_against_the_next_session(self):
        # A daily bar can't say whether its low came before 11:00 or after.
        self.place(stop_kind="fixed", stop_level="45")
        (stop,) = self.all_stops()
        self.assertIsNone(stop["first_session"])
        self.assertEqual(
            stops_math.first_session(stop["created_at"], live_from=stop["first_session"]),
            SESSION)

    def test_a_buy_with_no_stop_writes_none(self):
        order = self.place()
        self.assertEqual((order["status"], order["stop_kind"], order["stop_set"]),
                         ("filled", None, None))
        self.assertEqual(self.all_stops(), [])

    def test_a_second_buy_with_a_stop_replaces_the_symbols_stop(self):
        first = self.place(stop_kind="fixed", stop_level="45")
        second = self.place(stop_kind="fixed", stop_level="47")

        by_ref = {s["source_ref"]: s for s in self.all_stops()}
        self.assertEqual(by_ref[f"order:{first['order_id']}"]["status"], "replaced")
        self.assertEqual(
            (by_ref[f"order:{second['order_id']}"]["status"],
             by_ref[f"order:{second['order_id']}"]["stop_level"]),
            ("active", D("47")),
        )

    def test_a_second_buy_with_no_stop_leaves_the_symbols_stop(self):
        self.place(stop_kind="fixed", stop_level="45")
        self.place()
        self.assertEqual([s["status"] for s in self.all_stops()], ["active"])

    def test_a_fixed_level_at_or_above_the_fill_price_is_bad_input_and_stores_nothing(self):
        for level in ("50", "62.5"):
            with self.subTest(level=level), self.assertRaisesRegex(
                ValueError, "below the price the order fills at, which is 50.00"
            ):
                self.place(stop_kind="fixed", stop_level=level)
        self.assertEqual(self.order_count(), 0)
        self.assertEqual(self.all_stops(), [])
        self.assertEqual(self.lots.list_lots_for_symbol(OWNER, SYMBOL, book_id=self.book), [])

    def test_a_trail_too_small_to_give_a_level_is_bad_input_and_says_so(self):
        # 0.0001% under $50.00 rounds back to $50.00: the owner gave no level
        # to be "below", so the message is about the trail.
        with self.assertRaisesRegex(ValueError, "the trail is too small.*which is 50.00"):
            self.place(stop_kind="trailing", stop_trail_pct="0.0001")
        self.assertEqual(self.order_count(), 0)
        self.assertEqual(self.all_stops(), [])

    def test_a_buy_the_cash_cannot_cover_gets_no_stop(self):
        order = self.place(shares=500, stop_kind="fixed", stop_level="45")
        self.assertEqual((order["status"], order["reject_reason"], order["stop_set"]),
                         ("rejected", "insufficient_cash", None))
        self.assertEqual(self.all_stops(), [])

    def test_a_buy_rejected_for_its_quote_gets_no_stop_and_is_not_bad_input(self):
        self.gateway.quotes[SYMBOL] = None
        order = self.place(stop_kind="fixed", stop_level="999")
        self.assertEqual((order["status"], order["stop_kind"], order["stop_set"]),
                         ("rejected", "fixed", None))
        self.assertEqual(self.all_stops(), [])

    def test_a_replayed_key_writes_no_second_stop(self):
        first = self.place(idempotency_key="same", stop_kind="fixed", stop_level="45")
        again = self.place(idempotency_key="same", stop_kind="fixed", stop_level="45")
        self.assertEqual((again["replayed"], again["order_id"]), (True, first["order_id"]))
        self.assertEqual(len(self.all_stops()), 1)

    def test_the_stop_is_in_the_buyers_book_and_nobody_elses(self):
        self.place(stop_kind="fixed", stop_level="45")
        self.assertEqual(self.all_stops(book_id=None), [])       # the owner's real book
        self.assertEqual(self.all_stops(owner=OTHER, book_id=None), [])
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT owner, book_id FROM stops WHERE owner IN (%s, %s)", (OWNER, OTHER)
            ).fetchall()
        self.assertEqual([(r["owner"], r["book_id"]) for r in rows], [(OWNER, self.book)])


class BadStopRequestTest(_Base):
    def test_bad_stop_terms_are_refused_before_anything_is_stored_or_fetched(self):
        cases = [
            ("only a buy can carry a stop", dict(side="sell", stop_kind="fixed", stop_level="45")),
            ("a stop needs a kind", dict(stop_level="45")),
            ("a stop needs a kind", dict(stop_trail_pct="5")),
            ("a stop needs a kind", dict(stop_method="technical")),
            ("a stop needs a kind", dict(stop_proposed_level="45")),
            ("kind must be fixed or trailing", dict(stop_kind="limit", stop_level="45")),
            ("needs a stop_level", dict(stop_kind="fixed")),
            ("stop_level is not a valid number", dict(stop_kind="fixed", stop_level="abc")),
            ("stop_level must be a finite number", dict(stop_kind="fixed", stop_level="NaN")),
            ("more than 0 and less than 100", dict(stop_kind="trailing", stop_trail_pct="0")),
            ("method must be one of",
             dict(stop_kind="fixed", stop_level="45", stop_method="hunch")),
        ]
        for pattern, kwargs in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                self.place(**kwargs)
        self.assertEqual((self.gateway.calls, self.order_count()), (0, 0))


class StopOnAQueuedBuyTest(_Base):
    def test_a_queued_buy_has_no_stop_until_it_fills(self):
        order = self.queue(stop_kind="fixed", stop_level="45")
        self.assertEqual((order["stop_kind"], order["stop_level"], order["stop_set"]),
                         ("fixed", D("45"), None))
        self.assertEqual(self.all_stops(), [])

        filled = self.fill(order)

        self.assertEqual((filled["status"], filled["fill_price"], filled["stop_set"]),
                         ("filled", D("51"), True))
        (stop,) = self.all_stops()
        self.assertEqual(
            (stop["stop_level"], stop["reference_price"], stop["as_of"], stop["created_at"],
             stop["source_ref"]),
            (D("45"), D("51"), "2026-06-16", JOB_NOW, f"order:{order['order_id']}"),
        )
        # It filled at the open, so the stop is live from that session's bar.
        self.assertEqual(stop["first_session"], SESSION)

    def test_a_queued_trailing_stop_starts_from_the_open_it_filled_at(self):
        filled = self.fill(self.queue(stop_kind="trailing", stop_trail_pct="10"))
        self.assertTrue(filled["stop_set"])
        (stop,) = self.all_stops()
        self.assertEqual((stop["high_water_mark"], stop["stop_level"]), (D("51"), D("45.9")))

    def test_a_buy_that_opens_at_or_under_its_level_is_rejected_and_opens_no_position(self):
        # Decision 64: no position without the stop it was asked for.
        for opened in (45.0, 40.0):
            with self.subTest(open=opened):
                self.prices.open = opened
                rejected = self.fill(self.queue(shares=1, stop_kind="fixed", stop_level="45"))
                self.assertEqual(
                    (rejected["status"], rejected["reject_reason"], rejected["stop_set"],
                     rejected["filled_shares"]),
                    ("rejected", "stop_not_below_fill", None, None))
        self.assertEqual(self.all_stops(), [])
        self.assertEqual(self.lots.list_lots_for_symbol(OWNER, SYMBOL, book_id=self.book), [])
        self.assertEqual(
            CashService(CashRepository(), self.books).ledger(
                owner=OWNER, book_id=self.book)["balance"], D("1000.00"))

    def test_the_stop_is_checked_before_the_cash(self):
        self.prices.open = 40.0
        rejected = self.fill(self.queue(shares=500, stop_kind="fixed", stop_level="45"))
        self.assertEqual(rejected["reject_reason"], "stop_not_below_fill")

    def test_a_queued_trail_too_small_to_give_a_level_is_rejected(self):
        rejected = self.fill(self.queue(stop_kind="trailing", stop_trail_pct="0.0001"))
        self.assertEqual((rejected["status"], rejected["reject_reason"]),
                         ("rejected", "stop_not_below_fill"))
        self.assertEqual(self.all_stops(), [])
        self.assertEqual(self.lots.list_lots_for_symbol(OWNER, SYMBOL, book_id=self.book), [])

    def test_a_cancelled_order_never_gets_its_stop(self):
        order = self.queue(stop_kind="fixed", stop_level="45")
        cancelled = self.service.cancel_order(
            owner=OWNER, book_id=self.book, order_id=order["order_id"], now=JOB_NOW)
        self.assertEqual((cancelled["status"], cancelled["stop_set"]), ("cancelled", None))
        self.assertEqual(self.all_stops(), [])

    def test_a_queued_buy_rejected_at_the_open_never_gets_its_stop(self):
        order = self.queue(shares=500, stop_kind="fixed", stop_level="45")
        rejected = self.fill(order)
        self.assertEqual((rejected["status"], rejected["reject_reason"], rejected["stop_set"]),
                         ("rejected", "insufficient_cash", None))
        self.assertEqual(self.all_stops(), [])

    def test_filling_twice_writes_one_stop(self):
        order = self.queue(stop_kind="fixed", stop_level="45")
        self.fill(order)
        self.fill(order)
        self.assertEqual(len(self.all_stops()), 1)


class StopLiveOnTheFillSessionTest(_Base):
    """Plan decision 63: a buy that filled at its session's open has a stop
    that the checker tests against that session's own bar, as a broker's
    attached stop is live once the entry fills."""

    def setUp(self):
        super().setUp()
        self.bars = []
        self.prices.closed_bars_since = lambda symbol, since: [
            b for b in self.bars if b["session"] >= since]
        self.checker = StopsService(self.stops, self.books, self.prices, self.service)

    def filled_with_stop(self, **stop):
        stop = stop or {"stop_kind": "fixed", "stop_level": "45"}
        self.fill(self.queue(**stop))
        (row,) = self.all_stops()
        return row

    def check(self, stop, today=SESSION, now=JOB_NOW):
        return self.checker.check_stop(
            owner=OWNER, book_id=self.book, stop_id=stop["stop_id"], today=today, now=now)

    def due(self, today):
        return [s["stop_id"] for s in self.checker.due_stops(today=today) if s["owner"] == OWNER]

    def test_the_stop_is_due_the_night_its_buy_fills(self):
        stop = self.filled_with_stop()
        self.assertEqual(self.due(SESSION), [stop["stop_id"]])

    def test_a_low_under_the_level_on_the_fill_day_sells_at_the_level_that_day(self):
        stop = self.filled_with_stop()
        self.bars = [{"session": SESSION, "open": 51.0, "high": 52.0, "low": 44.0, "close": 46.0}]

        result = self.check(stop)

        self.assertEqual(
            (result["outcome"], result["status"], result["triggered_session"],
             result["trigger_price"]),
            ("triggered", "triggered", SESSION, D("45")))
        self.assertEqual(self.lots.list_lots_for_symbol(OWNER, SYMBOL, book_id=self.book), [])
        (sale,) = self.lots.list_realized_sales(OWNER, book_id=self.book)
        self.assertEqual(
            (sale["source"], sale["sale_price"], sale["sale_trade_date"], sale["trade_date"]),
            ("stop", D("45"), "2026-06-16", "2026-06-16"))

    def test_a_fill_day_that_holds_is_recorded_and_a_trail_rises_on_its_high(self):
        stop = self.filled_with_stop(stop_kind="trailing", stop_trail_pct="10")
        self.bars = [{"session": SESSION, "open": 51.0, "high": 60.0, "low": 50.0, "close": 59.0}]

        result = self.check(stop)

        self.assertEqual(
            (result["outcome"], result["last_bar_checked"], result["high_water_mark"],
             result["stop_level"]),
            ("checked", SESSION, D("60"), D("54")))

    def test_a_fill_recorded_days_late_is_still_tested_from_its_own_session(self):
        late = ET.localize(datetime.datetime(2026, 6, 18, 18, 0))
        order = self.queue(stop_kind="fixed", stop_level="45")
        self.service.fill_queued_order(
            owner=OWNER, book_id=self.book, order_id=order["order_id"],
            today=datetime.date(2026, 6, 18), now=late)
        (stop,) = self.all_stops()
        self.bars = [
            {"session": SESSION, "open": 51.0, "high": 52.0, "low": 50.0, "close": 51.0},
            {"session": datetime.date(2026, 6, 17), "open": 44.0, "high": 46.0, "low": 43.0,
             "close": 45.0},
            {"session": datetime.date(2026, 6, 18), "open": 60.0, "high": 61.0, "low": 59.0,
             "close": 60.0},
        ]

        result = self.check(stop, today=datetime.date(2026, 6, 18), now=late)

        # Wednesday opened under the level: sold at that open, dated Wednesday.
        self.assertEqual(
            (result["outcome"], result["triggered_session"], result["trigger_price"]),
            ("triggered", datetime.date(2026, 6, 17), D("44")))

    def test_a_stop_from_a_fill_in_market_hours_is_not_due_that_night(self):
        self.gateway.quotes[SYMBOL] = quote(50.0)
        self.place(stop_kind="fixed", stop_level="45")
        (stop,) = self.all_stops()
        self.bars = [{"session": MONDAY, "open": 50.0, "high": 51.0, "low": 40.0, "close": 50.0}]

        self.assertEqual(self.due(MONDAY), [])
        self.assertEqual(self.check(stop, today=MONDAY)["outcome"], "waiting")
        self.assertEqual(self.due(SESSION), [stop["stop_id"]])


class LiveFromRuleTest(unittest.TestCase):
    def test_live_from_replaces_the_first_session_and_nothing_else(self):
        self.assertEqual(stops_math.first_session(JOB_NOW), datetime.date(2026, 6, 17))
        self.assertEqual(stops_math.first_session(JOB_NOW, live_from=SESSION), SESSION)
        self.assertEqual(stops_math.next_bar_date(JOB_NOW, None, live_from=SESSION), SESSION)
        self.assertEqual(
            stops_math.next_bar_date(JOB_NOW, SESSION, live_from=SESSION),
            datetime.date(2026, 6, 17))
        self.assertTrue(stops_math.is_due(JOB_NOW, None, SESSION, live_from=SESSION))
        self.assertFalse(stops_math.is_due(JOB_NOW, None, SESSION))
        self.assertFalse(stops_math.is_due(JOB_NOW, SESSION, SESSION, live_from=SESSION))


class DatabaseRuleTest(_Base):
    def _insert(self, **columns):
        row = {
            "side": "buy", "kind": "market", "stop_kind": None, "stop_level": None,
            "stop_trail_pct": None, "stop_method": None, "stop_set": None, **columns,
        }
        with closing(get_connection()) as conn:
            try:
                conn.execute(
                    "INSERT INTO symbols (ticker, created_at) VALUES (%s, %s) "
                    "ON CONFLICT(ticker) DO NOTHING", (SYMBOL, OPEN_NOW.isoformat()))
                conn.execute(
                    "INSERT INTO orders (owner, book_id, symbol_id, side, requested_shares, "
                    "source, idempotency_key, session_date, placed_at, kind, stop_kind, "
                    "stop_level, stop_trail_pct, stop_method, stop_set) VALUES (%s, %s, "
                    "(SELECT symbol_id FROM symbols WHERE ticker = %s), %s, 1, 'manual', %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s)",
                    (OWNER, self.book, SYMBOL, row["side"], "rule", SESSION, OPEN_NOW,
                     row["kind"], row["stop_kind"], row["stop_level"], row["stop_trail_pct"],
                     row["stop_method"], row["stop_set"]),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def test_the_database_refuses_what_the_service_would(self):
        bad = [
            dict(side="sell", stop_kind="fixed", stop_level=45, stop_method="manual"),
            dict(kind="seed", stop_kind="fixed", stop_level=45, stop_method="manual"),
            dict(stop_kind="fixed", stop_method="manual"),
            dict(stop_kind="fixed", stop_level=45, stop_trail_pct=5, stop_method="manual"),
            dict(stop_kind="trailing", stop_trail_pct=100, stop_method="manual"),
            dict(stop_kind="fixed", stop_level=45),
            dict(stop_level=45),
            # A whole stop but for its kind (V24, the #567 review): V21's rule
            # came out NULL here, and a CHECK passes on NULL.
            dict(stop_level=45, stop_method="manual"),
            dict(stop_trail_pct=8, stop_method="trailing"),
            dict(stop_set=True),
            # Whether the stop was set is only known once the order has filled.
            dict(stop_kind="fixed", stop_level=45, stop_method="manual", stop_set=True),
        ]
        for columns in bad:
            with self.subTest(columns=columns), self.assertRaises(psycopg2.errors.CheckViolation):
                self._insert(**columns)

    def test_a_pending_buy_with_a_stop_is_accepted(self):
        self._insert(stop_kind="trailing", stop_trail_pct=8, stop_method="trailing")
        self.assertEqual(self.order_count(), 1)


class RouteTest(_Base):
    def setUp(self):
        super().setUp()
        OwnerIdentityRepository().upsert(identity=IDENTITY, owner=OWNER)
        self.app = create_app()
        self.client = TestClient(self.app)
        principal = Principal(subject=IDENTITY, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal
        services = patch("api.routers.orders.services")
        services.start().return_value.orders = self.service
        self.addCleanup(services.stop)
        clock = patch("quantcore.services.orders.datetime")
        frozen = clock.start()
        frozen.datetime.now.return_value = OPEN_NOW
        frozen.timezone = datetime.timezone
        self.addCleanup(clock.stop)

    def post(self, key="k1", **body):
        body = {"book_id": self.book, "symbol": SYMBOL, "side": "buy", "shares": "2",
                "idempotency_key": key, **body}
        return self.client.post("/api/portfolio/orders", json=body)

    def test_a_buy_with_a_stop_answers_201_with_the_stop_and_whether_it_was_set(self):
        placed = self.post(stop={"kind": "fixed", "stop_level": "45", "method": "confluence",
                                 "proposed_level": "44.8"})
        self.assertEqual(placed.status_code, 201)
        body = placed.json()
        self.assertEqual(
            (body["status"], body["stop_kind"], body["stop_level"], body["stop_method"],
             body["stop_proposed_level"], body["stop_set"]),
            ("filled", "fixed", 45, "confluence", 44.8, True),
        )
        (stop,) = self.all_stops()
        self.assertEqual((stop["status"], stop["stop_level"]), ("active", D("45")))

    def test_a_trailing_stop_is_sent_as_a_percentage(self):
        body = self.post(stop={"kind": "trailing", "trail_pct": "8"}).json()
        self.assertEqual((body["stop_kind"], body["stop_trail_pct"], body["stop_set"]),
                         ("trailing", 8, True))

    def test_a_stop_that_cannot_be_set_is_a_422_and_stores_nothing(self):
        for stop in (
            {"kind": "fixed", "stop_level": "50"},          # not below the fill price
            {"kind": "fixed"},                              # no level
            {"kind": "limit", "stop_level": "45"},          # not a kind
            {"kind": "fixed", "stop_level": "45", "method": "hunch"},
        ):
            with self.subTest(stop=stop):
                self.assertEqual(self.post(stop=stop).status_code, 422)
        self.assertEqual(self.post(side="sell", stop={"kind": "fixed", "stop_level": "45"})
                         .status_code, 422)
        self.assertEqual((self.order_count(), self.all_stops()), (0, []))

    def test_the_stop_takes_no_status_source_or_reference(self):
        body = self.post(stop={
            "kind": "fixed", "stop_level": "45", "status": "triggered", "source": "ai",
            "reference_price": "1", "stop_set": False,
        }).json()
        self.assertTrue(body["stop_set"])
        (stop,) = self.all_stops()
        self.assertEqual((stop["status"], stop["source"], stop["reference_price"]),
                         ("active", "manual", D("50")))


class RealizedSourceTest(_Base):
    def test_a_sale_says_what_prompted_it_and_which_order_made_it(self):
        self.place(shares=4, stop_kind="fixed", stop_level="45")
        sold = self.place(side="sell", shares=1)
        (stop,) = self.all_stops()
        by_stop = self.service.sell_for_stop(
            owner=OWNER, book_id=self.book, stop_id=stop["stop_id"], symbol=SYMBOL,
            session=SESSION, price=D("45"), stop_level=D("45"), high_water_mark=None,
            now=JOB_NOW)

        sales = self.lots.list_realized_sales(OWNER, book_id=self.book)

        self.assertEqual(
            [(s["source"], s["source_ref"], s["order_id"]) for s in sales],
            [("manual", None, sold["order_id"]),
             ("stop", f"stop:{stop['stop_id']}", by_stop["order_id"])],
        )

    def test_a_sale_recorded_by_hand_is_manual_with_no_order(self):
        lot_id = self.lots.add_position(OWNER, {
            "symbol": SYMBOL, "name": SYMBOL, "purchase_price": D("10"), "quantity": D("3"),
            "purchase_date": "2026-01-05", "trade_date": "2026-01-05", "currency": "USD",
        })
        self.lots.close_lots(OWNER, [(lot_id, D("3"))], D("12"), "2026-06-15")

        (sale,) = self.lots.list_realized_sales(OWNER)

        self.assertEqual((sale["source"], sale["source_ref"], sale["order_id"]),
                         ("manual", None, None))


if __name__ == "__main__":
    unittest.main()
