# Deep Research Agent — 多轮对话深度研究 Agent

基于 **LangGraph** + **FastAPI** + **PostgreSQL / pgvector** 的多轮对话深度研究系统。采用 **Viking L0 / L1 / L2 渐进压缩链** 在不同粒度上管理信息，配合 pgvector HNSW 向量索引实现跨会话语义记忆复用。

用户通过连续对话即可完成自动搜索、信息提取、报告生成与增量更新，无需切换会话。

## 系统架构

```
【前端 Vue 3】 ←── SSE + REST ──→ 【FastAPI 后端】 ←── LangGraph ──→ 【Viking Memory Store】
                                           ↕                                 ↕
                                    19 节点 Agent 调度                PostgreSQL + pgvector
```

### Agent 流程图

```
入口 → resolve_context → intent_classifier
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
          check_history    refine_section    simple_llm
                ▼               │                ▼
             planner            │          memory_llm → END
            ╱      ╲           │
      hitl_adjust   search ◄───┘
           │          │
           └────────→ │
                      ▼
                   scrape → context_mgr → dedup_rerank
                      │
                      ▼
                   clarify ──→ hitl_scope ──→ planner
                      │
                      ▼
                   assess ───────┬─────────┐
                    ╱╲           │         │
               hitl_conflict     │         │
                   │             ▼         ▼
                    │         search ◄─────┘ (最多 2 轮回环)
                   ▼
                synthesize
                   │
                   ▼
                hitl_outline
                   │
                   ▼
                report → memory → END
```

---

## 核心能力

### 4 条意图路径

| 意图 | 触发场景 | 执行链路 |
|------|----------|----------|
| `deep_research` | 首次提问 / 全新话题 | resolve → history → planner → search → scrape → context_mgr → dedup_rerank → clarify → assess(最多 2 轮) → synthesize → report → memory |
| `refine_section` | 追问已有章节详情 | 仅 search(限定章节) → scrape → context_mgr → dedup_rerank → synthesize → report(patch替换) → memory |
| `new_search_topic` | 提出新方面 | resolve → history → planner → search → scrape → context_mgr → dedup_rerank → clarify → assess → synthesize → report(patch追加) → memory |
| `simple_llm` | 总结 / 改写 / 追问细节 | 直接 LLM 回答，不搜索，仅写聊天记录（memory_llm） |

### 4 个 HITL 人工介入点

| 介入点 | 时机 | 判断依据 | 交互方式 |
|--------|------|----------|----------|
| 范围选择 `hitl_scope` | 查询过于宽泛时 | `clarify_node` 输出 `need_scope=true` | 复选维度 + 自定义文本 |
| 方向微调 `hitl_adjust` | 子查询不够精准时 | `planner_node` 输出 `need_adjust=true` | 文本框编辑子查询 |
| 冲突采信 `hitl_conflict` | 检测到矛盾观点时 | `assess_node` 输出 `has_conflict=true` | 单选 A/B/并列 |
| 大纲微调 `hitl_outline` | 报告生成前 | `synthesize_node` 输出 `need_outline_review=true` | 编辑 7 章大纲 |

> 所有 HITL 通过 LangGraph `interrupt` + `MemorySaver` checkpoint + `Command(resume=...)` 实现图执行的中断与恢复，用户不确认则不继续。

### assess 覆盖度评估回环

assess 节点判断当前收集信息是否充足：
- 核心维度有覆盖 + 数据点足够 → 进入 synthesize
- 缺少关键维度 → 自动生成新子查询，**回环到 search**，最多 2 轮
- 检测到冲突观点 → 自动抓取原文二次裁决；无法裁决时触发 HITL

### Viking L0 / L1 / L2 渐进压缩链

