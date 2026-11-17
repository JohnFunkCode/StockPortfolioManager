"""The bar-close stop checker (#431 phase 2, #221; plan step 4.2).

Bottom up: the rules in `analytics.stored_stops`; `PricesService.closed_bars_since`
against the real `ohlcv` table with the Yahoo fetch stubbed; the checker's
statements in `StopsRepository`, with their wrong-owner and wrong-book cases;
`StopsService.check_stop` and `OrdersService.sell_for_stop` against the real
repositories with a stub in place of the bar read; the walk, the alarm, the
breach alert and the outer guard in `main.py` with a mock service; the two
Discord messages; and one run of the whole step on real services.
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
from notifier import Notifier, _paper_fill_line, _stop_alert_line  # noqa: E402
from quantcore.analytics import stored_stops as sm  # noqa: E402
from quantcore.analytics.market_time import ET  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import BooksRepository  # noqa: E402
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.ohlcv_repository import OhlcvRepository  # noqa: E402
from quantcore.repositories.orders_repository import OrdersRepository  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.repositories.stops_repository import StopsRepository  # noqa: E402
from quantcore.services.cash import CashService  # noqa: E402
from quantcore.services.orders import (  # noqa: E402
    OrdersService,
    _opened_by,
    _stop_sale_terms,
)
from quantcore.services.prices import PricesService  # noqa: E402
from quantcore.services.stops import StopNotFoundError, StopsService  # noqa: E402

from tests.test_orders_api import StubGateway  # noqa: E402
from tests.test_stops_repository import purge  # noqa: E402

OWNER = "zz_stop_checks_owner"
OTHER = "zz_stop_checks_other"
SYMBOL = "ZZSTOPCHK"
SECOND = "ZZSTOPCHK2"
D = Decimal

# Monday 2026-06-15, 11:00 ET: in the session, so a stop set then is first
# tested against Tuesday's bar. Friday 2026-06-19 is a holiday (Juneteenth).
SET_AT = ET.localize(datetime.datetime(2026, 6, 15, 11, 0))
FRIDAY = datetime.date(2026, 6, 12)
MONDAY = datetime.date(2026, 6, 15)
TUESDAY = datetime.date(2026, 6, 16)
WEDNESDAY = datetime.date(2026, 6, 17)
THURSDAY = datetime.date(2026, 6, 18)
JOB_NOW = ET.localize(datetime.datetime(2026, 6, 16, 17, 5))
LAST_CLOSE = datetime.datetime(2026, 6, 12, tzinfo=datetime.timezone.utc)


def LOOKBACK_FROM(today):  # noqa: N802 — reads as the constant it stands for
    """Where a check's one read starts: far enough back for the ATR."""
    return today - datetime.timedelta(days=sm.APPROACH_LOOKBACK_DAYS)


def bar(session, open_, high, low, close=None):
    return {"session": session, "open": open_, "high": high, "low": low,
            "close": close if close is not None else open_}


def ts_of(day: datetime.date) -> int:
    return int(datetime.datetime.combine(
        day, datetime.time(0), tzinfo=datetime.timezone.utc).timestamp())


def daily_bars(rows: dict) -> pd.DataFrame:
    """Daily bars dated at midnight UTC, from `{date: (open, high, low, close)}`."""
    days = sorted(rows)
    return pd.DataFrame(
        {"Open": [rows[d][0] for d in days], "High": [rows[d][1] for d in days],
         "Low": [rows[d][2] for d in days], "Close": [rows[d][3] for d in days],
         "Volume": [1_000_000] * len(days)},
        index=pd.DatetimeIndex([pd.Timestamp(ts_of(d), unit="s", tz="UTC") for d in days]),
    )


def _purge():
    purge((OWNER, OTHER), (SYMBOL, SECOND))
    with closing(get_connection()) as conn:
        for symbol in (SYMBOL, SECOND):
            conn.execute("DELETE FROM ohlcv WHERE symbol = %s", (symbol,))
            conn.execute("DELETE FROM fetch_log WHERE symbol = %s", (symbol,))
        conn.commit()


