import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { config } from "../config.ts";

const LEGACY_PREFIXES = ["/wechat_kb", "/api/wechat_kb"];

export async function legacyProxyRoutes(app: FastifyInstance) {
  app.get("/api/wechat_kb/*", proxyToPython);
  app.post("/api/wechat_kb/*", proxyToPython);
}

async function proxyToPython(req: FastifyRequest, reply: FastifyReply) {
  const url = req.raw.url ?? "/";
  const target = new URL(url, config.pythonFallbackUrl);

  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (value === undefined) continue;
    if (["host", "connection", "content-length"].includes(key.toLowerCase())) continue;
    if (Array.isArray(value)) {
      for (const v of value) headers.append(key, v);
    } else {
      headers.set(key, value);
    }
  }
  headers.set("host", target.host);

  try {
    const response = await fetch(target.toString(), {
      method: req.method,
      headers,
      body: req.method === "POST" || req.method === "PUT" ? (req.body as RequestInit["body"]) : undefined,
    });

    reply.status(response.status);
    response.headers.forEach((value, key) => {
      if (["content-encoding", "transfer-encoding"].includes(key.toLowerCase())) return;
      reply.header(key, value);
    });

    const body = await response.arrayBuffer();
    reply.send(Buffer.from(body));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    req.log.error(`代理到 Python 遗留服务失败: ${message}`);
    reply.status(502).send({ error: "Python 遗留服务不可达", detail: message });
  }
}
