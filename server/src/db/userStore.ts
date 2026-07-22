import { query, queryOne } from "./index.ts";
import { decryptApiKey, encryptApiKey } from "../utils/encryption.ts";

export interface UserLlmConfig {
  enabled?: boolean;
  provider?: string;
  baseUrl?: string;
  apiKey?: string;
  modelDaily?: string;
  modelComplex?: string;
  modelSummary?: string;
}

export interface UserRecord {
  userId: string;
  name: string;
  role: string;
  entityType: string;
  isAdmin: boolean;
  orgId: string;
  teamId?: string;
  wechatUserId?: string;
  status: string;
  llmConfig?: UserLlmConfig;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface UserFilters {
  activeOnly?: boolean;
  orgId?: string;
}

export class UserStore {
  async listUsers(filters: UserFilters = {}): Promise<UserRecord[]> {
    const conditions: string[] = [];
    const params: unknown[] = [];

    if (filters.activeOnly) {
      conditions.push("u.status = 'active'");
    }
    if (filters.orgId) {
      params.push(filters.orgId);
      conditions.push(`u.org_id = $${params.length}`);
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

    return await query<UserRecordDbRow>(
      `SELECT u.user_id, u.name, u.role, u.entity_type, u.is_admin, u.org_id,
              u.team_id, u.wechat_user_id, u.status, u.created_at, u.updated_at,
              u.llm_config
       FROM user_profiles u
       ${whereClause}
       ORDER BY u.created_at DESC`,
      params,
    ).then((rows) => rows.map(dbRowToRecord));
  }

  async getUser(userId: string): Promise<UserRecord | undefined> {
    const row = await queryOne<UserRecordDbRow>(
      `SELECT user_id, name, role, entity_type, is_admin, org_id,
              team_id, wechat_user_id, status, created_at, updated_at, llm_config
       FROM user_profiles WHERE user_id = $1`,
      [userId],
    );
    return row ? dbRowToRecord(row) : undefined;
  }

  async getUserLlmConfig(userId: string): Promise<UserLlmConfig | undefined> {
    const row = await queryOne<{ llm_config: unknown }>(
      `SELECT llm_config FROM user_profiles WHERE user_id = $1`,
      [userId],
    );
    if (!row?.llm_config) return undefined;
    const cfg = parseLlmConfig(row.llm_config);
    if (cfg?.apiKey) {
      try {
        cfg.apiKey = decryptApiKey(cfg.apiKey);
      } catch (e) {
        console.error(`[UserStore] 解密用户 ${userId} 的 apiKey 失败:`, e);
      }
    }
    return cfg;
  }

  async createUser(record: UserRecord): Promise<boolean> {
    const llmConfigJson = record.llmConfig ? JSON.stringify(encryptLlmConfig(record.llmConfig)) : "{}";
    const result = await query(
      `INSERT INTO user_profiles
       (user_id, name, role, entity_type, is_admin, org_id, team_id, wechat_user_id, status, llm_config)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active', $9)
       ON CONFLICT (user_id) DO NOTHING
       RETURNING user_id`,
      [record.userId, record.name, record.role, record.entityType, record.isAdmin, record.orgId, record.teamId, record.wechatUserId, llmConfigJson],
    );
    return (result?.length ?? 0) > 0;
  }

  async updateUser(userId: string, updates: Partial<UserRecord>): Promise<boolean> {
    const fields: string[] = [];
    const params: unknown[] = [];

    const addField = (column: string, value: unknown) => {
      if (value === undefined) return;
      params.push(value);
      fields.push(`${column} = $${params.length}`);
    };

    addField("name", updates.name);
    addField("role", updates.role);
    addField("entity_type", updates.entityType);
    addField("is_admin", updates.isAdmin);
    addField("org_id", updates.orgId);
    addField("team_id", updates.teamId);
    addField("wechat_user_id", updates.wechatUserId);
    addField("status", updates.status);
    if (updates.llmConfig !== undefined) {
      addField("llm_config", JSON.stringify(encryptLlmConfig(updates.llmConfig)));
    }

    if (fields.length === 0) return false;

    params.push(userId);
    const result = await query(
      `UPDATE user_profiles
       SET ${fields.join(", ")}, updated_at = CURRENT_TIMESTAMP
       WHERE user_id = $${params.length}
       RETURNING user_id`,
      params,
    );
    return (result?.length ?? 0) > 0;
  }

  async deactivateUser(userId: string): Promise<boolean> {
    return this.updateUser(userId, { status: "disabled" });
  }

  async ensureUserExists(userId: string, name?: string, orgId = "org_default"): Promise<boolean> {
    const existing = await this.getUser(userId);
    if (existing) return false;
    return this.createUser({
      userId,
      name: name || userId,
      role: "成员",
      entityType: "user",
      isAdmin: false,
      orgId,
      status: "active",
    });
  }
}

interface UserRecordDbRow {
  user_id: string;
  name: string;
  role: string;
  entity_type: string;
  is_admin: boolean;
  org_id: string;
  team_id?: string;
  wechat_user_id?: string;
  status: string;
  llm_config?: unknown;
  created_at: Date;
  updated_at: Date;
}

function parseLlmConfig(raw: unknown): UserLlmConfig | undefined {
  if (!raw) return undefined;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as UserLlmConfig;
    } catch {
      return undefined;
    }
  }
  if (typeof raw === "object") return raw as UserLlmConfig;
  return undefined;
}

function encryptLlmConfig(cfg: UserLlmConfig): UserLlmConfig {
  if (!cfg.apiKey) return cfg;
  return { ...cfg, apiKey: encryptApiKey(cfg.apiKey) };
}

function dbRowToRecord(row: UserRecordDbRow): UserRecord {
  const cfg = parseLlmConfig(row.llm_config);
  if (cfg?.apiKey) {
    try {
      cfg.apiKey = decryptApiKey(cfg.apiKey);
    } catch (e) {
      console.error(`[UserStore] 解密用户 ${row.user_id} 的 apiKey 失败:`, e);
    }
  }
  return {
    userId: row.user_id,
    name: row.name,
    role: row.role,
    entityType: row.entity_type,
    isAdmin: row.is_admin,
    orgId: row.org_id,
    teamId: row.team_id,
    wechatUserId: row.wechat_user_id,
    status: row.status,
    llmConfig: cfg,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}
