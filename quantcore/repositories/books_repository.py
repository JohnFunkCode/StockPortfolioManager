"""BooksRepository — owner-scoped persistence for the `books` table (#431).

A book is a named, owned container of positions. Each owner has exactly one
REAL book (a partial unique index makes that a database rule) and any number
of PAPER books. A book is not an account: `positions.account` is the brokerage
account *within* a book.

Rules this module keeps (CLAUDE.md, "Unified Database"):

- `owner` is a required keyword-only argument and is bound in every statement.
  Another owner's book reads as `None` and its mutations change nothing.
- Books are archived, never deleted: a position row points at its book.
- The real book is created on first write (`ensure_real_book`), never by a
  read, so reading an owner who holds nothing writes nothing.

SQL only — no analytics.
"""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.errors

from quantcore.db import get_connection

REAL = "real"
PAPER = "paper"
REAL_BOOK_NAME = "Real"

SQL_INSERT_REAL_BOOK = """
INSERT INTO books (owner, name, kind)
VALUES (:owner, 'Real', 'real')
ON CONFLICT (owner) WHERE kind = 'real' DO NOTHING;
"""

SQL_GET_REAL_BOOK_ID = """
SELECT book_id FROM books WHERE owner = :owner AND kind = 'real';
"""

SQL_GET_BOOK = """
SELECT book_id, owner, name, kind, starting_cash, created_at, archived_at
FROM books
WHERE owner = :owner AND book_id = :book_id;
"""

SQL_LIST_BOOKS = """
SELECT book_id, owner, name, kind, starting_cash, created_at, archived_at
FROM books
WHERE owner = :owner
ORDER BY CASE WHEN kind = 'real' THEN 0 ELSE 1 END, created_at, book_id;
"""

SQL_INSERT_PAPER_BOOK = """
INSERT INTO books (owner, name, kind, starting_cash)
VALUES (:owner, :name, 'paper', :starting_cash)
RETURNING book_id;
"""

# A real book is never archived: it is where every default read and write goes.
SQL_ARCHIVE_BOOK = """
UPDATE books SET archived_at = now()
WHERE owner = :owner
  AND book_id = :book_id
  AND kind = 'paper'
  AND archived_at IS NULL;
"""


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "book_id": int(row["book_id"]),
        "owner": row["owner"],
        "name": row["name"],
        "kind": row["kind"],
        "starting_cash": Decimal(str(row["starting_cash"])),
        "created_at": row["created_at"],
        "archived_at": row["archived_at"],
    }


def ensure_real_book(conn, *, owner: str) -> int:
    """Return `owner`'s real book id, creating the book if there is none.

    Runs on the caller's connection and does not commit, so the book is created
    in the same transaction as the write that needed it.
    """
    conn.execute(SQL_INSERT_REAL_BOOK, {"owner": owner})
    row = conn.execute(SQL_GET_REAL_BOOK_ID, {"owner": owner}).fetchone()
    return int(row["book_id"])


def resolve_book_id(conn, *, owner: str, book_id: Optional[int]) -> int:
    """The book a write goes to: `owner`'s real book when `book_id` is None
    (created if missing), otherwise `book_id` — which must be `owner`'s.

    Raises `ValueError` for a book that isn't this owner's; it never says
    whether the id exists for someone else.
    """
    if book_id is None:
        return ensure_real_book(conn, owner=owner)
    row = conn.execute(SQL_GET_BOOK, {"owner": owner, "book_id": book_id}).fetchone()
    if row is None:
        raise ValueError(f"book {book_id} not found for owner {owner!r}")
    return int(row["book_id"])


class BooksRepository:
    """SQL persistence for books, scoped by owner."""

    def ensure_real_book(self, *, owner: str) -> int:
        """Return `owner`'s real book id, creating the book if there is none."""
        with closing(get_connection()) as conn:
            try:
                book_id = ensure_real_book(conn, owner=owner)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return book_id

    def get_book(self, *, owner: str, book_id: int) -> Optional[Dict[str, Any]]:
        with closing(get_connection()) as conn:
            row = conn.execute(SQL_GET_BOOK, {"owner": owner, "book_id": book_id}).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list_books(self, *, owner: str) -> List[Dict[str, Any]]:
        """`owner`'s books, the real book first, archived ones included."""
        with closing(get_connection()) as conn:
            rows = conn.execute(SQL_LIST_BOOKS, {"owner": owner}).fetchall()
        return [_row_to_dict(r) for r in rows]

    def create_paper_book(self, *, owner: str, name: str, starting_cash: Any = 0) -> int:
        """Create a paper book. Returns its `book_id`.

        The owner's real book is created first if it is missing, so an owner
        with a paper book always has a real one for default reads to land on.
        Raises `ValueError` for an empty name, a negative starting cash, or a
        name this owner already uses.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("a book needs a name")
        cash = Decimal(str(starting_cash))
        if cash < 0:
            raise ValueError("starting cash cannot be negative")
        with closing(get_connection()) as conn:
            try:
                ensure_real_book(conn, owner=owner)
                cur = conn.execute(
                    SQL_INSERT_PAPER_BOOK,
                    {"owner": owner, "name": name, "starting_cash": cash},
                )
                book_id = int(cur.fetchone()["book_id"])
                conn.commit()
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise ValueError(f"a book named {name!r} already exists") from None
            except Exception:
                conn.rollback()
                raise
        return book_id

    def archive_book(self, *, owner: str, book_id: int) -> bool:
        """Archive a paper book. Returns True if a book was archived.

        False for another owner's book, the real book, or one already archived.
        """
        with closing(get_connection()) as conn:
            try:
                cur = conn.execute(SQL_ARCHIVE_BOOK, {"owner": owner, "book_id": book_id})
                archived = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return archived > 0
