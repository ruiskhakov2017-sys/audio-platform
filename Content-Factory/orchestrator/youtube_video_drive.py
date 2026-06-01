"""Drive/Colab handoff for YouTube video segment rendering."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_video_segments import (
    YoutubeVideoPrepareSegmentsOptions,
    get_media_duration,
    is_valid_video_file,
    run_youtube_video_prepare_segments,
)


CONFIG_PATH = Path("configs/youtube_video_render.yaml")
COLAB_WORKERS_CONFIG_PATH = Path("configs/youtube_video_colab_workers.yaml")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXT = ".mp4"
REPORT_EXPORT = "video_export_job_report.json"
REPORT_STATUS = "video_drive_status_report.json"
REPORT_SETUP = "video_setup_colab_workers_report.json"
REPORT_DISPATCH = "video_dispatch_segments_report.json"
REPORT_RECLAIM = "video_reclaim_stale_segments_report.json"
REPORT_QUEUE_STATUS = "video_queue_status_report.json"
REPORT_WORKERS_AUDIT = "video_workers_audit_report.json"
REPORT_INSPECT = "video_inspect_segment_report.json"
REPORT_IMPORT = "video_import_results_report.json"
REPORT_FINAL = "final_video_report.json"


@dataclass
class YoutubeVideoExportJobOptions:
    story_id: str
    execute: bool = False
    force: bool = False


@dataclass
class YoutubeVideoDriveStatusOptions:
    story_id: str


@dataclass
class YoutubeVideoImportResultsOptions:
    story_id: str
    execute: bool = False


@dataclass
class YoutubeVideoAssembleFinalOptions:
    story_id: str
    execute: bool = False


@dataclass
class YoutubeVideoFullDriveFlowOptions:
    story_id: str
    execute: bool = False
    force: bool = False


@dataclass
class YoutubeVideoSetupColabWorkersOptions:
    story_id: str
    execute: bool = False
    youtube_folder_id: str = ""


@dataclass
class YoutubeVideoColabBrowserProfilesOptions:
    config_path: Path = COLAB_WORKERS_CONFIG_PATH


@dataclass
class YoutubeVideoWorkersAuditOptions:
    story_id: str
    config_path: Path = COLAB_WORKERS_CONFIG_PATH


@dataclass
class YoutubeVideoDispatchSegmentsOptions:
    story_id: str
    workers: str = ""
    target_per_worker: int = 1
    max_total_assigned: int = 5
    execute: bool = False


@dataclass
class YoutubeVideoReclaimStaleSegmentsOptions:
    story_id: str
    stale_minutes: int = 10
    execute: bool = False
    max_attempts: int = 3
    dry_run: bool = False


@dataclass
class YoutubeVideoQueueStatusOptions:
    story_id: str
    stale_minutes: int = 10
    quick: bool = False


@dataclass
class YoutubeVideoInspectSegmentOptions:
    story_id: str
    segment_id: str


@dataclass
class YoutubeVideoValidateJobAssetsOptions:
    story_id: str
    dry_run: bool = False


@dataclass
class YoutubeVideoCleanupPartialOptions:
    story_id: str
    execute: bool = False
    dry_run: bool = False


@dataclass
class YoutubeVideoWatchQueueOptions:
    story_id: str
    poll_seconds: int = 60
    stale_minutes: int = 10
    max_attempts: int = 3
    pending_per_worker: int = 1
    max_total_assigned: int = 50
    workers: str = ""
    execute: bool = False
    dry_run: bool = False
    once: bool = False
    max_runtime_minutes: float = 0.0
    auto_import_on_complete: bool = True
    skip_asset_preflight: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_if_exists(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw_text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_simple_yaml(raw_text)
    data = yaml.safe_load(raw_text) or {}
    return data if isinstance(data, dict) else {}


def _parse_scalar(value: str) -> Any:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        return cleaned[1:-1]
    if cleaned.lower() in {"true", "false"}:
        return cleaned.lower() == "true"
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        return float(cleaned)
    except ValueError:
        return cleaned


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Small fallback parser for the flat config used by youtube_video_render.yaml."""
    data: dict[str, Any] = {}
    current_list_key = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key:
            data.setdefault(current_list_key, []).append(_parse_scalar(stripped[2:]))
            continue
        current_list_key = ""
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not value.strip():
            data[key] = []
            current_list_key = key
        else:
            data[key] = _parse_scalar(value)
    return data


def _render_settings(config: OrchestratorConfig) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "drive_root": r"G:\Мой диск\ContentFactory_YouTube",
        "drive_video_root": "video_jobs",
        "segment_sec": 180,
        "workers": [
            "ru.iskhakov2017@gmail.com",
            "isi.cordeiro@gmail.com",
            "iheuko119@gmail.com",
            "goegoeseijin@gmail.com",
            "suteadodesun6@gmail.com",
        ],
        "future_workers_max": 10,
        "require_effects": False,
        "effects_assets": ["film.mp4", "dust.mp4", "images/start.*"],
        "local_output_root": "output/youtube",
        "render_mode": "segments_then_concat",
        "final_video_name": "final_video.mp4",
    }
    raw = _load_yaml(config.root_dir / CONFIG_PATH)
    settings = {**defaults, **raw}
    settings["drive_root"] = str(settings["drive_root"])
    settings["drive_video_root"] = str(settings["drive_video_root"])
    settings["segment_sec"] = float(settings["segment_sec"] or defaults["segment_sec"])
    raw_workers = settings.get("workers") or defaults["workers"]
    if isinstance(raw_workers, int):
        worker_emails = list(defaults["workers"])[:raw_workers]
    elif isinstance(raw_workers, str):
        worker_emails = [item.strip() for item in raw_workers.split(",") if item.strip()]
    else:
        worker_emails = [str(item).strip() for item in raw_workers if str(item).strip()]
    settings["workers"] = worker_emails
    settings["workers_count"] = len(worker_emails)
    settings["future_workers_max"] = int(settings["future_workers_max"] or defaults["future_workers_max"])
    settings["require_effects"] = bool(settings["require_effects"])
    settings["effects_assets"] = list(settings.get("effects_assets") or defaults["effects_assets"])
    settings["local_output_root"] = str(settings["local_output_root"])
    settings["render_mode"] = str(settings["render_mode"])
    settings["final_video_name"] = str(settings["final_video_name"])
    return settings


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("._-")
    return slug or "story"


def _story_dir(config: OrchestratorConfig, settings: dict[str, Any], story_id: str) -> Path:
    return (config.root_dir / settings["local_output_root"] / story_id).resolve()


def _video_dirs(story_dir: Path) -> dict[str, Path]:
    root = story_dir / "08_video"
    return {
        "root": root,
        "manifests": root / "manifests",
        "segments": root / "segments",
        "logs": root / "logs",
        "reports": root / "reports",
        "work": root / "_work",
    }


def _audio_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def _frames_dir(story_dir: Path) -> Path:
    return story_dir / "07_frames"


def _timeline_path(story_dir: Path) -> Path:
    return _video_dirs(story_dir)["manifests"] / "video_timeline.json"


def _segment_jobs_path(story_dir: Path) -> Path:
    return _video_dirs(story_dir)["manifests"] / "segment_jobs.json"


def _story_manifest_path(story_dir: Path) -> Path:
    return story_dir / "youtube_story_manifest.json"


def _drive_job_root(settings: dict[str, Any], story_slug: str) -> Path:
    return Path(str(settings["drive_root"])) / str(settings["drive_video_root"]) / story_slug


def _job_dirs(job_root: Path) -> dict[str, Path]:
    return {
        "root": job_root,
        "assets": job_root / "assets",
        "assets_audio": job_root / "assets" / "audio",
        "assets_frames": job_root / "assets" / "frames",
        "assets_effects": job_root / "assets" / "effects",
        "manifests": job_root / "manifests",
        "queue": job_root / "queue",
        "global_pending": job_root / "queue" / "global_pending",
        "assigned": job_root / "queue" / "assigned",
        "legacy_pending": job_root / "segments" / "pending",
        "legacy_processing": job_root / "segments" / "processing",
        "legacy_done": job_root / "segments" / "done",
        "legacy_failed": job_root / "segments" / "failed",
        "segments": job_root / "segments",
        "work_segments": job_root / "work_segments",
        "reports": job_root / "reports",
        "final": job_root / "final",
        "status": job_root / "status",
        "scripts": job_root / "scripts",
    }


def _scan_segment_checkpoints(job_root: Path) -> dict[str, Any]:
    """Scan work_segments/<segment_id> and segments/<id>.mp4.done.json.

    Возвращает: total_with_checkpoint, fully_final_with_marker, partial_count, per_segment[].
    """
    summary: dict[str, Any] = {
        "checkpointed_segments_count": 0,
        "partial_segments_count": 0,
        "final_marker_count": 0,
        "per_segment": [],
    }
    dirs = _job_dirs(job_root)
    work_root = dirs["work_segments"]
    segments_dir = dirs["segments"]
    final_markers: set[str] = set()
    if segments_dir.is_dir():
        for marker in segments_dir.glob("segment_*.mp4.done.json"):
            final_markers.add(marker.name.split(".mp4.done.json")[0])
    if work_root.is_dir():
        for seg_dir in sorted([p for p in work_root.iterdir() if p.is_dir()]):
            seg_id = seg_dir.name
            clips_dir = seg_dir / "clips"
            raw_dir = seg_dir / "raw"
            effects_dir = seg_dir / "effects"
            clips_total = 0
            clips_done = 0
            if clips_dir.is_dir():
                for marker in clips_dir.glob("clip_*.mp4.done.json"):
                    clips_total = max(clips_total, 1)
                    if marker.with_name(marker.name.replace(".done.json", "")).is_file():
                        clips_done += 1
                clips_total = max(clips_total, clips_done)
            raw_done = any(raw_dir.glob("*.raw.mp4.done.json")) if raw_dir.is_dir() else False
            effects_done = any(effects_dir.glob("*.effects.mp4.done.json")) if effects_dir.is_dir() else False
            partial_present = False
            for stage in (clips_dir, raw_dir, effects_dir):
                if stage.is_dir() and any(stage.glob("*.partial.mp4")):
                    partial_present = True
                    break
                if stage.is_dir():
                    for mp4 in stage.glob("*.mp4"):
                        if mp4.name.endswith(".partial.mp4"):
                            partial_present = True
                            break
                        marker = mp4.with_suffix(mp4.suffix + ".done.json")
                        if not marker.is_file():
                            partial_present = True
                            break
                if partial_present:
                    break
            has_any = clips_done > 0 or raw_done or effects_done
            if has_any:
                summary["checkpointed_segments_count"] += 1
            if partial_present:
                summary["partial_segments_count"] += 1
            summary["per_segment"].append(
                {
                    "segment_id": seg_id,
                    "clips_done": clips_done,
                    "raw_done": raw_done,
                    "effects_done": effects_done,
                    "final_marker": seg_id in final_markers,
                    "partial_present": partial_present,
                }
            )
    summary["final_marker_count"] = len(final_markers)
    return summary


def _root_compat_dirs(settings: dict[str, Any]) -> dict[str, Path]:
    root = Path(str(settings["drive_root"]))
    return {
        "root": root,
        "scripts": root / "scripts",
        "compat_queue_pending": root / "queue" / "video" / "pending",
        "compat_queue_root": root / "queue" / "video",
    }


def _first_existing_path(candidates: list[Path]) -> Path | None:
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _browser_candidates(browser: str) -> list[Path]:
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


def _browser_user_data_dir(browser: str) -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    if browser == "chrome":
        return local / "Google" / "Chrome" / "User Data"
    if browser == "yandex":
        return local / "Yandex" / "YandexBrowser" / "User Data"
    return Path()


