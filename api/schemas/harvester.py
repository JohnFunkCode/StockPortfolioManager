"""Pydantic models for the Harvester CRUD + dashboard endpoints.

These mirror ``frontend/src/api/types.ts`` exactly so the published OpenAPI
schema documents the real frontend contract. Response models are attached to
the routes via ``response_model=`` for documentation only — the handlers
return a ``QuantCoreJSONResponse`` directly, which FastAPI ships verbatim
(bypassing response-model coercion) so analytics-grade parity is preserved on
the raw service dicts (no key stripping, no Decimal/datetime re-casting).

Request models are real: FastAPI validates request bodies against them. Where
the legacy Flask app returned a custom ``{"error","status"}`` 400 for a
missing field, the field is kept optional here and the manual check is
preserved in the router so the error contract is unchanged.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Response models (OpenAPI documentation; mirror types.ts)
# ---------------------------------------------------------------------------


class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")

    instance_id: int
    symbol: str
    status: Literal["ACTIVE", "SUPERSEDED"]
    created_at: str
    asof_date: str
    price_asof: float
    shares_initial: int
    v0_floor: float
    capital_at_risk: float
    h_threshold: float
    n_iterations: int
    annual_vol: float
    r_daily: float
    history_end_date: str
    history_window_days: int
    template_id: int
    symbol_id: int
    position_id: Optional[int] = None
    stats_price_series: str
    supersedes_instance_id: Optional[int] = None
    notes: Optional[str] = None
    metadata_json: Optional[str] = None


class Rung(BaseModel):
    model_config = ConfigDict(extra="allow")

    rung_id: int
    instance_id: int
    rung_index: int
    target_price: float
    shares_before: int
    shares_sold_planned: int
    shares_after_planned: int
    expected_days_from_now: Optional[int] = None
    expected_date: Optional[str] = None
    gross_harvest_planned: float
    cumulative_harvest_planned: float
    remaining_value_planned: float
    total_wealth_planned: float
    total_return_planned: float
    status: Literal["PENDING", "ACHIEVED", "EXECUTED"]
    triggered_at: Optional[str] = None
    trigger_price: Optional[float] = None
    executed_at: Optional[str] = None
    executed_price: Optional[float] = None
    shares_sold_actual: Optional[int] = None
    gross_harvest_actual: Optional[float] = None
    tax_paid_actual: Optional[float] = None
    net_harvest_actual: Optional[float] = None
    notes: Optional[str] = None


class PlanListResponse(BaseModel):
    plans: list[Plan]


class PlanWithRungs(BaseModel):
    plan: Plan
    rungs: list[Rung]


class RungListResponse(BaseModel):
    rungs: list[Rung]


class RungEnvelope(BaseModel):
    rung: Rung


class SymbolInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol_id: int
    ticker: str
    name: Optional[str] = None
    currency: Optional[str] = None
    active_plan_id: Optional[int] = None


class SymbolListResponse(BaseModel):
    symbols: list[SymbolInfo]


class SymbolPriceResponse(BaseModel):
    ticker: str
    price: float


class DashboardStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    total_plans: int
    active_plans: Optional[int] = None
    superseded_plans: Optional[int] = None
    pending_rungs: Optional[int] = None
    achieved_rungs: Optional[int] = None
    executed_rungs: Optional[int] = None
    symbols_tracked: int
    active_alerts: int


class HealthResponse(BaseModel):
    status: str
    db_connected: bool
    message: Optional[str] = None


# Small acknowledgement payloads returned by mutating routes.


class PlanUpdatedAck(BaseModel):
    instance_id: int
    updated: bool


class PlanDeletedAck(BaseModel):
    instance_id: int
    deleted: bool


class RungStatusAck(BaseModel):
    rung_id: int
    status: str


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PlanBuildParamsRequest(BaseModel):
    history_window_days: int = 360
    n_iterations: int = 4
    alpha: float = 0.5
    min_H: float = 0.05
    max_H: float = 0.30
    max_s0: int = 1000


class CreatePlanRequest(BaseModel):
    # symbol is logically required, but kept optional so the legacy
    # {"error":"symbol is required","status":400} contract is preserved by the
    # router's manual check (rather than a 422 validation error).
    symbol: Optional[str] = None
    template_name: str = "Default Template"
    params: Optional[PlanBuildParamsRequest] = None


class UpdatePlanRequest(BaseModel):
    notes: Optional[str] = None
    metadata: Optional[dict] = None


class AchieveRungRequest(BaseModel):
    trigger_price: Optional[float] = None
    triggered_at: Optional[str] = None


class ExecuteRungRequest(BaseModel):
    executed_price: Optional[float] = None
    shares_sold: Optional[int] = None
    tax_paid: float = 0.0
    executed_at: Optional[str] = None
    notes: Optional[str] = None
