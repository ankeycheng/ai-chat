"""Pluggable SearchProvider (FR-W01)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    summary: str = ""
    content: str = ""
    site_name: str | None = None
    site_icon: str | None = None
    date: str | None = None
    rerank_score: float | None = None
    authority: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class SearchError(Exception):
    def __init__(self, message: str, *, status: int | None = None, code: str = "SEARCH_FAILED") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, *, count: int, ori_question: str | None = None
    ) -> list[SearchResult]: ...

    async def close(self) -> None: ...
