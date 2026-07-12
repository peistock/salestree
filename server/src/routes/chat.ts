import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";

export async function chatPageRoutes(app: FastifyInstance) {
  app.get("/chat", (_req: FastifyRequest, reply: FastifyReply) => {
    reply.redirect("/chat.html");
  });
}
