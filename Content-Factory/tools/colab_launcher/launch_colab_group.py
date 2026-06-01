from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "youtube_video_colab_workers.yaml"
DEFAULT_STORY_ID = "Becoming A Slut Wife Alma"
DEFAULT_STORY_SLUG = "Becoming_A_Slut_Wife_Alma"
DEFAULT_DRIVE_ROOT = Path(r"G:\Мой диск\ContentFactory_YouTube")
NEW_COLAB_URL = "https://colab.research.google.com/#create=true"
REPORT_NAME = "colab_launcher_report.json"
AUDIT_REPORT_PATH = PROJECT_ROOT / "output" / "youtube" / "_diagnostics" / "colab_launch_audit_report.json"


@dataclass
class WorkerConfig:
    group: str
    browser: str
    email: str
    profile_strategy: str
    launch_mode: str
    use_user_data_dir: bool
    profile_dir: str
    notebook_path: str
    notebook_url: str
    require_t4: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_email_name(email: str) -> str:
    return email.replace("@", "_").replace(".", "_")


def unquote(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def dependency_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "python_executable": os.sys.executable,
        "pyautogui_installed": False,
        "pyperclip_installed": False,
        "pillow_installed": False,
        "pygetwindow_installed": False,
        "playwright_installed": False,
        "errors": {},
    }
    try:
        import pyautogui  # type: ignore  # noqa: F401

        status["pyautogui_installed"] = True
    except Exception as exc:
        status["errors"]["pyautogui"] = repr(exc)
    try:
        import pyperclip  # type: ignore  # noqa: F401

        status["pyperclip_installed"] = True
    except Exception as exc:
        status["errors"]["pyperclip"] = repr(exc)
    try:
        from PIL import Image  # type: ignore  # noqa: F401

        status["pillow_installed"] = True
    except Exception as exc:
        status["errors"]["pillow"] = repr(exc)
    try:
        import pygetwindow  # type: ignore  # noqa: F401

        status["pygetwindow_installed"] = True
    except Exception as exc:
        status["errors"]["pygetwindow"] = repr(exc)
    try:
        import playwright  # type: ignore  # noqa: F401

        status["playwright_installed"] = True
    except Exception as exc:
        status["errors"]["playwright"] = repr(exc)
    status["ok_for_injection"] = bool(status["pyautogui_installed"] and status["pyperclip_installed"])
    status["ok_for_operator"] = bool(status["ok_for_injection"] and status["pillow_installed"] and status["pygetwindow_installed"])
    status["ok_for_cdp_operator"] = bool(status["pyperclip_installed"] and status["playwright_installed"])
    status["ok_for_debug_screenshots"] = bool(status["ok_for_injection"] and status["pillow_installed"])
    return status


def print_dependency_status(status: dict[str, Any]) -> None:
    print(f"python_executable={status.get('python_executable')}")
    print(f"pyautogui_installed={status.get('pyautogui_installed')}")
    print(f"pyperclip_installed={status.get('pyperclip_installed')}")
    print(f"pillow_installed={status.get('pillow_installed')}")
    print(f"pygetwindow_installed={status.get('pygetwindow_installed')}")
    print(f"playwright_installed={status.get('playwright_installed')}")
    print(f"ok_for_injection={status.get('ok_for_injection')}")
    print(f"ok_for_operator={status.get('ok_for_operator')}")
    print(f"ok_for_cdp_operator={status.get('ok_for_cdp_operator')}")
    print(f"ok_for_debug_screenshots={status.get('ok_for_debug_screenshots')}")
    if status.get("errors"):
        print(f"errors={json.dumps(status.get('errors'), ensure_ascii=True)}")


def read_yaml_like(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_expected_yaml(text)
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return data


def parse_expected_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    group_name = ""
    current_worker: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            group_name = stripped[:-1]
            result[group_name] = {"workers": []}
            current_worker = None
            continue
        if not group_name:
            continue
        if stripped.startswith("- email:"):
            current_worker = {"email": unquote(stripped.split(":", 1)[1].strip())}
            result[group_name]["workers"].append(current_worker)
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = unquote(value.strip())
        if current_worker is not None and key in {"email", "browser", "profile_strategy", "launch_mode", "use_user_data_dir", "profile_dir", "notebook_path", "notebook_url", "require_t4"}:
            current_worker[key] = value
            continue
        if current_worker is None and key in {"browser", "profile_strategy", "launch_mode", "use_user_data_dir"}:
            result[group_name][key] = value
    return result


def load_workers(path: Path, group: str, browser_override: str = "", mode_override: str = "") -> list[WorkerConfig]:
    raw = read_yaml_like(path)
    groups = ["yandex", "chrome"] if group == "all" else [group]
    workers: list[WorkerConfig] = []
    for group_name in groups:
        group_data = raw.get(group_name)
        if not isinstance(group_data, dict):
            raise ValueError(f"group not found in config: {group_name}")
        group_browser = str(group_data.get("browser") or group_name).strip().lower()
        group_profile_strategy = str(group_data.get("profile_strategy") or "browser_default_tabs").strip()
        group_launch_mode = str(group_data.get("launch_mode") or "new_colab_inject_tabs").strip()
        group_use_user_data_dir = parse_bool(group_data.get("use_user_data_dir", False))
        for item in group_data.get("workers") or []:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip()
            if not email:
                continue
            browser = (browser_override or str(item.get("browser") or group_browser)).strip().lower()
            if browser not in {"chrome", "yandex"}:
                raise ValueError(f"unsupported browser for {email}: {browser!r}")
            launch_mode = (mode_override or str(item.get("launch_mode") or group_launch_mode)).strip()
            if launch_mode == "browser_default_tabs":
                launch_mode = "new_colab_inject_tabs"
            if launch_mode == "browser_operator":
                launch_mode = "browser_operator"
            if launch_mode == "existing_profiles_sequential_operator":
                launch_mode = "existing_profiles_sequential_operator"
            if launch_mode == "existing_profiles_cdp_operator":
                launch_mode = "existing_profiles_cdp_operator"
            if launch_mode == "prepared_notebook_url":
                launch_mode = "prepared_notebook_url"
            profile_strategy = str(item.get("profile_strategy") or group_profile_strategy).strip()
            use_user_data_dir = parse_bool(item.get("use_user_data_dir", group_use_user_data_dir))
            if profile_strategy == "existing_logged_in_profiles" or launch_mode in {"existing_profiles_sequential_operator", "existing_profiles_cdp_operator", "prepared_notebook_url"}:
                use_user_data_dir = True
            workers.append(
                WorkerConfig(
                    group=group_name,
                    browser=browser,
                    email=email,
                    profile_strategy=profile_strategy,
                    launch_mode=launch_mode,
                    use_user_data_dir=use_user_data_dir,
                    profile_dir=str(item.get("profile_dir") or "").strip(),
                    notebook_path=str(item.get("notebook_path") or "").strip(),
                    notebook_url=str(item.get("notebook_url") or "").strip(),
                    require_t4=parse_bool(item.get("require_t4", False)),
                )
            )
    return workers


def story_slug_from_id(story_id: str) -> str:
    cleaned = []
    for char in str(story_id or "").strip():
        cleaned.append(char if char.isalnum() or char in "._-" else "_")
    return "_".join("".join(cleaned).split("_")).strip("_") or DEFAULT_STORY_SLUG


def read_prepared_notebook_urls_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return [{str(k): str(v or "") for k, v in row.items()} for row in csv.DictReader(f)]
    except OSError:
        return []


def read_render_config_workers(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        raw = read_yaml_like(path)
        workers = raw.get("workers") if isinstance(raw, dict) else []
        if isinstance(workers, list):
            return [str(item).strip() for item in workers if str(item).strip()]
    except Exception:
        pass
    workers: list[str] = []
    in_workers = False
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not raw_line.startswith(" ") and stripped.endswith(":"):
                in_workers = stripped == "workers:"
                continue
            if in_workers and stripped.startswith("- "):
                workers.append(unquote(stripped[2:].strip()))
                continue
            if in_workers and not raw_line.startswith(" "):
                break
    except OSError:
        return []
    return [worker for worker in workers if worker]


def build_colab_launch_audit(*, config_path: Path, story_id: str) -> dict[str, Any]:
    all_workers = load_workers(config_path, "all")
    render_config_path = PROJECT_ROOT / "configs" / "youtube_video_render.yaml"
    render_workers = read_render_config_workers(render_config_path)
    launch_worker_emails = sorted({worker.email for worker in all_workers})
    render_worker_emails = sorted(set(render_workers))
    browser_exes = {browser: first_existing(browser_candidates(browser)) for browser in {"chrome", "yandex"}}
    story_slug = story_slug_from_id(story_id)
    drive_job_root = DEFAULT_DRIVE_ROOT / "video_jobs" / story_slug
    drive_scripts_dir = DEFAULT_DRIVE_ROOT / "scripts"
    prepared_csv = PROJECT_ROOT / "tools" / "colab_launcher" / "workers_prepared_notebook_urls.csv"
    csv_rows = read_prepared_notebook_urls_csv(prepared_csv)
    local_worker_script = PROJECT_ROOT / "colab" / "youtube_video_worker_colab.py"
    local_bootstrap_script = PROJECT_ROOT / "colab" / "youtube_video_bootstrap_colab.py"
    workers = []
    for worker in all_workers:
        local_notebook = PROJECT_ROOT / worker.notebook_path if worker.notebook_path else Path("")
        profile_dir = Path(worker.profile_dir) if worker.profile_dir else Path("")
        workers.append(
            {
                "email": worker.email,
                "group": worker.group,
                "browser": worker.browser,
                "profile_strategy": worker.profile_strategy,
                "launch_mode": worker.launch_mode,
                "profile_dir": worker.profile_dir,
                "profile_dir_exists": bool(worker.profile_dir and profile_dir.is_dir()),
                "notebook_path": worker.notebook_path,
                "notebook_exists": bool(worker.notebook_path and local_notebook.is_file()),
                "notebook_url": worker.notebook_url,
                "require_t4": worker.require_t4,
                "open_command": (
                    f'python tools/colab_launcher/launch_colab_group.py --config "{config_path}" '
                    f'--group {worker.group} --email "{worker.email}" --mode prepared-notebook-url'
                ),
            }
        )
    report = {
        "ok": True,
        "status": "audit",
        "config_path": str(config_path.resolve()),
        "config_exists": config_path.is_file(),
        "workers_total": len(all_workers),
        "workers_by_group": {
            "yandex": sum(1 for worker in all_workers if worker.group == "yandex"),
            "chrome": sum(1 for worker in all_workers if worker.group == "chrome"),
        },
        "launch_modes": sorted({worker.launch_mode for worker in all_workers}),
        "profile_strategies": sorted({worker.profile_strategy for worker in all_workers}),
        "browser_executables": {key: str(value) if value else "" for key, value in browser_exes.items()},
        "prepared_notebook_urls_csv": str(prepared_csv),
        "prepared_notebook_urls_csv_exists": prepared_csv.is_file(),
        "prepared_notebook_urls_csv_rows": len(csv_rows),
        "queue_render_config_path": str(render_config_path),
        "queue_render_config_exists": render_config_path.is_file(),
        "queue_render_workers_count": len(render_worker_emails),
        "queue_render_workers": render_worker_emails,
        "workers_missing_from_queue_render_config": sorted(set(launch_worker_emails) - set(render_worker_emails)),
        "queue_workers_missing_from_launcher_config": sorted(set(render_worker_emails) - set(launch_worker_emails)),
        "local_worker_script": str(local_worker_script),
        "local_worker_script_exists": local_worker_script.is_file(),
        "local_bootstrap_script": str(local_bootstrap_script),
        "local_bootstrap_script_exists": local_bootstrap_script.is_file(),
        "drive_root": str(DEFAULT_DRIVE_ROOT),
        "drive_root_exists": DEFAULT_DRIVE_ROOT.is_dir(),
        "drive_scripts_dir": str(drive_scripts_dir),
        "drive_worker_script": str(drive_scripts_dir / "youtube_video_worker_colab.py"),
        "drive_worker_script_exists": (drive_scripts_dir / "youtube_video_worker_colab.py").is_file(),
        "drive_bootstrap_script": str(drive_scripts_dir / "youtube_video_bootstrap_colab.py"),
        "drive_bootstrap_script_exists": (drive_scripts_dir / "youtube_video_bootstrap_colab.py").is_file(),
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(drive_job_root),
        "drive_job_exists": drive_job_root.is_dir(),
        "expected_job_ready_marker": str(drive_job_root / "VIDEO_JOB_READY.json"),
        "job_ready_marker_exists": (drive_job_root / "VIDEO_JOB_READY.json").is_file(),
        "expected_queue_root": str(drive_job_root / "queue"),
        "expected_assigned_queue": str(drive_job_root / "queue" / "assigned" / "<worker_email>" / "pending"),
        "expected_status_dir": str(drive_job_root / "status"),
        "expected_worker_status_glob": str(drive_job_root / "status" / "workers" / "*.json"),
        "expected_legacy_status_glob": str(drive_job_root / "status" / "COLAB_WORKER_STATUS_*.json"),
        "expected_reports_dir": str(drive_job_root / "reports"),
        "operator_workflow": [
            "Open prepared notebook URL in the existing logged-in profile.",
            "If auto-run is unreliable, click Runtime -> Run all or run the first bootstrap cell manually.",
            "The prepared notebook runs scripts/youtube_video_bootstrap_colab.py from ContentFactory_YouTube/scripts.",
            "Status is checked with: python -m orchestrator youtube video queue-status --story-id \"<story>\".",
        ],
        "single_worker_smoke_command": (
            f'python tools/colab_launcher/launch_colab_group.py --config "{config_path}" '
            "--group yandex --limit 1 --mode prepared-notebook-url --wait-after-open-seconds 0 --wait-for-run-start-seconds 0"
        ),
        "single_worker_smoke_autorun_command": (
            f'python tools/colab_launcher/launch_colab_group.py --config "{config_path}" '
            "--group yandex --limit 1 --mode prepared-notebook-url --auto-run --wait-after-open-seconds 30 --wait-for-run-start-seconds 60"
        ),
        "group_worker_command_yandex": (
            f'python tools/colab_launcher/launch_colab_group.py --config "{config_path}" '
            "--group yandex --mode prepared-notebook-url --auto-run --sequential"
        ),
        "group_worker_command_chrome": (
            f'python tools/colab_launcher/launch_colab_group.py --config "{config_path}" '
            "--group chrome --mode prepared-notebook-url --auto-run --sequential"
        ),
        "status_command": f'python -m orchestrator youtube video queue-status --story-id "{story_id}" --quick',
        "full_status_command": f'python -m orchestrator youtube video queue-status --story-id "{story_id}"',
        "workers": workers,
        "warnings": [
            "prepared_notebook_url is the stable launch mode; avoid old code injection modes for production.",
            "Auto-run through browser UI is best-effort. Manual Run all is the reliable fallback.",
            "No production run is started by audit.",
        ],
        "written_at": utc_now(),
        "report_path": str(AUDIT_REPORT_PATH),
    }
    AUDIT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def browser_candidates(browser: str) -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = [Path(os.environ.get("PROGRAMFILES", "")), Path(os.environ.get("PROGRAMFILES(X86)", ""))]
    if browser == "chrome":
        return [
            local / "Google" / "Chrome" / "Application" / "chrome.exe",
            *(root / "Google" / "Chrome" / "Application" / "chrome.exe" for root in program_files if str(root)),
        ]
    if browser == "yandex":
        return [
            local / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
            *(root / "Yandex" / "YandexBrowser" / "Application" / "browser.exe" for root in program_files if str(root)),
        ]
    return []


def build_worker_cell(email: str, require_t4: bool = False) -> str:
    require_t4_value = "1" if require_t4 else "0"
    return f'''# === ContentFactory YouTube VIDEO Worker ===
# Worker: {email}

from google.colab import drive
drive.mount("/content/drive")

!apt-get update -qq
!apt-get install -y -qq ffmpeg

import os
import subprocess
from pathlib import Path

WORKER_EMAIL = "{email}"

ROOT = Path("/content/drive/MyDrive/ContentFactory_YouTube")
BOOTSTRAP_PATH = ROOT / "scripts" / "youtube_video_bootstrap_colab.py"
SCRIPT_PATH = ROOT / "scripts" / "youtube_video_worker_colab.py"

print("WORKER_EMAIL:", WORKER_EMAIL)
print("ROOT exists:", ROOT.exists(), ROOT)
print("BOOTSTRAP exists:", BOOTSTRAP_PATH.exists(), BOOTSTRAP_PATH)
print("SCRIPT exists:", SCRIPT_PATH.exists(), SCRIPT_PATH)

if not ROOT.exists():
    raise RuntimeError(
        "ContentFactory_YouTube не найден. "
        "Проверь, что для этого Google-аккаунта создан shortcut в My Drive."
    )

if not BOOTSTRAP_PATH.exists():
    raise RuntimeError(
        "youtube_video_bootstrap_colab.py не найден. "
        "Сначала запусти setup-colab-workers на Windows."
    )

if not SCRIPT_PATH.exists():
    raise RuntimeError(
        "youtube_video_worker_colab.py не найден. "
        "Сначала запусти setup-colab-workers на Windows."
    )

os.environ["CONTENT_FACTORY_WORKER_EMAIL"] = WORKER_EMAIL
os.environ["CONTENT_FACTORY_YOUTUBE_ROOT"] = str(ROOT)
os.environ["CONTENT_FACTORY_VIDEO_QUEUE_MODE"] = "1"
os.environ["CONTENT_FACTORY_MAX_JOBS_PER_RUN"] = "0"
os.environ["CONTENT_FACTORY_POLL_SECONDS"] = "10"
os.environ["CONTENT_FACTORY_IDLE_TIMEOUT_MIN"] = "15"
os.environ["CONTENT_FACTORY_IDLE_EXIT_SECONDS"] = "900"
os.environ["CONTENT_FACTORY_SELF_RECLAIM_STALE_MINUTES"] = "10"
os.environ["CONTENT_FACTORY_SELF_RECLAIM_MAX_ATTEMPTS"] = "3"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["CONTENT_FACTORY_REQUIRE_T4"] = "{require_t4_value}"

gpu = subprocess.run(
    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
    capture_output=True,
    text=True,
)
gpu_name = gpu.stdout.strip().splitlines()[0].strip() if gpu.returncode == 0 and gpu.stdout.strip() else ""
print("GPU:", gpu_name or "not available")
if not gpu_name:
    message = "GPU not available. In Colab use Runtime -> Change runtime type -> GPU."
    if os.environ.get("CONTENT_FACTORY_REQUIRE_T4") == "1":
        raise RuntimeError(message)
    print("[WARN]", message)
elif "T4" not in gpu_name.upper():
    message = f"GPU is not T4: {{gpu_name}}. Continuing because CONTENT_FACTORY_REQUIRE_T4=0."
    if os.environ.get("CONTENT_FACTORY_REQUIRE_T4") == "1":
        raise RuntimeError(message)
    print("[WARN]", message)

%run "/content/drive/MyDrive/ContentFactory_YouTube/scripts/youtube_video_bootstrap_colab.py" --story-slug "{DEFAULT_STORY_SLUG}" --worker-email "{email}" --max-jobs-per-run "0" --idle-timeout-min "15" --poll-seconds "10"
'''


def set_clipboard_text(text: str) -> dict[str, Any]:
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return {"ok": True, "method": "pyperclip"}
    except Exception as exc:
        pyperclip_error = repr(exc)
    temp_path = Path(tempfile.gettempdir()) / f"content_factory_colab_cell_{int(time.time() * 1000)}.txt"
    try:
        temp_path.write_text(text, encoding="utf-8")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", f"Get-Content -Raw -LiteralPath '{temp_path}' | Set-Clipboard"],
            check=True,
            capture_output=True,
            text=True,
        )
        return {"ok": True, "method": "powershell_set_clipboard", "temp_path": str(temp_path), "pyperclip_error": pyperclip_error}
    except Exception as exc:
        return {"ok": False, "reason": "set_clipboard_failed", "error": repr(exc), "pyperclip_error": pyperclip_error}


def get_clipboard_text() -> str:
    try:
        import pyperclip  # type: ignore

        return pyperclip.paste() or ""
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Get-Clipboard -Raw"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout or ""
    except Exception:
        return ""


def configure_pyautogui_for_launcher(pyautogui: Any) -> None:
    # Production Colab automation may run while the cursor is parked in a screen corner.
    # The default failsafe aborts screenshots/hotkeys before the run sequence starts.
    try:
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.15
    except Exception:
        pass


def save_debug_screenshot(debug_dir: Path | None, filename: str) -> str:
    if debug_dir is None:
        return ""
    try:
        import pyautogui  # type: ignore
    except Exception:
        return ""
    configure_pyautogui_for_launcher(pyautogui)
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / filename
        pyautogui.screenshot(str(path))
        return str(path)
    except Exception:
        return ""


def verify_code_injected(email: str) -> dict[str, Any]:
    sentinel = f"CONTENT_FACTORY_VERIFY_SENTINEL_{int(time.time() * 1000)}"
    set_clipboard_text(sentinel)
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        return {"ok": False, "reason": "pyautogui_unavailable", "error": repr(exc)}
    configure_pyautogui_for_launcher(pyautogui)
    try:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.3)
    except Exception as exc:
        return {"ok": False, "reason": "copy_after_paste_failed", "error": repr(exc)}
    copied = get_clipboard_text()
    has_marker = "CONTENT_FACTORY_WORKER_EMAIL" in copied
    has_email = email in copied
    return {
        "ok": bool(has_marker and has_email),
        "reason": "verified" if has_marker and has_email else "code_not_injected",
        "has_content_factory_worker_email": has_marker,
        "has_worker_email": has_email,
        "copied_chars": len(copied),
    }


