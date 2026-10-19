"""Generation flow integration tests (T01–T06, T09–T11, T16, T20, T21, §5.9 history context)."""

import asyncio
import uuid

from app.infra.broker.base import session_stream_key
from app.infra.llm.base import LLMError
from app.infra.search.base import SearchError


async def test_t01_basic_stream_done_after_persist(srv, sse_collector):
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        await asyncio.sleep(0.2)
        r, rid = await srv.send(sid, "你好，世界")
        assert r.status_code == 202 and r.json()["accepted"] is True
        await c.wait_event("done", request_id=rid)
        # done ⇒ history already contains the full assistant message (FR-M05/C3)
        hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    names = [e["event"] for e in c.business()]
    assert names[0] == "start" and names[-1] == "done" and "delta" in names
    deltas = "".join(e["json"]["text"] for e in c.named("delta") if e["json"]["kind"] == "content")
    assistant = [m for m in hist if m["role"] == "assistant"]
    assert len(assistant) == 1 and assistant[0]["content"] == deltas
    done_payload = c.named("done")[0]["json"]
    assert done_payload["message_id"] == assistant[0]["id"]
    assert "message" in done_payload
    assert done_payload["message"]["id"] == assistant[0]["id"]
    assert done_payload["message"]["content"] == deltas
    assert done_payload["message"]["sequence_no"] == assistant[0]["sequence_no"]
    assert done_payload["message"]["role"] == "assistant"
    assert [m["sequence_no"] for m in hist] == [1, 2]
    start = c.named("start")[0]["json"]
    assert start["user_message"]["content"] == "你好，世界"  # other windows can render the user turn
    ids = [e["id"] for e in c.business()]
    assert len(set(ids)) == len(ids)


async def test_t02_t03_thinking_toggle(srv, sse_collector):
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        for _ in range(2):
            _, rid = await srv.send(sid, "q", thinking=True)
            await c.wait_event("done", request_id=rid)
            await srv.wait_idle(sid)
        for _ in range(2):
            _, rid = await srv.send(sid, "q2", thinking=False)
            await c.wait_event("done", request_id=rid)
            await srv.wait_idle(sid)
    reqs = srv.llm.requests
    stream_reqs = [r for r in reqs if r.max_tokens != 40]
    assert [r.thinking for r in stream_reqs] == [True, True, False, False]
    by_rid = {}
    for e in c.named("delta"):
        by_rid.setdefault(e["json"]["request_id"], set()).add(e["json"]["kind"])
    kinds = list(by_rid.values())
    assert "reasoning" in kinds[0] and "reasoning" not in kinds[-1]
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert hist[1]["reasoning_content"] and hist[-1]["reasoning_content"] == ""
    # reasoning is never sent back as input (§5.9)
    assert all("reasoning_content" not in m for r in stream_reqs for m in r.messages)


async def test_t03b_unexpected_reasoning_dropped_when_thinking_off(srv, sse_collector):
    srv.llm.leak_reasoning = True
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        _, rid = await srv.send(sid, "q", thinking=False)
        await c.wait_event("done", request_id=rid)
    assert {e["json"]["kind"] for e in c.named("delta")} == {"content"}
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert hist[-1]["reasoning_content"] == ""


async def test_t04_web_search_success(srv, sse_collector):
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        _, rid = await srv.send(sid, "今天天气怎么样", web_search=True)
        await c.wait_event("done", request_id=rid)
    assert srv.search.queries == ["今天天气怎么样"]
    src = c.named("sources")[0]["json"]["sources"]
    assert src[0]["id"] == "S1" and src[0]["url"].startswith("https://")
    req = srv.llm.requests[0]
    sys_blocks = [m["content"] for m in req.messages if m["role"] == "system"]
    assert len(sys_blocks) == 2 and "[S1]" in sys_blocks[1] and "https://example.com/a" in sys_blocks[1]
    assert req.messages[-1] == {"role": "user", "content": "今天天气怎么样"}
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert hist[1]["sources"][0]["title"] == "示例来源一"


