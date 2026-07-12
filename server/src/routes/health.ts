import type { FastifyInstance } from "fastify";

export async function healthRoutes(app: FastifyInstance) {
  app.get("/health", async (_req, reply) => {
    reply.send({ status: "ok", service: "xiaoxiaoshu-server" });
  });
}
