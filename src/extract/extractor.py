"""提取器：L2（网页全文）→ L1（核心发现）+ L0（一句话摘要），一次 LLM 调用。"""

from __future__ import annotations

import json
from typing import Any

from src.llm.client import chat_json
from src.logging_config import get_logger

logger = get_logger("extract")

_EXTRACT_PROMPT = """你是一个信息提取专家。从以下网页内容中提取核心发现。

要求：
1. L0（一句话摘要）：用一句话总结该网页的核心内容（≤100 字）
2. L1（核心发现列表）：提取 3-8 条关键事实/数据/观点，每条 ≤200 字
3. 每条 L1 标注所属主题（如"市场规模""技术架构""落地案例"）
4. 保留原文中的数字、日期、人名等关键信息
5. 只写事实，不添加评价或推测

网页标题：{title}
网页内容：
{content}

返回 JSON：
{{
    "l0_summary": "...",
    "findings": [
        {{"topic": "主题", "content": "发现内容"}},
        ...
    ]
}}
"""


def extract_from_content(title: str, content: str) -> dict[str, Any]:
    """从 L2 网页内容中提取 L0 摘要和 L1 发现列表。"""
    logger.debug(f"extract_from_content title='{title[:50]}...'")
    max_chars = 15000
    truncated = content[:max_chars]
    if len(content) > max_chars:
        truncated += "\n\n（内容过长，已截断）"

    result = chat_json([
        {"role": "system", "content": "你是信息提取专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _EXTRACT_PROMPT.format(
            title=title, content=truncated
        )},
    ])

    return {
        "l0_summary": result.get("l0_summary", ""),
        "findings": result.get("findings", []),
    }
