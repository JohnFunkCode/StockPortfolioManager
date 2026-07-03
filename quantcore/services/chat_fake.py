"""FakeChatClient — deterministic scripted LLM for CHAT_FAKE=1.

Implements the same ChatClient protocol as AnthropicChatClient but plays a
canned two-turn INTC script through the REAL agent loop and REAL directive
validation, so route-integration tests and Playwright E2E run keyless and
offline at the LLM layer. The contract (TextDelta* -> Directive(signals/INTC)
-> TextDelta* -> Done) is pinned by test_chat_service.TestFakeChatClient.
"""
from __future__ import annotations

from types import SimpleNamespace


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def _final(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeChatClient:
    """Scripted stand-in for the Anthropic client. One instance per conversation."""

    def __init__(self):
        self._turn = 0

    def stream_turn(self, *, system, tools, messages):  # noqa: ARG002 — protocol parity
        self._turn += 1
        if self._turn == 1:
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
