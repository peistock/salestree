import fs from "node:fs";
import path from "node:path";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import { config } from "../config.ts";

const getNewsDigestSchema = Type.Object({
  focus: Type.Optional(Type.String({ description: "可选：聚焦某个客户/主题/账号，例如'快手'、'小红书'、'抖音'", default: "" })),
  max_articles: Type.Optional(Type.Number({ description: "最多返回几篇文章", default: 10 })),
  max_leads: Type.Optional(Type.Number({ description: "最多返回几条线索", default: 10 })),
});

interface Article {
  id: string;
  account?: string;
  title?: string;
  digest?: string;
  content?: string;
  publishDate?: string;
  link?: string;
}

interface Lead {
  id?: string;
  articleId?: string;
  sourceAccount?: string;
  sourceTitle?: string;
  company?: string;
  industry?: string;
  signal?: string;
  playbook?: string;
  relatedCompanies?: string[];
  similarIndustries?: string[];
  serviceOpportunities?: string[];
  confidence?: number;
}

interface KnowledgeBase {
  version: string;
  updatedAt: string;
  topics?: Array<{
    id: string;
    name: string;
    keywords: string[];
    articleIds: string[];
  }>;
  articles?: Record<string, Article> | Article[];
  leads?: Record<string, Lead[]> | Lead[];
}

function findLatestKnowledgeBasePath(): string | null {
  const outputDir = path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    "../",
    config.wechatKbOutputDir,
  );
  if (!fs.existsSync(outputDir)) return null;

  // prefer knowledge_base.json if present
  const kbPath = path.join(outputDir, "knowledge_base.json");
  if (fs.existsSync(kbPath)) return kbPath;

  // fallback to newest articles_YYYYMMDD.json
  const files = fs.readdirSync(outputDir);
  const articleFiles = files
    .filter((f) => f.startsWith("articles_") && f.endsWith(".json"))
    .sort()
    .reverse();
  if (articleFiles.length === 0) return null;
  return path.join(outputDir, articleFiles[0]);
}

function loadKnowledgeBase(): KnowledgeBase | null {
  const filePath = findLatestKnowledgeBasePath();
  if (!filePath) return null;
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as KnowledgeBase;
  } catch (err) {
    console.error("[getNewsDigest] 读取知识库失败:", err);
    return null;
  }
}

function toArticleArray(input?: Record<string, Article> | Article[]): Article[] {
  if (!input) return [];
  if (Array.isArray(input)) return input;
  return Object.values(input);
}

function toLeadArray(input?: Record<string, Lead[]> | Lead[]): Lead[] {
  if (!input) return [];
  if (Array.isArray(input)) return input;
  return Object.values(input).flat();
}

export const getNewsDigestTool: AgentTool<typeof getNewsDigestSchema> = {
  name: "get_news_digest",
  label: "Get News Digest",
  description:
    "读取销销资讯看板（/wechat_kb）的最新内容，包括文章标题、摘要、线索库等。当用户问'总结资讯看板'、'今天有什么资讯'、'最近行业动态'时使用。",
  parameters: getNewsDigestSchema,
  async execute(_toolCallId, params: Static<typeof getNewsDigestSchema>) {
    const kb = loadKnowledgeBase();
    if (!kb) {
      return {
        content: [{ type: "text" as const, text: "资讯看板暂无内容，请稍后再试。" }],
        details: {},
      };
    }

    const focus = params.focus?.trim().toLowerCase() || "";
    const maxArticles = params.max_articles ?? 10;
    const maxLeads = params.max_leads ?? 10;

    let articles = toArticleArray(kb.articles);
    if (focus) {
      articles = articles.filter((a) =>
        [a.title, a.digest, a.content, a.account].some((field) =>
          field?.toLowerCase().includes(focus),
        ),
      );
    }
    articles = articles.slice(0, maxArticles);

    let leads = toLeadArray(kb.leads);
    if (focus) {
      leads = leads.filter((l) =>
        [l.company, l.playbook, l.signal, l.sourceAccount, l.industry, ...(l.serviceOpportunities ?? [])].some((field) =>
          typeof field === "string" && field.toLowerCase().includes(focus),
        ),
      );
    }
    leads = leads.slice(0, maxLeads);

    const lines: string[] = [];
    lines.push(`资讯看板摘要（更新于 ${kb.updatedAt ?? "未知"}）`);

    if (kb.topics && kb.topics.length > 0) {
      lines.push("\n【主题分类】");
      for (const t of kb.topics.slice(0, 10)) {
        lines.push(`- ${t.name}（关键词：${t.keywords.slice(0, 5).join("、")}）`);
      }
    }

    if (articles.length > 0) {
      lines.push("\n【精选文章】");
      for (const a of articles) {
        lines.push(`- ${a.account ? `[${a.account}] ` : ""}${a.title ?? "无标题"}`);
        if (a.digest) lines.push(`  摘要：${a.digest}`);
      }
    } else {
      lines.push("\n【精选文章】无匹配文章");
    }

    if (leads.length > 0) {
      lines.push("\n【线索库】");
      for (const l of leads) {
        const company = l.company ?? l.sourceAccount ?? "未知公司";
        const desc = [l.signal, l.playbook].filter(Boolean).join("；");
        lines.push(`- ${company}：${desc}`);
      }
    }

    return {
      content: [{ type: "text" as const, text: lines.join("\n") }],
      details: { articleCount: articles.length, leadCount: leads.length },
    };
  },
};
