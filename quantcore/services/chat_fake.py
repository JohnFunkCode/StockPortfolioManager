"""FakeChatClient — deterministic scripted LLM for CHAT_FAKE=1.

Implements the same ChatClient protocol as AnthropicChatClient but plays a
canned two-turn INTC script through the REAL agent loop and REAL directive
validation, so route-integration tests and Playwright E2E run keyless and
offline at the LLM layer. The contract (TextDelta* -> Directive(signals/INTC)
-> TextDelta* -> Done) is pinned by test_chat_service.TestFakeChatClient.

If the latest user turn carries a [UI_INTERACTION] envelope (the interaction
backchannel), the fake acknowledges the selection instead — echoing the
payload values so tests can assert the envelope round-tripped.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

_MARKER = "[UI_INTERACTION] "


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def _final(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def _interaction_ack(messages):
    """If the last user turn has [UI_INTERACTION] lines, build the ack text."""
    content = messages[-1].get("content", "") if messages else ""
    if not isinstance(content, str) or _MARKER not in content:
        return None
    summaries = []
    for line in content.splitlines():
        if not line.startswith(_MARKER):
            continue
        try:
            body = json.loads(line[len(_MARKER):])
        except ValueError:
            continue
        payload = body.get("payload", {})
        detail = ", ".join(f"{k} {v}" for k, v in sorted(payload.items()))
        summaries.append(f"{body.get('action', 'interaction')} ({detail})")
    if not summaries:
        return None
    return ["Noted your selection — ", "; ".join(summaries), "."]


# Far-future expiration so the payoff card's DTE stays positive in tests forever.
SPREAD_PROPS = {
    "ticker": "WMT",
    "expiration": "2099-12-19",
    "long_strike": 120,
    "short_strike": 125,
    "kind": "call",
}


class FakeChatClient:
    """Scripted stand-in for the Anthropic client. One instance per conversation."""

    def __init__(self):
        self._turn = 0
        self._spread = False

    def stream_turn(self, *, system, tools, messages):  # noqa: ARG002 — protocol parity
        self._turn += 1
        ack = _interaction_ack(messages)
        if ack is not None:
            for chunk in ack:
                yield ("delta", chunk)
            yield ("final", _final("end_turn", _text("".join(ack))))
            return
        if self._turn == 1:
            prompt = messages[-1].get("content", "") if messages else ""
            self._spread = isinstance(prompt, str) and "spread" in prompt.lower()
        if self._spread:
            if self._turn == 1:
                for chunk in ("Pricing the WMT ", "120/125 call spread."):
                    yield ("delta", chunk)
                yield (
                    "final",
                    _final(
                        "tool_use",
                        _text("Pricing the WMT 120/125 call spread."),
                        _tool_use(
                            "fake_tool_spread",
                            "show_component",
                            {"component": "spread_payoff", "props": dict(SPREAD_PROPS)},
                        ),
                    ),
                )
            else:
                for chunk in ("The risk graph below is live — ", "click a strike to ask about it."):
                    yield ("delta", chunk)
                yield (
                    "final",
                    _final(
                        "end_turn",
                        _text(
                            "The risk graph below is live — click a strike to ask about it."
                        ),
                    ),
                )
        elif self._turn == 1:
            for chunk in ("Let me pull up ", "the INTC signals."):
                yield ("delta", chunk)
            yield (
                "final",
                _final(
                    "tool_use",
                    _text("Let me pull up the INTC signals."),
                    _tool_use(
                        "fake_tool_1",
                        "show_component",
                        {"component": "signals", "props": {"ticker": "INTC"}},
                    ),
                ),
            )
        else:
            for chunk in ("Here are the INTC signals — ", "the panel below is live."):
                yield ("delta", chunk)
            yield (
                "final",
                _final(
                    "end_turn",
                    _text("Here are the INTC signals — the panel below is live."),
                ),
            )
