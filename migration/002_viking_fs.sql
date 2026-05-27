-- Viking 文件系统节点表
-- 统一存储长期记忆（user/memories）+ 中期会话（session/{sid}）+ 资源
-- 替代 memory_store 表的职责

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS fs_nodes (
    id            SERIAL PRIMARY KEY,
    uri           VARCHAR(1024) NOT NULL UNIQUE,   -- viking://user/memories/entities/surface_code
    parent_uri    VARCHAR(1024),                    -- viking://user/memories/entities
    is_directory  BOOLEAN NOT NULL DEFAULT FALSE,
    name          VARCHAR(256) NOT NULL,
    context_type  VARCHAR(32) NOT NULL,             -- user_memory / session / resource
    level         VARCHAR(2),                       -- L0 / L1 / NULL(目录)
    content       TEXT,                             -- L1 知识正文 / 报告全文
    abstract      TEXT,                             -- L0 摘要（~100 tokens）
    overview      TEXT,                             -- 目录级概览
    embedding     vector(1024),                     -- 仅 L1 实体节点有
    source_url    TEXT,                             -- 知识溯源 URL
    metadata      JSONB,                            -- category / 创建时间 / 来源统计
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fs_uri ON fs_nodes(uri);
CREATE INDEX IF NOT EXISTS idx_fs_parent ON fs_nodes(parent_uri);
CREATE INDEX IF NOT EXISTS idx_fs_context_type ON fs_nodes(context_type);
CREATE INDEX IF NOT EXISTS idx_fs_embedding
  ON fs_nodes USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 200);
