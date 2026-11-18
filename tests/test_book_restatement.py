"""Restating a book's history (#431 plan step 5.2, decisions 85 and 88 as John
revised them): the marks the owner's writes set, the rebuild that clears
them, and the history backfill. Against the real database, with prices and FX
stubbed: `tests/__init__.py` blocks Yahoo."""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.analytics.performance import time_weighted_return  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.book_snapshots_repository import BookSnapshotsRepository  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.performance import PerformanceService  # noqa: E402

from tests.test_orders_repository import purge  # noqa: E402

OWNER = "zz_restate_owner"
OTHER = "zz_restate_other"
SYMBOL = "ZZRSTAAA"
FOREIGN = "ZZRSTGBP"
FRI = datetime.date(2026, 11, 6)
MON = datetime.date(2026, 11, 9)
TUE = datetime.date(2026, 11, 10)
WED = datetime.date(2026, 11, 11)
D = Decimal


class StubPrices:
    """`close_on` and `closed_bars_since` from `{symbol: {session: close}}`."""

    def __init__(self, closes, error=None):
        self.closes = closes
        self.error = error
        self.since = []

    def close_on(self, symbol, session):
        days = [d for d in self.closes.get(symbol, {}) if d <= session]
        if not days:
            return {"close": None, "session": None}
        return {"close": self.closes[symbol][max(days)], "session": max(days)}

    def closed_bars_since(self, symbol, since):
        self.since.append((symbol, since))
        if self.error:
            raise self.error
        return [{"session": d, "close": c}
                for d, c in sorted(self.closes.get(symbol, {}).items()) if d >= since]


class StubFx:
    def __init__(self, closes=None, error=None):
        self.closes = closes or {}
        self.error = error

    def fx_daily_closes(self, currency, start, end):
        if self.error:
            raise self.error
        return self.closes.get(currency, {})


def lot(day=MON, quantity=10, price=50.0, symbol=SYMBOL, **extra):
    row = {"name": "Restate Co", "symbol": symbol, "purchase_price": price,
           "quantity": quantity, "purchase_date": day.isoformat(),
           "trade_date": day.isoformat(), "currency": "USD"}
    row.update(extra)
    return row


class _Base(unittest.TestCase):
    def setUp(self):
        purge((OWNER, OTHER), (SYMBOL, FOREIGN))
        self.addCleanup(purge, (OWNER, OTHER), (SYMBOL, FOREIGN))
        self.books = BooksRepository()
        self.cash = CashRepository()
        self.lots = PortfolioRepository()
        self.snapshots = BookSnapshotsRepository()
        self.prices = StubPrices({SYMBOL: {MON: 50.0, TUE: 50.0, WED: 55.0}})
        self.fx = StubFx()
        self.service = PerformanceService(
            self.snapshots, self.books, self.cash, self.lots, self.prices, fx_gateway=self.fx)

    def real_book_with_rows(self, owner=OWNER, sessions=(MON, TUE, WED)):
        """A real book that opened Monday with $1,000, one row a session."""
        self.cash.set_opening_balance(owner=owner, entry_date=MON, amount=D("1000.00"))
        for session in sessions:
            self.service.snapshot_book(owner=owner, session=session)
        self.assertIsNone(self.snapshots.restate_mark(owner=owner), "the nightly row never marks")

    def mark(self, owner=OWNER, book_id=None):
        return self.snapshots.restate_mark(owner=owner, book_id=book_id)

    def rows(self, owner=OWNER, book_id=None):
        return {r["session_date"]: r
                for r in self.snapshots.list_snapshots(owner=owner, book_id=book_id)}