def lot(symbol=SYMBOL, **overrides):
    row = {
        "name": "Stop Co", "symbol": symbol, "purchase_price": 80.0, "quantity": 5,
        "purchase_date": "2026-01-02", "currency": "USD",
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# 1. The rules
# --------------------------------------------------------------------------

class FirstSessionTest(unittest.TestCase):
    def test_a_stop_set_in_the_session_starts_with_the_next_sessions_bar(self):
        self.assertEqual(sm.first_session(SET_AT), TUESDAY)

    def test_a_stop_set_before_the_open_is_tested_against_that_days_bar(self):
        early = ET.localize(datetime.datetime(2026, 6, 15, 8, 0))
        self.assertEqual(sm.first_session(early), MONDAY)

    def test_a_stop_set_after_the_close_or_at_a_weekend_skips_to_the_next_session(self):
        evening = ET.localize(datetime.datetime(2026, 6, 15, 18, 0))
        saturday = ET.localize(datetime.datetime(2026, 6, 13, 12, 0))
        before_holiday = ET.localize(datetime.datetime(2026, 6, 18, 18, 0))
        self.assertEqual(sm.first_session(evening), TUESDAY)
        self.assertEqual(sm.first_session(saturday), MONDAY)
        self.assertEqual(sm.first_session(before_holiday), datetime.date(2026, 6, 22))

    def test_the_next_bar_follows_the_last_one_checked(self):
        self.assertEqual(sm.next_bar_date(SET_AT, None), TUESDAY)
        self.assertEqual(sm.next_bar_date(SET_AT, TUESDAY), WEDNESDAY)
        # A check recorded before the first session doesn't bring it forward.
        self.assertEqual(sm.next_bar_date(SET_AT, FRIDAY), TUESDAY)

    def test_a_stop_is_due_once_its_first_session_has_come_and_until_today_is_checked(self):
        self.assertFalse(sm.is_due(SET_AT, None, MONDAY))
        self.assertTrue(sm.is_due(SET_AT, None, TUESDAY))
        self.assertFalse(sm.is_due(SET_AT, TUESDAY, TUESDAY))
        self.assertTrue(sm.is_due(SET_AT, TUESDAY, THURSDAY))


class CheckStopTest(unittest.TestCase):
    def check(self, bars, **stop):
        stop.setdefault("kind", "fixed")
        stop.setdefault("stop_level", D("90.00"))
        stop.setdefault("trail_pct", None)
        stop.setdefault("high_water_mark", None)
        return sm.check_stop(bars=bars, **stop)

    def test_no_bars_changes_nothing_and_checks_nothing(self):
        self.assertEqual(self.check([]), sm.StopCheck(D("90.00"), None, None))

    def test_bars_above_the_level_hold_and_the_newest_is_recorded(self):
        result = self.check([bar(TUESDAY, 95, 97, 91), bar(WEDNESDAY, 96, 99, 90.01)])
        self.assertEqual(result, sm.StopCheck(D("90.00"), None, WEDNESDAY))

    def test_a_low_at_the_level_reaches_it_and_fills_at_the_level(self):
        result = self.check([bar(TUESDAY, 95, 96, 90.0, close=94)])
        self.assertEqual(result.trigger, sm.Trigger(TUESDAY, D("90.00"), D("90.00"), False))
        self.assertEqual(result.last_bar, TUESDAY)

    def test_a_bar_that_dips_and_recovers_still_reaches_the_stop(self):
        # Decision 5: a close above the level doesn't undo the touch.
        result = self.check([bar(TUESDAY, 95, 97, 88, close=96)])
        self.assertEqual(result.trigger.price, D("90.00"))
        self.assertFalse(result.trigger.gapped)

    def test_an_open_below_the_level_fills_at_the_open(self):
        result = self.check([bar(TUESDAY, 84.126, 86, 83)])
        self.assertEqual(result.trigger, sm.Trigger(TUESDAY, D("84.13"), D("90.00"), True))

    def test_an_open_exactly_at_the_level_is_not_a_gap(self):
        result = self.check([bar(TUESDAY, 90.0, 91, 89)])
        self.assertEqual((result.trigger.price, result.trigger.gapped), (D("90.00"), False))

    def test_the_walk_ends_at_the_first_bar_that_reaches_the_stop(self):
        result = self.check([bar(TUESDAY, 95, 96, 89), bar(WEDNESDAY, 70, 71, 60)])
        self.assertEqual((result.last_bar, result.trigger.session), (TUESDAY, TUESDAY))

    def test_a_fixed_stop_never_moves(self):
        result = self.check([bar(TUESDAY, 95, 150, 94)])
        self.assertEqual((result.stop_level, result.high_water_mark), (D("90.00"), None))

    def trailing(self, bars, mark="100.00", pct="10"):
        return self.check(
            bars, kind="trailing", stop_level=sm.trailing_level(D(mark), D(pct)),
            trail_pct=D(pct), high_water_mark=D(mark),
        )

    def test_a_trailing_stop_rises_with_a_new_high(self):
        result = self.trailing([bar(TUESDAY, 101, 110.004, 99)])
        self.assertEqual(
            result, sm.StopCheck(D("99.00"), D("110.00"), TUESDAY))

    def test_a_trailing_stop_never_falls(self):
        result = self.trailing([bar(TUESDAY, 101, 110, 99.5), bar(WEDNESDAY, 104, 105, 100)])
        self.assertEqual((result.stop_level, result.high_water_mark), (D("99.00"), D("110.00")))
        self.assertIsNone(result.trigger)

    def test_a_bar_is_tested_against_the_level_it_started_with(self):
        # High 120 would put the level at 108, above this bar's low of 95.
        # The bar doesn't say which came first, so it is not reached here...
        held = self.trailing([bar(TUESDAY, 101, 120, 95)])
        self.assertIsNone(held.trigger)
        self.assertEqual(held.stop_level, D("108.00"))
        # ...and the next bar opens under the raised level.
        result = self.trailing([bar(TUESDAY, 101, 120, 95), bar(WEDNESDAY, 96, 97, 94)])
        self.assertEqual(result.trigger, sm.Trigger(WEDNESDAY, D("96.00"), D("108.00"), True))
        self.assertEqual(result.high_water_mark, D("120.00"))

    def test_a_triggered_trailing_stop_keeps_the_terms_it_was_reached_with(self):
        result = self.trailing([bar(TUESDAY, 101, 110, 99.5), bar(WEDNESDAY, 100, 130, 98)])
        self.assertEqual((result.stop_level, result.high_water_mark), (D("99.00"), D("110.00")))
        self.assertEqual(result.trigger.price, D("99.00"))

    def test_a_bar_with_a_missing_or_bad_price_is_left_out(self):
        for bad in (None, float("nan"), 0, -1):
            with self.subTest(bad=bad):
                result = self.check([bar(TUESDAY, 95, 96, bad), bar(WEDNESDAY, bad, 96, 80)])
                self.assertEqual(result, sm.StopCheck(D("90.00"), None, None))


class StopSaleHelpersTest(unittest.TestCase):
    def test_lots_opened_after_the_session_are_left_out(self):
        lots = [
            {"lot_id": 1, "trade_date": datetime.date(2026, 6, 16), "purchase_date": "2026-01-02"},
            {"lot_id": 2, "trade_date": None, "purchase_date": "2026-06-16"},
            {"lot_id": 3, "trade_date": datetime.date(2026, 6, 17), "purchase_date": "2026-06-17"},
            {"lot_id": 4, "trade_date": None, "purchase_date": None},
        ]
        self.assertEqual([lot["lot_id"] for lot in _opened_by(lots, TUESDAY)], [1, 2, 4])

    def test_a_sale_at_the_level_has_no_price_source_and_a_gap_is_the_open(self):
        self.assertEqual(
            _stop_sale_terms(D("90.00"), D("90.00")), (None, "Sold by its stop at 90.00"))
        source, note = _stop_sale_terms(D("84.13"), D("90.00"))
        self.assertEqual(source, "session_open")
        self.assertIn("opened below it", note)


# --------------------------------------------------------------------------
# 2. The bars
# --------------------------------------------------------------------------

class ClosedBarsSinceTest(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.ohlcv = OhlcvRepository()
        self.gateway = mock.Mock()
        self.gateway.fetch_history.return_value = pd.DataFrame()
        self.prices = PricesService(
            ohlcv_repository=self.ohlcv, yfinance_gateway=self.gateway,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())

    def test_reads_the_closed_bars_from_a_date_on_oldest_first(self):
        self.ohlcv.store_bars(SYMBOL, "1d", daily_bars({
            MONDAY: (50, 52, 49, 51), TUESDAY: (51, 53, 50.5, 52), WEDNESDAY: (52, 54, 47, 48),
        }))
        self.assertEqual(self.prices.closed_bars_since(SYMBOL, TUESDAY), [
            {"session": TUESDAY, "open": 51.0, "high": 53.0, "low": 50.5, "close": 52.0},
            {"session": WEDNESDAY, "open": 52.0, "high": 54.0, "low": 47.0, "close": 48.0},
        ])

    def test_a_bar_still_open_is_not_read(self):
        self.ohlcv.store_bars(SYMBOL, "1d", daily_bars({
            MONDAY: (50, 52, 49, 51), TUESDAY: (51, 52, 10, 11)}))
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE ohlcv SET status = 'OPEN' WHERE symbol = %s AND interval = '1d' "
                "AND ts = %s", (SYMBOL, ts_of(TUESDAY)))
            conn.commit()
        self.assertEqual(
            [b["session"] for b in self.prices.closed_bars_since(SYMBOL, MONDAY)], [MONDAY])

    def test_nothing_stored_and_nothing_fetched_is_no_bars(self):
        self.assertEqual(self.prices.closed_bars_since(SYMBOL, MONDAY), [])

    def test_a_fetch_that_raises_reaches_the_caller(self):
        self.gateway.fetch_history.side_effect = RuntimeError("yahoo down")
        with self.assertRaises(RuntimeError):
            self.prices.closed_bars_since(SYMBOL, MONDAY)


# --------------------------------------------------------------------------
# 3. and 4. The rows and the services
# --------------------------------------------------------------------------

class StubPrices:
    """`closed_bars_since` from a dict of `{symbol: [bar, ...]}`, and the
    `last_close` a new stop is set against."""

    def __init__(self):
        self.bars = {}
        self.error = None
        self.before_answer = None
        self.calls = []

    def last_close(self, symbol):
        return {"close": 100.0, "as_of": LAST_CLOSE}

    def closed_bars_since(self, symbol, since):
        self.calls.append((symbol, since))
        if self.before_answer is not None:
            self.before_answer()
        if self.error is not None:
            raise self.error
        return [b for b in self.bars.get(symbol, []) if b["session"] >= since]


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.repo = StopsRepository(self.lots)
        self.order_rows = OrdersRepository(self.lots)
        self.prices = StubPrices()
        self.orders = OrdersService(self.order_rows, self.books, StubGateway(), self.prices)
        self.service = StopsService(self.repo, self.books, self.prices, self.orders)
        self.cash = CashService(CashRepository(), self.books)
        # A real holding and a paper holding of the same symbol.
        self.lots.add_position(OWNER, lot())
        self.paper = self.books.create_paper_book(
            owner=OWNER, name="Paper", starting_cash=1000, opened_on=datetime.date(2026, 1, 1))
        self.lots.add_position(OWNER, lot(), book_id=self.paper)

    def set_stop(self, **overrides):
        args = {"owner": OWNER, "symbol": SYMBOL, "kind": "fixed", "stop_level": "90",
                "now": SET_AT}
        args.update(overrides)
        return self.service.set_stop(**args)

    def check(self, stop, today=TUESDAY, **kwargs):
        kwargs.setdefault("owner", OWNER)
        kwargs.setdefault("book_id", stop["book_id"])
        return self.service.check_stop(
            stop_id=stop["stop_id"], today=today, now=JOB_NOW, **kwargs)

    def stored(self, stop):
        return self.repo.get_stop(owner=OWNER, stop_id=stop["stop_id"], book_id=stop["book_id"])

    def open_lots(self, book_id=None, symbol=SYMBOL):
        return [l for l in self.lots.list_lots_for_symbol(OWNER, symbol, book_id=book_id)
                if l["status"] == "OPEN"]

    def paper_orders(self):
        return self.order_rows.list_orders(owner=OWNER, book_id=self.paper)

    def sales(self, book_id=None):
        with closing(get_connection()) as conn:
            return conn.execute(
                "SELECT ls.shares_sold, ls.sale_price, ls.sale_trade_date, ls.source, "
                "ls.source_ref, ls.notes, ls.order_id FROM lot_sales ls "
                "JOIN positions p ON p.position_id = ls.lot_id "
                "WHERE p.owner = %s AND p.book_id = %s ORDER BY ls.sale_id",
                (OWNER, book_id or self.paper),
            ).fetchall()


class CheckerRowsTest(_Base):
    def test_a_check_that_held_records_the_bar_and_the_raised_terms(self):
        stop = self.set_stop(kind="trailing", stop_level=None, trail_pct="10")
        self.assertTrue(self.repo.record_check(
            owner=OWNER, stop_id=stop["stop_id"], last_bar_checked=TUESDAY,
            stop_level=D("99.00"), high_water_mark=D("110.00")))

        stored = self.stored(stop)
        self.assertEqual(
            (stored["status"], stored["last_bar_checked"], stored["stop_level"],
             stored["high_water_mark"], stored["resolved_at"]),
            ("active", TUESDAY, D("99.000000"), D("110.000000"), None))

    def test_a_triggered_stop_stores_the_bar_and_the_price_and_is_final(self):
        stop = self.set_stop()
        trigger = dict(
            owner=OWNER, stop_id=stop["stop_id"], stop_level=D("90.00"), high_water_mark=None,
            triggered_session=TUESDAY, trigger_price=D("84.13"), resolved_at=JOB_NOW)
        self.assertTrue(self.repo.trigger_stop(**trigger))
        self.assertFalse(self.repo.trigger_stop(**trigger))

        stored = self.stored(stop)
        self.assertEqual(
            (stored["status"], stored["triggered_session"], stored["trigger_price"],
             stored["last_bar_checked"], stored["resolved_at"], stored["alerted_at"]),
            ("triggered", TUESDAY, D("84.130000"), TUESDAY, JOB_NOW, None))
        # A final stop takes no more checks.
        self.assertFalse(self.repo.record_check(
            owner=OWNER, stop_id=stop["stop_id"], last_bar_checked=WEDNESDAY,
            stop_level=D("1.00"), high_water_mark=None))
        self.assertEqual(self.stored(stop)["stop_level"], D("90.000000"))

    def test_only_a_triggered_stop_is_marked_alerted_and_only_once(self):
        stop = self.set_stop()
        mark = lambda: self.repo.mark_alerted(  # noqa: E731
            owner=OWNER, stop_id=stop["stop_id"], alerted_at=JOB_NOW)
        self.assertFalse(mark())
        self.repo.trigger_stop(
            owner=OWNER, stop_id=stop["stop_id"], stop_level=D("90.00"), high_water_mark=None,
            triggered_session=TUESDAY, trigger_price=D("90.00"), resolved_at=JOB_NOW)
        self.assertTrue(mark())
        self.assertFalse(mark())
        self.assertEqual(self.stored(stop)["alerted_at"], JOB_NOW)

    def test_flat_stops_are_closed_in_their_own_book_only(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper)
        self.assertEqual(
            self.repo.close_flat_stops(owner=OWNER, resolved_at=JOB_NOW, book_id=self.paper), 0)

        # The paper book's lot goes; the real book still holds the symbol.
        self.lots.remove_position(OWNER, SYMBOL, book_id=self.paper)
        self.assertEqual(self.repo.close_flat_stops(owner=OWNER, resolved_at=JOB_NOW), 0)
        self.assertEqual(
            self.repo.close_flat_stops(owner=OWNER, resolved_at=JOB_NOW, book_id=self.paper), 1)
        self.assertEqual(
            (self.stored(paper)["status"], self.stored(paper)["resolved_at"]),
            ("closed", JOB_NOW))
        self.assertEqual(self.stored(real)["status"], "active")

    def test_a_closed_lot_does_not_keep_a_stop_alive(self):
        stop = self.set_stop()
        lot_id = self.open_lots()[0]["lot_id"]
        self.lots.close_lots(OWNER, [(lot_id, D("5"))], 95.0, "2026-06-16")
        self.assertEqual(self.stored(stop)["status"], "closed")

    def test_a_partial_sale_leaves_the_stop_on(self):
        stop = self.set_stop()
        lot_id = self.open_lots()[0]["lot_id"]
        self.lots.close_lots(OWNER, [(lot_id, D("2"))], 95.0, "2026-06-16")
        self.assertEqual(self.stored(stop)["status"], "active")

    def test_a_sale_in_one_book_leaves_the_other_books_stop(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper)
        lot_id = self.open_lots()[0]["lot_id"]
        self.lots.close_lots(OWNER, [(lot_id, D("5"))], 95.0, "2026-06-16")
        self.assertEqual(
            (self.stored(real)["status"], self.stored(paper)["status"]), ("closed", "active"))

    def test_wrong_owner_and_wrong_book_checker_writes_change_nothing(self):
        stop = self.set_stop(book_id=self.paper)
        self.lots.add_position(OTHER, lot())
        self.lots.remove_position(OWNER, SYMBOL, book_id=self.paper)
        trigger = dict(
            stop_id=stop["stop_id"], stop_level=D("1.00"), high_water_mark=None,
            triggered_session=TUESDAY, trigger_price=D("1.00"), resolved_at=JOB_NOW)
        for owner, book in ((OTHER, None), (OTHER, self.paper), (OWNER, None)):
            with self.subTest(owner=owner, book=book):
                self.assertFalse(self.repo.record_check(
                    owner=owner, stop_id=stop["stop_id"], last_bar_checked=TUESDAY,
                    stop_level=D("1.00"), high_water_mark=None, book_id=book))
                self.assertFalse(self.repo.trigger_stop(owner=owner, book_id=book, **trigger))
                self.assertFalse(self.repo.mark_alerted(
                    owner=owner, stop_id=stop["stop_id"], alerted_at=JOB_NOW, book_id=book))
                # The stop's own book is flat, and this names another one.
                self.assertEqual(self.repo.close_flat_stops(
                    owner=owner, resolved_at=JOB_NOW, book_id=book), 0)
        stored = self.stored(stop)
        self.assertEqual(
            (stored["status"], stored["stop_level"], stored["last_bar_checked"]),
            ("active", D("90.000000"), None))


class CheckStopServiceTest(_Base):
    def test_a_stop_is_not_tested_before_its_first_session(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(MONDAY, 95, 96, 50)]

        result = self.check(stop, today=MONDAY)

        self.assertEqual((result["outcome"], result["status"]), ("waiting", "active"))
        self.assertEqual(self.prices.calls, [])

    def test_the_bar_that_was_trading_when_the_stop_was_set_is_never_tested(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(MONDAY, 95, 96, 50), bar(TUESDAY, 95, 96, 94)]

        result = self.check(stop)

        self.assertEqual((result["outcome"], result["last_bar_checked"]), ("checked", TUESDAY))
        # One read, reaching back for the approach's ATR (plan step 4.4):
        # Monday's low of 50 is in it and was not tested.
        self.assertEqual(self.prices.calls, [(SYMBOL, LOOKBACK_FROM(TUESDAY))])

    def test_no_new_bar_waits_and_writes_nothing(self):
        stop = self.set_stop()
        result = self.check(stop)
        self.assertEqual((result["outcome"], result["last_bar_checked"]), ("waiting", None))

    def test_a_bar_dated_after_today_is_not_tested(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 94), bar(WEDNESDAY, 80, 81, 70)]
        result = self.check(stop, today=TUESDAY)
        self.assertEqual((result["outcome"], result["last_bar_checked"]), ("checked", TUESDAY))

    def test_a_trailing_stop_is_raised_and_stored(self):
        stop = self.set_stop(kind="trailing", stop_level=None, trail_pct="10")
        self.assertEqual((stop["stop_level"], stop["high_water_mark"]),
                         (D("90.000000"), D("100.000000")))
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 101, 110, 99)]

        result = self.check(stop)

        self.assertEqual(
            (result["outcome"], result["stop_level"], result["high_water_mark"]),
            ("checked", D("99.000000"), D("110.000000")))

    def test_every_bar_since_the_last_check_is_worked_through(self):
        # The job missed Tuesday and Wednesday. Thursday's run finds the
        # stop was reached on Wednesday, not only that Thursday is fine.
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [
            bar(TUESDAY, 95, 96, 94), bar(WEDNESDAY, 93, 94, 89), bar(THURSDAY, 95, 99, 94)]

        result = self.check(stop, today=THURSDAY)

        self.assertEqual(
            (result["outcome"], result["triggered_session"], result["trigger_price"]),
            ("triggered", WEDNESDAY, D("90.000000")))

    def test_a_second_run_tests_only_bars_after_the_last_one_checked(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 94)]
        self.check(stop)
        # Tuesday's bar is read again, for the ATR, but not tested again: a
        # low under the level there now would trigger the stop.
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 50), bar(WEDNESDAY, 95, 96, 94)]
        result = self.check(stop, today=WEDNESDAY)
        self.assertEqual((result["outcome"], result["last_bar_checked"]), ("checked", WEDNESDAY))
        self.assertEqual(self.prices.calls, [(SYMBOL, LOOKBACK_FROM(TUESDAY)),
                                             (SYMBOL, LOOKBACK_FROM(WEDNESDAY))])

    def test_a_real_book_stop_is_marked_and_nothing_is_sold(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 84.12, 86, 83)]

        result = self.check(stop)

        self.assertEqual(
            (result["outcome"], result["status"], result["trigger_price"],
             result["resolved_at"], result["alerted_at"]),
            ("triggered", "triggered", D("84.120000"), JOB_NOW, None))
        self.assertEqual(len(self.open_lots()), 1)
        self.assertEqual(self.sales(self.books.ensure_real_book(owner=OWNER)), [])

    def test_a_paper_stop_sells_the_position_at_the_level(self):
        stop = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88, close=95)]

        result = self.check(stop)

        self.assertEqual((result["outcome"], result["status"]), ("triggered", "triggered"))
        self.assertEqual(self.open_lots(self.paper), [])
        order = self.paper_orders()[0]
        self.assertEqual(
            (order["status"], order["kind"], order["side"], order["source"],
             order["source_ref"], order["filled_shares"], order["fill_price"],
             order["price_source"], order["session_date"]),
            ("filled", "market", "sell", "stop", f"stop:{stop['stop_id']}", D("5.000000"),
             D("90.000000"), None, "2026-06-16"))
        sale = self.sales()[0]
        self.assertEqual(
            (D(str(sale["shares_sold"])), D(str(sale["sale_price"])),
             str(sale["sale_trade_date"]), sale["source"], sale["source_ref"],
             sale["order_id"], sale["notes"]),
            (D("5"), D("90"), "2026-06-16", "stop", f"stop:{stop['stop_id']}",
             order["order_id"], "Sold by its stop at 90.000000"))
        self.assertEqual(
            self.cash.ledger(owner=OWNER, book_id=self.paper)["balance"], D("1050.00"))
        # The real book's holding of the same symbol is untouched.
        self.assertEqual(len(self.open_lots()), 1)

    def test_a_paper_stop_that_opened_below_its_level_fills_at_the_open(self):
        stop = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 84.126, 86, 83)]

        self.check(stop)

        order = self.paper_orders()[0]
        self.assertEqual((order["fill_price"], order["price_source"]),
                         (D("84.130000"), "session_open"))
        self.assertIn("opened below it", self.sales()[0]["notes"])
        self.assertEqual(self.stored(stop)["trigger_price"], D("84.130000"))

    def test_checking_twice_sells_once(self):
        stop = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]
        self.check(stop)

        again = self.check(stop, today=WEDNESDAY)

        self.assertEqual((again["outcome"], again["status"]), ("skipped", "triggered"))
        self.assertEqual(len(self.paper_orders()), 1)

    def test_a_stop_cancelled_while_the_bars_were_fetched_sells_nothing(self):
        stop = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]
        self.prices.before_answer = lambda: self.service.cancel_stop(
            owner=OWNER, stop_id=stop["stop_id"], book_id=self.paper)

        result = self.check(stop)

        self.assertEqual((result["outcome"], result["status"]), ("skipped", "cancelled"))
        self.assertEqual(self.paper_orders(), [])
        self.assertEqual(len(self.open_lots(self.paper)), 1)

    def test_a_lot_bought_after_the_bar_that_reached_the_stop_is_not_sold(self):
        # A late run: Wednesday's queued buy has filled before Tuesday's bar
        # is tested. The stop sells what it covered on Tuesday.
        stop = self.set_stop(book_id=self.paper)
        self.lots.add_position(
            OWNER, lot(quantity=3, purchase_date="2026-06-17"), book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]

        self.check(stop, today=WEDNESDAY)

        left = self.open_lots(self.paper)
        self.assertEqual([(l["quantity"], l["purchase_date"]) for l in left],
                         [(D("3.000000"), "2026-06-17")])
        self.assertEqual(self.paper_orders()[0]["filled_shares"], D("5.000000"))
        self.assertEqual(self.stored(stop)["status"], "triggered")

    def test_a_paper_stop_with_nothing_left_to_sell_is_closed(self):
        stop = self.set_stop(book_id=self.paper)
        self.lots.remove_position(OWNER, SYMBOL, book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]

        result = self.check(stop)

        self.assertEqual((result["outcome"], result["status"]), ("skipped", "closed"))
        self.assertEqual(self.paper_orders(), [])

    def test_a_sale_that_fails_leaves_the_stop_active(self):
        stop = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]
        with mock.patch.object(
                self.order_rows._lots, "close_lots_on", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.check(stop)

        stored = self.stored(stop)
        self.assertEqual((stored["status"], stored["last_bar_checked"]), ("active", None))
        self.assertEqual(self.paper_orders(), [])

    def test_a_bar_fetch_that_raises_leaves_the_stop_as_it_was(self):
        stop = self.set_stop()
        self.prices.error = RuntimeError("yahoo down")
        with self.assertRaises(RuntimeError):
            self.check(stop)
        self.assertEqual(self.stored(stop)["last_bar_checked"], None)

    def test_another_owner_or_book_cannot_check_it(self):
        stop = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]
        for owner, book in ((OWNER, None), (OTHER, None)):
            with self.subTest(owner=owner), self.assertRaises(StopNotFoundError):
                self.check(stop, owner=owner, book_id=book)
        self.assertEqual(self.stored(stop)["status"], "active")

    def test_the_real_book_cannot_be_sold_by_a_stop(self):
        stop = self.set_stop()
        with self.assertRaisesRegex(ValueError, "only in a paper book"):
            self.orders.sell_for_stop(
                owner=OWNER, stop_id=stop["stop_id"], symbol=SYMBOL, session=TUESDAY,
                price=D("90.00"), stop_level=D("90.00"), high_water_mark=None)
        self.assertEqual(self.stored(stop)["status"], "active")

    def test_a_paper_sell_of_the_whole_position_closes_its_stop(self):
        stop = self.set_stop(book_id=self.paper)
        with self.order_rows.transaction(owner=OWNER, book_id=self.paper) as tx:
            lots = tx.open_lots(SYMBOL)
            tx.sell([(lots[0]["lot_id"], D("5"))], D("95.00"), "2026-06-16",
                    allocation_method="FIFO", source="manual", source_ref=None, order_id=None)
        self.assertEqual(self.stored(stop)["status"], "closed")


