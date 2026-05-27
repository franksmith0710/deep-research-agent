# Deep Research Agent — 多轮对话深度研究 Agent

基于 **LangGraph** + **FastAPI** + **PostgreSQL / pgvector** 的多轮对话深度研究系统。采用 **Viking 双层记忆（L0 + L1）** 在不同粒度上管理信息，配合 pgvector HNSW 向量索引实现跨会话语义记忆复用。

用户通过连续对话即可完成自动搜索、信息提取、报告生成与增量更新，无需切换会话。

## 系统架构

```
【前端 Vue 3】 ←── SSE + REST ──→ 【FastAPI 后端】 ←── LangGraph ──→ 【Viking Memory Store】
                                           ↕                                 ↕
                                    19 节点 Agent 调度               PostgreSQL + pgvector
```

### Agent 流程图

```
入口 → resolve_context → intent_classifier
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
          check_history    refine_section    simple_llm
                ▼         (走 planner 后直连)       ▼
             planner         search           memory_llm → END
            ╱      ╲           │
      hitl_adjust   clarify ◄──┘
           │        ╱    ╲
           │       /      └──┐
           └─────→│    hitl_scope
                  │        │
                  └────────→ search
                              │
                           scrape → context_mgr → dedup_rerank
                              │
                     ┌────────┴────────┐
                     ▼                 ▼
               synthesize        assess ←──┐
              (refine 跳过 assess)   │      │
                     │              ╱ ╲     │
                     │             /   └────┘
                     │        hitl_conflict
                     │            │
                      └──────┬─────┘
                             ▼
                   report(异步流式) → memory → END
```

**路径说明：**
- `deep_research`：完整路径 → `check_history → planner → clarify → search → scrape → context_mgr → dedup_rerank → assess(回环，最多 3 轮) → synthesize → report`
- `refine_section`：`check_history → planner → search → scrape → context_mgr → dedup_rerank → synthesize(跳过 assess) → report(patch 替换)`
- `new_search_topic`：`check_history → planner → clarify → search → scrape → context_mgr → dedup_rerank → synthesize(跳过 assess) → report(patch 追加)`
- `simple_llm`：直连 `simple_llm` → `memory_llm`，无搜索
- `_llm_fallback`：搜索全部失败时，`assess_node` 标记后跳 `synthesize_node` 直接 LLM 回答

---

## 核心能力

### 4 条意图路径

| 意图 | 触发场景 | 执行链路 |
|------|----------|----------|
| `deep_research` | 首次提问 / 全新话题 | resolve → history → planner → clarify → search → scrape → context_mgr → dedup_rerank → assess(最多 3 轮) → synthesize → report → memory |
| `refine_section` | 追问已有章节详情 | resolve → history → planner → search → scrape → context_mgr → dedup_rerank → synthesize(跳过 assess) → report(patch 替换) → memory |
| `new_search_topic` | 提出新方面 | resolve → history → planner → clarify → search → scrape → context_mgr → dedup_rerank → assess(单轮不回环) → synthesize → report(patch 追加) → memory |
| `simple_llm` | 总结 / 改写 / 追问细节 | 直接 LLM 回答，不搜索，仅写聊天记录（memory_llm） |

> **搜索失败降级：** 若所有 sub_query 搜索均失败，`assess_node` 标记 `_llm_fallback=true`，直接使用 LLM 已有知识回答，不阻塞流程。

### 4 个 HITL 人工介入点

| 介入点 | 时机 | 判断依据 | 交互方式 |
|--------|------|----------|----------|
| 范围选择 `hitl_scope` | 查询过于宽泛时 | `clarify_node` 输出 `need_scope=true` | 复选维度 + 自定义文本 |
| 方向微调 `hitl_adjust` | 子查询不够精准时 | `planner_node` 输出 `need_adjust=true` | 文本框编辑子查询 |
| 冲突采信 `hitl_conflict` | 检测到矛盾观点时 | `assess_node` 输出 `has_conflict=true` | 单选 A/B/并列 |


> 所有 HITL 通过 LangGraph `interrupt` + `MemorySaver` checkpoint + `Command(resume=...)` 实现图执行的中断与恢复，用户不确认则不继续。

### 自动冲突裁决机制

`assess_node` 检测到信息冲突后，自动执行多步裁决流程：

