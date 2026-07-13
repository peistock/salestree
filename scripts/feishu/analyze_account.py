#!/usr/bin/env python3
"""通用飞书群销售情报分析脚本。

用法：
    python3 analyze_account.py --account 红果星广

读取 data/projects/{account}_messages.json，输出：
    data/projects/{account}_analysis.json
    data/projects/{account}_analysis.md
    data/projects/{account}_history.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_PATH = PROJECT_ROOT / "data" / "projects" / "feishu_accounts.json"
DATA_DIR = PROJECT_ROOT / "data" / "projects"


def load_accounts():
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9一-龥]", "_", name)


def parse_time(t):
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=timezone(timedelta(hours=8)))
        except Exception:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description="分析飞书群销售情报")
    parser.add_argument("--account", required=True, help="账户名，如 红果星广")
    parser.add_argument("--model", help="覆盖 MODEL_DAILY 环境变量")
    args = parser.parse_args()

    accounts = load_accounts()
    account_config = accounts.get("accounts", {}).get(args.account)
    if not account_config:
        print(f"错误：找不到账户 {args.account}，请检查 {ACCOUNTS_PATH}", file=sys.stderr)
        sys.exit(1)

    safe_account = sanitize(args.account)
    messages_path = DATA_DIR / f"{safe_account}_messages.json"
    if not messages_path.exists():
        print(f"错误：找不到消息文件 {messages_path}，请先执行 pull_account.py", file=sys.stderr)
        sys.exit(1)

    with open(messages_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("data", {}).get("messages", [])

    # 按 chat_type 分组
    customer_lines = []
    internal_lines = []
    all_lines = []
    for m in messages:
        chat = m.get("chat_name", "")
        chat_type = m.get("chat_type", "")
        sender = m.get("sender", {}).get("name", m.get("sender", {}).get("id", ""))
        time = m.get("create_time", "")
        content = m.get("content", "")
        line = f"[{chat}] {time} {sender}:\n{content}\n"
        all_lines.append(line)
        if chat_type == "customer":
            customer_lines.append(line)
        elif chat_type == "internal":
            internal_lines.append(line)
        else:
            # 兜底：按群名关键词判断
            if "客户" in chat or "投放" in chat or "素材" in chat or "外部" in chat:
                customer_lines.append(line)
            else:
                internal_lines.append(line)

    customer_context = "\n".join(customer_lines)
    internal_context = "\n".join(internal_lines)
    all_context = "\n".join(all_lines)

    times = [parse_time(m.get("create_time", "")) for m in messages]
    times = [t for t in times if t]
    if times:
        min_time = min(times)
        max_time = max(times)
        date_range_str = f"{min_time.strftime('%Y-%m-%d')} ~ {max_time.strftime('%Y-%m-%d')}"
    else:
        date_range_str = ""

    client = OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://api.kimi.com/coding/v1"),
        api_key=os.getenv("LLM_API_KEY"),
    )
    model = args.model or os.getenv("MODEL_DAILY", "k2.6")

    prompt = f"""你是 B2B 销售情报分析专家。请根据下面客户相关的飞书群消息，为销售团队提炼结构化情报。

该客户涉及多个群：客户群（可能分为投放群、素材对接群等）和内部协同群。请先分别整理，再交叉验证。

## 分析要求

1. **分群整理**：分别输出「客户群关键内容」和「内部群关键内容」，不要混为一谈。如果客户群有多个（如投放群、素材群），在客户群摘要里用子标题区分。
2. **话题粒度**：每个群按话题组织，每个话题包含「时间 + 参与者 + 核心内容 + 关键原话」。
3. **交叉验证**：把客户群提出的每一条「要求 / 问题 / 风险 / deadline」，拿到内部群里找响应。
   - 已响应：内部群有明确执行人、方案或闭环
   - 部分响应：有回应但无最终闭环
   - 未响应：内部群完全没有提到
   对「未响应」和「部分响应」要重点标出，并提醒销售跟进。
4. **信号提取**：从两边聊天中提炼 5-10 条关键信号，类型包括：客户动态、内部协同、待办、风险、机会。
5. **待办事项**：列出销售需要直接跟进的事项，按紧急程度排序。
6. **销售洞察**：给出 200 字内的洞察和建议，指出销售该主动介入的点。

