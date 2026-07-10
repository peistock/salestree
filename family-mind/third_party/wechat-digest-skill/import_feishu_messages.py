"""
把飞书群消息导入 wechat-digest-skill 知识库。
- 过滤最近一周消息
- 将 post/interactive/text 消息转为 article 记录
- 与已有公众号文章去重（以公众号为准）
- 写入 knowledge_base.json
"""
import json
import os
import sys
import re
import hashlib
from pathlib import Path
from datetime import datetime

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


def canonical_id_from_text(text: str) -> str:
    """为飞书消息生成稳定 id。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def clean_content(text: str) -> str:
    """清理飞书消息内容，去掉 <card> 标签等。"""
    # 去掉 <card title="..."> ... </card>
    text = re.sub(r"<card[^>]*>", "", text)
    text = re.sub(r"</card>", "", text)
    # 去掉 ![Image](...)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # 去掉图片标记 img_key:...
    text = re.sub(r"图片\(img_key:[^)]+\)", "", text)
    # 去掉多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_title(content: str, msg_type: str) -> str:
    """从消息内容提取标题。"""
    lines = content.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # card title
        if line.startswith("【") and "】" in line:
            return line.strip("【】")
        if line.startswith("**") and line.endswith("**"):
            return line.strip("*")
        # 普通第一行，截断
        if line:
            return line[:80]
    return "飞书群消息"


def message_to_article(msg: dict) -> dict:
    """把飞书消息转为 knowledge_base article 结构。"""
    content = clean_content(msg.get("content", ""))
    title = extract_title(content, msg.get("msg_type", ""))
    msg_id = msg.get("message_id", "")
    create_time = msg.get("create_time", "")
    # 标准化日期
    publish_date = ""
    if create_time:
        try:
            dt = datetime.strptime(create_time, "%Y-%m-%d %H:%M")
            publish_date = dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    sender = msg.get("sender", {})
    sender_name = sender.get("name", "")

    # 生成稳定 id：避免与公众号 id 冲突，加前缀
    text_for_id = f"feishu:{msg_id or title}"
    aid = f"feishu_{canonical_id_from_text(text_for_id)}"

    return {
        "id": aid,
        "account": "飞书媒体信息同步群",
        "query": "",
        "title": title,
        "link": msg.get("message_app_link", ""),
        "cover": "",
        "images": [],
        "publishDate": publish_date,
        "digest": content[:200],
        "content": content,
        "collectedAt": datetime.now().isoformat(timespec="seconds"),
        "ingestedAt": datetime.now().isoformat(timespec="seconds"),
        "analyzedAt": None,
        "analysis": kb._empty_analysis(),
        "topicIds": [],
        "crossRefs": [],
        "source": "feishu",
    }


def is_duplicate(client, model, feishu_article, existing_articles):
    """用 LLM 判断飞书消息是否与已有公众号文章重复。"""
    existing_summaries = []
    for idx, a in enumerate(existing_articles[:30], 1):  # 取最近 30 篇做对比
        existing_summaries.append(f"{idx}. [{a.get('account', '')}] {a.get('title', '')}")

    prompt = f"""判断下面这条飞书群消息是否与已有的公众号文章重复。
如果讲的是同一个活动/公告/主题，回答 "是"；如果只是相关但内容不同，回答 "否"。

飞书消息标题：{feishu_article['title']}
飞书消息内容前 500 字：
{feishu_article['content'][:500]}

已有公众号文章标题列表：
""" + "\n".join(existing_summaries) + """

只回答 "是" 或 "否"，不要解释。"""

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是内容去重助手，严格判断是否为重复内容。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=10,
    )
    text = resp.choices[0].message.content.strip().lower()
    return "是" in text or "yes" in text or "true" in text


def main():
    client, model = get_client()
    messages_path = Path("/tmp/feishu_chat_messages.json")
    if not messages_path.exists():
        print(f"未找到消息文件: {messages_path}")
        return

    with open(messages_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("data", {}).get("messages", [])
    print(f"飞书消息总数: {len(messages)}")

    store = kb.load_kb()
    existing_articles = list(store["articles"].values())

    added = 0
    skipped = 0
    for msg in messages:
        article = message_to_article(msg)
        if not article["content"]:
            skipped += 1
            continue

        # 检查是否重复
        if is_duplicate(client, model, article, existing_articles):
            print(f"跳过（重复）: {article['title'][:60]}")
            skipped += 1
            continue

        # 加入知识库
        if article["id"] not in store["articles"]:
            store["articles"][article["id"]] = article
            added += 1
            print(f"新增: {article['title'][:60]}")
        else:
            skipped += 1

    if added:
        kb.rebuild_tags(store)
        kb.rebuild_topic_members(store)
        kb.save_kb(store)

    print(f"\n完成：新增 {added} 篇，跳过 {skipped} 篇，库内共 {len(store['articles'])} 篇")


if __name__ == "__main__":
    main()
