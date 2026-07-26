"""Arbitrage scanner routes.

  GET /api/arbitrage/universe            curated pair list + staleness flags
  GET /api/arbitrage/scan                ranked candidates across the universe
  GET /api/arbitrage/pairs/{security}    full workup for one pair
  GET /api/arbitrage/discover            statistical sweep for unmapped links

Thin adapters — each is exactly one ArbitrageService call deep (arch-v2 §5.2).

Every numeric parameter is bounded at this edge. These routes fan out into
per-symbol price fetches, so an unbounded ``days`` or symbol list is an
expensive request, and a non-positive ``top_n`` used to slice as
``candidates[:-1]`` — quietly dropping a candidate and reporting a negative
count rather than failing.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..deps import services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])

# Each swept symbol costs a history fetch plus a ticker_info lookup.
MAX_DISCOVER_SYMBOLS = 25
MAX_DISCOVER_REFERENCES = 10

# Below ~30 daily bars the service reports "no price history" anyway; the
# ceiling is a sanity bound, well past the gateway's 730-day fetch cap.
MIN_DAYS, MAX_DAYS = 30, 3650


def _split(raw: Optional[str]) -> Optional[list[str]]:
    """Comma-separated query value -> list, or None when absent/blank.

    An empty list must not reach the service: for ``kinds`` it would filter
    every family out, which is the opposite of the "unset means all" intent.
    """
    items = [part.strip() for part in (raw or "").split(",") if part.strip()]
    return items or None


@router.get("/universe")
def get_universe() -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().arbitrage.get_universe())


@router.get("/scan")
def scan(
    kinds: Optional[str] = Query(
        None,
        max_length=200,
        description="Comma-separated families: nav_vehicle, commodity_etf, producer",
    ),
    top_n: int = Query(20, ge=1, le=100),
    days: int = Query(365, ge=MIN_DAYS, le=MAX_DAYS),
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().arbitrage.scan(kinds=_split(kinds), top_n=top_n, days=days)
    )


@router.get("/discover")
def discover(
    symbols: str = Query(..., min_length=1, max_length=500,
                         description="Comma-separated tickers to sweep"),
    references: Optional[str] = Query(
        None, max_length=200,
        description="Comma-separated reference series; defaults to the panel",
    ),
    days: int = Query(365, ge=MIN_DAYS, le=MAX_DAYS),
    min_abs_correlation: float = Query(0.4, ge=0.0, le=1.0),
    require_economic_link: bool = True,
) -> QuantCoreJSONResponse:
    symbol_list = _split(symbols)
    if not symbol_list:
        raise HTTPException(status_code=422, detail="symbols must not be blank")
    if len(symbol_list) > MAX_DISCOVER_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail=f"symbols is limited to {MAX_DISCOVER_SYMBOLS} tickers per "
                   f"request (got {len(symbol_list)})",
        )
    reference_list = _split(references)
    if reference_list and len(reference_list) > MAX_DISCOVER_REFERENCES:
        raise HTTPException(
            status_code=422,
            detail=f"references is limited to {MAX_DISCOVER_REFERENCES} series "
                   f"per request (got {len(reference_list)})",
        )
    return QuantCoreJSONResponse(
        services().arbitrage.discover_pairs(
            symbols=symbol_list,
            references=reference_list,
            days=days,
            min_abs_correlation=min_abs_correlation,
            require_economic_link=require_economic_link,
        )
    )


@router.get("/pairs/{security}")
def analyze_pair(
    security: str,
    underlying: Optional[str] = Query(None, max_length=32),
    days: int = Query(365, ge=MIN_DAYS, le=MAX_DAYS),
    # A z-score needs at least two observations to have a standard deviation.
    zscore_window: Optional[int] = Query(None, ge=2, le=MAX_DAYS),
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().arbitrage.analyze_pair(
            security.upper(),
            underlying=underlying,
            days=days,
            zscore_window=zscore_window,
        )
    )
