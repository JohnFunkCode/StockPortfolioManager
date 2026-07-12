"""POST /api/chat — the chat-sidekick SSE stream.

The one endpoint that must NOT use QuantCoreJSONResponse: it serves
Server-Sent Events via StreamingResponse (see api/sse.py for the protocol).
Exactly one service call deep per the architectural standard.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..deps import services
from ..schemas.chat import ChatRequest
from ..sse import event_stream

router = APIRouter(prefix="/api/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.post("")
def chat(body: ChatRequest) -> StreamingResponse:
    events = services().chat.stream_chat(
        [{"role": m.role, "content": m.content} for m in body.messages]
    )
    return StreamingResponse(
        event_stream(events),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