class DueStopsTest(_Base):
    def mine(self, today=TUESDAY):
        return [s for s in self.service.due_stops(today=today) if s["owner"] == OWNER]

    def test_lists_active_stops_whose_bar_for_today_is_untested(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper)
        self.assertEqual(self.mine(today=MONDAY), [])
        self.assertEqual(
            sorted((s["stop_id"], s["book_id"], s["symbol"], s["last_bar_checked"])
                   for s in self.mine()),
            sorted([(real["stop_id"], real["book_id"], SYMBOL, None),
                    (paper["stop_id"], self.paper, SYMBOL, None)]))

    def test_a_checked_stop_is_not_due_until_the_next_session(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 94)]
        self.check(stop)
        self.assertEqual(self.mine(), [])
        self.assertEqual([s["last_bar_checked"] for s in self.mine(today=WEDNESDAY)], [TUESDAY])

    def test_a_stop_that_is_no_longer_active_is_not_due(self):
        stop = self.set_stop()
        self.service.cancel_stop(owner=OWNER, stop_id=stop["stop_id"])
        self.assertEqual(self.mine(), [])

    def test_an_archived_books_stop_is_not_due(self):
        self.set_stop(book_id=self.paper)
        self.books.archive_book(owner=OWNER, book_id=self.paper)
        self.assertEqual(self.mine(), [])

    def test_flat_stops_are_closed_across_live_books(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper)
        self.lots.remove_position(OWNER, SYMBOL)
        self.lots.remove_position(OWNER, SYMBOL, book_id=self.paper)

        self.service.close_flat_stops(now=JOB_NOW)

        self.assertEqual(
            (self.stored(real)["status"], self.stored(paper)["status"]), ("closed", "closed"))


