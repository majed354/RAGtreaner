#!/bin/zsh
set -eu

PROJECT_ROOT="/Users/majd/Desktop/codex/شات الاستشارات"
PYTHON="/Users/majd/Desktop/codex/.venv/bin/python"

cd "$PROJECT_ROOT"
export PYTHONUNBUFFERED=1

exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
