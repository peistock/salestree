"""
四层记忆系统（Phase 2.5 升级版）
对外：创作放大器（朋友圈、自传、绘本、照片配文、方案整理）
对内：销售助理（客户、商机、文案、研究）

- L1 核心记忆：全局规则、当前时间、安全底线
- L2 个人画像：结构化用户档案（兴趣/客户/团队/经历）
- L3 情景记忆：对话线程 + 历史摘要 + 创作草稿
- L4 技能库：按场景分类的 skill 目录
"""
import os
import time
import json
import uuid
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor

from mind.llm_client import chat
from mind.encryption import encrypt_api_key, decrypt_api_key

logger = logging.getLogger(__name__)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "family")
DB_PASSWORD = os.getenv("DB_PASSWORD", "salesmind2026")
DB_NAME = os.getenv("DB_NAME", "salesmind")

MODEL_SUMMARY = os.getenv("MODEL_SUMMARY", "gemma-4-26b-a4b-it-ud")

# 摘要触发阈值
THREAD_SUMMARY_THRESHOLD = 20
THREAD_INACTIVE_MINUTES = 30


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, dbname=DB_NAME
    )


def _encrypt_llm_config(config: Optional[dict]) -> Optional[dict]:
    """加密 llm_config 中的 apiKey。"""
    if not config:
        return config
    cfg = dict(config)
    if cfg.get("apiKey"):
        cfg["apiKey"] = encrypt_api_key(cfg["apiKey"])
    return cfg


def _decrypt_llm_config(config: Optional[dict]) -> Optional[dict]:
    """解密 llm_config 中的 apiKey。"""
    if not config:
        return config
    cfg = dict(config)
    if cfg.get("apiKey"):
        try:
            cfg["apiKey"] = decrypt_api_key(cfg["apiKey"])
        except Exception as e:
            logger.warning(f"llm_config apiKey 解密失败: {e}")
    return cfg


def init_db():
    """初始化数据库"""
    conn = get_conn()
    # 确保扩展字段存在（兼容已部署实例）
    with conn.cursor() as c:
        c.execute("""
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
            ADD COLUMN IF NOT EXISTS org_id TEXT DEFAULT 'org_default',
            ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS llm_config JSONB DEFAULT '{}';
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_profiles_org
            ON user_profiles(org_id, status);
        """)
        c.execute("""
            ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS research_summary TEXT,
            ADD COLUMN IF NOT EXISTS last_research_at TIMESTAMP;
        """)
        c.execute("""
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
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_creation_workspace_user
            ON creation_workspace(user_id, updated_at DESC);
        """)
        # Phase 4：外挂大脑表
        c.execute("""
            CREATE TABLE IF NOT EXISTS briefings (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_by TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                effective_date DATE DEFAULT CURRENT_DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_briefings_user_date
            ON briefings(user_id, effective_date DESC);
        """)
        c.execute("""
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
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_overrides_user_status
            ON overrides(user_id, status, priority DESC);
        """)
        # 兼容旧表名：若存在 couple_notifications 则重命名为 notifications
        c.execute("""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_schema='public' AND table_name='couple_notifications')
                    AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                                    WHERE table_schema='public' AND table_name='notifications')
                THEN
                    ALTER TABLE couple_notifications RENAME TO notifications;
                END IF;
            END $$;
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_user_read
            ON notifications(user_id, is_read, created_at DESC);
        """)
        # 销售管理表
        c.execute("""
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
                research_summary TEXT,
                last_research_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_id, updated_at DESC);
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_accounts_stage ON accounts(stage, updated_at DESC);
        """)
        c.execute("""
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
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_account ON contacts(account_id, updated_at DESC);
        """)
        c.execute("""
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
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_deals_owner ON deals(owner_id, updated_at DESC);
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_deals_account ON deals(account_id, updated_at DESC);
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage, updated_at DESC);
        """)
        c.execute("""
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
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_activities_entity ON activities(entity_type, entity_id, created_at DESC);
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_activities_owner ON activities(owner_id, created_at DESC);
        """
        )
        # Todo 持久化表
        c.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                store_key TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                todos_json JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_todos_user
            ON todos(user_id, updated_at DESC);
        """)
    conn.commit()
    conn.close()
    logger.info("数据库连接正常，schema 已同步")
    seed_sales_data()


def seed_sales_data():
    """如果销售表为空，插入示例销售人员、客户、联系人、商机和活动"""
    try:
        conn = get_conn()
        with conn.cursor() as c:
            # 只有不存在销售人员时才插入示例销售
            c.execute("SELECT COUNT(*) FROM user_profiles WHERE entity_type='sales'")
            if c.fetchone()[0] == 0:
                c.execute("""
                    INSERT INTO user_profiles (user_id, name, role, preferences, entity_type, team_id)
                    VALUES ('sales_001', '李明', '销售经理', '高效简洁，关注赢单', 'sales', 'team_a'),
                           ('sales_002', '王芳', '客户经理', '详细完整，关注客户关系', 'sales', 'team_a')
                    ON CONFLICT (user_id) DO NOTHING;
                """)

            # 客户/联系人/商机/活动是新表，直接插入示例数据（幂等）
            c.execute("""
                INSERT INTO accounts (account_id, name, industry, website, stage, owner_id, region, notes)
                VALUES ('acc_kuaishou', '快手', '互联网/短视频', 'https://www.kuaishou.com', 'qualified', 'sales_001', '北京', '港股上市公司，短视频和直播广告投放需求大'),
                       ('acc_xiaohongshu', '小红书', '互联网/内容社区', 'https://www.xiaohongshu.com', 'prospect', 'sales_001', '上海', '种草营销、KOL 合作潜在需求'),
                       ('acc_bytedance', '字节跳动', '互联网/综合', 'https://www.bytedance.com', 'closed-won', 'sales_002', '北京', '已合作效果广告年度框架')
                ON CONFLICT (account_id) DO NOTHING;
            """)
            c.execute("""
                INSERT INTO contacts (contact_id, account_id, name, title, department, role_in_deal, email, notes)
                VALUES ('ct_kuaishou_1', 'acc_kuaishou', '张总', '市场总监', '市场部', 'decision', 'zhang@kuaishou.com', '关注 ROI 和案例'),
                       ('ct_kuaishou_2', 'acc_kuaishou', '刘经理', '效果广告负责人', '增长部', 'user', 'liu@kuaishou.com', '关注投放技术和数据对接'),
                       ('ct_xiaohongshu_1', 'acc_xiaohongshu', '陈总监', '品牌合作负责人', '商业化', 'budget', 'chen@xiaohongshu.com', '关注 KOL 资源和内容质量')
                ON CONFLICT (contact_id) DO NOTHING;
            """)
            c.execute("""
                INSERT INTO deals (deal_id, account_id, owner_id, name, stage, service_line, expected_value, next_step)
                VALUES ('deal_kuaishou_2026', 'acc_kuaishou', 'sales_001', '快手 Q3 效果广告投放', 'proposal', 'performance-ads', 1500000, '周四前提交方案'),
                       ('deal_xiaohongshu_kol', 'acc_xiaohongshu', 'sales_001', '小红书 KOL 种草campaign', 'qualification', 'kol', 800000, '约陈总监下周沟通预算')
                ON CONFLICT (deal_id) DO NOTHING;
            """)
            c.execute("""
                INSERT INTO activities (activity_id, entity_type, entity_id, owner_id, activity_type, direction, content, sentiment)
                VALUES ('act_kuaishou_1', 'account', 'acc_kuaishou', 'sales_001', 'meeting', 'outbound', '与张总、刘经理开会，讨论 Q3 投放目标和 KPI', 'positive'),
                       ('act_kuaishou_2', 'contact', 'ct_kuaishou_1', 'sales_001', 'email', 'outbound', '发送案例集和报价单', 'neutral'),
                       ('act_xiaohongshu_1', 'account', 'acc_xiaohongshu', 'sales_001', 'call', 'outbound', '与陈总监初步沟通 KOL 合作模式', 'positive')
                ON CONFLICT (activity_id) DO NOTHING;
            """)
        conn.commit()
        conn.close()
        logger.info("销售示例数据已初始化")
    except Exception as e:
        logger.warning(f"销售示例数据初始化失败: {e}")


class Memory:
    """四层记忆系统"""

    def __init__(self, user_id: str, user_name: str = ""):
        self.user_id = user_id
        self.user_name = user_name
        self.conn = get_conn()

    def load(self) -> Dict[str, str]:
        """加载全部记忆，组装成 Prompt 上下文"""
        return {
            "l1_core": self._l1_core(),
            "l2_profile": self._l2_profile(),
            "l3_episodes": self._l3_episodes(),
            "l3_summaries": self._l3_summaries(),
            "l3_team_shared": self._l3_team_shared(),
            "l3_workspace": self._l3_workspace(),
            "l4_skills": self._l4_skills(),
        }

    def _l1_core(self) -> str:
        now = datetime.now().strftime("%Y年%m月%d日 %H:%M %A")
        return f"""你是亿科数字的销售智能助手「销销」，服务互联网广告/营销公司的销售团队。
