"""ChatService — the /api/chat conversational agent loop.

Design notes:
  * The service depends on a minimal ChatClient protocol (``stream_turn``)
    rather than the Anthropic SDK directly, so unit tests drive the loop with
    scripted clients and CHAT_FAKE=1 swaps in FakeChatClient (chat_fake.py).
  * The real provider adapter (AnthropicChatClient) lives in
    quantcore/gateways/anthropic_gateway.py per architectural-standard-v2
    §5.3; it is loaded lazily via _default_client_factory so this module —
    and the registry that imports it — never touches the SDK at import time
    (MCP stdio servers and requirements-base images depend on that).
  * Model default is claude-fable-5: thinking is always on (the ``thinking``
    parameter must be omitted entirely), sampling params are not accepted, and
    depth is controlled via ``output_config.effort``. Server-side refusal
    fallbacks to claude-opus-4-8 are opted in by default per current API
    guidance.
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterator, Protocol

from quantcore.error_text import safe_error_text
from quantcore.services.chat_tools import (
    TOOL_SCHEMAS,
    validate_directive,
    validate_interaction,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the QuantCore sidekick — a market-analysis assistant embedded in the
QuantUI portfolio dashboard. You have data tools for prices, technical signals,
RSI, MACD, fundamental scores, news sentiment, and vertical option spread
pricing (price_vertical_spread — real contracts, real bid/ask).

You can also render live UI components inline in the conversation with the
show_component tool: 'signals' (full technical/options/risk signal panel),
'live_price' (compact auto-refreshing price chip), 'price_chart' (price
history chart with moving averages), and 'spread_payoff' (interactive risk
graph for a vertical spread — expiration payoff plus a value-today curve).
After pricing a spread with price_vertical_spread, always render it with
show_component('spread_payoff', {ticker, expiration, long_strike,
short_strike, kind}) using the exact same parameters. After discussing a
ticker, prefer showing the relevant component so the user sees live data —
the component fetches its own data; never restate numbers the component will
display.

Rendered components are interactive: when the user clicks inside one (a
strike on a spread_payoff chart, a point on a price_chart), their message
arrives with [UI_INTERACTION] lines — JSON naming the component instance, the
action, its payload, and the props of that instance. Treat these as precise
context from the user ("this strike" means the payload strike). Answer about
the selected element directly; never echo the raw JSON back.

Numbers you state in prose must come from tool results in this conversation,
never from memory. Be concise; this is a side rail, not a report."""


# ---------------------------------------------------------------------------
# Stream event vocabulary (maps 1:1 onto SSE frames — see api/sse.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextDelta:
    """A chunk of assistant prose."""
    delta: str


@dataclass(frozen=True)
class ToolStatus:
    """Lifecycle of one data-tool invocation ('running' | 'done' | 'error')."""
    tool: str
    args: dict = field(default_factory=dict)
    state: str = "running"


@dataclass(frozen=True)
class Directive:
    """A validated show_component call — render this registry component.

    ``component_id`` identifies the rendered instance so UI interactions can
    reference exactly which chart the user touched (the backchannel).
    """
    component: str
    props: dict = field(default_factory=dict)
    component_id: str = ""


@dataclass(frozen=True)
class ErrorEvent:
    """Terminal failure — the stream ends after this event."""
    message: str


@dataclass(frozen=True)
class Done:
    """Clean end of turn — always the final event on success."""
    stop_reason: str = "end_turn"


ChatEvent = TextDelta | ToolStatus | Directive | ErrorEvent | Done


# ---------------------------------------------------------------------------
# Per-request turn context (BYOK packet 3c)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnContext:
    """Per-request identity + key material handed from the route to the
    client factory. The envelope/scope are opaque dicts here — the keyproxy
    owns every decision about them; ChatService never inspects or logs them."""

    key_envelope: dict | None = None
    scope: dict | None = None
    auth_token: str | None = None
    subject: str = "local"


ENVELOPE_REQUIRED_MESSAGE = (
    "Add your Anthropic API key in Settings to use the sidekick."
)
CHAT_NOT_CONFIGURED_MESSAGE = (
    "The chat sidekick is not configured on this deployment."
)


class ChatKeyRequired(RuntimeError):
    """No usable key for this turn — surfaced as a clean ErrorEvent, no log."""


