"""清空数据并迁移 memory_store 到 pgvector。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings

import psycopg2

conn = psycopg2.connect(settings.postgres_dsn)
conn.autocommit = True
cur = conn.cursor()

print("1/4 清空所有表...")
cur.execute("TRUNCATE chat_history, research_tasks, source_credibility, memory_store RESTART IDENTITY CASCADE;")

print("2/4 启用 pgvector 扩展...")
cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

print("3/4 转换 embedding 列类型...")
cur.execute("ALTER TABLE memory_store ALTER COLUMN embedding TYPE vector(1024) USING embedding::vector;")

print("4/4 创建 HNSW 索引...")
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_memory_embedding
    ON memory_store USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);
""")

cur.close()
conn.close()
print("全部完成 ✓")