核心定位：
1. 知更多——帮销售人员快速完成客户公司、竞品、决策链的深度研究
2. 说得准——基于客户动态和联系人角色，生成个性化、有策略的沟通内容
3. 做得细——主动提醒商机 next_step、客户互动频率和潜在风险
安全与专业底线：
- 客户公司敏感信息只读不写，不对外泄露
- 不确定时反问，不瞎猜
- 引用外部信息必须标注来源
- 当前时间：{now}"""

    def _l2_profile(self) -> str:
        """L2 个人画像（OpenViking 8 类 Memory 组织）"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """SELECT name, role, preferences,
                          interests, current_projects, communication_style,
                          life_experiences, writing_patterns
                   FROM user_profiles WHERE user_id=%s""",
                (self.user_id,)
            )
            row = c.fetchone()
            if not row:
                return f"用户：{self.user_name}（新成员）"

            parts = []

            # 1. Profile — 基础画像
            parts.append(f"【画像】{row['name']}，{row['role']}。")

            # 2. Preferences — 偏好
            prefs = row.get("preferences") or ""
            if prefs and prefs != "无":
                parts.append(f"【偏好】{prefs}。")

            # 3. Events — 事件记录（从最近摘要中提取）
            with self.conn.cursor(cursor_factory=RealDictCursor) as c2:
                c2.execute(
                    """SELECT summary_text, summary_type FROM memory_summaries
                       WHERE user_id=%s ORDER BY created_at DESC LIMIT 2""",
                    (self.user_id,)
                )
                event_rows = c2.fetchall()
                if event_rows:
                    events = [f"[{r['summary_type']}] {r['summary_text'][:80]}" for r in event_rows]
                    parts.append(f"【事件】{'；'.join(events)}。")

            # 4. Cases — 交互案例
            style = row.get("communication_style") or {}
            if isinstance(style, str):
                try:
                    style = json.loads(style)
                except Exception:
                    style = {}
            cases = style.get("interaction_cases", []) if isinstance(style, dict) else []
            if cases:
                case_texts = [c.get("case", "")[:40] for c in cases[-3:]]
                parts.append(f"【案例】{'；'.join(case_texts)}。")

            # 5. Patterns — 模式（文风 + 沟通风格）
            writing = row.get("writing_patterns") or {}
            if isinstance(writing, str):
                try:
                    writing = json.loads(writing)
                except Exception:
                    writing = {}
            if writing:
                fp_parts = []
                if writing.get("tone"):
                    fp_parts.append(f"语气{writing['tone']}")
                if writing.get("sentence_length"):
                    fp_parts.append(f"句子{writing['sentence_length']}")
                if writing.get("common_phrases"):
                    fp_parts.append(f"常用语：{'、'.join(writing['common_phrases'][:3])}")
                if fp_parts:
                    parts.append(f"【模式-文风】{', '.join(fp_parts)}。")
            if style and isinstance(style, dict):
                if style.get("verbosity"):
                    parts.append(f"【模式-沟通】喜欢{style['verbosity']}的回复")

            # 6. Tools — 工具知识
            tools = style.get("tool_knowledge", []) if isinstance(style, dict) else []
            if tools:
                tool_notes = [t.get("note", "")[:30] for t in tools[-3:]]
                parts.append(f"【工具】{'；'.join(tool_notes)}。")

            # 7. Skills — 技能/创作项目
            projects = row.get("current_projects") or []
            if isinstance(projects, str):
                try:
                    projects = json.loads(projects)
                except Exception:
                    projects = []
            if projects:
                proj_lines = []
                for p in projects:
                    if isinstance(p, dict):
                        name = p.get("name", "")
                        status = p.get("status", "")
                        proj_lines.append(f"{name}（{status}）")
                    else:
                        proj_lines.append(str(p))
                parts.append(f"【技能/创作】{'；'.join(proj_lines)}。")

            # 人生经历（独立展示）
            if row.get("life_experiences"):
                parts.append(f"【经历】{row['life_experiences']}。")

            return " ".join(parts)

    def _l3_episodes(self, limit: int = 15) -> str:
        """L3 短期记忆：当前线程的最近对话"""
        thread_id = self._get_latest_thread()
        if not thread_id:
            return "（无近期记忆）"

        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT role, content FROM episodic_memory
                WHERE user_id=%s AND thread_id=%s
                ORDER BY created_at DESC LIMIT %s
                """,
                (self.user_id, thread_id, limit)
            )
            rows = c.fetchall()
            if not rows:
                return "（无近期记忆）"
            lines = [f"{'用户' if r['role'] == 'user' else '销销'}：{r['content'][:300]}"
                     for r in rows]
            return "当前对话：\n" + "\n".join(reversed(lines))

    def _l3_summaries(self, limit: int = 3) -> str:
        """L3 中期记忆：历史摘要（含当前线程，新线程可继承上下文）"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT summary_text, summary_type, created_at FROM memory_summaries
                WHERE user_id=%s
                ORDER BY created_at DESC LIMIT %s
                """,
                (self.user_id, limit)
            )
            rows = c.fetchall()
            if not rows:
                return "（无历史摘要）"
            lines = []
            for r in rows:
                date_str = r['created_at'].strftime("%m月%d日") if hasattr(r['created_at'], 'strftime') else ""
                lines.append(f"[{date_str} {r['summary_type']}] {r['summary_text'][:150]}")
            return "历史回顾：\n" + "\n".join(lines)

    def _l3_team_shared(self) -> str:
        """L3 团队共享记忆：同团队销售人员的重点客户与商机摘要"""
        # 查询当前用户 team
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT team_id FROM user_profiles WHERE user_id=%s", (self.user_id,))
            row = c.fetchone()
            if not row or not row.get('team_id'):
                return ""
            team_id = row['team_id']

        lines = []
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            # 同团队其他销售的最近活跃客户
            c.execute("""
                SELECT a.account_id, a.name, a.stage, a.notes, up.name as owner_name
                FROM accounts a
                JOIN user_profiles up ON a.owner_id = up.user_id
                WHERE up.team_id = %s AND a.owner_id != %s
                ORDER BY a.updated_at DESC LIMIT 5
            """, (team_id, self.user_id))
            accounts = c.fetchall()

            # 同团队其他销售的近期商机
            c.execute("""
                SELECT d.deal_id, d.name, d.stage, d.expected_value, d.next_step, up.name as owner_name
                FROM deals d
                JOIN user_profiles up ON d.owner_id = up.user_id
                WHERE up.team_id = %s AND d.owner_id != %s
                ORDER BY d.updated_at DESC LIMIT 5
            """, (team_id, self.user_id))
            deals = c.fetchall()

        if accounts:
            lines.append("团队重点客户：")
            for a in accounts:
                lines.append(f"- {a['name']}（{a['stage']}，负责人：{a['owner_name']}）")
        if deals:
            lines.append("团队近期商机：")
            for d in deals:
                value = f"{d['expected_value']:,.0f}" if d['expected_value'] else "未设定"
                lines.append(f"- {d['name']}（{d['stage']}，预计 {value} 元，负责人：{d['owner_name']}）")

        return "\n".join(lines) if lines else ""

    def _l3_workspace(self) -> str:
        """L3 工作空间：当前进行中的项目草稿"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT project_name, project_type, draft_content, status,
                       version, style_preset, source_material
                FROM creation_workspace
                WHERE user_id=%s AND status IN ('drafting', 'reviewing')
                ORDER BY updated_at DESC LIMIT 3
                """,
                (self.user_id,)
            )
            rows = c.fetchall()
            if not rows:
                return "（无进行中的项目）"
            lines = ["进行中的项目："]
            for r in rows:
                lines.append(f"- [{r['project_type']}] {r['project_name']}（v{r['version']} {r['status']}）：{r['draft_content'][:80]}...")
            return "\n".join(lines)

    # ========== 创作空间操作接口（v1.1）==========

    def workspace_read(self, project_name: str = None, status: str = "drafting") -> dict:
        """读取创作空间草稿"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            if project_name:
                c.execute(
                    """
                    SELECT * FROM creation_workspace
                    WHERE user_id=%s AND project_name=%s AND status=%s
                    ORDER BY version DESC LIMIT 1
                    """,
                    (self.user_id, project_name, status)
                )
            else:
                c.execute(
                    """
                    SELECT * FROM creation_workspace
                    WHERE user_id=%s AND status=%s
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (self.user_id, status)
                )
            row = c.fetchone()
            if row:
                # 更新 last_accessed_by_agent
                c.execute(
                    "UPDATE creation_workspace SET last_accessed_by_agent=NOW() WHERE id=%s",
                    (row['id'],)
                )
                self.conn.commit()
            return dict(row) if row else {}

    def workspace_write(self, project_name: str, content: str,
                        project_type: str = "draft",
                        status: str = "drafting",
                        style_preset: dict = None,
                        source_material: dict = None,
                        care_context: dict = None) -> dict:
        """写入/更新创作空间草稿"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            # 查找现有草稿
            c.execute(
                """
                SELECT id, version FROM creation_workspace
                WHERE user_id=%s AND project_name=%s AND status='drafting'
                ORDER BY version DESC LIMIT 1
                """,
                (self.user_id, project_name)
            )
            row = c.fetchone()

            if row:
                # 更新现有草稿
                draft_id = row['id']
                updates = ["draft_content = %s", "updated_at = NOW()", "status = %s"]
                params = [content, status]

                if style_preset:
                    updates.append("style_preset = %s")
                    params.append(json.dumps(style_preset))
                if source_material:
                    updates.append("source_material = %s")
                    params.append(json.dumps(source_material))
                if care_context:
                    updates.append("care_context = %s")
                    params.append(json.dumps(care_context))

                if status == 'reviewing':
                    updates.append("version = version + 1")
                if status == 'done':
                    updates.append("completed_at = NOW()")

                params.append(draft_id)
                c.execute(
                    f"UPDATE creation_workspace SET {', '.join(updates)} WHERE id = %s",
                    tuple(params)
                )
                self.conn.commit()
                return {"action": "updated", "project_name": project_name, "status": status}
            else:
                # 新建草稿
                c.execute(
                    """
                    INSERT INTO creation_workspace
                    (user_id, project_name, project_type, draft_content, status,
                     style_preset, source_material, care_context, version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        self.user_id, project_name, project_type, content, status,
                        json.dumps(style_preset or {}),
                        json.dumps(source_material or {}),
                        json.dumps(care_context or {}),
                    )
                )
                self.conn.commit()
                return {"action": "created", "project_name": project_name, "status": status}

    def _l4_skills(self) -> str:
        """加载默认技能库（按需分类）"""
        skills_dir = os.path.join(os.getenv("DATA_DIR", "./data"), "skills")
        if not os.path.exists(skills_dir):
            return "（无技能库）"

        texts = []

        # 1. 优先加载 skill_index.md
        index_path = os.path.join(skills_dir, "skill_index.md")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    texts.append(f"--- Skill Index ---\n{f.read()[:1200]}")
            except Exception:
                pass

        # 2. 加载通用 skill
        for fname in os.listdir(skills_dir):
            if not fname.endswith(".md"):
                continue
            if fname == "skill_index.md":
                continue
            if "通用" in fname:
                fpath = os.path.join(skills_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        texts.append(f"--- Skill: {fname} ---\n{f.read()[:800]}")
                except Exception:
                    pass

        return "\n\n".join(texts) if texts else "（无匹配技能）"

    def load_skill(self, skill_name: str, max_chars: int = 2000) -> str:
        """
        按需加载指定 skill 的完整内容
        skill_name: 文件名（不含 .md）或文件名包含的关键词
        """
        skills_dir = os.path.join(os.getenv("DATA_DIR", "./data"), "skills")
        if not os.path.exists(skills_dir):
            return ""

        # 先尝试精确匹配
        candidates = []
        for root, dirs, files in os.walk(skills_dir):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                name_without_ext = fname[:-3]
                if name_without_ext == skill_name or skill_name in name_without_ext:
                    candidates.append(os.path.join(root, fname))

        if not candidates:
            return ""

        # 优先选择 skills_dir 根目录下的（适配版），其次是子目录
        candidates.sort(key=lambda p: 0 if os.path.dirname(p) == skills_dir else 1)

        try:
            with open(candidates[0], "r", encoding="utf-8") as f:
                content = f.read()
                if max_chars > 0 and len(content) > max_chars:
                    content = content[:max_chars] + "\n...（skill 内容已截断）"
                return f"--- Skill: {skill_name} ---\n{content}"
        except Exception as e:
            logger.warning(f"加载 skill {skill_name} 失败: {e}")
            return ""

    def load_skills_by_category(self, category: str, max_chars: int = 1500) -> str:
        """按分类加载 skills（creation/info/expression/social/world）"""
        skills_dir = os.path.join(os.getenv("DATA_DIR", "./data"), "skills")
        cat_dir = os.path.join(skills_dir, category)
        if not os.path.exists(cat_dir):
            return ""

        texts = []
        for fname in sorted(os.listdir(cat_dir)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(cat_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                    if len(content) > max_chars:
                        content = content[:max_chars] + "\n..."
                    texts.append(f"--- {category}/{fname} ---\n{content}")
            except Exception:
                pass

        return "\n\n".join(texts) if texts else ""

    def _get_latest_thread(self) -> Optional[str]:
        """获取用户最新的活跃线程（30 分钟内）"""
        with self.conn.cursor() as c:
            c.execute(
                """
                SELECT thread_id FROM conversation_threads
                WHERE user_id=%s AND is_archived=FALSE
                  AND updated_at > NOW() - INTERVAL '30 minutes'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (self.user_id,)
            )
            row = c.fetchone()
            return row[0] if row else None

    def save_message(self, thread_id: str, role: str, content: str, tags: str = ""):
        """保存对话记录，并检查是否需要触发摘要"""
        with self.conn.cursor() as c:
            c.execute(
                """
                INSERT INTO episodic_memory (user_id, thread_id, role, content, tags, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (self.user_id, thread_id, role, content, tags)
            )
            c.execute(
                """
                INSERT INTO conversation_threads (thread_id, user_id, message_count, updated_at)
                VALUES (%s, %s, 1, NOW())
                ON CONFLICT (thread_id) DO UPDATE
                SET message_count = conversation_threads.message_count + 1,
                    updated_at = NOW()
                """,
                (thread_id, self.user_id)
            )
            self.conn.commit()

        self._maybe_summarize(thread_id)

    def load_recent_messages(self, thread_id: str, limit: int = 20):
        """加载当前线程最近的对话消息，返回 [(role, content), ...]（按时间正序）"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT role, content FROM episodic_memory
                WHERE user_id=%s AND thread_id=%s
                ORDER BY created_at DESC LIMIT %s
                """,
                (self.user_id, thread_id, limit)
            )
            rows = c.fetchall()
        # 去掉最新一条（当前 query 还未保存时会包含；已保存时不应重复）
        # 调用方负责过滤当前 query
        return [(r["role"], r["content"]) for r in reversed(rows)]

    def _maybe_summarize(self, thread_id: str):
        """检查是否满足摘要触发条件"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT message_count, updated_at, created_at FROM conversation_threads WHERE thread_id=%s",
                (thread_id,)
            )
            row = c.fetchone()
            if not row:
                return

            msg_count = row["message_count"]
            updated = row["updated_at"]
            created = row["created_at"]

            if msg_count >= THREAD_SUMMARY_THRESHOLD:
                logger.info(f"线程 {thread_id} 消息数 {msg_count}，触发摘要")
                self._summarize_and_archive(thread_id)
                return

            # 用数据库 NOW() 避免 Python 本地时区与数据库 UTC 时差（8小时）
            # 注意：必须用普通 cursor，RealDictCursor 的 fetchone() 返回字典不能用 [0] 索引
            with self.conn.cursor() as c2:
                c2.execute("SELECT NOW()")
                now = c2.fetchone()[0]
            if isinstance(updated, str):
                updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if isinstance(now, str):
                now = datetime.fromisoformat(now.replace("Z", "+00:00"))
            # 统一去掉时区信息，避免 aware 和 naive datetime 相减报错
            if now.tzinfo is not None:
                now = now.replace(tzinfo=None)

            age = now - created
            inactive = now - updated

            if age > timedelta(hours=2) and inactive > timedelta(minutes=THREAD_INACTIVE_MINUTES):
                logger.info(f"线程 {thread_id} 已闲置 {inactive.seconds//60} 分钟，触发归档")
                self._summarize_and_archive(thread_id)

    def _summarize_and_archive(self, thread_id: str):
        """生成摘要并归档线程"""
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as c:
                c.execute(
                    """
                    SELECT role, content FROM episodic_memory
                    WHERE thread_id=%s ORDER BY created_at
                    """,
                    (thread_id,)
                )
                messages = c.fetchall()

            if len(messages) < 4:
                logger.debug(f"线程 {thread_id} 消息太少，不生成摘要")
                return

            dialogue = "\n".join([
                f"{'用户' if m['role'] == 'user' else '销销'}：{m['content'][:200]}"
                for m in messages
            ])

            summary = self._generate_summary(dialogue)
            if not summary:
                return

            with self.conn.cursor() as c:
                c.execute(
                    """
                    INSERT INTO memory_summaries (user_id, thread_id, summary_type, summary_text)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (self.user_id, thread_id, "thread", summary)
                )
                c.execute(
                    "UPDATE conversation_threads SET is_archived=TRUE, summary=%s WHERE thread_id=%s",
                    (summary, thread_id)
                )
                self.conn.commit()

            logger.info(f"线程 {thread_id} 已归档，摘要：{summary[:80]}...")

        except Exception as e:
            logger.error(f"摘要生成失败: {e}")

    def _generate_summary(self, dialogue: str) -> str:
        """用 LLM 生成对话摘要"""
        try:
            return chat(
                system="你是一个对话摘要助手。用1-2句话总结以下对话的核心内容，保留关键事实（如创作意图、客户动态、商机进展、团队事务）。",
                user_prompt=f"请总结这段对话：\n\n{dialogue[:3000]}",
                model=MODEL_SUMMARY,
                max_tokens=300,
                temperature=0.3,
            )
        except Exception as e:
            logger.error(f"LLM 摘要失败: {e}")
            return ""

    def get_or_create_thread(self, user_id: str) -> str:
        """获取用户最新的活跃线程，或创建新线程（30 分钟窗口）"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT thread_id FROM conversation_threads
                WHERE user_id=%s AND is_archived=FALSE
                  AND updated_at > NOW() - INTERVAL '30 minutes'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,)
            )
            row = c.fetchone()
            if row:
                return row["thread_id"]

            # 30 分钟无活动：归档旧线程并生成摘要，再创建新线程
            c.execute(
                """
                SELECT thread_id FROM conversation_threads
                WHERE user_id=%s AND is_archived=FALSE
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,)
            )
            old_row = c.fetchone()
            if old_row:
                self._summarize_and_archive(old_row["thread_id"])

            thread_id = f"{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            c.execute(
                "INSERT INTO conversation_threads (thread_id, user_id) VALUES (%s, %s)",
                (thread_id, user_id)
            )
            self.conn.commit()
            logger.info(f"创建新线程: {thread_id}")
            return thread_id

    def new_thread(self, user_id: str) -> str:
        """强制创建新线程（用于 Web UI 的'新建任务'按钮）"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            # 归档当前活跃线程
            c.execute(
                """
                SELECT thread_id FROM conversation_threads
                WHERE user_id=%s AND is_archived=FALSE
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,)
            )
            old_row = c.fetchone()
            if old_row:
                self._summarize_and_archive(old_row["thread_id"])

            thread_id = f"{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            c.execute(
                "INSERT INTO conversation_threads (thread_id, user_id) VALUES (%s, %s)",
                (thread_id, user_id)
            )
            self.conn.commit()
            logger.info(f"新建任务创建新线程: {thread_id}")
            return thread_id

    # ========== 创作空间（L5）==========

    def get_workspace_drafts(self, project_name: str = None, limit: int = 3) -> List[dict]:
        """获取用户的创作草稿"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            if project_name:
                c.execute(
                    """
                    SELECT * FROM creation_workspace
                    WHERE user_id=%s AND project_name=%s
                    ORDER BY updated_at DESC LIMIT %s
                    """,
                    (self.user_id, project_name, limit)
                )
            else:
                c.execute(
                    """
                    SELECT * FROM creation_workspace
                    WHERE user_id=%s AND status IN ('drafting', 'reviewing')
                    ORDER BY updated_at DESC LIMIT %s
                    """,
                    (self.user_id, limit)
                )
            return c.fetchall()

    def save_workspace_draft(self, project_name: str, project_type: str,
                              draft_content: str, status: str = "drafting"):
        """保存或更新创作草稿"""
        with self.conn.cursor() as c:
            # 检查是否已有同名项目
            c.execute(
                """
                SELECT id, version FROM creation_workspace
                WHERE user_id=%s AND project_name=%s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (self.user_id, project_name)
            )
            row = c.fetchone()

            if row:
                # 更新现有项目
                c.execute(
                    """
                    UPDATE creation_workspace
                    SET draft_content=%s, version=version+1, status=%s, updated_at=NOW()
                    WHERE id=%s
                    """,
                    (draft_content, status, row[0])
                )
            else:
                # 创建新项目
                c.execute(
                    """
                    INSERT INTO creation_workspace (user_id, project_name, project_type, draft_content, status)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (self.user_id, project_name, project_type, draft_content, status)
                )
            self.conn.commit()
        logger.info(f"创作草稿已保存: {project_name} ({status})")

    def update_project_status(self, project_name: str, status: str):
        """更新创作项目状态"""
        with self.conn.cursor() as c:
            c.execute(
                """
                UPDATE creation_workspace SET status=%s, updated_at=NOW()
                WHERE user_id=%s AND project_name=%s
                """,
                (status, self.user_id, project_name)
            )
            self.conn.commit()

    # ========== 销售 CRM 基础 CRUD ==========

    def create_account(self, account_id: str, name: str, **kwargs) -> bool:
        """创建客户公司"""
        try:
            with self.conn.cursor() as c:
                fields = ["account_id", "name"]
                values = [account_id, name]
                for k, v in kwargs.items():
                    if v is not None:
                        fields.append(k)
                        values.append(v)
                sql = f"""
                    INSERT INTO accounts ({', '.join(fields)})
                    VALUES ({', '.join(['%s'] * len(fields))})
                    ON CONFLICT (account_id) DO UPDATE SET
                    {', '.join([f"{f}=EXCLUDED.{f}" for f in fields[2:]])},
                    updated_at=NOW()
                """
                c.execute(sql, values)
                self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"创建客户失败: {e}")
            return False

    def get_account(self, account_id: str) -> Optional[dict]:
        """获取客户公司详情"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM accounts WHERE account_id=%s", (account_id,))
            return c.fetchone()

    def get_account_by_name(self, name: str) -> Optional[dict]:
        """按名称模糊匹配客户"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM accounts WHERE name ILIKE %s LIMIT 1", (f"%{name}%",))
            return c.fetchone()

    def save_account_research(self, name: str, summary: str) -> bool:
        """保存客户研究结果到 account.research_summary；如不存在则新建"""
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as c:
                c.execute("SELECT account_id FROM accounts WHERE name ILIKE %s LIMIT 1", (f"%{name}%",))
                row = c.fetchone()
                if row:
                    c.execute(
                        "UPDATE accounts SET research_summary=%s, last_research_at=NOW(), updated_at=NOW() WHERE account_id=%s",
                        (summary, row["account_id"])
                    )
                else:
                    import uuid
                    account_id = f"acc_{uuid.uuid4().hex[:12]}"
                    c.execute(
                        "INSERT INTO accounts (account_id, name, research_summary, last_research_at) VALUES (%s, %s, %s, NOW())",
                        (account_id, name, summary)
                    )
                self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"保存客户研究结果失败: {e}")
            return False

    def list_accounts(self, owner_id: str = None, limit: int = 50) -> List[dict]:
        """列出客户公司"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            if owner_id:
                c.execute("SELECT * FROM accounts WHERE owner_id=%s ORDER BY updated_at DESC LIMIT %s", (owner_id, limit))
            else:
                c.execute("SELECT * FROM accounts ORDER BY updated_at DESC LIMIT %s", (limit,))
            return c.fetchall()

    def delete_account(self, account_id: str) -> bool:
        """删除客户公司，级联删除联系人、商机和相关活动记录"""
        try:
            with self.conn.cursor() as c:
                # 删除关联活动记录
                c.execute("DELETE FROM activities WHERE entity_type = 'account' AND entity_id = %s", (account_id,))
                # contacts 和 deals 已通过 ON DELETE CASCADE 自动删除
                c.execute("DELETE FROM accounts WHERE account_id = %s", (account_id,))
                self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"删除客户失败: {e}")
            return False

    def update_account(self, account_id: str, **kwargs) -> bool:
        """更新客户公司信息，只更新非 None 字段"""
        try:
            allowed_fields = {"name", "industry", "website", "stage", "owner_id", "annual_revenue_band", "employee_count_band", "region", "notes", "research_summary"}
            fields = []
            values = []
            for k, v in kwargs.items():
                if k in allowed_fields and v is not None:
                    fields.append(f"{k} = %s")
                    values.append(v)
            if not fields:
                return False
            values.append(account_id)
            with self.conn.cursor() as c:
                c.execute(
                    f"UPDATE accounts SET {', '.join(fields)}, updated_at = NOW() WHERE account_id = %s",
                    tuple(values)
                )
                self.conn.commit()
                return c.rowcount > 0
        except Exception as e:
            logger.warning(f"更新客户失败: {e}")
            return False

    def create_contact(self, contact_id: str, account_id: str, name: str, **kwargs) -> bool:
        """创建联系人"""
        try:
            with self.conn.cursor() as c:
                fields = ["contact_id", "account_id", "name"]
                values = [contact_id, account_id, name]
                for k, v in kwargs.items():
                    if v is not None:
                        fields.append(k)
                        values.append(v)
                sql = f"""
                    INSERT INTO contacts ({', '.join(fields)})
                    VALUES ({', '.join(['%s'] * len(fields))})
                    ON CONFLICT (contact_id) DO UPDATE SET
                    {', '.join([f"{f}=EXCLUDED.{f}" for f in fields[3:]])},
                    updated_at=NOW()
                """
                c.execute(sql, values)
                self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"创建联系人失败: {e}")
            return False

    def get_contact(self, contact_id: str) -> Optional[dict]:
        """获取联系人"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM contacts WHERE contact_id=%s", (contact_id,))
            return c.fetchone()

    def list_contacts_by_account(self, account_id: str) -> List[dict]:
        """列出某客户下的联系人"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM contacts WHERE account_id=%s ORDER BY updated_at DESC", (account_id,))
            return c.fetchall()

    def create_deal(self, deal_id: str, account_id: str, name: str, **kwargs) -> bool:
        """创建商机"""
        try:
            with self.conn.cursor() as c:
                fields = ["deal_id", "account_id", "name"]
                values = [deal_id, account_id, name]
                for k, v in kwargs.items():
                    if v is not None:
                        fields.append(k)
                        values.append(v)
                sql = f"""
                    INSERT INTO deals ({', '.join(fields)})
                    VALUES ({', '.join(['%s'] * len(fields))})
                    ON CONFLICT (deal_id) DO UPDATE SET
                    {', '.join([f"{f}=EXCLUDED.{f}" for f in fields[3:]])},
                    updated_at=NOW()
                """
                c.execute(sql, values)
                self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"创建商机失败: {e}")
            return False

    def get_deal(self, deal_id: str) -> Optional[dict]:
        """获取商机"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM deals WHERE deal_id=%s", (deal_id,))
            return c.fetchone()

    def list_deals_by_account(self, account_id: str) -> List[dict]:
        """列出某客户下的商机"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM deals WHERE account_id=%s ORDER BY updated_at DESC", (account_id,))
            return c.fetchall()

    def log_activity(self, activity_id: str, entity_type: str, entity_id: str,
                     activity_type: str, content: str, direction: str = None,
                     sentiment: str = None, owner_id: str = None) -> bool:
        """记录销售活动"""
        try:
            with self.conn.cursor() as c:
                c.execute("""
                    INSERT INTO activities (activity_id, entity_type, entity_id, owner_id,
                                            activity_type, direction, content, sentiment)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (activity_id) DO UPDATE SET
                    content=EXCLUDED.content, sentiment=EXCLUDED.sentiment, updated_at=NOW()
                """, (activity_id, entity_type, entity_id, owner_id or self.user_id,
                      activity_type, direction, content, sentiment))
                self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"记录活动失败: {e}")
            return False

    def list_activities(self, entity_type: str = None, entity_id: str = None,
                        owner_id: str = None, limit: int = 20) -> List[dict]:
        """查询活动记录"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            if entity_type and entity_id:
                c.execute("""
                    SELECT * FROM activities
                    WHERE entity_type=%s AND entity_id=%s
                    ORDER BY created_at DESC LIMIT %s
                """, (entity_type, entity_id, limit))
            elif owner_id:
                c.execute("""
                    SELECT * FROM activities
                    WHERE owner_id=%s
                    ORDER BY created_at DESC LIMIT %s
                """, (owner_id, limit))
            else:
                c.execute("""
                    SELECT * FROM activities ORDER BY created_at DESC LIMIT %s
                """, (limit,))
            return c.fetchall()

    # ========== 画像更新 ==========

    def update_profile_field(self, field: str, value):
        """更新用户画像字段（支持 JSONB 和 TEXT）"""
        allowed = {"preferences", "interests", "current_projects",
                   "communication_style", "life_experiences"}
        if field not in allowed:
            logger.warning(f"不允许更新的字段: {field}")
            return

        with self.conn.cursor() as c:
            if field in ("interests", "current_projects", "communication_style"):
                # JSONB 字段
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                c.execute(
                    f"UPDATE user_profiles SET {field}=%s::jsonb, updated_at=NOW() WHERE user_id=%s",
                    (value, self.user_id)
                )
            else:
                c.execute(
                    f"UPDATE user_profiles SET {field}=%s, updated_at=NOW() WHERE user_id=%s",
                    (value, self.user_id)
                )
            self.conn.commit()

    def consolidate(self, query: str, summary: str, thread_id: str = None):
        """
        记忆沉淀：从任务总结中提取事实更新画像（OpenViking 8 类 Memory 适配）
        1. profile/preferences/entities/events/patterns — 已有字段映射
        2. cases/tools — 新增到 communication_style JSONB
        """
        try:
            lower = summary.lower()
            updated = False

            # 1. 提取兴趣（preferences）
            interest_keywords = ["喜欢", "爱好", "感兴趣", "想学", "最近在学"]
            if any(k in lower for k in interest_keywords):
                with self.conn.cursor() as c:
                    c.execute("SELECT interests FROM user_profiles WHERE user_id=%s", (self.user_id,))
                    row = c.fetchone()
                    current = []
                    if row and row[0]:
                        try:
                            current = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        except Exception:
                            current = []
                    if not isinstance(current, list):
                        current = []
                    new_interests = list(set(current + [summary[:100]]))
                    self.update_profile_field("interests", new_interests[:10])
                updated = True

            # 2. 提取创作项目（patterns/skills）
            project_keywords = ["整理", "写", "创作", "做", "编辑", "制作", "绘本", "自传", "朋友圈", "文案"]
            if any(k in lower for k in project_keywords):
                with self.conn.cursor() as c:
                    c.execute("SELECT current_projects FROM user_profiles WHERE user_id=%s", (self.user_id,))
                    row = c.fetchone()
                    projects = []
                    if row and row[0]:
                        try:
                            projects = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        except Exception:
                            projects = []
                    if not isinstance(projects, list):
                        projects = []
                    new_project = {"name": summary[:50], "status": "进行中", "last_update": datetime.now().strftime("%Y-%m-%d")}
                    projects.append(new_project)
                    self.update_profile_field("current_projects", projects[:5])
                updated = True

            # 3. 提取创作项目（patterns/skills）
            project_keywords = ["整理", "写", "创作", "做", "编辑", "制作", "绘本", "自传", "朋友圈", "文案"]
            if any(k in lower for k in project_keywords):
                with self.conn.cursor() as c:
                    c.execute("SELECT current_projects FROM user_profiles WHERE user_id=%s", (self.user_id,))
                    row = c.fetchone()
                    projects = []
                    if row and row[0]:
                        try:
                            projects = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        except Exception:
                            projects = []
                    if not isinstance(projects, list):
                        projects = []
                    new_project = {"name": summary[:50], "status": "进行中", "last_update": datetime.now().strftime("%Y-%m-%d")}
                    projects.append(new_project)
                    self.update_profile_field("current_projects", projects[:5])
                updated = True

            # 4. 提取交互案例（cases — 新增）
            case_keywords = ["例子", "案例", "情况", "时候", "曾经", "上次", "有一次"]
            if any(k in lower for k in case_keywords):
                with self.conn.cursor() as c:
                    c.execute("SELECT communication_style FROM user_profiles WHERE user_id=%s", (self.user_id,))
                    row = c.fetchone()
                    style = {}
                    if row and row[0]:
                        try:
                            style = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        except Exception:
                            style = {}
                    if not isinstance(style, dict):
                        style = {}
                    cases = style.get("interaction_cases", [])
                    if not isinstance(cases, list):
                        cases = []
                    cases.append({"date": datetime.now().strftime("%Y-%m-%d"), "case": summary[:200]})
                    style["interaction_cases"] = cases[-20:]  # 保留最近 20 条
                    self.update_profile_field("communication_style", style)
                updated = True

            # 5. 提取工具知识（tools — 新增）
            tool_keywords = ["用", "工具", "软件", "app", "小程序", "公众号", "怎么操作", "怎么用"]
            if any(k in lower for k in tool_keywords):
                with self.conn.cursor() as c:
                    c.execute("SELECT communication_style FROM user_profiles WHERE user_id=%s", (self.user_id,))
                    row = c.fetchone()
                    style = {}
                    if row and row[0]:
                        try:
                            style = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        except Exception:
                            style = {}
                    if not isinstance(style, dict):
                        style = {}
                    tools = style.get("tool_knowledge", [])
                    if not isinstance(tools, list):
                        tools = []
                    tools.append({"date": datetime.now().strftime("%Y-%m-%d"), "note": summary[:200]})
                    style["tool_knowledge"] = tools[-10:]
                    self.update_profile_field("communication_style", style)
                updated = True

            # 保存到情景记忆（events）
            if thread_id:
                self.save_message(thread_id, "system", f"[反思] {summary[:200]}", "consolidation")

            if updated:
                logger.info(f"用户 {self.user_id} 画像已更新（OpenViking 8 类）")

        except Exception as e:
            logger.error(f"记忆沉淀失败: {e}")

    def close(self):
        self.conn.close()


# ========== Phase 4：外挂大脑查询接口 ==========

def get_active_briefings(user_id: str, effective_date=None) -> List[dict]:
    """获取某用户今日 active 的 briefings"""
    from datetime import date
    if effective_date is None:
        effective_date = date.today()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT id, content, created_by, created_at
                FROM briefings
                WHERE user_id=%s AND effective_date=%s AND status='active'
                ORDER BY created_at DESC
                """,
                (user_id, effective_date)
            )
            return c.fetchall()
    finally:
        conn.close()


