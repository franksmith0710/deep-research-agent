"""记忆检索器：L1 语义检索 + L2 溯源 + 历史记忆合并。"""

from __future__ import annotations

from typing import Any

from src.db.postgres import (
    search_memory_by_vector,
    get_l2_by_url,
    get_memories_by_task,
)
from src.local_models.embedder import embed_query


def retrieve_l1(query: str, limit: int = 5, min_score: float = 0.7) -> list[dict[str, Any]]:
    """L1 语义检索：embedding 余弦相似度。"""
    embedding = embed_query(query)
    return search_memory_by_vector(embedding, limit=limit, min_score=min_score)


def retrieve_l2(source_url: str) -> list[dict[str, Any]]:
    """L2 精确检索：按 source_url 查。"""
    return get_l2_by_url(source_url)


def retrieve_by_task(task_id: int, level: str | None = None) -> list[dict[str, Any]]:
    """按 task_id 检索全部记忆。"""
    return get_memories_by_task(task_id, level)
