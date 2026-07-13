#!/bin/bash
# 拉取环平保险两个飞书群最近消息
set -e

OUT_DIR="/tmp/feishu_huanping"
mkdir -p "$OUT_DIR"

LARK_CLI="$HOME/.nvm/versions/node/v20.20.0/bin/lark-cli"

# 客户群
"$LARK_CLI" im +chat-messages-list --chat-id oc_a288086c5090e188af23742cb40e61d8 --as user --page-size 50 --order desc --format json > "$OUT_DIR/customer.json"
echo "客户群消息已保存到 $OUT_DIR/customer.json"

# 内部群
"$LARK_CLI" im +chat-messages-list --chat-id oc_826147c7ff46ad39a5285bec50672d62 --as user --page-size 50 --order desc --format json > "$OUT_DIR/internal.json"
echo "内部群消息已保存到 $OUT_DIR/internal.json"

# 合并为 import_feishu_messages.py 需要的格式
python3 - "$OUT_DIR/customer.json" "$OUT_DIR/internal.json" "$OUT_DIR/messages.json" <<'PY'
import json
import sys

customer_path, internal_path, out_path = sys.argv[1:4]

messages = []
for path, chat_name in [(customer_path, "环平保险代运营客户群"), (internal_path, "环平保险内部群")]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for msg in data.get("data", {}).get("messages", []):
        msg["chat_name"] = chat_name
        messages.append(msg)

# 按时间倒序
messages.sort(key=lambda m: m.get("create_time", ""), reverse=True)

with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"data": {"messages": messages}}, f, ensure_ascii=False, indent=2)

print(f"合并后共 {len(messages)} 条消息，保存到 {out_path}")
PY
