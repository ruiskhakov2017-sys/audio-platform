"""Browser + profile preflight for safe stage before batch start."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.account_capabilities import resolve_gemini_account_indices
from orchestrator.config import OrchestratorConfig
from orchestrator.gemini_colab_proxy import GeminiColabProxySession, apply_gemini_colab_proxy_env
from orchestrator.youtube_full_auto.bridge_errors import (
    REASON_PROFILE_DIR_MISSING_OR_EMPTY,
    REASON_SAFE_BOT_PREFLIGHT_FAILED,
)
from orchestrator.youtube_full_auto.safe_account_mapping import (
    build_safe_account_mapping,
    format_safe_mapping_table,
    resolve_safe_profile_dir,
)
from orchestrator.youtube_safe_english_bridge import _pick_safe_bot


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_safe_preflight_log(log_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in log_text.splitlines():
        low = line.lower()
        if "[safe_preflight]" in low:
            for key in (
                "current_url",
                "page_title",
                "input_ready",
                "model_menu_ready",
                "selected_model",
                "gemini_page_ok",
                "login_required",
                "profile_email",
                "expected_email",
                "email_match",
            ):
                token = f"{key}="
                if token in low:
                    out[key] = line.split(token, 1)[-1].strip()
        if "[safe_model_selected]" in low and "model=" in low:
            out["resolved_selected_model"] = line.split("model=", 1)[-1].split()[0].strip()
            out["model_selected_state"] = True
        if "[model] selected=" in low:
            out["resolved_selected_model"] = line.split("selected=", 1)[-1].split()[0].strip()
    for flag in ("input_ready", "model_menu_ready", "gemini_page_ok", "login_required", "email_match"):
        if flag in out:
            first = str(out[flag]).strip().split()[0] if str(out[flag]).strip() else ""
            out[flag] = first.lower() in {"true", "1", "yes"}
    if "selected_model" in out:
        selected = str(out["selected_model"]).strip()
        if " " in selected:
            selected = selected.split()[0]
        out["selected_model"] = selected
    if out.get("model_selected_state"):
        out["model_selected_state"] = True
    low_all = log_text.lower()
    if (
        "target page, context or browser has been closed" in low_all
        or "targetclosederror" in low_all
        or "browser has been closed" in low_all
    ):
        out["browser_closed_externally"] = True
    return out


def _preflight_model_ready(parsed: dict[str, Any]) -> bool:
    if bool(parsed.get("model_menu_ready")):
        return True
    if bool(parsed.get("model_selected_state")):
        return True
    return bool(str(parsed.get("selected_model") or parsed.get("resolved_selected_model") or "").strip())


def _load_cached_usable_preflight_rows(
    batch_root: Path,
    *,
    youtube_run_id: str,
) -> dict[int, dict[str, Any]]:
    path = batch_root / "reports" / "gemini_accounts_preflight.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if str(payload.get("youtube_run_id") or "") != youtube_run_id:
        return {}
    cached: dict[int, dict[str, Any]] = {}
    for row in payload.get("accounts") or []:
        if not row.get("usable"):
            continue
        log_path = Path(str(row.get("preflight_log") or ""))
        if not log_path.is_file():
            continue
        cached[int(row["account_index"])] = dict(row)
    return cached


def _append_safe_stage_log(batch_root: Path, message: str) -> None:
    log_path = batch_root / "logs" / "stages" / "safe.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _run_browser_preflight_row(
    *,
    config: OrchestratorConfig,
    batch_root: Path,
    account_index: int,
    mapping_row: dict[str, Any],
    proxy_session: GeminiColabProxySession | None,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    root = config.root_dir.resolve()
    runner_dir = (root / "legacy" / "youtube_tts").resolve()
    gemini_auto = runner_dir / "gemini_auto.py"
    profile_dir = Path(str(mapping_row.get("profile_dir") or ""))
    expected_email = str(mapping_row.get("expected_email") or "")
    gem_url = str(mapping_row.get("expected_gem_bot_url") or "")
    log_path = (batch_root / "gemini_preflight" / "safe" / f"acc{account_index}" / "preflight.log").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stories_dir = log_path.parent / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)

    row: dict[str, Any] = {
        **mapping_row,
        "ok": False,
        "usable": False,
        "reason_code": "",
        "preflight_log": str(log_path),
    }

    if str(mapping_row.get("profile_reason") or "") == REASON_PROFILE_DIR_MISSING_OR_EMPTY:
        row["reason_code"] = REASON_PROFILE_DIR_MISSING_OR_EMPTY
        row["error"] = "profile_dir_missing_or_empty"
        return row

    if not bool(mapping_row.get("email_match")):
        row["reason_code"] = REASON_SAFE_BOT_PREFLIGHT_FAILED
        row["error"] = "profile_email_mismatch"
        return row

    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "GEMINI_STORIES_DIR": str(stories_dir),
            "GEMINI_USER_DATA_DIR": str(profile_dir),
            "GEMINI_LOG_FILE": str(log_path),
            "GEMINI_URL": gem_url,
            "GEMINI_ACCOUNT_EMAIL": expected_email,
            "START_ACCOUNT_INDEX": str(int(account_index)),
            "GEMINI_LOG_ACCOUNT_INDEX": str(int(account_index)),
            "GEMINI_SAFE_PREFLIGHT_ONLY": "1",
            "GEMINI_NON_INTERACTIVE": "1",
            "GEMINI_SKIP_SESSION_BOT_SYNC": "1",
        }
    )
    if proxy_session is not None:
        env = apply_gemini_colab_proxy_env(env, proxy_session)

    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(gemini_auto)],
            cwd=str(runner_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, int(timeout_seconds or 90)),
        )
    except subprocess.TimeoutExpired as exc:
        row["reason_code"] = REASON_SAFE_BOT_PREFLIGHT_FAILED
        row["error"] = "preflight_subprocess_timeout"
        row["timeout_seconds"] = max(30, int(timeout_seconds or 90))
        row["subprocess_stdout_tail"] = str(exc.stdout or "")[-2000:]
        return row
    except Exception as exc:
        row["reason_code"] = REASON_SAFE_BOT_PREFLIGHT_FAILED
        row["error"] = repr(exc)
        row["traceback_tail"] = traceback.format_exc()[-2000:]
        return row

    log_text = ""
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    log_text = log_text + "\n" + (proc.stdout or "") + "\n" + (proc.stderr or "")
    parsed = _parse_safe_preflight_log(log_text)
    row.update(parsed)
    row["bridge_exit_code"] = int(proc.returncode or 0)
    model_ready = _preflight_model_ready(parsed)

    usable = (
        bool(parsed.get("gemini_page_ok"))
        and bool(parsed.get("input_ready"))
        and model_ready
        and not bool(parsed.get("login_required"))
    )
    row["ok"] = usable
    row["usable"] = usable
    if not usable:
        if parsed.get("login_required"):
            row["reason_code"] = "login_required"
        elif parsed.get("browser_closed_externally"):
            row["reason_code"] = "preflight_browser_closed_externally"
            row["error"] = "browser_window_closed_before_input_ready"
        elif proc.returncode != 0:
            row["reason_code"] = REASON_SAFE_BOT_PREFLIGHT_FAILED
        else:
            row["reason_code"] = REASON_SAFE_BOT_PREFLIGHT_FAILED
        row["error"] = row.get("error") or "safe_browser_preflight_failed"
    return row


def run_safe_accounts_preflight(
    *,
    config: OrchestratorConfig,
    batch_root: Path,
    youtube_run_id: str,
    gemini_accounts: str = "0,1,2,3,4",
    gemini_workers: int = 5,
    execute: bool = False,
    registry_path: Path | None = None,
    required_usable: int = 0,
    per_account_timeout_seconds: int = 90,
) -> dict[str, Any]:
    account_indices, warnings = resolve_gemini_account_indices(
        gemini_accounts=gemini_accounts,
        gemini_workers=gemini_workers,
        strict_invalid=True,
    )
    mapping = build_safe_account_mapping(
        config=config,
        account_indices=account_indices,
        registry_path=registry_path,
    )
    mapping_table = format_safe_mapping_table(mapping)

    rows: list[dict[str, Any]] = []
    out_path = batch_root / "reports" / "gemini_accounts_preflight.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    required_usable_count = max(0, min(len(mapping), int(required_usable or 0)))

    def _usable_count() -> int:
        return len([r for r in rows if r.get("usable")])

    def _has_enough_usable() -> bool:
        return required_usable_count > 0 and _usable_count() >= required_usable_count

    def _write_preflight_payload(*, interrupted: bool = False) -> dict[str, Any]:
        usable_accounts = [int(r["account_index"]) for r in rows if r.get("usable")]
        payload: dict[str, Any] = {
            "schema_version": 1,
            "written_at": _utc_now(),
            "youtube_run_id": youtube_run_id,
            "stage": "safe",
            "execute": bool(execute),
            "interrupted": bool(interrupted),
            "mapping_table": mapping_table,
            "accounts": rows,
            "usable_accounts": usable_accounts,
            "usable_count": len(usable_accounts),
            "required_usable": required_usable_count,
            "ok": len(usable_accounts) > 0 and not interrupted,
            "reason_code": ""
            if usable_accounts and not interrupted
            else ("run_interrupted_by_user" if interrupted else REASON_SAFE_BOT_PREFLIGHT_FAILED),
            "warnings": warnings,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    if not execute:
        for m in mapping:
            row = dict(m)
            row["ok"] = bool(m.get("profile_nonempty")) and bool(m.get("email_match"))
            row["usable"] = row["ok"]
            row["reason_code"] = "" if row["ok"] else str(m.get("profile_reason") or REASON_SAFE_BOT_PREFLIGHT_FAILED)
            rows.append(row)
    else:
        cached_usable = _load_cached_usable_preflight_rows(batch_root, youtube_run_id=youtube_run_id)
        if execute:
            _append_safe_stage_log(
                batch_root,
                f"[SAFE_PREFLIGHT] begin accounts={','.join(str(int(m['account_index'])) for m in mapping)} "
                f"cached_usable={','.join(str(i) for i in sorted(cached_usable)) or 'none'} "
                f"required_usable={required_usable_count or 'any'} "
                f"timeout_sec={max(30, int(per_account_timeout_seconds or 90))}",
            )
            _append_safe_stage_log(
                batch_root,
                "[SAFE_PREFLIGHT] hint=sequential_browser_checks do_not_close_chrome_windows",
            )
        proxy_session: GeminiColabProxySession | None = None
        interrupted = False
        try:
            try:
                proxy_session = GeminiColabProxySession(config.root_dir.resolve()).start()
            except Exception:
                proxy_session = None
            for m in mapping:
                if _has_enough_usable():
                    _append_safe_stage_log(
                        batch_root,
                        f"[SAFE_PREFLIGHT_ENOUGH] usable={_usable_count()} required={required_usable_count}",
                    )
                    break
                account_index = int(m["account_index"])
                if account_index in cached_usable:
                    row = dict(cached_usable[account_index])
                    row["preflight_skipped"] = True
                    rows.append(row)
                    _append_safe_stage_log(
                        batch_root,
                        "[SAFE_PREFLIGHT_ACCOUNT] "
                        f"account={account_index} usable=true reason_code=cached_ok "
                        f"exit_code={row.get('bridge_exit_code', 'n/a')} "
                        f"selected_model={row.get('resolved_selected_model') or row.get('selected_model') or ''}",
                    )
                    _write_preflight_payload()
                    continue
                row = _run_browser_preflight_row(
                    config=config,
                    batch_root=batch_root,
                    account_index=account_index,
                    mapping_row=m,
                    proxy_session=proxy_session,
                    timeout_seconds=max(30, int(per_account_timeout_seconds or 90)),
                )
                rows.append(row)
                _append_safe_stage_log(
                    batch_root,
                    "[SAFE_PREFLIGHT_ACCOUNT] "
                    f"account={account_index} usable={str(bool(row.get('usable'))).lower()} "
                    f"reason_code={row.get('reason_code') or 'ok'} "
                    f"exit_code={row.get('bridge_exit_code', 'n/a')} "
                    f"selected_model={row.get('resolved_selected_model') or row.get('selected_model') or ''}",
                )
                _write_preflight_payload()
        except KeyboardInterrupt:
            interrupted = True
            _append_safe_stage_log(batch_root, "[SAFE_PREFLIGHT] interrupted_by_user=true")
            _write_preflight_payload(interrupted=True)
            raise
        finally:
            if proxy_session is not None:
                try:
                    proxy_session.stop()
                except Exception:
                    pass

    payload = _write_preflight_payload(interrupted=False)
    payload["report_path"] = str(out_path)
    return payload
