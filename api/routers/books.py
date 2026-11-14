"""Books routes (#431 step 1.2).

  GET   /api/books                     the owner's books, the real book first
  POST  /api/books                     create a paper book
  POST  /api/books/{book_id}/archive   archive a paper book

Exactly one service call deep. The owner comes from the authenticated
principal (``require_owner``); there is no ``?owner=``. A book that isn't the
owner's answers 404, the same as one that doesn't exist.

There is no delete route: a book is archived, never deleted, because position
rows point at it. Archive is a POST, not a DELETE, for the same reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from quantcore.services.books import BookNotFoundError

from ..auth import require_owner
from ..deps import route_error_plain, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.books import BookResponse, BooksResponse, CreateBookRequest

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("", response_model=BooksResponse)
def list_books(
    include_archived: bool = False, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    books = services().books.list_books(owner=owner, include_archived=include_archived)
    return QuantCoreJSONResponse({"books": books})


@router.post("", response_model=BookResponse, status_code=201)
def create_book(
    body: CreateBookRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    try:
        book = services().books.create_paper_book(
            owner=owner, name=body.name, starting_cash=body.starting_cash
        )
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(book, status_code=201)


@router.post("/{book_id}/archive", response_model=BookResponse)
def archive_book(book_id: int, owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    try:
        book = services().books.archive_book(owner=owner, book_id=book_id)
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(book)
