import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { config } from "../config.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const KB_PATH = path.resolve(__dirname, "../../", config.wechatKbOutputDir, "knowledge_base.json");

interface Lead {
  id: string;
  company?: string;
  industry?: string;
  relatedCompanies?: string[];
  similarIndustries?: string[];
  serviceOpportunities?: string[];
  publishDate?: string;
  [key: string]: unknown;
}

interface KnowledgeBase {
  articles?: Record<string, unknown>;
  topics?: unknown[];
  tags?: Record<string, unknown>;
  collections?: Record<string, unknown>;
  leads?: Record<string, Lead[]>;
}

function emptyKb(): KnowledgeBase {
  return { articles: {}, topics: [], tags: {}, collections: {}, leads: {} };
}

function loadKb(): KnowledgeBase {
  if (!fs.existsSync(KB_PATH)) return emptyKb();
  try {
    const raw = fs.readFileSync(KB_PATH, "utf-8");
    const parsed = JSON.parse(raw) as KnowledgeBase;
    return { ...emptyKb(), ...parsed };
  } catch (err) {
    console.error("[companyLeads] 读取 knowledge_base.json 失败:", err);
    return emptyKb();
  }
}

function norm(s: unknown): string {
  return String(s ?? "").trim().toLowerCase();
}

function cleanList(v: unknown): string[] {
  if (!v) return [];
  if (typeof v === "string") return v.trim() ? [v.trim()] : [];
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => String(x ?? "").trim())
    .filter((x) => x && x !== "无" && x !== "暂无" && x !== "-");
}

function majorityValue(values: (string | undefined)[]): string {
  const counts = new Map<string, number>();
  for (const v of values) {
    if (!v) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  let best = "";
  let bestCount = 0;
  for (const [value, count] of counts.entries()) {
    if (count > bestCount || (count === bestCount && value < best)) {
      best = value;
      bestCount = count;
    }
  }
  return best;
}

interface CompanyLeadView {
  name: string;
  industry: string;
  matched_lead_ids: string[];
  matched_leads_count: number;
  competitors: string[];
  similar_industries: string[];
  service_opportunities: string[];
  expandable_services: string[];
  latest_lead_date: string;
}

function buildCompanyLeads(kb: KnowledgeBase, companyQuery: string, limit: number) {
  const allLeads: Lead[] = [];
  for (const ls of Object.values(kb.leads || {})) {
    if (Array.isArray(ls)) allLeads.push(...ls);
  }

  let companyNames = Array.from(
    new Set(allLeads.map((l) => norm(l.company)).filter(Boolean)),
  ).sort();

  if (companyQuery) {
    const cq = norm(companyQuery);
    companyNames = companyNames.filter((c) => c.includes(cq) || cq.includes(c));
  }

  function leadsAbout(name: string): Lead[] {
    return allLeads.filter((l) => {
      const lc = norm(l.company);
      if (lc === name) return true;
      const related = (l.relatedCompanies || []).map(norm);
      return related.includes(name);
    });
  }

  const result: CompanyLeadView[] = [];
  for (const c of companyNames) {
    const matched = leadsAbout(c);
    const direct = matched.filter((l) => norm(l.company) === c);
    const pool = direct.length ? direct : matched;
    const industry = majorityValue(pool.map((l) => l.industry));

    const competitors = Array.from(
      new Set(
        matched
          .flatMap((l) => cleanList(l.relatedCompanies))
          .filter((x) => norm(x) !== c),
      ),
    ).sort();

    const similarIndustries = Array.from(
      new Set(matched.flatMap((l) => cleanList(l.similarIndustries))),
    ).sort();

    const services = Array.from(
      new Set(matched.flatMap((l) => cleanList(l.serviceOpportunities))),
    ).sort();

    const ownServices = new Set(services.map(norm));
    const expandable = Array.from(
      new Set(
        allLeads
          .filter((l) => norm(l.industry) === norm(industry) && !matched.includes(l))
          .flatMap((l) => cleanList(l.serviceOpportunities))
          .filter((x) => !ownServices.has(norm(x))),
      ),
    ).sort();

    const latest = matched
      .map((l) => l.publishDate || "")
      .filter(Boolean)
      .sort()
      .pop() || "";

    const displayName = direct[0]?.company || matched[0]?.company || c;

    result.push({
      name: displayName,
      industry,
      matched_lead_ids: matched.map((l) => String(l.id)),
      matched_leads_count: matched.length,
      competitors,
      similar_industries: similarIndustries,
      service_opportunities: services,
      expandable_services: expandable,
      latest_lead_date: latest,
    });
  }

  result.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  result.sort((a, b) => (b.latest_lead_date || "").localeCompare(a.latest_lead_date || ""));
  result.sort((a, b) => b.matched_leads_count - a.matched_leads_count);

  return { companies: result.slice(0, limit), total: result.length };
}

export async function companyLeadsRoutes(app: FastifyInstance) {
  app.get("/api/wechat_kb/company_leads", async (req: FastifyRequest, reply: FastifyReply) => {
    const query = req.query as { company?: string; limit?: string };
    const company = String(query.company || "").trim();
    const limit = parseInt(query.limit || "200", 10) || 200;
    const kb = loadKb();
    return reply.send(buildCompanyLeads(kb, company, limit));
  });
}
