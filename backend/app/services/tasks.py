"""Tracks background coroutines so shutdown can wait/cancel them (NFR-10, FR-E05)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine

log = logging.getLogger("app.tasks")


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task %s crashed", task.get_name(), exc_info=task.exception())

    def find(self, name: str) -> asyncio.Task | None:
        return next((t for t in self._tasks if t.get_name() == name), None)

    @property
    def count(self) -> int:
        return len(self._tasks)

    async def shutdown(self, grace_seconds: float) -> None:
        if not self._tasks:
            return
        pending = list(self._tasks)
        _, still = await asyncio.wait(pending, timeout=grace_seconds)
        for t in still:
            t.cancel()
        if still:
            await asyncio.wait(still, timeout=5)