1. 收集冲突各方的来源 URL
2. 查询内存 LRU 缓存获取来源可信度评分（基于域名静态规则 + session 内成功/失败计数）
3. 重新抓取原文内容（`Scraper`）
4. 调用 LLM 进行事实核查（对比可信度、逻辑一致性、数据支撑）
5. 若能裁决 → 自动消解冲突，标记 `has_conflict=false`
6. 若无法裁决 → 保持冲突状态，触发 HITL 人工介入

### assess 覆盖度评估回环

assess 节点判断当前收集信息是否充足：
- 覆盖率 ≥ 70 → 信息足够，进入 synthesize
- 覆盖率 < 70 且轮次 < 3（即 round 0/1/2 三轮）→ 生成新子查询，**回环到 search**
- 覆盖率 < 70 且轮次 ≥ 3 → 强制进入 synthesize（防止死循环）
- 仅 `deep_research` 走回环；`refine_section` / `new_search_topic` 单轮即止

### Viking 双层记忆

记忆系统分两层，**完整报告（research_tasks.report）不属于记忆系统**，仅供前端展示和 patch 操作使用。

| 层级 | 范围 | 内容 | 写入时机 | 存储位置 | 检索用途 | 生命周期 |
|------|------|------|----------|----------|----------|----------|
| **L0** | 轮次间短期 | 本轮 query + 核心结论压缩摘要（≤100 字） | 每轮结束必存 | `research_tasks.l0_summary` | `resolve_context` 指代消解 / query 改写 | 单会话 |
| **L1** | 跨会话长期 | 结构化知识（entities / patterns），含 embedding | 仅 LLM 判断有知识时 | `fs_nodes(level=L1)` + pgvector HNSW | `check_history` 跨会话语义检索 | 跨会话永久 |

> L1 写入前经 LLM 质量过滤（排除时效性/碎片化内容）+ 双阈值去重。命中高相似度（≥0.88）时执行 UPDATE 覆盖，中等相似度（≥0.6）时 APPEND 拼接，低相似度则 ADD 新条目。保证记忆库时效性与知识密度。

### SSE 4 事件协议

| event | 触发时机 | 前端行为 |
|-------|----------|----------|
| `chain` | 每个节点执行 | ChatHistory 内联渲染（thought / action / action_result 三种样式） |
| `text` | 首次 deep_research | 打字机流式渲染完整报告（token 级别） |
| `patch` | refine / new_search | 增量替换或追加报告章节（携带 `append` 标记） |
| `hitl` | HITL 中断触发 | 弹出模态对话框 |

**SSE 实现架构：**
```python
# sse_manager.py — 基于 asyncio.Queue + EventSourceResponse
class SSEManager:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    async def event_generator(self) -> AsyncGenerator:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    def get_response(self) -> EventSourceResponse:
        return EventSourceResponse(self.event_generator())
```

### 存储层设计

| 表 | 存储内容 | 索引 |
|----|----------|------|
| `chat_history` | 对话消息（展示用） | session_id + created_at |
| `research_tasks` | 任务 + L0 摘要 + 完整报告（展示用） | session_id / status |
| `fs_nodes` | L1 结构化知识 + 向量嵌入（Viking 文件系统） | HNSW(embedding) / uri / parent_uri |

> 仅 PostgreSQL + pgvector，无 Redis / 无 Chroma / 无 FAISS。

**来源信誉规则（内存 LRU 缓存 + 静态域名规则）：**
- `.gov.cn` / `.edu.cn` 默认 `高`，其余域名默认 `中`
- 连续成功 3 次 → 升级（`中→高`）
- 失败 2 次 → 降级（`高→中` 或 `中→低`）
- 报告底部引用根据会话内累积表现显示可信度标签：`高` / `中` / `低`

---

## 关键技术指标

| 指标 | 数值 |
|------|------|
| LangGraph 节点数 | 19（15 功能节点 + 4 HITL 中断节点） |
| 条件路由函数数 | 4（route_by_intent / route_after_planner / route_after_rerank / route_assess） |
| 意图路由数 | 4 |
| 最大深度搜索轮次 | 3（会话级全局共享，assess_round 0/1/2 三轮） |
| 每轮子查询数 | 3-5 |
| 每子查询搜索结果数 | 3 |
| 最大抓取 URL 数 | 5 |
| LLM 最大重试次数 | 4（指数退避：2s, 3s, 5s, 9s） |
| LLM temperature | 0.3 |
| embedding 维度 | 1024（bge-m3） |
| embedding max_length | 512 |
| 记忆写入双阈值 | UPDATE ≥0.88 / APPEND ≥0.6 / ADD <0.6 |
| L1 检索最低相似度 | 0.7 |
| L1 检索返回数 | 3 |
| pgvector HNSW m | 16 |
| pgvector HNSW ef_construction | 200 |
| 数据库连接池 | 2-8 |
| 服务端口 | 8004 |
| 前端端口 | 5173（Vite Dev Server） |

