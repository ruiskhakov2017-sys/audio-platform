"""Archive stale YouTube frames without deleting them permanently."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


@dataclass
class YoutubeFramesResetOptions:
    story_id: str
    reason: str
    execute: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    return (config.root_dir / "output" / "youtube" / story_id).resolve()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_prompts(story_dir: Path) -> list[str]:
    path = story_dir / "06_prompts" / "prompts_list.txt"
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return []
    if "\n\n" in raw:
        return [part for part in raw.split("\n\n") if part.strip()]
    return [line for line in raw.splitlines() if line.strip()]


def _archive_candidates(story_dir: Path) -> list[Path]:
    frames_dir = story_dir / "07_frames"
    logs_dir = story_dir / "logs"
    candidates: list[Path] = []
    candidates.extend(sorted(frames_dir.glob("frame_*.png")))
    for name in (
        "frame_jobs.json",
        "frame_jobs_compact.json",
        "frame_jobs_balanced_compact.json",
        "frame_jobs_noface_compact.json",
        "failed_frames.json",
        "failed_frames_compact.json",
        "failed_frames_balanced_compact.json",
        "failed_frames_noface_compact.json",
    ):
        path = frames_dir / name
        if path.is_file():
            candidates.append(path)
    report = logs_dir / "youtube_frames_runpod_report.json"
    if report.is_file():
        candidates.append(report)
    payload_debug = logs_dir / "youtube_comfyui_payload_debug.json"
    if payload_debug.is_file():
        candidates.append(payload_debug)
    return candidates


def _archive_target(archive_dir: Path, story_dir: Path, source: Path) -> Path:
    try:
        rel = source.relative_to(story_dir)
    except ValueError:
        rel = Path(source.name)
    return archive_dir / rel


def _patch_manifest(story_dir: Path, *, reason: str, archive_dir: Path, expected: int) -> Path:
    path = story_dir / "youtube_story_manifest.json"
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        manifest = {}
    now = _now_iso()
    manifest.setdefault("pipeline_stage_status", {})
    if isinstance(manifest["pipeline_stage_status"], dict):
        manifest["pipeline_stage_status"]["frames"] = "stale_bad_continuity"
        manifest["pipeline_stage_status"]["visuals"] = "blocked"
    manifest.setdefault("status", {})
    if isinstance(manifest["status"], dict):
        manifest["status"]["frames_done"] = False
        manifest["status"]["video_done"] = False
    manifest["frames"] = {
        "status": "stale_bad_continuity",
        "valid": 0,
        "missing": expected,
        "expected": expected,
        "reason": reason,
        "archived_to": str(archive_dir),
        "updated_at": now,
    }
    _write_json(path, manifest)
    return path


def run_youtube_frames_reset(
    *,
    config: OrchestratorConfig,
    options: YoutubeFramesResetOptions,
) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    reason = str(options.reason or "").strip() or "unspecified"
    story_dir = _story_dir(config, story_id)
    frames_dir = story_dir / "07_frames"
    archive_dir = frames_dir / f"_stale_{reason}" / _timestamp()
    candidates = _archive_candidates(story_dir)
    expected = len(_load_prompts(story_dir))
    result: dict[str, Any] = {
        "ok": story_dir.is_dir(),
        "status": "dry_run" if not options.execute else "archived",
        "execute": bool(options.execute),
        "story_id": story_id,
        "reason": reason,
        "story_dir": str(story_dir),
        "frames_dir": str(frames_dir),
        "archive_dir": str(archive_dir),
        "expected_frames": expected,
        "archive_count": len(candidates),
        "archive_candidates": [str(path) for path in candidates],
        "changed_files": [],
        "missing": [] if story_dir.is_dir() else [str(story_dir)],
    }
    if not story_dir.is_dir():
        result["status"] = "missing_story"
        return result
    if not options.execute:
        return result

    archive_dir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for source in candidates:
        if not source.exists():
            continue
        target = _archive_target(archive_dir, story_dir, source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(str(target))
    manifest_path = _patch_manifest(story_dir, reason=reason, archive_dir=archive_dir, expected=expected)
    report_path = archive_dir / "frames_reset_report.json"
    result.update(
        {
            "archive_count": len(moved),
            "archived_files": moved,
            "manifest_path": str(manifest_path),
            "reset_report_path": str(report_path),
            "changed_files": [str(manifest_path), str(report_path), *moved],
        }
    )
    _write_json(report_path, {**result, "written_at": _now_iso()})
    return result
