"""Запуск `python -m orchestrator phase-a` с опциональным таймаутом (Windows: taskkill /T)."""
from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_proc_probe import kill_process_tree_windows


def run_orchestrator_phase_a_subprocess(
    config: OrchestratorConfig,
    cmd: list[str],
    *,
    timeout_seconds: float | None,
    poll_interval_seconds: float | None = None,
    on_poll: Callable[[int, float], None] | None = None,
) -> dict[str, Any]:
    """
    stdout/stderr наследуются от вызывающего процесса (интерактивные логи phase-a).
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(config.root_dir.resolve()),
        stdin=subprocess.DEVNULL,
    )
    pid = int(proc.pid)
    start = time.time()
    interval = float(poll_interval_seconds or 2.0)
    if interval <= 0:
        interval = 2.0
    try:
        timeout = float(timeout_seconds) if timeout_seconds is not None and float(timeout_seconds) > 0 else None
        while True:
            rc_now = proc.poll()
            elapsed = float(time.time() - start)
            if on_poll is not None:
                try:
                    on_poll(pid, elapsed)
                except Exception:
                    pass
            if rc_now is not None:
                return {"returncode": int(rc_now), "outcome": "completed", "pid": pid}
            if timeout is not None and elapsed >= timeout:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
            sleep_for = interval
            if timeout is not None:
                sleep_for = min(sleep_for, max(0.05, timeout - elapsed))
            time.sleep(max(0.05, sleep_for))
    except subprocess.TimeoutExpired:
        ok_kill, kill_msg = kill_process_tree_windows(pid, force=True)
        try:
            proc.wait(timeout=60)
        except Exception:
            pass
        return {
            "returncode": -9,
            "outcome": "timeout",
            "pid": pid,
            "kill_ok": ok_kill,
            "kill_detail": kill_msg,
        }
