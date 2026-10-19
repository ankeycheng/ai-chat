import asyncio
import json
import os
import uuid

import httpx

B = os.environ.get("BASE", "http://127.0.0.1:8080")


async def sse(c, url, out, headers=None, stop=None):
    async with c.stream("GET", url, headers=headers or {}) as r:
        out.append(("upstream", r.headers.get("x-upstream")))
        ev = {}
        async for line in r.aiter_lines():
            if line.startswith("id:"):
                ev["id"] = line[3:].strip()
            elif line.startswith("event:"):
                ev["event"] = line[6:].strip()
            elif line.startswith("data:"):
                ev["data"] = line[5:].strip()
            elif line == "" and ev:
                out.append(ev)
                if stop and stop(ev):
                    return
                ev = {}


async def main():
    res = {}
    async with httpx.AsyncClient(timeout=60) as c:
        g = []
        t = asyncio.create_task(sse(c, B + "/api/events", g))
        await asyncio.sleep(1)
        ups = set()
        for i in range(4):
            r = await c.post(B + "/api/sessions", json={})
            ups.add(r.headers["x-upstream"])
            sid = r.json()["id"] if "id" in r.json() else r.json()["session"]["id"]
        await asyncio.sleep(1.5)
        t.cancel()
        created = [e for e in g if isinstance(e, dict) and e.get("event") == "session.created"]
        res["global_sse_upstream"] = g[0][1]
        res["create_upstreams"] = sorted(ups)
        res["global_created_events"] = len(created)
        # stream: send message, read first half, reconnect with Last-Event-ID (likely other replica)
        r = await c.post(
            f"{B}/api/sessions/{sid}/messages",
            json={
                "request_id": str(uuid.uuid4()),
                "content": "用三句话介绍长城。",
                "thinking": False,
                "web_search": False,
            },
        )
        res["send_status"] = r.status_code
        res["send_upstream"] = r.headers["x-upstream"]
        a = []
        n = [0]

        def stop5(ev):
            if ev.get("event") == "delta":
                n[0] += 1
            return n[0] >= 5 or ev.get("event") in ("done", "error")

        await sse(c, f"{B}/api/sessions/{sid}/stream", a, stop=stop5)
        last = [e for e in a if isinstance(e, dict) and "id" in e][-1]["id"]
        b = []
        await sse(
            c,
            f"{B}/api/sessions/{sid}/stream",
            b,
            headers={"Last-Event-ID": last},
            stop=lambda e: e.get("event") in ("done", "error"),
        )
        res["stream1_upstream"] = a[0][1]
        res["stream2_upstream"] = b[0][1]
        txt = "".join(
            json.loads(e["data"])["text"]
            for e in a[1:] + b[1:]
            if isinstance(e, dict)
            and e.get("event") == "delta"
            and json.loads(e["data"])["kind"] == "content"
        )
        ids = [e["id"] for e in a[1:] + b[1:] if isinstance(e, dict) and "id" in e]
        res["no_dup_ids"] = len(ids) == len(set(ids))
        res["terminal"] = b[-1].get("event")
        await asyncio.sleep(1)
        m = (await c.get(f"{B}/api/sessions/{sid}/messages")).json()["items"]
        items = m["items"] if isinstance(m, dict) else m
        res["persisted_equals_streamed"] = items[-1]["content"] == txt
        res["chars"] = len(txt)
    print(json.dumps(res, ensure_ascii=False, indent=1))


asyncio.run(main())
