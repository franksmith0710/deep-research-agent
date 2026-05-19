from __future__ import annotations

from sentence_transformers import CrossEncoder

from src.config import settings
from src.logging_config import get_logger

logger = get_logger("reranker")

_model: CrossEncoder | None = None


def get_reranker() -> CrossEncoder:
    global _model
    if _model is None:
        logger.debug("Loading BGE reranker model")
        _model = CrossEncoder(
            settings.bge_reranker_path,
            device="cpu",
        )
        logger.debug("BGE reranker model loaded")
    return _model


def rerank(
    query: str,
    candidates: list[str],
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """用 bge-reranker-v2-m3 对候选项重排序。返回 (文本, 分数) 列表。"""
    if not candidates:
        return []
    logger.debug(f"rerank query='{query[:30]}...' candidates={len(candidates)}")
    model = get_reranker()
    pairs = [(query, c) for c in candidates]
    scores = model.predict(pairs)
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    if top_k:
        scored = scored[:top_k]
    logger.debug(f"rerank returned {len(scored)} results")
    return scored
