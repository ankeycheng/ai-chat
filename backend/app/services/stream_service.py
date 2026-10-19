"""SSE streams: per-session stream (FR-E01) and global sync stream (FR-Y01).

Per-session stream: one continuous XREAD BLOCK loop does replay *and* live reading (A3/C1),
cursor advanced to the last delivered ID (C2), ping without id (FR-E04).
Global stream: same loop over ``events:global`` for conversation-list sync.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import InvalidRequest, SessionNotFound
from app.core.logging import log_event
from app.core.metrics import SSE_CONNECTIONS
from app.core.stream_ids import ZERO_ID, compare_ids, is_valid_stream_id, max_id
from app.core.time import utcnow
from app.infra.broker.base import GLOBAL_STREAM_KEY, Broker, session_stream_key
from app.repositories.runs import RunRepository
from app.repositories.sessions import SessionRepository

log = logging.getLogger("app.sse")


def sse(event: str, data: str, id: str | None = None) -> str:
    lines = []
    if id is not None:
        lines.append(f"id: {id}")
    lines.append(f"event: {event}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def resolve_cursor(after: str | None, last_event_id: str | None) -> str | None:
    """FR-E03: validate both; when both present take the numerically newer one."""
    for name, v in (("after", after), ("Last-Event-ID", last_event_id)):
        if v is not None and v != "" and not is_valid_stream_id(v):
            raise InvalidRequest(f"{name} 游标格式非法")
    return max_id(after or None, last_event_id or None)


class StreamService:
    def __init__(self, sm: async_sessionmaker[AsyncSession], broker: Broker, settings: Settings) -> None:
        self.sm = sm
        self.broker = broker
        self.settings = settings

    async def prepare_session_stream(
        self, sid: str, after: str | None, header_id: str | None
    ) -> tuple[str, list[str]]:
        """Return (start cursor, control events to emit first). Raises before the response starts."""
        cursor = resolve_cursor(after, header_id)
        async with self.sm() as db:
            s = await SessionRepository(db).get(sid)
            if s is None:
                raise SessionNotFound()
            run = await RunRepository(db).active_for_session(sid)
        key = session_stream_key(sid)
        control: list[str] = []
        if cursor is None:  # FR-E09
            cursor = (
                (run.start_event_id if run and run.start_event_id else None) or s.last_event_id or ZERO_ID
            )
        first = await self.broker.first_id(key)
        if run is not None and run.start_event_id and not await self.broker.exists(key):
            # FR-D05: run is active but its stream is gone → explicit error, never fake completeness
            log_event(
                log, "stream.missing_for_active_run", logging.ERROR, session_id=sid, request_id=run.request_id
            )
            control.append(
                sse(
                    "resync",
                    json.dumps(
                        {"scope": "session", "reason": "STREAM_MISSING", "request_id": run.request_id}
                    ),
                )
            )
        elif first is not None and cursor != ZERO_ID and compare_ids(cursor, first) < 0:
            # cursor older than the retained window (trimmed/expired): completed rounds come from DB history
            control.append(
                sse(
                    "resync", json.dumps({"scope": "session", "reason": "CURSOR_TOO_OLD", "oldest_id": first})
                )
            )
        return cursor, control

    async def _loop(self, key: str, cursor: str, channel: str, first_events: list[str]) -> AsyncIterator[str]:
        SSE_CONNECTIONS.labels(channel).inc()
        try:
            yield "retry: 3000\n\n"
            for c in first_events:
                yield c
            ping_every = self.settings.ping_interval_seconds
            block_ms = max(50, min(self.settings.sse_block_ms, int(ping_every * 1000)))
            last_sent = time.monotonic()
            while True:
                try:
                    entries = await self.broker.read(key, cursor, block_ms)
                except Exception:
                    # Keep the SSE socket open across transient broker blips; client reconnect is worse.
                    log.exception("sse.read_failed channel=%s key=%s", channel, key)
                    yield sse("ping", json.dumps({"at": utcnow().isoformat(), "degraded": True}))
                    last_sent = time.monotonic()
                    continue
                for sid, ev, data in entries:
                    yield sse(ev, data, sid)
                    cursor = sid  # C2: advance to the last delivered entry
                if entries:
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= ping_every:
                    yield sse("ping", json.dumps({"at": utcnow().isoformat()}))  # FR-E04: no id
                    last_sent = time.monotonic()
        finally:
            SSE_CONNECTIONS.labels(channel).dec()

    def session_stream(self, sid: str, cursor: str, control: list[str]) -> AsyncIterator[str]:
        return self._loop(session_stream_key(sid), cursor, "session", control)

    async def prepare_global_stream(self, after: str | None, header_id: str | None) -> tuple[str, list[str]]:
        cursor = resolve_cursor(after, header_id)
        control: list[str] = []
        if cursor is None:
            cursor = await self.broker.last_id(GLOBAL_STREAM_KEY) or ZERO_ID
        else:
            first = await self.broker.first_id(GLOBAL_STREAM_KEY)
            if first is not None and cursor != ZERO_ID and compare_ids(cursor, first) < 0:
                control.append(sse("resync", json.dumps({"scope": "global", "reason": "CURSOR_TOO_OLD"})))
        # `ready` tells the client the live cursor; it then (re)loads the list and applies buffered events
        control.insert(0, sse("ready", json.dumps({"cursor": cursor})))
        return cursor, control

    def global_stream(self, cursor: str, control: list[str]) -> AsyncIterator[str]:
        return self._loop(GLOBAL_STREAM_KEY, cursor, "global", control)
