"""Key Proxy FastAPI app (packet 2b).

The tiny app factory assembling the packet 2a security core (auth, replay,
sessions, scopes) and the provider registry behind the endpoint surface from
docs/proposals/byok-key-proxy-plan.md ("Endpoints"):

* ``GET  /healthz``                — liveness, no auth
* ``GET  /v1/publickey``           — envelope encryption keys, newest first
* ``POST /v1/sessions``            — redeem an envelope (exactly once) into a session
* ``DELETE /v1/sessions/{id}``     — best-effort teardown, 204 either way
* ``POST /v1/keys/validate``       — redeem + probe the key, immediate teardown

Run with:  uvicorn keyproxy.main:app --host 127.0.0.1 --port 5002
       or:  python -m keyproxy.main

Logging policy (enforced by tests): every rejection is a generic 400/401/429
with a constant detail string; request bodies are never echoed (the default
FastAPI 422, which reflects input back, is overridden). Log lines this module
emits are the allowlist lines — correlation id, sub, provider, status — on
*successful* redemption/validation/teardown, plus one diagnostic line when a
provider call fails: exception class name and, for anthropic.APIStatusError
(detected structurally, not by import), its status code and fixed error-type
enum. Never the exception message/body text itself, which may echo request
material.

TEMPORARY (test-only): the provider-error diagnostic line also logs the
provider's error message/body text, with the session's API key redacted, to
chase an intermittent invalid_request_error on large tool_result follow-up
turns. Revert this block (and the matching test changes in
test_keyproxy_streaming.py) once that's diagnosed — see the "TEMPORARY"
comment at the log call site.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from keyproxy import crypto
from keyproxy.auth import Caller, require_caller
from keyproxy.providers import get_provider
from keyproxy.replay import JtiReplaySet, ReplayCapacityError, SubRateLimiter
from keyproxy.scopes import (
    BudgetExceededError,
    Scope,
    ScopeError,
    validate_scope,
)
from keyproxy.sessions import (
    HARD_LIFETIME_CAP_SECONDS,
    SessionError,
    SessionStore,
)

# Cloud Run's `uvicorn keyproxy.main:app` entrypoint never hits
# `if __name__ == "__main__"`, and uvicorn's own logging config only touches
# the "uvicorn*" loggers, leaving the root logger at its WARNING default —
# every logger.info(...) call below (session redeemed/deleted, turn streamed,
# key validated) was silently dropped before reaching Cloud Logging.
# basicConfig() is a no-op if a handler is already attached to root.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("keyproxy")

_INVALID = "invalid request"
_RATE_LIMITED = "rate limit exceeded"
_UNAVAILABLE = "temporarily unavailable"

DEV_KID = "kp-dev-ephemeral"


DEFAULT_HEARTBEAT_SECONDS = 15.0


def _max_iat_skew() -> int:
    return int(
        os.environ.get(
            "KEYPROXY_MAX_SKEW", str(crypto.DEFAULT_MAX_IAT_SKEW_SECONDS)
        )
    )


def _heartbeat_secs() -> float:
    return float(
        os.environ.get("KEYPROXY_HEARTBEAT_SECS", str(DEFAULT_HEARTBEAT_SECONDS))
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


# The complete, closed set of provider-error reason codes emitted on the SSE
# `error` frame. Only a member of this set ever crosses the wire — never the
# provider's raw message/body text (which may echo request material, e.g. an
# API key embedded in a message). The gateway maps each code to user-facing
# copy; an unknown/absent code falls back to the generic provider message, so
# adding a code here is forward/backward compatible across a deploy skew.
PROVIDER_REASON_CODES = frozenset({
    "insufficient_credits",
    "authentication",
    "permission",
    "rate_limit",
    "overloaded",
    "model_unavailable",
    "provider_error",
})


def _provider_reason(status_code, error_type, error_message) -> str:
    """Classify a provider failure into one closed-set reason code.

    ``error_message`` is untrusted and is *inspected only* — for the billing
    case it is matched against a fixed, request-material-free substring — and
    is never itself forwarded. Generic ``invalid_request_error`` complaints
    (e.g. a malformed follow-up turn) deliberately fall through to the opaque
    ``provider_error`` bucket rather than leaking their structural text.
    """
    message = (error_message or "").lower()
    if "credit balance is too low" in message:
        return "insufficient_credits"
    if error_type == "authentication_error":
        return "authentication"
    if error_type == "permission_error":
        return "permission"
    if error_type == "rate_limit_error" or status_code == 429:
        return "rate_limit"
    if error_type == "overloaded_error" or status_code == 529:
        return "overloaded"
    if error_type == "not_found_error":
        return "model_unavailable"
    return "provider_error"


def _parse_key_bundle(text: str) -> list[tuple[str, object]]:
    """Parse ``KEYPROXY_PRIVATE_KEYS`` — the generate-script output format.

    Each private key PEM is associated with the closest preceding
    ``kid: <value>`` line (exactly what ``scripts/generate_keyproxy_keypair.py``
    prints; rotation appends another rendered block). Public-key PEMs and
    commentary lines are ignored. Fail closed: a private key without a kid,
    an unparseable PEM, or an empty bundle refuses startup.
    """
    entries: list[tuple[str, object]] = []
    current_kid: Optional[str] = None
    pem_lines: Optional[list[str]] = None
    for line in text.splitlines():
        stripped = line.strip()
        if pem_lines is not None:
            pem_lines.append(stripped)
            if stripped == "-----END PRIVATE KEY-----":
                if not current_kid:
                    raise RuntimeError(
                        "KEYPROXY_PRIVATE_KEYS: private key without a kid line"
                    )
                try:
                    key = crypto.load_private_key_pem("\n".join(pem_lines))
                except Exception:
                    raise RuntimeError(
                        "KEYPROXY_PRIVATE_KEYS: unparseable private key PEM"
                    ) from None
                entries.append((current_kid, key))
                pem_lines = None
        elif stripped.lower().startswith("kid:"):
            current_kid = stripped.split(":", 1)[1].strip()
        elif stripped == "-----BEGIN PRIVATE KEY-----":
            pem_lines = [stripped]
    if pem_lines is not None:
        raise RuntimeError("KEYPROXY_PRIVATE_KEYS: truncated private key PEM")
    if not entries:
        raise RuntimeError("KEYPROXY_PRIVATE_KEYS: no private keys found")
    return entries


class KeyProxyState:
    """Per-app singletons: key material + the packet 2a security stores."""

    def __init__(self) -> None:
        bundle = os.environ.get("KEYPROXY_PRIVATE_KEYS")
        if bundle:
            entries = _parse_key_bundle(bundle)
        else:
            # Local/compose convenience: an ephemeral dev keypair. Envelopes
            # minted against it only work for this process's lifetime, which
            # is exactly the property a dev stack wants.
            entries = [(DEV_KID, crypto.generate_private_key())]
        self.private_keys: dict[str, object] = {}
        self._key_order: list[str] = []
        for kid, key in entries:
            if kid in self.private_keys:
                self._key_order.remove(kid)
            self.private_keys[kid] = key
            self._key_order.append(kid)
        self.sessions = SessionStore()
        self.replay = JtiReplaySet()
        self.rate_limiter = SubRateLimiter()

    def public_keys_newest_first(self) -> list[dict[str, str]]:
        """Advertised keys — rotation appends to the bundle, so reverse it."""
        return [
            {
                "kid": kid,
                "alg": crypto.ENVELOPE_ALG,
                "spki": crypto.b64url_encode(
                    crypto.public_key_spki_der(self.private_keys[kid].public_key())
                ),
            }
            for kid in reversed(self._key_order)
        ]


class RedemptionRequest(BaseModel):
    provider: str
    envelope: dict
    scope: dict


class StreamTurnRequest(BaseModel):
    # Mirrors AnthropicChatClient.stream_turn's surface; betas/fallbacks are
    # fixed provider-side (keyproxy.providers.anthropic) — not caller inputs.
    session_id: str
    model: str
    effort: str
    max_tokens: int = 8192
    system: Any = None
    tools: list = []
    messages: list


class SessionResponse(BaseModel):
    session_id: str
    expires_at: int


class ValidateResponse(BaseModel):
    valid: bool
    provider: str
    key_hint: str


def _redeem(
    state: KeyProxyState,
    caller: Caller,
    body: RedemptionRequest,
    *,
    expected_action: Optional[str] = None,
):
    """Shared redemption path: rate limit → scope → decrypt → burn jti.

    Returns ``(provider_module, scope, api_key)``. Every rejection is a
    generic constant-detail HTTPException; nothing on any failure path logs.
    """
    if not state.rate_limiter.allow(caller.sub):
        raise HTTPException(status_code=429, detail=_RATE_LIMITED)

    provider = get_provider(body.provider)
    if provider is None:
        raise HTTPException(status_code=400, detail=_INVALID)

    try:
        scope = validate_scope(body.scope)
    except ScopeError as exc:
        # Scope error text names fields only — except the token-budget copy,
        # which the plan requires to reach the user verbatim.
        raise HTTPException(status_code=400, detail=str(exc)) from None

    if scope.provider != provider.PROVIDER:
        raise HTTPException(status_code=400, detail=_INVALID)
    if expected_action is not None and scope.action != expected_action:
        raise HTTPException(status_code=400, detail=_INVALID)
    if not provider.supports_scope(
        action=scope.action, max_mutations=scope.max_mutations
    ):
        raise HTTPException(status_code=400, detail=_INVALID)

    try:
        api_key = crypto.decrypt_envelope(
            body.envelope,
            state.private_keys,
            expected_sub=caller.sub,
            expected_provider=provider.PROVIDER,
            scope=dict(scope.raw),
            max_iat_skew=_max_iat_skew(),
        )
    except crypto.EnvelopeError:
        raise HTTPException(status_code=400, detail=_INVALID) from None

    # Burn the jti only after a successful decrypt (crypto.py contract):
    # exactly one concurrent redemption wins; a burned jti is a replay.
    try:
        fresh = state.replay.burn(body.envelope["aad"]["jti"])
    except ReplayCapacityError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from None
    if not fresh:
        raise HTTPException(status_code=400, detail=_INVALID)

    return provider, scope, api_key


def _create_session(
    state: KeyProxyState, *, sub: str, provider: str, api_key: str, scope: Scope
):
    try:
        return state.sessions.create(
            sub=sub, provider=provider, api_key=api_key, scope=scope
        )
    except SessionError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from None


def create_app() -> FastAPI:
    """Application factory — mirrors ``api/main.py``'s conventions at micro scale."""
    app = FastAPI(title="QuantCore Key Proxy", version="1.0.0")
    state = KeyProxyState()
    app.state.keyproxy = state

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default 422 echoes the offending input back — for this
        # service that could reflect an envelope. Generic 400, constant body.
        return JSONResponse(status_code=400, content={"detail": _INVALID})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/publickey")
    def publickey() -> dict:
        return {"keys": state.public_keys_newest_first()}

    @app.post("/v1/sessions", response_model=SessionResponse)
    def create_session(
        body: RedemptionRequest, caller: Caller = Depends(require_caller)
    ) -> SessionResponse:
        provider, scope, api_key = _redeem(state, caller, body)
        session = _create_session(
            state,
            sub=caller.sub,
            provider=provider.PROVIDER,
            api_key=api_key,
            scope=scope,
        )
        logger.info(
            "session redeemed correlation_id=%s sub=%s provider=%s ttl=%ss",
            session.correlation_id,
            caller.sub,
            provider.PROVIDER,
            int(session.ttl),
        )
        expires_at = int(
            time.time() + min(session.ttl, HARD_LIFETIME_CAP_SECONDS)
        )
        return SessionResponse(
            session_id=session.session_id, expires_at=expires_at
        )

    @app.delete("/v1/sessions/{session_id}", status_code=204)
    def delete_session(
        session_id: str, caller: Caller = Depends(require_caller)
    ) -> Response:
        # Best effort: 204 whether or not the session exists or is the
        # caller's — existence must not be probeable. Only the owning sub's
        # request actually tears the session down.
        try:
            session = state.sessions.get(session_id, sub=caller.sub)
        except SessionError:
            return Response(status_code=204)
        state.sessions.delete(session_id)
        logger.info(
            "session deleted correlation_id=%s sub=%s provider=%s",
            session.correlation_id,
            caller.sub,
            session.provider,
        )
        return Response(status_code=204)

    @app.post("/v1/providers/anthropic/messages/stream")
    def stream_messages(
        body: StreamTurnRequest, caller: Caller = Depends(require_caller)
    ) -> StreamingResponse:
        # Per-call gate, all BEFORE the key is attached: session live + sub
        # match, the operation classifies as a read, and the call budget has
        # room. Every rejection is the same generic 400 (except budget
        # exhaustion, whose messages name no values and — for the token
        # ceiling — must reach the user verbatim).
        provider = get_provider("anthropic")
        try:
            session = state.sessions.get(body.session_id, sub=caller.sub)
        except SessionError:
            raise HTTPException(status_code=400, detail=_INVALID) from None
        if session.provider != provider.PROVIDER:
            raise HTTPException(status_code=400, detail=_INVALID)
        if provider.classify("messages.stream") != provider.READ:
            raise HTTPException(status_code=400, detail=_INVALID)
        try:
            session.budget.charge_call()
        except BudgetExceededError as exc:
            state.sessions.delete(session.session_id)
            raise HTTPException(status_code=400, detail=str(exc)) from None
        try:
            api_key = session.api_key
        except SessionError:
            raise HTTPException(status_code=400, detail=_INVALID) from None

        heartbeat = _heartbeat_secs()
        started = time.monotonic()

        def worker(out: queue.Queue) -> None:
            try:
                for kind, payload in provider.stream_turn(
                    api_key,
                    model=body.model,
                    effort=body.effort,
                    max_tokens=body.max_tokens,
                    system=body.system,
                    tools=body.tools,
                    messages=body.messages,
                ):
                    out.put((kind, payload))
                    if kind == "final":
                        return
                # Stream ended without a final frame — opaque provider fault.
                out.put(("error", {"code": "provider_error"}))
            except Exception as exc:
                # Never let exception text (which may echo request material,
                # e.g. an API key embedded in a message) reach a log — only
                # duck-typed, content-free metadata: the exception's class
                # name, and — for anthropic.APIStatusError specifically,
                # detected by attribute shape so this module never needs to
                # import the SDK — the HTTP status code and the provider's
                # fixed error-type enum (never `.message`/`.body` text).
                status_code = getattr(exc, "status_code", None)
                error_type = None
                error_message = None
                error_body = getattr(exc, "body", None)
                if isinstance(error_body, dict):
                    err = error_body.get("error")
                    if isinstance(err, dict):
                        error_type = err.get("type")
                        error_message = err.get("message")
                # TEMPORARY (test-only, revert once the intermittent
                # invalid_request_error on large tool_result follow-up turns
                # is diagnosed): the provider's validation message is the
                # only place that names the actual complaint. The API key is
                # the one piece of request material known to this closure, so
                # it's redacted before anything is logged; this does not
                # widen the guarantee to arbitrary echoed request content.
                error_detail = error_message if error_message is not None else str(exc)
                error_detail = error_detail.replace(api_key, "[REDACTED]")
                error_detail = error_detail.encode("utf-8", "replace").decode("utf-8")
                if len(error_detail) > 300:
                    error_detail = error_detail[:300] + "...(truncated)"
                reason = _provider_reason(status_code, error_type, error_message)
                logger.warning(
                    "provider stream failed exception_type=%s status_code=%s error_type=%s reason=%s error_detail=%s",
                    type(exc).__name__,
                    status_code,
                    error_type,
                    reason,
                    error_detail,
                )
                out.put(("error", {"code": reason}))

        def event_stream():
            out: queue.Queue = queue.Queue()
            threading.Thread(target=worker, args=(out,), daemon=True).start()
            while True:
                try:
                    kind, payload = out.get(timeout=heartbeat)
                except queue.Empty:
                    # SSE comment — invisible to parsers, keeps idle-timeout
                    # appliances from dropping the wire during thinking pauses.
                    yield ": ping\n\n"
                    continue
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                elif kind == "final":
                    usage = payload.get("usage") if isinstance(payload, dict) else None
                    tokens = sum(
                        v
                        for v in (
                            (usage or {}).get("input_tokens"),
                            (usage or {}).get("output_tokens"),
                        )
                        if isinstance(v, int) and not isinstance(v, bool)
                    )
                    try:
                        session.budget.charge_tokens(tokens)
                    except BudgetExceededError:
                        # Cumulative ceiling crossed: this response already
                        # streamed, but the session is dead for future calls.
                        state.sessions.delete(session.session_id)
                    yield _sse("final", payload)
                    logger.info(
                        "turn streamed correlation_id=%s sub=%s provider=%s "
                        "tokens=%s latency_ms=%s",
                        session.correlation_id,
                        caller.sub,
                        provider.PROVIDER,
                        tokens,
                        int((time.monotonic() - started) * 1000),
                    )
                    return
                else:
                    # Only a closed-set reason code crosses the wire — never
                    # provider text. Defensively re-validate the code against
                    # the known set before it leaves the process.
                    code = payload.get("code") if isinstance(payload, dict) else None
                    if code not in PROVIDER_REASON_CODES:
                        code = "provider_error"
                    yield _sse("error", {"code": code})
                    return

        # No compression middleware may ever touch text/event-stream — a
        # compressor buffers until its window fills, which looks exactly like
        # broken streaming. This app deliberately adds none.
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/keys/validate", response_model=ValidateResponse)
    def validate_key(
        body: RedemptionRequest, caller: Caller = Depends(require_caller)
    ) -> ValidateResponse:
        provider, scope, api_key = _redeem(
            state, caller, body, expected_action="key.validate"
        )
        # Immediate-teardown session: the key rides the same session
        # machinery as every other redemption, then is discarded.
        session = _create_session(
            state,
            sub=caller.sub,
            provider=provider.PROVIDER,
            api_key=api_key,
            scope=scope,
        )
        try:
            valid = provider.validate_key(session.api_key)
            hint = provider.key_hint(session.api_key)
        finally:
            state.sessions.delete(session.session_id)
        logger.info(
            "key validated correlation_id=%s sub=%s provider=%s valid=%s",
            session.correlation_id,
            caller.sub,
            provider.PROVIDER,
            valid,
        )
        return ValidateResponse(
            valid=valid, provider=provider.PROVIDER, key_hint=hint
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=5002)
