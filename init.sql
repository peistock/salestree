-- 销销 SalesMind 数据库初始化

-- 用户画像表（销售人员 + 客户联系人身份统一表）
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '成员',
    preferences TEXT DEFAULT '',
    health_notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 情景记忆表（对话历史）
CREATE TABLE IF NOT EXISTS episodic_memory (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 对话线程表
CREATE TABLE IF NOT EXISTS conversation_threads (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    summary TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    is_archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS result_preview TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS files_json JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS todos_json JSONB DEFAULT '[]';

-- 中期记忆摘要表
CREATE TABLE IF NOT EXISTS memory_summaries (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    summary_type TEXT DEFAULT 'thread',
    summary_text TEXT NOT NULL,
    tags TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 知识库文档表
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 向量扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 知识库向量表
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    id SERIAL PRIMARY KEY,
    doc_id INTEGER REFERENCES knowledge_docs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建向量索引
CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
ON knowledge_embeddings USING ivfflat (embedding vector_cosine_ops);

-- 中期记忆索引
CREATE INDEX IF NOT EXISTS idx_memory_summaries_user
ON memory_summaries(user_id, summary_type, created_at DESC);

-- 情绪日志表
CREATE TABLE IF NOT EXISTS emotion_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_text TEXT NOT NULL,
    emotions TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_emotion_logs_user_time
ON emotion_logs(user_id, created_at DESC);

-- 定时任务日志
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id SERIAL PRIMARY KEY,
    task_name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- user_profiles 扩展字段
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS interests JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS current_projects JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS communication_style JSONB DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS life_experiences TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS family_circle JSONB DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS writing_patterns JSONB DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS entity_type TEXT DEFAULT 'user',
    ADD COLUMN IF NOT EXISTS team_id TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS wechat_user_id TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS llm_config JSONB DEFAULT '{}';

-- 创作空间
CREATE TABLE IF NOT EXISTS creation_workspace (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    project_type TEXT NOT NULL,
    draft_content TEXT DEFAULT '',
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'drafting',
    style_preset JSONB DEFAULT '{}',
    source_material JSONB DEFAULT '{}',
    care_context JSONB DEFAULT '{}',
    last_accessed_by_agent TIMESTAMP,
    completed_at TIMESTAMP,
    reviewed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_creation_workspace_user ON creation_workspace(user_id, updated_at DESC);

-- ========== 销售管理表 ==========

-- 客户公司表
CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    account_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    industry TEXT,
    website TEXT,
    stage TEXT DEFAULT 'prospect',
    owner_id TEXT REFERENCES user_profiles(user_id),
    annual_revenue_band TEXT,
    employee_count_band TEXT,
    region TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_accounts_stage ON accounts(stage, updated_at DESC);

-- 联系人表（决策链人物）
CREATE TABLE IF NOT EXISTS contacts (
    id SERIAL PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    contact_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    department TEXT,
    role_in_deal TEXT,
    email TEXT,
    phone TEXT,
    social_links JSONB DEFAULT '{}',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_contacts_account ON contacts(account_id, updated_at DESC);

-- 商机表
CREATE TABLE IF NOT EXISTS deals (
    id SERIAL PRIMARY KEY,
    deal_id TEXT UNIQUE NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    owner_id TEXT REFERENCES user_profiles(user_id),
    name TEXT NOT NULL,
    stage TEXT DEFAULT 'qualification',
    service_line TEXT,
    expected_value NUMERIC(12,2),
    close_date DATE,
    probability INTEGER DEFAULT 0,
    next_step TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_deals_owner ON deals(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_deals_account ON deals(account_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage, updated_at DESC);

-- 活动记录表（电话、会议、邮件、微信、笔记、研究）
CREATE TABLE IF NOT EXISTS activities (
    id SERIAL PRIMARY KEY,
    activity_id TEXT UNIQUE NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    owner_id TEXT REFERENCES user_profiles(user_id),
    activity_type TEXT NOT NULL,
    direction TEXT,
    content TEXT NOT NULL,
    sentiment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activities_entity ON activities(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activities_owner ON activities(owner_id, created_at DESC);

-- ========== 协作与通知表（保留结构，语义改为销售） ==========

-- 每日重点客户托付
CREATE TABLE IF NOT EXISTS briefings (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    effective_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_briefings_user_date ON briefings(user_id, effective_date DESC);

-- 紧急 Override 指令
CREATE TABLE IF NOT EXISTS overrides (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    priority INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    created_by TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_overrides_user_status ON overrides(user_id, status, priority DESC);

-- 通知队列（销售日报 + 警报）
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read, created_at DESC);

-- 初始化销售人员与示例客户数据
INSERT INTO user_profiles (user_id, name, role, preferences, entity_type, team_id) VALUES
    ('sales_001', '李明', '销售经理', '高效简洁，关注赢单', 'sales', 'team_a'),
    ('sales_002', '王芳', '客户经理', '详细完整，关注客户关系', 'sales', 'team_a')
ON CONFLICT (user_id) DO NOTHING;

-- 注：accounts/contacts/deals/activities 不再预置演示数据（2026-07-21 移除），
-- 演示数据会污染 Agent 记忆上下文。真实客户由销售手动录入。

-- ========== 商业化：组织与 LLM 用量计量 ==========

CREATE TABLE IF NOT EXISTS organizations (
    org_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    monthly_token_quota BIGINT NOT NULL DEFAULT 10000000,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS org_id TEXT DEFAULT 'org_default',
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';

CREATE INDEX IF NOT EXISTS idx_user_profiles_org
    ON user_profiles(org_id, status);

-- 先插入默认组织并把已有用户归进去，再加外键约束
INSERT INTO organizations (org_id, name, monthly_token_quota)
VALUES ('org_default', '默认组织', 10000000)
ON CONFLICT (org_id) DO NOTHING;

UPDATE user_profiles
SET org_id = COALESCE(org_id, 'org_default')
WHERE org_id IS NULL;

ALTER TABLE user_profiles
    DROP CONSTRAINT IF EXISTS fk_user_profiles_org;

ALTER TABLE user_profiles
    ADD CONSTRAINT fk_user_profiles_org
        FOREIGN KEY (org_id) REFERENCES organizations(org_id)
        ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,8) NOT NULL DEFAULT 0,
    cost_cny NUMERIC(12,8) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_org_created
    ON llm_usage(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_created
    ON llm_usage(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_thread_created
    ON llm_usage(thread_id, created_at DESC);

CREATE OR REPLACE FUNCTION update_org_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organizations_updated_at ON organizations;
CREATE TRIGGER trg_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW
    EXECUTE FUNCTION update_org_updated_at();

-- ========== 共享项目频道 ==========
ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS project_name TEXT DEFAULT NULL;

-- 每个项目至多一个共享频道；部分唯一索引同时解决并发 find-or-create 竞争
CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_project_name
    ON conversation_threads(project_name) WHERE project_name IS NOT NULL;

-- 频道历史按 thread 读取（不再按 user_id 过滤）
CREATE INDEX IF NOT EXISTS idx_episodic_memory_thread
    ON episodic_memory(thread_id, created_at ASC);

-- ========== 自定义频道：成员与关联项目 ==========
ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS linked_project TEXT DEFAULT NULL;

CREATE TABLE IF NOT EXISTS channel_members (
    thread_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    added_by TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (thread_id, user_id)
);
