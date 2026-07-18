"""Anthropic provider module for the Key Proxy (packet 2b).

Owns the operation taxonomy and the egress allowlist for Anthropic keys, per
the plan's "Provider modules classify every call — fail closed" section:

* **Egress is allowlisted by construction** — ``BASE_URL`` is hardcoded; no
  request field, scope param, header, or environment override may influence
  where a decrypted key is sent.
* **The taxonomy is closed** — v1 permits exactly ``messages.stream`` (the
  streaming model turn, packet 3a) and ``key.validate`` (a ``models.list``
  probe), both reads. Anything else is unclassifiable and the caller must
  reject. Ambient chat scopes carry ``max_mutations: 0`` and this module has
  no mutate operations at all.

Logging policy: nothing here logs; the API key appears only in the outbound
``x-api-key`` header and is never part of any exception or return value.
"""

from __future__ import annotations

from typing import Optional

import requests

PROVIDER = "anthropic"

# Hardcoded egress target — the allowlist-by-construction guarantee.
BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

VALIDATE_TIMEOUT_SECONDS = 10.0

# Fixed turn parameters, mirroring AnthropicChatClient.stream_turn — callers
# supply model/effort/max_tokens/system/tools/messages; nothing else.
STREAM_BETAS = ["server-side-fallback-2026-06-01"]
STREAM_FALLBACKS = [{"model": "claude-opus-4-8"}]

READ = "read"
MUTATE = "mutate"

# operation name -> classification. Closed: absence means unclassifiable.
_OPERATIONS: dict[str, str] = {
    "messages.stream": READ,
    "key.validate": READ,
}

# Scope actions this provider will open a session for. ``chat.turn`` is the
# ambient chat scope (fans out into messages.stream calls); ``key.validate``
# is the immediate-teardown validation probe.
_ACTIONS = frozenset({"chat.turn", "key.validate"})


def classify(operation: object) -> Optional[str]:
    """Classify an outgoing operation as ``read``/``mutate``, or ``None``.

    ``None`` means unclassifiable — the caller must fail closed.
    """
    if not isinstance(operation, str):
        return None
    return _OPERATIONS.get(operation)


def supports_action(action: object) -> bool:
    """Whether this provider opens sessions for the given scope ``action``."""
    return isinstance(action, str) and action in _ACTIONS


def supports_scope(*, action: object, max_mutations: int) -> bool:
    """Vet a validated scope: known action, and no mutation budget at all.

    Anthropic v1 has zero mutate operations, so any scope asking for a
    mutation budget is unclassifiable intent — reject (fail closed).
    """
    return supports_action(action) and max_mutations == 0


def key_hint(api_key: str) -> str:
    """The displayable hint for a key — last 4 chars only, never more."""
    return "…" + api_key[-4:]


def stream_turn(api_key, *, model, effort, max_tokens, system, tools, messages):
    """Stream one model turn — the keyproxy-side mirror of
    ``AnthropicChatClient.stream_turn`` (quantcore/gateways/anthropic_gateway.py).

    Yields ``("delta", text)`` for each text delta, then ``("final",
    message_dict)`` exactly once. ``base_url`` is passed explicitly so the
    SDK's ``ANTHROPIC_BASE_URL`` environment override can never redirect
    egress. The SDK import is lazy — dev/CI environments don't ship it; the
    keyproxy image pins it in keyproxy/requirements.txt.
    """
    import anthropic  # lazy — see docstring

    client = anthropic.Anthropic(api_key=api_key, base_url=BASE_URL)
    with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=tools,
        messages=messages,
        output_config={"effort": effort},
        betas=STREAM_BETAS,
        fallbacks=STREAM_FALLBACKS,
    ) as stream:
        for event in stream:
            if (
                event.type == "content_block_delta"
                and getattr(event.delta, "type", "") == "text_delta"
            ):
                yield ("delta", event.delta.text)
        yield ("final", stream.get_final_message().model_dump(mode="json"))


def validate_key(api_key: str, *, timeout: float = VALIDATE_TIMEOUT_SECONDS) -> bool:
    """Probe the key with the cheapest read: ``models.list(limit=1)``.

    Returns ``True`` only on HTTP 200. Auth failures, network errors, and
    provider outages all yield ``False`` (fail closed) — no exception ever
    propagates carrying the key or the provider response body.
    """
    try:
        response = requests.get(
            f"{BASE_URL}/v1/models",
            params={"limit": 1},
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            timeout=timeout,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False
