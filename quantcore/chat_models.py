"""Sidekick chat model catalog — pure data, no I/O.

Single source of truth for the backend/`quantcore` side of the three
user-selectable Anthropic models (issue #124). The registry injects these
values into SettingsService and ChatService via constructor injection so the
services layer stays acyclic; chat.py and settings.py never import each
other or this module directly for validation logic.

The keyproxy (a separate deployable) and the frontend each keep their own
copy — see docs/proposals/sidekick-model-selector-plan.md.
"""

from __future__ import annotations

DEFAULT_CHAT_MODEL = "claude-sonnet-5"

CHAT_MODELS = [
    {
        "id": "claude-sonnet-5",
        "name": "Claude Sonnet 5",
        "description": "Recommended daily driver — balances speed and capability.",
    },
    {
        "id": "claude-opus-4-8",
        "name": "Claude Opus 4.8",
        "description": "Heavy-lifting flagship for complex tasks and deep reasoning.",
    },
    {
        "id": "claude-fable-5",
        "name": "Claude Fable 5",
        "description": "Most capable general model; best for long-horizon, multi-file agentic work.",
    },
]

MODEL_IDS = frozenset(m["id"] for m in CHAT_MODELS)
