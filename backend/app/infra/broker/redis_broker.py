"""Redis Streams broker (production, multi-instance)."""

from __future__ import annotations

import redis.asyncio as aioredis

from app.infra.broker.base import StreamEntry

_TERMINAL_LUA = """
local existing = redis.call('GET', KEYS[2])
if existing then
  return {existing, 0}
end
local id = redis.call('XADD', KEYS[1], '*', 'event', ARGV[1], 'data', ARGV[2])
redis.call('SET', KEYS[2], id, 'EX', tonumber(ARGV[3]))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return {id, 1}
"""

_RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""

_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisBroker:
    name = "redis"

    def __init__(self, url: str, max_connections: int = 2000) -> None:
        # non-blocking commands share a small pool; blocking XREAD uses its own large pool.
        # redis-py 8.x defaults socket_timeout to 5s (and may inherit connect timeout).
        # Blocking XREAD MUST use socket_timeout=None, otherwise block_ms >= 5000 aborts the
        # SSE loop with TimeoutError and the browser shows endless "重连中…".
        self._r = aioredis.from_url(
            url,
            decode_responses=True,
            max_connections=64,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
        self._rb = aioredis.from_url(
            url,
            decode_responses=True,
            max_connections=max_connections,
            socket_connect_timeout=5,
            socket_timeout=None,  # wait for XREAD BLOCK to finish (or client disconnect)
            health_check_interval=30,
        )
        self._terminal = self._r.register_script(_TERMINAL_LUA)
        self._renew = self._r.register_script(_RENEW_LUA)
        self._release = self._r.register_script(_RELEASE_LUA)

    async def publish(self, key: str, event: str, data: str) -> str:
        return await self._r.xadd(key, {"event": event, "data": data})

    async def publish_capped(self, key: str, event: str, data: str, maxlen: int) -> str:
        return await self._r.xadd(key, {"event": event, "data": data}, maxlen=maxlen, approximate=True)

    async def publish_terminal(self, key, marker_key, event, data, ttl_seconds):
        sid, created = await self._terminal(keys=[key, marker_key], args=[event, data, ttl_seconds])
        return sid, bool(int(created))

    async def read(self, key: str, after: str, block_ms: int, count: int = 500) -> list[StreamEntry]:
        try:
            res = await self._rb.xread({key: after}, count=count, block=block_ms if block_ms > 0 else None)
        except aioredis.TimeoutError:
            # Defensive: treat a client-side read timeout as an empty block, keep SSE alive.
            return []
        out: list[StreamEntry] = []
        for _key, entries in res or []:
            for sid, fields in entries:
                out.append((sid, fields.get("event", ""), fields.get("data", "{}")))
        return out

    async def trim_before(self, key: str, min_id: str) -> int:
        return int(await self._r.xtrim(key, minid=min_id, approximate=False))

    async def persist(self, key: str) -> None:
        await self._r.persist(key)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._r.delete(*keys)

    async def length(self, key: str) -> int:
        return int(await self._r.xlen(key))

    async def first_id(self, key: str) -> str | None:
        r = await self._r.xrange(key, "-", "+", count=1)
        return r[0][0] if r else None

    async def last_id(self, key: str) -> str | None:
        r = await self._r.xrevrange(key, "+", "-", count=1)
        return r[0][0] if r else None

    async def exists(self, key: str) -> bool:
        return bool(await self._r.exists(key))

    async def get(self, key: str) -> str | None:
        return await self._r.get(key)

    async def acquire_lock(self, key: str, value: str, ttl_seconds: int) -> bool:
        return bool(await self._r.set(key, value, nx=True, ex=ttl_seconds))

    async def renew_lock(self, key: str, value: str, ttl_seconds: int) -> bool:
        return bool(int(await self._renew(keys=[key], args=[value, ttl_seconds])))

    async def release_lock(self, key: str, value: str) -> bool:
        return bool(int(await self._release(keys=[key], args=[value])))

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def close(self) -> None:
        await self._r.aclose()
        await self._rb.aclose()
