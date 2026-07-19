import type { FastifyInstance } from "fastify";
import { saveUpload } from "../utils/fileStorage.ts";

function streamToBuffer(stream: NodeJS.ReadableStream): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    stream.on("data", (chunk: Buffer) => chunks.push(chunk));
    stream.on("end", () => resolve(Buffer.concat(chunks)));
    stream.on("error", reject);
  });
}

export async function uploadRoutes(app: FastifyInstance) {
  app.post("/api/upload", async (request, reply) => {
    const userId = ((request.query as any)?.user_id as string)?.trim() || "web_user";
    const threadId = ((request.query as any)?.thread_id as string)?.trim() || "default";

    try {
      const parts = request.files();
      const uploaded: Array<{ name: string; url: string; mimeType: string; size: number }> = [];

      for await (const part of parts) {
        if (!part.file) continue;
        const buffer = await streamToBuffer(part.file);
        const stored = saveUpload(buffer, part.filename || "unnamed", part.mimetype, userId, threadId);
        uploaded.push({
          name: stored.name,
          url: stored.url,
          mimeType: stored.mimeType,
          size: stored.size,
        });
      }

      return reply.send({ success: true, files: uploaded });
    } catch (err) {
      console.error("[upload] 失败:", err);
      return reply.status(400).send({
        success: false,
        error: err instanceof Error ? err.message : "上传失败",
      });
    }
  });
}
