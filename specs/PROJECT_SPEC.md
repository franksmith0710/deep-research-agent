# Deep Research Agent — 项目需求与设计

## 一、项目概述

一个多轮对话深度研究 Agent，用户在同一会话中多次提问、持续完善报告。系统自动完成：意图分类 → 指代消解 → Viking 三层记忆检索 → (深度搜索 / 补充搜索 / 直接回答) → 增量更新报告。

### 项目定位

- **用途**: 简历展示项目，展示复杂 Agent 系统的架构设计能力
- **特点**: 纯免费搜索方案 + 本地模型 + 低成本 API，**无 Redis + 无 Chroma 轻量化部署**
- **核心理念**: Viking L0/L1/L2 渐进压缩链，每层按需检索、不超过 token 预算

---

## 二、整体架构总图

```
【前端】
  左侧侧边栏(会话列表) | 右侧主窗口(对话+持续报告块+输入框)
     ↑↓ SSE + REST（4种 event: chain / text / patch / hitl）
【后端 FastAPI】
   API 接口 + SSE 流式输出
      ↑↓
【LangGraph Agent 调度核心】
   intent_classifier 4 分支:
     deep_research → 完整 14 节点 + 2 轮深搜
     refine_section → 短路径 patch 章节
     new_search_topic → 短路径追加章节
     simple_llm → 无搜索直接回答
      ↑↓
【Viking Memory Store】
     PG + pgvector（L0 / L1 / L2）
```

### 2.1 内容粒度（Viking L0 / L1 / L2）

| 层级 | Token | 内容 | 生成方式 | 存储位置 | 检索方式 | 消费者 |
|---|---|---|---|---|---|---|
| **L0** | ~100 | 一句话摘要 | DeepSeek 压缩 L2→L0 | research_tasks.l0_summary | SQL task_id 精确查 | assess_node 路由决策 |
| **L1** | ~2000 | 核心事实列表（含来源 URL） | DeepSeek 压缩 L2→L1 | memory_store (level=L1, embedding 必填) | pgvector 语义检索 | synthesize / dedup / rerank / report |
| **L2** | 全文 | 网页原始 Markdown | trafilatura 提取 | memory_store (level=L2, embedding=null) | SQL task_id/source_url 精确查 | report_node 引用溯源 |

### 2.2 渐进压缩链

```
网页 (L2) ──LLM──→ 核心发现 (L1) ──LLM──→ 一句话摘要 (L0)
  ↑                      ↑                        ↑
  trafilatura            每条含来源 URL           用于快速覆盖度判断
  保留全文供引用          写 memory_store          写 research_tasks
                          语义检索跨会话复用          父子任务间传递
```

---

## 三、用户完整流程（多轮对话）

### 第 1 步：打开页面

**前端:**
- 读取 session_id（localStorage），无则新建
- `GET /api/sessions` → 侧边栏展示所有历史会话列表（query 前 20 字 + 状态标签）
- `GET /api/sessions/{id}/history` → 右侧主窗口加载最近会话的完整对话 + 报告

**后端:**
- `SELECT * FROM chat_history WHERE session_id=? ORDER BY created_at`
- `SELECT * FROM research_tasks WHERE session_id=? ORDER BY created_at`
- 返回历史对话 + 当前最新报告（合并后的完整版）

---

### 第 2 步：用户输入问题

**前端:**
- 输入框始终可用，不阻塞
- `POST /api/research {query, session_id}`（无 parent_task_id，全在同一个会话内）
- 若前一个 SSE 仍在进行中，后端排队执行

**后端:**
- chat_history 追加一条 role=user
- 启动 LangGraph（一个会话共用一个 research_task，复用 report 字段）

---

### 第 3 步：意图分类 + 指代消解

**Agent 节点: `resolve_context_node` → `intent_classifier_node`**

```
resolve_context_node:
  从 research_tasks 读取当前会话已有 context（L0 + 已搜索关键词）
  LLM 指代消解: "市场规模方面再说详细一点" → "2025 国内 AI Agent 市场规模数据补充"

intent_classifier_node:
  LLM 判断本轮意图，4 种输出:
```
| 意图 | 触发场景 | 行为 |
|---|---|---|
| `deep_research` | 首次提问 / 全新话题 | 走完整 14 节点，话题不限 |
| `refine_section` | 要求详细化已有章节 | 仅搜索该章节→压缩→patch，不触发回溯 |
| `new_search_topic` | 提一个新方面 | 搜索新话题→压缩→patch 追加章节 |
| `simple_llm` | 总结 / 改写 / 追问细节 | 不搜索，仅 LLM 基于已有报告回答 |

