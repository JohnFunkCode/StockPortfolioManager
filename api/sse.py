"""Server-Sent Events encoding for the /api/chat stream.

The chat route is the one endpoint that must NOT use QuantCoreJSONResponse
(it sets application/json); it serves
``StreamingResponse(event_stream(...), media_type="text/event-stream")``.
Payload JSON mirrors api/json_response.py strictness: ``allow_nan=False`` so a
NaN can never produce invalid JSON mid-stream — tool results are sanitized
upstream in ChatService.

Heartbeats (BYOK packet 3c): the browser-facing hop mirrors the keyproxy's
worker/queue pattern — the event iterator runs on a thread, and whenever no
frame arrives within KEYPROXY_HEARTBEAT_SECS a ``: ping`` comment goes out so
proxies (IAP, Cloud Run) never see a silent connection during long provider
thinking pauses.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
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


def _heartbeat_secs() -> float:
    return float(os.environ.get("KEYPROXY_HEARTBEAT_SECS", "15"))


def sse_encode(event_type: str, data: dict) -> str:
    """Encode one SSE frame: ``event: <type>\\ndata: <one-line-json>\\n\\n``."""
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def event_stream(events: Iterator[ChatEvent]) -> Iterator[str]:
    """Map ChatEvents onto SSE frames with heartbeat comments during gaps;
    convert generator failures to an error frame, never a broken response."""
    out: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            for event in events:
                out.put(sse_encode(_EVENT_NAMES[type(event)], asdict(event)))
        except Exception as exc:  # noqa: BLE001 — stream must end with a frame
            logger.exception("chat event stream failed")
            out.put(sse_encode("error", {"message": str(exc)}))
        finally:
            out.put(None)

    threading.Thread(target=worker, daemon=True).start()
    heartbeat = _heartbeat_secs()
    while True:
        try:
            frame = out.get(timeout=heartbeat)
        except queue.Empty:
            yield ": ping\n\n"
            continue
        if frame is None:
            return
        yield frame
