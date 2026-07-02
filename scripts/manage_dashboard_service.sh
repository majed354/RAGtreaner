#!/bin/zsh
set -eu

PROJECT_ROOT="/Users/majd/Desktop/codex/شات الاستشارات"
PYTHON="/Users/majd/Desktop/codex/.venv/bin/python"
DAEMON="$PROJECT_ROOT/scripts/run_dashboard_daemon.py"
PID_FILE="/tmp/codex-saudi-legal-rag-dashboard-supervisor.pid"
LOG_FILE="/tmp/codex-saudi-legal-rag-dashboard-supervisor.log"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1
}

start_service() {
  if is_running; then
    echo "Dashboard supervisor is already running: $(cat "$PID_FILE")"
    return
  fi
  "$PYTHON" "$DAEMON"
  sleep 2
  if is_running; then
    echo "Dashboard supervisor started: $(cat "$PID_FILE")"
  else
    echo "Dashboard supervisor failed to start. Check $LOG_FILE" >&2
    exit 1
  fi
}

case "${1:-status}" in
  start)
    start_service
    ;;
  restart)
    if is_running; then
      kill "$(cat "$PID_FILE")"
      sleep 3
    fi
    start_service
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PID_FILE")"
      echo "Dashboard supervisor stopped."
    else
      echo "Dashboard supervisor is not running."
    fi
    ;;
  status)
    if is_running; then
      echo "Dashboard supervisor is running: $(cat "$PID_FILE")"
    else
      echo "Dashboard supervisor is not running."
      exit 1
    fi
    ;;
  logs)
    tail -n 160 "$LOG_FILE"
    ;;
  *)
    echo "Usage: $0 {start|restart|stop|status|logs}" >&2
    exit 2
    ;;
esac
