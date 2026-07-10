#!/bin/bash
# 工作日早间资讯采集脚本
# 采集最近 7 天的微信公众号文章，分析、提取线索、生成 digest.html

set -e

PROJECT_ROOT="/Users/cpp/sales-mind"
SKILL_DIR="$PROJECT_ROOT/third_party/wechat-digest-skill"
PY="$PROJECT_ROOT/venv/bin/python"

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

echo "[$(date)] 完成"
