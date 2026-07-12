import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import { config } from "../config.ts";

const searchWebSchema = Type.Object({
  query: Type.String({ description: "搜索关键词" }),
  max_results: Type.Optional(Type.Number({ description: "最多返回几条结果", default: 5 })),
});

interface SearxResult {
  title?: string;
  content?: string;
  url?: string;
  engine?: string;
}

async function searchCategory(query: string, category: string, timeoutMs = 8000): Promise<SearxResult[]> {
  const base = config.searxngUrl.replace(/\/$/, "");
  const url = new URL(`${base}/search`);
  url.searchParams.set("q", query);
  url.searchParams.set("format", "json");
  url.searchParams.set("language", "zh-CN");
  url.searchParams.set("safesearch", "2");
  url.searchParams.set("categories", category);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url.toString(), {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    clearTimeout(timer);
    if (!resp.ok) return [];
    const data = (await resp.json()) as { results?: SearxResult[] };
    return data.results ?? [];
  } catch {
    clearTimeout(timer);
    return [];
  }
}

function extractKeywords(query: string): { cn: string[]; en: string[] } {
  const cn = query.match(/[一-鿿]{2,}/g) ?? [];
  const en = query
    .replace(/["']/g, "")
    .match(/[a-zA-Z]{3,}/g)
    ?.map((w) => w.toLowerCase()) ?? [];
  return { cn, en };
}

function isRelevant(result: SearxResult, keywords: { cn: string[]; en: string[] }): boolean {
  const text = `${result.title ?? ""} ${result.content ?? ""}`.toLowerCase();
  if (keywords.cn.length > 0 && keywords.cn.some((k) => text.includes(k))) return true;
  if (keywords.en.length > 0 && keywords.en.some((k) => text.includes(k))) return true;
  return keywords.cn.length === 0 && keywords.en.length === 0;
}

export const searchWebTool: AgentTool<typeof searchWebSchema> = {
  name: "search_web",
  label: "Search Web",
  description: "联网搜索实时信息，适合查询新闻、公开资料、政策等",
  parameters: searchWebSchema,
  async execute(_toolCallId, params: Static<typeof searchWebSchema>) {
    const maxResults = params.max_results ?? 5;
    const [general, news] = await Promise.all([
      searchCategory(params.query, "general"),
      searchCategory(params.query, "news"),
    ]);

    const seen = new Set<string>();
    const merged: SearxResult[] = [];
    for (const r of [...general, ...news]) {
      const url = r.url ?? "";
      if (url && !seen.has(url)) {
        seen.add(url);
        merged.push(r);
      }
    }

    const keywords = extractKeywords(params.query);
    const relevant = merged.filter((r) => isRelevant(r, keywords));
    const irrelevant = merged.filter((r) => !isRelevant(r, keywords));
    const results = [...relevant, ...irrelevant].slice(0, maxResults);

    if (results.length === 0) {
      return {
        content: [{ type: "text" as const, text: `搜索「${params.query}」未找到结果。` }],
        details: {},
      };
    }

    const lines = [`搜索：${params.query}`];
    for (let i = 0; i < results.length; i++) {
      const r = results[i];
      const title = r.title ?? "";
      const content = (r.content ?? "").slice(0, 250);
      const url = r.url ?? "";
      const engine = r.engine ?? "";
      lines.push(`${i + 1}. ${title}\n   ${content}...\n   来源：${engine} | ${url}`);
    }

    const text = lines.join("\n\n");
    return {
      content: [{ type: "text" as const, text }],
      details: {},
    };
  },
};
