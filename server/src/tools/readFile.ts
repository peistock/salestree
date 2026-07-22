import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "../../..");
const UPLOAD_ROOT = path.resolve(PROJECT_ROOT, "data", "uploads");
const PROJECTS_ROOT = path.resolve(PROJECT_ROOT, "data", "projects");
const SKILLS_ROOT = path.resolve(PROJECT_ROOT, "data", "skills");

const MAX_READ_SIZE = 2 * 1024 * 1024; // 2 MB

const readFileSchema = Type.Object({
  path: Type.String({ description: "文件路径，例如 /data/uploads/web_user/default/xxx.html、/data/projects/抖音商城内广_lifecycle.md 或 /data/skills/guizang-ppt-skill/SKILL.md" }),
});

function resolveSafeFilePath(inputPath: string): string {
  const normalized = inputPath.trim();

  // /data/skills/... → SKILLS_ROOT
  if (normalized.startsWith("/data/skills/") || normalized.startsWith("data/skills/")) {
    const suffix = normalized.startsWith("/data/skills/")
      ? normalized.slice("/data/skills/".length)
      : normalized.slice("data/skills/".length);
    const target = path.resolve(SKILLS_ROOT, suffix);
    const relative = path.relative(SKILLS_ROOT, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("非法文件路径");
    }
    return target;
  }

  // /data/projects/... → PROJECTS_ROOT
  if (normalized.startsWith("/data/projects/") || normalized.startsWith("data/projects/")) {
    const suffix = normalized.startsWith("/data/projects/")
      ? normalized.slice("/data/projects/".length)
      : normalized.slice("data/projects/".length);
    const target = path.resolve(PROJECTS_ROOT, suffix);
    const relative = path.relative(PROJECTS_ROOT, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("非法文件路径");
    }
    return target;
  }

  // /data/uploads/... → UPLOAD_ROOT
  let uploadsNormalized = normalized;
  if (uploadsNormalized.startsWith("/data/uploads/")) {
    uploadsNormalized = uploadsNormalized.slice("/data/uploads/".length);
  } else if (uploadsNormalized.startsWith("data/uploads/")) {
    uploadsNormalized = uploadsNormalized.slice("data/uploads/".length);
  }

  const target = path.resolve(UPLOAD_ROOT, uploadsNormalized);
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
    "读取本地文件内容。支持 /data/uploads/ 下的用户上传文件、/data/projects/ 下的项目报告/分析文件，以及 /data/skills/ 下的 Skill 文件。支持文本类文件（HTML、TXT、MD、JSON、CSV 等）。",
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
