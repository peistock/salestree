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


def pull_group(chat_id: str, out_path: Path):
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
        "--format",
        "json",
    ]
    print(f"[pull] {' '.join(cmd)} > {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        subprocess.run(cmd, stdout=f, check=True)


def main():
    parser = argparse.ArgumentParser(description="拉取飞书群消息")
    parser.add_argument("--account", required=True, help="账户名，如 红果星广")
    parser.add_argument("--tmp-dir", help="临时目录，默认 /tmp/feishu_{account}")
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
        out_path = tmp_dir / f"{sanitize(chat_name)}.json"
        pull_group(chat_id, out_path)

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for msg in data.get("data", {}).get("messages", []):
            msg["chat_name"] = chat_name
            msg["chat_type"] = group_type
            all_messages.append(msg)

    # 按时间倒序
    all_messages.sort(key=lambda m: m.get("create_time", ""), reverse=True)

    merged = {"data": {"messages": all_messages}}
    tmp_merged_path = tmp_dir / "messages.json"
    with open(tmp_merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    project_merged_path = DATA_DIR / f"{sanitize(args.account)}_messages.json"
    with open(project_merged_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"合并后共 {len(all_messages)} 条消息")
    print(f"临时文件：{tmp_merged_path}")
    print(f"项目文件：{project_merged_path}")


if __name__ == "__main__":
    main()
