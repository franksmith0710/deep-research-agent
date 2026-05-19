-- Deep Research Agent 数据库初始化脚本
-- 无 Redis + 无 Chroma + 无 pgvector 轻量化设计
-- 嵌入存 DOUBLE PRECISION[]，余弦相似度在 Python 中计算
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

-- ============================================================
-- 来源信誉
-- ============================================================

CREATE TABLE IF NOT EXISTS source_credibility (
    id           SERIAL PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    domain       VARCHAR(256) NOT NULL,
    score        INT NOT NULL DEFAULT 50 CHECK (score >= 0 AND score <= 100),
    access_count INT NOT NULL DEFAULT 1 CHECK (access_count >= 0),
    last_status  VARCHAR(20) NOT NULL CHECK (last_status IN ('success','fail'))
);

CREATE INDEX IF NOT EXISTS idx_credibility_domain ON source_credibility(domain);
CREATE INDEX IF NOT EXISTS idx_credibility_score ON source_credibility(score DESC);

-- ============================================================
-- Viking L1 + L2 统一存储
-- 嵌入用 vector(1024) 类型 + HNSW 索引（pgvector 0.5+）
-- ============================================================

CREATE TABLE IF NOT EXISTS memory_store (
    id          SERIAL PRIMARY KEY,
    task_id     INT NOT NULL REFERENCES research_tasks(task_id) ON DELETE CASCADE,
    level       VARCHAR(2) NOT NULL CHECK (level IN ('L1','L2')),
    content     TEXT NOT NULL,
    source_url  TEXT,
    topic       VARCHAR(256),
    embedding   vector(1024),
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_task ON memory_store(task_id);
CREATE INDEX IF NOT EXISTS idx_memory_level ON memory_store(level);
CREATE INDEX IF NOT EXISTS idx_memory_topic ON memory_store(topic);
CREATE INDEX IF NOT EXISTS idx_memory_embedding
  ON memory_store USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 200);