def get_pending_overrides(user_id: str) -> List[dict]:
    """获取某用户的 pending overrides，按优先级排序"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT id, content, priority, created_by, created_at
                FROM overrides
                WHERE user_id=%s AND status='pending'
                ORDER BY priority DESC, created_at ASC
                """,
                (user_id,)
            )
            return c.fetchall()
    finally:
        conn.close()


def mark_override_applied(override_id: int):
    """标记 override 为已应用"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE overrides SET status='applied', applied_at=NOW()
                WHERE id=%s
                """,
                (override_id,)
            )
            conn.commit()
    finally:
        conn.close()


def save_notification(user_id: str, type_: str, title: str, content: str) -> int:
    """保存一条通知，返回通知 ID"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO notifications (user_id, type, title, content)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, type_, title, content)
            )
            nid = c.fetchone()[0]
            conn.commit()
            return nid
    finally:
        conn.close()


def get_notifications(user_id: str = None, unread_only: bool = False, limit: int = 50) -> List[dict]:
    """获取通知列表"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            sql = """
                SELECT id, user_id, type, title, content, is_read, created_at
                FROM notifications
                WHERE 1=1
            """
            params = []
            if user_id:
                sql += " AND (user_id=%s OR user_id IS NULL)"
                params.append(user_id)
            if unread_only:
                sql += " AND is_read=FALSE"
            sql += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            c.execute(sql, tuple(params))
            return c.fetchall()
    finally:
        conn.close()


def mark_notification_read(notification_id: int):
    """标记通知为已读"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE notifications SET is_read=TRUE WHERE id=%s",
                (notification_id,)
            )
            conn.commit()
    finally:
        conn.close()


