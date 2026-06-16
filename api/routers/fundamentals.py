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
from ..schemas.fundamentals import ScoresBatchRequest

router = APIRouter(prefix="/api/securities", tags=["fundamentals"])


# --------------------------------------------------------------------------- #
# Phase 3 Step 1 surface-gap endpoints (previously MCP-only) — collection-level
# rankings/cache + the batch scorer. Declared BEFORE the templated /{ticker}/...
# routes so the literal "fundamentals/" sub-paths are never shadowed.
# --------------------------------------------------------------------------- #
@router.post("/fundamentals/scores-batch")
def get_fundamental_scores_batch(body: ScoresBatchRequest) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().fundamentals.get_fundamental_scores_batch(body.symbols)
    )


@router.get("/fundamentals/top")
def get_top_fundamental_stocks(n: int = 10, min_coverage: float = 0.5) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().fundamentals.get_top_fundamental_stocks(n, min_coverage)
    )


@router.get("/fundamentals/upcoming-earnings")
def get_upcoming_earnings(days: int = 14, include_stale: bool = False) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().fundamentals.get_upcoming_earnings(days, include_stale)
    )


@router.get("/fundamentals/cache-stats")
def get_cache_stats() -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().fundamentals.get_cache_stats())


@router.get("/fundamentals/sector-breakdown")
def get_sector_fundamental_breakdown(
    sector: str | None = None, top_n: int = 5
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().fundamentals.get_sector_fundamental_breakdown(sector, top_n)
    )


@router.get("/fundamentals/score-changes")
def get_fundamental_score_changes(
    min_delta: int = 2, since_days: int = 90, direction: str = "both"
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().fundamentals.get_fundamental_score_changes(min_delta, since_days, direction)
    )


@router.get("/{ticker}/earnings")
def get_earnings_dates(ticker: str) -> QuantCoreJSONResponse:
    # No try/except — matches Flask; errors fall through to the framework handler.
    return QuantCoreJSONResponse(services().fundamentals.get_earnings_dates(ticker))


@router.get("/{ticker}/earnings-calendar")
def get_earnings_calendar(ticker: str) -> QuantCoreJSONResponse:
    """Next earnings date + options-risk profile (distinct from /earnings, which
    returns the raw earnings dates via get_earnings_dates)."""
    return QuantCoreJSONResponse(services().fundamentals.get_earnings_calendar(ticker))


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
