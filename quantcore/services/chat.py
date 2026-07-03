"""ChatService — the /api/chat conversational agent loop.

Design notes:
  * The service depends on a minimal ChatClient protocol (``stream_turn``)
    rather than the Anthropic SDK directly, so unit tests drive the loop with
    scripted clients and CHAT_FAKE=1 swaps in FakeChatClient (chat_fake.py).
  * IMPORTANT: never import ``anthropic`` at module top — this module is
    reachable from quantcore.services.registry, which MCP stdio servers import
    at startup. The SDK is lazy-imported inside AnthropicChatClient only.
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
from dataclasses import dataclass, field
from typing import Callable, Iterator, Protocol

from quantcore.services.chat_tools import TOOL_SCHEMAS, validate_directive

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the QuantCore sidekick — a market-analysis assistant embedded in the
QuantUI portfolio dashboard. You have data tools for prices, technical signals,
RSI, MACD, fundamental scores, and news sentiment.

You can also render live UI components inline in the conversation with the
show_component tool: 'signals' (full technical/options/risk signal panel),
'live_price' (compact auto-refreshing price chip), and 'price_chart' (price
history chart with moving averages). After discussing a ticker, prefer showing
the relevant component so the user sees live data — the component fetches its
own data; never restate numbers the component will display.

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
    """A validated show_component call — render this registry component."""
    component: str
    props: dict = field(default_factory=dict)


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
# Client protocol + real Anthropic adapter
# ---------------------------------------------------------------------------

class ChatClient(Protocol):
    def stream_turn(
        self, *, system: str, tools: list[dict], messages: list[dict]
    ) -> Iterator[tuple[str, object]]:
        """Yield ("delta", str) chunks, then exactly one ("final", message)."""
        ...


class AnthropicChatClient:
    """Real client: streams one model turn via the Anthropic SDK."""

    def __init__(self, model: str, effort: str, max_tokens: int = 8192):
        import anthropic  # lazy — see module docstring

        self._client = anthropic.Anthropic()
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    def stream_turn(self, *, system, tools, messages):
        # claude-fable-5: omit `thinking` entirely (always on); no sampling
        # params. Refusal fallbacks are opt-in — include them by default.
        with self._client.beta.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": self._effort},
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": "claude-opus-4-8"}],
        ) as stream:
            for event in stream:
                if (
                    event.type == "content_block_delta"
                    and getattr(event.delta, "type", "") == "text_delta"
                ):
                    yield ("delta", event.delta.text)
            yield ("final", stream.get_final_message())


def _sanitize(value):
    """Replace non-finite floats with None so json.dumps(allow_nan=False) holds."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


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
        model: str = "claude-fable-5",
        effort: str = "medium",
        max_iterations: int = 8,
        client_factory: Callable[[], ChatClient] | None = None,
    ):
        self._prices = prices
        self._fundamentals = fundamentals
        self._sentiment = sentiment
        self._model = model
        self._effort = effort
        self._max_iterations = max_iterations
        self._client_factory = client_factory or (
            lambda: AnthropicChatClient(self._model, self._effort)
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
        }

    def stream_chat(self, messages: list[dict]) -> Iterator[ChatEvent]:
        convo = [{"role": m["role"], "content": m["content"]} for m in messages]
        try:
            client = self._client_factory()
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
                for tu in tool_uses:
                    args = dict(tu.input or {})
                    if tu.name == "show_component":
                        component = args.get("component", "")
                        props = args.get("props")
                        ok, reason = validate_directive(component, props)
                        if ok:
                            yield Directive(component=component, props=props)
                            results.append(_tool_result(tu.id, {"rendered": True}))
                        else:
                            results.append(_tool_result(tu.id, reason, is_error=True))
                        continue

                    yield ToolStatus(tool=tu.name, args=args, state="running")
                    handler = self._handlers.get(tu.name)
                    if handler is None:
                        yield ToolStatus(tool=tu.name, args=args, state="error")
                        results.append(
                            _tool_result(tu.id, f"Unknown tool: {tu.name}", is_error=True)
                        )
                        continue
                    try:
                        out = handler(**args)
                    except Exception as exc:  # noqa: BLE001 — model gets to recover
                        logger.warning("chat tool %s failed: %s", tu.name, exc)
                        yield ToolStatus(tool=tu.name, args=args, state="error")
                        results.append(
                            _tool_result(tu.id, f"Error: {exc}", is_error=True)
                        )
                        continue
                    yield ToolStatus(tool=tu.name, args=args, state="done")
                    results.append(_tool_result(tu.id, out))

                convo.append({"role": "user", "content": results})

            yield ErrorEvent(
                message=f"Tool iteration limit ({self._max_iterations}) reached."
            )
        except Exception as exc:  # noqa: BLE001 — stream must end with a frame
            logger.exception("chat stream failed")
            yield ErrorEvent(message=str(exc))
