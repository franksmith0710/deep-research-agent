"""网页简报：从网页原文中提取关键要点（Key Points），纯 token 压缩，不是记忆。"""

from __future__ import annotations

import json
from typing import Any

from src.llm.client import chat_json_async
from src.logging_config import get_logger

logger = get_logger("page_brief")

_BRIEF_PROMPT = """你是一个信息提取专家。从以下网页内容中提取关键要点。

查询主题：{query}

要求：
    1. 提取最多 8 条关键事实/数据/观点，每条 ≤400 字
2. 每条标注所属主题（例如但不限于"市场规模""技术路线""临床数据""政策法规""竞品对比""落地案例"）
3. 保留原文中的数字、日期、人名等关键信息
4. 只写事实，不添加评价或推测
5. **只提取与查询主题相关的内容**，无关部分（如产品广告、游戏攻略、无关百科段落等）跳过

网页标题：{title}
网页内容：
{content}

返回 JSON：
{{
    "key_points": [
        {{"topic": "主题", "content": "发现内容"}},
        ...
    ]
}}
"""

_BATCH_BRIEF_PROMPT = """你是一个信息提取专家。从以下多个网页中同时提取关键要点。

查询主题：{query}

要求：
    1. 对每个网页提取最多 8 条关键事实/数据/观点，每条 ≤400 字
2. 每条标注所属主题（例如但不限于"市场规模""技术路线""临床数据""政策法规""竞品对比""落地案例"）
3. 保留原文中的数字、日期、人名等关键信息
4. 只写事实，不添加评价或推测
5. **只提取与查询主题相关的内容**，无关部分（如产品广告、游戏攻略、无关百科段落等）跳过

网页列表（JSON 格式，已截断过长内容）：
{pages_json}

返回 JSON：
{{
    "results": [
        {{
            "url": "对应网页的 url",
            "key_points": [
                {{"topic": "主题", "content": "发现内容"}},
                ...
            ]
        }},
        ...
    ]
}}
"""


async def batch_brief_from_pages(pages: list[dict], query: str = "") -> list[dict]:
    """从多个网页同时提取 key_points，每个 page 需包含 title, content, url。
    query 用作主题相关性过滤。"""
    if not pages:
        return []
    logger.debug(f"batch_brief_from_pages count={len(pages)}")
    max_chars = 15000
    entries = []
    for p in pages:
        content = p.get("content", "")[:max_chars]
        if len(p.get("content", "")) > max_chars:
            content += "\n\n（内容过长，已截断）"
        entries.append({"url": p["url"], "title": p.get("title", ""), "content": content})

    result = await chat_json_async([
        {"role": "system", "content": "你是信息提取专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _BATCH_BRIEF_PROMPT.format(
            pages_json=json.dumps(entries, ensure_ascii=False),
            query=query or "无",
        )},
    ])

    return result.get("results", [])
