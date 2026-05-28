"""Single-story YouTube visuals state machine.

This runner orchestrates local bridge steps only. It does not launch Gemini
unless a future explicit adapter is added, and it only calls RunPod/ComfyUI via
the existing frames bridge when both --execute and --runpod-url are provided.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_video_segments import (
    YoutubeVideoPrepareSegmentsOptions,
    run_youtube_video_prepare_segments,
)
from orchestrator.youtube_promo_bridge import (
    YoutubePromoRunOptions,
    YoutubePromoStatusOptions,
    run_youtube_promo_run,
    run_youtube_promo_status,
)
from orchestrator.youtube_language import EXPECTED_YOUTUBE_LANGUAGE, build_youtube_safe_status
from orchestrator.youtube_safe_english_bridge import YoutubeSafeEnglishRunOptions, run_youtube_safe_english_run
from orchestrator.youtube_visuals_bridge import (
    YoutubeCharactersExportOptions,
    YoutubeCharactersImportOptions,
    YoutubeDirectorPromptsExportOptions,
    YoutubeDirectorPromptsImportOptions,
    YoutubeFramesRunpodBridgeOptions,
    YoutubeGeminiAutoStageOptions,
    _audio_text_path,
    _characters_path,
    _characters_staging_dir,
    _duration_sec,
    _frame_status,
    _frames_dir,
    _load_prompts,
    _prompts_path,
    _prompts_staging_dir,
    _safe_story_path,
    _story_manifest_path,
    run_youtube_characters_export,
    run_youtube_characters_import,
    run_youtube_director_prompts_export,
    run_youtube_director_prompts_import,
    run_youtube_characters_auto_gemini,
    run_youtube_director_prompts_auto_gemini,
    run_youtube_frames_runpod_bridge,
)
from orchestrator.youtube_characters_anchor_audit import (
    YoutubeCharactersAnchorAuditOptions,
    run_youtube_characters_anchor_audit,
)
from orchestrator.youtube_visuals_clean import (
    YoutubeVisualsCleanOptions,
    run_youtube_visuals_clean,
    scan_legacy_visual_stale_sources,
    validate_visual_characters_file,
    validate_visual_prompts_file,
)
from orchestrator.youtube_gemini_registry import sync_youtube_gemini_legacy_files


@dataclass
class YoutubeVisualsRunOptions:
    story_id: str
    runpod_url: str = ""
    workflow: str = ""
    execute: bool = False
    watch: bool = False
    allow_gemini: bool = False
    auto_gemini: bool = False
    fresh_visuals: bool = False
    prompt_runpod_url: bool = True
    segment_sec: float = 180.0
    watch_interval_sec: int = 5
    watch_timeout_sec: int = 0


@dataclass
class YoutubeVisualsStatusOptions:
    story_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    return (config.root_dir / "output" / "youtube" / story_id).resolve()


def _logs_dir(story_dir: Path) -> Path:
    return story_dir / "logs"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_nonempty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _source_text_path(story_dir: Path) -> Path:
    audio_text = _audio_text_path(story_dir)
    if audio_text.is_file():
        return audio_text
    return _safe_story_path(story_dir)


def _source_cleaned_path(story_dir: Path) -> Path:
    return story_dir / "00_source" / "source_cleaned_story.txt"


def _narration_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def _characters_drop_dir(story_dir: Path) -> Path:
    return story_dir / "05_characters" / "_drop"


def _prompts_drop_dir(story_dir: Path) -> Path:
    return story_dir / "06_prompts" / "_drop"


def _expected_output_path_file(stage_dir: Path) -> Path:
    return stage_dir / "expected_output_path.txt"


def _video_timeline_path(story_dir: Path) -> Path:
    return story_dir / "08_video" / "manifests" / "video_timeline.json"


def _segment_jobs_path(story_dir: Path) -> Path:
    return story_dir / "08_video" / "manifests" / "segment_jobs.json"


def _load_manifest(story_dir: Path) -> dict[str, Any]:
    path = _story_manifest_path(story_dir)
    if not path.is_file():
        return {}
    try:
        data = _read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_manifest(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    path = _story_manifest_path(story_dir)
    manifest = _load_manifest(story_dir)
    if not manifest:
        manifest = {
            "story_id": story_id,
            "canonical_basename": story_id,
            "youtube_outputs": {
                "story_dir": str(story_dir),
                "safe_story_dir": "02_safe_story",
                "promo_dir": "03_promo",
                "audio_dir": "04_audio",
                "characters_dir": "05_characters",
                "director_dir": "06_prompts",
                "frames_dir": "07_frames",
                "video_dir": "08_video",
                "logs_dir": "logs",
            },
        }
    manifest.setdefault("expected_artifacts", {})
    if isinstance(manifest["expected_artifacts"], dict):
        manifest["expected_artifacts"].update(
            {
                "safe_story": str(_safe_story_path(story_dir)),
                "promo_text_ready_for_audio": str(_audio_text_path(story_dir)),
                "audio_mp3": str(_narration_path(story_dir)),
                "characters_txt": str(_characters_path(story_dir)),
                "prompts_list_txt": str(_prompts_path(story_dir)),
                "frames_dir": str(_frames_dir(story_dir)),
                "video_timeline": str(_video_timeline_path(story_dir)),
                "segment_jobs": str(_segment_jobs_path(story_dir)),
            }
        )
    manifest["updated_at"] = _now_iso()
    _write_json(path, manifest)
    return path


def _update_manifest_visuals(story_dir: Path, visuals: dict[str, Any]) -> Path:
    path = _story_manifest_path(story_dir)
    manifest = _load_manifest(story_dir)
    manifest.setdefault("visuals", {})
    if isinstance(manifest["visuals"], dict):
        manifest["visuals"].update(visuals)
    manifest["updated_at"] = _now_iso()
    _write_json(path, manifest)
    return path


def _write_expected_path(path: Path, expected_output: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(expected_output) + "\n", encoding="utf-8")


def _wait_for_file(path: Path, *, interval_sec: int, timeout_sec: int) -> bool:
    started = time.time()
    while True:
        if _is_nonempty(path):
            return True
        if timeout_sec > 0 and time.time() - started >= timeout_sec:
            return False
        time.sleep(max(1, interval_sec))


def _ask_runpod_url_at_checkpoint(*, story_id: str, frame_status: dict[str, Any], workflow: dict[str, Any] | None = None) -> str:
    print("", flush=True)
    print("=" * 72, flush=True)
    print("READY_FOR_RUNPOD", flush=True)
    print(f"Story: {story_id}", flush=True)
    if workflow:
        print(f"Workflow: {workflow.get('name') or workflow.get('path')}", flush=True)
    print(f"Prompts expected: {frame_status.get('expected')}", flush=True)
    print(f"Frames valid: {frame_status.get('generated')}", flush=True)
    print(f"Frames missing: {frame_status.get('pending')}", flush=True)
    print(f"Frames failed: {frame_status.get('failed')}", flush=True)
    print("", flush=True)
    print("Gemini stages are done. Start RunPod/ComfyUI now, then paste API URL.", flush=True)
    print("Leave empty to stop here without generating frames.", flush=True)
    print("=" * 72, flush=True)
    try:
        return input("RunPod URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _prompt_estimate(story_dir: Path) -> int | None:
    duration = _duration_sec(_narration_path(story_dir))
    if not duration:
        return None
    return max(1, round(duration / 25))


def _video_segments_status(story_dir: Path) -> dict[str, Any]:
    timeline = _video_timeline_path(story_dir)
    jobs_path = _segment_jobs_path(story_dir)
    total_segments = 0
    if jobs_path.is_file():
        try:
            data = _read_json(jobs_path)
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            total_segments = len(jobs) if isinstance(jobs, list) else 0
        except Exception:
            total_segments = 0
    return {
        "timeline_exists": timeline.is_file(),
        "segment_jobs_exists": jobs_path.is_file(),
        "timeline_path": str(timeline),
        "segment_jobs_path": str(jobs_path),
        "total_segments": total_segments,
    }


def run_youtube_visuals_status(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualsStatusOptions,
) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, story_id)
    manifest = _load_manifest(story_dir)
    safe_story = _safe_story_path(story_dir)
    audio_text = _audio_text_path(story_dir)
    narration = _narration_path(story_dir)
    characters = _characters_path(story_dir)
    prompts_path = _prompts_path(story_dir)
    prompts = _load_prompts(prompts_path)
    frames_dir = _frames_dir(story_dir)
    frame_status = _frame_status(frames_dir, prompts)
    video_segments = _video_segments_status(story_dir)
    audio_duration = _duration_sec(narration)
    promo_status = run_youtube_promo_status(config=config, options=YoutubePromoStatusOptions(story_id=story_id))
    language_status = build_youtube_safe_status(config=config, story_id=story_id, expected_language=EXPECTED_YOUTUBE_LANGUAGE)
    anchor_audit = run_youtube_characters_anchor_audit(config=config, options=YoutubeCharactersAnchorAuditOptions(story_id=story_id))
    characters_validation = validate_visual_characters_file(characters)
    prompts_validation = validate_visual_prompts_file(prompts_path)
    legacy_stale_scan = scan_legacy_visual_stale_sources(config, story_id)
    promo_audio = promo_status.get("audio") if isinstance(promo_status.get("audio"), dict) else {}

    current_blocker = ""
    next_action = "visuals pipeline ready"
    if not story_dir.is_dir():
        current_blocker = "missing story folder"
        next_action = "check --story-id"
    elif not safe_story.is_file():
        current_blocker = "missing 02_safe_story/safe_story.txt"
        next_action = "finish safe story first"
    elif language_status.get("safe_story_status") == "wrong_language":
        current_blocker = "youtube_safe_story_wrong_language"
        next_action = "run youtube safe-regenerate"
    elif promo_status.get("status") != "done":
        current_blocker = str(promo_status.get("current_blocker") or "promo_not_done")
        next_action = str(promo_status.get("next_action") or "run youtube promo-run --execute first")
    elif not narration.is_file():
        current_blocker = "missing 04_audio/narration.mp3"
        next_action = "run YouTube TTS from 03_promo/text_ready_for_audio.txt first"
    elif promo_audio.get("stale"):
        current_blocker = "youtube_audio_stale_after_promo_change"
        next_action = "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt"
    elif not characters.is_file():
        current_blocker = "missing 05_characters/characters.txt"
        next_action = "run visuals-run --execute --auto-gemini to generate characters automatically"
    elif not characters_validation.get("ok", False):
        current_blocker = "youtube_visuals_characters_stale_or_invalid"
        next_action = "run youtube visuals-clean --execute, then regenerate characters"
    elif anchor_audit.get("status") == "invalid":
        current_blocker = "youtube_characters_anchor_invalid"
        next_action = "fix Gemini Characters Bot and regenerate characters"
    elif not prompts_path.is_file():
        current_blocker = "missing 06_prompts/prompts_list.txt"
        next_action = "run visuals-run --execute --auto-gemini to generate director prompts automatically"
    elif not prompts_validation.get("ok", False):
        current_blocker = "youtube_visuals_prompts_stale_or_invalid"
        next_action = "run youtube visuals-clean --execute, then regenerate characters/prompts"
    elif not legacy_stale_scan.get("ok", False):
        current_blocker = "youtube_visuals_legacy_stale_source_detected"
        next_action = "run youtube visuals-clean --execute to quarantine stale legacy visual sources"
    elif isinstance(manifest.get("frames"), dict) and manifest.get("frames", {}).get("status") == "stale_bad_continuity":
        current_blocker = "frames_stale_bad_continuity"
        next_action = "regenerate raw frame jobs after fixing characters anchors and director prompts"
    elif frame_status["not_done"] > 0:
        current_blocker = "ready_for_runpod"
        next_action = "start RunPod, run visuals-run --execute --auto-gemini, then paste URL at READY_FOR_RUNPOD checkpoint"
    elif not video_segments["segment_jobs_exists"]:
        current_blocker = "video segments not prepared"
        next_action = "run visuals-run --execute to prepare video segments"

    report = {
        "ok": True,
        "story_id": story_id,
        "story_dir": str(story_dir),
        "safe_story": "exists" if safe_story.is_file() else "missing",
        "language": language_status,
        "promo": promo_status,
        "text_ready_for_audio": "exists" if audio_text.is_file() else "missing",
        "narration": {
            "status": promo_audio.get("status") or ("exists" if narration.is_file() else "missing"),
            "path": str(narration),
            "duration_sec": audio_duration,
            "stale": bool(promo_audio.get("stale")),
        },
        "characters": {
            "status": "done" if _is_nonempty(characters) and characters_validation.get("ok") else ("stale_or_invalid" if _is_nonempty(characters) else "missing"),
            "path": str(characters),
            "staging_path": str(_characters_staging_dir(story_dir)),
            "drop_path": str(_characters_drop_dir(story_dir)),
            "anchor_audit": anchor_audit,
            "validation": characters_validation,
        },
        "prompts": {
            "status": "done" if _is_nonempty(prompts_path) and prompts_validation.get("ok") else ("stale_or_invalid" if _is_nonempty(prompts_path) else "missing"),
            "path": str(prompts_path),
            "prompts_count": len(prompts),
            "estimated_prompts": _prompt_estimate(story_dir),
            "staging_path": str(_prompts_staging_dir(story_dir)),
            "drop_path": str(_prompts_drop_dir(story_dir)),
            "prompt_mode_available": {"raw_exists": prompts_path.is_file()},
            "available_prompt_modes": ["raw"],
            "recommended_prompt_mode": "raw",
            "validation": prompts_validation,
            "legacy_stale_scan": legacy_stale_scan,
        },
        "frames": {
            "status": (manifest.get("frames", {}) if isinstance(manifest.get("frames"), dict) else {}).get("status", ""),
            "reason": (manifest.get("frames", {}) if isinstance(manifest.get("frames"), dict) else {}).get("reason", ""),
            "archived_to": (manifest.get("frames", {}) if isinstance(manifest.get("frames"), dict) else {}).get("archived_to", ""),
            "output_dir": str(frames_dir),
            "expected": frame_status["expected"],
            "existing": frame_status["existing_total"],
            "valid": frame_status["generated"],
            "missing": frame_status["pending"],
            "failed": frame_status["failed"],
            "first_10_missing": frame_status["first_10_pending"],
            "first_10_failed": frame_status["first_10_failed"],
        },
        "video_segments": video_segments,
        "current_blocker": current_blocker,
        "next_action": next_action,
        "reports": {
            "run_report": str(_logs_dir(story_dir) / "youtube_visuals_run_report.json"),
            "status_report": str(_logs_dir(story_dir) / "youtube_visuals_status.json"),
            "frames_report": str(_logs_dir(story_dir) / "youtube_frames_runpod_report.json"),
        },
    }
    _write_json(_logs_dir(story_dir) / "youtube_visuals_status.json", report)
    return report


def _stage_row(name: str, status: str, message: str = "", **extra: Any) -> dict[str, Any]:
    return {"stage": name, "status": status, "message": message, "updated_at": _now_iso(), **extra}


def _visuals_manifest_from_status(status_report: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    chars = status_report.get("characters", {}) if isinstance(status_report.get("characters"), dict) else {}
    prompts = status_report.get("prompts", {}) if isinstance(status_report.get("prompts"), dict) else {}
    frames = status_report.get("frames", {}) if isinstance(status_report.get("frames"), dict) else {}
    video = status_report.get("video_segments", {}) if isinstance(status_report.get("video_segments"), dict) else {}
    return {
        "characters": {
            "status": chars.get("status", "missing"),
            "path": chars.get("path", ""),
            "staging_path": chars.get("staging_path", ""),
            "drop_path": chars.get("drop_path", ""),
            "updated_at": now,
        },
        "director_prompts": {
            "status": prompts.get("status", "missing"),
            "path": prompts.get("path", ""),
            "staging_path": prompts.get("staging_path", ""),
            "drop_path": prompts.get("drop_path", ""),
            "audio_duration_sec": (status_report.get("narration") or {}).get("duration_sec")
            if isinstance(status_report.get("narration"), dict)
            else None,
            "estimated_prompts": prompts.get("estimated_prompts"),
            "updated_at": now,
        },
        "frames": {
            "status": "done" if int(frames.get("expected") or 0) > 0 and int(frames.get("missing") or 0) == 0 and int(frames.get("failed") or 0) == 0 else "missing",
            "output_dir": frames.get("output_dir", ""),
            "expected": frames.get("expected", 0),
            "valid": frames.get("valid", 0),
            "missing": frames.get("missing", 0),
            "failed": frames.get("failed", 0),
            "updated_at": now,
        },
        "video_segments": {
            "status": "prepared" if video.get("segment_jobs_exists") else "missing",
            "timeline_path": video.get("timeline_path", ""),
            "segment_jobs_path": video.get("segment_jobs_path", ""),
            "total_segments": video.get("total_segments", 0),
            "updated_at": now,
        },
    }


def run_youtube_visuals_run(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualsRunOptions,
) -> dict[str, Any]:
    started_at = _now_iso()
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, story_id)
    changed_files: list[str] = []
    stages: list[dict[str, Any]] = []
    blockers: list[str] = []
    errors: list[str] = []
    mode = "watch" if options.watch else ("execute" if options.execute else "dry_run")
    auto_gemini_enabled = bool(options.auto_gemini or options.allow_gemini)

    def finish(status: str, next_action: str) -> dict[str, Any]:
        status_report = run_youtube_visuals_status(config=config, options=YoutubeVisualsStatusOptions(story_id=story_id))
        if options.execute or options.watch:
            manifest_path = _update_manifest_visuals(story_dir, _visuals_manifest_from_status(status_report))
            changed_files.append(str(manifest_path))
        report = {
            "ok": status in {"done", "dry_run", "ready_for_runpod"},
            "status": status,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "mode": mode,
            "allow_gemini": bool(options.allow_gemini),
            "auto_gemini": auto_gemini_enabled,
            "gemini_note": (
                "Legacy director_2_0 Gemini automation is launched only when --execute and "
                "--auto-gemini/--allow-gemini are both set."
            ),
            "story_id": story_id,
            "story_dir": str(story_dir),
            "stages": stages,
            "blockers": blockers,
            "next_action": next_action,
            "changed_files": changed_files,
            "errors": errors,
            "status_report": status_report,
        }
        if options.execute or options.watch:
            path = _logs_dir(story_dir) / "youtube_visuals_run_report.json"
            _write_json(path, report)
            changed_files.append(str(path))
        return report

    safe_story = _safe_story_path(story_dir)
    narration = _narration_path(story_dir)
    manifest_path = _story_manifest_path(story_dir)

    missing_preflight: list[str] = []
    if not story_dir.is_dir():
        missing_preflight.append(str(story_dir))
    if not _source_cleaned_path(story_dir).is_file():
        missing_preflight.append(str(_source_cleaned_path(story_dir)))
    if missing_preflight:
        stages.append(_stage_row("preflight", "blocked", "missing required inputs", missing=missing_preflight))
        blockers.append("missing preflight inputs")
        return finish("blocked", "finish missing preflight inputs")

    if options.execute:
        changed_files.append(str(_ensure_manifest(config, story_id, story_dir)))
    elif not manifest_path.is_file():
        stages.append(_stage_row("preflight", "would_create_manifest", "youtube_story_manifest.json missing"))
    stages.append(_stage_row("preflight", "done", "required story inputs are present"))

    gemini_bots_preflight = sync_youtube_gemini_legacy_files(
        config,
        story_dir=story_dir,
        execute=bool(options.execute),
    )
    changed_files.extend(
        str(gemini_bots_preflight.get(k))
        for k in ("report_path", "text_report_path")
        if gemini_bots_preflight.get(k)
    )
    changed_files.extend(str(path) for path in (gemini_bots_preflight.get("changed_files") or []) if path)
    stages.append(
        _stage_row(
            "gemini_bots_preflight",
            "done" if gemini_bots_preflight.get("ok") else "blocked",
            "sync YouTube visuals Gemini bot URLs from registry",
            result=gemini_bots_preflight,
        )
    )
    if not gemini_bots_preflight.get("ok", False):
        blockers.append("youtube_gemini_bots_preflight_failed")
        return finish("blocked", "fix configs/gemini_bots_registry.yaml or unknown active legacy Gemini URLs")

    if options.fresh_visuals:
        clean_result = run_youtube_visuals_clean(
            config=config,
            options=YoutubeVisualsCleanOptions(story_id=story_id, execute=bool(options.execute)),
        )
        stages.append(
            _stage_row(
                "fresh_visuals_clean",
                str(clean_result.get("status", "unknown")),
                "quarantine existing visual artifacts before characters/director regeneration",
                result=clean_result,
            )
        )
        changed_files.extend(str(item.get("target")) for item in (clean_result.get("moved_files") or []) if isinstance(item, dict) and item.get("target"))
        if clean_result.get("report_path"):
            changed_files.append(str(clean_result.get("report_path")))
        if not clean_result.get("ok", False) and options.execute:
            blockers.extend(str(blocker) for blocker in (clean_result.get("blockers") or []) if blocker)
            return finish("blocked", "fix visuals-clean blockers before regenerating characters")

    language_status = build_youtube_safe_status(config=config, story_id=story_id, expected_language=EXPECTED_YOUTUBE_LANGUAGE)
    if language_status.get("safe_story_status") != "done":
        if options.execute:
            safe_result = run_youtube_safe_english_run(
                config=config,
                options=YoutubeSafeEnglishRunOptions(story_id=story_id, execute=True, force=True),
            )
            changed_files.extend(str(p) for p in (safe_result.get("changed_files") or []) if p)
            stages.append(
                _stage_row(
                    "safe_language_check",
                    str(safe_result.get("status", "unknown")),
                    "English-safe rewrite adapter",
                    result=safe_result,
                )
            )
            language_status = build_youtube_safe_status(config=config, story_id=story_id, expected_language=EXPECTED_YOUTUBE_LANGUAGE)
            if language_status.get("safe_story_status") != "done":
                blockers.append(str(language_status.get("current_blocker") or "youtube_safe_story_wrong_language"))
                return finish("blocked", "fix English safe rewrite and rerun visuals-run")
        else:
            stages.append(
                _stage_row(
                    "safe_language_check",
                    "would_run",
                    "safe_story is not English; dry-run does not launch Gemini English-safe adapter",
                    status_report=language_status,
                )
            )
            blockers.append(str(language_status.get("current_blocker") or "youtube_safe_story_wrong_language"))
            return finish("dry_run", "run youtube safe-regenerate --execute")
    if language_status.get("safe_story_status") != "done":
        stages.append(
            _stage_row(
                "safe_language_check",
                "blocked",
                "02_safe_story/safe_story.txt must be English for YouTube pipeline",
                status_report=language_status,
            )
        )
        blockers.append(str(language_status.get("current_blocker") or "youtube_safe_story_wrong_language"))
        return finish("blocked", "run youtube safe-regenerate")
    stages.append(
        _stage_row(
            "safe_language_check",
            "done",
            "safe_story matches expected English language",
            safe_story_path=language_status.get("safe_story_path"),
        )
    )

    promo_status = run_youtube_promo_status(config=config, options=YoutubePromoStatusOptions(story_id=story_id))
    if promo_status.get("status") == "done":
        stages.append(
            _stage_row(
                "promo_check",
                "done",
                "03_promo/text_ready_for_audio.txt has legacy promo inserts",
                output_path=promo_status.get("output_path"),
                climax_snippet_path=promo_status.get("climax_snippet_path"),
            )
        )
    else:
        if options.execute:
            promo_result = run_youtube_promo_run(
                config=config,
                options=YoutubePromoRunOptions(story_id=story_id, execute=True, fresh_gemini_session=True, account_index=0),
            )
            changed_files.extend(str(p) for p in (promo_result.get("changed_files") or []) if p)
            stages.append(
                _stage_row(
                    "promo_check",
                    str(promo_result.get("status", "unknown")),
                    "legacy youtube_tts promo_inserter",
                    result=promo_result,
                )
            )
            promo_status = run_youtube_promo_status(config=config, options=YoutubePromoStatusOptions(story_id=story_id))
            if promo_status.get("status") != "done":
                blockers.append("promo_not_done")
                return finish("blocked", "fix legacy promo insertion and rerun visuals-run --execute")
        else:
            stages.append(
                _stage_row(
                    "promo_check",
                    "would_run",
                    "promo missing or incomplete; dry-run does not launch legacy Gemini promo inserter",
                    status_report=promo_status,
                )
            )
            blockers.append("promo_not_done")
            return finish("dry_run", "run youtube promo-run --execute, then rerun visuals-run")

    promo_audio = promo_status.get("audio") if isinstance(promo_status.get("audio"), dict) else {}
    if not narration.is_file():
        stages.append(_stage_row("audio_check", "blocked", "missing 04_audio/narration.mp3", path=str(narration)))
        blockers.append("missing 04_audio/narration.mp3; run YouTube TTS from 03_promo/text_ready_for_audio.txt first")
        return finish("blocked", "run YouTube TTS from 03_promo/text_ready_for_audio.txt first")
    if promo_audio.get("stale"):
        stages.append(
            _stage_row(
                "audio_check",
                "stale",
                "narration.mp3 was created from older/non-promo text",
                path=str(narration),
                reason="youtube_audio_stale_after_promo_change",
            )
        )
        blockers.append("youtube_audio_stale_after_promo_change")
        return finish("blocked", "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt")
    stages.append(_stage_row("audio_check", "done", "narration.mp3 matches current promo text", path=str(narration)))

    characters = _characters_path(story_dir)
    if _is_nonempty(characters):
        characters_validation = validate_visual_characters_file(characters)
        if not characters_validation.get("ok", False):
            stages.append(
                _stage_row(
                    "characters",
                    "stale_or_invalid",
                    "characters.txt exists but contains stale/forbidden visual anchors or lacks stable adult age bands",
                    path=str(characters),
                    validation=characters_validation,
                )
            )
            blockers.append("youtube_visuals_characters_stale_or_invalid")
            return finish("blocked", "run youtube visuals-clean --execute, then regenerate characters")
        stages.append(_stage_row("characters", "done", "characters.txt exists and passed visual validation", path=str(characters), validation=characters_validation))
    else:
        drop_dir = _characters_drop_dir(story_dir)
        drop_file = drop_dir / "characters.txt"
        if auto_gemini_enabled:
            auto_result = run_youtube_characters_auto_gemini(
                config=config,
                options=YoutubeGeminiAutoStageOptions(story_id=story_id, execute=bool(options.execute)),
            )
            if options.execute:
                changed_files.extend(
                    str(auto_result.get(k))
                    for k in (
                        "legacy_log_path",
                        "target_characters_path",
                        "manifest_path",
                        "report_path",
                        "prompt_source_report_path",
                        "outgoing_message_debug_report_path",
                        "outgoing_message_debug_text_report_path",
                    )
                    if auto_result.get(k)
                )
            stages.append(
                _stage_row(
                    "characters",
                    str(auto_result.get("status", "unknown")),
                    "legacy Gemini characters automation",
                    result=auto_result,
                )
            )
            if auto_result.get("status") == "would_run":
                return finish("dry_run", "rerun with --execute --auto-gemini to generate characters automatically")
            if not auto_result.get("ok") or not _is_nonempty(characters):
                status = str(auto_result.get("status") or "characters_auto_gemini_failed")
                if status == "youtube_visuals_characters_browser_context_closed":
                    blockers.append(status)
                    return finish("blocked", "Chrome/Gemini browser context closed during Characters stage; check legacy log and rerun")
                blockers.append("characters_auto_gemini_failed")
                return finish("blocked", "fix legacy Gemini characters automation and rerun visuals-run --execute --auto-gemini")
        elif options.execute:
            blockers.append("blocked_missing_auto_gemini")
            stages.append(
                _stage_row(
                    "characters",
                    "blocked_missing_auto_gemini",
                    "characters.txt is missing and automatic Gemini mode is disabled",
                )
            )
            return finish("blocked", "rerun with --execute --auto-gemini")
        if not _is_nonempty(characters):
            if options.execute:
                export_result = run_youtube_characters_export(
                    config=config,
                    options=YoutubeCharactersExportOptions(story_id=story_id, execute=True),
                )
                changed_files.extend(
                    str(export_result.get(k))
                    for k in ("staging_story_txt", "staging_readme", "report_path")
                    if export_result.get(k)
                )
                staging_expected_file = _expected_output_path_file(_characters_staging_dir(story_dir))
                _write_expected_path(staging_expected_file, characters)
                changed_files.append(str(staging_expected_file))
                drop_dir.mkdir(parents=True, exist_ok=True)
                expected_file = _expected_output_path_file(drop_dir)
                _write_expected_path(expected_file, characters)
                changed_files.append(str(expected_file))
            if _is_nonempty(drop_file):
                if options.execute:
                    imp = run_youtube_characters_import(
                        config=config,
                        options=YoutubeCharactersImportOptions(story_id=story_id, source=drop_file, execute=True),
                    )
                    changed_files.extend(str(imp.get(k)) for k in ("target_characters_path", "manifest_path", "report_path") if imp.get(k))
                    stages.append(_stage_row("characters", "imported", "imported from drop", drop_path=str(drop_file)))
                else:
                    stages.append(_stage_row("characters", "would_import", "drop file exists", drop_path=str(drop_file)))
            elif options.watch and options.execute:
                stages.append(_stage_row("characters", "watching", "waiting for drop file", drop_path=str(drop_file)))
                if _wait_for_file(drop_file, interval_sec=options.watch_interval_sec, timeout_sec=options.watch_timeout_sec):
                    imp = run_youtube_characters_import(
                        config=config,
                        options=YoutubeCharactersImportOptions(story_id=story_id, source=drop_file, execute=True),
                    )
                    changed_files.extend(str(imp.get(k)) for k in ("target_characters_path", "manifest_path", "report_path") if imp.get(k))
                    stages.append(_stage_row("characters", "imported", "imported from drop after watch", drop_path=str(drop_file)))
                else:
                    blockers.append("blocked_waiting_for_characters")
                    return finish("blocked", "rerun with --execute --auto-gemini or provide 05_characters/_drop/characters.txt")
            else:
                stages.append(
                    _stage_row(
                        "characters",
                        "blocked_waiting_for_characters",
                        "characters.txt is missing; automatic mode requires --execute --auto-gemini",
                        staging_path=str(_characters_staging_dir(story_dir)),
                        drop_path=str(drop_dir),
                    )
                )
                blockers.append("blocked_waiting_for_characters")
                return finish("blocked", "rerun with --execute --auto-gemini")

    characters_validation = validate_visual_characters_file(characters)
    if not characters_validation.get("ok", False):
        stages.append(
            _stage_row(
                "characters_validation",
                "stale_or_invalid",
                "characters.txt failed visual validation after generation/import",
                path=str(characters),
                validation=characters_validation,
            )
        )
        blockers.append("youtube_visuals_characters_stale_or_invalid")
        return finish("blocked", "run youtube visuals-clean --execute, then regenerate characters with clean Characters prompt")

    prompts_path = _prompts_path(story_dir)
    if _is_nonempty(prompts_path):
        prompts_validation = validate_visual_prompts_file(prompts_path)
        legacy_stale_scan = scan_legacy_visual_stale_sources(config, story_id)
        if not prompts_validation.get("ok", False):
            stages.append(
                _stage_row(
                    "director_prompts",
                    "stale_or_invalid",
                    "prompts_list.txt exists but contains stale forbidden visual tokens",
                    path=str(prompts_path),
                    validation=prompts_validation,
                )
            )
            blockers.append("youtube_visuals_prompts_stale_or_invalid")
            return finish("blocked", "run youtube visuals-clean --execute, then regenerate characters/prompts")
        if not legacy_stale_scan.get("ok", False):
            stages.append(
                _stage_row(
                    "legacy_visual_sources",
                    "stale_or_invalid",
                    "legacy director story folders contain stale visual source artifacts",
                    scan=legacy_stale_scan,
                )
            )
            blockers.append("youtube_visuals_legacy_stale_source_detected")
            return finish("blocked", "run youtube visuals-clean --execute to quarantine stale legacy visual sources")
        prompts = _load_prompts(prompts_path)
        stages.append(_stage_row("director_prompts", "done", "prompts_list.txt exists and passed visual validation", path=str(prompts_path), prompts_count=len(prompts), validation=prompts_validation))
    else:
        drop_dir = _prompts_drop_dir(story_dir)
        drop_file = drop_dir / "prompts_list.txt"
        if auto_gemini_enabled:
            auto_result = run_youtube_director_prompts_auto_gemini(
                config=config,
                options=YoutubeGeminiAutoStageOptions(story_id=story_id, execute=bool(options.execute)),
            )
            if options.execute:
                changed_files.extend(
                    str(auto_result.get(k))
                    for k in ("legacy_log_path", "target_prompts_path", "manifest_path", "report_path")
                    if auto_result.get(k)
                )
            stages.append(
                _stage_row(
                    "director_prompts",
                    str(auto_result.get("status", "unknown")),
                    "legacy Gemini director automation",
                    result=auto_result,
                )
            )
            if auto_result.get("status") == "would_run":
                return finish("dry_run", "rerun with --execute --auto-gemini to generate director prompts automatically")
            if not auto_result.get("ok") or not _load_prompts(prompts_path):
                blockers.append("director_prompts_auto_gemini_failed")
                return finish("blocked", "fix legacy Gemini director automation and rerun visuals-run --execute --auto-gemini")
        elif options.execute:
            blockers.append("blocked_missing_auto_gemini")
            stages.append(
                _stage_row(
                    "director_prompts",
                    "blocked_missing_auto_gemini",
                    "prompts_list.txt is missing and automatic Gemini mode is disabled",
                )
            )
            return finish("blocked", "rerun with --execute --auto-gemini")
        if not _load_prompts(prompts_path):
            if options.execute:
                export_result = run_youtube_director_prompts_export(
                    config=config,
                    options=YoutubeDirectorPromptsExportOptions(story_id=story_id, execute=True),
                )
                if not export_result.get("ok", False):
                    stages.append(_stage_row("director_prompts", "blocked", "missing prompt export inputs", missing=export_result.get("missing", [])))
                    blockers.append("blocked_missing_prompt_inputs")
                    return finish("blocked", "finish characters/audio/story inputs for director prompts")
                changed_files.extend(
                    str(export_result.get(k))
                    for k in ("staging_story_txt", "staging_characters_txt", "staging_narration_path_txt", "staging_readme", "report_path")
                    if export_result.get(k)
                )
                narration_info = _prompts_staging_dir(story_dir) / "narration_info.json"
                _write_json(
                    narration_info,
                    {
                        "audio_path": str(narration),
                        "duration_sec": _duration_sec(narration),
                        "estimated_prompts": _prompt_estimate(story_dir),
                    },
                )
                changed_files.append(str(narration_info))
                staging_expected_file = _expected_output_path_file(_prompts_staging_dir(story_dir))
                _write_expected_path(staging_expected_file, prompts_path)
                changed_files.append(str(staging_expected_file))
                drop_dir.mkdir(parents=True, exist_ok=True)
                expected_file = _expected_output_path_file(drop_dir)
                _write_expected_path(expected_file, prompts_path)
                changed_files.append(str(expected_file))
            if _is_nonempty(drop_file):
                if options.execute:
                    imp = run_youtube_director_prompts_import(
                        config=config,
                        options=YoutubeDirectorPromptsImportOptions(story_id=story_id, source=drop_file, execute=True),
                    )
                    changed_files.extend(str(imp.get(k)) for k in ("target_prompts_path", "manifest_path", "report_path") if imp.get(k))
                    stages.append(_stage_row("director_prompts", "imported", "imported from drop", drop_path=str(drop_file), prompts_count=imp.get("prompts_count")))
                else:
                    stages.append(_stage_row("director_prompts", "would_import", "drop file exists", drop_path=str(drop_file)))
            elif options.watch and options.execute:
                stages.append(_stage_row("director_prompts", "watching", "waiting for drop file", drop_path=str(drop_file)))
                if _wait_for_file(drop_file, interval_sec=options.watch_interval_sec, timeout_sec=options.watch_timeout_sec):
                    imp = run_youtube_director_prompts_import(
                        config=config,
                        options=YoutubeDirectorPromptsImportOptions(story_id=story_id, source=drop_file, execute=True),
                    )
                    changed_files.extend(str(imp.get(k)) for k in ("target_prompts_path", "manifest_path", "report_path") if imp.get(k))
                    stages.append(_stage_row("director_prompts", "imported", "imported from drop after watch", drop_path=str(drop_file), prompts_count=imp.get("prompts_count")))
                else:
                    blockers.append("blocked_waiting_for_prompts")
                    return finish("blocked", "rerun with --execute --auto-gemini or provide 06_prompts/_drop/prompts_list.txt")
            else:
                stages.append(
                    _stage_row(
                        "director_prompts",
                        "blocked_waiting_for_prompts",
                        "prompts_list.txt is missing; automatic mode requires --execute --auto-gemini",
                        staging_path=str(_prompts_staging_dir(story_dir)),
                        drop_path=str(drop_dir),
                    )
                )
                blockers.append("blocked_waiting_for_prompts")
                return finish("blocked", "rerun with --execute --auto-gemini")

    prompts_validation = validate_visual_prompts_file(prompts_path)
    if not prompts_validation.get("ok", False):
        stages.append(
            _stage_row(
                "director_prompts_validation",
                "stale_or_invalid",
                "prompts_list.txt failed stale token validation after generation/import",
                path=str(prompts_path),
                validation=prompts_validation,
            )
        )
        blockers.append("youtube_visuals_prompts_stale_or_invalid")
        return finish("blocked", "run youtube visuals-clean --execute, then regenerate characters/prompts")
    legacy_stale_scan = scan_legacy_visual_stale_sources(config, story_id)
    if not legacy_stale_scan.get("ok", False):
        stages.append(_stage_row("legacy_visual_sources", "stale_or_invalid", "legacy director story folders contain stale visual source artifacts", scan=legacy_stale_scan))
        blockers.append("youtube_visuals_legacy_stale_source_detected")
        return finish("blocked", "run youtube visuals-clean --execute to quarantine stale legacy visual sources")

    prompts = _load_prompts(prompts_path)
    if not prompts:
        stages.append(_stage_row("frames", "blocked_missing_prompts", "prompts are missing or empty"))
        blockers.append("blocked_missing_prompts")
        return finish("blocked", "import prompts_list.txt first")

    frames_status = _frame_status(_frames_dir(story_dir), prompts)

    if frames_status["not_done"] > 0:
        prep = run_youtube_frames_runpod_bridge(
            config=config,
            options=YoutubeFramesRunpodBridgeOptions(
                story_id=story_id,
                runpod_url="",
                execute=False,
                prepare_only=bool(options.execute),
                workflow=options.workflow,
            ),
        )
        if options.execute:
            changed_files.extend(str(prep.get(k)) for k in ("frame_jobs_path", "failed_frames_path", "report_path") if prep.get(k))
        workflow_meta = prep.get("workflow") if isinstance(prep.get("workflow"), dict) else {}
        workflow_validation = prep.get("workflow_validation") if isinstance(prep.get("workflow_validation"), dict) else {}
        if not prep.get("ok", False):
            stages.append(
                _stage_row(
                    "frames_workflow",
                    str(prep.get("status", "invalid")),
                    "workflow validation failed before RunPod",
                    workflow=workflow_meta,
                    workflow_validation=workflow_validation,
                    result=prep,
                )
            )
            blockers.append("frames_workflow_invalid")
            return finish("blocked", "fix selected workflow preset or choose another --workflow")
        stages.append(
            _stage_row(
                "ready_for_runpod",
                "ready",
                "characters/prompts are ready; RunPod URL is needed only now",
                workflow=workflow_meta,
                prompt_mode="raw",
                expected_frames=frames_status["expected"],
                valid_frames=frames_status["generated"],
                missing_frames=frames_status["pending"],
                failed_frames=frames_status["failed"],
            )
        )
        runpod_url = options.runpod_url.strip()
        if options.execute and not runpod_url and options.prompt_runpod_url and sys.stdin.isatty():
            runpod_url = _ask_runpod_url_at_checkpoint(story_id=story_id, frame_status=frames_status, workflow=workflow_meta)
        if not runpod_url:
            stages.append(_stage_row("frames", "ready_for_runpod", "RunPod not started yet; paste URL at checkpoint or pass --runpod-url after prompts are ready", **frames_status))
            blockers.append("ready_for_runpod")
            return finish("ready_for_runpod", "start RunPod, then rerun visuals-run --execute --auto-gemini and paste URL at checkpoint")
        if options.execute:
            frame_result = run_youtube_frames_runpod_bridge(
                config=config,
                options=YoutubeFramesRunpodBridgeOptions(
                    story_id=story_id,
                    runpod_url=runpod_url,
                    execute=True,
                    workflow=options.workflow,
                ),
            )
            changed_files.extend(str(frame_result.get(k)) for k in ("frame_jobs_path", "failed_frames_path", "report_path", "manifest_path") if frame_result.get(k))
            stages.append(_stage_row("frames", str(frame_result.get("status", "unknown")), "frames RunPod bridge finished", result=frame_result))
            if frame_result.get("status") != "done":
                blockers.append("frames incomplete")
                return finish("blocked", "fix failed/missing frames and rerun visuals-run")
        else:
            stages.append(_stage_row("frames", "would_generate", "frames incomplete; dry-run does not call RunPod", **frames_status))
            return finish("dry_run", "rerun with --execute --auto-gemini; RunPod URL will be requested at READY_FOR_RUNPOD")
    else:
        stages.append(_stage_row("frames", "done", "all frames are valid", **frames_status))

    if options.execute:
        prep = run_youtube_video_prepare_segments(
            config=config,
            options=YoutubeVideoPrepareSegmentsOptions(story_id=story_id, segment_sec=options.segment_sec, execute=True),
        )
    else:
        prep = run_youtube_video_prepare_segments(
            config=config,
            options=YoutubeVideoPrepareSegmentsOptions(story_id=story_id, segment_sec=options.segment_sec, execute=False),
        )
    stages.append(_stage_row("video_prepare_segments", str(prep.get("status", "failed")), "prepared segment jobs" if prep.get("ok") else str(prep.get("message", "")), result=prep))
    if prep.get("ok") and options.execute:
        changed_files.extend(str(prep.get(k)) for k in ("timeline_path", "segment_jobs_path") if prep.get(k))
        _update_manifest_visuals(
            story_dir,
            {
                "video_segments": {
                    "status": "prepared",
                    "timeline_path": prep.get("timeline_path"),
                    "segment_jobs_path": prep.get("segment_jobs_path"),
                    "total_segments": prep.get("total_segments"),
                    "updated_at": _now_iso(),
                }
            },
        )
    if not prep.get("ok"):
        blockers.append("video segments prepare failed")
        return finish("blocked", "fix frames/audio and rerun visuals-run")

    return finish("done" if options.execute else "dry_run", "visuals ready; next step is video segment render/dispatcher")
