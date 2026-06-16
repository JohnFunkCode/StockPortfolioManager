"""Pydantic models for the portfolio / watchlist / securities surface.

Response models mirror ``frontend/src/api/securitiesTypes.ts`` (Security,
SymbolLookupResponse, AddSecurityResponse). As with the harvester surface the
handlers return ``QuantCoreJSONResponse`` verbatim — these models exist for
OpenAPI documentation and request validation, not response coercion.
"""

from __future__ import annotations

from typing import List, Optional

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


class ImportResult(BaseModel):
    owner: str
    imported: int
