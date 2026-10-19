"""Broker contract tests for memory and Redis implementations (A5, T18, §7.3)."""

import asyncio
import os
import uuid

import pytest

from app.infra.broker.memory_broker import MemoryBroker
from tests.conftest import REDIS_URL, _tcp_ok


def _brokers():
    params = [pytest.param("memory", id="memory")]
    marks = [] if _tcp_ok("localhost", 6379) else [pytest.mark.skip(reason="redis not reachable")]
    if "postgres-redis" in os.getenv("TEST_MODES", "sqlite-memory,postgres-redis"):
        params.append(pytest.param("redis", id="redis", marks=marks))
    return params


@pytest.fixture(params=_brokers())
async def broker(request):
    if request.param == "memory":
        b = MemoryBroker()
    else:
        from app.infra.broker.redis_broker import RedisBroker

        b = RedisBroker(REDIS_URL)
    yield b
    await b.close()


def k(prefix="t"):
    return f"{prefix}:{uuid.uuid4()}"


async def test_cursor_semantics_exclusive_and_ordered(broker):
    key = k()
    ids = [await broker.publish(key, "delta", f'{{"i":{i}}}') for i in range(5)]
    got = await broker.read(key, ids[1], 0)
    assert [g[0] for g in got] == ids[2:]
    assert await broker.read(key, ids[-1], 50) == []
    assert await broker.first_id(key) == ids[0] and await broker.last_id(key) == ids[-1]
    await broker.delete(key)


async def test_blocking_read_wakes_on_publish(broker):
    key = k()
    last = await broker.publish(key, "x", "{}")

    async def later():
        await asyncio.sleep(0.1)
        await broker.publish(key, "y", "{}")

    t = asyncio.create_task(later())
    got = await broker.read(key, last, 2000)
    await t
    assert [g[1] for g in got] == ["y"]
    await broker.delete(key)


async def test_t18_lock_owner_safety(broker):
    key = k("lock")
    assert await broker.acquire_lock(key, "r1:old", 1)
    assert not await broker.acquire_lock(key, "r2:new", 1)
    await asyncio.sleep(1.2)  # old owner's lock expires
    assert await broker.acquire_lock(key, "r2:new", 5)
    assert not await broker.renew_lock(key, "r1:old", 5)  # old owner cannot renew
    assert not await broker.release_lock(key, "r1:old")  # …nor delete the new owner's lock
    assert await broker.get(key) == "r2:new"
    assert await broker.renew_lock(key, "r2:new", 5)
    assert await broker.release_lock(key, "r2:new")
    assert await broker.get(key) is None


async def test_terminal_publish_is_idempotent(broker):
    key, marker = k("stream"), k("terminal")
    await broker.publish(key, "start", "{}")
    id1, new1 = await broker.publish_terminal(key, marker, "done", '{"a":1}', 60)
    id2, new2 = await broker.publish_terminal(key, marker, "done", '{"a":1}', 60)
    assert new1 and not new2 and id1 == id2
    assert await broker.length(key) == 2
    await broker.delete(key, marker)


async def test_trim_before_keeps_min_id(broker):
    key = k()
    ids = [await broker.publish(key, "e", "{}") for i in range(10)]
    await broker.trim_before(key, ids[6])
    assert await broker.first_id(key) == ids[6]
    assert await broker.length(key) == 4
    await broker.delete(key)


async def test_capped_publish(broker):
    key = k()
    for _ in range(300):
        await broker.publish_capped(key, "e", "{}", 50)
    assert await broker.length(key) <= 150  # approximate trimming on Redis
    await broker.delete(key)
