"""DeepSeek Chat Completions adapter (httpx streaming, OpenAI-compatible SSE)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.infra.llm.base import ChatDelta, ChatRequest, LLMError


def build_payload(req: ChatRequest, stream: bool) -> dict:
    """Request body per api-docs.deepseek.com (thinking toggle §5.3)."""
    body: dict = {
        "model": req.model,
        "messages": req.messages,
        "max_tokens": req.max_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    if req.thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = req.reasoning_effort
        # temperature/top_p/penalties have no effect in thinking mode → never sent
    else:
        body["thinking"] = {"type": "disabled"}
        if req.temperature is not None:
            body["temperature"] = req.temperature
    body.update(req.extra)
    return body


class DeepSeekChatClient:
    def __init__(self, api_key: str, base_url: str, connect_timeout: float, read_timeout: float) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=connect_timeout, read=read_timeout, write=30, pool=10),
            headers={"Content-Type": "application/json"},
        )

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise LLMError("DEEPSEEK_API_KEY 未配置", status=None)
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _error_from_response(status: int, body: bytes) -> LLMError:
        msg = f"DeepSeek HTTP {status}"
        try:
            detail = json.loads(body).get("error", {}).get("message")
            if detail:
                msg += f": {str(detail)[:200]}"
        except Exception:  # noqa: BLE001
            pass
        return LLMError(msg, status=status, retryable=status in (408, 429) or status >= 500)

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        payload = build_payload(req, stream=True)
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload, headers=self._headers()
            ) as resp:
                if resp.status_code != 200:
                    raise self._error_from_response(resp.status_code, await resp.aread())
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue  # keep-alive comments
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        rc = delta.get("reasoning_content")
                        if rc:
                            yield ChatDelta(kind="reasoning", text=rc)
                        c = delta.get("content")
                        if c:
                            yield ChatDelta(kind="content", text=c)
                        if choice.get("finish_reason"):
                            yield ChatDelta(finish_reason=choice["finish_reason"], usage=usage)
        except httpx.TimeoutException as e:
            raise LLMError(f"DeepSeek timeout: {type(e).__name__}", retryable=True) from e
        except httpx.HTTPError as e:
            raise LLMError(f"DeepSeek transport error: {type(e).__name__}", retryable=True) from e

    async def complete(self, req: ChatRequest) -> str:
        payload = build_payload(req, stream=False)
        try:
            resp = await self._client.post("/chat/completions", json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(f"DeepSeek transport error: {type(e).__name__}", retryable=True) from e
        if resp.status_code != 200:
            raise self._error_from_response(resp.status_code, resp.content)
        data = resp.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    async def close(self) -> None:
        await self._client.aclose()
