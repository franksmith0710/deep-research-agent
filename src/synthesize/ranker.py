"""重排器：基于 bge-reranker-v2-m3 对搜索结果/发现排序。"""

from __future__ import annotations

from src.local_models.reranker import rerank
from src.logging_config import get_logger

logger = get_logger("ranker")


def rank_findings(
    query: str,
    findings: list[dict],
    top_k: int | None = None,
) -> list[dict]:
    """按查询相关性重排发现列表。findings 为 [{topic, content, ...}]。"""
    if not findings:
        return []
    logger.debug(f"rank_findings query='{query[:30]}...' findings={len(findings)}")
    texts = [f"{f.get('topic', '')}: {f.get('content', '')}" for f in findings]
    scored = rerank(query, texts, top_k=top_k)
    # 关联回原数据
    score_map = dict(scored)
    result = []
    for f in findings:
        text = f"{f.get('topic', '')}: {f.get('content', '')}"
        if text in score_map:
            f["score"] = round(float(score_map[text]), 4)
            result.append(f)
    result.sort(key=lambda x: x.get("score", 0), reverse=True)
    logger.debug(f"rank_findings returned {len(result)} results")
    return result
