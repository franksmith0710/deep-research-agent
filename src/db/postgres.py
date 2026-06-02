from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool

from pgvector.psycopg2 import register_vector

from src.config import settings
from src.logging_config import get_logger

logger = get_logger("db")

# 全局连接池
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool(minconn: int = 2, maxconn: int = 8) -> None:
    global _pool
    logger.info(f"PostgreSQL pool initializing dsn={settings.postgres_dsn.split('@')[1] if '@' in settings.postgres_dsn else 'unknown'}")
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn, maxconn, settings.postgres_dsn,
        client_encoding='utf8'
    )
    logger.info("PostgreSQL pool initialized")


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL pool closed")


def _dict_row(cursor: psycopg2.extensions.cursor) -> dict[str, Any] | None:
    cols = [desc[0] for desc in cursor.description] if cursor.description else []
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(cols, row))


def _dict_rows(cursor: psycopg2.extensions.cursor) -> list[dict[str, Any]]:
    cols = [desc[0] for desc in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ── chat_history ──────────────────────────────────────────────────────

def insert_chat_message(session_id: str, role: str, content: str) -> int:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_history (session_id, role, content) VALUES (%s, %s, %s) RETURNING id",
                    (session_id, role, content),
                )
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else -1
        finally:
            _pool.putconn(conn)


def get_chat_history(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, role, content, created_at FROM chat_history WHERE session_id = %s ORDER BY created_at LIMIT %s",
                    (session_id, limit),
                )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


# ── research_tasks ────────────────────────────────────────────────────

def create_session_idle(session_id: str) -> None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)
                cur.execute(
                    """INSERT INTO research_tasks (session_id, query, status, created_at, updated_at)
                       VALUES (%s, '', 'pending', %s, %s)""",
                    (session_id, now, now),
                )
                conn.commit()
        finally:
            _pool.putconn(conn)


def create_task(session_id: str, query: str) -> int:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)
                cur.execute(
                    """INSERT INTO research_tasks (session_id, query, status, created_at, updated_at)
                       VALUES (%s, %s, 'pending', %s, %s) RETURNING task_id""",
                    (session_id, query, now, now),
                )
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else -1
        finally:
            _pool.putconn(conn)


def get_task_by_session(session_id: str) -> dict[str, Any] | None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM research_tasks WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                )
                return cur.fetchone()
        finally:
            _pool.putconn(conn)


def get_recent_tasks_by_session(session_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """获取当前 session 最近 N 条任务的 L0 摘要，用于 resolve_context 指代消解。"""
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT task_id, query, l0_summary, created_at
                       FROM research_tasks
                       WHERE session_id = %s AND l0_summary IS NOT NULL AND l0_summary != ''
                       ORDER BY created_at DESC
                       LIMIT %s""",
                    (session_id, limit),
                )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


def update_task(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc)
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [task_id]
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE research_tasks SET {sets} WHERE task_id = %s",
                    vals,
                )
                conn.commit()
        finally:
            _pool.putconn(conn)


def load_task_context(session_id: str) -> dict[str, Any] | None:
    """加载会话最近的 task 上下文（report/sections/outline/deep_search_count）。

    sections 从 report 内容中按 h2 标题正则解析，而非从空的 outline 重建。
    """
    import re
    task = get_task_by_session(session_id)
    if not task:
        return None
    sections: dict[str, str] = {}
    report_text = task.get("report") or ""
    if report_text:
        parts = re.split(r"(?=^##\s+)", report_text, flags=re.MULTILINE)
        for part in parts:
            m = re.match(r"^##\s+(.+?)\s*$", part, re.MULTILINE)
            if m:
                title = m.group(1).strip()
                content = part[m.end():].strip()
                sections[title] = content
    if not sections and task.get("outline") and isinstance(task["outline"], list):
        for ch in task["outline"]:
            sec = ch.get("section", "") if isinstance(ch, dict) else str(ch)
            sections[sec] = ""
    return {
        "task_id": task["task_id"],
        "report": report_text,
        "sections": sections,
        "outline": task.get("outline", []) or [],
        "deep_search_count": task.get("deep_search_count", 0),
        "l0_summary": task.get("l0_summary", "") or "",
    }


def get_all_sessions() -> list[dict[str, Any]]:
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM (
                           SELECT DISTINCT ON (rt.session_id)
                               rt.session_id,
                               rt.query AS query_preview,
                               rt.status,
                               rt.updated_at
                           FROM research_tasks rt
                           ORDER BY rt.session_id, rt.updated_at DESC
                       ) sub
                       ORDER BY sub.updated_at DESC"""
                )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


def delete_session_data(session_id: str) -> None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chat_history WHERE session_id = %s",
                    (session_id,),
                )
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM research_tasks WHERE session_id = %s",
                    (session_id,),
                )
            conn.commit()
        finally:
            _pool.putconn(conn)


