import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const UPLOAD_ROOT = path.resolve(__dirname, "../../..", "data", "uploads");

const MAX_READ_SIZE = 2 * 1024 * 1024; // 2 MB

const readFileSchema = Type.Object({
  path: Type.String({ description: "文件路径，例如 /data/uploads/web_user/default/xxx.html" }),
});

function resolveSafeFilePath(inputPath: string): string {
  // Accept /data/uploads/... or data/uploads/... or absolute paths under project
  let normalized = inputPath.trim();
  if (normalized.startsWith("/data/uploads/")) {
    normalized = normalized.slice("/data/uploads/".length);
  } else if (normalized.startsWith("data/uploads/")) {
    normalized = normalized.slice("data/uploads/".length);
  }

  // Prevent directory traversal
  const target = path.resolve(UPLOAD_ROOT, normalized);
  const relative = path.relative(UPLOAD_ROOT, target);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("非法文件路径");
  }
  return target;
}

export const readFileTool: AgentTool<typeof readFileSchema> = {
  name: "read_file",
  label: "Read File",
  description:
    "读取用户上传的本地文件内容。支持文本类文件（HTML、TXT、MD、JSON、CSV 等）以及从 /data/uploads/ 路径访问的文件。",
  parameters: readFileSchema,
  async execute(_toolCallId, params: Static<typeof readFileSchema>) {
    const filePath = params.path;
    try {
      const target = resolveSafeFilePath(filePath);
      if (!fs.existsSync(target)) {
        return {
          content: [{ type: "text" as const, text: `文件不存在：${filePath}` }],
          details: {},
        };
      }
      const stat = fs.statSync(target);
      if (!stat.isFile()) {
        return {
          content: [{ type: "text" as const, text: `路径不是文件：${filePath}` }],
          details: {},
        };
      }
      if (stat.size > MAX_READ_SIZE) {
        return {
          content: [
            {
              type: "text" as const,
              text: `文件超过 ${MAX_READ_SIZE / 1024 / 1024}MB 限制，无法直接读取：${filePath}`,
            },
          ],
          details: {},
        };
      }
      const content = fs.readFileSync(target, "utf-8");
      return {
        content: [
          {
            type: "text" as const,
            text: `文件：${filePath}\n\n${content}`,
          },
        ],
        details: {},
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text" as const, text: `读取文件失败：${message}` }],
        details: {},
      };
    }
  },
};
