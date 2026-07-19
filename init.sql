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
    embedding vector(512),
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
    ADD COLUMN IF NOT EXISTS wechat_user_id TEXT DEFAULT NULL;

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

INSERT INTO accounts (account_id, name, industry, website, stage, owner_id, region, notes) VALUES
    ('acc_kuaishou', '快手', '互联网/短视频', 'https://www.kuaishou.com', 'qualified', 'sales_001', '北京', '港股上市公司，短视频和直播广告投放需求大'),
    ('acc_xiaohongshu', '小红书', '互联网/内容社区', 'https://www.xiaohongshu.com', 'prospect', 'sales_001', '上海', '种草营销、KOL 合作潜在需求'),
    ('acc_bytedance', '字节跳动', '互联网/综合', 'https://www.bytedance.com', 'closed-won', 'sales_002', '北京', '已合作效果广告年度框架')
ON CONFLICT (account_id) DO NOTHING;

INSERT INTO contacts (contact_id, account_id, name, title, department, role_in_deal, email, notes) VALUES
    ('ct_kuaishou_1', 'acc_kuaishou', '张总', '市场总监', '市场部', 'decision', 'zhang@kuaishou.com', '关注 ROI 和案例'),
    ('ct_kuaishou_2', 'acc_kuaishou', '刘经理', '效果广告负责人', '增长部', 'user', 'liu@kuaishou.com', '关注投放技术和数据对接'),
    ('ct_xiaohongshu_1', 'acc_xiaohongshu', '陈总监', '品牌合作负责人', '商业化', 'budget', 'chen@xiaohongshu.com', '关注 KOL 资源和内容质量')
ON CONFLICT (contact_id) DO NOTHING;

INSERT INTO deals (deal_id, account_id, owner_id, name, stage, service_line, expected_value, next_step) VALUES
    ('deal_kuaishou_2026', 'acc_kuaishou', 'sales_001', '快手 Q3 效果广告投放', 'proposal', 'performance-ads', 1500000, '周四前提交方案'),
    ('deal_xiaohongshu_kol', 'acc_xiaohongshu', 'sales_001', '小红书 KOL 种草campaign', 'qualification', 'kol', 800000, '约陈总监下周沟通预算')
ON CONFLICT (deal_id) DO NOTHING;

INSERT INTO activities (activity_id, entity_type, entity_id, owner_id, activity_type, direction, content, sentiment) VALUES
    ('act_kuaishou_1', 'account', 'acc_kuaishou', 'sales_001', 'meeting', 'outbound', '与张总、刘经理开会，讨论 Q3 投放目标和 KPI', 'positive'),
    ('act_kuaishou_2', 'contact', 'ct_kuaishou_1', 'sales_001', 'email', 'outbound', '发送案例集和报价单', 'neutral'),
    ('act_xiaohongshu_1', 'account', 'acc_xiaohongshu', 'sales_001', 'call', 'outbound', '与陈总监初步沟通 KOL 合作模式', 'positive')
ON CONFLICT (activity_id) DO NOTHING;

-- ========== 商业化：组织与 LLM 用量计量 ==========

CREATE TABLE IF NOT EXISTS organizations (
    org_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    monthly_token_quota BIGINT NOT NULL DEFAULT 10000000,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS org_id TEXT DEFAULT 'org_default';

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
