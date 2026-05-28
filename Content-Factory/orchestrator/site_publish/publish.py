from __future__ import annotations

import json
import importlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.site_publish.env_doctor import run_site_publish_env_doctor
from orchestrator.site_publish.paths import (
    describe_layout,
    is_run_scoped,
    resolve_launch_dir,
    resolve_site_publish_root,
    resolve_to_publish_root,
    site_publish_manifest_path,
)


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = key.strip()
        v = value.strip()
        if not k:
            continue
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim(s: str, limit: int = 4000) -> str:
    if not s:
        return ""
    return s[-limit:]


def _detect_reason(*, returncode: int, stdout: str, stderr: str, dry_run: bool) -> str:
    blob = f"{stdout}\n{stderr}".lower()
    if "unrecognized arguments: --dry-run" in blob or "unknown option --dry-run" in blob:
        return "legacy_publisher_does_not_support_dry_run_flag"
    if dry_run and returncode != 0:
        return "legacy_publisher_failed_in_dry_run_mode"
    if returncode != 0:
        return "legacy_publisher_failed"
    return "ok"


def _check_publish_dependencies() -> list[str]:
    missing: list[str] = []
    for mod in ("boto3", "botocore", "tinytag"):
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(mod)
    return missing


def run_site_publish(
    *,
    content_factory_root: Path,
    story: str = "",
    dry_run: bool = True,
    execute: bool = False,
    dirtysecrets_root: Path | None = None,
    allow_partial_tts: bool = False,
    launch_name: str = "",
    launch_dir: Path | None = None,
) -> dict[str, Any]:
    root = content_factory_root.resolve()
    service_dir = (root / ".orchestrator").resolve()
    logs_dir = (service_dir / "logs").resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    result_jsonl = (logs_dir / "site_publish_results.jsonl").resolve()
    report_path = (service_dir / "site_publish_report.json").resolve()
    mode = "execute" if execute else "dry-run"
    story_name = (story or "").strip()
    cwd = str(root)
    cmd: list[str] = []
    returncode: int | None = None
    stdout = ""
    stderr = ""
    reason = ""
    status = "error"
    exception_type = ""
    exception_message = ""
    env_report_path = ""

    run_scoped_requested = is_run_scoped(launch_name=launch_name, launch_dir=launch_dir)
    launch = resolve_launch_dir(root, launch_name=launch_name, launch_dir=launch_dir)
    layout_info = describe_layout(root, launch_name=launch_name, launch_dir=launch_dir)
    site_publish_manifest = site_publish_manifest_path(root, launch)
    output_site = resolve_site_publish_root(root, launch)
    to_publish_dir = resolve_to_publish_root(root, launch)

    def _persist() -> dict[str, Any]:
        row: dict[str, Any] = {
            "timestamp": _now_iso(),
            "story": story_name,
            "mode": mode,
            "allow_partial_tts": bool(allow_partial_tts),
            "launch_name": (launch_name or "").strip(),
            "launch_dir": str(launch) if launch is not None else "",
            "layout": layout_info,
            "site_publish_root": str(output_site),
            "to_publish_root": str(to_publish_dir),
            "dry_run_strategy": (
                "legacy_subprocess_with_--dry-run_flag"
                if dry_run and not execute
                else "legacy_subprocess_execute_mode"
            ),
            "command": cmd,
            "cwd": cwd,
            "returncode": returncode,
            "env_report_path": env_report_path,
            "stdout": _trim(stdout),
            "stderr": _trim(stderr),
            "exception_type": exception_type,
            "exception_message": exception_message,
            "reason": reason,
            "status": status,
        }
        _append_jsonl(result_jsonl, row)
        report_payload = dict(row)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out = dict(row)
        out["ok"] = status == "done"
        out["result_jsonl"] = str(result_jsonl)
        out["report_path"] = str(report_path)
        out["manifest_path"] = str(site_publish_manifest)
        if launch is not None:
            try:
                site_publish_manifest.parent.mkdir(parents=True, exist_ok=True)
                existing: dict[str, Any] = {}
                if site_publish_manifest.is_file():
                    try:
                        existing_raw = json.loads(site_publish_manifest.read_text(encoding="utf-8"))
                        if isinstance(existing_raw, dict):
                            existing = existing_raw
                    except (OSError, json.JSONDecodeError):
                        existing = {}
                existing.update(
                    {
                        "stage": "publish",
                        "launch_name": (launch_name or "").strip() or launch.name,
                        "launch_dir": str(launch),
                        "site_publish_root": str(output_site),
                        "to_publish_root": str(to_publish_dir),
                        "publish_status": status,
                        "publish_reason": reason,
                        "publish_returncode": returncode,
                        "publish_mode": mode,
                        "publish_story": story_name,
                        "publish_command": cmd,
                        "generated_at": _now_iso(),
                        "report_path": str(report_path),
                        "result_jsonl": str(result_jsonl),
                    }
                )
                site_publish_manifest.write_text(
                    json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return out

    if run_scoped_requested and launch is None:
        status = "blocked"
        reason = "launch_not_found_for_run_scoped_request"
        stderr = f"launch not found: launch_name={launch_name!r} launch_dir={launch_dir!r}"
        returncode = 2
        return _persist()

    env_report = run_site_publish_env_doctor(
        content_factory_root=root,
        dirtysecrets_root=dirtysecrets_root,
        write_env_file=True,
    )
    env_report_path = str(env_report.get("report_path", ""))
    blockers = list(env_report.get("blockers", []))

    hard_for_dry_run = list(blockers)
    if execute and blockers:
        status = "blocked"
        reason = "execute_blocked_by_env_doctor"
        stderr = "\n".join([f"blocker={b}" for b in blockers])
        returncode = 2
        return _persist()
    if dry_run and hard_for_dry_run:
        status = "blocked"
        reason = "dry_run_blocked_by_env_doctor"
        stderr = "\n".join([f"blocker={b}" for b in hard_for_dry_run])
        returncode = 2
        return _persist()

    missing_deps = _check_publish_dependencies()
    if missing_deps:
        status = "blocked"
        reason = "missing dependency: " + "/".join(missing_deps)
        returncode = 2
        stderr = (
            "missing dependency: "
            + ", ".join(missing_deps)
            + "\nrun: python -m pip install " + " ".join(missing_deps)
        )
        return _persist()

    entrypoint = (root / "legacy" / "autopublisher" / "publish_stories.py").resolve()
    if not entrypoint.is_file():
        status = "error"
        reason = f"missing_entrypoint:{entrypoint}"
        returncode = 2
        return _persist()

    output_site.mkdir(parents=True, exist_ok=True)
    to_publish_dir.mkdir(parents=True, exist_ok=True)
    env_file = (root / ".env.site_publish").resolve()

    cmd = [
        sys.executable,
        str(entrypoint),
        "--headless",
        "--bridge-output-site",
        str(output_site),
        "--to-publish-dir",
        str(to_publish_dir),
        "--result-jsonl",
        str(result_jsonl),
    ]
    if story.strip():
        cmd.extend(["--story-name", story.strip()])
    if dry_run and not execute:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env.update(_read_env_file(env_file))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Legacy publisher reads SUPABASE_SERVICE_ROLE_KEY; map from new Secret Key if needed.
    if not (env.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip():
        secret_key = (env.get("SUPABASE_SECRET_KEY", "") or "").strip()
        if secret_key:
            env["SUPABASE_SERVICE_ROLE_KEY"] = secret_key
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
            cwd=str(root),
        )
        returncode = int(proc.returncode)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        reason = _detect_reason(returncode=returncode, stdout=stdout, stderr=stderr, dry_run=dry_run)
        status = "done" if returncode == 0 else "failed"
        return _persist()
    except Exception as exc:
        returncode = 2
        status = "error"
        reason = "publish_wrapper_exception_before_subprocess" if not cmd else "publish_wrapper_exception"
        exception_type = type(exc).__name__
        exception_message = str(exc)
        return _persist()

