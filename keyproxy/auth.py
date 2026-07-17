"""Bearer-JWT verification for the Key Proxy (packet 2a).

Mirrors ``api/auth.py`` semantics at keyproxy scale: auth is inert until a
verification key is configured (``QUANTCORE_JWT_SECRET``), and
``KEYPROXY_AUTH_DISABLED`` is the compose-parity override that forces auth
off even when a secret is present. When auth is inactive the dependency
returns a synthetic local caller instead of rejecting, so route code is
identical in both modes.

HS256 only for now — packet 7a swaps this module to ES256-only verification
(public key, no signing material).

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


def _auth_active() -> bool:
    """Enforce JWT only when a secret is configured and not force-disabled."""
    if os.environ.get("KEYPROXY_AUTH_DISABLED", "").strip().lower() in _TRUTHY:
        return False
    return bool(os.environ.get("QUANTCORE_JWT_SECRET"))


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
            os.environ["QUANTCORE_JWT_SECRET"],
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        raise _reject() from None

    # Same subject resolution as api/auth.py's Principal — the envelope's
    # aad.sub must match what quantcore-api attributes work to.
    sub = str(claims.get("sub") or claims.get("email") or "")
    if not sub:
        raise _reject()
    return Caller(sub=sub, claims=claims, token=credentials.credentials)