def delete_episodic_memory(memory_id: int) -> bool:
    """删除单条情景记忆（记忆透明性）"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM episodic_memory WHERE id=%s", (memory_id,))
            conn.commit()
            return c.rowcount > 0
    except Exception as e:
        logger.error(f"删除记忆失败: {e}")
        return False
    finally:
        conn.close()


def save_todos(store_key: str, user_id: str, todos: List[Dict]) -> bool:
    """持久化 todo 列表到数据库"""
    import json as _json
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO todos (store_key, user_id, todos_json, updated_at)
                VALUES (%s, %s, %s::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (store_key) DO UPDATE SET
                    todos_json = EXCLUDED.todos_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (store_key, user_id, _json.dumps(todos, ensure_ascii=False))
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"保存 todos 失败: {e}")
        return False
    finally:
        conn.close()


def load_todos(store_key: str) -> List[Dict]:
    """从数据库加载 todo 列表"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT todos_json FROM todos WHERE store_key=%s",
                (store_key,)
            )
            row = c.fetchone()
            if row:
                value = row[0]
                if isinstance(value, str):
                    return json.loads(value)
                return value or []
            return []
    except Exception as e:
        logger.error(f"加载 todos 失败: {e}")
        return []
    finally:
        conn.close()


def list_pending_todos(user_id: str, limit: int = 20) -> List[Dict]:
    """获取某用户最近的待办事项（按 store_key 分组）"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT store_key, todos_json, updated_at
                FROM todos
                WHERE user_id=%s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user_id, limit)
            )
            rows = c.fetchall()
            result = []
            for r in rows:
                value = r["todos_json"]
                todos = json.loads(value) if isinstance(value, str) else (value or [])
                active = [t for t in todos if t.get("status") in ("pending", "in_progress")]
                if active:
                    result.append({
                        "store_key": r["store_key"],
                        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                        "todos": active,
                    })
            return result
    except Exception as e:
        logger.error(f"列出待办 todos 失败: {e}")
        return []
    finally:
        conn.close()


def get_sales_users() -> List[dict]:
    """获取所有销售用户（dashboard/scheduler 用）"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT user_id, name, role FROM user_profiles WHERE entity_type='sales' ORDER BY name"
            )
            return c.fetchall()
    finally:
        conn.close()


