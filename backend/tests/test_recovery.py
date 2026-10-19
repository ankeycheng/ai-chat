"""Crash/lease/terminal-repair/dependency tests (T08, T17, T19, T23) and multi-instance behaviour."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.infra.broker.base import lock_key, session_stream_key, terminal_key
from app.models.entities import MessageRow, RunRow
from app.repositories.runs import RunRepository
from app.repositories.sessions import SessionRepository
from app.services.terminal_publisher import terminal_event_for
from tests.conftest import PG_URL, REDIS_URL, _tcp_ok, base_settings, free_port, start_server

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pg_redis = pytest.mark.skipif(
    not (_tcp_ok("localhost", 5432) and _tcp_ok("localhost", 6379)), reason="postgres/redis not reachable"
)


async def _craft_active_run(srv, *, status="running", heartbeat_age=60.0, terminal=None, owner="deadbeef"):
    """Insert a session whose run looks like it belonged to a worker that died."""
    sid = await srv.new_session()
    rid = str(uuid.uuid4())
    now = utcnow()
    c = srv.container
    async with c.sm() as db:
        seq = await SessionRepository(db).try_begin_generation(sid, rid)
        db.add(
            MessageRow(
                id=str(uuid.uuid4()),
                session_id=sid,
                sequence_no=seq,
                role="user",
                content="q",
                reasoning_content="",
                sources_json="[]",
                request_id=rid,
                created_at=now,
            )
        )
        run = RunRow(
            session_id=sid,
            request_id=rid,
            request_hash="h",
            status=status,
            thinking=False,
            web_search=False,
            owner_token=owner,
            created_at=now - timedelta(seconds=heartbeat_age),
            updated_at=now,
            heartbeat_at=now - timedelta(seconds=heartbeat_age),
            terminal_event_published=False,
        )
        if terminal:
            run.status = terminal
            run.finished_at = now - timedelta(seconds=10)
            run.error_code = "UPSTREAM_ERROR" if terminal == "failed" else None
            run.error_message = "x" if terminal == "failed" else None
        db.add(run)
        await db.commit()
    await c.broker.acquire_lock(lock_key(sid), f"{rid}:{owner}", 60)
    start = await c.broker.publish(session_stream_key(sid), "start", json.dumps({"request_id": rid}))
    await c.broker.publish(
        session_stream_key(sid), "delta", json.dumps({"request_id": rid, "kind": "content", "text": "部分"})
    )
    async with c.sm() as db:
        await RunRepository(db).update_fields(sid, rid, start_event_id=start)
        await db.commit()
    return sid, rid


async def test_t17_lite_stale_lease_marked_interrupted(srv, sse_collector):
    sid, rid = await _craft_active_run(srv)
    ar = (await srv.client.get(f"/api/sessions/{sid}/active-run")).json()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream?after={ar['start_event_id']}") as c:
        await c.wait_event("error", request_id=rid, timeout=10)  # background monitor does it
    assert c.named("delta")[0]["json"]["text"] == "部分"  # already-written deltas replay
    err = c.named("error")[0]["json"]
    assert err["code"] == "GENERATION_INTERRUPTED"
    s = await srv.wait_idle(sid)
    assert s["active_request_id"] is None
    run = (await srv.client.get(f"/api/sessions/{sid}/runs/{rid}")).json()
    assert run["status"] == "interrupted" and run["terminal_event_published"]
    assert await srv.container.broker.get(lock_key(sid)) is None  # dead owner's lock released by token


async def test_fresh_heartbeat_not_interrupted(srv):
    sid, rid = await _craft_active_run(srv, heartbeat_age=0)
    res = await srv.container.monitor.scan_once()
    assert res["interrupted"] == 0
    run = (await srv.client.get(f"/api/sessions/{sid}/runs/{rid}")).json()
    assert run["status"] == "running"


async def test_t19a_db_terminal_committed_but_not_published(srv):
    sid, rid = await _craft_active_run(srv, terminal="failed", heartbeat_age=0)
    key = session_stream_key(sid)
    n0 = await srv.container.broker.length(key)
    await srv.container.monitor.scan_once()
    await srv.container.monitor.scan_once()  # second pass must not duplicate
    assert await srv.container.broker.length(key) == n0 + 1
    entries = await srv.container.broker.read(key, "0-0", 0)
    assert [e[1] for e in entries].count("error") == 1
    s = await srv.wait_idle(sid)
    assert s["last_event_id"] == entries[-1][0]


async def test_t19b_published_but_db_flag_not_set(srv):
    sid, rid = await _craft_active_run(srv, terminal="failed", heartbeat_age=0)
    c = srv.container
    async with c.sm() as db:
        run = await RunRepository(db).get(sid, rid)
    ev, data = terminal_event_for(run)
    key = session_stream_key(sid)
    tid, created = await c.broker.publish_terminal(key, terminal_key(sid, rid), ev, json.dumps(data), 60)
    assert created
    n = await c.broker.length(key)
    await c.monitor.scan_once()
    assert await c.broker.length(key) == n  # no second XADD
    run = (await srv.client.get(f"/api/sessions/{sid}/runs/{rid}")).json()
    assert run["terminal_event_published"] and run["terminal_event_id"] == tid
    await srv.wait_idle(sid)


async def test_busy_until_terminal_published_even_if_lock_expired(srv):
    sid, rid = await _craft_active_run(srv, heartbeat_age=0)
    await srv.container.broker.release_lock(lock_key(sid), f"{rid}:deadbeef")  # lock "expired"
    r, _ = await srv.send(sid, "新任务")
    assert r.status_code == 409 and r.json()["error"]["code"] == "GENERATION_IN_PROGRESS"
    assert await srv.container.broker.get(lock_key(sid)) is None  # our lock was safely released


async def test_t23_database_unavailable(tmp_path):
    s = base_settings(
        "postgres-redis",
        tmp_path,
        database_url="postgresql://ai_chat:ai_chat@127.0.0.1:1/none",
        broker_backend="memory",
    )
    srv = await start_server(s, migrate=False)
    try:
        r = await srv.client.post("/api/sessions")
        assert r.status_code == 503 and r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        r = await srv.client.post(
            f"/api/sessions/{uuid.uuid4()}/messages",
            json={"content": "x", "thinking": False, "web_search": False, "request_id": str(uuid.uuid4())},
        )
        assert r.status_code == 503  # never a fake 202
        r = await srv.client.get("/api/ready")
        assert r.status_code == 503 and r.json()["checks"]["database"].startswith("error")
        assert (await srv.client.get("/api/health")).status_code == 200  # liveness unaffected
    finally:
        await srv.stop()


async def test_t23b_redis_unavailable(tmp_path):
    s = base_settings("sqlite-memory", tmp_path, broker_backend="redis", redis_url="redis://127.0.0.1:1/0")
    srv = await start_server(s)
    try:
        sid = (await srv.client.post("/api/sessions")).json()["id"]  # sync publish is best-effort
        r, _ = await srv.send(sid, "x")
        assert r.status_code == 503
        hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
        assert hist == []  # no user message inserted when the lock cannot be taken
        assert (await srv.client.get("/api/ready")).status_code == 503
    finally:
        await srv.stop()


# ------------------------------------------------------------------ multi-instance (T08)
@pg_redis
async def test_t08_two_instances_shared_stream_and_mutex(tmp_path, sse_collector):
    s = base_settings("postgres-redis", tmp_path)
    a = await start_server(s)
    b = await start_server(base_settings("postgres-redis", tmp_path), migrate=False)
    try:
        a.llm.gate = asyncio.Event()
        async with sse_collector(b.base_url, "/api/events") as g:
            await g.wait_event("ready")
            sid = await a.new_session()
            await g.wait_event("session.created")  # created on A, seen on B
            async with (
                sse_collector(b.base_url, f"/api/sessions/{sid}/stream") as sb,
                sse_collector(a.base_url, f"/api/sessions/{sid}/stream") as sa,
            ):
                await asyncio.sleep(0.3)
                r, rid = await a.send(sid, "跨实例")
                assert r.status_code == 202
                await sb.wait_for(lambda c: len(c.named("delta")) >= 1)
                r2, _ = await b.send(sid, "另一个请求")  # mutex holds across instances
                assert r2.status_code == 409
                r3, _ = await b.send(sid, "跨实例", rid=rid)  # idempotent across instances
                assert r3.status_code == 202 and r3.json()["request_id"] == rid
                a.llm.gate.set()
                await sb.wait_event("done", request_id=rid)
                await sa.wait_event("done", request_id=rid)
            assert [(e["id"], e["data"]) for e in sa.business()] == [
                (e["id"], e["data"]) for e in sb.business()
            ]
            hist = (await b.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
            assert [m["role"] for m in hist] == ["user", "assistant"]
    finally:
        await a.stop()
        await b.stop()


# ------------------------------------------------------------------ real process kill (T17)
@pg_redis
async def test_t17_kill_worker_process(tmp_path, sse_collector):
    s = base_settings("postgres-redis", tmp_path)
    b = await start_server(s)  # survivor instance (resets storage)
    port = free_port()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("DEEPSEEK")},
        "APP_PROFILE": "test",
        "DATABASE_URL": PG_URL,
        "BROKER_BACKEND": "redis",
        "REDIS_URL": REDIS_URL,
        "LLM_PROVIDER": "fake",
        "SEARCH_PROVIDER": "fake",
        "FAKE_LLM_DELAY_MS": "100",
        "FAKE_LLM_CHUNKS": "500",
        "WORKER_HEARTBEAT_SECONDS": "0.5",
        "LOCK_TTL_SECONDS": "3",
        "WORKER_STALE_SECONDS": "3",
        "TITLE_GENERATION_ENABLED": "false",
        "LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "app.main:app_factory", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import httpx

        a_url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=a_url, timeout=10) as ca:
            for _ in range(100):
                try:
                    if (await ca.get("/api/ready")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            sid = (await ca.post("/api/sessions")).json()["id"]
            rid = str(uuid.uuid4())
            async with sse_collector(b.base_url, f"/api/sessions/{sid}/stream") as c:
                await asyncio.sleep(0.3)
                r = await ca.post(
                    f"/api/sessions/{sid}/messages",
                    json={"content": "kill me", "thinking": False, "web_search": False, "request_id": rid},
                )
                assert r.status_code == 202
                await c.wait_for(lambda c: len(c.named("delta")) >= 5, timeout=15)
                proc.send_signal(signal.SIGKILL)
                proc.wait(5)
                n_before = len(c.named("delta"))
                await c.wait_event("error", request_id=rid, timeout=20)
        assert c.named("error")[0]["json"]["code"] == "GENERATION_INTERRUPTED"
        assert n_before >= 5
        # a refreshed page on the survivor can still replay what was written before the kill
        run = (await b.client.get(f"/api/sessions/{sid}/runs/{rid}")).json()
        assert run["status"] == "interrupted"
        async with sse_collector(
            b.base_url, f"/api/sessions/{sid}/stream?after={run['start_event_id']}"
        ) as d:
            await d.wait_event("error", request_id=rid)
        assert len(d.named("delta")) >= n_before
        hist = (await b.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
        assert [m["role"] for m in hist] == ["user"]  # no auto re-run
        assert (await b.wait_idle(sid))["status"] == "idle"
    finally:
        if proc.poll() is None:
            proc.kill()
        await b.stop()
