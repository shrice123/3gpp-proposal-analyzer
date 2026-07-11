#!/bin/bash
# 3GPP 提案分析工具 — 便携版一键启动
# 适用：Apple Silicon (M1/M2/M3) Mac
# 无需安装、无需网络、无需 Python

DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$DIR/proposal-backend"
FRONTEND="$DIR/desktop-dist"
PORT_BACKEND=8765
PORT_FRONTEND=3000

echo "══════════════════════════════════════"
echo "  3GPP 提案洞察 — 便携版"
echo "══════════════════════════════════════"

if [ ! -f "$BACKEND" ]; then
    echo "✗ 缺少后端文件：proposal-backend"
    echo "  请确保 proposal-backend 与此脚本在同一目录"
    exit 1
fi

chmod +x "$BACKEND" 2>/dev/null

echo "▶ 启动分析服务..."
"$BACKEND" &
BACKEND_PID=$!

for i in $(seq 1 30); do
    curl -s "http://127.0.0.1:$PORT_BACKEND/api/health" > /dev/null 2>&1 && break
    sleep 0.5
done

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "✗ 分析服务启动失败"
    exit 1
fi

echo "✓ 分析服务就绪"
echo "▶ 启动前端 HTTP 服务..."
echo ""
echo "  浏览器打开 → http://localhost:$PORT_FRONTEND"
echo "  按 Ctrl+C 停止"
echo ""

cleanup() {
    echo ""
    echo "▶ 正在停止..."
    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    echo "✓ 已停止"
    exit 0
}
trap cleanup INT TERM

cd "$FRONTEND"
python3 -m http.server $PORT_FRONTEND --bind 127.0.0.1 2>/dev/null
