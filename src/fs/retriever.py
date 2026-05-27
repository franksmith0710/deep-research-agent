from __future__ import annotations

import heapq
from typing import Any

from src.fs import filesystem as fs
from src.local_models.embedder import embed_query
from src.logging_config import get_logger

logger = get_logger("fs_retriever")

_GLOBAL_TOPK = 10
_SEARCH_LIMIT = 3
_CONVERGENCE_ROUNDS = 3
_MAX_PARALLEL = 4


def search(
    query: str,
    root_uri: str = "viking://user/memories",
    limit: int = _SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    """目录递归检索：全局搜索 → 目录导航 → 子节点搜索 → 收敛。

    返回 top-k MatchedContext，每个附带检索路径。
    """
    query_vec = embed_query(query)

    # Step 1: 全局向量搜索，找出候选目录
    global_results = fs.search(query_vec, root_uri=root_uri, limit=_GLOBAL_TOPK, min_score=0.0)
    if not global_results:
        return []

    # Step 2: 从全局结果提取目录节点（非 L2 叶子）
    candidates: list[tuple[float, str]] = []
    seen_uris: set[str] = set()
    for r in global_results:
        uri = r.get("uri", "")
        level = r.get("level") or ""
        if level != "L1" and not r.get("is_directory", False):
            continue
        if uri in seen_uris:
            continue
        seen_uris.add(uri)
        score = r.get("_score", r.get("similarity", 0.0)) or 0.0
        candidates.append((score, uri))

    if not candidates:
        return []

    # 取 top-3 目录作为起点
    candidates.sort(key=lambda x: -x[0])
    candidates = candidates[:3]

    # Step 3: Priority-queue 驱动递归搜索
    collected: dict[str, dict[str, Any]] = {}
    queue: list[tuple[float, str]] = []
    visited: set[str] = set()

    for score, uri in candidates:
        heapq.heappush(queue, (-score, uri))

    prev_topk: set[str] = set()
    convergence = 0

    while queue and convergence < _CONVERGENCE_ROUNDS:
        # Batch pop
        batch: list[tuple[str, float]] = []
        while queue and len(batch) < _MAX_PARALLEL:
            neg_score, uri = heapq.heappop(queue)
            if uri in visited:
                continue
            visited.add(uri)
            batch.append((uri, -neg_score))

        for current_uri, parent_score in batch:
            children = fs_search_children(current_uri, query_vec)
            for child in children:
                child_uri = child.get("uri", "")
                if child_uri in collected:
                    continue
                child_score = child.get("_score", child.get("similarity", 0.0)) or 0.0
                final_score = child_score  # alpha=1.0
                if final_score < 0.6:
                    continue

                child["_search_path"] = current_uri
                collected[child_uri] = child

                if child.get("is_directory", False) and child_uri not in visited:
                    heapq.heappush(queue, (-final_score, child_uri))

        # 收敛检测
        current_topk = set(list(collected.keys())[:limit])
        if current_topk == prev_topk and len(current_topk) >= limit:
            convergence += 1
        else:
            convergence = 0
            prev_topk = current_topk

    # Step 4: 取 top-k
    sorted_results = sorted(
        collected.values(),
        key=lambda x: x.get("_score", x.get("similarity", 0.0)) or 0.0,
        reverse=True,
    )
    return sorted_results[:limit]


def fs_search_children(
    parent_uri: str,
    query_vec: list[float],
    min_score: float = 0.6,
) -> list[dict[str, Any]]:
    """搜索指定目录下的子节点（向量搜索 + 精确匹配）。"""
    return fs.search(query_vec, root_uri=parent_uri, limit=10, min_score=min_score)
