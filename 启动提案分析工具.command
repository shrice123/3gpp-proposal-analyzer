#!/bin/bash

# 3GPP 提案分析工具：源码网页版一键启动（macOS）
# 一个本地进程同时提供 API 与构建后的网页，不使用开发服务器。

set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$ROOT/.runtime"
LOG_FILE="$RUNTIME_DIR/proposal-tool.log"
PID_FILE="$RUNTIME_DIR/proposal-tool.pid"
WEB_DIST="$ROOT/desktop-dist"
SOURCE_PYTHON="$ROOT/.venv/bin/python"
SOURCE_BACKEND="$ROOT/backend/run.py"
PACKAGED_BACKEND="$ROOT/backend/dist/proposal-backend"
URL="http://127.0.0.1:8765"
BACKEND_PID=""
OWNS_BACKEND=0

mkdir -p "$RUNTIME_DIR"

ready() { curl -fsS --max-time 3 "$1" >/dev/null 2>&1; }

owned_pid() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  local command_line
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command_line" in
    *"$ROOT/backend/run.py"*|*"$ROOT/backend/dist/proposal-backend"*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_owned_backend() {
  if [ "$OWNS_BACKEND" -eq 1 ] && owned_pid "$BACKEND_PID"; then
    kill -TERM "$BACKEND_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$BACKEND_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE" 2>/dev/null || true)" = "$BACKEND_PID" ]; then
    rm -f "$PID_FILE"
  fi
}

cleanup() {
  if [ "$OWNS_BACKEND" -eq 1 ]; then
    echo
    echo "正在停止本地服务…"
  fi
  stop_owned_backend
}

fail() {
  echo
  echo "启动失败：$1"
  echo "日志位置：$LOG_FILE"
  tail -n 20 "$LOG_FILE" 2>/dev/null || true
  if [ -t 0 ] && [ "${PROPOSAL_EXIT_AFTER_HEALTH:-0}" != "1" ]; then
    read -r -p "按回车键关闭窗口…" _
  fi
  exit 1
}

trap cleanup EXIT
trap 'exit 130' INT TERM

clear 2>/dev/null || true
echo "══════════════════════════════════════"
echo "  3GPP 提案分析工具 0.2.2"
echo "══════════════════════════════════════"
echo
cd "$ROOT" || fail "无法进入工具目录"

USE_SOURCE=0
if [ -x "$SOURCE_PYTHON" ] && [ -f "$SOURCE_BACKEND" ]; then
  USE_SOURCE=1
fi

if [ "$USE_SOURCE" -eq 1 ]; then
  NEED_BUILD=0
  [ -f "$WEB_DIST/index.html" ] || NEED_BUILD=1
  if [ "$NEED_BUILD" -eq 0 ] && find "$ROOT/app" "$ROOT/public" "$ROOT/package.json" "$ROOT/package-lock.json" "$ROOT/vite.renderer.config.ts" -type f -newer "$WEB_DIST/index.html" -print -quit 2>/dev/null | grep -q .; then
    NEED_BUILD=1
  fi
  if [ "$NEED_BUILD" -eq 1 ]; then
    command -v npm >/dev/null 2>&1 || fail "网页资源需要更新，但没有找到 npm"
    echo "▶ 正在构建最新网页资源…"
    : >"$LOG_FILE"
    npm run build:renderer >>"$LOG_FILE" 2>&1 || fail "网页资源构建失败"
    echo "✓ 网页资源已更新"
  fi
elif [ ! -x "$PACKAGED_BACKEND" ]; then
  fail "找不到源码 Python 环境或打包后端"
fi

KNOWN_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [ -n "$KNOWN_PID" ] && ! owned_pid "$KNOWN_PID"; then
  rm -f "$PID_FILE"
  KNOWN_PID=""
fi

if ready "$URL/api/health"; then
  if [ -n "$KNOWN_PID" ]; then
    echo "✓ 本工具启动的本地服务已经运行（PID $KNOWN_PID）"
  else
    echo "✓ 端口上已有兼容的本地服务，将直接使用"
  fi
else
  if [ -n "$KNOWN_PID" ]; then
    echo "▶ 正在清理本工具上次遗留的进程…"
    BACKEND_PID="$KNOWN_PID"
    OWNS_BACKEND=1
    stop_owned_backend
    BACKEND_PID=""
    OWNS_BACKEND=0
  fi
  echo "▶ 正在启动网页与 API 服务…"
  : >"$LOG_FILE"
  if [ "$USE_SOURCE" -eq 1 ]; then
    PROPOSAL_TOOL_PORT=8765 PROPOSAL_WEB_DIST="$WEB_DIST" "$SOURCE_PYTHON" "$SOURCE_BACKEND" >"$LOG_FILE" 2>&1 &
  else
    PROPOSAL_TOOL_PORT=8765 "$PACKAGED_BACKEND" >"$LOG_FILE" 2>&1 &
  fi
  BACKEND_PID=$!
  OWNS_BACKEND=1
  echo "$BACKEND_PID" >"$PID_FILE"

  for _ in $(seq 1 120); do
    ready "$URL/api/health" && break
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      fail "服务进程意外退出；端口可能被其他程序或旧版本占用"
    fi
    sleep 0.25
  done
  ready "$URL/api/health" || fail "服务启动超时；请检查端口 8765 和日志"
  echo "✓ 本地服务已就绪"
fi

ready "$URL/api/model-options" || fail "模型接口检查失败"
ready "$URL/api/chats?state=active" || fail "会话接口检查失败"
HTML="$(curl -fsS --max-time 5 "$URL/" 2>/dev/null || true)"
case "$HTML" in
  *"<html"*|*"<!doctype html"*|*"<!DOCTYPE html"*) ;;
  *) fail "网页版资源未正确托管，请重新构建 desktop-dist" ;;
esac
echo "✓ API、会话和网页版检查通过"

if [ "${PROPOSAL_EXIT_AFTER_HEALTH:-0}" = "1" ]; then
  echo "✓ 启动冒烟测试通过"
  exit 0
fi

if [ "${PROPOSAL_NO_OPEN:-0}" != "1" ]; then
  echo "▶ 正在打开浏览器：$URL/"
  open "$URL/"
fi

echo
echo "工具正在运行。请保留此窗口；按 Control+C 可停止服务。"
echo "日志：$LOG_FILE"

if [ "$OWNS_BACKEND" -eq 1 ]; then
  wait "$BACKEND_PID"
else
  while ready "$URL/api/health"; do sleep 2; done
fi
