from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import InvalidRequest, SessionBusy, SessionNotFound
from app.infra.broker.base import Broker, lock_key, session_stream_key
from app.models.entities import MessageRow
from app.repositories.messages import MessageRepository
from app.repositories.runs import RunRepository
from app.repositories.sessions import SessionRepository
from app.schemas.message import MessageListOut
from app.schemas.run import ActiveRunOut, RunOut
from app.schemas.session import SessionListOut, SessionOut
from app.services.mappers import message_out, run_out, session_out
from app.services.sync_service import SyncService


def make_snippet(text: str, q: str, width: int = 60) -> str:
    low, ql = text.lower(), q.lower()
    i = low.find(ql)
    if i < 0:
        return text[: width * 2]
    start = max(0, i - width // 2)
    end = min(len(text), i + len(q) + width)
    s = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + s + ("…" if end < len(text) else "")


class SessionService:
    def __init__(
        self, sm: async_sessionmaker[AsyncSession], broker: Broker, sync: SyncService, settings: Settings
    ) -> None:
        self.sm = sm
        self.broker = broker
        self.sync = sync
        self.settings = settings

    async def create(self, title: str | None) -> SessionOut:
        title = " ".join((title or "").split())
        async with self.sm() as db:
            row = await SessionRepository(db).create(
                str(uuid.uuid4()),
                title or self.settings.default_session_title,
                "user" if title else "default",
            )
            await db.commit()
        await self.sync.session_created(row)
        return session_out(row)

    async def list(self, q: str | None, limit: int, cursor: str | None) -> SessionListOut:
        async with self.sm() as db:
            repo = SessionRepository(db)
            if q:
                q = q.strip()
                if not 1 <= len(q) <= 100:
                    raise InvalidRequest("q 长度须为 1..100")
                rows = await repo.search(q, limit)
                ql = q.lower()
                need_msg = [r.id for r in rows if ql not in r.title.lower()]
                hits = await repo.first_matching_messages(need_msg, q)
                items = []
                for r in rows:
                    if ql in r.title.lower():
                        match = {"field": "title", "snippet": r.title}
                    else:
                        m = hits.get(r.id)
                        match = {
                            "field": "message",
                            "snippet": make_snippet(m.content, q) if m else "",
                            "message_id": m.id if m else None,
                            "role": m.role if m else None,
                        }
                    items.append(session_out(r, match))
                return SessionListOut(items=items, next_cursor=None)
            before = None
            if cursor:
                try:
                    ts, cid = cursor.split("|", 1)
                    before = (datetime.fromisoformat(ts), cid)
                except ValueError as e:
                    raise InvalidRequest("cursor 非法") from e
            rows = await repo.list(limit + 1, before)
            nxt = None
            if len(rows) > limit:
                rows = rows[:limit]
                nxt = f"{rows[-1].updated_at.isoformat()}|{rows[-1].id}"
            return SessionListOut(items=[session_out(r) for r in rows], next_cursor=nxt)

    async def get(self, sid: str) -> SessionOut:
        async with self.sm() as db:
            row = await SessionRepository(db).get(sid)
            if row is None:
                raise SessionNotFound()
            return session_out(row)

    async def rename(self, sid: str, title: str) -> SessionOut:
        async with self.sm() as db:
            repo = SessionRepository(db)
            if not await repo.rename(sid, title, "user"):
                raise SessionNotFound()
            await db.commit()
            row = await repo.get(sid)
        await self.sync.session_updated(row)
        return session_out(row)

    async def delete(self, sid: str) -> None:
        async with self.sm() as db:
            repo = SessionRepository(db)
            row = await repo.get(sid)
            if row is None:
                raise SessionNotFound()
            # C12: no active run and no pending terminal publish
            if row.status != "idle" or await RunRepository(db).active_for_session(sid) is not None:
                raise SessionBusy()
            if await self.broker.get(lock_key(sid)):
                raise SessionBusy()
            version = row.version + 1
            if not await repo.delete_if_idle(sid):
                raise SessionBusy()
            await db.commit()
        await self.broker.delete(session_stream_key(sid))
        await self.sync.session_deleted(sid, version)

    async def messages(
        self,
        sid: str,
        *,
        after_sequence_no: int | None = None,
        before_sequence_no: int | None = None,
        limit: int | None = None,
    ) -> MessageListOut:
        async with self.sm() as db:
            if await SessionRepository(db).get(sid) is None:
                raise SessionNotFound()
            repo = MessageRepository(db)
            rows: list[MessageRow] = await repo.list_for_session(
                sid,
                after_sequence_no=after_sequence_no,
                before_sequence_no=before_sequence_no,
                limit=limit,
            )
            items = [message_out(r) for r in rows]
            oldest = items[0].sequence_no if items else None
            newest = items[-1].sequence_no if items else None
            has_more = False
            if oldest is not None:
                has_more = await repo.has_before(sid, oldest)
            return MessageListOut(
                items=items,
                oldest_sequence_no=oldest,
                newest_sequence_no=newest,
                has_more_before=has_more,
            )

    async def run(self, sid: str, rid: str) -> RunOut:
        from app.core.errors import RunNotFound

        async with self.sm() as db:
            row = await RunRepository(db).get(sid, rid)
            if row is None:
                raise RunNotFound()
            return run_out(row)

    async def active_run(self, sid: str) -> ActiveRunOut:
        async with self.sm() as db:
            s = await SessionRepository(db).get(sid)
            if s is None:
                raise SessionNotFound()
            run = await RunRepository(db).active_for_session(sid)
            if run is None:
                return ActiveRunOut(active=False, last_event_id=s.last_event_id)
            return ActiveRunOut(
                active=True,
                request_id=run.request_id,
                status=run.status,
                start_event_id=run.start_event_id,
                thinking=run.thinking,
                web_search=run.web_search,
                user_message_id=run.user_message_id,
                last_event_id=s.last_event_id,
            )
