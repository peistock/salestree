#!/usr/bin/env python3
"""混合来源项目群销售情报分析脚本。

用法：
    python3 analyze_account.py --account 千问腾讯

读取 data/projects/{account}_messages.json，输出：
    data/projects/{account}_analysis.json
    data/projects/{account}_analysis.md
    data/projects/{account}_history.json

支持消息中同时包含飞书（create_time）和钉钉（createTime）字段。
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
ACCOUNTS_PATH = PROJECT_ROOT / "data" / "projects" / "hybrid_accounts.json"
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
    parser = argparse.ArgumentParser(description="分析混合来源项目群销售情报")
    parser.add_argument("--account", required=True, help="账户名，如 千问腾讯")
    parser.add_argument("--model", help="覆盖 MODEL_DAILY 环境变量")
    parser.add_argument("--days", type=int, default=7, help="只分析最近 N 天的消息")
    parser.add_argument("--max-messages", type=int, default=200, help="最多分析消息条数")
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

    # 过滤：只分析最近 N 天，且不超过最大条数
    cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=args.days)
    cutoff_naive = cutoff.replace(tzinfo=None)
    filtered = []
    for m in messages:
        t = parse_time(m.get("create_time") or m.get("createTime", ""))
        if t:
            t_naive = t.replace(tzinfo=None) if t.tzinfo else t
            if t_naive < cutoff_naive:
                continue
        filtered.append(m)
    if len(filtered) > args.max_messages:
        filtered = filtered[:args.max_messages]
    messages = filtered
    print(f"[info] 加载 {len(data.get('data', {}).get('messages', []))} 条，分析最近 {args.days} 天内的 {len(messages)} 条")

    # 按 chat_name 分组，分别整理客户群和内部群
    from collections import defaultdict

    customer_groups = defaultdict(list)
    internal_groups = defaultdict(list)
    all_lines = []
    for m in messages:
        chat = m.get("chat_name", "") or "未知群"
        chat_type = m.get("chat_type", "")
        sender = m.get("sender_name", "")
        source = m.get("source", "")
        time = m.get("create_time") or m.get("createTime", "")
        content = m.get("content", "")
        line = f"[{source}] [{chat}] {time} {sender}:\n{content}\n"
        all_lines.append(line)
        is_customer = chat_type == "customer"
        is_internal = chat_type == "internal"
        if not is_customer and not is_internal:
            if "客户" in chat or "投放" in chat or "素材" in chat or "外部" in chat:
                is_customer = True
            else:
                is_internal = True
        if is_customer:
            customer_groups[chat].append(line)
        if is_internal:
            internal_groups[chat].append(line)

    def build_group_context(groups: dict) -> str:
        parts = []
        for chat_name, lines in groups.items():
            parts.append(f"===== {chat_name} =====\n" + "\n".join(lines))
        return "\n\n".join(parts)

    customer_context = build_group_context(customer_groups)
    internal_context = build_group_context(internal_groups)
    all_context = "\n".join(all_lines)

    times = [parse_time(m.get("create_time") or m.get("createTime", "")) for m in messages]
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

    def clean_json_text(text: str) -> str:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def llm_json(client: OpenAI, model: str, system: str, user: str, max_tokens: int = 8000) -> dict:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=1,
            max_tokens=max_tokens,
        )
        raw = resp.choices[0].message.content or ""
        print(f"[llm] finish_reason={resp.choices[0].finish_reason} usage={resp.usage} raw_len={len(raw)}")
        text = clean_json_text(raw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            debug_path = DATA_DIR / f"{safe_account}_analysis_raw_{datetime.now().strftime('%H%M%S')}.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[error] JSON 解析失败: {e}", file=sys.stderr)
            print(f"[debug] 原始输出已保存到 {debug_path}", file=sys.stderr)
            sys.exit(1)

    # Step 1a: 客户群分群摘要
    customer_prompt = f"""你是 B2B 销售情报分析专家。请根据下面的客户群消息，为每个客户群生成简洁摘要。

