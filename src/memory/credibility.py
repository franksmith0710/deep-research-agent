"""来源信誉管理：LRU 内存缓存 + 静态域名规则。

替代原 DB 持久化方案。每域名记录 session 内成功/失败次数，
连续成功 3 次升级，失败 2 次降级。静态默认：.gov.cn/.edu.cn 为"高"。
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from src.logging_config import get_logger

logger = get_logger("credibility")

_cache: dict[str, dict] = {}
_MAX_CACHE = 128
_TTL = 3600

_HIGH_DOMAINS = (".gov.cn", ".edu.cn")


def _domain_tag(domain: str) -> str:
    for suffix in _HIGH_DOMAINS:
        if domain.endswith(suffix):
            return "高"
    return "中"


def _score_from_tag(tag: str) -> int:
    return {"高": 80, "中": 50, "低": 20}.get(tag, 50)


def update_source_credibility(url: str, success: bool) -> None:
    """更新来源信誉（内存缓存）。"""
    domain = urlparse(url).netloc
    logger.debug(f"credibility update url={url} domain={domain} success={success}")

    if domain not in _cache:
        if len(_cache) >= _MAX_CACHE:
            _cache.pop(next(iter(_cache)))
        _cache[domain] = {"tag": _domain_tag(domain), "success": 0, "fail": 0, "ts": time.monotonic()}

    entry = _cache[domain]
    entry["ts"] = time.monotonic()
    if success:
        entry["success"] += 1
        if entry["success"] >= 3 and entry["tag"] == "中":
            entry["tag"] = "高"
    else:
        entry["fail"] += 1
        if entry["fail"] >= 2:
            if entry["tag"] == "高":
                entry["tag"] = "中"
            elif entry["tag"] == "中":
                entry["tag"] = "低"


def get_credibility(url: str) -> dict | None:
    """返回兼容旧接口的 dict，含 score 字段。"""
    domain = urlparse(url).netloc
    entry = _cache.get(domain)
    if entry and time.monotonic() - entry["ts"] < _TTL:
        return {"score": _score_from_tag(entry["tag"]), "domain": domain}
    tag = _domain_tag(domain)
    return {"score": _score_from_tag(tag), "domain": domain}


def get_source_tag(url: str) -> str:
    """获取来源可信度标签：高/中/低。"""
    cred = get_credibility(url)
    score = cred["score"] if cred else 50
    if score >= 80:
        return "高"
    if score >= 50:
        return "中"
    return "低"
