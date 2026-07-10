#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════
 墨摘 · 销售线索挖掘器 (wechat-digest skill)
 ─────────────────────────────────────────────────────────────────────────
 从已分析的知识库文章中自动提取 B2B 销售线索：
   · 文章里露出的客户公司及其营销玩法
   · 该客户的竞品 / 对标公司（纵向拓展）
   · 可以复制该玩法的类似行业（横向复制）
   · 我们（整合营销服务公司）可切入的服务机会

 设计哲学：与 analyze_kb.py 保持一致，调用本地 LLM（OpenAI-compatible），
 输出严格 JSON，结果写回 knowledge_base.json 的 leads 字段。

 用法：
   python3 extract_leads_kb.py                    # 全量提取
   python3 extract_leads_kb.py --max 20           # 只处理前 20 篇未提取的文章
   python3 extract_leads_kb.py --article <id>     # 单篇提取/重跑
════════════════════════════════════════════════════════════════════════════
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)

try:
    from openai import OpenAI
except ImportError as e:
    print("请安装 openai SDK: pip install openai", file=sys.stderr)
    raise SystemExit(1)

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
import kb

BATCH_SIZE = 5


def get_client():
    base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    api_key = os.getenv("LLM_API_KEY", "lm-studio")
    model = os.getenv("MODEL_DAILY") or os.getenv("MODEL_COMPLEX") or "qwen/qwen3.6-35b-a3b"
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)
    return client, model


def _lead_id(aid, company):
    key = f"{aid}::{company.strip()}"
    return "lead_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _clean_list(v):
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    out = []
    for x in v:
        x = str(x).strip().strip("'\"")
        if x and x not in ("无", "暂无", "-"):
            out.append(x)
    return out


def build_prompt(articles):
    """把一批文章组织成 LLM prompt。"""
    lines = [
        "你是一位 B2B 销售研究助理，擅长从行业媒体文章里提取销售线索。",
        "",
        "任务：基于每篇文章提到的客户案例、营销动作、行业玩法，提取可用于销售跟进的机会。",
        "",
        "每篇文章输出一个 leads 数组，每个 lead 对象必须包含以下字段：",
        "- company: 文中提到的核心客户/品牌公司名称（必填，用正式公司名，不要昵称）",
        "- industry: 该公司所属行业（如：休闲食品、美妆、3C、新茶饮、新能源汽车）",
        "- signal: 业务信号（如：新品上市、品牌升级、营销招标、出海、融资、GMV目标、达人种草、短视频投放、直播电商、私域运营）",
        "- playbook: 文中描述的具体玩法/策略（50字内，突出可复制的动作）",
        "- relatedCompanies: 该公司的竞品/对标公司数组（3-6个，便于纵向拓客）",
        "- similarIndustries: 可以复制该玩法的类似行业数组（2-4个）",
        "- serviceOpportunities: 我们（整合营销服务公司）可以切入的服务机会数组（如：整合营销方案、短视频种草、KOL合作、私域运营、电商代运营、品牌定位、效果广告投放、直播运营）",
        "- confidence: 置信度 1-5（5=非常确定是有效线索）",
        "",
        "注意：",
        "1. 只提取真实存在的公司/品牌，不要编造。",
        "2. 如果文章里没有明确客户案例，leads 可以为空数组。",
        "3. 同一篇文章里多个客户，分别输出多个 lead。",
        "4. 输出严格 JSON，不要 Markdown 代码块，不要解释。",
        "",
        "输出格式示例：",
        '{"articles": {"<article_id>": {"leads": [{"company":"三只松鼠","industry":"休闲食品","signal":"品牌升级","playbook":"高端性价比+供应链优化","relatedCompanies":["良品铺子","盐津铺子","洽洽食品"],"similarIndustries":["新茶饮","预制菜"],"serviceOpportunities":["整合营销方案","短视频种草","达人合作"],"confidence":4}]}}}',
        "",
        "待提取文章如下：",
    ]
    for idx, a in enumerate(articles, 1):
        content = a.get("content", "") or ""
        analysis = a.get("analysis") or {}
        if len(content) > 5000:
            content = content[:5000] + "\n...[内容过长，已截断]"
        lines.append(f"\n--- 文章 {idx} ---")
        lines.append(f"id: {a['id']}")
        lines.append(f"公众号: {a.get('account', '')}")
        lines.append(f"标题: {a.get('title', '')}")
        lines.append(f"发布时间: {a.get('publishDate', '')}")
        if analysis.get("summary"):
            lines.append(f"精要: {analysis['summary']}")
        if analysis.get("tags"):
            lines.append(f"标签: {', '.join(analysis['tags'])}")
        lines.append(f"正文:\n{content}\n")
    return "\n".join(lines)