class MarkTest(_Base):
    """Every write the owner makes to a book's cash or lots marks it, dated
    on or before its newest row (`restate_sql`)."""

    def test_a_late_deposit_marks_the_book_from_its_date(self):
        self.real_book_with_rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("100"))
        self.assertEqual(self.mark(), TUE)

    def test_a_write_after_the_newest_row_does_not(self):
        self.real_book_with_rows(sessions=(MON,))
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("100"))
        self.lots.add_position(OWNER, lot(day=TUE))
        self.assertIsNone(self.mark())

    def test_a_book_with_no_rows_has_nothing_to_restate(self):
        self.cash.set_opening_balance(owner=OWNER, entry_date=MON, amount=D("1000"))
        self.lots.add_position(OWNER, lot())
        self.assertIsNone(self.mark())

    def test_a_mark_only_moves_earlier(self):
        self.real_book_with_rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("100"))
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=WED, amount=D("100"))
        self.assertEqual(self.mark(), TUE)
        self.cash.add_entry(owner=OWNER, kind="withdrawal", entry_date=MON, amount=D("-10"))
        self.assertEqual(self.mark(), MON)

    def test_deleting_an_entry_marks_from_its_date(self):
        self.real_book_with_rows(sessions=(MON,))
        entry_id = self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE,
                                       amount=D("100"))
        self.service.snapshot_book(owner=OWNER, session=TUE)
        self.assertIsNone(self.mark())
        self.assertTrue(self.cash.delete_entry(owner=OWNER, entry_id=entry_id))
        self.assertEqual(self.mark(), TUE)

    def test_moving_the_opening_balance_marks_from_the_earlier_date(self):
        self.real_book_with_rows()
        self.cash.set_opening_balance(owner=OWNER, entry_date=TUE, amount=D("1000.00"))
        self.assertEqual(self.mark(), MON)

    def test_adding_editing_and_deleting_a_lot_mark_from_its_dates(self):
        self.real_book_with_rows()
        lot_id = self.lots.add_position(OWNER, lot(day=WED))
        self.assertEqual(self.mark(), WED)
        self._clear()
        # An edit that moves the trade date later restates from the earlier.
        self.assertTrue(self.lots.update_lot(OWNER, lot_id, {"trade_date": "2026-11-12"}))
        self.assertEqual(self.mark(), WED)
        self._clear()
        self.assertTrue(self.lots.update_lot(OWNER, lot_id, {"trade_date": TUE.isoformat()}))
        self.assertEqual(self.mark(), TUE)
        self._clear()
        self.assertTrue(self.lots.delete_lot(OWNER, lot_id))
        self.assertEqual(self.mark(), TUE)

    def test_a_sale_marks_from_its_date(self):
        self.real_book_with_rows()
        lot_id = self.lots.add_position(OWNER, lot(day=datetime.date(2026, 11, 12)))
        self.assertIsNone(self.mark())
        self.lots.close_lots(OWNER, [(lot_id, D("4"))], 55.0, WED.isoformat())
        self.assertEqual(self.mark(), WED)

    def test_removing_a_symbol_and_an_import_mark_from_their_earliest_lot(self):
        self.real_book_with_rows()
        self.lots.add_position(OWNER, lot(day=datetime.date(2026, 11, 12)))
        self.assertEqual(self.lots.remove_position(OWNER, SYMBOL), 1)
        self.assertIsNone(self.mark(), "dated after the newest row")
        self.lots.add_position(OWNER, lot(day=WED))
        self._clear()
        self.assertEqual(self.lots.remove_position(OWNER, SYMBOL), 1)
        self.assertEqual(self.mark(), WED)
        self._clear()
        self.lots.replace_owner_positions(OWNER, [lot(day=TUE)])
        self.assertEqual(self.mark(), TUE)

    def test_a_filled_fx_rate_marks_from_the_lot(self):
        self.real_book_with_rows()
        lot_id = self.lots.add_position(OWNER, lot(day=datetime.date(2026, 11, 12),
                                                   symbol=FOREIGN, currency="GBP"))
        # Moved back a day behind the repository's back, so only the fill marks.
        with closing(get_connection()) as conn:
            conn.execute("UPDATE positions SET trade_date = :d, purchase_date = :t "
                         "WHERE owner = :owner AND position_id = :id",
                         {"d": TUE, "t": TUE.isoformat(), "owner": OWNER, "id": lot_id})
            conn.commit()
        self.assertIsNone(self.mark())
        self.assertTrue(self.lots.fill_lot_fx_rate(OWNER, lot_id, D("1.25")))
        self.assertEqual(self.mark(), TUE)

    def test_a_fill_on_an_order_connection_never_marks(self):
        self.real_book_with_rows()
        with closing(get_connection()) as conn:
            self.lots.add_lot_on(conn, OWNER, lot(day=MON))
            conn.commit()
        self.assertIsNone(self.mark())

    def test_another_owner_and_another_book_are_never_marked(self):
        self.real_book_with_rows()
        self.real_book_with_rows(owner=OTHER)
        paper = self.books.create_paper_book(owner=OWNER, name="P", starting_cash=100,
                                             opened_on=MON)
        self.service.snapshot_book(owner=OWNER, book_id=paper, session=WED)
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("1"))
        self.assertEqual(self.mark(), TUE)
        self.assertIsNone(self.mark(owner=OTHER))
        self.assertIsNone(self.mark(book_id=paper))
        self.assertIsNone(self.mark(owner=OTHER, book_id=paper), "not OTHER's book")

    def _clear(self):
        with self.snapshots.restating(owner=OWNER) as tx:
            tx.clear_mark()


