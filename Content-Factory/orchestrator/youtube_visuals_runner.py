"""Single-story YouTube visuals state machine.

This runner orchestrates local bridge steps only. It does not launch Gemini
unless a future explicit adapter is added, and it only calls RunPod/ComfyUI via
the existing frames bridge when both --execute and --runpod-url are provided.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.launch_contract import build_launch_context
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
    YoutubeGeminiBatchOptions,
    YoutubeGeminiBatchStory,
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
    run_youtube_characters_batch_auto_gemini,
    run_youtube_director_prompts_auto_gemini,
    run_youtube_director_prompts_batch_auto_gemini,
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
    youtube_run_id: str = ""
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
    accept_known_promo_issues: bool = False


@dataclass
class YoutubeVisualsStatusOptions:
    story_id: str = ""
    youtube_run_id: str = ""
    accept_known_promo_issues: bool = False


@dataclass
class YoutubeVisualsRunAllOptions:
    youtube_run_id: str
    story_id: str = ""
    runpod_url: str = ""
    workflow: str = ""
    workers: int = 1
    limit: int = 0
    execute: bool = False
    auto_gemini: bool = False
    allow_gemini: bool = False
    accept_known_promo_issues: bool = False
    segment_sec: float = 180.0
    prompt_runpod_url: bool = False


@dataclass
class YoutubeStageSetOptions:
    youtube_run_id: str
    stage: str
    execute: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _story_dir(config: OrchestratorConfig, story_id: str, *, youtube_run_id: str | None = None) -> Path:
    from orchestrator.youtube_path_resolver import resolve_youtube_technical_story_dir

    return resolve_youtube_technical_story_dir(config, story_id, launch_id=youtube_run_id)


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


def _is_excluded_from_video(manifest: dict[str, Any]) -> bool:
    return bool(manifest.get("excluded_from_video") or manifest.get("drop_from_video_queue"))


def _launch_policy_accepts_promo_issues(manifest: dict[str, Any], *, explicit: bool = False) -> bool:
    policy = str(
        manifest.get("current_launch_policy")
        or manifest.get("launch_policy")
        or manifest.get("video_launch_policy")
        or ""
    ).strip()
    return bool(explicit or policy == "ACCEPTED_WITH_KNOWN_PROMO_ISSUES")


def _audio_ready_for_video(story_dir: Path, manifest: dict[str, Any]) -> bool:
    if _is_excluded_from_video(manifest):
        return False
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    if audio.get("valid_for_video") is False:
        return False
    return _narration_path(story_dir).is_file()


def _iter_launch_story_dirs(config: OrchestratorConfig, launch_id: str) -> list[Path]:
    ctx = build_launch_context(config, launch_id=launch_id)
    if not ctx.youtube_root.is_dir():
        return []
    return sorted(
        p
        for p in ctx.youtube_root.iterdir()
        if p.is_dir() and (p / "youtube_story_manifest.json").is_file()
    )


def _story_identity(story_dir: Path, manifest: dict[str, Any]) -> tuple[str, str]:
    story_id = str(manifest.get("story_id") or story_dir.name).strip() or story_dir.name
    title = str(manifest.get("title") or manifest.get("canonical_basename") or story_id).strip() or story_id
    return story_id, title


def _fmt_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _story_visual_readiness(config: OrchestratorConfig, story_dir: Path, story_id: str) -> dict[str, Any]:
    characters = _characters_path(config, story_id, story_dir)
    prompts_path = _prompts_path(config, story_id, story_dir)
    prompts = _load_prompts(prompts_path)
    frames = _frame_status(_frames_dir(config, story_id, story_dir), prompts)
    characters_ready = _is_nonempty(characters)
    prompts_ready = _is_nonempty(prompts_path)
    images_ready = (
        int(frames.get("expected") or 0) > 0
        and int(frames.get("pending") or 0) == 0
        and int(frames.get("failed") or 0) == 0
    )
    return {
        "characters_ready": characters_ready,
        "prompts_ready": prompts_ready,
        "images_ready": images_ready,
        "frames_expected": int(frames.get("expected") or 0),
        "frames_valid": int(frames.get("generated") or 0),
        "characters_status": "ok" if characters_ready else "missing",
        "prompts_status": "ok" if prompts_ready else "missing",
        "images_status": "ok" if images_ready else "skip",
    }


def _prompt_worker_user_data_dir(config: OrchestratorConfig, worker_index: int) -> str:
    if worker_index <= 1:
        return ""
    director_dir = (config.root_dir / config.legacy_modules.get("director_2_0", "legacy/director_2_0")).resolve()
    worker_root = director_dir / "worker_profiles" / f"prompts_worker_{worker_index}"
    target = worker_root / "user_data"
    source = director_dir / "user_data"
    if target.exists():
        return str(target)
    if source.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        return str(target)
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _run_prompts_worker_story(
    *,
    config: OrchestratorConfig,
    launch_id: str,
    story_id: str,
    execute: bool,
    worker_index: int,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_session

    with isolated_session(None, batch_launch_id=launch_id, config=config):
        return run_youtube_director_prompts_auto_gemini(
            config=config,
            options=YoutubeGeminiAutoStageOptions(
                story_id=story_id,
                execute=execute,
                user_data_dir=_prompt_worker_user_data_dir(config, worker_index) if execute else "",
            ),
        )


def _run_prompts_worker_batch(
    *,
    config: OrchestratorConfig,
    launch_id: str,
    stories: list[YoutubeGeminiBatchStory],
    execute: bool,
    worker_index: int,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_session

    with isolated_session(None, batch_launch_id=launch_id, config=config):
        return run_youtube_director_prompts_batch_auto_gemini(
            config=config,
            options=YoutubeGeminiBatchOptions(
                stories=stories,
                execute=execute,
                user_data_dir=_prompt_worker_user_data_dir(config, worker_index) if execute else "",
                worker_label=f"worker_{worker_index}",
            ),
        )


def set_launch_stage(
    config: OrchestratorConfig,
    *,
    launch_id: str,
    stage: str,
    execute: bool,
    reason: str = "",
) -> dict[str, Any]:
    ctx = build_launch_context(config, launch_id=launch_id)
    status_path = ctx.launch_root / "queue" / "stage_status.json"
    current = _read_json(status_path) if status_path.is_file() else {}
    if not isinstance(current, dict):
        current = {}
    updated = {
        **current,
        "updated_at": _now_iso(),
        "youtube_run_id": ctx.launch_id,
        "current_stage": str(stage).strip(),
    }
    if reason:
        updated["stage_change_reason"] = reason
    if execute:
        _write_json(status_path, updated)
    return {
        "ok": True,
        "execute": bool(execute),
        "youtube_run_id": ctx.launch_id,
        "stage": str(stage).strip(),
        "stage_status_path": str(status_path),
        "changed": bool(execute),
    }


def mark_story_excluded_from_video(
    config: OrchestratorConfig,
    *,
    launch_id: str,
    story_id: str,
    reason: str,
    execute: bool,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_session

    with isolated_session(None, batch_launch_id=launch_id, config=config):
        story_dir = _story_dir(config, story_id, youtube_run_id=launch_id)
        manifest_path = _story_manifest_path(story_dir)
        manifest = _load_manifest(story_dir)
        if not manifest:
            return {"ok": False, "message": f"manifest missing: {manifest_path}", "execute": bool(execute)}
        story_key, title = _story_identity(story_dir, manifest)
        manifest.update(
            {
                "excluded_from_video": True,
                "exclude_reason": reason,
                "current_launch_action": "drop_from_video_queue",
                "excluded_from_video_at": _now_iso(),
            }
        )
        status = dict(manifest.get("pipeline_stage_status") or {})
        for stage in ("visuals", "frames", "video", "assemble", "publish"):
            status[stage] = "excluded"
        manifest["pipeline_stage_status"] = status
        if execute:
            _write_json(manifest_path, manifest)
        return {
            "ok": True,
            "execute": bool(execute),
            "story_id": story_key,
            "title": title,
            "story_dir": str(story_dir),
            "manifest_path": str(manifest_path),
            "excluded_from_video": True,
            "exclude_reason": reason,
        }


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
                "characters_txt": str(_characters_path(config, story_id, story_dir)),
                "prompts_list_txt": str(_prompts_path(config, story_id, story_dir)),
                "frames_dir": str(_frames_dir(config, story_id, story_dir)),
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
    from orchestrator.isolated_launch_context import get_batch_launch_id, isolated_session

    story_id = str(options.story_id).strip()
    launch_id = str(options.youtube_run_id or get_batch_launch_id() or "").strip()
    active_launch_id = str(get_batch_launch_id() or "").strip()
    if launch_id and active_launch_id != launch_id:
        with isolated_session(None, batch_launch_id=launch_id, config=config):
            return run_youtube_visuals_status(config=config, options=options)
    story_dir = _story_dir(config, story_id, youtube_run_id=launch_id)
    manifest = _load_manifest(story_dir)
    excluded_from_video = _is_excluded_from_video(manifest)
    promo_issues_accepted = _launch_policy_accepts_promo_issues(
        manifest,
        explicit=bool(options.accept_known_promo_issues),
    )
    safe_story = _safe_story_path(story_dir)
    audio_text = _audio_text_path(story_dir)
    narration = _narration_path(story_dir)
    characters = _characters_path(config, story_id, story_dir)
    prompts_path = _prompts_path(config, story_id, story_dir)
    prompts = _load_prompts(prompts_path)
    frames_dir = _frames_dir(config, story_id, story_dir)
    frame_status = _frame_status(frames_dir, prompts)
    video_segments = _video_segments_status(story_dir)
    audio_duration = _duration_sec(narration)
    promo_status = run_youtube_promo_status(
        config=config,
        options=YoutubePromoStatusOptions(story_id=story_id, youtube_run_id=launch_id),
    )
    language_status = build_youtube_safe_status(config=config, story_id=story_id, expected_language=EXPECTED_YOUTUBE_LANGUAGE)
    anchor_audit = run_youtube_characters_anchor_audit(config=config, options=YoutubeCharactersAnchorAuditOptions(story_id=story_id))
    characters_validation = validate_visual_characters_file(characters)
    prompts_validation = validate_visual_prompts_file(prompts_path)
    legacy_stale_scan = scan_legacy_visual_stale_sources(config, story_id)
    promo_audio = promo_status.get("audio") if isinstance(promo_status.get("audio"), dict) else {}

    current_blocker = ""
    next_action = "visuals pipeline ready"
    if excluded_from_video:
        current_blocker = "excluded_from_video"
        next_action = "story is intentionally dropped from video queue"
    elif not story_dir.is_dir():
        current_blocker = "missing story folder"
        next_action = "check --story-id"
    elif not safe_story.is_file():
        current_blocker = "missing 02_safe_story/safe_story.txt"
        next_action = "finish safe story first"
    elif language_status.get("safe_story_status") == "wrong_language":
        current_blocker = "youtube_safe_story_wrong_language"
        next_action = "run youtube safe-regenerate"
    elif promo_status.get("status") != "done" and not promo_issues_accepted:
        current_blocker = str(promo_status.get("current_blocker") or "promo_not_done")
        next_action = str(promo_status.get("next_action") or "run youtube promo-run --execute first")
    elif not narration.is_file():
        current_blocker = "missing 04_audio/narration.mp3"
        next_action = "run YouTube TTS from 03_promo/text_ready_for_audio.txt first"
    elif promo_audio.get("stale") and not promo_issues_accepted:
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
        "excluded_from_video": excluded_from_video,
        "exclude_reason": str(manifest.get("exclude_reason") or ""),
        "promo_issues_accepted": promo_issues_accepted,
        "safe_story": "exists" if safe_story.is_file() else "missing",
        "language": language_status,
        "promo": promo_status,
        "text_ready_for_audio": "exists" if audio_text.is_file() else "missing",
        "narration": {
            "status": promo_audio.get("status") or ("exists" if narration.is_file() else "missing"),
            "path": str(narration),
            "duration_sec": audio_duration,
            "stale": bool(promo_audio.get("stale")) and not promo_issues_accepted,
            "stale_accepted": bool(promo_audio.get("stale")) and promo_issues_accepted,
        },
        "characters": {
            "status": "done" if _is_nonempty(characters) and characters_validation.get("ok") else ("stale_or_invalid" if _is_nonempty(characters) else "missing"),
            "path": str(characters),
            "staging_path": str(_characters_staging_dir(config, story_id, story_dir)),
            "drop_path": str(_characters_drop_dir(story_dir)),
            "anchor_audit": anchor_audit,
            "validation": characters_validation,
        },
        "prompts": {
            "status": "done" if _is_nonempty(prompts_path) and prompts_validation.get("ok") else ("stale_or_invalid" if _is_nonempty(prompts_path) else "missing"),
            "path": str(prompts_path),
            "prompts_count": len(prompts),
            "estimated_prompts": _prompt_estimate(story_dir),
            "staging_path": str(_prompts_staging_dir(config, story_id, story_dir)),
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


def run_youtube_visuals_launch_status(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualsStatusOptions,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_session

    launch_id = str(options.youtube_run_id or "").strip()
    if not launch_id:
        return {"ok": False, "message": "--youtube-run-id is required for launch visuals status"}

    rows: list[dict[str, Any]] = []
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        for story_dir in _iter_launch_story_dirs(config, launch_id):
            manifest = _load_manifest(story_dir)
            story_id, title = _story_identity(story_dir, manifest)
            excluded = _is_excluded_from_video(manifest)
            promo_accepted = _launch_policy_accepts_promo_issues(
                manifest,
                explicit=bool(options.accept_known_promo_issues),
            )
            narration = _narration_path(story_dir)
            characters = _characters_path(config, story_id, story_dir)
            prompts_path = _prompts_path(config, story_id, story_dir)
            prompts = _load_prompts(prompts_path)
            frames = _frame_status(_frames_dir(config, story_id, story_dir), prompts)
            audio_ready = _audio_ready_for_video(story_dir, manifest)
            visual_prompt_ready = _is_nonempty(prompts_path)
            images_ready = int(frames.get("expected") or 0) > 0 and int(frames.get("pending") or 0) == 0 and int(frames.get("failed") or 0) == 0
            if excluded:
                blocker = "excluded_from_video"
                next_action = "story is intentionally dropped from video queue"
            elif not audio_ready:
                blocker = "missing 04_audio/narration.mp3"
                next_action = "run/import YouTube TTS first"
            elif not _is_nonempty(characters):
                blocker = "missing 05_characters/characters.txt"
                next_action = "run visuals-run --execute --auto-gemini to generate characters automatically"
            elif not visual_prompt_ready:
                blocker = "missing 06_prompts/prompts_list.txt"
                next_action = "run visuals-run --execute --auto-gemini to generate director prompts automatically"
            elif not images_ready:
                blocker = "ready_for_runpod"
                next_action = "start RunPod, run visuals-run --execute --auto-gemini, then paste URL"
            else:
                blocker = ""
                next_action = "visuals pipeline ready"
            audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
            row = {
                "story_id": story_id,
                "title": title,
                "story_dir": str(story_dir),
                "excluded_from_video": excluded,
                "exclude_reason": str(manifest.get("exclude_reason") or ""),
                "promo_issues_accepted": promo_accepted,
                "audio_ready": audio_ready,
                "visual_prompt_ready": visual_prompt_ready,
                "images_ready": images_ready,
                "frames_expected": int(frames.get("expected") or 0),
                "frames_valid": int(frames.get("generated") or 0),
                "audio_duration_sec": audio.get("duration_sec"),
                "blocker": blocker,
                "next_action": next_action,
            }
            rows.append(row)

    active_rows = [row for row in rows if not row["excluded_from_video"]]
    summary = {
        "total_stories": len(rows),
        "total_tts_imported": sum(1 for row in rows if row["audio_ready"] or row["excluded_from_video"]),
        "excluded_from_video": sum(1 for row in rows if row["excluded_from_video"]),
        "ready_for_video": sum(1 for row in active_rows if row["audio_ready"]),
        "audio_ready": sum(1 for row in active_rows if row["audio_ready"]),
        "visual_prompts_ready": sum(1 for row in active_rows if row["visual_prompt_ready"]),
        "images_ready": sum(1 for row in active_rows if row["images_ready"]),
        "blocked": sum(1 for row in active_rows if row["blocker"] and row["blocker"] != "ready_for_runpod"),
        "pending": sum(1 for row in active_rows if row["audio_ready"] and not row["visual_prompt_ready"]),
        "ready_for_frames": sum(1 for row in active_rows if row["visual_prompt_ready"] and not row["images_ready"]),
        "known_promo_issues_accepted": any(row["promo_issues_accepted"] for row in rows),
    }
    ctx = build_launch_context(config, launch_id=launch_id)
    report = {
        "ok": True,
        "launch_id": ctx.launch_id,
        "generated_at": _now_iso(),
        "summary": summary,
        "stories": rows,
    }
    report_path = ctx.launch_root / "07_reports" / "YOUTUBE_VISUALS_STATUS.json"
    _write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def run_youtube_visuals_run_all(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualsRunAllOptions,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_session

    launch_id = str(options.youtube_run_id or "").strip()
    if not launch_id:
        return {"ok": False, "message": "--youtube-run-id is required"}
    ctx = build_launch_context(config, launch_id=launch_id)
    selected: list[Path] = []
    eligible: list[Path] = []
    skipped: list[dict[str, Any]] = []
    started_at = time.monotonic()
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        all_story_dirs = _iter_launch_story_dirs(config, launch_id)
        for story_dir in all_story_dirs:
            manifest = _load_manifest(story_dir)
            story_id, title = _story_identity(story_dir, manifest)
            if options.story_id and story_id.casefold() != options.story_id.casefold() and story_dir.name.casefold() != options.story_id.casefold():
                continue
            if _is_excluded_from_video(manifest):
                skipped.append(
                    {
                        "story_id": story_id,
                        "title": title,
                        "reason": "excluded_from_video",
                        "exclude_reason": str(manifest.get("exclude_reason") or ""),
                    }
                )
                continue
            if not _audio_ready_for_video(story_dir, manifest):
                skipped.append({"story_id": story_id, "title": title, "reason": "audio_not_ready"})
                continue
            eligible.append(story_dir)
        selected = eligible[: int(options.limit)] if options.limit else eligible

        excluded_count = sum(1 for row in skipped if row.get("reason") == "excluded_from_video")
        active_queue_count = len(selected)
        print("================ YOUTUBE VISUALS RUN ALL ================", flush=True)
        print(f"launch_id: {ctx.launch_id}", flush=True)
        print(f"total stories:        {len(all_story_dirs)}", flush=True)
        print(f"excluded_from_video:  {excluded_count}", flush=True)
        print(f"active queue:         {active_queue_count}", flush=True)
        print(f"auto_gemini:          {str(bool(options.auto_gemini or options.allow_gemini)).lower()}", flush=True)
        print(f"accept_promo_issues:  {str(bool(options.accept_known_promo_issues)).lower()}", flush=True)
        if options.limit:
            print(f"limit:                {int(options.limit)}", flush=True)
        print("==========================================================", flush=True)

        if options.execute and selected:
            set_launch_stage(
                config,
                launch_id=launch_id,
                stage="visuals",
                execute=True,
                reason="visuals-run-all started",
            )

        results: list[dict[str, Any]] = []
        progress = {"done": 0, "failed": 0, "blocked": 0, "skipped": 0}
        print("================ PHASE 1: CHARACTERS ====================", flush=True)
        character_jobs: list[tuple[int, Path, str, str]] = []
        for index, story_dir in enumerate(selected, start=1):
            manifest = _load_manifest(story_dir)
            story_id, title = _story_identity(story_dir, manifest)
            before = _story_visual_readiness(config, story_dir, story_id)
            if before["characters_ready"]:
                progress["done"] += 1
                progress["skipped"] += 1
                run_result = {
                    "ok": True,
                    "status": "characters_already_ready",
                    "next_action": "already_has_characters",
                    "story_dir": str(story_dir),
                    "blockers": [],
                }
                print(
                    f"[{index}/{active_queue_count}] SKIP characters: {story_id} / {title} | reason=already_has_characters | elapsed=00:00:00",
                    flush=True,
                )
                results.append(
                    {
                        "stage": "characters",
                        "story_id": story_id,
                        "title": title,
                        "status": run_result.get("status"),
                        "ok": bool(run_result.get("ok")),
                        "next_action": run_result.get("next_action"),
                        "blockers": run_result.get("blockers") or [],
                        "story_dir": run_result.get("story_dir"),
                    }
                )
            else:
                character_jobs.append((index, story_dir, story_id, title))

        if character_jobs:
            print(
                "CHARACTERS BROWSER: opening 1 browser; max 5 story requests per dialog before reload/new dialog.",
                flush=True,
            )
            for index, _story_dir, story_id, title in character_jobs:
                print(f"[{index}/{active_queue_count}] QUEUE characters: {story_id} / {title}", flush=True)
            batch_started = time.monotonic()
            batch_root = ctx.launch_root / "10_Временные_файлы" / "visuals_gemini_batch" / "characters"
            batch_stories = [
                YoutubeGeminiBatchStory(story_id=story_id, story_dir=story_dir, stage_dir=batch_root / story_id)
                for _index, story_dir, story_id, _title in character_jobs
            ]
            try:
                batch_result = run_youtube_characters_batch_auto_gemini(
                    config=config,
                    options=YoutubeGeminiBatchOptions(stories=batch_stories, execute=bool(options.execute)),
                )
            except Exception as exc:
                batch_result = {"ok": False, "status": "failed", "next_action": repr(exc), "blockers": [type(exc).__name__]}
            for index, story_dir, story_id, title in character_jobs:
                after = _story_visual_readiness(config, story_dir, story_id)
                elapsed = _fmt_elapsed(time.monotonic() - batch_started)
                status = str(batch_result.get("status") or "")
                if after["characters_ready"]:
                    progress["done"] += 1
                    label = "DONE"
                    ok = True
                elif status == "would_run":
                    progress["done"] += 1
                    label = "DRY-RUN"
                    ok = True
                else:
                    progress["failed"] += 1
                    label = "FAILED"
                    ok = False
                print(
                    f"[{index}/{active_queue_count}] {label} characters: {story_id} / {title} | "
                    f"characters={after['characters_status']} | elapsed={elapsed}",
                    flush=True,
                )
                if label == "FAILED":
                    print(f"[{index}/{active_queue_count}] FAILED reason={batch_result.get('next_action') or batch_result.get('status')}", flush=True)
                results.append(
                    {
                        "stage": "characters",
                        "story_id": story_id,
                        "title": title,
                        "status": batch_result.get("status"),
                        "ok": ok,
                        "next_action": batch_result.get("next_action"),
                        "blockers": batch_result.get("blockers") or batch_result.get("missing") or [],
                        "story_dir": str(story_dir),
                    }
                )
                processed = progress["done"] + progress["failed"] + progress["blocked"]
                pending = active_queue_count - processed
                print(
                    "PROGRESS: "
                    f"done={progress['done']} failed={progress['failed']} blocked={progress['blocked']} "
                    f"pending={max(0, pending)} remaining={max(0, pending)}",
                    flush=True,
                )

        characters_missing = []
        for story_dir in selected:
            manifest = _load_manifest(story_dir)
            story_id, title = _story_identity(story_dir, manifest)
            readiness = _story_visual_readiness(config, story_dir, story_id)
            if not readiness["characters_ready"]:
                characters_missing.append({"story_id": story_id, "title": title})

        if characters_missing:
            print("================ PHASE 2: PROMPTS SKIPPED ===============", flush=True)
            print(
                f"reason=characters_not_ready missing_characters={len(characters_missing)}",
                flush=True,
            )
            for row in characters_missing:
                print(f"- {row.get('story_id')} / {row.get('title')} -> missing characters", flush=True)
        else:
            print("================ PHASE 2: PROMPTS =======================", flush=True)
            prompt_workers = max(1, int(options.workers or 1))
            print(f"prompt_workers:       {prompt_workers}", flush=True)
            prompt_progress = {"done": 0, "failed": 0, "blocked": 0, "skipped": 0}
            prompt_jobs: list[tuple[int, Path, str, str]] = []
            for index, story_dir in enumerate(selected, start=1):
                manifest = _load_manifest(story_dir)
                story_id, title = _story_identity(story_dir, manifest)
                before = _story_visual_readiness(config, story_dir, story_id)
                story_started = time.monotonic()
                if before["prompts_ready"]:
                    prompt_progress["done"] += 1
                    prompt_progress["skipped"] += 1
                    run_result = {
                        "ok": True,
                        "status": "prompts_already_ready",
                        "next_action": "already_has_prompts",
                        "story_dir": str(story_dir),
                        "blockers": [],
                    }
                    elapsed = _fmt_elapsed(time.monotonic() - story_started)
                    print(
                        f"[{index}/{active_queue_count}] SKIP prompts: {story_id} / {title} | reason=already_has_prompts | elapsed={elapsed}",
                        flush=True,
                    )
                    results.append(
                        {
                            "stage": "prompts",
                            "story_id": story_id,
                            "title": title,
                            "status": run_result.get("status"),
                            "ok": bool(run_result.get("ok")),
                            "next_action": run_result.get("next_action"),
                            "blockers": run_result.get("blockers") or [],
                            "story_dir": run_result.get("story_dir"),
                        }
                    )
                    print(
                        "PROMPTS PROGRESS: "
                        f"done={prompt_progress['done']} failed={prompt_progress['failed']} blocked={prompt_progress['blocked']} "
                        f"pending={active_queue_count - index} remaining={active_queue_count - index}",
                        flush=True,
                    )
                else:
                    prompt_jobs.append((index, story_dir, story_id, title))

            if prompt_jobs:
                if not options.execute:
                    for job_number, (index, story_dir, story_id, title) in enumerate(prompt_jobs, start=1):
                        prompt_progress["done"] += 1
                        print(
                            f"[{index}/{active_queue_count}] DRY-RUN prompts: {story_id} / {title} | "
                            f"bot=gemini_director | worker={(job_number - 1) % prompt_workers + 1}/{prompt_workers} | prompts=missing",
                            flush=True,
                        )
                        results.append(
                            {
                                "stage": "prompts",
                                "story_id": story_id,
                                "title": title,
                                "status": "would_run",
                                "ok": True,
                                "next_action": None,
                                "blockers": [],
                                "story_dir": str(story_dir),
                            }
                        )
                        pending = len(prompt_jobs) - job_number
                        print(
                            "PROMPTS PROGRESS: "
                            f"done={prompt_progress['done']} failed={prompt_progress['failed']} blocked={prompt_progress['blocked']} "
                            f"pending={pending} remaining={pending}",
                            flush=True,
                        )
                else:
                    worker_count = min(prompt_workers, len(prompt_jobs))
                    worker_batches: list[list[tuple[int, Path, str, str]]] = [[] for _ in range(worker_count)]
                    for job_number, job in enumerate(prompt_jobs):
                        worker_batches[job_number % worker_count].append(job)
                    future_map = {}
                    with ThreadPoolExecutor(max_workers=prompt_workers) as executor:
                        for worker_index, worker_jobs in enumerate(worker_batches, start=1):
                            if not worker_jobs:
                                continue
                            print(
                                f"[worker {worker_index}/{worker_count}] START prompts browser | stories={len(worker_jobs)} | "
                                "one story per dialog, browser reused between stories",
                                flush=True,
                            )
                            worker_root = (
                                ctx.launch_root
                                / "10_Временные_файлы"
                                / "visuals_gemini_batch"
                                / "prompts"
                                / f"worker_{worker_index}"
                            )
                            batch_stories = [
                                YoutubeGeminiBatchStory(
                                    story_id=story_id,
                                    story_dir=story_dir,
                                    stage_dir=worker_root / story_id,
                                )
                                for _index, story_dir, story_id, _title in worker_jobs
                            ]
                            future = executor.submit(
                                _run_prompts_worker_batch,
                                config=config,
                                launch_id=launch_id,
                                stories=batch_stories,
                                execute=bool(options.execute),
                                worker_index=worker_index,
                            )
                            future_map[future] = (worker_index, worker_jobs, time.monotonic())
                        completed = 0
                        pending_futures = set(future_map)
                        while pending_futures:
                            done_futures, pending_futures = wait(
                                pending_futures,
                                timeout=30,
                                return_when=FIRST_COMPLETED,
                            )
                            if not done_futures:
                                active = []
                                for running_future in pending_futures:
                                    worker_index, worker_jobs, worker_started = future_map[running_future]
                                    active.append(
                                        f"worker={worker_index}/{worker_count} stories={len(worker_jobs)} "
                                        f"elapsed={_fmt_elapsed(time.monotonic() - worker_started)}"
                                    )
                                print(
                                    "PROMPTS ACTIVE: "
                                    + " | ".join(active)
                                    + f" | done={prompt_progress['done']} failed={prompt_progress['failed']} "
                                    + f"remaining={len(prompt_jobs) - completed}",
                                    flush=True,
                                )
                                continue
                            for future in done_futures:
                                worker_index, worker_jobs, worker_started = future_map[future]
                                try:
                                    batch_result = future.result()
                                except Exception as exc:
                                    batch_result = {
                                        "ok": False,
                                        "status": "failed",
                                        "next_action": repr(exc),
                                        "blockers": [type(exc).__name__],
                                    }
                                elapsed = _fmt_elapsed(time.monotonic() - worker_started)
                                print(
                                    f"[worker {worker_index}/{worker_count}] DONE prompts browser | "
                                    f"status={batch_result.get('status')} | elapsed={elapsed}",
                                    flush=True,
                                )
                                for index, story_dir, story_id, title in worker_jobs:
                                    after = _story_visual_readiness(config, story_dir, story_id)
                                    if after["prompts_ready"]:
                                        prompt_progress["done"] += 1
                                        label = "DONE"
                                        ok = True
                                    else:
                                        prompt_progress["failed"] += 1
                                        label = "FAILED"
                                        ok = False
                                    print(
                                        f"[{index}/{active_queue_count}] {label} prompts: {story_id} / {title} | "
                                        f"characters={after['characters_status']} | prompts={after['prompts_status']} | elapsed={elapsed}",
                                        flush=True,
                                    )
                                    if label == "FAILED":
                                        print(
                                            f"[{index}/{active_queue_count}] FAILED reason={batch_result.get('next_action') or batch_result.get('status')}",
                                            flush=True,
                                        )
                                    results.append(
                                        {
                                            "stage": "prompts",
                                            "story_id": story_id,
                                            "title": title,
                                            "status": batch_result.get("status"),
                                            "ok": ok,
                                            "next_action": batch_result.get("next_action"),
                                            "blockers": batch_result.get("blockers") or batch_result.get("missing") or [],
                                            "story_dir": str(story_dir),
                                        }
                                    )
                                    completed += 1
                                    pending = len(prompt_jobs) - completed
                                    print(
                                        "PROMPTS PROGRESS: "
                                        f"done={prompt_progress['done']} failed={prompt_progress['failed']} blocked={prompt_progress['blocked']} "
                                        f"pending={pending} remaining={pending}",
                                        flush=True,
                                    )

            prompts_missing = []
            frames_queue = []
            for story_dir in selected:
                manifest = _load_manifest(story_dir)
                story_id, title = _story_identity(story_dir, manifest)
                readiness = _story_visual_readiness(config, story_dir, story_id)
                if not readiness["prompts_ready"]:
                    prompts_missing.append({"story_id": story_id, "title": title})
                else:
                    frames_queue.append((story_dir, story_id, title))

            if prompts_missing:
                print("================ PHASE 3: RUNPOD SKIPPED ================", flush=True)
                print(f"reason=prompts_not_ready missing_prompts={len(prompts_missing)}", flush=True)
                for row in prompts_missing:
                    print(f"- {row.get('story_id')} / {row.get('title')} -> missing prompts", flush=True)
            else:
                print("================ PHASE 3: RUNPOD / FRAMES ===============", flush=True)
                runpod_url = str(options.runpod_url or "").strip()
                if options.execute and not runpod_url and options.prompt_runpod_url and sys.stdin.isatty():
                    first_story_dir, first_story_id, _first_title = frames_queue[0]
                    first_prompts = _load_prompts(_prompts_path(config, first_story_id, first_story_dir))
                    first_frame_status = _frame_status(_frames_dir(config, first_story_id, first_story_dir), first_prompts)
                    runpod_url = _ask_runpod_url_at_checkpoint(story_id="ALL_STORIES", frame_status=first_frame_status)
                if options.execute and not runpod_url:
                    print("RUNPOD WAIT: --runpod-url is missing; prepared frame jobs only, no image generation.", flush=True)
                frame_progress = {"done": 0, "failed": 0, "blocked": 0, "skipped": 0}
                for index, (story_dir, story_id, title) in enumerate(frames_queue, start=1):
                    story_started = time.monotonic()
                    readiness = _story_visual_readiness(config, story_dir, story_id)
                    if readiness["images_ready"]:
                        frame_progress["done"] += 1
                        frame_progress["skipped"] += 1
                        run_result = {
                            "ok": True,
                            "status": "frames_already_ready",
                            "next_action": "already_has_images",
                            "story_dir": str(story_dir),
                            "blockers": [],
                        }
                        elapsed = _fmt_elapsed(time.monotonic() - story_started)
                        print(f"[{index}/{len(frames_queue)}] SKIP frames: {story_id} / {title} | reason=already_has_images | elapsed={elapsed}", flush=True)
                    else:
                        mode = "generate" if options.execute and runpod_url else "prepare_only"
                        print(f"[{index}/{len(frames_queue)}] START frames: {story_id} / {title} | mode={mode}", flush=True)
                        try:
                            run_result = run_youtube_frames_runpod_bridge(
                                config=config,
                                options=YoutubeFramesRunpodBridgeOptions(
                                    story_id=story_id,
                                    runpod_url=runpod_url,
                                    execute=bool(options.execute and runpod_url),
                                    prepare_only=not bool(options.execute and runpod_url),
                                    workflow=options.workflow,
                                ),
                            )
                        except Exception as exc:
                            run_result = {
                                "ok": False,
                                "status": "failed",
                                "next_action": repr(exc),
                                "story_dir": str(story_dir),
                                "blockers": [type(exc).__name__],
                            }
                        after = _story_visual_readiness(config, story_dir, story_id)
                        elapsed = _fmt_elapsed(time.monotonic() - story_started)
                        status_value = str(run_result.get("status") or "")
                        if after["images_ready"] or status_value == "prepared":
                            frame_progress["done"] += 1
                            label = "DONE" if after["images_ready"] else "PREPARED"
                        else:
                            frame_progress["failed"] += 1
                            label = "FAILED"
                        print(
                            f"[{index}/{len(frames_queue)}] {label} frames: {story_id} / {title} | "
                            f"images={after['images_status']} | elapsed={elapsed}",
                            flush=True,
                        )
                        if label == "FAILED":
                            print(f"[{index}/{len(frames_queue)}] FAILED reason={run_result.get('next_action') or run_result.get('status')}", flush=True)
                    results.append(
                        {
                            "stage": "frames",
                            "story_id": story_id,
                            "title": title,
                            "status": run_result.get("status"),
                            "ok": bool(run_result.get("ok")) or str(run_result.get("status") or "") == "prepared",
                            "next_action": run_result.get("next_action"),
                            "blockers": run_result.get("blockers") or run_result.get("missing") or [],
                            "story_dir": run_result.get("story_dir"),
                        }
                    )
                    pending = len(frames_queue) - index
                    print(
                        "FRAMES PROGRESS: "
                        f"done={frame_progress['done']} failed={frame_progress['failed']} blocked={frame_progress['blocked']} "
                        f"pending={pending} remaining={pending}",
                        flush=True,
                    )

    status = run_youtube_visuals_launch_status(
        config=config,
        options=YoutubeVisualsStatusOptions(
            youtube_run_id=launch_id,
            accept_known_promo_issues=bool(options.accept_known_promo_issues),
        ),
    )
    summary = status.get("summary") if isinstance(status.get("summary"), dict) else {}
    failed_rows = [
        row
        for row in results
        if not row.get("ok") and str(row.get("status") or "") not in {"ready_for_runpod", "blocked"}
    ]
    blocked_rows = [
        row
        for row in results
        if str(row.get("status") or "") in {"ready_for_runpod", "blocked"} and not row.get("ok")
    ]
    print("================ YOUTUBE VISUALS SUMMARY ================", flush=True)
    print(f"launch_id: {ctx.launch_id}", flush=True)
    print(f"total stories:        {len(_iter_launch_story_dirs(config, launch_id))}", flush=True)
    print(f"excluded_from_video:  {sum(1 for row in skipped if row.get('reason') == 'excluded_from_video')}", flush=True)
    print(f"active queue:         {active_queue_count}", flush=True)
    print(f"done:                 {sum(1 for row in results if row.get('ok'))}", flush=True)
    print(f"failed:               {len(failed_rows)}", flush=True)
    print(f"blocked:              {len(blocked_rows)}", flush=True)
    print(f"pending:              {summary.get('pending', 0)}", flush=True)
    print(f"ready_for_frames:     {summary.get('ready_for_frames', 0)}", flush=True)
    print("excluded:", flush=True)
    excluded_rows = [row for row in skipped if row.get("reason") == "excluded_from_video"]
    if excluded_rows:
        for row in excluded_rows:
            print(f"- {row.get('story_id')} -> {row.get('exclude_reason')}", flush=True)
    else:
        print("- none", flush=True)
    print("failed:", flush=True)
    if failed_rows:
        for row in failed_rows:
            print(f"- {row.get('story_id')} -> {row.get('next_action')}", flush=True)
    else:
        print("- none", flush=True)
    print(f"next_stage_allowed: {str(bool(summary.get('ready_for_frames') or summary.get('images_ready'))).lower()}", flush=True)
    print("==========================================================", flush=True)
    payload = {
        "ok": all(
            bool(row.get("ok"))
            or row.get("status") in {"ready_for_runpod", "blocked", "dry_run", "already_ready"}
            for row in results
        )
        if results
        else True,
        "execute": bool(options.execute),
        "launch_id": ctx.launch_id,
        "selected_count": len(selected),
        "processed_count": len(results),
        "skipped_count": len(skipped),
        "promo_issues_accepted": bool(options.accept_known_promo_issues),
        "stage_status_path": str(ctx.launch_root / "queue" / "stage_status.json"),
        "skipped": skipped,
        "stories": results,
        "status": status,
        "elapsed_sec": round(time.monotonic() - started_at, 3),
        "generated_at": _now_iso(),
    }
    report_path = ctx.launch_root / "07_reports" / "YOUTUBE_VISUALS_RUN_ALL.json"
    _write_json(report_path, payload)
    payload["report_path"] = str(report_path)
    return payload


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
    from orchestrator.isolated_launch_context import get_batch_launch_id, isolated_session

    started_at = _now_iso()
    story_id = str(options.story_id).strip()
    launch_id = str(options.youtube_run_id or get_batch_launch_id() or "").strip()
    active_launch_id = str(get_batch_launch_id() or "").strip()
    if launch_id and active_launch_id != launch_id:
        with isolated_session(None, batch_launch_id=launch_id, config=config):
            return run_youtube_visuals_run(config=config, options=options)
    story_dir = _story_dir(config, story_id, youtube_run_id=launch_id)
    changed_files: list[str] = []
    stages: list[dict[str, Any]] = []
    blockers: list[str] = []
    errors: list[str] = []
    mode = "watch" if options.watch else ("execute" if options.execute else "dry_run")
    auto_gemini_enabled = bool(options.auto_gemini or options.allow_gemini)

    def finish(status: str, next_action: str) -> dict[str, Any]:
        status_report = run_youtube_visuals_status(
            config=config,
            options=YoutubeVisualsStatusOptions(story_id=story_id, youtube_run_id=launch_id),
        )
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
    manifest = _load_manifest(story_dir)
    if _is_excluded_from_video(manifest):
        stages.append(
            _stage_row(
                "preflight",
                "excluded",
                "story is intentionally excluded from visuals/video queue",
                exclude_reason=str(manifest.get("exclude_reason") or ""),
            )
        )
        blockers.append("excluded_from_video")
        return finish("excluded", "story is intentionally dropped from video queue")
    promo_issues_accepted = _launch_policy_accepts_promo_issues(
        manifest,
        explicit=bool(options.accept_known_promo_issues),
    )

    missing_preflight: list[str] = []
    if not story_dir.is_dir():
        missing_preflight.append(str(story_dir))
    if not _source_text_path(story_dir).is_file():
        missing_preflight.append(str(_source_text_path(story_dir)))
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

    promo_status = run_youtube_promo_status(
        config=config,
        options=YoutubePromoStatusOptions(story_id=story_id, youtube_run_id=launch_id),
    )
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
    elif promo_issues_accepted:
        stages.append(
            _stage_row(
                "promo_check",
                "accepted_with_known_issues",
                "current launch is explicitly accepted with known promo issues",
                status_report=promo_status,
                promo_issues_accepted=True,
            )
        )
    else:
        if options.execute:
            promo_result = run_youtube_promo_run(
                config=config,
                options=YoutubePromoRunOptions(
                    story_id=story_id,
                    execute=True,
                    fresh_gemini_session=True,
                    account_index=0,
                    youtube_run_id=launch_id,
                ),
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
            promo_status = run_youtube_promo_status(
                config=config,
                options=YoutubePromoStatusOptions(story_id=story_id, youtube_run_id=launch_id),
            )
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
    if promo_audio.get("stale") and not promo_issues_accepted:
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
    stages.append(
        _stage_row(
            "audio_check",
            "done",
            "narration.mp3 ready for accepted launch policy" if promo_issues_accepted else "narration.mp3 matches current promo text",
            path=str(narration),
            promo_issues_accepted=promo_issues_accepted,
        )
    )

    characters = _characters_path(config, story_id, story_dir)
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
                staging_expected_file = _expected_output_path_file(_characters_staging_dir(config, story_id, story_dir))
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
                        staging_path=str(_characters_staging_dir(config, story_id, story_dir)),
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

    prompts_path = _prompts_path(config, story_id, story_dir)
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
                narration_info = _prompts_staging_dir(config, story_id, story_dir) / "narration_info.json"
                _write_json(
                    narration_info,
                    {
                        "audio_path": str(narration),
                        "duration_sec": _duration_sec(narration),
                        "estimated_prompts": _prompt_estimate(story_dir),
                    },
                )
                changed_files.append(str(narration_info))
                staging_expected_file = _expected_output_path_file(_prompts_staging_dir(config, story_id, story_dir))
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
                        staging_path=str(_prompts_staging_dir(config, story_id, story_dir)),
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

    frames_status = _frame_status(_frames_dir(config, story_id, story_dir), prompts)

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
