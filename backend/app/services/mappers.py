from __future__ import annotations

import json

from app.models.entities import MessageRow, RunRow, SessionRow
from app.schemas.message import MessageOut
from app.schemas.run import RunOut
from app.schemas.session import SessionOut


def session_out(row: SessionRow, match: dict | None = None) -> SessionOut:
    return SessionOut(
        id=row.id,
        title=row.title,
        title_source=row.title_source,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_event_id=row.last_event_id,
        status=row.status,
        active_request_id=row.active_request_id,
        version=row.version,
        match=match,
    )


def message_out(row: MessageRow) -> MessageOut:
    try:
        sources = json.loads(row.sources_json or "[]")
    except json.JSONDecodeError:
        sources = []
    return MessageOut(
        id=row.id,
        session_id=row.session_id,
        sequence_no=row.sequence_no,
        role=row.role,
        content=row.content,
        reasoning_content=row.reasoning_content,
        sources=sources,
        finish_reason=row.finish_reason,
        request_id=row.request_id,
        created_at=row.created_at,
    )


def run_out(row: RunRow) -> RunOut:
    return RunOut(**{f: getattr(row, f) for f in RunOut.model_fields})
