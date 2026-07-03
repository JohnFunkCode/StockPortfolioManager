"""Server-Sent Events encoding for the /api/chat stream.

The chat route is the one endpoint that must NOT use QuantCoreJSONResponse
(it sets application/json); it serves
``StreamingResponse(event_stream(...), media_type="text/event-stream")``.
Payload JSON mirrors api/json_response.py strictness: ``allow_nan=False`` so a
NaN can never produce invalid JSON mid-stream — tool results are sanitized
upstream in ChatService.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Iterator

from quantcore.services.chat import (
    ChatEvent,
    Directive,
    Done,
    ErrorEvent,
    TextDelta,
    ToolStatus,
)

logger = logging.getLogger(__name__)

_EVENT_NAMES: dict[type, str] = {
    TextDelta: "text",
    ToolStatus: "tool_status",
    Directive: "directive",
    ErrorEvent: "error",
    Done: "done",
}


def sse_encode(event_type: str, data: dict) -> str:
    """Encode one SSE frame: ``event: <type>\\ndata: <one-line-json>\\n\\n``."""
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def event_stream(events: Iterator[ChatEvent]) -> Iterator[str]:
    """Map ChatEvents onto SSE frames; convert generator failures to an error frame."""
    try:
        for event in events:
            yield sse_encode(_EVENT_NAMES[type(event)], asdict(event))
    except Exception as exc:  # noqa: BLE001 — stream must end with a frame, not a 500
        logger.exception("chat event stream failed")
        yield sse_encode("error", {"message": str(exc)})
