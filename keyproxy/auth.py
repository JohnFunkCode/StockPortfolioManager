"""Bearer-JWT verification for the Key Proxy (packet 2a; ES256-only since 7a).

Auth is inert until the ES256 verification key is configured
(``QUANTCORE_JWT_PUBLIC_KEY``, a PEM public key), and
``KEYPROXY_AUTH_DISABLED`` is the compose-parity override that forces auth
off even when a key is present. When auth is inactive the dependency
returns a synthetic local caller instead of rejecting, so route code is
identical in both modes.

ES256-only, by design (decision #13): the keyproxy holds no signing
material at all — only the public half of the quantui Express signing key —
so a compromised verifier cannot mint identities. Tokens must carry
``aud`` including ``"quantcore-keyproxy"``; a token scoped to another
service replays nowhere here. HS256 service tokens (the MCP wrappers')
are rejected: those authenticate to quantcore-api, never to the keyproxy.

Logging policy: every rejection is a uniform 401 with the same constant
detail string. Token contents and PyJWT error text never reach the response
or any log.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_TRUTHY = {"1", "true", "yes", "on"}

# auto_error=False so a missing header reaches this dependency (uniform 401)
# rather than FastAPI's generic 403 — same choice as api/auth.py.
_bearer = HTTPBearer(auto_error=False)

_REJECT = "invalid or missing bearer token"

# The audience this verifier answers to. Hardcoded, not env-driven: a
# quantcore-api-only token must never redeem an envelope here.
_AUDIENCE = "quantcore-keyproxy"


def _auth_active() -> bool:
    """Enforce JWT only when the public key is configured and not force-disabled."""
    if os.environ.get("KEYPROXY_AUTH_DISABLED", "").strip().lower() in _TRUTHY:
        return False
    return bool(os.environ.get("QUANTCORE_JWT_PUBLIC_KEY"))


@dataclass(frozen=True)
class Caller:
    """The verified caller identity — ``sub`` is what envelope AAD binds to."""

    sub: str
    claims: dict[str, Any] = field(default_factory=dict)
    token: Optional[str] = None
    is_local: bool = False

    @classmethod
    def local(cls) -> "Caller":
        """Synthetic caller used when auth is inactive (local/compose)."""
        return cls(sub="local", is_local=True)


def _reject() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_REJECT,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_caller(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Caller:
    """FastAPI dependency: verify the Bearer JWT and return the ``Caller``."""
    if not _auth_active():
        return Caller.local()

    if credentials is None or not credentials.credentials:
        raise _reject()

    try:
        claims = jwt.decode(
            credentials.credentials,
            os.environ["QUANTCORE_JWT_PUBLIC_KEY"],
            algorithms=["ES256"],
            audience=_AUDIENCE,
        )
    except jwt.PyJWTError:
        raise _reject() from None

    # Same subject resolution as api/auth.py's Principal — the envelope's
    # aad.sub must match what quantcore-api attributes work to.
    sub = str(claims.get("sub") or claims.get("email") or "")
    if not sub:
        raise _reject()
    return Caller(sub=sub, claims=claims, token=credentials.credentials)
