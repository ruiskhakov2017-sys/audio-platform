from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import pyperclip  # type: ignore
except Exception:  # pragma: no cover - reported by dependency_preflight
    pyperclip = None

try:
    from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright  # type: ignore
except Exception:  # pragma: no cover - reported by dependency_preflight
    Browser = Any  # type: ignore
    Page = Any  # type: ignore
    PlaywrightTimeoutError = TimeoutError  # type: ignore
    sync_playwright = None

from operator_single_worker import build_worker_cell, browser_candidates, first_existing, safe_email_name


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORY_ID = "Becoming A Slut Wife Alma"
REPORT_NAME = "operator_cdp_single_worker_report.json"
COLAB_URL_TEMPLATE = "https://colab.research.google.com/?authuser={authuser}#create=true"
AUTH_TEXT_MARKERS = (
    "Войдите в аккаунт",
    "Sign in",
    "invalid authentication credentials",
    "GapiError",
    "accounts.google.com/signin",
    "oauth/consent",
)
OUTPUT_MARKERS = (
    "WORKER_EMAIL",
    "ContentFactory_YouTube",
    "nvidia-smi",
    "[BOOT]",
    "worker",
    "queue",
)
EDITOR_SELECTORS = [
    "textarea",
    '[contenteditable="true"]',
    ".cm-content",
    ".CodeMirror-code",
    '[role="textbox"]',
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir(story_id: str) -> Path:
    return PROJECT_ROOT / "output" / "youtube" / story_id / "08_video" / "reports"


def screenshots_dir(story_id: str, email: str) -> Path:
    return report_dir(story_id) / "operator_cdp_debug" / safe_email_name(email)


def report_path(story_id: str) -> Path:
    return report_dir(story_id) / REPORT_NAME


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dependency_preflight() -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": True,
        "pyperclip_installed": pyperclip is not None,
        "playwright_installed": sync_playwright is not None,
        "errors": {},
    }
    if pyperclip is None:
        status["ok"] = False
        status["errors"]["pyperclip"] = "pyperclip import failed"
    if sync_playwright is None:
        status["ok"] = False
        status["errors"]["playwright"] = "playwright import failed"
    return status


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def wait_for_cdp_port(port: int, timeout_seconds: int = 25) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def launch_browser(browser_exe: Path, profile_dir: str, port: int, url: str, *, chrome_compatible_retry: bool = False) -> subprocess.Popen[Any]:
    args = [
        str(browser_exe),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--new-window",
    ]
    if chrome_compatible_retry:
        args.extend(["--no-first-run", "--no-default-browser-check", "--disable-popup-blocking"])
    args.append(url)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def newest_colab_page(browser: Browser) -> Page | None:
    pages: list[Page] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    colab_pages = [page for page in pages if "colab.research.google.com" in page.url.lower()]
    if colab_pages:
        return colab_pages[-1]
    return pages[-1] if pages else None


def screenshot(page: Page | None, debug_dir: Path, name: str, warnings: list[str]) -> str:
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / name
    if page is None:
        return ""
    try:
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception as exc:
        warnings.append(f"screenshot_failed:{name}:{exc!r}")
        return ""


def page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def detect_auth_failure(page: Page) -> dict[str, Any]:
    url = page.url or ""
    text = page_text(page)
    haystack = f"{url}\n{text}"
    markers = [marker for marker in AUTH_TEXT_MARKERS if marker.lower() in haystack.lower()]
    return {
        "ok": not markers,
        "status": "ok" if not markers else "auth_failed",
        "markers": markers,
        "url": url,
    }


def focus_selector(page: Page, selector: str) -> bool:
    locator = page.locator(selector).first
    try:
        locator.wait_for(state="visible", timeout=5000)
        locator.click(timeout=5000)
        return True
    except Exception:
        return False


