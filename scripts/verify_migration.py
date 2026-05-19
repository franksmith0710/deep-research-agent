"""验证 pgvector 迁移结果。"""
import sys
sys.path.insert(0, '.')
from src.config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_dsn)
cur = conn.cursor()

cur.execute("SELECT oid, extname, extversion FROM pg_extension WHERE extname = 'vector'")
r = cur.fetchone()
print(f'Extension: {r[1]} v{r[2]}' if r else 'Extension: NOT FOUND')

cur.execute("""
    SELECT column_name, data_type, udt_name
    FROM information_schema.columns
    WHERE table_name = 'memory_store' AND column_name = 'embedding'
""")
r = cur.fetchone()
print(f'Column: {r[0]} type={r[1]} udt={r[2]}')

cur.execute("""
    SELECT indexname, indexdef FROM pg_indexes
    WHERE tablename = 'memory_store' AND indexdef LIKE '%hnsw%'
""")
r = cur.fetchone()
if r:
    print(f'Index: {r[0]}')
    print(f'Def: {r[1][:100]}')
else:
    print('HNSW index: NOT FOUND')

cur.close()
conn.close()
