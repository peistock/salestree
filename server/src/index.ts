import "dotenv/config";
import Fastify from "fastify";
import websocket from "@fastify/websocket";
import staticPlugin from "@fastify/static";
import multipart from "@fastify/multipart";
import path from "path";
import fs from "fs";
import { exec, spawn } from "child_process";
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
import { editorSaveRoutes } from "./routes/editorSave.ts";
import { adminRoutes } from "./routes/admin.ts";
import { ConversationStore } from "./memory/ConversationStore.ts";

const conversationStore = new ConversationStore();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = Fastify({ logger: true });

const WECHAT_ACCOUNTS_FILE = path.join(__dirname, "../../data/wechat_accounts.json");

function loadWechatAccounts(): string[] {
  try {
    const raw = fs.readFileSync(WECHAT_ACCOUNTS_FILE, "utf-8");
    const data = JSON.parse(raw);
    const accounts: string[] = (data.accounts || [])
      .map((s: unknown) => String(s).trim())
      .filter(Boolean) as string[];
    return [...new Set(accounts)];
  } catch {
    return [];
  }
}

function saveWechatAccounts(accounts: string[]): void {
  try {
    fs.mkdirSync(path.dirname(WECHAT_ACCOUNTS_FILE), { recursive: true });
    const cleaned = [...new Set(accounts.map((s) => s.trim()).filter(Boolean))];
    fs.writeFileSync(WECHAT_ACCOUNTS_FILE, JSON.stringify({ accounts: cleaned }, null, 2) + "\n");
  } catch (err) {
    console.error("[wechat_accounts] 保存账号列表失败:", err);
  }
}

function getKbLatestDate(scriptDir: string, allowedAccounts?: Set<string>): string | undefined {
  try {
    const kbPath = path.join(scriptDir, "output", "knowledge_base.json");
    const raw = fs.readFileSync(kbPath, "utf-8");
    const data = JSON.parse(raw);
    let articles = Object.values(data.articles || {}) as any[];
    if (allowedAccounts && allowedAccounts.size) {
      articles = articles.filter((a) => allowedAccounts.has(String(a.account || "").trim()));
    }
    if (!articles.length) return undefined;
    const dates = articles
      .map((a) => String(a.publishDate || "").trim())
      .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d));
    if (!dates.length) return undefined;
    return dates.sort().pop();
  } catch {
    return undefined;
  }
}

