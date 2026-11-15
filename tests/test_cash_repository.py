"""The cash ledger against the real database (#431 step 3.1): `CashRepository`,
and the buy and sell rows `PortfolioRepository` writes with a lot or a sale."""

import unittest
from contextlib import closing
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import psycopg2.errors  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402

OWNER = "zz_cash_owner"
OTHER = "zz_cash_other"
SYMBOL = "ZZCASH"
D = Decimal


def _purge():
    with closing(get_connection()) as conn:
        for owner in (OWNER, OTHER):
            # Lots first: their buy and sell rows go with them. What is left
            # (opening balances, deposits) has to go before its book can.
            conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM cash_entries WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM books WHERE owner = %s", (owner,))
        conn.execute("DELETE FROM symbols WHERE ticker = %s", (SYMBOL,))
        conn.commit()


def _lot(**overrides):
    row = {
        "name": "Cash Co", "symbol": SYMBOL, "purchase_price": 10.0, "quantity": 5,
        "purchase_date": "2026-01-02", "currency": "USD",
    }
    row.update(overrides)
    return row


class _Base(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        self.cash = CashRepository()
        self.lots = PortfolioRepository()
        self.books = BooksRepository()

    def _entries(self, owner=OWNER, book_id=None):
        return self.cash.list_entries(owner=owner, book_id=book_id)

    def _amounts(self, owner=OWNER, book_id=None):
        """(kind, entry_date, amount) of every row, oldest first."""
        return [(e["kind"], e["entry_date"], e["amount"]) for e in self._entries(owner, book_id)]

    def _all_rows(self, owner):
        with closing(get_connection()) as conn:
            return conn.execute(
                "SELECT kind, amount FROM cash_entries WHERE owner = %s ORDER BY entry_id",
                (owner,),
            ).fetchall()


class ManualEntriesTest(_Base):
    def test_a_read_creates_no_book_and_the_first_write_does(self):
        self.assertEqual(self._entries(), [])
        self.assertEqual(self.books.list_books(owner=OWNER), [], "a read must not create a book")

        self.cash.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=D("250.00")
        )
        self.assertEqual([b["kind"] for b in self.books.list_books(owner=OWNER)], ["real"])

    def test_deposits_and_withdrawals_are_listed_oldest_first(self):
        self.cash.add_entry(
            owner=OWNER, kind="withdrawal", entry_date="2026-03-01", amount=D("-40.25"),
            notes="rent",
        )
        self.cash.add_entry(owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=D("250"))

        entries = self._entries()
        self.assertEqual(
            [(e["kind"], e["entry_date"], e["amount"], e["notes"]) for e in entries],
            [("deposit", "2026-02-01", D("250.00"), None),
             ("withdrawal", "2026-03-01", D("-40.25"), "rent")],
        )
        # A manual row describes no trade.
        for entry in entries:
            for key in ("lot_id", "sale_id", "symbol", "shares", "price", "currency"):
                self.assertIsNone(entry[key], key)

    def test_only_a_deposit_or_withdrawal_can_be_added(self):
        for kind in ("buy", "sell", "opening_balance", "gift"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.cash.add_entry(owner=OWNER, kind=kind, entry_date="2026-02-01", amount=D("1"))
        self.assertEqual(self._all_rows(OWNER), [])

    def test_the_opening_balance_is_replaced_not_added_to(self):
        first = self.cash.set_opening_balance(
            owner=OWNER, entry_date="2026-01-01", amount=D("1000")
        )
        second = self.cash.set_opening_balance(
            owner=OWNER, entry_date="2026-01-15", amount=D("-25.50"), notes="margin"
        )

        self.assertEqual(first, second)
        self.assertEqual(self._amounts(), [("opening_balance", "2026-01-15", D("-25.50"))])
        self.assertEqual(self._entries()[0]["notes"], "margin")

    def test_delete_removes_only_what_the_owner_typed(self):
        opening = self.cash.set_opening_balance(
            owner=OWNER, entry_date="2026-01-01", amount=D("1000")
        )
        deposit = self.cash.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=D("250")
        )
        self.lots.add_position(OWNER, _lot())
        buy = next(e["entry_id"] for e in self._entries() if e["kind"] == "buy")

        self.assertFalse(self.cash.delete_entry(owner=OWNER, entry_id=opening))
        self.assertFalse(self.cash.delete_entry(owner=OWNER, entry_id=buy))
        self.assertTrue(self.cash.delete_entry(owner=OWNER, entry_id=deposit))
        self.assertFalse(self.cash.delete_entry(owner=OWNER, entry_id=deposit))
        self.assertEqual([e["kind"] for e in self._entries()], ["opening_balance", "buy"])

    def test_the_database_refuses_a_malformed_row(self):
        book = self.books.ensure_real_book(owner=OWNER)
        bad_rows = [
            ("gift", D("1"), None),            # not a kind
            ("buy", D("-1"), None),            # a buy with no lot
            ("deposit", None, None),           # a manual row with no amount
        ]
        for kind, amount, lot_id in bad_rows:
            with self.subTest(kind=kind), closing(get_connection()) as conn:
                with self.assertRaises(psycopg2.errors.CheckViolation):
                    conn.execute(
                        "INSERT INTO cash_entries (owner, book_id, kind, entry_date, amount, lot_id) "
                        "VALUES (%s, %s, %s, '2026-01-01', %s, %s)",
                        (OWNER, book, kind, amount, lot_id),
                    )
                conn.rollback()


