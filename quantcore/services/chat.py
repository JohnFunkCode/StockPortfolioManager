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
  * Model default is claude-sonnet-5. The turn request carries only what all
    three selectable models accept: ``thinking`` is omitted entirely,
    sampling params are not accepted, and depth comes from
    ``output_config.effort`` (CHAT_EFFORT, default "medium"). Provider
    parameters are NOT uniformly tolerated — an unsupported one 400s the
    whole turn instead of being ignored (``fallbacks`` did exactly that to
    claude-sonnet-5 and claude-opus-4-8), so anything new belongs in the
    gateway only after it is checked against every allow-listed model.
  * How much each model *thinks* varies (claude-sonnet-5 has been observed
    returning usage.thinking_tokens == 0), and thinking counts against the
    shared max_tokens budget — so the room left for visible output differs by
    model on an identical request. A turn that exhausts the budget comes back
    with stop_reason == "max_tokens" and is surfaced (Done.truncated) rather
    than ending the stream as if the model had chosen to stop.
"""
from __future__ import annotations

import dataclasses
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
single ticker, prefer showing the relevant component so the user sees live
data — the component fetches its own data; don't restate numbers that a
component you just rendered is already showing. Components are single-ticker
only: when the user asks about several symbols at once, give the numbers in
prose instead.

Rendered components are interactive: when the user clicks inside one (a
strike on a spread_payoff chart, a point on a price_chart), their message
arrives with [UI_INTERACTION] lines — JSON naming the component instance, the
action, its payload, and the props of that instance. Treat these as precise
context from the user ("this strike" means the payload strike). Answer about
the selected element directly; never echo the raw JSON back.

Numbers you state in prose must come from tool results in this conversation,
never from memory.

The tools above are your only source of market data. You have no web access:
never attempt a web search, never fetch a URL, and never pull prices, news,
filings, earnings figures, or any other market data from outside those tool
results — not from your training data, and not from a link the user pastes.
If the user asks you to search the web, look something up online, check
another site, or merge in outside data, politely decline and say plainly that
your system prompt restricts you to QuantCore's own data tools. Name the
closest tool you do have (get_news_sentiment for news, get_fundamental_score
for company financials, get_stock_price for quotes), then answer the rest of
their request normally — a declined sub-request is never a reason to abandon
the whole question. If the user supplies figures themselves, you may reason
about them, but say they are the user's numbers rather than QuantCore data.

Whenever you decline or narrow any part of a request because it conflicts
with these instructions, say so explicitly and name the conflict — "your
system prompt restricts me to QuantCore's own data tools," "my system prompt
tells me not to state numbers I haven't fetched." Do not disguise a
system-prompt restriction as missing data, a tool failure, or an inability on
your part; those send the user debugging the wrong thing. The user maintains
this system prompt, so they need to know which rule blocked them in order to
change it. Never silently drop part of a request you decided not to fulfill.
Attribute a refusal to this system prompt only when a rule above is genuinely
the cause — if you are holding back for some other reason, say that instead,
in your own words. Guessing wrong sends the user editing a rule that was
never involved.

Ranking, scoring, and comparing symbols on their signals is the core job here
and is always in scope: it is a summary of computed indicators, not personal
investment advice, and needs no disclaimer. Report what the signals say and
let the user draw conclusions. If someone asks what they personally should
buy, sell, or hold, or how to size a position against their own finances,
give the signal picture and note that the call is theirs.

Be concise: no preamble, no restating the question, no filler — this is a
side rail, not a report. Concision means cutting padding, never cutting
content the user asked for. When the user asks you to rank, prioritize,
compare, or screen a set of symbols, cover every symbol they named — a
complete ranked list IS the concise answer to that request. Never silently
shorten a list, drop symbols, or stop partway to stay brief. If you truly
cannot cover them all — a tool failed, or the list is too long to fetch —
rank the ones you have and say plainly which are missing and why.

Format a multi-symbol answer as one short line per symbol, in rank order,
with the driving signals after an em dash, like:
  1. NVDA — RSI 62, MACD bullish crossover, price above 50d
Your prose renders as plain text — no markdown is interpreted. Never use
markdown tables, pipes, or heading syntax (columns will not line up), and
never use **bold**, *italics*, or `backticks`: those show up as literal
asterisks and backtick characters on screen. Write tickers and labels bare.
Plain numbered or hyphenated lines are fine."""


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
    """End of turn — always the final event on success.

    ``truncated`` means the model ran out of output budget mid-answer
    (stop_reason "max_tokens") rather than finishing. The distinction matters
    to the user: a truncated ranked list looks identical to a deliberately
    short one, so the rail renders a visible note for it. Kept separate from
    ErrorEvent because the partial answer above it is still worth reading.
    """
    stop_reason: str = "end_turn"
    truncated: bool = False


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
    # Requested chat model for this turn (issue #124); resolved against the
    # allow-list and the caller's stored setting before use — see
    # ChatService._resolve_model.
    model: str | None = None


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
        model: str = "claude-sonnet-5",
        effort: str = "medium",
        max_iterations: int = 8,
        client_factory: Callable[[TurnContext], ChatClient] | None = None,
        settings=None,
        allowed: frozenset[str] = frozenset(),
    ):
        self._prices = prices
        self._fundamentals = fundamentals
        self._sentiment = sentiment
        self._options = options
        self._model = model
        self._effort = effort
        self._max_iterations = max_iterations
        self._settings = settings
        self._allowed = allowed
        self._client_factory = client_factory or (
            lambda context: _default_client_factory(context.model or self._model, self._effort)
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

    def _resolve_model(self, context: TurnContext) -> str:
        """Requested model wins if allow-listed; else fall back to the
        caller's stored setting (if allow-listed); else the service default."""
        if context.model and context.model in self._allowed:
            return context.model
        if self._settings is not None:
            stored = self._settings.get_chat_model(context.subject)
            if stored in self._allowed:
                return stored
        return self._model

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
            context = context or TurnContext()
            if self._allowed:
                context = dataclasses.replace(context, model=self._resolve_model(context))
            client = self._client_factory(context)
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

                # Budget exhausted mid-answer. Stop here even when the turn
                # carries tool_use blocks: a block cut off mid-stream can hold
                # incomplete argument JSON, and echoing that assistant turn
                # back to the provider is itself an invalid_request_error risk.
                # A partial answer the user can see beats a turn that dies
                # opaquely one iteration later.
                if final.stop_reason == "max_tokens":
                    logger.warning(
                        "chat turn truncated at max_tokens model=%s pending_tools=%d",
                        context.model or self._model,
                        len(tool_uses),
                    )
                    yield Done(stop_reason="max_tokens", truncated=True)
                    return
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
