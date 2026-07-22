import "dotenv/config";
import Fastify from "fastify";
import websocket from "@fastify/websocket";
import staticPlugin from "@fastify/static";
import compress from "@fastify/compress";
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
import { bidMonitorRoutes } from "./routes/bidMonitor.ts";
import { uploadRoutes } from "./routes/upload.ts";
import { editorSaveRoutes } from "./routes/editorSave.ts";
import { adminRoutes } from "./routes/admin.ts";
import { ConversationStore } from "./memory/ConversationStore.ts";
import { UserStore } from "./db/userStore.ts";

const conversationStore = new ConversationStore();
const userStore = new UserStore();

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
  await app.register(compress, { global: true });
  await app.register(multipart, { limits: { fileSize: 10 * 1024 * 1024 } });
  await app.register(healthRoutes);
  await app.register(adminRoutes);
  await app.register(uploadRoutes);
  await app.register(editorSaveRoutes);
  await app.register(wsChatRoutes);
  await app.register(wechatKbRoutes);
  await app.register(companyLeadsRoutes);
  await app.register(salesPolicyRoutes);
  await app.register(bidMonitorRoutes);
  await app.register(legacyProxyRoutes);
  await app.register(chatPageRoutes);

  // 返回活跃用户列表（与 dashboard 同源）
  app.get("/api/users", async (_req, reply) => {
    try {
      const users = await userStore.listUsers({ activeOnly: true });
      return reply.send(users.map((u) => ({
        user_id: u.userId,
        name: u.name,
        role: u.role,
        status: u.status,
        llm_provider: u.llmConfig?.enabled ? (u.llmConfig.provider || "custom") : undefined,
        llm_model: u.llmConfig?.enabled ? (u.llmConfig.modelDaily || undefined) : undefined,
      })));
    } catch (err) {
      console.error("[api] /api/users error:", err);
      return reply.send([{ user_id: "web_user", name: "默认用户" }]);
    }
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
      if (await conversationStore.getProjectName(threadId)) {
        return reply.send({ success: false, error: "项目频道不支持删除" });
      }
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
      if (await conversationStore.getProjectName(threadId)) {
        return reply.send({ success: false, error: "项目频道不支持重命名" });
      }
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
      if (await conversationStore.getProjectName(threadId)) {
        return reply.send({ success: false, error: "项目频道无需切换，直接打开即可" });
      }
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

  const wechatPath = path.join(__dirname, "../../data/projects/wechat_accounts.json");
  try {
    const raw = fs.readFileSync(wechatPath, "utf-8");
    const config = JSON.parse(raw);
    for (const [name, cfg] of Object.entries(config.accounts || {})) {
      if (!seen.has(name)) {
        const source = (cfg as any)?.source || "wechat";
        accounts.push({ name, source });
        seen.add(name);
      }
    }
  } catch (err) {
    console.error("[project_accounts] 读取微信账户失败:", err);
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

function getProjectAccountSource(name: string): "feishu" | "dingtalk" | "wechat" | "hybrid" | null {
  const accounts = loadProjectAccounts();
  const found = accounts.find((a) => a.name === name);
  return found ? (found.source as "feishu" | "dingtalk" | "wechat" | "hybrid") : null;
}

  // 项目看板：读取群分析结果
  app.get("/api/project_panel", async (req, reply) => {
    const account = (((req.query as any).account as string)?.trim()) || "环平保险";
    const safeAccount = account.replace(/[^a-zA-Z0-9一-龥]/g, "_");
    const analysisPath = path.join(__dirname, "../../data/projects", `${safeAccount}_analysis.json`);
    try {
      const data = fs.readFileSync(analysisPath, "utf-8");
      const parsed = JSON.parse(data);
      // 带上分析文件的生成时间，前端据此展示真实的数据新鲜度
      parsed._generated_at = fs.statSync(analysisPath).mtimeMs;
      return reply.send(parsed);
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

  // 项目频道列表：在管项目 ∪ 当前用户可见的已创建频道
  app.get("/api/project_channels", async (req, reply) => {
    const userId = ((req.query as any).user_id as string)?.trim() || "web_user";
    try {
      const accounts = loadProjectAccounts();
      const channels = await conversationStore.listProjectThreadsForUser(userId);
      const byProject = new Map(channels.map((c) => [c.project_name, c]));
      const items = accounts.map((a) => {
        const ch = byProject.get(a.name);
        return {
          project_name: a.name,
          source: a.source,
          thread_id: ch?.thread_id ?? null,
          message_count: ch?.message_count ?? 0,
          result_preview: ch?.result_preview ?? "",
          updated_at: ch ? Math.floor(new Date(ch.updated_at).getTime() / 1000) : null,
        };
      });
      // 自定义频道：不在在管项目里、但有频道线程的，追加在末尾（带创建者与成员信息）
      for (const ch of channels) {
        if (ch.project_name && !accounts.some((a) => a.name === ch.project_name)) {
          const members = await conversationStore.getChannelMembers(ch.thread_id);
          items.push({
            project_name: ch.project_name,
            source: "custom",
            thread_id: ch.thread_id,
            message_count: ch.message_count,
            result_preview: ch.result_preview ?? "",
            updated_at: Math.floor(new Date(ch.updated_at).getTime() / 1000),
            created_by: ch.user_id,
            linked_project: ch.linked_project ?? null,
            members,
          } as any);
        }
      }
      return reply.send({ channels: items });
    } catch (err) {
      console.error("[api] project_channels error:", err);
      return reply.send({ channels: [] });
    }
  });

  // 频道成员管理：仅创建者可加人/移人
  app.post("/api/channel_members", async (req, reply) => {
    const q = req.query as any;
    const userId = (q.user_id as string)?.trim();
    const threadId = (q.thread_id as string)?.trim();
    const member = (q.member as string)?.trim();
    const action = (q.action as string)?.trim();
    if (!userId || !threadId || !member || !["add", "remove"].includes(action)) {
      return reply.status(400).send({ success: false, error: "参数不完整（user_id/thread_id/member/action=add|remove）" });
    }
    try {
      const thread = await conversationStore.getThreadById(threadId);
      if (!thread?.project_name) return reply.send({ success: false, error: "频道不存在" });
      if (thread.user_id !== userId) return reply.send({ success: false, error: "只有频道创建者可以管理成员" });
      if (action === "add") {
        await conversationStore.addChannelMember(threadId, member, userId);
      } else {
        await conversationStore.removeChannelMember(threadId, member);
      }
      return reply.send({ success: true, members: await conversationStore.getChannelMembers(threadId) });
    } catch (err) {
      console.error("[api] channel_members error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  // 频道转让：仅创建者，接收方自动补进成员
  app.post("/api/channel_transfer", async (req, reply) => {
    const q = req.query as any;
    const userId = (q.user_id as string)?.trim();
    const threadId = (q.thread_id as string)?.trim();
    const newOwner = (q.new_owner as string)?.trim();
    if (!userId || !threadId || !newOwner) {
      return reply.status(400).send({ success: false, error: "参数不完整（user_id/thread_id/new_owner）" });
    }
    try {
      const thread = await conversationStore.getThreadById(threadId);
      if (!thread?.project_name) return reply.send({ success: false, error: "频道不存在" });
      if (thread.user_id !== userId) return reply.send({ success: false, error: "只有频道创建者可以转让" });
      if (newOwner === userId) return reply.send({ success: false, error: "已经是创建者" });
      await conversationStore.transferChannelOwnership(threadId, newOwner);
      // 受限频道里，接收方必须在成员中（否则他自己也进不来）
      const members = await conversationStore.getChannelMembers(threadId);
      if (members.length > 0 && !members.includes(newOwner)) {
        await conversationStore.addChannelMember(threadId, newOwner, userId);
      }
      return reply.send({ success: true });
    } catch (err) {
      console.error("[api] channel_transfer error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  // 删除频道：仅创建者，且仅限自定义频道（在管项目频道是基础设施，不可删）
  app.post("/api/delete_channel", async (req, reply) => {
    const q = req.query as any;
    const userId = (q.user_id as string)?.trim();
    const threadId = (q.thread_id as string)?.trim();
    if (!userId || !threadId) {
      return reply.status(400).send({ success: false, error: "参数不完整（user_id/thread_id）" });
    }
    try {
      const thread = await conversationStore.getThreadById(threadId);
      if (!thread?.project_name) return reply.send({ success: false, error: "频道不存在" });
      if (thread.user_id !== userId) return reply.send({ success: false, error: "只有频道创建者可以删除" });
      if (loadProjectAccounts().some((a) => a.name === thread.project_name)) {
        return reply.send({ success: false, error: "在管项目频道不可删除" });
      }
      await conversationStore.deleteChannel(threadId);
      return reply.send({ success: true });
    } catch (err) {
      console.error("[api] delete_channel error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  // 创建自定义频道：创建者选成员、选关联项目
  app.post("/api/create_channel", async (req, reply) => {
    const q = req.query as any;
    const userId = (q.user_id as string)?.trim() || "web_user";
    const name = (q.name as string)?.trim();
    const project = (q.project as string)?.trim() || null;
    const members = ((q.members as string) || "")
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!name) return reply.status(400).send({ success: false, error: "缺少频道名称" });
    if (name.length > 30) return reply.send({ success: false, error: "频道名太长（30 字以内）" });
    try {
      const threadId = await conversationStore.createCustomChannel(name, userId, project);
      // 成员不含创建者时把创建者补进去（创建者本身按 threads.user_id 永远可见，这里仅作记录）
      await conversationStore.setChannelMembers(threadId, members, userId);
      return reply.send({ success: true, thread_id: threadId });
    } catch (err: any) {
      if (err?.code === "23505") {
        return reply.send({ success: false, error: `频道「${name}」已存在` });
      }
      console.error("[api] create_channel error:", err);
      return reply.send({ success: false, error: String(err) });
    }
  });

  // 频道详情：全员可读，但必须确实是项目频道（防越权读他人私有线程）
  app.get("/api/channel_detail", async (req, reply) => {
    const threadId = ((req.query as any).thread_id as string)?.trim();
    if (!threadId) return reply.status(400).send({ error: "缺少 thread_id" });
    try {
      const t = await conversationStore.getThreadById(threadId);
      if (!t || !t.project_name) {
        return reply.status(403).send({ error: "不是项目频道" });
      }
      const messages = await conversationStore.getThreadMessages(threadId, 100);
      return reply.send({
        channel: {
          id: t.thread_id,
          project_name: t.project_name,
          message_count: t.message_count,
          result_preview: t.result_preview || "",
          files: (t.files_json || []).map((f: any) => ({ name: f.name || String(f), path: f.url || "" })),
        },
        messages: messages.map((m) => ({
          role: m.role,
          content: m.content,
          user_id: m.user_id,
          user_name: m.user_name,
          created_at: m.created_at,
        })),
      });
    } catch (err) {
      console.error("[api] channel_detail error:", err);
      return reply.status(500).send({ error: String(err) });
    }
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
      const sourceLabel =
        source === "dingtalk" ? "钉钉" :
        source === "wechat" ? "微信" :
        source === "hybrid" ? "混合来源" : "飞书";
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
