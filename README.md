# Deep Research Agent — 多轮对话深度研究 Agent

基于 **LangGraph** + **FastAPI** + **PostgreSQL / pgvector** 的多轮对话深度研究系统。采用 **Viking 双层记忆（L0 + L1）** 在不同粒度上管理信息，配合 pgvector HNSW 向量索引实现跨会话语义记忆复用。

用户通过连续对话即可完成自动搜索、信息提取、报告生成与增量更新，无需切换会话。

## 系统架构

```
【前端 Vue 3】 ←── SSE + REST ──→ 【FastAPI 后端】 ←── LangGraph ──→ 【Viking Memory Store】
                                           ↕                                 ↕
                                     13 节点 Agent 调度               PostgreSQL + pgvector
```

### Agent 流程图

```
入口 → resolve_context → intent_classifier
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
          check_history    refine_section    simple_llm
                ▼         (跳过 clarify)          ▼
             planner        → search          memory_llm → END
                │               ↑
                ▼               │
            clarify ◄───────────┘
            ╱      ╲
           │    hitl_scope
           │        │
           └────────→ search
                       │
                    scrape → context_mgr → dedup_rerank
                       │
               ┌────────┴────────┐
               ▼                 ▼
         synthesize           assess ←──────┐
        (refine/new_search      │  │        │
         跳过 assess)          ╱ ╲  └──────┘
               │              /   └──→ search (回环)
               │              │
                └──────┬──────┘
                       ▼
             report(异步流式) → memory → END
```

**路径说明：**
- `deep_research`：完整路径 → `check_history → planner → clarify → search → scrape → context_mgr → dedup_rerank → assess(回环，最多 3 轮) → synthesize → report`
- `refine_section`：`check_history → planner → search → scrape → context_mgr → dedup_rerank → synthesize(跳过 assess) → report(patch 替换)`
- `simple_llm`：直连 `simple_llm` → `memory_llm`，无搜索
- `_llm_fallback`：搜索全部失败时，`assess_node` 标记后跳 `report_node` 直接 LLM 回答

---

## 核心能力

### 3 条意图路径

| 意图 | 触发场景 | 执行链路 |
|------|----------|----------|
| `deep_research` | 首次提问 / 全新话题 | resolve → history → planner → clarify → search → scrape → context_mgr → dedup_rerank → assess(最多 3 轮) → synthesize → report → memory |
| `refine_section` | 追问已有章节详情 | resolve → history → planner → search → scrape → context_mgr → dedup_rerank → synthesize(跳过 assess) → report(patch 替换) → memory |
| `simple_llm` | 总结 / 改写 / 追问细节 | 直接 LLM 回答，不搜索，仅写聊天记录（memory_llm） |

> **搜索失败降级：** 若所有 sub_query 搜索均失败，`assess_node` 标记 `_llm_fallback=true`，直接使用 LLM 已有知识回答，不阻塞流程。

### 1 个 HITL 人工介入点

| 介入点 | 时机 | 判断依据 | 交互方式 |
|--------|------|----------|----------|
| 范围选择 `hitl_scope` | 查询过于宽泛时 | `clarify_node` 输出 `need_scope=true` | 复选维度 + 自定义文本 |


> 所有 HITL 通过 LangGraph `interrupt` + `MemorySaver` checkpoint + `Command(resume=...)` 实现图执行的中断与恢复，用户不确认则不继续。

### assess 覆盖度评估回环

assess 节点判断当前收集信息是否充足：
- 覆盖率 ≥ 60 → 信息足够，进入 synthesize
- 覆盖率 < 70 且轮次 < 3（即 round 0/1/2 三轮）→ 生成新子查询，**回环到 search**
- 覆盖率 < 70 且轮次 ≥ 3 → 强制进入 synthesize（防止死循环）
- 仅 `deep_research` 走回环；`refine_section` 单轮即止

### Viking 双层记忆

记忆系统分两层，**完整报告（research_tasks.report）不属于记忆系统**，仅供前端展示和 patch 操作使用。

| 层级 | 范围 | 内容 | 写入时机 | 存储位置 | 检索用途 | 生命周期 |
|------|------|------|----------|----------|----------|----------|
| **L0** | 轮次间短期 | 本轮 query + 核心结论压缩摘要（≤100 字） | 每轮结束必存 | `research_tasks.l0_summary` | `resolve_context` 指代消解 / query 改写 | 单会话 |
| **L1** | 跨会话长期 | 结构化知识（entities / patterns），含 embedding | 仅 LLM 判断有知识时 | `fs_nodes(level=L1)` + pgvector HNSW | `check_history` 跨会话语义检索 | 跨会话永久 |

> L1 写入前经 LLM 质量过滤（排除时效性/碎片化内容）+ 双阈值去重。命中高相似度（≥0.8）时执行 UPDATE 覆盖，中等相似度（≥0.6）时 APPEND 拼接，低相似度则 ADD 新条目。保证记忆库时效性与知识密度。

### SSE 4 事件协议

| event | 触发时机 | 前端行为 |
|-------|----------|----------|
| `chain` | 每个节点执行 | ChatHistory 内联渲染（thought / action / action_result 三种样式） |
| `text` | 首次 deep_research | 打字机流式渲染完整报告（token 级别） |
| `patch` | refine / new_search | 增量替换或追加报告章节（携带 `append` 标记） |
| `hitl` | HITL 中断触发 | 弹出模态对话框 |

