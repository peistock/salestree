import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import mammoth from "mammoth";
import * as XLSX from "xlsx";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const UPLOAD_ROOT = path.resolve(__dirname, "../..", "data", "uploads");

const MAX_READ_SIZE = 5 * 1024 * 1024; // 5 MB

const readDocumentSchema = Type.Object({
  path: Type.String({ description: "文件路径，例如 /data/uploads/web_user/default/xxx.pdf" }),
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
    throw new Error("非法文件路径");
  }
  return target;
}

function getExt(filePath: string): string {
  return path.extname(filePath).toLowerCase();
}

async function extractDocxText(filePath: string): Promise<string> {
  const result = await mammoth.extractRawText({ path: filePath });
  return result.value;
}

async function extractPdfText(filePath: string): Promise<string> {
  // pdf2json is CommonJS and event-based
  const PDFParser = (await import("pdf2json")).default as {
    new (): {
      loadPDF(filePath: string): void;
      on(event: "pdfParser_dataReady", handler: (data: unknown) => void): void;
      on(event: "pdfParser_dataError", handler: (err: { parserError: Error }) => void): void;
    };
  };

  return new Promise((resolve, reject) => {
    const parser = new PDFParser();
    parser.on("pdfParser_dataError", (err) => reject(err.parserError));
    parser.on("pdfParser_dataReady", (pdfData) => {
      try {
        const text = extractTextFromPdfJson(pdfData);
        resolve(text);
      } catch (err) {
        reject(err);
      }
    });
    parser.loadPDF(filePath);
  });
}

function extractTextFromPdfJson(pdfData: unknown): string {
  const pages = (pdfData as { Pages?: unknown[] })?.Pages ?? [];
  const lines: string[] = [];
  for (const page of pages) {
    const texts = (page as { Texts?: unknown[] })?.Texts ?? [];
    for (const textItem of texts) {
      const encoded = (textItem as { R?: Array<{ T?: string }> })?.R?.[0]?.T;
      if (encoded) {
        try {
          lines.push(decodeURIComponent(encoded));
        } catch {
          lines.push(encoded);
        }
      }
    }
  }
  return lines.join(" ");
}

function extractXlsxText(filePath: string): string {
  const buffer = fs.readFileSync(filePath);
  // xlsx ESM build exposes read() instead of readFile()
  const workbook = (XLSX as any).read(buffer, { type: "buffer" });
  const lines: string[] = [];
  for (const sheetName of workbook.SheetNames) {
    const sheet = workbook.Sheets[sheetName];
    const csv = (XLSX as any).utils.sheet_to_csv(sheet, { blankrows: false });
    if (csv.trim()) {
      lines.push(`--- Sheet: ${sheetName} ---`);
      lines.push(csv);
    }
  }
  return lines.join("\n");
}

function extractStringsFromJson(obj: unknown): string[] {
  const results: string[] = [];
  function walk(value: unknown) {
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) results.push(trimmed);
    } else if (Array.isArray(value)) {
      for (const item of value) walk(item);
    } else if (value && typeof value === "object") {
      for (const v of Object.values(value)) walk(v);
    }
  }
  walk(obj);
  return results;
}

async function extractPptxText(filePath: string): Promise<string> {
  // pptx2json is a CommonJS module without ESM types
  const PPTX2Json = (await import("pptx2json")).default as {
    new (): { toJson(file: string): Promise<unknown> };
  };
  const parser = new PPTX2Json();
  const json = await parser.toJson(filePath);
  const strings = extractStringsFromJson(json);
  // Deduplicate while preserving order
  const seen = new Set<string>();
  const unique = strings.filter((s) => {
    if (seen.has(s)) return false;
    seen.add(s);
    return true;
  });
  return unique.join("\n");
}

async function extractText(filePath: string): Promise<string> {
  const ext = getExt(filePath);
  switch (ext) {
    case ".docx":
      return extractDocxText(filePath);
    case ".pdf":
      return extractPdfText(filePath);
    case ".xlsx":
    case ".xls":
      return extractXlsxText(filePath);
    case ".pptx":
      return extractPptxText(filePath);
    case ".txt":
    case ".md":
    case ".json":
    case ".csv":
    case ".html":
    case ".htm":
      return fs.readFileSync(filePath, "utf-8");
    default:
      throw new Error(`不支持的文档类型：${ext}`);
  }
}

export const readDocumentTool: AgentTool<typeof readDocumentSchema> = {
  name: "read_document",
  label: "Read Document",
  description:
    "读取用户上传的 Word/Excel/PPT/PDF 等文档内容并提取文本。支持 .docx、.xlsx/.xls、.pptx、.pdf、.txt、.md、.html、.json、.csv。",
  parameters: readDocumentSchema,
  async execute(_toolCallId, params: Static<typeof readDocumentSchema>) {
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
      const text = await extractText(target);
      const truncated = text.length > 100_000 ? text.slice(0, 100_000) + "\n\n[内容已截断，超过 100KB]" : text;
      return {
        content: [{ type: "text" as const, text: `文件：${filePath}\n\n${truncated}` }],
        details: {},
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text" as const, text: `读取文档失败：${message}` }],
        details: {},
      };
    }
  },
};
