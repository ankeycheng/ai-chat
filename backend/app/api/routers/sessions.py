"""Thin HTTP layer: validation + delegation to services (no business logic)."""

from fastapi import APIRouter, Body, Depends, Query, Response

from app.api.deps import get_container
from app.container import Container
from app.schemas.message import AcceptOut, MessageCreate, MessageListOut
from app.schemas.run import ActiveRunOut, RunOut
from app.schemas.session import SessionCreate, SessionListOut, SessionOut, SessionUpdate

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=SessionOut)
async def create_session(body: SessionCreate | None = Body(None), c: Container = Depends(get_container)):
    return await c.sessions.create(body.title if body else None)


@router.get("", response_model=SessionListOut)
async def list_sessions(
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    c: Container = Depends(get_container),
):
    return await c.sessions.list(q, limit, cursor)


@router.get("/{sid}", response_model=SessionOut)
async def get_session(sid: str, c: Container = Depends(get_container)):
    return await c.sessions.get(sid)


@router.patch("/{sid}", response_model=SessionOut)
async def rename_session(sid: str, body: SessionUpdate, c: Container = Depends(get_container)):
    return await c.sessions.rename(sid, body.title)


@router.delete("/{sid}", status_code=204)
async def delete_session(sid: str, c: Container = Depends(get_container)):
    await c.sessions.delete(sid)
    return Response(status_code=204)


@router.get("/{sid}/messages", response_model=MessageListOut)
async def list_messages(
    sid: str,
    after_sequence_no: int | None = Query(None, ge=0),
    before_sequence_no: int | None = Query(None, ge=1),
    limit: int | None = Query(None, ge=1, le=200),
    c: Container = Depends(get_container),
):
    return await c.sessions.messages(
        sid,
        after_sequence_no=after_sequence_no,
        before_sequence_no=before_sequence_no,
        limit=limit,
    )


@router.post("/{sid}/messages", status_code=202, response_model=AcceptOut)
async def send_message(sid: str, body: MessageCreate, c: Container = Depends(get_container)):
    return await c.chat.accept(sid, body)


@router.get("/{sid}/runs/{request_id}", response_model=RunOut)
async def get_run(sid: str, request_id: str, c: Container = Depends(get_container)):
    return await c.sessions.run(sid, request_id)


@router.get("/{sid}/active-run", response_model=ActiveRunOut)
async def active_run(sid: str, c: Container = Depends(get_container)):
    return await c.sessions.active_run(sid)
