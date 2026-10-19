import asyncio
import json
import os
import subprocess
import time
import uuid

import httpx

B = os.environ.get("BASE", "http://127.0.0.1:9123")


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        sid = (await c.post(B + "/api/sessions", json={})).json()["id"]
        r = await c.post(
            f"{B}/api/sessions/{sid}/messages",
            json={
                "request_id": str(uuid.uuid4()),
                "content": "写一篇1500字的关于长江的散文。",
                "thinking": False,
                "web_search": False,
            },
        )
        ip = r.headers["x-upstream"].split(":")[0]
        rid = r.json().get("request_id") or r.json().get("run", {}).get("request_id")
        await asyncio.sleep(3)
        names = subprocess.check_output(
            ["sudo", "docker", "ps", "--filter", "name=ai-chat-backend", "--format", "{{.Names}}"], text=True
        ).split()
        victim = [
            n
            for n in names
            if ip
            in subprocess.check_output(
                [
                    "sudo",
                    "docker",
                    "inspect",
                    "-f",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    n,
                ],
                text=True,
            )
        ][0]
        subprocess.run(["sudo", "docker", "kill", "-s", "KILL", victim], check=True)
        t0 = time.time()
        print("killed", victim, "at", ip)
        while time.time() - t0 < 120:
            a = (await c.get(f"{B}/api/sessions/{sid}/active-run")).json()
            if not a or not a.get("run") and not a.get("request_id"):
                break
            await asyncio.sleep(2)
        msgs = (await c.get(f"{B}/api/sessions/{sid}/messages")).json()["items"]
        items = msgs["items"] if isinstance(msgs, dict) else msgs
        print(
            json.dumps(
                {
                    "seconds_to_recover": round(time.time() - t0, 1),
                    "last_status": items[-1].get("status"),
                    "last_role": items[-1]["role"],
                    "partial_chars": len(items[-1]["content"]),
                },
                ensure_ascii=False,
            )
        )
        # new message should be accepted after recovery
        r = await c.post(
            f"{B}/api/sessions/{sid}/messages",
            json={
                "request_id": str(uuid.uuid4()),
                "content": "一句话总结。",
                "thinking": False,
                "web_search": False,
            },
        )
        print("after-recovery send:", r.status_code)


asyncio.run(main())
