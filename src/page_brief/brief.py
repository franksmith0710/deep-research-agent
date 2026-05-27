"""网页简报：从网页原文中提取关键要点（Key Points），纯 token 压缩，不是记忆。"""

from __future__ import annotations

from typing import Any

from src.llm.client import chat_json
from src.logging_config import get_logger

logger = get_logger("page_brief")

_BRIEF_PROMPT = """你是一个信息提取专家。从以下网页内容中提取关键要点。

要求：
    1. 提取最多 8 条关键事实/数据/观点，每条 ≤400 字
2. 每条标注所属主题（例如但不限于"市场规模""技术架构""落地案例"）
3. 保留原文中的数字、日期、人名等关键信息
4. 只写事实，不添加评价或推测

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


def brief_from_page(title: str, content: str) -> dict[str, Any]:
    """从网页内容中提取 key_points（简报条目）。"""
    logger.debug(f"brief_from_page title='{title[:50]}...'")
    max_chars = 15000
    truncated = content[:max_chars]
    if len(content) > max_chars:
        truncated += "\n\n（内容过长，已截断）"

    result = chat_json([
        {"role": "system", "content": "你是信息提取专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _BRIEF_PROMPT.format(
            title=title, content=truncated
        )},
    ])

    return {
        "key_points": result.get("key_points", []),
    }
