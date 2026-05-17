from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool

from src.config import settings

# 全局连接池
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool(minconn: int = 2, maxconn: int = 8) -> None:
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn, maxconn, settings.postgres_dsn,
        client_encoding='utf8'
    )


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search_memory_by_vector(
    query_embedding: list[float], limit: int = 5, min_score: float = 0.7
) -> list[dict[str, Any]]:
    """内存计算余弦相似度，替代 pgvector <=> 操作符"""
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, task_id, content, source_url, topic, embedding
                       FROM memory_store
                       WHERE level = 'L1' AND embedding IS NOT NULL"""
                )
                rows = cur.fetchall()
        finally:
            _pool.putconn(conn)

    scored = []
    for row in rows:
        emb = row.get("embedding")
        if emb is None:
            continue
        sim = _cosine_similarity(query_embedding, emb)
        if sim >= min_score:
            row["similarity"] = round(sim, 4)
            scored.append(row)
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:limit]


def search_memory_by_topic(
    query_embedding: list[float], topic: str, limit: int = 2
) -> list[dict[str, Any]]:
    """按 topic 精确过滤 + 内存重排序"""
    with _pool.getconn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, task_id, content, source_url, topic, embedding
                       FROM memory_store
                       WHERE level = 'L1' AND topic = %s AND embedding IS NOT NULL""",
                    (topic,),
                )
                rows = cur.fetchall()
        finally:
            _pool.putconn(conn)

    scored = []
    for row in rows:
        emb = row.get("embedding")
        if emb is None:
            continue
        sim = _cosine_similarity(query_embedding, emb)
        row["similarity"] = round(sim, 4)
        scored.append(row)
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:limit]


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
