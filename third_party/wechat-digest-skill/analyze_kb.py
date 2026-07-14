"""
批量为 wechat-digest-skill 知识库文章生成五段式拆解。
调用项目配置的 LLM（OpenAI-compatible），把结果写回 knowledge_base.json。
"""
import json
import os
import sys
import re
import time
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录 .env
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
MAX_ARTICLES = None  # 设数字可限制只分析前 N 篇，None 表示全部


def get_client():
    base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    api_key = os.getenv("LLM_API_KEY", "lm-studio")
    model = os.getenv("MODEL_DAILY") or os.getenv("MODEL_COMPLEX") or "qwen/qwen3.6-35b-a3b"
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)
    return client, model


def build_prompt(articles):
    """把一批文章组织成 LLM prompt。"""
    lines = [
        "你是一位行业研究助理。请对下面每篇微信公众号文章做五段式精析，输出严格 JSON。",
        "",
        "每篇输出字段：",
        "- summary: 一句话总结（不超过 60 字，信息密度高，不复述标题）",
        "- viewpoints: 核心观点列表（2-5 条，每条一句话）",
        "- data: 关键数据/事实列表（1-5 条，带数字、品牌、平台名等）",
        "- tags: 关键词标签（3-6 个，覆盖平台、行业、品牌、概念、营销手段）",
        "- audience: 适用人群（1 句话，如'面向抖音电商美妆品牌操盘手'）",
        "",
        "注意：",
        "1. 如果正文太短或信息太少，允许 viewpoints/data 为空列表，但 summary 和 tags 必须有。",
        "2. tags 用简洁中文，不要带书名号、引号。",
        "3. 只输出 JSON，不要 Markdown 代码块，不要解释。",
        "",
        "输出格式示例：",
        '{"articles": {"<id>": {"summary":"...","viewpoints":["..."],"data":["..."],"tags":["..."],"audience":"..."}}}',
        "",
        "待分析文章如下：",
    ]
    for idx, a in enumerate(articles, 1):
        content = a.get("content", "") or ""
        # 控制长度，避免超长 content 撑爆 context
        if len(content) > 6000:
            content = content[:6000] + "\n...[内容过长，已截断]"
        lines.append(f"\n--- 文章 {idx} ---")
        lines.append(f"id: {a['id']}")
        lines.append(f"公众号: {a.get('account', '')}")
        lines.append(f"标题: {a.get('title', '')}")
        lines.append(f"发布时间: {a.get('publishDate', '')}")
        lines.append(f"正文:\n{content}\n")
    return "\n".join(lines)


def extract_json(text):
    """从 LLM 输出中提取 JSON。"""
    text = text.strip()
    # 去掉 Markdown 代码块
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 {} 包裹的最大块
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 LLM 输出为 JSON: {text[:200]}")


def analyze_batch(client, model, articles):
    prompt = build_prompt(articles)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一名专业的营销/电商/互联网研究助理，输出只含 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=16384,
    )
    text = resp.choices[0].message.content
    data = extract_json(text)
    return data.get("articles", data)


def main():
    client, model = get_client()
    print(f"使用模型: {model} @ {client.base_url}")

    store = kb.load_kb()
    articles = [
        a for a in store["articles"].values()
        if not a.get("analyzedAt") and (a.get("content") or "").strip()
    ]
    if MAX_ARTICLES:
        articles = articles[:MAX_ARTICLES]
    total = len(articles)
    print(f"待分析文章: {total} 篇，每批 {BATCH_SIZE} 篇")

    analyzed_count = 0
    for i in range(0, total, BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        print(f"\n[{i+1}/{total}] 分析 {len(batch)} 篇...", end=" ", flush=True)
        try:
            result = analyze_batch(client, model, batch)
        except Exception as e:
            print(f"失败: {e}")
            continue

        batch_payload = {}
        for a in batch:
            aid = a["id"]
            if aid in result:
                analysis = result[aid]
                # 规范化字段
                analysis.setdefault("summary", "")
                analysis.setdefault("viewpoints", [])
                analysis.setdefault("data", [])
                analysis.setdefault("tags", [])
                analysis.setdefault("audience", "")
                # tags 去重清洗
                tags = []
                for t in analysis["tags"]:
                    t = str(t).strip().strip("\"'").strip("《》")
                    if t and t not in tags:
                        tags.append(t)
                analysis["tags"] = tags
                batch_payload[aid] = {"analysis": analysis}
                analyzed_count += 1
            else:
                print(f"[缺失 {aid}]", end=" ")

        if batch_payload:
            counts = kb.apply_batch(store, {"articles": batch_payload})
            kb.save_kb(store)
            print(f"已应用 {counts['analysis']} 篇")
        time.sleep(0.5)

    print(f"\n完成：共分析 {analyzed_count} / {total} 篇")
    print(f"当前知识库：{len(store['articles'])} 篇，已分析 {sum(1 for a in store['articles'].values() if a.get('analyzedAt'))} 篇")


if __name__ == "__main__":
    main()
