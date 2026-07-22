#!/usr/bin/env python3
"""通用飞书群消息拉取脚本。

用法：
    python3 pull_account.py --account 红果星广

读取 data/projects/feishu_accounts.json 中的账户配置，
用 lark-cli 拉取每个群的消息，合并后存到：
    /tmp/feishu_{account}/messages.json
    data/projects/{account}_messages.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_PATH = PROJECT_ROOT / "data" / "projects" / "feishu_accounts.json"
DATA_DIR = PROJECT_ROOT / "data" / "projects"
LARK_CLI = os.path.expanduser("~/.nvm/versions/node/v20.20.0/bin/lark-cli")


def load_accounts():
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9一-龥]", "_", name)


def run_lark_cli(chat_id: str, start: str, end: str, page_token: str = "") -> dict:
    if not os.path.exists(LARK_CLI):
        raise FileNotFoundError(f"找不到 lark-cli: {LARK_CLI}")
    cmd = [
        LARK_CLI,
        "im",
        "+chat-messages-list",
        "--chat-id",
        chat_id,
        "--as",
        "user",
        "--page-size",
        "50",
        "--order",
        "desc",
        "--start",
        start,
        "--end",
        end,
        "--format",
        "json",
    ]
    if page_token:
        cmd += ["--page-token", page_token]
    print(f"[pull] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  拉取失败: {result.stderr}", file=sys.stderr)
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  JSON 解析失败，输出长度 {len(result.stdout)}", file=sys.stderr)
        return {}


def pull_group(chat_id: str, chat_name: str, days: int = 7, max_days: int = 90) -> list:
    """按时间段分片拉取群消息，返回按时间倒序的消息列表。"""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    all_messages = []
    cursor_end = now
    total_days = 0

    while total_days < max_days:
        cursor_start = cursor_end - timedelta(days=days)
        start_str = cursor_start.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        end_str = cursor_end.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        # 窗口内翻页：has_more 只表示"本窗口内还有下一页"，不代表"没有更早的历史"
        page_token = ""
        week_count = 0
        while True:
            data = run_lark_cli(chat_id, start_str, end_str, page_token)
            messages = data.get("data", {}).get("messages", []) or []
            has_more = data.get("data", {}).get("has_more", False)
            page_token = data.get("data", {}).get("page_token", "") or ""
            if messages:
                for msg in messages:
                    msg["chat_name"] = chat_name
                all_messages.extend(messages)
                week_count += len(messages)
            if not has_more or not page_token:
                break
        print(f"  {cursor_start.date()} ~ {cursor_end.date()}: got {week_count} messages")

        cursor_end = cursor_start
        total_days += days

    # 去重（按 message_id）
    seen = set()
    unique = []
    for m in all_messages:
        mid = m.get("message_id")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        unique.append(m)
    unique.sort(key=lambda m: m.get("create_time", ""), reverse=True)
    return unique


def main():
    parser = argparse.ArgumentParser(description="拉取飞书群消息")
    parser.add_argument("--account", required=True, help="账户名，如 红果星广")
    parser.add_argument("--tmp-dir", help="临时目录，默认 /tmp/feishu_{account}")
    parser.add_argument("--full", action="store_true", help="全量拉取，默认最近 7 天")
    args = parser.parse_args()

    accounts = load_accounts()
    account_config = accounts.get("accounts", {}).get(args.account)
    if not account_config:
        print(f"错误：找不到账户 {args.account}，请检查 {ACCOUNTS_PATH}", file=sys.stderr)
        sys.exit(1)

    tmp_dir = Path(args.tmp_dir or f"/tmp/feishu_{sanitize(args.account)}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    all_messages = []
    for group in account_config.get("groups", []):
        chat_id = group["id"]
        chat_name = group["name"]
        group_type = group.get("type", "unknown")
        max_days = 90 if args.full else 7
        messages = pull_group(chat_id, chat_name, days=7, max_days=max_days)
        for msg in messages:
            msg["chat_type"] = group_type
        all_messages.extend(messages)

    # 与已有项目文件合并（按 message_id 去重），增量拉取不再清空历史
    project_merged_path = DATA_DIR / f"{sanitize(args.account)}_messages.json"
    if project_merged_path.exists():
        try:
            with open(project_merged_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_messages = old.get("data", {}).get("messages", []) or []
            new_ids = {m.get("message_id") for m in all_messages if m.get("message_id")}
            kept = [m for m in old_messages if m.get("message_id") not in new_ids]
            if kept:
                print(f"[merge] 保留历史消息 {len(kept)} 条，新增/更新 {len(all_messages)} 条")
            all_messages.extend(kept)
        except Exception as e:
            print(f"[merge] 读取历史文件失败，仅保存本次拉取: {e}", file=sys.stderr)

    # 按时间倒序
    all_messages.sort(key=lambda m: m.get("create_time", ""), reverse=True)

    merged = {"data": {"messages": all_messages}}
    tmp_merged_path = tmp_dir / "messages.json"
    with open(tmp_merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    with open(project_merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"合并后共 {len(all_messages)} 条消息")
    print(f"临时文件：{tmp_merged_path}")
    print(f"项目文件：{project_merged_path}")


if __name__ == "__main__":
    main()