| 层级 | 大小 | 内容 | 生成方式 | 存储位置 | 检索方式 |
|------|------|------|----------|----------|----------|
| **L0** | ~100 token | 一句话摘要 | LLM 压缩 L1→L0 | research_tasks.l0_summary | SQL task_id 精确查 |
| **L1** | ~2000 token | 核心事实（含来源 URL） | LLM 压缩 L2→L1 | memory_store(level=L1) | pgvector HNSW 语义检索 |
| **L2** | 全文 | 网页原始 Markdown | trafilatura 提取 | memory_store(level=L2) | SQL source_url 精确查 |

> L1 写入前经 LLM 质量过滤（排除时效性/碎片化内容）+ 余弦 0.88 去重，保证记忆质量。

### SSE 4 事件协议

| event | 触发时机 | 前端行为 |
|-------|----------|----------|
| `chain` | 每个节点执行 | ChatHistory 内联渲染（thought / action / action_result 三种样式） |
| `text` | 首次 deep_research | 打字机流式渲染完整报告 |
| `patch` | refine / new_search | 增量替换或追加报告章节（携带 append 标记） |
| `hitl` | HITL 中断触发 | 弹出模态对话框 |

### 存储层设计

| 表 | 存储内容 | 索引 |
|----|----------|------|
| `chat_history` | 对话消息（展示用） | session_id + created_at |
| `research_tasks` | 任务 + 完整报告 + L0 | session_id / status |
| `memory_store` | L1 向量 + L2 原文 | HNSW(embedding) / task_id / source_url |
| `source_credibility` | 来源可信度评分（0-100） | domain / score |

> 仅 PostgreSQL + pgvector，无 Redis / 无 Chroma / 无 FAISS。

---

## 关键技术指标

| 指标 | 数值 |
|------|------|
| LangGraph 节点数 | 19（15 功能节点 + 4 HITL 中断节点） |
| 意图路由数 | 4 |
| 最大深度搜索轮次 | 2（会话级全局共享，route_assess 动态阈值） |
| 每轮子查询数 | 3-5 |
| 每子查询搜索结果数 | 3 |
| 最大抓取 URL 数 | 5 |
| LLM 最大重试次数 | 4 |
| LLM temperature | 0.3 |
| embedding 维度 | 1024（bge-m3） |
| embedding max_length | 512 |
| L1 去重余弦阈值 | 0.88 |
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
| API 框架 | FastAPI >= 0.135 |
| LLM | DeepSeek API (deepseek-chat) |
| 数据库 | PostgreSQL 15+ + pgvector 0.3+ |
| 文本嵌入 | bge-m3 (1024 维，HuggingFace 本地) |
| 重排模型 | bge-reranker-v2-m3 (sentence-transformers，本地) |
| 搜索引擎 | DuckDuckGo |
| 网页提取 | trafilatura + httpx |
| 前端 | Vue 3 + Vite + Pinia + marked |
| 日志 | Loguru |
| 监控 | LangSmith |

---

## 项目结构

