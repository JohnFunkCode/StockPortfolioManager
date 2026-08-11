"""Dashboard routes: /api/dashboard/stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_owner
from ..deps import services
from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(owner: str = Depends(require_owner)) -> QuantCoreJSONResponse:
    stats = services().harvester.get_dashboard_stats(owner=owner)
    return QuantCoreJSONResponse(stats)
