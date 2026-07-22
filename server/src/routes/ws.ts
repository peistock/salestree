import type { FastifyInstance } from "fastify";
import { createHash } from "crypto";
import fs, { readFileSync } from "fs";
import { resolve, dirname, join } from "path";
import { resolveUploadPath } from "../utils/fileStorage.ts";
import { AgentSession, type OutgoingMessage } from "../agent/Session.ts";
import { ConversationStore } from "../memory/ConversationStore.ts";
import { UsageStore } from "../db/usageStore.ts";
import { UserStore } from "../db/userStore.ts";
import { config } from "../config.ts";

interface AttachmentMeta {
  name: string;
  url: string;
  mimeType: string;
  size: number;
}

interface ClientMessage {
  user_id?: string;
  message?: string;
  action?: "hello" | "stop" | "new_thread";
  title?: string;
  thread_id?: string; // 显式目标线程（项目频道或私有线程）
  project_name?: string; // hello 时按项目名 find-or-create 频道
  attachments?: AttachmentMeta[];
}

interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  user_id?: string;
  user_name?: string;
}

const activeSessions = new Map<string, AgentSession>();
const conversationStore = new ConversationStore();
const usageStore = new UsageStore();
const userStore = new UserStore();

// 共享项目频道：按 thread 串行队列、进行中的频道会话、在线查看者
const channelQueues = new Map<string, Promise<void>>();
const channelSessions = new Map<string, AgentSession>();
const channelViewers = new Map<string, Set<any>>();
const socketMeta = new Map<any, { userId: string; viewingThreadId: string | null }>();

const MENTION_BOT = /[@＠](销销|xiaoxiao)\s*/gi;

// 在管项目名集合（与 index.ts loadProjectAccounts 同源）：
// 频道 hello 只对在管项目自动创建，其他名字必须走 /api/create_channel（带成员与关联项目）
function loadManagedProjectNames(): Set<string> {
  const names = new Set<string>();
  const baseDir = resolve(dirname(new URL(import.meta.url).pathname), "../../..", "data", "projects");
  for (const file of ["feishu_accounts.json", "dingtalk_accounts.json", "wechat_accounts.json", "hybrid_accounts.json"]) {
    try {
      const config = JSON.parse(readFileSync(join(baseDir, file), "utf-8"));
      for (const name of Object.keys(config.accounts || {})) names.add(name);
    } catch {
      // 配置文件缺失时跳过
    }
  }
  return names;
}

function addViewer(threadId: string, socket: any, userId: string) {
  removeViewer(socket);
  let set = channelViewers.get(threadId);
  if (!set) {
    set = new Set();
    channelViewers.set(threadId, set);
  }
  set.add(socket);
  socketMeta.set(socket, { userId, viewingThreadId: threadId });
}

function removeViewer(socket: any) {
  const meta = socketMeta.get(socket);
  if (meta?.viewingThreadId) {
    const set = channelViewers.get(meta.viewingThreadId);
    if (set) {
      set.delete(socket);
      if (set.size === 0) channelViewers.delete(meta.viewingThreadId);
    }
  }
  socketMeta.delete(socket);
}

function broadcastToChannel(threadId: string, msg: OutgoingMessage) {
  const set = channelViewers.get(threadId);
  if (!set) return;
  const payload = JSON.stringify({ ...msg, thread_id: threadId });
  for (const s of set) {
    if (s.readyState === 1) s.send(payload);
  }
}

const DEDUP_TTL_MS = 60_000;
const dedupCache = new Map<string, number>();

function dedupKey(userId: string, threadId: string, message: string): string {
  return createHash("sha256").update(`${userId}:${threadId}:${message}`).digest("hex");
}

function isDuplicate(key: string): boolean {
  const now = Date.now();
  const lastSeen = dedupCache.get(key);
  if (lastSeen && now - lastSeen < DEDUP_TTL_MS) {
    return true;
  }
  dedupCache.set(key, now);
  return false;
}

setInterval(() => {
  const now = Date.now();
  for (const [key, ts] of dedupCache.entries()) {
    if (now - ts > DEDUP_TTL_MS) dedupCache.delete(key);
  }
}, 300_000);