def copyback_verify(page: Page, email: str, sentinel: str) -> dict[str, Any]:
    assert pyperclip is not None
    pyperclip.copy(sentinel)
    page.keyboard.press("Control+A")
    time.sleep(0.2)
    page.keyboard.press("Control+C")
    time.sleep(0.5)
    copied = pyperclip.paste() or ""
    has_marker = "CONTENT_FACTORY_WORKER_EMAIL" in copied
    has_email = email in copied
    stale_clipboard = copied == sentinel
    return {
        "ok": bool(has_marker and has_email and not stale_clipboard),
        "has_marker": has_marker,
        "has_email": has_email,
        "stale_clipboard": stale_clipboard,
        "copied_chars": len(copied),
    }


def insert_code(page: Page, worker_code: str, email: str, debug_dir: Path, warnings: list[str]) -> tuple[bool, str, list[dict[str, Any]]]:
    assert pyperclip is not None
    attempts: list[dict[str, Any]] = []
    strategies: list[dict[str, Any]] = [{"type": "selector", "selector": selector} for selector in EDITOR_SELECTORS]
    strategies.append({"type": "mouse_fallback", "selector": "page.mouse.center_cell"})

    for index, strategy in enumerate(strategies[:5], start=1):
        selector_used = str(strategy["selector"])
        focused = False
        try:
            if strategy["type"] == "selector":
                focused = focus_selector(page, selector_used)
            else:
                viewport = page.viewport_size or {"width": 1280, "height": 800}
                page.mouse.click(int(viewport["width"] * 0.50), int(viewport["height"] * 0.48))
                focused = True
            if not focused:
                attempts.append({"attempt": index, "selector": selector_used, "focused": False, "ok": False, "reason": "selector_not_focused"})
                continue

            pyperclip.copy(worker_code)
            page.keyboard.press("Control+A")
            time.sleep(0.2)
            page.keyboard.press("Control+V")
            time.sleep(1.5)
            screenshot(page, debug_dir, f"{3 + (index * 2) - 1:02d}_after_inject_attempt_{index}.png", warnings)
            verify = copyback_verify(page, email, f"__CONTENT_FACTORY_CDP_SENTINEL_{int(time.time() * 1000)}__")
            screenshot(page, debug_dir, f"{3 + (index * 2):02d}_after_copyback_attempt_{index}.png", warnings)
            attempt = {"attempt": index, "selector": selector_used, "focused": focused, **verify}
            attempts.append(attempt)
            if verify["ok"]:
                return True, selector_used, attempts
        except Exception as exc:
            attempts.append({"attempt": index, "selector": selector_used, "focused": focused, "ok": False, "error": repr(exc)})
    pyperclip.copy(worker_code)
    return False, "", attempts


def detect_worker_output(page: Page, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_text = ""
    while time.time() < deadline:
        last_text = page_text(page)
        found = [marker for marker in OUTPUT_MARKERS if marker.lower() in last_text.lower()]
        if found:
            return {"ok": True, "markers": found, "text_chars": len(last_text)}
        time.sleep(5)
    return {"ok": False, "markers": [], "text_chars": len(last_text)}


def build_failure_report(args: argparse.Namespace, reason: str, errors: list[str], warnings: list[str], port: int = 0) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "failed",
        "email": args.email,
        "browser": args.browser,
        "profile_dir": args.profile_dir,
        "remote_debugging_port": port,
        "opened_url": COLAB_URL_TEMPLATE.format(authuser=quote(args.email, safe="")),
        "cdp_connected": False,
        "page_found": False,
        "auth_status": "unknown",
        "editor_selector_used": "",
        "injection_attempts": [],
        "code_injected": False,
        "run_attempted": False,
        "worker_started_detected": False,
        "manual_action_required": True,
        "reason": reason,
        "screenshots_dir": str(screenshots_dir(args.story_id, args.email)),
        "errors": errors,
        "warnings": warnings,
        "written_at": utc_now(),
    }


