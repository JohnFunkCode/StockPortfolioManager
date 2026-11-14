"""BooksService — list, create and archive an owner's books (#431 step 1.2).

A book is a named, owned container of positions: one REAL book per owner and
any number of PAPER books. A book is not an account — `positions.account` is
the brokerage account *within* a book.

Rules kept here (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument on every method.
- A read never creates a book: `list_books` for an owner with no book returns
  an empty list, and the UI shows the real book anyway.
- Books are archived, never deleted, and the real book can't be archived.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from quantcore.repositories.books_repository import (  # noqa: F401  (BookNotFoundError re-exported for routes)
    REAL,
    BookNotFoundError,
    BooksRepository,
)

MAX_BOOK_NAME_LENGTH = 60
# `books.starting_cash` is NUMERIC(18,2). A larger figure would overflow the
# column and surface as a database error rather than as bad input.
MAX_STARTING_CASH = Decimal("1000000000000")


def _public(book: Dict[str, Any]) -> Dict[str, Any]:
    """The book as a caller sees it: everything but the owner."""
    return {key: value for key, value in book.items() if key != "owner"}


class BooksService:
    def __init__(self, books_repository: BooksRepository) -> None:
        self._repo = books_repository

    def list_books(self, *, owner: str, include_archived: bool = False) -> List[Dict[str, Any]]:
        """`owner`'s books, the real book first. Archived books are left out
        unless asked for."""
        books = self._repo.list_books(owner=owner)
        if not include_archived:
            books = [book for book in books if book["archived_at"] is None]
        return [_public(book) for book in books]

    def create_paper_book(self, *, owner: str, name: str, starting_cash: Any = 0) -> Dict[str, Any]:
        """Create a paper book and return it.

        Raises `ValueError` for a name that is empty, too long or already used
        by this owner, and for a starting cash that is negative, not a finite
        number, or too large to store.
        """
        name = (name or "").strip()
        if len(name) > MAX_BOOK_NAME_LENGTH:
            raise ValueError(f"a book name can be at most {MAX_BOOK_NAME_LENGTH} characters")
        try:
            too_large = Decimal(str(starting_cash)) > MAX_STARTING_CASH
        except ArithmeticError:
            too_large = False  # not a number: the repository says so
        if too_large:
            raise ValueError("starting cash is too large")
        book_id = self._repo.create_paper_book(owner=owner, name=name, starting_cash=starting_cash)
        return _public(self._repo.get_book(owner=owner, book_id=book_id))

    def archive_book(self, *, owner: str, book_id: int) -> Dict[str, Any]:
        """Archive a paper book and return it. Archiving twice is not an error.

        Raises `BookNotFoundError` for a book that isn't this owner's, and
        `ValueError` for the real book.
        """
        book = self._repo.get_book(owner=owner, book_id=book_id)
        if book is None:
            raise BookNotFoundError(f"book {book_id} not found")
        if book["kind"] == REAL:
            raise ValueError("the real book cannot be archived")
        self._repo.archive_book(owner=owner, book_id=book_id)
        return _public(self._repo.get_book(owner=owner, book_id=book_id))
