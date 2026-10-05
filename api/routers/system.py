"""System routes: health check."""

from __future__ import annotations

import logging
from contextlib import closing

from fastapi import APIRouter

from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> QuantCoreJSONResponse:
    """Liveness + DB connectivity probe (parity with the Flask /api/health)."""
    try:
        from quantcore.db import get_connection

        with closing(get_connection()) as conn:
            conn.execute("SELECT 1;")
        return QuantCoreJSONResponse({"status": "ok", "db_connected": True})
    except Exception as exc:  # noqa: BLE001 — mirror Flask's broad catch
        # This route is unauthenticated, and a driver error's text names the host,
        # port and user. Neither the response nor the log carries it -- only the
        # exception's type (#297, never-log policy).
        logger.warning("health: database check failed (%s)", type(exc).__name__)
        return QuantCoreJSONResponse(
            {"status": "error", "db_connected": False, "message": "database unavailable"},
            status_code=500,
        )
