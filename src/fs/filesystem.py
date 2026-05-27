from __future__ import annotations

from typing import Any

from src.fs.uri import VikingURI
from src.db.postgres import (
    fs_get_node,
    fs_get_children,
    fs_write_node,
    fs_delete_node,
    fs_search_by_vector,
)
from src.logging_config import get_logger

logger = get_logger("fs")


def read(uri: str) -> str | None:
    """读取 L2 内容或 L1 知识正文。"""
    node = fs_get_node(uri)
    if node is None:
        return None
    return node.get("content") or node.get("abstract") or ""


def abstract(uri: str) -> str | None:
    """读取 L0 摘要。"""
    node = fs_get_node(uri)
    if node is None:
        return None
    return node.get("abstract")


def overview(uri: str) -> str | None:
    """读取 L1 概览。"""
    node = fs_get_node(uri)
    if node is None:
        return None
    return node.get("overview")


def ls(uri: str, recursive: bool = False) -> list[dict[str, Any]]:
    """列出目录下的子节点。"""
    return fs_get_children(uri, recursive=recursive)


def write(
    uri: str,
    content: str | None = None,
    *,
    abstract_text: str | None = None,
    overview_text: str | None = None,
    level: str | None = "L1",
    context_type: str = "user_memory",
    source_url: str | None = None,
    metadata: dict | None = None,
    embedding: list[float] | None = None,
) -> int:
    """写入文件节点。content 为 L1 正文。"""
    vuri = VikingURI.parse(uri)
    return fs_write_node(
        uri=uri,
        parent_uri=vuri.parent.full if vuri.path else None,
        name=vuri.name,
        is_directory=False,
        context_type=context_type,
        level=level,
        content=content,
        abstract=abstract_text,
        overview=overview_text,
        source_url=source_url,
        metadata=metadata,
        embedding=embedding,
    )


def mkdir(uri: str, context_type: str = "user_memory") -> int | None:
    """创建目录节点。"""
    vuri = VikingURI.parse(uri)
    existing = fs_get_node(uri)
    if existing:
        return existing["id"]
    return fs_write_node(
        uri=uri,
        parent_uri=vuri.parent.full if vuri.path else None,
        name=vuri.name,
        is_directory=True,
        context_type=context_type,
        level=None,
        content=None,
    )


def rm(uri: str, recursive: bool = False) -> bool:
    """删除节点。"""
    return fs_delete_node(uri, recursive=recursive)


def search(
    query_embedding: list[float],
    root_uri: str = "viking://user/memories",
    limit: int = 10,
    min_score: float = 0.6,
) -> list[dict[str, Any]]:
    """向量搜索指定根目录下的节点。"""
    return fs_search_by_vector(
        query_embedding,
        parent_uri=root_uri,
        limit=limit,
        min_score=min_score,
    )


def ensure_dir(uri: str, context_type: str = "user_memory") -> None:
    """递归确保目录存在。"""
    vuri = VikingURI.parse(uri)
    parts = [vuri.scope]
    for segment in vuri.path.strip("/").split("/"):
        if not segment:
            continue
        parts.append(segment)
        dir_uri = f"viking://{'/'.join(parts)}"
        existing = fs_get_node(dir_uri)
        if not existing:
            fs_write_node(
                uri=dir_uri,
                parent_uri=f"viking://{'/'.join(parts[:-1])}" if len(parts) > 1 else None,
                name=segment,
                is_directory=True,
                context_type=context_type,
                level=None,
                content=None,
            )
