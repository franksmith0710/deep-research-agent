# Deep Research Agent — 多轮对话深度研究 Agent

基于 **LangGraph** + **FastAPI** + **PostgreSQL** 的多轮对话深度研究系统，采用 **Viking L0/L1/L2 渐进压缩链** 实现跨会话记忆复用。

## 系统架构

```
【前端 Vue 3】 ←→ SSE + REST ←→ 【FastAPI 后端】 ←→ 【LangGraph Agent 调度核心】
                                                          ↕
                                               【Viking Memory Store】（PostgreSQL）
```

### Agent 流程图

```
入口 → resolve_context → intent_classifier
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        check_history       search         simple_llm → END
              ▼                │
           clarify             │
         ╱       ╲           │
    hitl_scope    planner     │
        │        ╱╲         │
        ▼       /  ╲       │
      planner hitl_adjust   │
          \     /          │
           ▼   ▼           │
           search ◄────────┘
             │
             ▼
          scrape → context_mgr → dedup → rerank
             │                    │
        (refine/new_search)   (deep_research)
             ▼                    ▼
         synthesize            assess
          │     ╲            ╱   │   ╲
          │   hitl_outline  /     │  search
          ▼        ▼       ▼     ▼
         report → report → synthesize → memory → END
```

## 核心概念

### Viking L0/L1/L2 渐进压缩链

| 层级 | 大小 | 内容 | 生成方式 | 存储位置 | 检索方式 |
|------|------|------|----------|----------|----------|
| **L0** | ~100 token | 一句话摘要 | LLM 压缩 L2→L0 | research_tasks | SQL 精确查 |
| **L1** | ~2000 token | 核心事实(含来源URL) | LLM 压缩 L2→L1 | memory_store | 语义检索(cosine) |
| **L2** | 全文 | 网页原始 Markdown | trafilatura 提取 | memory_store | SQL 精确查 |

### 4 条意图路径

| 意图 | 触发场景 | 行为 |
|------|----------|------|
| `deep_research` | 首次提问 / 全新话题 | 完整 15 节点，最多 2 轮深度搜索 |
| `refine_section` | 追问已有章节详情 | 仅搜索该章节 → patch 替换，不重建 |
| `new_search_topic` | 提出新方面 | 搜索新话题 → 追加章节 |
| `simple_llm` | 总结/改写/追问 | 不搜索，直接 LLM 回答 |

### 4 个 HITL 介入点

| 介入点 | 时机 | 前端交互 |
|--------|------|----------|
| 范围选择 | 查询太宽泛时 | 复选维度 + 自定义文本 |
| 方向微调 | 子搜索生成后 | 文本框编辑子查询 |
| 冲突采信 | 检测到矛盾观点 | 单选 A/B/并列 |
| 大纲微调 | 报告生成前 | 编辑 7 段大纲 |

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 语言 | Python >= 3.11 |
| Agent 框架 | LangGraph >= 1.1.8 |
| API 框架 | FastAPI >= 0.135 |
| LLM | DeepSeek API (deepseek-chat) |
| 数据库 | PostgreSQL (无 pgvector，Python 端余弦相似度) |
| 文本嵌入 | bge-m3 (1024 维) |
| 重排模型 | bge-reranker-v2-m3 |
| 搜索引擎 | DuckDuckGo (免费) |
| 网页提取 | trafilatura + httpx |
| 前端 | Vue 3 + Vite + Pinia |
| 日志 | Loguru |

## 项目结构

```
src/
├── main.py                  # 入口：uvicorn 启动
├── config.py                # Pydantic Settings 配置
├── models.py                # Pydantic 数据模型 & 枚举
├── agent/
│   ├── state.py             # AgentState TypedDict (65 字段)
│   ├── nodes.py             # 15 功能节点 + 4 HITL 节点 + 3 路由函数
│   └── graph.py             # LangGraph StateGraph 编排
├── api/
│   ├── server.py            # FastAPI 应用 + CORS
│   ├── routes.py            # 5 REST 端点 + SSE 推送
│   └── sse_manager.py       # 异步队列 SSE 管理
├── db/
│   └── postgres.py          # 4 表 CRUD + Python 端向量搜索
├── llm/
│   └── client.py            # DeepSeek API 封装 (4 次重试)
├── local_models/
│   ├── embedder.py          # bge-m3 嵌入
│   └── reranker.py          # bge-reranker-v2-m3 重排
├── search/
│   ├── engine.py            # 搜索接口抽象
│   ├── duckduckgo.py        # DuckDuckGo 搜索
│   └── scraper.py           # 网页抓取 (httpx + trafilatura)
├── planner/
│   └── planner.py           # 查询消歧 / 范围澄清 / 子查询分解
├── extract/
│   └── extractor.py         # L2→L1+L0 单 LLM 调用提取
├── synthesize/
│   ├── deduplicator.py      # 余弦相似度查重 (阈值 0.88)
│   ├── ranker.py            # bge-reranker 排序
│   └── synthesizer.py       # 话题聚类 + 章节综合
├── report/
│   └── writer.py            # 7 段大纲 + Markdown 报告生成
└── memory/
    ├── retriever.py         # L1 语义检索 + L2 精确检索
    └── credibility.py       # 来源信誉评分
```

## 快速开始

### 前置依赖

- Python 3.11+
- PostgreSQL 15+
- bge-m3 + bge-reranker-v2-m3 模型文件
- DeepSeek API Key

### 1. 环境配置

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Key 和路径
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

### 4. 启动前端 (可选)

```bash
cd frontend && npm run dev
# 运行在 http://localhost:5173
```

## API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/sessions` | 列出所有会话 |
| `GET` | `/api/sessions/{id}/history` | 加载会话历史 |
| `POST` | `/api/sessions` | 创建新会话 |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |
| `POST` | `/api/research` | 发起研究 (SSE 流式返回) |
| `POST` | `/api/hitl/callback` | HITL 回调恢复执行 |

## SSE 事件协议

| event | 触发时机 | 前端处理 |
|-------|----------|----------|
| `chain` | 每个节点执行 | ThoughtBlock 展示 |
| `text` | 首次 deep_research | 打字机流式渲染 |
| `patch` | refine/new_search | 增量更新报告章节 |
| `hitl` | HITL 中断 | 弹出交互对话框 |

## 设计亮点

- **无 Redis / 无 Chroma / 无 pgvector** — 仅 PostgreSQL，Python 端计算余弦相似度
- **零搜索成本** — DuckDuckGo + trafilatura
- **本地模型** — bge-m3 / bge-reranker 零推理费用
- **低成本 API** — DeepSeek-chat 做 LLM 骨干
- **跨会话记忆** — L1 语义检索自动复用历史发现
