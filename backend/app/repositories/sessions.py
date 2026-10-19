"""Session repository — all SQL for the sessions table lives here."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.models.entities import MessageRow, SessionRow


def escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, sid: str, title: str, title_source: str) -> SessionRow:
        now = utcnow()
        row = SessionRow(
            id=sid,
            title=title,
            title_source=title_source,
            created_at=now,
            updated_at=now,
            last_event_id="0-0",
            status="idle",
            active_request_id=None,
            next_sequence_no=1,
            version=1,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, sid: str) -> SessionRow | None:
        return await self.db.get(SessionRow, sid, populate_existing=True)

    async def list(self, limit: int, before: tuple[datetime, str] | None = None) -> list[SessionRow]:
        stmt = select(SessionRow)
        if before:
            ts, bid = before
            stmt = stmt.where(
                or_(SessionRow.updated_at < ts, and_(SessionRow.updated_at == ts, SessionRow.id < bid))
            )
        stmt = stmt.order_by(SessionRow.updated_at.desc(), SessionRow.id.desc()).limit(limit)
        return list((await self.db.execute(stmt)).scalars())

    async def search(self, q: str, limit: int) -> list[SessionRow]:
        pattern = f"%{escape_like(q)}%"
        msg_match = exists().where(
            MessageRow.session_id == SessionRow.id, MessageRow.content.ilike(pattern, escape="\\")
        )
        stmt = (
            select(SessionRow)
            .where(or_(SessionRow.title.ilike(pattern, escape="\\"), msg_match))
            .order_by(SessionRow.updated_at.desc(), SessionRow.id.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def first_matching_messages(self, session_ids: list[str], q: str) -> dict[str, MessageRow]:
        if not session_ids:
            return {}
        pattern = f"%{escape_like(q)}%"
        stmt = (
            select(MessageRow)
            .where(MessageRow.session_id.in_(session_ids), MessageRow.content.ilike(pattern, escape="\\"))
            .order_by(MessageRow.session_id, MessageRow.sequence_no.desc())
        )
        out: dict[str, MessageRow] = {}
        for m in (await self.db.execute(stmt)).scalars():
            out.setdefault(m.session_id, m)
        return out

    async def try_begin_generation(self, sid: str, request_id: str) -> int | None:
        """Conditionally flip idle→generating and reserve one sequence number.

        Returns the reserved user ``sequence_no`` or None when the session is not idle.
        The conditional UPDATE is the DB-level mutex backing the Redis lock (§5.2 锁要求).
        """
        res = await self.db.execute(
            update(SessionRow)
            .where(SessionRow.id == sid, SessionRow.status == "idle", SessionRow.active_request_id.is_(None))
            .values(
                status="generating",
                active_request_id=request_id,
                next_sequence_no=SessionRow.next_sequence_no + 1,
                version=SessionRow.version + 1,
                updated_at=utcnow(),
            )
            .returning(SessionRow.next_sequence_no)
        )
        row = res.first()
        return None if row is None else int(row[0]) - 1

    async def reserve_sequence(self, sid: str, request_id: str) -> int:
        res = await self.db.execute(
            update(SessionRow)
            .where(SessionRow.id == sid, SessionRow.active_request_id == request_id)
            .values(
                next_sequence_no=SessionRow.next_sequence_no + 1,
                updated_at=utcnow(),
                version=SessionRow.version + 1,
            )
            .returning(SessionRow.next_sequence_no)
        )
        row = res.first()
        if row is None:
            raise RuntimeError("session no longer owned by this run")
        return int(row[0]) - 1

    async def set_last_event_id(self, sid: str, event_id: str) -> None:
        await self.db.execute(update(SessionRow).where(SessionRow.id == sid).values(last_event_id=event_id))

    async def finish_generation(self, sid: str, request_id: str, last_event_id: str | None) -> bool:
        values: dict = dict(
            status="idle", active_request_id=None, version=SessionRow.version + 1, updated_at=utcnow()
        )
        if last_event_id:
            values["last_event_id"] = last_event_id
        res = await self.db.execute(
            update(SessionRow)
            .where(SessionRow.id == sid, SessionRow.active_request_id == request_id)
            .values(**values)
        )
        return res.rowcount == 1

    async def rename(self, sid: str, title: str, source: str, only_if_source: str | None = None) -> bool:
        stmt = update(SessionRow).where(SessionRow.id == sid)
        if only_if_source:
            stmt = stmt.where(SessionRow.title_source == only_if_source)
        res = await self.db.execute(
            stmt.values(title=title, title_source=source, version=SessionRow.version + 1, updated_at=utcnow())
        )
        return res.rowcount == 1

    async def delete_if_idle(self, sid: str) -> bool:
        res = await self.db.execute(
            delete(SessionRow).where(
                SessionRow.id == sid, SessionRow.status == "idle", SessionRow.active_request_id.is_(None)
            )
        )
        return res.rowcount == 1

    async def count(self) -> int:
        return int((await self.db.execute(select(func.count()).select_from(SessionRow))).scalar_one())
