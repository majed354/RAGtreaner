#!/usr/bin/env python3
"""Keep the local dashboard service alive without requiring system privileges."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = PROJECT_ROOT / "scripts" / "run_dashboard_service.sh"
LOCK_PATH = Path("/tmp/codex-saudi-legal-rag-dashboard-supervisor.lock")
PID_PATH = Path("/tmp/codex-saudi-legal-rag-dashboard-supervisor.pid")
RESTART_DELAY_SECONDS = 10

stopping = False
child: subprocess.Popen[str] | None = None


def stop_supervisor(_signum: int, _frame: object) -> None:
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.terminate()


def main() -> int:
    global child
    lock_handle = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Dashboard supervisor is already running.", flush=True)
        return 0

    PID_PATH.write_text(f"{os.getpid()}\n", encoding="utf-8")
    signal.signal(signal.SIGTERM, stop_supervisor)
    signal.signal(signal.SIGINT, stop_supervisor)
    print(f"Dashboard supervisor started: pid={os.getpid()}", flush=True)

    try:
        while not stopping:
            child = subprocess.Popen(
                [str(SERVICE_SCRIPT)],
                cwd=PROJECT_ROOT,
                text=True,
            )
            return_code = child.wait()
            child = None
            if stopping:
                break
            print(
                f"Dashboard service exited with code {return_code}; "
                f"restarting in {RESTART_DELAY_SECONDS}s.",
                flush=True,
            )
            time.sleep(RESTART_DELAY_SECONDS)
    finally:
        try:
            PID_PATH.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
