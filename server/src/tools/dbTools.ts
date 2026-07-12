import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import { query } from "../db/index.ts";

type Row = Record<string, any>;

function uuid(): string {
  return crypto.randomUUID();
}

const getAccountSchema = Type.Object({
  account_id: Type.Optional(Type.String({ description: "客户公司 account_id" })),
  name: Type.Optional(Type.String({ description: "客户公司名（支持模糊匹配）" })),
});

const listAccountsSchema = Type.Object({
  limit: Type.Optional(Type.Number({ description: "最多返回几条", default: 10 })),
});

const getContactsSchema = Type.Object({
  account_id: Type.String({ description: "客户公司 account_id" }),
  name: Type.Optional(Type.String({ description: "联系人姓名（支持模糊匹配）" })),
});

const getDealsSchema = Type.Object({
  account_id: Type.String({ description: "客户公司 account_id" }),
  name: Type.Optional(Type.String({ description: "商机名（支持模糊匹配）" })),
});

const getActivitiesSchema = Type.Object({
  entity_type: Type.String({ description: "关联实体类型：account / contact / deal" }),
  entity_id: Type.String({ description: "关联实体 ID" }),
  limit: Type.Optional(Type.Number({ description: "最多返回几条", default: 5 })),
});

const logActivitySchema = Type.Object({
  entity_type: Type.String({ description: "关联实体类型：account / contact / deal" }),
  entity_id: Type.String({ description: "关联实体 ID" }),
  activity_type: Type.String({ description: "活动类型：meeting / call / email / wechat / note" }),
  direction: Type.Optional(Type.String({ description: "方向：outbound / inbound", default: "outbound" })),
  content: Type.String({ description: "活动内容" }),
  sentiment: Type.Optional(Type.String({ description: "情绪：positive / neutral / negative", default: "neutral" })),
});

const saveAccountResearchSchema = Type.Object({
  account_id: Type.String({ description: "客户公司 account_id" }),
  summary: Type.String({ description: "研究摘要（500 字以内）" }),
});

