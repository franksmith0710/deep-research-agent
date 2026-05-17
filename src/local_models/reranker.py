from __future__ import annotations

from sentence_transformers import CrossEncoder

from src.config import settings

_model: CrossEncoder | None = None


def get_reranker() -> CrossEncoder:
    global _model
    if _model is None:
        _model = CrossEncoder(
            settings.bge_reranker_path,
            device="cpu",
        )
    return _model


def rerank(
    query: str,
    candidates: list[str],
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """用 bge-reranker-v2-m3 对候选项重排序。返回 (文本, 分数) 列表。"""
    if not candidates:
        return []
    model = get_reranker()
    pairs = [(query, c) for c in candidates]
    scores = model.predict(pairs)
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    if top_k:
        scored = scored[:top_k]
    return scored
