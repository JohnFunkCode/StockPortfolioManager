"""Arbitrage scanner routes.

  GET /api/arbitrage/universe            curated pair list + staleness flags
  GET /api/arbitrage/scan                ranked candidates across the universe
  GET /api/arbitrage/pairs/{security}    full workup for one pair
  GET /api/arbitrage/discover            statistical sweep for unmapped links

Thin adapters — each is exactly one ArbitrageService call deep (arch-v2 §5.2).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..deps import services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])


@router.get("/universe")
def get_universe() -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().arbitrage.get_universe())


@router.get("/scan")
def scan(
    kinds: Optional[str] = Query(
        None,
        description="Comma-separated families: nav_vehicle, commodity_etf, producer",
    ),
    top_n: int = 20,
    days: int = 365,
) -> QuantCoreJSONResponse:
    kind_list = [k for k in (kinds or "").split(",") if k.strip()] or None
    return QuantCoreJSONResponse(
        services().arbitrage.scan(kinds=kind_list, top_n=top_n, days=days)
    )


@router.get("/discover")
def discover(
    symbols: str = Query(..., description="Comma-separated tickers to sweep"),
    references: Optional[str] = Query(
        None, description="Comma-separated reference series; defaults to the panel"
    ),
    days: int = 365,
    min_abs_correlation: float = 0.4,
    require_economic_link: bool = True,
) -> QuantCoreJSONResponse:
    symbol_list = [s for s in symbols.split(",") if s.strip()]
    reference_list = [r for r in (references or "").split(",") if r.strip()] or None
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
    underlying: Optional[str] = None,
    days: int = 365,
    zscore_window: Optional[int] = None,
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().arbitrage.analyze_pair(
            security.upper(),
            underlying=underlying,
            days=days,
            zscore_window=zscore_window,
        )
    )
