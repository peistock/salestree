#!/bin/bash
# SalesMind 一键启动（Docker + Cloudflare Tunnel）
# 运行方式: bash scripts/start-all.sh

set -e

echo "=== SalesMind 启动 ==="

# 1. 启动 Docker 服务
echo "启动 PostgreSQL + App..."
cd "$(dirname "$0")/.."
docker-compose up -d

# 2. 等待服务就绪
echo "等待服务就绪..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ 本地服务已启动: http://localhost:8000"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo "❌ 服务启动超时，检查日志: docker-compose logs app"
        exit 1
    fi
done

# 3. 检查隧道配置
TUNNEL_NAME="sales-mind"
CONFIG_DIR="$HOME/.cloudflared"

if [ ! -f "$CONFIG_DIR/${TUNNEL_NAME}.json" ]; then
    echo ""
    echo "⚠️  Cloudflare Tunnel 未配置"
    echo "请先运行: bash scripts/setup-tunnel.sh"
    exit 1
fi

# 4. 获取 Tunnel ID 并显示 URL
TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}' | head -1)
if [ -n "$TUNNEL_ID" ]; then
    echo ""
    echo "🌐 公网访问地址: https://${TUNNEL_ID}.cfargotunnel.com"
    echo "📱 企微后台 URL: https://${TUNNEL_ID}.cfargotunnel.com/wechat"
    echo ""
fi

# 5. 启动隧道（后台运行）
echo "启动 Cloudflare Tunnel..."
nohup cloudflared tunnel run "$TUNNEL_NAME" > "$CONFIG_DIR/tunnel.log" 2>&1 &
echo "✅ Tunnel 已后台启动"
echo ""
echo "查看日志:"
echo "  应用日志: docker-compose logs -f app"
echo "  隧道日志: tail -f ~/.cloudflared/tunnel.log"
echo ""
echo "停止服务:"
echo "  docker-compose down"
echo "  pkill cloudflared"
