from __future__ import annotations

from app.infra.search.base import SearchError, SearchResult


class FakeSearchProvider:
    name = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail: SearchError | None = None
        self.results: list[SearchResult] = [
            SearchResult(
                title="示例来源一",
                url="https://example.com/a",
                snippet="片段A",
                summary="摘要A：今天是晴天。",
            ),
            SearchResult(title="示例来源二", url="https://example.org/b", snippet="片段B", summary="摘要B"),
        ]

    async def search(self, query, *, count, ori_question=None):
        self.queries.append(query)
        if self.fail:
            raise self.fail
        return self.results[:count]

    async def close(self) -> None:
        return None


class DisabledSearchProvider:
    name = "disabled"

    async def search(self, query, *, count, ori_question=None):
        raise SearchError("search provider disabled", code="SEARCH_DISABLED")

    async def close(self) -> None:
        return None