class TradeEntriesTest(_Base):
    """The buy and sell rows follow their lot and sale through every write."""

    def test_a_new_lot_writes_its_buy_row(self):
        lot = self.lots.add_position(OWNER, _lot(purchase_price=D("12.345"), quantity=D("2.5")))

        (entry,) = self._entries()
        self.assertEqual(
            (entry["kind"], entry["entry_date"], entry["amount"], entry["lot_id"]),
            ("buy", "2026-01-02", D("-30.86"), lot),
        )
        self.assertEqual(
            (entry["symbol"], entry["shares"], entry["price"], entry["currency"]),
            (SYMBOL, D("2.5"), D("12.345"), "USD"),
        )

    def test_the_price_keeps_every_digit_the_lot_shows(self):
        # purchase_price is a REAL column. Cast straight to NUMERIC it would
        # keep six significant digits and this would be -1234.57.
        self.lots.add_position(OWNER, _lot(purchase_price=1234.567, quantity=1))
        self.assertEqual(self._amounts(), [("buy", "2026-01-02", D("-1234.57"))])
        self.assertEqual(self._entries()[0]["price"], D("1234.567"))

        self.lots.add_position(OWNER, _lot(purchase_price=1234.567, quantity=1000))
        self.assertEqual(self._amounts()[1][2], D("-1234567.00"))

    def test_a_lot_that_was_not_bought_writes_no_row(self):
        self.lots.add_position(OWNER, _lot(acquisition_type="TRANSFER"))
        self.assertEqual(self._entries(), [])

    def test_an_edit_rewrites_the_buy_row(self):
        lot = self.lots.add_position(OWNER, _lot())
        self.assertEqual(self._amounts(), [("buy", "2026-01-02", D("-50.00"))])

        self.lots.update_lot(OWNER, lot, {"quantity": D("6"), "trade_date": "2026-01-05"})
        self.assertEqual(self._amounts(), [("buy", "2026-01-05", D("-60.00"))])

        # A lot that turns out to have been a transfer loses its row, and
        # gets it back if that is corrected again.
        self.lots.update_lot(OWNER, lot, {"acquisition_type": "TRANSFER"})
        self.assertEqual(self._entries(), [])
        self.lots.update_lot(OWNER, lot, {"acquisition_type": "BUY"})
        self.assertEqual(self._amounts(), [("buy", "2026-01-05", D("-60.00"))])

    def test_an_edit_that_matches_no_lot_changes_nothing(self):
        lot = self.lots.add_position(OWNER, _lot())
        self.assertFalse(self.lots.update_lot(OTHER, lot, {"quantity": D("9")}))
        self.assertEqual(self._amounts(), [("buy", "2026-01-02", D("-50.00"))])

    def test_a_full_close_writes_the_sell_row(self):
        lot = self.lots.add_position(OWNER, _lot())
        (result,) = self.lots.close_lots(OWNER, [(lot, D("5"))], D("12.5"), "2026-03-01")

        self.assertEqual(
            self._amounts(),
            [("buy", "2026-01-02", D("-50.00")), ("sell", "2026-03-01", D("62.50"))],
        )
        sell = self._entries()[1]
        self.assertEqual(
            (sell["sale_id"], sell["symbol"], sell["shares"], sell["price"], sell["currency"]),
            (result["sale_id"], SYMBOL, D("5"), D("12.5"), "USD"),
        )

    def test_a_split_rewrites_the_buy_row_of_each_half(self):
        lot = self.lots.add_position(OWNER, _lot())
        (result,) = self.lots.close_lots(OWNER, [(lot, D("2"))], D("12"), "2026-03-01")

        by_lot = {e["lot_id"]: e["amount"] for e in self._entries() if e["kind"] == "buy"}
        self.assertEqual(by_lot, {lot: D("-20.00"), result["child_lot_id"]: D("-30.00")})
        self.assertEqual(
            [e["amount"] for e in self._entries() if e["kind"] == "sell"], [D("24.00")]
        )
        # The purchase is still one outlay of 50, now on two rows.
        self.assertEqual(sum(by_lot.values()), D("-50.00"))

    def test_sync_cash_rewrites_each_lot_named_and_skips_none(self):
        first = self.lots.add_position(OWNER, _lot())
        second = self.lots.add_position(OWNER, _lot(quantity=2))
        with closing(get_connection()) as conn:
            conn.execute("UPDATE cash_entries SET amount = -1 WHERE owner = %s", (OWNER,))
            # A full close passes (lot, None): there is no remainder lot.
            PortfolioRepository._sync_cash(conn, OWNER, (first, None), book_id=None)
            conn.commit()
        by_lot = {e["lot_id"]: e["amount"] for e in self._entries()}
        self.assertEqual(by_lot, {first: D("-50.00"), second: D("-1.00")})

    def test_a_failed_close_leaves_the_ledger_as_it_was(self):
        lot = self.lots.add_position(OWNER, _lot())
        with self.assertRaises(ValueError):
            self.lots.close_lots(OWNER, [(lot, D("2")), (999999999, D("1"))], D("12"), "2026-03-01")
        self.assertEqual(self._amounts(), [("buy", "2026-01-02", D("-50.00"))])

    def test_insert_sale_writes_its_sell_row(self):
        lot = self.lots.add_position(OWNER, _lot())
        self.lots.insert_sale(OWNER, lot, D("1.5"), D("20"), "2026-04-01")
        self.assertEqual(self._amounts()[1], ("sell", "2026-04-01", D("30.00")))

    def test_deleting_a_lot_takes_its_rows_with_it(self):
        lot = self.lots.add_position(OWNER, _lot())
        self.lots.close_lots(OWNER, [(lot, D("5"))], D("12"), "2026-03-01")
        deposit = self.cash.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=D("100")
        )
        self.assertEqual(len(self._entries()), 3)

        self.assertTrue(self.lots.delete_lot(OWNER, lot))
        self.assertEqual([e["entry_id"] for e in self._entries()], [deposit])

    def test_a_csv_replace_replaces_the_trade_rows_and_keeps_the_rest(self):
        old = self.lots.add_position(OWNER, _lot())
        self.lots.close_lots(OWNER, [(old, D("5"))], D("12"), "2026-03-01")
        self.cash.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=D("500"))

        self.lots.replace_owner_positions(
            OWNER, [_lot(quantity=3), _lot(quantity=4, purchase_date="2026-02-02")]
        )
        self.assertEqual(self._amounts(), [
            ("opening_balance", "2026-01-01", D("500.00")),
            ("buy", "2026-01-02", D("-30.00")),
            ("buy", "2026-02-02", D("-40.00")),
        ])

    def test_a_non_usd_lot_is_converted_at_its_stored_rates(self):
        lot = self.lots.add_position(
            OWNER, _lot(currency="EUR", fx_rate_at_purchase=D("1.10"))
        )
        self.lots.close_lots(
            OWNER, [(lot, D("5"))], D("12"), "2026-03-01", fx_rates_at_sale={"EUR": D("1.05")}
        )
        self.assertEqual(
            self._amounts(),
            [("buy", "2026-01-02", D("-55.00")), ("sell", "2026-03-01", D("63.00"))],
        )
        # The description stays in the lot's own currency.
        self.assertEqual({e["currency"] for e in self._entries()}, {"EUR"})

    def test_a_missing_rate_is_no_amount_never_one_to_one(self):
        lot = self.lots.add_position(OWNER, _lot(currency="EUR"))
        self.lots.close_lots(OWNER, [(lot, D("5"))], D("12"), "2026-03-01")
        self.assertEqual(
            self._amounts(), [("buy", "2026-01-02", None), ("sell", "2026-03-01", None)]
        )

    def test_a_filled_rate_gives_the_row_its_amount(self):
        lot = self.lots.add_position(OWNER, _lot(currency="EUR"))
        (result,) = self.lots.close_lots(OWNER, [(lot, D("5"))], D("12"), "2026-03-01")

        self.assertTrue(self.lots.fill_lot_fx_rate(OWNER, lot, D("1.10")))
        self.assertEqual([a for _, _, a in self._amounts()], [D("-55.00"), None])
        self.assertTrue(self.lots.fill_sale_fx_rate(OWNER, result["sale_id"], D("1.05")))
        self.assertEqual([a for _, _, a in self._amounts()], [D("-55.00"), D("63.00")])
        # A rate that is already there is left alone, and so is the ledger.
        self.assertFalse(self.lots.fill_lot_fx_rate(OWNER, lot, D("9")))
        self.assertEqual([a for _, _, a in self._amounts()], [D("-55.00"), D("63.00")])

    def test_a_currency_edit_clears_the_sell_amount_with_the_sale_rate(self):
        lot = self.lots.add_position(OWNER, _lot(currency="EUR", fx_rate_at_purchase=D("1.10")))
        self.lots.close_lots(
            OWNER, [(lot, D("5"))], D("12"), "2026-03-01", fx_rates_at_sale={"EUR": D("1.05")}
        )
        self.lots.update_lot(OWNER, lot, {"currency": "GBP", "fx_rate_at_purchase": D("1.30")})
        self.assertEqual([a for _, _, a in self._amounts()], [D("-65.00"), None])

        self.lots.update_lot(OWNER, lot, {"currency": "USD", "fx_rate_at_purchase": None})
        self.assertEqual([a for _, _, a in self._amounts()], [D("-50.00"), D("60.00")])


