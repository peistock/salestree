import { query, queryOne } from "../db/index.ts";
import type {
  Account,
  Activity,
  Briefing,
  Contact,
  Deal,
  EpisodicMessage,
  Override,
  UserProfile,
} from "./types.ts";

export interface MemoryContext {
  core: string;
  profile: string;
  episodes: string;
  business: string;
  briefing: string;
  override: string;
}

export class Memory {
  constructor(private userId: string, private userName: string = "") {}

  async load(): Promise<MemoryContext> {
    const [profile, episodes, business, briefings, overrides] = await Promise.all([
      this.loadProfile(),
      this.loadRecentMessages(10),
      this.loadBusinessContext(),
      this.loadBriefings(),
      this.loadOverrides(),
    ]);

    return {
      core: this.buildCore(),
      profile,
      episodes,
      business,
      briefing: briefings,
      override: overrides,
    };
  }

  private buildCore(): string {
    const now = new Date().toLocaleString("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      weekday: "long",
    });
    return `你是销销，面向互联网广告/营销公司销售团队的 AI 助手。当前时间：${now}。`;
  }

  private async loadProfile(): Promise<string> {
    const row = await queryOne<UserProfile>(
      `SELECT user_id, name, role, preferences, interests, current_projects,
              communication_style, life_experiences, writing_patterns
       FROM user_profiles WHERE user_id = $1`,
      [this.userId]
    );
    if (!row) return `用户：${this.userName || this.userId}（新成员）`;
    const parts = [`【画像】${row.name}，${row.role}。`];
    if (row.preferences) parts.push(`偏好：${row.preferences}`);
    if (row.communication_style && Object.keys(row.communication_style).length > 0) {
      parts.push(`沟通风格：${JSON.stringify(row.communication_style)}`);
    }
    return parts.join("\n");
  }

  private async loadRecentMessages(limit: number): Promise<string> {
    const rows = await query<EpisodicMessage>(
      `SELECT role, content, created_at
       FROM episodic_memory
       WHERE user_id = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [this.userId, limit]
    );
    if (rows.length === 0) return "";
    const lines = rows
      .slice()
      .reverse()
      .map((m) => `${m.role === "user" ? "用户" : "销销"}：${m.content}`);
    return `【最近对话】\n${lines.join("\n")}`;
  }

  private async loadBusinessContext(): Promise<string> {
    const [accounts, contacts, deals, activities] = await Promise.all([
      query<Account>(
        `SELECT account_id, name, industry, website, stage, region, notes, research_summary
         FROM accounts WHERE owner_id = $1 OR $1 = ANY(
           SELECT owner_id FROM deals WHERE account_id = accounts.account_id
         )
         ORDER BY updated_at DESC LIMIT 20`,
        [this.userId]
      ),
      query<Contact>(
        `SELECT contact_id, account_id, name, title, department, role_in_deal, email, phone, notes
         FROM contacts
         WHERE account_id IN (SELECT account_id FROM accounts WHERE owner_id = $1)
         ORDER BY updated_at DESC LIMIT 30`,
        [this.userId]
      ),
      query<Deal>(
        `SELECT deal_id, account_id, name, stage, service_line, expected_value, close_date, probability, next_step
         FROM deals WHERE owner_id = $1 ORDER BY updated_at DESC LIMIT 20`,
        [this.userId]
      ),
      query<Activity>(
        `SELECT activity_id, entity_type, entity_id, activity_type, direction, content, sentiment, created_at
         FROM activities
         WHERE owner_id = $1 OR entity_id IN (
           SELECT account_id FROM accounts WHERE owner_id = $1
         )
         ORDER BY created_at DESC LIMIT 20`,
        [this.userId]
      ),
    ]);

    const lines: string[] = [];
    if (accounts.length > 0) {
      lines.push("【客户公司】");
      for (const a of accounts) {
        lines.push(`- ${a.name}（${a.industry ?? "未分类"}，阶段：${a.stage ?? "未知"}）${a.notes ? "备注：" + a.notes : ""}`);
      }
    }
    if (contacts.length > 0) {
      lines.push("【联系人】");
      for (const c of contacts) {
        lines.push(`- ${c.name}，${c.title ?? ""}，${c.department ?? ""}（${c.role_in_deal ?? ""}）`);
      }
    }
    if (deals.length > 0) {
      lines.push("【商机】");
      for (const d of deals) {
        lines.push(`- ${d.name}，阶段：${d.stage ?? "未知"}，预计金额：${d.expected_value ?? "未填"}，下一步：${d.next_step ?? "未填"}`);
      }
    }
    if (activities.length > 0) {
      lines.push("【最近活动】");
      for (const a of activities.slice(0, 10)) {
        lines.push(`- ${a.created_at} ${a.activity_type} ${a.direction ?? ""}：${a.content.slice(0, 120)}`);
      }
    }
    return lines.join("\n");
  }

  private async loadBriefings(): Promise<string> {
    const rows = await query<Briefing>(
      `SELECT id, content, effective_date FROM briefings
       WHERE user_id = $1 AND status = 'active' AND effective_date = CURRENT_DATE
       ORDER BY created_at DESC`,
      [this.userId]
    );
    if (rows.length === 0) return "";
    return `【今日托付】\n${rows.map((b) => `- ${b.content}`).join("\n")}`;
  }

  private async loadOverrides(): Promise<string> {
    const rows = await query<Override>(
      `SELECT id, content, priority FROM overrides
       WHERE user_id = $1 AND status = 'pending'
       ORDER BY priority DESC, created_at DESC
       LIMIT 1`,
      [this.userId]
    );
    if (rows.length === 0) return "";
    const o = rows[0];
    const label = { 3: "【立即】", 2: "【紧急】", 1: "【注意】" }[o.priority] ?? "【注意】";
    return `【紧急指令】${label}\n${o.content}`;
  }
}
