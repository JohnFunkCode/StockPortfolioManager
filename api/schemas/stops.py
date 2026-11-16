"""Pydantic models for /api/portfolio/stops (#431 phase 2, #221; plan step 4.1).

Stored stops. As elsewhere the handlers return ``QuantCoreJSONResponse``
verbatim; these models document the surface and validate requests.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel


class Stop(BaseModel):
    stop_id: int
    book_id: int
    symbol: str
    # fixed (a price) or trailing (a percentage below a high-water mark).
    kind: str
    # The price the stop sits at. For a trailing stop it is worked out from
    # the two fields below and stored.
    stop_level: Decimal
    trail_pct: Optional[Decimal] = None
    high_water_mark: Optional[Decimal] = None
    # How the level was chosen: manual, technical, trailing or confluence.
    method: str
    # What the analysis suggested, when one did. `stop_level` is what was saved.
    proposed_level: Optional[Decimal] = None
    # The last close the stop was set against, and that close's date. The
    # price is None when no close was on record.
    reference_price: Optional[Decimal] = None
    as_of: str
    # What set the stop, and a pointer to it.
    source: str
    source_ref: Optional[str] = None
    # active, triggered, cancelled, replaced or closed. Only active is live.
    status: str
    created_at: datetime
    # When it stopped being active. None while it is.
    resolved_at: Optional[datetime] = None
    # The session of the newest closed daily bar the checker has tested the
    # stop against. None until its first check.
    last_bar_checked: Optional[date] = None
    # For a triggered stop: the session of the bar that reached it, and the
    # price it was reached at (its level, or that session's open when the
    # stock opened below the level).
    triggered_session: Optional[date] = None
    trigger_price: Optional[Decimal] = None
    # When a real-book stop's breach alert went out.
    alerted_at: Optional[datetime] = None
    # On the answer to a new stop: the stop it took over from, if any.
    replaced_stop_id: Optional[int] = None


class StopsResponse(BaseModel):
    """GET /api/portfolio/stops."""

    book_kind: str
    active_count: int
    # Newest first. Active stops only unless `include_inactive` was asked for.
    stops: List[Stop]


class SetStopRequest(BaseModel):
    """The stop for one holding. A fixed stop gives `stop_level`; a trailing
    stop gives `trail_pct`, never both. Without `book_id`, the real book.
    A symbol that already has an active stop gets this one in its place."""

    symbol: str
    kind: Literal["fixed", "trailing"]
    stop_level: Optional[Decimal] = None
    trail_pct: Optional[Decimal] = None
    method: Literal["manual", "technical", "trailing", "confluence"] = "manual"
    proposed_level: Optional[Decimal] = None
    book_id: Optional[int] = None
