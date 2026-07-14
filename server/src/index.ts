import "dotenv/config";
import Fastify from "fastify";
import websocket from "@fastify/websocket";
import staticPlugin from "@fastify/static";
import multipart from "@fastify/multipart";
import path from "path";
import fs from "fs";
import { exec } from "child_process";
import { promisify } from "util";
import { fileURLToPath } from "url";
import { config } from "./config.ts";
import { chatPageRoutes } from "./routes/chat.ts";
import { healthRoutes } from "./routes/health.ts";
import { wsChatRoutes } from "./routes/ws.ts";
import { legacyProxyRoutes } from "./routes/proxy.ts";
import { wechatKbRoutes } from "./routes/wechatKb.ts";
import { companyLeadsRoutes } from "./routes/companyLeads.ts";
import { salesPolicyRoutes } from "./routes/policy.ts";
import { uploadRoutes } from "./routes/upload.ts";
import { ConversationStore } from "./memory/ConversationStore.ts";

const conversationStore = new ConversationStore();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = Fastify({ logger: true });

async function main() {
  await app.register(websocket);
  await app.register(multipart, { limits: { fileSize: 10 * 1024 * 1024 } });
  await app.register(healthRoutes);
  await app.register(uploadRoutes);
  await app.register(wsChatRoutes);
  await app.register(wechatKbRoutes);
  await app.register(companyLeadsRoutes);
  await app.register(salesPolicyRoutes);
  await app.register(legacyProxyRoutes);
  await app.register(chatPageRoutes);

  // 兼容旧页面调用的 API 占位
  app.get("/api/users", async (_req, reply) => {
    return reply.send([{ user_id: "sales_001", name: "李明" }, { user_id: "sales_002", name: "王芳" }]);
  });

  app.get("/api/task_history", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    const limit = parseInt(((req.query as any).limit as string) || "10", 10) || 10;
    const offset = parseInt(((req.query as any).offset as string) || "0", 10) || 0;
    try {
      const threads = await conversationStore.listThreads(userId, limit, offset);
      const tasks = threads.map((t) => ({
        id: t.thread_id,
        title: t.summary || t.thread_id,
        mtime: Math.floor(new Date(t.updated_at).getTime() / 1000),
      }));
      return reply.send({ tasks });
    } catch (err) {
      console.error("[api] task_history error:", err);
      return reply.send({ tasks: [] });
    }
  });

  app.get("/api/latest_task", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    try {
      const threads = await conversationStore.listThreads(userId, 1);
      const t = threads[0];
      if (!t) return reply.send({ task: null });
      const messages = await conversationStore.getRecentMessages(userId, t.thread_id, 100);
      const conversation = messages.map((m) => `${m.role === "user" ? "用户" : "销销"}：${m.content}`).join("\n\n");
      const task = {
        id: t.thread_id,
        title: t.summary || t.thread_id,
        iteration: t.message_count,
        timestamp: Math.floor(new Date(t.created_at).getTime() / 1000),
        result_preview: t.result_preview || "",
        conversation,
        files: (t.files_json || []).map((f: any) => ({ name: f.name || String(f), path: f.path || "" })),
        todos: (t.todos_json || []).map((todo: any) => ({
          content: typeof todo === "string" ? todo : todo.content || "",
          status: typeof todo === "string" ? "pending" : todo.status || "pending",
        })),
      };
      return reply.send({ task });
    } catch (err) {
      console.error("[api] latest_task error:", err);
      return reply.send({ task: null });
    }
  });

  app.get("/api/task_detail", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    const threadId = ((req.query as any).task_id as string)?.trim();
    if (!threadId) return reply.status(400).send({ error: "缺少 task_id" });
    try {
      const t = await conversationStore.getThread(userId, threadId);
      if (!t) return reply.send({ task: null });
      const messages = await conversationStore.getRecentMessages(userId, t.thread_id, 100);
      const conversation = messages.map((m) => `${m.role === "user" ? "用户" : "销销"}：${m.content}`).join("\n\n");
      const task = {
        id: t.thread_id,
        title: t.summary || t.thread_id,
        iteration: t.message_count,
        timestamp: Math.floor(new Date(t.created_at).getTime() / 1000),
        result_preview: t.result_preview || "",
        conversation,
        files: (t.files_json || []).map((f: any) => ({ name: f.name || String(f), path: f.path || "" })),
        todos: (t.todos_json || []).map((todo: any) => ({
          content: typeof todo === "string" ? todo : todo.content || "",
          status: typeof todo === "string" ? "pending" : todo.status || "pending",
        })),
      };
      return reply.send({ task });
    } catch (err) {
      console.error("[api] task_detail error:", err);
      return reply.send({ task: null });
    }
  });

  app.post("/api/delete_task", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    const threadId = ((req.query as any).task_id as string)?.trim();
    if (!threadId) return reply.status(400).send({ error: "缺少 task_id" });
    try {
      const success = await conversationStore.deleteThread(userId, threadId);
      return reply.send({ success });
    } catch (err) {
      console.error("[api] delete_task error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  app.post("/api/rename_task", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    const threadId = ((req.query as any).task_id as string)?.trim();
    const title = ((req.query as any).title as string)?.trim();
    if (!threadId || !title) return reply.status(400).send({ error: "缺少 task_id 或 title" });
    try {
      const success = await conversationStore.renameThread(userId, threadId, title);
      return reply.send({ success, title });
    } catch (err) {
      console.error("[api] rename_task error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  app.post("/api/switch_thread", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    const threadId = ((req.query as any).task_id as string)?.trim();
    if (!threadId) return reply.status(400).send({ error: "缺少 task_id" });
    try {
      const success = await conversationStore.activateThread(userId, threadId);
      return reply.send({ success });
    } catch (err) {
      console.error("[api] switch_thread error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  app.post("/api/new_thread", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    const title = ((req.query as any).title as string)?.trim();
    try {
      // 新建任务时把其他活跃线程归档，避免 getOrCreateActiveThread 回到旧线程
      await conversationStore.archiveAllThreads(userId);
      const threadId = await conversationStore.createThread(userId, title || "");
      return reply.send({ success: true, thread_id: threadId });
    } catch (err) {
      console.error("[api] new_thread error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  // 项目看板：读取飞书群分析结果
  app.get("/api/project_panel", async (req, reply) => {
    const account = (((req.query as any).account as string)?.trim()) || "环平保险";
    const safeAccount = account.replace(/[^a-zA-Z0-9一-龥]/g, "_");
    const analysisPath = path.join(__dirname, "../../data/projects", `${safeAccount}_analysis.json`);
    try {
      const data = fs.readFileSync(analysisPath, "utf-8");
      return reply.send(JSON.parse(data));
    } catch (err) {
      console.error("[api] project_panel error:", err);
      return reply.status(404).send({ error: `暂无 ${account} 的项目看板数据` });
    }
  });

  // 项目看板账户列表
  app.get("/api/project_accounts", async (_req, reply) => {
    const accountsPath = path.join(__dirname, "../../data/projects/feishu_accounts.json");
    try {
      const raw = fs.readFileSync(accountsPath, "utf-8");
      const config = JSON.parse(raw);
      const accounts = Object.keys(config.accounts || {}).map((name) => ({ name }));
      return reply.send({ accounts });
    } catch (err) {
      console.error("[api] project_accounts error:", err);
      return reply.send({ accounts: [{ name: "环平保险" }] });
    }
  });

  // 项目看板：手动拉取最新飞书消息并重新分析
  app.post("/api/refresh_project_panel", async (req, reply) => {
    const account = (((req.query as any).account as string)?.trim()) || "环平保险";
    const execAsync = promisify(exec);
    const scriptsDir = path.join(__dirname, "../../scripts/feishu");
    const pullScript = path.join(scriptsDir, "pull_account.py");
    const analyzeScript = path.join(scriptsDir, "analyze_account.py");

    if (!fs.existsSync(pullScript) || !fs.existsSync(analyzeScript)) {
      return reply.status(500).send({ error: "刷新脚本不存在" });
    }

    reply.send({ success: true, message: `正在刷新 ${account} 的项目看板，请稍后…` });

    try {
      console.log(`[refresh_project_panel] 开始拉取 ${account} 的飞书消息…`);
      const { stderr: pullErr } = await execAsync(
        `python3 "${pullScript}" --account "${account}"`,
        { timeout: 120_000 },
      );
      if (pullErr) console.error("[refresh_project_panel] pull stderr:", pullErr);

      console.log(`[refresh_project_panel] 开始分析 ${account} …`);
      const { stderr: analyzeErr } = await execAsync(
        `set -a && source /Users/cpp/salestree/.env && set +a && python3 "${analyzeScript}" --account "${account}"`,
        { timeout: 300_000, shell: "/bin/bash" },
      );
      if (analyzeErr) console.error("[refresh_project_panel] analyze stderr:", analyzeErr);

      console.log("[refresh_project_panel] 刷新完成");
    } catch (err) {
      console.error("[refresh_project_panel] 刷新失败:", err);
    }
  });

  // 资讯看板：重新渲染 digest.html
  app.post("/api/refresh_news_panel", async (_req, reply) => {
    const execAsync = promisify(exec);
    const renderScript = path.resolve(
      __dirname,
      "../../third_party/wechat-digest-skill/render_html.py",
    );
    if (!fs.existsSync(renderScript)) {
      return reply.status(500).send({ error: "渲染脚本不存在" });
    }
    try {
      await execAsync(`python3 "${renderScript}"`, {
        cwd: path.dirname(renderScript),
        timeout: 120_000,
      });
      return reply.send({ success: true, message: "资讯看板已刷新" });
    } catch (err) {
      console.error("[refresh_news_panel] 刷新失败:", err);
      return reply.status(500).send({ success: false, error: String(err) });
    }
  });

  // 静态资源：public 目录与项目 data 目录
  await app.register(staticPlugin, {
    root: path.join(__dirname, "../public"),
    prefix: "/",
  });
  await app.register(staticPlugin, {
    root: path.join(__dirname, "../../data"),
    prefix: "/data",
    decorateReply: false,
  });

  await app.listen({ port: config.port, host: "0.0.0.0" });
  console.log(`销销 TS 服务已启动: http://0.0.0.0:${config.port}`);
}

main().catch((err) => {
  app.log.error(err);
  process.exit(1);
});
