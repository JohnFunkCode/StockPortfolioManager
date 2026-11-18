"""`PerformanceService` against the real database, with prices and FX stubbed
(#431 plan step 5.1). No network: `tests/__init__.py` blocks Yahoo, and every
close and rate here comes from a stub."""

import datetime
import unittest
from decimal import Decimal
from unittest import mock

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.repositories.book_snapshots_repository import BookSnapshotsRepository  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.performance import PerformanceService  # noqa: E402
from quantcore.services.prices import CLOSE_ON_LOOKBACK_DAYS, PricesService  # noqa: E402

from tests.test_orders_repository import purge  # noqa: E402

OWNER = "zz_perf_svc_owner"
OTHER = "zz_perf_svc_other"
SYMBOL = "ZZPERFAAA"
FOREIGN = "ZZPERFGBP"
MON = datetime.date(2026, 11, 9)
TUE = datetime.date(2026, 11, 10)
WED = datetime.date(2026, 11, 11)
D = Decimal


class StubPrices:
    """`close_on` from a table of `{symbol: (close, bar session)}`."""

    def __init__(self, closes):
        self.closes = closes
        self.asked = []

    def close_on(self, symbol, session):
        self.asked.append((symbol, session))
        close, day = self.closes.get(symbol, (None, None))
        return {"close": close, "session": day}


class StubFx:
    def __init__(self, closes=None, error=None):
        self.closes = closes or {}
        self.error = error
        self.calls = []

    def fx_daily_closes(self, currency, start, end):
        self.calls.append((currency, start, end))
        if self.error:
            raise self.error
        return self.closes.get(currency, {})


def lot(symbol=SYMBOL, quantity=10, price=50.0, day=MON, currency="USD", **extra):
    row = {"name": "Perf Co", "symbol": symbol, "purchase_price": price, "quantity": quantity,
           "purchase_date": day.isoformat(), "trade_date": day.isoformat(), "currency": currency}
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
        self.prices = StubPrices({SYMBOL: (60.0, TUE)})
        self.fx = StubFx()
        self.service = PerformanceService(
            self.snapshots, self.books, self.cash, self.lots, self.prices, fx_gateway=self.fx)

    def paper_book(self, cash=1000):
        return self.books.create_paper_book(
            owner=OWNER, name="Paper", starting_cash=cash, opened_on=MON)

    def owned(self, owner=OWNER):
        """`due_snapshots` covers every owner in the database; these tests
        look only at their own (plan section 7, 4.2)."""
        return [d for d in self.service.due_snapshots(today=TUE) if d["owner"] == owner]


class SnapshotBookTest(_Base):
    def test_a_paper_book_is_cash_plus_holdings_at_the_close(self):
        paper = self.paper_book()
        self.lots.add_position(OWNER, lot(), book_id=paper)
        row = self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual(row["cash"], D("500.00"))
        self.assertEqual(row["holdings_value"], D("600.00"))
        self.assertEqual(row["book_value"], D("1100.00"))
        stored = self.snapshots.list_snapshots(owner=OWNER, book_id=paper)
        self.assertEqual([(r["session_date"], r["book_value"]) for r in stored],
                         [(TUE, D("1100.00"))])
        self.assertEqual(self.prices.asked, [(SYMBOL, TUE)])
        self.assertEqual(self.fx.calls, [])

    def test_a_real_book_with_no_opening_balance_writes_nothing(self):
        self.lots.add_position(OWNER, lot())
        self.assertIsNone(self.service.snapshot_book(owner=OWNER, session=TUE))
        self.assertEqual(self.snapshots.list_snapshots(owner=OWNER), [])

    def test_a_real_book_from_its_opening_balance(self):
        self.lots.add_position(OWNER, lot(day=datetime.date(2026, 1, 5)))
        self.cash.set_opening_balance(owner=OWNER, entry_date=MON, amount=D("200.00"))
        row = self.service.snapshot_book(owner=OWNER, session=TUE)
        self.assertEqual(row["book_value"], D("800.00"))
        self.assertEqual(self.snapshots.list_snapshots(owner=OWNER)[0]["book_value"],
                         D("800.00"))

    def test_the_flow_counts_from_the_previous_snapshot(self):
        self.cash.set_opening_balance(owner=OWNER, entry_date=MON, amount=D("200.00"))
        self.service.snapshot_book(owner=OWNER, session=MON)
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=TUE, amount=D("50.00"))
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date=MON, amount=D("7.00"))
        row = self.service.snapshot_book(owner=OWNER, session=TUE)
        # The MON deposit was typed in after MON's row was taken. It is in
        # TUE's cash, so it is in TUE's flow too, and not a gain.
        self.assertEqual(row["flow"], D("57.00"))
        self.assertEqual(row["cash"], D("257.00"))
        self.assertEqual(row["contributed"], D("257.00"))

    def test_a_non_usd_holding_converts_at_the_sessions_rate(self):
        paper = self.paper_book(cash=0)
        self.prices.closes[FOREIGN] = (10.0, TUE)
        self.lots.add_position(OWNER, lot(FOREIGN, quantity=3, currency="GBP",
                                          acquisition_type="SEED"), book_id=paper)
        self.fx.closes = {"GBP": {MON: 1.2, TUE: 1.25}}
        row = self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual(row["holdings_value"], D("37.50"))
        self.assertEqual(self.fx.calls, [("GBP", TUE - datetime.timedelta(days=7), TUE)])

    def test_a_failed_rate_lookup_is_counted_and_never_one(self):
        paper = self.paper_book(cash=0)
        self.prices.closes[FOREIGN] = (10.0, TUE)
        self.lots.add_position(OWNER, lot(FOREIGN, currency="GBP", acquisition_type="SEED"),
                               book_id=paper)
        self.fx.error = RuntimeError("yahoo down")
        row = self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertIsNone(row["book_value"])
        self.assertEqual(row["fx_missing_count"], 1)

    def test_a_price_error_writes_nothing(self):
        paper = self.paper_book()
        self.lots.add_position(OWNER, lot(), book_id=paper)

        def boom(symbol, session):
            raise RuntimeError("fetch failed")

        self.prices.close_on = boom
        with self.assertRaises(RuntimeError):
            self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual(self.snapshots.list_snapshots(owner=OWNER, book_id=paper), [])

    def test_another_owners_book_is_not_found(self):
        paper = self.paper_book()
        with self.assertRaises(BookNotFoundError):
            self.service.snapshot_book(owner=OTHER, book_id=paper, session=TUE)
        with self.assertRaises(BookNotFoundError):
            self.service.list_snapshots(owner=OTHER, book_id=paper)
        self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual(self.service.list_snapshots(owner=OTHER), [])
        self.assertEqual(len(self.service.list_snapshots(owner=OWNER, book_id=paper)), 1)


