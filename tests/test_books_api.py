"""Books routes and the portfolio read routes' `book_id` (#431 step 1.2).

Runs the FastAPI app through TestClient against the real services and the
test database. Two owners, each reached through an `owner_identities` row, so
`require_owner` resolves them the way it does in production.
"""

import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.gateways.yfinance_gateway import YFinanceGateway  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.books import (  # noqa: E402
    MAX_BOOK_NAME_LENGTH,
    MAX_STARTING_CASH,
    BooksService,
)
from quantcore.services.portfolio import PortfolioService  # noqa: E402

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

OWNER = "zz_books_api_owner"
OTHER = "zz_books_api_other"
IDENTITY = "zz_books_api_owner@example.com"
OTHER_IDENTITY = "zz_books_api_other@example.com"
SYMBOL = "ZZBOOKAPI"


def _purge():
    with closing(get_connection()) as conn:
        for owner in (OWNER, OTHER):
            conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM books WHERE owner = %s", (owner,))
        conn.execute("DELETE FROM symbols WHERE ticker = %s", (SYMBOL,))
        for identity in (IDENTITY, OTHER_IDENTITY):
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (identity,))
        conn.commit()


def _lot(**overrides):
    row = {
        "name": "Book Api Co",
        "symbol": SYMBOL,
        "purchase_price": 10.0,
        "quantity": 5,
        "purchase_date": "2026-01-02",
        "currency": "USD",
    }
    row.update(overrides)
    return row


