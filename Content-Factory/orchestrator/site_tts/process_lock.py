from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator

_LOCK_NAME = "site_tts_execute.lock"
_STALE_SEC = 6 * 3600
_ACQUIRE_RETRIES = 80
_ACQUIRE_SLEEP = 0.05


def _windows_module_path() -> str | None:
    """Реальный путь образа текущего процесса (иногда отличается от sys.executable из-за shim)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf))
        return buf.value if n else None
    except Exception:
        return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            k.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _try_remove_stale(path: Path) -> bool:
    """Return True if lock file was removed (dead PID or too old)."""
    try:
        st = path.stat()
    except OSError:
        return False
    if time.time() - st.st_mtime > _STALE_SEC:
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            return False
        return True
    data = _read_lock(path)
    if not data:
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            return False
        return True
    pid = int(data.get("pid", 0) or 0)
    if not _pid_exists(pid):
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            return False
        return True
    return False


@contextlib.contextmanager
def site_tts_execute_lock(service_dir: Path) -> Iterator[None]:
    """
    Single-writer lock for site-tts --execute (cross-process).
    Uses a JSON file under service_dir; stale locks are cleared if PID is gone or file is old.
    """
    service_dir.mkdir(parents=True, exist_ok=True)
    path = service_dir / _LOCK_NAME
    payload = {
        "pid": os.getpid(),
        "executable": sys.executable,
        "windows_module_path": _windows_module_path(),
        "argv": sys.argv,
        "started": time.time(),
    }
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

    for attempt in range(_ACQUIRE_RETRIES):
        if path.exists():
            if not _try_remove_stale(path):
                data = _read_lock(path)
                pid = int((data or {}).get("pid", 0) or 0)
                exe = (data or {}).get("executable", "?")
                raise RuntimeError(
                    "site-tts уже выполняется с --execute (другой процесс держит lock). "
                    f"PID={pid}, interpreter={exe}. "
                    f"Дождитесь завершения или удалите вручную: {path}"
                )
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
            break
        except FileExistsError:
            time.sleep(_ACQUIRE_SLEEP)
    else:
        raise RuntimeError(
            f"Не удалось занять lock site-tts за {_ACQUIRE_RETRIES * _ACQUIRE_SLEEP:.0f}s: {path}"
        )

    try:
        yield
    finally:
        try:
            data = _read_lock(path)
            if data and int(data.get("pid", 0) or 0) == os.getpid():
                path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
