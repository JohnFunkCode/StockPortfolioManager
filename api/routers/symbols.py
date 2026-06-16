"""Symbol routes: /api/symbols and /api/symbols/{ticker}/price."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import route_error, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import SymbolListResponse, SymbolPriceResponse

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


@router.get("", response_model=SymbolListResponse)
def list_symbols() -> QuantCoreJSONResponse:
    symbols = services().harvester.list_all_symbols()
    return QuantCoreJSONResponse({"symbols": symbols})


@router.get("/{ticker}/price", response_model=SymbolPriceResponse)
def get_symbol_price(ticker: str) -> QuantCoreJSONResponse:
    price = services().harvester.poll_latest_close(ticker.upper())
    if price is None:
        return route_error(f"Could not fetch price for {ticker}", 404)
    return QuantCoreJSONResponse({"ticker": ticker.upper(), "price": price})
