"""Dashboard routes: /api/dashboard/stats."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import services
from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def dashboard_stats() -> QuantCoreJSONResponse:
    stats = services().harvester.get_dashboard_stats()
    return QuantCoreJSONResponse(stats)
