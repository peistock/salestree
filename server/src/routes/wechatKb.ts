import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { execSync } from "child_process";
import { config } from "../config.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const OUTPUT_DIR = path.resolve(__dirname, "../../", config.wechatKbOutputDir);
const DIGEST_HTML = path.join(OUTPUT_DIR, "digest.html");
const ASSETS_DIR = path.join(OUTPUT_DIR, "assets");
const RENDER_SCRIPT = path.resolve(__dirname, "../../../third_party/wechat-digest-skill/render_html.py");

function ensureDigestHtml(): void {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  fs.mkdirSync(ASSETS_DIR, { recursive: true });
  if (fs.existsSync(DIGEST_HTML)) return;
  if (!fs.existsSync(RENDER_SCRIPT)) {
    throw new Error(`未找到渲染脚本: ${RENDER_SCRIPT}`);
  }
  try {
    execSync(`python3 "${RENDER_SCRIPT}"`, { cwd: path.dirname(RENDER_SCRIPT), stdio: "inherit" });
  } catch (err) {
    console.error("生成 digest.html 失败:", err);
    throw err;
  }
}

export async function wechatKbRoutes(app: FastifyInstance) {
  ensureDigestHtml();

  app.get("/wechat_kb", (_req: FastifyRequest, reply: FastifyReply) => {
    reply.header("Cache-Control", "no-cache, no-store, must-revalidate");
    reply.header("Pragma", "no-cache");
    reply.header("Expires", "0");
    reply.sendFile("digest.html", OUTPUT_DIR);
  });

  await app.register(import("@fastify/static"), {
    root: ASSETS_DIR,
    prefix: "/wechat_kb/assets/",
    decorateReply: false,
  });
}
