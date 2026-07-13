import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";
import { config } from "../config.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const POLICY_FILE_PATH = path.resolve(
  __dirname,
  "../../",
  process.env.SALES_POLICY_FILE_PATH || config.salesPolicyFilePath || "data/sales_policies.json",
);
const SYNC_SCRIPT = path.resolve(__dirname, "../../../scripts/feishu/sync_sales_policy.py");

interface Sheet {
  id: string;
  name: string;
  effective_date?: string;
  notes?: string;
  header_row_count?: number;
  row_count: number;
  column_count: number;
  rows: string[][];
}

interface PolicyFile {
  updatedAt?: string;
  spreadsheetToken?: string;
  spreadsheetUrl?: string;
  sheets?: Sheet[];
}

function emptyPolicies(): PolicyFile {
  return { updatedAt: new Date().toISOString(), spreadsheetToken: "", spreadsheetUrl: "", sheets: [] };
}

function loadPolicies(): PolicyFile {
  if (!fs.existsSync(POLICY_FILE_PATH)) {
    console.warn(`[salesPolicy] 政策文件不存在: ${POLICY_FILE_PATH}`);
    return emptyPolicies();
  }
  try {
    const raw = fs.readFileSync(POLICY_FILE_PATH, "utf-8");
    const parsed = JSON.parse(raw) as PolicyFile;
    return { ...emptyPolicies(), ...parsed };
  } catch (err) {
    console.error("[salesPolicy] 读取政策文件失败:", err);
    return emptyPolicies();
  }
}

function normalizeText(s: unknown): string {
  return String(s ?? "").trim().toLowerCase();
}

function sheetMatches(sheet: Sheet, q: string): boolean {
  const nq = normalizeText(q);
  if (!nq) return true;
  const haystack = [sheet.name, sheet.notes || ""]
    .concat(sheet.rows.flat())
    .map(normalizeText)
    .join(" ");
  return haystack.includes(nq);
}

function highlightRows(sheet: Sheet, q: string): number[] {
  const nq = normalizeText(q);
  if (!nq) return [];
  const indices: number[] = [];
  sheet.rows.forEach((row, idx) => {
    if (row.some((cell) => normalizeText(cell).includes(nq))) {
      indices.push(idx);
    }
  });
  return indices;
}

export async function salesPolicyRoutes(app: FastifyInstance) {
  app.get("/api/sales_policies", async (req: FastifyRequest, reply: FastifyReply) => {
    const query = req.query as { q?: string; sheet?: string; limit?: string };
    const q = String(query.q || "").trim();
    const sheetName = String(query.sheet || "").trim();
    const limit = parseInt(query.limit || "200", 10) || 200;

    const data = loadPolicies();
    let sheets = (data.sheets || []).filter((s) => {
      if (sheetName && normalizeText(s.name) !== normalizeText(sheetName)) return false;
      return sheetMatches(s, q);
    });

    const resultSheets = sheets.slice(0, limit).map((s) => ({
      id: s.id,
      name: s.name,
      effective_date: s.effective_date,
      notes: s.notes,
      header_row_count: s.header_row_count,
      row_count: s.row_count,
      column_count: s.column_count,
      highlighted_rows: highlightRows(s, q),
      rows: s.rows,
    }));

    return reply.send({
      updatedAt: data.updatedAt,
      spreadsheetUrl: data.spreadsheetUrl,
      totalSheets: (data.sheets || []).length,
      sheetNames: (data.sheets || []).map((s) => s.name),
      q,
      sheets: resultSheets,
    });
  });

  app.post("/api/refresh_sales_policies", async (_req: FastifyRequest, reply: FastifyReply) => {
    if (!fs.existsSync(SYNC_SCRIPT)) {
      return reply.status(500).send({ error: "同步脚本不存在", script: SYNC_SCRIPT });
    }
    try {
      execSync(`python3 "${SYNC_SCRIPT}"`, { cwd: path.dirname(SYNC_SCRIPT), stdio: "pipe" });
      const data = loadPolicies();
      return reply.send({
        success: true,
        message: `已同步 ${data.sheets?.length || 0} 个 sheet`,
        updatedAt: data.updatedAt,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[salesPolicy] 刷新失败:", err);
      return reply.status(500).send({ success: false, error: message });
    }
  });
}