class RestateBookTest(_Base):
    def test_a_late_deposit_is_placed_on_its_date_and_the_rows_say_so(self):
        self.real_book_with_rows()
        before = self.rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("500"))
        result = self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual(result["restated_from"], TUE)
        self.assertTrue(result["cleared"])
        self.assertIsNone(self.mark())
        self.assertEqual((result["restated"], result["unchanged"], result["inserted"]), (2, 0, 0))
        after = self.rows()
        self.assertEqual(after[MON], before[MON], "a row before the change is untouched")
        self.assertIsNone(after[MON]["restated_at"])
        self.assertEqual((after[TUE]["flow"], after[TUE]["book_value"]),
                         (D("500.00"), D("1500.00")))
        self.assertEqual(after[WED]["flow"], D("0.00"))
        self.assertIsNotNone(after[TUE]["restated_at"])
        self.assertEqual(after[TUE]["taken_at"], before[TUE]["taken_at"])
        self.assertEqual(time_weighted_return(after.values())["twr"], D("0E-8"),
                         "a deposit is not a gain")

    def test_a_late_transfer_is_a_flow_on_its_trade_date(self):
        self.real_book_with_rows()
        self.lots.add_position(OWNER, lot(day=TUE, acquisition_type="TRANSFER"))
        self.service.restate_book(owner=OWNER, through=WED)
        rows = self.rows()
        self.assertEqual((rows[TUE]["flow"], rows[TUE]["holdings_value"]),
                         (D("500.00"), D("500.00")))
        self.assertEqual((rows[WED]["flow"], rows[WED]["holdings_value"]),
                         (D("0.00"), D("550.00")))

    def test_an_unchanged_row_is_left_alone(self):
        self.real_book_with_rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=WED, amount=D("1"))
        self.cash.delete_entry(owner=OWNER, entry_id=self.cash.list_entries(owner=OWNER)[-1]
                               ["entry_id"])
        result = self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual((result["restated"], result["unchanged"]), (0, 1))
        self.assertIsNone(self.rows()[WED]["restated_at"])

    def test_nothing_is_rebuilt_before_the_first_stored_row(self):
        self.cash.set_opening_balance(owner=OWNER, entry_date=FRI, amount=D("1000.00"))
        self.service.snapshot_book(owner=OWNER, session=TUE)
        self.service.snapshot_book(owner=OWNER, session=WED)
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=MON, amount=D("5"))
        self.assertEqual(self.mark(), MON)
        result = self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual(sorted(self.rows()), [TUE, WED], "the backfill's job, not this")
        self.assertEqual(result["sessions"], 2)
        # Tuesday is still the base (decision 83): the deposit is in its
        # value, and the base's flow is never used.
        self.assertEqual(self.rows()[TUE]["book_value"], D("1005.00"))
        self.assertIsNotNone(self.rows()[TUE]["restated_at"])

    def test_a_hole_in_the_range_is_filled(self):
        self.real_book_with_rows(sessions=(MON, WED))
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("5"))
        result = self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual((result["inserted"], result["restated"]), (1, 1))
        self.assertEqual(sorted(self.rows()), [MON, TUE, WED])
        self.assertIsNone(self.rows()[TUE]["restated_at"], "a new row corrected nothing")

    def test_a_session_the_book_has_no_value_for_any_more_loses_its_row(self):
        self.real_book_with_rows()
        self.cash.set_opening_balance(owner=OWNER, entry_date=TUE, amount=D("1000.00"))
        result = self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(sorted(self.rows()), [TUE, WED])

    def test_a_failed_fetch_writes_nothing_and_keeps_the_mark(self):
        self.lots.add_position(OWNER, lot())
        self.real_book_with_rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("500"))
        before = self.rows()
        self.prices.error = RuntimeError("yahoo")
        with self.assertRaises(RuntimeError):
            self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.mark(), TUE)

    def test_a_failed_fx_fetch_keeps_the_mark_too(self):
        self.lots.add_position(OWNER, lot(symbol=FOREIGN, currency="GBP"))
        self.prices.closes[FOREIGN] = {MON: 40.0, TUE: 40.0, WED: 40.0}
        self.real_book_with_rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("5"))
        self.fx.error = RuntimeError("fx")
        with self.assertRaises(RuntimeError):
            self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual(self.mark(), TUE)

    def test_an_unmarked_book_is_not_rebuilt(self):
        self.real_book_with_rows()
        self.assertEqual(self.service.restate_book(owner=OWNER, through=WED),
                         {"restated_from": None, "sessions": 0})
        self.assertEqual(self.prices.since, [])

    def test_another_owners_book_is_refused(self):
        self.real_book_with_rows(owner=OTHER)
        paper = self.books.create_paper_book(owner=OTHER, name="P", starting_cash=1,
                                             opened_on=MON)
        with self.assertRaises(BookNotFoundError):
            self.service.restate_book(owner=OWNER, book_id=paper, through=WED)

    def test_due_snapshots_names_a_marked_book_restate(self):
        self.real_book_with_rows()
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("5"))
        owed = [d for d in self.service.due_snapshots(today=WED) if d["owner"] == OWNER]
        self.assertEqual([(d["reason"], d["restate_from"]) for d in owed], [("restate", TUE)])
        self.service.restate_book(owner=OWNER, through=WED)
        self.assertEqual(
            [d for d in self.service.due_snapshots(today=WED) if d["owner"] == OWNER], [])


