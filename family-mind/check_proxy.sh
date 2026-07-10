#!/bin/bash
# FamilyMind 代理健康监控脚本
# 每 30 分钟由 crontab 触发，代理不通时自动重建 SSH 隧道

PROXY_URL="http://localhost:3128"
TEST_URL="https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=test&corpsecret=test"
AUTOSSH_BIN="/opt/homebrew/bin/autossh"
SSH_OPTS="-M 0 -f -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -L 3128:127.0.0.1:8888 root@36.151.144.153"
LOG_FILE="/Users/peter/family-mind/logs/proxy_health.log"

mkdir -p "$(dirname "$LOG_FILE")"

if curl -sf --max-time 8 -x "$PROXY_URL" "$TEST_URL" > /dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') proxy OK" >> "$LOG_FILE"
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') proxy DOWN, restarting..." >> "$LOG_FILE"

# 杀掉已有的 autossh / ssh 隧道进程
pkill -f "autossh.*3128:127.0.0.1:8888" 2>/dev/null
pkill -f "ssh.*3128:127.0.0.1:8888" 2>/dev/null
sleep 2

# 先尝试重启远程 tinyproxy（常见根因：远端进程假死）
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
    root@36.151.144.153 "systemctl restart tinyproxy" >> "$LOG_FILE" 2>&1
sleep 3

# 启动新隧道
$AUTOSSH_BIN $SSH_OPTS

sleep 3

# 二次验证
if curl -sf --max-time 8 -x "$PROXY_URL" "$TEST_URL" > /dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') proxy RESTORED" >> "$LOG_FILE"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') proxy RESTART FAILED" >> "$LOG_FILE"
fi
