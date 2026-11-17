"""Stored stop routes (#431 phase 2, #221; plan step 4.1).

  GET   /api/portfolio/stops                   a book's stops
  GET   /api/portfolio/stops/holdings          the one stop each holding shows
  POST  /api/portfolio/stops                   set the stop on a holding
  POST  /api/portfolio/stops/{stop_id}/cancel  take an active stop off

Exactly one service call deep. The owner comes from the authenticated
principal (``require_owner``); there is no ``?owner=``. Without a ``book_id``
a route acts on the real book, and a book that isn't the owner's answers 404.

A stop here is a record. Nothing on these routes places an order at a broker
or checks a price: the daily job tests each active stop against closed daily
bars (plan step 4.2). No route takes a ``source``, so a caller can't say an
automated trigger set a stop, and none takes a status: a stop leaves
``active`` only by being cancelled, replaced by a new one, or by the checker.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from quantcore.services.stops import BookNotFoundError, StopNotFoundError

from ..auth import require_owner
from ..deps import route_error_plain, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.stops import HoldingStopsResponse, SetStopRequest, Stop, StopsResponse

router = APIRouter(prefix="/api/portfolio/stops", tags=["stops"])


@router.get("", response_model=StopsResponse)
def list_stops(
    book_id: Optional[int] = None,
    include_inactive: bool = False,
    owner: str = Depends(require_owner),
) -> QuantCoreJSONResponse:
    """A book's active stops, newest first. `include_inactive` adds the ones
    that were triggered, cancelled, replaced or closed."""
    try:
        stops = services().stops.list_stops(
            owner=owner, book_id=book_id, include_inactive=include_inactive
        )
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    return QuantCoreJSONResponse(stops)


@router.get("/holdings", response_model=HoldingStopsResponse)
def holding_stops(
    book_id: Optional[int] = None, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """The stop each of a book's holdings shows: its live stop, or one a bar
    reached while the holding was already open. One a symbol at most."""
    try:
        stops = services().stops.holding_stops(owner=owner, book_id=book_id)
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    return QuantCoreJSONResponse(stops)


@router.post("", response_model=Stop, status_code=201)
def set_stop(body: SetStopRequest, owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    """Set the stop on a holding. 201 with the stop; `replaced_stop_id` names
    the stop it took over from. 422 for a symbol the book doesn't hold or a
    level at or above the last close."""
    try:
        stop = services().stops.set_stop(
            owner=owner, book_id=body.book_id, symbol=body.symbol, kind=body.kind,
            stop_level=body.stop_level, trail_pct=body.trail_pct, method=body.method,
            proposed_level=body.proposed_level,
        )
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(stop, status_code=201)


@router.post("/{stop_id}/cancel", response_model=Stop)
def cancel_stop(
    stop_id: int, book_id: Optional[int] = None, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    try:
        stop = services().stops.cancel_stop(owner=owner, stop_id=stop_id, book_id=book_id)
    except (BookNotFoundError, StopNotFoundError) as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(stop)
