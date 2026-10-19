"""In-process broker for single-process local development (A5).

Same cursor semantics as Redis Streams; IDs are ``ms-seq`` and strictly increasing.
Does NOT provide cross-process/instance broadcast.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.core.stream_ids import parse_stream_id
from app.infra.broker.base import StreamEntry


@dataclass
class _Stream:
    entries: list[tuple[tuple[int, int], str, str, str]] = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    last: tuple[int, int] = (0, 0)


class MemoryBroker:
    name = "memory"

    def __init__(self) -> None:
        self._streams: dict[str, _Stream] = {}
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._expiry: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ helpers
    def _expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        if exp is not None and exp <= time.monotonic():
            self._expiry.pop(key, None)
            st = self._streams.get(key)
            if st is not None:
                st.entries.clear()  # keep `last` so IDs stay monotonic, keep cond for readers
            return True
        return False

    def _stream(self, key: str) -> _Stream:
        self._expired(key)
        st = self._streams.get(key)
        if st is None:
            st = self._streams[key] = _Stream()
        return st

    def _next_id(self, st: _Stream) -> tuple[int, int]:
        now = int(time.time() * 1000)
        ms, seq = st.last
        new = (now, 0) if now > ms else (ms, seq + 1)
        st.last = new
        return new

    async def _add(self, key: str, event: str, data: str, maxlen: int | None = None) -> str:
        st = self._stream(key)
        sid = self._next_id(st)
        id_str = f"{sid[0]}-{sid[1]}"
        st.entries.append((sid, id_str, event, data))
        if maxlen is not None and len(st.entries) > maxlen:
            del st.entries[: len(st.entries) - maxlen]
        async with st.cond:
            st.cond.notify_all()
        return id_str

    # ------------------------------------------------------------ streams
    async def publish(self, key: str, event: str, data: str) -> str:
        return await self._add(key, event, data)

    async def publish_capped(self, key: str, event: str, data: str, maxlen: int) -> str:
        return await self._add(key, event, data, maxlen)

    async def publish_terminal(self, key, marker_key, event, data, ttl_seconds):
        async with self._lock:
            existing = await self.get(marker_key)
            if existing:
                return existing, False
            sid = await self._add(key, event, data)
            self._kv[marker_key] = (sid, time.monotonic() + ttl_seconds)
            self._expiry[key] = time.monotonic() + ttl_seconds
            return sid, True

    def _after(self, st: _Stream, after: tuple[int, int], count: int) -> list[StreamEntry]:
        out: list[StreamEntry] = []
        for sid, id_str, ev, data in st.entries:
            if sid > after:
                out.append((id_str, ev, data))
                if len(out) >= count:
                    break
        return out

    async def read(self, key: str, after: str, block_ms: int, count: int = 500) -> list[StreamEntry]:
        a = parse_stream_id(after)
        st = self._stream(key)
        res = self._after(st, a, count)
        if res or block_ms <= 0:
            return res
        deadline = time.monotonic() + block_ms / 1000
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            async with st.cond:
                try:
                    await asyncio.wait_for(st.cond.wait(), remaining)
                except TimeoutError:
                    pass
            st = self._stream(key)
            res = self._after(st, a, count)
            if res:
                return res

    async def trim_before(self, key: str, min_id: str) -> int:
        m = parse_stream_id(min_id)
        st = self._stream(key)
        before = len(st.entries)
        st.entries = [e for e in st.entries if e[0] >= m]
        return before - len(st.entries)

    async def persist(self, key: str) -> None:
        self._expiry.pop(key, None)

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._streams.pop(k, None)
            self._kv.pop(k, None)
            self._expiry.pop(k, None)

    async def length(self, key: str) -> int:
        return len(self._stream(key).entries)

    async def first_id(self, key: str) -> str | None:
        st = self._stream(key)
        return st.entries[0][1] if st.entries else None

    async def last_id(self, key: str) -> str | None:
        st = self._stream(key)
        return st.entries[-1][1] if st.entries else None

    async def exists(self, key: str) -> bool:
        if key in self._kv:
            return await self.get(key) is not None
        return bool(self._stream(key).entries)

    # ------------------------------------------------------------ kv / locks
    async def get(self, key: str) -> str | None:
        item = self._kv.get(key)
        if item is None:
            return None
        value, exp = item
        if exp is not None and exp <= time.monotonic():
            self._kv.pop(key, None)
            return None
        return value

    async def acquire_lock(self, key: str, value: str, ttl_seconds: int) -> bool:
        async with self._lock:
            if await self.get(key) is not None:
                return False
            self._kv[key] = (value, time.monotonic() + ttl_seconds)
            return True

    async def renew_lock(self, key: str, value: str, ttl_seconds: int) -> bool:
        async with self._lock:
            if await self.get(key) != value:
                return False
            self._kv[key] = (value, time.monotonic() + ttl_seconds)
            return True

    async def release_lock(self, key: str, value: str) -> bool:
        async with self._lock:
            if await self.get(key) != value:
                return False
            self._kv.pop(key, None)
            return True

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None
