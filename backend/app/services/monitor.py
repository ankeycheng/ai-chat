"""Lease monitor + terminal repair scanner (FR-M08, FR-F02, C9, C11).

Runs on every instance. All state transitions are conditional DB updates, so several
monitors (and a still-alive worker) can race safely; terminal publishing is idempotent.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.core.time import utcnow
from app.repositories.runs import RunRepository
from app.services.terminal_publisher import TerminalPublisher

log = logging.getLogger("app.monitor")


class RunMonitor:
    def __init__(
        self, sm: async_sessionmaker[AsyncSession], terminal: TerminalPublisher, settings: Settings
    ) -> None:
        self.sm = sm
        self.terminal = terminal
        self.settings = settings

    async def scan_once(self) -> dict[str, int]:
        interrupted = repaired = 0
        now = utcnow()
        stale_threshold = now - timedelta(seconds=self.settings.worker_stale_seconds)
        async with self.sm() as db:
            stale = await RunRepository(db).stale_active(stale_threshold)
        for run in stale:
            async with self.sm() as db:
                won = await RunRepository(db).mark_interrupted_if_stale(run, stale_threshold)
                await db.commit()
            if won:
                interrupted += 1
                log_event(
                    log,
                    "run.interrupted",
                    logging.WARNING,
                    session_id=run.session_id,
                    request_id=run.request_id,
                    last_heartbeat=str(run.heartbeat_at),
                )
                try:
                    await self.terminal.publish_and_finalize(run.session_id, run.request_id)
                except Exception as e:  # noqa: BLE001
                    log_event(log, "monitor.publish_failed", logging.ERROR, error=type(e).__name__)
        grace = now - timedelta(seconds=self.settings.terminal_repair_grace_seconds)
        async with self.sm() as db:
            pending = await RunRepository(db).unpublished_terminal(grace)
        for run in pending:
            try:
                await self.terminal.publish_and_finalize(run.session_id, run.request_id)
                repaired += 1
                log_event(log, "run.terminal_repaired", session_id=run.session_id, request_id=run.request_id)
            except Exception as e:  # noqa: BLE001
                log_event(log, "monitor.repair_failed", logging.ERROR, error=type(e).__name__)
        return {"interrupted": interrupted, "repaired": repaired}

    async def run_forever(self) -> None:
        interval = min(
            self.settings.terminal_repair_interval_seconds, max(self.settings.worker_stale_seconds / 3, 0.2)
        )
        while True:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - DB down etc.; keep scanning
                log_event(log, "monitor.scan_failed", logging.WARNING, error=type(e).__name__)
            await asyncio.sleep(interval)