def _scan_browser_profiles(browser: str) -> dict[str, Any]:
    user_data_dir = _browser_user_data_dir(browser)
    profiles: list[dict[str, Any]] = []
    if user_data_dir.is_dir():
        for path in sorted(user_data_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_dir():
                continue
            if path.name != "Default" and not path.name.lower().startswith("profile"):
                continue
            profiles.append(
                {
                    "profile_name": path.name,
                    "path": str(path),
                    "hint": f'profile_name: "{path.name}"',
                }
            )
    exe = _first_existing_path(_browser_candidates(browser))
    return {
        "browser": browser,
        "executable": str(exe) if exe else "",
        "executable_found": bool(exe),
        "user_data_dir": str(user_data_dir),
        "user_data_dir_exists": user_data_dir.is_dir(),
        "profiles": profiles,
        "profile_names_hint": [item["profile_name"] for item in profiles],
    }


def run_youtube_video_colab_browser_profiles(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoColabBrowserProfilesOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = "Becoming A Slut Wife Alma"
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    drive_reports = _root_compat_dirs(settings)["root"] / "reports"
    report = {
        "ok": True,
        "status": "diagnostic",
        "read_only": True,
        "config_path": str((config.root_dir / options.config_path).resolve()),
        "chrome": _scan_browser_profiles("chrome"),
        "yandex": _scan_browser_profiles("yandex"),
        "manual_action_required": [
            "Fill configs/youtube_video_colab_workers.yaml profile_name for each account.",
            "Fill notebook_url with https://colab.research.google.com/drive/<FILE_ID> for each notebook.",
            "Use existing logged-in browser profiles only; no passwords/tokens are stored.",
        ],
        "written_at": _now_iso(),
        "report_path": str(local_dirs["reports"] / "colab_browser_profiles_report.json"),
        "drive_report_path": str(drive_reports / "colab_browser_profiles_report.json"),
    }
    local_dirs["reports"].mkdir(parents=True, exist_ok=True)
    _write_json(local_dirs["reports"] / "colab_browser_profiles_report.json", report)
    try:
        drive_reports.mkdir(parents=True, exist_ok=True)
        _write_json(drive_reports / "colab_browser_profiles_report.json", report)
    except OSError:
        report["drive_report_write_warning"] = "failed to write drive report"
    return report


def _safe_email(email: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", email.strip()).strip("._-") or "worker"


def _colab_launcher_workers(config_path: Path) -> list[dict[str, Any]]:
    raw = _load_yaml(config_path)
    rows: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for group_name, group_data in raw.items():
            if not isinstance(group_data, dict):
                continue
            group_browser = str(group_data.get("browser") or group_name).strip().lower()
            group_profile_strategy = str(group_data.get("profile_strategy") or "").strip()
            group_launch_mode = str(group_data.get("launch_mode") or "").strip()
            for item in group_data.get("workers") or []:
                if not isinstance(item, dict):
                    continue
                email = str(item.get("email") or "").strip()
                if not email:
                    continue
                rows.append(
                    {
                        "group": str(group_name),
                        "email": email,
                        "browser": str(item.get("browser") or group_browser).strip().lower(),
                        "profile_strategy": str(item.get("profile_strategy") or group_profile_strategy).strip(),
                        "launch_mode": str(item.get("launch_mode") or group_launch_mode).strip(),
                        "profile_dir": str(item.get("profile_dir") or "").strip(),
                        "notebook_path": str(item.get("notebook_path") or "").strip(),
                        "notebook_url": str(item.get("notebook_url") or "").strip(),
                        "require_t4": bool(item.get("require_t4", False)),
                    }
                )
    if rows:
        return rows
    return _parse_colab_launcher_workers_fallback(config_path)


def _parse_colab_launcher_workers_fallback(config_path: Path) -> list[dict[str, Any]]:
    if not config_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    group_name = ""
    group_defaults: dict[str, str] = {}
    current_worker: dict[str, Any] | None = None
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            group_name = stripped[:-1]
            group_defaults = {}
            current_worker = None
            continue
        if not group_name:
            continue
        if stripped.startswith("- email:"):
            current_worker = {
                "group": group_name,
                "email": str(_parse_scalar(stripped.split(":", 1)[1])),
                "browser": group_defaults.get("browser", group_name),
                "profile_strategy": group_defaults.get("profile_strategy", ""),
                "launch_mode": group_defaults.get("launch_mode", ""),
                "profile_dir": "",
                "notebook_path": "",
                "notebook_url": "",
                "require_t4": False,
            }
            rows.append(current_worker)
            continue
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        value = _parse_scalar(raw_value)
        if current_worker is None:
            if key in {"browser", "profile_strategy", "launch_mode"}:
                group_defaults[key] = str(value)
            continue
        if key in {"browser", "profile_strategy", "launch_mode", "profile_dir", "notebook_path", "notebook_url"}:
            current_worker[key] = str(value)
        elif key == "require_t4":
            current_worker[key] = bool(value)
    return rows


def _status_by_worker_email(job_root: Path) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for row in _worker_statuses(job_root):
        email = str(row.get("worker_email") or row.get("email") or "").strip()
        if not email:
            continue
        statuses[email] = row
    return statuses


def _assigned_worker_dirs(job_root: Path, worker_email: str) -> dict[str, Path]:
    base = _job_dirs(job_root)["assigned"] / worker_email
    return {
        "base": base,
        "pending": base / "pending",
        "processing": base / "processing",
        "done": base / "done",
        "failed": base / "failed",
    }


def _ensure_assigned_dirs(job_root: Path, workers: list[str]) -> None:
    for email in workers:
        for path in _assigned_worker_dirs(job_root, email).values():
            path.mkdir(parents=True, exist_ok=True)


def _iter_assigned_job_files(job_root: Path, states: tuple[str, ...] = ("pending", "processing", "done", "failed")) -> list[tuple[str, str, Path]]:
    root = _job_dirs(job_root)["assigned"]
    rows: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return rows
    for worker_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        for state in states:
            state_dir = worker_dir / state
            if not state_dir.is_dir():
                continue
            for path in sorted(state_dir.glob("segment_*.json"), key=lambda p: p.name.lower()):
                rows.append((worker_dir.name, state, path))
    return rows


def _segment_id_from_path(path: Path) -> str:
    name = path.stem
    if "__" in name:
        name = name.split("__", 1)[0]
    return name


def _assigned_segment_ids(job_root: Path) -> set[str]:
    return {_segment_id_from_path(path) for _worker, _state, path in _iter_assigned_job_files(job_root)}


def _segment_output_path(job_root: Path, segment_id: str) -> Path:
    return _job_dirs(job_root)["segments"] / f"{segment_id}.mp4"


def _valid_segment_output(job_root: Path, segment_id: str, expected_duration_sec: float | None = None) -> bool:
    path = _segment_output_path(job_root, segment_id)
    return is_valid_video_file(path, expected_duration_sec=expected_duration_sec, require_audio=False)[0]


def _copy_or_move_children(source_dir: Path, target_dir: Path, *, execute: bool) -> list[str]:
    moved: list[str] = []
    if not source_dir.is_dir():
        return moved
    if execute:
        target_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_dir.iterdir(), key=lambda p: p.name.lower()):
        target = target_dir / source.name
        if target.exists():
            continue
        if execute:
            shutil.move(str(source), str(target))
        moved.append(f"{source} -> {target}")
    return moved


def migrate_video_job_to_assigned_queue(
    *,
    job_root: Path,
    settings: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    dirs = _job_dirs(job_root)
    workers = list(settings["workers"])
    actions: list[str] = []
    warnings: list[str] = []
    if execute:
        for key in ("assets_audio", "assets_frames", "assets_effects", "manifests", "global_pending", "segments", "reports", "status", "final"):
            dirs[key].mkdir(parents=True, exist_ok=True)
        _ensure_assigned_dirs(job_root, workers)

    # Move old export layout into the assigned-queue layout. Existing targets win.
    for source_rel, target_key in (
        ("input/audio", "assets_audio"),
        ("input/frames", "assets_frames"),
        ("input/effects", "assets_effects"),
        ("input/manifests", "manifests"),
    ):
        actions.extend(_copy_or_move_children(job_root / source_rel, dirs[target_key], execute=execute))

    root_manifest = job_root / "VIDEO_JOB_MANIFEST.json"
    target_manifest = dirs["manifests"] / "video_job_manifest.json"
    if root_manifest.is_file() and not target_manifest.is_file():
        if execute:
            shutil.copy2(root_manifest, target_manifest)
        actions.append(f"{root_manifest} -> {target_manifest}")

    assigned_ids = _assigned_segment_ids(job_root)
    global_ids = {_segment_id_from_path(path) for path in dirs["global_pending"].glob("segment_*.json")} if dirs["global_pending"].is_dir() else set()
    migrated = 0
    skipped = 0
    legacy_pending = dirs["legacy_pending"]
    if legacy_pending.is_dir():
        for source in sorted(legacy_pending.glob("segment_*.json"), key=lambda p: p.name.lower()):
            segment_id = _segment_id_from_path(source)
            target = dirs["global_pending"] / f"{segment_id}.json"
            if segment_id in global_ids or segment_id in assigned_ids or _segment_output_path(job_root, segment_id).is_file():
                skipped += 1
                continue
            if execute:
                dirs["global_pending"].mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
            actions.append(f"{source} -> {target}")
            global_ids.add(segment_id)
            migrated += 1
    legacy_left = sorted([p.name for p in legacy_pending.glob("segment_*.json")], key=str.lower) if legacy_pending.is_dir() else []
    if legacy_left:
        warnings.append("legacy segments/pending still contains json files; it is no longer an active queue")

    return {
        "execute": bool(execute),
        "job_root": str(job_root),
        "migrated_legacy_pending": migrated,
        "skipped_legacy_pending": skipped,
        "legacy_pending_left": legacy_left,
        "actions": actions,
        "warnings": warnings,
    }


def _collect_frames(frames_dir: Path) -> list[Path]:
    if not frames_dir.is_dir():
        return []
    return sorted(p for p in frames_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def _load_segment_jobs(story_dir: Path) -> dict[str, Any]:
    path = _segment_jobs_path(story_dir)
    if not path.is_file():
        return {}
    data = _read_json_if_exists(path)
    return data if isinstance(data, dict) else {}


def _load_timeline(story_dir: Path) -> dict[str, Any]:
    path = _timeline_path(story_dir)
    if not path.is_file():
        return {}
    data = _read_json_if_exists(path)
    return data if isinstance(data, dict) else {}


def _expected_frames(story_dir: Path, timeline: dict[str, Any]) -> int:
    frame_jobs = _read_json_if_exists(story_dir / "07_frames" / "frame_jobs.json")
    if isinstance(frame_jobs, dict) and isinstance(frame_jobs.get("jobs"), list):
        return len(frame_jobs["jobs"])
    report = _read_json_if_exists(story_dir / "logs" / "youtube_frames_runpod_report.json")
    if isinstance(report, dict):
        for key in ("expected_frames", "prompts_total", "generated_frames", "frames_generated"):
            try:
                value = int(report.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
    try:
        return int(timeline.get("total_frames") or 0)
    except (TypeError, ValueError):
        return 0


def _ensure_segment_manifests(
    *,
    config: OrchestratorConfig,
    settings: dict[str, Any],
    story_id: str,
    force: bool,
    execute: bool,
) -> dict[str, Any]:
    story_dir = _story_dir(config, settings, story_id)
    timeline = _timeline_path(story_dir)
    jobs = _segment_jobs_path(story_dir)
    if timeline.is_file() and jobs.is_file() and not force:
        return {
            "ok": True,
            "status": "already_prepared",
            "timeline_path": str(timeline),
            "segment_jobs_path": str(jobs),
        }
    return run_youtube_video_prepare_segments(
        config=config,
        options=YoutubeVideoPrepareSegmentsOptions(
            story_id=story_id,
            segment_sec=float(settings["segment_sec"]),
            execute=execute,
            force=force,
        ),
    )


def _resolve_effect_assets(config: OrchestratorConfig, settings: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    base = config.root_dir / "legacy" / "AutoVideo"
    found: list[dict[str, str]] = []
    missing: list[str] = []
    for spec_raw in settings["effects_assets"]:
        spec = str(spec_raw).replace("\\", "/").strip()
        if not spec:
            continue
        if any(ch in spec for ch in "*?[]"):
            parent = base / Path(spec).parent
            pattern = Path(spec).name
            matches = sorted(parent.glob(pattern)) if parent.is_dir() else []
            if not matches:
                missing.append(spec)
                continue
            for src in matches:
                if src.is_file():
                    found.append({"spec": spec, "source": str(src), "name": src.name})
            continue
        src = base / spec
        if src.is_file():
            found.append({"spec": spec, "source": str(src), "name": src.name})
        else:
            missing.append(spec)
    return found, missing


def _drive_segment_job(job: dict[str, Any]) -> dict[str, Any]:
    frames = []
    for frame in job.get("frames", []) if isinstance(job.get("frames"), list) else []:
        if not isinstance(frame, dict):
            continue
        name = Path(str(frame.get("name") or frame.get("path") or "")).name
        frames.append(
            {
                "frame_index": frame.get("frame_index"),
                "name": name,
                "input_frame_path": f"assets/frames/{name}",
                "duration_sec": frame.get("duration_sec"),
                "zoom_in": bool(frame.get("zoom_in", True)),
                "segment_start_sec": frame.get("segment_start_sec"),
                "segment_end_sec": frame.get("segment_end_sec"),
                "global_start_sec": frame.get("global_start_sec"),
                "global_end_sec": frame.get("global_end_sec"),
            }
        )
    segment_id = str(job.get("segment_id") or "")
    return {
        "schema_version": 1,
        "story_id": job.get("story_id"),
        "segment_id": segment_id,
        "segment_index": job.get("segment_index"),
        "render_mode": "video_only",
        "start_sec": job.get("start_sec"),
        "end_sec": job.get("end_sec"),
        "duration_sec": job.get("duration_sec"),
        "expected_duration_sec": job.get("expected_duration_sec") or job.get("duration_sec"),
        "frame_start_index": job.get("frame_start_index"),
        "frame_end_index": job.get("frame_end_index"),
        "output_segment_name": f"{segment_id}{VIDEO_EXT}",
        "frames": frames,
        "validation_rules": job.get("validation_rules") or {},
    }


def _worker_commands(story_slug: str, settings: dict[str, Any]) -> list[str]:
    root = "/content/drive/MyDrive/" + Path(str(settings["drive_root"])).name
    script = f"{root}/scripts/youtube_video_bootstrap_colab.py"
    return [
        f'CONTENT_FACTORY_WORKER_EMAIL="{email}" '
        'CONTENT_FACTORY_MAX_JOBS_PER_RUN="1" '
        f'python "{script}" '
        f'--drive-root "{root}" --story-slug "{story_slug}" '
        "--poll-seconds 10 --idle-timeout-min 15"
        for email in settings["workers"]
    ]


def _colab_bootstrap_cell(story_slug: str, worker_email: str, youtube_folder_id: str = "") -> str:
    folder_id = youtube_folder_id.strip() or "PASTE_CONTENTFACTORY_YOUTUBE_FOLDER_ID_HERE"
    return "\n".join(
        [
            "import os, runpy, sys, time",
            "from pathlib import Path",
            'os.environ["CONTENT_FACTORY_WORKER_EMAIL"] = "' + worker_email + '"',
            'os.environ["CONTENT_FACTORY_MAX_JOBS_PER_RUN"] = "1"',
            'os.environ["CONTENT_FACTORY_YOUTUBE_FOLDER_ID"] = "' + folder_id + '"',
            'os.environ["CONTENT_FACTORY_FFMPEG_PROGRESS_INTERVAL_SECONDS"] = "15"',
            'os.environ["CONTENT_FACTORY_FFMPEG_STALL_TIMEOUT_SECONDS"] = "600"',
            '# Debug only:',
            '# os.environ["CONTENT_FACTORY_SKIP_EFFECTS"] = "1"',
            '# os.environ["CONTENT_FACTORY_FFMPEG_PRESET"] = "veryfast"',
            "",
            "from google.colab import drive",
            "drive.mount('/content/drive')",
            "",
            "root = Path('/content/drive/MyDrive/ContentFactory_YouTube')",
            "folder_id = os.environ.get('CONTENT_FACTORY_YOUTUBE_FOLDER_ID', '').strip()",
            "if not root.exists():",
            "    if not folder_id or folder_id.startswith('PASTE_'):",
            "        raise RuntimeError('ContentFactory_YouTube is not in this MyDrive. Paste CONTENT_FACTORY_YOUTUBE_FOLDER_ID from the shared folder URL.')",
            "    from google.colab import auth",
            "    from googleapiclient.discovery import build",
            "    auth.authenticate_user()",
            "    service = build('drive', 'v3')",
            "    service.files().get(fileId=folder_id, fields='id,name,mimeType', supportsAllDrives=True).execute()",
            "    q = \"'root' in parents and trashed=false and name='ContentFactory_YouTube'\"",
            "    found = service.files().list(q=q, fields='files(id,mimeType,shortcutDetails)', supportsAllDrives=True).execute().get('files', [])",
            "    ok = any(x.get('mimeType') == 'application/vnd.google-apps.shortcut' and (x.get('shortcutDetails') or {}).get('targetId') == folder_id for x in found)",
            "    if not ok:",
            "        service.files().create(body={'name': 'ContentFactory_YouTube', 'mimeType': 'application/vnd.google-apps.shortcut', 'shortcutDetails': {'targetId': folder_id}, 'parents': ['root']}, fields='id', supportsAllDrives=True).execute()",
            "    for _ in range(30):",
            "        if root.exists():",
            "            break",
            "        time.sleep(1)",
            "if not root.exists():",
            "    raise RuntimeError('ContentFactory_YouTube shortcut is not visible. Check folder access for this Google account and reconnect Drive.')",
            "os.environ['CONTENT_FACTORY_YOUTUBE_ROOT'] = str(root)",
            "bootstrap = root / 'scripts' / 'youtube_video_bootstrap_colab.py'",
            "if not bootstrap.is_file():",
            "    raise RuntimeError(f'Bootstrap script missing: {bootstrap}. Run setup-colab-workers on Windows.')",
            "sys.argv = [str(bootstrap), '--story-slug', '" + story_slug + "', '--worker-email', '" + worker_email + "', '--max-jobs-per-run', '1', '--idle-timeout-min', '5', '--poll-seconds', '10']",
            "runpy.run_path(str(bootstrap), run_name='__main__')",
            "",
        ]
    )


def _build_export_plan(config: OrchestratorConfig, options: YoutubeVideoExportJobOptions) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    dirs = _video_dirs(story_dir)
    audio = _audio_path(story_dir)
    frames_dir = _frames_dir(story_dir)
    prepare = _ensure_segment_manifests(
        config=config,
        settings=settings,
        story_id=story_id,
        force=bool(options.force),
        execute=bool(options.execute),
    )
    segment_jobs = _load_segment_jobs(story_dir)
    timeline = _load_timeline(story_dir)
    jobs = [j for j in segment_jobs.get("jobs", []) if isinstance(j, dict)] if isinstance(segment_jobs.get("jobs"), list) else []
    frames = _collect_frames(frames_dir)
    expected_frames = _expected_frames(story_dir, timeline)
    effects_found, missing_effects = _resolve_effect_assets(config, settings)
    job_root = _drive_job_root(settings, story_slug)
    missing: list[str] = []
    if not story_dir.is_dir():
        missing.append(str(story_dir))
    if not audio.is_file():
        missing.append(str(audio))
    if not frames_dir.is_dir():
        missing.append(str(frames_dir))
    if not frames:
        missing.append(f"{frames_dir}/frame_*")
    if expected_frames and len(frames) != expected_frames:
        missing.append(f"frames count mismatch: expected {expected_frames}, found {len(frames)}")
    if not _timeline_path(story_dir).is_file():
        missing.append(str(_timeline_path(story_dir)))
    if not _segment_jobs_path(story_dir).is_file():
        missing.append(str(_segment_jobs_path(story_dir)))
    if not jobs:
        missing.append("segment_jobs.json has no jobs")
    if settings["require_effects"] and missing_effects:
        missing.extend(f"missing required effect: {item}" for item in missing_effects)
    return {
        "ok": not missing and bool(prepare.get("ok", False)),
        "status": "ready_to_export" if not missing and prepare.get("ok", False) else "blocked",
        "execute": bool(options.execute),
        "force": bool(options.force),
        "settings": settings,
        "story_id": story_id,
        "story_slug": story_slug,
        "story_dir": str(story_dir),
        "audio_path": str(audio),
        "frames_dir": str(frames_dir),
        "frames_count": len(frames),
        "expected_frames": expected_frames or len(frames),
        "timeline_path": str(_timeline_path(story_dir)),
        "segment_jobs_path": str(_segment_jobs_path(story_dir)),
        "total_segments": len(jobs),
        "drive_job_root": str(job_root),
        "local_reports_dir": str(dirs["reports"]),
        "prepare": prepare,
        "effects_found": bool(effects_found),
        "effects_assets_found": effects_found,
        "missing_effects": missing_effects,
        "render_will_continue_without_optional_effects": bool(missing_effects and not settings["require_effects"]),
        "missing": missing,
        "worker_commands": _worker_commands(story_slug, settings),
    }


def run_youtube_video_export_job(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoExportJobOptions,
) -> dict[str, Any]:
    plan = _build_export_plan(config, options)
    report_path = Path(str(plan["local_reports_dir"])) / REPORT_EXPORT
    if not plan["ok"]:
        report = {"ok": False, **plan, "written_at": _now_iso(), "report_path": str(report_path)}
        _write_json(report_path, report)
        return report
    if not options.execute:
        report = {**plan, "ok": True, "status": "dry_run", "written_at": _now_iso(), "report_path": str(report_path)}
        _write_json(report_path, report)
        return report

    settings = plan["settings"]
    story_dir = Path(str(plan["story_dir"]))
    story_slug = str(plan["story_slug"])
    job_root = Path(str(plan["drive_job_root"]))
    uploading = job_root.parent / f"_uploading_{story_slug}_{_timestamp()}"
    dirs = _job_dirs(uploading)
    for key in ("assets_audio", "assets_frames", "assets_effects", "manifests", "global_pending", "assigned", "segments", "reports", "final", "status"):
        path = dirs[key]
        path.mkdir(parents=True, exist_ok=True)

    audio = Path(str(plan["audio_path"]))
    frames = _collect_frames(Path(str(plan["frames_dir"])))
    shutil.copy2(audio, dirs["assets_audio"] / "narration.mp3")
    for frame in frames:
        shutil.copy2(frame, dirs["assets_frames"] / frame.name)
    shutil.copy2(Path(str(plan["timeline_path"])), dirs["manifests"] / "video_timeline.json")
    shutil.copy2(Path(str(plan["segment_jobs_path"])), dirs["manifests"] / "segment_jobs.json")

    story_info = {
        "schema_version": 1,
        "story_id": plan["story_id"],
        "story_slug": story_slug,
        "source_story_dir": plan["story_dir"],
        "story_manifest": _read_json_if_exists(_story_manifest_path(story_dir)),
        "exported_at": _now_iso(),
    }
    _write_json(dirs["input"] / "story_info.json", story_info)

    copied_effects = []
    for item in plan["effects_assets_found"]:
        src = Path(str(item["source"]))
        dst = dirs["assets_effects"] / src.name
        shutil.copy2(src, dst)
        copied_effects.append({**item, "drive_path": f"assets/effects/{src.name}"})

    segment_jobs = _load_segment_jobs(story_dir)
    jobs = [j for j in segment_jobs.get("jobs", []) if isinstance(j, dict)]
    for job in jobs:
        drive_job = _drive_segment_job(job)
        _write_json(dirs["global_pending"] / f"{drive_job['segment_id']}.json", drive_job)
    _ensure_assigned_dirs(uploading, list(settings["workers"]))

    expected_segments = len(jobs)
    expected_frames = int(plan["expected_frames"])
    _write_text(uploading / "EXPECTED_SEGMENTS.txt", f"{expected_segments}\n")
    _write_text(uploading / "EXPECTED_FRAMES.txt", f"{expected_frames}\n")
    manifest = {
        "schema_version": 1,
        "kind": "youtube_video_render_job",
        "created_at": _now_iso(),
        "story_id": plan["story_id"],
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "render_mode": settings["render_mode"],
        "segment_sec": settings["segment_sec"],
        "workers": settings["workers"],
        "workers_count": settings["workers_count"],
        "future_workers_max": settings["future_workers_max"],
        "expected_segments": expected_segments,
        "expected_frames": expected_frames,
        "assets": {
            "audio": "assets/audio/narration.mp3",
            "frames": "assets/frames",
            "effects": "assets/effects",
        },
        "manifests": {
            "timeline": "manifests/video_timeline.json",
            "segment_jobs": "manifests/segment_jobs.json",
        },
        "effects": {
            "require_effects": settings["require_effects"],
            "copied": copied_effects,
            "missing": plan["missing_effects"],
            "render_will_continue_without_optional_effects": plan["render_will_continue_without_optional_effects"],
        },
        "queue": {
            "global_pending": "queue/global_pending",
            "assigned": "queue/assigned",
        },
        "worker_commands": plan["worker_commands"],
    }
    _write_json(dirs["manifests"] / "video_job_manifest.json", manifest)
    _write_json(uploading / "VIDEO_JOB_MANIFEST.json", manifest)
    ready = {"schema_version": 1, "status": "ready", "ready_at": _now_iso(), "story_slug": story_slug}
    _write_json(uploading / "VIDEO_JOB_READY.json", ready)

    backup_path = ""
    if job_root.exists():
        backup = job_root.parent / f"_previous_{story_slug}_{_timestamp()}"
        shutil.move(str(job_root), str(backup))
        backup_path = str(backup)
    shutil.move(str(uploading), str(job_root))

    final_copied_effects = [
        {**item, "drive_path": str(job_root / item["drive_path"])}
        for item in copied_effects
    ]
    final_report = {
        **plan,
        "ok": True,
        "status": "exported",
        "drive_job_root": str(job_root),
        "backup_previous_job": backup_path,
        "copied_frames": len(frames),
        "copied_effects": final_copied_effects,
        "global_pending_segments_written": expected_segments,
        "ready_marker": str(job_root / "VIDEO_JOB_READY.json"),
        "manifest_path": str(job_root / "manifests" / "video_job_manifest.json"),
        "written_at": _now_iso(),
        "report_path": str(report_path),
    }
    _write_json(report_path, final_report)
    return final_report


def _count_files(path: Path, pattern: str) -> int:
    if not path.is_dir():
        return 0
    return len([p for p in path.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, pattern)])


def _worker_statuses(job_root: Path) -> list[dict[str, Any]]:
    status_dir = _job_dirs(job_root)["status"]
    if not status_dir.is_dir():
        return []
    rows = []
    for path in sorted(status_dir.glob("COLAB_WORKER_STATUS_*.json")):
        data = _read_json_if_exists(path)
        if isinstance(data, dict):
            rows.append({"path": str(path), **data})
    workers_dir = status_dir / "workers"
    if workers_dir.is_dir():
        for path in sorted(workers_dir.glob("*.json")):
            data = _read_json_if_exists(path)
            if isinstance(data, dict):
                rows.append({"path": str(path), **data})
    return rows


def _load_job_manifest(job_root: Path) -> dict[str, Any]:
    dirs = _job_dirs(job_root)
    data = _read_json_if_exists(dirs["manifests"] / "video_job_manifest.json")
    if isinstance(data, dict) and data:
        return data
    data = _read_json_if_exists(job_root / "VIDEO_JOB_MANIFEST.json")
    return data if isinstance(data, dict) else {}


def _all_queue_segment_locations(job_root: Path) -> dict[str, list[str]]:
    dirs = _job_dirs(job_root)
    locations: dict[str, list[str]] = {}
    for path in sorted(dirs["global_pending"].glob("segment_*.json")) if dirs["global_pending"].is_dir() else []:
        locations.setdefault(_segment_id_from_path(path), []).append(f"global_pending/{path.name}")
    for worker, state, path in _iter_assigned_job_files(job_root):
        locations.setdefault(_segment_id_from_path(path), []).append(f"assigned/{worker}/{state}/{path.name}")
    return locations


def _duplicate_locations(locations: dict[str, list[str]], *, states: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    duplicates = []
    for segment_id, rows in sorted(locations.items()):
        filtered = rows
        if states is not None:
            filtered = [row for row in rows if any(f"/{state}/" in row for state in states)]
        if len(filtered) > 1:
            duplicates.append({"segment_id": segment_id, "locations": filtered})
    return duplicates


def _expected_segment_ids(job_root: Path) -> list[str]:
    manifest = _load_job_manifest(job_root)
    expected = int(manifest.get("expected_segments") or 0) if manifest else 0
    if expected:
        return [f"segment_{idx:04d}" for idx in range(1, expected + 1)]
    segment_jobs = _read_json_if_exists(_job_dirs(job_root)["manifests"] / "segment_jobs.json")
    jobs = segment_jobs.get("jobs", []) if isinstance(segment_jobs, dict) else []
    return [str(job.get("segment_id")) for job in jobs if isinstance(job, dict) and job.get("segment_id")]


def _worker_counts(job_root: Path, workers: list[str]) -> dict[str, dict[str, int]]:
    counts = {email: {"pending": 0, "processing": 0, "done": 0, "failed": 0} for email in workers}
    for worker, state, _path in _iter_assigned_job_files(job_root):
        counts.setdefault(worker, {"pending": 0, "processing": 0, "done": 0, "failed": 0})
        counts[worker][state] = counts[worker].get(state, 0) + 1
    return counts


def _build_queue_status(
    config: OrchestratorConfig,
    story_id: str,
    *,
    write_report: bool = True,
    stale_minutes: int = 10,
    include_asset_preflight: bool = True,
    preflight_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    migration = migrate_video_job_to_assigned_queue(job_root=job_root, settings=settings, execute=False) if job_root.is_dir() else {}
    dirs = _job_dirs(job_root)
    manifest = _load_job_manifest(job_root)
    expected_segments = int(manifest.get("expected_segments") or len(_expected_segment_ids(job_root)) or 0) if isinstance(manifest, dict) else 0
    workers = list(settings["workers"])
    counts = _worker_counts(job_root, workers)
    locations = _all_queue_segment_locations(job_root)
    legacy_pending_left = sorted([p.name for p in dirs["legacy_pending"].glob("segment_*.json")], key=str.lower) if dirs["legacy_pending"].is_dir() else []
    segments_done_count = _count_files(dirs["segments"], "segment_*.mp4")
    local_segments = _count_files(local_dirs["segments"], "segment_*.mp4")
    workers_status = _worker_statuses(job_root)
    status_by_email = {
        str(row.get("worker_email") or ""): row
        for row in workers_status
        if row.get("worker_email")
    }

    now = datetime.now(timezone.utc)
    stale_minutes_eff = max(1, int(stale_minutes))
    stale_seconds = stale_minutes_eff * 60

    stale_processing_by_worker: dict[str, int] = {worker: 0 for worker in sorted(counts)}
    stale_processing_segments: list[dict[str, Any]] = []

    for worker, state, path in _iter_assigned_job_files(job_root, states=("processing",)):
        if state != "processing":
            continue
        segment_id = _segment_id_from_path(path)
        payload = _read_json_if_exists(path)
        payload = payload if isinstance(payload, dict) else {}
        verdict = _classify_processing_stale(
            segment_id,
            worker,
            payload,
            status_by_email.get(worker),
            now,
            stale_seconds,
        )
        if not verdict["stale"]:
            continue
        stale_processing_by_worker[worker] = stale_processing_by_worker.get(worker, 0) + 1
        try:
            attempt = int(payload.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        stale_processing_segments.append(
            {
                "segment_id": segment_id,
                "worker": worker,
                "reason": verdict["reason"],
                "age_seconds": verdict.get("age_seconds"),
                "heartbeat_at": verdict.get("heartbeat_at"),
                "attempt": attempt,
                "json_path": str(path),
            }
        )

    worker_details = {}
    for worker in sorted(counts):
        row = status_by_email.get(worker) or {}
        heartbeat = row.get("last_heartbeat_at") or row.get("heartbeat_at") or row.get("updated_at") or "NO_HEARTBEAT"
        status_dt = _parse_iso(row.get("last_heartbeat_at") or row.get("heartbeat_at") or row.get("updated_at"))
        if status_dt is not None and status_dt.tzinfo is None:
            status_dt = status_dt.replace(tzinfo=timezone.utc)
        status_age_seconds = int((now - status_dt).total_seconds()) if status_dt else None
        status_offline = (status_age_seconds is None) or (status_age_seconds >= stale_seconds)
        last_status = row.get("status") or "NO_HEARTBEAT"
        last_exit_reason = row.get("last_exit_reason") or ""
        worker_details[worker] = {
            "pending": counts.get(worker, {}).get("pending", 0),
            "processing": counts.get(worker, {}).get("processing", 0),
            "stale_processing": stale_processing_by_worker.get(worker, 0),
            "done": counts.get(worker, {}).get("done", 0),
            "failed": counts.get(worker, {}).get("failed", 0),
            "last_status": last_status,
            "current_segment": row.get("current_segment_id") or row.get("current_job") or "",
            "last_heartbeat_at": heartbeat,
            "worker_status_age_seconds": status_age_seconds,
            "worker_status_offline": status_offline,
            "last_message": row.get("last_message") or "",
            "last_error": row.get("last_error") or row.get("error") or "",
            "last_exit_reason": last_exit_reason,
            "exited_at": row.get("exited_at") or "",
            "exited_by_idle_timeout": str(last_status).lower() == "exited" and (last_exit_reason == "idle_timeout" or row.get("last_message") == "idle timeout"),
        }
    active_workers = sorted(
        worker
        for worker, detail in worker_details.items()
        if not bool(detail.get("worker_status_offline"))
    )
    offline_workers = sorted(
        worker
        for worker, detail in worker_details.items()
        if bool(detail.get("worker_status_offline"))
    )
    idle_workers = sorted(
        worker
        for worker, detail in worker_details.items()
        if str(detail.get("last_status") or "").lower() == "idle"
    )
    workers_with_assigned_pending = sorted(
        worker
        for worker, detail in worker_details.items()
        if int(detail.get("pending") or 0) > 0
    )
    workers_exited_by_idle_timeout = sorted(
        worker
        for worker, detail in worker_details.items()
        if bool(detail.get("exited_by_idle_timeout"))
    )

    warnings: list[str] = []
    if legacy_pending_left:
        warnings.append("legacy segments/pending still contains json files")
    stale_count = sum(stale_processing_by_worker.values())
    if stale_count:
        warnings.append(
            f"{stale_count} processing segment(s) have stale heartbeat (older than {stale_minutes_eff} min); "
            "run `youtube video reclaim-stale-segments --execute` to release them"
        )

    checkpoints = _scan_segment_checkpoints(job_root)
    if checkpoints["partial_segments_count"] > 0:
        warnings.append(
            f"{checkpoints['partial_segments_count']} segment(s) have orphan partial/no-marker checkpoint files; "
            "run `youtube video cleanup-partial-checkpoints --execute` to clean them"
        )

    asset_preflight_ok = True
    asset_preflight_included = False
    missing_asset_segments_count = 0
    missing_assets_count = 0
    missing_asset_segments: list[dict[str, Any]] = []
    if include_asset_preflight and job_root.is_dir():
        preflight = preflight_report
        if preflight is None:
            try:
                preflight = run_youtube_video_validate_job_assets(
                    config=config,
                    options=YoutubeVideoValidateJobAssetsOptions(story_id=story_id, dry_run=True),
                )
            except Exception:
                preflight = None
                asset_preflight_ok = False
        if isinstance(preflight, dict):
            asset_preflight_included = True
            asset_preflight_ok = bool(preflight.get("ok"))
            missing_asset_segments_count = int(preflight.get("missing_asset_segments_count") or 0)
            missing_assets_count = int(preflight.get("missing_frames_count") or 0)
            for seg, items in (preflight.get("missing_by_segment") or {}).items():
                missing_asset_segments.append({"segment_id": seg, "missing_count": len(items), "missing_frames": [it.get("name") for it in items]})
    if asset_preflight_included and not asset_preflight_ok and missing_asset_segments_count:
        warnings.append(
            f"input assets missing for {missing_asset_segments_count} segment(s) ({missing_assets_count} unique frames); "
            "run `youtube video validate-job-assets` for details"
        )

    permanent_failed_count = 0
    for _worker, state, path in _iter_assigned_job_files(job_root, states=("failed",)):
        if state != "failed":
            continue
        payload = _read_json_if_exists(path)
        if isinstance(payload, dict) and str(payload.get("error_kind") or "") == "input_asset_missing":
            permanent_failed_count += 1

    assigned_pending_total = sum(counts.get(w, {}).get("pending", 0) for w in counts)
    assigned_processing_total = sum(counts.get(w, {}).get("processing", 0) for w in counts)
    assigned_failed_total = sum(counts.get(w, {}).get("failed", 0) for w in counts)
    assigned_done_total = sum(counts.get(w, {}).get("done", 0) for w in counts)
    total_segments = expected_segments

    report = {
        "ok": True,
        "status": "ready" if (job_root / "VIDEO_JOB_READY.json").is_file() else "not_ready",
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "job_exists": job_root.is_dir(),
        "job_ready": (job_root / "VIDEO_JOB_READY.json").is_file(),
        "total_segments": total_segments,
        "expected_segments": expected_segments,
        "stale_minutes_threshold": stale_minutes_eff,
        "global_pending": _count_files(dirs["global_pending"], "segment_*.json"),
        "assigned_pending_total": assigned_pending_total,
        "assigned_processing_total": assigned_processing_total,
        "assigned_done_total": assigned_done_total,
        "assigned_failed_total": assigned_failed_total,
        "assigned_pending_by_worker": {worker: counts.get(worker, {}).get("pending", 0) for worker in sorted(counts)},
        "assigned_processing_by_worker": {worker: counts.get(worker, {}).get("processing", 0) for worker in sorted(counts)},
        "assigned_done_by_worker": {worker: counts.get(worker, {}).get("done", 0) for worker in sorted(counts)},
        "assigned_failed_by_worker": {worker: counts.get(worker, {}).get("failed", 0) for worker in sorted(counts)},
        "stale_processing_by_worker": stale_processing_by_worker,
        "stale_processing_count": stale_count,
        "stale_processing_segments": stale_processing_segments,
        "segments_done_count": segments_done_count,
        "final_marker_count": checkpoints["final_marker_count"],
        "checkpointed_segments_count": checkpoints["checkpointed_segments_count"],
        "partial_segments_count": checkpoints["partial_segments_count"],
        "checkpoints_per_segment": checkpoints["per_segment"],
        "asset_preflight_ok": asset_preflight_ok,
        "asset_preflight_included": asset_preflight_included,
        "missing_asset_segments_count": missing_asset_segments_count,
        "missing_assets_count": missing_assets_count,
        "missing_asset_segments": missing_asset_segments,
        "permanent_failed_count": permanent_failed_count,
        "duplicate_assigned_segments": _duplicate_locations(locations, states=("pending", "processing", "done", "failed")),
        "duplicate_processing_segments": _duplicate_locations(locations, states=("processing",)),
        "legacy_segments_pending_json": legacy_pending_left,
        "warnings": warnings,
        "workers": workers_status,
        "worker_count": len(workers),
        "active_worker_count": len(active_workers),
        "active_workers": active_workers,
        "offline_workers": offline_workers,
        "idle_workers": idle_workers,
        "idle_worker_count": len(idle_workers),
        "workers_with_assigned_pending": workers_with_assigned_pending,
        "workers_with_assigned_pending_count": len(workers_with_assigned_pending),
        "workers_exited_by_idle_timeout": workers_exited_by_idle_timeout,
        "workers_exited_by_idle_timeout_count": len(workers_exited_by_idle_timeout),
        "worker_details": worker_details,
        "can_import": segments_done_count > 0,
        "local_segments": local_segments,
        "can_assemble": expected_segments > 0 and local_segments >= expected_segments,
        "migration": migration,
        "report_path": str(local_dirs["reports"] / REPORT_QUEUE_STATUS),
        "written_at": _now_iso(),
    }
    if write_report:
        _write_json(local_dirs["reports"] / REPORT_QUEUE_STATUS, report)
    return report


def run_youtube_video_setup_colab_workers(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoSetupColabWorkersOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    root_dirs = _root_compat_dirs(settings)
    source_script = config.root_dir / "colab" / "youtube_video_worker_colab.py"
    source_bootstrap = config.root_dir / "colab" / "youtube_video_bootstrap_colab.py"
    target_script = root_dirs["scripts"] / "youtube_video_worker_colab.py"
    target_bootstrap = root_dirs["scripts"] / "youtube_video_bootstrap_colab.py"
    readme = root_dirs["compat_queue_root"] / "README_CURRENT_VIDEO_QUEUE.txt"
    bootstrap_cell = root_dirs["scripts"] / "COLAB_YOUTUBE_VIDEO_BOOTSTRAP_CELL.py"
    migration = migrate_video_job_to_assigned_queue(job_root=job_root, settings=settings, execute=bool(options.execute)) if job_root.exists() else {}
    actions = []
    if options.execute:
        root_dirs["scripts"].mkdir(parents=True, exist_ok=True)
        root_dirs["compat_queue_pending"].mkdir(parents=True, exist_ok=True)
        if source_script.is_file():
            shutil.copy2(source_script, target_script)
            actions.append(f"{source_script} -> {target_script}")
        if source_bootstrap.is_file():
            shutil.copy2(source_bootstrap, target_bootstrap)
            actions.append(f"{source_bootstrap} -> {target_bootstrap}")
        first_worker = list(settings["workers"])[0] if settings["workers"] else ""
        _write_text(bootstrap_cell, _colab_bootstrap_cell(story_slug, first_worker, options.youtube_folder_id))
        actions.append(f"wrote {bootstrap_cell}")
        try:
            _write_text(
                readme,
                "Compatibility folder for old Colab notebooks.\n"
                "Old notebooks may check ROOT/queue/video/pending, but actual YouTube video jobs are assigned by Windows dispatcher.\n"
                f"Current job queue: video_jobs/{story_slug}/queue/assigned/<worker_email>/pending\n"
                "Colab workers must read only their own assigned folder and must not claim global_pending.\n"
                "Run the bootstrap, not the worker directly. If /content/drive/MyDrive/ContentFactory_YouTube is missing,\n"
                "set CONTENT_FACTORY_YOUTUBE_FOLDER_ID in the Colab cell so bootstrap can create/check the Drive shortcut.\n"
                f"Example cell: scripts/{bootstrap_cell.name}\n",
            )
        except OSError as exc:
            actions.append(f"warning: failed to update {readme}: {exc}")
        _ensure_assigned_dirs(job_root, list(settings["workers"]))
    report = {
        "ok": source_script.is_file() and source_bootstrap.is_file() and (not options.execute or (target_script.is_file() and target_bootstrap.is_file())),
        "status": "setup" if options.execute else "dry_run",
        "execute": bool(options.execute),
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "root_worker_script_source": str(source_script),
        "root_bootstrap_script_source": str(source_bootstrap),
        "root_worker_script": str(target_script),
        "root_bootstrap_script": str(target_bootstrap),
        "root_worker_script_exists": target_script.is_file(),
        "root_bootstrap_script_exists": target_bootstrap.is_file(),
        "colab_bootstrap_cell_path": str(bootstrap_cell),
        "colab_bootstrap_cell": _colab_bootstrap_cell(story_slug, list(settings["workers"])[0] if settings["workers"] else "", options.youtube_folder_id),
        "youtube_folder_id_set": bool(options.youtube_folder_id.strip()),
        "root_compat_queue": str(root_dirs["compat_queue_pending"]),
        "root_compat_queue_exists": root_dirs["compat_queue_pending"].is_dir(),
        "workers": list(settings["workers"]),
        "migration": migration,
        "actions": actions,
        "report_path": str(local_dirs["reports"] / REPORT_SETUP),
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_SETUP, report)
    return report


def run_youtube_video_drive_status(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoDriveStatusOptions,
) -> dict[str, Any]:
    report = _build_queue_status(config, str(options.story_id).strip(), write_report=False)
    report["report_path"] = str(_video_dirs(_story_dir(config, _render_settings(config), str(options.story_id).strip()))["reports"] / REPORT_STATUS)
    _write_json(Path(str(report["report_path"])), report)
    return report


def run_youtube_video_queue_status(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoQueueStatusOptions,
) -> dict[str, Any]:
    return _build_queue_status(
        config,
        str(options.story_id).strip(),
        write_report=True,
        stale_minutes=int(getattr(options, "stale_minutes", 10) or 10),
        include_asset_preflight=not bool(getattr(options, "quick", False)),
    )


def run_youtube_video_workers_audit(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoWorkersAuditOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    job_dirs = _job_dirs(job_root)
    launcher_config_path = (config.root_dir / options.config_path).resolve() if not options.config_path.is_absolute() else options.config_path
    launcher_workers = _colab_launcher_workers(launcher_config_path)
    render_workers = list(settings["workers"])
    launcher_emails = [str(row.get("email") or "").strip() for row in launcher_workers if str(row.get("email") or "").strip()]
    launcher_email_set = set(launcher_emails)
    render_email_set = set(render_workers)
    missing_in_render_config = sorted(launcher_email_set - render_email_set)
    extra_in_render_config = sorted(render_email_set - launcher_email_set)
    assigned_root = job_dirs["assigned"]
    assigned_folder_names = sorted([p.name for p in assigned_root.iterdir() if p.is_dir()], key=str.lower) if assigned_root.is_dir() else []
    statuses_by_email = _status_by_worker_email(job_root)
    now = datetime.now(timezone.utc)
    stale_seconds = 10 * 60

    rows: list[dict[str, Any]] = []
    for worker in launcher_workers:
        email = str(worker.get("email") or "").strip()
        assigned_dirs = _assigned_worker_dirs(job_root, email)
        status = statuses_by_email.get(email) or {}
        last_heartbeat = (
            status.get("last_heartbeat_at")
            or status.get("heartbeat_at")
            or status.get("updated_at")
            or ""
        )
        heartbeat_dt = _parse_iso(last_heartbeat)
        heartbeat_age_seconds = int((now - heartbeat_dt).total_seconds()) if heartbeat_dt else None
        rows.append(
            {
                "group": worker.get("group", ""),
                "email": email,
                "browser": worker.get("browser", ""),
                "profile_dir": worker.get("profile_dir", ""),
                "profile_dir_exists": Path(str(worker.get("profile_dir") or "")).is_dir(),
                "worker_id": email,
                "safe_worker_id": _safe_email(email),
                "in_render_queue_config": email in render_email_set,
                "assigned_dir": str(assigned_dirs["base"]),
                "assigned_dir_exists": assigned_dirs["base"].is_dir(),
                "assigned_pending_dir_exists": assigned_dirs["pending"].is_dir(),
                "assigned_processing_dir_exists": assigned_dirs["processing"].is_dir(),
                "assigned_done_dir_exists": assigned_dirs["done"].is_dir(),
                "assigned_failed_dir_exists": assigned_dirs["failed"].is_dir(),
                "assigned_pending_count": _count_files(assigned_dirs["pending"], "segment_*.json"),
                "assigned_processing_count": _count_files(assigned_dirs["processing"], "segment_*.json"),
                "assigned_done_count": _count_files(assigned_dirs["done"], "segment_*.json"),
                "assigned_failed_count": _count_files(assigned_dirs["failed"], "segment_*.json"),
                "heartbeat_exists": bool(last_heartbeat),
                "last_heartbeat": last_heartbeat,
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "worker_status_offline": heartbeat_age_seconds is None or heartbeat_age_seconds >= stale_seconds,
                "last_status": status.get("status") or "NO_HEARTBEAT",
                "current_segment": status.get("current_segment_id") or status.get("current_job") or "",
                "status_path": status.get("path", ""),
                "notebook_path": worker.get("notebook_path", ""),
                "notebook_url": worker.get("notebook_url", ""),
            }
        )

    missing_assigned_dirs = sorted([row["email"] for row in rows if not row["assigned_dir_exists"]])
    missing_heartbeat = sorted([row["email"] for row in rows if not row["heartbeat_exists"]])
    config_alignment_ok = not missing_in_render_config and not extra_in_render_config
    assigned_dirs_complete = not missing_assigned_dirs
    report = {
        "ok": config_alignment_ok,
        "status": "aligned" if config_alignment_ok else "mismatch",
        "story_id": story_id,
        "story_slug": story_slug,
        "launcher_config_path": str(launcher_config_path),
        "render_queue_config_path": str((config.root_dir / CONFIG_PATH).resolve()),
        "drive_job_root": str(job_root),
        "job_exists": job_root.is_dir(),
        "job_ready": (job_root / "VIDEO_JOB_READY.json").is_file(),
        "workers_in_launcher": launcher_emails,
        "workers_in_launcher_count": len(launcher_emails),
        "workers_in_render_queue_config": render_workers,
        "workers_in_render_queue_config_count": len(render_workers),
        "missing_in_render_config": missing_in_render_config,
        "extra_in_render_config": extra_in_render_config,
        "mismatch_count": len(missing_in_render_config) + len(extra_in_render_config),
        "existing_assigned_folders": assigned_folder_names,
        "missing_assigned_dirs": missing_assigned_dirs,
        "assigned_dirs_complete": assigned_dirs_complete,
        "missing_heartbeat": missing_heartbeat,
        "workers": rows,
        "single_worker_command": (
            'python tools/colab_launcher/launch_colab_group.py --config "configs/youtube_video_colab_workers.yaml" '
            "--group yandex --limit 1 --mode prepared-notebook-url --wait-after-open-seconds 0 --wait-for-run-start-seconds 0"
        ),
        "five_yandex_workers_command": (
            'python tools/colab_launcher/launch_colab_group.py --config "configs/youtube_video_colab_workers.yaml" '
            "--group yandex --mode prepared-notebook-url --auto-run --sequential"
        ),
        "ten_workers_command": (
            'python tools/colab_launcher/launch_colab_group.py --config "configs/youtube_video_colab_workers.yaml" '
            "--group all --mode prepared-notebook-url --auto-run --sequential"
        ),
        "queue_status_command": f'python -m orchestrator youtube video queue-status --story-id "{story_id}" --quick',
        "report_path": str(local_dirs["reports"] / REPORT_WORKERS_AUDIT),
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_WORKERS_AUDIT, report)
    return report


def _parse_workers_arg(workers_arg: str, settings: dict[str, Any]) -> list[str]:
    if workers_arg.strip():
        return [item.strip() for item in workers_arg.split(",") if item.strip()]
    return list(settings["workers"])


def _update_job_assignment_payload(path: Path, worker: str, status: str) -> dict[str, Any]:
    payload = _read_json_if_exists(path)
    if not isinstance(payload, dict):
        payload = {}
    try:
        attempt = int(payload.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    payload.update(
        {
            "assigned_worker": worker,
            "assigned_at": _now_iso(),
            "attempt": max(1, attempt),
            "status": status,
        }
    )
    return payload


def run_youtube_video_dispatch_segments(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoDispatchSegmentsOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    workers = _parse_workers_arg(str(options.workers), settings)
    migration = migrate_video_job_to_assigned_queue(job_root=job_root, settings={**settings, "workers": workers}, execute=bool(options.execute))
    dirs = _job_dirs(job_root)
    if options.execute:
        _ensure_assigned_dirs(job_root, workers)
    counts = _worker_counts(job_root, workers)
    assigned_ids = _assigned_segment_ids(job_root)
    assigned_ids.update(_segment_id_from_path(path) for path in dirs["segments"].glob("segment_*.mp4") if dirs["segments"].is_dir())
    pending_files = sorted(dirs["global_pending"].glob("segment_*.json"), key=lambda p: p.name.lower()) if dirs["global_pending"].is_dir() else []
    assignments: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    blocked_missing_assets: list[dict[str, Any]] = []
    max_total = max(0, int(options.max_total_assigned))
    target = max(0, int(options.target_per_worker))
    pending_index = 0
    frame_resolution_cache: dict[str, bool] = {}
    for worker in workers:
        if len(assignments) >= max_total:
            break
        current_pending = counts.get(worker, {}).get("pending", 0)
        capacity = max(0, target - current_pending)
        for _ in range(capacity):
            if len(assignments) >= max_total:
                break
            selected: Path | None = None
            selected_payload: dict[str, Any] | None = None
            while pending_index < len(pending_files):
                candidate = pending_files[pending_index]
                pending_index += 1
                segment_id = _segment_id_from_path(candidate)
                if segment_id in assigned_ids:
                    skipped.append({"segment_id": segment_id, "reason": "already_assigned_or_done", "path": str(candidate)})
                    continue
                payload_for_check = _read_json_if_exists(candidate)
                payload_for_check = payload_for_check if isinstance(payload_for_check, dict) else {}
                required_names = _segment_required_frame_names(payload_for_check)
                missing_for_segment: list[str] = []
                for name in required_names:
                    if name not in frame_resolution_cache:
                        ok_frame, _resolved, _size = _resolve_frame_on_drive(job_root, name)
                        frame_resolution_cache[name] = ok_frame
                    if not frame_resolution_cache[name]:
                        missing_for_segment.append(name)
                if missing_for_segment:
                    blocked_missing_assets.append(
                        {
                            "segment_id": segment_id,
                            "missing_frames": missing_for_segment,
                            "missing_count": len(missing_for_segment),
                            "from": str(candidate),
                            "reason": "input_asset_missing",
                        }
                    )
                    skipped.append({"segment_id": segment_id, "reason": "input_asset_missing", "missing_count": str(len(missing_for_segment)), "path": str(candidate)})
                    continue
                selected = candidate
                selected_payload = payload_for_check
                break
            if selected is None:
                break
            segment_id = _segment_id_from_path(selected)
            target_path = _assigned_worker_dirs(job_root, worker)["pending"] / selected.name
            payload = _update_job_assignment_payload(selected, worker, "assigned")
            if options.execute:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _write_json(target_path, payload)
                selected.unlink()
            assigned_ids.add(segment_id)
            assignments.append({"segment_id": segment_id, "worker": worker, "from": str(selected), "to": str(target_path)})
    # На случай если global_pending содержит сегменты с missing assets, но они не дошли до per-worker
    # цикла (capacity исчерпана). Делаем второй проход только для блокирующего отчёта.
    while pending_index < len(pending_files):
        candidate = pending_files[pending_index]
        pending_index += 1
        segment_id = _segment_id_from_path(candidate)
        if segment_id in assigned_ids:
            continue
        payload_for_check = _read_json_if_exists(candidate)
        payload_for_check = payload_for_check if isinstance(payload_for_check, dict) else {}
        required_names = _segment_required_frame_names(payload_for_check)
        missing_for_segment: list[str] = []
        for name in required_names:
            if name not in frame_resolution_cache:
                ok_frame, _resolved, _size = _resolve_frame_on_drive(job_root, name)
                frame_resolution_cache[name] = ok_frame
            if not frame_resolution_cache[name]:
                missing_for_segment.append(name)
        if missing_for_segment:
            blocked_missing_assets.append(
                {
                    "segment_id": segment_id,
                    "missing_frames": missing_for_segment,
                    "missing_count": len(missing_for_segment),
                    "from": str(candidate),
                    "reason": "input_asset_missing",
                }
            )

    status = _build_queue_status(config, story_id, write_report=False, include_asset_preflight=False)
    report = {
        "ok": True,
        "status": "dispatched" if options.execute else "dry_run",
        "execute": bool(options.execute),
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "workers": workers,
        "target_per_worker": target,
        "max_total_assigned": max_total,
        "assigned_count": len(assignments),
        "assignments": assignments,
        "skipped": skipped,
        "blocked_missing_assets_count": len(blocked_missing_assets),
        "blocked_missing_assets": blocked_missing_assets,
        "migration": migration,
        "queue_status": status,
        "report_path": str(local_dirs["reports"] / REPORT_DISPATCH),
        "drive_report_path": str(dirs["reports"] / "dispatch_segments_report.json"),
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_DISPATCH, report)
    if options.execute:
        _write_json(dirs["reports"] / "dispatch_segments_report.json", report)
    return report


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _job_heartbeat_time(job_payload: dict[str, Any], worker_status: dict[str, Any] | None = None) -> datetime | None:
    candidates = [
        job_payload.get("heartbeat_at"),
        job_payload.get("updated_at"),
        job_payload.get("processing_started_at"),
    ]
    if worker_status:
        candidates.extend([worker_status.get("heartbeat_at"), worker_status.get("last_heartbeat_at"), worker_status.get("updated_at")])
    parsed = [_parse_iso(value) for value in candidates]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _classify_processing_stale(
    segment_id: str,
    worker: str,
    payload: dict[str, Any],
    worker_status: dict[str, Any] | None,
    now: datetime,
    stale_seconds: int,
) -> dict[str, Any]:
    """Return verdict for one processing segment.

    Поля: ``stale`` (bool), ``reason`` (str), ``age_seconds`` (int|None), ``heartbeat_at`` (str|empty).
    """
    if not worker_status:
        return {"stale": True, "reason": "worker_status_missing", "age_seconds": None, "heartbeat_at": ""}
    current_segment = str(
        worker_status.get("current_segment_id")
        or worker_status.get("current_job")
        or ""
    ).strip()
    if current_segment and current_segment != segment_id:
        return {
            "stale": True,
            "reason": f"worker_status_current_segment_mismatch:{current_segment}",
            "age_seconds": None,
            "heartbeat_at": "",
        }
    heartbeat = _job_heartbeat_time(payload, worker_status)
    if heartbeat is None:
        return {"stale": True, "reason": "missing_processing_heartbeat", "age_seconds": None, "heartbeat_at": ""}
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    age_seconds = int((now - heartbeat).total_seconds())
    if age_seconds >= stale_seconds:
        return {
            "stale": True,
            "reason": f"heartbeat_stale_{age_seconds // 60}m",
            "age_seconds": age_seconds,
            "heartbeat_at": heartbeat.isoformat(),
        }
    return {
        "stale": False,
        "reason": "fresh",
        "age_seconds": age_seconds,
        "heartbeat_at": heartbeat.isoformat(),
    }


def _delete_partial_segment_mp4(job_root: Path, segment_id: str, expected_duration: float | None) -> dict[str, Any]:
    """Удаляет partial output mp4, если он есть и не validated.

    Возвращает diag для отчёта: ``existed``, ``valid``, ``deleted``, ``size_bytes``, ``path``.
    """
    out_path = _segment_output_path(job_root, segment_id)
    if not out_path.is_file():
        return {"existed": False, "valid": False, "deleted": False, "size_bytes": 0, "path": str(out_path)}
    try:
        size = out_path.stat().st_size
    except OSError:
        size = 0
    if _valid_segment_output(job_root, segment_id, expected_duration):
        return {"existed": True, "valid": True, "deleted": False, "size_bytes": size, "path": str(out_path)}
    try:
        out_path.unlink()
        deleted = True
    except OSError:
        deleted = False
    return {"existed": True, "valid": False, "deleted": deleted, "size_bytes": size, "path": str(out_path)}


def run_youtube_video_reclaim_stale_segments(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoReclaimStaleSegmentsOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    execute = bool(options.execute) and not bool(options.dry_run)
    migrate_video_job_to_assigned_queue(job_root=job_root, settings=settings, execute=execute)
    dirs = _job_dirs(job_root)
    now = datetime.now(timezone.utc)
    stale_minutes = max(1, int(options.stale_minutes))
    stale_seconds = stale_minutes * 60
    max_attempts = max(1, int(options.max_attempts))
    worker_status_by_email = {
        str(row.get("worker_email") or ""): row
        for row in _worker_statuses(job_root)
        if row.get("worker_email")
    }

    reclaimed: list[dict[str, Any]] = []
    moved_to_failed: list[dict[str, Any]] = []
    marked_done: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    scanned_workers: set[str] = set()
    scanned_segments = 0

    for worker, state, path in _iter_assigned_job_files(job_root, states=("processing",)):
        if state != "processing":
            continue
        scanned_workers.add(worker)
        scanned_segments += 1
        segment_id = _segment_id_from_path(path)
        payload = _read_json_if_exists(path)
        payload = payload if isinstance(payload, dict) else {}
        expected_duration = payload.get("expected_duration_sec") or payload.get("duration_sec")
        try:
            expected_duration_float = float(expected_duration) if expected_duration is not None else None
        except (TypeError, ValueError):
            expected_duration_float = None

        if _valid_segment_output(job_root, segment_id, expected_duration_float):
            target = _assigned_worker_dirs(job_root, worker)["done"] / path.name
            payload.update(
                {
                    "status": "done",
                    "done_at": _now_iso(),
                    "output_segment_path": str(_segment_output_path(job_root, segment_id)),
                }
            )
            if execute:
                _write_json(target, payload)
                path.unlink()
            entry = {
                "segment_id": segment_id,
                "worker": worker,
                "action": "marked_done",
                "from": str(path),
                "to": str(target),
                "reason": "valid_segment_output_present",
            }
            marked_done.append(entry)
            details.append(entry)
            continue

        if str(payload.get("error_kind") or "") == "input_asset_missing":
            entry = {
                "segment_id": segment_id,
                "worker": worker,
                "action": "skipped_input_asset_missing",
                "reason": "input_asset_missing",
            }
            details.append(entry)
            continue

        verdict = _classify_processing_stale(
            segment_id,
            worker,
            payload,
            worker_status_by_email.get(worker),
            now,
            stale_seconds,
        )
        if not verdict["stale"]:
            entry = {
                "segment_id": segment_id,
                "worker": worker,
                "action": "skipped_fresh",
                "age_seconds": verdict.get("age_seconds"),
                "heartbeat_at": verdict.get("heartbeat_at"),
                "reason": verdict.get("reason"),
            }
            fresh.append(entry)
            details.append(entry)
            continue

        try:
            attempt = int(payload.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        try:
            reclaim_count = int(payload.get("reclaim_count") or 0)
        except (TypeError, ValueError):
            reclaim_count = 0
        previous_worker = str(payload.get("assigned_worker") or worker)
        partial_diag = _delete_partial_segment_mp4(job_root, segment_id, expected_duration_float) if execute else {
            "existed": _segment_output_path(job_root, segment_id).is_file(),
            "valid": False,
            "deleted": False,
            "size_bytes": 0,
            "path": str(_segment_output_path(job_root, segment_id)),
        }

        new_attempt = attempt + 1
        common_update = {
            "attempt": new_attempt,
            "reclaim_count": reclaim_count + 1,
            "last_reclaimed_at": _now_iso(),
            "last_reclaimed_reason": verdict["reason"],
            "previous_worker_email": previous_worker,
            "reclaimed_from_worker": previous_worker,
            "reclaimed_age_seconds": verdict.get("age_seconds"),
            "partial_output_cleanup": partial_diag,
        }

        if new_attempt > max_attempts:
            target = _assigned_worker_dirs(job_root, worker)["failed"] / path.name
            payload.update({"status": "failed", "failed_at": _now_iso(), **common_update})
            if execute:
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_json(target, payload)
                path.unlink()
            entry = {
                "segment_id": segment_id,
                "worker": worker,
                "action": "moved_to_failed",
                "from": str(path),
                "to": str(target),
                "attempt": new_attempt,
                "max_attempts": max_attempts,
                "reason": verdict["reason"],
                "partial_output_cleanup": partial_diag,
            }
            moved_to_failed.append(entry)
            details.append(entry)
            continue

        payload.update({"status": "pending", **common_update})
        payload.pop("assigned_worker", None)
        target = dirs["global_pending"] / path.name
        if execute:
            dirs["global_pending"].mkdir(parents=True, exist_ok=True)
            _write_json(target, payload)
            path.unlink()
        entry = {
            "segment_id": segment_id,
            "worker": worker,
            "action": "reclaimed_to_pending",
            "from": str(path),
            "to": str(target),
            "attempt": new_attempt,
            "max_attempts": max_attempts,
            "previous_worker_email": previous_worker,
            "age_seconds": verdict.get("age_seconds"),
            "heartbeat_at": verdict.get("heartbeat_at"),
            "reason": verdict["reason"],
            "partial_output_cleanup": partial_diag,
        }
        reclaimed.append(entry)
        details.append(entry)

    status = _build_queue_status(config, story_id, stale_minutes=stale_minutes, write_report=False, include_asset_preflight=False)
    report = {
        "ok": True,
        "status": "reclaimed" if execute else "dry_run",
        "execute": execute,
        "dry_run": not execute,
        "story_id": story_id,
        "story_slug": story_slug,
        "stale_minutes": stale_minutes,
        "max_attempts": max_attempts,
        "scanned_workers_count": len(scanned_workers),
        "scanned_workers": sorted(scanned_workers),
        "scanned_processing_segments": scanned_segments,
        "reclaimed_count": len(reclaimed),
        "moved_to_failed_count": len(moved_to_failed),
        "marked_done_count": len(marked_done),
        "skipped_count": len(fresh),
        "reclaimed": reclaimed,
        "moved_to_failed": moved_to_failed,
        "marked_done": marked_done,
        "fresh_processing": fresh,
        "details": details,
        "queue_status": status,
        "report_path": str(local_dirs["reports"] / REPORT_RECLAIM),
        "drive_report_path": str(dirs["reports"] / REPORT_RECLAIM),
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_RECLAIM, report)
    if execute:
        _write_json(dirs["reports"] / REPORT_RECLAIM, report)
    return report


def run_youtube_video_inspect_segment(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoInspectSegmentOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    segment_id = _segment_id_from_path(Path(str(options.segment_id).strip()))
    dirs = _job_dirs(job_root)
    locations = []
    global_path = dirs["global_pending"] / f"{segment_id}.json"
    if global_path.is_file():
        locations.append({"kind": "global_pending", "path": str(global_path), "payload": _read_json_if_exists(global_path)})
    for worker, state, path in _iter_assigned_job_files(job_root):
        if _segment_id_from_path(path) == segment_id:
            locations.append({"kind": f"assigned/{worker}/{state}", "worker": worker, "state": state, "path": str(path), "payload": _read_json_if_exists(path)})
    output = _segment_output_path(job_root, segment_id)
    output_valid, output_validation = is_valid_video_file(output, require_audio=False)
    report = {
        "ok": True,
        "story_id": story_id,
        "story_slug": story_slug,
        "segment_id": segment_id,
        "drive_job_root": str(job_root),
        "locations": locations,
        "output_segment": str(output),
        "output_exists": output.is_file(),
        "output_valid": output_valid,
        "output_validation": output_validation,
        "report_path": str(local_dirs["reports"] / REPORT_INSPECT),
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_INSPECT, report)
    return report


def run_youtube_video_import_results(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoImportResultsOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    drive_dirs = _job_dirs(job_root)
    migrate_video_job_to_assigned_queue(job_root=job_root, settings=settings, execute=bool(options.execute))
    manifest = _read_json_if_exists(drive_dirs["manifests"] / "video_job_manifest.json") or _read_json_if_exists(job_root / "VIDEO_JOB_MANIFEST.json")
    expected_segments = int(manifest.get("expected_segments") or 0) if isinstance(manifest, dict) else 0
    done_segments = sorted(drive_dirs["segments"].glob("segment_*.mp4")) if drive_dirs["segments"].is_dir() else []
    failed_segments = [path for _worker, state, path in _iter_assigned_job_files(job_root, states=("failed",)) if state == "failed"]
    missing = []
    if expected_segments:
        done_names = {p.name for p in done_segments}
        missing = [f"segment_{idx:04d}.mp4" for idx in range(1, expected_segments + 1) if f"segment_{idx:04d}.mp4" not in done_names]
    plan = {
        "ok": job_root.is_dir(),
        "status": "ready_to_import" if job_root.is_dir() else "missing_drive_job",
        "execute": bool(options.execute),
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "expected_segments": expected_segments,
        "drive_done_segments": len(done_segments),
        "drive_failed_segments": len(failed_segments),
        "missing_segments": missing,
        "failed_segment_reports": [str(p) for p in failed_segments],
        "local_segments_dir": str(local_dirs["segments"]),
        "local_reports_dir": str(local_dirs["reports"]),
        "report_path": str(local_dirs["reports"] / REPORT_IMPORT),
    }
    if not plan["ok"] or not options.execute:
        report = {**plan, "ok": bool(plan["ok"]), "status": "dry_run" if plan["ok"] else plan["status"], "written_at": _now_iso()}
        _write_json(local_dirs["reports"] / REPORT_IMPORT, report)
        return report

    local_dirs["segments"].mkdir(parents=True, exist_ok=True)
    local_dirs["reports"].mkdir(parents=True, exist_ok=True)
    copied_segments = []
    for src in done_segments:
        dst = local_dirs["segments"] / src.name
        shutil.copy2(src, dst)
        copied_segments.append(str(dst))
    copied_reports = []
    if drive_dirs["reports"].is_dir():
        for src in sorted(drive_dirs["reports"].glob("*.json")):
            dst = local_dirs["reports"] / src.name
            shutil.copy2(src, dst)
            copied_reports.append(str(dst))
    report = {
        **plan,
        "ok": not missing and not failed_segments,
        "status": "imported_complete" if not missing and not failed_segments else "imported_partial",
        "copied_segments": copied_segments,
        "copied_reports": copied_reports,
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_IMPORT, report)
    return report


def _ffmpeg_run(cmd: list[str], log_path: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("COMMAND:\n" + " ".join(cmd) + "\n\nSTDERR_STDOUT:\n" + output, encoding="utf-8")
    return proc.returncode, output


def run_youtube_video_assemble_final(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoAssembleFinalOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    segment_jobs = _load_segment_jobs(story_dir)
    jobs = [j for j in segment_jobs.get("jobs", []) if isinstance(j, dict)] if isinstance(segment_jobs.get("jobs"), list) else []
    audio = _audio_path(story_dir)
    final_path = local_dirs["root"] / str(settings["final_video_name"])
    expected_segments = len(jobs)
    segment_paths = [local_dirs["segments"] / f"{str(job.get('segment_id'))}.mp4" for job in jobs]
    missing = [str(p) for p in segment_paths if not p.is_file()]
    plan = {
        "ok": bool(jobs) and audio.is_file() and not missing,
        "status": "ready_to_assemble" if bool(jobs) and audio.is_file() and not missing else "blocked",
        "execute": bool(options.execute),
        "story_id": story_id,
        "story_dir": str(story_dir),
        "audio_path": str(audio),
        "expected_segments": expected_segments,
        "missing_segments": missing,
        "final_video_path": str(final_path),
        "report_path": str(local_dirs["reports"] / REPORT_FINAL),
    }
    if not plan["ok"] or not options.execute:
        report = {**plan, "status": "dry_run" if plan["ok"] else plan["status"], "written_at": _now_iso()}
        _write_json(local_dirs["reports"] / REPORT_FINAL, report)
        return report

    local_dirs["work"].mkdir(parents=True, exist_ok=True)
    local_dirs["logs"].mkdir(parents=True, exist_ok=True)
    concat_list = local_dirs["work"] / "final_segments_concat.txt"
    lines = [f"file '{p.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for p in segment_paths]
    concat_list.write_text("\n".join(lines), encoding="utf-8")
    partial = final_path.with_name(f"{final_path.stem}.partial{final_path.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(partial),
    ]
    code, output = _ffmpeg_run(cmd, local_dirs["logs"] / "final_assemble.ffmpeg.log")
    if code != 0:
        report = {**plan, "ok": False, "status": "failed", "error": output[-5000:], "written_at": _now_iso()}
        _write_json(local_dirs["reports"] / REPORT_FINAL, report)
        return report
    valid, validation = is_valid_video_file(
        partial,
        expected_duration_sec=get_media_duration(audio),
        require_audio=True,
        duration_tolerance_sec=5.0,
    )
    if not valid:
        report = {**plan, "ok": False, "status": "failed_validation", "validation": validation, "written_at": _now_iso()}
        _write_json(local_dirs["reports"] / REPORT_FINAL, report)
        return report
    final_path.parent.mkdir(parents=True, exist_ok=True)
    partial.replace(final_path)
    final_valid, final_validation = is_valid_video_file(
        final_path,
        expected_duration_sec=get_media_duration(audio),
        require_audio=True,
        duration_tolerance_sec=5.0,
    )
    report = {
        **plan,
        "ok": final_valid,
        "status": "assembled" if final_valid else "failed_validation",
        "validation": final_validation,
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_FINAL, report)
    return report


REPORT_WATCH = "video_watch_queue_report.json"
EVENTS_WATCH = "video_watch_queue_events.jsonl"
REPORT_CLEANUP_PARTIAL = "video_cleanup_partial_checkpoints_report.json"
REPORT_VALIDATE_ASSETS = "video_validate_job_assets_report.json"


def _segment_required_frame_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    frames = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(frames, list):
        return names
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        raw = str(frame.get("name") or frame.get("path") or frame.get("input_frame_path") or "")
        name = Path(raw).name
        if name:
            names.append(name)
    return names


def _resolve_frame_on_drive(job_root: Path, frame_name: str) -> tuple[bool, Path | None, int]:
    """True/False + (где нашли | где должен быть) + size_bytes."""
    candidates = [job_root / "assets" / "frames" / frame_name, job_root / "input" / "frames" / frame_name]
    for candidate in candidates:
        try:
            if candidate.is_file():
                try:
                    size = candidate.stat().st_size
                except OSError:
                    size = 0
                if size > 0:
                    return True, candidate, size
        except OSError:
            continue
    return False, candidates[0], 0


def _collect_segments_for_validation(job_root: Path) -> list[dict[str, Any]]:
    """Объединяет global_pending + assigned/<worker>/{pending,processing,done,failed} в уникальный список."""
    dirs = _job_dirs(job_root)
    out: dict[str, dict[str, Any]] = {}
    if dirs["global_pending"].is_dir():
        for path in sorted(dirs["global_pending"].glob("segment_*.json")):
            seg_id = _segment_id_from_path(path)
            payload = _read_json_if_exists(path)
            if isinstance(payload, dict):
                out.setdefault(seg_id, {"segment_id": seg_id, "payload": payload, "source": "global_pending", "path": str(path)})
    for worker, state, path in _iter_assigned_job_files(job_root):
        seg_id = _segment_id_from_path(path)
        payload = _read_json_if_exists(path)
        if not isinstance(payload, dict):
            continue
        out.setdefault(seg_id, {"segment_id": seg_id, "payload": payload, "source": f"assigned/{worker}/{state}", "path": str(path)})
    return list(out.values())


def run_youtube_video_validate_job_assets(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoValidateJobAssetsOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    dirs = _job_dirs(job_root)

    segments = _collect_segments_for_validation(job_root)
    required_frames: dict[str, set[str]] = {}
    missing_by_segment: dict[str, list[dict[str, Any]]] = {}
    existing_set: set[str] = set()
    missing_set: set[str] = set()
    frame_resolution_cache: dict[str, tuple[bool, str, int]] = {}

    for entry in segments:
        seg_id = entry["segment_id"]
        names = _segment_required_frame_names(entry["payload"])
        required_frames[seg_id] = set(names)
        for name in names:
            if name not in frame_resolution_cache:
                ok, resolved_path, size = _resolve_frame_on_drive(job_root, name)
                frame_resolution_cache[name] = (ok, str(resolved_path) if resolved_path else "", int(size))
            ok, _resolved_path, _size = frame_resolution_cache[name]
            if ok:
                existing_set.add(name)
            else:
                missing_set.add(name)
                missing_by_segment.setdefault(seg_id, []).append(
                    {"name": name, "expected_path": str(job_root / "assets" / "frames" / name)}
                )

    total_required_frames = sum(len(s) for s in required_frames.values())
    unique_required = len(set().union(*required_frames.values())) if required_frames else 0
    ok = len(missing_set) == 0 and len(segments) > 0

    report = {
        "ok": ok,
        "status": "ok" if ok else "missing_assets",
        "execute": False,
        "dry_run": bool(options.dry_run),
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "job_exists": job_root.is_dir(),
        "total_segments": len(segments),
        "total_required_frames": int(total_required_frames),
        "unique_required_frames": int(unique_required),
        "existing_frames_count": len(existing_set),
        "missing_frames_count": len(missing_set),
        "missing_asset_segments_count": len(missing_by_segment),
        "missing_frames": sorted(missing_set),
        "missing_by_segment": {seg: items for seg, items in sorted(missing_by_segment.items())},
        "assets_frames_dir": str(dirs["assets_frames"]),
        "input_frames_dir": str(job_root / "input" / "frames"),
        "report_path": str(local_dirs["reports"] / REPORT_VALIDATE_ASSETS),
        "drive_report_path": str(dirs["reports"] / REPORT_VALIDATE_ASSETS),
        "written_at": _now_iso(),
    }
    try:
        local_dirs["reports"].mkdir(parents=True, exist_ok=True)
        _write_json(local_dirs["reports"] / REPORT_VALIDATE_ASSETS, report)
    except OSError:
        pass
    try:
        dirs["reports"].mkdir(parents=True, exist_ok=True)
        _write_json(dirs["reports"] / REPORT_VALIDATE_ASSETS, report)
    except OSError:
        pass
    return report


def _cleanup_partial_in_dir(stage_dir: Path, *, execute: bool) -> list[dict[str, Any]]:
    """Возвращает план/факты удаления *.partial.mp4 и mp4 без сопутствующего .done.json."""
    actions: list[dict[str, Any]] = []
    if not stage_dir.is_dir():
        return actions
    for partial in sorted(stage_dir.glob("*.partial.mp4")):
        size = 0
        try:
            size = partial.stat().st_size
        except OSError:
            pass
        deleted = False
        error = ""
        if execute:
            try:
                partial.unlink()
                deleted = True
            except OSError as exc:
                error = str(exc)
        actions.append({"path": str(partial), "kind": "partial", "size_bytes": int(size), "deleted": deleted, "error": error})
    for mp4 in sorted(stage_dir.glob("*.mp4")):
        if mp4.name.endswith(".partial.mp4"):
            continue
        marker = mp4.with_suffix(mp4.suffix + ".done.json")
        if marker.is_file():
            continue
        size = 0
        try:
            size = mp4.stat().st_size
        except OSError:
            pass
        deleted = False
        error = ""
        if execute:
            try:
                mp4.unlink()
                deleted = True
            except OSError as exc:
                error = str(exc)
        actions.append({"path": str(mp4), "kind": "orphan_no_done_marker", "size_bytes": int(size), "deleted": deleted, "error": error})
    return actions


def run_youtube_video_cleanup_partial_checkpoints(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoCleanupPartialOptions,
) -> dict[str, Any]:
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    work_root = _job_dirs(job_root)["work_segments"]
    execute = bool(options.execute) and not bool(options.dry_run)
    per_segment: list[dict[str, Any]] = []
    total_actions: list[dict[str, Any]] = []
    if work_root.is_dir():
        for seg_dir in sorted([p for p in work_root.iterdir() if p.is_dir()]):
            actions: list[dict[str, Any]] = []
            for stage_name in ("clips", "raw", "effects"):
                actions.extend(_cleanup_partial_in_dir(seg_dir / stage_name, execute=execute))
            if actions:
                per_segment.append({"segment_id": seg_dir.name, "actions": actions})
                total_actions.extend(actions)
    segments_dir = _job_dirs(job_root)["segments"]
    final_actions = _cleanup_partial_in_dir(segments_dir, execute=execute)
    if final_actions:
        per_segment.append({"segment_id": "_final_segments_dir", "actions": final_actions})
        total_actions.extend(final_actions)
    deleted_count = sum(1 for a in total_actions if a.get("deleted"))
    report = {
        "ok": True,
        "status": "cleaned" if execute else "dry_run",
        "execute": execute,
        "dry_run": not execute,
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "scanned_segments": len([s for s in per_segment if s.get("segment_id") != "_final_segments_dir"]),
        "actions_total": len(total_actions),
        "deleted_total": deleted_count,
        "skipped_total": len(total_actions) - deleted_count if execute else 0,
        "per_segment": per_segment,
        "report_path": str(local_dirs["reports"] / REPORT_CLEANUP_PARTIAL),
        "drive_report_path": str(_job_dirs(job_root)["reports"] / REPORT_CLEANUP_PARTIAL),
        "written_at": _now_iso(),
    }
    _write_json(local_dirs["reports"] / REPORT_CLEANUP_PARTIAL, report)
    if execute:
        try:
            _job_dirs(job_root)["reports"].mkdir(parents=True, exist_ok=True)
            _write_json(_job_dirs(job_root)["reports"] / REPORT_CLEANUP_PARTIAL, report)
        except OSError:
            pass
    return report


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _watcher_summary_line(tick: int, status: dict[str, Any], reclaimed: int, dispatched: int) -> str:
    expected = int(status.get("expected_segments") or 0)
    done_count = int(status.get("segments_done_count") or 0)
    processing_total = sum(int(v or 0) for v in (status.get("assigned_processing_by_worker") or {}).values())
    failed_total = sum(int(v or 0) for v in (status.get("assigned_failed_by_worker") or {}).values())
    return (
        f"watcher tick={tick} "
        f"reclaimed={reclaimed} "
        f"dispatched={dispatched} "
        f"done={done_count}/{expected} "
        f"processing={processing_total} "
        f"failed={failed_total} "
        f"global_pending={status.get('global_pending')} "
        f"stale_processing={status.get('stale_processing_count')} "
        f"checkpointed={status.get('checkpointed_segments_count')} "
        f"partial={status.get('partial_segments_count')}"
    )


def run_youtube_video_watch_queue(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoWatchQueueOptions,
) -> dict[str, Any]:
    """Production watcher loop: reclaim stale -> dispatch -> status -> (optional final import).

    Не запускает рендер локально. Не трогает ffmpeg. Только перекладывает json
    в Drive queue и копирует готовые сегменты в local segments при завершении job-а.
    """
    settings = _render_settings(config)
    story_id = str(options.story_id).strip()
    story_slug = _safe_slug(story_id)
    story_dir = _story_dir(config, settings, story_id)
    local_dirs = _video_dirs(story_dir)
    job_root = _drive_job_root(settings, story_slug)
    drive_dirs = _job_dirs(job_root)

    execute = bool(options.execute) and not bool(options.dry_run)
    poll_seconds = max(5, int(options.poll_seconds or 60))
    stale_minutes = max(1, int(options.stale_minutes or 10))
    max_attempts = max(1, int(options.max_attempts or 3))
    pending_per_worker = max(0, int(options.pending_per_worker or 1))
    max_total_assigned = max(0, int(options.max_total_assigned or (pending_per_worker * max(1, len(settings["workers"])))))
    max_runtime_seconds = max(0.0, float(options.max_runtime_minutes or 0.0)) * 60.0

    local_dirs["reports"].mkdir(parents=True, exist_ok=True)
    local_events_path = local_dirs["reports"] / EVENTS_WATCH
    local_report_path = local_dirs["reports"] / REPORT_WATCH
    drive_events_path = drive_dirs["reports"] / EVENTS_WATCH
    drive_report_path = drive_dirs["reports"] / REPORT_WATCH

    started_at = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    tick = 0
    totals = {
        "reclaimed_count": 0,
        "moved_to_failed_count": 0,
        "marked_done_count": 0,
        "dispatched_count": 0,
    }
    last_status: dict[str, Any] = {}
    last_reclaim: dict[str, Any] = {}
    last_dispatch: dict[str, Any] = {}
    last_preflight: dict[str, Any] = {}
    last_import: dict[str, Any] = {}
    stop_reason = "unspecified"
    interrupted = False
    final_status_field = "running"

    def _emit_event(kind: str, payload: dict[str, Any]) -> None:
        event = {
            "tick": tick,
            "kind": kind,
            "ts": _now_iso(),
            "execute": execute,
            **payload,
        }
        _append_jsonl(local_events_path, event)
        if execute:
            _append_jsonl(drive_events_path, event)

    try:
        while True:
            tick += 1
            tick_started = datetime.now(timezone.utc)

            reclaim_report = run_youtube_video_reclaim_stale_segments(
                config=config,
                options=YoutubeVideoReclaimStaleSegmentsOptions(
                    story_id=story_id,
                    stale_minutes=stale_minutes,
                    max_attempts=max_attempts,
                    execute=execute,
                    dry_run=not execute,
                ),
            )
            reclaim_count = int(reclaim_report.get("reclaimed_count") or 0)
            moved_failed_count = int(reclaim_report.get("moved_to_failed_count") or 0)
            marked_done_count = int(reclaim_report.get("marked_done_count") or 0)
            totals["reclaimed_count"] += reclaim_count
            totals["moved_to_failed_count"] += moved_failed_count
            totals["marked_done_count"] += marked_done_count
            last_reclaim = reclaim_report

            asset_preflight_ok = True
            preflight_report: dict[str, Any] = {}
            if not bool(options.skip_asset_preflight):
                preflight_report = run_youtube_video_validate_job_assets(
                    config=config,
                    options=YoutubeVideoValidateJobAssetsOptions(story_id=story_id, dry_run=True),
                )
                asset_preflight_ok = bool(preflight_report.get("ok"))
            last_preflight = preflight_report

            if not asset_preflight_ok:
                dispatch_report = {
                    "ok": True,
                    "status": "blocked_missing_assets",
                    "execute": execute,
                    "assigned_count": 0,
                    "target_per_worker": pending_per_worker,
                    "max_total_assigned": max_total_assigned,
                    "blocked_missing_assets_count": int(preflight_report.get("missing_asset_segments_count") or 0),
                    "missing_frames_count": int(preflight_report.get("missing_frames_count") or 0),
                    "missing_by_segment": preflight_report.get("missing_by_segment") or {},
                    "skipped_reason": "asset_preflight_failed",
                }
                dispatched_count = 0
                _emit_event(
                    "preflight_blocked",
                    {
                        "missing_asset_segments_count": dispatch_report["blocked_missing_assets_count"],
                        "missing_frames_count": dispatch_report["missing_frames_count"],
                        "missing_by_segment": dispatch_report["missing_by_segment"],
                    },
                )
            else:
                dispatch_report = run_youtube_video_dispatch_segments(
                    config=config,
                    options=YoutubeVideoDispatchSegmentsOptions(
                        story_id=story_id,
                        workers=str(options.workers or ""),
                        target_per_worker=pending_per_worker,
                        max_total_assigned=max_total_assigned,
                        execute=execute,
                    ),
                )
                dispatched_count = int(dispatch_report.get("assigned_count") or 0)
            totals["dispatched_count"] += dispatched_count
            last_dispatch = dispatch_report

            status = _build_queue_status(
                config,
                story_id,
                write_report=False,
                stale_minutes=stale_minutes,
                include_asset_preflight=not bool(options.skip_asset_preflight),
                preflight_report=preflight_report if not bool(options.skip_asset_preflight) else None,
            )
            last_status = status

            expected = int(status.get("expected_segments") or 0)
            done_count = int(status.get("segments_done_count") or 0)
            processing_total = sum(int(v or 0) for v in (status.get("assigned_processing_by_worker") or {}).values())
            assigned_pending_total = sum(int(v or 0) for v in (status.get("assigned_pending_by_worker") or {}).values())
            failed_total = sum(int(v or 0) for v in (status.get("assigned_failed_by_worker") or {}).values())
            global_pending = int(status.get("global_pending") or 0)
            stale_processing_count = int(status.get("stale_processing_count") or 0)
            can_import = bool(status.get("can_import"))
            can_assemble = bool(status.get("can_assemble"))

            line = _watcher_summary_line(tick, status, reclaim_count, dispatched_count)
            preflight_suffix = f" preflight_ok={asset_preflight_ok}" if not bool(options.skip_asset_preflight) else " preflight_ok=skipped"
            missing_asset_segments_total = int(status.get("missing_asset_segments_count") or 0)
            print(
                f"{line} can_import={can_import} can_assemble={can_assemble} "
                f"assigned_pending_total={assigned_pending_total}{preflight_suffix} "
                f"missing_asset_segments={missing_asset_segments_total}",
                flush=True,
            )
            _emit_event(
                "tick",
                {
                    "reclaimed_count": reclaim_count,
                    "moved_to_failed_count": moved_failed_count,
                    "marked_done_count": marked_done_count,
                    "dispatched_count": dispatched_count,
                    "global_pending": global_pending,
                    "assigned_pending_total": assigned_pending_total,
                    "processing_total": processing_total,
                    "failed_total": failed_total,
                    "stale_processing_count": stale_processing_count,
                    "done_count": done_count,
                    "expected_segments": expected,
                    "can_import": can_import,
                    "can_assemble": can_assemble,
                    "checkpointed_segments_count": status.get("checkpointed_segments_count"),
                    "partial_segments_count": status.get("partial_segments_count"),
                    "asset_preflight_ok": asset_preflight_ok,
                    "asset_preflight_skipped": bool(options.skip_asset_preflight),
                    "missing_asset_segments_count": missing_asset_segments_total,
                    "missing_assets_count": int(status.get("missing_assets_count") or 0),
                    "stale_processing_by_worker": status.get("stale_processing_by_worker") or {},
                    "summary_line": line,
                },
            )

            if expected and done_count >= expected:
                stop_reason = "all_segments_done"
                final_status_field = "completed"
                if execute and bool(options.auto_import_on_complete):
                    last_import = run_youtube_video_import_results(
                        config=config,
                        options=YoutubeVideoImportResultsOptions(story_id=story_id, execute=True),
                    )
                    _emit_event(
                        "auto_import",
                        {
                            "status": last_import.get("status"),
                            "drive_done_segments": last_import.get("drive_done_segments"),
                            "expected_segments": last_import.get("expected_segments"),
                            "missing_segments": last_import.get("missing_segments") or [],
                        },
                    )
                _emit_event("stop", {"reason": stop_reason})
                break

            if (
                global_pending == 0
                and assigned_pending_total == 0
                and processing_total == 0
                and failed_total > 0
            ):
                stop_reason = "no_more_work_with_failed_segments"
                final_status_field = "stopped_with_failures"
                _emit_event("stop", {"reason": stop_reason, "failed_total": failed_total})
                break

            if bool(options.once):
                stop_reason = "once"
                final_status_field = "tick_done" if execute else "dry_run"
                _emit_event("stop", {"reason": stop_reason})
                break

            if max_runtime_seconds > 0 and (time.monotonic() - started_mono) >= max_runtime_seconds:
                stop_reason = "max_runtime_reached"
                final_status_field = "stopped_max_runtime"
                _emit_event("stop", {"reason": stop_reason, "max_runtime_minutes": float(options.max_runtime_minutes)})
                break

            sleep_until = time.monotonic() + poll_seconds
            while time.monotonic() < sleep_until:
                time.sleep(min(1.0, sleep_until - time.monotonic()))
            _ = tick_started
    except KeyboardInterrupt:
        interrupted = True
        stop_reason = "keyboard_interrupt"
        final_status_field = "interrupted"
        _emit_event("stop", {"reason": stop_reason})

    ended_at = datetime.now(timezone.utc)
    runtime_seconds = int((ended_at - started_at).total_seconds())

    report = {
        "ok": True,
        "status": final_status_field,
        "stop_reason": stop_reason,
        "execute": execute,
        "dry_run": not execute,
        "once": bool(options.once),
        "interrupted": interrupted,
        "story_id": story_id,
        "story_slug": story_slug,
        "drive_job_root": str(job_root),
        "poll_seconds": poll_seconds,
        "stale_minutes": stale_minutes,
        "max_attempts": max_attempts,
        "pending_per_worker": pending_per_worker,
        "max_total_assigned": max_total_assigned,
        "max_runtime_minutes": float(options.max_runtime_minutes or 0.0),
        "ticks": tick,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "runtime_seconds": runtime_seconds,
        "totals": totals,
        "last_status": last_status,
        "last_reclaim": {
            "status": last_reclaim.get("status"),
            "reclaimed_count": last_reclaim.get("reclaimed_count"),
            "moved_to_failed_count": last_reclaim.get("moved_to_failed_count"),
            "marked_done_count": last_reclaim.get("marked_done_count"),
            "skipped_count": last_reclaim.get("skipped_count"),
            "scanned_processing_segments": last_reclaim.get("scanned_processing_segments"),
        },
        "last_dispatch": {
            "status": last_dispatch.get("status"),
            "assigned_count": last_dispatch.get("assigned_count"),
            "target_per_worker": last_dispatch.get("target_per_worker"),
            "max_total_assigned": last_dispatch.get("max_total_assigned"),
            "blocked_missing_assets_count": last_dispatch.get("blocked_missing_assets_count"),
        },
        "last_preflight": {
            "ok": last_preflight.get("ok"),
            "total_segments": last_preflight.get("total_segments"),
            "missing_asset_segments_count": last_preflight.get("missing_asset_segments_count"),
            "missing_frames_count": last_preflight.get("missing_frames_count"),
            "missing_by_segment": last_preflight.get("missing_by_segment"),
        },
        "last_import": last_import,
        "report_path": str(local_report_path),
        "drive_report_path": str(drive_report_path),
        "local_events_path": str(local_events_path),
        "drive_events_path": str(drive_events_path),
        "written_at": _now_iso(),
    }
    _write_json(local_report_path, report)
    if execute:
        try:
            drive_report_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(drive_report_path, report)
        except OSError:
            pass
    return report


def run_youtube_video_full_drive_flow(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoFullDriveFlowOptions,
) -> dict[str, Any]:
    export = run_youtube_video_export_job(
        config=config,
        options=YoutubeVideoExportJobOptions(story_id=options.story_id, execute=options.execute, force=options.force),
    )
    status = run_youtube_video_drive_status(
        config=config,
        options=YoutubeVideoDriveStatusOptions(story_id=options.story_id),
    )
    return {
        "ok": bool(export.get("ok")),
        "status": "exported" if export.get("ok") and options.execute else "dry_run",
        "execute": bool(options.execute),
        "story_id": options.story_id,
        "export": export,
        "drive_status": status,
        "worker_commands": export.get("worker_commands") or [],
        "note": "Start the Colab workers manually. This command does not wait for Colab.",
    }
