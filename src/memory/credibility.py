"""来源信誉管理：更新/查询信誉分。"""

from __future__ import annotations

from urllib.parse import urlparse

from src.db.postgres import upsert_credibility, get_credibility


def update_source_credibility(url: str, success: bool) -> None:
    """更新来源信誉分。"""
    domain = urlparse(url).netloc
    upsert_credibility(url, domain, success)


def get_source_tag(url: str) -> str:
    """获取来源可信度标签：高/中/低。"""
    cred = get_credibility(url)
    if cred is None:
        return "未知"
    score = cred["score"]
    if score >= 80:
        return "高"
    if score >= 50:
        return "中"
    return "低"
