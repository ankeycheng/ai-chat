"""Idempotent terminal-event publisher + state finalisation (spec §4.2 A7, §7.3, C9).

The terminal event payload is derived purely from the persisted run row, so the normal
path and the repair scanner produce identical events; the Lua/atomic marker guarantees
at most one terminal entry per run in the stream.

``done`` also carries the full persisted assistant ``message`` (MessageOut) so clients
can promote the temp bubble without refetching history (C3: message is committed first).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.core.metrics import EVENTS_PUBLISHED, RUN_DURATION, RUN_TERMINAL
from app.infra.broker.base import Broker, lock_key, session_stream_key, terminal_key
from app.models.entities import TERMINAL_RUN_STATUSES, MessageRow, RunRow
from app.repositories.messages import MessageRepository
from app.repositories.runs import RunRepository
from app.repositories.sessions import SessionRepository
from app.services.mappers import message_out
from app.services.sync_service import SyncService

log = logging.getLogger("app.terminal")


def terminal_event_for(run: RunRow, assistant: MessageRow | None = None) -> tuple[str, dict]:
    if run.status == "completed":
        data: dict = {
            "request_id": run.request_id,
            "message_id": run.assistant_message_id,
            "finish_reason": run.finish_reason or "stop",
        }
        if assistant is not None:
            data["message"] = message_out(assistant).model_dump(mode="json")
        return "done", data
    return "error", {
        "request_id": run.request_id,
        "code": run.error_code or "INTERNAL_ERROR",
        "message": run.error_message or "生成失败",
        "status": run.status,
    }


class TerminalPublisher:
    def __init__(
        self, sm: async_sessionmaker[AsyncSession], broker: Broker, sync: SyncService, settings: Settings
    ) -> None:
        self.sm = sm
        self.broker = broker
        self.sync = sync
        self.settings = settings

    async def publish_and_finalize(self, sid: str, rid: str) -> str | None:
        async with self.sm() as db:
            run = await RunRepository(db).get(sid, rid)
            assistant: MessageRow | None = None
            if run is not None and run.status == "completed" and run.assistant_message_id:
                assistant = await MessageRepository(db).get(run.assistant_message_id)
        if run is None or run.status not in TERMINAL_RUN_STATUSES:
            return None
        event, data = terminal_event_for(run, assistant)
        stream_id, created = await self.broker.publish_terminal(
            session_stream_key(sid),
            terminal_key(sid, rid),
            event,
            json.dumps(data, ensure_ascii=False),
            self.settings.stream_ttl_seconds,
        )
        if created:
            EVENTS_PUBLISHED.labels(event).inc()
        async with self.sm() as db:
            runs, sessions = RunRepository(db), SessionRepository(db)
            await runs.mark_published(sid, rid, stream_id)
            await sessions.finish_generation(sid, rid, stream_id)
            await db.commit()
            srow = await sessions.get(sid)
        if run.owner_token:
            await self.broker.release_lock(lock_key(sid), f"{rid}:{run.owner_token}")
        if created:
            RUN_TERMINAL.labels(run.status).inc()
            if run.finished_at and run.created_at:
                RUN_DURATION.observe((run.finished_at - run.created_at).total_seconds())
        log_event(
            log,
            "run.terminal_published",
            session_id=sid,
            request_id=rid,
            status=run.status,
            terminal_event_id=stream_id,
            newly_published=created,
        )
        if srow is not None:
            await self.sync.session_updated(srow)
        return stream_id
