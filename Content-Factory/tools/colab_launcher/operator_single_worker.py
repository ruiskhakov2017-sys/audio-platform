from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORY_ID = "Becoming A Slut Wife Alma"
DEFAULT_STORY_SLUG = "Becoming_A_Slut_Wife_Alma"
REPORT_NAME = "operator_single_worker_report.json"
NEW_COLAB_URL_TEMPLATE = "https://colab.research.google.com/?authuser={authuser}#create=true"
EXISTING_SESSION_COLAB_URL = "https://colab.research.google.com/#create=true"
ACCOUNT_CHOOSER_URL_TEMPLATE = "https://accounts.google.com/AccountChooser?Email={email}&continue={continue_url}"


@dataclass
class Dependencies:
    ok: bool
    pyautogui: Any = None
    pyperclip: Any = None
    pygetwindow: Any = None
    errors: dict[str, str] | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_email_name(email: str) -> str:
    return email.replace("@", "_").replace(".", "_")


def report_dir(story_id: str) -> Path:
    return PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports"


def screenshots_dir(story_id: str, email: str) -> Path:
    return report_dir(story_id) / "operator_debug" / safe_email_name(email)


def report_path(story_id: str) -> Path:
    return report_dir(story_id) / REPORT_NAME


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dependency_preflight() -> Dependencies:
    errors: dict[str, str] = {}
    pyautogui = None
    pyperclip = None
    pygetwindow = None
    try:
        import pyautogui as imported_pyautogui  # type: ignore

        pyautogui = imported_pyautogui
    except Exception as exc:
        errors["pyautogui"] = repr(exc)
    try:
        import pyperclip as imported_pyperclip  # type: ignore

        pyperclip = imported_pyperclip
    except Exception as exc:
        errors["pyperclip"] = repr(exc)
    try:
        from PIL import Image  # type: ignore  # noqa: F401
    except Exception as exc:
        errors["pillow"] = repr(exc)
    try:
        import pygetwindow as imported_pygetwindow  # type: ignore

        pygetwindow = imported_pygetwindow
    except Exception as exc:
        errors["pygetwindow"] = repr(exc)
    return Dependencies(ok=not errors, pyautogui=pyautogui, pyperclip=pyperclip, pygetwindow=pygetwindow, errors=errors)


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


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


try:
    from youtube_worker_notebook_mount import colab_safe_drive_mount_block
except ImportError:
    from tools.colab_launcher.youtube_worker_notebook_mount import colab_safe_drive_mount_block

try:
    from colab_worker_gpu_check import colab_worker_gpu_check_block
except ImportError:
    from tools.colab_launcher.colab_worker_gpu_check import colab_worker_gpu_check_block


def build_worker_cell(email: str, require_t4: bool = False) -> str:
    require_t4_value = "1" if require_t4 else "0"
    drive_mount_block = colab_safe_drive_mount_block(with_cf_boot=False)
    return f'''# === ContentFactory YouTube VIDEO Worker ===
# Worker: {email}

{drive_mount_block}
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

{colab_worker_gpu_check_block()}

%run "/content/drive/MyDrive/ContentFactory_YouTube/scripts/youtube_video_bootstrap_colab.py" --story-slug "{DEFAULT_STORY_SLUG}" --worker-email "{email}" --max-jobs-per-run "0" --idle-timeout-min "15" --poll-seconds "10"
'''


