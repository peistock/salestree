import { query, queryOne } from "../db/index.ts";

export interface ThreadMessage {
  role: "user" | "assistant";
  content: string;
  created_at: Date;
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
       WHERE user_id = $1 AND is_archived = FALSE
       ORDER BY updated_at DESC LIMIT 1`,
      [userId],
    );
    if (row) return row.thread_id;
    return this.createThread(userId);
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
              result_preview, files_json, todos_json, created_at, updated_at
       FROM conversation_threads
       WHERE user_id = $1
       ORDER BY is_archived ASC, updated_at DESC LIMIT $2 OFFSET $3`,
      [userId, limit, offset],
    );
  }

  async getThread(userId: string, threadId: string): Promise<Thread | undefined> {
    return await queryOne<Thread>(
      `SELECT thread_id, user_id, summary, message_count, is_archived,
              result_preview, files_json, todos_json, created_at, updated_at
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
       WHERE user_id = $1 AND thread_id != $2`,
      [userId, threadId],
    );
    await query(
      `UPDATE conversation_threads
       SET is_archived = FALSE
       WHERE user_id = $1 AND thread_id = $2`,
      [userId, threadId],
    );
    return true;
  }

  async archiveAllThreads(userId: string): Promise<void> {
    await query(
      `UPDATE conversation_threads
       SET is_archived = TRUE, updated_at = CURRENT_TIMESTAMP
       WHERE user_id = $1 AND is_archived = FALSE`,
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
}
