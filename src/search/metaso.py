from __future__ import annotations

import httpx

from src.config import settings
from src.search.engine import SearchEngine, SearchResultItem


class MetasoSearch(SearchEngine):
    BASE = "https://metaso.cn/api/open"

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=self.BASE,
            headers={"Authorization": f"Bearer {settings.metaso_api_key}"},
            timeout=30,
        )

    def search(self, query: str, num_results: int = 5) -> list[SearchResultItem]:
        body = {"question": query, "lang": "zh", "stream": False}
        resp = self._client.post("/search/v2", json=body)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        refs = data.get("references", [])
        items = []
        for r in refs:
            if len(items) >= num_results:
                break
            url = r.get("link", "")
            title = r.get("title", "")
            if url and title:
                items.append(SearchResultItem(
                    title=title.strip(),
                    url=url,
                    snippet="",
                ))
        return items