async def test_t05_web_search_failure_degrades(srv, sse_collector):
    srv.search.fail = SearchError("boom", status=500)
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        _, rid = await srv.send(sid, "搜索一下", web_search=True)
        await c.wait_event("done", request_id=rid)
    notices = c.named("notice")
    assert notices and notices[0]["json"]["code"] == "SEARCH_UNAVAILABLE"
    assert not c.named("error")
    assert [m["role"] for m in srv.llm.requests[0].messages] == ["system", "user"]


async def test_t06_refresh_replays_from_start_event(srv, sse_collector):
    srv.llm.gate = asyncio.Event()
    sid = await srv.new_session()
    _, rid = await srv.send(sid, "长回答", thinking=True)
    await asyncio.sleep(0.5)  # deltas written, run still active (gate closed)
    ar = (await srv.client.get(f"/api/sessions/{sid}/active-run")).json()
    assert ar["active"] and ar["request_id"] == rid and ar["start_event_id"]
    # "new page": subscribe from the active run's start_event_id (exclusive → no start)
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream?after={ar['start_event_id']}") as c:
        await c.wait_for(lambda c: len(c.named("delta")) >= 3)
        srv.llm.gate.set()
        await c.wait_event("done", request_id=rid)
    assert not c.named("start")
    text = "".join(e["json"]["text"] for e in c.named("delta") if e["json"]["kind"] == "content")
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert hist[-1]["content"] == text


async def test_t06b_default_cursor_uses_active_run_start(srv, sse_collector):
    srv.llm.gate = asyncio.Event()
    sid = await srv.new_session()
    _, rid = await srv.send(sid, "abc")
    await asyncio.sleep(0.4)
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:  # no after / Last-Event-ID
        await c.wait_for(lambda c: len(c.named("delta")) >= 1)
        srv.llm.gate.set()
        await c.wait_event("done", request_id=rid)
    assert not c.named("start") and c.named("delta")


async def test_t09_concurrent_duplicate_is_idempotent(srv):
    sid = await srv.new_session()
    rid = str(uuid.uuid4())
    body = {"content": "same", "thinking": False, "web_search": False, "request_id": rid}
    rs = await asyncio.gather(
        *[srv.client.post(f"/api/sessions/{sid}/messages", json=body) for _ in range(5)]
    )
    assert [r.status_code for r in rs] == [202] * 5, [r.text for r in rs]
    assert len({r.json()["user_message_id"] for r in rs}) == 1
    await srv.wait_idle(sid)
    r2 = await srv.client.post(f"/api/sessions/{sid}/messages", json=body)  # retry after completion
    assert r2.status_code == 202 and r2.json()["status"] == "completed"
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert len([r for r in srv.llm.requests if r.max_tokens != 40]) == 1  # model called once


async def test_t10_idempotency_key_reuse(srv):
    sid = await srv.new_session()
    r, rid = await srv.send(sid, "原内容")
    assert r.status_code == 202
    await srv.wait_idle(sid)
    r2, _ = await srv.send(sid, "改过的内容", rid=rid)
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    r3, _ = await srv.send(sid, "原内容", rid=rid, thinking=True)
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_t11_same_session_mutex(srv):
    srv.llm.gate = asyncio.Event()
    sid = await srv.new_session()
    r1, _ = await srv.send(sid, "第一轮")
    r2, rid2 = await srv.send(sid, "第二轮")
    assert r1.status_code == 202
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "GENERATION_IN_PROGRESS"
    srv.llm.gate.set()
    await srv.wait_idle(sid)
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert "第二轮" not in [m["content"] for m in hist]
    run = await srv.client.get(f"/api/sessions/{sid}/runs/{rid2}")
    assert run.status_code == 404 and run.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_t11b_parallel_different_requests_only_one_wins(srv):
    srv.llm.gate = asyncio.Event()
    sid = await srv.new_session()
    rs = await asyncio.gather(*[srv.send(sid, f"并发{i}") for i in range(6)])
    codes = sorted(r.status_code for r, _ in rs)
    assert codes.count(202) == 1 and codes.count(409) == 5
    srv.llm.gate.set()
    await srv.wait_idle(sid)
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert [m["role"] for m in hist] == ["user", "assistant"]


