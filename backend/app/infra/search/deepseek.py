"""DeepSeek standalone Search API (POST /v1/tools/search, launched 2026-10-17).

Docs: https://api-docs.deepseek.com/guides/search/
"""

from __future__ import annotations

import httpx

from app.infra.search.base import SearchError, SearchResult


class DeepSeekSearchProvider:
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def search(self, query: str, *, count: int, ori_question: str | None = None) -> list[SearchResult]:
        if not self._api_key:
            raise SearchError("DEEPSEEK_API_KEY 未配置", code="SEARCH_NOT_CONFIGURED")
        body: dict = {"query": query, "count": count, "need_content": False}
        if ori_question:
            body["ori_question"] = ori_question[:128]
        try:
            resp = await self._client.post(
                "/v1/tools/search",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            )
        except httpx.TimeoutException as e:
            raise SearchError("search timeout", code="SEARCH_TIMEOUT") from e
        except httpx.HTTPError as e:
            raise SearchError(f"search transport error: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise SearchError(f"search HTTP {resp.status_code}", status=resp.status_code)
        results = []
        for item in (resp.json().get("results") or [])[:count]:
            if not item.get("url"):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or item["url"]),
                    url=str(item["url"]),
                    snippet=str(item.get("snippet") or ""),
                    summary=str(item.get("summary") or ""),
                    content=str(item.get("content") or ""),
                    site_name=item.get("site_name"),
                    site_icon=item.get("site_icon"),
                    date=item.get("date"),
                    rerank_score=item.get("rerank_score"),
                    authority=item.get("authority"),
                )
            )
        return results

    async def close(self) -> None:
        await self._client.aclose()
