from __future__ import annotations

import ddgs

from src.search.engine import SearchEngine, SearchResultItem
from src.logging_config import get_logger

logger = get_logger("search")


class DuckDuckGoSearch(SearchEngine):
    """封装 ddgs 包（DuckDuckGo 最新官方 SDK）。"""

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def search(self, query: str, num_results: int = 3) -> list[SearchResultItem]:
        logger.debug(f"DDGS search query='{query}' num={num_results}")
        try:
            with ddgs.DDGS(timeout=self._timeout) as d:
                raw = list(d.text(query, max_results=num_results))
        except Exception as e:
            logger.warning(f"DuckDuckGo search failed: {e}")
            raise RuntimeError(f"DuckDuckGo 搜索失败: {e}")

        results: list[SearchResultItem] = []
        for r in raw:
            results.append(SearchResultItem(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            ))
        logger.debug(f"DDGS search returned {len(results)} results")
        return results
