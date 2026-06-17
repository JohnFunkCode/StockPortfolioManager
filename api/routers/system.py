"""System routes: health check."""

from __future__ import annotations

from contextlib import closing

from fastapi import APIRouter

from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import HealthResponse

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
        return QuantCoreJSONResponse(
            {"status": "error", "db_connected": False, "message": str(exc)},
            status_code=500,
        )
