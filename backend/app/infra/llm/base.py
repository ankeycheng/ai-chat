"""LLM client abstraction (infra adapter)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass
class ChatDelta:
    kind: Literal["reasoning", "content"] | None = None
    text: str = ""
    finish_reason: str | None = None
    usage: dict | None = None


@dataclass
class ChatRequest:
    messages: list[dict]
    thinking: bool
    max_tokens: int
    model: str
    reasoning_effort: str = "high"
    temperature: float | None = None
    extra: dict = field(default_factory=dict)


class LLMError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ChatClient(Protocol):
    def stream_chat(self, req: ChatRequest) -> AsyncIterator[ChatDelta]: ...

    async def complete(self, req: ChatRequest) -> str: ...

    async def close(self) -> None: ...
