"""规划器：指代消解 + 子问题分解 + 澄清 + 搜索规划。"""

from __future__ import annotations

import json
import math
from typing import Any

from src.llm.client import chat_json
from src.local_models.embedder import embed_query
from src.logging_config import get_logger

logger = get_logger("planner")

_RESOLVE_PROMPT = """你是一个调研规划助手。用户在一个多轮对话中提出新问题，你需要：
1. 消解指代，例如："刚才说的那个方面再详细讲讲" → "固态电池量产进展再详细讲讲"
2. 结合会话历史上下文，理解当前问题指的是什么
3. 将口语化/模糊的表达改写成专业、精确的搜索查询
   例如："这个东西贵不贵" → "固态电池量产成本与价格分析"
         "现在做到啥程度了" → "固态电池当前技术成熟度与产业化进展"
         "有啥新技术" → "最新固态电池电解质材料技术突破"
4. 返回 resolved_query（消解+改写后的查询）

当前会话历史（最近任务摘要）：
{session_history}

用户问题：{query}

返回 JSON：{{"resolved_query": "..."}}
"""

_CLARIFY_PROMPT = """你需要判断用户的查询是否需要补充范围。如果用户的问题模糊或过于宽泛，
请返回建议的调研维度（最多 4 个）。否则返回空数组。

用户查询：{resolved_query}

返回 JSON：{{"need_scope": true/false, "suggested_dimensions": ["维度1", "维度2", ...]}}
"""

_PLAN_PROMPT = """你是一个搜索规划专家。基于用户的调研需求，生成 3-5 个子搜索查询，
覆盖不同维度，便于搜索引擎获取全面信息。

调研需求：{resolved_query}

返回 JSON：{{"sub_queries": ["查询1", "查询2", "查询3", ...]}}
"""


def resolve_query(query: str, session_history: str = "") -> str:
    """指代消解 + 口语查询改写：消歧指代词，并将口语化表达转为专业搜索查询。
    改写后进行语义兜底，与原始 query 余弦相似度 < 0.2 时回退原话。"""
    logger.debug(f"resolve_query: '{query}' history_len={len(session_history)}")
    result = chat_json([
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


def need_clarification(resolved_query: str) -> dict[str, Any]:
    """判断是否需要用户补充范围。返回 {need_scope, suggested_dimensions}。"""
    result = chat_json([
        {"role": "system", "content": "你是调研助手，负责判断是否需要澄清范围。请用 JSON 格式回答。"},
        {"role": "user", "content": _CLARIFY_PROMPT.format(
            resolved_query=resolved_query,
        )},
    ])
    need_scope = result.get("need_scope", False)
    dims = result.get("suggested_dimensions", [])
    logger.debug(f"need_clarification need_scope={need_scope} dims={dims}")
    return {
        "need_scope": need_scope,
        "suggested_dimensions": dims,
    }


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(ai * bi for ai, bi in zip(a, b))
    na = math.sqrt(sum(ai * ai for ai in a))
    nb = math.sqrt(sum(bi * bi for bi in b))
    return dot / (na * nb + 1e-8)


def generate_sub_queries(resolved_query: str) -> list[str]:
    """生成子搜索查询。"""
    result = chat_json([
        {"role": "system", "content": "你是搜索规划专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _PLAN_PROMPT.format(resolved_query=resolved_query)},
    ])
    sub_queries = result.get("sub_queries", [resolved_query])
    logger.debug(f"generate_sub_queries: {sub_queries}")
    return sub_queries
