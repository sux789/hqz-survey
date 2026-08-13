#!/usr/bin/env bash
# ─────────────────────────────────────────────
# survey 本地独立测试启动/停止脚本
# 同时挂载 admin(/admin/) + user(/survey/)
# 不依赖 forest-data 认证，自动以管理员身份登录
# ─────────────────────────────────────────────
# 用法:
#   ./start.sh              # 启动（默认端口 8090）
#   ./start.sh 8080         # 指定端口启动
#   ./start.sh stop         # 停止所有 survey 服务
#   ./start.sh status       # 查看运行状态
#   SURVEY_LOCAL_USER=张三 ./start.sh  # 自定义用户名
# ─────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"

ARG=${1:-start}

# ── stop ──
if [ "$ARG" = "stop" ]; then
  PIDS=$(pgrep -f "survey.launcher" 2>/dev/null || true)
  if [ -z "$PIDS" ]; then
    echo "没有运行中的 survey 服务"
    exit 0
  fi
  echo "停止 survey 服务: $PIDS"
  echo "$PIDS" | xargs kill 2>/dev/null || true
  sleep 1
  REMAIN=$(pgrep -f "survey.launcher" 2>/dev/null || true)
  if [ -n "$REMAIN" ]; then
    echo "强制停止: $REMAIN"
    echo "$REMAIN" | xargs kill -9 2>/dev/null || true
  fi
  echo "✓ 已停止所有 survey 服务"
  exit 0
fi

# ── status ──
if [ "$ARG" = "status" ]; then
  PIDS=$(pgrep -fl "survey.launcher" 2>/dev/null || true)
  if [ -z "$PIDS" ]; then
    echo "survey 服务未运行"
  else
    echo "运行中的 survey 服务:"
    echo "$PIDS"
  fi
  exit 0
fi

# ── start ──
if [[ "$ARG" =~ ^[0-9]+$ ]]; then
  PORT=$ARG
else
  PORT=8090
fi

export SURVEY_LOCAL_DEV=1
export SURVEY_LOCAL_USER=${SURVEY_LOCAL_USER:-本地测试员}
export FLASK_ENV=development

echo "════════════════════════════════════════════"
echo "  林业野外调查系统 — 本地测试模式"
echo "════════════════════════════════════════════"
echo "  User 端 (调查/打卡):  http://127.0.0.1:$PORT/survey/"
echo "  Admin 端 (GDB/管理):  http://127.0.0.1:$PORT/admin/"
echo "  用户:      $SURVEY_LOCAL_USER (管理员)"
echo "  认证:      已禁用（本地开发模式）"
echo "════════════════════════════════════════════"
echo ""

lsof -ti :$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true

exec python3 -m survey.launcher "$PORT"
