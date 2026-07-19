import { query, queryOne } from "./index.ts";

export interface UserRecord {
  userId: string;
  name: string;
  role: string;
  entityType: string;
  orgId: string;
  teamId?: string;
  wechatUserId?: string;
  status: string;
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
      `SELECT u.user_id, u.name, u.role, u.entity_type, u.org_id,
              u.team_id, u.wechat_user_id, u.status, u.created_at, u.updated_at
       FROM user_profiles u
       ${whereClause}
       ORDER BY u.created_at DESC`,
      params,
    ).then((rows) => rows.map(dbRowToRecord));
  }

  async getUser(userId: string): Promise<UserRecord | undefined> {
    const row = await queryOne<UserRecordDbRow>(
      `SELECT user_id, name, role, entity_type, org_id,
              team_id, wechat_user_id, status, created_at, updated_at
       FROM user_profiles WHERE user_id = $1`,
      [userId],
    );
    return row ? dbRowToRecord(row) : undefined;
  }

  async createUser(record: UserRecord): Promise<boolean> {
    const result = await query(
      `INSERT INTO user_profiles
       (user_id, name, role, entity_type, org_id, team_id, wechat_user_id, status)
       VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')
       ON CONFLICT (user_id) DO NOTHING
       RETURNING user_id`,
      [record.userId, record.name, record.role, record.entityType, record.orgId, record.teamId, record.wechatUserId],
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
    addField("org_id", updates.orgId);
    addField("team_id", updates.teamId);
    addField("wechat_user_id", updates.wechatUserId);
    addField("status", updates.status);

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
  org_id: string;
  team_id?: string;
  wechat_user_id?: string;
  status: string;
  created_at: Date;
  updated_at: Date;
}

function dbRowToRecord(row: UserRecordDbRow): UserRecord {
  return {
    userId: row.user_id,
    name: row.name,
    role: row.role,
    entityType: row.entity_type,
    orgId: row.org_id,
    teamId: row.team_id,
    wechatUserId: row.wechat_user_id,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}
