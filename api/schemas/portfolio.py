"""Pydantic models for the portfolio / watchlist / securities surface.

Response models mirror ``frontend/src/api/securitiesTypes.ts`` (Security,
SymbolLookupResponse, AddSecurityResponse). As with the harvester surface the
handlers return ``QuantCoreJSONResponse`` verbatim — these models exist for
OpenAPI documentation and request validation, not response coercion.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class AddPositionRequest(BaseModel):
    """POST /api/portfolio body (matches AddSecurityPayload, portfolio fields)."""

    # symbol kept optional so the handler can emit Flask's custom 400 rather
    # than FastAPI's 422 when it is missing.
    symbol: Optional[str] = None
    name: Optional[str] = ""
    currency: Optional[str] = None
    purchase_price: Optional[float] = None
    quantity: Optional[float] = None
    purchase_date: Optional[str] = None


class AddWatchlistRequest(BaseModel):
    """POST /api/watchlist body (matches AddSecurityPayload, watchlist fields)."""

    symbol: Optional[str] = None
    name: Optional[str] = ""
    currency: Optional[str] = None
    tags: Optional[List[str]] = None


class ImportPortfolioRequest(BaseModel):
    """JSON branch of POST /api/portfolio/import (multipart 'file' takes priority)."""

    path: Optional[str] = None


class CreateLotRequest(BaseModel):
    """POST /api/portfolio/lots body — creates one new lot (issue #126 Step 4.5)."""

    # symbol kept optional so the handler can emit a 400 rather than FastAPI's
    # 422 when it is missing, matching AddPositionRequest's convention.
    symbol: Optional[str] = None
    name: Optional[str] = ""
    currency: Optional[str] = None
    purchase_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    trade_date: Optional[str] = None
    fees: Optional[Decimal] = None
    acquisition_type: Optional[str] = None
    account: Optional[str] = None
    notes: Optional[str] = None


class UpdateLotRequest(BaseModel):
    """PATCH /api/portfolio/lots/{lot_id} body — corrects a mistaken lot's fields.

    Excludes symbol/status/parent_lot_id/sale_price/sale_date: those are
    system-managed (symbol is immutable; the sale fields are only ever set via
    POST .../close, not a direct user correction).
    """

    name: Optional[str] = None
    purchase_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    purchase_date: Optional[str] = None
    currency: Optional[str] = None
    trade_date: Optional[str] = None
    fees: Optional[Decimal] = None
    acquisition_type: Optional[str] = None
    account: Optional[str] = None
    notes: Optional[str] = None


class CloseLotRequest(BaseModel):
    """POST /api/portfolio/lots/{lot_id}/close body — records a sale.

    A domain event, not a field edit (AIP-136 custom method): may split the
    anchor lot, create a child with preserved lineage, and write lot_sales
    rows — hence POST, not PATCH.
    """

    shares: Decimal
    sale_price: Decimal
    sale_trade_date: str
    method: str = "FIFO"
    lots: Optional[List[Tuple[int, Decimal]]] = None
    fees: Optional[Decimal] = None


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class Security(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    symbol: str
    currency: str
    source: str
    tags: List[str]
    purchase_price: Optional[float]
    quantity: Optional[float]
    purchase_date: Optional[str]
    sale_price: Optional[float]
    sale_date: Optional[str]


class SecuritiesResponse(BaseModel):
    securities: List[Security]


class SymbolLookupResponse(BaseModel):
    symbol: str
    name: str
    suggested_tags: List[str]


class AddSecurityResponse(BaseModel):
    symbol: str
    destination: str


class RemovePositionResponse(BaseModel):
    symbol: str
    removed: bool


class RemoveWatchlistResponse(BaseModel):
    """Same shape as ``RemovePositionResponse``, kept separate so the two
    endpoints name their own component in the OpenAPI docs rather than the
    watchlist route advertising a "position" model.
    """

    symbol: str
    removed: bool


class ImportResult(BaseModel):
    owner: str
    imported: int


class LotResponse(BaseModel):
    """A single lot, enriched with live price / gain-loss / days-held / $-per-day
    (issue #126 Step 4.5). extra="allow" since PortfolioService._enrich_lot's
    dict is the source of truth for the exact key set, not this schema."""

    model_config = ConfigDict(extra="allow")

    lot_id: int
    symbol: str
    name: str
    status: Optional[str] = None
    parent_lot_id: Optional[int] = None
    purchase_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    purchase_date: Optional[str] = None
    trade_date: Optional[str] = None
    currency: str
    sale_price: Optional[Decimal] = None
    sale_date: Optional[str] = None
    fees: Optional[Decimal] = None
    acquisition_type: Optional[str] = None
    account: Optional[str] = None
    notes: Optional[str] = None
    current_price: Optional[Decimal] = None
    price_as_of: Optional[datetime] = None
    price_stale: bool = True
    gain_loss: Optional[Decimal] = None
    gain_loss_pct: Optional[Decimal] = None
    days_held: Optional[int] = None
    dollars_per_day: Optional[Decimal] = None


class LotsResponse(BaseModel):
    lots: List[LotResponse]


class SymbolRowResponse(BaseModel):
    """One symbol's roll-up: aggregate totals, period returns, MM hedge bias,
    the allocation bar's segments (issue #147 Part A), and its open child lots
    (issue #126 Step 4.5)."""

    model_config = ConfigDict(extra="allow")

    symbol: str
    lots: List[LotResponse]
    total_investment: Optional[Decimal] = None
    total_current_value: Optional[Decimal] = None
    total_gain_loss: Optional[Decimal] = None
    total_gain_loss_pct: Optional[Decimal] = None
    total_dollars_per_day: Optional[Decimal] = None
    return_7d: Optional[Decimal] = None
    return_30d: Optional[Decimal] = None
    return_90d: Optional[Decimal] = None
    mm_hedge_bias: Optional[Any] = None
    mm_hedge_bias_captured_at: Optional[Any] = None
    # Stacked allocation bar (issue #147 Part A). bar_loss is negative — see
    # quantcore.analytics.portfolio_math.allocation_segments.
    bar_base: Optional[Decimal] = None
    bar_gain: Optional[Decimal] = None
    bar_loss: Optional[Decimal] = None


class PortfolioTotals(BaseModel):
    """Portfolio-wide Summary-section totals (issue #126 Step 4.6), aggregated
    across every symbol's open lots by PortfolioService.portfolio_totals."""

    total_investment: Optional[Decimal] = None
    total_current_value: Optional[Decimal] = None
    total_gain_loss: Optional[Decimal] = None
    total_gain_loss_pct: Optional[Decimal] = None
    total_dollars_per_day: Optional[Decimal] = None


class SymbolRowsResponse(BaseModel):
    symbols: List[SymbolRowResponse]
    totals: PortfolioTotals


class CreateLotResponse(BaseModel):
    """Mirrors PortfolioService.add_position's return shape exactly — do not
    add lot_id here without also changing the service (existing unit tests
    assert exact dict equality on that return value)."""

    symbol: str


class UpdateLotResponse(BaseModel):
    lot_id: int
    updated: bool


class DeleteLotResponse(BaseModel):
    lot_id: int
    deleted: bool


class CloseLotAllocation(BaseModel):
    model_config = ConfigDict(extra="allow")

    lot_id: int
    sale_id: int
    shares_sold: Decimal
    child_lot_id: Optional[int] = None


class CloseLotResponse(BaseModel):
    symbol: str
    shares_sold: Decimal
    sale_price: Decimal
    sale_trade_date: str
    allocations: List[CloseLotAllocation]


class WatchlistFundamentalsRow(BaseModel):
    """One row of the watchlist returns + fundamentals table (issue #147 Part B5).

    Every analytical field is Optional and every one means "not known" when
    absent — a symbol the fundamentals cache has never scored, or one whose
    price history is too short for a 1y window. None of them default to 0: a
    zero score and an unscored symbol sort to very different places.
    """

    symbol: str
    name: str
    currency: str
    tags: List[str] = []

    price: Optional[float] = None
    price_as_of: Optional[str] = None
    return_5d: Optional[float] = None
    return_30d: Optional[float] = None
    return_60d: Optional[float] = None
    return_ytd: Optional[float] = None
    return_1y: Optional[float] = None

    composite_score: Optional[float] = None
    fundamental_label: Optional[str] = None
    coverage: Optional[float] = None
    sector: Optional[str] = None

    # Three fields, not one: market_cap is the native figure in
    # market_cap_currency, and only market_cap_usd is comparable across
    # securities. Sorting a mixed-currency column on market_cap is the bug that
    # put SK hynix's ₩1,456T at the top of a dollar column.
    market_cap: Optional[float] = None
    market_cap_currency: Optional[str] = None
    market_cap_usd: Optional[float] = None

    rev_cagr_3y: Optional[float] = None
    rev_cagr_score: Optional[float] = None
    rev_accel_score: Optional[float] = None
    op_margin_score: Optional[float] = None
    fcf_margin_score: Optional[float] = None
    valuation_score: Optional[float] = None
    momentum_score: Optional[float] = None

    revenue_trajectory: Optional[str] = None
    earnings_date: Optional[str] = None
    eps_acceleration: Optional[str] = None

    fundamentals_cached_at: Optional[str] = None
    fundamentals_stale: Optional[bool] = None
    fundamentals_age_hours: Optional[float] = None


class WatchlistFundamentalsResponse(BaseModel):
    generated_at: str
    as_of: Optional[str] = None
    count: int
    stale_count: int
    unscored_count: int
    rows: List[WatchlistFundamentalsRow]