**复用规则:**

deep_search_count 整个会话全局共享，最多 2 轮深度搜索：
- `deep_research` 可用 2 轮
- `refine_section` / `new_search_topic` 不做深度迭代（搜完即止）
- `simple_llm` 不消耗额度

**前端反馈:** `event: chain, {type:"thought", ...}` / `event: chain, {type:"action", ...}`

---

### 第 4 步：路径 A — deep_research（完整调研）

当前会话没有已有报告时触发。走完完整 14 节点:

```
intent_classifier → check_history → clarify → HITL 范围选择
  → planner → HITL 方向微调 → search → scrape
  → context_mgr → dedup → rerank → assess（最多 2 轮）
  → HITL 冲突采信 → synthesize → HITL 大纲微调 → report
```

生成标准 7 章报告（摘要/背景/现状/观点/风险/总结/引用），通过 `event: text` 流式输出。

第一轮生成的结构是整个会话的报告骨架，后续轮次在此基础上修改/补充，不重建。

---

### 第 5 步：路径 B — refine_section（补充已有章节）

```
intent_classifier → 仅执行:
  search（该章节相关 sub_queries）
  → scrape → context_mgr → dedup → rerank
  → 直接 report（不经过 assess/synthesize，不修改大纲结构）
```

SSE 输出:
```
event: patch
data: {"section": "核心现状与关键数据", "content": "## 核心现状与关键数据\n\n新增数据...", "append": true}
```

前端客户端合并逻辑:
```
reportContent = reportContent.replace(/## 核心现状与关键数据[\s\S]*?(?=## )/, patch.content)
```

普通补充搜索（单轮，不触发 assess→deep_search 循环）。

---

### 第 6 步：路径 C — new_search_topic（新增话题章节）

```
intent_classifier → 仅执行:
  search → scrape → context_mgr → dedup → rerank
  → synthesize（生成新章节内容）
  → 直接 report（追加到现有报告末尾，不修改已有章节）
```

SSE 输出:
```
event: patch
data: {"section": "中美政策对比", "content": "## 中美政策对比\n\n...", "append": false}
```

`append=false` 表示替换整个 section；若 section 不存在，追加到末尾。

普通补充搜索（单轮，不触发 assess→deep_search 循环）。

---

### 第 7 步：路径 D — simple_llm（不搜索）

```
intent_classifier → 直接 LLM 回答
  基于 research_tasks.report（当前完整报告）+ 用户 query
  输出纯文本回答，不产生新 discovery
```

SSE 输出:
```
event: text
data: {content: "根据已调研的信息，总结为三点：\n1. ...\n2. ...\n3. ..."}
```

该回答不会修改报告结构，只在聊天框显示。

---

### 第 8 步：记忆沉淀（所有路径共享）

```
memory_node（异步后台执行，仅 deep_research / refine_section / new_search_topic 路径结束后触发）:
  1. research_tasks: 更新 report（合并了 patch 后的完整版）
  2. memory_store:
     a. 新增 L1: 先查重（pgvector >0.88），命中则 UPDATE content + 刷新 created_at，未命中则 INSERT（含 embedding）
     b. 新增 L2: 直接 INSERT（无需查重，全文不用于语义检索）
  3. source_credibility: 遍历本轮所有 source_url，UPSERT 更新 score 和 access_count
  4. chat_history: 追加一条记录本轮操作
```

**报告合并规则:**
- 前端维护 `reportContent`（完整报告 state）
- 首次 deep_research → `event: text` 流式构建 reportContent
- 后续 refine/new_search → `event: patch` 更新 reportContent 的对应 section
- 切换会话时，后端返回合并后的完整 report，前端直接渲染

**前端 Vue 状态:**
```
chatStore.sessionReport = reactive({ content: "", sections: [] })
patchHandler(event) {
  const idx = this.sections.findIndex(s => s.name === event.section)
  if (idx >= 0) this.sections[idx].content = event.content
  else this.sections.push({ name: event.section, content: event.content })
  this.renderFullReport()
}
```