def get_all_users() -> List[dict]:
    """获取所有活跃用户（dashboard 用）"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT user_id, name, role FROM user_profiles WHERE status='active' ORDER BY name"
            )
            return c.fetchall()
    finally:
        conn.close()


def get_user_profile(user_id: str) -> dict:
    """获取完整用户画像"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT user_id, name, role, preferences,
                       interests, current_projects, communication_style,
                       life_experiences, writing_patterns,
                       created_at, updated_at
                FROM user_profiles WHERE user_id=%s
                """,
                (user_id,)
            )
            row = c.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


# ========== 商业化：组织、用量与用户管理 ==========

def list_organizations() -> List[dict]:
    """返回组织列表，附带用户数和当月已用 token"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT
                    o.org_id,
                    o.name,
                    o.monthly_token_quota,
                    o.created_at,
                    o.updated_at,
                    COUNT(u.user_id) FILTER (WHERE u.status = 'active') AS active_user_count
                FROM organizations o
                LEFT JOIN user_profiles u ON u.org_id = o.org_id
                GROUP BY o.org_id, o.name, o.monthly_token_quota, o.created_at, o.updated_at
                ORDER BY o.created_at DESC
                """
            )
            orgs = c.fetchall()
            # 当月已用 token
            now = datetime.now()
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                end = now.replace(year=now.year + 1, month=1, day=1)
            else:
                end = now.replace(month=now.month + 1, day=1)
            c.execute(
                """
                SELECT org_id, COALESCE(SUM(total_tokens), 0) AS used_tokens
                FROM llm_usage
                WHERE created_at >= %s AND created_at < %s
                GROUP BY org_id
                """,
                (start, end)
            )
            usage_map = {row["org_id"]: int(row["used_tokens"]) for row in c.fetchall()}
            for org in orgs:
                org["used_tokens"] = usage_map.get(org["org_id"], 0)
            return orgs
    finally:
        conn.close()


def get_organization(org_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                "SELECT org_id, name, monthly_token_quota, created_at, updated_at FROM organizations WHERE org_id=%s",
                (org_id,)
            )
            row = c.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_org_quota(org_id: str, quota: int) -> bool:
    """更新组织月度 token 配额"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE organizations
                SET monthly_token_quota = %s, updated_at = CURRENT_TIMESTAMP
                WHERE org_id = %s
                """,
                (quota, org_id)
            )
            conn.commit()
            return c.rowcount > 0
    finally:
        conn.close()


def get_monthly_usage(org_id: str, year: int, month: int) -> int:
    """按自然月汇总某组织的 total_tokens"""
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0) AS total
                FROM llm_usage
                WHERE org_id = %s AND created_at >= %s AND created_at < %s
                """,
                (org_id, start, end)
            )
            return int(c.fetchone()[0])
    finally:
        conn.close()


