import type { FastifyInstance } from "fastify";
import { createHash } from "crypto";
import fs from "fs";
import { resolveUploadPath } from "../utils/fileStorage.ts";
import { AgentSession, type OutgoingMessage } from "../agent/Session.ts";
import { ConversationStore } from "../memory/ConversationStore.ts";
import { UsageStore } from "../db/usageStore.ts";

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
  attachments?: AttachmentMeta[];
}

interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

const activeSessions = new Map<string, AgentSession>();
const conversationStore = new ConversationStore();
const usageStore = new UsageStore();

const DEDUP_TTL_MS = 60_000;
const dedupCache = new Map<string, number>();

function dedupKey(userId: string, message: string): string {
  return createHash("sha256").update(`${userId}:${message}`).digest("hex");
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

      if (data.action === "stop") {
        activeSessions.get(userId)?.abort();
        send({ type: "status", message: "已停止生成" });
        return;
      }

      if (data.action === "hello") {
        try {
          const threadId = await conversationStore.getOrCreateActiveThread(userId);
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
      const key = dedupKey(userId, `${message}::${attachmentSummary}`);
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

      let threadId: string;
      try {
        threadId = await conversationStore.getOrCreateActiveThread(userId);
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

      const session = new AgentSession(userId, "", threadId, conversationStore, orgId, usageStore);
      activeSessions.set(userId, session);

      send({ type: "status", message: "销销正在思考…" });
      const result = await session.run(message, attachmentsWithData, send);
      send(result);
      activeSessions.delete(userId);
    });

    socket.on("close", () => {
      // 清理由后续连接处理
    });
  });
}
