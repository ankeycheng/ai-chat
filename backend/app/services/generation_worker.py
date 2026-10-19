"""Background generation worker: start → stream → terminal, under lease/heartbeat (FR-M04, FR-M05, C11, C15)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ContextTooLong
from app.core.logging import log_event, request_id_var, session_id_var
from app.core.metrics import (
    ACTIVE_RUNS,
    ERRORS,
    EVENTS_PUBLISHED,
    FIRST_TOKEN,
    PUBLISH_DURATION,
    STREAM_LENGTH,
)
from app.core.time import utcnow
from app.infra.broker.base import Broker, lock_key, session_stream_key
from app.infra.llm.base import ChatClient, ChatRequest, LLMError
from app.models.entities import MessageRow
from app.repositories.messages import MessageRepository
from app.repositories.runs import RunRepository
from app.repositories.sessions import SessionRepository
from app.services.context_builder import build_messages
from app.services.search_service import SearchService
from app.services.terminal_publisher import TerminalPublisher
from app.services.title_service import TitleService

log = logging.getLogger("app.worker")

DONE_FINISH_REASONS = {"stop", "length", "content_filter"}


class LeaseLost(Exception):
    """Raised when the run is no longer owned by this worker (e.g. marked interrupted)."""


class _Job:
    def __init__(self, sid: str, rid: str, owner: str) -> None:
        self.sid, self.rid, self.owner = sid, rid, owner
        self.lost = False
        self.task: asyncio.Task | None = None


class GenerationWorker:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        broker: Broker,
        llm: ChatClient,
        search: SearchService,
        terminal: TerminalPublisher,
        titles: TitleService,
        settings: Settings,
        spawn,
    ) -> None:
        self.sm = sm
        self.broker = broker
        self.llm = llm
        self.search = search
        self.terminal = terminal
        self.titles = titles
        self.settings = settings
        self._spawn = spawn

    def start(self, sid: str, rid: str, owner: str) -> None:
        job = _Job(sid, rid, owner)
        job.task = self._spawn(self.execute(job), f"run:{sid}:{rid}")

    # ------------------------------------------------------------------ lease
    async def _heartbeat_loop(self, job: _Job) -> None:
        interval = self.settings.worker_heartbeat_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.broker.renew_lock(
                    lock_key(job.sid), f"{job.rid}:{job.owner}", self.settings.lock_ttl_seconds
                )
                if not renewed:
                    log_event(
                        log, "lock.renew_failed", logging.WARNING, session_id=job.sid, request_id=job.rid
                    )
                async with self.sm() as db:
                    ok = await RunRepository(db).heartbeat(job.sid, job.rid, job.owner)
                    await db.commit()
                if not ok:
                    job.lost = True
                    log_event(log, "run.lease_lost", logging.WARNING, session_id=job.sid, request_id=job.rid)
                    if job.task:
                        job.task.cancel()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - transient; the monitor decides staleness
                log_event(
                    log,
                    "heartbeat.failed",
                    logging.WARNING,
                    session_id=job.sid,
                    request_id=job.rid,
                    error=type(e).__name__,
                )

    # ------------------------------------------------------------------ main
    async def execute(self, job: _Job) -> None:
        sid, rid = job.sid, job.rid
        session_id_var.set(sid)
        request_id_var.set(rid)
        ACTIVE_RUNS.inc()
        hb = asyncio.create_task(self._heartbeat_loop(job), name=f"hb:{sid}:{rid}")
        try:
            await self._generate(job)
        except asyncio.CancelledError:
            if not job.lost:
                # graceful shutdown/cancellation: converge to interrupted (NFR-10)
                await self._fail(job, "interrupted", "GENERATION_INTERRUPTED", "生成任务因服务关闭而中断")
                raise
        except LeaseLost:
            pass
        except ContextTooLong as e:
            await self._fail(job, "failed", e.code, e.message)
        except LLMError as e:
            await self._fail(job, "failed", "UPSTREAM_ERROR", "模型服务调用失败，请稍后重试。", detail=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("generation crashed")
            await self._fail(
                job, "failed", "INTERNAL_ERROR", "生成过程中发生内部错误。", detail=type(e).__name__
            )
        finally:
            hb.cancel()
            ACTIVE_RUNS.dec()

    async def _publish(self, key: str, event: str, data: dict) -> str:
        t0 = time.perf_counter()
        sid = await self.broker.publish(key, event, json.dumps(data, ensure_ascii=False))
        PUBLISH_DURATION.observe(time.perf_counter() - t0)
        EVENTS_PUBLISHED.labels(event).inc()
        return sid

    async def _generate(self, job: _Job) -> None:
        sid, rid, owner = job.sid, job.rid, job.owner
        s = self.settings
        key = session_stream_key(sid)
        async with self.sm() as db:
            runs = RunRepository(db)
            if not await runs.mark_running(sid, rid, owner):
                raise LeaseLost()
            await db.commit()
            run = await runs.get(sid, rid)
            prev_terminal = await runs.last_terminal_event_id(sid, rid)
            user_msg = await MessageRepository(db).get(run.user_message_id)
            history = await MessageRepository(db).context_rows(sid, user_msg.sequence_no)

        # Step 6: trim the previous round (keep its terminal event), clear TTL, publish start
        await self.broker.persist(key)
        if prev_terminal:
            trimmed = await self.broker.trim_before(key, prev_terminal)
            log_event(
                log, "stream.trimmed", session_id=sid, request_id=rid, removed=trimmed, min_id=prev_terminal
            )
        start_id = await self._publish(
            key,
            "start",
            {
                "request_id": rid,
                "status": "running",
                "thinking": run.thinking,
                "web_search": run.web_search,
                "user_message": {
                    "id": user_msg.id,
                    "sequence_no": user_msg.sequence_no,
                    "role": "user",
                    "content": user_msg.content,
                    "request_id": rid,
                    "created_at": user_msg.created_at.isoformat(),
                },
            },
        )
        async with self.sm() as db:
            if not await RunRepository(db).update_fields(sid, rid, owner, start_event_id=start_id):
                raise LeaseLost()
            await SessionRepository(db).set_last_event_id(sid, start_id)
            await db.commit()
        last_id = start_id
        t_accept = time.perf_counter()

        # Web search (application side, FR-W01–FR-W05)
        sources: list[dict] = []
        search_block = None
        if run.web_search:
            outcome = await self.search.run(user_msg.content)
            if outcome.error is not None:
                last_id = await self._publish(
                    key,
                    "notice",
                    {
                        "request_id": rid,
                        "code": "SEARCH_UNAVAILABLE"
                        if outcome.error.code != "SEARCH_NO_RESULTS"
                        else "SEARCH_NO_RESULTS",
                        "message": "联网搜索不可用，正在无搜索模式下继续"
                        if outcome.error.code != "SEARCH_NO_RESULTS"
                        else "未找到相关搜索结果，正在无搜索模式下继续",
                    },
                )
            else:
                sources, search_block = outcome.sources, outcome.block
                last_id = await self._publish(key, "sources", {"request_id": rid, "sources": sources})

        messages, stats = build_messages(
            system_prompt=s.system_prompt,
            history=history,
            current_user=user_msg.content,
            search_block=search_block,
            max_input_tokens=s.context_max_input_tokens,
            max_history_messages=s.context_max_history_messages,
        )
        log_event(
            log,
            "context.built",
            session_id=sid,
            request_id=rid,
            turns=stats.included_turns,
            dropped=stats.dropped_turns,
            est_tokens=stats.estimated_tokens,
            search_chars=stats.search_chars,
        )
        async with self.sm() as db:
            await RunRepository(db).update_fields(sid, rid, owner, context_message_count=len(messages))
            await db.commit()

        req = ChatRequest(
            messages=messages,
            thinking=run.thinking,
            max_tokens=s.max_output_tokens,
            model=run.model or s.deepseek_model,
            reasoning_effort=s.reasoning_effort,
        )
        content: list[str] = []
        reasoning: list[str] = []
        finish_reason: str | None = None
        usage: dict | None = None
        first = True
        t_up = time.perf_counter()
        n_since_flush, last_flush = 0, time.monotonic()
        dropped_reasoning = 0
        async for d in self.llm.stream_chat(req):
            if job.lost:
                raise LeaseLost()
            if d.kind == "reasoning" and d.text and not run.thinking:
                # FR-T02: thinking explicitly disabled → never leak unexpected reasoning deltas
                if dropped_reasoning == 0:
                    log_event(
                        log, "upstream.unexpected_reasoning", logging.WARNING, session_id=sid, request_id=rid
                    )
                dropped_reasoning += 1
                continue
            if d.kind and d.text:
                if first:
                    FIRST_TOKEN.observe(time.perf_counter() - t_up)
                    log_event(
                        log,
                        "upstream.first_token",
                        session_id=sid,
                        request_id=rid,
                        upstream_ms=round((time.perf_counter() - t_up) * 1000),
                        since_start_ms=round((time.perf_counter() - t_accept) * 1000),
                    )
                    first = False
                (reasoning if d.kind == "reasoning" else content).append(d.text)
                last_id = await self._publish(
                    key, "delta", {"request_id": rid, "kind": d.kind, "text": d.text}
                )
                n_since_flush += 1
                if (
                    n_since_flush >= s.last_event_id_flush_events
                    or time.monotonic() - last_flush >= s.last_event_id_flush_seconds
                ):
                    async with self.sm() as db:
                        await SessionRepository(db).set_last_event_id(sid, last_id)
                        await db.commit()
                    n_since_flush, last_flush = 0, time.monotonic()
            if d.finish_reason:
                finish_reason = d.finish_reason
                usage = d.usage
        if finish_reason is None:
            raise LLMError("upstream stream ended without finish_reason")
        if finish_reason not in DONE_FINISH_REASONS:
            raise LLMError(f"upstream finish_reason={finish_reason}")

        # Step 8: persist final assistant message + run completed (C3), then publish done
        amid = str(uuid.uuid4())
        async with self.sm() as db:
            sessions, runs = SessionRepository(db), RunRepository(db)
            seq = await sessions.reserve_sequence(sid, rid)
            await MessageRepository(db).add(
                MessageRow(
                    id=amid,
                    session_id=sid,
                    sequence_no=seq,
                    role="assistant",
                    content="".join(content),
                    reasoning_content="".join(reasoning),
                    sources_json=json.dumps(sources, ensure_ascii=False),
                    finish_reason=finish_reason,
                    request_id=rid,
                    created_at=utcnow(),
                )
            )
            ok = await runs.finish(
                sid,
                rid,
                owner,
                "completed",
                assistant_message_id=amid,
                finish_reason=finish_reason,
                usage_json=json.dumps(usage) if usage else None,
            )
            if not ok:
                await db.rollback()
                raise LeaseLost()
            await sessions.set_last_event_id(sid, last_id)
            await db.commit()
        try:
            STREAM_LENGTH.set(await self.broker.length(key))
        except Exception:  # noqa: BLE001
            pass
        await self._publish_terminal_safely(sid, rid)
        log_event(
            log,
            "run.completed",
            session_id=sid,
            request_id=rid,
            finish_reason=finish_reason,
            content_chars=sum(map(len, content)),
            reasoning_chars=sum(map(len, reasoning)),
        )
        # Title generation is asynchronous and never blocks `done` (FR-N02)
        self._spawn(self.titles.maybe_generate(sid), f"title:{sid}")

    async def _publish_terminal_safely(self, sid: str, rid: str) -> None:
        try:
            await self.terminal.publish_and_finalize(sid, rid)
        except Exception as e:  # noqa: BLE001 - repair scanner will retry (C9)
            log_event(
                log,
                "terminal.publish_failed",
                logging.ERROR,
                session_id=sid,
                request_id=rid,
                error=type(e).__name__,
            )

    async def _fail(self, job: _Job, status: str, code: str, message: str, detail: str | None = None) -> None:
        ERRORS.labels(code).inc()
        log_event(
            log,
            "run.failed",
            logging.WARNING,
            session_id=job.sid,
            request_id=job.rid,
            status=status,
            code=code,
            detail=detail,
        )
        try:
            async with self.sm() as db:
                ok = await RunRepository(db).finish(
                    job.sid, job.rid, job.owner, status, error_code=code, error_message=message
                )
                await db.commit()
        except Exception as e:  # noqa: BLE001 - monitor will mark interrupted later
            log_event(log, "run.fail_persist_failed", logging.ERROR, error=type(e).__name__)
            return
        if ok:
            await self._publish_terminal_safely(job.sid, job.rid)