**会话层级限制:**

| 资源 | 上限 | 范围 |
|---|---|---|
| deep_search 轮次 | 2 轮 | 整个会话全局共享 |
| 补充搜索次数 | 不限 | 单轮即止，不做深度迭代 |
| 报告章节 | 固定 7 章 | 不随轮次增加而重构 |

---

## 四、技术架构

### 4.1 技术栈

| 层级 | 技术选型 |
|---|---|
| 编程语言 | Python >= 3.11 |
| Agent 框架 | LangGraph >= 0.2 |
| API 框架 | FastAPI >= 0.115 |
| LLM | DeepSeek API (deepseek-chat) |
| 数据库 | PostgreSQL + pgvector |
| 文本嵌入模型 | bge-m3（L1 嵌入 + pgvector 语义检索） |
| 重排模型 | bge-reranker-v2-m3 |
| 搜索引擎 | DuckDuckGo |
| 网页提取 | trafilatura + httpx |
| 前端框架 | Vue 3 + Vite + Pinia |
| 监控 | LangSmith |

### 4.2 架构分层

```
【前端】极简聊天界面
  左侧侧边栏: 会话列表
  右侧主窗口: 对话历史 + ThoughtBlock + HITL 弹窗 + 流式报告 + 输入框
  前端不做: 记忆、检索、指代消解、搜索、压缩、去重

【后端 FastAPI】
  API 层: 接口 + SSE 流式输出（4 种 event: chain / text / patch / hitl）

【LangGraph Agent 调度核心】
   15 节点 + intent_classifier 4 分支 + 4 HITL 中断点 + 条件边
   deep_research → 完整路径, refine_section → 短路径, new_search_topic → 短路径, simple_llm → 无搜索

【Viking Memory Store】
  memory_store: L1(含pgvector) + L2(全文)
  research_tasks: L0 + 任务状态 + 报告
  chat_history: 对话展示
  source_credibility: 来源信誉

【基础设施】
  llm/client | local_models/embedder + reranker
  shared: models.py | config.py
```

### 4.3 数据流（4 条意图路径）

```
打开页面 → GET /api/sessions（读 PG chat_history + research_tasks）
   ↓
输入问题 → POST /api/research → 启动 LangGraph
   ↓
resolve_context（指代消解: 当前 query + 会话已有报告）
   ↓
intent_classifier ─┬── deep_research ──→ check_history → clarify → HITL 范围选择
                   │                       → planner → HITL 方向微调 → search → scrape
                   │                       → context_mgr → dedup → rerank
                   │                       → assess（最多 2 轮深搜）→ HITL 冲突
                   │                       → synthesize → HITL 大纲 → report（text）
                   │
                   ├── refine_section ──→ search（限定该章节）→ scrape
                   │                       → context_mgr → dedup → rerank
                   │                       → report（patch 替换该章节）
                   │
                   ├── new_search_topic ─→ search（新话题）→ scrape
                   │                       → context_mgr → dedup → rerank
                   │                       → synthesize → report（patch 追加章节）
                   │
                    └── simple_llm ───────→ 直接 LLM 回答（无搜索，无记忆写入，结束）
    ↓
memory（异步: research_tasks 更新报告 + memory_store 持久化，仅 deep/refine/new 路径触发）
```

### 4.4 HITL 系统

| 介入点 | 时机 | 前端 mode | 交互方式 | 超时行为 |
|---|---|---|---|---|---|
| 范围选择 | clarify 后/模糊时 | scope_select | 复选+可选文本 | 一直等待用户确认 |
| 方向微调 | planner 后 | scope_select(edit) | 文本框+推荐示例 | 一直等待用户确认 |
| 冲突采信 | assess 后/有冲突 | conflict_resolve | 单选 A/B/并列 + 可疑处理 | 一直等待用户确认 |
| 大纲微调 | synthesize 后 | outline_edit | 文本框+推荐示例 | 一直等待用户确认 |

**条件触发规则：**
每个 HITL 介入是否展示由上游节点 LLM 判断，节点在 State 输出布尔标记，graph.py 条件边决定是否进入中断点：

