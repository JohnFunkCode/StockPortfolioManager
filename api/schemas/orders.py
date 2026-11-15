"""Pydantic models for /api/portfolio/orders (#431 step 3.3).

Paper orders. As elsewhere the handlers return ``QuantCoreJSONResponse``
verbatim; these models document the surface and validate requests.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel


class Order(BaseModel):
    order_id: int
    book_id: int
    symbol: str
    side: str
    # A buy asks for shares or for a dollar amount. A sell asks for shares.
    requested_shares: Optional[Decimal] = None
    requested_amount: Optional[Decimal] = None
    # pending (queued for session_date), filled, cancelled or rejected.
    status: str
    # What prompted the trade, and a pointer to it.
    source: str
    source_ref: Optional[str] = None
    # The session the order belongs to: the one it filled in, or waits for.
    session_date: str
    placed_at: datetime
    # When it was filled, cancelled or rejected. None while pending.
    resolved_at: Optional[datetime] = None
    filled_shares: Optional[Decimal] = None
    fill_price: Optional[Decimal] = None
    # "quote" (the live quote when placed) or "session_open".
    price_source: Optional[str] = None
    # The time of the trade the fill price came from.
    quote_time: Optional[datetime] = None
    # stale_quote, no_quote, non_usd, insufficient_cash, insufficient_shares
    # or no_open_price. Set only on a rejected order.
    reject_reason: Optional[str] = None
    # True when the request repeated an idempotency key: nothing new happened.
    replayed: bool = False


class OrdersResponse(BaseModel):
    """GET /api/portfolio/orders. The real book has no orders."""

    book_kind: str
    pending_count: int
    # Newest first.
    orders: List[Order]


class PlaceOrderRequest(BaseModel):
    """A market order in a paper book. Give `shares` or, for a buy, `amount`
    (dollars), never both. `idempotency_key` is the caller's own id for this
    request: sending it again returns the first order and places nothing."""

    book_id: int
    symbol: str
    side: Literal["buy", "sell"]
    shares: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    idempotency_key: str
