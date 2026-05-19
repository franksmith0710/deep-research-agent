"""综合器：按主题聚类 L1 发现，生成综合内容。"""

from __future__ import annotations

import json
from typing import Any

from src.llm.client import chat_json
from src.logging_config import get_logger

logger = get_logger("synthesize")

_SYNTHESIZE_PROMPT = """你是一个研究综合专家。根据以下发现列表，
按主题维度生成综合内容（新搜索话题的章节内容）。

要求：
1. 按主题分组，每个主题写一段连贯的段落
2. 引用来源：[1][2] 格式标注每条信息的来源序号
3. 内容客观，只写事实
4. Markdown 格式

发现列表（带来源序号）：
{findings_with_refs}

生成主题（输出为此章节的完整 Markdown 内容）：
{topic}

返回 JSON：
{{
    "section_title": "章节标题",
    "content": "完整 Markdown 内容"
}}
"""


def synthesize_section(
    findings: list[dict],
    topic: str,
) -> dict[str, Any]:
    """综合多个 L1 发现生成一个章节。findings 中应包含 topic, content, source_url。"""
    logger.debug(f"synthesize_section topic='{topic}' findings={len(findings)}")
    lines = []
    for i, f in enumerate(findings, 1):
        lines.append(f"[{i}] topic={f.get('topic', '')} | source={f.get('source_url', '')}")
        lines.append(f"    {f.get('content', '')}")
    findings_text = "\n".join(lines)

    result = chat_json([
        {"role": "system", "content": "你是研究综合专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _SYNTHESIZE_PROMPT.format(
            findings_with_refs=findings_text, topic=topic
        )},
    ])

    return {
        "section_title": result.get("section_title", topic),
        "content": result.get("content", ""),
    }
