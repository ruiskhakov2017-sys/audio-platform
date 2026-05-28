from __future__ import annotations

import argparse
import json
import os
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


def save_debug_screenshot(debug_dir: Path | None, filename: str) -> str:
    if debug_dir is None:
        return ""
    try:
        import pyautogui  # type: ignore
    except Exception:
        return ""
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


def launch_prepared_notebook_url(
    worker: WorkerConfig,
    browser_exe: Path | None,
    *,
    story_id: str,
    dry_run: bool,
    auto_run: bool,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    args: list[str] = []
    opened = False
    run_attempted = False
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
            "--new-window",
            worker.notebook_url,
        ]

    if not dry_run and not errors:
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
            if auto_run:
                try:
                    import pyautogui  # type: ignore

                    time.sleep(20)
                    pyautogui.hotkey("ctrl", "f9")
                    run_attempted = True
                except Exception as exc:
                    warnings.append(f"prepared_notebook_autorun_failed: {exc!r}")
        except OSError as exc:
            errors.append(f"browser_launch_failed: {exc}")

    manual_action_required = bool(errors) or (not dry_run and (not opened or (auto_run and not run_attempted)))
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
        "auto_run_attempted": run_attempted,
        "auto_run_result": {
            "attempted": bool(auto_run),
            "ok": bool(run_attempted) if auto_run else True,
            "reason": "prepared_notebook_opened_ctrl_f9" if run_attempted else ("dry_run" if dry_run else "manual_run_required"),
        },
        "reason": "dry_run" if dry_run else ("prepared_notebook_opened" if opened else "prepared_notebook_not_opened"),
        "manual_action_required": manual_action_required,
        "debug_screenshots": False,
        "debug_dir": "",
        "worker_cell_preview": "",
        "launch_args": args,
        "warnings": warnings,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check-deps", action="store_true", help="Print launcher dependency status and exit.")
    parser.add_argument("--group", choices=["yandex", "chrome", "all"], default="")
    parser.add_argument("--email", default="", help="Run only this worker email from the selected group.")
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
    parser.add_argument("--debug-screenshots", action="store_true", help="Save screenshots for each launch/injection stage.")
    parser.add_argument("--sequential", action="store_true", help="Process workers sequentially. Auto-run uses sequential mode by default.")
    parser.add_argument("--stagger-seconds", type=int, default=30, help="Delay between workers when running sequential group launches.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.check_deps:
        print_dependency_status(dependency_status())
        return 0
    if not args.group:
        parser.error("--group is required unless --check-deps is used")

    mode_override = args.mode.replace("-", "_") if args.mode else ""
    workers = load_workers(args.config, args.group, args.browser, mode_override=mode_override)
    if args.email.strip():
        requested_email = args.email.strip().lower()
        selected = [worker for worker in workers if worker.email.lower() == requested_email]
        if not selected:
            available = ", ".join(worker.email for worker in workers)
            parser.error(f"--email {args.email!r} was not found in group {args.group!r}. Available: {available}")
        workers = selected
    operator_required = any(worker.launch_mode == "existing_profiles_sequential_operator" for worker in workers)
    cdp_operator_required = any(worker.launch_mode == "existing_profiles_cdp_operator" for worker in workers)
    prepared_notebook_requested = any(worker.launch_mode == "prepared_notebook_url" for worker in workers)
    injection_requested = any(worker.launch_mode in {"browser_default_tabs", "new_colab_inject_tabs", "browser_operator", "existing_profiles_sequential_operator", "existing_profiles_cdp_operator"} for worker in workers)
    if not args.dry_run and injection_requested:
        dep_status = dependency_status()
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
                )
            )
            if sequential and not args.dry_run and index < len(workers):
                time.sleep(stagger_seconds)
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
        "debug_screenshots": bool(args.debug_screenshots),
        "sequential": sequential,
        "stagger_seconds": stagger_seconds,
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
    print(f"uses_user_data_dir={report['uses_user_data_dir']}")
    print(f"debug_screenshots={report['debug_screenshots']}")
    print(f"sequential={report['sequential']}")
    print(f"stagger_seconds={report['stagger_seconds']}")
    print(f"workers_total={report['workers_total']}")
    print(f"opened_count={report['opened_count']}")
    print(f"code_injected_count={report['code_injected_count']}")
    print(f"manual_action_required_count={report['manual_action_required_count']}")
    print(f"report_path={local_report}")
    print(f"drive_report_path={drive_report}")
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