class BreachAlertRowsTest(_Base):
    def test_only_the_real_books_unalerted_triggers_are_owed(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper)
        self.assertEqual(self.service.unalerted_triggers(owner=OWNER), [])
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]
        self.check(real)
        self.check(paper)

        owed = self.service.unalerted_triggers(owner=OWNER)
        self.assertEqual([s["stop_id"] for s in owed], [real["stop_id"]])
        self.assertEqual(self.service.unalerted_triggers(owner=OTHER), [])

        self.assertEqual(self.service.mark_alerted(
            owner=OWNER, stop_ids=[real["stop_id"], paper["stop_id"]], now=JOB_NOW), 1)
        self.assertEqual(self.service.unalerted_triggers(owner=OWNER), [])
        self.assertEqual(self.stored(paper)["alerted_at"], None)

    def test_another_owner_cannot_mark_it(self):
        real = self.set_stop()
        self.prices.bars[SYMBOL] = [bar(TUESDAY, 95, 96, 88)]
        self.check(real)
        self.assertEqual(self.service.mark_alerted(owner=OTHER, stop_ids=[real["stop_id"]]), 0)
        self.assertEqual(len(self.service.unalerted_triggers(owner=OWNER)), 1)


# --------------------------------------------------------------------------
# 5. The step in main.py
# --------------------------------------------------------------------------