---

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 语言 | Python >= 3.11 |
| Agent 框架 | LangGraph >= 1.1.8 |
| API 框架 | FastAPI >= 0.135 + SSE-Starlette |
| LLM | DeepSeek API (deepseek-chat, temperature=0.3) |
| LLM 客户端 | langchain-openai + langchain-core（兼容 OpenAI 协议） |
| 数据库 | PostgreSQL 15+ + pgvector 0.3+ + psycopg2-binary |
| 文本嵌入 | bge-m3 (1024 维，transformers 直接加载) |
| 重排模型 | bge-reranker-v2-m3 (sentence-transformers CrossEncoder) |
| 搜索引擎 | **MetasoSearch**（主要）+ DuckDuckGo（备选实现） |
| 网页提取 | trafilatura + httpx |
| 前端 | Vue 3 + Vite + Pinia + marked |
| 日志 | Loguru |
| 监控 | LangSmith |

---

## 项目结构

```
src/
├── main.py                  # 入口：uvicorn 启动（host=0.0.0.0:8004）
├── config.py                # Pydantic Settings 配置（环境变量加载）
├── models.py                # Pydantic 数据模型 & 枚举
├── logging_config.py        # Loguru 配置
├── agent/
│   ├── state.py             # AgentState TypedDict（27 字段）
│   ├── nodes.py             # 15 功能节点 + 4 HITL 节点 + 4 路由函数
│   └── graph.py             # LangGraph StateGraph 编排（19 节点 + 4 HITL 中断点）
├── api/
│   ├── server.py            # FastAPI 应用 + CORS
│   ├── routes.py            # 8 REST 端点 + SSE 流 + HITL 回调恢复
│   ├── sse_manager.py       # 异步队列 SSE 管理（4 种 event）
│   └── contexts.py          # contextvars 流式回调
├── db/
│   └── postgres.py          # 5 表 CRUD + pgvector HNSW 语义检索
├── fs/
│   ├── uri.py               # viking:// URI 解析（VikingURI dataclass）
│   ├── filesystem.py        # 文件系统操作：读写/目录管理/向量搜索
│   └── retriever.py         # 目录递归向量检索 + 收敛检测
├── llm/
│   └── client.py            # DeepSeek API 封装（chat / chat_stream / chat_json，4 次重试）
├── local_models/
│   ├── embedder.py          # bge-m3 嵌入（transformers 直接加载，无 sentence-transformers）
│   └── reranker.py          # bge-reranker-v2-m3 重排（sentence-transformers CrossEncoder）
├── search/
│   ├── engine.py            # 搜索接口抽象（SearchEngine ABC + SearchResultItem）
│   ├── metaso.py            # MetasoSearch 实现（metaso.cn API，主要搜索引擎）
│   ├── duckduckgo.py        # DuckDuckGo 实现（ddgs SDK，备选）
│   └── scraper.py           # 网页抓取（httpx + trafilatura，含重试/降级/JS 检测）
├── planner/
│   └── planner.py           # 指代消解 / 范围澄清 / 子查询分解
├── page_brief/
│   └── brief.py             # 网页关键要点提取（LLM 单次调用，替代原 extractor）
├── synthesize/
│   ├── ranker.py            # bge-reranker 排序
│   └── synthesizer.py       # 话题聚类 + 章节综合
├── report/
│   └── writer.py            # 7 章大纲生成 + Markdown 报告（流式/非流式双模式）
└── memory/
    ├── retriever.py         # L1 语义检索（调 fs 递归检索 user/memories/）
    ├── updater.py           # 长期记忆写入：报告→知识提取→双阈值去重（ADD/APPEND/UPDATE）
    └── credibility.py       # 来源信誉 LRU 缓存 + 静态域名规则

frontend/src/
├── App.vue                  # 根组件 + SSE 连接管理
├── main.ts                  # Vue 应用入口
├── types.ts                 # TypeScript 类型定义（SSEEvent / HITLEvent 等）
├── api/index.ts             # REST + SSE（XHR 流式解析）客户端封装
├── stores/
│   ├── chatStore.ts         # 对话消息 + chain 事件 + 状态轮询
│   ├── reportStore.ts       # 报告 state + patch 合并逻辑
│   └── sessionStore.ts      # 侧边栏会话列表、切换
├── composables/
│   ├── useSSE.ts            # SSE 连接解析（4 种 event 分发）
│   └── useHITL.ts          # HITL 弹窗控制
└── components/
    ├── Sidebar.vue          # 左侧会话列表（280px，深色背景）
    ├── SessionList.vue      # 会话列表容器
    ├── SessionItem.vue      # 单个条目（query 前 20 字 + 状态标签）
    ├── MainArea.vue         # 右侧主窗口容器
    ├── ChatHistory.vue      # 对话历史 + chain 事件内联渲染
    ├── QueryInput.vue       # 底部固定输入框，始终可用
    └── HITLDialog.vue       # HITL 交互弹窗（scope_select / conflict_resolve / direction_adjust）

migration/
├── 001_init.sql             # chat_history / research_tasks DDL
└── 002_viking_fs.sql        # fs_nodes 表 + pgvector HNSW 索引 DDL

tests/
├── test_db.py               # 数据库 CRUD 测试（8 项）
├── test_extractor.py        # 网页关键要点提取测试
├── test_memory.py           # 记忆检索 + 信誉分测试
├── test_synthesize.py       # 综合器 + 重排测试
├── test_writer.py           # 报告生成测试（7 章大纲）
└── test_integration.py      # 端到端集成测试（17 模块一次性验证）
```

