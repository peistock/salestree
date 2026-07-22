#!/usr/bin/env python3
"""通用钉钉群消息拉取脚本。

用法：
    python3 pull_account.py --account 海南亿科

读取 data/projects/dingtalk_accounts.json 中的账户配置，
用 dws chat message list 拉取每个群的消息（自动翻页），合并后存到：
    /tmp/dingtalk_{account}/messages.json
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
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_PATH = PROJECT_ROOT / "data" / "projects" / "dingtalk_accounts.json"
DATA_DIR = PROJECT_ROOT / "data" / "projects"
DWS_BIN = os.environ.get("DWS_BIN", "dws")  # 默认从 PATH 找 dws


def load_accounts():
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9一-龥]", "_", name)


def run_dws(args: list) -> dict:
    cmd = [DWS_BIN] + args + ["--format", "json"]
    print(f"[dws] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def parse_create_time(t: str) -> datetime:
    return datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))


def pull_group(open_conversation_id: str, since: Optional[str] = None, limit: int = 100):
    """拉取单个群的消息，自动翻页，返回消息列表（按时间倒序）。

    使用 --direction newer 从 since 往现在拉，确保增量更新能拿到最新消息。
    """
    messages = []
    time_param = since or (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    page = 0
    while True:
        page += 1
        try:
            resp = run_dws([
                "chat", "message", "list",
                "--group", open_conversation_id,
                "--time", time_param,
                "--direction", "newer",
                "--limit", str(limit),
            ])
        except subprocess.CalledProcessError as e:
            print(f"  page {page} 拉取失败: {e.stderr or e.stdout}", file=sys.stderr)
            break
        result = resp.get("result", {})
        batch = result.get("messages", []) or []
        has_more = result.get("hasMore", False)
        next_cursor = result.get("nextCursor")
        print(f"  page {page}: got {len(batch)} messages, hasMore={has_more}")
        if batch:
            messages.extend(batch)
        if not has_more or not next_cursor:
            break
        # nextCursor 是时间戳（毫秒），转成 dws 要求的时间格式
        try:
            next_ts = int(next_cursor)
            dt = datetime.fromtimestamp(next_ts / 1000, tz=timezone(timedelta(hours=8)))
            time_param = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            print(f"  无法解析 nextCursor: {next_cursor}，停止翻页", file=sys.stderr)
            break
    return messages


def load_existing_messages(account: str) -> List[dict]:
    """读取该账户已有的消息文件，返回消息列表。"""
    path = DATA_DIR / f"{sanitize(account)}_messages.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("data", {}).get("messages", []) or []
    except Exception as e:
        print(f"  读取已有消息文件失败: {e}", file=sys.stderr)
        return []


def infer_since(existing_messages: List[dict]) -> Optional[str]:
    """从已有消息里找最新的 createTime，作为增量拉取起点。"""
    times = []
    for m in existing_messages:
        t = m.get("createTime")
        if t:
            try:
                times.append(datetime.strptime(t, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass
    if not times:
        return None
    # 往回退 1 小时，避免时区/秒级偏差漏消息
    latest = max(times) - timedelta(hours=1)
    return latest.strftime("%Y-%m-%d %H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description="拉取钉钉群消息")
    parser.add_argument("--account", required=True, help="账户名，如 海南亿科")
    parser.add_argument("--tmp-dir", help="临时目录，默认 /tmp/dingtalk_{account}")
    parser.add_argument("--since", help="起始时间，格式 yyyy-MM-dd HH:mm:ss；不传则增量拉取")
    parser.add_argument("--limit", type=int, default=100, help="每页消息数")
    parser.add_argument("--full", action="store_true", help="全量拉取（忽略已有消息）")
    args = parser.parse_args()

    accounts = load_accounts()
    account_config = accounts.get("accounts", {}).get(args.account)
    if not account_config:
        print(f"错误：找不到账户 {args.account}，请检查 {ACCOUNTS_PATH}", file=sys.stderr)
        sys.exit(1)

    existing = [] if args.full else load_existing_messages(args.account)
    since = args.since
    if not since and not args.full:
        since = infer_since(existing)
    if not since:
        # 首次全量兜底：最近 7 天
        since = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[info] 无历史消息，默认拉取最近 7 天: {since}")
    else:
        print(f"[info] 增量起点: {since}")

    tmp_dir = Path(args.tmp_dir or f"/tmp/dingtalk_{sanitize(args.account)}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    all_messages = []
    for group in account_config.get("groups", []):
        open_id = group["id"]
        chat_name = group["name"]
        group_type = group.get("type", "unknown")
        print(f"\n[pull] {chat_name} ({open_id})")
        try:
            messages = pull_group(open_id, since=since, limit=args.limit)
        except subprocess.CalledProcessError as e:
            print(f"  拉取失败: {e.stderr or e.stdout}", file=sys.stderr)
            continue
        for msg in messages:
            msg["chat_name"] = chat_name
            msg["chat_type"] = group_type
        all_messages.extend(messages)

    # 与已有消息合并、去重、按时间倒序
    seen = set()
    merged_messages = []
    for m in existing + all_messages:
        mid = m.get("openMessageId")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        merged_messages.append(m)
    merged_messages.sort(key=lambda m: m.get("createTime", ""), reverse=True)

    merged = {"data": {"messages": merged_messages}}
    tmp_merged_path = tmp_dir / "messages.json"
    with open(tmp_merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    project_merged_path = DATA_DIR / f"{sanitize(args.account)}_messages.json"
    with open(project_merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\n合并后共 {len(merged_messages)} 条消息（新增 {len(all_messages)}，去重 {len(existing) + len(all_messages) - len(merged_messages)}）")
    print(f"临时文件：{tmp_merged_path}")
    print(f"项目文件：{project_merged_path}")


if __name__ == "__main__":
    main()
