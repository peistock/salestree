import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const UPLOAD_ROOT = path.resolve(__dirname, "../..", "data", "uploads");

const MAX_WRITE_SIZE = 10 * 1024 * 1024; // 10 MB

const writeFileSchema = Type.Object({
  path: Type.String({
    description:
      "文件保存路径，必须以 /data/uploads/ 开头，例如 /data/uploads/sales_001/thread_xxx/名校堂包投方案.html",
  }),
  content: Type.String({
    description: "文件内容，例如 HTML、Markdown、JSON、TXT 等文本内容",
  }),
});

function resolveSafeFilePath(inputPath: string): string {
  let normalized = inputPath.trim();
  if (normalized.startsWith("/data/uploads/")) {
    normalized = normalized.slice("/data/uploads/".length);
  } else if (normalized.startsWith("data/uploads/")) {
    normalized = normalized.slice("data/uploads/".length);
  }

  const target = path.resolve(UPLOAD_ROOT, normalized);
  const relative = path.relative(UPLOAD_ROOT, target);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("非法文件路径，只能写入 /data/uploads/ 目录下");
  }
  return target;
}

export const writeFileTool: AgentTool<typeof writeFileSchema> = {
  name: "write_file",
  label: "Write File",
  description:
    "将文本内容写入本地文件，用于生成 HTML 网页 PPT、Markdown 报告、JSON 数据等可下载产出物。路径必须以 /data/uploads/ 开头。",
  parameters: writeFileSchema,
  async execute(_toolCallId, params: Static<typeof writeFileSchema>) {
    const filePath = params.path;
    const content = params.content;

    try {
      if (Buffer.byteLength(content, "utf-8") > MAX_WRITE_SIZE) {
        return {
          content: [
            {
              type: "text" as const,
              text: `文件内容超过 ${MAX_WRITE_SIZE / 1024 / 1024}MB 限制，未写入：${filePath}`,
            },
          ],
          details: {},
        };
      }

      const target = resolveSafeFilePath(filePath);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, content, "utf-8");

      const stat = fs.statSync(target);
      const publicUrl = filePath.startsWith("/data/uploads/")
        ? filePath
        : `/data/uploads/${path.relative(UPLOAD_ROOT, target)}`;

      return {
        content: [
          {
            type: "text" as const,
            text: `文件已保存：${publicUrl}（${stat.size} 字节）`,
          },
        ],
        details: {
          name: path.basename(target),
          url: publicUrl,
          size: stat.size,
        },
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text" as const, text: `写入文件失败：${message}` }],
        details: {},
      };
    }
  },
};
