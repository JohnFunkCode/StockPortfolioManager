"""GET/PUT /api/settings — per-user Sidekick settings (issue #124).

Exactly one service call deep per the architectural standard. ``ValueError``
from an unlisted ``chat_model`` or an out-of-range daily token budget (G2,
#461) is left to propagate — the app-wide handler in api/errors.py already
turns it into a 400 ``ValidationError`` body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from quantcore.services.settings import UNCHANGED

from ..auth import Principal, require_principal
from ..deps import services
from ..json_response import QuantCoreJSONResponse
from ..schemas.settings import SettingsResponse, UpdateSettingsRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _body(view) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        {
            "chat_model": view.chat_model,
            "models": view.models,
            "chat_daily_token_budget": view.chat_daily_token_budget,
            "chat_daily_token_budget_ceiling": view.chat_daily_token_budget_ceiling,
            "chat_tokens_used_today": view.chat_tokens_used_today,
            "chat_daily_token_budget_effective": view.chat_daily_token_budget_effective,
        }
    )


@router.get("", response_model=SettingsResponse)
def get_settings(principal: Principal = Depends(require_principal)) -> QuantCoreJSONResponse:
    return _body(services().settings.get_settings(principal.owner))


@router.put("", response_model=SettingsResponse)
def update_settings(
    body: UpdateSettingsRequest, principal: Principal = Depends(require_principal)
) -> QuantCoreJSONResponse:
    # A budget key sent as null clears the cap; one left out leaves it alone.
    budget = (
        body.chat_daily_token_budget
        if "chat_daily_token_budget" in body.model_fields_set
        else UNCHANGED
    )
    return _body(
        services().settings.update(
            principal.owner, chat_model=body.chat_model, chat_daily_token_budget=budget
        )
    )
