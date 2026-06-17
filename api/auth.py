"""JWT authentication + identity passthrough for the REST tier (Phase 3 Step 6).

The FastAPI front door is the single enforcement point of architectural-standard-v2
(Rule 6): every AI agent reaches the services layer only through an MCP wrapper that
calls this REST tier over HTTP, forwarding the caller's ``Authorization: Bearer`` token.
This module verifies that token once, here, and resolves the calling identity so routes
and (eventually) the services-layer audit hook can attribute work to a real principal.

Local / container parity
-------------------------
Auth is **inert until configured**: the dependency enforces JWT only when a verification
key is present (``QUANTCORE_JWT_SECRET`` / ``QUANTCORE_JWT_PUBLIC_KEY``) *and*
``AUTH_DISABLED`` is not set. This preserves Phase 2's open local contract — bare
``uvicorn api.main:app``, the docker-compose stack, the React dev server, and ``main.py``
all run without tokens exactly as before — while Cloud Run turns enforcement on simply by
injecting the secret. ``AUTH_DISABLED=1`` is an explicit override that forces auth off even
if a key happens to be present (belt-and-suspenders for the compose stack). When auth is
inactive the dependency returns a synthetic ``Principal.local()`` rather than rejecting the
call, so route code is identical in both modes.

Configuration (env, read at call time so compose/tests can toggle it)
---------------------------------------------------------------------
    QUANTCORE_JWT_SECRET     shared secret for HS* algorithms (presence → auth ON)
    QUANTCORE_JWT_PUBLIC_KEY PEM public key for RS*/ES* algorithms (presence → auth ON)
    AUTH_DISABLED            truthy ("1"/"true"/"yes"/"on") → force auth OFF (local/compose)
    QUANTCORE_JWT_ALGORITHMS comma list of accepted algs (default "HS256")
    QUANTCORE_JWT_ISSUER     optional expected ``iss`` claim
    QUANTCORE_JWT_AUDIENCE   optional expected ``aud`` claim
    QUANTCORE_JWT_LEEWAY     clock-skew tolerance in seconds (default 0)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_TRUTHY = {"1", "true", "yes", "on"}

# auto_error=False so a missing/blank header reaches our dependency (which then either
# bypasses, when AUTH_DISABLED, or raises a uniform 401) rather than FastAPI's generic 403.
_bearer = HTTPBearer(auto_error=False)


def _auth_active() -> bool:
    """Enforce JWT only when a verification key is configured and not force-disabled."""
    if os.environ.get("AUTH_DISABLED", "").strip().lower() in _TRUTHY:
        return False
    return _verification_key() is not None


def _algorithms() -> list[str]:
    raw = os.environ.get("QUANTCORE_JWT_ALGORITHMS", "HS256")
    return [a.strip() for a in raw.split(",") if a.strip()] or ["HS256"]


def _leeway() -> float:
    try:
        return float(os.environ.get("QUANTCORE_JWT_LEEWAY", "0") or "0")
    except ValueError:
        return 0.0


def _verification_key() -> Optional[str]:
    """The HS secret or asymmetric public key, whichever is configured."""
    return (
        os.environ.get("QUANTCORE_JWT_PUBLIC_KEY")
        or os.environ.get("QUANTCORE_JWT_SECRET")
        or None
    )


@dataclass(frozen=True)
class Principal:
    """The authenticated caller resolved from a verified JWT (or a local stand-in).

    ``owner`` is the identity key the rest of the system attributes work to (today the
    portfolio ``?owner=`` partition); it prefers ``sub`` and falls back to ``email``.
    """

    subject: str
    email: Optional[str] = None
    name: Optional[str] = None
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict)
    token: Optional[str] = None
    is_local: bool = False

    @property
    def owner(self) -> str:
        return self.subject or (self.email or "unknown")

    @classmethod
    def local(cls) -> "Principal":
        """Synthetic principal used when AUTH_DISABLED — keeps local/compose open."""
        return cls(subject="local", name="local", roles=("local",), is_local=True)

    @classmethod
    def from_claims(cls, claims: dict[str, Any], token: Optional[str]) -> "Principal":
        roles = claims.get("roles") or claims.get("role") or []
        if isinstance(roles, str):
            roles = [roles]
        raw_scope = claims.get("scope") or claims.get("scopes") or []
        scopes = raw_scope.split() if isinstance(raw_scope, str) else list(raw_scope)
        subject = str(claims.get("sub") or claims.get("email") or "")
        return cls(
            subject=subject,
            email=claims.get("email"),
            name=claims.get("name"),
            roles=tuple(str(r) for r in roles),
            scopes=tuple(str(s) for s in scopes),
            claims=claims,
            token=token,
        )


def _decode(token: str) -> dict[str, Any]:
    key = _verification_key()
    if not key:
        # Auth enabled but no key configured → server misconfiguration, fail closed.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="authentication is enabled but no JWT verification key is configured",
        )

    options: dict[str, Any] = {}
    audience = os.environ.get("QUANTCORE_JWT_AUDIENCE") or None
    issuer = os.environ.get("QUANTCORE_JWT_ISSUER") or None
    if audience is None:
        options["verify_aud"] = False

    try:
        return jwt.decode(
            token,
            key,
            algorithms=_algorithms(),
            audience=audience,
            issuer=issuer,
            leeway=_leeway(),
            options=options,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    """FastAPI dependency: verify the Bearer JWT and return the resolved ``Principal``.

    When auth is inactive (no JWT key configured, or ``AUTH_DISABLED`` set) this bypasses
    verification and returns ``Principal.local()`` so local/compose runs stay open.
    Otherwise a missing or invalid token yields 401.
    """
    if not _auth_active():
        return Principal.local()

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = _decode(credentials.credentials)
    return Principal.from_claims(claims, credentials.credentials)
