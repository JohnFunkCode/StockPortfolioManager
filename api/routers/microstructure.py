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


# --------------------------------------------------------------------------- #
# Phase 3 Step 1 surface-gap endpoints (previously MCP-only) — the three
# microstructure signals exposed individually with their full parameter sets,
# mirroring the market_analysis MCP tool signatures. Each is one service call
# deep; the fan-out route above stays for the dashboard view.
# --------------------------------------------------------------------------- #
@router.get("/{ticker}/short-interest")
def get_short_interest(ticker: str) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().microstructure.get_short_interest(ticker.upper()))


@router.get("/{ticker}/dark-pool")
def get_dark_pool(
    ticker: str, lookback: int = 20, interval: str = "1d"
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().microstructure.get_dark_pool(ticker.upper(), lookback=lookback, interval=interval)
    )


@router.get("/{ticker}/bid-ask-spread")
def get_bid_ask_spread(ticker: str, lookback: int = 20) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().microstructure.get_bid_ask_spread(ticker.upper(), lookback=lookback)
    )
