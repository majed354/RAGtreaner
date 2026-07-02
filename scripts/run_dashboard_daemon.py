#!/usr/bin/env python3
"""Detach the dashboard supervisor from the launching terminal session."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/Users/majd/Desktop/codex/.venv/bin/python")
SUPERVISOR = PROJECT_ROOT / "scripts" / "run_dashboard_supervisor.py"
LOG_PATH = Path("/tmp/codex-saudi-legal-rag-dashboard-supervisor.log")


def main() -> int:
    first_child = os.fork()
    if first_child:
        return 0

    os.setsid()
    second_child = os.fork()
    if second_child:
        os._exit(0)

    os.chdir(PROJECT_ROOT)
    log_fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    null_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.execv(str(PYTHON), [str(PYTHON), str(SUPERVISOR)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
