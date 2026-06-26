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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from youtube_worker_notebook_mount import colab_safe_drive_mount_block
except ImportError:
    from tools.colab_launcher.youtube_worker_notebook_mount import colab_safe_drive_mount_block

try:
    from colab_worker_gpu_check import colab_worker_gpu_check_block
except ImportError:
    from tools.colab_launcher.colab_worker_gpu_check import colab_worker_gpu_check_block

try:
    from proxy_local_bridge import (
        BRIDGE_START_STAGGER_SECONDS,
        LOCAL_BRIDGE_BASE_PORT,
        LOCAL_BRIDGE_HOST,
        LocalProxyBridge,
        UpstreamProxy,
        bridge_diagnostics_row,
        bridge_healthcheck_row,
        healthcheck_http_via_proxy,
        healthcheck_https_via_proxy,
        is_port_listening,
        local_bridge_port_for_index,
        print_bridge_diagnostics_table,
        print_bridge_healthcheck_table,
    )
except ImportError:
    from tools.colab_launcher.proxy_local_bridge import (
        BRIDGE_START_STAGGER_SECONDS,
        LOCAL_BRIDGE_BASE_PORT,
        LOCAL_BRIDGE_HOST,
        LocalProxyBridge,
        UpstreamProxy,
        bridge_diagnostics_row,
        bridge_healthcheck_row,
        healthcheck_http_via_proxy,
        healthcheck_https_via_proxy,
        is_port_listening,
        local_bridge_port_for_index,
        print_bridge_diagnostics_table,
        print_bridge_healthcheck_table,
    )


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
    drive_notebook_url: str
    require_t4: bool
    proxy_url: str = ""
    proxy_protocol: str = ""
    proxy_host: str = ""
    proxy_http_port: int = 0
    proxy_socks5_port: int = 0
    proxy_username: str = ""
    proxy_password: str = ""


@dataclass
class ProxyConfig:
    protocol: str
    host: str
    port: int
    username: str
    password: str


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _proxy_from_url(proxy_url: str) -> ProxyConfig | None:
    raw = str(proxy_url or "").strip()
    if not raw:
        return None
    match = re.match(r"^(https?|socks5)://(?:(?P<user>[^:@/]+):(?P<password>[^@/]+)@)?(?P<host>[^:/]+):(?P<port>\d+)$", raw, re.IGNORECASE)
    if not match:
        return None
    protocol = str(match.group(1) or "http").lower()
    host = str(match.group("host") or "").strip()
    port = _safe_int(match.group("port"), 0)
    username = str(match.group("user") or "").strip()
    password = str(match.group("password") or "").strip()
    if not host or port <= 0:
        return None
    return ProxyConfig(protocol=protocol, host=host, port=port, username=username, password=password)


def _proxy_from_mapping(raw: dict[str, Any]) -> ProxyConfig | None:
    protocol = str(raw.get("protocol") or "http").strip().lower()
    host = str(raw.get("host") or "").strip()
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "").strip()
    http_port = _safe_int(raw.get("http_port"), 0)
    socks5_port = _safe_int(raw.get("socks5_port"), 0)
    port = http_port if protocol in {"http", "https"} else socks5_port
    if port <= 0:
        port = _safe_int(raw.get("port"), 0)
    if not host or port <= 0:
        return None
    return ProxyConfig(protocol=protocol, host=host, port=port, username=username, password=password)


