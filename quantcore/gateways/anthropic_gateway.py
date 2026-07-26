"""Anthropic provider gateway — streams chat-sidekick model turns.

Architectural-standard-v2 §5.3: external-system adapters (API calls, streaming,
error surfaces) live here, never in services. ChatService depends on the
ChatClient protocol (quantcore/services/chat.py) and receives this class via
its default client factory; tests and CHAT_FAKE inject substitutes.

IMPORTANT — lazy SDK import, deliberate deviation from eager registry
construction: requirements-base images (the 5 MCP wrappers, the report job,
and CI) do not ship the ``anthropic`` package — only the API image installs
requirements-ml.txt. This module must therefore import cleanly without the
SDK, and the SDK loads only when a client is actually constructed (i.e. on
the first real /api/chat turn). Guarded by test_anthropic_gateway.py.
"""
from __future__ import annotations


class AnthropicChatClient:
    """Real client: streams one model turn via the Anthropic SDK."""

    def __init__(self, model: str, effort: str, max_tokens: int = 16384):
        import anthropic  # lazy — see module docstring

        self._client = anthropic.Anthropic()
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    def stream_turn(self, *, system, tools, messages):
        # Deliberately minimal request shape — and it must stay that way: only
        # parameters that EVERY selectable model accepts. `thinking` is omitted
        # (on by default), sampling params are not accepted, and depth comes
        # from output_config.effort.
        #
        # No betas/fallbacks. The server-side-fallback beta was sent here and
        # removed: only claude-fable-5 accepts `fallbacks`, and a model that
        # doesn't rejects the ENTIRE turn with a 400 ("'<model>' does not
        # support the `fallbacks` parameter") rather than ignoring it. Any new
        # parameter added here needs checking against all three models first.
        # Mirrored in keyproxy/providers/anthropic.py.
        with self._client.beta.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": self._effort},
        ) as stream:
            for event in stream:
                if (
                    event.type == "content_block_delta"
                    and getattr(event.delta, "type", "") == "text_delta"
                ):
                    yield ("delta", event.delta.text)
            yield ("final", stream.get_final_message())
