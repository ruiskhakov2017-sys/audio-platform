"""
Обнаружение/остановка связанных с запуском процессов Python (Windows).

Не трогает бизнес-логику Gemini; только оркестрация безопасности smoke/full-site-cycle.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def kill_process_tree_windows(pid: int, *, force: bool = True) -> tuple[bool, str]:
    if sys.platform != "win32" or pid <= 0:
        return False, "kill_process_tree_windows: only Windows / positive pid"
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=False)
        ok = r.returncode == 0
        msg = (r.stdout or "") + (r.stderr or "")
        return ok, msg.strip() or f"taskkill exit={r.returncode}"
    except Exception as ex:
        return False, str(ex)


def list_python_processes_windows(*, timeout_sec: float = 45.0) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" "
        "| Select-Object ProcessId,CommandLine "
        "| ConvertTo-Json -Depth 3 -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except Exception:
        return []
    raw = (r.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = [x for x in data if isinstance(x, dict)]
    else:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            pid = int(row.get("ProcessId", 0))
        except (TypeError, ValueError):
            pid = 0
        cl = str(row.get("CommandLine", "") or "")
        out.append({"pid": pid, "command_line": cl})
    return out


def find_phase_a_conflicts(*, story_run_id: str, exclude_pid: int | None = None) -> list[dict[str, Any]]:
    """Другой orchestrator phase-a с тем же --story-id (конфликт)."""
    rid = (story_run_id or "").strip()
    if not rid:
        return []
    hits: list[dict[str, Any]] = []
    for row in list_python_processes_windows():
        pid = int(row.get("pid") or 0)
        if exclude_pid is not None and pid == exclude_pid:
            continue
        cl = str(row.get("command_line") or "")
        if "phase-a" not in cl and "phase_a" not in cl:
            continue
        if "orchestrator" not in cl:
            continue
        m = re.search(r"--story-id(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)", cl)
        sid = ""
        if m:
            sid = m.group(1).strip().strip("\"'")
        if sid == rid:
            hits.append(row)
    return hits


def count_gemini_auto_processes(*, exclude_pid: int | None = None) -> int:
    n = 0
    for row in list_python_processes_windows():
        if exclude_pid is not None and int(row.get("pid") or 0) == exclude_pid:
            continue
        cl = str(row.get("command_line") or "")
        if "gemini_auto.py" in cl:
            n += 1
    return n


def count_legacy_hint_processes(*, story_run_id: str, exclude_pid: int | None = None) -> int:
    """phase-a с тем же story-id или любой gemini_auto (индикатор нагрузки)."""
    n = len(find_phase_a_conflicts(story_run_id=story_run_id, exclude_pid=exclude_pid))
    n += count_gemini_auto_processes(exclude_pid=exclude_pid)
    return n
