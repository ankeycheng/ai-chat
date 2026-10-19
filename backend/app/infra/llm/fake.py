"""Deterministic offline LLM for tests / offline dev (LLM_PROVIDER=fake).

Behaviour can be scripted via ``FakeChatClient.script`` (tests) or environment
variables FAKE_LLM_DELAY_MS / FAKE_LLM_CHUNKS (subprocess crash tests).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable

from app.infra.llm.base import ChatDelta, ChatRequest, LLMError


class FakeChatClient:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []
        self.delay_ms = int(os.getenv("FAKE_LLM_DELAY_MS", "5"))
        self.chunks = int(os.getenv("FAKE_LLM_CHUNKS", "0"))  # 0 → derive from reply
        self.fail_with: LLMError | None = None
        self.fail_after: int | None = None
        self.hang = False
        self.reply_fn: Callable[[ChatRequest], str] | None = None
        self.reasoning_fn: Callable[[ChatRequest], str] | None = None
        self.title = "测试标题"
        self.title_fail = False
        self.finish_reason = "stop"
        self.leak_reasoning = False  # emit reasoning even when thinking is disabled (FR-T02 test)
        self.gate: asyncio.Event | None = None  # when set, stream waits for gate before finishing

    def _reply(self, req: ChatRequest) -> str:
        if self.reply_fn:
            return self.reply_fn(req)
        last = next((m["content"] for m in reversed(req.messages) if m["role"] == "user"), "")
        return f"收到：{last[:200]}"

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        self.requests.append(req)
        if self.fail_with and self.fail_after is None:
            raise self.fail_with
        if req.thinking or self.leak_reasoning:
            reasoning = self.reasoning_fn(req) if self.reasoning_fn else "思考中……"
            for ch in _split(reasoning, 3):
                await asyncio.sleep(self.delay_ms / 1000)
                yield ChatDelta(kind="reasoning", text=ch)
        text = self._reply(req)
        pieces = [f"tok{i} " for i in range(self.chunks)] if self.chunks else _split(text, 4)
        for i, piece in enumerate(pieces):
            if self.fail_with and self.fail_after is not None and i >= self.fail_after:
                raise self.fail_with
            while self.hang:
                await asyncio.sleep(0.05)
            if self.delay_ms:
                await asyncio.sleep(self.delay_ms / 1000)
            yield ChatDelta(kind="content", text=piece)
        if self.gate is not None:
            await self.gate.wait()
        yield ChatDelta(
            finish_reason=self.finish_reason, usage={"prompt_tokens": 1, "completion_tokens": len(pieces)}
        )

    async def complete(self, req: ChatRequest) -> str:
        self.requests.append(req)
        if self.title_fail:
            raise LLMError("title failure", status=500)
        return self.title

    async def close(self) -> None:
        return None


def _split(text: str, n: int) -> list[str]:
    return [text[i : i + n] for i in range(0, len(text), n)] or [""]