### 存储层设计

| 表 | 存储内容 |
|----|----------|
| `chat_history` | 对话消息 |
| `research_tasks` | 任务 + 报告 |
| `fs_nodes` | L1 结构化知识（含向量嵌入） |

> 仅 PostgreSQL + pgvector，无额外中间件。

---

## 关键技术指标

| 指标 | 数值 |
|------|------|
| LangGraph 节点数 | 13（12 功能 + 1 HITL） |
| 条件路由函数 | 3 |
| 意图路径 | 4 |
| 最大搜索轮次 | 3 |
| 每轮子查询数 | 3-5 |
| LLM | DeepSeek-chat, temp=0.3, 4 次重试 |
| embedding | bge-m3（1024 维） |
| 记忆写入双阈值 | UPDATE ≥0.8 / APPEND ≥0.6 / ADD <0.6 |
| L1 检索 | min_score=0.7, limit=3 |
| 服务端口 | 8004 / 前端 5173 |

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
├── main.py                  # 入口
├── config.py                # 配置
├── models.py                # 数据模型
├── logging_config.py        # 日志
├── agent/
│   ├── state.py             # AgentState（33 字段）
│   ├── nodes.py             # 12 功能 + 1 HITL 节点 + 3 路由
│   └── graph.py             # 图编排（13 节点）
├── api/
│   ├── server.py            # FastAPI
│   ├── routes.py            # REST + SSE
│   ├── sse_manager.py       # SSE 管理
│   └── contexts.py          # 流式回调
├── db/
│   └── postgres.py          # PostgreSQL + pgvector CRUD
├── fs/
│   ├── uri.py               # viking:// URI
│   ├── filesystem.py        # 读写/目录/向量搜索
│   └── retriever.py         # 递归向量检索
├── llm/
│   └── client.py            # DeepSeek API 封装
├── local_models/
│   ├── embedder.py          # bge-m3 嵌入
│   └── reranker.py          # bge-reranker 重排
├── search/
│   ├── engine.py            # 搜索抽象接口
│   ├── metaso.py            # MetasoSearch
│   ├── duckduckgo.py        # DuckDuckGo 备选
│   └── scraper.py           # 网页抓取
├── planner/
│   └── planner.py           # 指代消解/子查询
├── page_brief/
│   └── brief.py             # 网页摘要
├── synthesize/
│   ├── ranker.py            # 重排排序
│   └── synthesizer.py       # 话题聚类
├── report/
│   └── writer.py            # Markdown 报告生成
└── memory/
    ├── retriever.py         # L1 语义检索
    ├── updater.py           # 长期记忆写入
    └── credibility.py       # 来源信誉

frontend/src/
├── App.vue                  # 根组件
├── main.ts                  # 入口
├── types.ts                 # TS 类型定义
├── api/index.ts             # REST + SSE 客户端
├── stores/
│   ├── chatStore.ts         # 对话消息 + 状态轮询
│   ├── reportStore.ts       # 报告 + patch
│   └── sessionStore.ts      # 会话列表
├── composables/
│   ├── useSSE.ts            # SSE 事件分发
│   └── useHITL.ts          # HITL 弹窗
└── components/
    ├── Sidebar.vue          # 侧边栏
    ├── SessionList.vue      # 会话列表
    ├── SessionItem.vue      # 会话条目
    ├── MainArea.vue         # 主窗口
    ├── WelcomeSuggestions.vue  # 新会话建议
    ├── ChatHistory.vue      # 对话历史
    ├── QueryInput.vue       # 输入框
    └── HITLDialog.vue       # HITL 弹窗

migration/
├── 001_init.sql             # chat_history / research_tasks
└── 002_viking_fs.sql        # fs_nodes + pgvector HNSW

tests/
├── test_db.py               # 数据库 CRUD
├── test_extractor.py        # 网页提取
├── test_memory.py           # 记忆检索
├── test_synthesize.py       # 综合器
├── test_writer.py           # 报告生成
└── test_integration.py      # 端到端集成测试
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

## 关键架构决策

### 为什么要 4 条意图路径

单链路无法同时满足"快速回答"和"深度调研"两种需求。4 条路径使 Agent 可以根据用户意图选择最短执行路径：
- `simple_llm` 不经过任何搜索节点，延迟最低
- `refine_section` 不走 assess 回环，单轮即止
- `deep_research` 才触发覆盖度评估循环（最多 3 轮，动态阈值 70）

### 为什么要 4 种 SSE 事件

`text` 和 `patch` 互斥：首次报告用 `text` 从头构建，后续增量更新用 `patch` 替换/追加章节，不重复发送已有内容。`chain` 展示实时思考链路，`hitl` 中断执行流，两个通道并行工作互不阻塞。

### 为什么搜索失败时不阻断流程

`assess_node` 在 `search_all_failed=True` 时标记 `_llm_fallback=true`，跳转至 `report_node` 直接调用 LLM 已有知识回答。保证即使用户搜索配额耗尽或搜索引擎不可用，系统仍能给出有用响应。

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


