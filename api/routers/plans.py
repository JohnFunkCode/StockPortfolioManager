"""Harvester plan routes: /api/plans* and /api/plans/{id}/rungs."""

from __future__ import annotations

import json

from fastapi import APIRouter

from quantcore.services.harvester import PlanBuildParams

from ..deps import route_error, services
from ..json_response import QuantCoreJSONResponse
from ..schemas.harvester import (
    CreatePlanRequest,
    PlanDeletedAck,
    PlanListResponse,
    PlanUpdatedAck,
    PlanWithRungs,
    RungListResponse,
    UpdatePlanRequest,
)

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("", response_model=PlanListResponse)
def list_plans(status: str = "ACTIVE") -> QuantCoreJSONResponse:
    status = status.upper()
    if status not in ("ACTIVE", "SUPERSEDED", "ALL"):
        return route_error("Invalid status filter", 400)
    plans = services().harvester.display_all_plans(status=status)
    return QuantCoreJSONResponse({"plans": plans})


@router.post("", status_code=201)
def create_plan(body: CreatePlanRequest) -> QuantCoreJSONResponse:
    if not body.symbol:
        return route_error("symbol is required", 400)

    p = body.params
    params = PlanBuildParams(
        history_window_days=p.history_window_days if p else 360,
        n_iterations=p.n_iterations if p else 4,
        alpha=p.alpha if p else 0.5,
        min_H=p.min_H if p else 0.05,
        max_H=p.max_H if p else 0.30,
        max_s0=p.max_s0 if p else 1000,
    )

    try:
        result = services().harvester.build_plan(
            symbol=body.symbol, template_name=body.template_name, params=params
        )
    except RuntimeError as exc:
        return route_error(str(exc), 422)

    return QuantCoreJSONResponse(result, status_code=201)


@router.get("/{instance_id}", response_model=PlanWithRungs)
def get_plan(instance_id: int) -> QuantCoreJSONResponse:
    svc = services().harvester
    plan = svc.get_plan_by_id(instance_id)
    if not plan:
        return route_error("Plan not found", 404)
    rungs = svc.get_rungs_for_plan(instance_id) if plan["status"] == "ACTIVE" else []
    return QuantCoreJSONResponse({"plan": plan, "rungs": rungs})


@router.patch("/{instance_id}", response_model=PlanUpdatedAck)
def update_plan(instance_id: int, body: UpdatePlanRequest) -> QuantCoreJSONResponse:
    metadata_json = json.dumps(body.metadata) if body.metadata is not None else None
    updated = services().harvester.update_plan_metadata(
        instance_id, notes=body.notes, metadata_json=metadata_json
    )
    if not updated:
        return route_error("Plan not found or nothing to update", 404)
    return QuantCoreJSONResponse({"instance_id": instance_id, "updated": True})


@router.delete("/{instance_id}", response_model=PlanDeletedAck)
def delete_plan(instance_id: int) -> QuantCoreJSONResponse:
    deleted = services().harvester.delete_plan(instance_id)
    if not deleted:
        return route_error("Plan not found or not active", 404)
    return QuantCoreJSONResponse({"instance_id": instance_id, "deleted": True})


@router.get("/{instance_id}/rungs", response_model=RungListResponse)
def list_rungs(instance_id: int) -> QuantCoreJSONResponse:
    svc = services().harvester
    plan = svc.get_plan_by_id(instance_id)
    if not plan:
        return route_error("Plan not found", 404)
    rungs = svc.get_rungs_for_plan(instance_id) if plan["status"] == "ACTIVE" else []
    return QuantCoreJSONResponse({"rungs": rungs})