## 输出格式

必须严格输出以下 JSON，不要加任何额外解释：

{{
  "account_name": "{args.account}",
  "date_range": "{date_range_str}",
  "summary": "整体动态摘要（200字内）",
  "customer_group_summary": "客户群关键内容。按话题/子群组织，保留关键原话和发言人。",
  "internal_group_summary": "内部群关键内容。按话题组织，保留关键原话和发言人。",
  "cross_reference": [
    {{
      "customer_requirement": "客户群提出的具体requirement / 问题 / 风险",
      "internal_response": "内部群如何响应，如果没有响应写'未在内部群看到响应'",
      "status": "已响应 / 未响应 / 部分响应"
    }}
  ],
  "signals": [
    {{
      "type": "客户动态 / 内部协同 / 待办 / 风险 / 机会",
      "content": "信号内容",
      "source": "客户群 / 内部群",
      "time": "YYYY-MM-DD"
    }}
  ],
  "action_items": ["销售需要跟进的事项"],
  "insights": "对销售的洞察和建议（200字内）"
}}

## 消息内容

【客户群消息】
{customer_context}

【内部群消息】
{internal_context}

【全部消息（用于交叉验证）】
{all_context}
"""

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是 B2B 销售情报分析助手，擅长从飞书群聊中提炼客户动态、识别未响应风险、生成销售待办。"},
            {"role": "user", "content": prompt},
        ],
        temperature=1,
        max_tokens=8000,
    )

    result_text = resp.choices[0].message.content.strip()
    if result_text.startswith("```json"):
        result_text = result_text[7:]
    if result_text.startswith("```"):
        result_text = result_text[3:]
    if result_text.endswith("```"):
        result_text = result_text[:-3]
    result_text = result_text.strip()

    result = json.loads(result_text)

    out_json_path = DATA_DIR / f"{safe_account}_analysis.json"
    out_md_path = DATA_DIR / f"{safe_account}_analysis.md"
    history_path = DATA_DIR / f"{safe_account}_history.json"

    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    def md_section(title, content):
        if not content:
            return f"## {title}\n\n暂无。\n\n"
        if isinstance(content, list):
            body = "\n".join(f"- {item}" for item in content)
        elif isinstance(content, str):
            body = content
        else:
            body = json.dumps(content, ensure_ascii=False, indent=2)
        return f"## {title}\n\n{body}\n\n"

    md_lines = [
        f"# {args.account} 飞书群销售情报 · {date_range_str}",
        "",
        md_section("整体动态", result.get("summary", "")),
        md_section("客户群动态", result.get("customer_group_summary", "")),
        md_section("内部群动态", result.get("internal_group_summary", "")),
    ]

    md_lines.append("## 客户要求 × 内部响应\n\n")
    cross = result.get("cross_reference", [])
    if cross:
        for ref in cross:
            md_lines.append(f"**{ref.get('status', '')}**  ")
            md_lines.append(f"- 客户要求：{ref.get('customer_requirement', '')}")
            md_lines.append(f"- 内部响应：{ref.get('internal_response', '')}")
            md_lines.append("")
    else:
        md_lines.append("暂无交叉匹配数据。\n")

    md_lines.append("## 关键信号\n\n")
    signals = result.get("signals", [])
    if signals:
        for s in signals:
            md_lines.append(f"**[{s.get('type', '')}]** {s.get('content', '')}  ")
            md_lines.append(f"来源：{s.get('source', '')} · {s.get('time', '')}")
            md_lines.append("")
    else:
        md_lines.append("暂无信号。\n")

    md_lines.append(md_section("待办事项", result.get("action_items", [])))
    md_lines.append(md_section("销售洞察", result.get("insights", "")))

    with open(out_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    history = {}
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    last_message_time = ""
    if times:
        last_message_time = max(times).strftime("%Y-%m-%d %H:%M")

    history["account_name"] = args.account
    history["last_digest"] = {
        "file": out_md_path.name,
        "date_range": date_range_str,
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "message_count": len(messages),
        "last_message_time": last_message_time,
    }

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"分析完成：{out_json_path}")
    print(f"Markdown 摘要：{out_md_path}")
    print(f"历史指针：{history_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
