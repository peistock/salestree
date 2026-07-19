import type { FastifyInstance } from "fastify";
import fs from "node:fs";
import path from "node:path";
import { UPLOAD_ROOT, resolveUploadPath } from "../utils/fileStorage.ts";

export async function editorSaveRoutes(app: FastifyInstance) {
  app.post("/api/editor/save", async (request, reply) => {
    const body = request.body as { url?: string; html?: string };
    const url = body?.url?.trim();
    const html = body?.html;

    if (!url || typeof html !== "string") {
      return reply.status(400).send({ success: false, error: "缺少 url 或 html" });
    }

    // Only allow saving under /data/uploads/
    if (!url.startsWith("/data/uploads/")) {
      return reply.status(403).send({ success: false, error: "只允许保存 /data/uploads/ 下的文件" });
    }

    // Reject non-HTML files at the API level
    const lowerUrl = url.toLowerCase();
    if (!lowerUrl.endsWith(".html") && !lowerUrl.endsWith(".htm")) {
      return reply.status(400).send({ success: false, error: "只支持保存 HTML 文件" });
    }

    const relativePath = url.replace(/^\/data\/uploads\//, "").replace(/\//g, path.sep);
    const targetPath = resolveUploadPath(relativePath);
    const resolvedTarget = path.resolve(targetPath);
    const resolvedRoot = path.resolve(UPLOAD_ROOT);

    if (!resolvedTarget.startsWith(resolvedRoot + path.sep) && resolvedTarget !== resolvedRoot) {
      return reply.status(403).send({ success: false, error: "非法文件路径" });
    }

    try {
      fs.writeFileSync(resolvedTarget, html, "utf-8");
      const stat = fs.statSync(resolvedTarget);
      return reply.send({ success: true, url, size: stat.size });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[editor/save] 保存失败:", message);
      return reply.status(500).send({ success: false, error: `保存失败：${message}` });
    }
  });
}