async def test_t16_upstream_failure(srv, sse_collector):
    srv.llm.fail_with = LLMError("HTTP 502", status=502)
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        _, rid = await srv.send(sid, "会失败")
        await c.wait_event("error", request_id=rid)
    err = c.named("error")[0]["json"]
    assert err["code"] == "UPSTREAM_ERROR" and "502" not in err["message"] and "sk-" not in err["message"]
    await srv.wait_idle(sid)
    run = (await srv.client.get(f"/api/sessions/{sid}/runs/{rid}")).json()
    assert run["status"] == "failed" and run["terminal_event_published"]
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert [m["role"] for m in hist] == ["user"]  # no empty assistant message
    s = await srv.wait_idle(sid)
    assert s["active_request_id"] is None
    srv.llm.fail_with = None
    r, _ = await srv.send(sid, "再试一次")  # UI can send again
    assert r.status_code == 202


async def test_t16b_midstream_failure_keeps_partial_events(srv, sse_collector):
    srv.llm.fail_with = LLMError("timeout", retryable=True)
    srv.llm.fail_after = 2
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        _, rid = await srv.send(sid, "abcdefghijklmnop")
        await c.wait_event("error", request_id=rid)
    assert len(c.named("delta")) == 2 and len(c.named("error")) == 1 and not c.named("done")


async def test_history_context_multi_turn_recall(srv):
    sid = await srv.new_session()
    await srv.send(sid, "我的名字是安琪")
    await srv.wait_idle(sid)
    srv.llm.fail_with = LLMError("x", status=500)
    await srv.send(sid, "这一轮会失败")
    await srv.wait_idle(sid)
    srv.llm.fail_with = None
    await srv.send(sid, "我叫什么名字？", thinking=True)
    await srv.wait_idle(sid)
    reqs = [r for r in srv.llm.requests if r.max_tokens != 40]
    last = reqs[-1].messages
    assert [m["role"] for m in last] == ["system", "user", "assistant", "user"]
    assert last[1]["content"] == "我的名字是安琪" and last[2]["content"].startswith("收到")
    assert "这一轮会失败" not in str(last)
    run = None
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "user", "assistant"]
    del run


async def test_t20_long_answer_not_trimmed_until_next_round(srv):
    srv.llm.chunks = 5200
    srv.llm.delay_ms = 0
    sid = await srv.new_session()
    await srv.send(sid, "超长回答")
    await srv.wait_idle(sid, timeout=120)
    key = session_stream_key(sid)
    n = await srv.container.broker.length(key)
    assert n >= 5202  # start + 5200 deltas + done, nothing trimmed
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assert hist[-1]["content"].count("tok") == 5200
    srv.llm.chunks = 3
    await srv.send(sid, "下一轮")
    await srv.wait_idle(sid)
    n2 = await srv.container.broker.length(key)
    assert n2 <= 1 + 1 + 3 + 1  # previous terminal kept + new start/deltas/done
    first = await srv.container.broker.first_id(key)
    s = (await srv.client.get(f"/api/sessions/{sid}")).json()
    assert first is not None and s["last_event_id"] != first


async def test_t21_delete_session(srv, sse_collector):
    srv.llm.gate = asyncio.Event()
    sid = await srv.new_session()
    await srv.send(sid, "生成中")
    await asyncio.sleep(0.2)
    r = await srv.client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "SESSION_BUSY"
    srv.llm.gate.set()
    await srv.wait_idle(sid)
    await asyncio.sleep(0.2)  # let async title generation finish
    r = await srv.client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 204
    assert (await srv.client.get(f"/api/sessions/{sid}/messages")).status_code == 404
    assert not await srv.container.broker.exists(session_stream_key(sid))
    assert (await srv.client.delete(f"/api/sessions/{sid}")).status_code == 404
