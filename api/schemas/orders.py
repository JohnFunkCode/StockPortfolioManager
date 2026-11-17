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
    # "quote" (the live quote when placed) or "session_open". None on a
    # manual close, whose price is the last good one on record.
    price_source: Optional[str] = None
    # The time of the trade, or the daily bar, the fill price came from.
    quote_time: Optional[datetime] = None
    # stale_quote, no_quote, non_usd, insufficient_cash, insufficient_shares
    # no_open_price or stop_not_below_fill (a queued buy whose opening price
    # left no room for the stop it asked for). Set only on a rejected order.
    reject_reason: Optional[str] = None
    # market (a buy or sell that moves cash), seed (a real holding copied
    # into a new paper book, at no cost) or manual_close (a position sold at
    # the last good price because its symbol can't be quoted).
    kind: str = "market"
    # The stop a buy asked for (plan step 4.3): fixed with a level, or
    # trailing with a percentage below a mark that starts at the fill price.
    # All None on an order that asked for none.
    stop_kind: Optional[str] = None
    stop_level: Optional[Decimal] = None
    stop_trail_pct: Optional[Decimal] = None
    # manual, or the stop card's candidate the level came from.
    stop_method: Optional[str] = None
    stop_proposed_level: Optional[Decimal] = None
    # True once the fill wrote the stop. None until the order fills, and on
    # an order that asked for none. (False is no longer written: a buy whose
    # fill leaves no room for its stop is rejected.)
    stop_set: Optional[bool] = None
    # True when the request repeated an idempotency key: nothing new happened.
    replayed: bool = False


class OrdersResponse(BaseModel):
    """GET /api/portfolio/orders. The real book has no orders."""

    book_kind: str
    pending_count: int
    # Newest first.
    orders: List[Order]


class OrderStop(BaseModel):
    """The stop a buy asks for. `fixed` gives `stop_level`; `trailing` gives
    `trail_pct`, the percent below a high-water mark that starts at the fill
    price. `method` says how the level was chosen and `proposed_level` what
    the stop card suggested, when it did. No status and no source is taken."""

    kind: Literal["fixed", "trailing"]
    stop_level: Optional[Decimal] = None
    trail_pct: Optional[Decimal] = None
    method: Literal["manual", "technical", "trailing", "confluence"] = "manual"
    proposed_level: Optional[Decimal] = None


class PlaceOrderRequest(BaseModel):
    """A market order in a paper book. Give `shares` or, for a buy, `amount`
    (dollars), never both. `idempotency_key` is the caller's own id for this
    request: sending it again returns the first order and places nothing.
    A buy may carry a `stop`, which is set when the buy fills."""

    book_id: int
    symbol: str
    side: Literal["buy", "sell"]
    shares: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    idempotency_key: str
    stop: Optional[OrderStop] = None


class SeedBookRequest(BaseModel):
    """Copy the real book's open holdings into a paper book that has had no
    orders."""

    book_id: int


class SeedBookResponse(BaseModel):
    """POST /api/portfolio/orders/seed. One `seed` order per symbol."""

    book_id: int
    filled: int
    # Queued for the next session: placed outside market hours.
    pending: int
    rejected: int
    # Real holdings whose symbol an order can't name.
    skipped: List[str]
    orders: List[Order]


class ManualCloseRequest(BaseModel):
    """Sell a paper book's whole position in a symbol that can't be quoted,
    at the last good price on record. No price is sent."""

    book_id: int
    symbol: str
    idempotency_key: str
