-- Deep Research Agent 数据库初始化脚本
-- 无 Redis + 无 Chroma 轻量化设计
-- 嵌入存 vector(1024) + HNSW 索引（pgvector）
-- chat_history（对话展示）
-- research_tasks（任务 + 报告 + L0）
-- source_credibility（来源信誉）
-- memory_store（Viking L1 向量 + L2 全文）

-- ============================================================
-- 对话展示
-- ============================================================

CREATE TABLE IF NOT EXISTS chat_history (
    id          SERIAL PRIMARY KEY,
    session_id  VARCHAR(64) NOT NULL,
    role        VARCHAR(20) NOT NULL CHECK (role IN ('user','assistant','hitl','system')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id, created_at);

-- ============================================================
-- 任务 + 报告 + L0
-- ============================================================

CREATE TABLE IF NOT EXISTS research_tasks (
    task_id         SERIAL PRIMARY KEY,
    session_id      VARCHAR(64) NOT NULL,
    query           TEXT NOT NULL,
    resolved_query  TEXT,
    scope           JSONB,
    sub_queries     JSONB,
    outline         JSONB,
    report          TEXT,
    l0_summary      TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','hitl_waiting','completed','error')),
    error_message   TEXT,
    deep_search_count INT NOT NULL DEFAULT 0 CHECK (deep_search_count >= 0),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON research_tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON research_tasks(status);

-- memory_store 表已移除，由 fs_nodes 替代
-- source_credibility 表已移除，改用内存 LRU 缓存 + 静态域名规则