def operate_loaded_page(page: Page, args: argparse.Namespace, debug_dir: Path, warnings: list[str]) -> dict[str, Any]:
    auth_status = "unknown"
    editor_selector_used = ""
    injection_attempts: list[dict[str, Any]] = []
    code_injected = False
    run_attempted = False
    worker_started_detected = False
    reason = ""

    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        warnings.append("domcontentloaded_timeout")
    time.sleep(max(2, int(args.settle_seconds)))
    screenshot(page, debug_dir, "01_after_open.png", warnings)
    screenshot(page, debug_dir, "02_after_cdp_connect.png", warnings)

    auth = detect_auth_failure(page)
    auth_status = str(auth["status"])
    if not auth["ok"]:
        reason = "profile_auth_invalid"
        screenshot(page, debug_dir, "final.png", warnings)
    else:
        worker_code = build_worker_cell(args.email, require_t4=bool(args.require_t4))
        screenshot(page, debug_dir, "03_before_inject.png", warnings)
        code_injected, editor_selector_used, injection_attempts = insert_code(page, worker_code, args.email, debug_dir, warnings)
        if not code_injected:
            reason = "cdp_code_not_injected"
            assert pyperclip is not None
            pyperclip.copy(worker_code)
            screenshot(page, debug_dir, "final.png", warnings)
        else:
            page.keyboard.press("Control+F9")
            run_attempted = True
            time.sleep(1)
            screenshot(page, debug_dir, "after_run.png", warnings)
            output = detect_worker_output(page, timeout_seconds=int(args.run_wait_seconds))
            worker_started_detected = bool(output["ok"])
            if worker_started_detected:
                reason = "worker_started_detected"
            else:
                reason = "worker_started_not_confirmed"
                warnings.append("worker_started_not_confirmed")
            screenshot(page, debug_dir, "final.png", warnings)

    return {
        "auth_status": auth_status,
        "editor_selector_used": editor_selector_used,
        "injection_attempts": injection_attempts,
        "code_injected": code_injected,
        "run_attempted": run_attempted,
        "worker_started_detected": worker_started_detected,
        "reason": reason,
    }


