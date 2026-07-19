"""Key Proxy gateway — the api-side client for the BYOK keyproxy (packet 3b).

Architectural-standard-v2 §5.3 / plan Rule 3: API calls, auth forwarding,
streaming, and error translation ONLY — no validation rules, no decisions
(anti-pattern 8 guard: this module must never grow logic).

``KeyProxyChatClient`` implements the ``ChatClient`` protocol
(quantcore/services/chat.py) against the keyproxy's streaming endpoint,
carrying the session exchange from the plan's Session mechanics:

* the envelope is redeemed **exactly once per client instance** —
  ``POST /v1/sessions`` lazily before the first turn;
* every turn presents the resulting ``session_id`` (plus the caller's JWT);
* teardown (``DELETE /v1/sessions/{id}``) is best-effort — fired when a turn
  is terminal for the ChatService loop (no tool_use blocks, or a refusal) and
  on every error path; the keyproxy TTL is the backstop.

Streaming hardening (decision #11): all requests ride one **persistent pooled
``httpx.Client``** (module-level, keep-alive), so turns 2+ — and later sends —
reuse the warm connection to the single keyproxy instance instead of paying
TCP setup per turn.

Content blocks from the provider's final message are wrapped in an attribute
view (``ContentBlock``) so ChatService's tool loop can keep its ``getattr``
access pattern, while the underlying dict is kept byte-exact — thinking-block
``signature`` fields must survive the echo back into the conversation and the
serialization onto the next turn's request unchanged.

Error policy: nothing here logs, and no exception raised from this module
carries envelope, key, token, or request material — only the keyproxy's own
constant-detail strings (which name no values; the token-budget copy must
reach the user verbatim) or this module's canned user-facing messages.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Iterator, Optional

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:5002"

# Read timeout must comfortably exceed the keyproxy's heartbeat interval
# (~15 s): during provider thinking pauses the only bytes on the wire are the
# `: ping` comments, and each one resets this clock.
DEFAULT_TIMEOUT = httpx.Timeout(10.0, read=60.0)

# The keyproxy's constant rejection detail (keyproxy/main.py `_INVALID`).
_KEYPROXY_GENERIC = "invalid request"

# User-facing translations (ChatService surfaces str(exc) as the ErrorEvent).
RESEND_MESSAGE = (
    "Your chat session expired or was rejected — please re-send your message."
)
PROVIDER_ERROR_MESSAGE = "The model provider returned an error — please try again."
UNAVAILABLE_MESSAGE = (
    "The key service is temporarily unavailable — please try again shortly."
)
RATE_LIMITED_MESSAGE = (
    "Rate limit exceeded — please wait a moment and re-send your message."
)


class KeyProxyError(RuntimeError):
    """A keyproxy interaction failed; the message is safe to show the user."""


def _base_url() -> str:
    return os.environ.get("KEYPROXY_URL", DEFAULT_BASE_URL).rstrip("/")


def is_configured() -> bool:
    """Whether a keyproxy has been wired in (registry precedence, packet 3c)."""
    return bool(os.environ.get("KEYPROXY_URL"))


_client_lock = threading.Lock()
_client: Optional[httpx.Client] = None


def _shared_client() -> httpx.Client:
    """The persistent pooled client (decision #11). No base_url is bound so
    requests always target the current ``KEYPROXY_URL``; httpx pools
    keep-alive connections per host underneath."""
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(timeout=DEFAULT_TIMEOUT)
        return _client


# --- Google IAM layer (packet 8b) -----------------------------------------
#
# On Cloud Run the keyproxy is deployed --no-allow-unauthenticated, so every
# hop must ALSO carry a Google-signed ID token (defense-in-depth layer 1).
# It rides in X-Serverless-Authorization — Cloud Run's front end verifies and
# strips that header, which leaves Authorization free for the user's ES256
# JWT that the keyproxy app itself verifies (layer 2). Activation is explicit
# via KEYPROXY_ID_TOKEN_AUDIENCE (set to the keyproxy service URL on the
# quantcore-api service); local/compose stacks leave it unset and skip the
# IAM layer entirely. Google ID tokens live 1 h — cache and refresh early so
# the metadata-server round-trip is off the per-request path.

_ID_TOKEN_AUDIENCE_ENV = "KEYPROXY_ID_TOKEN_AUDIENCE"
_ID_TOKEN_TTL_SECONDS = 50 * 60

_id_token_lock = threading.Lock()
_id_token_cache: Optional[tuple[str, float]] = None  # (token, monotonic fetch time)


def _google_id_token() -> Optional[str]:
    audience = os.environ.get(_ID_TOKEN_AUDIENCE_ENV)
    if not audience:
        return None
    global _id_token_cache
    with _id_token_lock:
        now = time.monotonic()
        if _id_token_cache is not None and now - _id_token_cache[1] < _ID_TOKEN_TTL_SECONDS:
            return _id_token_cache[0]
        try:
            from google.auth.transport import requests as google_auth_requests
            from google.oauth2 import id_token as google_id_token

            token = google_id_token.fetch_id_token(
                google_auth_requests.Request(), audience
            )
        except Exception:
            # Never-log policy: google-auth exceptions can embed request and
            # response material, so the cause is dropped — the constant
            # user-facing message is all that propagates.
            raise KeyProxyError(UNAVAILABLE_MESSAGE) from None
        _id_token_cache = (token, now)
        return token


def _headers(auth_token: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    id_token = _google_id_token()
    if id_token:
        headers["X-Serverless-Authorization"] = f"Bearer {id_token}"
    # An empty token (AUTH_DISABLED dev stacks) must omit the header entirely:
    # "Bearer " with no token is an illegal header value that h11 rejects
    # client-side before the request is ever sent.
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def _error_message(status_code: int, detail: object) -> str:
    """Translate a keyproxy rejection into user-facing copy.

    Non-generic 400 details are passed through verbatim: by the keyproxy's
    logging/response policy they name no values, and the token-budget copy is
    contractually required to reach the user unchanged.
    """
    if status_code == 400:
        if isinstance(detail, str) and detail and detail != _KEYPROXY_GENERIC:
            return detail
        return RESEND_MESSAGE
    if status_code == 429:
        return RATE_LIMITED_MESSAGE
    return UNAVAILABLE_MESSAGE


def _response_detail(response: httpx.Response) -> object:
    try:
        return response.json().get("detail")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Message wrappers — attribute access for the tool loop, byte-exact raw dicts
# ---------------------------------------------------------------------------

class ContentBlock:
    """Attribute view over one provider content-block dict.

    ``raw`` is the exact dict off the wire; it is what gets serialized back
    onto the next turn's request, so every field — including thinking-block
    ``signature`` values — round-trips unchanged.
    """

    __slots__ = ("raw",)

    def __init__(self, raw: dict):
        self.raw = raw

    def __getattr__(self, name: str):
        try:
            return self.raw[name]
        except KeyError:
            raise AttributeError(name) from None


class KeyProxyMessage:
    """The final message of one turn, shaped for ChatService's loop."""

    __slots__ = ("raw", "stop_reason", "content")

    def __init__(self, raw: dict):
        self.raw = raw
        self.stop_reason = raw.get("stop_reason")
        self.content = [
            ContentBlock(block) if isinstance(block, dict) else block
            for block in (raw.get("content") or [])
        ]


def _wire_messages(messages: list[dict]) -> list[dict]:
    """Unwrap ContentBlock views back to their raw dicts for the request body."""
    out = []
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            content = [
                block.raw if isinstance(block, ContentBlock) else block
                for block in content
            ]
        out.append({"role": message["role"], "content": content})
    return out


def _is_terminal(message: KeyProxyMessage) -> bool:
    """Whether ChatService's loop ends after this turn (Done or refusal)."""
    if message.stop_reason == "refusal":
        return True
    return not any(
        getattr(block, "type", None) == "tool_use" for block in message.content
    )


def _iter_sse(lines) -> Iterator[tuple[str, dict]]:
    """Parse SSE lines into (event, data) pairs; comments (`: ping`) skipped."""
    event: Optional[str] = None
    data_lines: list[str] = []
    for line in lines:
        if line == "":
            if event is not None:
                yield event, json.loads("\n".join(data_lines))
            event, data_lines = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())