## 要求
- 必须按群分述，**每个群一个独立的一级标题（# / ##）**，标题用群名。
- 每个群下按话题组织，每个话题一句话，包含「时间 + 参与者 + 核心内容 + 关键原话」。
- 每个群摘要控制在 200 字以内，整体输出简洁。

## 输出格式
必须严格输出以下 JSON，不要加任何额外解释：

{{
  "customer_group_summary": "客户群关键内容。按群/话题组织。"
}}

## 客户群消息
{customer_context}
"""

    customer_part = llm_json(
        client,
        model,
        "你是 B2B 销售情报分析助手，擅长从群聊中提炼客户动态。",
        customer_prompt,
        max_tokens=8000,
    )

    # Step 1b: 内部群分群摘要
    internal_prompt = f"""你是 B2B 销售情报分析专家。请根据下面的内部群消息，为每个内部群生成简洁摘要。

## 要求
- 必须按群分述，**每个群一个独立的一级标题（# / ##）**，标题用群名。
- 每个群下按话题组织，每个话题一句话，包含「时间 + 参与者 + 核心内容 + 关键原话」。
- 每个群摘要控制在 200 字以内，整体输出简洁。

## 输出格式
必须严格输出以下 JSON，不要加任何额外解释：

{{
  "internal_group_summary": "内部群关键内容。按群/话题组织。"
}}

## 内部群消息
{internal_context}
"""

    internal_part = llm_json(
        client,
        model,
        "你是 B2B 销售情报分析助手，擅长从群聊中提炼内部协同动态。",
        internal_prompt,
        max_tokens=8000,
    )

    # Step 2: 基于摘要与原始消息生成交叉验证、信号、待办、洞察
    details_prompt = f"""你是 B2B 销售情报分析专家。请基于以下已整理的客户群/内部群摘要与原始消息，提炼整体摘要、交叉验证、关键信号、待办事项与销售洞察。

## 分析要求
1. **整体摘要**：200 字内概括本周期客户与内部的总体动态。
2. **交叉验证**：把客户群提出的每一条「要求 / 问题 / 风险 / deadline」，拿到内部群里找响应。
   - 已响应：内部群有明确执行人、方案或闭环
   - 部分响应：有回应但无最终闭环
   - 未响应：内部群完全没有提到
   对「未响应」和「部分响应」要重点标出，并提醒销售跟进。
3. **信号提取**：最多提炼 5 条关键信号，类型包括：客户动态、内部协同、待办、风险、机会。
4. **待办事项**：列出销售需要直接跟进的事项，最多 5 条，按紧急程度排序，每条不超过 80 字。
5. **销售洞察**：给出 150 字内的洞察和建议，指出销售该主动介入的点。

## 输出格式
必须严格输出以下 JSON，不要加任何额外解释：

{{
  "summary": "整体动态摘要（200字内）",
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
  "insights": "对销售的洞察和建议（150字内）"
}}

## 已整理的群摘要

客户群摘要：
{customer_part.get("customer_group_summary", "")}

内部群摘要：
{internal_part.get("internal_group_summary", "")}

## 内部群原始消息（用于交叉验证客户要求是否有内部响应）
{internal_context}
"""

    details_part = llm_json(
        client,
        model,
        "你是 B2B 销售情报分析助手，擅长从群聊中提炼客户动态、识别未响应风险、生成销售待办。",
        details_prompt,
        max_tokens=8000,
    )

    result = {
        "account_name": args.account,
        "date_range": date_range_str,
        "summary": details_part.get("summary", ""),
        "customer_group_summary": customer_part.get("customer_group_summary", ""),
        "internal_group_summary": internal_part.get("internal_group_summary", ""),
        "cross_reference": details_part.get("cross_reference", []),
        "signals": details_part.get("signals", []),
        "action_items": details_part.get("action_items", []),
        "insights": details_part.get("insights", ""),
    }

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
        f"# {args.account} 项目群销售情报 · {date_range_str}",
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