def run_operator(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    debug_dir = screenshots_dir(args.story_id, args.email)
    opened_url = COLAB_URL_TEMPLATE.format(authuser=quote(args.email, safe=""))
    port = int(args.remote_debugging_port or find_free_port())

    deps = dependency_preflight()
    if not deps["ok"]:
        return build_failure_report(args, "dependency_preflight_failed", [json.dumps(deps["errors"], ensure_ascii=True)], warnings, port)

    browser_exe = first_existing(browser_candidates(args.browser))
    if browser_exe is None:
        return build_failure_report(args, "browser_executable_not_found", [f"{args.browser}_executable_not_found"], warnings, port)
    profile_path = Path(args.profile_dir)
    if not profile_path.is_dir():
        return build_failure_report(args, "profile_dir_missing", [f"profile_dir_missing: {profile_path}"], warnings, port)

    browser: Browser | None = None
    page: Page | None = None
    cdp_connected = False
    page_found = False
    auth_status = "unknown"
    editor_selector_used = ""
    injection_attempts: list[dict[str, Any]] = []
    code_injected = False
    run_attempted = False
    worker_started_detected = False
    reason = ""
    fallback_used = ""

    launch_browser(browser_exe, args.profile_dir, port, opened_url)
    if not wait_for_cdp_port(port, timeout_seconds=25):
        warnings.append("first_cdp_port_wait_failed; retrying with chrome-compatible flags")
        port = find_free_port()
        launch_browser(browser_exe, args.profile_dir, port, opened_url, chrome_compatible_retry=True)
        if not wait_for_cdp_port(port, timeout_seconds=25):
            if args.browser == "yandex":
                chrome_exe = first_existing(browser_candidates("chrome"))
                if chrome_exe is None:
                    warnings.append("chrome_compatible_cdp_fallback_unavailable: chrome_executable_not_found")
                else:
                    chrome_port = find_free_port()
                    warnings.append("yandex_cdp_port_not_open; trying chrome executable with the same existing profile_dir")
                    launch_browser(chrome_exe, args.profile_dir, chrome_port, opened_url, chrome_compatible_retry=True)
                    if wait_for_cdp_port(chrome_port, timeout_seconds=25):
                        with sync_playwright() as playwright:
                            try:
                                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{chrome_port}")
                                cdp_connected = True
                                fallback_used = "chrome_executable_cdp_same_profile"
                                deadline = time.time() + 25
                                while time.time() < deadline:
                                    page = newest_colab_page(browser)
                                    if page and "colab.research.google.com" in (page.url or "").lower():
                                        page_found = True
                                        break
                                    time.sleep(1)
                                if page_found and page:
                                    details = operate_loaded_page(page, args, debug_dir, warnings)
                                    auth_status = details["auth_status"]
                                    editor_selector_used = details["editor_selector_used"]
                                    injection_attempts = details["injection_attempts"]
                                    code_injected = bool(details["code_injected"])
                                    run_attempted = bool(details["run_attempted"])
                                    worker_started_detected = bool(details["worker_started_detected"])
                                    reason = str(details["reason"])
                                else:
                                    reason = "cdp_page_not_found"
                                    errors.append("chrome_compatible_cdp_page_not_found")
                            except Exception as exc:
                                reason = "chrome_compatible_cdp_failed"
                                errors.append(repr(exc))
                            finally:
                                try:
                                    if browser:
                                        browser.close()
                                except Exception:
                                    pass
                        manual_action_required = auth_status == "auth_failed" or not code_injected
                        return {
                            "ok": bool(code_injected and run_attempted and worker_started_detected and not manual_action_required),
                            "status": "auth_failed" if auth_status == "auth_failed" else ("ok" if code_injected else "failed"),
                            "email": args.email,
                            "browser": args.browser,
                            "profile_dir": args.profile_dir,
                            "remote_debugging_port": chrome_port,
                            "opened_url": opened_url,
                            "cdp_connected": cdp_connected,
                            "fallback_used": fallback_used,
                            "page_found": page_found,
                            "auth_status": auth_status,
                            "editor_selector_used": editor_selector_used,
                            "injection_attempts": injection_attempts,
                            "code_injected": code_injected,
                            "run_attempted": run_attempted,
                            "worker_started_detected": worker_started_detected,
                            "manual_action_required": manual_action_required,
                            "reason": reason or "unknown",
                            "screenshots_dir": str(debug_dir),
                            "errors": errors,
                            "warnings": warnings,
                            "written_at": utc_now(),
                        }
                    warnings.append("chrome_compatible_cdp_port_not_open")
            warnings.append("cdp_port_not_open; trying Playwright launch_persistent_context with the same existing profile_dir")
            assert sync_playwright is not None
            with sync_playwright() as playwright:
                context = None
                try:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=args.profile_dir,
                        executable_path=str(browser_exe),
                        headless=False,
                        args=[
                            "--remote-allow-origins=*",
                            "--no-first-run",
                            "--no-default-browser-check",
                        ],
                    )
                    fallback_used = "launch_persistent_context_existing_profile"
                    page = context.pages[-1] if context.pages else context.new_page()
                    page.goto(opened_url, wait_until="domcontentloaded", timeout=45000)
                    page_found = True
                    details = operate_loaded_page(page, args, debug_dir, warnings)
                    auth_status = details["auth_status"]
                    editor_selector_used = details["editor_selector_used"]
                    injection_attempts = details["injection_attempts"]
                    code_injected = bool(details["code_injected"])
                    run_attempted = bool(details["run_attempted"])
                    worker_started_detected = bool(details["worker_started_detected"])
                    reason = str(details["reason"])
                except Exception as exc:
                    errors.append(repr(exc))
                    reason = "persistent_context_fallback_failed"
                finally:
                    try:
                        if context:
                            context.close()
                    except Exception:
                        pass
            manual_action_required = auth_status == "auth_failed" or not code_injected
            return {
                "ok": bool(code_injected and run_attempted and worker_started_detected and not manual_action_required),
                "status": "auth_failed" if auth_status == "auth_failed" else ("ok" if code_injected else "failed"),
                "email": args.email,
                "browser": args.browser,
                "profile_dir": args.profile_dir,
                "remote_debugging_port": port,
                "opened_url": opened_url,
                "cdp_connected": False,
                "fallback_used": fallback_used,
                "page_found": page_found,
                "auth_status": auth_status,
                "editor_selector_used": editor_selector_used,
                "injection_attempts": injection_attempts,
                "code_injected": code_injected,
                "run_attempted": run_attempted,
                "worker_started_detected": worker_started_detected,
                "manual_action_required": manual_action_required,
                "reason": reason or "cdp_port_not_open",
                "screenshots_dir": str(debug_dir),
                "errors": errors,
                "warnings": warnings,
                "written_at": utc_now(),
            }

    assert sync_playwright is not None
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            cdp_connected = True
        except Exception as exc:
            return build_failure_report(args, "cdp_connect_failed", [repr(exc)], warnings, port)

        try:
            deadline = time.time() + 25
            while time.time() < deadline:
                page = newest_colab_page(browser)
                if page and "colab.research.google.com" in (page.url or "").lower():
                    page_found = True
                    break
                time.sleep(1)
            if not page:
                reason = "cdp_page_not_found"
                errors.append("no_pages_visible_over_cdp")
            elif not page_found:
                reason = "cdp_page_not_found"
                errors.append(f"colab_page_not_found: {page.url}")
            else:
                screenshot(page, debug_dir, "02_after_cdp_connect.png", warnings)
                details = operate_loaded_page(page, args, debug_dir, warnings)
                auth_status = details["auth_status"]
                editor_selector_used = details["editor_selector_used"]
                injection_attempts = details["injection_attempts"]
                code_injected = bool(details["code_injected"])
                run_attempted = bool(details["run_attempted"])
                worker_started_detected = bool(details["worker_started_detected"])
                reason = str(details["reason"])
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    manual_action_required = auth_status == "auth_failed" or not code_injected
    return {
        "ok": bool(code_injected and run_attempted and worker_started_detected and not manual_action_required),
        "status": "auth_failed" if auth_status == "auth_failed" else ("ok" if code_injected else "failed"),
        "email": args.email,
        "browser": args.browser,
        "profile_dir": args.profile_dir,
        "remote_debugging_port": port,
        "opened_url": opened_url,
        "cdp_connected": cdp_connected,
        "fallback_used": fallback_used,
        "page_found": page_found,
        "auth_status": auth_status,
        "editor_selector_used": editor_selector_used,
        "injection_attempts": injection_attempts,
        "code_injected": code_injected,
        "run_attempted": run_attempted,
        "worker_started_detected": worker_started_detected,
        "manual_action_required": manual_action_required,
        "reason": reason or "unknown",
        "screenshots_dir": str(debug_dir),
        "errors": errors,
        "warnings": warnings,
        "written_at": utc_now(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--browser", choices=["yandex", "chrome"], required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--story-id", default=DEFAULT_STORY_ID)
    parser.add_argument("--require-t4", action="store_true")
    parser.add_argument("--remote-debugging-port", type=int, default=0)
    parser.add_argument("--settle-seconds", type=int, default=12)
    parser.add_argument("--run-wait-seconds", type=int, default=120)
    args = parser.parse_args(argv)

    report = run_operator(args)
    write_json(report_path(args.story_id), report)
    print(f"ok={report.get('ok')}")
    print(f"status={report.get('status')}")
    print(f"email={report.get('email')}")
    print(f"browser={report.get('browser')}")
    print(f"profile_dir={report.get('profile_dir')}")
    print(f"remote_debugging_port={report.get('remote_debugging_port')}")
    print(f"opened_url={report.get('opened_url')}")
    print(f"cdp_connected={report.get('cdp_connected')}")
    print(f"page_found={report.get('page_found')}")
    print(f"auth_status={report.get('auth_status')}")
    print(f"editor_selector_used={report.get('editor_selector_used')}")
    print(f"code_injected={report.get('code_injected')}")
    print(f"run_attempted={report.get('run_attempted')}")
    print(f"worker_started_detected={report.get('worker_started_detected')}")
    print(f"manual_action_required={report.get('manual_action_required')}")
    print(f"reason={report.get('reason')}")
    print(f"screenshots_dir={report.get('screenshots_dir')}")
    print(f"report_path={report_path(args.story_id)}")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
