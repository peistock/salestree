import type { FastifyInstance } from "fastify";
import { AgentSession, type OutgoingMessage } from "../agent/Session.ts";

interface ClientMessage {
  user_id?: string;
  message?: string;
  action?: "hello" | "stop";
}

const activeSessions = new Map<string, AgentSession>();

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
        send({ type: "status", message: "已连接" });
        return;
      }

      const message = data.message?.trim();
      if (!message) {
        send({ type: "error", message: "消息不能为空" });
        return;
      }

      // 串行：同一个 user_id 只保留最新会话，旧的中止
      activeSessions.get(userId)?.abort();
      const session = new AgentSession(userId);
      activeSessions.set(userId, session);

      send({ type: "status", message: "销销正在思考…" });
      const result = await session.run(message, send);
      send(result);
      activeSessions.delete(userId);
    });

    socket.on("close", () => {
      // 清理由后续连接处理
    });
  });
}
