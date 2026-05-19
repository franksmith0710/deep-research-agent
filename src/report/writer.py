"""报告写入器：报告大纲规划 + Markdown 完整报告生成。"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Callable

from src.llm.client import chat_json, chat_stream
from src.logging_config import get_logger

logger = get_logger("report")

_OUTLINE_PROMPT = """你是一个报告架构师。为以下调研主题规划 7 章标准报告大纲。

主题：{query}

要求：
1. 固定 7 章：摘要 / 调研背景与范围 / 核心现状与关键数据 / 多方观点与信息冲突 / 风险与信息缺口 / 总结与建议 / 引用来源
2. 每章给出 2-4 个要点
3. 中文

返回 JSON：
{{
    "outline": [
        {{"section": "摘要", "points": ["要点1", "要点2"]}},
        ...
    ]
}}
"""

_REPORT_PROMPT = """你是一个专业报告写手。基于以下调研发现，生成完整 Markdown 报告。

大纲：
{outline_text}

调研发现（含来源引用）：
{findings_text}

要求：
1. 严格按大纲章节组织
2. [1][2] 格式标注引用
3. 引用来源章节列出所有来源（带可信度标签）
4. 语言客观、精炼
5. 全文 Markdown

返回 JSON：
{{
    "report": "# 报告标题\\n\\n## 摘要\\n...",
    "sections": {{
        "摘要": "## 摘要\\n...",
        "调研背景与范围": "## 调研背景与范围\\n..."
    }}
}}
"""


def generate_outline(query: str) -> list[dict[str, Any]]:
    """生成 7 章报告大纲。"""
    logger.debug(f"generate_outline query='{query[:50]}...'")
    result = chat_json([
        {"role": "system", "content": "你是报告架构师。请用 JSON 格式回答。"},
        {"role": "user", "content": _OUTLINE_PROMPT.format(query=query)},
    ])
    return result.get("outline", [])


def generate_report(query: str, outline: list[dict], findings: list[dict]) -> dict[str, Any]:
    """基于大纲和发现生成完整报告。"""
    logger.debug(f"generate_report query='{query[:50]}...' outline={len(outline)} findings={len(findings)}")
    outline_lines = []
    for o in outline:
        outline_lines.append(f"## {o.get('section', '')}")
        for p in o.get("points", []):
            outline_lines.append(f"  - {p}")
    outline_text = "\n".join(outline_lines)

    findings_lines = []
    for i, f in enumerate(findings, 1):
        findings_lines.append(f"[{i}] topic={f.get('topic', '')} | source={f.get('source_url', '')}")
        findings_lines.append(f"    {f.get('content', '')}")
    findings_text = "\n".join(findings_lines)

    result = chat_json([
        {"role": "system", "content": "你是专业报告写手。请用 JSON 格式回答。"},
        {"role": "user", "content": _REPORT_PROMPT.format(
            outline_text=outline_text, findings_text=findings_text
        )},
    ])

    return {
        "report": result.get("report", ""),
        "sections": result.get("sections", {}),
    }


async def generate_report_stream(
    query: str,
    outline: list[dict],
    findings: list[dict],
    on_token: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """流式生成报告，边生成边输出 token。"""
    logger.debug(f"generate_report_stream query='{query[:50]}...' outline={len(outline)} findings={len(findings)}")

    outline_lines = []
    for o in outline:
        outline_lines.append(f"## {o.get('section', '')}")
        for p in o.get("points", []):
            outline_lines.append(f"  - {p}")
    outline_text = "\n".join(outline_lines)

    findings_lines = []
    for i, f in enumerate(findings, 1):
        findings_lines.append(f"[{i}] topic={f.get('topic', '')} | source={f.get('source_url', '')}")
        findings_lines.append(f"    {f.get('content', '')}")
    findings_text = "\n".join(findings_lines)

    messages = [
        {"role": "system", "content": "你是专业报告写手。请用 JSON 格式回答。"},
        {"role": "user", "content": _REPORT_PROMPT.format(
            outline_text=outline_text, findings_text=findings_text
        )},
    ]

    full_content = ""
    async for token in chat_stream(messages):
        full_content += token
        if on_token:
            await on_token(token)

    try:
        result = json.loads(full_content)
    except json.JSONDecodeError as e:
        logger.warning(f"Streaming result parse JSON failed, treating as raw: {e}")
        result = {"report": full_content, "sections": {}}

    return {
        "report": result.get("report", ""),
        "sections": result.get("sections", {}),
    }
