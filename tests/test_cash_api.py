"""`CashService` and the /api/portfolio/cash routes (#431 step 3.1).

Runs the FastAPI app through TestClient against the real services and the
test database. Two owners, each reached through an `owner_identities` row, so
`require_owner` resolves them the way it does in production.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.books import BooksService  # noqa: E402
from quantcore.services.cash import (  # noqa: E402
    MAX_CASH_AMOUNT,
    MAX_NOTE_LENGTH,
    CashEntryNotFoundError,
    CashService,
)

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

OWNER = "zz_cash_api_owner"
OTHER = "zz_cash_api_other"
IDENTITY = "zz_cash_api_owner@example.com"
OTHER_IDENTITY = "zz_cash_api_other@example.com"
SYMBOL = "ZZCASHAPI"
TODAY = datetime.date(2026, 6, 15)
D = Decimal


def _purge():
    with closing(get_connection()) as conn:
        for owner in (OWNER, OTHER):
            conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM cash_entries WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM books WHERE owner = %s", (owner,))
        conn.execute("DELETE FROM symbols WHERE ticker = %s", (SYMBOL,))
        for identity in (IDENTITY, OTHER_IDENTITY):
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (identity,))
        conn.commit()


def _lot(**overrides):
    row = {
        "name": "Cash Api Co", "symbol": SYMBOL, "purchase_price": 10.0, "quantity": 5,
        "purchase_date": "2026-01-02", "currency": "USD",
    }
    row.update(overrides)
    return row


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        # "Today" is pinned for both services, so "not in the future" and a new
        # paper book's opening date don't depend on when the suite runs.
        for target in ("quantcore.services.cash.market_date", "quantcore.services.books.market_date"):
            patcher = patch(target, return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.repo = CashRepository()


class CashServiceTest(_Base):
    def setUp(self):
        super().setUp()
        self.service = CashService(self.repo, self.books)
        self.book_service = BooksService(self.books)

    def test_a_ledger_with_no_opening_balance_shows_nothing_and_creates_no_book(self):
        ledger = self.service.ledger(owner=OWNER)

        self.assertEqual(ledger["book_kind"], "real")
        self.assertIsNone(ledger["balance"])
        self.assertEqual(ledger["entries"], [])
        self.assertEqual(self.books.list_books(owner=OWNER), [])

    def test_lots_wait_for_an_opening_balance(self):
        self.lots.add_position(OWNER, _lot())
        ledger = self.service.ledger(owner=OWNER)
        self.assertEqual((ledger["balance"], ledger["earlier_entry_count"]), (None, 1))

    def test_the_real_books_ledger_end_to_end(self):
        # A lot from before the book's history starts, and one from after.
        self.lots.add_position(OWNER, _lot(purchase_date="2025-12-01"))
        lot = self.lots.add_position(OWNER, _lot(quantity=4))

        self.service.set_opening_balance(
            owner=OWNER, entry_date="2026-01-01", amount="1000", notes=" from the statement "
        )
        self.service.add_entry(owner=OWNER, kind="deposit", entry_date="2026-02-01", amount="250.505")
        self.service.add_entry(owner=OWNER, kind="withdrawal", entry_date="2026-02-02", amount=75)
        self.lots.close_lots(OWNER, [(lot, D("4"))], D("12.5"), "2026-03-01")
        ledger = self.service.ledger(owner=OWNER)

        self.assertEqual((ledger["opening_date"], ledger["opening_balance"]),
                         ("2026-01-01", D("1000.00")))
        # 1000 - 40 + 250.51 - 75 + 50
        self.assertEqual(ledger["balance"], D("1185.51"))
        self.assertEqual(
            (ledger["deposits"], ledger["withdrawals"], ledger["bought"], ledger["sold"]),
            (D("250.51"), D("-75.00"), D("-40.00"), D("50.00")),
        )
        self.assertEqual((ledger["entry_count"], ledger["earlier_entry_count"]), (5, 1))
        self.assertEqual(
            [e["kind"] for e in ledger["entries"]],
            ["sell", "withdrawal", "deposit", "buy", "opening_balance"],
        )
        self.assertEqual(ledger["entries"][-1]["notes"], "from the statement")
        self.assertEqual(ledger["entries"][0]["symbol"], SYMBOL)

    def test_moving_the_opening_date_changes_what_counts(self):
        self.lots.add_position(OWNER, _lot())
        early = self.service.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=100)
        self.assertEqual((early["balance"], early["earlier_entry_count"]), (D("50.00"), 0))
        late = self.service.set_opening_balance(owner=OWNER, entry_date="2026-01-03", amount=100)
        self.assertEqual((late["balance"], late["earlier_entry_count"]), (D("100.00"), 1))

    def test_the_real_book_goes_negative_and_says_so(self):
        self.service.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=10)
        self.lots.add_position(OWNER, _lot())
        ledger = self.service.add_entry(
            owner=OWNER, kind="withdrawal", entry_date="2026-01-05", amount="20"
        )
        self.assertEqual(ledger["balance"], D("-60.00"))
        self.assertTrue(ledger["negative"])

    def test_a_negative_opening_balance_is_taken(self):
        ledger = self.service.set_opening_balance(
            owner=OWNER, entry_date="2026-01-01", amount="-12.345"
        )
        self.assertEqual(ledger["opening_balance"], D("-12.35"))

    def test_bad_input_is_a_value_error_and_writes_nothing(self):
        self.service.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=100)
        good = {"kind": "deposit", "entry_date": "2026-02-01", "amount": "5"}
        for change in (
            {"kind": "buy"},
            {"kind": "opening_balance"},
            {"amount": "0"},
            {"amount": "-5"},
            {"amount": "lots"},
            {"amount": "NaN"},
            {"amount": "Infinity"},
            {"amount": MAX_CASH_AMOUNT + 1},
            {"entry_date": "02/01/2026"},
            {"entry_date": ""},
            {"entry_date": "2026-06-16"},   # tomorrow
            {"notes": "x" * (MAX_NOTE_LENGTH + 1)},
        ):
            with self.subTest(**change), self.assertRaises(ValueError):
                self.service.add_entry(owner=OWNER, **{**good, **change})
        for change in ({"amount": "NaN"}, {"amount": MAX_CASH_AMOUNT + 1},
                       {"entry_date": "2026-06-16"}, {"entry_date": "soon"}):
            with self.subTest(opening=change), self.assertRaises(ValueError):
                self.service.set_opening_balance(
                    owner=OWNER, **{"entry_date": "2026-01-01", "amount": 1, **change}
                )
        ledger = self.service.ledger(owner=OWNER)
        self.assertEqual((ledger["entry_count"], ledger["balance"]), (1, D("100.00")))

    def test_today_is_allowed_and_a_blank_note_is_no_note(self):
        self.service.set_opening_balance(owner=OWNER, entry_date=TODAY.isoformat(), amount=1)
        ledger = self.service.add_entry(
            owner=OWNER, kind="deposit", entry_date=TODAY.isoformat(), amount=1, notes="   "
        )
        self.assertIsNone(ledger["entries"][0]["notes"])
        self.assertEqual(ledger["balance"], D("2.00"))

    def test_delete_takes_back_a_deposit_and_nothing_else(self):
        self.service.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=100)
        ledger = self.service.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=5
        )
        deposit, opening = (e["entry_id"] for e in ledger["entries"])

        with self.assertRaises(CashEntryNotFoundError):
            self.service.delete_entry(owner=OWNER, entry_id=opening)
        with self.assertRaises(CashEntryNotFoundError):
            self.service.delete_entry(owner=OTHER, entry_id=deposit)
        after = self.service.delete_entry(owner=OWNER, entry_id=deposit)
        self.assertEqual((after["entry_count"], after["balance"]), (1, D("100.00")))
        with self.assertRaises(CashEntryNotFoundError):
            self.service.delete_entry(owner=OWNER, entry_id=deposit)

    def test_a_new_paper_book_opens_with_its_starting_cash(self):
        book = self.book_service.create_paper_book(owner=OWNER, name="Momentum", starting_cash="2500.50")
        ledger = self.service.ledger(owner=OWNER, book_id=book["book_id"])

        self.assertEqual(ledger["book_kind"], "paper")
        self.assertEqual((ledger["opening_date"], ledger["opening_balance"], ledger["balance"]),
                         (TODAY.isoformat(), D("2500.50"), D("2500.50")))
        # A lot bought the same day counts against it.
        self.lots.add_position(
            OWNER, _lot(purchase_date=TODAY.isoformat()), book_id=book["book_id"]
        )
        self.assertEqual(
            self.service.ledger(owner=OWNER, book_id=book["book_id"])["balance"], D("2450.50")
        )
        # The real book has no part in it.
        self.assertIsNone(self.service.ledger(owner=OWNER)["balance"])

    def test_a_paper_book_takes_nothing_the_owner_types(self):
        book = self.book_service.create_paper_book(owner=OWNER, name="Momentum", starting_cash=100)
        paper = book["book_id"]
        before = self.service.ledger(owner=OWNER, book_id=paper)
        opening = before["entries"][0]["entry_id"]

        calls = (
            lambda: self.service.add_entry(
                owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=5, book_id=paper),
            lambda: self.service.set_opening_balance(
                owner=OWNER, entry_date="2026-01-01", amount=5, book_id=paper),
            lambda: self.service.delete_entry(owner=OWNER, entry_id=opening, book_id=paper),
        )
        for call in calls:
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertNotIsInstance(caught.exception, (BookNotFoundError, CashEntryNotFoundError))
        self.assertEqual(self.service.ledger(owner=OWNER, book_id=paper), before)

    def test_a_book_that_is_not_the_owners_is_not_found(self):
        book = self.book_service.create_paper_book(owner=OWNER, name="Momentum", starting_cash=100)
        real = self.books.ensure_real_book(owner=OWNER)
        for book_id in (book["book_id"], real, 999999999):
            with self.subTest(book_id=book_id):
                with self.assertRaises(BookNotFoundError):
                    self.service.ledger(owner=OTHER, book_id=book_id)
                with self.assertRaises(BookNotFoundError):
                    self.service.add_entry(
                        owner=OTHER, kind="deposit", entry_date="2026-02-01", amount=5,
                        book_id=book_id,
                    )
        self.assertEqual(self.books.list_books(owner=OTHER), [])

    def test_naming_the_real_book_is_the_same_as_naming_none(self):
        self.service.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=100)
        real = self.books.ensure_real_book(owner=OWNER)
        self.service.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=5, book_id=real
        )
        self.assertEqual(
            self.service.ledger(owner=OWNER, book_id=real), self.service.ledger(owner=OWNER)
        )
        self.assertEqual(self.service.ledger(owner=OWNER)["balance"], D("105.00"))

    def test_backfill_covers_every_book_of_the_owner(self):
        self.lots.add_position(OWNER, _lot())
        paper = self.books.create_paper_book(owner=OWNER, name="Momentum", starting_cash=100)
        self.lots.add_position(OWNER, _lot(quantity=2), book_id=paper)
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM cash_entries WHERE owner = %s", (OWNER,))
            conn.commit()

        dry = self.service.backfill(owner=OWNER)
        self.assertEqual(
            [(b["kind"], b["name"], b["buys"], b["sells"], b["openings"]) for b in dry],
            [("real", "Real", 1, 0, 0), ("paper", "Momentum", 1, 0, 1)],
        )
        self.assertEqual(self.repo.list_entries(owner=OWNER), [])

        self.assertEqual(
            [(b["buys"], b["openings"]) for b in self.service.backfill(owner=OWNER, apply=True)],
            [(1, 0), (1, 1)],
        )
        self.assertEqual(
            [(b["buys"], b["openings"]) for b in self.service.backfill(owner=OWNER, apply=True)],
            [(0, 0), (0, 0)],
        )
        self.assertEqual(self.service.backfill(owner=OTHER, apply=True), [])
        self.assertIn(OWNER, self.service.list_owners())


class CashApiTest(_Base):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def setUp(self):
        super().setUp()
        identities = OwnerIdentityRepository()
        identities.upsert(IDENTITY, OWNER)
        identities.upsert(OTHER_IDENTITY, OTHER)
        self.addCleanup(self.app.dependency_overrides.pop, require_principal, None)
        self._as(IDENTITY)

    def _as(self, identity):
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal

    def _open(self, amount="1000", date="2026-01-01"):
        resp = self.client.put(
            "/api/portfolio/cash/opening-balance", json={"date": date, "amount": amount}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _deposit(self, **body):
        resp = self.client.post(
            "/api/portfolio/cash/entries",
            json={"kind": "deposit", "date": "2026-02-01", "amount": "250", **body},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def test_an_owner_with_nothing_gets_an_empty_ledger_and_no_book(self):
        resp = self.client.get("/api/portfolio/cash")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual((body["book_kind"], body["balance"], body["entries"]), ("real", None, []))
        self.assertEqual(BooksRepository().list_books(owner=OWNER), [])

    def test_the_payload_has_exactly_the_services_keys(self):
        self._open()
        self.lots.add_position(OWNER, _lot())
        body = self.client.get("/api/portfolio/cash").json()
        service = CashService(self.repo, self.books).ledger(owner=OWNER)

        self.assertEqual(set(body), set(service))
        self.assertEqual(set(body["entries"][0]), set(service["entries"][0]))
        self.assertNotIn("owner", body["entries"][0])
        self.assertEqual(D(str(body["balance"])), D("950"))

    def test_opening_balance_then_entries_then_delete(self):
        opened = self._open()
        self.assertEqual(D(str(opened["balance"])), D("1000"))

        deposited = self._deposit(notes="bonus")
        self.assertEqual(D(str(deposited["balance"])), D("1250"))
        entry = deposited["entries"][0]
        self.assertEqual((entry["kind"], entry["notes"]), ("deposit", "bonus"))

        withdrawn = self.client.post(
            "/api/portfolio/cash/entries",
            json={"kind": "withdrawal", "date": "2026-02-02", "amount": "50.25"},
        ).json()
        self.assertEqual(D(str(withdrawn["balance"])), D("1199.75"))
        self.assertEqual(D(str(withdrawn["entries"][0]["amount"])), D("-50.25"))

        resp = self.client.delete(f"/api/portfolio/cash/entries/{entry['entry_id']}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(D(str(resp.json()["balance"])), D("949.75"))
        self.assertEqual(
            self.client.delete(f"/api/portfolio/cash/entries/{entry['entry_id']}").status_code, 404
        )

    def test_a_trade_made_over_the_api_shows_in_the_ledger(self):
        self._open()
        created = self.client.post("/api/portfolio/lots", json={
            "symbol": SYMBOL, "name": "Cash Api Co", "purchase_price": "10", "quantity": "5",
            "trade_date": "2026-01-02", "currency": "USD",
        })
        self.assertEqual(created.status_code, 201, created.text)
        lot_id = self.lots.list_positions(OWNER)[0]["lot_id"]
        closed = self.client.post(f"/api/portfolio/lots/{lot_id}/close", json={
            "shares": "2", "sale_price": "12", "sale_trade_date": "2026-03-01",
        })
        self.assertEqual(closed.status_code, 200, closed.text)

        body = self.client.get("/api/portfolio/cash").json()
        self.assertEqual(
            [(e["kind"], D(str(e["amount"]))) for e in body["entries"]],
            [("sell", D("24")), ("buy", D("-30")), ("buy", D("-20")),
             ("opening_balance", D("1000"))],
        )
        self.assertEqual(D(str(body["balance"])), D("974"))

    def test_bad_input_is_a_422(self):
        self._open()
        for body in (
            {"kind": "buy"},
            {"kind": "opening_balance"},
            {"amount": "0"},
            {"amount": "-1"},
            {"amount": "NaN"},
            {"amount": "lots"},
            {"date": "tomorrow"},
            {"date": "2026-06-16"},
            {"notes": "x" * (MAX_NOTE_LENGTH + 1)},
        ):
            with self.subTest(**body):
                resp = self.client.post(
                    "/api/portfolio/cash/entries",
                    json={"kind": "deposit", "date": "2026-02-01", "amount": "5", **body},
                )
                self.assertEqual(resp.status_code, 422, resp.text)
        for body in ({"date": "2026-06-16", "amount": "1"}, {"date": "2026-01-01", "amount": "NaN"},
                     {"date": "2026-01-01"}):
            with self.subTest(opening=body):
                resp = self.client.put("/api/portfolio/cash/opening-balance", json=body)
                self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(self.client.get("/api/portfolio/cash").json()["entry_count"], 1)

    def test_owners_are_isolated(self):
        self._open()
        entry_id = self._deposit()["entries"][0]["entry_id"]
        real = BooksRepository().ensure_real_book(owner=OWNER)

        self._as(OTHER_IDENTITY)
        body = self.client.get("/api/portfolio/cash").json()
        self.assertEqual((body["balance"], body["entries"], body["earlier_entry_count"]),
                         (None, [], 0))
        self.assertEqual(self.client.get(f"/api/portfolio/cash?book_id={real}").status_code, 404)
        self.assertEqual(
            self.client.delete(f"/api/portfolio/cash/entries/{entry_id}").status_code, 404
        )
        # OTHER's own opening balance goes to OTHER's own book.
        self.assertEqual(D(str(self._open(amount="7")["balance"])), D("7"))

        self._as(IDENTITY)
        mine = self.client.get("/api/portfolio/cash").json()
        self.assertEqual((mine["entry_count"], D(str(mine["balance"]))), (2, D("1250")))

    def test_a_paper_books_ledger_is_read_only_over_the_api(self):
        book = self.client.post(
            "/api/books", json={"name": "Momentum", "starting_cash": "5000"}
        ).json()
        paper = self.client.get(f"/api/portfolio/cash?book_id={book['book_id']}")
        self.assertEqual(paper.status_code, 200, paper.text)
        body = paper.json()
        self.assertEqual(
            (body["book_kind"], D(str(body["balance"])), body["opening_date"]),
            ("paper", D("5000"), TODAY.isoformat()),
        )
        # The writes name no book, so they can only ever reach the real one.
        self._open(amount="10")
        self._deposit()
        again = self.client.get(f"/api/portfolio/cash?book_id={book['book_id']}").json()
        self.assertEqual(again, body)
        self.assertEqual(self.client.get("/api/portfolio/cash?book_id=999999999").status_code, 404)

    def test_the_routes_need_a_known_owner(self):
        self._as("zz_cash_api_nobody@example.com")
        self.assertEqual(self.client.get("/api/portfolio/cash").status_code, 403)
        resp = self.client.post(
            "/api/portfolio/cash/entries",
            json={"kind": "deposit", "date": "2026-02-01", "amount": "5"},
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