| 介入点 | 上游节点 | 判断逻辑 | 条件边 |
|---|---|---|---|
| 范围选择 | `clarify` | `need_scope=true` 时展示，否则跳过 | `clarify → HITL` / `clarify → planner` |
| 方向微调 | `planner` | `need_adjust=true` 时展示，否则跳过 | `planner → HITL` / `planner → search` |
| 冲突采信 | `assess` | `has_conflict=true` 时展示，否则跳过 | `assess → HITL` / `assess → synthesize` |
| 大纲微调 | `synthesize` | `need_outline_review=true` 时展示，否则跳过 | `synthesize → HITL` / `synthesize → report` |

**原则:** 复选 80%场景1秒完成 / 单选三选一不纠结 / 文本框+推荐降门槛 / **不做拖拽** / 用户不点不继续

### 4.5 节点上下文装配规则

每个节点按需从 sources 中读取对应层级，不传全量历史：

| 节点 | 读取内容 | 来源 | 检索方式 |
|---|---|---|---|---|
| `resolve_context` | 会话已搜关键词 + 已有报告 | research_tasks report + l0_summary | SQL session_id 精确查 |
| `intent_classifier` | resolved_query + 会话状态 | research_tasks（deep_search_count, 报告结构） | SQL session_id 精确查 |
| `check_history` | L1 top-3 语义相关 | memory_store WHERE level='L1' | pgvector ORDER BY embedding <=> query |
| `clarify` | 当前 query | 无检索（直接 LLM） | — |
| `planner` | 已搜 query/URL | memory_store WHERE level='L1' 历史记录 | SQL 精确匹配过滤 |
| `context_mgr` | 同话题已有 L1 top-2（防重复提炼） | memory_store WHERE level='L1' | pgvector ORDER BY embedding <=> topic |
| `assess` | State.page_summaries[] + 历史 L0 | 内存 + research_tasks | 内存数组 + SQL |
| `synthesize` | 全部相关 L1 + 对应 L2 引用片段 | memory_store | pgvector + SQL source_url 溯源 |
| `report` | 全部 L1 + L2 引用片段 + 用户 outline | 由 synthesize 传入，不重复检索 | — |

---

### 4.6 Fallback 策略

| 节点 | 失败场景 | Fallback | 代码影响 |
|---|---|---|---|
| `search` | DuckDuckGo 限流/全失败 | 降级用 LLM 已有知识回答，报告注明"搜索受限，部分内容基于已有知识" | +8 行 |
| `scrape` | 部分 URL 抓取失败 | 跳过失败 URL，继续处理成功 URL | +5 行 |
| `llm/client` | 4 次重试后仍失败 | 中断图执行，`research_tasks.status=error`，前端显示错误 + [重试] | 已有 |
| `intent_classifier` | LLM 调用失败 | 默认走 `simple_llm`（最安全路径，不搜索不写记忆） | +3 行 |

**不搞 fallback 直接报 error 的：**
- `embedder`/`reranker` 加载失败 → 启动时模型校验失败，不进入运行时降级
- `planner` 失败 → 核心节点，降级无意义
- `synthesize` 失败 → 无综合结果就没有报告
- `assess` 第 2 轮失败 → demo 场景 1 轮足够
- DB 连接失败 → 本地开发不应发生，直接报错

**原则：简历展示项目，不搞生产级兜底，最少量代码覆盖最关键的断裂点（约 20 行）。**

---

## 五、数据库设计（无 Redis + 无 Chroma，纯 PG + pgvector）

### 5.1 表结构

**3 张业务表 + pgvector 扩展，全部搞定。**

#### chat_history（对话展示）

```sql
CREATE TABLE chat_history (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user','assistant','hitl','system')),
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_chat_session ON chat_history(session_id, created_at);
```

#### research_tasks（任务 + 报告 + L0）

```sql
CREATE TABLE research_tasks (
    task_id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    query TEXT NOT NULL,
    resolved_query TEXT,
    scope JSONB,                      -- 用户选择的维度
    sub_queries JSONB,                -- 子问题列表
    outline JSONB,                    -- 报告大纲
    report TEXT,                      -- Markdown 完整报告
    l0_summary TEXT,                  -- L0: 一句话摘要（供 assess_node 快速判断）
    status VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending','running','hitl_waiting','completed','error')),
    error_message TEXT,
    deep_search_count INT NOT NULL DEFAULT 0 CHECK (deep_search_count >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tasks_session ON research_tasks(session_id);
CREATE INDEX idx_tasks_status ON research_tasks(status);
```

