"""重排器：基于 bge-reranker-v2-m3 对搜索结果/发现排序。"""

from __future__ import annotations

from src.local_models.reranker import rerank


def rank_findings(
    query: str,
    findings: list[dict],
    top_k: int | None = None,
) -> list[dict]:
    """按查询相关性重排发现列表。findings 为 [{topic, content, ...}]。"""
    if not findings:
        return []
    texts = [f"{f.get('topic', '')}: {f.get('content', '')}" for f in findings]
    scored = rerank(query, texts, top_k=top_k)
    # 关联回原数据
    score_map = {texts[i]: scored[i][1] for i in range(len(scored))}
    result = []
    for f in findings:
        text = f"{f.get('topic', '')}: {f.get('content', '')}"
        if text in score_map:
            f["score"] = round(float(score_map[text]), 4)
            result.append(f)
    result.sort(key=lambda x: x.get("score", 0), reverse=True)
    return result