def verify_login_or_oauth_block() -> dict[str, Any]:
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        return {"ok": False, "reason": "pyautogui_unavailable", "error": repr(exc)}
    configure_pyautogui_for_launcher(pyautogui)
    try:
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.2)
    except Exception as exc:
        return {"ok": False, "reason": "url_copy_failed", "error": repr(exc)}
    url = get_clipboard_text().strip()
    lowered = url.lower()
    blocked = any(marker in lowered for marker in ("accounts.google.com", "signin", "oauth", "consent"))
    return {
        "ok": True,
        "blocked": blocked,
        "url": url,
        "reason": "login_or_oauth_required" if blocked else "not_detected",
    }


def inject_and_run_best_effort(
    cell_code: str,
    *,
    email: str,
    auto_run: bool,
    debug_dir: Path | None = None,
    settle_seconds: int = 10,
) -> dict[str, Any]:
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        return {"attempted": True, "ok": False, "reason": "pyautogui_unavailable", "error": repr(exc)}
    screenshots: dict[str, str] = {}
    try:
        time.sleep(max(1, settle_seconds))
        screenshots["after_open_colab"] = save_debug_screenshot(debug_dir, "after_open_colab.png")
        last_clipboard: dict[str, Any] = {}
        last_verify: dict[str, Any] = {}
        for attempt in range(1, 4):
            last_clipboard = set_clipboard_text(cell_code)
            if not last_clipboard.get("ok"):
                return {
                    "attempted": True,
                    "ok": False,
                    "reason": "clipboard_unavailable",
                    "code_injected": False,
                    "auto_run_attempted": False,
                    "clipboard": last_clipboard,
                    "screenshots": screenshots,
                }
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(1)
            screenshots[f"after_paste_attempt_{attempt}"] = save_debug_screenshot(debug_dir, f"after_paste_attempt_{attempt}.png")
            last_verify = verify_code_injected(email)
            if last_verify.get("ok"):
                if auto_run:
                    pyautogui.hotkey("ctrl", "f9")
                    time.sleep(0.5)
                    screenshots["after_run_hotkey"] = save_debug_screenshot(debug_dir, "after_run_hotkey.png")
                return {
                    "attempted": True,
                    "ok": True,
                    "code_injected": True,
                    "auto_run_attempted": bool(auto_run),
                    "method": "clipboard_paste_verified_ctrl_f9" if auto_run else "clipboard_paste_verified",
                    "warning": "best_effort_only",
                    "clipboard": last_clipboard,
                    "verification": last_verify,
                    "screenshots": screenshots,
                }
        screenshots["after_run_hotkey"] = ""
        return {
            "attempted": True,
            "ok": False,
            "reason": "code_not_injected",
            "code_injected": False,
            "auto_run_attempted": False,
            "clipboard": last_clipboard,
            "verification": last_verify,
            "screenshots": screenshots,
        }
    except Exception as exc:
        return {"attempted": True, "ok": False, "reason": "pyautogui_failed", "error": repr(exc), "screenshots": screenshots}


def browser_operator_flow(
    cell_code: str,
    *,
    email: str,
    auto_run: bool,
    debug_dir: Path,
    settle_seconds: int = 12,
) -> dict[str, Any]:
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        return {"attempted": True, "ok": False, "reason": "pyautogui_unavailable", "error": repr(exc)}

    debug_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []
    screenshots: dict[str, str] = {}

    def snap(name: str) -> str:
        path = save_debug_screenshot(debug_dir, name)
        screenshots[name.rsplit(".", 1)[0]] = path
        return path

    def add_step(name: str, ok: bool, **fields: Any) -> None:
        steps.append({"step": name, "ok": ok, "at": utc_now(), **fields})

    try:
        time.sleep(max(1, settle_seconds))
        add_step("wait_colab_load", True, seconds=settle_seconds)
        snap("after_open_colab.png")

        block = verify_login_or_oauth_block()
        add_step("login_oauth_url_check", bool(block.get("ok")), **block)
        if block.get("blocked"):
            snap("manual_action_required_login_or_oauth.png")
            return {
                "attempted": True,
                "ok": False,
                "reason": block.get("reason") or "login_or_oauth_required",
                "code_injected": False,
                "auto_run_attempted": False,
                "manual_action_required": True,
                "steps": steps,
                "screenshots": screenshots,
            }

        gpu_result = {
            "attempted": True,
            "ok": False,
            "reason": "no_stable_colab_ui_selector",
            "warning": "GPU/T4 UI selection is not stable via public selectors. Worker cell runs nvidia-smi and require_t4 check.",
        }
        add_step("gpu_t4_best_effort", False, **gpu_result)

        paste_strategies = [
            {"name": "click_center_ctrl_a_paste", "click": (0.50, 0.55), "hotkeys": [("ctrl", "a"), ("ctrl", "v")]},
            {"name": "click_lower_cell_ctrl_a_paste", "click": (0.50, 0.72), "hotkeys": [("ctrl", "a"), ("ctrl", "v")]},
            {"name": "keyboard_focus_paste", "click": None, "hotkeys": [("tab",), ("tab",), ("ctrl", "a"), ("ctrl", "v")]},
        ]
        screen_w, screen_h = pyautogui.size()
        last_verify: dict[str, Any] = {}
        last_clipboard: dict[str, Any] = {}
        for index, strategy in enumerate(paste_strategies, start=1):
            last_clipboard = set_clipboard_text(cell_code)
            if not last_clipboard.get("ok"):
                add_step("set_clipboard", False, attempt=index, clipboard=last_clipboard)
                return {
                    "attempted": True,
                    "ok": False,
                    "reason": "clipboard_unavailable",
                    "code_injected": False,
                    "auto_run_attempted": False,
                    "manual_action_required": True,
                    "steps": steps,
                    "screenshots": screenshots,
                }
            click = strategy["click"]
            if click:
                pyautogui.click(int(screen_w * click[0]), int(screen_h * click[1]))
                time.sleep(0.4)
            for hotkey in strategy["hotkeys"]:
                pyautogui.hotkey(*hotkey)
                time.sleep(0.25)
            time.sleep(1)
            snap(f"after_paste_attempt_{index}.png")
            last_verify = verify_code_injected(email)
            add_step("paste_and_verify", bool(last_verify.get("ok")), attempt=index, strategy=strategy["name"], verification=last_verify)
            if last_verify.get("ok"):
                if auto_run:
                    pyautogui.hotkey("ctrl", "f9")
                    time.sleep(1)
                    snap("after_run_hotkey.png")
                    add_step("run_hotkey_ctrl_f9", True)
                else:
                    add_step("run_hotkey_ctrl_f9", False, reason="auto_run_disabled")
                return {
                    "attempted": True,
                    "ok": True,
                    "reason": "verified",
                    "code_injected": True,
                    "auto_run_attempted": bool(auto_run),
                    "manual_action_required": False,
                    "clipboard": last_clipboard,
                    "verification": last_verify,
                    "gpu_t4_attempt_result": gpu_result,
                    "steps": steps,
                    "screenshots": screenshots,
                }

        snap("after_paste_failed.png")
        return {
            "attempted": True,
            "ok": False,
            "reason": "code_not_injected",
            "code_injected": False,
            "auto_run_attempted": False,
            "manual_action_required": True,
            "clipboard": last_clipboard,
            "verification": last_verify,
            "gpu_t4_attempt_result": gpu_result,
            "steps": steps,
            "screenshots": screenshots,
        }
    except Exception as exc:
        snap("operator_exception.png")
        add_step("operator_exception", False, error=repr(exc))
        return {
            "attempted": True,
            "ok": False,
            "reason": "browser_operator_failed",
            "error": repr(exc),
            "code_injected": False,
            "auto_run_attempted": False,
            "manual_action_required": True,
            "steps": steps,
            "screenshots": screenshots,
        }