export function createDbTools(userId: string): AgentTool[] {
  const getAccountTool: AgentTool<typeof getAccountSchema> = {
    name: "get_account",
    label: "Get Account",
    description: "根据 account_id 或公司名查询客户公司详情、联系人、商机和最近活动",
    parameters: getAccountSchema,
    async execute(_toolCallId, params: Static<typeof getAccountSchema>) {
      let account: Row | undefined;
      if (params.account_id) {
        account = (await query<Row>(`SELECT * FROM accounts WHERE account_id=$1 LIMIT 1`, [params.account_id]))[0];
      }
      if (!account && params.name) {
        account = (await query<Row>(`SELECT * FROM accounts WHERE owner_id=$1 AND name ILIKE $2 LIMIT 1`, [userId, `%${params.name}%`]))[0];
      }
      if (!account) {
        return { content: [{ type: "text" as const, text: "未找到匹配的客户公司。" }], details: {} };
      }
      const accountId = account.account_id as string;
      const [contacts, deals, activities] = await Promise.all([
        query<Row>(`SELECT * FROM contacts WHERE account_id=$1 ORDER BY updated_at DESC`, [accountId]),
        query<Row>(`SELECT * FROM deals WHERE account_id=$1 ORDER BY updated_at DESC`, [accountId]),
        query<Row>(`SELECT * FROM activities WHERE entity_id=$1 ORDER BY created_at DESC LIMIT 5`, [accountId]),
      ]);
      const text = [
        `【客户公司】${account.name ?? ""}`,
        `行业：${account.industry ?? ""} | 阶段：${account.stage ?? ""} | 地区：${account.region ?? ""}`,
        `备注：${account.notes ?? ""}`,
        `研究摘要：${account.research_summary ?? "无"}`,
        "",
        "【联系人】",
        ...contacts.map((c) => `  - ${c.name ?? ""}（${c.title ?? ""}，${c.department ?? ""}）角色：${c.role_in_deal ?? ""}`),
        "",
        "【商机】",
        ...deals.map((d) => `  - ${d.name ?? ""} | 阶段：${d.stage ?? ""} | 预计金额：${d.expected_value ?? ""} | 下一步：${d.next_step ?? ""}`),
        "",
        "【最近活动】",
        ...activities.map((a) => `  - [${a.activity_type ?? ""}] ${a.content ?? ""}`),
      ].join("\n");
      return { content: [{ type: "text" as const, text }], details: {} };
    },
  };

  const listAccountsTool: AgentTool<typeof listAccountsSchema> = {
    name: "list_accounts",
    label: "List Accounts",
    description: "列出当前销售负责的客户公司",
    parameters: listAccountsSchema,
    async execute(_toolCallId, params: Static<typeof listAccountsSchema>) {
      const rows = await query<Row>(`SELECT account_id, name, industry, stage FROM accounts WHERE owner_id=$1 ORDER BY updated_at DESC LIMIT $2`, [userId, params.limit ?? 10]);
      const text = rows.length === 0 ? "暂无客户公司记录。" : ["【客户公司列表】", ...rows.map((r) => `  - ${r.name ?? ""}（${r.industry ?? ""}，${r.stage ?? ""}）`)].join("\n");
      return { content: [{ type: "text" as const, text }], details: {} };
    },
  };

  const getContactsTool: AgentTool<typeof getContactsSchema> = {
    name: "get_contacts",
    label: "Get Contacts",
    description: "查询某个客户公司下的联系人",
    parameters: getContactsSchema,
    async execute(_toolCallId, params: Static<typeof getContactsSchema>) {
      let sql = `SELECT * FROM contacts WHERE account_id=$1`;
      const args: unknown[] = [params.account_id];
      if (params.name) {
        sql += ` AND name ILIKE $2`;
        args.push(`%${params.name}%`);
      }
      sql += ` ORDER BY updated_at DESC`;
      const rows = await query<Row>(sql, args);
      const text = rows.length === 0
        ? "未找到联系人。"
        : ["【联系人】", ...rows.map((c) => `  - ${c.name ?? ""}（${c.title ?? ""}，${c.department ?? ""}）角色：${c.role_in_deal ?? ""}`)].join("\n");
      return { content: [{ type: "text" as const, text }], details: {} };
    },
  };

  const getDealsTool: AgentTool<typeof getDealsSchema> = {
    name: "get_deals",
    label: "Get Deals",
    description: "查询某个客户公司下的商机",
    parameters: getDealsSchema,
    async execute(_toolCallId, params: Static<typeof getDealsSchema>) {
      let sql = `SELECT * FROM deals WHERE account_id=$1`;
      const args: unknown[] = [params.account_id];
      if (params.name) {
        sql += ` AND name ILIKE $2`;
        args.push(`%${params.name}%`);
      }
      sql += ` ORDER BY updated_at DESC`;
      const rows = await query<Row>(sql, args);
      const text = rows.length === 0
        ? "未找到商机。"
        : ["【商机】", ...rows.map((d) => `  - ${d.name ?? ""} | 阶段：${d.stage ?? ""} | 预计金额：${d.expected_value ?? ""} | 下一步：${d.next_step ?? ""}`)].join("\n");
      return { content: [{ type: "text" as const, text }], details: {} };
    },
  };

  const getActivitiesTool: AgentTool<typeof getActivitiesSchema> = {
    name: "get_activities",
    label: "Get Activities",
    description: "查询某个实体（account/contact/deal）最近的活动记录",
    parameters: getActivitiesSchema,
    async execute(_toolCallId, params: Static<typeof getActivitiesSchema>) {
      const rows = await query<Row>(
        `SELECT * FROM activities WHERE entity_type=$1 AND entity_id=$2 ORDER BY created_at DESC LIMIT $3`,
        [params.entity_type, params.entity_id, params.limit ?? 5],
      );
      const text = rows.length === 0
        ? "暂无活动记录。"
        : ["【活动记录】", ...rows.map((a) => `  - [${a.activity_type ?? ""}] ${a.content ?? ""}`)].join("\n");
      return { content: [{ type: "text" as const, text }], details: {} };
    },
  };

  const logActivityTool: AgentTool<typeof logActivitySchema> = {
    name: "log_activity",
    label: "Log Activity",
    description: "将一次跟进活动记录到 CRM",
    parameters: logActivitySchema,
    async execute(_toolCallId, params: Static<typeof logActivitySchema>) {
      await query(
        `INSERT INTO activities (activity_id, entity_type, entity_id, owner_id, activity_type, direction, content, sentiment) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
        [uuid(), params.entity_type, params.entity_id, userId, params.activity_type, params.direction ?? "outbound", params.content, params.sentiment ?? "neutral"],
      );
      return { content: [{ type: "text" as const, text: "活动已记录。" }], details: {} };
    },
  };

  const saveAccountResearchTool: AgentTool<typeof saveAccountResearchSchema> = {
    name: "save_account_research",
    label: "Save Account Research",
    description: "将客户研究摘要保存到 CRM 的 research_summary 字段",
    parameters: saveAccountResearchSchema,
    async execute(_toolCallId, params: Static<typeof saveAccountResearchSchema>) {
      await query(`UPDATE accounts SET research_summary=$1, updated_at=NOW() WHERE account_id=$2`, [params.summary, params.account_id]);
      return { content: [{ type: "text" as const, text: "研究摘要已保存。" }], details: {} };
    },
  };

  return [
    getAccountTool,
    listAccountsTool,
    getContactsTool,
    getDealsTool,
    getActivitiesTool,
    logActivityTool,
    saveAccountResearchTool,
  ];
}
