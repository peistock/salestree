import "dotenv/config";
import Fastify from "fastify";
import websocket from "@fastify/websocket";
import staticPlugin from "@fastify/static";
import path from "path";
import { fileURLToPath } from "url";
import { config } from "./config.ts";
import { chatPageRoutes } from "./routes/chat.ts";
import { healthRoutes } from "./routes/health.ts";
import { wsChatRoutes } from "./routes/ws.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = Fastify({ logger: true });

async function main() {
  await app.register(websocket);
  await app.register(healthRoutes);
  await app.register(wsChatRoutes);
  await app.register(chatPageRoutes);

  // 兼容旧页面调用的 API 占位
  app.get("/api/users", async (_req, reply) => {
    return reply.send({ users: [{ user_id: "sales_001", name: "李明" }, { user_id: "sales_002", name: "王芳" }] });
  });
  app.get("/api/task_history", async (_req, reply) => reply.send({ tasks: [] }));
  app.get("/api/latest_task", async (_req, reply) => reply.send({ task: null }));
  app.get("/api/task_detail", async (_req, reply) => reply.send({ task: null }));
  app.post("/api/delete_task", async (_req, reply) => reply.send({ success: true }));
  app.post("/api/rename_task", async (_req, reply) => reply.send({ success: true }));
  app.post("/api/new_thread", async (_req, reply) => reply.send({ success: true, thread_id: `thread_${Date.now()}` }));

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