def summarize_llm_usage(
    org_id: str = None,
    user_id: str = None,
    thread_id: str = None,
    start_date: str = None,
    end_date: str = None,
) -> dict:
    """汇总 LLM 用量，支持按 org/user/thread/日期过滤"""
    conditions = []
    params = []

    def add_filter(column: str, value):
        if value:
            conditions.append(f"{column} = %s")
            params.append(value)

    add_filter("org_id", org_id)
    add_filter("user_id", user_id)
    add_filter("thread_id", thread_id)
    if start_date:
        conditions.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("created_at < %s::timestamp + INTERVAL '1 day'")
        params.append(end_date)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                f"""
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd,
                    COALESCE(SUM(cost_cny), 0) AS cost_cny,
                    COUNT(*) AS count
                FROM llm_usage
                {where_clause}
                """,
                tuple(params)
            )
            total = dict(c.fetchone())

            c.execute(
                f"""
                SELECT
                    model,
                    provider,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd,
                    COALESCE(SUM(cost_cny), 0) AS cost_cny,
                    COUNT(*) AS count
                FROM llm_usage
                {where_clause}
                GROUP BY model, provider
                ORDER BY total_tokens DESC
                """,
                tuple(params)
            )
            by_model = [dict(row) for row in c.fetchall()]

            return {
                "input_tokens": int(total["input_tokens"]),
                "output_tokens": int(total["output_tokens"]),
                "total_tokens": int(total["total_tokens"]),
                "cost_usd": float(total["cost_usd"]),
                "cost_cny": float(total["cost_cny"]),
                "count": int(total["count"]),
                "by_model": by_model,
            }
    finally:
        conn.close()


