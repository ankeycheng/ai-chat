"""Test harness: runs the real ASGI app on a real uvicorn socket (SSE needs true streaming).

Modes (TEST_MODES env, default both): ``sqlite-memory`` and ``postgres-redis``.
postgres-redis uses TEST_DATABASE_URL (default postgresql://ai_chat:ai_chat@localhost/ai_chat_test)
and TEST_REDIS_URL (default redis://localhost:6379/15); skipped when unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import uuid
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn

from app.container import Container
from app.core.config import Settings
from app.infra.llm.fake import FakeChatClient
from app.infra.search.fake import FakeSearchProvider
from app.main import create_app

PG_URL = os.getenv("TEST_DATABASE_URL", "postgresql://ai_chat:ai_chat@localhost/ai_chat_test")
REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")
MODES = [m.strip() for m in os.getenv("TEST_MODES", "sqlite-memory,postgres-redis").split(",") if m.strip()]

_pg_ready: bool | None = None


def _tcp_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def base_settings(mode: str, tmp_path, **overrides) -> Settings:
    common = dict(
        app_profile="test",
        llm_provider="fake",
        search_provider="fake",
        log_format="text",
        log_level="WARNING",
        ping_interval_seconds=1,
        sse_block_ms=200,
        worker_heartbeat_seconds=0.5,
        lock_ttl_seconds=3,
        worker_stale_seconds=3,
        terminal_repair_interval_seconds=0.5,
        terminal_repair_grace_seconds=0.5,
        idempotency_wait_seconds=3,
        deepseek_api_key="sk-test",
        cors_origins="http://localhost:5173",
    )
    if mode == "sqlite-memory":
        common.update(db_backend="sqlite", sqlite_path=str(tmp_path / "test.db"), broker_backend="memory")
    else:
        common.update(database_url=PG_URL, broker_backend="redis", redis_url=REDIS_URL)
    common.update(overrides)
    return Settings(**common)


async def reset_storage(settings: Settings) -> None:
    from app.db.migrate import upgrade_head

    if settings.is_sqlite:
        await upgrade_head(settings)
        return
    global _pg_ready
    if not _pg_ready:
        await upgrade_head(settings)
        _pg_ready = True
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(settings.sqlalchemy_url)
    async with eng.begin() as conn:
        await conn.execute(text("TRUNCATE messages, generation_runs, sessions CASCADE"))
    await eng.dispose()
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url)
    await r.flushdb()
    await r.aclose()


@dataclass
class Server:
    container: Container
    base_url: str
    client: httpx.AsyncClient
    llm: FakeChatClient
    search: FakeSearchProvider
    _server: uvicorn.Server | None = None
    _task: asyncio.Task | None = None
    extra: dict = field(default_factory=dict)

    async def new_session(self) -> str:
        r = await self.client.post("/api/sessions")
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def send(self, sid: str, content: str = "你好", *, thinking=False, web_search=False, rid=None):
        rid = rid or str(uuid.uuid4())
        r = await self.client.post(
            f"/api/sessions/{sid}/messages",
            json={"content": content, "thinking": thinking, "web_search": web_search, "request_id": rid},
        )
        return r, rid

    async def wait_idle(self, sid: str, timeout: float = 10) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            r = await self.client.get(f"/api/sessions/{sid}")
            data = r.json()
            if data["status"] == "idle":
                return data
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"session not idle: {data}")
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        await self.client.aclose()
        if self._server:
            self._server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, 15)


async def start_server(settings: Settings, *, llm=None, search=None, migrate=True) -> Server:
    if migrate:
        await reset_storage(settings)
    llm = llm or FakeChatClient()
    llm.delay_ms = 2
    search = search or FakeSearchProvider()
    container = Container.build(settings, llm=llm, search_provider=search)
    app = create_app(settings, container=container)
    port = free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on", timeout_graceful_shutdown=3
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    client = httpx.AsyncClient(base_url=base, timeout=20)
    return Server(
        container=container, base_url=base, client=client, llm=llm, search=search, _server=server, _task=task
    )


def _mode_params():
    params = []
    for m in MODES:
        marks = []
        if m == "postgres-redis" and not (_tcp_ok("localhost", 5432) and _tcp_ok("localhost", 6379)):
            marks.append(pytest.mark.skip(reason="postgres/redis not reachable"))
        params.append(pytest.param(m, marks=marks, id=m))
    return params


@pytest.fixture(params=_mode_params())
def mode(request) -> str:
    return request.param


@pytest.fixture
async def srv(mode, tmp_path):
    s = await start_server(base_settings(mode, tmp_path))
    yield s
    await s.stop()


class SSECollector:
    """Background SSE reader that records parsed events."""

    def __init__(self, base_url: str, path: str, headers: dict | None = None) -> None:
        self.base_url, self.path, self.headers = base_url, path, headers or {}
        self.events: list[dict] = []
        self.status: int | None = None
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Event()

    async def _run(self) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as c:
            async with c.stream("GET", self.path, headers=self.headers) as resp:
                self.status = resp.status_code
                if resp.status_code != 200:
                    self.body = (await resp.aread()).decode()
                    self._changed.set()
                    return
                cur: dict = {}
                async for line in resp.aiter_lines():
                    if line == "":
                        if cur.get("event"):
                            if "data" in cur:
                                try:
                                    cur["json"] = json.loads(cur["data"])
                                except json.JSONDecodeError:
                                    cur["json"] = None
                            self.events.append(cur)
                            self._changed.set()
                        cur = {}
                        continue
                    k, _, v = line.partition(":")
                    v = v[1:] if v.startswith(" ") else v
                    if k == "data":
                        cur["data"] = cur.get("data", "") + ("\n" if "data" in cur else "") + v
                    elif k in ("id", "event"):
                        cur[k] = v

    async def __aenter__(self) -> SSECollector:
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *a) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    def business(self) -> list[dict]:
        return [e for e in self.events if "id" in e]

    def named(self, name: str) -> list[dict]:
        return [e for e in self.events if e.get("event") == name]

    async def wait_for(self, pred, timeout: float = 10) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not pred(self):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise AssertionError(f"timeout waiting; got events: {[e.get('event') for e in self.events]}")
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), min(remaining, 0.2))

    async def wait_event(self, name: str, n: int = 1, timeout: float = 10, request_id: str | None = None):
        def pred(c):
            evs = [
                e
                for e in c.named(name)
                if request_id is None or (e.get("json") or {}).get("request_id") == request_id
            ]
            return len(evs) >= n

        await self.wait_for(pred, timeout)
        return self.named(name)


@pytest.fixture
def sse_collector():
    return SSECollector
