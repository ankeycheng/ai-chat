"""Message acceptance: idempotency → mutex → transactional insert → 202 (FR-M01, FR-M07, C5)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import GenerationInProgress, IdempotencyKeyReused, SessionNotFound
from app.core.logging import log_event
from app.core.time import utcnow
from app.infra.broker.base import Broker, lock_key
from app.models.entities import MessageRow, RunRow
from app.repositories.messages import MessageRepository
from app.repositories.runs import RunRepository
from app.repositories.sessions import SessionRepository
from app.schemas.message import AcceptOut, MessageCreate
from app.services.sync_service import SyncService

log = logging.getLogger("app.chat")


def normalize_content(content: str) -> str:
    return content.replace("\r\n", "\n").strip()


def request_hash(body: MessageCreate) -> str:
    canon = json.dumps(
        {
            "content": normalize_content(body.content),
            "thinking": body.thinking,
            "web_search": body.web_search,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canon.encode()).hexdigest()


class ChatService:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        broker: Broker,
        sync: SyncService,
        settings: Settings,
        start_worker,
    ) -> None:
        self.sm = sm
        self.broker = broker
        self.sync = sync
        self.settings = settings
        self._start_worker = start_worker  # callable(sid, rid, owner) – injected by the container

    @staticmethod
    def _accepted(run: RunRow) -> AcceptOut:
        return AcceptOut(
            session_id=run.session_id,
            request_id=run.request_id,
            status=run.status,
            user_message_id=run.user_message_id,
        )

    async def _existing(self, sid: str, rid: str, h: str) -> AcceptOut | None:
        async with self.sm() as db:
            run = await RunRepository(db).get(sid, rid)
        if run is None:
            return None
        if run.request_hash != h:
            raise IdempotencyKeyReused(request_id=rid)
        return self._accepted(run)

    async def accept(self, sid: str, body: MessageCreate) -> AcceptOut:
        rid = body.request_id
        h = request_hash(body)
        # 1. session exists
        async with self.sm() as db:
            session = await SessionRepository(db).get(sid)
        if session is None:
            raise SessionNotFound()
        # 2. idempotency lookup precedes lock competition (C5)
        if (prev := await self._existing(sid, rid, h)) is not None:
            return prev
        if session.status == "generating" and session.active_request_id != rid:
            raise GenerationInProgress(request_id=rid)
        # 3. compete for the generation lock
        owner = secrets.token_hex(16)
        lock_value = f"{rid}:{owner}"
        deadline = time.monotonic() + self.settings.idempotency_wait_seconds
        while not await self.broker.acquire_lock(lock_key(sid), lock_value, self.settings.lock_ttl_seconds):
            holder = await self.broker.get(lock_key(sid)) or ""
            if not holder.startswith(f"{rid}:"):
                if holder:
                    raise GenerationInProgress(request_id=rid)
                continue  # lock vanished in between – retry
            # concurrent duplicate of the same request: wait for the first acceptance result
            if (prev := await self._existing(sid, rid, h)) is not None:
                return prev
            if time.monotonic() > deadline:
                raise GenerationInProgress("相同 request_id 的请求正在处理中，请稍后重试。", request_id=rid)
            await asyncio.sleep(0.05)
        # re-check after obtaining the lock (first request may have completed meanwhile)
        try:
            if (prev := await self._existing(sid, rid, h)) is not None:
                await self.broker.release_lock(lock_key(sid), lock_value)
                return prev
        except IdempotencyKeyReused:
            await self.broker.release_lock(lock_key(sid), lock_value)
            raise
        # 4. single transaction: session idle→generating, user message, run(accepted)
        try:
            async with self.sm() as db:
                sessions = SessionRepository(db)
                seq = await sessions.try_begin_generation(sid, rid)
                if seq is None:
                    await db.rollback()
                    raise GenerationInProgress(request_id=rid)
                now = utcnow()
                umid = str(uuid.uuid4())
                await MessageRepository(db).add(
                    MessageRow(
                        id=umid,
                        session_id=sid,
                        sequence_no=seq,
                        role="user",
                        content=normalize_content(body.content),
                        reasoning_content="",
                        sources_json="[]",
                        request_id=rid,
                        created_at=now,
                    )
                )
                run = await RunRepository(db).add(
                    RunRow(
                        session_id=sid,
                        request_id=rid,
                        request_hash=h,
                        status="accepted",
                        thinking=body.thinking,
                        web_search=body.web_search,
                        model=self.settings.deepseek_model,
                        owner_token=owner,
                        instance_id=self.settings.instance_id,
                        user_message_id=umid,
                        created_at=now,
                        updated_at=now,
                        heartbeat_at=now,
                        terminal_event_published=False,
                    )
                )
                await db.commit()
                srow = await sessions.get(sid)
        except IntegrityError:
            await self.broker.release_lock(lock_key(sid), lock_value)
            if (prev := await self._existing(sid, rid, h)) is not None:
                return prev
            raise GenerationInProgress(request_id=rid) from None
        except BaseException:
            await self.broker.release_lock(lock_key(sid), lock_value)
            raise
        log_event(
            log,
            "run.accepted",
            session_id=sid,
            request_id=rid,
            thinking=body.thinking,
            web_search=body.web_search,
            sequence_no=seq,
        )
        # 5. start the background worker on this instance
        self._start_worker(sid, rid, owner)
        if srow is not None:
            await self.sync.session_updated(srow)
        return self._accepted(run)
