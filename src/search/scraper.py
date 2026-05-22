from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx
import trafilatura
from trafilatura.settings import use_config

from src.models import ScrapedPage
from src.logging_config import get_logger

logger = get_logger("scraper")

_MAX_RETRIES = 3
_BASE_DELAY = 1.5
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 20.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _is_js_heavy(html: str) -> bool:
    """简单检测是否重度依赖 JS 渲染（SPA 页面）。"""
    scripts = re.findall(r'<script', html, re.IGNORECASE)
    noscript = re.findall(r'<noscript', html, re.IGNORECASE)
    text_len = len(re.sub(r'<[^>]+>', '', html))
    # script 密集且正文极短 → 疑似 JS 渲染页
    if len(scripts) > 10 and text_len < 500:
        return True
    return False


def _extract_fallback(html: str, title: str) -> str:
    """trafilatura 提取失败时，从 meta / <p> 标签降级提取。"""
    fragments = []

    # meta description
    m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        fragments.append(m.group(1))

    # meta keywords
    m = re.search(r'<meta\s+name=["\']keywords["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        fragments.append(f"关键词: {m.group(1)}")

    # <h1>-<h3> 标题
    for tag in ("h1", "h2", "h3"):
        for match in re.finditer(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.IGNORECASE | re.DOTALL):
            t = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            if t:
                fragments.append(t)

    # 前 5 个 <p> 段落
    p_count = 0
    for match in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.IGNORECASE | re.DOTALL):
        t = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if t and len(t) > 20:
            fragments.append(t)
            p_count += 1
            if p_count >= 5:
                break

    result = "\n\n".join(fragments) if fragments else ""
    if result:
        logger.debug(f"fallback 提取成功（{len(fragments)} 段，共 {len(result)} 字）")
    return result


class Scraper:
    """用 httpx + trafilatura 提取网页正文，含重试、超时、降级。"""

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def _fetch(self, url: str) -> tuple[str, str, str]:
        """获取页面 HTML，返回 (html, final_url, error)。"""
        last_error = ""
        for attempt in range(_MAX_RETRIES):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
                    follow_redirects=True,
                ) as client:
                    resp = client.get(url, headers={"User-Agent": _USER_AGENT})
                    resp.raise_for_status()
                    return resp.text, str(resp.url), ""
            except httpx.TimeoutException as e:
                last_error = f"超时: {e}"
                logger.warning(f"第 {attempt + 1} 次超时 url={url}")
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}"
                logger.warning(f"第 {attempt + 1} 次 HTTP 错误 url={url} code={e.response.status_code}")
                if e.response.status_code in (403, 404, 410):
                    break  # 不重试
            except Exception as e:
                last_error = str(e)
                logger.warning(f"第 {attempt + 1} 次失败 url={url}: {e}")

            if attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY * (2 ** attempt)
                logger.debug(f"等待 {delay}s 后重试 url={url}")
                time.sleep(delay)

        return "", "", last_error

    def scrape(self, url: str) -> ScrapedPage:
        logger.debug(f"Scraping url={url}")
        html, final_url, error = self._fetch(url)

        if error:
            return ScrapedPage(url=url, title="", content="", success=False, error=error)

        # JS 重型页面检测
        if _is_js_heavy(html):
            logger.warning(f"检测到 JS 重型页面，跳过 url={url}")
            return ScrapedPage(
                url=url, title="", content="", success=False,
                error="JS 渲染页面，跳过抓取"
            )

        # trafilatura 配置：更宽松的提取
        config = use_config()
        config["DEFAULT"]["EXTRACTION_TIMEOUT"] = "5"

        title = trafilatura.extract(
            html, output_format="txt", include_tables=False, include_links=False,
            config=config, favor_precision=True,
        )
        content = trafilatura.extract(
            html, output_format="markdown", include_tables=True, include_links=True,
            config=config, favor_recall=True,
        )

        if not content:
            logger.info(f"trafilatura 提取失败，尝试 fallback url={url}")
            content = _extract_fallback(html, title or "")
            if not content:
                return ScrapedPage(
                    url=url, title=title or "", content="", success=False,
                    error="trafilatura 及 fallback 均未能提取正文"
                )

        logger.debug(f"Scraped url={final_url} title='{title[:50] if title else ''}...' len={len(content)}")
        return ScrapedPage(
            url=final_url or url,
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
        pass