class BackfillBookTest(_Base):
    def setUp(self):
        super().setUp()
        self.cash.set_opening_balance(owner=OWNER, entry_date=FRI, amount=D("1000.00"))
        self.lots.add_position(OWNER, lot(day=MON))
        self.prices.closes[SYMBOL][FRI] = 48.0

    def test_a_dry_run_reports_and_writes_nothing(self):
        result = self.service.backfill_book(owner=OWNER, through=WED)
        self.assertEqual(result["start"], FRI)
        self.assertEqual((result["sessions"], result["inserted"]), (4, 4))
        self.assertEqual(self.rows(), {})

    def test_an_applied_run_writes_the_history_from_the_opening_balance(self):
        result = self.service.backfill_book(owner=OWNER, through=WED, apply=True)
        rows = self.rows()
        self.assertEqual(sorted(rows), [FRI, MON, TUE, WED])
        self.assertEqual(rows[FRI]["book_value"], D("1000.00"))
        self.assertEqual((rows[MON]["cash"], rows[MON]["holdings_value"]),
                         (D("500.00"), D("500.00")))
        self.assertEqual(rows[MON]["flow"], D("0.00"), "a buy is not a flow")
        self.assertEqual(rows[WED]["book_value"], D("1050.00"))
        self.assertEqual(result["unvalued"], [])
        again = self.service.backfill_book(owner=OWNER, through=WED, apply=True)
        self.assertEqual((again["inserted"], again["restated"], again["unchanged"]), (0, 0, 4))

    def test_it_reports_the_days_it_could_not_value(self):
        del self.prices.closes[SYMBOL][MON]
        del self.prices.closes[SYMBOL][TUE]
        self.prices.closes[SYMBOL][datetime.date(2026, 10, 1)] = 1.0
        result = self.service.backfill_book(owner=OWNER, through=WED, apply=True)
        self.assertEqual(result["stale"], [MON, TUE], "priced from Friday's close")
        del self.prices.closes[SYMBOL][FRI]
        result = self.service.backfill_book(owner=OWNER, through=WED, apply=True)
        self.assertEqual(result["unvalued"], [MON, TUE])

    def test_a_run_that_covers_the_mark_clears_it(self):
        self.service.snapshot_book(owner=OWNER, session=WED)
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("5"))
        self.assertEqual(self.mark(), TUE)
        dry = self.service.backfill_book(owner=OWNER, through=WED)
        self.assertTrue(dry["cleared"], "what an applied run would do")
        self.assertEqual(self.mark(), TUE, "but a dry run leaves it")
        partial = self.service.backfill_book(owner=OWNER, start=WED, through=WED, apply=True)
        self.assertFalse(partial["cleared"], "it started after the mark")
        self.assertTrue(self.service.backfill_book(owner=OWNER, through=WED,
                                                   apply=True)["cleared"])
        self.assertIsNone(self.mark())

    def test_a_book_with_no_opening_balance_does_nothing(self):
        result = self.service.backfill_book(owner=OTHER, through=WED, apply=True)
        self.assertEqual(result, {"start": None, "sessions": 0})


if __name__ == "__main__":
    unittest.main()