def _simple_kv_yaml(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            parsed[key.strip()] = unquote(value.strip())
    except Exception:
        return {}
    return parsed


def _resolve_proxy_config(*, worker: WorkerConfig, proxy_config_path: str, proxy_url: str) -> ProxyConfig | None:
    resolved = _resolve_proxy_with_meta(worker=worker, proxy_config_path=proxy_config_path, proxy_url=proxy_url)
    proxy = resolved.get("proxy")
    return proxy if isinstance(proxy, ProxyConfig) else None


def _proxy_report_fields(proxy: ProxyConfig | None) -> dict[str, Any]:
    if proxy is None:
        return {
            "proxy_enabled": False,
            "proxy_protocol": "",
            "proxy_host": "",
            "proxy_port": 0,
            "proxy_auth": False,
            "proxy_username_masked": "",
            "proxy_password_logged": False,
            "full_proxy_url_logged": False,
        }
    return {
        "proxy_enabled": True,
        "proxy_protocol": proxy.protocol,
        "proxy_host": proxy.host,
        "proxy_port": proxy.port,
        "proxy_auth": bool(proxy.username or proxy.password),
        "proxy_username_masked": proxy.username,
        "proxy_password_logged": False,
        "full_proxy_url_logged": False,
    }


def _mask_proxy_auth(proxy: ProxyConfig | None) -> str:
    if proxy is None:
        return ""
    if proxy.username or proxy.password:
        return f"{proxy.username}:***@{proxy.host}:{proxy.port}"
    return f"{proxy.host}:{proxy.port}"


PROXY_AUTH_EXTENSIONS_ROOT = PROJECT_ROOT / ".browser_profiles" / "_proxy_auth_extensions"


def _proxy_auth_extension_dir(email: str) -> Path:
    return PROXY_AUTH_EXTENSIONS_ROOT / safe_email_name(email)


def _write_proxy_auth_extension(extension_dir: Path, proxy: ProxyConfig) -> Path:
    if not (proxy.username or proxy.password):
        raise ValueError("proxy auth extension requires username and/or password")
    extension_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "CF Proxy Auth",
        "permissions": ["webRequest", "webRequestBlocking", "<all_urls>"],
        "background": {"scripts": ["background.js"], "persistent": True},
    }
    background_js = (
        "function onAuthRequired() {\n"
        "  return {\n"
        f"    authCredentials: {{ username: {json.dumps(proxy.username)}, password: {json.dumps(proxy.password)} }}\n"
        "  };\n"
        "}\n"
        "chrome.webRequest.onAuthRequired.addListener(\n"
        "  onAuthRequired,\n"
        '  { urls: ["<all_urls>"] },\n'
        '  ["blocking"]\n'
        ");\n"
    )
    (extension_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (extension_dir / "background.js").write_text(background_js, encoding="utf-8")
    return extension_dir.resolve()


def _normalize_proxy_auth_mode(requested: str, proxy: ProxyConfig | None) -> str:
    mode = str(requested or "").strip().lower().replace("_", "-")
    if mode in {"profile-managed", "profilemanaged"}:
        return "profile-managed"
    if mode in {"browser-managed", "browsermanaged"}:
        return "browser-managed"
    if mode in {"extension", "local-bridge", "direct"}:
        return mode
    if proxy is not None and (proxy.username or proxy.password):
        return "local-bridge"
    return "direct"


def _upstream_proxy_from_config(proxy: ProxyConfig) -> UpstreamProxy:
    return UpstreamProxy(
        host=proxy.host,
        port=proxy.port,
        username=proxy.username,
        password=proxy.password,
    )


def _append_proxy_launch_args(
    args: list[str],
    *,
    proxy: ProxyConfig | None,
    worker_email: str,
    proxy_required: bool,
    proxy_auth_mode: str = "local-bridge",
    local_bridge_port: int = 0,
) -> tuple[list[str], dict[str, Any]]:
    meta: dict[str, Any] = {
        "proxy_auth_extension_loaded": False,
        "proxy_auth_extension_path": "",
        "proxy_auth_mode": _normalize_proxy_auth_mode(proxy_auth_mode, proxy),
        "local_bridge_host": "",
        "local_bridge_port": 0,
        "browser_proxy_arg": "",
        "upstream_proxy_host": "",
        "upstream_proxy_port": 0,
        "upstream_auth": False,
    }
    if proxy is None:
        return args, meta
    meta["upstream_proxy_host"] = proxy.host
    meta["upstream_proxy_port"] = proxy.port
    meta["upstream_auth"] = bool(proxy.username or proxy.password)
    mode = meta["proxy_auth_mode"]
    if mode == "profile-managed":
        meta["browser_proxy_arg"] = "none"
        meta["proxy_source"] = "browser_profile"
        meta["upstream_auth_handled_by_bridge"] = False
        meta["local_bridge_used"] = False
        return args, meta
    if mode == "browser-managed":
        browser_proxy = f"http://{proxy.host}:{proxy.port}"
        meta["browser_proxy_arg"] = browser_proxy
        meta["upstream_auth_handled_by_bridge"] = False
        args.append(f"--proxy-server={browser_proxy}")
        return args, meta
    if mode == "local-bridge":
        if local_bridge_port <= 0:
            if proxy_required:
                raise ValueError(f"local_bridge_port_missing_for_{worker_email}")
            return args, meta
        meta["local_bridge_host"] = LOCAL_BRIDGE_HOST
        meta["local_bridge_port"] = int(local_bridge_port)
        browser_proxy = f"http://{LOCAL_BRIDGE_HOST}:{local_bridge_port}"
        meta["browser_proxy_arg"] = browser_proxy
        args.append(f"--proxy-server={browser_proxy}")
        return args, meta
    if mode == "direct" or not (proxy.username or proxy.password):
        browser_proxy = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
        meta["browser_proxy_arg"] = browser_proxy
        args.append(f"--proxy-server={browser_proxy}")
        return args, meta
    extension_dir = _write_proxy_auth_extension(_proxy_auth_extension_dir(worker_email), proxy)
    extension_path = str(extension_dir)
    browser_proxy = f"{proxy.protocol}://{proxy.host}:{proxy.port}"
    meta["browser_proxy_arg"] = browser_proxy
    args.append(f"--proxy-server={browser_proxy}")
    args.append(f"--load-extension={extension_path}")
    meta["proxy_auth_extension_loaded"] = True
    meta["proxy_auth_extension_path"] = extension_path
    meta["proxy_auth_mode"] = "extension"
    return args, meta


def _plan_local_bridge_ports(workers: list[WorkerConfig], *, base_port: int = LOCAL_BRIDGE_BASE_PORT) -> dict[str, int]:
    ports: dict[str, int] = {}
    for index, worker in enumerate(workers, start=1):
        ports[worker.email.lower()] = local_bridge_port_for_index(index, base_port=base_port)
    return ports


def _verify_worker_bridge_health(*, email: str, local_port: int, upstream: UpstreamProxy) -> dict[str, Any]:
    listening = is_port_listening(LOCAL_BRIDGE_HOST, local_port)
    error_parts: list[str] = []
    if not listening:
        error_parts.append("port_not_listening")
        return bridge_healthcheck_row(
            email=email,
            local_port=local_port,
            upstream=upstream,
            listening=False,
            healthcheck_http="FAIL",
            healthcheck_https="FAIL",
            error_message="; ".join(error_parts),
        )
    http_ok, http_err = healthcheck_http_via_proxy(LOCAL_BRIDGE_HOST, local_port)
    https_ok, https_err = healthcheck_https_via_proxy(LOCAL_BRIDGE_HOST, local_port)
    if http_err:
        error_parts.append(f"http:{http_err}")
    if https_err:
        error_parts.append(f"https:{https_err}")
    return bridge_healthcheck_row(
        email=email,
        local_port=local_port,
        upstream=upstream,
        listening=True,
        healthcheck_http="OK" if http_ok else "FAIL",
        healthcheck_https="OK" if https_ok else "FAIL",
        error_message="; ".join(error_parts),
    )


def _start_and_verify_local_proxy_bridges(
    workers: list[WorkerConfig],
    *,
    proxy_meta_by_email: dict[str, dict[str, Any]],
    bridge_ports_by_email: dict[str, int],
) -> tuple[list[LocalProxyBridge], list[dict[str, Any]], list[str]]:
    started: list[LocalProxyBridge] = []
    health_rows: list[dict[str, Any]] = []
    failed_emails: list[str] = []
    for worker in workers:
        proxy_meta = proxy_meta_by_email.get(worker.email.lower(), {})
        proxy_obj = proxy_meta.get("proxy")
        local_port = int(bridge_ports_by_email.get(worker.email.lower(), 0) or 0)
        if not isinstance(proxy_obj, ProxyConfig):
            health_rows.append(
                bridge_healthcheck_row(
                    email=worker.email,
                    local_port=local_port,
                    upstream=UpstreamProxy(host="", port=0, username="", password=""),
                    listening=False,
                    healthcheck_http="FAIL",
                    healthcheck_https="FAIL",
                    error_message=str(proxy_meta.get("reason") or "proxy_not_configured"),
                )
            )
            failed_emails.append(worker.email)
            continue
        upstream = _upstream_proxy_from_config(proxy_obj)
        try:
            bridge = LocalProxyBridge(listen_host=LOCAL_BRIDGE_HOST, listen_port=local_port, upstream=upstream)
            bridge.start()
            started.append(bridge)
            time.sleep(BRIDGE_START_STAGGER_SECONDS)
        except Exception as exc:
            health_rows.append(
                bridge_healthcheck_row(
                    email=worker.email,
                    local_port=local_port,
                    upstream=upstream,
                    listening=False,
                    healthcheck_http="FAIL",
                    healthcheck_https="FAIL",
                    error_message=f"bridge_start_failed:{exc!r}",
                )
            )
            failed_emails.append(worker.email)
            continue
        row = _verify_worker_bridge_health(email=worker.email, local_port=local_port, upstream=upstream)
        health_rows.append(row)
        if str(row.get("status")) != "OK":
            failed_emails.append(worker.email)
    if failed_emails:
        for bridge in started:
            bridge.stop()
        return [], health_rows, failed_emails
    return started, health_rows, []


def _stop_local_proxy_bridges(bridges: list[LocalProxyBridge]) -> None:
    for bridge in bridges:
        try:
            bridge.stop()
        except Exception:
            pass


def _bridge_monitor_slots(
    workers: list[WorkerConfig],
    bridge_ports_by_email: dict[str, int],
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for worker in workers:
        port = int(bridge_ports_by_email.get(worker.email.lower(), 0) or 0)
        if port <= 0:
            continue
        slots.append({"email": worker.email, "port": port})
    return slots


def _count_listening_bridge_ports(slots: list[dict[str, Any]]) -> int:
    alive = 0
    for slot in slots:
        port = int(slot.get("port") or 0)
        if port > 0 and is_port_listening(LOCAL_BRIDGE_HOST, port):
            alive += 1
    return alive


def _run_local_bridge_keepalive_loop(*, monitor_slots: list[dict[str, Any]]) -> None:
    stop_event = threading.Event()
    _run_local_bridge_keepalive_until_stop(monitor_slots=monitor_slots, stop_event=stop_event, quiet=False)


def _run_local_bridge_keepalive_until_stop(
    *,
    monitor_slots: list[dict[str, Any]],
    stop_event: threading.Event,
    poll_seconds: float = 10.0,
    quiet: bool = False,
) -> int:
    total = len(monitor_slots)
    if total <= 0:
        return 0
    if not quiet:
        print("bridges_active=True")
        print("keepalive_mode=True")
        print(f"ports_alive={_count_listening_bridge_ports(monitor_slots)}/{total}")
        print('message="Keep this PowerShell window open while Colab workers are running. Press Ctrl+C to stop bridges."')
    last_alive = -1
    while not stop_event.is_set():
        if stop_event.wait(max(0.5, float(poll_seconds))):
            break
        alive = _count_listening_bridge_ports(monitor_slots)
        if alive != last_alive and not quiet:
            print(f"ports_alive={alive}/{total}")
        last_alive = alive
        for slot in monitor_slots:
            email = str(slot.get("email") or "")
            port = int(slot.get("port") or 0)
            if port > 0 and not is_port_listening(LOCAL_BRIDGE_HOST, port) and not quiet:
                print(f"ERROR: bridge_dead email={email} port={port}")
    return last_alive if last_alive >= 0 else _count_listening_bridge_ports(monitor_slots)


@dataclass
class ColabGroupLaunchHandoff:
    ok: bool
    exit_code: int
    reason: str
    active_local_bridges: list[LocalProxyBridge] = field(default_factory=list)
    bridge_monitor_slots: list[dict[str, Any]] = field(default_factory=list)
    local_bridge_worker_count: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    opened_count: int = 0
    workers_total: int = 0


_LAST_LAUNCH_HANDOFF: ColabGroupLaunchHandoff | None = None


def consume_last_launch_handoff() -> ColabGroupLaunchHandoff | None:
    global _LAST_LAUNCH_HANDOFF
    handoff = _LAST_LAUNCH_HANDOFF
    _LAST_LAUNCH_HANDOFF = None
    return handoff


def peek_last_launch_handoff() -> ColabGroupLaunchHandoff | None:
    return _LAST_LAUNCH_HANDOFF


def _resolve_proxy_with_meta(*, worker: WorkerConfig, proxy_config_path: str, proxy_url: str) -> dict[str, Any]:
    raw_proxy_url = str(proxy_url or "").strip()
    if raw_proxy_url:
        parsed = _proxy_from_url(raw_proxy_url)
        if parsed is None:
            return {"status": "INVALID_PROXY", "reason": "invalid_cli_proxy_url", "proxy": None, "proxy_id": "cli_proxy_url"}
        return {"status": "OK", "reason": "", "proxy": parsed, "proxy_id": "cli_proxy_url"}

    worker_proxy_url = str(getattr(worker, "proxy_url", "") or "").strip()
    if worker_proxy_url:
        parsed = _proxy_from_url(worker_proxy_url)
        if parsed is None:
            return {"status": "INVALID_PROXY", "reason": "invalid_worker_proxy_url", "proxy": None, "proxy_id": f"worker:{worker.email}:proxy_url"}
        return {"status": "OK", "reason": "", "proxy": parsed, "proxy_id": f"worker:{worker.email}:proxy_url"}

    worker_raw: dict[str, Any] = {
        "protocol": str(getattr(worker, "proxy_protocol", "") or ""),
        "host": str(getattr(worker, "proxy_host", "") or ""),
        "http_port": int(getattr(worker, "proxy_http_port", 0) or 0),
        "socks5_port": int(getattr(worker, "proxy_socks5_port", 0) or 0),
        "username": str(getattr(worker, "proxy_username", "") or ""),
        "password": str(getattr(worker, "proxy_password", "") or ""),
    }
    has_inline_proxy_data = bool(
        str(worker_raw.get("protocol") or "").strip()
        or str(worker_raw.get("host") or "").strip()
        or int(worker_raw.get("http_port") or 0) > 0
        or int(worker_raw.get("socks5_port") or 0) > 0
        or str(worker_raw.get("username") or "").strip()
        or str(worker_raw.get("password") or "").strip()
    )
    inline_proxy = _proxy_from_mapping(worker_raw)
    if inline_proxy is not None:
        return {"status": "OK", "reason": "", "proxy": inline_proxy, "proxy_id": f"worker:{worker.email}:inline"}
    if has_inline_proxy_data:
        return {"status": "INVALID_PROXY", "reason": "invalid_worker_inline_proxy", "proxy": None, "proxy_id": f"worker:{worker.email}:inline"}

    path_raw = str(proxy_config_path or "").strip()
    if not path_raw:
        return {"status": "MISSING_PROXY", "reason": "proxy_not_configured", "proxy": None, "proxy_id": ""}
    path = Path(path_raw)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    if not path.is_file():
        return {"status": "INVALID_PROXY", "reason": f"proxy_config_not_found:{path}", "proxy": None, "proxy_id": ""}
    try:
        payload = read_yaml_like(path)
    except Exception as exc:
        return {"status": "INVALID_PROXY", "reason": f"proxy_config_parse_error:{exc!r}", "proxy": None, "proxy_id": ""}
    if not isinstance(payload, dict):
        return {"status": "INVALID_PROXY", "reason": "proxy_config_root_not_mapping", "proxy": None, "proxy_id": ""}

    workers_map = payload.get("workers")
    if isinstance(workers_map, dict):
        item = workers_map.get(worker.email)
        if isinstance(item, dict):
            by_worker = _proxy_from_mapping(item)
            if by_worker is None:
                return {"status": "INVALID_PROXY", "reason": "invalid_proxy_for_worker_in_config", "proxy": None, "proxy_id": f"proxy_config:workers:{worker.email}"}
            return {"status": "OK", "reason": "", "proxy": by_worker, "proxy_id": f"proxy_config:workers:{worker.email}"}

    default_map = payload.get("default")
    if isinstance(default_map, dict):
        default_proxy = _proxy_from_mapping(default_map)
        if default_proxy is None:
            return {"status": "INVALID_PROXY", "reason": "invalid_proxy_default_in_config", "proxy": None, "proxy_id": "proxy_config:default"}
        return {"status": "OK", "reason": "", "proxy": default_proxy, "proxy_id": "proxy_config:default"}

    root_proxy = _proxy_from_mapping(payload)
    if root_proxy is not None:
        return {"status": "OK", "reason": "", "proxy": root_proxy, "proxy_id": "proxy_config:root"}
    simple_payload = _simple_kv_yaml(path)
    simple_proxy = _proxy_from_mapping(simple_payload)
    if simple_proxy is not None:
        return {"status": "OK", "reason": "", "proxy": simple_proxy, "proxy_id": "proxy_config:root-simple"}
    return {"status": "MISSING_PROXY", "reason": "proxy_not_found_for_worker", "proxy": None, "proxy_id": ""}


def _print_proxy_table(rows: list[dict[str, Any]]) -> None:
    print("proxy_check_columns=email|group|profile_dir|browser|proxy_id|proxy_host|proxy_port|proxy_auth|status|reason")
    for row in rows:
        print(
            "proxy_check_row="
            f"{row.get('email')}|{row.get('group')}|{row.get('profile_dir')}|{row.get('browser')}|"
            f"{row.get('proxy_id')}|{row.get('proxy_host')}|{row.get('proxy_port')}|"
            f"{row.get('proxy_auth')}|{row.get('status')}|{row.get('reason')}"
        )


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
        if current_worker is not None and key in {"email", "browser", "profile_strategy", "launch_mode", "use_user_data_dir", "profile_dir", "notebook_path", "notebook_url", "drive_notebook_url", "require_t4", "proxy_url", "proxy_protocol", "proxy_host", "proxy_http_port", "proxy_socks5_port", "proxy_username", "proxy_password"}:
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
                    drive_notebook_url=str(item.get("drive_notebook_url") or item.get("drive_colab_url") or "").strip(),
                    require_t4=parse_bool(item.get("require_t4", False)),
                    proxy_url=str(item.get("proxy_url") or "").strip(),
                    proxy_protocol=str(item.get("proxy_protocol") or "").strip(),
                    proxy_host=str(item.get("proxy_host") or "").strip(),
                    proxy_http_port=_safe_int(item.get("proxy_http_port"), 0),
                    proxy_socks5_port=_safe_int(item.get("proxy_socks5_port"), 0),
                    proxy_username=str(item.get("proxy_username") or "").strip(),
                    proxy_password=str(item.get("proxy_password") or "").strip(),
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
    drive_mount_block = colab_safe_drive_mount_block(with_cf_boot=True)
    return f'''# === ContentFactory YouTube VIDEO Worker ===
# Worker: {email}

import os
import sys
import json
from pathlib import Path

WORKER_EMAIL = "{email}"
print("[CF_BOOT] cell_started account=" + WORKER_EMAIL, flush=True)
print("[CF_BOOT] python_version python=" + sys.version.replace("\\n", " "), flush=True)
print("[CF_BOOT] cwd cwd=" + str(Path.cwd()), flush=True)
{drive_mount_block}
!apt-get update -qq
!apt-get install -y -qq ffmpeg

import subprocess

ROOT = Path("/content/drive/MyDrive/ContentFactory_YouTube")
BOOTSTRAP_PATH = ROOT / "scripts" / "youtube_video_bootstrap_colab.py"
SCRIPT_PATH = ROOT / "scripts" / "youtube_video_worker_colab.py"
BOOT_STATUS_PATH = ROOT / "logs" / "colab_boot_status.json"

def write_cell_boot_status(stage, **updates):
    ROOT.joinpath("logs").mkdir(parents=True, exist_ok=True)
    payload = {{
        "account": WORKER_EMAIL,
        "started_at": updates.pop("started_at", None),
        "last_stage": stage,
        "last_stage_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "ok": False,
        "error_stage": None,
        "error": None,
        "traceback": None,
        "heartbeat_path": "",
        "heartbeat_written_once": False,
        "worker_main_loop_started": False,
    }}
    if BOOT_STATUS_PATH.exists():
        try:
            payload.update(json.loads(BOOT_STATUS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    payload.update(updates)
    payload["account"] = WORKER_EMAIL
    payload["last_stage"] = stage
    payload["last_stage_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    BOOT_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return BOOT_STATUS_PATH

print("WORKER_EMAIL:", WORKER_EMAIL)
print("ROOT exists:", ROOT.exists(), ROOT)
print("BOOTSTRAP exists:", BOOTSTRAP_PATH.exists(), BOOTSTRAP_PATH)
print("SCRIPT exists:", SCRIPT_PATH.exists(), SCRIPT_PATH)
print("[CF_BOOT] project_root_detected root=" + str(ROOT) + " root_exists=" + str(ROOT.exists()), flush=True)
try:
    write_cell_boot_status("project_root_detected")
except Exception as exc:
    print("[CF_BOOT_ERROR] stage=cell_boot_status", flush=True)
    print("[CF_BOOT_ERROR] exception=" + repr(exc), flush=True)

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
print("[CF_BOOT] env_loaded account=" + WORKER_EMAIL, flush=True)
try:
    write_cell_boot_status("env_loaded")
except Exception as exc:
    print("[CF_BOOT_ERROR] stage=cell_boot_status", flush=True)
    print("[CF_BOOT_ERROR] exception=" + repr(exc), flush=True)

{colab_worker_gpu_check_block()}

print("[CF_BOOT] worker_import_start worker_script=" + str(SCRIPT_PATH), flush=True)
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


def browser_args(
    browser_exe: Path,
    worker: WorkerConfig,
    url: str,
    proxy: ProxyConfig | None = None,
    *,
    proxy_required: bool = False,
    proxy_auth_mode: str = "local-bridge",
    local_bridge_port: int = 0,
) -> list[str]:
    args = [str(browser_exe)]
    if worker.use_user_data_dir:
        if not worker.profile_dir:
            raise ValueError(f"use_user_data_dir=true but profile_dir is empty for {worker.email}")
        args.append(f"--user-data-dir={worker.profile_dir}")
    args, _proxy_meta = _append_proxy_launch_args(
        args,
        proxy=proxy,
        worker_email=worker.email,
        proxy_required=proxy_required,
        proxy_auth_mode=proxy_auth_mode,
        local_bridge_port=local_bridge_port,
    )
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
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(local_path, payload)
    try:
        drive_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(drive_path, payload)
    except OSError as exc:
        report.setdefault("warnings", []).append(f"failed_to_write_drive_report: {exc}")
    return local_path, drive_path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _launcher_stage_report(
    *,
    story_id: str,
    worker: WorkerConfig,
    stage: str,
    events: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    screenshots: dict[str, str],
    url: str = "",
    url_source: str = "",
    exception: str = "",
    failure_reason: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    extra_payload = dict(extra or {})
    for reserved_key in ("kind", "ts", "stage", "url", "url_source", "exception"):
        extra_payload.pop(reserved_key, None)
    stage_event = autorun_event(
        "launcher_stage_progress",
        stage=stage,
        url=url,
        url_source=url_source,
        exception=exception,
        **extra_payload,
    )
    events.append(stage_event)
    reason = failure_reason or (exception and stage) or "stage_progress"
    page_open_stages = {
        "after_page_goto",
        "before_wait_colab_ready",
        "after_wait_colab_ready",
        "before_trust_modal_check",
        "after_trust_modal_check",
        "before_run_all",
        "after_run_all",
    }
    opened = stage in page_open_stages
    run_attempted = stage in {"before_run_all", "after_run_all"}
    original_notebook_url = str(extra_payload.get("original_notebook_url") or url)
    normalized_colab_url = str(extra_payload.get("normalized_colab_url") or url)
    normalization_applied = bool(extra_payload.get("normalization_applied", False))
    summary = summarize_browser_tab_autorun(
        events,
        confirmation={"attempted": True, "ok": False, "reason": reason, "stage": stage},
        failure_reason=failure_reason,
    )
    report = {
        "ok": False,
        "status": "launcher_stage_progress",
        "stage": stage,
        "stage_timestamp": stage_event["ts"],
        "stage_exception": exception,
        "stage_extra": extra_payload,
        "original_notebook_url": original_notebook_url,
        "normalized_colab_url": normalized_colab_url,
        "normalization_applied": normalization_applied,
        "group": worker.group,
        "mode": "prepared-notebook-url",
        "profile_strategy": worker.profile_strategy,
        "uses_user_data_dir": True,
        "config_path": str(DEFAULT_CONFIG.resolve()),
        "story_id": story_id,
        "dry_run": False,
        "auto_run_requested": True,
        "email_filter": worker.email,
        "workers_total": 1,
        "opened_count": 0,
        "code_injected_count": 1 if url else 0,
        "manual_action_required_count": 1,
        "results": [
            {
                "email": worker.email,
                "browser": worker.browser,
                "group": worker.group,
                "profile_strategy": worker.profile_strategy,
                "profile_dir": worker.profile_dir,
                "profile_dir_exists": bool(worker.profile_dir and Path(worker.profile_dir).is_dir()),
                "use_user_data_dir": True,
                "launch_mode": worker.launch_mode,
                "opened_url": url,
                "opened": opened,
                "notebook_path": worker.notebook_path,
                "notebook_url": worker.notebook_url,
                "drive_notebook_url": str(getattr(worker, "drive_notebook_url", "") or ""),
                "original_notebook_url": original_notebook_url,
                "normalized_colab_url": normalized_colab_url,
                "normalization_applied": normalization_applied,
                "url_source": url_source,
                "url_used": url,
                "github_fallback_used": url_source == "github_fallback",
                "config_missing_drive_notebook_url": False,
                "require_t4": worker.require_t4,
                "code_injected": bool(url),
                "run_attempted": run_attempted,
                "worker_started_detected": False,
                "auto_run_attempted": True,
                "auto_run_result": {
                    "attempted": True,
                    "ok": False,
                    "reason": reason,
                    "confirmation": {"attempted": True, "ok": False, "reason": reason, "stage": stage},
                    "attempts": attempts,
                    "prompt_attempts": [],
                    "events": events,
                    "autorun_mode": "browser-tab",
                    "autorun_summary": summary,
                    "failure_reason": failure_reason,
                },
                "autorun_mode": "browser-tab",
                "autorun_summary": summary,
                "failure_reason": failure_reason,
                "reason": reason,
                "manual_action_required": True,
                "debug_screenshots": bool(screenshots),
                "debug_dir": str(debug_dir_for(story_id, worker.email)) if screenshots else "",
                "screenshots": screenshots,
                "warnings": [],
                "errors": [exception] if exception else [],
            }
        ],
        "warnings": ["launcher stage progress report; final report may overwrite this"],
        "written_at": utc_now(),
    }
    write_report(story_id, report)


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
    browser_profile_dir = ""
    browser_executable = ""
    cdp_debugging_port = None
    browser_profile_pids: list[int] = []
    prelaunch_lock_snapshot: dict[str, Any] = {}
    lock_recheck_snapshots: list[dict[str, Any]] = []
    final_lock_snapshot: dict[str, Any] = {}
    lock_classification = ""
    for item in events:
        if not isinstance(item, dict):
            continue
        if item.get("backend"):
            backend = str(item.get("backend"))
        if item.get("browser_profile_dir"):
            browser_profile_dir = str(item.get("browser_profile_dir"))
        if item.get("browser_executable"):
            browser_executable = str(item.get("browser_executable"))
        if item.get("cdp_port"):
            cdp_debugging_port = item.get("cdp_port")
        if item.get("debugging_port"):
            cdp_debugging_port = item.get("debugging_port")
        if isinstance(item.get("browser_pids"), list):
            browser_profile_pids = [int(pid) for pid in item.get("browser_pids") if str(pid).isdigit()]
        if isinstance(item.get("prelaunch_lock_snapshot"), dict):
            prelaunch_lock_snapshot = dict(item.get("prelaunch_lock_snapshot") or {})
        if isinstance(item.get("lock_recheck_snapshots"), list):
            lock_recheck_snapshots = list(item.get("lock_recheck_snapshots") or [])
        if isinstance(item.get("final_lock_snapshot"), dict):
            final_lock_snapshot = dict(item.get("final_lock_snapshot") or {})
        if item.get("lock_classification"):
            lock_classification = str(item.get("lock_classification") or "")
    run_all_clicked = "run_all_clicked" in kinds
    ctrl_f9_sent = "ctrl_f9_sent" in kinds
    heartbeat_restored = "heartbeat_restored" in kinds
    page_output_detected = bool(confirmation.get("page_output_detected") or "page_output_detected" in kinds)
    stopped_at = failure_reason or ""
    if not stopped_at and not heartbeat_restored:
        for step in (
            "browser_connection_failed",
            "launch_context_timeout",
            "page_new_timeout",
            "page_goto_timeout",
            "drive_colab_goto_partial_open",
            "colab_readiness_timeout",
            "browser_page_closed_before_runtime_start",
            "browser_context_closed_before_runtime_start",
            "browser_disconnected_before_runtime_start",
            "run_all_action_failed",
            "run_all_action_timeout",
            "colab_tab_not_found",
            "drive_permission_required",
            "oauth_required",
            "runtime_menu_timeout",
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
        "browser_backend_selected": backend,
        "browser_profile_dir": browser_profile_dir,
        "browser_executable": browser_executable,
        "browser_tab_autorun_attempted": "browser_tab_autorun_started" in kinds,
        "browser_connected": "browser_connection_ok" in kinds,
        "gpu_safe_mode": "gpu_safe_mode_enabled" in kinds,
        "browser_connection_failed_reason": failure_reason or stopped_at or "",
        "browser_connection_failed": "browser_connection_failed" in kinds,
        "browser_profile_locked": "browser_profile_locked" in kinds,
        "browser_profile_pids": browser_profile_pids,
        "prelaunch_lock_snapshot": prelaunch_lock_snapshot,
        "lock_recheck_snapshots": lock_recheck_snapshots,
        "final_lock_snapshot": final_lock_snapshot,
        "lock_classification": lock_classification or ("stable_lock" if "browser_profile_locked" in kinds else "no_lock"),
        "browser_launched_by_playwright": "browser_launched_by_playwright" in kinds,
        "cdp_port_assigned": any(isinstance(item, dict) and item.get("kind") == "cdp_port_assigned" for item in events),
        "cdp_debugging_port": cdp_debugging_port,
        "cdp_port_available": "cdp_port_available" in kinds,
        "cdp_connected": "cdp_connected" in kinds,
        "colab_page_opened": "colab_page_opened" in kinds,
        "page_goto_started": "page_goto_started" in kinds,
        "page_goto_commit_ok": "page_goto_commit_ok" in kinds,
        "page_goto_domcontentloaded": "page_goto_domcontentloaded" in kinds,
        "page_goto_timeout": "page_goto_timeout" in kinds,
        "drive_colab_goto_partial_open": "drive_colab_goto_partial_open" in kinds,
        "colab_readiness_wait_started": "colab_readiness_wait_started" in kinds,
        "colab_readiness_ok": "colab_readiness_ok" in kinds,
        "colab_readiness_timeout": "colab_readiness_timeout" in kinds,
        "colab_tab_found": "colab_tab_found" in kinds,
        "runtime_menu_found": "runtime_menu_found" in kinds,
        "runtime_menu_timeout": "runtime_menu_timeout" in kinds,
        "runtime_menu_clicked": "runtime_menu_clicked" in kinds,
        "run_all_found": "run_all_found" in kinds,
        "run_all_clicked": run_all_clicked,
        "run_all_attempted": bool(run_all_clicked or ctrl_f9_sent),
        "run_all_action_timeout": "run_all_action_timeout" in kinds,
        "ctrl_f9_attempted": "ctrl_f9_attempted" in kinds,
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
        "[CF_BOOT] cell_started",
        "[CF_BOOT] worker_import_start",
        "[CF_BOOT] worker_import_ok",
        "[CF_BOOT] heartbeat_written_once",
        "[CF_BOOT] worker_main_loop_start",
        "[CF_BOOT] worker_main_loop_alive",
        "[CF_BOOT_ERROR]",
        "[HEARTBEAT]",
        "[LOOP]",
        "[CLAIM]",
        "youtube video bootstrap resolved root",
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
    email_runtime_hint = email in text and any(marker in text for marker in ("[HEARTBEAT]", "[CF_BOOT]", "[LOOP]", "[CLAIM]"))
    if email_runtime_hint and email not in found:
        found.append(email)
    return {
        "attempted": True,
        "ok": bool(found),
        "reason": "live_worker_output_detected" if found else "live_worker_output_not_detected",
        "found_markers": found,
        "copied_chars": len(text),
        "output_text_tail": text[-4000:],
    }


def _click_first_visible_text(page: Any, patterns: list[str], *, timeout_ms: int = 2500) -> str:
    deadline = time.monotonic() + max(0.5, int(timeout_ms) / 1000.0)
    for pattern in patterns:
        remaining_ms = int(max(0, deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return ""
        regex = re.compile(pattern, re.IGNORECASE)
        candidates = [
            page.get_by_role("button", name=regex),
            page.get_by_role("menuitem", name=regex),
            page.get_by_role("link", name=regex),
            page.get_by_text(regex),
        ]
        for candidate in candidates:
            remaining_ms = int(max(0, deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return ""
            try:
                first = candidate.first
                first.click(timeout=min(1200, remaining_ms))
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


def _save_playwright_screenshot(
    page: Any,
    debug_dir: Path | None,
    screenshots: dict[str, str] | None,
    key: str,
    events: list[dict[str, Any]],
) -> str:
    if debug_dir is None or screenshots is None:
        return ""
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{key}.png"
        page.screenshot(path=str(path), full_page=False)
        screenshots[key] = str(path)
        events.append(autorun_event("browser_tab_screenshot_saved", key=key, path=str(path)))
        return str(path)
    except Exception as exc:
        events.append(autorun_event("browser_tab_screenshot_failed", key=key, error=repr(exc)))
        return ""


def _body_text_tail(page: Any, limit: int = 2000) -> str:
    try:
        text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        try:
            text = page.content()
        except Exception:
            return ""
    return text[-limit:]


def _page_diagnostics(page: Any | None, context: Any | None = None, *, include_body: bool = True) -> dict[str, Any]:
    if page is None:
        return {"page_present": False, "page_closed": True, "page_url": "", "page_title": "", "body_text_tail": "", "context_pages": []}
    closed = _safe_page_is_closed(page)
    try:
        url = page.url if not closed else ""
    except Exception:
        url = ""
    try:
        title = page.title(timeout=1000) if not closed else ""
    except Exception:
        title = ""
    body_tail = ""
    if include_body and not closed:
        body_tail = _body_text_tail(page, limit=2500)
    context_snapshot: dict[str, Any] = {"pages": []}
    try:
        context_snapshot = _context_pages_snapshot(context or page.context)
    except Exception as exc:
        context_snapshot = {"ok": False, "count": 0, "pages": [], "error": repr(exc)}
    return {
        "page_present": True,
        "page_closed": closed,
        "page_url": url,
        "page_title": title,
        "body_text_tail": body_tail,
        "context_pages": context_snapshot.get("pages", []),
        "context_pages_count": context_snapshot.get("count", 0),
        "context_ok": context_snapshot.get("ok", False),
        "context_error": context_snapshot.get("error", ""),
    }


def _attach_page_debug_events(page: Any, events: list[dict[str, Any]]) -> None:
    try:
        page.on(
            "console",
            lambda msg: events.append(
                autorun_event(
                    "browser_console",
                    level=getattr(msg, "type", ""),
                    text=(getattr(msg, "text", "") or "")[-1200:],
                )
            )
            if str(getattr(msg, "type", "") or "").lower() in {"error", "warning"}
            else None,
        )
    except Exception:
        pass
    try:
        page.on("pageerror", lambda exc: events.append(autorun_event("browser_page_error", error=repr(exc))))
    except Exception:
        pass
    try:
        def handle_request_failed(request: Any) -> None:
            try:
                url = str(getattr(request, "url", "") or "")
                resource_type = str(getattr(request, "resource_type", "") or "")
                if resource_type != "document" and "colab.research.google.com" not in url and "drive.google.com" not in url:
                    return
                failure = request.failure or {}
            except Exception:
                url = ""
                resource_type = ""
                failure = {}
            events.append(autorun_event("browser_request_failed", url=url, resource_type=resource_type, failure=failure))

        page.on("requestfailed", handle_request_failed)
    except Exception:
        pass


def _wait_for_colab_ui_ready(page: Any, events: list[dict[str, Any]], *, timeout_seconds: int = 25) -> bool:
    events.append(autorun_event("colab_readiness_wait_started", diagnostics=_page_diagnostics(page, include_body=False)))
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    selectors = [
        "#runtime-menu-button",
        "colab-main-menu-button#runtime",
        "[aria-label='Runtime']",
        "[aria-label='Среда выполнения']",
        "colab-toolbar",
        "colab-run-button",
        "div.notebook-content",
        "colab-connect-button",
    ]
    text_patterns = [
        r"Colab",
        r"notebook",
        r"блокнот",
        r"Runtime",
        r"Среда выполнения",
        r"Connect",
        r"Подключиться",
        r"Run all",
        r"Выполнить",
    ]
    while time.monotonic() < deadline:
        if _safe_page_is_closed(page):
            events.append(autorun_event("colab_readiness_timeout", reason="page_closed"))
            return False
        diagnostics = _page_diagnostics(page, include_body=True)
        page_url = str(diagnostics.get("page_url") or "")
        body_tail = str(diagnostics.get("body_text_tail") or "")
        title = str(diagnostics.get("page_title") or "")
        if re.search(r"access denied|permission denied|request access|sign in|войдите|нет доступа|доступ запрещ", body_tail + "\n" + title, re.IGNORECASE):
            events.append(
                autorun_event(
                    "colab_readiness_timeout",
                    reason="login_or_access_denied",
                    current_url=page_url,
                    page_title=title,
                    body_tail=body_tail[-2000:],
                )
            )
            return False
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible(timeout=800):
                    events.append(
                        autorun_event(
                            "colab_readiness_ok",
                            selector=selector,
                            current_url=page_url,
                            page_title=title,
                            body_tail=body_tail[-2000:],
                        )
                    )
                    return True
            except Exception:
                continue
        matched = _page_text_contains_any(page, text_patterns)
        if matched:
            diagnostics = _page_diagnostics(page, include_body=True)
            events.append(
                autorun_event(
                    "colab_readiness_ok",
                    matched_text=matched,
                    current_url=diagnostics.get("page_url", ""),
                    page_title=diagnostics.get("page_title", ""),
                    body_tail=str(diagnostics.get("body_text_tail") or "")[-2000:],
                )
            )
            return True
        time.sleep(1)
    diagnostics = _page_diagnostics(page, include_body=True)
    events.append(
        autorun_event(
            "colab_readiness_timeout",
            current_url=diagnostics.get("page_url", ""),
            page_title=diagnostics.get("page_title", ""),
            body_tail=str(diagnostics.get("body_text_tail") or "")[-2500:],
            diagnostics=diagnostics,
        )
    )
    return False


def _failure_reason_from_open_error(error_text: str) -> str:
    lowered = str(error_text or "").lower()
    if "launch_context_timeout" in lowered or ("timeout" in lowered and "launch_persistent_context" in lowered):
        return "launch_context_timeout"
    if "page_new_timeout" in lowered:
        return "page_new_timeout"
    if "drive_colab_goto_partial_open" in lowered:
        return "drive_colab_goto_partial_open"
    if "page_goto_timeout" in lowered or "drive_colab_goto_timeout" in lowered or ("page.goto" in lowered and "timeout" in lowered):
        return "page_goto_timeout"
    if "colab_readiness_timeout" in lowered or "colab_ui_ready_timeout" in lowered:
        return "colab_readiness_timeout"
    return "colab_page_open_failed"


def _click_github_trust_modal(page: Any, events: list[dict[str, Any]]) -> str:
    patterns = [
        r"Выполнить",
        r"Всё равно выполнить",
        r"Все равно выполнить",
        r"Run anyway",
        r"^Run$",
        r"Trust",
        r"Доверять",
        r"I understand",
        r"Продолжить",
        r"Continue",
    ]
    for pattern in patterns:
        regex = re.compile(pattern, re.IGNORECASE)
        locators = [
            page.get_by_role("dialog").get_by_role("button", name=regex),
            page.get_by_role("button", name=regex),
            page.locator("paper-dialog button").filter(has_text=regex),
            page.locator("colab-dialog button").filter(has_text=regex),
            page.locator("mwc-button").filter(has_text=regex),
            page.locator("[role='dialog'] button").filter(has_text=regex),
        ]
        for locator in locators:
            try:
                target = locator.last
                target.scroll_into_view_if_needed(timeout=1500)
                target.click(timeout=3000)
                events.append(autorun_event("github_trust_modal_confirmed", matched=pattern, strategy="locator"))
                return pattern
            except Exception:
                continue
    js_patterns = [pattern.strip("^$") for pattern in patterns]
    try:
        matched = page.evaluate(
            """(patterns) => {
                const norm = (value) => (value || '').trim();
                const candidates = Array.from(document.querySelectorAll('button, mwc-button, paper-button, [role="button"]'));
                for (const pattern of patterns) {
                    const re = new RegExp(pattern, 'i');
                    for (const el of candidates) {
                        const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label'));
                        const box = el.getBoundingClientRect();
                        if (!text || box.width <= 0 || box.height <= 0) continue;
                        if (re.test(text)) {
                            el.click();
                            return text;
                        }
                    }
                }
                return '';
            }""",
            js_patterns,
        )
        if matched:
            events.append(autorun_event("github_trust_modal_confirmed", matched=matched, strategy="evaluate"))
            return str(matched)
    except Exception as exc:
        events.append(autorun_event("github_trust_modal_js_click_failed", error=repr(exc)))
    return ""


def _handle_permission_prompts_browser_tab(
    page: Any,
    events: list[dict[str, Any]],
    *,
    max_oauth_continue_clicks: int = 2,
    debug_dir: Path | None = None,
    screenshots: dict[str, str] | None = None,
) -> dict[str, Any]:
    state = {
        "warning_detected": False,
        "warning_confirmed": False,
        "drive_permission_required": False,
        "drive_permission_handled": False,
        "oauth_required": False,
        "oauth_handled": False,
        "oauth_continue_click_count": 0,
        "github_trust_modal_handled": False,
        "human_blocker": False,
        "human_blocker_text": "",
    }
    warning_detected = _page_text_contains_any(
        page,
        [
            r"wasn.t authored by Google",
            r"не создан Google",
            r"Google не имеет отношения к созданию",
            r"not authored by Google",
            r"untrusted notebook",
            r"notebook.*GitHub",
            r"блокнот.*GitHub",
        ],
    )
    if warning_detected:
        state["warning_detected"] = True
        events.append(autorun_event("warning_modal_detected", matched=warning_detected))
        _save_playwright_screenshot(page, debug_dir, screenshots, "github_trust_modal_before", events)
    warning_matched = _click_github_trust_modal(page, events) if warning_detected else ""
    if not warning_matched:
        warning_matched = _click_first_visible_text(
            page,
            [
                r"Выполнить",
                r"Всё равно выполнить",
                r"Все равно выполнить",
                r"Run anyway",
                r"Trust",
                r"I understand",
                r"Доверять",
                r"Продолжить",
                r"Continue",
                r"^Run$",
            ],
            timeout_ms=2500,
        )
    if warning_matched:
        state["warning_confirmed"] = True
        state["github_trust_modal_handled"] = True
        events.append(autorun_event("warning_modal_confirmed", matched=warning_matched))
        time.sleep(2)
        _save_playwright_screenshot(page, debug_dir, screenshots, "github_trust_modal_after", events)
        still_detected = _page_text_contains_any(
            page,
            [
                r"wasn.t authored by Google",
                r"не создан Google",
                r"Google не имеет отношения к созданию",
                r"not authored by Google",
                r"untrusted notebook",
            ],
        )
        if still_detected:
            state["human_blocker"] = True
            state["human_blocker_text"] = _body_text_tail(page)
            events.append(
                autorun_event(
                    "github_trust_modal_unhandled",
                    matched=still_detected,
                    modal_text_tail=state["human_blocker_text"],
                )
            )
    elif warning_detected:
        state["human_blocker"] = True
        state["human_blocker_text"] = _body_text_tail(page)
        events.append(
            autorun_event(
                "github_trust_modal_unhandled",
                matched=warning_detected,
                modal_text_tail=state["human_blocker_text"],
            )
        )

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
    deadline = time.monotonic() + 8
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
        if time.monotonic() >= deadline:
            break
        try:
            locator = page.locator(selector).first
            locator.click(timeout=1200)
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
        runtime_matched = _click_first_visible_text(page, runtime_patterns, timeout_ms=max(1000, int((deadline - time.monotonic()) * 1000)))
    if not runtime_matched:
        events.append(autorun_event("runtime_menu_timeout"))
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
        events.append(autorun_event("run_all_action_timeout", reason="run_all_menu_item_not_found"))
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
        if _safe_page_is_closed(page):
            confirmation["reason"] = "browser_page_closed_before_runtime_start"
            events.append(autorun_event("browser_page_closed_before_runtime_start", stage="heartbeat_wait"))
            return confirmation
        try:
            prompt_state = _handle_permission_prompts_browser_tab(page, events)
        except Exception as exc:
            if "closed" in repr(exc).lower():
                confirmation["reason"] = "browser_page_closed_before_runtime_start"
                confirmation["error"] = repr(exc)
                events.append(autorun_event("browser_page_closed_before_runtime_start", stage="heartbeat_wait", error=repr(exc)))
                return confirmation
            raise
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
        if prompt_state.get("human_blocker"):
            confirmation["reason"] = "external_human_step_required"
            confirmation["human_blocker"] = True
            confirmation["human_blocker_text"] = prompt_state.get("human_blocker_text") or ""
            events.append(
                autorun_event(
                    "external_human_step_required",
                    reason="github_trust_modal_unhandled",
                    human_blocker_text=confirmation["human_blocker_text"],
                )
            )
            return confirmation

        try:
            page_confirmation = _page_has_live_worker_output(page, email)
        except Exception as exc:
            if "closed" in repr(exc).lower():
                confirmation["reason"] = "browser_page_closed_before_runtime_start"
                confirmation["error"] = repr(exc)
                events.append(autorun_event("browser_page_closed_before_runtime_start", stage="heartbeat_wait", error=repr(exc)))
                return confirmation
            raise
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


def _is_browser_profile_locked_error(exc: Exception) -> bool:
    text = repr(exc).lower()
    return any(
        marker in text
        for marker in (
            "processsingleton",
            "profile is already in use",
            "singletonlock",
            "singletoncookie",
            "singletonsocket",
            "user data directory is already in use",
            "browser_profile_locked",
        )
    )


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _process_exists(pid: int) -> bool:
    if os.name != "nt" or pid <= 0:
        return False
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Id"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
        )
    except Exception:
        return False
    return bool((proc.stdout or "").strip())


def _extract_main_process_pid(command_line: str) -> int | None:
    match = re.search(r"--annotation=main_process_pid=(\d+)", command_line or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


WORKER_COLAB_PROFILE_MARKER = ".browser_profiles/youtube_colab/"


def _profile_dir_path_variants(profile_dir: str) -> list[str]:
    if not profile_dir:
        return []
    normalized = os.path.normpath(profile_dir)
    return sorted({profile_dir, normalized, normalized.replace("\\", "/"), profile_dir.replace("/", "\\")})


def _command_line_contains_profile_dir(command_line: str, profile_dir: str) -> bool:
    cmd = str(command_line or "")
    if not cmd:
        return False
    return any(variant and variant in cmd for variant in _profile_dir_path_variants(profile_dir))


def _command_line_has_user_data_dir_for_profile(command_line: str, profile_dir: str) -> bool:
    cmd = str(command_line or "")
    if not cmd:
        return False
    for variant in _profile_dir_path_variants(profile_dir):
        if not variant:
            continue
        needles = (
            f"--user-data-dir={variant}",
            f'--user-data-dir="{variant}"',
            f"--user-data-dir='{variant}'",
        )
        if any(needle in cmd for needle in needles):
            return True
    return False


def _is_youtube_colab_worker_profile_dir(profile_dir: str) -> bool:
    if not profile_dir:
        return False
    normalized = profile_dir.replace("\\", "/").lower()
    return WORKER_COLAB_PROFILE_MARKER.lower() in normalized


def _is_closeable_profile_browser_process(holder: dict[str, Any], profile_dir: str, browser: str) -> bool:
    if not _is_youtube_colab_worker_profile_dir(profile_dir):
        return False
    process_name = str(holder.get("name") or "").strip().lower()
    browser_key = str(browser or "yandex").strip().lower()
    executable_path = str(holder.get("executable_path") or "").strip().lower()
    if browser_key == "chrome":
        if process_name != "chrome.exe":
            return False
    else:
        if process_name != "browser.exe":
            return False
        if executable_path:
            if executable_path.endswith("chrome.exe") or "\\chrome.exe" in executable_path or "/chrome.exe" in executable_path:
                return False
            if not (executable_path.endswith("\\browser.exe") or executable_path.endswith("/browser.exe")):
                return False
    command_line = str(holder.get("command_line") or "")
    if not _command_line_has_user_data_dir_for_profile(command_line, profile_dir):
        return False
    if bool(holder.get("is_crashpad_handler")):
        return False
    return True


def _is_closeable_worker_browser_process(holder: dict[str, Any], profile_dir: str) -> bool:
    return _is_closeable_profile_browser_process(holder, profile_dir, "yandex")


def _terminate_process_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    if not _process_exists(pid):
        return True
    try:
        proc = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
        )
    except Exception:
        return not _process_exists(pid)
    if proc.returncode == 0:
        return True
    return not _process_exists(pid)


def _remove_worker_profile_singleton_lock_files(profile_dir: str) -> list[str]:
    removed: list[str] = []
    root = Path(profile_dir)
    if not root.is_dir():
        return removed
    for path in sorted(root.glob("Singleton*")):
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed.append(str(path))
            print(f"removed_lock_file={path}")
        except OSError as exc:
            print(f"removed_lock_file_failed={path} error={exc!r}")
    return removed


def _close_profile_browser(*, email: str, profile_dir: str, browser: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "email": email,
        "profile_dir": profile_dir,
        "browser": browser,
        "closed_process_ids": [],
        "removed_lock_files": [],
        "errors": [],
    }
    if not profile_dir:
        result["errors"].append("profile_dir_missing_in_config")
        return result
    if not _is_youtube_colab_worker_profile_dir(profile_dir):
        result["errors"].append("profile_dir_not_youtube_colab_worker_path")
        return result
    snapshot = _browser_profile_lock_snapshot(profile_dir)
    candidate_pids: list[int] = []
    for holder in snapshot.get("holders") or []:
        if not isinstance(holder, dict):
            continue
        if not _is_closeable_profile_browser_process(holder, profile_dir, browser):
            continue
        pid = int(holder.get("pid") or 0)
        if pid > 0 and pid not in candidate_pids:
            candidate_pids.append(pid)
    for pid in candidate_pids:
        if _terminate_process_pid(pid):
            result["closed_process_ids"].append(pid)
            print(f"closed_profile_browser email={email} browser={browser} profile_dir={profile_dir} process_id={pid}")
        else:
            result["errors"].append(f"terminate_failed_pid={pid}")
    if candidate_pids:
        time.sleep(1)
    result["removed_lock_files"] = _remove_worker_profile_singleton_lock_files(profile_dir)
    return result


def _close_single_worker_browser(*, email: str, profile_dir: str) -> dict[str, Any]:
    return _close_profile_browser(email=email, profile_dir=profile_dir, browser="yandex")


def _run_close_worker_browsers(workers: list[WorkerConfig]) -> int:
    if not workers:
        print("ok=False")
        print("reason=no_workers_selected")
        return 2
    all_errors: list[str] = []
    for worker in workers:
        if not worker.profile_dir:
            all_errors.append(f"{worker.email}:profile_dir_missing_in_config")
            continue
        item = _close_single_worker_browser(email=worker.email, profile_dir=worker.profile_dir)
        all_errors.extend(str(err) for err in item.get("errors") or [])
    ok = not all_errors
    print(f"ok={ok}")
    if all_errors:
        print(f"errors={json.dumps(all_errors, ensure_ascii=True)}")
    return 0 if ok else 2


def _browser_profile_lock_snapshot(profile_dir: str) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"ts": utc_now(), "profile_dir": profile_dir, "holders": []}
    if os.name != "nt" or not profile_dir:
        return snapshot
    normalized = os.path.normpath(profile_dir)
    variants = sorted({profile_dir, normalized, normalized.replace("\\", "/"), profile_dir.replace("/", "\\")})
    needles = "@(" + ",".join(_powershell_quote(item) for item in variants if item) + ")"
    command = (
        "$needles = " + needles + "; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $cmd = $_.CommandLine; $cmd -and ($needles | Where-Object { $cmd.Contains($_) }) } | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Depth 4"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
        )
    except Exception:
        return snapshot
    text = (proc.stdout or "").strip()
    if not text:
        return snapshot
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return snapshot
    rows = raw if isinstance(raw, list) else [raw]
    holders: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        command_line = str(row.get("CommandLine") or "")
        lower_command_line = command_line.lower()
        if "get-ciminstance win32_process" in lower_command_line and "convertto-json" in lower_command_line:
            continue
        main_pid = _extract_main_process_pid(command_line)
        is_crashpad = "crashpad-handler" in command_line.lower() or "crashpad" in str(row.get("Name") or "").lower()
        holder = {
            "pid": pid,
            "name": str(row.get("Name") or ""),
            "executable_path": str(row.get("ExecutablePath") or ""),
            "command_line": command_line,
            "main_process_pid": main_pid,
            "main_process_exists": _process_exists(main_pid) if main_pid is not None else None,
            "is_crashpad_handler": is_crashpad,
        }
        holders.append(holder)
    snapshot["holders"] = holders
    return snapshot


def _holder_pids(snapshot: dict[str, Any]) -> list[int]:
    return [int(item.get("pid") or 0) for item in snapshot.get("holders") or [] if int(item.get("pid") or 0) > 0]


def _terminate_orphan_crashpad_holders(snapshot: dict[str, Any]) -> list[int]:
    killed: list[int] = []
    for holder in snapshot.get("holders") or []:
        if not isinstance(holder, dict):
            continue
        pid = int(holder.get("pid") or 0)
        if pid <= 0:
            continue
        if not bool(holder.get("is_crashpad_handler")):
            continue
        if holder.get("main_process_pid") is None or bool(holder.get("main_process_exists")):
            continue
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=8,
            )
            killed.append(pid)
        except Exception:
            continue
    return killed


def _resolve_browser_profile_lock(profile_dir: str) -> dict[str, Any]:
    prelaunch = _browser_profile_lock_snapshot(profile_dir)
    if not _holder_pids(prelaunch):
        return {
            "locked": False,
            "classification": "no_lock",
            "prelaunch_lock_snapshot": prelaunch,
            "lock_recheck_snapshots": [],
            "final_lock_snapshot": prelaunch,
            "cleared_orphan_crashpad_pids": [],
        }

    rechecks: list[dict[str, Any]] = []
    time.sleep(1)
    first_recheck = _browser_profile_lock_snapshot(profile_dir)
    rechecks.append(first_recheck)
    if not _holder_pids(first_recheck):
        return {
            "locked": False,
            "classification": "transient_lock_cleared",
            "prelaunch_lock_snapshot": prelaunch,
            "lock_recheck_snapshots": rechecks,
            "final_lock_snapshot": first_recheck,
            "cleared_orphan_crashpad_pids": [],
            "transient_profile_lock_pid": _holder_pids(prelaunch)[0],
        }

    killed = _terminate_orphan_crashpad_holders(first_recheck)
    if killed:
        time.sleep(1)
        after_kill = _browser_profile_lock_snapshot(profile_dir)
        rechecks.append(after_kill)
        if not _holder_pids(after_kill):
            return {
                "locked": False,
                "classification": "orphan_crashpad_cleared",
                "prelaunch_lock_snapshot": prelaunch,
                "lock_recheck_snapshots": rechecks,
                "final_lock_snapshot": after_kill,
                "cleared_orphan_crashpad_pids": killed,
            }

    time.sleep(2.5)
    final_snapshot = _browser_profile_lock_snapshot(profile_dir)
    rechecks.append(final_snapshot)
    if not _holder_pids(final_snapshot):
        return {
            "locked": False,
            "classification": "transient_lock_cleared",
            "prelaunch_lock_snapshot": prelaunch,
            "lock_recheck_snapshots": rechecks,
            "final_lock_snapshot": final_snapshot,
            "cleared_orphan_crashpad_pids": killed,
            "transient_profile_lock_pid": _holder_pids(prelaunch)[0],
        }

    killed.extend(_terminate_orphan_crashpad_holders(final_snapshot))
    if killed:
        time.sleep(1)
        after_final_kill = _browser_profile_lock_snapshot(profile_dir)
        rechecks.append(after_final_kill)
        if not _holder_pids(after_final_kill):
            return {
                "locked": False,
                "classification": "orphan_crashpad_cleared",
                "prelaunch_lock_snapshot": prelaunch,
                "lock_recheck_snapshots": rechecks,
                "final_lock_snapshot": after_final_kill,
                "cleared_orphan_crashpad_pids": sorted(set(killed)),
            }
        final_snapshot = after_final_kill

    return {
        "locked": True,
        "classification": "stable_lock",
        "prelaunch_lock_snapshot": prelaunch,
        "lock_recheck_snapshots": rechecks,
        "final_lock_snapshot": final_snapshot,
        "cleared_orphan_crashpad_pids": sorted(set(killed)),
    }


def _browser_tab_failure_result(
    *,
    opened: bool,
    run_attempted: bool,
    confirmation: dict[str, Any],
    screenshots: dict[str, str],
    warnings: list[str],
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    failure_reason: str,
    url_source: str = "",
    url_used: str = "",
    github_fallback_used: bool = False,
    config_missing_drive_notebook_url: bool = False,
    original_notebook_url: str = "",
    normalized_colab_url: str = "",
    normalization_applied: bool = False,
) -> dict[str, Any]:
    if not any(str(item.get("kind") or "") == "browser_tab_autorun_failed" for item in events if isinstance(item, dict)):
        events.append(autorun_event("browser_tab_autorun_failed", failure_step=failure_reason, confirmation=confirmation))
    if not any(str(item.get("kind") or "") == "manual_run_required" for item in events if isinstance(item, dict)):
        events.append(autorun_event("manual_run_required", reason=failure_reason))
    summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
    return {
        "opened": opened,
        "run_attempted": run_attempted,
        "worker_started_detected": False,
        "confirmation": confirmation,
        "screenshots": screenshots,
        "warnings": warnings,
        "attempts": attempts,
        "prompt_attempts": [],
        "events": events,
        "autorun_mode": "browser-tab",
        "autorun_summary": summary,
        "failure_reason": failure_reason,
        "url_source": url_source,
        "url_used": url_used,
        "original_notebook_url": original_notebook_url or url_used,
        "normalized_colab_url": normalized_colab_url or url_used,
        "normalization_applied": bool(normalization_applied),
        "github_fallback_used": bool(github_fallback_used),
        "config_missing_drive_notebook_url": bool(config_missing_drive_notebook_url),
    }


def _is_recoverable_goto_error(exc: BaseException) -> bool:
    text = repr(exc).lower()
    return any(
        marker in text
        for marker in (
            "targetclosederror",
            "target page, context or browser has been closed",
            "page.goto: net::err_connection_closed",
            "page.goto: net::err_connection_reset",
            "page.goto: net::err_aborted",
            "browser has been closed",
        )
    )


def _open_colab_page_in_persistent_context(
    context: Any,
    notebook_url: str,
    events: list[dict[str, Any]],
    *,
    worker: WorkerConfig,
    story_id: str,
    url_source: str,
    stage_report: Any | None = None,
    open_attempt: int = 1,
    debug_dir: Path | None = None,
    screenshots: dict[str, str] | None = None,
) -> Any:
    last_error: BaseException | None = None
    strategies = ["existing_or_new_page", "new_page"]
    for strategy in strategies:
        try:
            if stage_report:
                stage_report("before_new_page", url=notebook_url, url_source=url_source, extra={"open_attempt": open_attempt, "strategy": strategy})
            if strategy == "existing_or_new_page" and context.pages:
                page = context.pages[-1]
                page_created = False
            else:
                page = context.new_page()
                page_created = True
            _attach_page_debug_events(page, events)
            if stage_report:
                stage_report(
                    "after_new_page",
                    url=notebook_url,
                    url_source=url_source,
                    extra={"open_attempt": open_attempt, "strategy": strategy, "page_created": page_created, "diagnostics": _page_diagnostics(page, context, include_body=False)},
                )
            events.append(
                autorun_event(
                    "page_goto_attempted",
                    open_attempt=open_attempt,
                    strategy=strategy,
                    notebook_url=notebook_url,
                    page_closed=_safe_page_is_closed(page),
                    context_pages=_context_pages_snapshot(context),
                )
            )
            _save_playwright_screenshot(page, debug_dir, screenshots, f"before_goto_attempt_{open_attempt}_{strategy}", events)
            if stage_report:
                stage_report("before_page_goto", url=notebook_url, url_source=url_source, extra={"open_attempt": open_attempt, "strategy": strategy})
            events.append(
                autorun_event(
                    "page_goto_started",
                    open_attempt=open_attempt,
                    strategy=strategy,
                    notebook_url=notebook_url,
                    wait_until="commit",
                    timeout_ms=15000,
                )
            )
            response_status = None
            goto_exception = ""
            wait_until_used = "commit"
            try:
                try:
                    response = page.goto(notebook_url, wait_until="commit", timeout=15000)
                    events.append(autorun_event("page_goto_commit_ok", current_url=page.url, open_attempt=open_attempt, strategy=strategy))
                except Exception as commit_exc:
                    commit_error = repr(commit_exc)
                    if "wait_until" not in commit_error.lower() and "expected one of" not in commit_error.lower():
                        raise
                    events.append(autorun_event("page_goto_commit_unsupported", error=commit_error))
                    events.append(
                        autorun_event(
                            "page_goto_started",
                            open_attempt=open_attempt,
                            strategy=strategy,
                            notebook_url=notebook_url,
                            wait_until="domcontentloaded",
                            timeout_ms=25000,
                        )
                    )
                    wait_until_used = "domcontentloaded"
                    response = page.goto(notebook_url, wait_until="domcontentloaded", timeout=25000)
                    events.append(autorun_event("page_goto_domcontentloaded", current_url=page.url, open_attempt=open_attempt, strategy=strategy))
                try:
                    response_status = response.status if response is not None else None
                except Exception:
                    response_status = None
                events.append(
                    autorun_event(
                        "colab_page_goto_completed",
                        page_url=page.url,
                        open_attempt=open_attempt,
                        strategy=strategy,
                        response_status=response_status,
                    )
                )
            except Exception as goto_exc:
                goto_exception = repr(goto_exc)
                diagnostics = _page_diagnostics(page, context, include_body=True)
                _save_playwright_screenshot(page, debug_dir, screenshots, f"after_goto_timeout_attempt_{open_attempt}_{strategy}", events)
                if "timeout" in goto_exception.lower() and not diagnostics.get("page_closed") and "colab.research.google.com" in str(diagnostics.get("page_url") or "").lower():
                    events.append(
                        autorun_event(
                            "drive_colab_goto_partial_open",
                            page_url=diagnostics.get("page_url", ""),
                            open_attempt=open_attempt,
                            strategy=strategy,
                            error=goto_exception,
                            diagnostics=diagnostics,
                        )
                    )
                else:
                    failure_kind = "page_goto_timeout" if "timeout" in goto_exception.lower() else "page_goto_failed"
                    events.append(
                        autorun_event(
                            failure_kind,
                            open_attempt=open_attempt,
                            strategy=strategy,
                            notebook_url=notebook_url,
                            error=goto_exception,
                            diagnostics=diagnostics,
                        )
                    )
                    raise
            if stage_report:
                stage_report(
                    "after_page_goto",
                    url=notebook_url,
                    url_source=url_source,
                    exception=goto_exception,
                    extra={
                        "open_attempt": open_attempt,
                        "strategy": strategy,
                        "response_status": response_status,
                        "wait_until_used": wait_until_used,
                        "diagnostics": _page_diagnostics(page, context, include_body=True),
                    },
                )
            events.append(autorun_event("colab_page_opened", page_url=page.url, open_attempt=open_attempt, strategy=strategy, response_status=response_status))
            _save_playwright_screenshot(page, debug_dir, screenshots, f"colab_page_opened_attempt_{open_attempt}_{strategy}", events)
            if stage_report:
                stage_report("before_wait_colab_ready", url=notebook_url, url_source=url_source, extra={"open_attempt": open_attempt, "strategy": strategy})
            if not _wait_for_colab_ui_ready(page, events, timeout_seconds=25):
                diagnostics = _page_diagnostics(page, context, include_body=True)
                _save_playwright_screenshot(page, debug_dir, screenshots, f"colab_ui_ready_timeout_attempt_{open_attempt}_{strategy}", events)
                if stage_report:
                    stage_report(
                        "after_wait_colab_ready",
                        url=notebook_url,
                        url_source=url_source,
                        exception="colab_readiness_timeout",
                        failure_reason="colab_readiness_timeout",
                        extra={"open_attempt": open_attempt, "strategy": strategy, "diagnostics": diagnostics},
                    )
                raise RuntimeError("colab_readiness_timeout")
            if stage_report:
                stage_report(
                    "after_wait_colab_ready",
                    url=notebook_url,
                    url_source=url_source,
                    extra={"open_attempt": open_attempt, "strategy": strategy, "diagnostics": _page_diagnostics(page, context, include_body=False)},
                )
            events.append(autorun_event("colab_loaded", page_url=page.url, open_attempt=open_attempt, strategy=strategy))
            return page
        except Exception as exc:
            last_error = exc
            try:
                snapshot = _context_pages_snapshot(context)
            except Exception as snapshot_exc:
                snapshot = {"ok": False, "count": 0, "pages": [], "error": repr(snapshot_exc)}
            events.append(
                autorun_event(
                    "page_goto_failed",
                    open_attempt=open_attempt,
                    strategy=strategy,
                    notebook_url=notebook_url,
                    error=repr(exc),
                    recoverable=_is_recoverable_goto_error(exc),
                    context_pages=snapshot,
                )
            )
            try:
                if "page" in locals() and not _safe_page_is_closed(page):
                    _save_playwright_screenshot(page, debug_dir, screenshots, f"page_goto_failed_attempt_{open_attempt}_{strategy}", events)
            except Exception:
                pass
            try:
                if "page" in locals() and not _safe_page_is_closed(page):
                    page.close()
                    events.append(autorun_event("page_closed_after_goto_failed", open_attempt=open_attempt, strategy=strategy))
            except Exception as close_exc:
                events.append(autorun_event("page_close_after_goto_failed_failed", open_attempt=open_attempt, strategy=strategy, error=repr(close_exc)))
            if strategy == "existing_or_new_page":
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("No Colab page open strategy attempted")


def _terminate_profile_holders_for_retry(profile_dir: str, events: list[dict[str, Any]], *, stage: str) -> dict[str, Any]:
    before = _browser_profile_lock_snapshot(profile_dir)
    killed: list[int] = []
    for holder in before.get("holders") or []:
        if not isinstance(holder, dict):
            continue
        pid = int(holder.get("pid") or 0)
        if pid <= 0:
            continue
        try:
            proc = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=8,
            )
            if proc.returncode == 0:
                killed.append(pid)
        except Exception:
            continue
    if killed:
        time.sleep(1)
    after = _browser_profile_lock_snapshot(profile_dir)
    result = {"stage": stage, "holders_before": before.get("holders") or [], "killed": killed, "holders_after": after.get("holders") or []}
    events.append(autorun_event("browser_profile_holders_cleanup_after_goto_failure", **result))
    return result


def _is_github_colab_url(url: str) -> bool:
    return "/github/" in str(url or "").lower()


def _normalize_colab_notebook_url(url: str) -> dict[str, Any]:
    original = str(url or "").strip()
    if not original:
        return {"original_notebook_url": "", "normalized_colab_url": "", "normalization_applied": False, "file_id": ""}
    direct_match = re.search(r"colab\.research\.google\.com/drive/([^/?#]+)", original, re.IGNORECASE)
    if direct_match:
        file_id = direct_match.group(1)
        normalized = f"https://colab.research.google.com/drive/{file_id}"
        return {
            "original_notebook_url": original,
            "normalized_colab_url": normalized,
            "normalization_applied": normalized != original,
            "file_id": file_id,
        }
    patterns = [
        r"drive\.google\.com/file/d/([^/?#]+)",
        r"[?&]id=([^&#]+)",
        r"/open\?id=([^&#]+)",
        r"/uc\?id=([^&#]+)",
    ]
    file_id = ""
    for pattern in patterns:
        match = re.search(pattern, original, re.IGNORECASE)
        if match:
            file_id = match.group(1)
            break
    if not file_id and re.fullmatch(r"[A-Za-z0-9_-]{20,}", original):
        file_id = original
    if file_id:
        return {
            "original_notebook_url": original,
            "normalized_colab_url": f"https://colab.research.google.com/drive/{file_id}",
            "normalization_applied": True,
            "file_id": file_id,
        }
    return {
        "original_notebook_url": original,
        "normalized_colab_url": original,
        "normalization_applied": False,
        "file_id": "",
    }


def _notebook_url_candidates(
    worker: WorkerConfig,
    notebook_url: str,
    events: list[dict[str, Any]],
    *,
    allow_github_fallback: bool = False,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    drive_url = str(getattr(worker, "drive_notebook_url", "") or "").strip()
    config_missing_drive_notebook_url = not bool(drive_url)

    if drive_url:
        normalized = _normalize_colab_notebook_url(drive_url)
        normalized_url = str(normalized.get("normalized_colab_url") or drive_url)
        candidates.append({"url": normalized_url, "source": "drive_notebook_url", **normalized})
        seen_urls.add(normalized_url)

    github_urls: list[str] = []
    for url in [notebook_url, getattr(worker, "notebook_url", "")]:
        text = str(url or "").strip()
        if text and text not in seen_urls and text not in github_urls:
            github_urls.append(text)
    if allow_github_fallback:
        for url in github_urls:
            normalized = _normalize_colab_notebook_url(url)
            normalized_url = str(normalized.get("normalized_colab_url") or url)
            if normalized_url in seen_urls:
                continue
            source = "github_fallback" if _is_github_colab_url(url) else "notebook_url"
            candidates.append({"url": normalized_url, "source": source, **normalized})
            seen_urls.add(normalized_url)

    events.append(
        autorun_event(
            "notebook_url_candidates_resolved",
            primary_url=notebook_url,
            drive_notebook_url=drive_url,
            candidates=candidates,
            allow_github_fallback=bool(allow_github_fallback),
            github_fallback_available=bool(github_urls),
            config_missing_drive_notebook_url=config_missing_drive_notebook_url,
            production_uses_github_url=bool(candidates and _is_github_colab_url(candidates[0].get("url", ""))),
            drive_url_available=bool(drive_url),
        )
    )
    return candidates


def _safe_page_is_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


def _context_pages_snapshot(context: Any) -> dict[str, Any]:
    try:
        pages = list(context.pages)
    except Exception as exc:
        return {"ok": False, "count": 0, "pages": [], "error": repr(exc)}
    page_rows: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        closed = _safe_page_is_closed(page)
        try:
            url = page.url if not closed else ""
        except Exception:
            url = ""
        try:
            title = page.title(timeout=1000) if not closed else ""
        except Exception:
            title = ""
        page_rows.append({"index": index, "closed": closed, "url": url, "title": title})
    return {"ok": True, "count": len(pages), "pages": page_rows}


def _page_action_snapshot(page: Any, stage: str) -> dict[str, Any]:
    closed = _safe_page_is_closed(page)
    try:
        url = page.url if not closed else ""
    except Exception:
        url = ""
    try:
        title = page.title(timeout=1000) if not closed else ""
    except Exception:
        title = ""
    try:
        context_snapshot = _context_pages_snapshot(page.context)
    except Exception as exc:
        context_snapshot = {"ok": False, "count": 0, "pages": [], "error": repr(exc)}
    return {
        "stage": stage,
        "page_closed": closed,
        "page_url": url,
        "page_title": title,
        "context_pages_count": context_snapshot.get("count", 0),
        "context_pages": context_snapshot.get("pages", []),
        "context_ok": context_snapshot.get("ok", False),
        "context_error": context_snapshot.get("error", ""),
    }


def _attach_browser_lifecycle_diagnostics(context: Any, page: Any, events: list[dict[str, Any]]) -> None:
    try:
        page.on("close", lambda *args: events.append(autorun_event("page_close", page_url=getattr(page, "url", ""))))
    except Exception:
        pass
    try:
        context.on("close", lambda *args: events.append(autorun_event("context_close")))
    except Exception:
        pass
    try:
        browser = context.browser
        if browser is not None:
            browser.on("disconnected", lambda *args: events.append(autorun_event("browser_disconnected")))
    except Exception:
        pass


def _select_live_colab_page(context: Any, current_page: Any, events: list[dict[str, Any]], stage: str) -> Any | None:
    snapshot = _page_action_snapshot(current_page, stage)
    events.append(autorun_event("browser_page_lifecycle_snapshot", **snapshot))
    if not snapshot.get("context_ok", False):
        events.append(autorun_event("browser_context_closed_before_runtime_start", stage=stage, snapshot=snapshot))
        return None

    try:
        if not _safe_page_is_closed(current_page) and "colab.research.google.com" in (current_page.url or "").lower():
            return current_page
    except Exception:
        pass

    try:
        pages = list(context.pages)
    except Exception as exc:
        events.append(autorun_event("browser_context_closed_before_runtime_start", stage=stage, error=repr(exc)))
        return None

    for candidate in reversed(pages):
        if _safe_page_is_closed(candidate):
            continue
        try:
            url = candidate.url or ""
        except Exception:
            url = ""
        if "colab.research.google.com" not in url.lower():
            continue
        try:
            candidate.bring_to_front()
        except Exception:
            pass
        events.append(
            autorun_event(
                "colab_page_reselected",
                stage=stage,
                page_url=url,
                context_pages_count=len(pages),
            )
        )
        return candidate

    events.append(autorun_event("browser_page_closed_before_runtime_start", stage=stage, snapshot=snapshot))
    return None


def _operate_browser_tab_page(
    *,
    page: Any,
    worker: WorkerConfig,
    debug_dir: Path,
    wait_for_run_start_seconds: int,
    events: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    screenshots: dict[str, str],
    stage_report: Any | None = None,
    url_source: str = "",
    url_used: str = "",
) -> tuple[bool, dict[str, Any], str]:
    context = page.context
    _attach_browser_lifecycle_diagnostics(context, page, events)
    live_page = _select_live_colab_page(context, page, events, "before_run_all_click")
    if live_page is None:
        confirmation = {"attempted": True, "ok": False, "reason": "browser_page_closed_before_runtime_start"}
        attempts.append({"method": "runtime_run_all_menu", "run_all_clicked": False, "ctrl_f9_sent": False, "confirmation": confirmation})
        return False, confirmation, "browser_page_closed_before_runtime_start"
    page = live_page

    try:
        page.bring_to_front()
    except Exception:
        pass

    debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = debug_dir / "browser_tab_after_load.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=False)
        screenshots["browser_tab_after_load"] = str(screenshot_path)
    except Exception:
        pass

    try:
        if stage_report:
            stage_report("before_trust_modal_check", url=url_used or page.url, url_source=url_source, extra={"diagnostics": _page_diagnostics(page, context, include_body=True)})
        prompt_state = _handle_permission_prompts_browser_tab(page, events, debug_dir=debug_dir, screenshots=screenshots)
        if stage_report:
            stage_report(
                "after_trust_modal_check",
                url=url_used or page.url,
                url_source=url_source,
                extra={"prompt_state": prompt_state, "diagnostics": _page_diagnostics(page, context, include_body=True)},
            )
        if prompt_state.get("human_blocker"):
            confirmation = {
                "attempted": True,
                "ok": False,
                "reason": "external_human_step_required",
                "human_blocker": True,
                "human_blocker_text": prompt_state.get("human_blocker_text") or "",
            }
            attempts.append({"method": "permission_prompts", "run_all_clicked": False, "ctrl_f9_sent": False, "confirmation": confirmation})
            return False, confirmation, "external_human_step_required"
    except Exception as exc:
        if "closed" in repr(exc).lower():
            confirmation = {"attempted": True, "ok": False, "reason": "browser_page_closed_before_runtime_start", "error": repr(exc)}
            attempts.append({"method": "permission_prompts", "run_all_clicked": False, "ctrl_f9_sent": False, "confirmation": confirmation})
            events.append(autorun_event("browser_page_closed_before_runtime_start", stage="before_run_all_click", error=repr(exc)))
            return False, confirmation, "browser_page_closed_before_runtime_start"
        raise

    before_run_path = debug_dir / "browser_tab_before_run_all.png"
    try:
        page.screenshot(path=str(before_run_path), full_page=False)
        screenshots["browser_tab_before_run_all"] = str(before_run_path)
    except Exception:
        pass

    run_all_clicked = False
    live_page = _select_live_colab_page(context, page, events, "before_run_all_click")
    if live_page is None:
        confirmation = {"attempted": True, "ok": False, "reason": "browser_page_closed_before_runtime_start"}
        attempts.append({"method": "runtime_run_all_menu", "run_all_clicked": False, "ctrl_f9_sent": False, "confirmation": confirmation})
        return False, confirmation, "browser_page_closed_before_runtime_start"
    page = live_page
    try:
        if stage_report:
            stage_report("before_run_all", url=url_used or page.url, url_source=url_source, extra={"diagnostics": _page_diagnostics(page, context, include_body=False)})
        run_all_clicked = _attempt_runtime_run_all_browser_tab(page, events)
        if stage_report:
            stage_report(
                "after_run_all",
                url=url_used or page.url,
                url_source=url_source,
                extra={"run_all_clicked": run_all_clicked, "diagnostics": _page_diagnostics(page, context, include_body=True)},
            )
    except Exception as exc:
        error_text = repr(exc)
        if stage_report:
            stage_report(
                "after_run_all",
                url=url_used or page.url,
                url_source=url_source,
                exception=error_text,
                failure_reason="run_all_action_timeout" if "timeout" in error_text.lower() else "run_all_action_failed",
                extra={"diagnostics": _page_diagnostics(page, context, include_body=True)},
            )
        if "closed" in error_text.lower():
            events.append(autorun_event("browser_page_closed_before_runtime_start", stage="before_run_all_click", error=error_text))
            confirmation = {"attempted": True, "ok": False, "reason": "browser_page_closed_before_runtime_start", "error": error_text}
            attempts.append({"method": "runtime_run_all_menu", "run_all_clicked": False, "ctrl_f9_sent": False, "confirmation": confirmation})
            return False, confirmation, "browser_page_closed_before_runtime_start"
        failure_kind = "run_all_action_timeout" if "timeout" in error_text.lower() else "run_all_action_failed"
        events.append(autorun_event(failure_kind, error=error_text))
        run_all_clicked = False

    ctrl_f9_sent = False
    if not run_all_clicked:
        live_page = _select_live_colab_page(context, page, events, "before_ctrl_f9")
        if live_page is None:
            confirmation = {"attempted": True, "ok": False, "reason": "browser_page_closed_before_runtime_start"}
            attempts.append({"method": "ctrl_f9", "run_all_clicked": False, "ctrl_f9_sent": False, "confirmation": confirmation})
            return False, confirmation, "browser_page_closed_before_runtime_start"
        page = live_page
        events.append(autorun_event("ctrl_f9_attempted", stage="before_ctrl_f9"))
        try:
            page.keyboard.press("Control+F9")
            ctrl_f9_sent = True
            events.append(autorun_event("ctrl_f9_sent", stage="after_ctrl_f9"))
            if stage_report:
                stage_report(
                    "after_run_all",
                    url=url_used or page.url,
                    url_source=url_source,
                    extra={
                        "run_all_clicked": run_all_clicked,
                        "ctrl_f9_sent": ctrl_f9_sent,
                        "fallback": "ctrl_f9",
                        "diagnostics": _page_diagnostics(page, context, include_body=False),
                    },
                )
        except Exception as exc:
            error_text = repr(exc)
            snapshot = _page_action_snapshot(page, "during_ctrl_f9")
            events.append(autorun_event("browser_page_lifecycle_snapshot", **snapshot))
            if "closed" in error_text.lower():
                reason = "browser_context_closed_before_runtime_start" if not snapshot.get("context_ok", False) else "browser_page_closed_before_runtime_start"
                events.append(autorun_event(reason, stage="during_ctrl_f9", error=error_text, snapshot=snapshot))
                confirmation = {"attempted": True, "ok": False, "reason": reason, "error": error_text}
                attempts.append({"method": "ctrl_f9", "run_all_clicked": run_all_clicked, "ctrl_f9_sent": False, "confirmation": confirmation})
                return True, confirmation, reason
            try:
                page.evaluate("() => { window.focus(); document.body && document.body.focus && document.body.focus(); }")
                page.keyboard.press("Control+F9")
                ctrl_f9_sent = True
                events.append(autorun_event("ctrl_f9_sent", stage="after_ctrl_f9", strategy="js_focus_keyboard"))
                if stage_report:
                    stage_report(
                        "after_run_all",
                        url=url_used or page.url,
                        url_source=url_source,
                        extra={
                            "run_all_clicked": run_all_clicked,
                            "ctrl_f9_sent": ctrl_f9_sent,
                            "fallback": "ctrl_f9_js_focus_keyboard",
                            "diagnostics": _page_diagnostics(page, context, include_body=False),
                        },
                    )
            except Exception as fallback_exc:
                fallback_error = repr(fallback_exc)
                events.append(
                    autorun_event(
                        "run_all_action_failed",
                        stage="during_ctrl_f9",
                        error=error_text,
                        fallback_error=fallback_error,
                        snapshot=snapshot,
                    )
                )
                confirmation = {"attempted": True, "ok": False, "reason": "run_all_action_failed", "error": error_text, "fallback_error": fallback_error}
                attempts.append({"method": "ctrl_f9", "run_all_clicked": run_all_clicked, "ctrl_f9_sent": False, "confirmation": confirmation})
                return True, confirmation, "run_all_action_failed"
        time.sleep(1)

    after_run_path = debug_dir / "browser_tab_after_run_all_action.png"
    try:
        page.screenshot(path=str(after_run_path), full_page=False)
        screenshots["browser_tab_after_run_all_action"] = str(after_run_path)
    except Exception:
        pass

    events.append(
        autorun_event(
            "cell_start_attempted",
            method="ctrl_f9" if ctrl_f9_sent else "runtime_run_all_menu",
            run_all_clicked=run_all_clicked,
            ctrl_f9_sent=ctrl_f9_sent,
        )
    )

    live_page = _select_live_colab_page(context, page, events, "after_ctrl_f9" if ctrl_f9_sent else "after_run_all_click")
    if live_page is None:
        confirmation = {"attempted": True, "ok": False, "reason": "browser_page_closed_before_runtime_start"}
        attempts.append(
            {
                "method": "ctrl_f9" if ctrl_f9_sent else "runtime_run_all_menu",
                "run_all_clicked": run_all_clicked,
                "ctrl_f9_sent": ctrl_f9_sent,
                "confirmation": confirmation,
            }
        )
        return True, confirmation, "browser_page_closed_before_runtime_start"
    page = live_page

    confirmation = _wait_for_browser_tab_worker_output(
        page,
        worker.email,
        max(20, int(wait_for_run_start_seconds)),
        events,
    )
    attempts.append(
        {
            "method": "ctrl_f9" if ctrl_f9_sent else "runtime_run_all_menu",
            "strategy": "browser_tab",
            "run_all_clicked": run_all_clicked,
            "ctrl_f9_sent": ctrl_f9_sent,
            "confirmation": confirmation,
        }
    )

    if str(confirmation.get("reason") or "") in {
        "browser_page_closed_before_runtime_start",
        "browser_context_closed_before_runtime_start",
        "browser_disconnected_before_runtime_start",
        "run_all_action_failed",
    }:
        failure_reason = str(confirmation.get("reason"))
    elif confirmation.get("drive_permission_required") and not confirmation.get("drive_permission_handled"):
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
    return True, confirmation, failure_reason


def _run_browser_tab_cdp_attach(
    *,
    playwright: Any,
    worker: WorkerConfig,
    browser_exe: Path,
    notebook_url: str,
    story_id: str,
    debug_dir: Path,
    wait_after_open_seconds: int,
    wait_for_run_start_seconds: int,
    events: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    screenshots: dict[str, str],
    warnings: list[str],
    stage_report: Any | None = None,
    url_source: str = "",
) -> dict[str, Any]:
    backend = "playwright_cdp_attach"
    url_meta = _normalize_colab_notebook_url(notebook_url)
    normalized_url = str(url_meta.get("normalized_colab_url") or notebook_url)
    port = find_free_port()
    devtools_url = f"http://127.0.0.1:{port}"
    args = [
        str(browser_exe),
        f"--user-data-dir={worker.profile_dir}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-features=UseSkiaRenderer,Vulkan",
        "--new-window",
        "about:blank",
    ]
    events.append(
        autorun_event(
            "browser_backend_selected",
            backend=backend,
            browser_profile_dir=worker.profile_dir,
            browser_executable=str(browser_exe),
        )
    )
    events.append(
        autorun_event(
            "gpu_safe_mode_enabled",
            gpu_safe_mode=True,
            backend=backend,
            launch_args=["--disable-gpu", "--disable-software-rasterizer", "--disable-features=UseSkiaRenderer,Vulkan"],
        )
    )
    events.append(
        autorun_event(
            "cdp_port_assigned",
            backend=backend,
            cdp_port=port,
            debugging_port=port,
            devtools_url=devtools_url,
        )
    )
    events.append(
        autorun_event(
            "browser_connection_attempted",
            backend=backend,
            browser_profile_dir=worker.profile_dir,
            browser_executable=str(browser_exe),
            cdp_port=port,
            debugging_port=port,
            devtools_url=devtools_url,
        )
    )
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    events.append(
        autorun_event(
            "notebook_opened",
            strategy=backend,
            cdp_port=port,
            debugging_port=port,
            browser_pid=proc.pid,
            devtools_url=devtools_url,
        )
    )
    if wait_after_open_seconds > 0:
        time.sleep(min(5, int(wait_after_open_seconds)))
    if not wait_for_cdp_port(port, timeout_seconds=45):
        failure_reason = "cdp_port_not_available"
        events.append(
            autorun_event(
                "browser_connection_failed",
                backend=backend,
                reason=failure_reason,
                cdp_port=port,
                debugging_port=port,
                browser_pid=proc.pid,
                devtools_url=devtools_url,
            )
        )
        events.append(autorun_event("browser_connection_failed_reason", reason=failure_reason))
        confirmation = {"attempted": True, "ok": False, "reason": failure_reason}
        return _browser_tab_failure_result(
            opened=True,
            run_attempted=False,
            confirmation=confirmation,
            screenshots=screenshots,
            warnings=[*warnings, "browser_tab_cdp_port_not_available"],
            attempts=attempts,
            events=events,
            failure_reason=failure_reason,
            url_source=url_source,
            url_used=normalized_url,
            original_notebook_url=str(url_meta.get("original_notebook_url") or normalized_url),
            normalized_colab_url=normalized_url,
            normalization_applied=bool(url_meta.get("normalization_applied")),
        )
    events.append(autorun_event("cdp_port_available", cdp_port=port, debugging_port=port, devtools_url=devtools_url))
    browser = playwright.chromium.connect_over_cdp(devtools_url)
    events.append(autorun_event("cdp_connected", cdp_port=port, debugging_port=port, devtools_url=devtools_url))
    events.append(
        autorun_event(
            "browser_connection_ok",
            backend=backend,
            cdp_port=port,
            debugging_port=port,
            browser_pid=proc.pid,
            devtools_url=devtools_url,
        )
    )

    try:
        context = browser.contexts[0]
    except Exception:
        context = None
    if context is None:
        failure_reason = "colab_tab_not_found"
        events.append(autorun_event("colab_tab_not_found", notebook_url=normalized_url, cdp_port=port, browser_pid=proc.pid))
        confirmation = {"attempted": True, "ok": False, "reason": failure_reason}
        return _browser_tab_failure_result(
            opened=True,
            run_attempted=False,
            confirmation=confirmation,
            screenshots=screenshots,
            warnings=[*warnings, "browser_tab_colab_tab_not_found"],
            attempts=attempts,
            events=events,
            failure_reason=failure_reason,
            url_source=url_source,
            url_used=normalized_url,
            original_notebook_url=str(url_meta.get("original_notebook_url") or normalized_url),
            normalized_colab_url=normalized_url,
            normalization_applied=bool(url_meta.get("normalization_applied")),
        )
    try:
        page = _open_colab_page_in_persistent_context(
            context,
            normalized_url,
            events,
            worker=worker,
            story_id=story_id,
            url_source=url_source,
            stage_report=stage_report,
            open_attempt=1,
            debug_dir=debug_dir,
            screenshots=screenshots,
        )
    except Exception as exc:
        failure_reason = _failure_reason_from_open_error(repr(exc))
        events.append(autorun_event(failure_reason, notebook_url=normalized_url, cdp_port=port, browser_pid=proc.pid, error=repr(exc)))
        confirmation = {"attempted": True, "ok": False, "reason": failure_reason, "error": repr(exc)}
        return _browser_tab_failure_result(
            opened=False,
            run_attempted=False,
            confirmation=confirmation,
            screenshots=screenshots,
            warnings=[*warnings, f"browser_tab_cdp_open_failed: {exc!r}"],
            attempts=attempts,
            events=events,
            failure_reason=failure_reason,
            url_source=url_source,
            url_used=normalized_url,
            original_notebook_url=str(url_meta.get("original_notebook_url") or normalized_url),
            normalized_colab_url=normalized_url,
            normalization_applied=bool(url_meta.get("normalization_applied")),
        )

    events.append(autorun_event("colab_tab_found", page_url=page.url, cdp_port=port, browser_pid=proc.pid))

    run_attempted, confirmation, failure_reason = _operate_browser_tab_page(
        page=page,
        worker=worker,
        debug_dir=debug_dir,
        wait_for_run_start_seconds=wait_for_run_start_seconds,
        events=events,
        attempts=attempts,
        screenshots=screenshots,
        stage_report=stage_report,
        url_source=url_source,
        url_used=normalized_url,
    )
    summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
    return {
        "opened": True,
        "run_attempted": run_attempted,
        "worker_started_detected": False,
        "confirmation": confirmation,
        "screenshots": screenshots,
        "warnings": warnings,
        "attempts": attempts,
        "prompt_attempts": [],
        "events": events,
        "autorun_mode": "browser-tab",
        "autorun_summary": summary,
        "failure_reason": failure_reason,
        "url_source": url_source,
        "url_used": normalized_url,
        "original_notebook_url": str(url_meta.get("original_notebook_url") or normalized_url),
        "normalized_colab_url": normalized_url,
        "normalization_applied": bool(url_meta.get("normalization_applied")),
        "gpu_safe_mode": True,
        "github_fallback_used": url_source == "github_fallback",
        "config_missing_drive_notebook_url": False,
    }


def run_browser_tab_autorun(
    *,
    worker: WorkerConfig,
    browser_exe: Path,
    launch_args: list[str],
    notebook_url: str,
    story_id: str,
    debug_dir: Path,
    wait_after_open_seconds: int,
    wait_for_run_start_seconds: int,
    reuse_profile_window: bool,
    dry_run: bool,
    allow_github_fallback: bool = False,
    proxy: ProxyConfig | None = None,
) -> dict[str, Any]:
    backend = "playwright_persistent_context"
    autorun_started_at = utc_now()
    events: list[dict[str, Any]] = [
        autorun_event(
            "browser_backend_selected",
            backend=backend,
            browser_profile_dir=worker.profile_dir,
            browser_executable=str(browser_exe),
        ),
        autorun_event(
            "browser_tab_autorun_started",
            backend=backend,
            autorun_mode="browser-tab",
            autorun_started_at=autorun_started_at,
            browser_profile_dir=worker.profile_dir,
            browser_executable=str(browser_exe),
        ),
    ]
    attempts: list[dict[str, Any]] = []
    screenshots: dict[str, str] = {}
    warnings: list[str] = []
    failure_reason = ""
    initial_url_meta = _normalize_colab_notebook_url(str(getattr(worker, "drive_notebook_url", "") or "").strip())
    selected_url_source = "drive_notebook_url" if str(initial_url_meta.get("normalized_colab_url") or "").strip() else ""
    selected_url_used = str(initial_url_meta.get("normalized_colab_url") or "").strip()
    selected_url_meta: dict[str, Any] = dict(initial_url_meta)
    github_fallback_used = False
    config_missing_drive_notebook_url = not bool(selected_url_used)

    def stage_report(
        stage: str,
        *,
        url: str = "",
        url_source: str = "",
        exception: str = "",
        failure_reason: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        _launcher_stage_report(
            story_id=story_id,
            worker=worker,
            stage=stage,
            events=events,
            attempts=attempts,
            screenshots=screenshots,
            url=url or selected_url_used or notebook_url,
            url_source=url_source or selected_url_source,
            exception=exception,
            failure_reason=failure_reason,
            extra={**selected_url_meta, **(extra or {})},
        )

    if dry_run:
        url_candidates = _notebook_url_candidates(
            worker,
            notebook_url,
            events,
            allow_github_fallback=allow_github_fallback,
        )
        if url_candidates:
            selected_url_source = str(url_candidates[0].get("source") or "")
            selected_url_used = str(url_candidates[0].get("url") or "")
            selected_url_meta = dict(url_candidates[0])
            github_fallback_used = selected_url_source == "github_fallback"
        events.extend(
            [
                autorun_event(
                    "browser_connection_attempted",
                    backend=backend,
                    dry_run=True,
                    browser_profile_dir=worker.profile_dir,
                    browser_executable=str(browser_exe),
                ),
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
            "gpu_safe_mode": True,
            "url_source": selected_url_source,
            "url_used": selected_url_used,
            "github_fallback_used": github_fallback_used,
            "config_missing_drive_notebook_url": config_missing_drive_notebook_url,
        }

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        failure_reason = "playwright_unavailable"
        events.append(autorun_event("browser_connection_attempted", backend=backend, browser_profile_dir=worker.profile_dir))
        events.append(autorun_event("browser_connection_failed", error=repr(exc)))
        events.append(autorun_event("browser_connection_failed_reason", reason=failure_reason, error=repr(exc)))
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
            "gpu_safe_mode": True,
            "url_source": selected_url_source,
            "url_used": selected_url_used,
            "github_fallback_used": github_fallback_used,
            "config_missing_drive_notebook_url": config_missing_drive_notebook_url,
        }

    profile_dir = str(worker.profile_dir or "")
    context: Any | None = None
    confirmation: dict[str, Any] = {"attempted": True, "ok": False, "reason": "not_attempted"}
    lock_resolution = _resolve_browser_profile_lock(profile_dir)
    events.append(
        autorun_event(
            "browser_profile_lock_checked",
            browser_profile_dir=profile_dir,
            prelaunch_lock_snapshot=lock_resolution.get("prelaunch_lock_snapshot"),
            lock_recheck_snapshots=lock_resolution.get("lock_recheck_snapshots"),
            final_lock_snapshot=lock_resolution.get("final_lock_snapshot"),
            lock_classification=lock_resolution.get("classification"),
            transient_profile_lock_detected=bool(_holder_pids(lock_resolution.get("prelaunch_lock_snapshot") or {})),
            transient_profile_lock_pid=lock_resolution.get("transient_profile_lock_pid"),
            transient_profile_lock_cleared=lock_resolution.get("classification") == "transient_lock_cleared",
            cleared_orphan_crashpad_pids=lock_resolution.get("cleared_orphan_crashpad_pids") or [],
        )
    )
    if lock_resolution.get("classification") in {"transient_lock_cleared", "orphan_crashpad_cleared"}:
        events.append(
            autorun_event(
                str(lock_resolution.get("classification")),
                browser_profile_dir=profile_dir,
                prelaunch_lock_snapshot=lock_resolution.get("prelaunch_lock_snapshot"),
                lock_recheck_snapshots=lock_resolution.get("lock_recheck_snapshots"),
                final_lock_snapshot=lock_resolution.get("final_lock_snapshot"),
                cleared_orphan_crashpad_pids=lock_resolution.get("cleared_orphan_crashpad_pids") or [],
            )
        )
    if bool(lock_resolution.get("locked")):
        failure_reason = "browser_profile_locked"
        final_snapshot = dict(lock_resolution.get("final_lock_snapshot") or {})
        running_profile_pids = _holder_pids(final_snapshot)
        events.append(
            autorun_event(
                "browser_profile_locked",
                browser_profile_dir=profile_dir,
                browser_pids=running_profile_pids,
                prelaunch_lock_snapshot=lock_resolution.get("prelaunch_lock_snapshot"),
                lock_recheck_snapshots=lock_resolution.get("lock_recheck_snapshots"),
                final_lock_snapshot=final_snapshot,
                lock_classification=lock_resolution.get("classification"),
            )
        )
        events.append(
            autorun_event(
                "manual_close_existing_browser_required",
                browser_profile_dir=profile_dir,
                browser_pids=running_profile_pids,
                final_lock_snapshot=final_snapshot,
            )
        )
        events.append(
            autorun_event(
                "browser_connection_failed",
                backend=backend,
                reason=failure_reason,
                browser_profile_dir=profile_dir,
                browser_executable=str(browser_exe),
            )
        )
        events.append(autorun_event("browser_connection_failed_reason", reason=failure_reason))
        confirmation = {
            "attempted": True,
            "ok": False,
            "reason": failure_reason,
            "browser_pids": running_profile_pids,
            "prelaunch_lock_snapshot": lock_resolution.get("prelaunch_lock_snapshot"),
            "lock_recheck_snapshots": lock_resolution.get("lock_recheck_snapshots"),
            "final_lock_snapshot": final_snapshot,
            "lock_classification": lock_resolution.get("classification"),
        }
        return _browser_tab_failure_result(
            opened=False,
            run_attempted=False,
            confirmation=confirmation,
            screenshots=screenshots,
            warnings=["browser_profile_locked"],
            attempts=attempts,
            events=events,
            failure_reason=failure_reason,
        )
    try:
        with sync_playwright() as playwright:
            last_open_error = ""
            url_candidates = _notebook_url_candidates(
                worker,
                notebook_url,
                events,
                allow_github_fallback=allow_github_fallback,
            )
            if not url_candidates:
                failure_reason = "config_missing_drive_notebook_url" if config_missing_drive_notebook_url else "notebook_url_missing"
                confirmation = {
                    "attempted": False,
                    "ok": False,
                    "reason": failure_reason,
                    "allow_github_fallback": bool(allow_github_fallback),
                }
                return _browser_tab_failure_result(
                    opened=False,
                    run_attempted=False,
                    confirmation=confirmation,
                    screenshots=screenshots,
                    warnings=[failure_reason],
                    attempts=attempts,
                    events=events,
                    failure_reason=failure_reason,
                    url_source=selected_url_source,
                    url_used=selected_url_used,
                    github_fallback_used=github_fallback_used,
                    config_missing_drive_notebook_url=config_missing_drive_notebook_url,
                )
            for context_attempt in range(1, 4):
                events.append(
                    autorun_event(
                        "browser_connection_attempted",
                        backend=backend,
                        attempt=context_attempt,
                        browser_profile_dir=profile_dir,
                        browser_executable=str(browser_exe),
                    )
                )
                try:
                    stage_report(
                        "before_launch_persistent_context",
                        url=selected_url_used or notebook_url,
                        url_source=selected_url_source,
                        extra={
                            "attempt": context_attempt,
                            "browser_profile_dir": profile_dir,
                            "browser_executable": str(browser_exe),
                            "proxy_enabled": bool(proxy),
                            "proxy_server": f"{proxy.protocol}://{proxy.host}:{proxy.port}" if proxy else "",
                            "proxy_auth": bool(proxy and (proxy.username or proxy.password)),
                        },
                    )
                    launch_proxy: dict[str, str] | None = None
                    if proxy is not None:
                        launch_proxy = {
                            "server": f"{proxy.protocol}://{proxy.host}:{proxy.port}",
                            "username": proxy.username,
                            "password": proxy.password,
                        }
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=profile_dir,
                        executable_path=str(browser_exe),
                        headless=False,
                        viewport=None,
                        timeout=60000,
                        args=[
                            "--remote-allow-origins=*",
                            "--no-first-run",
                            "--no-default-browser-check",
                            "--disable-popup-blocking",
                            "--disable-gpu",
                            "--disable-software-rasterizer",
                            "--disable-features=UseSkiaRenderer,Vulkan",
                        ],
                        proxy=launch_proxy,
                    )
                    events.append(
                        autorun_event(
                            "gpu_safe_mode_enabled",
                            gpu_safe_mode=True,
                            backend=backend,
                            attempt=context_attempt,
                            launch_args=["--disable-gpu", "--disable-software-rasterizer", "--disable-features=UseSkiaRenderer,Vulkan"],
                        )
                    )
                    try:
                        context.set_default_timeout(10000)
                        context.set_default_navigation_timeout(60000)
                    except Exception:
                        pass
                    try:
                        debug_dir.mkdir(parents=True, exist_ok=True)
                        context.tracing.start(screenshots=True, snapshots=True, sources=False)
                        events.append(autorun_event("playwright_trace_started", attempt=context_attempt))
                    except Exception as trace_exc:
                        events.append(autorun_event("playwright_trace_start_failed", attempt=context_attempt, error=repr(trace_exc)))
                    stage_report(
                        "after_launch_persistent_context",
                        url=selected_url_used or notebook_url,
                        url_source=selected_url_source,
                        extra={"attempt": context_attempt, "context_pages": _context_pages_snapshot(context)},
                    )
                except Exception as exc:
                    profile_locked = _is_browser_profile_locked_error(exc)
                    failure_reason = "browser_profile_locked" if profile_locked else ("launch_context_timeout" if "timeout" in repr(exc).lower() else "browser_connection_failed")
                    stage_report(
                        "after_launch_persistent_context",
                        url=selected_url_used or notebook_url,
                        url_source=selected_url_source,
                        exception=repr(exc),
                        failure_reason=failure_reason,
                        extra={"attempt": context_attempt},
                    )
                    if profile_locked:
                        events.append(autorun_event("browser_profile_locked", browser_profile_dir=profile_dir, error=repr(exc)))
                        events.append(autorun_event("manual_close_existing_browser_required", browser_profile_dir=profile_dir))
                    events.append(
                        autorun_event(
                            "browser_connection_failed",
                            backend=backend,
                            reason=failure_reason,
                            attempt=context_attempt,
                            browser_profile_dir=profile_dir,
                            browser_executable=str(browser_exe),
                            error=repr(exc),
                        )
                    )
                    events.append(autorun_event("browser_connection_failed_reason", reason=failure_reason, error=repr(exc), attempt=context_attempt))
                    if failure_reason == "launch_context_timeout" and context_attempt < 3:
                        _terminate_profile_holders_for_retry(profile_dir, events, stage="launch_context_timeout")
                        events.append(autorun_event("browser_context_launch_retry", attempt=context_attempt + 1, reason=failure_reason))
                        time.sleep(2)
                        continue
                    if failure_reason == "launch_context_timeout" and context_attempt >= 3:
                        _terminate_profile_holders_for_retry(profile_dir, events, stage="launch_context_timeout_before_cdp_fallback")
                        events.append(autorun_event("browser_tab_cdp_attach_fallback_started", reason=failure_reason, attempts=context_attempt))
                        return _run_browser_tab_cdp_attach(
                            playwright=playwright,
                            worker=worker,
                            browser_exe=browser_exe,
                            notebook_url=selected_url_used or notebook_url,
                            story_id=story_id,
                            debug_dir=debug_dir,
                            wait_after_open_seconds=wait_after_open_seconds,
                            wait_for_run_start_seconds=wait_for_run_start_seconds,
                            events=events,
                            attempts=attempts,
                            screenshots=screenshots,
                            warnings=[*warnings, "persistent_context_launch_timeout_cdp_fallback_used"],
                            stage_report=stage_report,
                            url_source=selected_url_source,
                        )
                    confirmation = {"attempted": True, "ok": False, "reason": failure_reason, "error": repr(exc)}
                    return _browser_tab_failure_result(
                        opened=False,
                        run_attempted=False,
                        confirmation=confirmation,
                        screenshots=screenshots,
                        warnings=[f"browser_tab_connection_failed: {exc!r}"],
                        attempts=attempts,
                        events=events,
                        failure_reason=failure_reason,
                        url_source=selected_url_source,
                        url_used=selected_url_used,
                        github_fallback_used=github_fallback_used,
                        config_missing_drive_notebook_url=config_missing_drive_notebook_url,
                        original_notebook_url=str(selected_url_meta.get("original_notebook_url") or selected_url_used),
                        normalized_colab_url=str(selected_url_meta.get("normalized_colab_url") or selected_url_used),
                        normalization_applied=bool(selected_url_meta.get("normalization_applied")),
                    )

                events.append(
                    autorun_event(
                        "browser_launched_by_playwright",
                        backend=backend,
                        attempt=context_attempt,
                        browser_profile_dir=profile_dir,
                        browser_executable=str(browser_exe),
                    )
                )
                events.append(
                    autorun_event(
                        "browser_connection_ok",
                        backend=backend,
                        attempt=context_attempt,
                        browser_profile_dir=profile_dir,
                        browser_executable=str(browser_exe),
                    )
                )

                if wait_after_open_seconds > 0:
                    time.sleep(min(5, int(wait_after_open_seconds)))

                page = None
                for candidate_index, candidate in enumerate(url_candidates, start=1):
                    candidate_url = str(candidate.get("url") or "").strip()
                    candidate_source = str(candidate.get("source") or "notebook_url").strip()
                    try:
                        page = _open_colab_page_in_persistent_context(
                            context,
                            candidate_url,
                            events,
                            worker=worker,
                            story_id=story_id,
                            url_source=candidate_source,
                            stage_report=stage_report,
                            open_attempt=context_attempt,
                            debug_dir=debug_dir,
                            screenshots=screenshots,
                        )
                        notebook_url = candidate_url
                        selected_url_used = candidate_url
                        selected_url_source = candidate_source
                        selected_url_meta = dict(candidate)
                        github_fallback_used = candidate_source == "github_fallback"
                        events.append(
                            autorun_event(
                                "notebook_url_selected",
                                attempt=context_attempt,
                                candidate_index=candidate_index,
                                url_source=selected_url_source,
                                url_used=selected_url_used,
                                github_fallback_used=github_fallback_used,
                            )
                        )
                        break
                    except Exception as exc:
                        last_open_error = repr(exc)
                        open_failure_reason = _failure_reason_from_open_error(last_open_error)
                        events.append(
                            autorun_event(
                                open_failure_reason,
                                attempt=context_attempt,
                                candidate_index=candidate_index,
                                notebook_url=candidate_url,
                                url_source=candidate_source,
                                error=last_open_error,
                                recoverable=_is_recoverable_goto_error(exc),
                            )
                        )
                        if candidate_index < len(url_candidates):
                            continue
                        break

                if page is None:
                    try:
                        if context is not None:
                            try:
                                trace_path = debug_dir / f"playwright_trace_attempt_{context_attempt}_open_failed.zip"
                                context.tracing.stop(path=str(trace_path))
                                screenshots[f"playwright_trace_attempt_{context_attempt}_open_failed"] = str(trace_path)
                                events.append(autorun_event("playwright_trace_saved", attempt=context_attempt, path=str(trace_path)))
                            except Exception as trace_exc:
                                events.append(autorun_event("playwright_trace_save_failed", attempt=context_attempt, error=repr(trace_exc)))
                            context.close()
                            events.append(autorun_event("browser_context_closed_for_retry", attempt=context_attempt, reason="colab_page_open_failed"))
                    except Exception as close_exc:
                        events.append(autorun_event("browser_context_close_for_retry_failed", attempt=context_attempt, error=repr(close_exc)))
                    _terminate_profile_holders_for_retry(profile_dir, events, stage="colab_page_open_failed")
                    context = None
                    if context_attempt < 3:
                        time.sleep(2)
                        continue
                    failure_reason = _failure_reason_from_open_error(last_open_error)
                    confirmation = {"attempted": True, "ok": False, "reason": failure_reason, "error": last_open_error}
                    return _browser_tab_failure_result(
                        opened=False,
                        run_attempted=False,
                        confirmation=confirmation,
                        screenshots=screenshots,
                        warnings=[f"colab_page_open_failed: {last_open_error}"],
                        attempts=attempts,
                        events=events,
                        failure_reason=failure_reason,
                        url_source=selected_url_source,
                        url_used=selected_url_used,
                        github_fallback_used=github_fallback_used,
                        config_missing_drive_notebook_url=config_missing_drive_notebook_url,
                        original_notebook_url=str(selected_url_meta.get("original_notebook_url") or selected_url_used),
                        normalized_colab_url=str(selected_url_meta.get("normalized_colab_url") or selected_url_used),
                        normalization_applied=bool(selected_url_meta.get("normalization_applied")),
                    )

                if "colab.research.google.com" not in (page.url or "").lower():
                    failure_reason = "colab_tab_not_found"
                    events.append(autorun_event("colab_tab_not_found", notebook_url=notebook_url, page_url=page.url))
                    confirmation = {"attempted": True, "ok": False, "reason": failure_reason}
                    return _browser_tab_failure_result(
                        opened=True,
                        run_attempted=False,
                        confirmation=confirmation,
                        screenshots=screenshots,
                        warnings=["browser_tab_colab_page_not_opened"],
                        attempts=attempts,
                        events=events,
                        failure_reason=failure_reason,
                        url_source=selected_url_source,
                        url_used=selected_url_used,
                        github_fallback_used=github_fallback_used,
                        config_missing_drive_notebook_url=config_missing_drive_notebook_url,
                    )

                events.append(autorun_event("colab_tab_found", page_url=page.url))
                run_attempted, confirmation, failure_reason = _operate_browser_tab_page(
                    page=page,
                    worker=worker,
                    debug_dir=debug_dir,
                    wait_for_run_start_seconds=wait_for_run_start_seconds,
                    events=events,
                    attempts=attempts,
                    screenshots=screenshots,
                    stage_report=stage_report,
                    url_source=selected_url_source,
                    url_used=selected_url_used,
                )

                summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
                try:
                    trace_path = debug_dir / f"playwright_trace_attempt_{context_attempt}.zip"
                    context.tracing.stop(path=str(trace_path))
                    screenshots[f"playwright_trace_attempt_{context_attempt}"] = str(trace_path)
                    events.append(autorun_event("playwright_trace_saved", attempt=context_attempt, path=str(trace_path)))
                except Exception as trace_exc:
                    events.append(autorun_event("playwright_trace_save_failed", attempt=context_attempt, error=repr(trace_exc)))
                return {
                    "opened": True,
                    "run_attempted": run_attempted,
                    "worker_started_detected": False,
                    "confirmation": confirmation,
                    "screenshots": screenshots,
                    "warnings": warnings,
                    "attempts": attempts,
                    "prompt_attempts": [],
                    "events": events,
                    "autorun_mode": "browser-tab",
                    "autorun_summary": summary,
                    "failure_reason": failure_reason,
                    "url_source": selected_url_source,
                    "url_used": selected_url_used,
                    "github_fallback_used": github_fallback_used,
                    "config_missing_drive_notebook_url": config_missing_drive_notebook_url,
                }
    except Exception as exc:
        failure_reason = "browser_tab_autorun_exception"
        events.append(autorun_event("browser_connection_failed_reason", reason=failure_reason, error=repr(exc)))
        events.append(autorun_event("browser_tab_autorun_failed", failure_step=failure_reason, error=repr(exc)))
        events.append(autorun_event("manual_run_required", reason=failure_reason, error=repr(exc)))
        confirmation = {"attempted": True, "ok": False, "reason": failure_reason, "error": repr(exc)}
        summary = summarize_browser_tab_autorun(events, confirmation=confirmation, failure_reason=failure_reason)
        return {
            "opened": context is not None,
            "run_attempted": context is not None,
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
            "gpu_safe_mode": True,
            "url_source": selected_url_source,
            "url_used": selected_url_used,
            "original_notebook_url": str(selected_url_meta.get("original_notebook_url") or selected_url_used),
            "normalized_colab_url": str(selected_url_meta.get("normalized_colab_url") or selected_url_used),
            "normalization_applied": bool(selected_url_meta.get("normalization_applied")),
            "github_fallback_used": github_fallback_used,
            "config_missing_drive_notebook_url": config_missing_drive_notebook_url,
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
    allow_github_fallback: bool = False,
    proxy_config_path: str = "",
    proxy_url: str = "",
    proxy_required: bool = False,
    proxy_id: str = "",
    require_fresh_browser_process: bool = False,
    proxy_auth_mode: str = "local-bridge",
    local_bridge_port: int = 0,
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
    auto_run_requested = bool(auto_run)
    # Policy: Colab UI autorun is disabled for local safety.
    auto_run = False
    normalized_autorun_mode = "manual"
    debug_dir = debug_dir_for(story_id, worker.email)
    screenshots: dict[str, str] = {}
    url_resolution_events: list[dict[str, Any]] = []
    url_candidates = _notebook_url_candidates(
        worker,
        worker.notebook_url,
        url_resolution_events,
        allow_github_fallback=allow_github_fallback,
    )
    auto_run_events.extend(url_resolution_events)
    if auto_run_requested:
        warning = "colab_autorun_disabled_policy: notebook opened only, operator must click Runtime -> Run all manually"
        warnings.append(warning)
        auto_run_events.append(
            autorun_event(
                "colab_autorun_disabled_policy",
                requested_auto_run=True,
                forced_auto_run=False,
                forced_autorun_mode="manual",
                reason="manual_colab_ui_policy",
            )
        )
    url_source = str(url_candidates[0].get("source") or "") if url_candidates else ""
    url_used = str(url_candidates[0].get("url") or "") if url_candidates else ""
    original_notebook_url = str(url_candidates[0].get("original_notebook_url") or url_used) if url_candidates else ""
    normalized_colab_url = str(url_candidates[0].get("normalized_colab_url") or url_used) if url_candidates else ""
    normalization_applied = bool(url_candidates[0].get("normalization_applied")) if url_candidates else False
    github_fallback_used = url_source == "github_fallback"
    config_missing_drive_notebook_url = not bool(str(getattr(worker, "drive_notebook_url", "") or "").strip())
    proxy = _resolve_proxy_config(worker=worker, proxy_config_path=proxy_config_path, proxy_url=proxy_url)
    proxy_fields = _proxy_report_fields(proxy)
    if proxy_required and proxy is None:
        errors.append("proxy_required_but_missing")

    if config_missing_drive_notebook_url and not allow_github_fallback:
        errors.append("config_missing_drive_notebook_url")
    elif not url_used:
        errors.append("notebook_url_missing")
    if not worker.profile_dir:
        errors.append("profile_dir_missing_in_config")
    elif not Path(worker.profile_dir).is_dir():
        errors.append(f"profile_dir_missing: {worker.profile_dir}")
    if browser_exe is None:
        errors.append(f"{worker.browser}_executable_not_found")
    proxy_launch_meta: dict[str, Any] = {
        "proxy_auth_extension_loaded": False,
        "proxy_auth_extension_path": "",
        "proxy_auth_mode": _normalize_proxy_auth_mode(proxy_auth_mode, proxy),
        "local_bridge_host": "",
        "local_bridge_port": 0,
        "browser_proxy_arg": "",
        "upstream_proxy_host": "",
        "upstream_proxy_port": 0,
        "upstream_auth": False,
    }
    if browser_exe is not None and url_used and worker.profile_dir:
        args = [
            str(browser_exe),
            f"--user-data-dir={worker.profile_dir}",
        ]
        if not reuse_profile_window:
            args.insert(2, "--new-window")
        try:
            args, proxy_launch_meta = _append_proxy_launch_args(
                args,
                proxy=proxy,
                worker_email=worker.email,
                proxy_required=proxy_required,
                proxy_auth_mode=proxy_auth_mode,
                local_bridge_port=local_bridge_port,
            )
        except Exception as exc:
            errors.append(f"proxy_launch_args_failed: {exc!r}")
        args.append(url_used)
        if proxy_required and not any(str(item).startswith("--proxy-server=") for item in args):
            errors.append("proxy_required_missing_proxy_server_arg")
        normalized_mode = str(proxy_launch_meta.get("proxy_auth_mode") or "")
        if proxy is not None:
            if normalized_mode == "local-bridge":
                lb_port = int(proxy_launch_meta.get("local_bridge_port") or local_bridge_port or 0)
                warnings.append(f"proxy_auth_mode={normalized_mode}")
                warnings.append(f"browser_proxy_arg=http://{LOCAL_BRIDGE_HOST}:{lb_port}")
                warnings.append(f"upstream_proxy={_mask_proxy_auth(proxy)}")
            else:
                warnings.append(f"proxy_enabled_for_browser={_mask_proxy_auth(proxy)}")
        if proxy_required and normalized_mode == "local-bridge":
            proxy_server_args = [str(item) for item in args if str(item).startswith("--proxy-server=")]
            if not proxy_server_args or not all("127.0.0.1" in item for item in proxy_server_args):
                errors.append("proxy_required_local_bridge_must_use_127.0.0.1")
            if any(str(item).startswith("--load-extension=") for item in args):
                errors.append("proxy_local_bridge_must_not_use_load_extension")
        elif proxy_required and proxy is not None and (proxy.username or proxy.password) and normalized_mode == "extension":
            if not proxy_launch_meta.get("proxy_auth_extension_loaded"):
                errors.append("proxy_required_missing_proxy_auth_extension")
            elif not any(str(item).startswith("--load-extension=") for item in args):
                errors.append("proxy_required_missing_load_extension_arg")

    if not dry_run and not errors:
        try:
            lock_resolution = _resolve_browser_profile_lock(worker.profile_dir)
            if bool(lock_resolution.get("locked")):
                lock_pids = _holder_pids(lock_resolution.get("final_lock_snapshot") or {})
                if require_fresh_browser_process:
                    errors.append(f"browser_profile_already_open:{worker.profile_dir}")
                    errors.append(f"browser_profile_open_pids:{','.join(str(pid) for pid in lock_pids)}")
                else:
                    warnings.append(
                        f"browser_profile_already_open_warning:{worker.profile_dir}; existing process may ignore new proxy args; pids={','.join(str(pid) for pid in lock_pids)}"
                    )
            if errors:
                raise OSError(";".join(errors))
            launched_for_autorun = False
            if auto_run and normalized_autorun_mode == "browser-tab":
                browser_tab_result = run_browser_tab_autorun(
                    worker=worker,
                    browser_exe=browser_exe,
                    launch_args=args,
                    notebook_url=url_used,
                    story_id=story_id,
                    debug_dir=debug_dir,
                    wait_after_open_seconds=wait_after_open_seconds,
                    wait_for_run_start_seconds=wait_for_run_start_seconds,
                    reuse_profile_window=reuse_profile_window,
                    dry_run=False,
                    allow_github_fallback=allow_github_fallback,
                    proxy=proxy,
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
                url_source = str(browser_tab_result.get("url_source") or url_source)
                url_used = str(browser_tab_result.get("url_used") or url_used)
                original_notebook_url = str(browser_tab_result.get("original_notebook_url") or original_notebook_url)
                normalized_colab_url = str(browser_tab_result.get("normalized_colab_url") or normalized_colab_url)
                normalization_applied = bool(browser_tab_result.get("normalization_applied", normalization_applied))
                github_fallback_used = bool(browser_tab_result.get("github_fallback_used", github_fallback_used))
                config_missing_drive_notebook_url = bool(browser_tab_result.get("config_missing_drive_notebook_url", config_missing_drive_notebook_url))
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
            notebook_url=url_used,
            story_id=story_id,
            debug_dir=debug_dir,
            wait_after_open_seconds=wait_after_open_seconds,
            wait_for_run_start_seconds=wait_for_run_start_seconds,
            reuse_profile_window=reuse_profile_window,
            dry_run=True,
            allow_github_fallback=allow_github_fallback,
            proxy=proxy,
        )
        run_attempted = bool(browser_tab_result.get("run_attempted"))
        auto_run_events.extend(list(browser_tab_result.get("events") or []))
        autorun_summary = dict(browser_tab_result.get("autorun_summary") or {})
        url_source = str(browser_tab_result.get("url_source") or url_source)
        url_used = str(browser_tab_result.get("url_used") or url_used)
        original_notebook_url = str(browser_tab_result.get("original_notebook_url") or original_notebook_url)
        normalized_colab_url = str(browser_tab_result.get("normalized_colab_url") or normalized_colab_url)
        normalization_applied = bool(browser_tab_result.get("normalization_applied", normalization_applied))
        github_fallback_used = bool(browser_tab_result.get("github_fallback_used", github_fallback_used))
        config_missing_drive_notebook_url = bool(browser_tab_result.get("config_missing_drive_notebook_url", config_missing_drive_notebook_url))

    manual_action_required = bool(errors) or (not dry_run and not opened)
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
        elif auto_run_requested:
            reason = "colab_autorun_disabled_policy"
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
        "opened_url": url_used,
        "opened": opened,
        "notebook_path": worker.notebook_path,
        "notebook_url": worker.notebook_url,
        "drive_notebook_url": str(getattr(worker, "drive_notebook_url", "") or ""),
        "original_notebook_url": original_notebook_url,
        "normalized_colab_url": normalized_colab_url,
        "normalization_applied": bool(normalization_applied),
        "url_source": url_source,
        "url_used": url_used,
        "github_fallback_used": bool(github_fallback_used),
        "config_missing_drive_notebook_url": bool(config_missing_drive_notebook_url),
        "allow_github_fallback": bool(allow_github_fallback),
        "require_t4": worker.require_t4,
        "code_injected": True if url_used else False,
        "run_attempted": run_attempted,
        "worker_started_detected": worker_started_detected,
        "auto_run_attempted": False,
        "auto_run_result": {
            "attempted": False,
            "ok": bool(opened) if not dry_run else True,
            "reason": "colab_autorun_disabled_policy",
            "confirmation": run_confirmation,
            "attempts": run_attempts,
            "prompt_attempts": prompt_attempts,
            "events": auto_run_events,
            "autorun_mode": normalized_autorun_mode,
            "autorun_summary": autorun_summary,
            "failure_reason": failure_reason,
            "requested_auto_run": auto_run_requested,
            "forced_auto_run": False,
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
        "proxy_id": proxy_id,
        "proxy_auth_extension_loaded": bool(proxy_launch_meta.get("proxy_auth_extension_loaded")),
        "proxy_auth_extension_path": str(proxy_launch_meta.get("proxy_auth_extension_path") or ""),
        "proxy_auth_mode": str(proxy_launch_meta.get("proxy_auth_mode") or ""),
        "local_bridge_host": str(proxy_launch_meta.get("local_bridge_host") or ""),
        "local_bridge_port": int(proxy_launch_meta.get("local_bridge_port") or 0),
        "browser_proxy_arg": str(proxy_launch_meta.get("browser_proxy_arg") or ""),
        "upstream_proxy_host": str(proxy_launch_meta.get("upstream_proxy_host") or proxy_fields.get("proxy_host") or ""),
        "upstream_proxy_port": int(proxy_launch_meta.get("upstream_proxy_port") or proxy_fields.get("proxy_port") or 0),
        "upstream_auth": bool(proxy_launch_meta.get("upstream_auth")),
        "warnings": warnings,
        "errors": errors,
        **proxy_fields,
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
    parser.add_argument(
        "--allow-github-fallback",
        action="store_true",
        help="Prepared notebooks: allow fallback to notebook_url/GitHub if drive_notebook_url is missing or fails.",
    )
    parser.add_argument(
        "--proxy-config",
        default="",
        help="Path to local proxy config (recommended ignored file). Supports top-level/default/workers[email] mapping.",
    )
    parser.add_argument(
        "--proxy-url",
        default="",
        help="Direct proxy URL, example: http://user:pass@host:port . Avoid using this in shared logs/history.",
    )
    parser.add_argument("--check-proxies-only", action="store_true", help="Validate and print per-worker proxy resolution without opening browsers.")
    parser.add_argument(
        "--check-bridges-only",
        action="store_true",
        help="Start local proxy bridges, run HTTP/HTTPS health-checks through each port, print results, and exit.",
    )
    parser.add_argument(
        "--proxy-auth-mode",
        choices=["local-bridge", "browser-managed", "profile-managed", "extension", "direct"],
        default="local-bridge",
        help="local-bridge: 127.0.0.1 bridge. browser-managed: --proxy-server upstream. profile-managed: proxy from browser profile only.",
    )
    parser.add_argument(
        "--require-fresh-browser-process",
        action="store_true",
        help="Fail if a selected worker profile is already open (proxy args may be ignored by existing browser process).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-bridge-keepalive",
        action="store_true",
        help="Leave local proxy bridges running after launch; caller owns keepalive and shutdown.",
    )
    parser.add_argument(
        "--close-worker-browsers",
        action="store_true",
        help="Close only Yandex browser.exe processes whose CommandLine contains the worker profile_dir, then remove Singleton* lock files for those profiles.",
    )
    parser.add_argument(
        "--window-mode",
        choices=["separate-profiles", "single-window-tabs"],
        default="separate-profiles",
        help="separate-profiles: one browser window/profile per worker (default). single-window-tabs: one window per group with multiple Colab tabs.",
    )
    parser.add_argument(
        "--shared-profile-per-group",
        action="store_true",
        help="Use one shared profile directory per browser group (.browser_profiles/youtube_colab/*_group_tabs). Implied by --window-mode single-window-tabs.",
    )
    parser.add_argument(
        "--tab-account-mode",
        choices=["manual", "authuser"],
        default="manual",
        help="manual: open clean drive notebook URLs; user picks Google account per tab (recommended). authuser: append authuser=email to Colab URL (experimental).",
    )
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
    if bool(getattr(args, "close_worker_browsers", False)):
        return _run_close_worker_browsers(workers)
    if int(args.limit or 0) > 0:
        workers = workers[: int(args.limit)]
    proxy_config_path = str(getattr(args, "proxy_config", "") or "")
    proxy_url = str(getattr(args, "proxy_url", "") or "")
    proxy_enforced = bool(proxy_config_path.strip() or proxy_url.strip())
    proxy_auth_mode_cli = str(getattr(args, "proxy_auth_mode", "local-bridge") or "local-bridge")
    bridge_ports_by_email = _plan_local_bridge_ports(workers)
    proxy_meta_by_email: dict[str, dict[str, Any]] = {}
    proxy_rows: list[dict[str, Any]] = []
    bridge_rows: list[dict[str, Any]] = []
    for worker in workers:
        proxy_meta = _resolve_proxy_with_meta(worker=worker, proxy_config_path=proxy_config_path, proxy_url=proxy_url)
        proxy_meta_by_email[worker.email.lower()] = proxy_meta
        proxy_obj = proxy_meta.get("proxy")
        proxy_status = str(proxy_meta.get("status") or "MISSING_PROXY")
        proxy_rows.append(
            {
                "email": worker.email,
                "group": worker.group,
                "profile_dir": worker.profile_dir,
                "browser": worker.browser,
                "proxy_id": str(proxy_meta.get("proxy_id") or ""),
                "proxy_host": str(getattr(proxy_obj, "host", "") or ""),
                "proxy_port": int(getattr(proxy_obj, "port", 0) or 0),
                "proxy_auth": "yes" if bool(getattr(proxy_obj, "username", "") or getattr(proxy_obj, "password", "")) else "no",
                "status": proxy_status,
                "reason": str(proxy_meta.get("reason") or ""),
            }
        )
        local_port = int(bridge_ports_by_email.get(worker.email.lower(), 0) or 0)
        effective_mode = _normalize_proxy_auth_mode(proxy_auth_mode_cli, proxy_obj if isinstance(proxy_obj, ProxyConfig) else None)
        if effective_mode == "local-bridge" and isinstance(proxy_obj, ProxyConfig):
            upstream = _upstream_proxy_from_config(proxy_obj)
            bridge_status = "OK" if proxy_status == "OK" else proxy_status
            bridge_rows.append(
                bridge_diagnostics_row(
                    email=worker.email,
                    upstream=upstream,
                    local_host=LOCAL_BRIDGE_HOST,
                    local_port=local_port,
                    proxy_auth_mode="local-bridge",
                    status=bridge_status,
                    reason="" if bridge_status == "OK" else str(proxy_meta.get("reason") or ""),
                )
            )
    if bool(getattr(args, "check_proxies_only", False)):
        _print_proxy_table(proxy_rows)
        if bridge_rows:
            print_bridge_diagnostics_table(bridge_rows)
        check_ok = all(str(item.get("status")) == "OK" for item in proxy_rows)
        if bridge_rows:
            check_ok = check_ok and all(str(item.get("status")) == "OK" for item in bridge_rows)
        print(f"proxy_check_ok={check_ok}")
        return 0 if check_ok else 2
    if bool(getattr(args, "check_bridges_only", False)):
        if not proxy_enforced:
            print("status=bridge_check_failed")
            print("ok=False")
            print("reason=proxy_config_required_for_bridge_check")
            return 2
        local_bridge_workers = [
            worker
            for worker in workers
            if _normalize_proxy_auth_mode(
                proxy_auth_mode_cli,
                proxy_meta_by_email.get(worker.email.lower(), {}).get("proxy"),
            )
            == "local-bridge"
            and isinstance(proxy_meta_by_email.get(worker.email.lower(), {}).get("proxy"), ProxyConfig)
        ]
        if not local_bridge_workers:
            print("status=bridge_check_failed")
            print("ok=False")
            print("reason=no_local_bridge_workers_selected")
            return 2
        active_bridges, health_rows, failed_emails = _start_and_verify_local_proxy_bridges(
            local_bridge_workers,
            proxy_meta_by_email=proxy_meta_by_email,
            bridge_ports_by_email=bridge_ports_by_email,
        )
        print_bridge_healthcheck_table(health_rows)
        bridge_ok = bool(health_rows) and not failed_emails and all(str(row.get("status")) == "OK" for row in health_rows)
        print(f"bridge_healthcheck_ok={bridge_ok}")
        print(f"bridges_ready={len(active_bridges)}/{len(local_bridge_workers)}")
        _stop_local_proxy_bridges(active_bridges)
        return 0 if bridge_ok else 2
    if proxy_enforced:
        invalid_proxy_workers = [item for item in proxy_rows if str(item.get("status")) != "OK"]
        if invalid_proxy_workers:
            _print_proxy_table(proxy_rows)
            bad_emails = [str(item.get("email") or "") for item in invalid_proxy_workers]
            print("status=proxy_preflight_failed")
            print("ok=False")
            print(f"reason=proxy_required_for_all_workers")
            print(f"proxy_missing_or_invalid_workers={json.dumps(bad_emails, ensure_ascii=True)}")
            return 2
    if bool(getattr(args, "require_fresh_browser_process", False)):
        opened_profiles: list[str] = []
        for worker in workers:
            lock_resolution = _resolve_browser_profile_lock(worker.profile_dir)
            if bool(lock_resolution.get("locked")):
                opened_profiles.append(worker.email)
        if opened_profiles:
            print("status=fresh_browser_required_failed")
            print("ok=False")
            print("reason=browser_profiles_already_open")
            print(f"opened_profile_workers={json.dumps(opened_profiles, ensure_ascii=True)}")
            return 2
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
    active_local_bridges: list[LocalProxyBridge] = []
    local_bridge_workers: list[WorkerConfig] = []
    bridge_monitor_slots: list[dict[str, Any]] = []
    window_mode = str(getattr(args, "window_mode", "separate-profiles") or "separate-profiles")
    use_tabbed_launch = window_mode == "single-window-tabs"
    if use_tabbed_launch and args.group == "all":
        print("status=tabbed_launch_failed")
        print("ok=False")
        print("reason=single-window-tabs requires --group yandex or chrome, not all")
        return 2
    needs_local_bridge = any(
        _normalize_proxy_auth_mode(
            proxy_auth_mode_cli,
            proxy_meta_by_email.get(worker.email.lower(), {}).get("proxy"),
        )
        == "local-bridge"
        and isinstance(proxy_meta_by_email.get(worker.email.lower(), {}).get("proxy"), ProxyConfig)
        for worker in workers
    )
    if proxy_enforced and needs_local_bridge and not use_tabbed_launch:
        local_bridge_workers = [
            worker
            for worker in workers
            if _normalize_proxy_auth_mode(
                proxy_auth_mode_cli,
                proxy_meta_by_email.get(worker.email.lower(), {}).get("proxy"),
            )
            == "local-bridge"
            and isinstance(proxy_meta_by_email.get(worker.email.lower(), {}).get("proxy"), ProxyConfig)
        ]
        bridge_monitor_slots = _bridge_monitor_slots(local_bridge_workers, bridge_ports_by_email)
        if not bool(args.dry_run):
            active_local_bridges, bridge_health_rows, bridge_errors = _start_and_verify_local_proxy_bridges(
                local_bridge_workers,
                proxy_meta_by_email=proxy_meta_by_email,
                bridge_ports_by_email=bridge_ports_by_email,
            )
            print_bridge_healthcheck_table(bridge_health_rows)
            bridge_ok = bool(bridge_health_rows) and not bridge_errors and all(
                str(row.get("status")) == "OK" for row in bridge_health_rows
            )
            print(f"bridge_healthcheck_ok={bridge_ok}")
            print(f"bridges_ready={len(active_local_bridges)}/{len(local_bridge_workers)}")
            if not bridge_ok:
                print("status=local_bridge_preflight_failed")
                print("ok=False")
                print("reason=local_proxy_bridge_healthcheck_failed")
                print(f"bridge_failed_workers={json.dumps(bridge_errors, ensure_ascii=True)}")
                _stop_local_proxy_bridges(active_local_bridges)
                return 2
        else:
            print("status=local_bridge_dry_run_skipped_live_healthcheck")
            print("hint=run_with_check_bridges_only_before_real_launch")
            if bridge_rows:
                print_bridge_diagnostics_table(bridge_rows)
    if use_tabbed_launch and prepared_notebook_requested:
        from colab_tabbed_group_launch import normalize_tab_account_mode, run_single_window_tabs_launch

        tab_account_mode = normalize_tab_account_mode(str(getattr(args, "tab_account_mode", "manual") or "manual"))
        proxy_auth_mode_cli = _normalize_proxy_auth_mode(str(getattr(args, "proxy_auth_mode", "local-bridge") or "local-bridge"), None)
        tabbed = run_single_window_tabs_launch(
            workers=workers,
            group=str(args.group),
            story_id=str(args.story_id),
            dry_run=bool(args.dry_run),
            proxy_meta_by_email=proxy_meta_by_email,
            proxy_required=proxy_enforced,
            proxy_auth_mode=proxy_auth_mode_cli,
            allow_github_fallback=bool(getattr(args, "allow_github_fallback", False)),
            project_root=PROJECT_ROOT,
            tab_account_mode=tab_account_mode,
        )
        if tabbed.reason == "TABBED_MODE_UNSUPPORTED_DIFFERENT_PROXIES":
            print("TABBED_MODE_UNSUPPORTED_DIFFERENT_PROXIES")
            print("status=tabbed_launch_failed")
            print("ok=False")
            return 2
        results = list(tabbed.results)
        active_local_bridges = list(tabbed.bridges)
        bridge_monitor_slots = list(tabbed.monitor_slots)
        if not tabbed.ok and not args.dry_run:
            _stop_local_proxy_bridges(active_local_bridges)
            print(f"status=tabbed_launch_failed reason={tabbed.reason}")
            print("ok=False")
            return 2
    elif not use_tabbed_launch:
        try:
            for index, worker in enumerate(workers, start=1):
                browser_exe = browser_exes.get(worker.browser)
                if worker.launch_mode == "prepared_notebook_url":
                    worker_proxy_meta = proxy_meta_by_email.get(worker.email.lower(), {})
                    worker_proxy = worker_proxy_meta.get("proxy")
                    worker_proxy_id = str(worker_proxy_meta.get("proxy_id") or "")
                    worker_auth_mode = _normalize_proxy_auth_mode(proxy_auth_mode_cli, worker_proxy if isinstance(worker_proxy, ProxyConfig) else None)
                    worker_bridge_port = int(bridge_ports_by_email.get(worker.email.lower(), 0) or 0)
                    print(f"launch_diagnostic_email={worker.email}")
                    print(f"launch_diagnostic_profile_dir={worker.profile_dir}")
                    print(f"launch_diagnostic_browser_executable={str(browser_exe) if browser_exe else ''}")
                    print(f"launch_diagnostic_proxy_id={worker_proxy_id}")
                    print(f"launch_diagnostic_proxy_auth_mode={worker_auth_mode}")
                    if isinstance(worker_proxy, ProxyConfig):
                        print(f"launch_diagnostic_upstream_proxy_host={worker_proxy.host}")
                        print(f"launch_diagnostic_upstream_proxy_port={worker_proxy.port}")
                        print(
                            "launch_diagnostic_upstream_auth="
                            + ("yes" if (worker_proxy.username or worker_proxy.password) else "no")
                        )
                    if worker_auth_mode == "local-bridge":
                        print(f"launch_diagnostic_local_bridge_host={LOCAL_BRIDGE_HOST}")
                        print(f"launch_diagnostic_local_bridge_port={worker_bridge_port}")
                        print(f"launch_diagnostic_browser_proxy_arg=http://{LOCAL_BRIDGE_HOST}:{worker_bridge_port}")
                    elif isinstance(worker_proxy, ProxyConfig):
                        print(f"launch_diagnostic_proxy_server={worker_proxy.protocol}://{worker_proxy.host}:{worker_proxy.port}")
                        print(
                            "launch_diagnostic_proxy_auth="
                            + (
                                _mask_proxy_auth(worker_proxy)
                                if worker_proxy.username or worker_proxy.password
                                else "none"
                            )
                        )
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
                            allow_github_fallback=bool(getattr(args, "allow_github_fallback", False)),
                            proxy_config_path=proxy_config_path,
                            proxy_url=proxy_url,
                            proxy_required=proxy_enforced,
                            proxy_id=worker_proxy_id,
                            require_fresh_browser_process=bool(getattr(args, "require_fresh_browser_process", False)),
                            proxy_auth_mode=worker_auth_mode,
                            local_bridge_port=worker_bridge_port,
                        )
                    )
                    if results and results[-1].get("launch_args") is not None:
                        print(f"launch_diagnostic_browser_args={json.dumps(results[-1].get('launch_args') or [], ensure_ascii=True)}")
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
            
        except KeyboardInterrupt:
            _stop_local_proxy_bridges(active_local_bridges)
            active_local_bridges = []
            raise

    opened_count = sum(1 for item in results if item.get("opened"))
    code_injected_count = sum(1 for item in results if item.get("code_injected"))
    manual_action_required_count = sum(1 for item in results if item.get("manual_action_required"))
    has_errors = any(item.get("errors") for item in results)
    if use_tabbed_launch:
        run_ok = not has_errors and code_injected_count == len(results) and opened_count == len(results)
    else:
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
        "window_mode": window_mode,
        "reuse_profile_window": bool(getattr(args, "reuse_profile_window", False)),
        "allow_github_fallback": bool(getattr(args, "allow_github_fallback", False)),
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
    if active_local_bridges and needs_local_bridge and not args.dry_run and opened_count > 0:
        report["bridges_active"] = True
        report["keepalive_mode"] = True
        report["proxy_auth_mode"] = "local-bridge"
        report["bridge_ports"] = [
            {"email": str(slot.get("email") or ""), "port": int(slot.get("port") or 0)} for slot in bridge_monitor_slots
        ]
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
                    "original_notebook_url": item.get("original_notebook_url", ""),
                    "normalized_colab_url": item.get("normalized_colab_url", ""),
                    "normalization_applied": bool(item.get("normalization_applied")),
                    "url_source": item.get("url_source", ""),
                    "url_used": item.get("url_used", ""),
                    "github_fallback_used": bool(item.get("github_fallback_used")),
                    "config_missing_drive_notebook_url": bool(item.get("config_missing_drive_notebook_url")),
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
    exit_code = 0 if report["ok"] else 2
    no_bridge_keepalive = bool(getattr(args, "no_bridge_keepalive", False))
    handoff_ready = (
        active_local_bridges
        and needs_local_bridge
        and not args.dry_run
        and opened_count > 0
        and bridge_monitor_slots
    )
    if handoff_ready and no_bridge_keepalive:
        global _LAST_LAUNCH_HANDOFF
        _LAST_LAUNCH_HANDOFF = ColabGroupLaunchHandoff(
            ok=bool(opened_count > 0 and not has_errors),
            exit_code=exit_code,
            reason=str(report.get("status") or ""),
            active_local_bridges=active_local_bridges,
            bridge_monitor_slots=list(bridge_monitor_slots),
            local_bridge_worker_count=len(local_bridge_workers),
            results=list(results),
            opened_count=int(opened_count),
            workers_total=len(results),
        )
        print(f"bridge_handoff=True opened_count={opened_count}/{len(results)}")
        return exit_code if opened_count > 0 else 2
    if handoff_ready and not no_bridge_keepalive:
        try:
            _run_local_bridge_keepalive_loop(monitor_slots=bridge_monitor_slots)
        except KeyboardInterrupt:
            pass
        finally:
            _stop_local_proxy_bridges(active_local_bridges)
            active_local_bridges = []
    elif active_local_bridges:
        _stop_local_proxy_bridges(active_local_bridges)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
