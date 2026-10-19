"""Broker abstraction (A5): event streams + coordination primitives."""

from __future__ import annotations

from typing import Protocol

StreamEntry = tuple[str, str, str]  # (stream_id, event_name, data_json)


def session_stream_key(sid: str) -> str:
    # literal braces double as a Redis Cluster hash tag so all per-session keys co-locate
    return f"stream:{{{sid}}}"


def lock_key(sid: str) -> str:
    return f"lock:gen:{{{sid}}}"


def terminal_key(sid: str, request_id: str) -> str:
    return f"terminal:{{{sid}}}:{request_id}"


GLOBAL_STREAM_KEY = "events:global"


class Broker(Protocol):
    name: str

    async def publish(self, key: str, event: str, data: str) -> str: ...

    async def publish_capped(self, key: str, event: str, data: str, maxlen: int) -> str: ...

    async def publish_terminal(
        self, key: str, marker_key: str, event: str, data: str, ttl_seconds: int
    ) -> tuple[str, bool]:
        """Atomically XADD a terminal event unless ``marker_key`` exists.

        Returns ``(stream_id, newly_published)``; when the marker exists, returns the
        previously published id and does NOT add a second entry (C9).
        Also applies ``ttl_seconds`` to the stream (FR-D04).
        """
        ...

    async def read(self, key: str, after: str, block_ms: int, count: int = 500) -> list[StreamEntry]: ...

    async def trim_before(self, key: str, min_id: str) -> int: ...

    async def persist(self, key: str) -> None: ...

    async def delete(self, *keys: str) -> None: ...

    async def length(self, key: str) -> int: ...

    async def first_id(self, key: str) -> str | None: ...

    async def last_id(self, key: str) -> str | None: ...

    async def exists(self, key: str) -> bool: ...

    async def acquire_lock(self, key: str, value: str, ttl_seconds: int) -> bool: ...

    async def renew_lock(self, key: str, value: str, ttl_seconds: int) -> bool: ...

    async def release_lock(self, key: str, value: str) -> bool: ...

    async def get(self, key: str) -> str | None: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...
