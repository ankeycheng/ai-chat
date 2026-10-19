"""End-to-end smoke test against a *running* server (dev server or nginx in docker).

Exercises the real DeepSeek upstream: streaming, thinking on, web search on,
multi-turn recall and auto title. Usage:

    uv run python scripts/e2e_live.py --base-url http://127.0.0.1:8000 [--report ../docs/e2e-report.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

import httpx


async def read_until_terminal(
    client: httpx.AsyncClient, sid: str, after: str, rid: str, timeout: float = 240
):
    events: list[dict] = []
    t0 = time.perf_counter()
    first_delta_at = None
    async with client.stream(
        "GET",
        f"/api/sessions/{sid}/stream",
        params={"after": after},
        timeout=httpx.Timeout(timeout, read=timeout),
    ) as resp:
        resp.raise_for_status()
        cur: dict = {}
        async for line in resp.aiter_lines():
            if line == "":
                if cur.get("event"):
                    cur["json"] = json.loads(cur.get("data") or "null") if cur.get("data") else None
                    events.append(cur)
                    if cur["event"] == "delta" and first_delta_at is None:
                        first_delta_at = time.perf_counter() - t0
                    if cur["event"] in ("done", "error") and (cur["json"] or {}).get("request_id") == rid:
                        break
                cur = {}
                continue
            k, _, v = line.partition(":")
            v = v[1:] if v.startswith(" ") else v
            if k in ("id", "event"):
                cur[k] = v
            elif k == "data":
                cur["data"] = cur.get("data", "") + v
    return events, first_delta_at


async def turn(client, sid, content, *, thinking=False, web_search=False):
    active = (await client.get(f"/api/sessions/{sid}/active-run")).json()
    after = active.get("last_event_id") or "0-0"
    rid = str(uuid.uuid4())
    t0 = time.perf_counter()
    r = await client.post(
        f"/api/sessions/{sid}/messages",
        json={"content": content, "thinking": thinking, "web_search": web_search, "request_id": rid},
    )
    accept_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 202, r.text
    events, first_delta = await read_until_terminal(client, sid, after, rid)
    deltas = [e["json"] for e in events if e["event"] == "delta"]
    content_text = "".join(d["text"] for d in deltas if d["kind"] == "content")
    reasoning_text = "".join(d["text"] for d in deltas if d["kind"] == "reasoning")
    terminal = events[-1]
    hist = (await client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    final = next((m for m in hist if m["role"] == "assistant" and m["request_id"] == rid), None)
    # wait for session to go idle (terminal finalisation)
    for _ in range(50):
        if (await client.get(f"/api/sessions/{sid}")).json()["status"] == "idle":
            break
        await asyncio.sleep(0.1)
    return {
        "request_id": rid,
        "prompt": content,
        "thinking": thinking,
        "web_search": web_search,
        "accept_ms": round(accept_ms, 1),
        "first_delta_s": round(first_delta or -1, 2),
        "terminal": terminal["event"],
        "terminal_data": terminal["json"],
        "n_delta": len(deltas),
        "content_chars": len(content_text),
        "reasoning_chars": len(reasoning_text),
        "notices": [e["json"] for e in events if e["event"] == "notice"],
        "sources": next((e["json"]["sources"] for e in events if e["event"] == "sources"), []),
        "persisted_matches_deltas": bool(
            final and final["content"] == content_text and final["reasoning_content"] == reasoning_text
        ),
        "answer_preview": content_text[:300],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--report")
    args = ap.parse_args()
    report: dict = {"base_url": args.base_url, "checks": {}}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        ready = (await client.get("/api/ready")).json()
        report["ready"] = ready
        sid = (await client.post("/api/sessions")).json()["id"]
        report["session_id"] = sid

        t1 = await turn(client, sid, "你好！请记住：我的名字叫安琪，我是一名 AI 开发工程师。请简短回复确认。")
        t2 = await turn(client, sid, "9.11 和 9.8 哪个大？简要回答。", thinking=True)
        t3 = await turn(client, sid, "DeepSeek 最近发布了什么新的 API 能力？请引用来源。", web_search=True)
        t4 = await turn(client, sid, "我叫什么名字？我的职业是什么？只回答这两个信息。")
        report["turns"] = [t1, t2, t3, t4]

        title = None
        for _ in range(60):
            s = (await client.get(f"/api/sessions/{sid}")).json()
            if s["title_source"] == "auto":
                title = s["title"]
                break
            await asyncio.sleep(0.5)
        report["title"] = title
        hits = (await client.get("/api/sessions", params={"q": "安琪"})).json()["items"]

    c = report["checks"]
    c["streamed_reply"] = t1["terminal"] == "done" and t1["n_delta"] > 1 and t1["persisted_matches_deltas"]
    c["thinking_on_has_reasoning"] = t2["terminal"] == "done" and t2["reasoning_chars"] > 0
    c["thinking_off_no_reasoning"] = t1["reasoning_chars"] == 0 and t4["reasoning_chars"] == 0
    c["web_search_sources"] = t3["terminal"] == "done" and len(t3["sources"]) > 0 and not t3["notices"]
    c["web_search_cited"] = "[S" in t3["answer_preview"] or "[S" in json.dumps(t3)
    c["multi_turn_recall"] = "安琪" in t4["answer_preview"]
    c["title_generated"] = bool(title)
    c["search_conversations"] = any(h["id"] == sid for h in hits)
    report["all_passed"] = all(c.values())
    out = json.dumps(report, ensure_ascii=False, indent=2)
    print(out)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(out)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
