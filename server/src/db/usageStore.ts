import { query, queryOne } from "./index.ts";

export interface LlmUsageRecord {
  id?: number;
  orgId: string;
  userId: string;
  threadId: string;
  model: string;
  provider: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  costUsd: number;
  costCny: number;
  createdAt?: Date;
}

export interface UsageFilters {
  orgId?: string;
  userId?: string;
  threadId?: string;
  startDate?: string; // ISO date, e.g. 2026-07-01
  endDate?: string; // inclusive
  limit?: number;
  offset?: number;
}

export interface UsageSummary {
  totalInputTokens: number;
  totalOutputTokens: number;
  totalTokens: number;
  totalCostUsd: number;
  totalCostCny: number;
  count: number;
  byModel: Array<{
    model: string;
    provider: string;
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    costUsd: number;
    costCny: number;
    count: number;
  }>;
}

export interface Organization {
  org_id: string;
  name: string;
  monthly_token_quota: number;
  created_at: Date;
  updated_at: Date;
}

function monthBounds(year: number, month: number): { start: Date; end: Date } {
  const start = new Date(Date.UTC(year, month - 1, 1, 0, 0, 0, 0));
  const end = new Date(Date.UTC(year, month, 1, 0, 0, 0, 0));
  return { start, end };
}

export class UsageStore {
  async getOrgForUser(userId: string): Promise<string> {
    const row = await queryOne<{ org_id: string }>(
      `SELECT org_id FROM user_profiles WHERE user_id = $1`,
      [userId],
    );
    return row?.org_id ?? "org_default";
  }

  async getOrganization(orgId: string): Promise<Organization | undefined> {
    return await queryOne<Organization>(
      `SELECT org_id, name, monthly_token_quota, created_at, updated_at
       FROM organizations WHERE org_id = $1`,
      [orgId],
    );
  }