class CloseOnTest(unittest.TestCase):
    """`PricesService.close_on`, over a stubbed `closed_bars_since` (the
    #573 review): the newest closed bar on or before the session, within
    the lookback window."""

    def setUp(self):
        self.prices = PricesService(
            ohlcv_repository=mock.Mock(), yfinance_gateway=mock.Mock(),
            options_repository=mock.Mock(), sentiment_repository=mock.Mock())
        self.bars = []
        self.asked = []

        def closed_bars_since(symbol, since):
            self.asked.append((symbol, since))
            return [b for b in self.bars if b["session"] >= since]

        self.prices.closed_bars_since = closed_bars_since

    def bar(self, day, close):
        self.bars.append({"session": day, "open": close, "high": close, "low": close,
                          "close": close})

    def test_the_sessions_own_close(self):
        self.bar(MON, 10.0)
        self.bar(TUE, 11.0)
        self.assertEqual(self.prices.close_on(SYMBOL, TUE), {"close": 11.0, "session": TUE})
        self.assertEqual(
            self.asked, [(SYMBOL, TUE - datetime.timedelta(days=CLOSE_ON_LOOKBACK_DAYS))])

    def test_an_earlier_close_when_the_session_has_none(self):
        self.bar(MON, 10.0)
        self.assertEqual(self.prices.close_on(SYMBOL, TUE), {"close": 10.0, "session": MON})

    def test_a_later_bar_is_never_used(self):
        self.bar(MON, 10.0)
        self.bar(WED, 12.0)
        self.assertEqual(self.prices.close_on(SYMBOL, TUE), {"close": 10.0, "session": MON})

    def test_nothing_in_the_window_is_no_close(self):
        self.bar(TUE - datetime.timedelta(days=CLOSE_ON_LOOKBACK_DAYS + 1), 9.0)
        self.assertEqual(self.prices.close_on(SYMBOL, TUE), {"close": None, "session": None})

    def test_a_fetch_error_reaches_the_caller(self):
        def boom(symbol, since):
            raise RuntimeError("yahoo down")

        self.prices.closed_bars_since = boom
        with self.assertRaises(RuntimeError):
            self.prices.close_on(SYMBOL, TUE)


class DueSnapshotsTest(_Base):
    def test_owed_until_a_complete_row_lands(self):
        paper = self.paper_book()
        self.lots.add_position(OWNER, lot(), book_id=paper)
        self.assertEqual(self.owned(), [
            {"owner": OWNER, "book_id": paper, "kind": "paper", "reason": "missing"},
        ])
        self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual(self.owned(), [])

    def test_a_real_book_with_no_opening_balance_owes_nothing(self):
        self.lots.add_position(OWNER, lot())
        self.assertEqual(self.owned(), [])
        self.cash.set_opening_balance(owner=OWNER, entry_date=WED, amount=D("1.00"))
        self.assertEqual(self.owned(), [])
        self.cash.set_opening_balance(owner=OWNER, entry_date=MON, amount=D("1.00"))
        self.assertEqual([d["kind"] for d in self.owned()], ["real"])

    def test_an_incomplete_or_stale_row_is_still_owed(self):
        paper = self.paper_book()
        self.lots.add_position(OWNER, lot(), book_id=paper)
        self.prices.closes = {}
        self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual([d["reason"] for d in self.owned()], ["incomplete"])
        self.prices.closes = {SYMBOL: (60.0, MON)}
        self.service.snapshot_book(owner=OWNER, book_id=paper, session=TUE)
        self.assertEqual([d["reason"] for d in self.owned()], ["stale"])

    def test_an_archived_book_owes_nothing(self):
        paper = self.paper_book()
        self.books.archive_book(owner=OWNER, book_id=paper)
        self.assertEqual(self.owned(), [])


if __name__ == "__main__":
    unittest.main()
