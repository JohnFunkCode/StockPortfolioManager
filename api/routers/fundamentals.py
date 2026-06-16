"""Fundamentals routes (pass-through).

Step 5 ported the single pre-existing endpoint (``/earnings``); Step 7 adds the
fundamentals surface-gap endpoints, each one call deep over FundamentalsService:
  GET /api/securities/{ticker}/earnings
  GET /api/securities/{ticker}/fundamentals
  GET /api/securities/{ticker}/fundamentals/score
  GET /api/securities/{ticker}/fundamentals/revenue-growth
  GET /api/securities/{ticker}/fundamentals/earnings-acceleration
  GET /api/securities/{ticker}/fundamentals/history?data_type=&since_days=365
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/securities", tags=["fundamentals"])


@router.get("/{ticker}/earnings")
def get_earnings_dates(ticker: str) -> QuantCoreJSONResponse:
    # No try/except — matches Flask; errors fall through to the framework handler.
    return QuantCoreJSONResponse(services().fundamentals.get_earnings_dates(ticker))


# --------------------------------------------------------------------------- #
# Step 7 surface-gap endpoints (previously MCP-only)
# --------------------------------------------------------------------------- #
@router.get("/{ticker}/fundamentals")
def get_full_fundamental_profile(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().fundamentals.get_full_fundamental_profile(ticker))


@router.get("/{ticker}/fundamentals/score")
def get_fundamental_score(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().fundamentals.get_fundamental_score(ticker))


@router.get("/{ticker}/fundamentals/revenue-growth")
def get_revenue_growth(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().fundamentals.get_revenue_growth(ticker))


@router.get("/{ticker}/fundamentals/earnings-acceleration")
def get_earnings_acceleration(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().fundamentals.get_earnings_acceleration(ticker))


@router.get("/{ticker}/fundamentals/history")
def get_fundamental_history(
    ticker: str, data_type: str, since_days: int = 365
) -> QuantCoreJSONResponse:
    # data_type is required (no default in the service): one of
    # fundamental_score / revenue_growth / earnings_acceleration / earnings_calendar.
    return QuantCoreJSONResponse(
        services().fundamentals.get_fundamental_history(ticker, data_type, since_days=since_days)
    )
