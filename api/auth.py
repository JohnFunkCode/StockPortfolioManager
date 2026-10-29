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
    QUANTCORE_JWT_PUBLIC_KEY PEM public key for ES256 user tokens (presence → auth ON)
    AUTH_DISABLED            truthy ("1"/"true"/"yes"/"on") → force auth OFF (local/compose)
    QUANTCORE_JWT_ALGORITHMS comma list of accepted HS* algs (default "HS256")
    QUANTCORE_JWT_ISSUER     optional expected ``iss`` claim
    QUANTCORE_JWT_AUDIENCE   optional expected ``aud`` claim (HS path only)
    QUANTCORE_JWT_LEEWAY     clock-skew tolerance in seconds (default 0)

Dual-mode verification (BYOK packet 7a, decision #13)
-----------------------------------------------------
Two token populations coexist and are routed by the token's declared ``alg``:

* **ES256 user tokens** — short-lived, minted by the quantui Express server
  from a private key only it holds. Verified against
  ``QUANTCORE_JWT_PUBLIC_KEY`` with this service's own name
  (``"quantcore-api"``) required in ``aud``. Asymmetric on purpose: this
  verifier cannot mint identities.
* **HS* service tokens** — the MCP wrappers' long-lived tokens, verified
  against ``QUANTCORE_JWT_SECRET`` exactly as before.

Each branch is pinned to its own key *and* algorithm family — the HS branch
never sees the public key and the ES branch never sees the secret — so the
classic algorithm-confusion attack (an HS token signed with the public key
as its HMAC secret) fails closed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from quantcore.services.identity import UnknownIdentityError
from quantcore.services.registry import get_services

_TRUTHY = {"1", "true", "yes", "on"}

_security_logger = logging.getLogger("quantcore.security")

# auto_error=False so a missing/blank header reaches our dependency (which then either
# bypasses, when AUTH_DISABLED, or raises a uniform 401) rather than FastAPI's generic 403.
_bearer = HTTPBearer(auto_error=False)

# The only 401 bodies a rejected token gets (#473). PyJWT's own message ("Signature
# verification failed", "Invalid audience", ...) says how far verification got, so it
# stays out of the response; the log line carries the reason instead. Expiry is the
# one distinction worth keeping: PyJWT raises it only after the signature verified,
# and it tells a user their 90-day token lapsed.
INVALID_TOKEN_DETAIL = "invalid token"
EXPIRED_TOKEN_DETAIL = "expired token"


def _auth_active() -> bool:
    """Enforce JWT only when a verification key is configured and not force-disabled."""
    if os.environ.get("AUTH_DISABLED", "").strip().lower() in _TRUTHY:
        return False
    return _verification_key() is not None


class AuthConfigurationError(RuntimeError):
    """Raised at startup when a Cloud Run service would serve with auth inactive."""


def assert_auth_enforced_on_cloud_run() -> None:
    """Refuse to start a Cloud Run service whose auth would be inert (issue #459).

    "Inert until configured" is the local/compose contract, but ``quantcore-api`` is
    ``allUsers`` (#297) and this module is its single enforcement point: a revision
    that lost its secret binding, or picked up ``AUTH_DISABLED``, would answer every
    route as ``Principal.local()`` — and still pass its health check. Cloud Run sets
    ``K_SERVICE`` on every service (never on a Job, a CI runner or compose), so raising
    here keeps such a revision from ever taking traffic, as ``SchemaDriftError`` does.
    The message names env vars only, never their values.
    """
    service = os.environ.get("K_SERVICE")
    if not service:
        return
    if os.environ.get("AUTH_DISABLED", "").strip().lower() in _TRUTHY:
        reason = "AUTH_DISABLED is set"
    elif _verification_key() is None:
        reason = "neither QUANTCORE_JWT_PUBLIC_KEY nor QUANTCORE_JWT_SECRET is configured"
    else:
        return
    _security_logger.error("refusing to start Cloud Run service %s: %s", service, reason)
    raise AuthConfigurationError(
        f"auth would be inactive on Cloud Run service {service}: {reason}"
    )


# The audience this service requires in ES256 user tokens. Hardcoded, not
# env-driven: a token minted for another service must not authenticate here.
_ES256_AUDIENCE = "quantcore-api"


def _algorithms() -> list[str]:
    """Accepted algorithms for the shared-secret path — HS family only.

    Anything non-HS in ``QUANTCORE_JWT_ALGORITHMS`` is dropped: asymmetric
    verification is the ES256 branch's job, and letting an attacker-chosen
    ``alg`` pair with the wrong key is exactly the confusion attack.
    """
    raw = os.environ.get("QUANTCORE_JWT_ALGORITHMS", "HS256")
    algs = [a.strip() for a in raw.split(",") if a.strip().upper().startswith("HS")]
    return algs or ["HS256"]


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
        """The raw authenticated identity (IAP email or MCP token ``sub``).

        This is *not* the canonical short-handle owner used in ``positions.owner``
        etc. — that resolution happens in ``require_owner``, via ``IdentityService``.
        """
        return self.subject or (self.email or "unknown")

    @classmethod
    def local(cls) -> "Principal":
        """Synthetic principal used when AUTH_DISABLED — keeps local/compose open."""
        return cls(subject="local", name="local", roles=("local",), is_local=True)

    @classmethod
    def from_claims(cls, claims: dict[str, Any], token: Optional[str]) -> "Principal":
        """Build the principal from verified claims.

        Raises ``jwt.InvalidTokenError`` when ``roles``/``role`` or ``scope``/``scopes``
        is neither a string nor a list of strings: PyJWT doesn't check them, and a
        token we signed with a malformed claim is an invalid token, not a 500 (#472).
        """
        subject = str(claims.get("sub") or claims.get("email") or "")
        return cls(
            subject=subject,
            email=claims.get("email"),
            name=claims.get("name"),
            roles=_string_list_claim(claims, ("roles", "role"), split=False),
            scopes=_string_list_claim(claims, ("scope", "scopes"), split=True),
            claims=claims,
            token=token,
        )


def _string_list_claim(
    claims: dict[str, Any], keys: tuple[str, ...], *, split: bool
) -> tuple[str, ...]:
    """The first of ``keys`` that is set, as a tuple of strings.

    A string is one value (``split=True``: space-separated values, the OAuth scope
    form); a list must hold only strings. Anything else -- a number, an object,
    ``true``, a list with a non-string in it -- raises ``jwt.InvalidTokenError``.
    Unset, ``""`` and ``[]`` fall through to the next key, as ``or`` did before.
    """
    for key in keys:
        value = claims.get(key)
        if value not in _UNSET_CLAIM:
            return _as_strings(key, value, split=split)
    return ()


# Compared by equality: 0 and False equal none of these, so they are validated
# (and rejected) rather than read as unset.
_UNSET_CLAIM = (None, "", [])


def _as_strings(key: str, value: Any, *, split: bool) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(value.split()) if split else (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise jwt.InvalidTokenError(f"malformed {key} claim")


def _verify(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    """Verify ``token`` and return its claims; raises ``jwt.PyJWTError`` on any failure.

    ``verify_exp=False`` exists only so :func:`_decode` can name the holder of an
    expired token: the signature, ``aud`` and ``iss`` are still checked, so the
    identity it reads back is one we issued.
    """
    issuer = os.environ.get("QUANTCORE_JWT_ISSUER") or None
    alg = str(jwt.get_unverified_header(token).get("alg", ""))

    if alg == "ES256":
        # User-token branch: public key only, own name required in aud.
        public_key = os.environ.get("QUANTCORE_JWT_PUBLIC_KEY")
        if not public_key:
            raise jwt.InvalidKeyError("ES256 verification is not configured")
        return jwt.decode(
            token,
            public_key,
            algorithms=["ES256"],
            audience=_ES256_AUDIENCE,
            issuer=issuer,
            leeway=_leeway(),
            options={"verify_exp": verify_exp},
        )

    # Service-token branch: shared secret only, HS-family algorithms only.
    # The public key must never reach HMAC verification (confusion attack).
    secret = os.environ.get("QUANTCORE_JWT_SECRET")
    if not secret:
        raise jwt.InvalidKeyError("shared-secret verification is not configured")
    options: dict[str, Any] = {"verify_exp": verify_exp}
    audience = os.environ.get("QUANTCORE_JWT_AUDIENCE") or None
    if audience is None:
        options["verify_aud"] = False
    return jwt.decode(
        token,
        secret,
        algorithms=_algorithms(),
        audience=audience,
        issuer=issuer,
        leeway=_leeway(),
        options=options,
    )


def _expired_identity(token: str) -> str:
    """The verified identity behind an expired token, or ``"unknown"``.

    PyJWT checks ``exp`` before ``iss``/``aud``/``sub``, so an expired token proves
    only its signature; re-verifying with just ``exp`` skipped is what keeps a token
    minted for another service from being attributed here. Only ``sub``/``email``
    is read, and only as a string — not a full ``Principal``, whose roles/scopes
    parsing would raise on a malformed optional claim. This is diagnostics on a
    request already being rejected: nothing in it may change the 401, so any
    failure falls back to ``"unknown"``.
    """
    try:
        claims = _verify(token, verify_exp=False)
    except Exception:  # noqa: BLE001 — diagnostics only, the 401 stands either way
        return "unknown"
    for key in ("sub", "email"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _log_rejection(reason: str, identity: Optional[str] = None) -> None:
    """One ``SECURITY auth_rejected`` line per 401, for the G6 alert (issue #464).

    ``reason`` is a fixed word chosen from the exception class, never ``str(exc)``;
    ``identity`` is logged only for an expired token, and only as
    :func:`_expired_identity` re-verified it. An invalid token's claims are
    attacker-controlled and stay out of the log.
    """
    if identity is None:
        _security_logger.warning("SECURITY auth_rejected reason=%s", reason)
    else:
        _security_logger.warning(
            "SECURITY auth_rejected reason=%s identity=%s", reason, identity
        )


def _decode(token: str) -> dict[str, Any]:
    key = _verification_key()
    if not key:
        # Auth enabled but no key configured → server misconfiguration, fail closed.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="authentication is enabled but no JWT verification key is configured",
        )

    try:
        return _verify(token)
    except jwt.PyJWTError as exc:
        if isinstance(exc, jwt.ExpiredSignatureError):
            _log_rejection("expired", _expired_identity(token))
            detail = EXPIRED_TOKEN_DETAIL
        else:
            _log_rejection("invalid")
            detail = INVALID_TOKEN_DETAIL
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
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
        _log_rejection("missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = _decode(credentials.credentials)
    try:
        return Principal.from_claims(claims, credentials.credentials)
    except jwt.InvalidTokenError:
        # A malformed roles/scope claim on a token we signed (#472). Same log line
        # and body as any other invalid token, so the reply says nothing about
        # how far verification got.
        _log_rejection("invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_TOKEN_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_owner(principal: Principal = Depends(require_principal)) -> str:
    """FastAPI dependency: resolve the authenticated principal to its canonical owner.

    Every owner-scoped route depends on this instead of accepting an ``?owner=``
    query param (issue #126 decision #1) — there is no legitimate cross-user read
    path on this endpoint. An identity with no ``owner_identities`` mapping is
    never auto-provisioned (decision #2): it logs a security event and 403s,
    identically for ES256 UI tokens and HS256 MCP tokens (decision #4).

    ``Principal.local()`` (auth inactive — local/compose, no key configured) is
    exempt: it resolves straight to ``"john"``, preserving the same open-by-default
    local contract ``require_principal`` already grants ``Principal.local()``.
    """
    if principal.is_local:
        return "john"

    try:
        return get_services().identity.resolve_owner(principal.owner)
    except UnknownIdentityError:
        # Identity only — never the token, header, or raw claims.
        _security_logger.warning(
            "SECURITY unmapped_principal identity=%s at=%s",
            principal.owner,
            datetime.now(timezone.utc).isoformat(),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_provisioned")


def resolve_owner_or_none(principal: Principal) -> str | None:
    """Resolve a principal to its canonical owner, or ``None`` if unmapped.

    The soft sibling of :func:`require_owner`, for a request that is only
    *partly* owner-scoped. A Sidekick chat turn serves many tools, most of which
    read nobody's private data; 403-ing the whole turn because the caller's
    portfolio identity is unprovisioned would kill ``get_stock_price`` along with
    ``get_portfolio_summary``. So the resolution is soft here and the *tool call*
    fails closed instead (``ChatService`` refuses to touch ``PortfolioService``
    with a ``None`` owner) — the degrade is per capability, not per request.

    Not a ``Depends``: it takes the principal a route has already resolved, so a
    route can thread the answer into a context object rather than a parameter.

    Every other property of ``require_owner`` is deliberately identical — the
    local exemption, and above all **never auto-provisioning** (issue #126
    decision #2). Softening the failure must not soften the mapping rule; the
    only difference is that the caller gets to decide what the miss costs. The
    security event is logged under a **distinct** name so the soft path stays
    separable from the hard 403 in monitoring.
    """
    if principal.is_local:
        return "john"

    try:
        return get_services().identity.resolve_owner(principal.owner)
    except UnknownIdentityError:
        # Identity only — never the token, header, or raw claims.
        _security_logger.warning(
            "SECURITY unmapped_principal_soft identity=%s at=%s",
            principal.owner,
            datetime.now(timezone.utc).isoformat(),
        )
        return None
