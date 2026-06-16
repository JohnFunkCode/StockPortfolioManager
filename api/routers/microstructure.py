"""Microstructure route (Step 7 surface gap — previously MCP-only).

  GET /api/securities/{ticker}/microstructure

Fans the three MicrostructureService signals out into a single response
(mirroring the existing options-flow fan-out pattern) and returns
``{"ticker", "short_interest", "dark_pool", "bid_ask_spread"}``.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/securities", tags=["microstructure"])


@router.get("/{ticker}/microstructure")
def get_microstructure(ticker: str) -> QuantCoreJSONResponse:
    ticker = ticker.upper()
    micro = services().microstructure
    return QuantCoreJSONResponse(
        {
            "ticker": ticker,
            "short_interest": micro.get_short_interest(ticker),
            "dark_pool": micro.get_dark_pool(ticker),
            "bid_ask_spread": micro.get_bid_ask_spread(ticker),
        }
    )