```
src/
├── main.py                  # 入口：uvicorn 启动
├── config.py                # Pydantic Settings 配置（环境变量加载）
├── models.py                # Pydantic 数据模型 & 枚举（Intent / TaskStatus / HITLMode / SSEEvent 等）
├── logging_config.py        # Loguru 配置
├── agent/
│   ├── state.py             # AgentState TypedDict（30+ 字段）
│   ├── nodes.py             # 15 功能节点 + 4 HITL 节点 + 3 路由函数
│   └── graph.py             # LangGraph StateGraph 编排
├── api/
│   ├── server.py            # FastAPI 应用 + CORS
│   ├── routes.py            # 6 REST 端点 + SSE 流 + HITL 回调恢复
│   ├── sse_manager.py       # 异步队列 SSE 管理
│   └── contexts.py          # contextvars 流式回调
├── db/
│   └── postgres.py          # 4 表 CRUD + pgvector HNSW 语义检索
├── llm/
│   └── client.py            # DeepSeek API 封装（同步 + 流式 + 4 次重试）
├── local_models/
│   ├── embedder.py          # bge-m3 嵌入（无 sentence-transformers 依赖）
│   └── reranker.py          # bge-reranker-v2-m3 重排
├── search/
│   ├── engine.py            # 搜索接口抽象
│   ├── duckduckgo.py        # DuckDuckGo 搜索
│   └── scraper.py           # 网页抓取（httpx + trafilatura）
├── planner/
│   └── planner.py           # 指代消解 / 范围澄清 / 子查询分解
├── extract/
│   └── extractor.py         # L2→L1+L0 单 LLM 调用提取
├── synthesize/
│   ├── deduplicator.py      # 余弦相似度查重（阈值 0.88）
│   ├── ranker.py            # bge-reranker 排序
│   └── synthesizer.py       # 话题聚类 + 章节综合
├── report/
│   └── writer.py            # 7 章大纲生成 + Markdown 报告（同步+流式）
└── memory/
    ├── retriever.py         # L1 语义检索 + L2 精确检索
    └── credibility.py       # 来源信誉评分管理

frontend/src/
├── App.vue                  # 根组件
├── main.ts                  # Vue 应用入口
├── types.ts                 # TypeScript 类型定义
├── api/index.ts             # REST + SSE 客户端封装
├── stores/
│   ├── chatStore.ts         # 对话消息 + chain 事件
│   ├── reportStore.ts       # 报告 state + patch 合并逻辑
│   └── sessionStore.ts      # 侧边栏会话列表
├── composables/
│   ├── useSSE.ts            # SSE 连接解析
│   └── useHITL.ts          # HITL 弹窗控制
└── components/
    ├── Sidebar.vue          # 左侧会话列表
    ├── SessionList.vue      # 会话列表容器
    ├── SessionItem.vue      # 会话列表中单个条目
    ├── MainArea.vue         # 右侧主窗口容器
    ├── ChatHistory.vue      # 对话历史 + chain 事件内联渲染
    ├── QueryInput.vue       # 底部输入框
    └── HITLDialog.vue       # HITL 交互弹窗

migration/
└── 001_init.sql             # 4 表 DDL + pgvector HNSW 索引

scripts/
├── migrate_pgvector.py       # pgvector 迁移脚本
└── verify_migration.py      # 迁移验证脚本

tests/
├── test_db.py               # 数据库 CRUD 测试
├── test_extractor.py        # 提取器测试
├── test_memory.py           # 记忆检索测试
├── test_synthesize.py       # 综合器测试
├── test_writer.py           # 报告生成测试
└── test_integration.py      # 端到端集成测试
```

---

## 快速开始

### 前置依赖

- Python 3.11+
- PostgreSQL 15+（需安装 pgvector 扩展）
- bge-m3 + bge-reranker-v2-m3 模型文件
- DeepSeek API Key

### 1. 环境配置

```bash
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY、POSTGRES_DSN、模型路径
```

### 2. 数据库初始化

