"""`StopsRepository` against the real database (#431 plan step 4.1).

The wrong-owner and wrong-book tests here are the ones
`tests/test_owner_isolation_coverage.py` and
`tests/test_book_isolation_coverage.py` name for the `stops` table.
"""

import datetime
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
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.repositories.stops_repository import (  # noqa: E402
    StopsRepository,
    SymbolNotHeldError,
)

from tests.test_orders_repository import purge as purge_books  # noqa: E402

OWNER = "zz_stops_repo_owner"
OTHER = "zz_stops_repo_other"
SYMBOL = "ZZSTOPREPO"
UNHELD = "ZZSTOPNONE"
NOW = datetime.datetime(2026, 6, 15, 15, 0, tzinfo=datetime.timezone.utc)
LATER = NOW + datetime.timedelta(hours=1)
DAY = datetime.date(2026, 6, 12)
D = Decimal


def purge(owners, symbols):
    """Remove everything these test owners wrote. Stops first: they point at
    the books and symbols the rest of the purge deletes."""
    with closing(get_connection()) as conn:
        for owner in owners:
            conn.execute("DELETE FROM stops WHERE owner = %s", (owner,))
        conn.commit()
    purge_books(owners, symbols)


def lot(symbol=SYMBOL, **overrides):
    row = {
        "name": "Stop Co", "symbol": symbol, "purchase_price": 100.0, "quantity": 5,
        "purchase_date": "2026-01-02", "currency": "USD",
    }
    row.update(overrides)
    return row


def fixed(level="90.00", **overrides):
    stop = {
        "symbol": SYMBOL, "kind": "fixed", "stop_level": D(level), "trail_pct": None,
        "high_water_mark": None, "method": "manual", "proposed_level": None,
        "reference_price": D("100.00"), "as_of": DAY, "source": "manual",
        "source_ref": None, "created_at": NOW,
    }
    stop.update(overrides)
    return stop


class _Base(unittest.TestCase):
    def setUp(self):
        purge((OWNER, OTHER), (SYMBOL, UNHELD))
        self.addCleanup(purge, (OWNER, OTHER), (SYMBOL, UNHELD))
        self.books = BooksRepository()
        self.lots = PortfolioRepository()
        self.repo = StopsRepository(self.lots)
        # A real holding and a paper holding of the same symbol.
        self.lots.add_position(OWNER, lot())
        self.paper = self.books.create_paper_book(owner=OWNER, name="Paper", starting_cash=0)
        self.lots.add_position(OWNER, lot(), book_id=self.paper)

    def _statuses(self, owner=OWNER, book_id=None):
        return [(s["stop_id"], s["status"]) for s in self.repo.list_stops(owner=owner, book_id=book_id)]

    def _all_rows(self, owner):
        with closing(get_connection()) as conn:
            return conn.execute(
                "SELECT stop_id, status FROM stops WHERE owner = %s ORDER BY stop_id", (owner,)
            ).fetchall()