class FakeNotifier:
    def __init__(self, accepted=True):
        self.unchecked = []
        self.alerts = []
        self.accepted = accepted

    def send_unchecked_stops_alert(self, stop_count, symbols, symbol_count, oldest_check):
        self.unchecked.append((stop_count, symbols, symbol_count, oldest_check))

    def send_stop_alert(self, stops, stop_count, day):
        self.alerts.append((stops, stop_count, day))
        return self.accepted


class _Clock:
    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def due(n, symbol="AAA", last=None):
    return [{"owner": f"secret-owner-{i}", "book_id": 7, "stop_id": i + 1,
             "symbol": symbol, "last_bar_checked": last} for i in range(n)]


def fake_stops(queue=None, outcomes=(), left=None, fail=()):
    """A mock `StopsService`: `queue` is what is due before the checks,
    `left` what is still due after them."""
    stops = mock.Mock()
    queue = queue if queue is not None else due(len(outcomes) or 2)
    stops.due_stops.side_effect = [queue, left if left is not None else []]
    stops.close_flat_stops.return_value = 0
    results = iter(outcomes or ["checked"] * len(queue))

    def check(*, owner, book_id, stop_id, today, approach_atr_multiple):
        if stop_id in fail:
            raise RuntimeError("secret detail")
        return {"stop_id": stop_id, "outcome": next(results)}

    stops.check_stop.side_effect = check
    return stops


