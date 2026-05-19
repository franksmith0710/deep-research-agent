from __future__ import annotations

from urllib.parse import urlparse

import httpx
import trafilatura

from src.models import ScrapedPage
from src.logging_config import get_logger

logger = get_logger("scraper")


class Scraper:
    """用 httpx + trafilatura 提取网页正文。"""

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def scrape(self, url: str) -> ScrapedPage:
        logger.debug(f"Scraping url={url}")
        try:
            resp = self._client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            })
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning(f"HTTP error url={url}: {e}")
            return ScrapedPage(url=url, title="", content="", success=False, error=str(e))

        title = trafilatura.extract(html, output_format="txt", include_tables=False, include_links=False)
        content = trafilatura.extract(html, output_format="markdown", include_tables=True, include_links=True)

        if not content:
            logger.warning(f"trafilatura extract failed url={url}")
            return ScrapedPage(
                url=url, title=title or "", content="", success=False,
                error="trafilatura 未能提取正文"
            )

        logger.debug(f"Scraped url={url} title='{title[:50]}...' len={len(content)}")
        return ScrapedPage(
            url=url,
            title=title or "",
            content=content,
            success=True,
        )

    def scrape_many(self, urls: list[str]) -> list[ScrapedPage]:
        results: list[ScrapedPage] = []
        for url in urls:
            results.append(self.scrape(url))
        return results

    def close(self) -> None:
        self._client.close()
