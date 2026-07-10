"""
为 wechat-digest-skill 知识库文章做主题聚类。
调用项目配置的 LLM，自动产出 topics 并给每篇文章分配 topicIds，然后写回 knowledge_base.json。
"""
import json
import os
import sys
import re
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


def get_client():
    base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    api_key = os.getenv("LLM_API_KEY", "lm-studio")
    model = os.getenv("MODEL_DAILY") or os.getenv("MODEL_COMPLEX") or "qwen/qwen3.6-35b-a3b"
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)
    return client, model


def build_prompt(articles):
    lines = [
        "你是一位行业研究编辑。请为下面一批微信公众号文章设计主题聚类，并给每篇文章分配主题。",
        "",
        "要求：",
        "1. 设计 6-10 个主题，每个主题有：id（英文短横线连接小写，如 douyin-ecommerce）、name（中文主题名）、keywords（3-6 个关键词）",
        "2. 主题要覆盖营销、电商、AI、数据、平台广告、品牌增长等维度，不要按公众号简单分。",
        "3. 每篇文章分配 1-3 个最相关的主题 id。",
        "4. 只输出 JSON，不要 Markdown 代码块，不要解释。",
        "",
        "输出格式：",
        '{"topics": [{"id":"douyin-ecommerce","name":"抖音电商","keywords":["抖音","电商","美妆榜单"]}], "articles": {"<id>": {"topicIds": ["douyin-ecommerce"]}}}',
        "",
        "文章列表：",
    ]
    for idx, a in enumerate(articles, 1):
        analysis = a.get("analysis") or {}
        summary = analysis.get("summary", "")
        tags = analysis.get("tags", [])
        content = a.get("content", "") or ""
        # 用摘要+前 300 字正文帮助 LLM 判断
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


def cluster_topics(client, model, articles):
    prompt = build_prompt(articles)
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
    articles = list(store["articles"].values())
    total = len(articles)
    print(f"知识库文章: {total} 篇，开始主题聚类...")

    result = cluster_topics(client, model, articles)

    topics = result.get("topics", [])
    article_topics = result.get("articles", {})

    # LLM 有时返回列表 [{"id": ..., "topicIds": ...}]，有时返回字典 {"id": {"topicIds": ...}}
    normalized = {}
    if isinstance(article_topics, list):
        for item in article_topics:
            if isinstance(item, dict) and "id" in item:
                normalized[item["id"]] = item
    elif isinstance(article_topics, dict):
        normalized = article_topics
    else:
        print(f"警告：articles 字段格式异常：{type(article_topics)}", file=sys.stderr)

    batch = {"topics": topics, "articles": {}}
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

    print(f"主题创建: {counts['topics']} 个")
    print(f"文章归类: {counts['meta']} 篇")
    print("主题列表:")
    for t in store["topics"]:
        print(f"  - {t['id']}: {t['name']} ({len(t.get('articleIds', []))} 篇)")


if __name__ == "__main__":
    main()