# ---------------------------------------------------------------------------
# The ChatClient
# ---------------------------------------------------------------------------

class KeyProxyChatClient:
    """ChatClient over the keyproxy: one instance covers one chat send."""

    def __init__(
        self,
        *,
        envelope: dict,
        scope: dict,
        auth_token: str,
        model: str,
        effort: str,
        max_tokens: int = 8192,
    ):
        self._envelope = envelope
        self._scope = scope
        self._auth_token = auth_token
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._session_id: Optional[str] = None

    # -- session exchange ---------------------------------------------------

    def _ensure_session(self) -> str:
        if self._session_id is not None:
            return self._session_id
        try:
            response = _shared_client().post(
                f"{_base_url()}/v1/sessions",
                json={
                    "provider": self._scope.get("provider"),
                    "envelope": self._envelope,
                    "scope": self._scope,
                },
                headers=_headers(self._auth_token),
            )
        except httpx.HTTPError:
            raise KeyProxyError(UNAVAILABLE_MESSAGE) from None
        if response.status_code != 200:
            raise KeyProxyError(
                _error_message(response.status_code, _response_detail(response))
            )
        self._session_id = response.json()["session_id"]
        return self._session_id

    def close(self) -> None:
        """Best-effort session teardown; the keyproxy TTL is the backstop."""
        session_id, self._session_id = self._session_id, None
        if session_id is None:
            return
        try:
            _shared_client().delete(
                f"{_base_url()}/v1/sessions/{session_id}",
                headers=_headers(self._auth_token),
            )
        except (httpx.HTTPError, KeyProxyError):
            # Best-effort includes an ID-token fetch failure — TTL backstop.
            pass

    # -- the protocol method ------------------------------------------------

    def stream_turn(self, *, system, tools, messages):
        session_id = self._ensure_session()
        body = {
            "session_id": session_id,
            "model": self._model,
            "effort": self._effort,
            "max_tokens": self._max_tokens,
            "system": system,
            "tools": tools,
            "messages": _wire_messages(messages),
        }
        try:
            with _shared_client().stream(
                "POST",
                f"{_base_url()}/v1/providers/anthropic/messages/stream",
                json=body,
                headers=_headers(self._auth_token),
            ) as response:
                if response.status_code != 200:
                    response.read()
                    # The session is unusable (expired / budget-exhausted /
                    # killed) — a clean re-send error, never a hang.
                    self._session_id = None
                    raise KeyProxyError(
                        _error_message(
                            response.status_code, _response_detail(response)
                        )
                    )
                for event, data in _iter_sse(response.iter_lines()):
                    if event == "delta":
                        yield ("delta", data.get("text", ""))
                    elif event == "final":
                        message = KeyProxyMessage(data)
                        if _is_terminal(message):
                            self.close()
                        yield ("final", message)
                        return
                    elif event == "error":
                        self.close()
                        raise KeyProxyError(PROVIDER_ERROR_MESSAGE)
        except httpx.HTTPError:
            self.close()
            raise KeyProxyError(UNAVAILABLE_MESSAGE) from None
        # Stream ended without a final frame.
        self.close()
        raise KeyProxyError(PROVIDER_ERROR_MESSAGE)