# ====================================================================
# fs_nodes CRUD（Viking 文件系统持久化层）
# ====================================================================


def fs_get_node(uri: str) -> dict[str, Any] | None:
    """按 URI 精确查找节点。"""
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM fs_nodes WHERE uri = %s",
                    (uri,),
                )
                return cur.fetchone()
        finally:
            _pool.putconn(conn)


def fs_get_children(parent_uri: str, recursive: bool = False) -> list[dict[str, Any]]:
    """列出目录下的子节点。"""
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if recursive:
                    cur.execute(
                        """SELECT * FROM fs_nodes
                           WHERE uri LIKE %s
                           ORDER BY uri""",
                        (parent_uri.rstrip("/") + "/%",),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM fs_nodes WHERE parent_uri = %s ORDER BY name",
                        (parent_uri,),
                    )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


def fs_write_node(
    uri: str,
    parent_uri: str | None = None,
    name: str = "",
    is_directory: bool = False,
    context_type: str = "user_memory",
    level: str | None = "L1",
    content: str | None = None,
    abstract: str | None = None,
    overview: str | None = None,
    source_url: str | None = None,
    metadata: dict | None = None,
    embedding: list[float] | None = None,
) -> int:
    """写入或更新文件节点。存在则 UPDATE，否则 INSERT。"""
    existing = fs_get_node(uri)
    now = datetime.now(timezone.utc)
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    if existing:
        with _pool.getconn() as conn:
            register_vector(conn)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE fs_nodes SET
                               parent_uri = %s, name = %s, is_directory = %s,
                               context_type = %s, level = %s, content = %s,
                               abstract = %s, overview = %s, source_url = %s,
                               metadata = COALESCE(%s, metadata),
                               embedding = %s, updated_at = %s
                           WHERE uri = %s""",
                        (parent_uri, name, is_directory,
                         context_type, level, content,
                         abstract, overview, source_url,
                         meta_json, embedding, now, uri),
                    )
                    conn.commit()
                return existing["id"]
            finally:
                _pool.putconn(conn)
    else:
        with _pool.getconn() as conn:
            register_vector(conn)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO fs_nodes
                               (uri, parent_uri, name, is_directory, context_type,
                                level, content, abstract, overview, source_url,
                                metadata, embedding, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           RETURNING id""",
                        (uri, parent_uri, name, is_directory, context_type,
                         level, content, abstract, overview, source_url,
                         meta_json, embedding, now, now),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return row[0] if row else -1
            finally:
                _pool.putconn(conn)


def fs_delete_node(uri: str, recursive: bool = False) -> bool:
    """删除节点。recursive=True 删除所有子节点。"""
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                if recursive:
                    cur.execute(
                        "DELETE FROM fs_nodes WHERE uri = %s OR uri LIKE %s",
                        (uri, uri.rstrip("/") + "/%"),
                    )
                else:
                    cur.execute(
                        "DELETE FROM fs_nodes WHERE uri = %s",
                        (uri,),
                    )
                conn.commit()
                return cur.rowcount > 0
        finally:
            _pool.putconn(conn)


def fs_search_by_vector(
    query_embedding: list[float],
    parent_uri: str | None = None,
    limit: int = 10,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    """向量搜索指定前缀根目录下的节点。
    
    返回结果包含 _score 字段。
    """
    with _pool.getconn() as conn:
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                if parent_uri:
                    cur.execute(
                        """WITH scored AS (
                               SELECT id, uri, parent_uri, name, is_directory,
                                      context_type, level, content, abstract,
                                      overview, source_url, metadata,
                                      1 - (embedding <=> %s::vector) AS similarity,
                                      created_at, updated_at
                               FROM fs_nodes
                               WHERE embedding IS NOT NULL
                                 AND (uri = %s OR uri LIKE %s)
                           )
                           SELECT * FROM scored
                           WHERE similarity >= %s
                           ORDER BY similarity DESC
                           LIMIT %s""",
                        (query_embedding, parent_uri, parent_uri.rstrip("/") + "/%",
                         min_score, limit),
                    )
                else:
                    cur.execute(
                        """WITH scored AS (
                               SELECT id, uri, parent_uri, name, is_directory,
                                      context_type, level, content, abstract,
                                      overview, source_url, metadata,
                                      1 - (embedding <=> %s::vector) AS similarity,
                                      created_at, updated_at
                               FROM fs_nodes
                               WHERE embedding IS NOT NULL
                           )
                           SELECT * FROM scored
                           WHERE similarity >= %s
                           ORDER BY similarity DESC
                           LIMIT %s""",
                        (query_embedding, min_score, limit),
                    )
                return _dict_rows(cur)
        finally:
            _pool.putconn(conn)