  async recordUsage(record: LlmUsageRecord): Promise<void> {
    await query(
      `INSERT INTO llm_usage
       (org_id, user_id, thread_id, model, provider, input_tokens, output_tokens, total_tokens, cost_usd, cost_cny)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
      [
        record.orgId,
        record.userId,
        record.threadId,
        record.model,
        record.provider,
        record.inputTokens,
        record.outputTokens,
        record.totalTokens,
        record.costUsd,
        record.costCny,
      ],
    );
  }

  async getMonthlyUsage(orgId: string, year: number, month: number): Promise<number> {
    const { start, end } = monthBounds(year, month);
    const row = await queryOne<{ total: number }>(
      `SELECT COALESCE(SUM(total_tokens), 0)::bigint AS total
       FROM llm_usage
       WHERE org_id = $1 AND created_at >= $2 AND created_at < $3`,
      [orgId, start.toISOString(), end.toISOString()],
    );
    return Number(row?.total ?? 0);
  }

  async listUsage(
    filters: UsageFilters,
  ): Promise<{ rows: LlmUsageRecord[]; total: number }> {
    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIndex = 0;

    const addFilter = (column: string, value: unknown) => {
      if (value === undefined || value === null || value === "") return;
      paramIndex++;
      conditions.push(`${column} = $${paramIndex}`);
      params.push(value);
    };

    addFilter("org_id", filters.orgId);
    addFilter("user_id", filters.userId);
    addFilter("thread_id", filters.threadId);

    if (filters.startDate) {
      paramIndex++;
      conditions.push(`created_at >= $${paramIndex}`);
      params.push(filters.startDate);
    }
    if (filters.endDate) {
      paramIndex++;
      conditions.push(`created_at < $${paramIndex}::timestamp + INTERVAL '1 day'`);
      params.push(filters.endDate);
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

    const countRow = await queryOne<{ total: number }>(
      `SELECT COUNT(*)::int AS total FROM llm_usage ${whereClause}`,
      params,
    );
    const total = Number(countRow?.total ?? 0);

    const limit = Math.max(1, Math.min(filters.limit ?? 20, 1000));
    const offset = Math.max(0, filters.offset ?? 0);

    paramIndex++;
    const limitParam = paramIndex;
    paramIndex++;
    const offsetParam = paramIndex;

    const rows = await query<LlmUsageRecordDbRow>(
      `SELECT id, org_id, user_id, thread_id, model, provider,
              input_tokens, output_tokens, total_tokens, cost_usd, cost_cny, created_at
       FROM llm_usage
       ${whereClause}
       ORDER BY created_at DESC
       LIMIT $${limitParam} OFFSET $${offsetParam}`,
      [...params, limit, offset],
    );

    return { total, rows: rows.map(dbRowToRecord) };
  }

  async summarizeUsage(filters: UsageFilters): Promise<UsageSummary> {
    const conditions: string[] = [];
    const params: unknown[] = [];
    let paramIndex = 0;

    const addFilter = (column: string, value: unknown) => {
      if (value === undefined || value === null || value === "") return;
      paramIndex++;
      conditions.push(`${column} = $${paramIndex}`);
      params.push(value);
    };

    addFilter("org_id", filters.orgId);
    addFilter("user_id", filters.userId);
    addFilter("thread_id", filters.threadId);

    if (filters.startDate) {
      paramIndex++;
      conditions.push(`created_at >= $${paramIndex}`);
      params.push(filters.startDate);
    }
    if (filters.endDate) {
      paramIndex++;
      conditions.push(`created_at < $${paramIndex}::timestamp + INTERVAL '1 day'`);
      params.push(filters.endDate);
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

    const totalRow = await queryOne<{
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      cost_usd: number;
      cost_cny: number;
      count: number;
    }>(
      `SELECT COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
              COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
              COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
              COALESCE(SUM(cost_usd), 0)::numeric AS cost_usd,
              COALESCE(SUM(cost_cny), 0)::numeric AS cost_cny,
              COUNT(*)::int AS count
       FROM llm_usage ${whereClause}`,
      params,
    );

    const byModel = await query<{
      model: string;
      provider: string;
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      cost_usd: number;
      cost_cny: number;
      count: number;
    }>(
      `SELECT model, provider,
              COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
              COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
              COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
              COALESCE(SUM(cost_usd), 0)::numeric AS cost_usd,
              COALESCE(SUM(cost_cny), 0)::numeric AS cost_cny,
              COUNT(*)::int AS count
       FROM llm_usage
       ${whereClause}
       GROUP BY model, provider
       ORDER BY total_tokens DESC`,
      params,
    );

    return {
      totalInputTokens: Number(totalRow?.input_tokens ?? 0),
      totalOutputTokens: Number(totalRow?.output_tokens ?? 0),
      totalTokens: Number(totalRow?.total_tokens ?? 0),
      totalCostUsd: Number(totalRow?.cost_usd ?? 0),
      totalCostCny: Number(totalRow?.cost_cny ?? 0),
      count: Number(totalRow?.count ?? 0),
      byModel: byModel.map((r) => ({
        model: r.model,
        provider: r.provider,
        inputTokens: Number(r.input_tokens),
        outputTokens: Number(r.output_tokens),
        totalTokens: Number(r.total_tokens),
        costUsd: Number(r.cost_usd),
        costCny: Number(r.cost_cny),
        count: Number(r.count),
      })),
    };
  }

  async listOrganizations(): Promise<Organization[]> {
    return await query<Organization>(
      `SELECT org_id, name, monthly_token_quota, created_at, updated_at
       FROM organizations
       ORDER BY created_at DESC`,
    );
  }

  async updateQuota(orgId: string, quota: number): Promise<boolean> {
    const result = await query(
      `UPDATE organizations
       SET monthly_token_quota = $1
       WHERE org_id = $2
       RETURNING org_id`,
      [quota, orgId],
    );
    return (result?.length ?? 0) > 0;
  }
}

interface LlmUsageRecordDbRow {
  id: number;
  org_id: string;
  user_id: string;
  thread_id: string;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_cny: number;
  created_at: Date;
}

function dbRowToRecord(row: LlmUsageRecordDbRow): LlmUsageRecord {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    threadId: row.thread_id,
    model: row.model,
    provider: row.provider,
    inputTokens: Number(row.input_tokens),
    outputTokens: Number(row.output_tokens),
    totalTokens: Number(row.total_tokens),
    costUsd: Number(row.cost_usd),
    costCny: Number(row.cost_cny),
    createdAt: row.created_at,
  };
}