# ---------------------------------------------------------------------------
# Pubkey / validate calls (consumed by quantcore/services/keyproxy.py)
# ---------------------------------------------------------------------------

def get_public_keys() -> list[dict]:
    """``GET /v1/publickey`` — the envelope encryption keys, newest first."""
    try:
        # No user token — the endpoint is public at the app layer — but the
        # Cloud Run IAM layer still requires the Google ID token.
        response = _shared_client().get(
            f"{_base_url()}/v1/publickey", headers=_headers("")
        )
    except httpx.HTTPError:
        raise KeyProxyError(UNAVAILABLE_MESSAGE) from None
    if response.status_code != 200:
        raise KeyProxyError(UNAVAILABLE_MESSAGE)
    return response.json()["keys"]


def validate_key(*, envelope: dict, scope: dict, auth_token: str) -> dict:
    """``POST /v1/keys/validate`` — redeem + probe, immediate teardown."""
    try:
        response = _shared_client().post(
            f"{_base_url()}/v1/keys/validate",
            json={
                "provider": scope.get("provider"),
                "envelope": envelope,
                "scope": scope,
            },
            headers=_headers(auth_token),
        )
    except httpx.HTTPError:
        raise KeyProxyError(UNAVAILABLE_MESSAGE) from None
    if response.status_code != 200:
        raise KeyProxyError(
            _error_message(response.status_code, _response_detail(response))
        )
    return response.json()
