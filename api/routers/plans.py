"""Harvester plan routes: /api/plans* and /api/plans/{id}/rungs.

A plan belongs to an owner (issue #147 Part H1). The owner is resolved from the
authenticated principal via ``require_owner`` -- there is no ``?owner=`` param,
because there is no legitimate cross-user read path here either. Isolation is
enforced in SQL, so these routes trust a None/zero-rowcount result and turn it
into a 404 rather than fetching the row and comparing owners themselves; the
404 also keeps the endpoint from revealing which instance ids are taken.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from quantcore.services.harvester import PlanBuildParams

from ..auth import require_owner
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
def list_plans(
    status: str = "ACTIVE", owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    status = status.upper()
    if status not in ("ACTIVE", "SUPERSEDED", "CLOSED", "ALL"):
        return route_error("Invalid status filter", 400)
    plans = services().harvester.display_all_plans(status=status, owner=owner)
    return QuantCoreJSONResponse({"plans": plans})


@router.post("", status_code=201)
def create_plan(
    body: CreatePlanRequest, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
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
            symbol=body.symbol,
            template_name=body.template_name,
            params=params,
            owner=owner,
        )
    except RuntimeError as exc:
        return route_error(str(exc), 422)

    return QuantCoreJSONResponse(result, status_code=201)


@router.get("/{instance_id}", response_model=PlanWithRungs)
def get_plan(
    instance_id: int, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    svc = services().harvester
    plan = svc.get_plan_by_id(instance_id, owner=owner)
    if not plan:
        return route_error("Plan not found", 404)
    rungs = (
        svc.get_rungs_for_plan(instance_id, owner=owner)
        if plan["status"] == "ACTIVE"
        else []
    )
    return QuantCoreJSONResponse({"plan": plan, "rungs": rungs})


@router.patch("/{instance_id}", response_model=PlanUpdatedAck)
def update_plan(
    instance_id: int,
    body: UpdatePlanRequest,
    owner: str = Depends(require_owner),
) -> QuantCoreJSONResponse:
    metadata_json = json.dumps(body.metadata) if body.metadata is not None else None
    updated = services().harvester.update_plan_metadata(
        instance_id, notes=body.notes, metadata_json=metadata_json, owner=owner
    )
    if not updated:
        return route_error("Plan not found or nothing to update", 404)
    return QuantCoreJSONResponse({"instance_id": instance_id, "updated": True})


@router.delete("/{instance_id}", response_model=PlanDeletedAck)
def delete_plan(
    instance_id: int, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    deleted = services().harvester.delete_plan(instance_id, owner=owner)
    if not deleted:
        return route_error("Plan not found or not active", 404)
    return QuantCoreJSONResponse({"instance_id": instance_id, "deleted": True})


@router.get("/{instance_id}/rungs", response_model=RungListResponse)
def list_rungs(
    instance_id: int, owner: str = Depends(require_owner)
) -> QuantCoreJSONResponse:
    svc = services().harvester
    plan = svc.get_plan_by_id(instance_id, owner=owner)
    if not plan:
        return route_error("Plan not found", 404)
    rungs = (
        svc.get_rungs_for_plan(instance_id, owner=owner)
        if plan["status"] == "ACTIVE"
        else []
    )
    return QuantCoreJSONResponse({"rungs": rungs})