# ---------------------------------------------------------------------------
# Client protocol (the provider adapter itself lives in
# quantcore/gateways/anthropic_gateway.py per architectural-standard-v2 §5.3)
# ---------------------------------------------------------------------------

class ChatClient(Protocol):
    def stream_turn(
        self, *, system: str, tools: list[dict], messages: list[dict]
    ) -> Iterator[tuple[str, object]]:
        """Yield ("delta", str) chunks, then exactly one ("final", message)."""
        ...


def _default_client_factory(model: str, effort: str) -> ChatClient:
    # Late import + attribute lookup: keeps this module (and the registry that
    # imports it) free of the SDK for requirements-base images, and lets tests
    # patch quantcore.gateways.anthropic_gateway.AnthropicChatClient.
    from quantcore.gateways import anthropic_gateway

    return anthropic_gateway.AnthropicChatClient(model, effort)


def _sanitize(value):
    """Replace non-finite floats with None so json.dumps(allow_nan=False) holds."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _fold_interactions(convo: list[dict], interactions: list[dict]) -> None:
    """Append [UI_INTERACTION] envelope lines to the final user turn (adding
    one if the conversation doesn't end on a user turn). Interactions are
    current-turn context only — future turns rely on the assistant's reply,
    exactly like MCP Apps' update-model-context semantics."""
    lines = []
    for it in interactions:
        body = {
            k: it[k]
            for k in ("component", "component_id", "action", "payload", "props")
            if it.get(k) is not None
        }
        lines.append(
            "[UI_INTERACTION] " + json.dumps(_sanitize(body), sort_keys=True)
        )
    block = "\n".join(lines)
    if convo and convo[-1]["role"] == "user" and isinstance(convo[-1]["content"], str):
        convo[-1]["content"] = f"{convo[-1]['content']}\n\n{block}"
    else:
        convo.append({"role": "user", "content": block})


def _tool_result(tool_use_id: str, payload, is_error: bool = False) -> dict:
    content = payload if isinstance(payload, str) else json.dumps(
        _sanitize(payload), allow_nan=False
    )
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class ChatService:
    """Agent loop behind POST /api/chat. One instance lives in the registry."""

    def __init__(
        self,
        prices,
        fundamentals,
        sentiment,
        options,
        model: str = "claude-fable-5",
        effort: str = "medium",
        max_iterations: int = 8,
        client_factory: Callable[[TurnContext], ChatClient] | None = None,
    ):
        self._prices = prices
        self._fundamentals = fundamentals
        self._sentiment = sentiment
        self._options = options
        self._model = model
        self._effort = effort
        self._max_iterations = max_iterations
        self._client_factory = client_factory or (
            lambda context: _default_client_factory(self._model, self._effort)
        )
        # Tool name -> bound dispatch. Positional args mirror the service
        # signatures so tests can assert exact calls.
        self._handlers: dict[str, Callable] = {
            "get_stock_price": lambda symbol: self._prices.get_stock_price(symbol),
            "get_technical_signals": lambda ticker: self._prices.get_technical_signals(ticker),
            "get_rsi": lambda symbol, period=14, interval="1d": self._prices.get_rsi(
                symbol, period, interval
            ),
            "get_macd": lambda symbol, interval="1d": self._prices.get_macd(symbol, interval),
            "get_fundamental_score": lambda symbol: self._fundamentals.get_fundamental_score(
                symbol
            ),
            "get_news_sentiment": lambda symbol, days=7: self._sentiment.get_news_sentiment(
                symbol, days
            ),
            "price_vertical_spread": (
                lambda symbol, expiration, long_strike, short_strike, kind="call":
                self._options.price_vertical_spread(
                    symbol,
                    expiration=expiration,
                    long_strike=long_strike,
                    short_strike=short_strike,
                    kind=kind,
                )
            ),
        }

    def stream_chat(
        self,
        messages: list[dict],
        interactions: list[dict] | None = None,
        context: TurnContext | None = None,
    ) -> Iterator[ChatEvent]:
        convo = [{"role": m["role"], "content": m["content"]} for m in messages]
        if interactions:
            for it in interactions:
                ok, reason = validate_interaction(it)
                if not ok:
                    yield ErrorEvent(message=f"Invalid interaction: {reason}")
                    return
            _fold_interactions(convo, interactions)
        try:
            client = self._client_factory(context or TurnContext())
            for _ in range(self._max_iterations):
                final = None
                for kind, payload in client.stream_turn(
                    system=SYSTEM_PROMPT, tools=TOOL_SCHEMAS, messages=convo
                ):
                    if kind == "delta":
                        yield TextDelta(delta=payload)
                    elif kind == "final":
                        final = payload
                if final is None:
                    yield ErrorEvent(message="model returned no final message")
                    return
                if final.stop_reason == "refusal":
                    yield ErrorEvent(message="The model declined this request.")
                    return

                tool_uses = [
                    b for b in final.content if getattr(b, "type", None) == "tool_use"
                ]
                if not tool_uses:
                    yield Done(stop_reason=str(final.stop_reason or "end_turn"))
                    return

                # Echo assistant content back unchanged (thinking blocks included).
                convo.append({"role": "assistant", "content": final.content})
                results = []
                degraded: list[str] = []
                for tu in tool_uses:
                    args = dict(tu.input or {})
                    if tu.name == "show_component":
                        component = args.get("component", "")
                        props = args.get("props")
                        ok, reason = validate_directive(component, props)
                        if ok:
                            yield Directive(
                                component=component,
                                props=props,
                                component_id=uuid.uuid4().hex[:12],
                            )
                            results.append(_tool_result(tu.id, {"rendered": True}))
                        else:
                            results.append(_tool_result(tu.id, reason, is_error=True))
                            degraded.append(tu.name)
                        continue

                    yield ToolStatus(tool=tu.name, args=args, state="running")
                    handler = self._handlers.get(tu.name)
                    if handler is None:
                        yield ToolStatus(tool=tu.name, args=args, state="error")
                        results.append(
                            _tool_result(tu.id, f"Unknown tool: {tu.name}", is_error=True)
                        )
                        degraded.append(tu.name)
                        continue
                    try:
                        out = handler(**args)
                    except Exception as exc:  # noqa: BLE001 — model gets to recover
                        safe_exc = safe_error_text(exc)
                        logger.warning("chat tool %s failed: %s", tu.name, safe_exc)
                        yield ToolStatus(tool=tu.name, args=args, state="error")
                        results.append(
                            _tool_result(tu.id, f"Error: {safe_exc}", is_error=True)
                        )
                        degraded.append(tu.name)
                        continue
                    yield ToolStatus(tool=tu.name, args=args, state="done")
                    results.append(_tool_result(tu.id, out))
                    # Some handlers (e.g. get_technical_signals, get_options_flow_signals)
                    # fan out internally and degrade individual sub-results rather than
                    # raising — surface those partial failures here too.
                    if isinstance(out, dict) and out.get("_errors"):
                        degraded.append(tu.name)

                convo.append({"role": "user", "content": results})
                # Diagnostic only: tool names are a fixed, non-sensitive enum
                # (TOOL_SCHEMAS) and this is a byte count, not content — safe
                # under the never-log policy. Narrows whether a provider
                # invalid_request_error on the follow-up call correlates with
                # multi-tool turns or with oversized tool_result payloads.
                try:
                    results_bytes = len(json.dumps(results))
                except (TypeError, ValueError):
                    results_bytes = -1
                if degraded:
                    logger.warning(
                        "tool turn had degraded results tool_count=%d degraded=%s results_bytes=%d",
                        len(tool_uses), degraded, results_bytes,
                    )
                logger.info(
                    "tool turn built tool_count=%d tools=%s results_bytes=%d",
                    len(tool_uses),
                    [tu.name for tu in tool_uses],
                    results_bytes,
                )

            yield ErrorEvent(
                message=f"Tool iteration limit ({self._max_iterations}) reached."
            )
        except ChatKeyRequired as exc:
            # Expected keyless state, not a failure — clean event, no log noise
            # (and nothing from the context may ever reach a log anyway).
            yield ErrorEvent(message=str(exc))
        except Exception as exc:  # noqa: BLE001 — stream must end with a frame
            logger.exception("chat stream failed")
            yield ErrorEvent(message=str(exc))
