# 开发进度

## Step 1 — 项目骨架 ✅
- 创建 `pyproject.toml`（13 依赖 + Python >=3.11）
- 创建 `.env.example`（配置模板，不含真实密钥）
- 创建 `src/config.py`（Pydantic Settings，加载 `.env`，`extra="ignore"` 兼容旧字段）
- 创建 `src/main.py`（uvicorn 入口，port 8004，reload）
- 创建 `src/__init__.py` + 9 个子包 `__init__.py`
- **测试**: `python -c "from src.config import settings"` → OK

---

## Step 2 — 数据类型 models.py ✅
- SSE 事件（chain/text/patch/hitl）
- HITL 请求/状态 + ContextType
- 搜索/抓取/摘要
- 来源信誉 + 记忆 + FSNode
- API 请求/响应
- **测试**: `python -c "from src.models import Intent, SSEEvent"` → OK

---

## Step 3 — PostgreSQL CRUD ✅
- db/postgres.py（连接池 + chat_history/research_tasks/fs_nodes/source_credibility 完整 CRUD）
- pgvector 保留在 fs_nodes.embedding + HNSW 索引
- **测试:** `python tests/test_db.py` → 8 项全 PASS

---

## Step 4 — LLM 客户端 ✅
- llm/client.py（OpenAI 兼容封装，retry 4 次，支持 chat + chat_stream + chat_json）
- **测试:** `chat()` 单次调用成功返回

---

## Step 5 — 本地模型 ✅
- local_models/embedder.py（bge-m3，transformers 直接加载，避 sentence-transformers v5 兼容问题）
- local_models/reranker.py（bge-reranker-v2-m3，使用 sentence-transformers CrossEncoder）
- **测试:** embedder 1024 维 → OK, reranker → OK

---

## Step 6 — 搜索层 ✅
- search/engine.py（SearchEngine ABC + SearchResultItem）
- search/metaso.py（MetasoSearch 实现，主要搜索引擎）
- search/duckduckgo.py（DuckDuckGo 实现，备选）
- search/scraper.py（httpx + trafilatura，含重试/降级/JS 检测）

---

## Step 7 — Planner ✅
- planner/planner.py（resolve_query + need_clarification + generate_sub_queries）
- **测试:** 指代消解 → OK, 子查询生成 → OK

## Step 8 — 网页简报 ✅
- page_brief/brief.py（从网页原文提取 key points，替代原 extractor）
- **测试:** 200 字输入 → 提取 2 条发现 → OK

## Step 9 — 去重 + 重排 ✅
- 去重逻辑内联在 nodes.py dedup_rerank_node（torch 余弦相似度 >0.88）
- synthesize/ranker.py（bge-reranker-v2-m3 排序）
- **测试:** rerank 结果排序正确

## Step 10 — 综合 + 报告 ✅
- synthesize/synthesizer.py（按主题聚类综合）
- report/writer.py（大纲 + 完整报告流式生成，7 章固定结构）
- **测试:** generate_outline → 7 章 → OK

## Step 11 — 记忆系统 ✅
- memory/retriever.py（L1 语义检索，调 fs 递归检索 user/memories/）
- memory/updater.py（报告→知识提取→双阈值去重 ADD/APPEND/UPDATE）
- memory/credibility.py（信誉分更新 + 标签查询）
- **测试:** credibility 更新 + 查询 → OK

## Step 12 — LangGraph 图 ✅
- agent/state.py（AgentState TypedDict + make_initial_state，27 字段）
- agent/nodes.py（15 节点 + 4 路由函数 + assess 回环 + 自动冲突裁决）
- agent/graph.py（StateGraph 编译，4 条件边 + MemorySaver checkpoint）
- **测试:** graph 编译 → 15 节点全注册 → OK

## Step 13 — API + SSE ✅
- api/sse_manager.py（4 种 SSE event：chain/text/patch/hitl）
- api/routes.py（8 个 REST 端点 + 异步 agent 运行 + HITL 超时清理）
- api/contexts.py（contextvars 流式回调）
- api/server.py（FastAPI 应用 + CORS）
- **测试:** server 启动 → 8 路由注册 → OK

---

## Step 14 — 前端 ✅
- 项目结构：package.json / vite.config.ts / index.html / main.ts
- 类型定义：types.ts（SSEvent / ChatMessage / HITLEvent 等）
- API 层：api/index.ts（会话 CRUD + SSE XHR 流式解析 + 状态轮询）
- Stores：sessionStore / chatStore（含 reportStore patch 合并逻辑）
- Composables：useSSE / useHITL
- 8 组件：App / Sidebar / SessionList / SessionItem / MainArea / ChatHistory / QueryInput / HITLDialog

---

## Step 15 — 测试 ✅
- tests/test_db.py — 数据库 CRUD 8 项全 PASS
- tests/test_extractor.py — 提取测试
- tests/test_synthesize.py — rerank 排序验证
- tests/test_writer.py — 7 章大纲生成
- tests/test_memory.py — 信誉分更新 + 查询
- tests/test_integration.py — 全部 17 模块一次性验证 PASS

---

## Step 16 — Viking 文件系统 ✅
- fs/uri.py（viking:// URI 解析）
- fs/filesystem.py（读写/目录管理/向量搜索）
- fs/retriever.py（目录递归向量检索 + 收敛检测）
- migration/002_viking_fs.sql（fs_nodes 表 + HNSW 索引）
