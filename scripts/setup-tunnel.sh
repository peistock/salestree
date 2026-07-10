#!/bin/bash
# Cloudflare Tunnel 一键配置脚本
# 运行方式: bash scripts/setup-tunnel.sh

set -e

TUNNEL_NAME="sales-mind"
CONFIG_DIR="$HOME/.cloudflared"

echo "=== SalesMind Cloudflare Tunnel 配置 ==="

# 1. 检查 cloudflared
if ! command -v cloudflared &> /dev/null; then
    echo "正在安装 cloudflared..."
    if command -v brew &> /dev/null; then
        brew install cloudflared
    else
        echo "错误: 请手动安装 cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/"
        exit 1
    fi
fi

echo "cloudflared 已安装"

# 2. 登录 Cloudflare（首次需要浏览器授权）
if [ ! -f "$CONFIG_DIR/cert.pem" ]; then
    echo ""
    echo "首次使用需要登录 Cloudflare，即将打开浏览器..."
    echo "登录后请关闭浏览器，回到终端继续"
    read -p "按回车继续..."
    cloudflared tunnel login
fi

# 3. 创建命名隧道（如果不存在）
if [ ! -f "$CONFIG_DIR/${TUNNEL_NAME}.json" ]; then
    echo ""
    echo "创建命名隧道: $TUNNEL_NAME"
    cloudflared tunnel create "$TUNNEL_NAME"
else
    echo "隧道 $TUNNEL_NAME 已存在"
fi

# 4. 获取 Tunnel ID
TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}' | head -1)

if [ -z "$TUNNEL_ID" ]; then
    echo "错误: 无法获取 Tunnel ID"
    exit 1
fi

echo "Tunnel ID: $TUNNEL_ID"

# 5. 写入配置文件
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/config.yml" << EOF
tunnel: $TUNNEL_ID
credentials-file: $CONFIG_DIR/$TUNNEL_ID.json

ingress:
  - service: http://localhost:8000
EOF

echo ""
echo "=== 配置完成 ==="
echo ""
echo "你的固定公网 URL 是:"
echo "  https://${TUNNEL_ID}.cfargotunnel.com"
echo ""
echo "企微后台「接收消息」URL 填:"
echo "  https://${TUNNEL_ID}.cfargotunnel.com/wechat"
echo ""
echo "启动隧道命令:"
echo "  cloudflared tunnel run $TUNNEL_NAME"
echo ""
echo "或者后台运行:"
echo "  cloudflared tunnel run $TUNNEL_NAME &"
echo ""