class CheckStopsWalkTest(unittest.TestCase):
    def test_closes_flat_stops_then_walks_what_is_due_and_counts_each_outcome(self):
        stops = fake_stops(outcomes=["triggered", "checked", "waiting", "skipped", "checked"],
                           fail={5})
        stops.close_flat_stops.return_value = 2

        with mock.patch("builtins.print") as printed:
            summary = main.check_stops(stops, budget_seconds=100, clock=_Clock(0), today=TUESDAY)

        self.assertEqual(
            {k: summary[k] for k in (
                "due", "closed_flat", "attempted", "triggered", "checked", "waiting",
                "skipped", "failed", "budget_exhausted")},
            {"due": 5, "closed_flat": 2, "attempted": 5, "triggered": 1, "checked": 1,
             "waiting": 1, "skipped": 1, "failed": 1, "budget_exhausted": False})
        self.assertEqual(
            [c[0] for c in stops.method_calls[:2]], ["close_flat_stops", "due_stops"])
        stops.check_stop.assert_any_call(
            owner="secret-owner-0", book_id=7, stop_id=1, today=TUESDAY,
            approach_atr_multiple=D("1.0"))
        # A failure names the stop and the exception's class, never its text
        # or an owner.
        out = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("RuntimeError", out)
        self.assertNotIn("secret", out)

    def test_an_outcome_it_does_not_know_counts_as_skipped(self):
        summary = main.check_stops(
            fake_stops(outcomes=["something-new"]), budget_seconds=100, clock=_Clock(0),
            today=TUESDAY)
        self.assertEqual(summary["skipped"], 1)

    def test_budget_stops_the_walk(self):
        stops = fake_stops(queue=due(10))
        summary = main.check_stops(stops, budget_seconds=3, clock=_Clock(1.0), today=TUESDAY)
        self.assertLess(summary["attempted"], 10)
        self.assertTrue(summary["budget_exhausted"])

    def test_budget_comes_from_the_environment(self):
        stops = fake_stops(queue=due(10))
        with mock.patch.dict("os.environ", {main.STOP_CHECK_BUDGET_SECONDS_ENV: "3"}):
            summary = main.check_stops(stops, clock=_Clock(1.0), today=TUESDAY)
        self.assertTrue(summary["budget_exhausted"])

    def test_unparseable_budget_falls_back_to_the_default(self):
        stops = fake_stops(queue=due(3))
        with mock.patch.dict("os.environ", {main.STOP_CHECK_BUDGET_SECONDS_ENV: "soon"}):
            summary = main.check_stops(stops, clock=_Clock(1.0), today=TUESDAY)
        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(main.DEFAULT_STOP_CHECK_BUDGET_SECONDS, 120.0)


