"""Message list pagination / incremental sync (after_sequence_no, before_sequence_no, limit)."""

import asyncio
import uuid

from app.core.time import utcnow
from app.models.entities import MessageRow
from app.repositories.messages import MessageRepository


async def _seed_messages(srv, sid: str, n: int) -> list[dict]:
    """Insert n user/assistant pairs directly (fast; no LLM). Returns all MessageOut-like dicts."""
    async with srv.container.sm() as db:
        repo = MessageRepository(db)
        now = utcnow()
        for i in range(n):
            rid = str(uuid.uuid4())
            seq_u = 2 * i + 1
            seq_a = 2 * i + 2
            await repo.add(
                MessageRow(
                    id=str(uuid.uuid4()),
                    session_id=sid,
                    sequence_no=seq_u,
                    role="user",
                    content=f"u{i}",
                    reasoning_content="",
                    sources_json="[]",
                    finish_reason=None,
                    request_id=rid,
                    created_at=now,
                )
            )
            await repo.add(
                MessageRow(
                    id=str(uuid.uuid4()),
                    session_id=sid,
                    sequence_no=seq_a,
                    role="assistant",
                    content=f"a{i}",
                    reasoning_content="",
                    sources_json="[]",
                    finish_reason="stop",
                    request_id=rid,
                    created_at=now,
                )
            )
        # bump session next_sequence_no
        from app.repositories.sessions import SessionRepository

        s = await SessionRepository(db).get(sid)
        s.next_sequence_no = 2 * n + 1
        await db.commit()
    return (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]


async def test_messages_after_sequence_no(srv):
    sid = await srv.new_session()
    all_msgs = await _seed_messages(srv, sid, 5)  # seq 1..10
    assert len(all_msgs) == 10
    body = (await srv.client.get(f"/api/sessions/{sid}/messages", params={"after_sequence_no": 5})).json()
    assert [m["sequence_no"] for m in body["items"]] == [6, 7, 8, 9, 10]
    assert body["oldest_sequence_no"] == 6
    assert body["newest_sequence_no"] == 10
    assert body["has_more_before"] is True


async def test_messages_limit_latest_window(srv):
    sid = await srv.new_session()
    await _seed_messages(srv, sid, 10)  # seq 1..20
    body = (await srv.client.get(f"/api/sessions/{sid}/messages", params={"limit": 6})).json()
    assert [m["sequence_no"] for m in body["items"]] == [15, 16, 17, 18, 19, 20]
    assert body["oldest_sequence_no"] == 15
    assert body["newest_sequence_no"] == 20
    assert body["has_more_before"] is True


async def test_messages_before_sequence_no_page(srv):
    sid = await srv.new_session()
    await _seed_messages(srv, sid, 8)  # seq 1..16
    body = (
        await srv.client.get(
            f"/api/sessions/{sid}/messages",
            params={"before_sequence_no": 15, "limit": 4},
        )
    ).json()
    assert [m["sequence_no"] for m in body["items"]] == [11, 12, 13, 14]
    assert body["has_more_before"] is True
    # oldest page
    body2 = (
        await srv.client.get(
            f"/api/sessions/{sid}/messages",
            params={"before_sequence_no": 11, "limit": 20},
        )
    ).json()
    assert [m["sequence_no"] for m in body2["items"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert body2["has_more_before"] is False


async def test_done_payload_includes_full_message(srv, sse_collector):
    sid = await srv.new_session()
    async with sse_collector(srv.base_url, f"/api/sessions/{sid}/stream") as c:
        await asyncio.sleep(0.15)
        _, rid = await srv.send(sid, "ping-done-message")
        await c.wait_event("done", request_id=rid)
    done = c.named("done")[0]["json"]
    hist = (await srv.client.get(f"/api/sessions/{sid}/messages")).json()["items"]
    assistant = next(m for m in hist if m["role"] == "assistant")
    assert done["message_id"] == assistant["id"]
    msg = done["message"]
    for key in ("id", "session_id", "sequence_no", "role", "content", "reasoning_content", "sources", "finish_reason", "request_id", "created_at"):
        assert key in msg
    assert msg["id"] == assistant["id"]
    assert msg["content"] == assistant["content"]
    assert msg["request_id"] == rid
