"""A book's performance against SPY, the Compare books view (#431 plan step
5.3) and the read-only book tool's overview (step 5.4):
`PerformanceService.book_performance`, `compare_books` and `books_overview`
against the real database, and their routes through TestClient. No network: SPY's
bars come from a stub, and `tests/__init__.py` blocks Yahoo.

The windows are rolling and end today, so every date here is counted back
from the market date.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.analytics.market_time import market_date  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.book_snapshots_repository import BookSnapshotsRepository  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.performance import PerformanceService  # noqa: E402

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402
from tests.test_orders_repository import purge  # noqa: E402

OWNER = "zz_book_perf_owner"
OTHER = "zz_book_perf_other"
IDENTITY = "zz_book_perf_owner@example.com"
SYMBOL = "ZZBKPERF"
D = Decimal
TODAY = market_date()


def ago(days):
    return TODAY - datetime.timedelta(days=days)


def snap(day, value, flow="0.00", cash="100.00"):
    return {
        "session_date": day, "cash": D(cash), "holdings_value": D(value) - D(cash),
        "book_value": D(value), "flow": D(flow), "contributed": D("1000.00"),
        "holding_count": 1, "unpriced_count": 0, "stale_count": 0, "fx_missing_count": 0,
    }


class StubPrices:
    """`closed_bars_since` from a fixed list of SPY bars, or an error."""

    def __init__(self, bars=(), error=None):
        self.bars = list(bars)
        self.error = error
        self.asked = []

    def closed_bars_since(self, symbol, since):
        self.asked.append((symbol, since))
        if self.error:
            raise self.error
        return [{"session": d, "open": c, "high": c, "low": c, "close": c}
                for d, c in self.bars if d >= since]


def _purge():
    purge((OWNER, OTHER), (SYMBOL,))
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM owner_identities WHERE identity = %s", (IDENTITY,))
        conn.commit()


class _Base(unittest.TestCase):
    SPY = [(ago(40), 400.0), (ago(20), 420.0), (ago(10), 440.0)]

    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.snapshots = BookSnapshotsRepository()
        self.lots = PortfolioRepository()
        self.prices = StubPrices(self.SPY)
        self.service = PerformanceService(
            self.snapshots, self.books, CashRepository(), self.lots, self.prices)
        self.real = self.books.ensure_real_book(owner=OWNER)
        self.paper = self.books.create_paper_book(owner=OWNER, name="Momentum",
                                                  starting_cash=1000)

    def rows(self, book_id=None, owner=OWNER):
        for day, value in ((ago(40), "1000.00"), (ago(20), "1100.00"), (ago(10), "1210.00")):
            self.snapshots.upsert_snapshot(owner=owner, snapshot=snap(day, value),
                                           book_id=book_id)


class BookPerformanceTest(_Base):
    def test_the_window_is_measured_from_the_row_before_it(self):
        self.rows()
        result = self.service.book_performance(owner=OWNER, period="month")
        # ago(40) is outside the month but is its base, so the return covers
        # the whole month: 1000 -> 1210.
        self.assertEqual(result["twr"], D("0.21000000"))
        self.assertEqual(result["benchmark_return"], D("0.10000000"))
        self.assertEqual(result["vs_benchmark"], D("0.11000000"))
        self.assertEqual(result["first_session"], ago(40))
        self.assertEqual((result["kind"], result["book_id"], result["period"]),
                         ("real", self.real, "month"))
        self.assertEqual(result["start_date"], ago(30).isoformat())
        # SPY is read from the lookback before the base row.
        self.assertEqual(self.prices.asked, [("SPY", ago(47))])

    def test_all_has_no_base_and_starts_at_the_first_row(self):
        self.rows()
        result = self.service.book_performance(owner=OWNER, period="all")
        self.assertEqual(result["twr"], D("0.21000000"))
        self.assertIsNone(result["start_date"])

    def test_a_paper_book_by_id_and_the_real_book_is_not_mixed_in(self):
        self.rows(book_id=self.paper)
        result = self.service.book_performance(owner=OWNER, period="year", book_id=self.paper)
        self.assertEqual((result["kind"], result["name"]), ("paper", "Momentum"))
        self.assertEqual(len(result["series"]), 3)
        self.assertEqual(self.service.book_performance(owner=OWNER)["series"], [])

    def test_another_owners_book_is_not_found(self):
        other_paper = self.books.create_paper_book(owner=OTHER, name="Theirs", starting_cash=5)
        self.rows(book_id=other_paper, owner=OTHER)
        with self.assertRaises(BookNotFoundError):
            self.service.book_performance(owner=OWNER, book_id=other_paper)

    def test_another_owners_rows_are_not_read_for_the_real_book(self):
        self.books.ensure_real_book(owner=OTHER)
        self.rows(owner=OTHER)
        self.assertEqual(self.service.book_performance(owner=OWNER)["series"], [])

    def test_an_owner_with_no_book_gets_an_empty_real_book_and_none_is_created(self):
        result = self.service.book_performance(owner=OTHER)
        self.assertEqual((result["kind"], result["book_id"], result["series"]),
                         ("real", None, []))
        self.assertEqual(self.books.list_books(owner=OTHER), [])
        # Nothing to compare, so SPY is not read.
        self.assertEqual(self.prices.asked, [])

    def test_a_failed_benchmark_read_leaves_it_out(self):
        self.rows()
        self.prices.error = RuntimeError("yahoo said no")
        with self.assertLogs("quantcore.services.performance", "WARNING") as logs:
            result = self.service.book_performance(owner=OWNER, period="year")
        self.assertFalse(result["benchmark_available"])
        self.assertIsNone(result["vs_benchmark"])
        self.assertEqual(result["twr"], D("0.21000000"))
        # The class only: an exception's text never reaches a log.
        self.assertNotIn("yahoo said no", "\n".join(logs.output))

    def test_an_unknown_period_is_refused(self):
        with self.assertRaises(ValueError):
            self.service.book_performance(owner=OWNER, period="ytd")

    def test_realized_by_source_over_the_window(self):
        lot_id = self.lots.add_position(OWNER, {
            "name": "Perf Co", "symbol": SYMBOL, "purchase_price": 10.0, "quantity": 4,
            "purchase_date": ago(60).isoformat(), "trade_date": ago(60).isoformat(),
            "currency": "USD"})
        self.lots.close_lots(OWNER, [(lot_id, D("2"))], D("15"), ago(5).isoformat())
        self.lots.close_lots(OWNER, [(self._open_lot(), D("2"))], D("9"), ago(4).isoformat())
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE lot_sales SET source = 'stop' WHERE sale_price = 9 AND lot_id IN "
                "(SELECT lot_id FROM positions WHERE owner = %s)", (OWNER,))
            conn.commit()
        attribution = self.service.book_performance(owner=OWNER, period="month")["attribution"]
        self.assertEqual(
            [(s["source"], s["sale_count"], s["realized_pnl"]) for s in attribution["sources"]],
            [("manual", 1, D("10.00")), ("stop", 1, D("-2.00"))])
        self.assertEqual(attribution["totals"]["realized_pnl"], D("8.00"))
        # The paper book's attribution has none of the real book's sales.
        paper = self.service.book_performance(owner=OWNER, period="month", book_id=self.paper)
        self.assertEqual(paper["attribution"]["sources"], [])

    def _open_lot(self):
        return next(l["lot_id"] for l in self.lots.list_positions(OWNER, "OPEN"))


class CompareBooksTest(_Base):
    def test_every_open_book_real_first_with_one_benchmark_read(self):
        self.rows()
        self.rows(book_id=self.paper)
        archived = self.books.create_paper_book(owner=OWNER, name="Old", starting_cash=1)
        self.books.archive_book(owner=OWNER, book_id=archived)
        result = self.service.compare_books(owner=OWNER, period="year")
        self.assertEqual([(b["kind"], b["name"]) for b in result["books"]],
                         [("real", "Real"), ("paper", "Momentum")])
        self.assertEqual([b["twr"] for b in result["books"]], [D("0.21000000")] * 2)
        self.assertEqual(len(self.prices.asked), 1)

    def test_another_owners_books_are_not_listed(self):
        self.books.create_paper_book(owner=OTHER, name="Theirs", starting_cash=5)
        names = [b["name"] for b in self.service.compare_books(owner=OWNER)["books"]]
        self.assertEqual(names, ["Real", "Momentum"])

    def test_an_owner_with_no_book_sees_the_real_book_and_none_is_created(self):
        result = self.service.compare_books(owner=OTHER)
        self.assertEqual([(b["kind"], b["book_id"]) for b in result["books"]], [("real", None)])
        self.assertEqual(self.books.list_books(owner=OTHER), [])


class BooksOverviewTest(_Base):
    """`books_overview`, what the `get_books` tool reads (step 5.4)."""

    def _lot(self, quantity, price, book_id=None, owner=OWNER):
        return self.lots.add_position(owner, {
            "name": "Perf Co", "symbol": SYMBOL, "purchase_price": price,
            "quantity": quantity, "purchase_date": ago(60).isoformat(),
            "trade_date": ago(60).isoformat(), "currency": "USD"}, book_id=book_id)

    def test_each_open_book_in_brief_with_its_own_holdings(self):
        self.rows()
        self.rows(book_id=self.paper)
        self._lot(1.5, 10.0, book_id=self.paper)
        self._lot(0.5, 14.0, book_id=self.paper)
        self._lot(3, 20.0)
        result = self.service.books_overview(owner=OWNER, period="year")
        self.assertEqual(result["period"], "year")
        real, paper = result["books"]
        self.assertEqual((real["kind"], paper["name"]), ("real", "Momentum"))
        self.assertEqual(paper["twr"], D("0.21000000"))
        self.assertEqual(paper["latest"]["book_value"], D("1210.00"))
        self.assertEqual(paper["holdings"], [{
            "symbol": SYMBOL, "currency": "USD", "quantity": D("2.000000"),
            "avg_price": D("11.00"), "lot_count": 2}])
        self.assertEqual([(h["quantity"], h["lot_count"]) for h in real["holdings"]],
                         [(D("3.000000"), 1)])

    def test_the_daily_series_is_left_out_and_the_gaps_counted(self):
        self.rows()
        book = self.service.books_overview(owner=OWNER, period="year")["books"][0]
        self.assertNotIn("series", book)
        self.assertNotIn("gaps", book)
        self.assertIsInstance(book["gap_count"], int)
        self.assertEqual(len(self.prices.asked), 1)

    def test_another_owners_books_and_lots_are_not_read(self):
        theirs = self.books.create_paper_book(owner=OTHER, name="Theirs", starting_cash=5)
        self._lot(7, 5.0, owner=OTHER)
        self._lot(9, 5.0, book_id=theirs, owner=OTHER)
        books = self.service.books_overview(owner=OWNER)["books"]
        self.assertEqual([b["name"] for b in books], ["Real", "Momentum"])
        self.assertEqual([b["holdings"] for b in books], [[], []])

    def test_an_owner_with_no_book_gets_the_stand_in_and_none_is_created(self):
        books = self.service.books_overview(owner=OTHER)["books"]
        self.assertEqual([(b["kind"], b["book_id"], b["holdings"]) for b in books],
                         [("real", None, [])])
        self.assertEqual(self.books.list_books(owner=OTHER), [])

    def test_an_unknown_period_is_refused(self):
        with self.assertRaises(ValueError):
            self.service.books_overview(owner=OWNER, period="ytd")


class PerformanceRoutesTest(_Base):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def setUp(self):
        super().setUp()
        OwnerIdentityRepository().upsert(IDENTITY, OWNER)
        principal = Principal(subject=IDENTITY, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal
        self.addCleanup(self.app.dependency_overrides.pop, require_principal, None)
        patcher = patch("api.routers.books.services")
        services = patcher.start()
        self.addCleanup(patcher.stop)
        services.return_value.performance = self.service

    def test_one_book(self):
        self.rows(book_id=self.paper)
        resp = self.client.get(f"/api/books/performance?period=all&book_id={self.paper}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["name"], "Momentum")
        self.assertAlmostEqual(body["twr"], 0.21)
        self.assertNotIn("owner", body)

    def test_another_owners_book_is_404(self):
        theirs = self.books.create_paper_book(owner=OTHER, name="Theirs", starting_cash=5)
        resp = self.client.get(f"/api/books/performance?book_id={theirs}")
        self.assertEqual(resp.status_code, 404)

    def test_a_bad_period_is_422(self):
        self.assertEqual(self.client.get("/api/books/performance?period=ytd").status_code, 422)
        self.assertEqual(
            self.client.get("/api/books/performance/compare?period=ytd").status_code, 422)

    def test_overview(self):
        self.rows(book_id=self.paper)
        resp = self.client.get("/api/books/overview?period=all")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["period"], "all")
        self.assertEqual([b["name"] for b in body["books"]], ["Real", "Momentum"])
        self.assertAlmostEqual(body["books"][1]["twr"], 0.21)
        self.assertNotIn("series", body["books"][1])
        self.assertEqual(self.client.get("/api/books/overview?period=ytd").status_code, 422)

    def test_compare(self):
        resp = self.client.get("/api/books/performance/compare")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([b["name"] for b in resp.json()["books"]], ["Real", "Momentum"])


if __name__ == "__main__":
    unittest.main()
