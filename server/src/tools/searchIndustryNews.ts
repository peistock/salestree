import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { query } from "../db/index.ts";
import { config } from "../config.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const searchIndustryNewsSchema = Type.Object({
  query: Type.String({ description: "搜索关键词" }),
  max_results: Type.Optional(Type.Number({ description: "最多返回几条", default: 3 })),
});

interface Article {
  id?: string;
  account?: string;
  title?: string;
  digest?: string;
  content?: string;
  publishDate?: string;
  link?: string;
}

interface KnowledgeBase {
  version?: string;
  updatedAt?: string;
  articles?: Record<string, Article> | Article[];
}

function loadKnowledgeBaseJson(): KnowledgeBase | null {
  const kbPath = path.resolve(
    __dirname,
    "../../",
    config.wechatKbOutputDir,
    "knowledge_base.json",
  );
  if (!fs.existsSync(kbPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(kbPath, "utf-8")) as KnowledgeBase;
  } catch (err) {
    console.error("[searchIndustryNews] 读取 knowledge_base.json 失败:", err);
    return null;
  }
}

function toArticleArray(input?: Record<string, Article> | Article[]): Article[] {
  if (!input) return [];
  return Array.isArray(input) ? input : Object.values(input);
}

function searchKnowledgeBaseJson(query: string, limit: number): Array<{ filename: string; content: string }> {
  const kb = loadKnowledgeBaseJson();
  if (!kb) return [];

  const keywords = query
    .split(/\s+/)
    .filter(Boolean)
    .map((k) => k.toLowerCase());
  if (keywords.length === 0) return [];

  const articles = toArticleArray(kb.articles);
  const matched: Array<{ article: Article; score: number }> = [];

  for (const article of articles) {
    const text = `${article.title ?? ""}\n${article.account ?? ""}\n${article.digest ?? ""}\n${article.content ?? ""}`.toLowerCase();
    const hitCount = keywords.filter((k) => text.includes(k)).length;
    if (hitCount === 0) continue;
    matched.push({ article, score: hitCount });
  }

  matched.sort((a, b) => b.score - a.score);

  return matched.slice(0, limit).map(({ article }) => {
    const header = article.title ? `《${article.title}》` : "无标题";
    const meta = [article.account, article.publishDate].filter(Boolean).join(" · ");
    const body = [article.digest, article.content].filter(Boolean).join("\n").slice(0, 500);
    const link = article.link ? `\n来源：${article.link}` : "";
    return {
      filename: meta ? `${header}｜${meta}` : header,
      content: `${header}\n${body}${link}`,
    };
  });
}

export const searchIndustryNewsTool: AgentTool<typeof searchIndustryNewsSchema> = {
  name: "search_industry_news",
  label: "Search Industry News",
  description: "在销销资讯工作台/知识库中搜索与客户或行业相关的内部文章",
  parameters: searchIndustryNewsSchema,
  async execute(_toolCallId, params: Static<typeof searchIndustryNewsSchema>) {
    const limit = params.max_results ?? 3;
    const keywords = params.query.split(/\s+/).filter(Boolean);

    // 1. 先查 PGVector 知识库（语义搜索，更准）
    const conditions = keywords.map((_, i) => `content ILIKE $${i + 1}`).join(" OR ");
    let rows: Record<string, any>[] = [];
    if (keywords.length > 0) {
      try {
        rows = await query(
          `SELECT filename, content FROM knowledge_docs WHERE ${conditions} ORDER BY created_at DESC LIMIT $${keywords.length + 1}`,
          [...keywords.map((k) => `%${k}%`), limit],
        );
      } catch (err) {
        console.error("[searchIndustryNews] 数据库查询失败，尝试 fallback:", err);
      }
    }

    // 2. 数据库为空/失败时，fallback 读取 wechat-digest knowledge_base.json
    if (rows.length === 0) {
      rows = searchKnowledgeBaseJson(params.query, limit);
    }

    if (rows.length === 0) {
      return {
        content: [{ type: "text" as const, text: `知识库中未找到与「${params.query}」相关的文章。` }],
        details: {},
      };
    }

    const lines = [`知识库搜索结果：${params.query}`];
    for (const r of rows as Record<string, any>[]) {
      const text = (r.content as string).replace(/\s+/g, " ").slice(0, 300);
      lines.push(`[${r.filename}]\n${text}...`);
    }
    return { content: [{ type: "text" as const, text: lines.join("\n\n") }], details: {} };
  },
};