export async function wsChatRoutes(app: FastifyInstance) {
  app.get("/ws/chat", { websocket: true }, (socket: any, req) => {

    const send = (msg: OutgoingMessage) => {
      if (socket.readyState === 1) {
        socket.send(JSON.stringify(msg));
      }
    };

    socket.on("message", async (raw: Buffer) => {
      let data: ClientMessage;
      try {
        data = JSON.parse(raw.toString()) as ClientMessage;
      } catch {
        send({ type: "error", message: "消息格式错误" });
        return;
      }

      const userId = data.user_id?.trim() || "web_user";

      // 用户存在性校验
      if (config.requireKnownUsers) {
        const existing = await userStore.getUser(userId);
        if (!existing || existing.status !== "active") {
          send({ type: "error", message: "用户不存在或已被禁用，请联系管理员。" });
          return;
        }
      } else {
        // 默认自动创建占位用户，避免幽灵账号
        await userStore.ensureUserExists(userId, userId).catch((err) => {
          console.error("[ws] 确保用户存在失败:", err);
        });
      }

      if (data.action === "stop") {
        if (data.thread_id && channelSessions.has(data.thread_id)) {
          channelSessions.get(data.thread_id)?.abort();
          broadcastToChannel(data.thread_id, { type: "status", message: "已停止生成" });
        } else {
          activeSessions.get(userId)?.abort();
          send({ type: "status", message: "已停止生成" });
        }
        return;
      }

      if (data.action === "hello") {
        try {
          // 项目频道：按项目名 find-or-create，或按 thread_id 直接打开
          const projectName = data.project_name?.trim();
          if (projectName || data.thread_id) {
            let thread = data.thread_id
              ? await conversationStore.getThreadById(data.thread_id)
              : undefined;
            if (projectName && !thread) {
              thread = await conversationStore.getThreadByProjectName(projectName);
              if (!thread) {
                // 频道不存在：仅在管项目允许自动创建；自定义频道必须走 /api/create_channel
                if (!loadManagedProjectNames().has(projectName)) {
                  send({ type: "error", message: "频道不存在，请通过「项目频道 +」创建" });
                  return;
                }
                const threadId = await conversationStore.getOrCreateProjectThread(projectName, userId);
                thread = await conversationStore.getThreadById(threadId);
              }
            }
            if (!thread) {
              send({ type: "error", message: "线程不存在" });
              return;
            }
            const isChannel = !!thread.project_name;
            if (!isChannel && thread.user_id !== userId) {
              send({ type: "error", message: "无权访问该线程" });
              return;
            }
            if (isChannel && !(await conversationStore.canAccessChannel(thread, userId))) {
              send({ type: "error", message: "你不在该频道成员中" });
              return;
            }
            if (isChannel) {
              addViewer(thread.thread_id, socket, userId);
              const messages = await conversationStore.getThreadMessages(thread.thread_id, 50);
              send({
                type: "history",
                thread_id: thread.thread_id,
                project_name: thread.project_name ?? undefined,
                hint: `已进入频道：${thread.project_name}`,
                messages: messages.map((m) => ({
                  role: m.role,
                  content: m.content,
                  user_id: m.user_id,
                  user_name: m.user_name,
                })),
              } as OutgoingMessage);
            } else {
              removeViewer(socket);
              const messages = await conversationStore.getRecentMessages(userId, thread.thread_id, 50);
              send({
                type: "history",
                thread_id: thread.thread_id,
                hint: messages.length > 0 ? "已恢复当前对话" : undefined,
                messages: messages.map((m) => ({ role: m.role, content: m.content })),
              } as OutgoingMessage);
            }
            return;
          }

          // 默认：私有活跃线程
          removeViewer(socket);
          const threadId = await conversationStore.getActiveThread(userId);
          if (!threadId) {
            send({ type: "history", thread_id: undefined, hint: undefined, messages: [] } as OutgoingMessage);
            return;
          }
          const messages = await conversationStore.getRecentMessages(userId, threadId, 50);
          const history: HistoryMessage[] = messages.map((m) => ({
            role: m.role,
            content: m.content,
          }));
          send({
            type: "history",
            thread_id: threadId,
            hint: history.length > 0 ? "已恢复当前对话" : undefined,
            messages: history,
          } as OutgoingMessage);
        } catch (err) {
          console.error("[ws] 恢复历史失败:", err);
          send({ type: "status", message: "已连接" });
        }
        return;
      }

      if (data.action === "new_thread") {
        try {
          await conversationStore.archiveAllThreads(userId);
          const threadId = await conversationStore.createThread(userId, data.title || "");
          send({
            type: "history",
            hint: `已新建任务：${data.title || "新任务"}`,
            messages: [],
          } as OutgoingMessage);
          console.log(`[ws] 新建线程 ${threadId} for ${userId}, title=${data.title || ""}`);
        } catch (err) {
          console.error("[ws] 新建线程失败:", err);
          send({ type: "error", message: "新建任务失败" });
        }
        return;
      }

      const message = data.message?.trim() || "";
      const attachments = data.attachments || [];
      if (!message && attachments.length === 0) {
        send({ type: "error", message: "消息不能为空" });
        return;
      }

      const attachmentSummary = attachments.map((a) => `${a.name}(${a.mimeType})`).join("|");
      const key = dedupKey(userId, data.thread_id ?? "", `${message}::${attachmentSummary}`);
      if (isDuplicate(key)) {
        send({ type: "status", message: "消息正在处理中，请勿重复发送" });
        return;
      }

      // 串行：同一个 user_id 只保留最新会话，旧的中止
      activeSessions.get(userId)?.abort();

      // 组织月度配额检查
      let orgId: string;
      try {
        orgId = await usageStore.getOrgForUser(userId);
        const org = await usageStore.getOrganization(orgId);
        if (org) {
          const now = new Date();
          const used = await usageStore.getMonthlyUsage(orgId, now.getFullYear(), now.getMonth() + 1);
          if (used >= org.monthly_token_quota) {
            send({ type: "error", message: "当前组织本月 LLM 额度已用完，请联系管理员升级套餐。" });
            return;
          }
        }
      } catch (err) {
        console.error("[ws] 检查组织配额失败:", err);
        send({ type: "error", message: "配额检查失败，请稍后再试" });
        return;
      }

      // 解析目标线程：显式 thread_id（频道或本人私有）或默认私有活跃线程
      let threadId: string;
      let projectName: string | null = null;
      let contextProject: string | null = null;
      try {
        if (data.thread_id) {
          const thread = await conversationStore.getThreadById(data.thread_id);
          if (!thread) {
            send({ type: "error", message: "线程不存在" });
            return;
          }
          if (thread.project_name) {
            if (!(await conversationStore.canAccessChannel(thread, userId))) {
              send({ type: "error", message: "你不在该频道成员中" });
              return;
            }
            projectName = thread.project_name;
            contextProject = thread.linked_project || thread.project_name;
          } else if (thread.user_id !== userId) {
            send({ type: "error", message: "无权访问该线程" });
            return;
          }
          threadId = thread.thread_id;
        } else {
          threadId = await conversationStore.getOrCreateActiveThread(userId);
          // 首次消息自动创建线程时通知前端，避免刷新后重复创建
          if (!data.thread_id) {
            send({ type: "thread_created", thread_id: threadId } as OutgoingMessage);
          }
        }
      } catch (err) {
        console.error("[ws] 获取线程失败:", err);
        send({ type: "error", message: "无法创建对话线程" });
        return;
      }

      // 为图片附件读取本地 base64 数据
      const attachmentsWithData = attachments.map((a) => {
        if (a.mimeType.startsWith("image/")) {
          try {
            const rel = a.url.replace("/data/uploads/", "");
            const buffer = fs.readFileSync(resolveUploadPath(rel));
            return { ...a, data: buffer.toString("base64") };
          } catch (err) {
            console.error("[ws] 读取图片失败:", a.url, err);
          }
        }
        return a;
      });

      // ============ 项目频道路径 ============
      if (projectName) {
        const sender = await userStore.getUser(userId).catch(() => undefined);
        const senderName = sender?.name || userId;

        // 未 @销销：纯人-人消息，只持久化 + 广播
        if (!MENTION_BOT.test(message)) {
          MENTION_BOT.lastIndex = 0;
          try {
            await conversationStore.addMessageToProjectThread(userId, threadId, "user", message);
            broadcastToChannel(threadId, {
              type: "channel_message",
              role: "user",
              user_id: userId,
              user_name: senderName,
              content: message,
            } as OutgoingMessage);
          } catch (err) {
            console.error("[ws] 频道消息持久化失败:", err);
            send({ type: "error", message: "消息发送失败" });
          }
          return;
        }
        MENTION_BOT.lastIndex = 0;
        const query = message.replace(MENTION_BOT, "").trim();
        if (!query) {
          send({ type: "status", message: "@销销 后请跟上具体问题" });
          return;
        }

        // 先立即持久化原文（保留 @销销）并广播，入队等待期间其他成员也能看到
        try {
          await conversationStore.addMessageToProjectThread(userId, threadId, "user", message);
          broadcastToChannel(threadId, {
            type: "channel_message",
            role: "user",
            user_id: userId,
            user_name: senderName,
            content: message,
          } as OutgoingMessage);
        } catch (err) {
          console.error("[ws] 频道消息持久化失败:", err);
          send({ type: "error", message: "消息发送失败" });
          return;
        }

        // @销销：进入按 thread 串行队列，逐条执行
        const prev = channelQueues.get(threadId) ?? Promise.resolve();
        const run = prev.then(async () => {
          const llmConfig = await userStore.getUserLlmConfig(userId);
          const session = new AgentSession(
            userId,
            senderName,
            threadId,
            conversationStore,
            orgId,
            usageStore,
            { projectName, contextProject: contextProject ?? projectName, skipUserPersist: true, llmConfig },
          );
          channelSessions.set(threadId, session);
          activeSessions.set(userId, session);
          const bcast = (msg: OutgoingMessage) => broadcastToChannel(threadId, msg);
          try {
            bcast({ type: "status", message: "销销正在思考…" });
            const result = await session.run(query, attachmentsWithData, bcast);
            bcast(result);
          } finally {
            channelSessions.delete(threadId);
            if (activeSessions.get(userId) === session) activeSessions.delete(userId);
          }
        });
        channelQueues.set(threadId, run.catch(() => {}));
        await run.catch((err) => {
          console.error("[ws] 频道会话执行失败:", err);
          broadcastToChannel(threadId, { type: "error", message: "频道回复失败，请重试" });
        });
        return;
      }

      // ============ 私有线程路径（原逻辑） ============
      const llmConfig = await userStore.getUserLlmConfig(userId);
      const session = new AgentSession(userId, "", threadId, conversationStore, orgId, usageStore, { llmConfig });
      activeSessions.set(userId, session);

      send({ type: "status", message: "销销正在思考…" });
      const result = await session.run(message, attachmentsWithData, send);
      send(result);
      activeSessions.delete(userId);
    });

    socket.on("close", () => {
      removeViewer(socket);
    });
  });
}