def list_llm_usage(
    org_id: str = None,
    user_id: str = None,
    thread_id: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """分页返回 LLM 用量明细"""
    conditions = []
    params = []

    def add_filter(column: str, value):
        if value:
            conditions.append(f"{column} = %s")
            params.append(value)

    add_filter("org_id", org_id)
    add_filter("user_id", user_id)
    add_filter("thread_id", thread_id)
    if start_date:
        conditions.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("created_at < %s::timestamp + INTERVAL '1 day'")
        params.append(end_date)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                f"""
                SELECT COUNT(*) AS total FROM llm_usage {where_clause}
                """,
                tuple(params)
            )
            total = int(c.fetchone()["total"])

            c.execute(
                f"""
                SELECT id, org_id, user_id, thread_id, model, provider,
                       input_tokens, output_tokens, total_tokens,
                       cost_usd, cost_cny, created_at
                FROM llm_usage
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [limit, offset])
            )
            rows = [dict(row) for row in c.fetchall()]
            return {"total": total, "rows": rows}
    finally:
        conn.close()


def create_user(
    user_id: str,
    name: str,
    role: str = "成员",
    entity_type: str = "user",
    org_id: str = "org_default",
    team_id: str = None,
    wechat_user_id: str = None,
    is_admin: bool = False,
    llm_config: Optional[dict] = None,
) -> bool:
    """创建用户，user_id 已存在则返回 False"""
    encrypted_config = _encrypt_llm_config(llm_config)
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO user_profiles
                (user_id, name, role, entity_type, org_id, team_id, wechat_user_id, status, is_admin, llm_config)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id, name, role, entity_type, org_id, team_id, wechat_user_id, is_admin, json.dumps(encrypted_config) if encrypted_config else '{}')
            )
            conn.commit()
            return c.rowcount > 0
    finally:
        conn.close()


def update_user(
    user_id: str,
    name: str = None,
    role: str = None,
    entity_type: str = None,
    is_admin: bool = None,
    org_id: str = None,
    team_id: str = None,
    wechat_user_id: str = None,
    status: str = None,
    llm_config: Optional[dict] = None,
) -> bool:
    """更新用户信息，只更新非 None 字段"""
    fields = []
    values = []
    if name is not None:
        fields.append("name = %s")
        values.append(name)
    if role is not None:
        fields.append("role = %s")
        values.append(role)
    if entity_type is not None:
        fields.append("entity_type = %s")
        values.append(entity_type)
    if is_admin is not None:
        fields.append("is_admin = %s")
        values.append(is_admin)
    if org_id is not None:
        fields.append("org_id = %s")
        values.append(org_id)
    if team_id is not None:
        fields.append("team_id = %s")
        values.append(team_id)
    if wechat_user_id is not None:
        fields.append("wechat_user_id = %s")
        values.append(wechat_user_id)
    if status is not None:
        fields.append("status = %s")
        values.append(status)
    if llm_config is not None:
        fields.append("llm_config = %s")
        encrypted_config = _encrypt_llm_config(llm_config)
        values.append(json.dumps(encrypted_config) if encrypted_config else '{}')
    if not fields:
        return False

    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(user_id)

    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                f"UPDATE user_profiles SET {', '.join(fields)} WHERE user_id = %s",
                tuple(values)
            )
            conn.commit()
            return c.rowcount > 0
    finally:
        conn.close()


def get_user_full(user_id: str) -> Optional[dict]:
    """获取用户完整信息，包含组织名"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT u.user_id, u.name, u.role, u.entity_type, u.is_admin, u.org_id,
                       u.team_id, u.wechat_user_id, u.status, u.created_at, u.updated_at,
                       u.llm_config,
                       o.name AS org_name
                FROM user_profiles u
                LEFT JOIN organizations o ON o.org_id = u.org_id
                WHERE u.user_id = %s
                """,
                (user_id,)
            )
            row = c.fetchone()
            if not row:
                return None
            user = dict(row)
            user["llm_config"] = _decrypt_llm_config(user.get("llm_config") or {})
            return user
    finally:
        conn.close()


def list_users_full(active_only: bool = False) -> List[dict]:
    """返回用户管理列表"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            where = "WHERE u.status = 'active'" if active_only else ""
            c.execute(
                f"""
                SELECT u.user_id, u.name, u.role, u.entity_type, u.is_admin, u.org_id,
                       u.team_id, u.wechat_user_id, u.status, u.created_at, u.updated_at,
                       u.llm_config,
                       o.name AS org_name
                FROM user_profiles u
                LEFT JOIN organizations o ON o.org_id = u.org_id
                {where}
                ORDER BY u.created_at DESC
                """
            )
            rows = c.fetchall()
            for user in rows:
                user["llm_config"] = _decrypt_llm_config(user.get("llm_config") or {})
            return rows
    finally:
        conn.close()


def deactivate_user(user_id: str) -> bool:
    return update_user(user_id, status="disabled")


def reactivate_user(user_id: str) -> bool:
    return update_user(user_id, status="active")


def ensure_user_exists(
    user_id: str,
    name: str = None,
    org_id: str = "org_default",
) -> bool:
    """确保用户存在，不存在则创建占位用户"""
    if get_user_full(user_id):
        return False
    return create_user(
        user_id=user_id,
        name=name or user_id,
        role="成员",
        entity_type="user",
        org_id=org_id,
    )


def get_episodes(user_id: str, limit: int = 20) -> List[dict]:
    """获取某用户的最近情景记忆"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT id, role, content, tags, created_at
                FROM episodic_memory
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit)
            )
            return c.fetchall()
    finally:
        conn.close()


def get_summaries(user_id: str, limit: int = 10) -> List[dict]:
    """获取某用户的最近记忆摘要"""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                """
                SELECT id, summary_type, summary_text, tags, created_at
                FROM memory_summaries
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit)
            )
            return c.fetchall()
    finally:
        conn.close()


