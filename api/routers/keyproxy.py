"""/api/keyproxy — pubkey discovery + key validation relays (BYOK 3c).

Exactly one service call deep; the KeyProxyService is itself a pure relay to
the keyproxy gateway. Nothing here logs — envelopes and tokens pass through
opaquely, and KeyProxyError messages are safe user-facing copy by the
gateway's error policy.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from quantcore.gateways.keyproxy_gateway import KeyProxyError

from ..auth import Principal, require_principal
from ..deps import services
from ..schemas.keyproxy import ValidateRequest

router = APIRouter(prefix="/api/keyproxy", tags=["keyproxy"])


@router.get("/publickey")
def publickey(principal: Principal = Depends(require_principal)) -> dict:
    """Relay the keyproxy's envelope-encryption keys, plus the caller's
    subject — the UI seals envelopes with ``aad.sub`` set to exactly this."""
    try:
        keys = services().keyproxy.get_public_keys()
    except KeyProxyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"keys": keys, "sub": principal.subject}


@router.post("/validate")
def validate(
    body: ValidateRequest, principal: Principal = Depends(require_principal)
) -> dict:
    """Relay a Settings-flow key validation to the keyproxy."""
    try:
        return services().keyproxy.validate_key(
            envelope=body.envelope.model_dump(),
            scope=body.scope.model_dump(),
            auth_token=principal.token or "",
        )
    except KeyProxyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
