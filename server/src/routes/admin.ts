import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import { config } from "../config.ts";
import { UsageStore, type UsageFilters } from "../db/usageStore.ts";

const usageStore = new UsageStore();

function parseDateParam(value: unknown): string | undefined {
  if (typeof value !== "string" || value.trim() === "") return undefined;
  return value.trim();
}

function parseNumberParam(value: unknown, defaultValue: number): number {
  if (typeof value !== "string") return defaultValue;
  const n = Number(value);
  return Number.isNaN(n) ? defaultValue : n;
}

function buildFilters(req: FastifyRequest): UsageFilters {
  const query = req.query as Record<string, unknown>;
  return {
    orgId: typeof query.org_id === "string" ? query.org_id.trim() : undefined,
    userId: typeof query.user_id === "string" ? query.user_id.trim() : undefined,
    threadId: typeof query.thread_id === "string" ? query.thread_id.trim() : undefined,
    startDate: parseDateParam(query.start_date),
    endDate: parseDateParam(query.end_date),
    limit: parseNumberParam(query.limit, 20),
    offset: parseNumberParam(query.offset, 0),
  };
}

export async function adminRoutes(app: FastifyInstance) {
  app.addHook("preHandler", async (req: FastifyRequest, reply: FastifyReply) => {
    const key = req.headers["x-admin-key"];
    if (!config.admin.apiKey || key !== config.admin.apiKey) {
      return reply.status(401).send({ success: false, error: "Unauthorized" });
    }
  });

  app.get("/api/admin/usage", async (req: FastifyRequest, reply: FastifyReply) => {
    try {
      const filters = buildFilters(req);
      const result = await usageStore.listUsage(filters);
      return reply.send({ success: true, ...result });
    } catch (err) {
      console.error("[admin] 查询用量失败:", err);
      return reply.status(500).send({ success: false, error: String(err) });
    }
  });

  app.get("/api/admin/usage/summary", async (req: FastifyRequest, reply: FastifyReply) => {
    try {
      const filters = buildFilters(req);
      const summary = await usageStore.summarizeUsage(filters);
      return reply.send({ success: true, summary });
    } catch (err) {
      console.error("[admin] 汇总用量失败:", err);
      return reply.status(500).send({ success: false, error: String(err) });
    }
  });

  app.get("/api/admin/orgs", async (_req: FastifyRequest, reply: FastifyReply) => {
    try {
      const orgs = await usageStore.listOrganizations();
      return reply.send({ success: true, orgs });
    } catch (err) {
      console.error("[admin] 查询组织失败:", err);
      return reply.status(500).send({ success: false, error: String(err) });
    }
  });

  app.patch(
    "/api/admin/orgs/:org_id/quota",
    async (req: FastifyRequest, reply: FastifyReply) => {
      try {
        const { org_id } = req.params as { org_id?: string };
        const body = req.body as { monthly_token_quota?: unknown };
        if (!org_id || typeof org_id !== "string") {
          return reply.status(400).send({ success: false, error: "缺少 org_id" });
        }
        const quota = Number(body?.monthly_token_quota);
        if (!Number.isFinite(quota) || quota < 0) {
          return reply
            .status(400)
            .send({ success: false, error: "monthly_token_quota 必须是大于等于 0 的数字" });
        }
        const updated = await usageStore.updateQuota(org_id, Math.floor(quota));
        if (!updated) {
          return reply.status(404).send({ success: false, error: "组织不存在" });
        }
        return reply.send({ success: true, org_id, monthly_token_quota: Math.floor(quota) });
      } catch (err) {
        console.error("[admin] 更新配额失败:", err);
        return reply.status(500).send({ success: false, error: String(err) });
      }
    },
  );
}