class AlertIfStopsUncheckedTest(unittest.TestCase):
    def test_quiet_when_nothing_is_left(self):
        stops, n = mock.Mock(), FakeNotifier()
        stops.due_stops.return_value = []
        self.assertFalse(main.alert_if_stops_unchecked(stops, n, today=TUESDAY))
        self.assertEqual(n.unchecked, [])

    def test_alarm_carries_the_count_a_symbol_sample_and_the_oldest_check(self):
        stops, n = mock.Mock(), FakeNotifier()
        stops.due_stops.return_value = (
            due(2, "BBB", last=MONDAY) + due(1, "AAA", last=FRIDAY))
        with mock.patch("builtins.print") as printed:
            self.assertTrue(main.alert_if_stops_unchecked(stops, n, today=TUESDAY))
        self.assertEqual(n.unchecked, [(3, ["AAA", "BBB"], 2, "2026-06-12")])
        self.assertNotIn("secret-owner", " ".join(str(c) for c in printed.call_args_list))

    def test_a_stop_never_checked_is_reported_as_such(self):
        stops, n = mock.Mock(), FakeNotifier()
        stops.due_stops.return_value = due(1, last=MONDAY) + due(1, last=None)
        main.alert_if_stops_unchecked(stops, n, today=TUESDAY)
        self.assertEqual(n.unchecked[0][3], None)

    def test_dead_webhook_does_not_raise(self):
        stops, n = mock.Mock(), mock.Mock()
        stops.due_stops.return_value = due(1)
        n.send_unchecked_stops_alert.side_effect = RuntimeError("webhook down")
        self.assertTrue(main.alert_if_stops_unchecked(stops, n, today=TUESDAY))


class RunStopChecksTest(unittest.TestCase):
    def test_checks_then_alarms_on_what_the_database_still_holds(self):
        stops, n = fake_stops(queue=due(2), left=due(1)), FakeNotifier()
        main.run_stop_checks(stops, n, budget_seconds=100, clock=_Clock(0), today=TUESDAY)
        self.assertEqual(stops.check_stop.call_count, 2)
        self.assertEqual(len(n.unchecked), 1)

    def test_the_alarm_still_runs_when_the_checks_raise(self):
        stops, n = mock.Mock(), FakeNotifier()
        stops.close_flat_stops.side_effect = RuntimeError("db down")
        stops.due_stops.return_value = due(1)
        main.run_stop_checks(stops, n, budget_seconds=100, today=TUESDAY)
        self.assertEqual(len(n.unchecked), 1)

    def test_never_raises(self):
        stops, n = mock.Mock(), mock.Mock()
        stops.close_flat_stops.side_effect = RuntimeError("db down")
        stops.due_stops.side_effect = RuntimeError("db down")
        main.run_stop_checks(stops, n, budget_seconds=100, today=TUESDAY)


def reached(n):
    return [{"stop_id": i + 1, "symbol": f"S{i}"} for i in range(n)]


class ReportTriggeredStopsTest(unittest.TestCase):
    def test_nothing_owed_sends_nothing(self):
        stops, n = mock.Mock(), FakeNotifier()
        stops.unalerted_triggers.return_value = []
        self.assertEqual(main.report_triggered_stops(stops, n, "john", today=TUESDAY), 0)
        self.assertEqual(n.alerts, [])
        stops.mark_alerted.assert_not_called()

    def test_sends_one_message_for_the_owners_stops_then_marks_them(self):
        stops, n = mock.Mock(), FakeNotifier()
        stops.unalerted_triggers.return_value = reached(2)
        self.assertEqual(main.report_triggered_stops(stops, n, "john", today=TUESDAY), 2)
        stops.unalerted_triggers.assert_called_once_with(owner="john")
        self.assertEqual(n.alerts, [(reached(2), 2, "2026-06-16")])
        stops.mark_alerted.assert_called_once_with(owner="john", stop_ids=[1, 2])

    def test_a_long_list_is_cut_but_every_stop_is_marked(self):
        stops, n = mock.Mock(), FakeNotifier()
        many = reached(main.STOP_ALERT_MESSAGE_LINES + 3)
        stops.unalerted_triggers.return_value = many
        main.report_triggered_stops(stops, n, "john", today=TUESDAY)
        self.assertEqual((len(n.alerts[0][0]), n.alerts[0][1]),
                         (main.STOP_ALERT_MESSAGE_LINES, len(many)))
        self.assertEqual(len(stops.mark_alerted.call_args.kwargs["stop_ids"]), len(many))

    def test_a_refused_message_leaves_the_alert_owed(self):
        stops, n = mock.Mock(), FakeNotifier(accepted=False)
        stops.unalerted_triggers.return_value = reached(1)
        self.assertEqual(main.report_triggered_stops(stops, n, "john", today=TUESDAY), 0)
        stops.mark_alerted.assert_not_called()

    def test_a_send_that_raises_leaves_the_alert_owed_and_does_not_raise(self):
        stops, n = mock.Mock(), mock.Mock()
        stops.unalerted_triggers.return_value = reached(1)
        n.send_stop_alert.side_effect = RuntimeError("webhook down")
        self.assertEqual(main.report_triggered_stops(stops, n, "john", today=TUESDAY), 0)
        stops.mark_alerted.assert_not_called()

    def test_a_failed_read_does_not_raise(self):
        stops = mock.Mock()
        stops.unalerted_triggers.side_effect = RuntimeError("db down")
        self.assertEqual(main.report_triggered_stops(stops, FakeNotifier(), "john"), 0)


# --------------------------------------------------------------------------
# 6. The messages
# --------------------------------------------------------------------------

def reached_stop(**overrides):
    stop = {"symbol": "INTC", "kind": "fixed", "stop_level": D("20.000000"),
            "trail_pct": None, "trigger_price": D("20.000000"), "triggered_session": TUESDAY}
    stop.update(overrides)
    return stop


