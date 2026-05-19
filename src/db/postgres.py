from __future__ import annotations

from contextlib import asynccontextmanager
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


@asynccontextmanager
async def get_conn():
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        yield conn
        _pool.putconn(conn)
    except Exception:
        _pool.putconn(conn)
        raise


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
                    """SELECT DISTINCT ON (rt.session_id)
                           rt.session_id,
                           rt.query AS query_preview,
                           rt.status,
                           rt.updated_at
                       FROM research_tasks rt
                       ORDER BY rt.session_id, rt.updated_at DESC"""
                )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


def delete_session_data(session_id: str) -> None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id FROM research_tasks WHERE session_id = %s",
                    (session_id,),
                )
                task_ids = [row[0] for row in cur.fetchall()]
            if task_ids:
                placeholders = ",".join(["%s"] * len(task_ids))
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM memory_store WHERE task_id IN ({placeholders})",
                        tuple(task_ids),
                    )
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


# ── memory_store ──────────────────────────────────────────────────────

def insert_memory(
    task_id: int,
    level: str,
    content: str,
    source_url: str | None = None,
    topic: str | None = None,
    embedding: list[float] | None = None,
) -> int:
    with _pool.getconn() as conn:
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO memory_store (task_id, level, content, source_url, topic, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (task_id, level, content, source_url, topic, embedding),
                )
                row = cur.fetchone()
                conn.commit()
                return row[0] if row else -1
        finally:
            _pool.putconn(conn)


def update_memory(id: int, content: str) -> None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE memory_store SET content = %s, created_at = NOW() WHERE id = %s",
                    (content, id),
                )
                conn.commit()
        finally:
            _pool.putconn(conn)


def search_memory_by_vector(
    query_embedding: list[float], limit: int = 5, min_score: float = 0.7
) -> list[dict[str, Any]]:
    """pgvector HNSW 余弦相似度搜索"""
    with _pool.getconn() as conn:
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """WITH scored AS (
                           SELECT id, task_id, content, source_url, topic,
                                  1 - (embedding <=> %s::vector) AS similarity
                           FROM memory_store
                           WHERE level = 'L1' AND embedding IS NOT NULL
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


def search_memory_by_topic(
    query_embedding: list[float], topic: str, limit: int = 2
) -> list[dict[str, Any]]:
    """按 topic 精确过滤 + pgvector 余弦相似度排序"""
    with _pool.getconn() as conn:
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """WITH scored AS (
                           SELECT id, task_id, content, source_url, topic,
                                  1 - (embedding <=> %s::vector) AS similarity
                           FROM memory_store
                           WHERE level = 'L1' AND topic = %s AND embedding IS NOT NULL
                       )
                       SELECT * FROM scored
                       ORDER BY similarity DESC
                       LIMIT %s""",
                    (query_embedding, topic, limit),
                )
                return _dict_rows(cur)
        finally:
            _pool.putconn(conn)


def get_l2_by_url(source_url: str) -> list[dict[str, Any]]:
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM memory_store WHERE level = 'L2' AND source_url = %s ORDER BY created_at DESC",
                    (source_url,),
                )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


def get_memories_by_task(task_id: int, level: str | None = None) -> list[dict[str, Any]]:
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if level:
                    cur.execute(
                        "SELECT * FROM memory_store WHERE task_id = %s AND level = %s",
                        (task_id, level),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM memory_store WHERE task_id = %s",
                        (task_id,),
                    )
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


# ── source_credibility ────────────────────────────────────────────────

def upsert_credibility(url: str, domain: str, success: bool) -> None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO source_credibility (url, domain, score, access_count, last_status)
                       VALUES (%s, %s, %s, 1, %s)
                       ON CONFLICT (url) DO UPDATE SET
                         access_count = source_credibility.access_count + 1,
                         score = GREATEST(0, LEAST(100,
                           source_credibility.score + CASE WHEN %s THEN 2 ELSE -5 END
                         )),
                         last_status = %s""",
                    (
                        url,
                        domain,
                        52 if success else 45,
                        "success" if success else "fail",
                        success,
                        "success" if success else "fail",
                    ),
                )
                conn.commit()
        finally:
            _pool.putconn(conn)


def get_credibility(url: str) -> dict[str, Any] | None:
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM source_credibility WHERE url = %s",
                    (url,),
                )
                return cur.fetchone()
        finally:
            _pool.putconn(conn)
