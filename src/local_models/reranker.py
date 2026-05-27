from __future__ import annotations

import threading

import torch
from sentence_transformers import CrossEncoder

from src.config import settings
from src.logging_config import get_logger

logger = get_logger("reranker")

_model: CrossEncoder | None = None
_model_lock = threading.Lock()


def get_reranker() -> CrossEncoder:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                logger.debug("Loading BGE reranker model")
                _model = CrossEncoder(
                    settings.bge_reranker_path,
                    device="cuda",
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
    if next(model.model.parameters()).device.type != "cuda":
        model.model.to("cuda")
        logger.debug("Reranker moved to GPU for inference")
    pairs = [(query, c) for c in candidates]
    scores = model.predict(pairs)
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    if top_k:
        scored = scored[:top_k]
    logger.debug(f"rerank returned {len(scored)} results")
    return scored


def release_gpu() -> None:
    global _model
    if _model is not None:
        _model.model.to("cpu")
        torch.cuda.empty_cache()
        logger.debug("Reranker moved to CPU")
