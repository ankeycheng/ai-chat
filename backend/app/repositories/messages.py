from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import MessageRow, RunRow


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, row: MessageRow) -> MessageRow:
        self.db.add(row)
        await self.db.flush()
        return row

    async def list_for_session(
        self,
        sid: str,
        *,
        after_sequence_no: int | None = None,
        before_sequence_no: int | None = None,
        limit: int | None = None,
    ) -> list[MessageRow]:
        """Return messages for a session, optionally windowed / filtered by sequence_no.

        - after_sequence_no: sequence_no > N (asc; optional limit caps the head of that range)
        - before_sequence_no: sequence_no < N; with limit, the nearest (highest) messages below N
        - limit alone (no after/before): latest N messages
        - no filters: all messages ascending
        """
        cond = [MessageRow.session_id == sid]
        if after_sequence_no is not None:
            cond.append(MessageRow.sequence_no > after_sequence_no)
        if before_sequence_no is not None:
            cond.append(MessageRow.sequence_no < before_sequence_no)

        # Latest window, or older page: fetch descending then reverse for asc order.
        use_desc = limit is not None and (
            before_sequence_no is not None or (after_sequence_no is None and before_sequence_no is None)
        )
        if use_desc:
            stmt = select(MessageRow).where(*cond).order_by(MessageRow.sequence_no.desc()).limit(limit)
            rows = list((await self.db.execute(stmt)).scalars())
            rows.reverse()
            return rows

        stmt = select(MessageRow).where(*cond).order_by(MessageRow.sequence_no.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.db.execute(stmt)).scalars())

    async def has_before(self, sid: str, sequence_no: int) -> bool:
        stmt = (
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.session_id == sid, MessageRow.sequence_no < sequence_no)
        )
        return int((await self.db.execute(stmt)).scalar_one()) > 0

    async def get(self, mid: str) -> MessageRow | None:
        return await self.db.get(MessageRow, mid)

    async def context_rows(self, sid: str, before_seq: int) -> list[tuple[MessageRow, str | None]]:
        """Messages before ``before_seq`` joined with their run status (for context building)."""
        stmt = (
            select(MessageRow, RunRow.status)
            .outerjoin(
                RunRow,
                (RunRow.session_id == MessageRow.session_id) & (RunRow.request_id == MessageRow.request_id),
            )
            .where(MessageRow.session_id == sid, MessageRow.sequence_no < before_seq)
            .order_by(MessageRow.sequence_no.asc())
        )
        return [(m, s) for m, s in (await self.db.execute(stmt)).all()]

    async def first_user_message(self, sid: str) -> MessageRow | None:
        stmt = (
            select(MessageRow)
            .where(MessageRow.session_id == sid, MessageRow.role == "user")
            .order_by(MessageRow.sequence_no.asc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()
