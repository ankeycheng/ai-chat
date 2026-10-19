from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.models.entities import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES, RunRow


class RunRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, row: RunRow) -> RunRow:
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, sid: str, request_id: str) -> RunRow | None:
        return await self.db.get(RunRow, (sid, request_id), populate_existing=True)

    async def active_for_session(self, sid: str) -> RunRow | None:
        stmt = (
            select(RunRow)
            .where(
                RunRow.session_id == sid,
                or_(RunRow.status.in_(ACTIVE_RUN_STATUSES), RunRow.terminal_event_published.is_(False)),
            )
            .order_by(RunRow.created_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def last_terminal_event_id(self, sid: str, exclude_request_id: str) -> str | None:
        stmt = (
            select(RunRow.terminal_event_id)
            .where(
                RunRow.session_id == sid,
                RunRow.request_id != exclude_request_id,
                RunRow.terminal_event_id.is_not(None),
            )
            .order_by(RunRow.finished_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def mark_running(self, sid: str, rid: str, owner: str) -> bool:
        now = utcnow()
        res = await self.db.execute(
            update(RunRow)
            .where(
                RunRow.session_id == sid,
                RunRow.request_id == rid,
                RunRow.owner_token == owner,
                RunRow.status == "accepted",
            )
            .values(status="running", started_at=now, heartbeat_at=now, updated_at=now)
        )
        return res.rowcount == 1

    async def heartbeat(self, sid: str, rid: str, owner: str) -> bool:
        now = utcnow()
        res = await self.db.execute(
            update(RunRow)
            .where(
                RunRow.session_id == sid,
                RunRow.request_id == rid,
                RunRow.owner_token == owner,
                RunRow.status.in_(ACTIVE_RUN_STATUSES),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
        return res.rowcount == 1

    async def update_fields(self, sid: str, rid: str, owner: str | None = None, **values) -> bool:
        stmt = update(RunRow).where(RunRow.session_id == sid, RunRow.request_id == rid)
        if owner is not None:
            stmt = stmt.where(RunRow.owner_token == owner)
        res = await self.db.execute(stmt.values(updated_at=utcnow(), **values))
        return res.rowcount == 1

    async def finish(self, sid: str, rid: str, owner: str | None, status: str, **values) -> bool:
        """Conditional transition active→terminal (only one writer wins)."""
        now = utcnow()
        stmt = update(RunRow).where(
            RunRow.session_id == sid, RunRow.request_id == rid, RunRow.status.in_(ACTIVE_RUN_STATUSES)
        )
        if owner is not None:
            stmt = stmt.where(RunRow.owner_token == owner)
        res = await self.db.execute(stmt.values(status=status, finished_at=now, updated_at=now, **values))
        return res.rowcount == 1

    async def mark_interrupted_if_stale(self, run: RunRow, threshold: datetime) -> bool:
        now = utcnow()
        stmt = update(RunRow).where(
            RunRow.session_id == run.session_id,
            RunRow.request_id == run.request_id,
            RunRow.status.in_(ACTIVE_RUN_STATUSES),
        )
        if run.heartbeat_at is not None:
            stmt = stmt.where(RunRow.heartbeat_at < threshold)
        else:
            stmt = stmt.where(RunRow.heartbeat_at.is_(None), RunRow.created_at < threshold)
        res = await self.db.execute(
            stmt.values(
                status="interrupted",
                error_code="GENERATION_INTERRUPTED",
                error_message="生成任务中断（工作实例无心跳）",
                finished_at=now,
                updated_at=now,
            )
        )
        return res.rowcount == 1

    async def stale_active(self, threshold: datetime, limit: int = 50) -> list[RunRow]:
        stmt = (
            select(RunRow)
            .where(
                RunRow.status.in_(ACTIVE_RUN_STATUSES),
                or_(
                    RunRow.heartbeat_at < threshold,
                    (RunRow.heartbeat_at.is_(None)) & (RunRow.created_at < threshold),
                ),
            )
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def unpublished_terminal(self, finished_before: datetime, limit: int = 50) -> list[RunRow]:
        stmt = (
            select(RunRow)
            .where(
                RunRow.status.in_(TERMINAL_RUN_STATUSES),
                RunRow.terminal_event_published.is_(False),
                RunRow.finished_at < finished_before,
            )
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def mark_published(self, sid: str, rid: str, terminal_event_id: str) -> None:
        await self.db.execute(
            update(RunRow)
            .where(RunRow.session_id == sid, RunRow.request_id == rid)
            .values(terminal_event_published=True, terminal_event_id=terminal_event_id, updated_at=utcnow())
        )
