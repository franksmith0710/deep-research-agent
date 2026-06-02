"""记忆检索器：跨会话长期记忆检索，调 fs.retriever 递归检索。"""

from __future__ import annotations

from typing import Any

from src.fs import retriever as fs_retriever
from src.logging_config import get_logger

logger = get_logger("memory")


def retrieve_l1(query: str, limit: int = 3, min_score: float = 0.0) -> list[dict[str, Any]]:
    """长期记忆检索：目录递归搜索 user/memories/。

    返回匹配的 fs_nodes 结果列表，每项含 content/source_url/abstract 等。
    结果按 _score 降序排列。min_score 用于过滤低相似度结果。
    """
    logger.debug(f"retrieve_l1 query='{query[:50]}...' limit={limit} min_score={min_score}")
    results = fs_retriever.search(query, root_uri="viking://user/memories", limit=limit)
    if min_score > 0:
        before = len(results)
        results = [r for r in results if (r.get("_score", r.get("similarity", 0.0)) or 0.0) >= min_score]
        if len(results) < before:
            logger.debug(f"retrieve_l1 min_score={min_score} filtered {before - len(results)}/{before}")
    logger.debug(f"retrieve_l1 returned {len(results)} results")
    return results
