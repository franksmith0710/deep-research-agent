from __future__ import annotations

from duckduckgo_search import DDGS

from src.search.engine import SearchEngine, SearchResultItem


class DuckDuckGoSearch(SearchEngine):
    """封装 duckduckgo_search 包。"""
    
    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def search(self, query: str, num_results: int = 10) -> list[SearchResultItem]:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(keywords=query, max_results=num_results))
        except Exception as e:
            raise RuntimeError(f"DuckDuckGo 搜索失败: {e}")

        results: list[SearchResultItem] = []
        for r in raw:
            results.append(SearchResultItem(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            ))
        return results