async function main() {
  await app.register(websocket);
  await app.register(multipart, { limits: { fileSize: 10 * 1024 * 1024 } });
  await app.register(healthRoutes);
  await app.register(adminRoutes);
  await app.register(uploadRoutes);
  await app.register(editorSaveRoutes);
  await app.register(wsChatRoutes);
  await app.register(wechatKbRoutes);
  await app.register(companyLeadsRoutes);
  await app.register(salesPolicyRoutes);
  await app.register(legacyProxyRoutes);
  await app.register(chatPageRoutes);

  // 兼容旧页面调用的 API 占位
  app.get("/api/users", async (_req, reply) => {
    return reply.send([{ user_id: "sales_001", name: "陈沛" }, { user_id: "sales_002", name: "亿树" }]);
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
        files: (t.files_json || []).map((f: any) => ({ name: f.name || String(f), path: f.url || "" })),
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
        files: (t.files_json || []).map((f: any) => ({ name: f.name || String(f), path: f.url || "" })),
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

function loadProjectAccounts(): { name: string; source: string }[] {
  const accounts: { name: string; source: string }[] = [];
  const seen = new Set<string>();

  const feishuPath = path.join(__dirname, "../../data/projects/feishu_accounts.json");
  try {
    const raw = fs.readFileSync(feishuPath, "utf-8");
    const config = JSON.parse(raw);
    for (const name of Object.keys(config.accounts || {})) {
      if (!seen.has(name)) {
        accounts.push({ name, source: "feishu" });
        seen.add(name);
      }
    }
  } catch (err) {
    console.error("[project_accounts] 读取飞书账户失败:", err);
  }

  const dingtalkPath = path.join(__dirname, "../../data/projects/dingtalk_accounts.json");
  try {
    const raw = fs.readFileSync(dingtalkPath, "utf-8");
    const config = JSON.parse(raw);
    for (const name of Object.keys(config.accounts || {})) {
      if (!seen.has(name)) {
        accounts.push({ name, source: "dingtalk" });
        seen.add(name);
      }
    }
  } catch (err) {
    console.error("[project_accounts] 读取钉钉账户失败:", err);
  }

  const hybridPath = path.join(__dirname, "../../data/projects/hybrid_accounts.json");
  try {
    const raw = fs.readFileSync(hybridPath, "utf-8");
    const config = JSON.parse(raw);
    for (const name of Object.keys(config.accounts || {})) {
      if (!seen.has(name)) {
        accounts.push({ name, source: "hybrid" });
        seen.add(name);
      }
    }
  } catch (err) {
    console.error("[project_accounts] 读取混合账户失败:", err);
  }

  return accounts;
}

function getProjectAccountSource(name: string): "feishu" | "dingtalk" | "hybrid" | null {
  const accounts = loadProjectAccounts();
  const found = accounts.find((a) => a.name === name);
  return found ? (found.source as "feishu" | "dingtalk" | "hybrid") : null;
}

  // 项目看板：读取群分析结果
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
    const accounts = loadProjectAccounts();
    if (accounts.length === 0) {
      return reply.send({ accounts: [{ name: "环平保险", source: "feishu" }] });
    }
    return reply.send({ accounts });
  });

  // 项目看板：手动拉取最新群消息并重新分析
  app.post("/api/refresh_project_panel", async (req, reply) => {
    const account = (((req.query as any).account as string)?.trim()) || "环平保险";
    const source = getProjectAccountSource(account) || "feishu";
    const execAsync = promisify(exec);
    const scriptsDir = path.join(__dirname, "../../scripts", source);
    const pullScript = path.join(scriptsDir, "pull_account.py");
    const analyzeScript = path.join(scriptsDir, "analyze_account.py");

    if (!fs.existsSync(pullScript) || !fs.existsSync(analyzeScript)) {
      return reply.status(500).send({ error: "刷新脚本不存在" });
    }

    reply.send({ success: true, message: `正在刷新 ${account} 的项目看板，请稍后…` });

    try {
      const sourceLabel = source === "dingtalk" ? "钉钉" : source === "hybrid" ? "混合来源" : "飞书";
      console.log(`[refresh_project_panel] 开始拉取 ${account} 的${sourceLabel}消息…`);
      const { stderr: pullErr } = await execAsync(
        `python3 "${pullScript}" --account "${account}"`,
        { timeout: 300_000 },
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

  // 资讯看板账号列表
  app.get("/api/news_accounts", async (_req, reply) => {
    const accounts = loadWechatAccounts();
    return reply.send({ accounts: accounts.map((name) => ({ name })) });
  });

  // 资讯看板：采集 → 分析 → 重新渲染 digest.html
  app.post("/api/refresh_news_panel", async (req, reply) => {
    const body = (req.body || {}) as any;
    const q = req.query as any;

    let accounts: string[] = [];
    const rawAccounts = body.accounts ?? q.accounts;
    if (Array.isArray(rawAccounts)) {
      accounts = rawAccounts.map(String).map((s) => s.trim()).filter(Boolean);
    } else if (typeof rawAccounts === "string") {
      accounts = rawAccounts.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
    }
    if (accounts.length === 0) {
      accounts = loadWechatAccounts();
    }

    const scriptDir = path.resolve(__dirname, "../../third_party/wechat-digest-skill");
    const allowedAccounts = new Set(accounts);

    const since = (body.since ?? q.since)?.trim()
      || getKbLatestDate(scriptDir, allowedAccounts)
      || new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const count = Math.min(
      Math.max(parseInt(body.count ?? q.count ?? "10", 10) || 10, 1),
      50,
    );

    const collector = path.join(scriptDir, "wechat_collector.py");
    const analyzer = path.join(scriptDir, "analyze_kb.py");
    const renderer = path.join(scriptDir, "render_html.py");

    if (!fs.existsSync(collector) || !fs.existsSync(analyzer) || !fs.existsSync(renderer)) {
      return reply.status(500).send({ success: false, error: "采集脚本不完整" });
    }

    // 凭证：优先用请求里带来的，否则复用本地 credentials.json
    const credentialsFile = path.join(scriptDir, "credentials.json");
    let token = String(body.token || "").trim();
    let cookie = String(body.cookie || "").trim();
    if (!token || !cookie) {
      if (fs.existsSync(credentialsFile)) {
        try {
          const existing = JSON.parse(fs.readFileSync(credentialsFile, "utf-8"));
          token = token || String(existing.token || "").trim();
          cookie = cookie || String(existing.cookie || "").trim();
        } catch {
          // ignore
        }
      }
    }
    if (!/^\d+$/.test(token) || !cookie) {
      return reply.status(400).send({
        success: false,
        error: "缺少有效的 token 和 cookie，请在采集配置中填写",
      });
    }

    try {
      let credData: any = {};
      if (fs.existsSync(credentialsFile)) {
        credData = JSON.parse(fs.readFileSync(credentialsFile, "utf-8"));
      }
      credData.token = token;
      credData.cookie = cookie;
      fs.writeFileSync(credentialsFile, JSON.stringify(credData, null, 2) + "\n");
    } catch (err) {
      console.error("[refresh_news_panel] 保存凭证失败:", err);
      return reply.status(500).send({ success: false, error: "保存凭证失败" });
    }

    // 把本次使用的公众号列表持久化
    saveWechatAccounts(accounts);

    // 立即返回，后台执行采集→分析→渲染
    reply.send({
      success: true,
      message: `正在采集 ${accounts.join("、")} 的最近 ${count} 篇文章并重新生成看板，请稍后刷新页面…`,
    });

    const shellQuote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
    const accountArgs = accounts.map(shellQuote).join(" ");
    const cmd = `python3 "${collector}" collect ${accountArgs} --since "${since}" --count ${count} && python3 "${analyzer}" && python3 "${renderer}"`;
    const logPath = path.join(scriptDir, "output", "refresh.log");
    let outFd: number | undefined;
    try {
      outFd = fs.openSync(logPath, "a");
    } catch {
      // ignore
    }

    const child = spawn(
      "/bin/bash",
      ["-c", `set -a && source /Users/cpp/salestree/.env && set +a && ${cmd}`],
      {
        cwd: scriptDir,
        detached: true,
        stdio: ["ignore", outFd ?? "ignore", outFd ?? "ignore"],
      },
    );
    child.unref();
    if (outFd !== undefined) {
      try { fs.closeSync(outFd); } catch { /* ignore */ }
    }
    child.on("error", (err) => console.error("[refresh_news_panel] 后台任务失败:", err));
    child.on("exit", (code) => {
      console.log(`[refresh_news_panel] 后台任务结束，退出码 ${code}`);
    });
  });

  // 资讯看板状态：返回 digest.html 最后修改时间，供前端轮询刷新
  app.get("/api/news_status", async (_req, reply) => {
    const scriptDir = path.resolve(__dirname, "../../third_party/wechat-digest-skill");
    const digestPath = path.join(scriptDir, "output", "digest.html");
    try {
      const stat = fs.statSync(digestPath);
      return reply.send({
        ready: true,
        updatedAt: stat.mtime.toISOString(),
        updatedAtTimestamp: stat.mtime.getTime(),
      });
    } catch (err) {
      return reply.status(404).send({ ready: false, error: "digest.html 尚未生成" });
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
