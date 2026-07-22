#!/bin/bash
# 工作日早间资讯采集脚本
# 采集最近 7 天的微信公众号文章，分析、提取线索、生成 digest.html

set -e

# 从脚本自身位置推导项目根目录（兼容目录改名/迁移）
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 加载环境变量
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/.env"
  set +a
fi

SKILL_DIR="$PROJECT_ROOT/third_party/wechat-digest-skill"
PY="$PROJECT_ROOT/venv/bin/python"
[ -x "$PY" ] || PY="/Users/cpp/xiaoxiao/venv/bin/python"
[ -x "$PY" ] || PY="python3"

# 最近 7 天
SINCE=$(date -v-7d +%Y-%m-%d)
COUNT=10

ACCOUNTS=(
  "星图数据"
  "巨量引擎营销观察"
  "沥金"
  "腾讯营销"
  "深响"
  "阿里妈妈数字营销"
  "QuestMobile"
  "支付宝广告"
  "小红书商业动态"
  "TikTok Shop跨境电商"
  "TikTok for Business出海营销"
)

cd "$SKILL_DIR"

echo "[$(date)] 开始采集，since=$SINCE"
for acc in "${ACCOUNTS[@]}"; do
  echo "[$(date)] 采集: $acc"
  "$PY" wechat_collector.py collect "$acc" --since "$SINCE" --count "$COUNT" 2>&1 | tail -15
  sleep 5
done

echo "[$(date)] 开始分析未分析文章"
"$PY" analyze_kb.py 2>&1 | tail -10

echo "[$(date)] 开始提取销售线索"
"$PY" extract_leads_kb.py --max 50 2>&1 | tail -10

echo "[$(date)] 生成离线工作台"
"$PY" kb.py export-html 2>&1 | tail -5

echo "[$(date)] 同步到销销知识库"
set +e
SYNC_OUTPUT=$(PYTHONPATH="$PROJECT_ROOT" "$PY" -m mind.wechat_digest sync 2>&1)
SYNC_STATUS=$?
set -e
if [ $SYNC_STATUS -eq 0 ]; then
  echo "$SYNC_OUTPUT" | tail -5
  echo "[$(date)] 同步完成"
else
  echo "[$(date)] 同步失败: 本地 LM Studio 未启动（:1234）或 embedding 模型未加载。"
  echo "         Agent 仍会通过 knowledge_base.json 离线读取资讯看板内容。"
  echo "$SYNC_OUTPUT" | tail -3
fi

echo "[$(date)] 完成"