class NotifierStopMessagesTest(unittest.TestCase):
    def setUp(self):
        self.notifier = Notifier.__new__(Notifier)
        self.notifier.discord_webhook_url = None
        self.sent = []
        self.answer = None
        self.notifier.send_notifications = (
            lambda embed, webhook_url=None: self.sent.append(embed) or self.answer)

    def test_a_stop_reached_at_its_level_reads_as_such(self):
        self.assertEqual(
            _stop_alert_line(reached_stop()),
            "INTC: traded down to its stop at $20.00 (fixed) on 2026-06-16")

    def test_a_stop_gapped_through_names_the_open(self):
        line = _stop_alert_line(reached_stop(
            kind="trailing", trail_pct=D("8.0000"), trigger_price=D("0.812500"),
            stop_level=D("0.900000")))
        self.assertEqual(
            line, "INTC: opened at $0.8125, below its stop at $0.9000 (trailing 8%) on 2026-06-16")

    def test_breach_alert_lists_the_stops_and_says_nothing_was_sold(self):
        self.assertTrue(self.notifier.send_stop_alert([reached_stop()], 3, "2026-06-16"))
        embed = self.sent[0]["embeds"][0]
        self.assertEqual(embed["title"], "3 stop(s) reached, reported 2026-06-16")
        self.assertIn("- INTC: traded down to its stop at $20.00", embed["description"])
        self.assertIn("- and 2 more", embed["description"])
        self.assertIn("Nothing was sold", embed["description"])
        self.assertNotIn("[PAPER]", self.sent[0]["content"])

    def test_breach_alert_is_unsent_only_when_the_send_says_so(self):
        self.answer = False
        self.assertFalse(self.notifier.send_stop_alert([reached_stop()], 1, "2026-06-16"))
        for sent in (True, None):  # None: a duplicate that went out earlier
            self.answer = sent
            self.assertTrue(self.notifier.send_stop_alert([reached_stop()], 1, "2026-06-16"))

    def test_unchecked_alarm_names_the_count_sample_and_oldest_check(self):
        self.notifier.send_unchecked_stops_alert(12, ["AAA", "BBB"], 5, "2026-06-12")
        text = self.sent[0]["embeds"][0]["description"]
        self.assertIn("12 active stop(s)", text)
        self.assertIn("across 5 symbol(s): AAA, BBB and 3 more", text)
        self.assertIn("last tested against the 2026-06-12 bar", text)

    def test_unchecked_alarm_says_when_a_stop_was_never_tested(self):
        self.notifier.send_unchecked_stops_alert(1, ["AAA"], 1, None)
        self.assertIn("never been tested", self.sent[0]["embeds"][0]["description"])

    def test_a_stop_sale_reads_as_one_in_the_paper_fills(self):
        fill = {"book_name": "Paper", "kind": "market", "side": "sell", "source": "stop",
                "filled_shares": D("5"), "symbol": "INTC", "fill_price": D("20"),
                "price_source": None}
        self.assertEqual(
            _paper_fill_line(fill), "Paper: sold by its stop 5 INTC at $20.00, its stop level")
        self.assertEqual(
            _paper_fill_line({**fill, "price_source": "session_open", "fill_price": D("19.5")}),
            "Paper: sold by its stop 5 INTC at $19.50, the opening price, below its stop")

    def test_an_ordinary_sale_reads_as_before(self):
        fill = {"book_name": "Paper", "kind": "market", "side": "sell", "source": "manual",
                "filled_shares": D("5"), "symbol": "INTC", "fill_price": D("20"),
                "price_source": "quote"}
        self.assertEqual(_paper_fill_line(fill), "Paper: sold 5 INTC at $20.00")


class SendNotificationsAnswerTest(unittest.TestCase):
    def send(self, status, webhook="https://discord.invalid/hook", logged=""):
        notifier = Notifier.__new__(Notifier)
        notifier.discord_webhook_url = webhook
        embed = {"embeds": [{"title": f"zz stop check answer {status}"}]}
        with mock.patch("notifier.os.path.exists", return_value=bool(logged)), \
                mock.patch("builtins.open", mock.mock_open(read_data=logged)), \
                mock.patch("notifier.requests.post") as post:
            post.return_value.status_code = status
            post.return_value.text = ""
            self.posts = post
            return notifier.send_notifications(embed)

    def test_true_when_discord_took_it_and_false_when_it_refused(self):
        self.assertIs(self.send(204), True)
        self.assertIs(self.send(500), False)

    def test_false_when_there_is_no_webhook_to_send_it_to(self):
        self.assertIs(self.send(204, webhook=None), False)
        self.posts.assert_not_called()

    def test_none_only_for_a_duplicate_that_already_went_out(self):
        self.assertIsNone(self.send(204, logged="zz stop check answer 204"))
        self.posts.assert_not_called()

    def test_a_breach_alert_with_no_webhook_is_not_counted_as_sent(self):
        notifier = Notifier.__new__(Notifier)
        notifier.discord_webhook_url = None
        with mock.patch("notifier.os.path.exists", return_value=False), \
                mock.patch("builtins.open", mock.mock_open()):
            self.assertFalse(notifier.send_stop_alert([reached_stop()], 1, "2026-06-16"))


# --------------------------------------------------------------------------
# 7. The whole step on real services
# --------------------------------------------------------------------------

class StepOnRealServicesTest(_Base):
    """Real `StopsService`, `OrdersService` and `PricesService` over the real
    `ohlcv` table. Only the Yahoo calls are stubbed."""

    def setUp(self):
        super().setUp()
        history = mock.Mock()
        history.fetch_history.return_value = pd.DataFrame()
        self.real_prices = PricesService(
            ohlcv_repository=OhlcvRepository(), yfinance_gateway=history,
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())
        self.orders = OrdersService(self.order_rows, self.books, StubGateway(), self.real_prices)
        self.service = StopsService(self.repo, self.books, self.real_prices, self.orders)

    def run_step(self, today):
        n = FakeNotifier()
        mine = lambda today: [  # noqa: E731
            s for s in StopsService.due_stops(self.service, today=today)
            if s["owner"] == OWNER]
        with mock.patch.object(self.service, "due_stops", side_effect=mine):
            main.run_stop_checks(self.service, n, budget_seconds=60, today=today)
        return n

    def set_stops(self):
        # Set against the stub's close of 100, like every other test here.
        with mock.patch.object(self.service, "_prices", self.prices):
            return self.set_stop(), self.set_stop(book_id=self.paper)

    def test_a_reached_stop_alerts_for_the_real_book_and_sells_for_the_paper_one(self):
        real, paper = self.set_stops()
        OhlcvRepository().store_bars(SYMBOL, "1d", daily_bars({
            MONDAY: (99, 101, 60, 100), TUESDAY: (95, 96, 88, 94)}))

        n = self.run_step(TUESDAY)

        self.assertEqual(n.unchecked, [])
        self.assertEqual(
            (self.stored(real)["status"], self.stored(paper)["status"]),
            ("triggered", "triggered"))
        self.assertEqual(self.open_lots(self.paper), [])
        self.assertEqual(len(self.open_lots()), 1)
        fills = self.orders.fills_on(owner=OWNER, day=datetime.date.today())
        self.assertEqual([(f["source"], f["fill_price"]) for f in fills],
                         [("stop", D("90.000000"))])

        self.assertEqual(main.report_triggered_stops(self.service, n, OWNER, today=TUESDAY), 1)
        self.assertEqual([s["stop_id"] for s in n.alerts[0][0]], [real["stop_id"]])
        # A second run that evening reports nothing twice.
        self.assertEqual(main.report_triggered_stops(self.service, n, OWNER, today=TUESDAY), 0)
        self.assertEqual(len(n.alerts), 1)

    def test_a_stop_with_no_bar_for_today_stays_due_and_the_alarm_says_so(self):
        self.set_stops()
        OhlcvRepository().store_bars(SYMBOL, "1d", daily_bars({MONDAY: (99, 101, 60, 100)}))

        n = self.run_step(TUESDAY)

        self.assertEqual(n.unchecked, [(2, [SYMBOL], 1, None)])


if __name__ == "__main__":
    unittest.main()