class SingleWorkerOperator:
    def __init__(
        self,
        *,
        email: str,
        browser: str,
        story_id: str,
        profile_dir: str,
        require_t4: bool,
        wait_seconds: int,
        run_wait_seconds: int,
    ) -> None:
        self.email = email
        self.browser = browser
        self.story_id = story_id
        self.profile_dir = profile_dir.strip()
        self.use_user_data_dir = bool(self.profile_dir)
        self.require_t4 = require_t4
        self.wait_seconds = wait_seconds
        self.run_wait_seconds = run_wait_seconds
        self.opened_url = NEW_COLAB_URL_TEMPLATE.format(authuser=quote(email, safe=""))
        self.opened_urls: list[str] = []
        self.manual_login_url = ACCOUNT_CHOOSER_URL_TEMPLATE.format(
            email=quote(email, safe=""),
            continue_url=quote(EXISTING_SESSION_COLAB_URL, safe=""),
        )
        self.screenshots_dir = screenshots_dir(story_id, email)
        self.steps: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.screenshot_index = 1
        self.window: Any = None
        self.active_window_title = ""
        self.window_found = False
        self.code_injected = False
        self.inject_attempts_count = 0
        self.successful_attempt = 0
        self.gpu_attempted = False
        self.gpu_result: dict[str, Any] = {}
        self.run_attempted = False
        self.worker_started_detected = False
        self.manual_action_required = False
        self.reason = ""
        self.inject_verifications: list[dict[str, Any]] = []

    def add_step(self, name: str, ok: bool, **fields: Any) -> None:
        self.steps.append({"step": name, "ok": ok, "at": utc_now(), **fields})

    def screenshot(self, pyautogui: Any, name: str) -> str:
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        if name in {"after_gpu_attempt.png", "after_run.png", "final.png"}:
            filename = name
        else:
            filename = f"{self.screenshot_index:02d}_{name}"
            self.screenshot_index += 1
        path = self.screenshots_dir / filename
        try:
            image = pyautogui.screenshot()
            image.save(path)
            return str(path)
        except Exception as exc:
            self.warnings.append(f"screenshot_failed:{filename}:{exc!r}")
            return ""

    def colab_urls(self) -> list[tuple[str, str]]:
        return [
            ("authuser_email", NEW_COLAB_URL_TEMPLATE.format(authuser=quote(self.email, safe=""))),
            ("existing_browser_session", EXISTING_SESSION_COLAB_URL),
        ]

    def browser_launch_args(self, browser_exe: Path, url: str) -> list[str]:
        args = [str(browser_exe)]
        if self.profile_dir:
            args.append(f"--user-data-dir={self.profile_dir}")
        args.extend(["--new-window", url])
        return args

    def open_colab(self, browser_exe: Path, url: str, label: str) -> None:
        self.opened_url = url
        self.opened_urls.append(url)
        launch_args = self.browser_launch_args(browser_exe, url)
        subprocess.Popen(launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.add_step("open_colab", True, url=url, url_label=label, browser_exe=str(browser_exe), launch_args=launch_args)

    def reset_page_state_for_retry(self) -> None:
        self.window = None
        self.active_window_title = ""
        self.window_found = False
        self.manual_action_required = False
        self.reason = ""

    def find_and_focus_window(self, pyautogui: Any, pygetwindow: Any) -> None:
        markers = ("Colab", "Untitled", "Яндекс", "Yandex", "Chrome")
        candidates = []
        deadline = time.time() + max(10, self.wait_seconds)
        while time.time() < deadline:
            candidates = [w for w in pygetwindow.getAllWindows() if any(marker.lower() in (w.title or "").lower() for marker in markers)]
            if candidates:
                break
            time.sleep(1)
        if not candidates:
            self.add_step("find_window", False, reason="window_not_found")
            self.reason = "browser_window_not_found"
            self.manual_action_required = True
            return
        preferred_browser = "Yandex" if self.browser == "yandex" else "Chrome"
        preferred = [w for w in candidates if preferred_browser.lower() in (w.title or "").lower()]
        self.window = preferred[-1] if preferred else candidates[-1]
        self.window_found = True
        self.active_window_title = self.window.title or ""
        try:
            self.window.activate()
            time.sleep(0.5)
            try:
                self.window.maximize()
            except Exception:
                pyautogui.hotkey("win", "up")
            self.add_step("focus_window", True, title=self.active_window_title)
        except Exception as exc:
            self.add_step("focus_window", False, title=self.active_window_title, error=repr(exc))
            self.warnings.append(f"window_focus_failed:{exc!r}")
        self.screenshot(pyautogui, "after_focus.png")

    def is_auth_or_login_window(self) -> bool:
        title = (self.active_window_title or "").lower()
        auth_markers = ("accounts.google.com", "sign in", "вход", "google account")
        return any(marker in title for marker in auth_markers)

    def close_panels(self, pyautogui: Any) -> None:
        for _ in range(3):
            pyautogui.press("esc")
            time.sleep(0.2)
        if self.window:
            x = self.window.left + int(self.window.width * 0.42)
            y = self.window.top + int(self.window.height * 0.48)
            pyautogui.click(x, y)
        self.add_step("close_panels", True, method="esc_x3_click_workspace")
        self.screenshot(pyautogui, "after_close_panels.png")

    def set_and_verify_clipboard(self, pyperclip: Any, code: str) -> bool:
        pyperclip.copy(code)
        pasted = pyperclip.paste() or ""
        ok = "CONTENT_FACTORY_WORKER_EMAIL" in pasted and self.email in pasted
        self.add_step("clipboard_set_verify", ok, contains_marker="CONTENT_FACTORY_WORKER_EMAIL" in pasted, contains_email=self.email in pasted, chars=len(pasted))
        if not ok:
            self.reason = "clipboard_verification_failed"
            self.manual_action_required = True
        return ok

    def click_for_attempt(self, pyautogui: Any, attempt: int) -> None:
        if not self.window:
            return
        left, top, width, height = self.window.left, self.window.top, self.window.width, self.window.height
        points = {
            1: (0.50, 0.46),
            2: (0.34, 0.46),
            3: (0.50, 0.55),
            4: (0.22, 0.48),
            5: (0.50, 0.50),
        }
        if attempt == 5:
            pyautogui.press("tab")
            time.sleep(0.2)
            pyautogui.press("enter")
            time.sleep(0.2)
            return
        point = points.get(attempt, points[1])
        pyautogui.click(left + int(width * point[0]), top + int(height * point[1]))
        time.sleep(0.4)

    def copy_back_verify(self, pyautogui: Any, pyperclip: Any) -> dict[str, Any]:
        sentinel = f"__CONTENT_FACTORY_COPYBACK_SENTINEL_{int(time.time() * 1000)}__"
        pyperclip.copy(sentinel)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.5)
        copied = pyperclip.paste() or ""
        has_marker = "CONTENT_FACTORY_WORKER_EMAIL" in copied
        has_email = self.email in copied
        stale_clipboard = copied == sentinel
        return {
            "ok": bool(has_marker and has_email and not stale_clipboard),
            "has_marker": has_marker,
            "has_email": has_email,
            "stale_clipboard": stale_clipboard,
            "copied_chars": len(copied),
        }

    def inject_code(self, pyautogui: Any, pyperclip: Any, code: str) -> None:
        for local_attempt in range(1, 6):
            global_attempt = self.inject_attempts_count + 1
            self.inject_attempts_count = global_attempt
            if self.window:
                try:
                    self.window.activate()
                    time.sleep(0.3)
                except Exception:
                    pass
            self.screenshot(pyautogui, f"before_paste_attempt_{global_attempt}.png")
            self.click_for_attempt(pyautogui, local_attempt)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyperclip.copy(code)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(2)
            self.screenshot(pyautogui, f"after_paste_attempt_{global_attempt}.png")
            verify = self.copy_back_verify(pyautogui, pyperclip)
            self.inject_verifications.append({"attempt": global_attempt, "local_attempt": local_attempt, **verify})
            self.screenshot(pyautogui, f"after_copyback_attempt_{global_attempt}.png")
            self.add_step("paste_attempt", bool(verify["ok"]), attempt=global_attempt, local_attempt=local_attempt, verification=verify)
            if verify["ok"]:
                self.code_injected = True
                self.successful_attempt = global_attempt
                self.reason = "code_injected"
                return
        all_copybacks_were_stale = bool(self.inject_verifications) and all(item.get("stale_clipboard") for item in self.inject_verifications)
        if all_copybacks_were_stale:
            self.reason = "colab_auth_or_login_requires_manual_confirmation"
            self.warnings.append("Colab page did not expose an editable cell. If the screen says 'Войдите в аккаунт', sign in manually and rerun.")
        else:
            self.reason = "code_not_injected"
        self.manual_action_required = True

    def gpu_best_effort(self, pyautogui: Any) -> None:
        self.gpu_attempted = True
        result = {
            "ok": False,
            "reason": "best_effort_hotkeys_only",
            "warning": "Colab GPU/T4 UI is unstable; worker cell still runs nvidia-smi and require_t4 check.",
        }
        try:
            pyautogui.hotkey("alt", "r")
            time.sleep(0.8)
            self.screenshot(pyautogui, "after_gpu_attempt.png")
            result["ok"] = True
            result["method"] = "alt_r_menu_open_attempt"
        except Exception as exc:
            result["error"] = repr(exc)
        self.gpu_result = result
        self.add_step("gpu_t4_best_effort", bool(result.get("ok")), result=result)

    def drive_mount_best_effort(self, pyautogui: Any) -> None:
        if not self.window:
            self.add_step("drive_mount_best_effort", False, reason="window_not_available")
            return
        try:
            # Colab's Drive mount confirmation usually appears below the running cell.
            # This does not enter credentials or bypass account/2FA prompts.
            x = self.window.left + int(self.window.width * 0.50)
            y = self.window.top + int(self.window.height * 0.66)
            pyautogui.click(x, y)
            time.sleep(0.4)
            pyautogui.press("enter")
            self.add_step("drive_mount_best_effort", True, method="click_output_area_then_enter")
        except Exception as exc:
            self.add_step("drive_mount_best_effort", False, error=repr(exc))
            self.warnings.append(f"drive_mount_best_effort_failed:{exc!r}")

    def run_cell_and_watch(self, pyautogui: Any, pyperclip: Any) -> None:
        self.run_attempted = True
        pyautogui.hotkey("ctrl", "f9")
        time.sleep(1)
        self.screenshot(pyautogui, "after_run.png")
        time.sleep(8)
        self.drive_mount_best_effort(pyautogui)
        deadline = time.time() + max(30, self.run_wait_seconds)
        found = False
        last_verify: dict[str, Any] = {}
        output_markers = (
            "Mounted at /content/drive",
            "Drive already mounted",
            "[BOOT]",
            "youtube video bootstrap resolved root",
            "[CLAIM]",
            "[LOOP]",
            "[HEARTBEAT]",
        )
        while time.time() < deadline:
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.3)
            copied = pyperclip.paste() or ""
            found_markers = [marker for marker in output_markers if marker in copied]
            last_verify = {"copied_chars": len(copied), "found_markers": found_markers}
            if found_markers:
                found = True
                break
            copied_lower = copied.lower()
            if "accounts.google.com" in copied_lower or "authorization" in copied_lower or "oauth" in copied_lower:
                self.manual_action_required = True
                self.reason = "drive_oauth_requires_manual_confirmation"
                break
            time.sleep(5)
        self.worker_started_detected = found
        if found:
            self.reason = "worker_started_detected"
        elif self.reason != "drive_oauth_requires_manual_confirmation":
            self.reason = "worker_start_not_detected"
            self.manual_action_required = True
        self.add_step("run_and_watch", found, verification=last_verify)

    def final_report(self) -> dict[str, Any]:
        return {
            "ok": bool(self.code_injected and self.run_attempted and self.worker_started_detected and not self.manual_action_required),
            "email": self.email,
            "browser": self.browser,
            "profile_dir": self.profile_dir,
            "use_user_data_dir": self.use_user_data_dir,
            "opened_url": self.opened_url,
            "opened_urls": self.opened_urls,
            "manual_login_url": self.manual_login_url,
            "window_found": self.window_found,
            "active_window_title": self.active_window_title,
            "code_injected": self.code_injected,
            "inject_attempts_count": self.inject_attempts_count,
            "successful_attempt": self.successful_attempt,
            "gpu_attempted": self.gpu_attempted,
            "gpu_result": self.gpu_result,
            "run_attempted": self.run_attempted,
            "worker_started_detected": self.worker_started_detected,
            "manual_action_required": self.manual_action_required,
            "reason": self.reason,
            "screenshots_dir": str(self.screenshots_dir),
            "errors": self.errors,
            "warnings": self.warnings,
            "inject_verifications": self.inject_verifications,
            "steps": self.steps,
            "written_at": utc_now(),
        }

    def run(self, deps: Dependencies) -> dict[str, Any]:
        assert deps.pyautogui is not None
        assert deps.pyperclip is not None
        assert deps.pygetwindow is not None
        pyautogui = deps.pyautogui
        pyperclip = deps.pyperclip
        pygetwindow = deps.pygetwindow

        browser_exe = first_existing(browser_candidates(self.browser))
        if browser_exe is None:
            self.errors.append(f"{self.browser}_executable_not_found")
            self.reason = "browser_executable_not_found"
            self.manual_action_required = True
            return self.final_report()
        if self.profile_dir:
            profile_path = Path(self.profile_dir)
            if not profile_path.is_dir():
                self.errors.append(f"profile_dir_missing: {profile_path}")
                self.reason = "profile_dir_missing"
                self.manual_action_required = True
                return self.final_report()

        code = build_worker_cell(self.email, self.require_t4)
        for url_label, url in self.colab_urls():
            self.reset_page_state_for_retry()
            self.open_colab(browser_exe, url, url_label)
            time.sleep(max(20, self.wait_seconds))
            self.screenshot(pyautogui, "after_open.png")
            self.find_and_focus_window(pyautogui, pygetwindow)
            if not self.window_found:
                self.screenshot(pyautogui, "final.png")
                return self.final_report()
            if self.is_auth_or_login_window():
                self.reason = "colab_auth_or_login_requires_manual_confirmation"
                self.manual_action_required = True
                self.add_step("auth_or_login_detected", False, title=self.active_window_title, url_label=url_label)
                self.screenshot(pyautogui, "final.png")
                if url_label == "authuser_email":
                    self.warnings.append("authuser=email opened a Google login page; retrying Colab with the existing browser session.")
                    continue
                return self.final_report()
            self.close_panels(pyautogui)
            if not self.set_and_verify_clipboard(pyperclip, code):
                self.screenshot(pyautogui, "final.png")
                return self.final_report()
            self.inject_code(pyautogui, pyperclip, code)
            if self.code_injected:
                break
            self.screenshot(pyautogui, "final.png")
            if self.reason == "colab_auth_or_login_requires_manual_confirmation" and url_label == "authuser_email":
                self.warnings.append("Colab auth popup/overlay blocked editing; retrying without authuser in the current browser session.")
                continue
            return self.final_report()
        if not self.code_injected:
            self.reason = self.reason or "code_not_injected"
            self.manual_action_required = True
            return self.final_report()
        self.gpu_best_effort(pyautogui)
        self.run_cell_and_watch(pyautogui, pyperclip)
        self.screenshot(pyautogui, "final.png")
        return self.final_report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--browser", choices=["yandex", "chrome"], required=True)
    parser.add_argument("--story-id", default=DEFAULT_STORY_ID)
    parser.add_argument("--profile-dir", default="")
    parser.add_argument("--require-t4", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=30)
    parser.add_argument("--run-wait-seconds", type=int, default=90)
    args = parser.parse_args(argv)

    operator = SingleWorkerOperator(
        email=args.email.strip(),
        browser=args.browser.strip(),
        story_id=args.story_id,
        profile_dir=args.profile_dir.strip(),
        require_t4=bool(args.require_t4),
        wait_seconds=int(args.wait_seconds),
        run_wait_seconds=int(args.run_wait_seconds),
    )

    deps = dependency_preflight()
    if not deps.ok:
        report = {
            "ok": False,
            "email": args.email,
            "browser": args.browser,
            "profile_dir": args.profile_dir.strip(),
            "use_user_data_dir": bool(args.profile_dir.strip()),
            "opened_url": operator.opened_url,
            "opened_urls": [],
            "manual_login_url": operator.manual_login_url,
            "window_found": False,
            "active_window_title": "",
            "code_injected": False,
            "inject_attempts_count": 0,
            "successful_attempt": 0,
            "gpu_attempted": False,
            "gpu_result": {},
            "run_attempted": False,
            "worker_started_detected": False,
            "manual_action_required": True,
            "reason": "dependency_preflight_failed",
            "screenshots_dir": str(operator.screenshots_dir),
            "errors": deps.errors or {},
            "warnings": ["Install dependencies: python -m pip install pyautogui pyperclip pillow pygetwindow"],
            "written_at": utc_now(),
        }
        write_json(report_path(args.story_id), report)
        print("ok=False")
        print("reason=dependency_preflight_failed")
        print(f"errors={json.dumps(deps.errors or {}, ensure_ascii=True)}")
        print("Run: python -m pip install pyautogui pyperclip pillow pygetwindow")
        print(f"report_path={report_path(args.story_id)}")
        return 2

    report = operator.run(deps)
    write_json(report_path(args.story_id), report)
    print(f"ok={report.get('ok')}")
    print(f"profile_dir={report.get('profile_dir')}")
    print(f"use_user_data_dir={report.get('use_user_data_dir')}")
    print(f"opened_url={report.get('opened_url')}")
    print(f"opened_urls={json.dumps(report.get('opened_urls') or [], ensure_ascii=True)}")
    print(f"manual_login_url={report.get('manual_login_url')}")
    print(f"window_found={report.get('window_found')}")
    print(f"active_window_title={report.get('active_window_title')}")
    print(f"code_injected={report.get('code_injected')}")
    print(f"inject_attempts_count={report.get('inject_attempts_count')}")
    print(f"successful_attempt={report.get('successful_attempt')}")
    print(f"gpu_attempted={report.get('gpu_attempted')}")
    print(f"gpu_result={json.dumps(report.get('gpu_result') or {}, ensure_ascii=True)}")
    print(f"run_attempted={report.get('run_attempted')}")
    print(f"worker_started_detected={report.get('worker_started_detected')}")
    print(f"manual_action_required={report.get('manual_action_required')}")
    print(f"reason={report.get('reason')}")
    print(f"screenshots_dir={report.get('screenshots_dir')}")
    print(f"report_path={report_path(args.story_id)}")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