class BooksServiceTest(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.repo = BooksRepository()
        self.service = BooksService(self.repo)

    def test_listing_never_creates_a_book(self):
        self.assertEqual(self.service.list_books(owner=OWNER), [])
        self.assertEqual(self.repo.list_books(owner=OWNER), [])

    def test_create_returns_the_book_without_its_owner(self):
        book = self.service.create_paper_book(owner=OWNER, name=" Momentum ", starting_cash="2500.50")

        self.assertEqual((book["name"], book["kind"]), ("Momentum", "paper"))
        self.assertEqual(book["starting_cash"], Decimal("2500.50"))
        self.assertIsNone(book["archived_at"])
        self.assertNotIn("owner", book)
        self.assertEqual(
            [(b["kind"], b["name"]) for b in self.service.list_books(owner=OWNER)],
            [("real", "Real"), ("paper", "Momentum")],
        )

    def test_create_rejects_bad_input(self):
        for kwargs in (
            {"name": ""},
            {"name": "x" * (MAX_BOOK_NAME_LENGTH + 1)},
            {"name": "Rich", "starting_cash": MAX_STARTING_CASH + 1},
            {"name": "Short", "starting_cash": -1},
            {"name": "Words", "starting_cash": "lots"},
            {"name": "Real"},
        ):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                self.service.create_paper_book(owner=OWNER, **kwargs)

    def test_a_name_at_the_limit_is_accepted(self):
        book = self.service.create_paper_book(owner=OWNER, name="x" * MAX_BOOK_NAME_LENGTH)
        self.assertEqual(len(book["name"]), MAX_BOOK_NAME_LENGTH)

    def test_archive_hides_a_book_from_the_default_list(self):
        book = self.service.create_paper_book(owner=OWNER, name="Momentum")

        archived = self.service.archive_book(owner=OWNER, book_id=book["book_id"])
        self.assertIsNotNone(archived["archived_at"])
        # Twice is not an error, and does not move the timestamp.
        again = self.service.archive_book(owner=OWNER, book_id=book["book_id"])
        self.assertEqual(again["archived_at"], archived["archived_at"])

        self.assertEqual([b["kind"] for b in self.service.list_books(owner=OWNER)], ["real"])
        self.assertEqual(
            [b["kind"] for b in self.service.list_books(owner=OWNER, include_archived=True)],
            ["real", "paper"],
        )

    def test_the_real_book_cannot_be_archived(self):
        real = self.repo.ensure_real_book(owner=OWNER)
        with self.assertRaises(ValueError) as caught:
            self.service.archive_book(owner=OWNER, book_id=real)
        self.assertNotIsInstance(caught.exception, BookNotFoundError)

    def test_another_owners_book_is_not_found(self):
        book = self.service.create_paper_book(owner=OWNER, name="Momentum")

        with self.assertRaises(BookNotFoundError):
            self.service.archive_book(owner=OTHER, book_id=book["book_id"])
        self.assertEqual(self.service.list_books(owner=OTHER), [])
        self.assertIsNone(self.repo.get_book(owner=OWNER, book_id=book["book_id"])["archived_at"])


class _Prices:
    """No quotes and no history: the rows keep their lots, unpriced."""

    def get_quotes(self, symbols, force=False):
        return {}

    def bulk_period_returns(self, symbols, periods):
        return {s: {"bars": 1} for s in symbols}

    def quote_changes(self, symbols, return_1d):
        return {}


class _Plans:
    def __init__(self):
        self.calls = 0

    def active_plan_ids(self, *, owner):
        self.calls += 1
        return {SYMBOL: 77}


class PortfolioServiceBookTest(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.books = BooksRepository()
        self.repo = PortfolioRepository()
        self.plans = _Plans()
        self.service = PortfolioService(
            self.repo, prices=_Prices(), harvester_repository=self.plans,
            books_repository=self.books,
        )
        self.real_lot = self.repo.add_position(OWNER, _lot())
        self.paper = self.books.create_paper_book(owner=OWNER, name="Momentum")
        self.paper_lot = self.repo.add_position(OWNER, _lot(quantity=7), book_id=self.paper)

    def _lot_ids(self, rows):
        return [lot["lot_id"] for row in rows for lot in row["lots"]]

    def test_no_book_named_reads_the_real_book(self):
        self.assertEqual([l["lot_id"] for l in self.service.list_lots(OWNER)], [self.real_lot])
        rows = self.service.symbol_rows(OWNER)
        self.assertEqual(self._lot_ids(rows), [self.real_lot])
        self.assertEqual(rows[0]["active_plan_id"], 77)

    def test_a_paper_book_reads_its_own_lots_and_gets_no_plan_chip(self):
        lots = self.service.list_lots(OWNER, book_id=self.paper)
        self.assertEqual([l["lot_id"] for l in lots], [self.paper_lot])

        self.plans.calls = 0
        rows = self.service.symbol_rows(OWNER, book_id=self.paper)
        self.assertEqual(self._lot_ids(rows), [self.paper_lot])
        self.assertIsNone(rows[0]["active_plan_id"])
        self.assertEqual(self.plans.calls, 0)

    def test_naming_the_real_book_is_the_same_as_naming_none(self):
        real = self.books.ensure_real_book(owner=OWNER)
        rows = self.service.symbol_rows(OWNER, book_id=real)
        self.assertEqual(self._lot_ids(rows), [self.real_lot])
        self.assertEqual(rows[0]["active_plan_id"], 77)

    def test_sales_follow_the_named_book(self):
        self.repo.close_lots(
            OWNER, [(self.paper_lot, Decimal("2"))], Decimal("12"), "2026-02-01",
            book_id=self.paper,
        )
        self.assertEqual(self.service.list_sales(OWNER), [])
        self.assertEqual(len(self.service.list_sales(OWNER, book_id=self.paper)), 1)
        self.assertEqual(len(self.service.list_sales(OWNER, SYMBOL.lower(), book_id=self.paper)), 1)

    def test_a_book_that_is_not_the_owners_is_not_found(self):
        for call in (
            lambda: self.service.list_lots(OTHER, book_id=self.paper),
            lambda: self.service.symbol_rows(OTHER, book_id=self.paper),
            lambda: self.service.list_sales(OTHER, book_id=self.paper),
            lambda: self.service.symbol_rows(OWNER, book_id=999_999_999),
        ):
            with self.assertRaises(BookNotFoundError):
                call()

    def test_without_a_books_repository_a_named_book_is_refused(self):
        service = PortfolioService(self.repo, prices=_Prices())
        with self.assertRaises(BookNotFoundError):
            service.list_lots(OWNER, book_id=self.paper)
        # No book named still works: that is every caller written before books.
        self.assertEqual([l["lot_id"] for l in service.list_lots(OWNER)], [self.real_lot])


class BooksRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        for name, value in (
            ("batch_quotes", lambda self, symbols, timeout=8.0: {s.upper(): None for s in symbols}),
            ("fetch_history", lambda self, symbol, interval, days, **kw:
                pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])),
        ):
            patcher = patch.object(YFinanceGateway, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        identities = OwnerIdentityRepository()
        identities.upsert(IDENTITY, OWNER)
        identities.upsert(OTHER_IDENTITY, OTHER)
        self.addCleanup(self.app.dependency_overrides.pop, require_principal, None)
        self._as(IDENTITY)

    def _as(self, identity):
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal

    def _create(self, **body):
        resp = self.client.post("/api/books", json={"name": "Momentum", **body})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def test_an_owner_with_no_book_lists_none_and_none_is_created(self):
        resp = self.client.get("/api/books")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"books": []})
        self.assertEqual(BooksRepository().list_books(owner=OWNER), [])

    def test_create_then_list(self):
        book = self._create(starting_cash="10000")

        self.assertEqual((book["name"], book["kind"]), ("Momentum", "paper"))
        self.assertEqual(Decimal(str(book["starting_cash"])), Decimal("10000"))
        self.assertNotIn("owner", book)
        books = self.client.get("/api/books").json()["books"]
        self.assertEqual([(b["kind"], b["name"]) for b in books], [("real", "Real"), ("paper", "Momentum")])

    def test_create_rejects_bad_input_with_422(self):
        self._create()
        for body in (
            {"name": "Momentum"},
            {"name": "   "},
            {"name": "x" * (MAX_BOOK_NAME_LENGTH + 1)},
            {"name": "Short", "starting_cash": "-1"},
            {"name": "NotANumber", "starting_cash": "NaN"},
            {"starting_cash": "5"},
        ):
            with self.subTest(**body):
                resp = self.client.post("/api/books", json=body)
                self.assertEqual(resp.status_code, 422, resp.text)

    def test_archive_and_the_archived_list(self):
        book = self._create()
        url = f"/api/books/{book['book_id']}/archive"

        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNotNone(resp.json()["archived_at"])
        self.assertEqual(self.client.post(url).status_code, 200)

        self.assertEqual([b["kind"] for b in self.client.get("/api/books").json()["books"]], ["real"])
        everything = self.client.get("/api/books", params={"include_archived": "true"}).json()["books"]
        self.assertEqual([b["kind"] for b in everything], ["real", "paper"])

    def test_archiving_the_real_book_is_422(self):
        self._create()
        real = next(b for b in self.client.get("/api/books").json()["books"] if b["kind"] == "real")
        resp = self.client.post(f"/api/books/{real['book_id']}/archive")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json(), {"error": "the real book cannot be archived"})

    def test_another_owner_cannot_see_or_archive_a_book(self):
        book = self._create()

        self._as(OTHER_IDENTITY)
        self.assertEqual(self.client.get("/api/books").json(), {"books": []})
        resp = self.client.post(f"/api/books/{book['book_id']}/archive")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": f"book {book['book_id']} not found"})
        # The same name is free for the other owner.
        self.assertEqual(self.client.post("/api/books", json={"name": "Momentum"}).status_code, 201)

        self._as(IDENTITY)
        mine = self.client.get("/api/books").json()["books"]
        self.assertEqual([b["book_id"] for b in mine if b["kind"] == "paper"], [book["book_id"]])

    def test_portfolio_reads_take_a_book(self):
        repo = PortfolioRepository()
        real_lot = repo.add_position(OWNER, _lot())
        paper = self._create()["book_id"]
        paper_lot = repo.add_position(OWNER, _lot(quantity=7), book_id=paper)
        repo.close_lots(OWNER, [(paper_lot, Decimal("2"))], Decimal("12"), "2026-02-01", book_id=paper)

        def lot_ids(params=None):
            return [l["lot_id"] for l in self.client.get("/api/portfolio/lots", params=params).json()["lots"]]

        self.assertEqual(lot_ids(), [real_lot])
        self.assertEqual(len(lot_ids({"book_id": paper})), 1)
        self.assertNotIn(real_lot, lot_ids({"book_id": paper}))

        body = self.client.get("/api/portfolio/symbols", params={"book_id": paper}).json()
        self.assertEqual([r["symbol"] for r in body["symbols"]], [SYMBOL])
        self.assertNotIn(real_lot, [l["lot_id"] for l in body["symbols"][0]["lots"]])
        self.assertIn("total_investment", body["totals"])

        self.assertEqual(self.client.get("/api/portfolio/sales").json(), {"sales": []})
        sales = self.client.get("/api/portfolio/sales", params={"book_id": paper}).json()["sales"]
        self.assertEqual(len(sales), 1)

    def test_a_book_that_is_not_the_owners_is_404_on_every_read(self):
        paper = self._create()["book_id"]
        PortfolioRepository().add_position(OWNER, _lot(), book_id=paper)

        self._as(OTHER_IDENTITY)
        for path in ("/api/portfolio/lots", "/api/portfolio/symbols", "/api/portfolio/sales"):
            for book_id in (paper, 999_999_999):
                with self.subTest(path=path, book_id=book_id):
                    resp = self.client.get(path, params={"book_id": book_id})
                    self.assertEqual(resp.status_code, 404, resp.text)
                    self.assertEqual(resp.json(), {"error": f"book {book_id} not found"})

    def test_a_book_id_that_is_not_a_number_is_422(self):
        resp = self.client.get("/api/portfolio/symbols", params={"book_id": "abc"})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
