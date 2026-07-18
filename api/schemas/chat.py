"""Request schema for POST /api/chat."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .keyproxy import KeyEnvelope, KeyScope


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=32_000)


class ChatInteraction(BaseModel):
    """One UI interaction (click inside a rendered component) — the
    backchannel. Vocabulary is validated in the service against
    BACKEND_INTERACTION_REGISTRY; this layer only enforces shape."""

    component_id: str = Field(min_length=1, max_length=64)
    component: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)
    props: dict | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    interactions: list[ChatInteraction] = Field(default_factory=list, max_length=20)
    # BYOK (packet 3c): sealed key material for this turn, relayed opaquely to
    # the keyproxy. Absent on legacy/env-key deployments.
    key_envelope: KeyEnvelope | None = None
    scope: KeyScope | None = None
