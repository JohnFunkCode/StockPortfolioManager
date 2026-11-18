"""`BookSnapshotsRepository` against the real database (#431 plan step 5.1).

The wrong-owner and wrong-book tests here are the ones
`tests/test_owner_isolation_coverage.py` and
`tests/test_book_isolation_coverage.py` name for `book_value_snapshots`.
"""

import datetime
import unittest
from contextlib import closing
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import psycopg2.errors  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.book_snapshots_repository import BookSnapshotsRepository  # noqa: E402
from quantcore.repositories.books_repository import BooksRepository  # noqa: E402

from tests.test_orders_repository import purge  # noqa: E402

OWNER = "zz_snap_repo_owner"
OTHER = "zz_snap_repo_other"
MON = datetime.date(2026, 11, 9)
TUE = datetime.date(2026, 11, 10)
D = Decimal


def snap(day=MON, cash="100.00", holdings="50.00", **overrides):
    row = {
        "session_date": day, "cash": D(cash),
        "holdings_value": None if holdings is None else D(holdings),
        "book_value": None if holdings is None else D(cash) + D(holdings),
        "flow": D("0.00"), "contributed": D(cash), "holding_count": 1, "unpriced_count": 0,
        "stale_count": 0, "fx_missing_count": 0,
    }
    row.update(overrides)
    return row


class _Base(unittest.TestCase):
    def setUp(self):
        purge((OWNER, OTHER), ())
        self.addCleanup(purge, (OWNER, OTHER), ())
        self.books = BooksRepository()
        self.repo = BookSnapshotsRepository()
        self.real = self.books.ensure_real_book(owner=OWNER)
        self.paper = self.books.create_paper_book(owner=OWNER, name="Paper", starting_cash=1000)
        self.other_real = self.books.ensure_real_book(owner=OTHER)


class SnapshotRowsTest(_Base):
    def test_a_written_snapshot_reads_back(self):
        snapshot_id = self.repo.upsert_snapshot(owner=OWNER, snapshot=snap())
        rows = self.repo.list_snapshots(owner=OWNER)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["snapshot_id"], snapshot_id)
        self.assertEqual(row["book_id"], self.real)
        self.assertEqual(row["session_date"], MON)
        self.assertEqual((row["cash"], row["holdings_value"], row["book_value"]),
                         (D("100.00"), D("50.00"), D("150.00")))
        self.assertEqual(row["flow"], D("0.00"))

    def test_a_second_run_replaces_the_sessions_row(self):
        first = self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(holdings=None))
        second = self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(holdings="70.00"))
        self.assertEqual(first, second)
        rows = self.repo.list_snapshots(owner=OWNER)
        self.assertEqual([r["book_value"] for r in rows], [D("170.00")])

    def test_range_and_previous_snapshot(self):
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(MON))
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(TUE))
        self.assertEqual([r["session_date"] for r in self.repo.list_snapshots(owner=OWNER)],
                         [MON, TUE])
        self.assertEqual(
            [r["session_date"] for r in self.repo.list_snapshots(owner=OWNER, start=TUE)], [TUE])
        self.assertEqual(
            [r["session_date"] for r in self.repo.list_snapshots(owner=OWNER, end=MON)], [MON])
        self.assertEqual(self.repo.previous_snapshot(owner=OWNER, session=TUE)["session_date"], MON)
        self.assertEqual(self.repo.previous_snapshot(owner=OWNER, session=TUE)["contributed"],
                         D("100.00"))
        self.assertIsNone(self.repo.previous_snapshot(owner=OWNER, session=MON))

    def test_a_value_that_does_not_add_up_is_refused(self):
        bad_shapes = [
            snap(book_value=D("999.00")),
            # A NULL holdings value with a book value: cash + NULL is NULL, and
            # a CHECK passes on NULL unless the rule asks IS NOT NULL first.
            snap(holdings=None, book_value=D("100.00")),
        ]
        for shape in bad_shapes:
            with self.subTest(shape=shape), self.assertRaises(psycopg2.errors.CheckViolation):
                self.repo.upsert_snapshot(owner=OWNER, snapshot=shape)

    def test_a_negative_count_is_refused(self):
        for column in ("holding_count", "unpriced_count", "stale_count", "fx_missing_count"):
            with self.subTest(column=column), self.assertRaises(psycopg2.errors.CheckViolation):
                self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(**{column: -1}))


