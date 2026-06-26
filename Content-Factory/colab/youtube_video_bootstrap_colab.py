"""Bootstrap for Content-Factory YouTube video Colab workers.

This script resolves the actual Google Drive root for ContentFactory_YouTube,
then runs scripts/youtube_video_worker_colab.py from that root.

Supported environment:
- CONTENT_FACTORY_WORKER_EMAIL
- CONTENT_FACTORY_MAX_JOBS_PER_RUN
- CONTENT_FACTORY_YOUTUBE_ROOT
- CONTENT_FACTORY_YOUTUBE_FOLDER_ID
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import time
import traceback
from pathlib import Path
from typing import Any


DEFAULT_ROOT_NAME = "ContentFactory_YouTube"
DEFAULT_ROOT = Path("/content/drive/MyDrive") / DEFAULT_ROOT_NAME


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def print_stage(stage: str, message: str, **fields: Any) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    suffix = f" {details}" if details else ""
    print(f"[{stage}] {message}{suffix}", flush=True)


def cf_boot(stage: str, **fields: Any) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    suffix = f" {details}" if details else ""
    print(f"[CF_BOOT] {stage}{suffix}", flush=True)


def cf_boot_error(stage: str, error: BaseException) -> None:
    tb = traceback.format_exc()
    print(f"[CF_BOOT_ERROR] stage={stage}", flush=True)
    print(f"[CF_BOOT_ERROR] exception={error!r}", flush=True)
    print(f"[CF_BOOT_ERROR] traceback={tb}", flush=True)


def safe_worker_name(account: str) -> str:
    cleaned = []
    for char in account.strip():
        cleaned.append(char if char.isalnum() or char in "._-" else "_")
    return "".join(cleaned).strip("._-") or "worker"


def write_boot_status(root: Path, *, account: str, stage: str, ok: bool = False, **updates: Any) -> Path:
    status_path = root / "logs" / f"colab_boot_status_{safe_worker_name(account)}.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "account": account,
        "worker_email": account,
        "started_at": updates.pop("started_at", None) or utc_now(),
        "last_stage": stage,
        "last_stage_at": utc_now(),
        "ok": bool(ok),
        "error_stage": None,
        "error": None,
        "traceback": None,
        "heartbeat_path": "",
        "heartbeat_written_once": False,
        "worker_main_loop_started": False,
    }
    existing: dict[str, Any] = {}
    if status_path.is_file():
        try:
            existing = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    if isinstance(existing, dict):
        payload.update(existing)
    payload.update(updates)
    payload["account"] = account
    payload["last_stage"] = stage
    payload["last_stage_at"] = utc_now()
    payload["ok"] = bool(ok)
    tmp = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(status_path)
    return status_path


def mount_colab_drive_if_available() -> None:
    try:
        from google.colab import drive  # type: ignore  # noqa: F401
    except Exception:
        return
    cf_boot("drive_mount_start")
    try:
        from safe_colab_drive_mount import safe_mount_google_drive
    except ImportError:
        import sys

        script_dir = Path(__file__).resolve().parent
        script_dir_text = str(script_dir)
        if script_dir_text not in sys.path:
            sys.path.insert(0, script_dir_text)
        from safe_colab_drive_mount import safe_mount_google_drive

    safe_mount_google_drive("/content/drive")
    cf_boot("drive_mount_ok")


def create_shortcut_from_folder_id(folder_id: str, shortcut_name: str = DEFAULT_ROOT_NAME) -> Path:
    folder_id = (folder_id or "").strip()
    if not folder_id:
        raise RuntimeError("CONTENT_FACTORY_YOUTUBE_FOLDER_ID is empty")
    mount_colab_drive_if_available()
    try:
        from google.colab import auth  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.errors import HttpError  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Google Drive API is unavailable in this runtime; set CONTENT_FACTORY_YOUTUBE_ROOT "
            "to an already mounted ContentFactory_YouTube path."
        ) from exc
    try:
        auth.authenticate_user()
        service = build("drive", "v3")
        meta = service.files().get(fileId=folder_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise RuntimeError(f"CONTENT_FACTORY_YOUTUBE_FOLDER_ID does not point to a folder: {meta}")
        escaped_name = shortcut_name.replace("'", "\\'")
        query = f"'root' in parents and trashed=false and name='{escaped_name}'"
        existing = service.files().list(q=query, fields="files(id,name,mimeType,shortcutDetails)", supportsAllDrives=True).execute()
        files = existing.get("files") or []
        has_target_shortcut = any(
            item.get("mimeType") == "application/vnd.google-apps.shortcut"
            and (item.get("shortcutDetails") or {}).get("targetId") == folder_id
            for item in files
        )
        if not has_target_shortcut:
            service.files().create(
                body={
                    "name": shortcut_name,
                    "mimeType": "application/vnd.google-apps.shortcut",
                    "shortcutDetails": {"targetId": folder_id},
                    "parents": ["root"],
                },
                fields="id,name",
                supportsAllDrives=True,
            ).execute()
    except HttpError as exc:
        raise RuntimeError(
            "Cannot access ContentFactory_YouTube by CONTENT_FACTORY_YOUTUBE_FOLDER_ID. "
            "Give this Google account access to the ContentFactory_YouTube Drive folder."
        ) from exc
    return Path("/content/drive/MyDrive") / shortcut_name


def candidate_roots() -> list[Path]:
    raw = os.environ.get("CONTENT_FACTORY_YOUTUBE_ROOT", "").strip()
    candidates = [
        Path(raw).expanduser() if raw else None,
        DEFAULT_ROOT,
        Path("/content/drive/MyDrive") / DEFAULT_ROOT_NAME,
        Path("/content/drive/Shareddrives") / DEFAULT_ROOT_NAME,
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        if item is None:
            continue
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def resolve_youtube_root(cli_root: str = "") -> Path:
    mount_colab_drive_if_available()
    explicit = os.environ.get("CONTENT_FACTORY_YOUTUBE_ROOT", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return path
        raise RuntimeError(f"CONTENT_FACTORY_YOUTUBE_ROOT is set but does not exist: {path}")

    if cli_root.strip():
        path = Path(cli_root.strip()).expanduser()
        if path.exists():
            return path

    folder_id = os.environ.get("CONTENT_FACTORY_YOUTUBE_FOLDER_ID", "").strip()
    if folder_id:
        shortcut = create_shortcut_from_folder_id(folder_id)
        for _ in range(20):
            if shortcut.exists():
                os.environ["CONTENT_FACTORY_YOUTUBE_ROOT"] = str(shortcut)
                return shortcut
            time.sleep(1)
        raise RuntimeError(
            f"Created/verified shortcut for ContentFactory_YouTube, but mounted path is not visible yet: {shortcut}. "
            "Reconnect Google Drive in Colab and rerun the cell."
        )

    existing = [path for path in candidate_roots() if path.exists()]
    if existing:
        os.environ["CONTENT_FACTORY_YOUTUBE_ROOT"] = str(existing[0])
        return existing[0]
    raise RuntimeError(
        "ContentFactory_YouTube root is not accessible in this Colab account. "
        "Set CONTENT_FACTORY_YOUTUBE_ROOT to a mounted folder, or set CONTENT_FACTORY_YOUTUBE_FOLDER_ID "
        "and share the ContentFactory_YouTube folder with this Google account."
    )


def main() -> int:
    started_at = utc_now()
    cf_boot("cell_started", source="bootstrap")
    cf_boot("python_version", python=sys.version.replace("\n", " "))
    cf_boot("cwd", cwd=Path.cwd())
    parser = argparse.ArgumentParser()
    parser.add_argument("--drive-root", default="")
    parser.add_argument("--story-slug", default="")
    parser.add_argument("--worker-email", default="")
    parser.add_argument("--max-jobs-per-run", default="")
    parser.add_argument("--poll-seconds", default="10")
    parser.add_argument("--idle-timeout-min", default="15")
    args, unknown = parser.parse_known_args()

    if args.worker_email.strip():
        os.environ["CONTENT_FACTORY_WORKER_EMAIL"] = args.worker_email.strip()
    if args.max_jobs_per_run.strip():
        os.environ["CONTENT_FACTORY_MAX_JOBS_PER_RUN"] = args.max_jobs_per_run.strip()

    account = os.environ.get("CONTENT_FACTORY_WORKER_EMAIL", "").strip()
    root: Path | None = None
    try:
        root = resolve_youtube_root(args.drive_root)
        os.environ["CONTENT_FACTORY_YOUTUBE_ROOT"] = str(root)
        cf_boot("project_root_detected", root=root, root_exists=root.exists())
        write_boot_status(root, account=account, stage="project_root_detected", started_at=started_at)
        cf_boot("env_loaded", account=account, max_jobs=os.environ.get("CONTENT_FACTORY_MAX_JOBS_PER_RUN", ""))
        write_boot_status(root, account=account, stage="env_loaded")
        worker_script = root / "scripts" / "youtube_video_worker_colab.py"
        print_stage(
            "BOOT",
            "youtube video bootstrap resolved root",
            root=root,
            root_exists=root.exists(),
            worker_script=worker_script,
            worker_script_exists=worker_script.is_file(),
            worker_email=account,
            max_jobs=os.environ.get("CONTENT_FACTORY_MAX_JOBS_PER_RUN", ""),
        )
        if not worker_script.is_file():
            raise RuntimeError(
                f"Worker script is missing at {worker_script}. Run setup-colab-workers on Windows, "
                "or check that this account has access to ContentFactory_YouTube."
            )

        worker_argv = [str(worker_script), "--drive-root", str(root)]
        if args.story_slug.strip():
            worker_argv.extend(["--story-slug", args.story_slug.strip()])
        if args.poll_seconds.strip():
            worker_argv.extend(["--poll-seconds", args.poll_seconds.strip()])
        if args.idle_timeout_min.strip():
            worker_argv.extend(["--idle-timeout-min", args.idle_timeout_min.strip()])
        worker_argv.extend(unknown)
        sys.argv = worker_argv
        cf_boot("worker_import_start", worker_script=worker_script)
        write_boot_status(root, account=account, stage="worker_import_start")
        runpy.run_path(str(worker_script), run_name="__main__")
        write_boot_status(root, account=account, stage="worker_main_loop_exit", ok=True)
        return 0
    except Exception as exc:
        cf_boot_error("bootstrap", exc)
        if root is not None:
            write_boot_status(
                root,
                account=account,
                stage="bootstrap_error",
                ok=False,
                error_stage="bootstrap",
                error=repr(exc),
                traceback=traceback.format_exc(),
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
