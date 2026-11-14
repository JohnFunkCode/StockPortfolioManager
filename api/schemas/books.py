"""Pydantic models for /api/books (#431 step 1.2).

A book is a named container of positions: the owner's one real book, or a
paper book. It is not an account — a lot's brokerage account is a field on
the lot.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class BookResponse(BaseModel):
    book_id: int
    name: str
    # "real" or "paper".
    kind: str
    starting_cash: Decimal
    created_at: datetime
    archived_at: Optional[datetime] = None


class BooksResponse(BaseModel):
    books: List[BookResponse]


class CreateBookRequest(BaseModel):
    """A new paper book. The real book is never created through the API."""

    name: str
    starting_cash: Decimal = Decimal("0")
