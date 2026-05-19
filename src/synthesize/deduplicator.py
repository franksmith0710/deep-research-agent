"""去重器：基于嵌入余弦相似度的单条查重。"""

from __future__ import annotations

from src.local_models.embedder import embed_text
from src.db.postgres import search_memory_by_vector
from src.logging_config import get_logger

logger = get_logger("dedup")

_DEDUP_THRESHOLD = 0.88


def dedup_check(content: str) -> dict:
    """检查新内容是否与 memory_store 中已有 L1 重复。
    返回 {is_duplicate: bool, matched_id: int | None, similarity: float}
    """
    logger.debug(f"dedup_check content_len={len(content)}")
    embedding = embed_text(content)
    results = search_memory_by_vector(embedding, limit=1, min_score=_DEDUP_THRESHOLD)
    if results:
        logger.debug(f"dedup_check found duplicate id={results[0]['id']} similarity={results[0].get('similarity', 0)}")
        return {
            "is_duplicate": True,
            "matched_id": results[0]["id"],
            "similarity": results[0].get("similarity", 0),
        }
    logger.debug("dedup_check no duplicate found")
    return {
        "is_duplicate": False,
        "matched_id": None,
        "similarity": 0,
    }