---

## 快速开始

### 前置依赖

- Python 3.11+
- PostgreSQL 15+（需安装 pgvector 扩展）
- bge-m3 + bge-reranker-v2-m3 模型文件
- DeepSeek API Key（或兼容的 OpenAI API Key）

### 1. 环境配置

```bash
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY、POSTGRES_DSN、模型路径
```

### 2. 数据库初始化

```bash
psql -d search_agent -f migration/001_init.sql
psql -d search_agent -f migration/002_viking_fs.sql
```

### 3. 启动后端

```bash
python -m src.main
# 服务运行在 http://localhost:8004
```

### 4. 启动前端

```bash
cd frontend && npm install && npm run dev
# 前端运行在 http://localhost:5173（自动代理 /api 到 8004）
```

---

## API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/sessions` | 列出所有会话（query 前 20 字 + 状态标签） |
| `GET` | `/api/sessions/{id}/history` | 加载会话历史（消息 + 完整报告） |
| `GET` | `/api/sessions/{id}/status` | 获取会话当前状态（运行中 / HITL 等待 / 已恢复等） |
| `POST` | `/api/sessions` | 创建新会话（返回 8 位 uuid session_id） |
| `DELETE` | `/api/sessions/{id}` | 删除会话及其所有数据（级联清除 fs_nodes / research_tasks / chat_history） |
| `POST` | `/api/research` | 发起研究（SSE 流式返回，同 session 串行排队） |
| `POST` | `/api/research/{id}/cancel` | 取消正在运行的研究任务（支持 HITL 等待中和运行中两种状态） |
| `POST` | `/api/hitl/callback` | HITL 用户确认回调，恢复图执行（通过 `Command(resume=...)` 恢复） |

---

## SSE 事件协议

```bash
event: chain
data: {"type": "thought", "node": "resolve_context", "content": "正在理解你的问题", "ts": "..."}

event: chain
data: {"type": "action", "node": "search", "content": "正在搜索相关信息...", "ts": "..."}

event: chain
data: {"type": "action_result", "node": "scrape", "content": "抓取完成，共 10 个页面", "ts": "..."}

event: text
data: {"content": "# 报告标题\n\n## 摘要\n..."}  # 流式追加，打字机渲染

event: patch
data: {"section": "核心现状与关键数据", "content": "## ...", "append": true, "ts": "..."}

event: hitl
data: {"mode": "scope_select", "session_id": "abc123", "options": {"dimensions": ["市场规模", "技术架构"]}, "ts": "..."}
```

**patch 合并规则：**
- `append=true`：追加到该章节末尾
- `append=false`：替换整个章节（section 不存在时追加到末尾）

---

## 关键架构决策

### 为什么要 4 条意图路径

单链路无法同时满足"快速回答"和"深度调研"两种需求。4 条路径使 Agent 可以根据用户意图选择最短执行路径：
- `simple_llm` 不经过任何搜索节点，延迟最低
- `refine_section` / `new_search_topic` 不走 assess 回环，单轮即止
- `deep_research` 才触发覆盖度评估循环（最多 3 轮，动态阈值 70）

### 为什么要 4 种 SSE 事件

