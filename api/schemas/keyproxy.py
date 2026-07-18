"""Request/response shapes for the /api/keyproxy relay routes (BYOK 3c).

These mirror the keyproxy's own wire contract (envelope v1 + scope v1) at the
shape level only — every semantic decision (AAD checks, jti replay, budgets)
belongs to the keyproxy. The api tier relays opaque material and never logs it.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class KeyEnvelope(BaseModel):
    """Envelope v1 — sealed key material, opaque to this tier."""

    v: int
    alg: str = Field(min_length=1, max_length=128)
    kid: str = Field(min_length=1, max_length=128)
    epk: str = Field(min_length=1, max_length=512)
    iv: str = Field(min_length=1, max_length=128)
    ct: str = Field(min_length=1, max_length=8192)
    aad: dict


class KeyScope(BaseModel):
    """Scope v1 — what the sealed key may be used for."""

    v: int
    provider: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    budget: dict


class ValidateRequest(BaseModel):
    envelope: KeyEnvelope
    scope: KeyScope
