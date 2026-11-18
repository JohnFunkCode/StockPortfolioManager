"""`OrdersRepository` against the real database (#431 step 3.3).

The wrong-owner and wrong-book tests here are the ones
`tests/test_owner_isolation_coverage.py` and
`tests/test_book_isolation_coverage.py` name for the `orders` table.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import (  # noqa: E402
    BookNotFoundError,
    BooksRepository,
)
from quantcore.repositories.cash_repository import CashRepository  # noqa: E402
from quantcore.repositories.orders_repository import OrdersRepository  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402

OWNER = "zz_orders_repo_owner"
OTHER = "zz_orders_repo_other"
SYMBOL = "ZZORDREPO"
NOW = datetime.datetime(2026, 6, 15, 15, 0, tzinfo=datetime.timezone.utc)
DAY = datetime.date(2026, 6, 15)
D = Decimal


def purge(owners, symbols):
    """Remove everything these test owners wrote, children first."""
    with closing(get_connection()) as conn:
        for owner in owners:
            conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM cash_entries WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM orders WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM book_value_snapshots WHERE owner = %s", (owner,))
            conn.execute("DELETE FROM books WHERE owner = %s", (owner,))
        for symbol in symbols:
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (symbol,))
        conn.commit()


def _order(key="k1", **overrides):
    order = {
        "symbol": SYMBOL, "side": "buy", "requested_shares": D("2"), "requested_amount": None,
        "source": "manual", "source_ref": None, "idempotency_key": key,
        "session_date": DAY, "placed_at": NOW,
    }
    order.update(overrides)
    return order


class _Base(unittest.TestCase):
    def setUp(self):
        purge((OWNER, OTHER), (SYMBOL,))
        self.addCleanup(purge, (OWNER, OTHER), (SYMBOL,))
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.cash = CashRepository()
        self.repo = OrdersRepository(self.lots)
        self.paper = self.books.create_paper_book(
            owner=OWNER, name="Paper", starting_cash=1000, opened_on=DAY
        )

    def _place(self, owner=OWNER, book_id=None, **order):
        book_id = self.paper if book_id is None else book_id
        with self.repo.transaction(owner=owner, book_id=book_id) as tx:
            return tx.insert(_order(**order))


class OrderRowsTest(_Base):
    def test_a_new_order_is_pending_and_reads_back(self):
        order, created = self._place()

        self.assertTrue(created)
        self.assertEqual(
            (order["status"], order["symbol"], order["side"], order["requested_shares"]),
            ("pending", SYMBOL, "buy", D("2")),
        )
        self.assertEqual((order["session_date"], order["placed_at"]), ("2026-06-15", NOW))
        self.assertIsNone(order["resolved_at"])
        self.assertEqual(self.repo.get_order(owner=OWNER, order_id=order["order_id"], book_id=self.paper), order)
        self.assertEqual(self.repo.list_orders(owner=OWNER, book_id=self.paper), [order])

    def test_the_same_key_twice_returns_the_first_order_and_writes_nothing(self):
        first, _ = self._place(key="dup")
        again, created = self._place(key="dup", requested_shares=D("99"))

        self.assertFalse(created)
        self.assertEqual(again, first)
        self.assertEqual(len(self.repo.list_orders(owner=OWNER, book_id=self.paper)), 1)

    def test_a_key_used_in_another_of_the_owners_books_is_refused(self):
        other_book = self.books.create_paper_book(owner=OWNER, name="Second", starting_cash=0)
        self._place(key="dup")

        with self.assertRaises(ValueError):
            self._place(book_id=other_book, key="dup")
        self.assertEqual(self.repo.list_orders(owner=OWNER, book_id=other_book), [])

    def test_two_owners_may_use_the_same_key(self):
        theirs = self.books.create_paper_book(owner=OTHER, name="Paper", starting_cash=0)
        mine, _ = self._place(key="shared")
        other, created = self._place(owner=OTHER, book_id=theirs, key="shared")

        self.assertTrue(created)
        self.assertNotEqual(other["order_id"], mine["order_id"])

    def test_orders_list_newest_first(self):
        old, _ = self._place(key="a")
        new, _ = self._place(key="b", placed_at=NOW + datetime.timedelta(minutes=1))

        listed = self.repo.list_orders(owner=OWNER, book_id=self.paper)
        self.assertEqual([o["order_id"] for o in listed], [new["order_id"], old["order_id"]])

    def test_the_database_refuses_an_order_with_both_sizes_or_neither(self):
        for sizes in (
            {"requested_shares": D("1"), "requested_amount": D("5")},
            {"requested_shares": None, "requested_amount": None},
            {"side": "sell", "requested_shares": None, "requested_amount": D("5")},
            {"requested_shares": D("0")},
        ):
            with self.subTest(sizes=sizes), self.assertRaises(Exception):
                self._place(key=str(sizes), **sizes)
        self.assertEqual(self.repo.list_orders(owner=OWNER, book_id=self.paper), [])


class ResolveTest(_Base):
    def test_an_order_leaves_pending_exactly_once(self):
        order, _ = self._place()
        oid = order["order_id"]

        with self.repo.transaction(owner=OWNER, book_id=self.paper) as tx:
            first = tx.resolve(
                oid, "filled", resolved_at=NOW, filled_shares=D("2"), fill_price=D("10.5"),
                price_source="quote", quote_time=NOW,
            )
            second = tx.resolve(oid, "cancelled", resolved_at=NOW)
            third = tx.resolve(oid, "rejected", resolved_at=NOW, reject_reason="stale_quote")

        self.assertEqual((first, second, third), (True, False, False))
        stored = self.repo.get_order(owner=OWNER, order_id=oid, book_id=self.paper)
        self.assertEqual(
            (stored["status"], stored["filled_shares"], stored["fill_price"], stored["price_source"]),
            ("filled", D("2"), D("10.5"), "quote"),
        )
        self.assertEqual((stored["resolved_at"], stored["quote_time"]), (NOW, NOW))
        self.assertIsNone(stored["reject_reason"])

    def test_a_block_that_raises_leaves_nothing_behind(self):
        with self.assertRaises(RuntimeError):
            with self.repo.transaction(owner=OWNER, book_id=self.paper) as tx:
                order, _ = tx.insert(_order())
                tx.buy({
                    "symbol": SYMBOL, "name": SYMBOL, "purchase_price": D("10"), "quantity": D("2"),
                    "purchase_date": "2026-06-15", "currency": "USD", "order_id": order["order_id"],
                })
                raise RuntimeError("crash between the lot and the order")

        self.assertEqual(self.repo.list_orders(owner=OWNER, book_id=self.paper), [])
        self.assertEqual(self.lots.list_positions(OWNER, book_id=self.paper), [])
        kinds = [e["kind"] for e in self.cash.list_entries(owner=OWNER, book_id=self.paper)]
        self.assertEqual(kinds, ["opening_balance"])

    def test_an_undone_trade_keeps_the_order_and_drops_the_lot(self):
        with self.repo.transaction(owner=OWNER, book_id=self.paper) as tx:
            order, _ = tx.insert(_order())
            tx.savepoint()
            tx.buy({
                "symbol": SYMBOL, "name": SYMBOL, "purchase_price": D("10"), "quantity": D("2"),
                "purchase_date": "2026-06-15", "currency": "USD", "order_id": order["order_id"],
            })
            self.assertEqual(len(tx.open_lots(SYMBOL)), 1)
            self.assertEqual(len(tx.cash_entries()), 2)
            tx.undo_trade()
            tx.resolve(order["order_id"], "rejected", resolved_at=NOW, reject_reason="insufficient_cash")

        stored = self.repo.get_order(owner=OWNER, order_id=order["order_id"], book_id=self.paper)
        self.assertEqual((stored["status"], stored["reject_reason"]), ("rejected", "insufficient_cash"))
        self.assertEqual(self.lots.list_positions(OWNER, book_id=self.paper), [])

    def test_queued_sells_are_summed_without_the_order_asking(self):
        first, _ = self._place(key="s1", side="sell", requested_shares=D("3"))
        second, _ = self._place(key="s2", side="sell", requested_shares=D("1.5"))
        self._place(key="b1")  # a buy is not a promise of shares
        done, _ = self._place(key="s3", side="sell", requested_shares=D("7"))

        with self.repo.transaction(owner=OWNER, book_id=self.paper) as tx:
            tx.resolve(done["order_id"], "cancelled", resolved_at=NOW)
            others = tx.queued_sell_shares(SYMBOL, excluding_order_id=second["order_id"])
            everything = tx.queued_sell_shares(SYMBOL, excluding_order_id=0)
            elsewhere = tx.queued_sell_shares("ZZNOSUCH", excluding_order_id=0)

        self.assertEqual((others, everything, elsewhere), (D("3"), D("4.5"), D("0")))


class IsolationTest(_Base):
    def setUp(self):
        super().setUp()
        self.order, _ = self._place()
        self.oid = self.order["order_id"]

    def test_another_owner_reads_no_orders(self):
        self.books.ensure_real_book(owner=OTHER)

        self.assertEqual(self.repo.list_orders(owner=OTHER, book_id=self.paper), [])
        self.assertEqual(self.repo.list_orders(owner=OTHER), [])
        self.assertIsNone(self.repo.get_order(owner=OTHER, order_id=self.oid, book_id=self.paper))
        self.assertIsNone(
            self.repo.get_order_by_key(owner=OTHER, idempotency_key="k1", book_id=self.paper)
        )

    def test_wrong_owner_mutations_change_nothing(self):
        theirs = self.books.create_paper_book(owner=OTHER, name="Paper", starting_cash=0)

        # Another owner can't open a transaction on this owner's book ...
        with self.assertRaises(BookNotFoundError):
            with self.repo.transaction(owner=OTHER, book_id=self.paper):
                pass
        # ... and from their own book this owner's order is out of reach.
        with self.repo.transaction(owner=OTHER, book_id=theirs) as tx:
            self.assertIsNone(tx.get(self.oid))
            self.assertFalse(tx.resolve(self.oid, "cancelled", resolved_at=NOW))
            self.assertEqual(tx.queued_sell_shares(SYMBOL, excluding_order_id=0), D("0"))

        stored = self.repo.get_order(owner=OWNER, order_id=self.oid, book_id=self.paper)
        self.assertEqual(stored, self.order)

    def test_default_reads_see_only_the_real_book(self):
        # No book named is the real book, which holds no orders.
        self.assertEqual(self.repo.list_orders(owner=OWNER), [])
        self.assertIsNone(self.repo.get_order(owner=OWNER, order_id=self.oid))
        self.assertIsNone(self.repo.get_order_by_key(owner=OWNER, idempotency_key="k1"))

    def test_a_named_book_reads_and_resolves_only_that_book(self):
        second = self.books.create_paper_book(owner=OWNER, name="Second", starting_cash=0)

        self.assertEqual(self.repo.list_orders(owner=OWNER, book_id=second), [])
        self.assertIsNone(self.repo.get_order(owner=OWNER, order_id=self.oid, book_id=second))
        with self.repo.transaction(owner=OWNER, book_id=second) as tx:
            self.assertIsNone(tx.get(self.oid))
            self.assertFalse(tx.resolve(self.oid, "cancelled", resolved_at=NOW))

        stored = self.repo.get_order(owner=OWNER, order_id=self.oid, book_id=self.paper)
        self.assertEqual(stored["status"], "pending")


if __name__ == "__main__":
    unittest.main()
