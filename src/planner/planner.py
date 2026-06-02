"""规划器：指代消解 + 子问题分解 + 澄清 + 搜索规划。"""

from __future__ import annotations

import math
from typing import Any

from src.llm.client import chat_json_async
from src.local_models.embedder import embed_query
from src.logging_config import get_logger

logger = get_logger("planner")

_RESOLVE_PROMPT = """你是一个调研规划助手。用户在一个多轮对话中提出新问题，你需要：
1. 消解指代，例如：
   "刚才说的那个方面再详细讲讲" → "固态电池量产进展再详细讲讲"（材料科学）
   "它和竞品比有什么优势" → "ChatGPT与Claude功能对比分析"（AI产品）
   "最近有什么新政策" → "2025年新能源汽车购置税减免新政策"（政策法规）
2. 结合会话历史上下文，理解当前问题指的是什么
3. 将口语化/模糊的表达改写成专业、精确的搜索查询
    例如：
    "这个东西贵不贵" → "固态电池量产成本与价格分析"
    "现在做到啥程度了" → "GPT-4o多模态能力当前技术水平与进展"
    "有啥新风险" → "AI大模型数据隐私与安全风险最新研究"
    "哪些公司在做" → "固态电池领域主要厂商及竞争格局"
4. 返回 resolved_query（消解+改写后的查询）

当前会话历史（最近任务摘要）：
{session_history}

用户问题：{query}

返回 JSON：{{"resolved_query": "..."}}
"""

_CLARIFY_PROMPT = """你需要判断用户的查询是否需要补充范围或细节。如果用户的问题模糊、过于宽泛、或缺少关键细节，请返回：
- suggested_dimensions：建议从哪几个方面展开调研（最多 4 个）
- details_to_add：当前查询缺少哪些具体信息需要用户补充（如具体数据指标、时间范围、对比对象等）
否则返回空数组。

用户查询：{resolved_query}

返回 JSON：
{{
    "need_scope": true/false,
    "suggested_dimensions": ["维度1", "维度2", ...],
    "details_to_add": ["2024年最新数据", "具体技术指标", "竞品对比对象"]
}}
"""

_PLAN_PROMPT = """你是一个搜索规划专家。基于用户的调研需求，生成 3-5 个子搜索查询，
覆盖不同维度，便于搜索引擎获取全面信息。

调研需求：{resolved_query}

返回 JSON：{{"sub_queries": ["查询1", "查询2", "查询3", ...]}}
"""


async def resolve_query(query: str, session_history: str = "") -> str:
    """指代消解 + 口语查询改写：消歧指代词，并将口语化表达转为专业搜索查询。
    改写后进行语义兜底，与原始 query 余弦相似度 < 0.2 时回退原话。"""
    logger.debug(f"resolve_query: '{query}' history_len={len(session_history)}")
    result = await chat_json_async([
        {"role": "system", "content": "你是调研助手，负责消解指代并改写为专业查询。请用 JSON 格式回答。"},
        {"role": "user", "content": _RESOLVE_PROMPT.format(
            session_history=session_history[:500], query=query
        )},
    ])
    resolved = result.get("resolved_query", query)
    if resolved != query:
        sim = _cosine_sim(embed_query(query), embed_query(resolved))
        if sim < 0.2:
            logger.warning(f"改写语义偏离，回退原始 query sim={sim:.3f} resolved='{resolved}'")
            resolved = query
    logger.debug(f"resolve_query result: '{resolved}'")
    return resolved


async def need_clarification(resolved_query: str) -> dict[str, Any]:
    """判断是否需要用户补充范围或细节。返回 {need_scope, suggested_dimensions, details_to_add}。"""
    result = await chat_json_async([
        {"role": "system", "content": "你是调研助手，负责判断是否需要澄清范围。请用 JSON 格式回答。"},
        {"role": "user", "content": _CLARIFY_PROMPT.format(
            resolved_query=resolved_query,
        )},
    ])
    need_scope = result.get("need_scope", False)
    dims = result.get("suggested_dimensions", [])
    details = result.get("details_to_add", [])
    logger.debug(f"need_clarification need_scope={need_scope} dims={dims} details={details}")
    return {
        "need_scope": need_scope,
        "suggested_dimensions": dims,
        "details_to_add": details,
    }


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(ai * bi for ai, bi in zip(a, b))
    na = math.sqrt(sum(ai * ai for ai in a))
    nb = math.sqrt(sum(bi * bi for bi in b))
    return dot / (na * nb + 1e-8)


async def generate_sub_queries(resolved_query: str) -> list[str]:
    """生成子搜索查询。"""
    result = await chat_json_async([
        {"role": "system", "content": "你是搜索规划专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _PLAN_PROMPT.format(resolved_query=resolved_query)},
    ])
    sub_queries = result.get("sub_queries", [resolved_query])
    logger.debug(f"generate_sub_queries: {sub_queries}")
    return sub_queries
