import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import { query } from "../db/index.ts";

const searchIndustryNewsSchema = Type.Object({
  query: Type.String({ description: "搜索关键词" }),
  max_results: Type.Optional(Type.Number({ description: "最多返回几条", default: 3 })),
});

export const searchIndustryNewsTool: AgentTool<typeof searchIndustryNewsSchema> = {
  name: "search_industry_news",
  label: "Search Industry News",
  description: "在销销资讯工作台/知识库中搜索与客户或行业相关的内部文章",
  parameters: searchIndustryNewsSchema,
  async execute(_toolCallId, params: Static<typeof searchIndustryNewsSchema>) {
    const limit = params.max_results ?? 3;
    const keywords = params.query.split(/\s+/).filter(Boolean);
    const conditions = keywords.map((_, i) => `content ILIKE $${i + 1}`).join(" OR ");
    const rows = await query(
      `SELECT filename, content FROM knowledge_docs WHERE ${conditions} ORDER BY created_at DESC LIMIT $${keywords.length + 1}`,
      [...keywords.map((k) => `%${k}%`), limit],
    );
    if (rows.length === 0) {
      return { content: [{ type: "text" as const, text: `知识库中未找到与「${params.query}」相关的文章。` }], details: {} };
    }
    const lines = [`知识库搜索结果：${params.query}`];
    for (const r of rows as Record<string, any>[]) {
      const text = (r.content as string).replace(/\s+/g, " ").slice(0, 300);
      lines.push(`[${r.filename}]\n${text}...`);
    }
    return { content: [{ type: "text" as const, text: lines.join("\n\n") }], details: {} };
  },
};
