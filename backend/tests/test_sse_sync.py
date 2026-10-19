"""SSE protocol (T07, T12, T13, T15), global sync channel, search, titles, validation."""

import asyncio
import uuid

from app.core.stream_ids import compare_ids


async def test_t07_two_windows_same_sequence_independent_cursors(srv, sse_collector):
    sid = await srv.new_session()
    async with (
        sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as a,
        sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as b,
    ):
        await asyncio.sleep(0.2)
        for i in range(2):
            _, rid = await srv.send(sid, f"第{i}轮")
            await a.wait_event("done", request_id=rid)
            await b.wait_event("done", request_id=rid)
            await srv.wait_idle(sid)
    seq_a = [(e["id"], e["event"], e["data"]) for e in a.business()]
    seq_b = [(e["id"], e["event"], e["data"]) for e in b.business()]
    assert seq_a == seq_b and len(seq_a) > 4
    ids = [x[0] for x in seq_a]
    assert all(compare_ids(ids[i], ids[i + 1]) < 0 for i in range(len(ids) - 1))


async def test_last_event_id_reconnect_no_duplicates(srv, sse_collector):
    srv.llm.gate = asyncio.Event()
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as a:
        _, rid = await srv.send(sid, "abcdefghijkl")
        await a.wait_for(lambda c: len(c.named("delta")) >= 2)
    last = a.business()[-1]["id"]
    seen = [e["id"] for e in a.business()]
    # browser auto-reconnect: Last-Event-ID header wins over an older ?after
    async with sse_collector(
        srv.base_url, f"/api/sessions/{sid}/stream?after=0-0", headers={"Last-Event-ID": last}
    ) as b:
        srv.llm.gate.set()
        await b.wait_event("done", request_id=rid)
    again = [e["id"] for e in b.business()]
    assert not set(seen) & set(again)
    assert compare_ids(again[0], last) > 0


async def test_t12_old_cursor_triggers_resync(srv, sse_collector):
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        for i in range(3):
            _, rid = await srv.send(sid, f"r{i}")
            await c.wait_event("done", request_id=rid)
            await srv.wait_idle(sid)
    first_ever = c.business()[0]["id"]
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream?after={first_ever}") as d:
        await d.wait_event("resync")
    assert d.named("resync")[0]["json"]["reason"] == "CURSOR_TOO_OLD"
    assert "id" not in d.named("resync")[0]


async def test_t13_invalid_cursor(srv):
    sid = await srv.new_session()
    for bad in ["abc", "-1-0", "1-2-3"]:
        r = await srv.client.get(f"/api/sessions/{sid}/stream", params={"after": bad})
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = await srv.client.get(f"/api/sessions/{sid}/stream", headers={"Last-Event-ID": "zz"})
    assert r.status_code == 400
    r = await srv.client.get("/api/sessions/nope/stream")
    assert r.status_code == 404 and r.json()["error"]["code"] == "SESSION_NOT_FOUND"
    r = await srv.client.get("/api/events", params={"after": "x"})
    assert r.status_code == 400


async def test_t15_ping_has_no_id(srv, sse_collector):
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        await c.wait_event("ping", n=2, timeout=5)
    pings = c.named("ping")
    assert all("id" not in p for p in pings)
    assert not c.business()


