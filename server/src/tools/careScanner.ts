import { query } from "../db/index.ts";

const RISK_KEYWORDS = ["竞品", "竞争", "对手", "贵", "太贵", "价格", "便宜", "考虑", "担心", "犹豫", "不急", "暂缓", "预算", "领导", "内部评估", "不满意", "问题", "风险", "流失", "取消", "拒"];

export function detectRiskSignals(text: string): string[] {
  const found: string[] = [];
  for (const kw of RISK_KEYWORDS) {
    if (text.includes(kw)) found.push(kw);
  }
  return found;
}

export async function notifyRisk(userId: string, source: string, signals: string[], text: string): Promise<void> {
  const title = `销售风险信号：${signals.join(" / ")}`;
  const content = `来源：${source}\n命中关键词：${signals.join(", ")}\n原文摘要：${text.slice(0, 300)}`;
  await query(`INSERT INTO notifications (user_id, type, title, content) VALUES ($1,$2,$3,$4)`, [userId, "risk", title, content]);
}

export async function scanAndNotify(userId: string, source: string, text: string): Promise<void> {
  const signals = detectRiskSignals(text);
  if (signals.length > 0) {
    await notifyRisk(userId, source, signals, text);
  }
}
