"""Symbol routes: /api/symbols and /api/symbols/{ticker}/price.

The symbol table itself is shared, but ``active_plan_id`` on each row is not --
it must be *this* owner's plan or the UI would link to a plan that 404s -- so
the list route is owner-scoped (issue #147 Part H1). ``/price`` reads no
owned data at all and deliberately takes no owner.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_owner
from ..deps import route_error, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import SymbolListResponse, SymbolPriceResponse

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


@router.get("", response_model=SymbolListResponse)
def list_symbols(owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    symbols = services().harvester.list_all_symbols(owner=owner)
    return QuantCoreJSONResponse({"symbols": symbols})


@router.get("/{ticker}/price", response_model=SymbolPriceResponse)
def get_symbol_price(ticker: str) -> QuantCoreJSONResponse:
    price = services().harvester.poll_latest_close(ticker.upper())
    if price is None:
        return route_error(f"Could not fetch price for {ticker}", 404)
    return QuantCoreJSONResponse({"ticker": ticker.upper(), "price": price})
