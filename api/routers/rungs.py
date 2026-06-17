"""Harvester rung routes: /api/rungs/{id}*."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import route_error, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import (
    AchieveRungRequest,
    ExecuteRungRequest,
    RungEnvelope,
    RungStatusAck,
)

router = APIRouter(prefix="/api/rungs", tags=["rungs"])


@router.get("/{rung_id}", response_model=RungEnvelope)
def get_rung(rung_id: int) -> QuantCoreJSONResponse:
    rung = services().harvester.get_rung_by_id(rung_id)
    if not rung:
        return route_error("Rung not found", 404)
    return QuantCoreJSONResponse({"rung": rung})


@router.post("/{rung_id}/achieve", response_model=RungStatusAck)
def achieve_rung(rung_id: int, body: AchieveRungRequest) -> QuantCoreJSONResponse:
    if body.trigger_price is None:
        return route_error("trigger_price is required", 400)

    updated = services().harvester.mark_rungs_achieved(
        rung_ids=[rung_id],
        trigger_price=float(body.trigger_price),
        triggered_at=body.triggered_at,
    )
    if updated == 0:
        return route_error("Rung not found or not pending", 404)
    return QuantCoreJSONResponse({"rung_id": rung_id, "status": "ACHIEVED"})


@router.post("/{rung_id}/execute", response_model=RungStatusAck)
def execute_rung(rung_id: int, body: ExecuteRungRequest) -> QuantCoreJSONResponse:
    if body.executed_price is None or body.shares_sold is None:
        return route_error("executed_price and shares_sold are required", 400)

    services().harvester.record_execution(
        rung_id=rung_id,
        executed_price=float(body.executed_price),
        shares_sold=int(body.shares_sold),
        tax_paid=float(body.tax_paid),
        executed_at=body.executed_at,
        notes=body.notes,
    )
    return QuantCoreJSONResponse({"rung_id": rung_id, "status": "EXECUTED"})