def save_briefing(user_id: str, content: str, created_by: str, effective_date=None) -> int:
    """保存一条 briefing"""
    from datetime import date
    if effective_date is None:
        effective_date = date.today()
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO briefings (user_id, content, created_by, effective_date)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, content, created_by, effective_date)
            )
            bid = c.fetchone()[0]
            conn.commit()
            return bid
    finally:
        conn.close()


def save_override(user_id: str, content: str, priority: int, created_by: str) -> int:
    """保存一条 override 指令"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO overrides (user_id, content, priority, created_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, content, priority, created_by)
            )
            oid = c.fetchone()[0]
            conn.commit()
            return oid
    finally:
        conn.close()


def dismiss_override(override_id: int):
    """取消一条 pending override"""
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE overrides SET status='dismissed' WHERE id=%s",
                (override_id,)
            )
            conn.commit()
    finally:
        conn.close()


def get_today_overrides(user_id: str = None) -> List[dict]:
    """获取今日的所有 overrides（dashboard 用）"""
    from datetime import date
    today = date.today()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            if user_id:
                c.execute(
                    """
                    SELECT id, user_id, content, priority, status, created_by, created_at, applied_at
                    FROM overrides
                    WHERE user_id=%s AND created_at::date=%s
                    ORDER BY created_at DESC
                    """,
                    (user_id, today)
                )
            else:
                c.execute(
                    """
                    SELECT id, user_id, content, priority, status, created_by, created_at, applied_at
                    FROM overrides
                    WHERE created_at::date=%s
                    ORDER BY created_at DESC
                    """,
                    (today,)
                )
            return c.fetchall()
    finally:
        conn.close()


def get_today_briefings(user_id: str = None) -> List[dict]:
    """获取今日的所有 briefings（dashboard 用）"""
    from datetime import date
    today = date.today()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            if user_id:
                c.execute(
                    """
                    SELECT id, user_id, content, status, created_by, created_at
                    FROM briefings
                    WHERE user_id=%s AND effective_date=%s
                    ORDER BY created_at DESC
                    """,
                    (user_id, today)
                )
            else:
                c.execute(
                    """
                    SELECT id, user_id, content, status, created_by, created_at
                    FROM briefings
                    WHERE effective_date=%s
                    ORDER BY created_at DESC
                    """,
                    (today,)
                )
            return c.fetchall()
    finally:
        conn.close()
