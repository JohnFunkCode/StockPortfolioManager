"""Pydantic models for GET/PUT /api/settings (issue #124).

Response models mirror ``quantcore.services.settings.SettingsView`` — the
server is authoritative for the model catalog, so ``models`` is echoed back
on every response rather than left for the client to hardcode.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    name: str
    description: str


class SettingsResponse(BaseModel):
    chat_model: str
    models: List[ModelInfo]


class UpdateSettingsRequest(BaseModel):
    chat_model: str
