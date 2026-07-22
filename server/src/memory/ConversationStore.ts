import { query, queryOne } from "../db/index.ts";

export interface ThreadMessage {
  role: "user" | "assistant";
  content: string;
  created_at: Date;
  user_id?: string;
  user_name?: string;
}

export interface Thread {
  thread_id: string;
  user_id: string;
  summary: string;
  message_count: number;
  is_archived: boolean;
  result_preview: string;
  files_json: unknown[];
  todos_json: unknown[];
  created_at: Date;
  updated_at: Date;
  project_name: string | null;
  linked_project?: string | null;
}

function generateThreadId(): string {
  return `thread_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export class ConversationStore {
  async createThread(userId: string, summary = ""): Promise<string> {
    const threadId = generateThreadId();
    await query(
      `INSERT INTO conversation_threads
       (thread_id, user_id, summary, message_count, is_archived, result_preview, files_json, todos_json)
       VALUES ($1, $2, $3, 0, FALSE, '', '[]', '[]')`,
      [threadId, userId, summary],
    );
    return threadId;
  }

  async getOrCreateActiveThread(userId: string): Promise<string> {
    const row = await queryOne<{ thread_id: string }>(
      `SELECT thread_id FROM conversation_threads
       WHERE user_id = $1 AND is_archived = FALSE AND project_name IS NULL
       ORDER BY updated_at DESC LIMIT 1`,
      [userId],
    );
    if (row) return row.thread_id;
    return this.createThread(userId);
  }

  async getActiveThread(userId: string): Promise<string | undefined> {
    const row = await queryOne<{ thread_id: string }>(
      `SELECT thread_id FROM conversation_threads
       WHERE user_id = $1 AND is_archived = FALSE AND project_name IS NULL
       ORDER BY updated_at DESC LIMIT 1`,
      [userId],
    );
    return row?.thread_id;
  }

  async addMessage(
    userId: string,
    threadId: string,
    role: "user" | "assistant",
    content: string,
  ): Promise<void> {
    await query(
      `INSERT INTO episodic_memory (user_id, thread_id, role, content)
       VALUES ($1, $2, $3, $4)`,
      [userId, threadId, role, content],
    );

    if (role === "user") {
      await query(
        `UPDATE conversation_threads
         SET message_count = message_count + 1,
             updated_at = CURRENT_TIMESTAMP,
             summary = COALESCE(NULLIF(summary, ''), LEFT($1, 80))
         WHERE thread_id = $2 AND user_id = $3`,
        [content.trim(), threadId, userId],
      );
    } else {
      await query(
        `UPDATE conversation_threads
         SET message_count = message_count + 1,
             updated_at = CURRENT_TIMESTAMP,
             result_preview = $1
         WHERE thread_id = $2 AND user_id = $3`,
        [content.trim(), threadId, userId],
      );
    }
  }

  async getRecentMessages(
    userId: string,
    threadId: string,
    limit = 50,
  ): Promise<ThreadMessage[]> {
    return await query<ThreadMessage>(
      `SELECT role, content, created_at FROM episodic_memory
       WHERE user_id = $1 AND thread_id = $2
       ORDER BY created_at ASC LIMIT $3`,
      [userId, threadId, limit],
    );
  }

  async listThreads(userId: string, limit = 20, offset = 0): Promise<Thread[]> {
    return await query<Thread>(
      `SELECT thread_id, user_id, summary, message_count, is_archived,
              result_preview, files_json, todos_json, created_at, updated_at, project_name
       FROM conversation_threads
       WHERE user_id = $1 AND project_name IS NULL
       ORDER BY is_archived ASC, updated_at DESC LIMIT $2 OFFSET $3`,
      [userId, limit, offset],
    );
  }

  async getThread(userId: string, threadId: string): Promise<Thread | undefined> {
    return await queryOne<Thread>(
      `SELECT thread_id, user_id, summary, message_count, is_archived,
              result_preview, files_json, todos_json, created_at, updated_at, project_name
       FROM conversation_threads
       WHERE thread_id = $1 AND user_id = $2`,
      [threadId, userId],
    );
  }

  async deleteThread(userId: string, threadId: string): Promise<boolean> {
    await query(`DELETE FROM episodic_memory WHERE thread_id = $1 AND user_id = $2`, [
      threadId,
      userId,
    ]);
    await query(
      `DELETE FROM conversation_threads WHERE thread_id = $1 AND user_id = $2`,
      [threadId, userId],
    );
    return true;
  }

  async renameThread(userId: string, threadId: string, title: string): Promise<boolean> {
    await query(
      `UPDATE conversation_threads
       SET summary = $1, updated_at = CURRENT_TIMESTAMP
       WHERE thread_id = $2 AND user_id = $3`,
      [title, threadId, userId],
    );
    return true;
  }

  async activateThread(userId: string, threadId: string): Promise<boolean> {
    await query(
      `UPDATE conversation_threads
       SET is_archived = TRUE
       WHERE user_id = $1 AND thread_id != $2 AND project_name IS NULL`,
      [userId, threadId],
    );
    await query(
      `UPDATE conversation_threads
       SET is_archived = FALSE
       WHERE user_id = $1 AND thread_id = $2 AND project_name IS NULL`,
      [userId, threadId],
    );
    return true;
  }

  async archiveAllThreads(userId: string): Promise<void> {
    await query(
      `UPDATE conversation_threads
       SET is_archived = TRUE, updated_at = CURRENT_TIMESTAMP
       WHERE user_id = $1 AND is_archived = FALSE AND project_name IS NULL`,
      [userId],
    );
  }

  async updateThreadMeta(
    userId: string,
    threadId: string,
    meta: { result_preview?: string; files_json?: unknown[]; todos_json?: unknown[] },
  ): Promise<void> {
    const thread = await this.getThread(userId, threadId);
    if (!thread) return;
    await query(
      `UPDATE conversation_threads
       SET result_preview = $1,
           files_json = $2,
           todos_json = $3,
           updated_at = CURRENT_TIMESTAMP
       WHERE thread_id = $4 AND user_id = $5`,
      [
        meta.result_preview ?? thread.result_preview,
        JSON.stringify(meta.files_json ?? thread.files_json),
        JSON.stringify(meta.todos_json ?? thread.todos_json),
        threadId,
        userId,
      ],
    );
  }

  // ========== 共享项目频道（thread 维度，不按 user 过滤） ==========

  // find-or-create：任何成员首次打开即创建；并发首开靠部分唯一索引兜底
  async getOrCreateProjectThread(projectName: string, creatorUserId: string): Promise<string> {
    const existing = await queryOne<{ thread_id: string }>(
      `SELECT thread_id FROM conversation_threads WHERE project_name = $1`,
      [projectName],
    );
    if (existing) return existing.thread_id;
    const threadId = generateThreadId();
    await query(
      `INSERT INTO conversation_threads
       (thread_id, user_id, summary, message_count, is_archived, result_preview, files_json, todos_json, project_name)
       VALUES ($1, $2, $3, 0, FALSE, '', '[]', '[]', $3)
       ON CONFLICT (project_name) WHERE project_name IS NOT NULL DO NOTHING`,
      [threadId, creatorUserId, projectName],
    );
    const row = await queryOne<{ thread_id: string }>(
      `SELECT thread_id FROM conversation_threads WHERE project_name = $1`,
      [projectName],
    );
    return row!.thread_id;
  }

  async listProjectThreads(): Promise<Thread[]> {
    return await query<Thread>(
      `SELECT thread_id, user_id, summary, message_count, is_archived,
              result_preview, files_json, todos_json, created_at, updated_at, project_name
       FROM conversation_threads
       WHERE project_name IS NOT NULL
       ORDER BY updated_at DESC`,
    );
  }

  async getThreadById(threadId: string): Promise<Thread | undefined> {
    return await queryOne<Thread>(
      `SELECT thread_id, user_id, summary, message_count, is_archived,
              result_preview, files_json, todos_json, created_at, updated_at, project_name, linked_project
       FROM conversation_threads
       WHERE thread_id = $1`,
      [threadId],
    );
  }

  async getProjectName(threadId: string): Promise<string | null> {
    const row = await queryOne<{ project_name: string | null }>(
      `SELECT project_name FROM conversation_threads WHERE thread_id = $1`,
      [threadId],
    );
    return row?.project_name ?? null;
  }

  async getThreadByProjectName(projectName: string): Promise<Thread | undefined> {
    return await queryOne<Thread>(
      `SELECT thread_id, user_id, summary, message_count, is_archived,
              result_preview, files_json, todos_json, created_at, updated_at, project_name, linked_project
       FROM conversation_threads
       WHERE project_name = $1`,
      [projectName],
    );
  }

  // 频道历史：thread 维度（含全体成员消息），联表取发言者姓名
  async getThreadMessages(threadId: string, limit = 50): Promise<ThreadMessage[]> {
    return await query<ThreadMessage>(
      `SELECT m.role, m.content, m.created_at, m.user_id,
              COALESCE(p.name, m.user_id) AS user_name
       FROM episodic_memory m
       LEFT JOIN user_profiles p ON p.user_id = m.user_id
       WHERE m.thread_id = $1
       ORDER BY m.created_at ASC LIMIT $2`,
      [threadId, limit],
    );
  }

  // 频道写消息：thread 更新不带 user_id 条件；标题恒为项目名，user 消息不覆盖 summary
  async addMessageToProjectThread(
    authorUserId: string,
    threadId: string,
    role: "user" | "assistant",
    content: string,
  ): Promise<void> {
    await query(
      `INSERT INTO episodic_memory (user_id, thread_id, role, content)
       VALUES ($1, $2, $3, $4)`,
      [authorUserId, threadId, role, content],
    );

    if (role === "user") {
      await query(
        `UPDATE conversation_threads
         SET message_count = message_count + 1,
             updated_at = CURRENT_TIMESTAMP
         WHERE thread_id = $1`,
        [threadId],
      );
    } else {
      await query(
        `UPDATE conversation_threads
         SET message_count = message_count + 1,
             updated_at = CURRENT_TIMESTAMP,
             result_preview = $1
         WHERE thread_id = $2`,
        [content.trim(), threadId],
      );
    }
  }

  async updateThreadMetaById(
    threadId: string,
    meta: { result_preview?: string; files_json?: unknown[]; todos_json?: unknown[] },
  ): Promise<void> {
    const thread = await this.getThreadById(threadId);
    if (!thread) return;
    await query(
      `UPDATE conversation_threads
       SET result_preview = $1,
           files_json = $2,
           todos_json = $3,
           updated_at = CURRENT_TIMESTAMP
       WHERE thread_id = $4`,
      [
        meta.result_preview ?? thread.result_preview,
        JSON.stringify(meta.files_json ?? thread.files_json),
        JSON.stringify(meta.todos_json ?? thread.todos_json),
        threadId,
      ],
    );
  }

  // ========== 自定义频道：创建、成员与访问控制 ==========

  // 显式创建自定义频道；重名抛错（部分唯一索引 23505 由调用方转成友好提示）
  async createCustomChannel(
    name: string,
    creatorUserId: string,
    linkedProject: string | null,
  ): Promise<string> {
    const threadId = generateThreadId();
    await query(
      `INSERT INTO conversation_threads
       (thread_id, user_id, summary, message_count, is_archived, result_preview, files_json, todos_json, project_name, linked_project)
       VALUES ($1, $2, $3, 0, FALSE, '', '[]', '[]', $3, $4)`,
      [threadId, creatorUserId, name, linkedProject],
    );
    return threadId;
  }

  async setChannelMembers(threadId: string, userIds: string[], addedBy: string): Promise<void> {
    await query(`DELETE FROM channel_members WHERE thread_id = $1`, [threadId]);
    for (const uid of userIds) {
      await query(
        `INSERT INTO channel_members (thread_id, user_id, added_by) VALUES ($1, $2, $3)
         ON CONFLICT (thread_id, user_id) DO NOTHING`,
        [threadId, uid, addedBy],
      );
    }
  }

  async addChannelMember(threadId: string, userId: string, addedBy: string): Promise<void> {
    await query(
      `INSERT INTO channel_members (thread_id, user_id, added_by) VALUES ($1, $2, $3)
       ON CONFLICT (thread_id, user_id) DO NOTHING`,
      [threadId, userId, addedBy],
    );
  }

  async removeChannelMember(threadId: string, userId: string): Promise<void> {
    await query(`DELETE FROM channel_members WHERE thread_id = $1 AND user_id = $2`, [
      threadId,
      userId,
    ]);
  }

  // 转让频道：threads.user_id 换成新创建者
  async transferChannelOwnership(threadId: string, newOwnerId: string): Promise<void> {
    await query(`UPDATE conversation_threads SET user_id = $1 WHERE thread_id = $2`, [
      newOwnerId,
      threadId,
    ]);
  }

  // 删除频道（含成员与全部消息），调用方负责权限校验
  async deleteChannel(threadId: string): Promise<void> {
    await query(`DELETE FROM channel_members WHERE thread_id = $1`, [threadId]);
    await query(`DELETE FROM episodic_memory WHERE thread_id = $1`, [threadId]);
    await query(`DELETE FROM conversation_threads WHERE thread_id = $1`, [threadId]);
  }

  async getChannelMembers(threadId: string): Promise<string[]> {
    const rows = await query<{ user_id: string }>(
      `SELECT user_id FROM channel_members WHERE thread_id = $1`,
      [threadId],
    );
    return rows.map((r) => r.user_id);
  }

  // 访问规则：无成员记录 = 全员开放（如默认项目频道）；
  // 有成员记录 = 仅创建者（threads.user_id）或成员可访问
  async canAccessChannel(thread: Thread, userId: string): Promise<boolean> {
    const members = await this.getChannelMembers(thread.thread_id);
    if (members.length === 0) return true;
    if (thread.user_id === userId) return true;
    return members.includes(userId);
  }

  // 频道列表：只返回对该用户可见的（开放频道 + 自己创建/加入的受限频道）
  async listProjectThreadsForUser(userId: string): Promise<Thread[]> {
    return await query<Thread>(
      `SELECT t.thread_id, t.user_id, t.summary, t.message_count, t.is_archived,
              t.result_preview, t.files_json, t.todos_json, t.created_at, t.updated_at,
              t.project_name, t.linked_project
       FROM conversation_threads t
       WHERE t.project_name IS NOT NULL
         AND (
           NOT EXISTS (SELECT 1 FROM channel_members m WHERE m.thread_id = t.thread_id)
           OR t.user_id = $1
           OR EXISTS (SELECT 1 FROM channel_members m WHERE m.thread_id = t.thread_id AND m.user_id = $1)
         )
       ORDER BY t.updated_at DESC`,
      [userId],
    );
  }
}