class StopRowsTest(_Base):
    def test_a_fixed_stop_round_trips(self):
        stop = self.repo.set_stop(owner=OWNER, stop=fixed(
            method="confluence", proposed_level=D("89.50"), source_ref="card",
        ))

        self.assertEqual(stop["replaced_stop_id"], None)
        self.assertEqual(
            {k: stop[k] for k in (
                "symbol", "kind", "stop_level", "trail_pct", "high_water_mark", "method",
                "proposed_level", "reference_price", "as_of", "source", "source_ref", "status",
                "created_at", "resolved_at",
            )},
            {
                "symbol": SYMBOL, "kind": "fixed", "stop_level": D("90.000000"),
                "trail_pct": None, "high_water_mark": None, "method": "confluence",
                "proposed_level": D("89.500000"), "reference_price": D("100.000000"),
                "as_of": "2026-06-12", "source": "manual", "source_ref": "card",
                "status": "active", "created_at": NOW, "resolved_at": None,
            },
        )
        stored = self.repo.get_stop(owner=OWNER, stop_id=stop["stop_id"])
        self.assertEqual({**stored, "replaced_stop_id": None}, stop)

    def test_a_trailing_stop_keeps_its_percentage_and_mark(self):
        stop = self.repo.set_stop(owner=OWNER, stop=fixed(
            "92.00", kind="trailing", trail_pct=D("8"), high_water_mark=D("100.00"),
            method="trailing",
        ))
        self.assertEqual(
            (stop["kind"], stop["stop_level"], stop["trail_pct"], stop["high_water_mark"]),
            ("trailing", D("92.000000"), D("8.0000"), D("100.000000")),
        )

    def test_a_second_stop_replaces_the_first_and_keeps_it(self):
        first = self.repo.set_stop(owner=OWNER, stop=fixed("90.00"))
        second = self.repo.set_stop(owner=OWNER, stop=fixed("95.00", created_at=LATER))

        self.assertEqual(second["replaced_stop_id"], first["stop_id"])
        self.assertEqual(
            self._statuses(), [(second["stop_id"], "active"), (first["stop_id"], "replaced")]
        )
        self.assertEqual(
            self.repo.get_stop(owner=OWNER, stop_id=first["stop_id"])["resolved_at"], LATER
        )

    def test_a_symbol_the_book_does_not_hold_gets_no_stop(self):
        with self.assertRaises(SymbolNotHeldError):
            self.repo.set_stop(owner=OWNER, stop=fixed(symbol=UNHELD))
        # A closed lot is not a holding.
        lot_id = self.lots.list_lots_for_symbol(OWNER, SYMBOL)[0]["lot_id"]
        self.lots.close_lots(OWNER, [(lot_id, D("5"))], 110.0, "2026-06-01")
        with self.assertRaises(SymbolNotHeldError):
            self.repo.set_stop(owner=OWNER, stop=fixed())
        self.assertEqual(self._all_rows(OWNER), [])

    def test_a_refused_stop_leaves_the_earlier_one_active(self):
        first = self.repo.set_stop(owner=OWNER, stop=fixed())
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.repo.set_stop(owner=OWNER, stop=fixed("0", created_at=LATER))
        self.assertEqual(self._statuses(), [(first["stop_id"], "active")])

    def test_a_stop_leaves_active_once(self):
        stop = self.repo.set_stop(owner=OWNER, stop=fixed())
        resolve = lambda status: self.repo.resolve_stop(  # noqa: E731
            owner=OWNER, stop_id=stop["stop_id"], status=status, resolved_at=LATER
        )

        self.assertTrue(resolve("triggered"))
        self.assertFalse(resolve("cancelled"))
        stored = self.repo.get_stop(owner=OWNER, stop_id=stop["stop_id"])
        self.assertEqual((stored["status"], stored["resolved_at"]), ("triggered", LATER))

    def test_a_symbol_can_have_a_new_stop_after_its_last_one_ended(self):
        first = self.repo.set_stop(owner=OWNER, stop=fixed())
        self.repo.resolve_stop(
            owner=OWNER, stop_id=first["stop_id"], status="cancelled", resolved_at=LATER
        )
        second = self.repo.set_stop(owner=OWNER, stop=fixed(created_at=LATER))
        self.assertIsNone(second["replaced_stop_id"])
        self.assertEqual(
            self._statuses(), [(second["stop_id"], "active"), (first["stop_id"], "cancelled")]
        )

    def test_the_database_refuses_a_malformed_row(self):
        book = self.books.ensure_real_book(owner=OWNER)
        bad_rows = [
            {"kind": "limit"},                                   # not a kind
            {"stop_level": D("0")},                              # not a price
            {"kind": "trailing"},                                # a trail with no percentage
            {"trail_pct": D("5")},                               # a fixed stop with one
            {"kind": "trailing", "trail_pct": D("100"), "high_water_mark": D("1")},
            {"status": "paused"},                                # not a status
            {"status": "cancelled"},                             # final with no resolved_at
            {"source": "stop"},                                  # a stop can't set a stop
        ]
        for overrides in bad_rows:
            row = {
                "kind": "fixed", "stop_level": D("90"), "trail_pct": None,
                "high_water_mark": None, "status": "active", "source": "manual", **overrides,
            }
            with self.subTest(row=overrides), closing(get_connection()) as conn:
                with self.assertRaises(psycopg2.errors.CheckViolation):
                    conn.execute(
                        "INSERT INTO stops (owner, book_id, symbol_id, kind, stop_level, "
                        "trail_pct, high_water_mark, as_of, status, source) VALUES (%s, %s, "
                        "(SELECT symbol_id FROM symbols WHERE ticker = %s), %s, %s, %s, %s, "
                        "%s, %s, %s)",
                        (OWNER, book, SYMBOL, row["kind"], row["stop_level"], row["trail_pct"],
                         row["high_water_mark"], DAY, row["status"], row["source"]),
                    )
                conn.rollback()

    def test_the_database_allows_one_active_stop_per_symbol_per_book(self):
        self.repo.set_stop(owner=OWNER, stop=fixed())
        book = self.books.ensure_real_book(owner=OWNER)
        with closing(get_connection()) as conn:
            with self.assertRaises(psycopg2.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO stops (owner, book_id, symbol_id, kind, stop_level, as_of) "
                    "VALUES (%s, %s, (SELECT symbol_id FROM symbols WHERE ticker = %s), "
                    "'fixed', 80, %s)",
                    (OWNER, book, SYMBOL, DAY),
                )
            conn.rollback()


