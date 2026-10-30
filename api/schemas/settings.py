"""Pydantic models for GET/PUT /api/settings (issue #124).

Response models mirror ``quantcore.services.settings.SettingsView`` — the
server is authoritative for the model catalog, so ``models`` is echoed back
on every response rather than left for the client to hardcode. The same goes
for the daily Sidekick token budget's ceiling (G2, #461).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    name: str
    description: str


class SettingsResponse(BaseModel):
    chat_model: str
    models: List[ModelInfo]
    # The user's own cap; null means the ceiling applies.
    chat_daily_token_budget: Optional[int] = None
    chat_daily_token_budget_ceiling: int
    # Today's (America/New_York) usage, outstanding reservations included.
    chat_tokens_used_today: int
    # The figure a turn is measured against: the cap, never above the ceiling.
    chat_daily_token_budget_effective: int


class UpdateSettingsRequest(BaseModel):
    """Either field may be sent alone. An explicit null budget clears the cap."""

    chat_model: Optional[str] = None
    chat_daily_token_budget: Optional[int] = None
