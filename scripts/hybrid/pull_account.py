#!/usr/bin/env python3
"""混合来源项目群消息拉取脚本。

用法：
    python3 pull_account.py --account 千问腾讯

读取 data/projects/hybrid_accounts.json，按 group.source 分别调用：
- dingtalk: dws chat message list
- feishu: lark-cli im +chat-messages-list

合并、去重后保存到 data/projects/{account}_messages.json。
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
ACCOUNTS_PATH = PROJECT_ROOT / "data" / "projects" / "hybrid_accounts.json"
DATA_DIR = PROJECT_ROOT / "data" / "projects"
DWS_BIN = os.environ.get("DWS_BIN", "dws")
LARK_CLI = os.environ.get(
    "LARK_CLI",
    os.path.expanduser("~/.nvm/versions/node/v20.20.0/bin/lark-cli"),
)
WX_CLI_BIN = os.environ.get("WX_CLI_BIN", "wx")


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


def run_wx_cli(args: list, timeout: int = 300) -> dict:
    cmd = [WX_CLI_BIN] + args
    print(f"[wx] {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(f"找不到 wx: {WX_CLI_BIN}。请确认已安装并加入 PATH，或通过 WX_CLI_BIN 环境变量指定。") from e
    if result.returncode != 0:
        raise RuntimeError(f"wx 执行失败 (code={result.returncode}): {result.stderr or result.stdout}")
    text = result.stdout.strip()
    if not text:
        return {}
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"无法解析 wx 输出: {e}\n{text[:500]}") from e


def pull_wechat_group(chat_name: str, since: Optional[str] = None, until: Optional[str] = None, limit: int = 500) -> list:
    """拉取单个微信群的消息，返回消息列表（按时间倒序）。

    jackwener/wx 的 `history` 命令：
        wx history <CHAT> --since YYYY-MM-DD --until YYYY-MM-DD -n <LIMIT> --json
    返回消息字段：local_id, sender, content, time, timestamp, type
    """
    args = ["history", chat_name, "--json"]
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if limit:
        args.extend(["-n", str(limit)])

    data = run_wx_cli(args)
    messages = data if isinstance(data, list) else (data.get("messages") or data.get("data", {}).get("messages", []) or [])
    if not isinstance(messages, list):
        print(f"  wx 返回的消息格式异常: {type(messages)}", file=sys.stderr)
        return []

    normalized = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        create_time = msg.get("time") or ""
        sender_name = msg.get("sender") or "未知"
        content = msg.get("content") or ""
        msg_id = str(msg.get("local_id") or "")

        normalized.append({
            "message_id": msg_id,
            "create_time": create_time,
            "createTime": create_time,
            "sender": {"name": sender_name, "id": sender_name},
            "sender_name": sender_name,
            "content": content,
            "source": "wechat",
            "chat_name": chat_name,
            "chat_type": "group",
            "msg_type": msg.get("type", ""),
            "timestamp": msg.get("timestamp"),
            "raw": msg,
        })
    return normalized


def pull_dingtalk_group(open_conversation_id: str, cutoff: Optional[datetime] = None, limit: int = 100) -> List[dict]:
    """拉取钉钉群消息，按时间倒序（从新到旧），遇到早于 cutoff 的消息时停止。"""
    messages = []
    now = datetime.now(timezone(timedelta(hours=8)))
    time_param = now.strftime("%Y-%m-%d %H:%M:%S")
    page = 0
    while True:
        page += 1
        try:
            resp = run_dws([
                "chat", "message", "list",
                "--group", open_conversation_id,
                "--time", time_param,
                "--direction", "older",
                "--limit", str(limit),
            ])
        except subprocess.CalledProcessError as e:
            print(f"  page {page} 拉取失败: {e.stderr or e.stdout}", file=sys.stderr)
            break
        result = resp.get("result", {})
        batch = result.get("messages", []) or []
        has_more = result.get("hasMore", False)
        next_cursor = result.get("nextCursor")

        # 只保留晚于 cutoff 的消息；一旦遇到早于等于 cutoff 的，说明后续都更旧，停止
        batch_new = []
        for msg in batch:
            t_str = msg.get("createTime", "")
            try:
                t = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                t = None
            if cutoff and t and t <= cutoff:
                break
            batch_new.append(msg)

        print(f"  page {page}: got {len(batch)} messages, usable {len(batch_new)}, hasMore={has_more}")
        if batch_new:
            messages.extend(batch_new)
        if len(batch_new) < len(batch):
            break
        if not has_more or not next_cursor:
            break
        try:
            next_ts = int(next_cursor)
            dt = datetime.fromtimestamp(next_ts / 1000, tz=timezone(timedelta(hours=8)))
            time_param = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            print(f"  无法解析 nextCursor: {next_cursor}，停止翻页", file=sys.stderr)
            break
    return messages


def pull_feishu_group(chat_id: str, tmp_path: Path) -> List[dict]:
    if not os.path.exists(LARK_CLI):
        raise FileNotFoundError(f"找不到 lark-cli: {LARK_CLI}")
    cmd = [
        LARK_CLI,
        "im",
        "+chat-messages-list",
        "--chat-id", chat_id,
        "--as", "user",
        "--page-size", "50",
        "--order", "desc",
        "--format", "json",
    ]
    print(f"[lark] {' '.join(cmd)} > {tmp_path}")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        subprocess.run(cmd, stdout=f, check=True)
    with open(tmp_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("data", {}).get("messages", []) or []


def normalize_message(msg: dict, source: str, chat_name: str, chat_type: str) -> dict:
    """统一字段，方便下游分析。保留原始字段，追加标准化字段。"""
    msg = dict(msg)
    msg["source"] = source
    msg["chat_name"] = chat_name
    msg["chat_type"] = chat_type

    # 时间统一为 create_time（保留原格式）
    if "createTime" in msg and "create_time" not in msg:
        msg["create_time"] = msg["createTime"]
    if "create_time" in msg and "createTime" not in msg:
        msg["createTime"] = msg["create_time"]

    # 发送人名称统一
    sender_name = ""
    sender = msg.get("sender")
    if isinstance(sender, dict):
        sender_name = sender.get("name") or sender.get("id") or ""
    elif isinstance(sender, str):
        sender_name = sender
    msg["sender_name"] = sender_name

    # 内容统一
    if "content" not in msg:
        msg["content"] = ""

    # ID 统一
    if "message_id" not in msg and "openMessageId" in msg:
        msg["message_id"] = msg["openMessageId"]
    if "openMessageId" not in msg and "message_id" in msg:
        msg["openMessageId"] = msg["message_id"]

    return msg


def load_existing_messages(account: str) -> List[dict]:
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


def infer_cutoff(existing_messages: List[dict]) -> Optional[datetime]:
    """从已有消息里找最新的时间，作为增量拉取起点。"""
    times = []
    for m in existing_messages:
        t = m.get("createTime") or m.get("create_time", "")
        if t:
            try:
                times.append(datetime.strptime(t, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass
    if not times:
        return None
    latest = max(times) - timedelta(hours=1)
    return latest


def main():
    parser = argparse.ArgumentParser(description="拉取混合来源项目群消息")
    parser.add_argument("--account", required=True, help="账户名，如 千问腾讯")
    parser.add_argument("--tmp-dir", help="临时目录")
    parser.add_argument("--full", action="store_true", help="全量拉取（忽略已有消息）")
    args = parser.parse_args()

    accounts = load_accounts()
    account_config = accounts.get("accounts", {}).get(args.account)
    if not account_config:
        print(f"错误：找不到账户 {args.account}，请检查 {ACCOUNTS_PATH}", file=sys.stderr)
        sys.exit(1)

    existing = [] if args.full else load_existing_messages(args.account)
    cutoff = None if args.full else infer_cutoff(existing)
    if not cutoff and not args.full:
        cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=7)).replace(tzinfo=None)
        print(f"[info] 无历史消息，默认拉取最近 7 天: {cutoff.strftime('%Y-%m-%d %H:%M:%S')}")
    elif cutoff:
        print(f"[info] 增量起点: {cutoff.strftime('%Y-%m-%d %H:%M:%S')}")

    tmp_dir = Path(args.tmp_dir or f"/tmp/hybrid_{sanitize(args.account)}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    all_messages = []
    for group in account_config.get("groups", []):
        source = group.get("source")
        chat_name = group["name"]
        chat_type = group.get("type", "unknown")
        group_id = group["id"]
        print(f"\n[pull] [{source}] {chat_name} ({group_id})")
        try:
            if source == "dingtalk":
                batch = pull_dingtalk_group(group_id, cutoff=cutoff)
            elif source == "feishu":
                feishu_tmp = tmp_dir / f"{sanitize(chat_name)}.json"
                batch = pull_feishu_group(group_id, feishu_tmp)
            elif source == "wechat":
                since_str = cutoff.strftime("%Y-%m-%d") if cutoff else None
                until_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                batch = pull_wechat_group(chat_name, since=since_str, until=until_str, limit=500)
            else:
                print(f"  未知来源: {source}，跳过", file=sys.stderr)
                continue
        except subprocess.CalledProcessError as e:
            print(f"  拉取失败: {e.stderr or e.stdout}", file=sys.stderr)
            continue
        except (subprocess.TimeoutExpired, RuntimeError, FileNotFoundError) as e:
            print(f"  拉取失败: {e}", file=sys.stderr)
            continue

        for msg in batch:
            all_messages.append(normalize_message(msg, source, chat_name, chat_type))

    # 与已有消息合并、去重
    seen = set()
    merged_messages = []
    for m in existing + all_messages:
        mid = m.get("message_id") or m.get("openMessageId")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        merged_messages.append(m)

    # 按时间倒序
    def sort_key(m):
        t = m.get("create_time") or m.get("createTime") or ""
        return t

    merged_messages.sort(key=sort_key, reverse=True)

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
