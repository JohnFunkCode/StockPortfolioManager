"""Recommendation / stop-loss / relative-strength routes.

Step 7 surface-gap endpoints (previously MCP-only), each one call deep over
RecommendationsService:
  GET /api/securities/{ticker}/recommendation?capital=5000
  GET /api/securities/{ticker}/stop-loss?cost_basis=0&shares=0
  GET /api/securities/{ticker}/relative-strength
  GET /api/securities/{ticker}/relative-strength/history?since_days=90

Pass-through responses (analytics dicts ship verbatim via QuantCoreJSONResponse).
Query-param names mirror the service method signatures exactly so each route
stays a literal one-call-deep adapter.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/securities", tags=["recommendations"])


@router.get("/{ticker}/recommendation")
def get_trade_recommendation(ticker: str, capital: float = 5000.0) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().recommendations.get_trade_recommendation(ticker, capital=capital)
    )


@router.get("/{ticker}/stop-loss")
def get_stop_loss_analysis(
    ticker: str, cost_basis: float = 0.0, shares: int = 0, max_expirations: int = 4
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().recommendations.get_stop_loss_analysis(
            ticker, cost_basis=cost_basis, shares=shares, max_expirations=max_expirations
        )
    )


# The literal /relative-strength/history sub-path is declared before the bare
# /relative-strength route so it is never shadowed.
@router.get("/{ticker}/relative-strength/history")
def get_relative_strength_history(
    ticker: str, since_days: int = 90, rs_period: int = 21, interval: str = "1d"
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().recommendations.get_relative_strength_history(
            ticker, since_days=since_days, rs_period=rs_period, interval=interval
        )
    )


@router.get("/{ticker}/relative-strength")
def get_relative_strength(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().recommendations.get_relative_strength(ticker))
