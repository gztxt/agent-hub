#!/bin/bash
# Agent Hub 控制脚本
set -euo pipefail

APP_DIR="$HOME/agent-hub"
PID_FILE="$APP_DIR/data/agent-hub.pid"
LOG_FILE="$APP_DIR/data/logs/agent-hub.log"

case "${1:-status}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Agent Hub 已在运行（PID $(cat "$PID_FILE")）"
      exit 0
    fi
    cd "$APP_DIR"
    # 清除可能的 PORT 环境变量，强制使用 3102
    env -u PORT PORT=3102 nohup ~/agent-hub/venv/bin/python3 -m uvicorn src.main:app --host 0.0.0.0 --port 3102 >"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Agent Hub 启动中... PID=$!"
    for i in $(seq 1 10); do
      curl -sS http://127.0.0.1:3102/health >/dev/null 2>&1 && echo "✅ 健康检查通过" && exit 0
      sleep 1
    done
    echo "⚠️  健康检查未通过，请查看日志: $LOG_FILE"
    tail -20 "$LOG_FILE"
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null && rm -f "$PID_FILE"
      echo "Agent Hub 已停止"
    else
      echo "Agent Hub 未运行"
    fi
    ;;
  restart)
    $0 stop; sleep 2; $0 start
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "✅ 运行中（PID $(cat "$PID_FILE")）"
      curl -sS http://127.0.0.1:3102/health | python3 -m json.tool 2>/dev/null
    else
      echo "❌ 未运行"
    fi
    ;;
  log)
    tail -f "$LOG_FILE"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|log}"
    exit 1
    ;;
esac