class BookIsolationTest(_Base):
    def test_default_reads_and_writes_stay_in_the_real_book(self):
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(cash="1.00"))
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(cash="2.00"), book_id=self.paper)
        self.assertEqual([r["cash"] for r in self.repo.list_snapshots(owner=OWNER)], [D("1.00")])
        self.assertEqual(self.repo.list_snapshots(owner=OWNER)[0]["book_id"], self.real)
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(TUE))
        self.assertIsNone(
            self.repo.previous_snapshot(owner=OWNER, session=MON, book_id=self.paper))
        later = self.repo.previous_snapshot(owner=OWNER, session=TUE + datetime.timedelta(days=1))
        self.assertEqual((later["session_date"], later["book_id"]), (TUE, self.real))

    def test_a_named_book_reads_and_writes_only_that_book(self):
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(cash="2.00"), book_id=self.paper)
        rows = self.repo.list_snapshots(owner=OWNER, book_id=self.paper)
        self.assertEqual([(r["book_id"], r["cash"]) for r in rows], [(self.paper, D("2.00"))])
        self.assertEqual(self.repo.list_snapshots(owner=OWNER), [])
        previous = self.repo.previous_snapshot(owner=OWNER, session=TUE, book_id=self.paper)
        self.assertEqual((previous["session_date"], previous["book_id"]), (MON, self.paper))
        self.assertIsNone(self.repo.previous_snapshot(owner=OWNER, session=TUE))


class OwnerIsolationTest(_Base):
    def test_another_owner_reads_no_snapshots(self):
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap())
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(), book_id=self.paper)
        self.assertEqual(self.repo.list_snapshots(owner=OTHER), [])
        self.assertEqual(self.repo.list_snapshots(owner=OTHER, book_id=self.paper), [])
        self.assertEqual(self.repo.list_snapshots(owner=OTHER, book_id=self.real), [])
        self.assertIsNone(self.repo.previous_snapshot(owner=OTHER, session=TUE, book_id=self.real))
        self.assertIsNone(self.repo.previous_snapshot(owner=OTHER, session=TUE, book_id=self.paper))

    def test_wrong_owner_writes_change_nothing(self):
        self.repo.upsert_snapshot(owner=OWNER, snapshot=snap(cash="1.00"))
        # Another owner naming this owner's books writes nothing, and doesn't
        # replace the row those books have.
        self.assertIsNone(self.repo.upsert_snapshot(
            owner=OTHER, snapshot=snap(cash="9.00"), book_id=self.real))
        self.assertIsNone(self.repo.upsert_snapshot(
            owner=OTHER, snapshot=snap(cash="9.00"), book_id=self.paper))
        self.assertEqual([r["cash"] for r in self.repo.list_snapshots(owner=OWNER)], [D("1.00")])
        self.assertEqual(self.repo.list_snapshots(owner=OWNER, book_id=self.paper), [])
        # Its own default write lands in its own real book.
        self.repo.upsert_snapshot(owner=OTHER, snapshot=snap(cash="9.00"))
        self.assertEqual(self.repo.list_snapshots(owner=OTHER)[0]["book_id"], self.other_real)
        with closing(get_connection()) as conn:
            owners = {r["owner"] for r in conn.execute(
                "SELECT owner FROM book_value_snapshots WHERE book_id IN (%s, %s)",
                (self.real, self.paper)).fetchall()}
        self.assertEqual(owners, {OWNER})

    def test_a_write_never_creates_a_book(self):
        nobody = "zz_snap_repo_nobody"
        self.addCleanup(purge, (nobody,), ())
        self.assertIsNone(self.repo.upsert_snapshot(owner=nobody, snapshot=snap()))
        self.assertEqual(self.books.list_books(owner=nobody), [])


if __name__ == "__main__":
    unittest.main()
