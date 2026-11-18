"""Books routes (#431 steps 1.2, 5.3 and 5.4, and #579).

  GET   /api/books                     the owner's books, the real book first
  GET   /api/books/performance         one book's return, drawdown and realized
                                       P/L by source, against SPY (step 5.3)
  GET   /api/books/performance/compare every open book side by side (step 5.3)
  GET   /api/books/overview            every open book in brief, with its
                                       holdings: the read-only book tool's
                                       source (step 5.4)
  GET   /api/books/stops               every open book's stops, how far each
                                       is from the last close, and the
                                       holdings with none: the read-only
                                       stops tool's source (#579)
  POST  /api/books                     create a paper book
  POST  /api/books/{book_id}/archive   archive a paper book

Exactly one service call deep. The owner comes from the authenticated
principal (``require_owner``); there is no ``?owner=``. A book that isn't the
owner's answers 404, the same as one that doesn't exist.

There is no delete route: a book is archived, never deleted, because position
rows point at it. Archive is a POST, not a DELETE, for the same reason.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends

from quantcore.services.books import BookNotFoundError

from ..auth import require_owner
from ..deps import route_error_plain, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.books import (
    BookPerformanceResponse,
    BookResponse,
    BooksOverviewResponse,
    BooksResponse,
    BookStopsOverviewResponse,
    CompareBooksResponse,
    CreateBookRequest,
)

Period = Literal["month", "quarter", "year", "all"]

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("", response_model=BooksResponse)
def list_books(
    include_archived: bool = False, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    books = services().books.list_books(owner=owner, include_archived=include_archived)
    return QuantCoreJSONResponse({"books": books})


@router.get("/performance", response_model=BookPerformanceResponse)
def get_book_performance(
    period: Period = "year",
    book_id: Optional[int] = None,
    owner: str = Depends(require_owner),
) -> QuantCoreJSONResponse:
    """One book's time-weighted return and drawdown against SPY over a
    rolling period (the last 30, 90 or 365 days, or everything), read from
    its daily snapshots, with realized P/L by source over the same window.
    `book_id` names one of the owner's books; without it, the real book."""
    try:
        result = services().performance.book_performance(
            owner=owner, period=period, book_id=book_id
        )
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    return QuantCoreJSONResponse(result)


@router.get("/performance/compare", response_model=CompareBooksResponse)
def compare_books(
    period: Period = "year", owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """Every one of the owner's open books over the same period, the real
    book first: the Compare books view."""
    return QuantCoreJSONResponse(services().performance.compare_books(owner=owner, period=period))


@router.get("/overview", response_model=BooksOverviewResponse)
def books_overview(
    period: Period = "year", owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """Every one of the owner's open books over a rolling period: its return
    and drawdown against SPY, newest value, realized P/L by source and open
    holdings, with the growth-of-$1 series against SPY sampled a point a day,
    week or month by the period. The MCP `get_books` tool's source;
    it names no book, so it lists only the caller's."""
    return QuantCoreJSONResponse(services().performance.books_overview(owner=owner, period=period))


@router.get("/stops", response_model=BookStopsOverviewResponse)
def books_stops(owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    """Every one of the owner's open books, the real book first, with the
    stop each holding shows: its terms, the last stored close and its
    distance above the level, and whether the daily job last found it close;
    and the held symbols with no live stop. No network call. The MCP
    `get_stops` tool's source; it names no book, so it lists only the
    caller's."""
    return QuantCoreJSONResponse(services().stops.stops_overview(owner=owner))


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
