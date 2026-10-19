from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_container
from app.container import Container

router = APIRouter(tags=["streams"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.get("/api/sessions/{sid}/stream")
async def session_stream(
    sid: str,
    after: str | None = Query(None),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    c: Container = Depends(get_container),
):
    cursor, control = await c.streams.prepare_session_stream(sid, after, last_event_id)
    return StreamingResponse(
        c.streams.session_stream(sid, cursor, control), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.get("/api/events")
async def global_events(
    after: str | None = Query(None),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    c: Container = Depends(get_container),
):
    cursor, control = await c.streams.prepare_global_stream(after, last_event_id)
    return StreamingResponse(
        c.streams.global_stream(cursor, control), media_type="text/event-stream", headers=SSE_HEADERS
    )