```bash
psql -d search_agent -f migration/001_init.sql
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
| `GET` | `/api/sessions` | 列出所有会话 |
| `GET` | `/api/sessions/{id}/history` | 加载会话历史（消息 + 报告） |
| `POST` | `/api/sessions` | 创建新会话 |
| `DELETE` | `/api/sessions/{id}` | 删除会话及其所有数据 |
| `POST` | `/api/research` | 发起研究（SSE 流式返回） |
| `POST` | `/api/hitl/callback` | HITL 用户确认回调，恢复图执行 |

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

---

## 关键架构决策

### 为什么要 4 条意图路径

单链路无法同时满足"快速回答"和"深度调研"两种需求。4 条路径使 Agent 可以根据用户意图选择最短执行路径：
- `simple_llm` 不经过任何搜索节点，延迟最低
- `refine_section` / `new_search_topic` 不走 assess 回环，单轮即止
- `deep_research` 才触发覆盖度评估循环（最多 2 轮，动态阈值）

### 为什么要 4 种 SSE 事件

`text` 和 `patch` 互斥：首次报告用 `text` 从头构建，后续增量更新用 `patch` 替换/追加章节，不重复发送已有内容。`chain` 展示实时思考链路，`hitl` 中断执行流，两个通道并行工作互不阻塞。

### 为什么要 pgvector HNSW 而非 Python 端计算

HNSW 索引在 10K+ 向量规模下查询延迟比 Python 余弦计算低 10x 以上（pgvector HNSW O(log n) vs 暴力扫描 O(n)）。PostgreSQL 内置检索避免数据跨进程传输，减少序列化开销。

### 为什么要 LLM 质量过滤而非规则过滤

规则（关键词黑名单、正则）无法识别"时效性信息"和"碎片化内容"等语义特征。LLM 判断基于语义理解，过滤准确率更高，且记忆库规模可控（只存有长期复用价值的内容）。

### 为什么 report_node 支持同步+流式双模式

`generate_report_stream` 在有 SSE callback 时流式输出（token 级别），前端实时渲染；无 callback 时（如 test 环境）返回完整 JSON。同一函数支持两种模式，代码复用且易于测试。

### 为什么要 MemorySaver 而非 PostgresPersister

MemorySaver 将 checkpoint 存在内存中，换页/重启后丢失。但对于个人 Demo 项目，宕机重启后从 PostgreSQL（report 字段 + sections 正则解析）恢复上下文已足够，无需额外部署持久化 checkpointer。

---

## 开发指南

### 测试

```bash
pytest tests/ -v
```

### 日志

```bash
# DEBUG 级别（默认）
python -m src.main

# INFO 级别
LOG_LEVEL=INFO python -m src.main
```

### LangSmith 监控

设置 `.env` 中的 `LANGCHAIN_TRACING_V2=true` 和 `LANGCHAIN_API_KEY`，LangSmith 控制台即可查看完整 Agent 执行轨迹（节点耗时、Token 消耗、中断状态）。

### HITL 中断恢复原理

```python
# 触发中断（nodes.py）
interrupt({"mode": "scope_select", "session_id": "...", "options": {...}})

# 检测中断（routes.py）
if "__interrupt__" in event:
    await sse.put_hitl(...)

# 恢复执行
graph.astream(Command(resume={"dimensions": ["市场规模"]}), config)
```




分析完毕。问题根因是 appendStream 的首次替换逻辑 + marked.parse CSP 拦截可能性 + 历史加载重复风险。具体诊断：
appendStream (chatStore.ts:33-39):
function appendStream(chunk: string) {
    if (!isStreaming.value) {           // ← 首次：replace
      isStreaming.value = true
      streamingText.value = chunk
    } else {
      streamingText.value += chunk      // ← 后续：append
    }
}
如果 SSE 连接中途因某种原因 finalizeStream() 重置了 isStreaming=false（例如错误恢复、重连），后续到达的 text 事件会用 = 替换而非 += 追加，导致内容截断或重复。
loadReport 去重 (MainArea.vue:15-27):
m.role === 'assistant' && m.content === report  // 精确字符串匹配
报告中有 footnotes 在 report_node 中拼接（nodes.py:678），而持久化的 sections 重建可能不含注脚 → 字符串不同 → loadReport 添加第二条重复消息。
修复方案
#	文件	问题	修复
1	chatStore.ts	appendStream 首次用 = 替换	改为始终用 +=，首次初始化 streamingText = ''
2	MainArea.vue	loadReport 精确匹配太脆弱	改为 includes 子串匹配 + 去重键（如长度阈值）
3	ChatHistory.vue	CSP 可能拦截 v-html	添加 marked.parse 异常兜底，出错时显示纯文本
4	ChatHistory.vue	无防抖，高频更新卡顿	streamingText 添加 requestAnimationFrame 节流
5	routes.py	patch 事件在 agent 完成后全量推送	不改（逻辑正确，仅用于持久化）