async def test_sse_headers(srv):
    sid = await srv.new_session()
    async with srv.client.stream("GET", f"/api/sessions/{sid}/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in r.headers["cache-control"]
        assert r.headers["x-accel-buffering"] == "no"


async def test_sse_disconnect_cleans_up(srv, sse_collector):
    from app.core.metrics import SSE_CONNECTIONS

    sid = await srv.new_session()
    g = SSE_CONNECTIONS.labels("session")
    before = g._value.get()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        await c.wait_for(lambda c: c.status == 200)
        await asyncio.sleep(0.3)
        assert g._value.get() == before + 1
    for _ in range(50):
        if g._value.get() == before:
            break
        await asyncio.sleep(0.1)
    assert g._value.get() == before  # FR-E05: loop terminated, no leaked coroutine


# ------------------------------------------------------------------ global sync (T26, T27)
async def test_global_sync_created_renamed_title_deleted(srv, sse_collector):
    async with sse_collector(srv.base_url, "/api/events") as g:
        await g.wait_event("ready")
        sid = await srv.new_session()
        await g.wait_event("session.created")
        _, rid = await srv.send(sid, "帮我规划一次杭州旅行")
        await srv.wait_idle(sid)
        await g.wait_for(
            lambda c: any(e["json"]["session"]["title_source"] == "auto" for e in c.named("session.updated"))
        )
        await srv.client.patch(f"/api/sessions/{sid}", json={"title": "我的旅行"})
        await g.wait_for(
            lambda c: any(e["json"]["session"]["title"] == "我的旅行" for e in c.named("session.updated"))
        )
        await asyncio.sleep(0.1)
        await srv.client.delete(f"/api/sessions/{sid}")
        await g.wait_event("session.deleted")
    created = g.named("session.created")[0]["json"]["session"]
    assert created["id"] == sid and created["title"] == "新对话"
    statuses = [e["json"]["session"]["status"] for e in g.named("session.updated")]
    assert "generating" in statuses and "idle" in statuses
    versions = [e["json"]["session"]["version"] for e in g.named("session.updated")]
    assert versions == sorted(versions)
    deleted = g.named("session.deleted")[0]["json"]
    assert deleted["id"] == sid and deleted["version"] > versions[-1]
    assert "id" not in g.named("ready")[0]


async def test_global_sync_reconnect_with_last_event_id(srv, sse_collector):
    async with sse_collector(srv.base_url, "/api/events") as g:
        await g.wait_event("ready")
        await srv.new_session()
        await g.wait_event("session.created")
    last = g.business()[-1]["id"]
    s2 = await srv.new_session()  # happens while "offline"
    async with sse_collector(srv.base_url, "/api/events", headers={"Last-Event-ID": last}) as g2:
        await g2.wait_event("session.created")
    assert g2.named("session.created")[0]["json"]["session"]["id"] == s2
    assert len(g2.named("session.created")) == 1


# ------------------------------------------------------------------ titles (T28, T29)
async def test_title_generated_after_first_turn_only(srv):
    srv.llm.title = '标题："杭州旅行规划"。'
    sid = await srv.new_session()
    await srv.send(sid, "帮我规划杭州三日游")
    await srv.wait_idle(sid)
    for _ in range(50):
        s = (await srv.client.get(f"/api/sessions/{sid}")).json()
        if s["title_source"] == "auto":
            break
        await asyncio.sleep(0.05)
    assert s["title"] == "杭州旅行规划" and s["title_source"] == "auto"
    srv.llm.title = "另一个标题"
    await srv.send(sid, "第二个问题")
    await srv.wait_idle(sid)
    await asyncio.sleep(0.3)
    assert (await srv.client.get(f"/api/sessions/{sid}")).json()["title"] == "杭州旅行规划"
    title_calls = [r for r in srv.llm.requests if r.max_tokens == 40]
    assert len(title_calls) == 1 and title_calls[0].thinking is False


async def test_title_fallback_and_user_title_preserved(srv):
    srv.llm.title_fail = True
    sid = await srv.new_session()
    await srv.send(sid, "这是一个用于测试标题回退逻辑的非常长的用户问题内容")
    await srv.wait_idle(sid)
    for _ in range(50):
        s = (await srv.client.get(f"/api/sessions/{sid}")).json()
        if s["title_source"] == "auto":
            break
        await asyncio.sleep(0.05)
    assert s["title"].startswith("这是一个用于测试") and len(s["title"]) <= 24
    r = await srv.client.post("/api/sessions", json={"title": "手动标题"})
    sid2 = r.json()["id"]
    assert r.json()["title_source"] == "user"
    srv.llm.title_fail = False
    await srv.send(sid2, "问题")
    await srv.wait_idle(sid2)
    await asyncio.sleep(0.3)
    assert (await srv.client.get(f"/api/sessions/{sid2}")).json()["title"] == "手动标题"


# ------------------------------------------------------------------ list & search (T30, T31, T33)
async def test_list_order_and_pagination(srv):
    ids = [await srv.new_session() for _ in range(5)]
    r = (await srv.client.get("/api/sessions", params={"limit": 2})).json()
    assert [i["id"] for i in r["items"]] == ids[::-1][:2] and r["next_cursor"]
    r2 = (await srv.client.get("/api/sessions", params={"limit": 10, "cursor": r["next_cursor"]})).json()
    assert [i["id"] for i in r2["items"]] == ids[::-1][2:]
    # activity bumps updated_at → moves to top
    await srv.send(ids[0], "hello")
    await srv.wait_idle(ids[0])
    top = (await srv.client.get("/api/sessions")).json()["items"][0]
    assert top["id"] == ids[0]


async def test_search_title_and_content(srv):
    a = (await srv.client.post("/api/sessions", json={"title": "Python 异步教程"})).json()["id"]
    b = await srv.new_session()
    await srv.send(b, "请解释 Kubernetes 的 Pod 调度")
    await srv.wait_idle(b)
    await asyncio.sleep(0.2)
    c = (await srv.client.post("/api/sessions", json={"title": "100% 完成率"})).json()["id"]

    r = (await srv.client.get("/api/sessions", params={"q": "python"})).json()["items"]
    assert [i["id"] for i in r] == [a] and r[0]["match"]["field"] == "title"  # case-insensitive
    r = (await srv.client.get("/api/sessions", params={"q": "kubernetes"})).json()["items"]
    assert [i["id"] for i in r] == [b] and r[0]["match"]["field"] == "message"
    assert "Kubernetes" in r[0]["match"]["snippet"] and r[0]["match"]["message_id"]
    r = (await srv.client.get("/api/sessions", params={"q": "%"})).json()["items"]
    assert [i["id"] for i in r] == [c]  # LIKE wildcards are escaped
    r = (await srv.client.get("/api/sessions", params={"q": "不存在的词"})).json()["items"]
    assert r == []
    assert (await srv.client.get("/api/sessions", params={"q": "x" * 101})).status_code == 400


async def test_search_ignores_reasoning(srv):
    srv.llm.reasoning_fn = lambda req: "zebra-secret-thought"
    sid = await srv.new_session()
    await srv.send(sid, "hello", thinking=True)
    await srv.wait_idle(sid)
    r = (await srv.client.get("/api/sessions", params={"q": "zebra"})).json()["items"]
    assert r == []


# ------------------------------------------------------------------ request validation
async def test_request_validation(srv):
    sid = await srv.new_session()
    url = f"/api/sessions/{sid}/messages"
    rid = str(uuid.uuid4())
    bad_bodies = [
        {"content": "  ", "thinking": False, "web_search": False, "request_id": rid},
        {"content": "x" * 32001, "thinking": False, "web_search": False, "request_id": rid},
        {"content": "x", "thinking": "true", "web_search": False, "request_id": rid},
        {"content": "x", "thinking": False, "web_search": False, "request_id": "not-a-uuid"},
        {"content": "x", "thinking": False, "web_search": False, "request_id": rid, "extra": 1},
        {"thinking": False, "web_search": False, "request_id": rid},
    ]
    for body in bad_bodies:
        r = await srv.client.post(url, json=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST", body
    r = await srv.client.post(
        "/api/sessions/missing/messages",
        json={"content": "x", "thinking": False, "web_search": False, "request_id": rid},
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert (await srv.client.get(url)).json() == {"items": [], "oldest_sequence_no": None, "newest_sequence_no": None, "has_more_before": False}


async def test_health_ready_metrics(srv):
    assert (await srv.client.get("/api/health")).json()["status"] == "ok"
    r = await srv.client.get("/api/ready")
    assert r.status_code == 200 and r.json()["checks"] == {"database": "ok", "broker": "ok"}
    m = await srv.client.get("/api/metrics")
    assert "aichat_sse_connections" in m.text
