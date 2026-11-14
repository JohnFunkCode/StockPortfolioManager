"""BooksRepository against the real database (#431 step 1.1)."""

import unittest
from contextlib import closing
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import psycopg2.errors  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import BooksRepository  # noqa: E402

OWNER = "zz_books_owner"
OTHER = "zz_books_other"


class BooksRepositoryTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = BooksRepository()

    def _purge(self):
        with closing(get_connection()) as conn:
            for owner in (OWNER, OTHER):
                conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
                conn.execute("DELETE FROM books WHERE owner = %s", (owner,))
            conn.commit()

    def test_ensure_real_book_is_idempotent(self):
        first = self.repo.ensure_real_book(owner=OWNER)
        second = self.repo.ensure_real_book(owner=OWNER)

        self.assertEqual(first, second)
        book = self.repo.get_book(owner=OWNER, book_id=first)
        self.assertEqual((book["kind"], book["name"], book["owner"]), ("real", "Real", OWNER))
        self.assertEqual(book["starting_cash"], Decimal("0"))
        self.assertIsNone(book["archived_at"])

    def test_the_database_refuses_a_second_real_book(self):
        self.repo.ensure_real_book(owner=OWNER)
        with closing(get_connection()) as conn:
            with self.assertRaises(psycopg2.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO books (owner, name, kind) VALUES (%s, %s, 'real')",
                    (OWNER, "Second real"),
                )
            conn.rollback()

    def test_create_paper_book_also_gives_the_owner_a_real_book(self):
        paper = self.repo.create_paper_book(owner=OWNER, name="  Momentum  ", starting_cash="2500.50")

        books = self.repo.list_books(owner=OWNER)
        self.assertEqual([(b["kind"], b["name"]) for b in books], [("real", "Real"), ("paper", "Momentum")])
        self.assertEqual(books[1]["book_id"], paper)
        self.assertEqual(books[1]["starting_cash"], Decimal("2500.50"))

    def test_create_paper_book_rejects_bad_input(self):
        self.repo.create_paper_book(owner=OWNER, name="Momentum")
        for kwargs in (
            {"name": "Momentum"},            # this owner already has one
            {"name": "Real"},                # the real book's name
            {"name": "   "},
            {"name": "Short", "starting_cash": -1},
            # NaN and Infinity pass a `< 0` check, and Postgres would store NaN.
            {"name": "NotANumber", "starting_cash": float("nan")},
            {"name": "Endless", "starting_cash": float("inf")},
            {"name": "Words", "starting_cash": "lots"},
        ):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                self.repo.create_paper_book(owner=OWNER, **kwargs)
        self.assertEqual(len(self.repo.list_books(owner=OWNER)), 2)

    def test_two_owners_may_use_the_same_book_name(self):
        mine = self.repo.create_paper_book(owner=OWNER, name="Momentum")
        theirs = self.repo.create_paper_book(owner=OTHER, name="Momentum")
        self.assertNotEqual(mine, theirs)

    def test_archive_marks_a_paper_book_and_never_the_real_one(self):
        paper = self.repo.create_paper_book(owner=OWNER, name="Momentum")
        real = self.repo.ensure_real_book(owner=OWNER)

        self.assertFalse(self.repo.archive_book(owner=OWNER, book_id=real))
        self.assertTrue(self.repo.archive_book(owner=OWNER, book_id=paper))
        self.assertFalse(self.repo.archive_book(owner=OWNER, book_id=paper), "already archived")

        self.assertIsNone(self.repo.get_book(owner=OWNER, book_id=real)["archived_at"])
        self.assertIsNotNone(self.repo.get_book(owner=OWNER, book_id=paper)["archived_at"])
        # Archived, not deleted: it is still listed.
        self.assertEqual(len(self.repo.list_books(owner=OWNER)), 2)

    def test_wrong_owner_reads_come_back_empty(self):
        paper = self.repo.create_paper_book(owner=OWNER, name="Momentum")

        self.assertIsNone(self.repo.get_book(owner=OTHER, book_id=paper))
        self.assertEqual(self.repo.list_books(owner=OTHER), [])

    def test_wrong_owner_mutations_change_nothing(self):
        paper = self.repo.create_paper_book(owner=OWNER, name="Momentum")

        self.assertFalse(self.repo.archive_book(owner=OTHER, book_id=paper))

        self.assertIsNone(self.repo.get_book(owner=OWNER, book_id=paper)["archived_at"])
        self.assertEqual(self.repo.list_books(owner=OTHER), [])


if __name__ == "__main__":
    unittest.main()