class BackfillTest(_Base):
    def _strip_trade_rows(self):
        """Put the ledger back to how it was before it existed for these lots."""
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM cash_entries WHERE owner = %s AND kind IN ('buy', 'sell')", (OWNER,)
            )
            conn.commit()

    def _seed(self):
        first = self.lots.add_position(OWNER, _lot())
        self.lots.add_position(OWNER, _lot(quantity=2, purchase_date="2026-02-02"))
        self.lots.close_lots(OWNER, [(first, D("5"))], D("12"), "2026-03-01")
        self._strip_trade_rows()

    def test_a_dry_run_counts_and_writes_nothing(self):
        self._seed()
        self.assertEqual(
            self.cash.backfill_entries(owner=OWNER),
            {"buys": 2, "sells": 1, "openings": 0},
        )
        self.assertEqual(self._entries(), [])

    def test_apply_writes_the_rows_once(self):
        self._seed()
        self.assertEqual(
            self.cash.backfill_entries(owner=OWNER, apply=True),
            {"buys": 2, "sells": 1, "openings": 0},
        )
        expected = [
            ("buy", "2026-01-02", D("-50.00")),
            ("buy", "2026-02-02", D("-20.00")),
            ("sell", "2026-03-01", D("60.00")),
        ]
        self.assertEqual(self._amounts(), expected)

        self.assertEqual(
            self.cash.backfill_entries(owner=OWNER, apply=True),
            {"buys": 0, "sells": 0, "openings": 0},
        )
        self.assertEqual(self._amounts(), expected)

    def test_a_row_that_is_there_is_never_changed(self):
        lot = self.lots.add_position(OWNER, _lot())
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE cash_entries SET amount = -1 WHERE owner = %s AND lot_id = %s", (OWNER, lot)
            )
            conn.commit()
        self.cash.backfill_entries(owner=OWNER, apply=True)
        self.assertEqual(self._amounts(), [("buy", "2026-01-02", D("-1.00"))])

    def test_a_paper_book_gets_its_starting_cash_and_a_real_book_gets_nothing(self):
        # Made the way a book was before the ledger: no opening row.
        paper = self.books.create_paper_book(owner=OWNER, name="Momentum", starting_cash=2500)
        self.assertEqual(self._entries(book_id=paper), [])

        self.assertEqual(self.cash.backfill_entries(owner=OWNER, apply=True)["openings"], 0)
        self.assertEqual(
            self.cash.backfill_entries(owner=OWNER, apply=True, book_id=paper)["openings"], 1
        )
        (opening,) = self._entries(book_id=paper)
        self.assertEqual((opening["kind"], opening["amount"]), ("opening_balance", D("2500.00")))
        with closing(get_connection()) as conn:
            made = conn.execute(
                "SELECT CAST(created_at AT TIME ZONE 'America/New_York' AS DATE) AS day "
                "FROM books WHERE book_id = %s", (paper,),
            ).fetchone()["day"]
        self.assertEqual(opening["entry_date"], str(made))

        self.assertEqual(
            self.cash.backfill_entries(owner=OWNER, apply=True, book_id=paper)["openings"], 0
        )
        self.assertEqual(self._entries(), [], "the real book is given no opening balance")

    def test_a_new_paper_book_can_open_its_own_ledger(self):
        paper = self.books.create_paper_book(
            owner=OWNER, name="Momentum", starting_cash="2500.50", opened_on="2026-05-04"
        )
        self.assertEqual(
            self._amounts(book_id=paper), [("opening_balance", "2026-05-04", D("2500.50"))]
        )

    def test_list_owners_finds_every_owner_with_a_book(self):
        self.books.ensure_real_book(owner=OWNER)
        self.books.create_paper_book(owner=OTHER, name="Momentum")
        owners = self.books.list_owners()
        self.assertIn(OWNER, owners)
        self.assertIn(OTHER, owners)
        self.assertEqual(owners, sorted(set(owners)))