def browser_args(browser_exe: Path, worker: WorkerConfig, url: str) -> list[str]:
    args = [str(browser_exe)]
    if worker.use_user_data_dir:
        if not worker.profile_dir:
            raise ValueError(f"use_user_data_dir=true but profile_dir is empty for {worker.email}")
        args.append(f"--user-data-dir={worker.profile_dir}")
    args.extend(["--new-tab", url])
    return args


def debug_dir_for(story_id: str, email: str) -> Path:
    return PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports" / "colab_launcher_debug" / safe_email_name(email)


def launch_worker_tab(
    worker: WorkerConfig,
    browser_exe: Path | None,
    *,
    story_id: str,
    dry_run: bool,
    auto_run: bool,
    debug_screenshots: bool,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    cell_code = build_worker_cell(worker.email, require_t4=worker.require_t4)
    opened_url = worker.notebook_url or NEW_COLAB_URL
    args: list[str] = []
    if browser_exe:
        try:
            args = browser_args(browser_exe, worker, opened_url)
        except ValueError as exc:
            errors.append(str(exc))
    else:
        errors.append(f"{worker.browser}_executable_not_found")

    opened = False
    inject_result = {"attempted": False, "ok": False, "reason": "dry_run" if dry_run else "not_started"}
    if not dry_run and browser_exe and not errors:
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
            if worker.launch_mode == "browser_operator":
                inject_result = browser_operator_flow(
                    cell_code,
                    email=worker.email,
                    auto_run=auto_run,
                    debug_dir=debug_dir_for(story_id, worker.email),
                    settle_seconds=12,
                )
            else:
                inject_result = inject_and_run_best_effort(
                    cell_code,
                    email=worker.email,
                    auto_run=auto_run,
                    debug_dir=debug_dir_for(story_id, worker.email) if debug_screenshots else None,
                    settle_seconds=12,
                )
        except OSError as exc:
            errors.append(f"browser_launch_failed: {exc}")

    if worker.profile_strategy == "browser_default_tabs" and worker.use_user_data_dir:
        warnings.append("profile_strategy=browser_default_tabs but use_user_data_dir=true")
    if not worker.notebook_url:
        warnings.append("new_colab_inject_tabs; notebook_url not required")
    code_injected = bool(inject_result.get("code_injected"))
    manual_action_required = bool(errors) or not opened or not code_injected or (auto_run and not bool(inject_result.get("ok")))
    if manual_action_required and not dry_run:
        warnings.append("manual_action_required: login/2FA/Colab UI may need manual paste/Connect/Run all")

    return {
        "email": worker.email,
        "browser": worker.browser,
        "group": worker.group,
        "profile_strategy": worker.profile_strategy,
        "profile_dir": worker.profile_dir,
        "use_user_data_dir": worker.use_user_data_dir,
        "launch_mode": worker.launch_mode,
        "opened_url": opened_url,
        "opened": opened,
        "notebook_url": worker.notebook_url,
        "require_t4": worker.require_t4,
        "code_injected": code_injected,
        "auto_run_attempted": bool(inject_result.get("attempted")) and bool(auto_run),
        "auto_run_result": inject_result,
        "reason": inject_result.get("reason", ""),
        "manual_action_required": manual_action_required,
        "debug_screenshots": bool(debug_screenshots),
        "debug_dir": str(debug_dir_for(story_id, worker.email)) if debug_screenshots or worker.launch_mode == "browser_operator" else "",
        "worker_cell_preview": cell_code if dry_run else "",
        "launch_args": args,
        "warnings": warnings,
        "errors": errors,
    }


def write_report(story_id: str, report: dict[str, Any]) -> tuple[Path, Path]:
    local_path = PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports" / REPORT_NAME
    drive_path = DEFAULT_DRIVE_ROOT / "reports" / REPORT_NAME
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        drive_path.parent.mkdir(parents=True, exist_ok=True)
        drive_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        report.setdefault("warnings", []).append(f"failed_to_write_drive_report: {exc}")
    return local_path, drive_path


def write_preflight_failure_report(
    *,
    story_id: str,
    group: str,
    mode: str,
    email_filter: str,
    debug_screenshots: bool,
    status: dict[str, Any],
    reason: str,
) -> tuple[dict[str, Any], Path, Path]:
    report = {
        "ok": False,
        "status": "dependency_preflight_failed",
        "reason": reason,
        "group": group,
        "mode": mode,
        "email_filter": email_filter,
        "debug_screenshots": debug_screenshots,
        "dependency_status": status,
        "message": "Colab launcher dependency preflight failed. Run INSTALL_COLAB_LAUNCHER_DEPS.bat",
        "written_at": utc_now(),
    }
    local_report, drive_report = write_report(story_id, report)
    return report, local_report, drive_report


def dependency_failure_reason(status: dict[str, Any], *, debug_screenshots: bool, operator_required: bool = False, cdp_operator_required: bool = False) -> str:
    if cdp_operator_required:
        if not status.get("pyperclip_installed"):
            return "pyperclip_unavailable_preflight"
        if not status.get("playwright_installed"):
            return "playwright_unavailable_preflight"
        return ""
    if not status.get("pyautogui_installed"):
        return "pyautogui_unavailable_preflight"
    if not status.get("pyperclip_installed"):
        return "pyperclip_unavailable_preflight"
    if (debug_screenshots or operator_required) and not status.get("pillow_installed"):
        return "pillow_unavailable_preflight"
    if operator_required and not status.get("pygetwindow_installed"):
        return "pygetwindow_unavailable_preflight"
    return ""


def launch_existing_profile_operator(
    worker: WorkerConfig,
    *,
    story_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    profile_dir = Path(worker.profile_dir) if worker.profile_dir else None
    is_cdp_operator = worker.launch_mode == "existing_profiles_cdp_operator"
    operator_script = PROJECT_ROOT / "tools" / "colab_launcher" / ("operator_cdp_single_worker.py" if is_cdp_operator else "operator_single_worker.py")
    command = [
        sys.executable,
        str(operator_script),
        "--email",
        worker.email,
        "--browser",
        worker.browser,
        "--story-id",
        story_id,
    ]
    if worker.profile_dir:
        command.extend(["--profile-dir", worker.profile_dir])
    if worker.require_t4:
        command.append("--require-t4")

    warnings: list[str] = []
    errors: list[str] = []
    profile_dir_exists = bool(profile_dir and profile_dir.is_dir())
    if not worker.profile_dir:
        errors.append("profile_dir_missing_in_config")
    elif not profile_dir_exists:
        errors.append(f"profile_dir_missing: {worker.profile_dir}")

    if dry_run:
        return {
            "email": worker.email,
            "browser": worker.browser,
            "group": worker.group,
            "profile_strategy": worker.profile_strategy,
            "profile_dir": worker.profile_dir,
            "profile_dir_exists": profile_dir_exists,
            "use_user_data_dir": True,
            "launch_mode": worker.launch_mode,
            "opened_url": "https://colab.research.google.com/?authuser=<email>#create=true",
            "opened": False,
            "notebook_url": "",
            "require_t4": worker.require_t4,
            "code_injected": False,
            "auto_run_attempted": False,
            "auto_run_result": {"attempted": False, "reason": "dry_run"},
            "operator_command": command,
            "reason": "dry_run" if not errors else "profile_preflight_failed",
            "manual_action_required": bool(errors),
            "debug_screenshots": True,
            "debug_dir": str(PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports" / ("operator_cdp_debug" if is_cdp_operator else "operator_debug") / safe_email_name(worker.email)),
            "warnings": warnings,
            "errors": errors,
        }

    if errors:
        return {
            "email": worker.email,
            "browser": worker.browser,
            "group": worker.group,
            "profile_strategy": worker.profile_strategy,
            "profile_dir": worker.profile_dir,
            "profile_dir_exists": profile_dir_exists,
            "use_user_data_dir": True,
            "launch_mode": worker.launch_mode,
            "opened_url": "",
            "opened": False,
            "notebook_url": "",
            "require_t4": worker.require_t4,
            "code_injected": False,
            "auto_run_attempted": False,
            "auto_run_result": {"attempted": False, "reason": "profile_preflight_failed"},
            "operator_command": command,
            "reason": "profile_preflight_failed",
            "manual_action_required": True,
            "debug_screenshots": True,
            "debug_dir": str(PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports" / ("operator_cdp_debug" if is_cdp_operator else "operator_debug") / safe_email_name(worker.email)),
            "warnings": warnings,
            "errors": errors,
        }

    started_at = utc_now()
    result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
    operator_report_path = PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports" / ("operator_cdp_single_worker_report.json" if is_cdp_operator else "operator_single_worker_report.json")
    operator_report: dict[str, Any] = {}
    if operator_report_path.is_file():
        try:
            operator_report = json.loads(operator_report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"operator_report_read_failed: {exc}")
    else:
        warnings.append("operator_report_not_found")
    if result.returncode != 0 and not operator_report:
        errors.append(f"operator_return_code={result.returncode}")

    operator_ok = bool(operator_report.get("ok"))
    return {
        "email": worker.email,
        "browser": worker.browser,
        "group": worker.group,
        "profile_strategy": worker.profile_strategy,
        "profile_dir": worker.profile_dir,
        "profile_dir_exists": profile_dir_exists,
        "use_user_data_dir": True,
        "launch_mode": worker.launch_mode,
        "opened_url": operator_report.get("opened_url", ""),
        "opened": bool(operator_report.get("window_found") or operator_report.get("opened_urls") or operator_report.get("page_found")),
        "notebook_url": "",
        "require_t4": worker.require_t4,
        "code_injected": bool(operator_report.get("code_injected")),
        "auto_run_attempted": bool(operator_report.get("run_attempted")),
        "auto_run_result": operator_report,
        "operator_command": command,
        "operator_return_code": result.returncode,
        "operator_stdout": result.stdout,
        "operator_stderr": result.stderr,
        "operator_started_at": started_at,
        "reason": operator_report.get("reason", "operator_failed"),
        "manual_action_required": not operator_ok,
        "debug_screenshots": True,
        "debug_dir": str(operator_report.get("screenshots_dir") or PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports" / ("operator_cdp_debug" if is_cdp_operator else "operator_debug") / safe_email_name(worker.email)),
        "warnings": warnings + list(operator_report.get("warnings") or []),
        "errors": errors + list(operator_report.get("errors") or []),
    }


def focus_browser_window(browser: str) -> dict[str, Any]:
    try:
        import pygetwindow as gw  # type: ignore
    except Exception as exc:
        return {"ok": False, "reason": "pygetwindow_unavailable", "error": repr(exc)}
    markers = ["Colab", "Google Colab", "Untitled"]
    markers.append("Yandex" if browser == "yandex" else "Chrome")
    windows = []
    try:
        windows = [window for window in gw.getAllWindows() if any(marker.lower() in (window.title or "").lower() for marker in markers)]
    except Exception as exc:
        return {"ok": False, "reason": "window_list_failed", "error": repr(exc)}
    if not windows:
        return {"ok": False, "reason": "browser_window_not_found"}
    window = windows[-1]
    try:
        window.activate()
        time.sleep(0.5)
        try:
            window.maximize()
        except Exception:
            pass
        return {
            "ok": True,
            "title": window.title or "",
            "left": int(window.left),
            "top": int(window.top),
            "width": int(window.width),
            "height": int(window.height),
        }
    except Exception as exc:
        return {"ok": False, "reason": "window_activate_failed", "title": window.title or "", "error": repr(exc)}


def detect_prepared_worker_start(email: str) -> dict[str, Any]:
    sentinel = f"CONTENT_FACTORY_PREPARED_RUN_VERIFY_{int(time.time() * 1000)}"
    set_clipboard_text(sentinel)
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        return {"attempted": True, "ok": False, "reason": "pyautogui_unavailable", "error": repr(exc)}
    configure_pyautogui_for_launcher(pyautogui)
    try:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.8)
    except Exception as exc:
        return {"attempted": True, "ok": False, "reason": "copy_page_text_failed", "error": repr(exc)}
    copied = get_clipboard_text()
    markers = [
        "WORKER_EMAIL",
        email,
        "ContentFactory_YouTube",
        "nvidia-smi",
        "[BOOT]",
        "youtube video bootstrap resolved root",
        "[CLAIM]",
        "[LOOP]",
        "[HEARTBEAT]",
    ]
    found = [marker for marker in markers if marker in copied]
    stale_clipboard = copied == sentinel
    return {
        "attempted": True,
        "ok": bool(found and not stale_clipboard),
        "reason": "worker_output_detected" if found and not stale_clipboard else "worker_output_not_detected",
        "found_markers": found,
        "stale_clipboard": stale_clipboard,
        "copied_chars": len(copied),
    }


def click_relative_to_window(pyautogui: Any, window_info: dict[str, Any], x_ratio: float, y_ratio: float) -> None:
    if window_info.get("ok") and window_info.get("width") and window_info.get("height"):
        x = int(window_info["left"] + (window_info["width"] * x_ratio))
        y = int(window_info["top"] + (window_info["height"] * y_ratio))
    else:
        screen_width, screen_height = pyautogui.size()
        x = int(screen_width * x_ratio)
        y = int(screen_height * y_ratio)
    pyautogui.click(x, y)


def autorun_event(kind: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "ts": utc_now(), **payload}


def _event_kinds(events: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("kind") or "") for item in events if isinstance(item, dict)}


def summarize_browser_tab_autorun(events: list[dict[str, Any]], *, confirmation: dict[str, Any], failure_reason: str = "") -> dict[str, Any]:
    kinds = _event_kinds(events)
    backend = "playwright_cdp"
    for item in events:
        if isinstance(item, dict) and item.get("backend"):
            backend = str(item.get("backend"))
            break
    run_all_clicked = "run_all_clicked" in kinds
    ctrl_f9_sent = "ctrl_f9_attempted" in kinds
    heartbeat_restored = "heartbeat_restored" in kinds
    page_output_detected = bool(confirmation.get("page_output_detected") or "page_output_detected" in kinds)
    stopped_at = failure_reason or ""
    if not stopped_at and not heartbeat_restored:
        for step in (
            "browser_connection_failed",
            "colab_tab_not_found",
            "drive_permission_required",
            "oauth_required",
            "runtime_menu_not_found",
            "run_all_not_found",
            "browser_tab_autorun_failed",
        ):
            if step in kinds:
                stopped_at = step
                break
        if not stopped_at and page_output_detected:
            stopped_at = "cell_output_only_no_heartbeat"
        if not stopped_at and "manual_run_required" in kinds:
            stopped_at = "manual_run_required"
        if not stopped_at:
            stopped_at = "heartbeat_not_restored"
    oauth_continue_click_count = sum(1 for item in events if isinstance(item, dict) and item.get("kind") == "oauth_continue_clicked")
    return {
        "backend": backend,
        "browser_tab_autorun_attempted": "browser_tab_autorun_started" in kinds,
        "browser_connected": "browser_connection_ok" in kinds,
        "browser_connection_failed": "browser_connection_failed" in kinds,
        "colab_tab_found": "colab_tab_found" in kinds,
        "runtime_menu_found": "runtime_menu_found" in kinds,
        "runtime_menu_clicked": "runtime_menu_clicked" in kinds,
        "run_all_found": "run_all_found" in kinds,
        "run_all_clicked": run_all_clicked,
        "ctrl_f9_sent_to_tab": ctrl_f9_sent,
        "warning_modal_detected": "warning_modal_detected" in kinds,
        "warning_modal_confirmed": "warning_modal_confirmed" in kinds,
        "cell_start_attempted": "cell_start_attempted" in kinds,
        "cell_started_unconfirmed": "cell_started_unconfirmed" in kinds,
        "page_output_detected": page_output_detected,
        "drive_connect_detected": "drive_connect_detected" in kinds,
        "drive_connect_clicked": "drive_connect_clicked" in kinds,
        "drive_permission_required": "drive_permission_required" in kinds,
        "drive_permission_handled": "drive_permission_handled" in kinds,
        "oauth_popup_detected": "oauth_popup_detected" in kinds,
        "oauth_continue_clicked": "oauth_continue_clicked" in kinds,
        "oauth_continue_click_count": oauth_continue_click_count,
        "oauth_required": "oauth_required" in kinds,
        "oauth_handled": "oauth_handled" in kinds,
        "heartbeat_wait_started": "heartbeat_wait_started" in kinds,
        "heartbeat_restored": heartbeat_restored,
        "worker_started_confirmed": "worker_started_confirmed" in kinds,
        "autorun_success": "autorun_success" in kinds,
        "worker_output_detected": page_output_detected,
        "failure_step": stopped_at,
        "exact_failure_reason": failure_reason or stopped_at or "",
    }


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def wait_for_cdp_port(port: int, timeout_seconds: int = 30) -> bool:
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _prepared_output_markers(email: str) -> list[str]:
    return [
        "WORKER_EMAIL",
        email,
        "ContentFactory_YouTube",
        "nvidia-smi",
        "[BOOT]",
        "[BOOT] worker starting",
        "worker starting",
        "youtube video bootstrap resolved root",
        "[CLAIM]",
        "[LOOP]",
        "[HEARTBEAT]",
    ]


def _page_has_prepared_worker_output(page: Any, email: str) -> dict[str, Any]:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        try:
            text = page.content()
        except Exception as exc:
            return {"attempted": True, "ok": False, "reason": "page_text_unavailable", "error": repr(exc)}
    found = [marker for marker in _prepared_output_markers(email) if marker in text]
    return {
        "attempted": True,
        "ok": bool(found),
        "reason": "worker_output_detected" if found else "worker_output_not_detected",
        "found_markers": found,
        "copied_chars": len(text),
    }


def _live_worker_output_markers() -> list[str]:
    return [
        "[BOOT] worker starting",
        "[BOOT]",
        "[HEARTBEAT]",
        "[LOOP]",
        "[CLAIM]",
        "youtube video bootstrap resolved root",
        "nvidia-smi",
    ]


def _page_has_live_worker_output(page: Any, email: str) -> dict[str, Any]:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        try:
            text = page.content()
        except Exception as exc:
            return {"attempted": True, "ok": False, "reason": "page_text_unavailable", "error": repr(exc)}
    found = [marker for marker in _live_worker_output_markers() if marker in text]
    email_runtime_hint = email in text and any(marker in text for marker in ("[HEARTBEAT]", "[BOOT]", "[LOOP]", "[CLAIM]"))
    if email_runtime_hint and email not in found:
        found.append(email)
    return {
        "attempted": True,
        "ok": bool(found),
        "reason": "live_worker_output_detected" if found else "live_worker_output_not_detected",
        "found_markers": found,
        "copied_chars": len(text),
    }


def _click_first_visible_text(page: Any, patterns: list[str], *, timeout_ms: int = 2500) -> str:
    for pattern in patterns:
        regex = re.compile(pattern, re.IGNORECASE)
        candidates = [
            page.get_by_role("button", name=regex),
            page.get_by_role("menuitem", name=regex),
            page.get_by_role("link", name=regex),
            page.get_by_text(regex),
        ]
        for candidate in candidates:
            try:
                first = candidate.first
                first.click(timeout=timeout_ms)
                return pattern
            except Exception:
                continue
    return ""


def _focus_first_code_cell_cdp(page: Any, events: list[dict[str, Any]]) -> bool:
    selectors = [
        "colab-cell",
        "colab-code-cell",
        ".cell.code",
        ".code-cell",
        ".cm-content",
        ".CodeMirror-code",
        "[role='textbox']",
        "textarea",
        "[contenteditable='true']",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.scroll_into_view_if_needed(timeout=3000)
            locator.click(timeout=3000)
            events.append(autorun_event("cell_focused", strategy="cdp_selector", selector=selector))
            return True
        except Exception:
            continue
    try:
        page.mouse.click(520, 330)
        events.append(autorun_event("cell_focused", strategy="cdp_fallback_body_click"))
        return True
    except Exception as exc:
        events.append(autorun_event("cell_focus_failed", strategy="cdp", error=repr(exc)))
        return False


def _handle_oauth_popups_cdp(context: Any, colab_page: Any, events: list[dict[str, Any]]) -> None:
    for _ in range(4):
        clicked_any = False
        for page in list(context.pages):
            try:
                title = page.title(timeout=1000)
                url = page.url
            except Exception:
                title = ""
                url = ""
            is_oauth_like = page is not colab_page and (
                "accounts.google.com" in url or "OAuth" in title or "Google" in title or "Sign in" in title
            )
            if not is_oauth_like:
                continue
            try:
                page.bring_to_front()
            except Exception:
                pass
            matched = _click_first_visible_text(page, [r"Продолжить", r"Continue"], timeout_ms=3000)
            if matched:
                clicked_any = True
                events.append(autorun_event("oauth_popup_continue_clicked", matched=matched, page_url=url))
                time.sleep(2)
        if not clicked_any:
            break
    try:
        colab_page.bring_to_front()
    except Exception:
        pass


def _handle_permission_prompts_cdp(page: Any, events: list[dict[str, Any]]) -> None:
    warning_matched = _click_first_visible_text(
        page,
        [
            r"Выполнить",
            r"Run anyway",
            r"^Run$",
        ],
        timeout_ms=2500,
    )
    if warning_matched:
        events.append(autorun_event("warning_modal_confirmed", matched=warning_matched))
        time.sleep(2)

    drive_matched = _click_first_visible_text(
        page,
        [
            r"Подключиться к Google Диску",
            r"Connect to Google Drive",
        ],
        timeout_ms=2500,
    )
    if drive_matched:
        events.append(autorun_event("drive_connect_clicked", matched=drive_matched))
        time.sleep(2)

    try:
        _handle_oauth_popups_cdp(page.context, page, events)
    except Exception as exc:
        events.append(autorun_event("oauth_popup_continue_failed", error=repr(exc)))


def _wait_for_cdp_worker_output(page: Any, email: str, seconds: int, events: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, int(seconds))
    confirmation: dict[str, Any] = {"attempted": True, "ok": False, "reason": "not_checked"}
    while time.monotonic() < deadline:
        _handle_permission_prompts_cdp(page, events)
        confirmation = _page_has_prepared_worker_output(page, email)
        if confirmation.get("ok"):
            events.append(autorun_event("autorun_success", strategy=strategy, confirmation=confirmation))
            return confirmation
        time.sleep(2)
    return confirmation


def _find_colab_page_for_url(browser: Any, notebook_url: str) -> Any | None:
    target = (notebook_url or "").strip().lower()
    target_tail = target.rstrip("/").split("/")[-1] if target else ""
    candidates: list[Any] = []
    for context in browser.contexts:
        for page in context.pages:
            url = (page.url or "").lower()
            if "colab.research.google.com" not in url:
                continue
            if target and target in url:
                return page
            if target_tail and target_tail in url:
                return page
            candidates.append(page)
    return candidates[-1] if candidates else None


def _page_text_contains_any(page: Any, patterns: list[str]) -> str:
    try:
        text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        try:
            text = page.content()
        except Exception:
            return ""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return ""


def _handle_permission_prompts_browser_tab(page: Any, events: list[dict[str, Any]], *, max_oauth_continue_clicks: int = 2) -> dict[str, Any]:
    state = {
        "warning_detected": False,
        "warning_confirmed": False,
        "drive_permission_required": False,
        "drive_permission_handled": False,
        "oauth_required": False,
        "oauth_handled": False,
        "oauth_continue_click_count": 0,
    }
    warning_detected = _page_text_contains_any(
        page,
        [
            r"wasn.t authored by Google",
            r"не создан Google",
            r"not authored by Google",
            r"untrusted notebook",
        ],
    )
    if warning_detected:
        state["warning_detected"] = True
        events.append(autorun_event("warning_modal_detected", matched=warning_detected))
    warning_matched = _click_first_visible_text(
        page,
        [
            r"Выполнить",
            r"Run anyway",
            r"^Run$",
        ],
        timeout_ms=2500,
    )
    if warning_matched:
        state["warning_confirmed"] = True
        events.append(autorun_event("warning_modal_confirmed", matched=warning_matched))
        time.sleep(2)

    drive_detected = _page_text_contains_any(
        page,
        [
            r"Подключиться к Google Диску",
            r"Connect to Google Drive",
            r"Connect to Drive",
        ],
    )
    if drive_detected:
        state["drive_permission_required"] = True
        events.append(autorun_event("drive_connect_detected", matched=drive_detected))
        events.append(autorun_event("drive_permission_required", matched=drive_detected))
    drive_matched = _click_first_visible_text(
        page,
        [
            r"Подключиться к Google Диску",
            r"Connect to Google Drive",
            r"Connect to Drive",
        ],
        timeout_ms=2500,
    )
    if drive_matched:
        state["drive_permission_handled"] = True
        events.append(autorun_event("drive_connect_clicked", matched=drive_matched))
        events.append(autorun_event("drive_permission_handled", matched=drive_matched))
        time.sleep(2)

    for page_item in list(page.context.pages):
        try:
            url = page_item.url
            title = page_item.title(timeout=1000)
        except Exception:
            url = ""
            title = ""
        is_oauth_like = page_item is not page and (
            "accounts.google.com" in url or "OAuth" in title or "Sign in" in title
        )
        if not is_oauth_like:
            continue
        state["oauth_required"] = True
        events.append(autorun_event("oauth_popup_detected", page_url=url, title=title))
        events.append(autorun_event("oauth_required", page_url=url, title=title))
        try:
            page_item.bring_to_front()
        except Exception:
            pass
        for _ in range(max(0, int(max_oauth_continue_clicks))):
            matched = _click_first_visible_text(page_item, [r"Продолжить", r"Continue"], timeout_ms=3000)
            if not matched:
                break
            state["oauth_handled"] = True
            state["oauth_continue_click_count"] += 1
            events.append(autorun_event("oauth_continue_clicked", matched=matched, page_url=url))
            time.sleep(2)
        if state["oauth_handled"]:
            events.append(
                autorun_event(
                    "oauth_handled",
                    page_url=url,
                    continue_click_count=state["oauth_continue_click_count"],
                )
            )
    try:
        page.bring_to_front()
    except Exception:
        pass
    return state


def _attempt_runtime_run_all_browser_tab(page: Any, events: list[dict[str, Any]]) -> bool:
    events.append(autorun_event("runtime_menu_search_started"))
    runtime_selectors = [
        "#runtime-menu-button",
        "#colab-toolbar .actionbutton",
        "colab-main-menu-button#runtime",
        "[aria-label='Runtime']",
        "[aria-label='Среда выполнения']",
        "button:has-text('Runtime')",
        "button:has-text('Среда выполнения')",
    ]
    runtime_matched = ""
    for selector in runtime_selectors:
        try:
            locator = page.locator(selector).first
            locator.click(timeout=3000)
            runtime_matched = selector
            break
        except Exception:
            continue
    if not runtime_matched:
        runtime_patterns = [
            r"^Runtime$",
            r"Среда выполнения",
            r"Время выполнения",
            r"Среда",
        ]
        runtime_matched = _click_first_visible_text(page, runtime_patterns, timeout_ms=5000)
    if not runtime_matched:
        events.append(autorun_event("runtime_menu_not_found"))
        return False
    events.append(autorun_event("runtime_menu_found", matched=runtime_matched))
    events.append(autorun_event("runtime_menu_clicked", matched=runtime_matched))
    time.sleep(0.8)

    events.append(autorun_event("run_all_search_started"))
    run_all_patterns = [
        r"Run all",
        r"Выполнить все",
        r"Run all cells",
        r"Выполнить все ячейки",
    ]
    run_all_matched = _click_first_visible_text(page, run_all_patterns, timeout_ms=5000)
    if not run_all_matched:
        events.append(autorun_event("run_all_not_found"))
        return False
    events.append(autorun_event("run_all_found", matched=run_all_matched))
    events.append(autorun_event("run_all_clicked", matched=run_all_matched))
    return True


def _wait_for_browser_tab_worker_output(
    page: Any,
    email: str,
    seconds: int,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    events.append(autorun_event("heartbeat_wait_started"))
    deadline = time.monotonic() + max(1, int(seconds))
    confirmation: dict[str, Any] = {
        "attempted": True,
        "ok": False,
        "reason": "page_output_not_detected",
        "page_output_detected": False,
        "drive_permission_required": False,
        "drive_permission_handled": False,
        "oauth_required": False,
        "oauth_handled": False,
        "oauth_continue_click_count": 0,
    }
    while time.monotonic() < deadline:
        prompt_state = _handle_permission_prompts_browser_tab(page, events)
        confirmation["drive_permission_required"] = bool(
            confirmation.get("drive_permission_required") or prompt_state.get("drive_permission_required")
        )
        confirmation["drive_permission_handled"] = bool(
            confirmation.get("drive_permission_handled") or prompt_state.get("drive_permission_handled")
        )
        confirmation["oauth_required"] = bool(confirmation.get("oauth_required") or prompt_state.get("oauth_required"))
        confirmation["oauth_handled"] = bool(confirmation.get("oauth_handled") or prompt_state.get("oauth_handled"))
        confirmation["oauth_continue_click_count"] = int(confirmation.get("oauth_continue_click_count") or 0) + int(
            prompt_state.get("oauth_continue_click_count") or 0
        )

        page_confirmation = _page_has_live_worker_output(page, email)
        if page_confirmation.get("ok"):
            confirmation.update(page_confirmation)
            confirmation["ok"] = False
            confirmation["page_output_detected"] = True
            confirmation["reason"] = "cell_started_unconfirmed"
            events.append(autorun_event("page_output_detected", confirmation=page_confirmation))
            events.append(autorun_event("cell_started_unconfirmed", confirmation=page_confirmation))
            return confirmation
        time.sleep(2)
    return confirmation


def _build_launch_args_with_cdp_port(launch_args: list[str], port: int) -> list[str]:
    args = list(launch_args)
    if not any(item.startswith("--remote-debugging-port=") for item in args):
        insert_at = 2 if len(args) >= 2 else len(args)
        args.insert(insert_at, f"--remote-debugging-port={port}")
        args.insert(insert_at + 1, "--remote-allow-origins=*")
    return args


def run_browser_tab_autorun(
    *,
    worker: WorkerConfig,
    browser_exe: Path,
    launch_args: list[str],
    notebook_url: str,
    debug_dir: Path,
    wait_after_open_seconds: int,
    wait_for_run_start_seconds: int,
    reuse_profile_window: bool,
    dry_run: bool,
) -> dict[str, Any]:
    backend = "playwright_cdp"
    autorun_started_at = utc_now()
    events: list[dict[str, Any]] = [
        autorun_event(
            "browser_tab_autorun_started",
            backend=backend,
            autorun_mode="browser-tab",
            autorun_started_at=autorun_started_at,
        ),
    ]
    attempts: list[dict[str, Any]] = []
    screenshots: dict[str, str] = {}
    warnings: list[str] = []
    failure_reason = ""

    if dry_run:
        events.extend(
            [
                autorun_event("browser_connection_attempted", backend=backend, dry_run=True),
                autorun_event("runtime_menu_search_started", dry_run=True),
                autorun_event("run_all_search_started", dry_run=True),
            ]
        )
        summary = summarize_browser_tab_autorun(events, confirmation={"attempted": False, "ok": False, "reason": "dry_run"})
        return {
            "opened": False,
            "run_attempted": True,
            "worker_started_detected": False,
            "confirmation": {"attempted": False, "ok": False, "reason": "dry_run"},
            "screenshots": screenshots,
            "warnings": warnings,
            "attempts": attempts,
            "prompt_attempts": [],
            "events": events,
            "autorun_mode": "browser-tab",
            "autorun_summary": summary,
            "failure_reason": "dry_run",
        }

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        failure_reason = "playwright_unavailable"
        events.append(autorun_event("browser_connection_attempted", backend=backend))
        events.append(autorun_event("browser_connection_failed", error=repr(exc)))
        events.append(autorun_event("browser_tab_autorun_failed", failure_step="playwright_unavailable", error=repr(exc)))
        events.append(autorun_event("manual_run_required", reason=failure_reason))
        confirmation = {"attempted": False, "ok": False, "reason": failure_reason, "error": repr(exc)}
        summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
        return {
            "opened": False,
            "run_attempted": False,
            "worker_started_detected": False,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": [f"browser_tab_autorun_unavailable: {exc!r}"],
            "attempts": attempts,
            "prompt_attempts": [],
            "events": events,
            "autorun_mode": "browser-tab",
            "autorun_summary": summary,
            "failure_reason": failure_reason,
        }

    port = find_free_port()
    args = _build_launch_args_with_cdp_port(launch_args, port)
    if reuse_profile_window and "--new-window" in args:
        args = [item for item in args if item != "--new-window"]

    events.append(autorun_event("browser_connection_attempted", backend=backend, cdp_port=port))
    proc: subprocess.Popen[Any] | None = None
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        events.append(autorun_event("notebook_opened", strategy="browser-tab", cdp_port=port))
        if wait_after_open_seconds > 0:
            time.sleep(int(wait_after_open_seconds))

        if not wait_for_cdp_port(port, timeout_seconds=45):
            failure_reason = "cdp_port_not_available"
            events.append(autorun_event("browser_connection_failed", reason=failure_reason, cdp_port=port))
            events.append(autorun_event("browser_tab_autorun_failed", failure_step=failure_reason))
            events.append(autorun_event("manual_run_required", reason=failure_reason))
            confirmation = {"attempted": True, "ok": False, "reason": failure_reason}
            summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
            return {
                "opened": True,
                "run_attempted": False,
                "worker_started_detected": False,
                "confirmation": confirmation,
                "screenshots": screenshots,
                "warnings": ["browser_tab_cdp_port_not_available"],
                "attempts": attempts,
                "prompt_attempts": [],
                "events": events,
                "autorun_mode": "browser-tab",
                "autorun_summary": summary,
                "failure_reason": failure_reason,
            }

        events.append(autorun_event("browser_connection_ok", backend=backend, cdp_port=port))

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            deadline = time.monotonic() + 30
            page = None
            while time.monotonic() < deadline:
                page = _find_colab_page_for_url(browser, notebook_url)
                if page is not None:
                    break
                time.sleep(1)

            if page is None:
                failure_reason = "colab_tab_not_found"
                events.append(autorun_event("colab_tab_not_found", notebook_url=notebook_url))
                events.append(autorun_event("browser_tab_autorun_failed", failure_step=failure_reason))
                events.append(autorun_event("manual_run_required", reason=failure_reason))
                browser.close()
                confirmation = {"attempted": True, "ok": False, "reason": failure_reason}
                summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
                return {
                    "opened": True,
                    "run_attempted": False,
                    "worker_started_detected": False,
                    "confirmation": confirmation,
                    "screenshots": screenshots,
                    "warnings": ["browser_tab_colab_tab_not_found"],
                    "attempts": attempts,
                    "prompt_attempts": [],
                    "events": events,
                    "autorun_mode": "browser-tab",
                    "autorun_summary": summary,
                    "failure_reason": failure_reason,
                }

            events.append(autorun_event("colab_tab_found", page_url=page.url))
            try:
                page.bring_to_front()
            except Exception:
                pass
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            events.append(autorun_event("colab_loaded", page_url=page.url))

            debug_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = debug_dir / "browser_tab_after_load.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
                screenshots["browser_tab_after_load"] = str(screenshot_path)
            except Exception:
                pass

            _handle_permission_prompts_browser_tab(page, events)
            run_all_clicked = _attempt_runtime_run_all_browser_tab(page, events)
            ctrl_f9_sent = False
            if not run_all_clicked:
                events.append(autorun_event("ctrl_f9_attempted", backend=backend))
                page.keyboard.press("Control+F9")
                ctrl_f9_sent = True
                time.sleep(1)
            events.append(
                autorun_event(
                    "cell_start_attempted",
                    method="ctrl_f9" if ctrl_f9_sent else "runtime_run_all_menu",
                    run_all_clicked=run_all_clicked,
                    ctrl_f9_sent=ctrl_f9_sent,
                )
            )

            confirmation = _wait_for_browser_tab_worker_output(
                page,
                worker.email,
                max(20, int(wait_for_run_start_seconds)),
                events,
            )
            attempts.append(
                {
                    "method": "ctrl_f9" if ctrl_f9_sent else "runtime_run_all_menu",
                    "strategy": "browser_tab_cdp",
                    "run_all_clicked": run_all_clicked,
                    "ctrl_f9_sent": ctrl_f9_sent,
                    "confirmation": confirmation,
                }
            )

            worker_started = False
            if confirmation.get("drive_permission_required") and not confirmation.get("drive_permission_handled"):
                failure_reason = "drive_permission_not_handled"
            elif confirmation.get("oauth_required") and not confirmation.get("oauth_handled"):
                failure_reason = "oauth_popup_not_handled"
            elif confirmation.get("page_output_detected"):
                failure_reason = "cell_output_only_no_heartbeat"
            elif not run_all_clicked and not ctrl_f9_sent:
                failure_reason = "runtime_not_started"
            else:
                failure_reason = "heartbeat_not_restored"
            events.append(
                autorun_event(
                    "browser_tab_autorun_failed",
                    failure_step=failure_reason,
                    confirmation=confirmation,
                    run_all_clicked=run_all_clicked,
                    ctrl_f9_sent=ctrl_f9_sent,
                )
            )
            events.append(
                autorun_event(
                    "manual_run_required",
                    reason=failure_reason,
                    operator_action=(
                        "Нажать Connect to Google Drive / Подключиться к Google Диску, "
                        "затем Continue / Продолжить два раза"
                    )
                    if failure_reason in {"drive_permission_not_handled", "oauth_popup_not_handled"}
                    else "Проверить Colab output и запустить Runtime -> Run all вручную",
                )
            )

            try:
                browser.close()
            except Exception:
                pass

            summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
            summary["run_all_clicked"] = run_all_clicked
            summary["ctrl_f9_sent_to_tab"] = ctrl_f9_sent
            return {
                "opened": True,
                "run_attempted": True,
                "worker_started_detected": worker_started,
                "confirmation": confirmation,
                "screenshots": screenshots,
                "warnings": warnings,
                "attempts": attempts,
                "prompt_attempts": [],
                "events": events,
                "autorun_mode": "browser-tab",
                "autorun_summary": summary,
                "failure_reason": failure_reason,
            }
    except Exception as exc:
        failure_reason = "browser_tab_autorun_exception"
        events.append(autorun_event("browser_tab_autorun_failed", failure_step=failure_reason, error=repr(exc)))
        events.append(autorun_event("manual_run_required", reason=failure_reason, error=repr(exc)))
        confirmation = {"attempted": True, "ok": False, "reason": failure_reason, "error": repr(exc)}
        summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
        return {
            "opened": bool(proc),
            "run_attempted": bool(proc),
            "worker_started_detected": False,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": [f"browser_tab_autorun_failed: {exc!r}"],
            "attempts": attempts,
            "prompt_attempts": [],
            "events": events,
            "autorun_mode": "browser-tab",
            "autorun_summary": summary,
            "failure_reason": failure_reason,
        }


def run_prepared_notebook_sequence_cdp(
    *,
    worker: WorkerConfig,
    browser_exe: Path,
    launch_args: list[str],
    debug_dir: Path,
    wait_for_run_start_seconds: int,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = [autorun_event("autorun_strategy_started", strategy="cdp")]
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        return {
            "opened": False,
            "run_attempted": False,
            "worker_started_detected": False,
            "confirmation": {"attempted": False, "ok": False, "reason": "playwright_unavailable", "error": repr(exc)},
            "screenshots": {},
            "warnings": [f"prepared_notebook_cdp_unavailable: {exc!r}"],
            "attempts": [],
            "prompt_attempts": [],
            "events": events,
        }

    port = find_free_port()
    args = list(launch_args)
    args.insert(2, f"--remote-debugging-port={port}")
    args.insert(3, "--remote-allow-origins=*")
    attempts: list[dict[str, Any]] = []
    screenshots: dict[str, str] = {}
    warnings: list[str] = []
    proc: subprocess.Popen[Any] | None = None
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        events.append(autorun_event("notebook_opened", strategy="cdp", cdp_port=port))
        if not wait_for_cdp_port(port, timeout_seconds=35):
            return {
                "opened": True,
                "run_attempted": False,
                "worker_started_detected": False,
                "confirmation": {"attempted": False, "ok": False, "reason": "cdp_port_not_available"},
                "screenshots": screenshots,
                "warnings": ["prepared_notebook_cdp_port_not_available"],
                "attempts": attempts,
                "prompt_attempts": [],
                "events": events,
            }
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            pages = list(context.pages)
            page = next((item for item in reversed(pages) if "colab.research.google.com" in item.url), pages[-1] if pages else context.new_page())
            page.bring_to_front()
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            screenshots["cdp_after_load"] = str(debug_dir / "cdp_after_load.png")
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshots["cdp_after_load"], full_page=False)
            except Exception:
                screenshots.pop("cdp_after_load", None)

            if not _focus_first_code_cell_cdp(page, events):
                warnings.append("prepared_notebook_cdp_cell_focus_failed")

            for shortcut_name, keys in (("ctrl_enter", "Control+Enter"), ("shift_enter", "Shift+Enter")):
                events.append(autorun_event("hotkey_attempted", strategy="cdp", hotkey=shortcut_name))
                attempts.append({"method": shortcut_name, "strategy": "cdp"})
                page.keyboard.press(keys)
                confirmation = _wait_for_cdp_worker_output(page, worker.email, 8, events, shortcut_name)
                attempts[-1]["confirmation"] = confirmation
                if confirmation.get("ok"):
                    events.append(autorun_event("hotkey_success", hotkey=shortcut_name, confirmation=confirmation))
                    browser.close()
                    return {
                        "opened": True,
                        "run_attempted": True,
                        "worker_started_detected": True,
                        "confirmation": confirmation,
                        "screenshots": screenshots,
                        "warnings": warnings,
                        "attempts": attempts,
                        "prompt_attempts": [],
                        "events": events,
                    }

            events.append(autorun_event("context_menu_attempted", strategy="cdp"))
            attempts.append({"method": "context_menu", "strategy": "cdp"})
            _focus_first_code_cell_cdp(page, events)
            try:
                page.mouse.click(520, 330, button="right")
            except Exception:
                page.locator("body").click(button="right", timeout=3000)
            matched_context = _click_first_visible_text(
                page,
                [
                    r"Выполнить код с фокусированной ячейки",
                    r"Run focused cell",
                    r"Run cell",
                    r"Выполнить ячейку",
                ],
                timeout_ms=4000,
            )
            if matched_context:
                events.append(autorun_event("context_menu_item_clicked", matched=matched_context))
                confirmation = _wait_for_cdp_worker_output(page, worker.email, 12, events, "context_menu")
                attempts[-1]["confirmation"] = confirmation
                if confirmation.get("ok"):
                    browser.close()
                    return {
                        "opened": True,
                        "run_attempted": True,
                        "worker_started_detected": True,
                        "confirmation": confirmation,
                        "screenshots": screenshots,
                        "warnings": warnings,
                        "attempts": attempts,
                        "prompt_attempts": [],
                        "events": events,
                    }

            events.append(autorun_event("runtime_menu_attempted", strategy="cdp", shortcut="Control+F9"))
            attempts.append({"method": "ctrl_f9", "strategy": "cdp"})
            page.keyboard.press("Control+F9")
            confirmation = _wait_for_cdp_worker_output(page, worker.email, max(20, int(wait_for_run_start_seconds)), events, "runtime_ctrl_f9")
            attempts[-1]["confirmation"] = confirmation
            browser.close()
            return {
                "opened": True,
                "run_attempted": True,
                "worker_started_detected": bool(confirmation.get("ok")),
                "confirmation": confirmation,
                "screenshots": screenshots,
                "warnings": warnings,
                "attempts": attempts,
                "prompt_attempts": [],
                "events": events,
            }
    except Exception as exc:
        return {
            "opened": bool(proc),
            "run_attempted": bool(proc),
            "worker_started_detected": False,
            "confirmation": {"attempted": True, "ok": False, "reason": "cdp_autorun_exception", "error": repr(exc)},
            "screenshots": screenshots,
            "warnings": [f"prepared_notebook_cdp_autorun_failed: {exc!r}"],
            "attempts": attempts,
            "prompt_attempts": [],
            "events": events,
        }


def handle_prepared_notebook_prompts(
    pyautogui: Any,
    *,
    window_info: dict[str, Any],
    debug_dir: Path,
    screenshots: dict[str, str],
    phase: str,
) -> list[dict[str, Any]]:
    prompt_attempts: list[dict[str, Any]] = []

    # GitHub notebooks show a first-run warning modal. We prefer explicit clicks
    # on the right button area first; plain Enter can be sent into the cell if
    # focus is not on the modal.
    modal_focus_points = [(0.54, 0.52), (0.56, 0.54)]
    for x_ratio, y_ratio in modal_focus_points:
        click_relative_to_window(pyautogui, window_info, x_ratio, y_ratio)
        time.sleep(0.2)
    run_anyway_points = [(0.70, 0.64), (0.74, 0.64), (0.78, 0.64), (0.76, 0.68)]
    for index, (x_ratio, y_ratio) in enumerate(run_anyway_points, start=1):
        click_relative_to_window(pyautogui, window_info, x_ratio, y_ratio)
        time.sleep(0.6)
        path = save_debug_screenshot(debug_dir, f"after_github_warning_confirm_{phase}_{index}.png")
        screenshots.setdefault("after_github_warning_confirm", path)
        screenshots[f"after_github_warning_confirm_{phase}_{index}"] = path
        prompt_attempts.append(
            {
                "prompt": "github_warning",
                "phase": phase,
                "attempt": index,
                "method": "click_run_anyway_area",
                "x_ratio": x_ratio,
                "y_ratio": y_ratio,
                "screenshot": path,
            }
        )
    # Only after click attempts, do a conservative keyboard fallback.
    pyautogui.press("tab")
    time.sleep(0.2)
    pyautogui.press("right")
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.5)
    fallback_path = save_debug_screenshot(debug_dir, f"after_github_warning_confirm_{phase}_kbd.png")
    screenshots[f"after_github_warning_confirm_{phase}_kbd"] = fallback_path
    prompt_attempts.append(
        {
            "prompt": "github_warning",
            "phase": phase,
            "method": "tab_right_enter_fallback",
            "screenshot": fallback_path,
        }
    )

    # Drive mount can render a lightweight confirmation in the output area. This
    # is best-effort only and does not handle password/2FA/account pages.
    drive_points = [(0.50, 0.66), (0.62, 0.70), (0.72, 0.72)]
    for index, (x_ratio, y_ratio) in enumerate(drive_points, start=1):
        click_relative_to_window(pyautogui, window_info, x_ratio, y_ratio)
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.5)
        path = save_debug_screenshot(debug_dir, f"after_drive_mount_confirm_{phase}_{index}.png")
        screenshots.setdefault("after_drive_mount_confirm", path)
        screenshots[f"after_drive_mount_confirm_{phase}_{index}"] = path
        prompt_attempts.append(
            {
                "prompt": "drive_mount",
                "phase": phase,
                "attempt": index,
                "method": "click_possible_confirm_then_enter",
                "x_ratio": x_ratio,
                "y_ratio": y_ratio,
                "screenshot": path,
            }
        )
    return prompt_attempts


def attempt_runtime_run_all_menu(
    pyautogui: Any,
    *,
    window_info: dict[str, Any],
    debug_dir: Path,
    screenshots: dict[str, str],
    phase: str,
) -> list[dict[str, Any]]:
    """Best-effort Runtime -> Run all clicks for prepared Colab notebooks."""
    attempts: list[dict[str, Any]] = []
    runtime_menu_points = [(0.16, 0.045), (0.20, 0.045), (0.24, 0.045)]
    run_all_points = [(0.16, 0.12), (0.20, 0.14), (0.24, 0.16), (0.18, 0.18)]

    for index, (x_ratio, y_ratio) in enumerate(runtime_menu_points, start=1):
        click_relative_to_window(pyautogui, window_info, x_ratio, y_ratio)
        time.sleep(0.8)
        path = save_debug_screenshot(debug_dir, f"runtime_menu_{phase}_{index}.png")
        screenshots[f"runtime_menu_{phase}_{index}"] = path
        attempts.append(
            {
                "method": "runtime_menu_click",
                "phase": phase,
                "attempt": index,
                "x_ratio": x_ratio,
                "y_ratio": y_ratio,
                "screenshot": path,
            }
        )
        for run_index, (run_x, run_y) in enumerate(run_all_points, start=1):
            click_relative_to_window(pyautogui, window_info, run_x, run_y)
            time.sleep(0.4)
            run_path = save_debug_screenshot(debug_dir, f"runtime_run_all_{phase}_{index}_{run_index}.png")
            screenshots[f"runtime_run_all_{phase}_{index}_{run_index}"] = run_path
            attempts.append(
                {
                    "method": "runtime_run_all_click",
                    "phase": phase,
                    "menu_attempt": index,
                    "attempt": run_index,
                    "x_ratio": run_x,
                    "y_ratio": run_y,
                    "screenshot": run_path,
                }
            )
        pyautogui.press("esc")
        time.sleep(0.3)
    return attempts


def run_prepared_notebook_sequence(
    *,
    worker: WorkerConfig,
    debug_dir: Path,
    wait_for_run_start_seconds: int,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = [autorun_event("autorun_strategy_started", strategy="pyautogui")]
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        return {
            "run_attempted": False,
            "worker_started_detected": False,
            "confirmation": {"attempted": False, "ok": False, "reason": "pyautogui_unavailable", "error": repr(exc)},
            "screenshots": {},
            "warnings": [f"prepared_notebook_autorun_failed: {exc!r}"],
            "attempts": [],
            "prompt_attempts": [],
            "events": events,
        }
    configure_pyautogui_for_launcher(pyautogui)

    screenshots: dict[str, str] = {}
    warnings: list[str] = []
    attempts: list[dict[str, Any]] = []
    prompt_attempts: list[dict[str, Any]] = []
    window_info = focus_browser_window(worker.browser)
    if not window_info.get("ok"):
        warnings.append(f"prepared_notebook_focus_failed: {window_info}")

    for _ in range(2):
        pyautogui.press("esc")
        time.sleep(0.3)

    events.append(autorun_event("cell_focused", strategy="pyautogui_body_click"))
    for x_ratio, y_ratio in ((0.50, 0.35), (0.50, 0.42), (0.45, 0.38)):
        click_relative_to_window(pyautogui, window_info, x_ratio, y_ratio)
        time.sleep(0.35)
    pyautogui.click(clicks=2, interval=0.15)
    time.sleep(0.5)

    events.append(autorun_event("hotkey_attempted", strategy="pyautogui", hotkey="ctrl_enter"))
    pyautogui.hotkey("ctrl", "enter")
    screenshots["after_ctrl_enter"] = save_debug_screenshot(debug_dir, "after_ctrl_enter.png")
    prompt_attempts.extend(handle_prepared_notebook_prompts(pyautogui, window_info=window_info, debug_dir=debug_dir, screenshots=screenshots, phase="ctrl_enter"))
    time.sleep(10)
    confirmation = detect_prepared_worker_start(worker.email)
    attempts.append({"method": "ctrl_enter", "confirmation": confirmation})
    if confirmation.get("ok"):
        events.append(autorun_event("hotkey_success", hotkey="ctrl_enter", confirmation=confirmation))
        events.append(autorun_event("autorun_success", strategy="hotkey_ctrl_enter", confirmation=confirmation))
        return {
            "run_attempted": True,
            "worker_started_detected": True,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": warnings,
            "attempts": attempts,
            "prompt_attempts": prompt_attempts,
            "events": events,
        }

    click_relative_to_window(pyautogui, window_info, 0.50, 0.35)
    time.sleep(0.5)
    events.append(autorun_event("hotkey_attempted", strategy="pyautogui", hotkey="shift_enter"))
    pyautogui.hotkey("shift", "enter")
    screenshots["after_shift_enter"] = save_debug_screenshot(debug_dir, "after_shift_enter.png")
    prompt_attempts.extend(handle_prepared_notebook_prompts(pyautogui, window_info=window_info, debug_dir=debug_dir, screenshots=screenshots, phase="shift_enter"))
    time.sleep(10)
    confirmation = detect_prepared_worker_start(worker.email)
    attempts.append({"method": "shift_enter", "confirmation": confirmation})
    if confirmation.get("ok"):
        events.append(autorun_event("hotkey_success", hotkey="shift_enter", confirmation=confirmation))
        events.append(autorun_event("autorun_success", strategy="hotkey_shift_enter", confirmation=confirmation))
        return {
            "run_attempted": True,
            "worker_started_detected": True,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": warnings,
            "attempts": attempts,
            "prompt_attempts": prompt_attempts,
            "events": events,
        }

    events.append(autorun_event("context_menu_attempted", strategy="pyautogui"))
    click_relative_to_window(pyautogui, window_info, 0.50, 0.35)
    time.sleep(0.3)
    pyautogui.click(button="right")
    time.sleep(0.8)
    screenshots["after_context_menu"] = save_debug_screenshot(debug_dir, "after_context_menu.png")
    # The DOM/CDP path clicks this menu item by text. In pure pyautogui fallback
    # we use a conservative keyboard pick instead of aiming at the Play icon.
    pyautogui.press("down")
    time.sleep(0.1)
    pyautogui.press("enter")
    prompt_attempts.extend(handle_prepared_notebook_prompts(pyautogui, window_info=window_info, debug_dir=debug_dir, screenshots=screenshots, phase="context_menu"))
    time.sleep(10)
    confirmation = detect_prepared_worker_start(worker.email)
    attempts.append({"method": "context_menu_keyboard_fallback", "confirmation": confirmation})
    if confirmation.get("ok"):
        events.append(autorun_event("context_menu_item_clicked", strategy="pyautogui_keyboard_fallback"))
        events.append(autorun_event("autorun_success", strategy="context_menu", confirmation=confirmation))
        return {
            "run_attempted": True,
            "worker_started_detected": True,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": warnings,
            "attempts": attempts,
            "prompt_attempts": prompt_attempts,
            "events": events,
        }

    events.append(autorun_event("runtime_menu_attempted", strategy="pyautogui_menu_clicks"))
    runtime_attempts = attempt_runtime_run_all_menu(
        pyautogui,
        window_info=window_info,
        debug_dir=debug_dir,
        screenshots=screenshots,
        phase="after_context_menu",
    )
    attempts.extend(runtime_attempts)
    prompt_attempts.extend(handle_prepared_notebook_prompts(pyautogui, window_info=window_info, debug_dir=debug_dir, screenshots=screenshots, phase="runtime_run_all"))
    time.sleep(10)
    confirmation = detect_prepared_worker_start(worker.email)
    attempts.append({"method": "runtime_run_all", "confirmation": confirmation})
    if confirmation.get("ok"):
        events.append(autorun_event("autorun_success", strategy="runtime_menu", confirmation=confirmation))
        return {
            "run_attempted": True,
            "worker_started_detected": True,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": warnings,
            "attempts": attempts,
            "prompt_attempts": prompt_attempts,
            "events": events,
        }

    events.append(autorun_event("runtime_menu_attempted", strategy="pyautogui_ctrl_f9"))
    pyautogui.hotkey("ctrl", "f9")
    prompt_attempts.extend(handle_prepared_notebook_prompts(pyautogui, window_info=window_info, debug_dir=debug_dir, screenshots=screenshots, phase="ctrl_f9"))
    time.sleep(10)
    confirmation = detect_prepared_worker_start(worker.email)
    attempts.append({"method": "ctrl_f9", "confirmation": confirmation})
    if confirmation.get("ok"):
        events.append(autorun_event("autorun_success", strategy="ctrl_f9", confirmation=confirmation))
        return {
            "run_attempted": True,
            "worker_started_detected": True,
            "confirmation": confirmation,
            "screenshots": screenshots,
            "warnings": warnings,
            "attempts": attempts,
            "prompt_attempts": prompt_attempts,
            "events": events,
        }

    remaining_wait = max(0, int(wait_for_run_start_seconds) - 40)
    if remaining_wait:
        time.sleep(remaining_wait)
    confirmation = detect_prepared_worker_start(worker.email)
    attempts.append({"method": "final_wait", "wait_seconds": remaining_wait, "confirmation": confirmation})
    return {
        "run_attempted": True,
        "worker_started_detected": bool(confirmation.get("ok")),
        "confirmation": confirmation,
        "screenshots": screenshots,
        "warnings": warnings,
        "attempts": attempts,
        "prompt_attempts": prompt_attempts,
        "events": events,
    }


def launch_prepared_notebook_url(
    worker: WorkerConfig,
    browser_exe: Path | None,
    *,
    story_id: str,
    dry_run: bool,
    auto_run: bool,
    wait_after_open_seconds: int,
    wait_for_run_start_seconds: int,
    reuse_profile_window: bool = False,
    autorun_mode: str = "legacy",
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    args: list[str] = []
    opened = False
    run_attempted = False
    worker_started_detected = False
    run_confirmation: dict[str, Any] = {"attempted": False, "ok": False, "reason": "not_attempted"}
    run_attempts: list[dict[str, Any]] = []
    prompt_attempts: list[dict[str, Any]] = []
    auto_run_events: list[dict[str, Any]] = []
    autorun_summary: dict[str, Any] = {}
    failure_reason = ""
    normalized_autorun_mode = str(autorun_mode or "legacy").strip().lower().replace("_", "-")
    debug_dir = debug_dir_for(story_id, worker.email)
    screenshots: dict[str, str] = {}
    if not worker.notebook_url:
        errors.append("notebook_url_missing")
    if not worker.profile_dir:
        errors.append("profile_dir_missing_in_config")
    elif not Path(worker.profile_dir).is_dir():
        errors.append(f"profile_dir_missing: {worker.profile_dir}")
    if browser_exe is None:
        errors.append(f"{worker.browser}_executable_not_found")
    if browser_exe is not None and worker.notebook_url and worker.profile_dir:
        args = [
            str(browser_exe),
            f"--user-data-dir={worker.profile_dir}",
            worker.notebook_url,
        ]
        if not reuse_profile_window:
            args.insert(2, "--new-window")

    if not dry_run and not errors:
        try:
            launched_for_autorun = False
            if auto_run and normalized_autorun_mode == "browser-tab":
                browser_tab_result = run_browser_tab_autorun(
                    worker=worker,
                    browser_exe=browser_exe,
                    launch_args=args,
                    notebook_url=worker.notebook_url,
                    debug_dir=debug_dir,
                    wait_after_open_seconds=wait_after_open_seconds,
                    wait_for_run_start_seconds=wait_for_run_start_seconds,
                    reuse_profile_window=reuse_profile_window,
                    dry_run=False,
                )
                opened = opened or bool(browser_tab_result.get("opened"))
                launched_for_autorun = bool(browser_tab_result.get("opened"))
                run_attempted = bool(browser_tab_result.get("run_attempted"))
                worker_started_detected = bool(browser_tab_result.get("worker_started_detected"))
                run_confirmation = dict(browser_tab_result.get("confirmation") or {})
                run_attempts.extend(list(browser_tab_result.get("attempts") or []))
                prompt_attempts.extend(list(browser_tab_result.get("prompt_attempts") or []))
                auto_run_events.extend(list(browser_tab_result.get("events") or []))
                screenshots.update(dict(browser_tab_result.get("screenshots") or {}))
                warnings.extend(list(browser_tab_result.get("warnings") or []))
                autorun_summary = dict(browser_tab_result.get("autorun_summary") or {})
                failure_reason = str(browser_tab_result.get("failure_reason") or "")
                if not worker_started_detected and not any(
                    str(item.get("kind") or "") == "manual_run_required" for item in auto_run_events if isinstance(item, dict)
                ):
                    auto_run_events.append(
                        autorun_event("manual_run_required", reason=failure_reason or "browser_tab_autorun_not_confirmed")
                    )
            elif auto_run and normalized_autorun_mode != "manual":
                cdp_result = run_prepared_notebook_sequence_cdp(
                    worker=worker,
                    browser_exe=browser_exe,
                    launch_args=args,
                    debug_dir=debug_dir,
                    wait_for_run_start_seconds=wait_for_run_start_seconds,
                )
                opened = opened or bool(cdp_result.get("opened"))
                launched_for_autorun = bool(cdp_result.get("opened"))
                run_attempted = bool(cdp_result.get("run_attempted"))
                worker_started_detected = bool(cdp_result.get("worker_started_detected"))
                run_confirmation = dict(cdp_result.get("confirmation") or {})
                run_attempts.extend(list(cdp_result.get("attempts") or []))
                prompt_attempts.extend(list(cdp_result.get("prompt_attempts") or []))
                auto_run_events.extend(list(cdp_result.get("events") or []))
                screenshots.update(dict(cdp_result.get("screenshots") or {}))
                warnings.extend(list(cdp_result.get("warnings") or []))

                if not opened:
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    opened = True

                if not worker_started_detected:
                    try:
                        if not launched_for_autorun:
                            time.sleep(max(0, int(wait_after_open_seconds)))
                        screenshots["after_wait_before_run"] = save_debug_screenshot(debug_dir, "prepared_after_wait_before_run.png")
                        run_result = run_prepared_notebook_sequence(
                            worker=worker,
                            debug_dir=debug_dir,
                            wait_for_run_start_seconds=wait_for_run_start_seconds,
                        )
                        run_attempted = run_attempted or bool(run_result.get("run_attempted"))
                        worker_started_detected = worker_started_detected or bool(run_result.get("worker_started_detected"))
                        run_confirmation = dict(run_result.get("confirmation") or {})
                        run_attempts.extend(list(run_result.get("attempts") or []))
                        prompt_attempts.extend(list(run_result.get("prompt_attempts") or []))
                        auto_run_events.extend(list(run_result.get("events") or []))
                        screenshots.update(dict(run_result.get("screenshots") or {}))
                        warnings.extend(list(run_result.get("warnings") or []))
                        screenshots["after_run_wait"] = save_debug_screenshot(debug_dir, "prepared_after_run_wait.png")
                    except Exception as exc:
                        warnings.append(f"prepared_notebook_autorun_failed: {exc!r}")
                        run_confirmation = {"attempted": True, "ok": False, "reason": "autorun_exception", "error": repr(exc)}
                        auto_run_events.append(autorun_event("manual_run_required", reason="autorun_exception", error=repr(exc)))
            elif not auto_run or normalized_autorun_mode == "manual":
                if not dry_run:
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    opened = True
        except OSError as exc:
            errors.append(f"browser_launch_failed: {exc}")
    elif dry_run and auto_run and normalized_autorun_mode == "browser-tab" and not errors:
        browser_tab_result = run_browser_tab_autorun(
            worker=worker,
            browser_exe=browser_exe or Path("browser.exe"),
            launch_args=args,
            notebook_url=worker.notebook_url,
            debug_dir=debug_dir,
            wait_after_open_seconds=wait_after_open_seconds,
            wait_for_run_start_seconds=wait_for_run_start_seconds,
            reuse_profile_window=reuse_profile_window,
            dry_run=True,
        )
        run_attempted = bool(browser_tab_result.get("run_attempted"))
        auto_run_events.extend(list(browser_tab_result.get("events") or []))
        autorun_summary = dict(browser_tab_result.get("autorun_summary") or {})

    manual_action_required = bool(errors) or (not dry_run and (not opened or (auto_run and (not run_attempted or not worker_started_detected))))
    if auto_run and normalized_autorun_mode == "browser-tab" and not autorun_summary:
        autorun_summary = summarize_browser_tab_autorun(
            auto_run_events,
            confirmation=run_confirmation,
            failure_reason=failure_reason,
        )
    reason = "dry_run"
    if not dry_run:
        if errors:
            reason = "prepared_notebook_not_opened"
        elif auto_run and not run_attempted:
            reason = "run_not_attempted"
        elif auto_run and not worker_started_detected:
            reason = failure_reason or "run_not_confirmed"
        else:
            reason = "prepared_notebook_opened"
    return {
        "email": worker.email,
        "browser": worker.browser,
        "group": worker.group,
        "profile_strategy": worker.profile_strategy,
        "profile_dir": worker.profile_dir,
        "profile_dir_exists": bool(worker.profile_dir and Path(worker.profile_dir).is_dir()),
        "use_user_data_dir": True,
        "launch_mode": worker.launch_mode,
        "opened_url": worker.notebook_url,
        "opened": opened,
        "notebook_path": worker.notebook_path,
        "notebook_url": worker.notebook_url,
        "require_t4": worker.require_t4,
        "code_injected": True if worker.notebook_url else False,
        "run_attempted": run_attempted,
        "worker_started_detected": worker_started_detected,
        "auto_run_attempted": run_attempted,
        "auto_run_result": {
            "attempted": bool(auto_run),
            "ok": bool(worker_started_detected) if auto_run else True,
            "reason": (
                "worker_started_detected"
                if worker_started_detected
                else ("dry_run" if dry_run else (failure_reason or "run_not_confirmed"))
            ),
            "confirmation": run_confirmation,
            "attempts": run_attempts,
            "prompt_attempts": prompt_attempts,
            "events": auto_run_events,
            "autorun_mode": normalized_autorun_mode,
            "autorun_summary": autorun_summary,
            "failure_reason": failure_reason,
        },
        "autorun_mode": normalized_autorun_mode,
        "autorun_summary": autorun_summary,
        "failure_reason": failure_reason,
        "reason": reason,
        "manual_action_required": manual_action_required,
        "wait_after_open_seconds": wait_after_open_seconds,
        "wait_for_run_start_seconds": wait_for_run_start_seconds,
        "debug_screenshots": bool(screenshots),
        "debug_dir": str(debug_dir) if screenshots else "",
        "screenshots": screenshots,
        "worker_cell_preview": "",
        "launch_args": args,
        "warnings": warnings,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check-deps", action="store_true", help="Print launcher dependency status and exit.")
    parser.add_argument("--audit", action="store_true", help="Write read-only YouTube Colab launch audit and exit.")
    parser.add_argument("--group", choices=["yandex", "chrome", "all"], default="")
    parser.add_argument("--email", default="", help="Run only this worker email from the selected group.")
    parser.add_argument("--limit", type=int, default=0, help="Limit selected workers after group/email filtering.")
    parser.add_argument("--browser", choices=["yandex", "chrome"], default="")
    parser.add_argument(
        "--mode",
        choices=[
            "browser-default-tabs",
            "new-colab-inject-tabs",
            "browser-operator",
            "existing-profiles-sequential-operator",
            "existing-profiles-cdp-operator",
            "prepared-notebook-url",
            "notebook-url",
        ],
        default="prepared-notebook-url",
    )
    parser.add_argument("--story-id", default=DEFAULT_STORY_ID)
    parser.add_argument("--auto-run", action="store_true")
    parser.add_argument(
        "--autorun-mode",
        choices=["browser-tab", "legacy", "manual"],
        default="legacy",
        help="browser-tab: safe Playwright/CDP tab autorun (no pyautogui); legacy: CDP then pyautogui fallback; manual: open only.",
    )
    parser.add_argument("--debug-screenshots", action="store_true", help="Save screenshots for each launch/injection stage.")
    parser.add_argument("--sequential", action="store_true", help="Process workers sequentially. Auto-run uses sequential mode by default.")
    parser.add_argument("--stagger-seconds", type=int, default=30, help="Delay between workers when running sequential group launches.")
    parser.add_argument("--wait-after-open-seconds", type=int, default=180, help="Prepared notebooks: wait after opening notebook before Run all.")
    parser.add_argument("--wait-before-next-worker-seconds", type=int, default=300, help="Prepared notebooks: wait before opening next worker.")
    parser.add_argument("--wait-for-run-start-seconds", type=int, default=180, help="Prepared notebooks: wait after Run all before checking output.")
    parser.add_argument(
        "--reuse-profile-window",
        action="store_true",
        help="Prepared notebooks: open URL in existing browser profile without forcing --new-window (reduces duplicate tabs).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.check_deps:
        print_dependency_status(dependency_status())
        return 0
    if args.audit:
        report = build_colab_launch_audit(config_path=args.config, story_id=args.story_id)
        print(f"status={report.get('status')}")
        print(f"ok={report.get('ok')}")
        print(f"config_path={report.get('config_path')}")
        print(f"workers_total={report.get('workers_total')}")
        print(f"workers_by_group={json.dumps(report.get('workers_by_group') or {}, ensure_ascii=True)}")
        print(f"queue_render_workers_count={report.get('queue_render_workers_count')}")
        print(f"workers_missing_from_queue_render_config={json.dumps(report.get('workers_missing_from_queue_render_config') or [], ensure_ascii=True)}")
        print(f"launch_modes={json.dumps(report.get('launch_modes') or [], ensure_ascii=True)}")
        print(f"prepared_notebook_urls_csv={report.get('prepared_notebook_urls_csv')}")
        print(f"drive_root_exists={report.get('drive_root_exists')}")
        print(f"drive_worker_script_exists={report.get('drive_worker_script_exists')}")
        print(f"drive_bootstrap_script_exists={report.get('drive_bootstrap_script_exists')}")
        print(f"drive_job_exists={report.get('drive_job_exists')}")
        print(f"job_ready_marker_exists={report.get('job_ready_marker_exists')}")
        print(f"single_worker_smoke_command={report.get('single_worker_smoke_command')}")
        print(f"group_worker_command_yandex={report.get('group_worker_command_yandex')}")
        print(f"group_worker_command_chrome={report.get('group_worker_command_chrome')}")
        print(f"status_command={report.get('status_command')}")
        print(f"report_path={report.get('report_path')}")
        return 0
    if not args.group:
        parser.error("--group is required unless --check-deps or --audit is used")

    mode_override = args.mode.replace("-", "_") if args.mode else ""
    workers = load_workers(args.config, args.group, args.browser, mode_override=mode_override)
    if args.email.strip():
        requested_email = args.email.strip().lower()
        selected = [worker for worker in workers if worker.email.lower() == requested_email]
        if not selected:
            available = ", ".join(worker.email for worker in workers)
            parser.error(f"--email {args.email!r} was not found in group {args.group!r}. Available: {available}")
        workers = selected
    if int(args.limit or 0) > 0:
        workers = workers[: int(args.limit)]
    operator_required = any(worker.launch_mode == "existing_profiles_sequential_operator" for worker in workers)
    cdp_operator_required = any(worker.launch_mode == "existing_profiles_cdp_operator" for worker in workers)
    prepared_notebook_requested = any(worker.launch_mode == "prepared_notebook_url" for worker in workers)
    browser_tab_autorun_requested = bool(args.auto_run) and str(getattr(args, "autorun_mode", "legacy")) == "browser-tab"
    injection_requested = any(worker.launch_mode in {"browser_default_tabs", "new_colab_inject_tabs", "browser_operator", "existing_profiles_sequential_operator", "existing_profiles_cdp_operator"} for worker in workers)
    if not args.dry_run and (injection_requested or browser_tab_autorun_requested):
        dep_status = dependency_status()
        if browser_tab_autorun_requested:
            reason = "playwright_unavailable_preflight" if not dep_status.get("playwright_installed") else ""
        else:
            reason = dependency_failure_reason(
                dep_status,
                debug_screenshots=bool(args.debug_screenshots),
                operator_required=operator_required,
                cdp_operator_required=cdp_operator_required,
            )
        if reason:
            report, local_report, drive_report = write_preflight_failure_report(
                story_id=args.story_id,
                group=args.group,
                mode=args.mode,
                email_filter=args.email.strip(),
                debug_screenshots=bool(args.debug_screenshots),
                status=dep_status,
                reason=reason,
            )
            print(f"status={report.get('status')}")
            print("ok=False")
            print(f"reason={reason}")
            if reason == "pyautogui_unavailable_preflight":
                print("pyautogui is not installed. Run INSTALL_COLAB_LAUNCHER_DEPS.bat")
            elif reason == "pyperclip_unavailable_preflight":
                print("pyperclip is not installed. Run INSTALL_COLAB_LAUNCHER_DEPS.bat")
            elif reason == "pillow_unavailable_preflight":
                print("pillow is not installed. Run INSTALL_COLAB_LAUNCHER_DEPS.bat")
            elif reason == "pygetwindow_unavailable_preflight":
                print("pygetwindow is not installed. Run INSTALL_COLAB_LAUNCHER_DEPS.bat")
            elif reason == "playwright_unavailable_preflight":
                print("playwright is not installed. Run INSTALL_COLAB_LAUNCHER_DEPS.bat")
            print(f"dependency_status={json.dumps(dep_status, ensure_ascii=True)}")
            print(f"report_path={local_report}")
            print(f"drive_report_path={drive_report}")
            return 2
    browser_exes = {browser: first_existing(browser_candidates(browser)) for browser in {"chrome", "yandex"}}
    results = []
    sequential = bool(args.sequential or args.auto_run or operator_required or cdp_operator_required or prepared_notebook_requested)
    stagger_seconds = max(0, int(args.stagger_seconds or 0))
    for index, worker in enumerate(workers, start=1):
        browser_exe = browser_exes.get(worker.browser)
        if worker.launch_mode == "prepared_notebook_url":
            results.append(
                launch_prepared_notebook_url(
                    worker,
                    browser_exe,
                    story_id=args.story_id,
                    dry_run=bool(args.dry_run),
                    auto_run=bool(args.auto_run),
                    wait_after_open_seconds=max(0, int(args.wait_after_open_seconds or 0)),
                    wait_for_run_start_seconds=max(0, int(args.wait_for_run_start_seconds or 0)),
                    reuse_profile_window=bool(getattr(args, "reuse_profile_window", False)),
                    autorun_mode=str(getattr(args, "autorun_mode", "legacy")),
                )
            )
            if sequential and not args.dry_run and index < len(workers):
                time.sleep(max(0, int(args.wait_before_next_worker_seconds or 0)))
            continue
        if worker.launch_mode in {"existing_profiles_sequential_operator", "existing_profiles_cdp_operator"}:
            result = launch_existing_profile_operator(worker, story_id=args.story_id, dry_run=bool(args.dry_run))
            results.append(result)
            if not args.dry_run and result.get("manual_action_required"):
                for skipped_worker in workers[index:]:
                    results.append(
                        {
                            "email": skipped_worker.email,
                            "browser": skipped_worker.browser,
                            "group": skipped_worker.group,
                            "profile_strategy": skipped_worker.profile_strategy,
                            "profile_dir": skipped_worker.profile_dir,
                            "profile_dir_exists": Path(skipped_worker.profile_dir).is_dir() if skipped_worker.profile_dir else False,
                            "use_user_data_dir": True,
                            "launch_mode": skipped_worker.launch_mode,
                            "opened_url": "",
                            "opened": False,
                            "notebook_url": "",
                            "require_t4": skipped_worker.require_t4,
                            "code_injected": False,
                            "auto_run_attempted": False,
                            "auto_run_result": {"attempted": False, "reason": "skipped_after_previous_worker_failed"},
                            "reason": "skipped_after_previous_worker_failed",
                            "manual_action_required": True,
                            "debug_screenshots": True,
                            "debug_dir": str(
                                PROJECT_ROOT
                                / "output"
                                / "youtube"
                                / args.story_id
                                / "08_video"
                                / "reports"
                                / ("operator_cdp_debug" if skipped_worker.launch_mode == "existing_profiles_cdp_operator" else "operator_debug")
                                / safe_email_name(skipped_worker.email)
                            ),
                            "warnings": ["not_opened_because_previous_worker_did_not_start"],
                            "errors": [],
                        }
                    )
                break
            if sequential and not args.dry_run and index < len(workers):
                time.sleep(stagger_seconds)
            continue
        if worker.launch_mode in {"browser_default_tabs", "new_colab_inject_tabs", "browser_operator"}:
            results.append(
                launch_worker_tab(
                    worker,
                    browser_exe,
                    story_id=args.story_id,
                    dry_run=bool(args.dry_run),
                    auto_run=bool(args.auto_run),
                    debug_screenshots=bool(args.debug_screenshots),
                )
            )
            if sequential and not args.dry_run and index < len(workers):
                time.sleep(stagger_seconds)
            continue
        if worker.launch_mode == "notebook_url":
            if not worker.notebook_url:
                results.append(
                    {
                        "email": worker.email,
                        "browser": worker.browser,
                        "group": worker.group,
                        "profile_strategy": worker.profile_strategy,
                        "profile_dir": worker.profile_dir,
                        "use_user_data_dir": worker.use_user_data_dir,
                        "launch_mode": worker.launch_mode,
                        "opened_url": "",
                        "opened": False,
                        "code_injected": False,
                        "auto_run_attempted": False,
                        "auto_run_result": {"attempted": False, "reason": "missing_notebook_url"},
                        "reason": "missing_notebook_url",
                        "manual_action_required": True,
                        "warnings": ["notebook_url_missing"],
                        "errors": ["notebook_url_missing"],
                    }
                )
                continue
            results.append(
                launch_worker_tab(
                    worker,
                    browser_exe,
                    story_id=args.story_id,
                    dry_run=bool(args.dry_run),
                    auto_run=bool(args.auto_run),
                    debug_screenshots=bool(args.debug_screenshots),
                )
            )
            if sequential and not args.dry_run and index < len(workers):
                time.sleep(stagger_seconds)
            continue
        raise ValueError(f"unsupported launch_mode: {worker.launch_mode}")

    opened_count = sum(1 for item in results if item.get("opened"))
    code_injected_count = sum(1 for item in results if item.get("code_injected"))
    manual_action_required_count = sum(1 for item in results if item.get("manual_action_required"))
    has_errors = any(item.get("errors") for item in results)
    run_ok = not has_errors and code_injected_count == len(results) and manual_action_required_count == 0
    report = {
        "ok": True if args.dry_run else run_ok,
        "status": "dry_run" if args.dry_run else ("opened" if run_ok else "manual_action_required"),
        "group": args.group,
        "mode": args.mode,
        "profile_strategy": "existing_logged_in_profiles" if (operator_required or cdp_operator_required or prepared_notebook_requested) else "browser_default_tabs",
        "uses_user_data_dir": any(bool(item.get("use_user_data_dir")) for item in results),
        "config_path": str(args.config.resolve()),
        "story_id": args.story_id,
        "dry_run": bool(args.dry_run),
        "auto_run_requested": bool(args.auto_run),
        "email_filter": args.email.strip(),
        "limit": int(args.limit or 0),
        "debug_screenshots": bool(args.debug_screenshots),
        "sequential": sequential,
        "stagger_seconds": stagger_seconds,
        "wait_after_open_seconds": max(0, int(args.wait_after_open_seconds or 0)),
        "wait_before_next_worker_seconds": max(0, int(args.wait_before_next_worker_seconds or 0)),
        "wait_for_run_start_seconds": max(0, int(args.wait_for_run_start_seconds or 0)),
        "reuse_profile_window": bool(getattr(args, "reuse_profile_window", False)),
        "browser_executables": {key: str(value) if value else "" for key, value in browser_exes.items()},
        "workers_total": len(results),
        "opened_count": opened_count,
        "code_injected_count": code_injected_count,
        "manual_action_required_count": manual_action_required_count,
        "results": results,
        "warnings": [
            "Production mode opens prepared notebooks that already contain worker code; no UI code injection is required.",
            "Existing logged-in browser profile dirs are reused with --user-data-dir per worker.",
            "Colab may not provide T4/GPU.",
            "If a profile is logged out or Drive OAuth appears, confirm manually; no passwords/2FA are automated.",
            "No passwords, app passwords, recovery codes, cookies, or access tokens are stored in project files.",
        ],
        "written_at": utc_now(),
    }
    local_report, drive_report = write_report(args.story_id, report)

    print(f"status={report['status']}")
    print(f"ok={report['ok']}")
    print(f"group={report['group']}")
    print(f"mode={report['mode']}")
    print(f"email_filter={report['email_filter']}")
    print(f"limit={report['limit']}")
    print(f"uses_user_data_dir={report['uses_user_data_dir']}")
    print(f"debug_screenshots={report['debug_screenshots']}")
    print(f"sequential={report['sequential']}")
    print(f"stagger_seconds={report['stagger_seconds']}")
    print(f"wait_after_open_seconds={report['wait_after_open_seconds']}")
    print(f"wait_before_next_worker_seconds={report['wait_before_next_worker_seconds']}")
    print(f"wait_for_run_start_seconds={report['wait_for_run_start_seconds']}")
    print(f"workers_total={report['workers_total']}")
    print(f"opened_count={report['opened_count']}")
    print(f"code_injected_count={report['code_injected_count']}")
    print(f"manual_action_required_count={report['manual_action_required_count']}")
    print(f"report_path={local_report}")
    print(f"drive_report_path={drive_report}")
    if any(item.get("reason") == "run_not_confirmed" for item in results):
        print("Notebook открыт с кодом, но автозапуск не подтверждён. Нажмите Run у первой ячейки вручную.")
    for item in results:
        print(
            "worker="
            + json.dumps(
                {
                    "email": item.get("email"),
                    "browser": item.get("browser"),
                    "group": item.get("group"),
                    "profile_strategy": item.get("profile_strategy"),
                    "use_user_data_dir": item.get("use_user_data_dir"),
                    "launch_mode": item.get("launch_mode"),
                    "opened_url": item.get("opened_url"),
                    "code_injected": item.get("code_injected"),
                    "reason": item.get("reason"),
                    "manual_action_required": item.get("manual_action_required"),
                    "debug_dir": item.get("debug_dir", ""),
                    "warnings": item.get("warnings") or [],
                    "errors": item.get("errors") or [],
                },
                ensure_ascii=True,
            )
        )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
