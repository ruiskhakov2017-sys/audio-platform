from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


def log_site_tts_bootstrap(cfg: OrchestratorConfig, *, execute: bool) -> None:
    """
    Одна строка JSON в service_dir/logs/site_tts_bootstrap.log при каждом входе в site-tts CLI
    (до импорта torch/kokoro). Нужен для отладки двойного процесса и lock.
    """
    log_dir = cfg.service_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "site_tts_bootstrap.log"
    lock_path = cfg.service_dir / "site_tts_execute.lock"
    lock_payload: dict[str, Any] | None = None
    if lock_path.is_file():
        try:
            lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lock_payload = {"_read_error": True}

    try:
        start_method = multiprocessing.get_start_method(allow_none=True)
    except ValueError:
        start_method = None

    parent_mp = None
    try:
        p = multiprocessing.parent_process()
        parent_mp = None if p is None else {"name": p.name, "pid": getattr(p, "pid", None)}
    except Exception:
        parent_mp = "unavailable"

    env_path = os.environ.get("PATH", "")
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "execute": execute,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "sys_executable": sys.executable,
        "argv": sys.argv,
        "mp_start_method": start_method,
        "mp_current_process_name": multiprocessing.current_process().name,
        "mp_parent_process": parent_mp,
        "path_head": env_path[:400],
        "lock_path": str(lock_path),
        "lock_payload": lock_payload,
    }
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(
        f"[site-tts bootstrap] pid={record['pid']} ppid={record['ppid']} "
        f"mp_name={record['mp_current_process_name']} exe={record['sys_executable']}",
        flush=True,
    )
