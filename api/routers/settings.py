"""GET/PUT /api/settings — per-user Sidekick settings (issue #124).

Exactly one service call deep per the architectural standard. ``ValueError``
from an unlisted ``chat_model`` is left to propagate — the app-wide handler
in api/errors.py already turns it into a 400 ``ValidationError`` body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import Principal, require_principal
from ..deps import services
from ..json_response import QuantCoreJSONResponse
from ..schemas.settings import SettingsResponse, UpdateSettingsRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
def get_settings(principal: Principal = Depends(require_principal)) -> QuantCoreJSONResponse:
    view = services().settings.get_settings(principal.owner)
    return QuantCoreJSONResponse({"chat_model": view.chat_model, "models": view.models})


@router.put("", response_model=SettingsResponse)
def update_settings(
    body: UpdateSettingsRequest, principal: Principal = Depends(require_principal)
) -> QuantCoreJSONResponse:
    services().settings.set_chat_model(principal.owner, body.chat_model)
    view = services().settings.get_settings(principal.owner)
    return QuantCoreJSONResponse({"chat_model": view.chat_model, "models": view.models})