class IsolationTest(_Base):
    def setUp(self):
        super().setUp()
        self.real_stop = self.repo.set_stop(owner=OWNER, stop=fixed("90.00"))
        self.paper_stop = self.repo.set_stop(
            owner=OWNER, stop=fixed("85.00"), book_id=self.paper
        )

    def _stored(self):
        return [tuple(r) for r in self._all_rows(OWNER)]

    def test_another_owner_reads_no_stops(self):
        self.assertEqual(self.repo.list_stops(owner=OTHER), [])
        self.assertEqual(self.repo.list_stops(owner=OTHER, book_id=self.paper), [])
        for stop, book in ((self.real_stop, None), (self.paper_stop, self.paper)):
            self.assertIsNone(
                self.repo.get_stop(owner=OTHER, stop_id=stop["stop_id"], book_id=book)
            )
        # Reading as someone with no book made none.
        self.assertEqual(self.books.list_books(owner=OTHER), [])

    def test_wrong_owner_mutations_change_nothing(self):
        before = self._stored()

        # Another owner can't set a stop in this owner's book ...
        with self.assertRaises(BookNotFoundError):
            self.repo.set_stop(owner=OTHER, stop=fixed("50.00"), book_id=self.paper)
        # ... nor in their own real book on a symbol only this owner holds ...
        with self.assertRaises(SymbolNotHeldError):
            self.repo.set_stop(owner=OTHER, stop=fixed("50.00"))
        # ... and can't end this owner's stops, by any book they can name.
        for stop in (self.real_stop, self.paper_stop):
            for book in (None, self.paper):
                self.assertFalse(self.repo.resolve_stop(
                    owner=OTHER, stop_id=stop["stop_id"], status="cancelled",
                    resolved_at=LATER, book_id=book,
                ))

        self.assertEqual(self._stored(), before)
        self.assertEqual(self._all_rows(OTHER), [])

    def test_another_owners_stop_on_the_same_symbol_is_not_replaced(self):
        self.lots.add_position(OTHER, lot())
        theirs = self.repo.set_stop(owner=OTHER, stop=fixed("70.00", created_at=LATER))

        self.assertIsNone(theirs["replaced_stop_id"])
        self.assertEqual(
            self._stored(),
            [(self.real_stop["stop_id"], "active"), (self.paper_stop["stop_id"], "active")],
        )

    def test_default_reads_and_writes_stay_in_the_real_book(self):
        # No book named is the real book: the paper stop is out of sight ...
        self.assertEqual(self._statuses(), [(self.real_stop["stop_id"], "active")])
        self.assertIsNone(self.repo.get_stop(owner=OWNER, stop_id=self.paper_stop["stop_id"]))
        # ... and out of reach.
        self.assertFalse(self.repo.resolve_stop(
            owner=OWNER, stop_id=self.paper_stop["stop_id"], status="cancelled", resolved_at=LATER
        ))
        self.assertEqual(
            self._statuses(book_id=self.paper), [(self.paper_stop["stop_id"], "active")]
        )

    def test_a_named_book_reads_and_resolves_only_that_book(self):
        self.assertEqual(
            self._statuses(book_id=self.paper), [(self.paper_stop["stop_id"], "active")]
        )
        self.assertIsNone(
            self.repo.get_stop(owner=OWNER, stop_id=self.real_stop["stop_id"], book_id=self.paper)
        )
        self.assertFalse(self.repo.resolve_stop(
            owner=OWNER, stop_id=self.real_stop["stop_id"], status="cancelled",
            resolved_at=LATER, book_id=self.paper,
        ))
        self.assertEqual(self._statuses(), [(self.real_stop["stop_id"], "active")])

    def test_a_new_stop_replaces_only_its_own_books_stop(self):
        newer = self.repo.set_stop(
            owner=OWNER, stop=fixed("88.00", created_at=LATER), book_id=self.paper
        )

        self.assertEqual(newer["replaced_stop_id"], self.paper_stop["stop_id"])
        self.assertEqual(newer["book_id"], self.paper)
        self.assertEqual(self._statuses(), [(self.real_stop["stop_id"], "active")])

    def test_a_holding_in_another_book_does_not_count(self):
        second = self.books.create_paper_book(owner=OWNER, name="Second", starting_cash=0)
        with self.assertRaises(SymbolNotHeldError):
            self.repo.set_stop(owner=OWNER, stop=fixed(), book_id=second)
        self.assertEqual(self.repo.list_stops(owner=OWNER, book_id=second), [])


if __name__ == "__main__":
    unittest.main()
