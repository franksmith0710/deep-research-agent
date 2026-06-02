"""全模块集成测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.postgres import init_pool, close_pool
init_pool()

# 1. config
from src.config import settings
assert settings.deepseek_model
print("[PASS] config")

# 2. models
from src.models import Intent, SSEEvent, ResearchRequest
assert len([e for e in Intent]) == 3
print("[PASS] models")

# 3. llm
from src.llm.client import chat
resp = chat([{"role": "user", "content": "Say OK in one word."}], timeout=10.0)
assert resp.strip().lower() == "ok" or "ok" in resp.lower()
print(f"[PASS] llm: {resp.strip()}")

# 4. embedder
from src.local_models.embedder import embed_text
emb = embed_text("测试")
assert len(emb) == 1024
print("[PASS] embedder (1024 dim)")

# 5. reranker
from src.local_models.reranker import rerank
r = rerank("测试", ["结果A", "结果B"])
assert len(r) == 2
print("[PASS] reranker")

# 6. search - 仅构造测试
from src.search.duckduckgo import DuckDuckGoSearch
s = DuckDuckGoSearch()
print("[PASS] search engine")

# 7. scraper
from src.search.scraper import Scraper
sc = Scraper()
sc.close()
print("[PASS] scraper")

# 8. planner
from src.planner.planner import resolve_query, generate_sub_queries
rq = resolve_query("市场规模方面再说详细点", "已有调研")
assert rq
print(f"[PASS] planner: {rq[:30]}")

# 10. ranker (原 11)
from src.synthesize.ranker import rank_findings
rf = rank_findings("AI", [{"topic": "市场", "content": "AI市场规模"}])
assert len(rf) == 1
print("[PASS] ranker")

# 13. writer
import asyncio
from src.report.writer import generate_outline
ol = asyncio.run(generate_outline("AI Agent市场"))
assert len(ol) == 7
print(f"[PASS] writer (7 chapters)")

# 14. memory
from src.memory.credibility import update_source_credibility, get_source_tag
update_source_credibility("https://test.com", True)
tag = get_source_tag("https://test.com")
assert tag in ("高", "中", "低", "未知")
print(f"[PASS] credibility: {tag}")

# 15. graph
from src.agent.graph import build_graph
g = build_graph()
assert len(list(g.nodes.keys())) == 13
print(f"[PASS] graph ({len(list(g.nodes.keys()))} nodes)")

# 16. SSE
from src.api.sse_manager import SSEManager
sse = SSEManager()
print("[PASS] SSE manager")

# 17. server
from src.api.server import app
assert app.title == "Deep Research Agent"
routes = [r.path for r in app.routes if hasattr(r, "path")]
assert "/api/sessions" in routes
print(f"[PASS] server ({len(routes)} routes)")

close_pool()
print("\n=== ALL MODULES PASSED ==="")