def extract_json(text):
    """从 LLM 输出中提取 JSON。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 LLM 输出为 JSON: {text[:200]}")


def extract_batch(client, model, articles):
    prompt = build_prompt(articles)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一名专业的 B2B 销售研究助理，输出只含 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )
    text = resp.choices[0].message.content
    data = extract_json(text)
    return data.get("articles", data)


def normalize_lead(article, raw_lead):
    """把 LLM 输出规范化为 KB lead 结构。"""
    company = str(raw_lead.get("company") or "").strip()
    if not company:
        return None
    return {
        "id": _lead_id(article["id"], company),
        "articleId": article["id"],
        "sourceAccount": article.get("account", ""),
        "sourceTitle": article.get("title", ""),
        "sourceLink": article.get("link", ""),
        "publishDate": article.get("publishDate", ""),
        "company": company,
        "industry": str(raw_lead.get("industry") or "").strip(),
        "signal": str(raw_lead.get("signal") or "").strip(),
        "playbook": str(raw_lead.get("playbook") or "").strip(),
        "relatedCompanies": _clean_list(raw_lead.get("relatedCompanies")),
        "similarIndustries": _clean_list(raw_lead.get("similarIndustries")),
        "serviceOpportunities": _clean_list(raw_lead.get("serviceOpportunities")),
        "confidence": max(1, min(5, int(raw_lead.get("confidence") or 3))),
        "extractedAt": kb.datetime.now().isoformat(timespec="seconds"),
    }


def extract_leads_for_articles(kb_obj, article_ids, client=None, model=None, verbose=True):
    """为指定文章列表提取线索并写回 KB。"""
    if client is None:
        client, model = get_client()

    articles = [kb_obj["articles"][aid] for aid in article_ids if aid in kb_obj["articles"]]
    if not articles:
        return 0

    total = 0
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        if verbose:
            print(f"[{i+1}/{len(articles)}] 提取 {len(batch)} 篇线索...", end=" ", flush=True)
        try:
            result = extract_batch(client, model, batch)
        except Exception as e:
            if verbose:
                print(f"失败: {e}")
            continue

        for a in batch:
            aid = a["id"]
            payload = (result.get(aid) or {}) if isinstance(result, dict) else {}
            raw_leads = payload.get("leads") if isinstance(payload, dict) else []
            if not isinstance(raw_leads, list):
                raw_leads = []

            leads = []
            for raw in raw_leads:
                lead = normalize_lead(a, raw)
                if lead:
                    leads.append(lead)

            kb_obj.setdefault("leads", {})[aid] = leads
            total += len(leads)

        if verbose:
            print(f"得到 {sum(len(kb_obj['leads'].get(a['id'], [])) for a in batch)} 条")
        time.sleep(0.5)

    return total


def main():
    p = argparse.ArgumentParser(
        prog="extract_leads_kb.py",
        description="从 wechat-digest 知识库文章中提取销售线索")
    p.add_argument("--kb", default=str(kb.KB_PATH), help="知识库路径")
    p.add_argument("--article", help="只处理指定文章 id")
    p.add_argument("--max", type=int, help="最多处理 N 篇未提取的文章")
    p.add_argument("--all", action="store_true", help="重跑所有文章（包括已提取的）")
    args = p.parse_args()

    store = kb.load_kb(args.kb)
    store.setdefault("leads", {})

    client, model = get_client()
    print(f"使用模型: {model} @ {client.base_url}")

    if args.article:
        if args.article not in store["articles"]:
            raise SystemExit(f"未找到文章 id={args.article}")
        ids = [args.article]
    else:
        ids = [aid for aid, a in store["articles"].items() if a.get("analyzedAt")]
        if not args.all:
            ids = [aid for aid in ids if aid not in store.get("leads", {})]
        if args.max:
            ids = ids[:args.max]

    print(f"待提取文章: {len(ids)} 篇")
    total = extract_leads_for_articles(store, ids, client=client, model=model)
    kb.save_kb(store, args.kb)
    print(f"\n✓ 线索提取完成：共 {total} 条，已保存到 {args.kb}")


if __name__ == "__main__":
    main()