`text` 和 `patch` 互斥：首次报告用 `text` 从头构建，后续增量更新用 `patch` 替换/追加章节，不重复发送已有内容。`chain` 展示实时思考链路，`hitl` 中断执行流，两个通道并行工作互不阻塞。

### 为什么要 pgvector HNSW 而非 Python 端计算

HNSW 索引在 10K+ 向量规模下查询延迟比 Python 余弦计算低 10x 以上（pgvector HNSW O(log n) vs 暴力扫描 O(n)）。PostgreSQL 内置检索避免数据跨进程传输，减少序列化开销。

### 为什么要 LLM 质量过滤而非规则过滤

规则（关键词黑名单、正则）无法识别"时效性信息"和"碎片化内容"等语义特征。LLM 判断基于语义理解，过滤准确率更高，且记忆库规模可控（只存有长期复用价值的内容）。

### 为什么 report_node 是异步函数

`report_node` 需要在 SSE 回调激活时以 token 级别流式输出报告。`nodes.py:652` 声明为 `async def report_node`，内部调用 `generate_report_stream` 实现流式/非流式双模式：
- 有 `stream_callback_var` → 逐 token 回调，前端打字机渲染
- 无 callback（如 test 环境）→ 返回完整 JSON，代码复用

### 为什么要 MemorySaver 而非 PostgresPersister

MemorySaver 将 checkpoint 存在内存中，换页/重启后丢失。但对于个人 Demo 项目，宕机重启后从 PostgreSQL（report 字段 + sections 正则解析）恢复上下文已足够，无需额外部署持久化 checkpointer。

### 为什么选择 MetasoSearch 而非纯 DuckDuckGo

MetasoSearch 提供稳定结构化引用数据（`title` + `link`），搜索结果质量更高，适合中文搜索场景。DuckDuckGo 作为备选实现保留 `src/search/duckduckgo.py`，通过 `SearchEngine` 抽象接口可随时切换。

### 为什么要自动冲突裁决而非全部走 HITL

简单冲突（如数据口径差异）由 LLM + 可信度评分自动解决，减少用户中断体验。仅在 LLM 无法裁决时（如双方来源均权威但观点对立）才触发 HITL，平衡自动化与可靠性。

### 为什么搜索失败时不阻断流程

`assess_node` 在 `search_all_failed=True` 时标记 `_llm_fallback=true`，跳转至 `synthesize_node` 直接调用 LLM 已有知识回答。保证即使用户搜索配额耗尽或搜索引擎不可用，系统仍能给出有用响应。

---

## 开发指南

### 测试

```bash
pytest tests/ -v
```

### 日志

```bash
# DEBUG 级别（默认，含 LLM token 级延迟日志）
python -m src.main

# INFO 级别（仅关键事件）
LOG_LEVEL=INFO python -m src.main
```

### LangSmith 监控

设置 `.env` 中的 `LANGCHAIN_TRACING_V2=true` 和 `LANGCHAIN_API_KEY`，LangSmith 控制台即可查看完整 Agent 执行轨迹（节点耗时、Token 消耗、中断状态）。

### LLM 重试策略

```python
# llm/client.py — 指数退避 + 4 次重试
for attempt in range(max_retries):
    try:
        resp = client.invoke(messages)
        return resp.content
    except (RateLimitError, APIConnectionError, APITimeoutError):
        wait = min(2 ** attempt + 1, 10)  # 2s, 3s, 5s, 9s
        time.sleep(wait)
    except APIError as e:
        if 500 <= e.status_code < 600:
            time.sleep(min(2 ** attempt + 1, 10))  # 仅重试服务端错误
        else:
            break  # 客户端错误（4xx）直接失败
```

### HITL 中断恢复原理

```python
# 触发中断（nodes.py）
interrupt({"mode": "scope_select", "session_id": "...", "options": {...}})

# 检测中断（routes.py）
if "__interrupt__" in event:
    await sse.put_hitl(...)

# 恢复执行（routes.py）
graph.astream(Command(resume={"dimensions": ["市场规模"]}), config)
```

### 会话级并发控制

每个 session 的 research 请求通过 `asyncio.Lock` 串行化，防止并发 Agent 执行导致状态冲突：
```python
# routes.py:181
if lock.locked():
    raise HTTPException(status_code=429, detail="该会话正在处理中，请等待")
```

### HITL 超时清理

中断会话 600 秒无回调自动清理，防止资源泄漏（`routes.py:_cleanup_hitl_timeout`）。清理后该会话可重新发起研究请求。
