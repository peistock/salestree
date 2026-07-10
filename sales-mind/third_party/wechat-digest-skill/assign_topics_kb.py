"""
为新增/未归类文章分配已有主题。如果某篇文章不适合任何已有主题，可选创建新主题。
"""
import json
import os
import sys
import re
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


def get_client():
    base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    api_key = os.getenv("LLM_API_KEY", "lm-studio")
    model = os.getenv("MODEL_DAILY") or os.getenv("MODEL_COMPLEX") or "qwen/qwen3.6-35b-a3b"
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)
    return client, model


def build_prompt(topics, articles):
    topic_lines = []
    for t in topics:
        topic_lines.append(f"- {t['id']}（{t['name']}）: 关键词 {', '.join(t.get('keywords', []))}")

    lines = [
        "你是一位行业内容编辑。请把下面这批尚未归类的公众号文章分配到已有的主题中。",
        "",
        "已有主题：",
    ] + topic_lines + [
        "",
        "要求：",
        "1. 每篇文章分配 1-3 个最相关的主题 id。",
        "2. 如果某篇文章完全不适合任何已有主题，可以建议 1 个新主题（id/name/keywords），但不要为了新而新。",
        "3. 只输出 JSON，不要 Markdown 代码块，不要解释。",
        "",
        "输出格式：",
        '{"articles": {"<id>": {"topicIds": ["platform-ad-strategy"]}}, "newTopics": []}',
        "",
        "未归类文章：",
    ]
    for idx, a in enumerate(articles, 1):
        analysis = a.get("analysis") or {}
        summary = analysis.get("summary", "")
        tags = analysis.get("tags", [])
        content = a.get("content", "") or ""
        context = summary
        if len(content) > 300:
            context += "\n" + content[:300]
        lines.append(f"\n--- 文章 {idx} ---")
        lines.append(f"id: {a['id']}")
        lines.append(f"公众号: {a.get('account', '')}")
        lines.append(f"标题: {a.get('title', '')}")
        lines.append(f"标签: {', '.join(tags)}")
        lines.append(f"摘要/正文:\n{context.strip()}\n")
    return "\n".join(lines)


def extract_json(text):
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


def assign_topics(client, model, topics, articles):
    prompt = build_prompt(topics, articles)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是专业的营销/电商内容编辑，擅长做主题归类，输出只含 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )
    text = resp.choices[0].message.content
    return extract_json(text)


def main():
    client, model = get_client()
    print(f"使用模型: {model} @ {client.base_url}")

    store = kb.load_kb()
    topics = store.get("topics", [])
    if not topics:
        print("尚无主题，请先运行 topic_cluster_kb.py 做主题聚类。")
        return

    articles = [a for a in store["articles"].values() if not a.get("topicIds")]
    if not articles:
        print("所有文章都已有主题，无需分配。")
        return

    total = len(articles)
    print(f"未归类文章: {total} 篇，开始分配主题...")

    # 一次性处理（通常新增不会太多）
    result = assign_topics(client, model, topics, articles)

    batch = {"topics": result.get("newTopics", []), "articles": {}}
    normalized = result.get("articles", {})
    if isinstance(normalized, list):
        tmp = {}
        for item in normalized:
            if isinstance(item, dict) and "id" in item:
                tmp[item["id"]] = item
        normalized = tmp

    for aid, payload in normalized.items():
        topic_ids = None
        if isinstance(payload, dict):
            topic_ids = payload.get("topicIds") or payload.get("topics")
        if isinstance(payload, list):
            topic_ids = payload
        if aid in store["articles"] and topic_ids:
            batch["articles"][aid] = {"topicIds": topic_ids}

    counts = kb.apply_batch(store, batch)
    kb.save_kb(store)

    print(f"新主题创建: {counts['topics']} 个")
    print(f"文章归类: {counts['meta']} / {total} 篇")
    print("当前主题分布:")
    for t in store["topics"]:
        print(f"  - {t['id']}: {t['name']} ({len(t.get('articleIds', []))} 篇)")


if __name__ == "__main__":
    main()