class IsolationTest(_Base):
    """Owner and book isolation, named by the two coverage tests."""

    def _seed(self):
        """A real book with an opening balance, a deposit and a traded lot, and
        a paper book with its opening balance and a lot. Returns the paper book."""
        self.cash.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=D("1000"))
        self.deposit = self.cash.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-01", amount=D("250")
        )
        self.real_lot = self.lots.add_position(OWNER, _lot())
        paper = self.books.create_paper_book(
            owner=OWNER, name="Momentum", starting_cash=5000, opened_on="2026-01-01"
        )
        self.paper_lot = self.lots.add_position(OWNER, _lot(quantity=7), book_id=paper)
        return paper

    def test_wrong_owner_reads_come_back_empty(self):
        paper = self._seed()
        real = self.books.ensure_real_book(owner=OWNER)

        self.assertEqual(self._entries(OTHER), [])
        self.assertEqual(self._entries(OTHER, book_id=real), [])
        self.assertEqual(self._entries(OTHER, book_id=paper), [])
        self.assertEqual(
            self.cash.backfill_entries(owner=OTHER, book_id=paper),
            {"buys": 0, "sells": 0, "openings": 0},
        )

    def test_wrong_owner_mutations_change_nothing(self):
        paper = self._seed()
        real = self.books.ensure_real_book(owner=OWNER)
        before = (self._entries(), self._entries(book_id=paper))

        self.assertFalse(self.cash.delete_entry(owner=OTHER, entry_id=self.deposit))
        self.assertFalse(self.cash.delete_entry(owner=OTHER, entry_id=self.deposit, book_id=real))
        for book in (real, paper):
            with self.subTest(book=book):
                with self.assertRaises(BookNotFoundError):
                    self.cash.add_entry(
                        owner=OTHER, kind="deposit", entry_date="2026-02-01", amount=D("1"),
                        book_id=book,
                    )
                with self.assertRaises(BookNotFoundError):
                    self.cash.set_opening_balance(
                        owner=OTHER, entry_date="2026-01-01", amount=D("1"), book_id=book
                    )
        # A write under its own name goes to OTHER's own book, never OWNER's.
        self.cash.set_opening_balance(owner=OTHER, entry_date="2026-01-01", amount=D("7"))
        self.cash.backfill_entries(owner=OTHER, apply=True, book_id=paper)
        # A lot write by the wrong owner reaches no row and so no cash row.
        self.assertFalse(self.lots.update_lot(OTHER, self.real_lot, {"quantity": D("99")}))
        self.assertFalse(self.lots.fill_lot_fx_rate(OTHER, self.real_lot, D("2")))

        self.assertEqual((self._entries(), self._entries(book_id=paper)), before)
        self.assertEqual([(r["kind"], r["amount"]) for r in self._all_rows(OTHER)],
                         [("opening_balance", D("7.00"))])

    def test_default_reads_and_writes_stay_in_the_real_book(self):
        paper = self._seed()

        self.assertEqual(
            [e["kind"] for e in self._entries()], ["opening_balance", "buy", "deposit"]
        )
        self.assertEqual(
            self._amounts(book_id=paper),
            [("opening_balance", "2026-01-01", D("5000.00")), ("buy", "2026-01-02", D("-70.00"))],
        )
        paper_before = self._entries(book_id=paper)

        # No book named: each of these lands on, or looks in, the real book.
        self.cash.add_entry(owner=OWNER, kind="withdrawal", entry_date="2026-02-02", amount=D("-5"))
        self.cash.set_opening_balance(owner=OWNER, entry_date="2026-01-01", amount=D("900"))
        paper_opening = paper_before[0]["entry_id"]
        self.assertFalse(self.cash.delete_entry(owner=OWNER, entry_id=paper_opening))
        self.cash.backfill_entries(owner=OWNER, apply=True)

        self.assertEqual(self._entries(book_id=paper), paper_before)
        self.assertEqual(self._amounts()[0], ("opening_balance", "2026-01-01", D("900.00")))

    def test_a_named_book_reads_and_writes_only_that_book(self):
        paper = self._seed()
        real_before = self._entries()

        # A deposit made in the real book can't be deleted through the paper book.
        self.assertFalse(self.cash.delete_entry(owner=OWNER, entry_id=self.deposit, book_id=paper))
        self.cash.add_entry(
            owner=OWNER, kind="deposit", entry_date="2026-02-03", amount=D("11"), book_id=paper
        )
        self.lots.update_lot(OWNER, self.paper_lot, {"quantity": D("8")}, book_id=paper)

        self.assertEqual(self._entries(), real_before)
        self.assertEqual(
            self._amounts(book_id=paper),
            [("opening_balance", "2026-01-01", D("5000.00")),
             ("buy", "2026-01-02", D("-80.00")),
             ("deposit", "2026-02-03", D("11.00"))],
        )

    def test_a_lot_named_in_the_wrong_book_writes_no_cash_row(self):
        paper = self._seed()
        before = (self._entries(), self._entries(book_id=paper))

        # The paper lot through the default (real) book, and the real lot
        # through the paper book: neither is found, so neither is rewritten.
        self.assertFalse(self.lots.update_lot(OWNER, self.paper_lot, {"quantity": D("99")}))
        self.assertFalse(
            self.lots.update_lot(OWNER, self.real_lot, {"quantity": D("99")}, book_id=paper)
        )
        self.assertFalse(self.lots.fill_lot_fx_rate(OWNER, self.paper_lot, D("2")))

        self.assertEqual((self._entries(), self._entries(book_id=paper)), before)


if __name__ == "__main__":
    unittest.main()
