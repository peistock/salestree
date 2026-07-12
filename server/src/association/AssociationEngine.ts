import { query as dbQuery } from "../db/index.ts";

const ASSOCIATION_RULES: Record<string, string[]> = {
  客户: ["account.basic", "account.signals", "deal.stage"],
  公司: ["account.basic", "account.signals", "deal.stage"],
  跟进: ["contact.last_touch", "activity.recent", "deal.next_step"],
  联系: ["contact.last_touch", "activity.recent", "deal.next_step"],
  触达: ["contact.last_touch", "activity.recent", "deal.next_step"],
  报价: ["deal.expected_value", "competitor.pricing"],
  价格: ["deal.expected_value", "competitor.pricing"],
  竞品: ["competitor.pricing", "competitor.reviews", "account.signals"],
  竞争: ["competitor.pricing", "competitor.reviews", "account.signals"],
  融资: ["account.signals"],
  人事: ["account.signals"],
  高管: ["account.signals"],
  方案: ["deal.stage", "activity.recent"],
  提案: ["deal.stage", "activity.recent"],
  合同: ["deal.stage", "activity.recent"],
  签约: ["deal.stage", "activity.recent"],
  商机: ["deal.stage", "deal.expected_value"],
  行业: ["account.signals"],
  资讯: ["account.signals"],
  动态: ["account.signals"],
  新闻: ["account.signals"],
  营销: ["account.signals", "deal.stage"],
  品牌: ["account.signals", "deal.stage"],
};

const MEMORY_QUERIES: Record<string, string> = {
  "account.basic": `
    SELECT name, industry, stage, region, notes
    FROM accounts WHERE owner_id=$1
    ORDER BY updated_at DESC LIMIT 3`,
  "account.signals": `
    SELECT a.name AS account_name, ac.activity_type, ac.content, ac.created_at
    FROM activities ac
    JOIN accounts a ON ac.entity_id = a.account_id
    WHERE ac.entity_type = 'account' AND a.owner_id=$1
    ORDER BY ac.created_at DESC LIMIT 5`,
  "deal.stage": `
    SELECT a.name AS account_name, d.name AS deal_name, d.stage,
           d.expected_value, d.next_step, d.updated_at
    FROM deals d
    JOIN accounts a ON d.account_id = a.account_id
    WHERE d.owner_id=$1
    ORDER BY d.updated_at DESC LIMIT 5`,
  "deal.next_step": `
    SELECT a.name AS account_name, d.name AS deal_name, d.stage,
           d.expected_value, d.next_step, d.updated_at
    FROM deals d
    JOIN accounts a ON d.account_id = a.account_id
    WHERE d.owner_id=$1 AND d.next_step IS NOT NULL AND d.next_step <> ''
    ORDER BY d.updated_at DESC LIMIT 5`,
  "deal.expected_value": `
    SELECT a.name AS account_name, d.name AS deal_name, d.expected_value, d.stage
    FROM deals d
    JOIN accounts a ON d.account_id = a.account_id
    WHERE d.owner_id=$1
    ORDER BY d.updated_at DESC LIMIT 5`,
  "contact.last_touch": `
    SELECT a.name AS account_name, c.name AS contact_name, c.title, c.department, c.updated_at
    FROM contacts c
    JOIN accounts a ON c.account_id = a.account_id
    WHERE a.owner_id=$1
    ORDER BY c.updated_at DESC LIMIT 5`,
  "activity.recent": `
    SELECT activity_type, direction, content, created_at
    FROM activities WHERE owner_id=$1
    ORDER BY created_at DESC LIMIT 5`,
  "competitor.pricing": `
    SELECT content, created_at FROM episodic_memory
    WHERE user_id=$1 AND role='user'
      AND (content ILIKE '%价格%' OR content ILIKE '%报价%' OR content ILIKE '%费用%')
    ORDER BY created_at DESC LIMIT 3`,
  "competitor.reviews": `
    SELECT content, created_at FROM episodic_memory
    WHERE user_id=$1 AND role='user'
      AND (content ILIKE '%竞品%' OR content ILIKE '%竞争%' OR content ILIKE '%对手%')
    ORDER BY created_at DESC LIMIT 3`,
};

const LABEL_MAP: Record<string, string> = {
  "account.basic": "客户基础",
  "account.signals": "客户动态",
  "deal.stage": "商机阶段",
  "deal.next_step": "下一步",
  "deal.expected_value": "商机金额",
  "contact.last_touch": "联系人",
  "activity.recent": "最近活动",
  "competitor.pricing": "竞品价格",
  "competitor.reviews": "竞品口碑",
};

export class AssociationEngine {
  detectSignals(query: string): string[] {
    return Object.keys(ASSOCIATION_RULES).filter((keyword) => query.includes(keyword));
  }

  async buildContext(userId: string, query: string): Promise<string> {
    const signals = this.detectSignals(query);
    if (signals.length === 0) return "";

    const allTypes = new Set<string>();
    for (const sig of signals) {
      for (const t of ASSOCIATION_RULES[sig] ?? []) allTypes.add(t);
    }

    const lines = ["【关联记忆】"];
    let hasAny = false;

    for (const mt of allTypes) {
      const sql = MEMORY_QUERIES[mt];
      if (!sql) continue;
      const rows = (await dbQuery(sql, [userId])) as Record<string, unknown>[];
      const texts: string[] = [];
      for (const r of rows) {
        const text = Object.values(r)
          .filter((v) => v !== null && v !== undefined)
          .map((v) => `${v}`)
          .join(" ")
          .trim();
        if (text.length > 0) texts.push(text.slice(0, 200));
      }

      if (texts.length > 0) {
        hasAny = true;
        const label = LABEL_MAP[mt] ?? mt;
        for (const t of texts) lines.push(`  • [${label}] ${t}`);
      }
    }

    return hasAny ? lines.join("\n") : "";
  }
}
