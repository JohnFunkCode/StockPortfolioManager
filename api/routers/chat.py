"""POST /api/chat — the chat-sidekick SSE stream.

The one endpoint that must NOT use QuantCoreJSONResponse: it serves
Server-Sent Events via StreamingResponse (see api/sse.py for the protocol).
Exactly one service call deep per the architectural standard.

BYOK (packet 3c): the route builds a TurnContext from the request's optional
sealed envelope/scope plus the caller's principal, and hands it to the
service opaquely. ``require_principal`` here is the same dependency the app
already applies router-wide, so FastAPI's per-request dependency cache means
the token is verified once, not twice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from quantcore.services.chat import TurnContext

from ..auth import Principal, require_principal
from ..deps import services
from ..schemas.chat import ChatRequest
from ..sse import event_stream

router = APIRouter(prefix="/api/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.post("")
def chat(
    body: ChatRequest, principal: Principal = Depends(require_principal)
) -> StreamingResponse:
    context = TurnContext(
        key_envelope=body.key_envelope.model_dump() if body.key_envelope else None,
        scope=body.scope.model_dump() if body.scope else None,
        auth_token=principal.token,
        subject=principal.subject,
    )
    events = services().chat.stream_chat(
        [{"role": m.role, "content": m.content} for m in body.messages],
        interactions=[i.model_dump(exclude_none=True) for i in body.interactions],
        context=context,
    )
    return StreamingResponse(
        event_stream(events),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
