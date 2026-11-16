"""Paper order routes (#431 step 3.3).

  GET   /api/portfolio/orders                    a book's orders
  POST  /api/portfolio/orders                    place a market order in a paper book
  POST  /api/portfolio/orders/{order_id}/cancel  withdraw a queued order
  POST  /api/portfolio/orders/seed               copy the real holdings into a new paper book
  POST  /api/portfolio/orders/manual-close       close a position that can't be quoted

Exactly one service call deep. The owner comes from the authenticated
principal (``require_owner``); there is no ``?owner=``. A book that isn't the
owner's answers 404.

Orders are for paper books only: the real book is refused with a 422, because
a real trade is recorded with Add Lot. The system sets every fill, so no route
takes a price or a date, and a placed order's ``source`` is always ``manual``.
That holds for a manual close too: its price is the last good one on record,
and a symbol that still has a quote is refused with a 422.

A refusal that depends on the market or on what the book holds (a stale
quote, a non-USD symbol, too little cash) is not an error response. The order
is stored and comes back with status ``rejected`` and its ``reject_reason``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from quantcore.services.orders import BookNotFoundError, OrderNotFoundError

from ..auth import require_owner
from ..deps import route_error_plain, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.orders import (
    ManualCloseRequest,
    Order,
    OrdersResponse,
    PlaceOrderRequest,
    SeedBookRequest,
    SeedBookResponse,
)

router = APIRouter(prefix="/api/portfolio/orders", tags=["orders"])


@router.get("", response_model=OrdersResponse)
def list_orders(
    book_id: Optional[int] = None, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """A book's orders, newest first. Without `book_id`, the real book, which
    has none."""
    try:
        orders = services().orders.list_orders(owner=owner, book_id=book_id)
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    return QuantCoreJSONResponse(orders)


@router.post("", response_model=Order, status_code=201)
def place_order(
    body: PlaceOrderRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """Place a market order. 201 with the order as it now is: filled,
    rejected or pending. 200 when the idempotency key had been used before."""
    try:
        order = services().orders.place_order(
            owner=owner, book_id=body.book_id, symbol=body.symbol, side=body.side,
            shares=body.shares, amount=body.amount, idempotency_key=body.idempotency_key,
        )
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(order, status_code=200 if order["replayed"] else 201)


@router.post("/{order_id}/cancel", response_model=Order)
def cancel_order(
    order_id: int, book_id: int, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    try:
        order = services().orders.cancel_order(owner=owner, order_id=order_id, book_id=book_id)
    except (BookNotFoundError, OrderNotFoundError) as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(order)


@router.post("/seed", response_model=SeedBookResponse, status_code=201)
def seed_book(body: SeedBookRequest, owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    """Copy the real book's open holdings into a paper book that has had no
    orders, on top of its starting cash. One seed order per symbol."""
    try:
        seeded = services().orders.seed_book(owner=owner, book_id=body.book_id)
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(seeded, status_code=201)


@router.post("/manual-close", response_model=Order, status_code=201)
def close_unquoted(
    body: ManualCloseRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    """Close a whole position whose symbol can't be quoted, at the last good
    price. 201 with the filled order; 200 when the key had been used before."""
    try:
        order = services().orders.close_unquoted(
            owner=owner, book_id=body.book_id, symbol=body.symbol,
            idempotency_key=body.idempotency_key,
        )
    except BookNotFoundError as exc:
        return route_error_plain(str(exc), 404)
    except ValueError as exc:
        return route_error_plain(str(exc), 422)
    return QuantCoreJSONResponse(order, status_code=200 if order["replayed"] else 201)
