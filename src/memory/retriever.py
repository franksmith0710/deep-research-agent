"""记忆检索器：跨会话长期记忆检索，调 fs.retriever 递归检索。"""

from __future__ import annotations

from typing import Any

from src.fs import retriever as fs_retriever
from src.logging_config import get_logger

logger = get_logger("memory")


def retrieve_l1(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """长期记忆检索：目录递归搜索 user/memories/。

    返回匹配的 fs_nodes 结果列表，每项含 content/source_url/abstract 等。
    结果按 _score 降序排列。
    """
    logger.debug(f"retrieve_l1 query='{query[:50]}...' limit={limit}")
    results = fs_retriever.search(query, root_uri="viking://user/memories", limit=limit)
    logger.debug(f"retrieve_l1 returned {len(results)} results")
    return results
