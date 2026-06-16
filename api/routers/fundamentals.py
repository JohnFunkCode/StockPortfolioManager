"""Fundamentals routes (pass-through).

Step 5 ports the single existing fundamentals endpoint; the new fundamentals
gap endpoints are added in Step 7.
  GET /api/securities/{ticker}/earnings
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