#### source_credibility（来源信誉）

```sql
CREATE TABLE source_credibility (
    id SERIAL PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    domain VARCHAR(256) NOT NULL,
    score INT NOT NULL DEFAULT 50 CHECK (score >= 0 AND score <= 100),
    access_count INT NOT NULL DEFAULT 1,
    last_status VARCHAR(20) NOT NULL CHECK (last_status IN ('success','fail'))
);
CREATE INDEX idx_credibility_domain ON source_credibility(domain);
CREATE INDEX idx_credibility_score ON source_credibility(score DESC);
```

**信誉分更新规则（memory_node 执行）:**
- 每次成功抓取 → `access_count += 1; score = LEAST(100, score + 2); last_status = 'success'`
- 每次抓取失败 → `access_count += 1; score = GREATEST(0, score - 5); last_status = 'fail'`
- 报告底部引用根据 score 显示可信度标签：`>=80` 高，`50-79` 中，`<50` 低
- 不设 TTL 过期，纯正向积累

#### memory_store（Viking L1 + L2 统一存储）

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memory_store (
    id SERIAL PRIMARY KEY,
    task_id INT NOT NULL REFERENCES research_tasks(task_id) ON DELETE CASCADE,
    level VARCHAR(2) NOT NULL CHECK (level IN ('L1','L2')),
    content TEXT NOT NULL,             -- L1: 核心发现(含来源URL) / L2: 原始正文
    source_url TEXT,
    topic VARCHAR(256),
    embedding vector(1024),           -- L1: NOT NULL / L2: NULL（仅 L1 需要语义检索）
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_memory_task ON memory_store(task_id);
CREATE INDEX idx_memory_level ON memory_store(level);
CREATE INDEX idx_memory_topic ON memory_store(topic);
CREATE INDEX idx_memory_embedding ON memory_store
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
    WHERE level = 'L1';              -- 仅索引 L1，L2 无需向量索引
```

### 5.2 检索策略

| 层级 | 表 | 查询方式 | 适用场景 |
|---|---|---|---|
| **L0** | State.page_summaries[] + research_tasks.l0_summary | 内存数组 + SQL | assess_node 覆盖度判断 / 最终归档 |
| **L1** | memory_store WHERE level='L1' | pgvector `ORDER BY embedding <=> query LIMIT k` | 跨会话语义复用 / 去重 / 综合 |
| **L2** | memory_store WHERE level='L2' | SQL `WHERE source_url=?` 或 `WHERE task_id=?` | 报告引用溯源 |

---

## 六、前端设计

### 6.1 页面布局

```
┌──────────────────────────────────────────────────────┐
│ ┌────────── 侧边栏 280px ──────────┐ ┌── 主窗口 ──┐ │
│ │ [+ 新建会话]                     │ │ 对话历史    │ │
│ │                                  │ │  用户消息 →  │ │
│ │ ── 历史会话 ──                   │ │  ← 助手消息   │ │
│ │                                  │ │  ← chain     │ │
│ │ ● 2025 国内 AI Agent 落地现状    │ │  → chain     │ │
│ │     已完成                       │ │  ← chain     │ │
│ │                                  │ │  ← [报告]    │ │
│ │ ● AI 大模型在教育行业应用        │ │             │ │
│ │     已完成                       │ │ ────────── │ │
│ │                                  │ │ │ 输入框     │ │
│ │ ● 中国企业出海合规挑战           │ │ │ [发送]     │ │
│ │     error                        │ │ └─────────── │ │
│ │                                  │ │              │ │
│ │ 选中项高亮                       │ │              │ │
│ └──────────────────────────────────┘ └──────────────┘ │
└──────────────────────────────────────────────────────┘

HITL 弹窗（居中模态，遮罩背景）:
┌────────────────────────────────────┐
│ 请选择调研方向                      │
│ ☐ 市场规模  ☐ 技术架构              │
│ ☐ 落地案例  ☐ 风险挑战              │
│ 其他补充: ________________________  │
│ [确认]                              │
└────────────────────────────────────┘
```

### 6.2 组件树

```
App.vue
  ├── Sidebar.vue（固定 280px，深色背景）
  │   ├── [ + 新建会话 ] 按钮（顶部）
  │   └── SessionList.vue（按 updated_at 倒序）
  │       └── SessionItem.vue（显示 query 前 20 字 + 状态标签）
  │           点击 → 切换右侧主窗口为该会话完整内容
  │           选中项高亮背景
  ├── MainArea.vue（右侧主窗口容器）
  │   ├── ChatHistory.vue（滚动到底部）
  │   │   ├── 用户消息（右对齐，user 气泡）
  │   │   ├── 助手消息（左对齐，assistant 气泡，仅 simple_llm 路径）
  │   │   ├── ThoughtBlock.vue（按 type 渲染三种样式）
  │   │   │   ├── type=thought: 灰色斜体，左侧缩进
  │   │   │   ├── type=action: 蓝色等宽，带⏳图标
  │   │   │   └── type=action_result: 绿色小字，带✓图标
  │   │   ├── ReportBlock.vue（固定位置，持续更新）    ← 非逐条追加
  │   │   │   ├── 多轮次更新标记（"第 2 次更新 - 已补充市场规模"）
  │   │   │   └── ReportViewer.vue（渲染完整 Markdown + [1][2] 上角标跳转）
  │   │   └── 分隔线
  │   ├── HITLDialog.vue（居中模态框，遮罩）
  │   │   ├── mode=scope_select: 复选+文本
  │   │   ├── mode=conflict_resolve: 单选三选一+可疑处理
  │   │   └── mode=outline_edit: 文本框+推荐
  │   └── QueryInput.vue（底部固定，始终可用，不阻塞）
  └── stores/
      ├── chatStore.ts（会话消息、chain 链、HITL 状态）
      ├── reportStore.ts（完整报告 state + patch 合并逻辑）  ← 新增
      └── sessionStore.ts（侧边栏会话列表、切换）
```

### 6.3 SSE 事件协议

4 种 event 类型：

| event | 前端渲染 | 说明 |
|---|---|---|
| `chain` | `<ThoughtBlock>` 按 data.type 区分样式 | `thought` / `action` / `action_result` |
| `text` | 打字机追加到对话 | 首次 deep_research 流式构建完整报告 |
| `patch` | 更新 reportStore 对应章节 | refine_section / new_search_topic 增量更新 |
| `hitl` | 弹出 `<HITLDialog>` 模态框 | 范围选择 / 冲突采信 / 大纲微调 |

消息格式:
```
event: chain
data: {"type": "thought", "node": "resolve_context", "content": "...", "ts": "2026-05-17T12:00:00Z"}

event: patch
data: {"section": "核心现状与关键数据", "content": "## ...", "append": true, "ts": "..."}
```

所有 chain 事件按节点顺序推送。patch 事件触发 reportStore 更新：
- `append=true`: 追加到该章节末尾
- `append=false`: 替换整个章节（section 不存在时追加到末尾）

### 6.4 交互行为

| 操作 | 行为 |
|---|---|
| 点击 [+ 新建会话] | `POST /api/sessions` → 生成新 session_id → 清空右侧主窗口 → 聚焦输入框 |
| 点击历史会话 | `GET /api/sessions/{id}/history` → 加载该 session 全部消息到右侧 |
| 发送问题 | `POST /api/research` → 开始 SSE 流接收 |
| 切换会话 | 保留另一个会话的聊天状态（不销毁），切换回时不重新加载 |
| 刷新页面 | 读取 localStorage session_id → 加载最近会话内容；若 session_id 无效则新建 |

### 6.5 前端责任

**做:**
- 左侧侧边栏：展示历史会话列表（query 前 20 字 + 状态标签）
- 右侧主窗口：对话历史（用户气泡 / 助手气泡 / chain 行动链）+ 持续更新的报告块 + HITL 弹窗 + 底部输入框
- SSE 事件解析与流式渲染（4 种 event: chain / text / patch / hitl）
- reportStore 维护：patch 合并、章节替换、section 排序
- 多轮次显示："第 2 次更新" 标记
- 输入框始终可用，不阻塞（前一个 SSE 运行时可发送新请求）
- 本地持久化 session_id（localStorage）
- 会话切换不丢失内存中的其他会话消息
- ❌ 不做记忆 / 检索
- ❌ 不做指代消解
- ❌ 不做搜索 / 抓取
- ❌ 不做压缩 / 去重 / 重排
- ❌ 不做任何复杂逻辑

### 6.6 错误展示

| 异常类型 | 前端展示 |
|---|---|
| 搜索超时 / 限流 | action_result 红色提示 + 报告头部注明"部分搜索受限" |
| LLM 调用失败 | 黄色横幅 + 保留已有进度 + [重试] 按钮 |
| 网页抓取部分失败 | action_result 灰色提示"已跳过 N 个不可达来源" |
| 后端不可达 | "无法连接服务" + 历史会话可浏览 |
| SSE 断连 | 自动重连 3 次，仍失败则显示"连接中断"横幅 |

---

## 七、报告格式规范

### 7.1 章节结构

```
# 报告标题

## 摘要
100-200 字核心结论

## 调研背景与范围
- 背景说明
- 范围界定

## 核心现状与关键数据
- 现状
- 数据点 [1]

## 多方观点 / 信息冲突说明
（HITL 结果在此体现）
- 观点 A [2] | 观点 B [3]（存在分歧）

## 风险、局限、信息缺口
- 已知风险
- 信息缺口

## 总结与建议
- 核心结论
- 后续建议

## 引用来源
[1] https://xxx | 来源：行业报告 | 可信度：高
[2] https://xxx | 来源：科技自媒体 | 可信度：中
[3] https://xxx | 来源：个人博客 | 可信度：低
```

### 7.2 引用规范

- 正文 `[1][2]` 渲染为 `<sup>` 可点击标签，每引用对应底部 `<a id="ref-1">`
- 点击 `[1]` → `window.location.hash = "#ref-1"` 锚点跳转，对应条目高亮闪烁
- 引用 URL 渲染为可点击 `<a href="..." target="_blank">` 跳转原文

---

## 八、项目文件结构

```
search_agent/
├── requirements.txt                  # pip 依赖
├── .env                              # 本地配置（不入库）
├── .env.example                      # 环境变量模板
├── specs/
│   └── PROJECT_SPEC.md               # 项目需求与设计文档
├── migration/
│   └── 001_init.sql                  # 3 表 + pgvector DDL
├── src/
│   ├── __init__.py
│   ├── main.py                       # FastAPI 服务入口，无 CLI
│   ├── config.py                     # Pydantic Settings 配置加载
│   ├── models.py                     # Pydantic 数据模型
│   ├── db/
│   │   ├── __init__.py
│   │   └── postgres.py               # 3 表 CRUD + 连接池
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py                 # DeepSeek API 封装（retry 3）
│   ├── local_models/
│   │   ├── __init__.py
│   │   ├── embedder.py               # bge-m3（L1 嵌入 + pgvector）
│   │   └── reranker.py               # bge-reranker-v2-m3
│   ├── search/
│   │   ├── __init__.py
│   │   ├── engine.py                 # 搜索接口抽象
│   │   ├── duckduckgo.py             # DuckDuckGo
│   │   └── scraper.py                # httpx + trafilatura 正文提取
│   ├── planner/
│   │   ├── __init__.py
│   │   └── planner.py                # 查询分解 + 指代消解 + 澄清
│   ├── extract/
│   │   ├── __init__.py
│   │   └── extractor.py              # L2→L1+L0 一次 LLM 调用
│   ├── synthesize/
│   │   ├── __init__.py
│   │   ├── deduplicator.py           # memory_store 写入前单条查重
│   │   ├── ranker.py                 # bge-reranker 排序
│   │   └── synthesizer.py            # 按主题聚类 + 综合
│   ├── report/
│   │   ├── __init__.py
│   │   └── writer.py                 # Markdown 报告生成
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── retriever.py              # L1 pgvector + L2 SQL 检索
│   │   └── credibility.py            # 来源信誉更新
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py                  # AgentState TypedDict
│   │   ├── nodes.py                  # 15 个节点 + intent 分支
│   │   └── graph.py                  # LangGraph 图（4 HITL 中断点 + 4 路径）
│   └── api/
│       ├── __init__.py
│       ├── server.py                 # FastAPI 应用
│       ├── routes.py                 # API 路由
│       └── sse_manager.py            # SSE 推送（4 种 event）
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.ts
│       ├── App.vue
│       ├── types.ts
│       ├── api/
│       │   └── index.ts
│       ├── stores/
│       │   ├── chatStore.ts          # 对话消息 + chain 链
│       │   ├── reportStore.ts        # 报告 state + patch 合并
│       │   └── sessionStore.ts       # 侧边栏会话列表
│       ├── composables/
│       │   ├── useSSE.ts             # SSE 连接解析
│       │   └── useHITL.ts            # HITL 弹窗控制
│       └── components/
│           ├── Sidebar.vue
│           ├── SessionList.vue
│           ├── SessionItem.vue         # 会话列表中单个条目
│           ├── MainArea.vue            # 右侧主窗口容器
│           ├── ChatHistory.vue
│           ├── ThoughtBlock.vue
│           ├── ReportBlock.vue       # 持续更新的报告块
│           ├── ReportViewer.vue      # Markdown 渲染 + 引用跳转
│           ├── QueryInput.vue
│           └── HITLDialog.vue
└── tests/
```

## 九、实现计划（17 步）

| 步骤 | 内容 | 产出 |
|---|---|---|
| 1 | 项目骨架 | pyproject.toml / .env.example / config.py |
| 2 | 数据库 DDL | migration/001_init.sql（3 表 + pgvector） |
| 3 | 数据模型 | models.py（Pydantic 模型） |
| 4 | PostgreSQL CRUD | db/postgres.py（3 表 CRUD + 连接池） |
| 5 | LLM 客户端 | llm/client.py（retry 3 + response_format） |
| 6 | 本地模型 | local_models/embedder.py + reranker.py |
| 7 | 搜索层 | search/engine.py + duckduckgo.py + scraper.py |
| 8 | Planner | planner/planner.py（分解+消解+澄清，中文 prompt） |
| 9 | Extractor | extract/extractor.py（L2→L1+L0 一次调用） |
| 10 | 去重 + 重排 | synthesize/（dedup + ranker） |
| 11 | 综合 + 报告 | synthesize/synthesizer.py + report/writer.py |
| 12 | 记忆系统 | memory/（retriever + credibility） |
| 13 | LangGraph 图 | agent/state.py + nodes.py(15) + graph.py（4 HITL 中断点 + 条件边） |
| 14 | API + SSE | api/sse_manager.py + routes.py + server.py |
| 15 | 入口 | main.py（serve 启动 uvicorn） |
| 16 | 前端 | 全部 Vue 组件 + store + composable |
| 17 | 测试 + 文档 | tests/ + ARCH.md + README.md |

---

## 十、运行环境

### .env

```
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_xxx
LANGCHAIN_PROJECT=search_agent
POSTGRES_DSN=postgresql://postgres:pass@localhost:5432/search_agent
BGE_EMBEDDER_PATH=D:/hf_models/BAAI/bge-m3
BGE_RERANKER_PATH=D:/hf_models/BAAI/bge-reranker-v2-m3
```

### 前置

- Python 3.11+
- PostgreSQL 15+（含 pgvector 扩展）
- bge-m3 + bge-reranker-v2-m3 模型文件
- DeepSeek API Key

### 启动

```bash
# 建库
psql -d search_agent -f migration/001_init.sql

# 后端（端口 8000）
python -m src.main

# 前端（新开终端，端口 5173，vite 配置代理 /api → localhost:8000）
cd frontend && npm run dev
```

---

## 十一、简历亮点

| 亮点 | 描述 |
|---|---|
| LangGraph Agent 架构 | 15 节点 + intent 4 分支，4 HITL 中断点 + 条件边自适应深度搜索 |
| Viking L0/L1/L2 渐进压缩 | LLM 一次调用输出 L0+L1，每层按需检索，不超过 token 预算 |
| 跨会话记忆 | memory_store L1 pgvector 语义检索自动复用历史发现 |
| 本地模型集成 | bge-m3 嵌入 + bge-reranker-v2-m3 重排，零推理成本 |
| HITL 四介入点 | 范围选择(复选+文本) / 冲突采信(单选三选一) / 大纲微调(文本+推荐) |
| SSE 流式报告 | 打字机渲染 Markdown + [1][2] 可点击上角标引用 |
| 无 Redis 无 Chroma | 仅 PG + pgvector，一套数据库搞定全部记忆层 |
| 免费搜索 | DuckDuckGo + trafilatura，零搜索成本 |

---

## 开发要求

- 中文注释清晰
- prompt 规范合理（中文）
- 代码规范