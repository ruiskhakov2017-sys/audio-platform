"""Single-story YouTube visuals state machine.

This runner orchestrates local bridge steps only. It does not launch Gemini
unless a future explicit adapter is added, and it only calls RunPod/ComfyUI via
the existing frames bridge when both --execute and --runpod-url are provided.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.youtube_prompts_failure_reasons import (
    PROMPTS_GENERATION_INCOMPLETE,
    classify_stage_prompts_failure,
    normalize_failure_reason,
)
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
from orchestrator.youtube_gemini_registry import resolve_youtube_gemini_bots, sync_youtube_gemini_legacy_files


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
    workers: int = 3
    limit: int = 0
    execute: bool = False
    auto_gemini: bool = False
    allow_gemini: bool = False
    accept_known_promo_issues: bool = False
    segment_sec: float = 180.0
    prompt_runpod_url: bool = False
    prompts_only: bool = False


@dataclass
class YoutubeStageSetOptions:
    youtube_run_id: str
    stage: str
    execute: bool = False


@dataclass
class YoutubeGeminiWorkersOptions:
    workers: int = 3
    execute: bool = False


@dataclass
class YoutubeGeminiPreflightAccountsOptions:
    stage: str = "visuals"
    youtube_run_id: str = ""
    accounts: str = "0,1,2"
    execute: bool = False


@dataclass
class YoutubePromptsResumeAuditOptions:
    youtube_run_id: str
    accept_known_promo_issues: bool = False


@dataclass
class YoutubePromptsProgressStatusOptions:
    youtube_run_id: str
    run_session_id: str = ""
    accept_known_promo_issues: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminal_safe(value: Any) -> str:
    text = str(value or "")
    normalized = (
        text.replace("\u2192", "->")
        .replace("\u2190", "<-")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2026", "...")
    )
    return normalized.encode("ascii", errors="backslashreplace").decode("ascii")


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


def _update_manifest_dict(story_dir: Path, patch: dict[str, Any]) -> None:
    manifest_path = _story_manifest_path(story_dir)
    manifest = _load_manifest(story_dir)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(manifest.get(key), dict):
            manifest[key].update(value)
        else:
            manifest[key] = value
    manifest["updated_at"] = _now_iso()
    _write_json(manifest_path, manifest)


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
    manifest = _load_manifest(story_dir)
    characters = _characters_path(config, story_id, story_dir)
    prompts_path = _prompts_path(config, story_id, story_dir)
    prompts = _load_prompts(prompts_path)
    prompts_validation = validate_visual_prompts_file(prompts_path)
    visual_prompts_meta = manifest.get("visual_prompts") if isinstance(manifest.get("visual_prompts"), dict) else {}
    frames = _frame_status(_frames_dir(config, story_id, story_dir), prompts)
    characters_ready = _is_nonempty(characters)
    manifest_prompts_done = (
        str(visual_prompts_meta.get("status") or "").strip() == "done"
        and str(visual_prompts_meta.get("validation") or "").strip() == "ok"
    )
    expected_prompts = visual_prompts_meta.get("expected_prompts") or _prompt_estimate(story_dir)
    actual_prompts = int(prompts_validation.get("prompts_count") or len(prompts) or 0)
    prompt_count_matches = True
    prompts_file_exists = _is_nonempty(prompts_path)
    if prompts_file_exists and expected_prompts is not None:
        try:
            prompt_count_matches = int(expected_prompts) == actual_prompts
        except (TypeError, ValueError):
            prompt_count_matches = False
    legacy_prompts_done = (
        not visual_prompts_meta
        and str((manifest.get("director_prompts") or {}).get("status") if isinstance(manifest.get("director_prompts"), dict) else "").strip() == "done"
    )
    prompts_ready = bool(prompts_file_exists and prompts_validation.get("ok") and prompt_count_matches and (manifest_prompts_done or legacy_prompts_done))
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
        "prompts_status": "ok" if prompts_ready else ("count_mismatch" if not prompt_count_matches else str(prompts_validation.get("status") or "missing")),
        "prompts_validation": prompts_validation,
        "visual_prompts": visual_prompts_meta,
        "images_status": "ok" if images_ready else "skip",
    }


def _director_module_dir_for_visuals(config: OrchestratorConfig) -> Path:
    director_dir = (config.root_dir / config.legacy_modules.get("director_2_0", "legacy/director_2_0")).resolve()
    return director_dir


def _prompt_worker_profile_dir(config: OrchestratorConfig, worker_index: int) -> Path:
    director_dir = _director_module_dir_for_visuals(config)
    return director_dir / "worker_profiles" / f"prompts_worker_{worker_index}" / "user_data"


def _scan_worker_config_candidates(config: OrchestratorConfig) -> list[dict[str, Any]]:
    candidates = [
        config.root_dir / "configs" / "gemini_bots_registry.yaml",
        config.root_dir / "configs" / "gemini_bots_registry.example.yaml",
        _director_module_dir_for_visuals(config) / "gemini_bots.json",
        config.root_dir / "configs" / "youtube_video_colab_workers.yaml",
        config.root_dir / "configs" / "youtube_tts_colab_workers.yaml",
        config.root_dir / "configs" / "youtube_video_render.yaml",
    ]
    selected = str((config.root_dir / "configs" / "gemini_bots_registry.yaml").resolve())
    rows: list[dict[str, Any]] = []
    for path in candidates:
        resolved = path.resolve()
        if not resolved.is_file():
            continue
        text = resolved.read_text(encoding="utf-8", errors="replace")
        contains_emails = "@" in text or "gmail.com" in text
        contains_bots = any(token in text.lower() for token in ("gemini", "aistudio", "makersuite", "bot"))
        contains_worker_mapping = "worker" in text.lower() or "account_index" in text.lower()
        rows.append(
            {
                "path": str(resolved),
                "contains_emails": contains_emails,
                "contains_bots": contains_bots,
                "contains_worker_mapping": contains_worker_mapping,
                "selected_as_source_of_truth": str(resolved) == selected,
            }
        )
    return rows


def _prompt_worker_mappings(config: OrchestratorConfig, worker_count: int) -> tuple[str, list[dict[str, Any]], list[str]]:
    resolved = resolve_youtube_gemini_bots(config)
    source = str(resolved.get("registry_path") or (config.root_dir / "configs" / "gemini_bots_registry.yaml"))
    chain = resolved.get("selected_director_chain") if isinstance(resolved.get("selected_director_chain"), list) else []
    blockers = [str(item) for item in (resolved.get("warnings") or []) if item]
    by_email: dict[str, dict[str, Any]] = {}
    for item in chain:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip().lower()
        if email:
            by_email[email] = item
    mappings: list[dict[str, Any]] = []
    for worker_index in range(1, worker_count + 1):
        worker_root = _prompt_worker_profile_dir(config, worker_index).parent
        profile_dir = worker_root / "user_data"
        account_marker = worker_root / ".account_email"
        marker_email = account_marker.read_text(encoding="utf-8", errors="replace").strip() if account_marker.is_file() else ""
        profile_email = ""
        try:
            from orchestrator.phase_a import _read_profile_email

            profile_email = _read_profile_email(profile_dir).strip() if profile_dir.is_dir() else ""
        except Exception:
            profile_email = ""
        runtime_email = (profile_email or marker_email).strip().lower()
        resolved_item = by_email.get(runtime_email, {})
        resolved_email = str(resolved_item.get("email") or "").strip()
        resolved_url = str(resolved_item.get("gem_url") or resolved_item.get("url") or "").strip()
        mapping_ok = bool(runtime_email and resolved_item and resolved_email and resolved_url)
        if not runtime_email:
            blockers.append(f"worker_{worker_index} identity unknown: missing profile/marker email")
        elif not resolved_item:
            blockers.append(f"worker_{worker_index} no registry director bot for email={runtime_email}")
        mappings.append(
            {
                "worker_id": worker_index,
                "profile_dir": str(profile_dir),
                "worker_root": str(worker_root),
                "actual_email_marker": marker_email,
                "actual_email_profile": profile_email,
                "runtime_email": runtime_email,
                "resolved_registry_email": resolved_email,
                "bot_url": resolved_url,
                "bot_name": "youtube_scene_prompts",
                "account_index": resolved_item.get("account_index"),
                "source_of_truth": source,
                "mapping_ok": mapping_ok,
            }
        )
    return source, mappings, blockers


def _worker_status_row(mapping: dict[str, Any]) -> dict[str, Any]:
    worker_root = Path(str(mapping.get("worker_root") or ""))
    profile_dir = Path(str(mapping.get("profile_dir") or ""))
    account_marker = worker_root / ".account_email"
    mapping_path = worker_root / "worker_mapping.json"
    cloned_marker = worker_root / ".orchestrator_clone_from_base"
    actual_email = account_marker.read_text(encoding="utf-8", errors="replace").strip() if account_marker.is_file() else ""
    prefs_email = str(mapping.get("actual_email_profile") or "").strip()
    resolved_email = str(mapping.get("resolved_registry_email") or "").strip()
    blockers: list[str] = []
    if cloned_marker.exists() and resolved_email and actual_email.lower() != resolved_email.lower():
        blockers.append("profile is cloned from base and has no matching unique identity")
    if not profile_dir.is_dir():
        blockers.append("profile directory missing; run gemini-workers-setup --execute, then login if browser asks")
    if not actual_email:
        blockers.append("actual email marker missing; run gemini-workers-setup --execute")
    if prefs_email and actual_email and prefs_email.lower() != actual_email.lower():
        blockers.append(f"profile/marker email mismatch: profile={prefs_email} marker={actual_email}")
    elif profile_dir.is_dir() and not prefs_email:
        blockers.append("chrome profile email unknown; login required in worker profile")
    if not resolved_email:
        blockers.append("registry mapping missing for worker runtime email")
    if not str(mapping.get("bot_url") or "").strip():
        blockers.append("bot URL missing")
    if not bool(mapping.get("mapping_ok")):
        blockers.append("worker mapping is not consistent")
    return {
        **mapping,
        "actual_email_marker": actual_email,
        "chrome_profile_email": prefs_email,
        "mapping_path": str(mapping_path),
        "cloned_profile": cloned_marker.exists(),
        "ready": not blockers,
        "blocker": "none" if not blockers else "; ".join(blockers),
        "blockers": blockers,
    }


def run_youtube_gemini_workers_status(config: OrchestratorConfig, options: YoutubeGeminiWorkersOptions) -> dict[str, Any]:
    workers = max(1, int(options.workers or 1))
    source, mappings, blockers = _prompt_worker_mappings(config, workers)
    rows = [_worker_status_row(mapping) for mapping in mappings]
    all_blockers = list(blockers)
    for row in rows:
        all_blockers.extend(row.get("blockers") or [])
    return {
        "ok": not all_blockers,
        "ready": not all_blockers,
        "workers": workers,
        "source_of_truth": source,
        "found_worker_configs": _scan_worker_config_candidates(config),
        "rows": rows,
        "blockers": all_blockers,
    }


def run_youtube_gemini_workers_setup(config: OrchestratorConfig, options: YoutubeGeminiWorkersOptions) -> dict[str, Any]:
    workers = max(1, int(options.workers or 1))
    source, mappings, blockers = _prompt_worker_mappings(config, workers)
    changed: list[str] = []
    for mapping in mappings:
        worker_root = Path(str(mapping["worker_root"]))
        profile_dir = Path(str(mapping["profile_dir"]))
        account_marker = worker_root / ".account_email"
        mapping_path = worker_root / "worker_mapping.json"
        if options.execute:
            profile_dir.mkdir(parents=True, exist_ok=True)
            account_marker.write_text(str(mapping.get("expected_email") or "") + "\n", encoding="utf-8")
            mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed.extend([str(account_marker), str(mapping_path)])
    status = run_youtube_gemini_workers_status(config, YoutubeGeminiWorkersOptions(workers=workers, execute=False))
    return {
        **status,
        "ok": not blockers and (status.get("ready") if options.execute else True),
        "execute": bool(options.execute),
        "source_of_truth": source,
        "changed_files": changed,
        "setup_blockers": blockers,
    }


def _parse_worker_indexes_from_accounts(accounts: str) -> list[int]:
    out: list[int] = []
    for raw in str(accounts or "").split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token.startswith("w"):
            token = token[1:]
            if token.isdigit():
                out.append(max(1, int(token)))
            continue
        if token.isdigit():
            out.append(max(1, int(token) + 1))
    return sorted(set(out)) or [1, 2, 3]


def _gem_id_from_url(url: str) -> str:
    text = str(url or "").strip()
    if "/gem/" not in text:
        return ""
    return text.split("/gem/", 1)[-1].split("/", 1)[0].strip()


def _offline_reason(page_text: str, final_url: str, browser_error: str) -> str:
    low = f"{page_text}\n{final_url}\n{browser_error}".lower()
    if "proxy authentication required" in low or " 407 " in low:
        return "PROXY_AUTH_REQUIRED"
    if "err_proxy_connection_failed" in low or "proxy connection failed" in low:
        return "PROXY_CONNECTION_FAILED"
    if "err_name_not_resolved" in low or "dns_probe_finished" in low:
        return "DNS_FAILED"
    if "нет соединения с интернетом" in low or "no internet" in low or "err_internet_disconnected" in low:
        return "BROWSER_NETWORK_OFFLINE"
    if "err_connection_timed_out" in low or "timed out" in low:
        return "GEMINI_LOAD_TIMEOUT"
    return ""


def _is_gemini_editor_available(page: Any) -> bool:
    selectors = ("textarea", "div[contenteditable='true']", "[role='textbox']", "rich-textarea")
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _proxy_required_for_preflight() -> bool:
    value = str(os.getenv("GEMINI_PROXY_REQUIRED") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _bootstrap_preflight_proxy(config: OrchestratorConfig) -> dict[str, Any]:
    existing_server = str(os.getenv("GEMINI_PROXY_SERVER") or "").strip()
    required = _proxy_required_for_preflight()
    if not required:
        return {
            "proxy_required": False,
            "proxy_server": existing_server,
            "proxy_source": "env" if existing_server else "unavailable",
            "bridge_started": False,
            "proxy_error": "",
            "proxy_session": None,
        }
    if existing_server:
        return {
            "proxy_required": True,
            "proxy_server": existing_server,
            "proxy_source": "env",
            "bridge_started": False,
            "proxy_error": "",
            "proxy_session": None,
        }
    try:
        from orchestrator.gemini_colab_proxy import GeminiColabProxySession

        session = GeminiColabProxySession(config.root_dir.resolve()).start()
        server = str(session.proxy_server or "").strip()
        if not server:
            session.stop()
            return {
                "proxy_required": True,
                "proxy_server": "",
                "proxy_source": "unavailable",
                "bridge_started": False,
                "proxy_error": "PROXY_BOOTSTRAP_FAILED: bridge started but proxy_server is empty",
                "proxy_session": None,
            }
        os.environ["GEMINI_PROXY_SERVER"] = server
        os.environ["GEMINI_PROXY_REQUIRED"] = "1"
        return {
            "proxy_required": True,
            "proxy_server": server,
            "proxy_source": "existing_bridge",
            "bridge_started": True,
            "proxy_error": "",
            "proxy_session": session,
        }
    except Exception as exc:
        return {
            "proxy_required": True,
            "proxy_server": "",
            "proxy_source": "unavailable",
            "bridge_started": False,
            "proxy_error": f"PROXY_BOOTSTRAP_FAILED: {exc!r}",
            "proxy_session": None,
        }


def _run_worker_browser_preflight(
    *,
    worker_row: dict[str, Any],
    worker_artifacts_dir: Path,
    proxy_server: str,
    proxy_source: str,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy"))
    from gemini_browser_proxy import append_chrome_proxy_args

    profile_dir = Path(str(worker_row.get("profile_dir") or ""))
    expected_email = str(worker_row.get("resolved_registry_email") or "").strip().lower()
    bot_url = str(worker_row.get("bot_url") or "").strip()
    expected_gem_id = _gem_id_from_url(bot_url)
    worker_artifacts_dir.mkdir(parents=True, exist_ok=True)
    nav_rows: list[dict[str, Any]] = []
    console_errors: list[str] = []
    browser_error = ""

    def _navigate(page: Any, label: str, url: str) -> dict[str, Any]:
        item = {
            "label": label,
            "requested_url": url,
            "final_url": "",
            "actual_gem_id": "",
            "internet_ok": False,
            "gemini_ok": False,
            "bot_ok": False,
            "offline_reason": "",
            "screenshot": "",
            "html": "",
            "error": "",
        }
        screenshot_path = worker_artifacts_dir / f"{label}.png"
        html_path = worker_artifacts_dir / f"{label}.html"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(1200)
            final_url = str(page.url or "")
            body_text = page.locator("body").inner_text(timeout=5000)
            html = page.content()
            page.screenshot(path=str(screenshot_path), full_page=True)
            html_path.write_text(html, encoding="utf-8")
            reason = _offline_reason(body_text + "\n" + html, final_url, browser_error)
            item.update(
                {
                    "final_url": final_url,
                    "actual_gem_id": _gem_id_from_url(final_url),
                    "internet_ok": not reason,
                    "gemini_ok": ("gemini.google.com" in final_url.lower() and not reason and _is_gemini_editor_available(page)),
                    "offline_reason": reason,
                    "screenshot": str(screenshot_path),
                    "html": str(html_path),
                }
            )
            if label == "bot":
                item["bot_ok"] = bool(expected_gem_id and item["actual_gem_id"] and item["actual_gem_id"].lower() == expected_gem_id.lower() and not reason)
        except Exception as exc:
            item["error"] = repr(exc)
            item["offline_reason"] = _offline_reason("", "", repr(exc)) or "PROXY_OR_NETWORK_ERROR"
        return item

    identity_ok = bool(str(worker_row.get("runtime_email") or "").strip().lower() == expected_email and expected_email)
    if not profile_dir.is_dir():
        return {
            **worker_row,
            "actual_email": str(worker_row.get("runtime_email") or "").strip().lower(),
            "proxy_server": proxy_server,
            "proxy_source": proxy_source,
            "actual_url": "",
            "identity_ok": False,
            "internet_ok": False,
            "gemini_ok": False,
            "bot_ok": False,
            "result": "FAIL",
            "issue": "PROFILE_EMPTY_OR_WRONG",
            "screenshot": "",
            "html": "",
            "browser_error": "profile_dir_missing",
        }

    context = None
    try:
        with sync_playwright() as pw:
            launch_kwargs = {
                "user_data_dir": str(profile_dir),
                "headless": False,
                "viewport": None,
                "args": append_chrome_proxy_args(["--disable-blink-features=AutomationControlled"]),
            }
            for channel in ("chrome", "msedge", None):
                try:
                    context = (
                        pw.chromium.launch_persistent_context(channel=channel, **launch_kwargs)
                        if channel is not None
                        else pw.chromium.launch_persistent_context(**launch_kwargs)
                    )
                    break
                except Exception as exc:
                    browser_error = repr(exc)
                    context = None
            if context is None:
                raise RuntimeError(browser_error or "browser_launch_failed")
            page = context.pages[0] if context.pages else context.new_page()
            page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type in {"error", "warning"} else None)
            nav_rows.append(_navigate(page, "google", "https://www.google.com"))
            nav_rows.append(_navigate(page, "gemini", "https://gemini.google.com"))
            nav_rows.append(_navigate(page, "bot", bot_url))
    except Exception as exc:
        browser_error = repr(exc)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

    google = next((row for row in nav_rows if row.get("label") == "google"), {})
    gemini = next((row for row in nav_rows if row.get("label") == "gemini"), {})
    bot = next((row for row in nav_rows if row.get("label") == "bot"), {})
    internet_ok = bool(google.get("internet_ok") and gemini.get("internet_ok") and bot.get("internet_ok"))
    gemini_ok = bool(gemini.get("gemini_ok"))
    bot_ok = bool(bot.get("bot_ok"))
    issue = (
        str(bot.get("offline_reason") or gemini.get("offline_reason") or google.get("offline_reason") or "")
        or ("WRONG_GEMINI_BOT" if not bot_ok else "")
        or ("WRONG_GOOGLE_ACCOUNT" if not identity_ok else "")
        or ("PROXY_OR_NETWORK_ERROR" if browser_error else "")
    )
    result = "PASS" if (identity_ok and bot_ok and internet_ok and gemini_ok and not issue) else "FAIL"
    return {
        **worker_row,
        "actual_email": str(worker_row.get("runtime_email") or "").strip().lower(),
        "proxy_server": proxy_server,
        "proxy_source": proxy_source,
        "actual_url": str(bot.get("final_url") or ""),
        "identity_ok": identity_ok,
        "internet_ok": internet_ok,
        "gemini_ok": gemini_ok,
        "bot_ok": bot_ok,
        "result": result,
        "issue": issue,
        "screenshot": str(bot.get("screenshot") or gemini.get("screenshot") or google.get("screenshot") or ""),
        "html": str(bot.get("html") or gemini.get("html") or google.get("html") or ""),
        "browser_error": browser_error,
        "console_errors": console_errors[-20:],
    }


def run_youtube_gemini_preflight_accounts(config: OrchestratorConfig, options: YoutubeGeminiPreflightAccountsOptions) -> dict[str, Any]:
    worker_indexes = _parse_worker_indexes_from_accounts(options.accounts)
    source, mappings, blockers = _prompt_worker_mappings(config, max(worker_indexes))
    by_worker = {int(row.get("worker_id")): row for row in mappings}
    selected_rows = [by_worker[index] for index in worker_indexes if index in by_worker]
    reports_root = (config.root_dir / "reports" / "gemini_execution").resolve()
    preflight_root = reports_root / "worker_preflight" / datetime.now().strftime("%Y%m%d_%H%M%S")
    rows: list[dict[str, Any]] = []
    proxy_bootstrap = _bootstrap_preflight_proxy(config)
    proxy_required = bool(proxy_bootstrap.get("proxy_required"))
    proxy_server = str(proxy_bootstrap.get("proxy_server") or "").strip()
    proxy_source = str(proxy_bootstrap.get("proxy_source") or "unavailable")
    proxy_error = str(proxy_bootstrap.get("proxy_error") or "")
    bootstrap_failed = bool(proxy_required and not proxy_server)
    for row in selected_rows:
        if options.execute:
            worker_dir = preflight_root / f"worker_{int(row.get('worker_id') or 0)}"
            if bootstrap_failed:
                rows.append(
                    {
                        **row,
                        "actual_email": str(row.get("runtime_email") or "").strip().lower(),
                        "proxy_server": proxy_server,
                        "proxy_source": proxy_source,
                        "actual_url": "",
                        "identity_ok": bool(row.get("mapping_ok")),
                        "internet_ok": False,
                        "gemini_ok": False,
                        "bot_ok": False,
                        "result": "FAIL",
                        "issue": "PROXY_BOOTSTRAP_FAILED",
                        "screenshot": "",
                        "html": "",
                        "browser_error": proxy_error,
                    }
                )
            else:
                rows.append(
                    _run_worker_browser_preflight(
                        worker_row=row,
                        worker_artifacts_dir=worker_dir,
                        proxy_server=proxy_server,
                        proxy_source=proxy_source,
                    )
                )
        else:
            rows.append(
                {
                    **row,
                    "actual_email": str(row.get("runtime_email") or "").strip().lower(),
                    "proxy_server": proxy_server,
                    "proxy_source": proxy_source,
                    "actual_url": "",
                    "identity_ok": bool(row.get("mapping_ok")),
                    "internet_ok": False,
                    "gemini_ok": False,
                    "bot_ok": bool(row.get("mapping_ok")),
                    "result": "DRY_RUN",
                    "issue": "" if row.get("mapping_ok") else "MAPPING_INCONSISTENT",
                    "screenshot": "",
                    "html": "",
                    "browser_error": "",
                }
            )
    ok = all(bool(row.get("identity_ok")) and bool(row.get("bot_ok")) and bool(row.get("internet_ok")) and bool(row.get("gemini_ok")) for row in rows) if options.execute else all(bool(row.get("mapping_ok")) for row in rows)
    payload = {
        "ok": ok and not blockers,
        "stage": str(options.stage or "visuals"),
        "youtube_run_id": str(options.youtube_run_id or ""),
        "execute": bool(options.execute),
        "accounts": str(options.accounts or ""),
        "workers": worker_indexes,
        "source_of_truth": source,
        "proxy_required": proxy_required,
        "proxy_server": proxy_server,
        "proxy_source": proxy_source,
        "bridge_started": bool(proxy_bootstrap.get("bridge_started")),
        "proxy_error": proxy_error,
        "blockers": blockers,
        "rows": rows,
        "reports_root": str(preflight_root) if options.execute else "",
    }
    reports_root.mkdir(parents=True, exist_ok=True)
    report_json = reports_root / "GEMINI_IDENTITY_NETWORK_PREFLIGHT.json"
    report_md = reports_root / "GEMINI_IDENTITY_NETWORK_PREFLIGHT.md"
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Gemini worker preflight",
        "",
        f"stage: {payload['stage']}",
        f"execute: {str(payload['execute']).lower()}",
        f"source_of_truth: {payload['source_of_truth']}",
        f"proxy_required: {str(bool(payload.get('proxy_required'))).lower()}",
        f"proxy_server: {payload.get('proxy_server')}",
        f"proxy_source: {payload.get('proxy_source')}",
        f"bridge_started: {str(bool(payload.get('bridge_started'))).lower()}",
        f"proxy_error: {payload.get('proxy_error')}",
        "",
        "worker | profile_dir | actual_email | resolved_registry_email | proxy_server | proxy_source | actual_url | bot_ok | internet_ok | gemini_ok | screenshot | result",
    ]
    for row in rows:
        lines.append(
            f"{row.get('worker_id')} | {row.get('profile_dir')} | {row.get('actual_email')} | "
            f"{row.get('resolved_registry_email')} | {row.get('proxy_server')} | {row.get('proxy_source')} | "
            f"{row.get('actual_url')} | {row.get('bot_ok')} | "
            f"{row.get('internet_ok')} | {row.get('gemini_ok')} | {row.get('screenshot')} | {row.get('result')}"
        )
    if blockers:
        lines.extend(["", "## Blockers", ""] + [f"- {item}" for item in blockers])
    report_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    payload["report_json"] = str(report_json)
    payload["report_md"] = str(report_md)
    session = proxy_bootstrap.get("proxy_session")
    if session is not None:
        try:
            session.stop()
        except Exception:
            pass
    return payload


def _print_gemini_workers_preflight(result: dict[str, Any]) -> None:
    print("================ GEMINI WORKERS PREFLIGHT ================", flush=True)
    print(f"workers: {result.get('workers')}", flush=True)
    print(f"source_of_truth: {result.get('source_of_truth')}", flush=True)
    for row in result.get("rows") or []:
        print(f"worker_{row.get('worker_id')}:", flush=True)
        print(f"  expected_email: {row.get('resolved_registry_email')}", flush=True)
        print(f"  actual_email_marker: {row.get('actual_email_marker')}", flush=True)
        print(f"  bot_url: {row.get('bot_url')}", flush=True)
        print(f"  profile: {row.get('profile_dir')}", flush=True)
        print(f"  cloned_profile: {str(bool(row.get('cloned_profile'))).lower()}", flush=True)
        print(f"  status: {'OK' if row.get('ready') else 'BLOCKED'}", flush=True)
        if not row.get("ready"):
            print(f"  blocker: {row.get('blocker')}", flush=True)
    print(f"GEMINI_WORKERS_READY = {str(bool(result.get('ready'))).lower()}", flush=True)
    blockers = result.get("blockers") or []
    if blockers:
        print("BLOCKERS:", flush=True)
        for blocker in blockers:
            print(f"- {blocker}", flush=True)
    print("===========================================================", flush=True)


def _prepare_prompt_worker_profiles(config: OrchestratorConfig, worker_count: int) -> None:
    status = run_youtube_gemini_workers_status(config, YoutubeGeminiWorkersOptions(workers=worker_count))
    _print_gemini_workers_preflight(status)
    if not status.get("ready"):
        raise RuntimeError("Gemini workers preflight failed")


def _mapping_for_worker(config: OrchestratorConfig, worker_index: int) -> dict[str, Any]:
    _source, mappings, _blockers = _prompt_worker_mappings(config, worker_index)
    return mappings[worker_index - 1]


def _load_prompt_worker_bot(config: OrchestratorConfig, worker_index: int) -> dict[str, str]:
    mapping = _mapping_for_worker(config, worker_index)
    return {
        "url": str(mapping.get("bot_url") or ""),
        "email": str(mapping.get("resolved_registry_email") or mapping.get("runtime_email") or ""),
    }


def _prompt_worker_user_data_dir(config: OrchestratorConfig, worker_index: int) -> str:
    return str(_prompt_worker_profile_dir(config, worker_index))


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

    user_data_dir = _prompt_worker_user_data_dir(config, worker_index) if execute else ""
    bot = _load_prompt_worker_bot(config, worker_index) if execute else {"url": "", "email": ""}
    if execute:
        print(
            f"[worker {worker_index}] prompts user_data_dir={user_data_dir} account={bot.get('email') or 'unknown'} url={bot.get('url')}",
            flush=True,
        )
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        return run_youtube_director_prompts_batch_auto_gemini(
            config=config,
            options=YoutubeGeminiBatchOptions(
                stories=stories,
                execute=execute,
                user_data_dir=user_data_dir,
                worker_label=f"worker_{worker_index}",
                start_url=bot.get("url", ""),
                account_email=bot.get("email", ""),
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


def _recover_temp_prompts_output(
    *,
    config: OrchestratorConfig,
    ctx: Any,
    story_id: str,
    story_dir: Path,
    execute: bool,
) -> dict[str, Any]:
    temp_root = ctx.launch_root / "10_Временные_файлы" / "visuals_gemini_batch" / "prompts"
    if not execute or not temp_root.is_dir():
        return {"ok": False, "status": "not_attempted"}
    candidates = [
        path
        for path in temp_root.rglob("prompts_list.txt")
        if path.is_file() and path.parent.name.casefold() == story_id.casefold()
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    attempts: list[dict[str, Any]] = []
    for candidate in candidates[:5]:
        validation = validate_visual_prompts_file(candidate)
        attempts.append({"path": str(candidate), "validation": validation})
        if not validation.get("ok", False):
            continue
        imp = run_youtube_director_prompts_import(
            config=config,
            options=YoutubeDirectorPromptsImportOptions(story_id=story_id, source=candidate, execute=True),
        )
        if imp.get("ok"):
            return {"ok": True, "status": "imported_temp_prompts", "source": str(candidate), "import": imp, "attempts": attempts}
    return {"ok": False, "status": "no_valid_temp_prompts", "attempts": attempts}


def _visual_prompts_status_row(config: OrchestratorConfig, story_dir: Path, *, accept_known_promo_issues: bool = False) -> dict[str, Any]:
    manifest = _load_manifest(story_dir)
    story_id, title = _story_identity(story_dir, manifest)
    excluded = _is_excluded_from_video(manifest)
    characters = _characters_path(config, story_id, story_dir)
    prompts_path = _prompts_path(config, story_id, story_dir)
    prompts = _load_prompts(prompts_path)
    characters_validation = validate_visual_characters_file(characters)
    prompts_validation = validate_visual_prompts_file(prompts_path)
    visual_prompts = manifest.get("visual_prompts") if isinstance(manifest.get("visual_prompts"), dict) else {}
    expected = visual_prompts.get("expected_prompts") or _prompt_estimate(story_dir)
    actual = int(prompts_validation.get("prompts_count") or len(prompts) or 0)
    validation_status = "ok" if prompts_validation.get("ok") else str(prompts_validation.get("status") or "failed")
    manifest_done = str(visual_prompts.get("status") or "").strip() == "done" and str(visual_prompts.get("validation") or "").strip() == "ok"
    legacy_done = not visual_prompts and isinstance(manifest.get("director_prompts"), dict) and str(manifest["director_prompts"].get("status") or "") == "done"
    count_matches = True
    if expected is not None and actual:
        try:
            count_matches = int(expected) == actual
        except (TypeError, ValueError):
            count_matches = False
    if validation_status == "ok" and not count_matches:
        validation_status = "count_mismatch"
    prompts_ready = bool(prompts_validation.get("ok") and count_matches and (manifest_done or legacy_done))
    audio_ready = _audio_ready_for_video(story_dir, manifest)
    characters_ready = bool(_is_nonempty(characters) and characters_validation.get("ok"))

    if excluded:
        status = "excluded"
        blocker = "excluded_from_video"
        next_action = "story is intentionally dropped from video queue"
    elif not audio_ready:
        status = "blocked"
        blocker = "missing_audio"
        next_action = "import/generate narration.mp3"
    elif not characters_ready:
        status = "blocked"
        blocker = "missing_or_invalid_characters"
        next_action = "regenerate characters"
    elif str(visual_prompts.get("status") or "").strip() == "in_progress":
        status = "in_progress"
        blocker = "stale_or_active_prompts_in_progress"
        next_action = "rerun visuals-run-all; incomplete story will be regenerated from story start"
    elif prompts_ready:
        status = "done"
        blocker = "none"
        next_action = "ready_for_runpod"
    elif prompts_validation.get("status") == "partial":
        status = "partial"
        blocker = "partial_prompt_checkpoint"
        next_action = "regenerate prompts for this story from story start"
    elif not count_matches:
        status = "failed"
        blocker = "prompt_count_mismatch"
        next_action = "regenerate prompts for this story"
    elif prompts_path.is_file():
        status = "failed"
        blocker = "invalid_prompts"
        next_action = "regenerate/repair prompts"
    else:
        status = "pending"
        blocker = "missing_prompts"
        next_action = "generate prompts"

    return {
        "story_id": story_id,
        "title": title,
        "story_dir": str(story_dir),
        "excluded_from_video": excluded,
        "audio_ready": audio_ready,
        "characters_status": "done" if characters_ready else ("invalid" if characters.is_file() else "missing"),
        "prompts_status": status,
        "expected": expected,
        "actual": actual,
        "validation": validation_status,
        "ready_for_runpod": bool(prompts_ready and audio_ready and characters_ready and not excluded),
        "blocker": blocker,
        "next_action": next_action,
        "prompts_path": str(prompts_path),
        "visual_prompts": visual_prompts,
        "prompts_validation": prompts_validation,
    }


def _write_prompts_resume_audit_reports(config: OrchestratorConfig, launch_id: str, report: dict[str, Any]) -> dict[str, str]:
    ctx = build_launch_context(config, launch_id=launch_id)
    reports_dir = ctx.launch_root / "07_reports" / "gemini_execution"
    json_path = reports_dir / "YOUTUBE_PROMPTS_RESUME_AUDIT.json"
    md_path = reports_dir / "YOUTUBE_PROMPTS_RESUME_AUDIT.md"
    _write_json(json_path, report)
    lines = [
        "# YOUTUBE_PROMPTS_RESUME_AUDIT",
        "",
        f"launch_id: {launch_id}",
        f"generated_at: {report.get('generated_at')}",
        f"total active stories: {report.get('total_active_stories')}",
        f"prompts done valid: {report.get('prompts_done_valid')}",
        f"prompts partial: {report.get('prompts_partial')}",
        f"prompts missing: {report.get('prompts_missing')}",
        f"prompts invalid: {report.get('prompts_invalid')}",
        f"ready_for_runpod: {report.get('ready_for_runpod')}",
        f"resume_safe: {str(bool(report.get('resume_safe'))).lower()}",
        f"runpod_safe: {str(bool(report.get('runpod_safe'))).lower()}",
        "",
        "| story_id | title | characters | prompts_status | expected | actual | validation | blocker | next_action |",
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in report.get("stories", []):
        lines.append(
            "| "
            + " | ".join(
                str(row.get(key, "")).replace("|", "\\|")
                for key in ("story_id", "title", "characters_status", "prompts_status", "expected", "actual", "validation", "blocker", "next_action")
            )
            + " |"
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def run_youtube_prompts_resume_audit(
    *,
    config: OrchestratorConfig,
    options: YoutubePromptsResumeAuditOptions,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_session

    launch_id = str(options.youtube_run_id or "").strip()
    if not launch_id:
        return {"ok": False, "message": "--youtube-run-id is required"}
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        stories = [
            _visual_prompts_status_row(config, story_dir, accept_known_promo_issues=bool(options.accept_known_promo_issues))
            for story_dir in _iter_launch_story_dirs(config, launch_id)
        ]
    active = [row for row in stories if not row["excluded_from_video"]]
    report: dict[str, Any] = {
        "ok": True,
        "launch_id": launch_id,
        "generated_at": _now_iso(),
        "total_active_stories": len(active),
        "prompts_done_valid": sum(1 for row in active if row["prompts_status"] == "done" and row["validation"] == "ok"),
        "prompts_partial": sum(1 for row in active if row["prompts_status"] == "partial"),
        "prompts_missing": sum(1 for row in active if row["prompts_status"] == "pending"),
        "prompts_invalid": sum(1 for row in active if row["prompts_status"] == "failed"),
        "ready_for_runpod": sum(1 for row in active if row["ready_for_runpod"]),
        "stories": stories,
    }
    report["resume_safe"] = bool(report["prompts_partial"] == 0 and report["prompts_invalid"] == 0)
    report["runpod_safe"] = bool(report["ready_for_runpod"] == report["total_active_stories"])
    report["reports"] = _write_prompts_resume_audit_reports(config, launch_id, report)
    return report


def _prompts_progress_root(ctx: Any) -> Path:
    return ctx.launch_root / "10_Временные_файлы" / "visuals_gemini_batch" / "progress"


def _prompts_progress_path(ctx: Any) -> Path:
    return _prompts_progress_root(ctx) / "visuals_progress.json"


def _prompts_batch_manifest_path(ctx: Any) -> Path:
    return _prompts_progress_root(ctx) / "visuals_batch_manifest.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json_safe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = _read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _current_prompt_checkpoint(stage_dir: Path) -> dict[str, Any]:
    checkpoint_path = stage_dir / "director_checkpoint.json"
    partial_path = stage_dir / "prompts_list.partial.txt"
    final_path = stage_dir / "prompts_list.txt"
    current_chunk = 0
    total_chunks = 0
    if checkpoint_path.is_file():
        try:
            data = _read_json(checkpoint_path)
            if isinstance(data, dict):
                current_chunk = int(data.get("next_chunk_index") or 0)
                total_chunks = int(data.get("total_chunks") or 0)
        except Exception:
            current_chunk = 0
            total_chunks = 0
    actual_prompts = 0
    source = partial_path if partial_path.is_file() else final_path
    if source.is_file():
        actual_prompts = len(_load_prompts(source))
    return {
        "current_chunk": current_chunk,
        "total_chunks": total_chunks,
        "actual_prompts": actual_prompts,
        "partial_exists": partial_path.is_file(),
        "tmp_exists": any(stage_dir.glob("*.tmp")),
        "final_exists": final_path.is_file(),
        "stage_output_path": str(final_path),
    }


def _validate_prompt_assignments(assignments: dict[str, list[str]], active_story_ids: list[str]) -> list[str]:
    blockers: list[str] = []
    seen: dict[str, str] = {}
    for worker_name, story_ids in assignments.items():
        for story_id in story_ids:
            if story_id in seen:
                blockers.append(f"DUPLICATE_STORY_ASSIGNMENT story={story_id} workers={seen[story_id]},{worker_name}")
            seen[story_id] = worker_name
    missing = [story_id for story_id in active_story_ids if story_id not in seen]
    extra = [story_id for story_id in seen if story_id not in set(active_story_ids)]
    if missing:
        blockers.append("missing assignments: " + ", ".join(missing[:10]))
    if extra:
        blockers.append("assignment contains non-active stories: " + ", ".join(extra[:10]))
    if len(seen) != len(active_story_ids):
        blockers.append(f"assignment count mismatch assigned={len(seen)} active_queue={len(active_story_ids)}")
    return blockers


def _initialize_prompts_progress(
    *,
    config: OrchestratorConfig,
    ctx: Any,
    launch_id: str,
    run_session_id: str,
    selected: list[Path],
    skipped: list[dict[str, Any]],
    worker_batches: list[list[tuple[int, Path, str, str]]],
) -> dict[str, Any]:
    assignments = {
        f"worker_{worker_index}": [story_id for _index, _story_dir, story_id, _title in worker_jobs]
        for worker_index, worker_jobs in enumerate(worker_batches, start=1)
    }
    assigned_story_ids = [story_id for worker_jobs in worker_batches for _index, _story_dir, story_id, _title in worker_jobs]
    blockers = _validate_prompt_assignments(assignments, assigned_story_ids)
    if blockers:
        raise RuntimeError("; ".join(blockers))
    now = _now_iso()
    stories: dict[str, Any] = {}
    assignment_lookup = {
        story_id: f"worker_{worker_index}"
        for worker_index, worker_jobs in enumerate(worker_batches, start=1)
        for _index, _story_dir, story_id, _title in worker_jobs
    }
    for story_dir in selected:
        manifest = _load_manifest(story_dir)
        story_id, title = _story_identity(story_dir, manifest)
        row = _visual_prompts_status_row(config, story_dir)
        assigned_worker = assignment_lookup.get(story_id, "already_ready" if row["prompts_status"] == "done" else "")
        stage_dir = ""
        if assigned_worker.startswith("worker_"):
            stage_dir = str(ctx.launch_root / "10_Временные_файлы" / "visuals_gemini_batch" / "prompts" / run_session_id / assigned_worker / story_id)
        stories[story_id] = {
            "title": title,
            "story_dir": str(story_dir),
            "stage_dir": stage_dir,
            "assigned_worker": assigned_worker,
            "status": "pending" if row["prompts_status"] != "done" else "done",
            "current_chunk": 0,
            "total_chunks": 0,
            "expected_prompts": row.get("expected") or 0,
            "actual_prompts": row.get("actual") or 0,
            "output_path": row.get("prompts_path", ""),
            "validation": "ok" if row["prompts_status"] == "done" else "pending",
            "error": None,
            "updated_at": now,
        }
    initial_done = sum(1 for row in stories.values() if row.get("status") == "done")
    initial_pending = sum(1 for row in stories.values() if row.get("status") == "pending")
    payload = {
        "launch_id": launch_id,
        "stage": "prompts",
        "run_session_id": run_session_id,
        "started_at": now,
        "updated_at": now,
        "total_stories": len(_iter_launch_story_dirs(config, launch_id)),
        "excluded_from_video": sum(1 for row in skipped if row.get("reason") == "excluded_from_video"),
        "active_queue": len(selected),
        "prompt_generation_queue": len(assigned_story_ids),
        "done": initial_done,
        "failed": 0,
        "blocked": 0,
        "partial": 0,
        "in_progress": 0,
        "pending": initial_pending,
        "remaining": initial_pending,
        "workers": {
            f"worker_{worker_index}": {
                "assigned_total": len(worker_jobs),
                "done": 0,
                "failed": 0,
                "partial": 0,
                "current_story_id": None,
                "current_title": None,
                "current_chunk": None,
                "total_chunks": None,
                "started_at": None,
                "updated_at": None,
            }
            for worker_index, worker_jobs in enumerate(worker_batches, start=1)
        },
        "stories": stories,
        "last_completed": [],
        "last_errors": [],
    }
    manifest = {
        "launch_id": launch_id,
        "stage": "prompts",
        "run_session_id": run_session_id,
        "active_queue": len(selected),
        "prompt_generation_queue": len(assigned_story_ids),
        "workers": len(worker_batches),
        "assignments": assignments,
        "created_at": now,
    }
    _atomic_write_json(_prompts_progress_path(ctx), payload)
    _atomic_write_json(_prompts_batch_manifest_path(ctx), manifest)
    return payload


def reconcile_visuals_progress_from_filesystem(
    *,
    config: OrchestratorConfig,
    launch_id: str,
    run_session_id: str = "",
    accept_known_promo_issues: bool = False,
) -> dict[str, Any]:
    ctx = build_launch_context(config, launch_id=launch_id)
    progress = _read_json_safe(_prompts_progress_path(ctx))
    session = run_session_id or str(progress.get("run_session_id") or "")
    stories_meta = progress.get("stories") if isinstance(progress.get("stories"), dict) else {}
    workers = progress.get("workers") if isinstance(progress.get("workers"), dict) else {}
    if not workers:
        workers = {}
    rows: dict[str, Any] = {}
    counts = {"done": 0, "failed": 0, "blocked": 0, "partial": 0, "in_progress": 0, "pending": 0, "excluded": 0}

    with_progress = bool(stories_meta)
    story_dirs = _iter_launch_story_dirs(config, launch_id)
    for story_dir in story_dirs:
        row = _visual_prompts_status_row(config, story_dir, accept_known_promo_issues=accept_known_promo_issues)
        story_id = str(row["story_id"])
        meta = stories_meta.get(story_id) if isinstance(stories_meta.get(story_id), dict) else {}
        assigned_worker = str(meta.get("assigned_worker") or "")
        stage_dir = Path(str(meta.get("stage_dir") or ""))
        if not stage_dir.is_dir() and session and assigned_worker:
            stage_dir = ctx.launch_root / "10_Временные_файлы" / "visuals_gemini_batch" / "prompts" / session / assigned_worker / story_id
        checkpoint = _current_prompt_checkpoint(stage_dir) if stage_dir.is_dir() else {}
        status = str(row["prompts_status"])
        if status == "done":
            normalized_status = "done"
        elif row["excluded_from_video"]:
            normalized_status = "excluded"
        elif checkpoint.get("partial_exists") or checkpoint.get("tmp_exists") or checkpoint.get("actual_prompts", 0):
            normalized_status = "partial"
        elif status == "in_progress":
            normalized_status = "in_progress"
        elif status == "failed":
            normalized_status = "failed"
        elif status == "blocked":
            normalized_status = "blocked"
        else:
            normalized_status = "pending"
        counts[normalized_status] = counts.get(normalized_status, 0) + 1
        error_value = meta.get("error")
        if normalized_status == "done":
            error_value = None
        elif normalized_status in {"failed", "pending", "partial", "blocked", "in_progress"}:
            error_value = normalize_failure_reason(
                str(error_value or ""),
                fallback=classify_stage_prompts_failure(
                    stage_dir=stage_dir if stage_dir.is_dir() else None,
                    canonical_ready=normalized_status == "done",
                ),
            )
        rows[story_id] = {
            **meta,
            "story_id": story_id,
            "title": row["title"],
            "assigned_worker": assigned_worker or meta.get("assigned_worker"),
            "status": normalized_status,
            "current_chunk": checkpoint.get("current_chunk", meta.get("current_chunk", 0)),
            "total_chunks": checkpoint.get("total_chunks", meta.get("total_chunks", 0)),
            "expected_prompts": row.get("expected") or meta.get("expected_prompts", 0),
            "actual_prompts": row.get("actual") or checkpoint.get("actual_prompts", 0),
            "output_path": row.get("prompts_path") or meta.get("output_path", ""),
            "validation": row.get("validation", "pending"),
            "blocker": row.get("blocker", ""),
            "next_action": row.get("next_action", ""),
            "error": error_value,
            "updated_at": _now_iso(),
        }

    active_queue = sum(1 for row in rows.values() if row.get("status") != "excluded") if with_progress else sum(1 for row in rows.values() if row.get("status") != "excluded")
    done = counts.get("done", 0)
    failed = counts.get("failed", 0)
    partial = counts.get("partial", 0)
    in_progress = counts.get("in_progress", 0)
    blocked = counts.get("blocked", 0)
    pending = counts.get("pending", 0)
    remaining = max(0, active_queue - done - failed - blocked)

    for worker_name, worker in workers.items():
        assigned = [row for row in rows.values() if row.get("assigned_worker") == worker_name]
        active = next((row for row in assigned if row.get("status") in {"in_progress", "partial"} and row.get("current_chunk")), None)
        worker.update(
            {
                "assigned_total": len(assigned) or int(worker.get("assigned_total") or 0),
                "done": sum(1 for row in assigned if row.get("status") == "done"),
                "failed": sum(1 for row in assigned if row.get("status") == "failed"),
                "partial": sum(1 for row in assigned if row.get("status") == "partial"),
                "current_story_id": active.get("story_id") if active else worker.get("current_story_id"),
                "current_title": active.get("title") if active else worker.get("current_title"),
                "current_chunk": active.get("current_chunk") if active else worker.get("current_chunk"),
                "total_chunks": active.get("total_chunks") if active else worker.get("total_chunks"),
                "updated_at": _now_iso(),
            }
        )

    progress.update(
        {
            "launch_id": launch_id,
            "stage": "prompts",
            "run_session_id": session,
            "started_at": progress.get("started_at") or _now_iso(),
            "updated_at": _now_iso(),
            "total_stories": len(story_dirs),
            "excluded_from_video": counts.get("excluded", 0),
            "active_queue": active_queue,
            "done": done,
            "failed": failed,
            "blocked": blocked,
            "partial": partial,
            "in_progress": in_progress,
            "pending": pending,
            "remaining": remaining,
            "workers": workers,
            "stories": rows,
            "ready_for_runpod": sum(1 for row in rows.values() if row.get("status") == "done"),
            "not_ready_for_runpod": sum(1 for row in rows.values() if row.get("status") not in {"done", "excluded"}),
        }
    )
    _atomic_write_json(_prompts_progress_path(ctx), progress)
    return progress


def _render_prompts_progress(progress: dict[str, Any]) -> None:
    print("================ PROMPTS PROGRESS ================", flush=True)
    print(f"launch_id: {progress.get('launch_id')}", flush=True)
    print(f"session: {progress.get('run_session_id')}", flush=True)
    print(f"active queue:   {progress.get('active_queue', 0)}", flush=True)
    for key in ("done", "failed", "partial", "in_progress", "pending", "remaining"):
        print(f"{key + ':':16}{progress.get(key, 0)}", flush=True)
    workers = progress.get("workers") if isinstance(progress.get("workers"), dict) else {}
    for worker_name in sorted(workers):
        worker = workers[worker_name]
        title = worker.get("current_title") or "idle"
        chunk = f"{worker.get('current_chunk') or 0}/{worker.get('total_chunks') or 0}"
        assigned_total = int(worker.get("assigned_total") or 0)
        worker_remaining = max(0, assigned_total - int(worker.get("done") or 0) - int(worker.get("failed") or 0))
        print(
            f"{worker_name}: {title} | chunk {chunk} | assigned {assigned_total} | "
            f"done {worker.get('done', 0)} | failed {worker.get('failed', 0)} | remaining {worker_remaining}",
            flush=True,
        )
    last_completed = progress.get("last_completed") if isinstance(progress.get("last_completed"), list) else []
    last_errors = progress.get("last_errors") if isinstance(progress.get("last_errors"), list) else []
    print("last completed:", flush=True)
    for item in last_completed[-3:] or ["none"]:
        print(f"- {item}", flush=True)
    print("last errors:", flush=True)
    for item in last_errors[-3:] or ["none"]:
        print(f"- {item}", flush=True)
    print("==================================================", flush=True)


def run_youtube_prompts_progress_status(
    *,
    config: OrchestratorConfig,
    options: YoutubePromptsProgressStatusOptions,
) -> dict[str, Any]:
    launch_id = str(options.youtube_run_id or "").strip()
    if not launch_id:
        return {"ok": False, "message": "--youtube-run-id is required"}
    progress = reconcile_visuals_progress_from_filesystem(
        config=config,
        launch_id=launch_id,
        run_session_id=str(options.run_session_id or "").strip(),
        accept_known_promo_issues=bool(options.accept_known_promo_issues),
    )
    stories = progress.get("stories") if isinstance(progress.get("stories"), dict) else {}
    return {
        "ok": True,
        **progress,
        "stories_list": list(stories.values()),
    }


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
            prompts_row = _visual_prompts_status_row(
                config,
                story_dir,
                accept_known_promo_issues=bool(options.accept_known_promo_issues),
            )
            visual_prompt_ready = bool(prompts_row["ready_for_runpod"])
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
                blocker = str(prompts_row["blocker"])
                next_action = str(prompts_row["next_action"])
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
                "characters_status": prompts_row["characters_status"],
                "prompts_status": prompts_row["prompts_status"],
                "expected_prompts": prompts_row["expected"],
                "actual_prompts": prompts_row["actual"],
                "prompts_validation": prompts_row["validation"],
                "ready_for_runpod": prompts_row["ready_for_runpod"],
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
        "prompts": {
            "done": sum(1 for row in active_rows if row.get("prompts_status") == "done"),
            "partial": sum(1 for row in active_rows if row.get("prompts_status") == "partial"),
            "failed": sum(1 for row in active_rows if row.get("prompts_status") == "failed"),
            "pending": sum(1 for row in active_rows if row.get("prompts_status") == "pending"),
            "in_progress": sum(1 for row in active_rows if row.get("prompts_status") == "in_progress"),
            "ready_for_runpod": sum(1 for row in active_rows if row.get("ready_for_runpod")),
        },
        "images_ready": sum(1 for row in active_rows if row["images_ready"]),
        "blocked": sum(1 for row in active_rows if row["blocker"] and row["blocker"] != "ready_for_runpod"),
        "pending": sum(1 for row in active_rows if row["audio_ready"] and not row["visual_prompt_ready"]),
        "ready_for_frames": sum(1 for row in active_rows if row["visual_prompt_ready"] and not row["images_ready"]),
        "known_promo_issues_accepted": any(row["promo_issues_accepted"] for row in rows),
    }
    not_ready_for_runpod = sum(1 for row in active_rows if not row.get("ready_for_runpod"))
    summary["not_ready_for_runpod"] = not_ready_for_runpod
    summary["next_stage_allowed"] = bool(
        not_ready_for_runpod == 0
        and (summary["ready_for_frames"] > 0 or summary["images_ready"] > 0)
    )
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
                    print(
                        f"[{index}/{active_queue_count}] FAILED reason={_terminal_safe(batch_result.get('next_action') or batch_result.get('status'))}",
                        flush=True,
                    )
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
            run_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"run_session_id:       {run_session_id}", flush=True)
            prompt_progress = {"done": 0, "failed": 0, "blocked": 0, "skipped": 0}
            prompt_jobs: list[tuple[int, Path, str, str]] = []
            for index, story_dir in enumerate(selected, start=1):
                manifest = _load_manifest(story_dir)
                story_id, title = _story_identity(story_dir, manifest)
                before = _story_visual_readiness(config, story_dir, story_id)
                story_started = time.monotonic()
                if not before["prompts_ready"] and options.execute:
                    recovery = _recover_temp_prompts_output(
                        config=config,
                        ctx=ctx,
                        story_id=story_id,
                        story_dir=story_dir,
                        execute=bool(options.execute),
                    )
                    if recovery.get("ok"):
                        before = _story_visual_readiness(config, story_dir, story_id)
                        print(
                            f"[{index}/{active_queue_count}] RECOVER prompts: {story_id} / {title} | "
                            f"source={recovery.get('source')}",
                            flush=True,
                        )
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
                    prompt_validation = before.get("prompts_validation") if isinstance(before.get("prompts_validation"), dict) else {}
                    if prompt_validation.get("status") == "partial":
                        print(
                            f"[{index}/{active_queue_count}] RESUME prompts: {story_id} / {title} | "
                            f"previous_partial={prompt_validation.get('prompts_count', 0)}/{_prompt_estimate(story_dir) or '?'} | "
                            "action=regenerate_from_story_start",
                            flush=True,
                        )
                    prompt_jobs.append((index, story_dir, story_id, title))

            if prompt_jobs:
                if not options.execute:
                    for job_number, (index, story_dir, story_id, title) in enumerate(prompt_jobs, start=1):
                        prompt_progress["done"] += 1
                        print(
                            f"[{index}/{active_queue_count}] DRY-RUN prompts: {story_id} / {title} | "
                            f"bot=gemini_director | worker={(job_number - 1) % prompt_workers + 1}/{prompt_workers} | prompts={_story_visual_readiness(config, story_dir, story_id)['prompts_status']}",
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
                    _prepare_prompt_worker_profiles(config, worker_count)
                    worker_batches: list[list[tuple[int, Path, str, str]]] = [[] for _ in range(worker_count)]
                    for job_number, job in enumerate(prompt_jobs):
                        worker_batches[job_number % worker_count].append(job)
                    progress_payload = _initialize_prompts_progress(
                        config=config,
                        ctx=ctx,
                        launch_id=launch_id,
                        run_session_id=run_session_id,
                        selected=selected,
                        skipped=skipped,
                        worker_batches=worker_batches,
                    )
                    print(f"visuals_progress_path: {_prompts_progress_path(ctx)}", flush=True)
                    print(f"visuals_batch_manifest_path: {_prompts_batch_manifest_path(ctx)}", flush=True)
                    _render_prompts_progress(progress_payload)
                    future_map = {}
                    with ThreadPoolExecutor(max_workers=prompt_workers) as executor:
                        for worker_index, worker_jobs in enumerate(worker_batches, start=1):
                            if not worker_jobs:
                                continue
                            if worker_index > 1:
                                print(
                                    f"[worker {worker_index}/{worker_count}] stagger start +45s "
                                    "so previous browser can initialize",
                                    flush=True,
                                )
                                time.sleep(45)
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
                                / run_session_id
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
                            for _index, story_dir, _story_id, _title in worker_jobs:
                                worker_name = f"worker_{worker_index}"
                                _update_manifest_dict(
                                    story_dir,
                                    {
                                        "visual_prompts": {
                                            "status": "in_progress",
                                            "started_at": _now_iso(),
                                            "updated_at": _now_iso(),
                                            "worker_id": worker_index,
                                            "expected_prompts": _prompt_estimate(story_dir),
                                            "actual_prompts": 0,
                                            "validation": "pending",
                                            "error": None,
                                        },
                                        "pipeline_stage_status": {"scenes_prompts": "in_progress", "director_prompts": "in_progress"},
                                    },
                                )
                                current_progress = _read_json_safe(_prompts_progress_path(ctx))
                                stories_progress = current_progress.get("stories") if isinstance(current_progress.get("stories"), dict) else {}
                                if isinstance(stories_progress.get(_story_id), dict):
                                    stories_progress[_story_id].update(
                                        {
                                            "status": "in_progress",
                                            "assigned_worker": worker_name,
                                            "stage_dir": str(worker_root / _story_id),
                                            "updated_at": _now_iso(),
                                        }
                                    )
                                workers_progress = current_progress.get("workers") if isinstance(current_progress.get("workers"), dict) else {}
                                if isinstance(workers_progress.get(worker_name), dict):
                                    workers_progress[worker_name].update(
                                        {
                                            "current_story_id": _story_id,
                                            "current_title": _title,
                                            "started_at": workers_progress[worker_name].get("started_at") or _now_iso(),
                                            "updated_at": _now_iso(),
                                        }
                                    )
                                current_progress["stories"] = stories_progress
                                current_progress["workers"] = workers_progress
                                current_progress["updated_at"] = _now_iso()
                                _atomic_write_json(_prompts_progress_path(ctx), current_progress)
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
                                progress_payload = reconcile_visuals_progress_from_filesystem(
                                    config=config,
                                    launch_id=launch_id,
                                    run_session_id=run_session_id,
                                    accept_known_promo_issues=bool(options.accept_known_promo_issues),
                                )
                                _render_prompts_progress(progress_payload)
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
                                        validation = after.get("prompts_validation") if isinstance(after.get("prompts_validation"), dict) else {}
                                        actual_prompts = int(validation.get("prompts_count") or len(_load_prompts(_prompts_path(config, story_id, story_dir))) or 0)
                                        completed_at = _now_iso()
                                        _update_manifest_dict(
                                            story_dir,
                                            {
                                                "status": {"director_done": True},
                                                "pipeline_stage_status": {"scenes_prompts": "done", "director_prompts": "done"},
                                                "visual_prompts": {
                                                    "status": "done",
                                                    "started_at": None,
                                                    "updated_at": completed_at,
                                                    "completed_at": completed_at,
                                                    "attempts": 1,
                                                    "worker_id": worker_index,
                                                    "expected_prompts": actual_prompts,
                                                    "actual_prompts": actual_prompts,
                                                    "validation": "ok",
                                                    "error": None,
                                                    "path": str(_prompts_path(config, story_id, story_dir)),
                                                },
                                            },
                                        )
                                        prompt_progress["done"] += 1
                                        label = "DONE"
                                        ok = True
                                    else:
                                        validation = after.get("prompts_validation") if isinstance(after.get("prompts_validation"), dict) else {}
                                        partial_status = str(validation.get("status") or after.get("prompts_status") or "failed")
                                        story_stage_dir = worker_root / story_id
                                        failure_reason = normalize_failure_reason(
                                            str(batch_result.get("next_action") or batch_result.get("status") or ""),
                                            fallback=classify_stage_prompts_failure(stage_dir=story_stage_dir),
                                        )
                                        if after["prompts_status"] == "partial":
                                            failure_reason = PROMPTS_GENERATION_INCOMPLETE
                                        _update_manifest_dict(
                                            story_dir,
                                            {
                                                "visual_prompts": {
                                                    "status": "partial" if partial_status == "partial" else "failed",
                                                    "updated_at": _now_iso(),
                                                    "worker_id": worker_index,
                                                    "expected_prompts": _prompt_estimate(story_dir),
                                                    "actual_prompts": int(validation.get("prompts_count") or 0),
                                                    "validation": "failed",
                                                    "error": failure_reason,
                                                },
                                                "pipeline_stage_status": {"scenes_prompts": partial_status, "director_prompts": partial_status},
                                            },
                                        )
                                        prompt_progress["failed"] += 1
                                        label = "PARTIAL" if after["prompts_status"] == "partial" else "FAILED"
                                        ok = False
                                    progress_payload = reconcile_visuals_progress_from_filesystem(
                                        config=config,
                                        launch_id=launch_id,
                                        run_session_id=run_session_id,
                                        accept_known_promo_issues=bool(options.accept_known_promo_issues),
                                    )
                                    progress_stories = progress_payload.get("stories") if isinstance(progress_payload.get("stories"), dict) else {}
                                    progress_workers = progress_payload.get("workers") if isinstance(progress_payload.get("workers"), dict) else {}
                                    if isinstance(progress_stories.get(story_id), dict):
                                        progress_stories[story_id].update(
                                            {
                                                "status": "done" if ok else ("partial" if label == "PARTIAL" else "failed"),
                                                "validation": "ok" if ok else "failed",
                                                "error": None if ok else failure_reason,
                                                "updated_at": _now_iso(),
                                            }
                                        )
                                    worker_name = f"worker_{worker_index}"
                                    if isinstance(progress_workers.get(worker_name), dict):
                                        progress_workers[worker_name].update(
                                            {
                                                "current_story_id": None,
                                                "current_title": None,
                                                "current_chunk": None,
                                                "total_chunks": None,
                                                "updated_at": _now_iso(),
                                            }
                                        )
                                    if ok:
                                        completed_items = progress_payload.get("last_completed") if isinstance(progress_payload.get("last_completed"), list) else []
                                        completed_items.append(title)
                                        progress_payload["last_completed"] = completed_items[-10:]
                                    elif label == "FAILED":
                                        error_items = progress_payload.get("last_errors") if isinstance(progress_payload.get("last_errors"), list) else []
                                        error_items.append(f"{story_id}: {batch_result.get('next_action') or batch_result.get('status')}")
                                        progress_payload["last_errors"] = error_items[-10:]
                                    progress_payload["stories"] = progress_stories
                                    progress_payload["workers"] = progress_workers
                                    progress_payload["updated_at"] = _now_iso()
                                    _atomic_write_json(_prompts_progress_path(ctx), progress_payload)
                                    print(
                                        f"[{index}/{active_queue_count}] {label} prompts: {story_id} / {title} | "
                                        f"characters={after['characters_status']} | prompts={after['prompts_status']} | "
                                        f"generated={(after.get('prompts_validation') or {}).get('prompts_count', 0)}/{_prompt_estimate(story_dir) or '?'} | "
                                        f"validation={(after.get('prompts_validation') or {}).get('status', 'failed')} | elapsed={elapsed}",
                                        flush=True,
                                    )
                                    if label == "FAILED":
                                        print(
                                            f"[{index}/{active_queue_count}] FAILED reason={_terminal_safe(batch_result.get('next_action') or batch_result.get('status'))}",
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
                                    progress_payload = reconcile_visuals_progress_from_filesystem(
                                        config=config,
                                        launch_id=launch_id,
                                        run_session_id=run_session_id,
                                        accept_known_promo_issues=bool(options.accept_known_promo_issues),
                                    )
                                    _render_prompts_progress(progress_payload)

            prompts_missing = []
            frames_queue = []
            print("================ RUNPOD_PROMPTS_PREFLIGHT ==============", flush=True)
            for story_dir in selected:
                manifest = _load_manifest(story_dir)
                story_id, title = _story_identity(story_dir, manifest)
                readiness = _story_visual_readiness(config, story_dir, story_id)
                if not readiness["prompts_ready"]:
                    validation = readiness.get("prompts_validation") if isinstance(readiness.get("prompts_validation"), dict) else {}
                    prompts_missing.append(
                        {
                            "story_id": story_id,
                            "title": title,
                            "status": readiness.get("prompts_status"),
                            "actual": validation.get("prompts_count", 0),
                            "expected": _prompt_estimate(story_dir),
                        }
                    )
                    print(
                        f"{story_id}: BLOCKED prompts_status={readiness.get('prompts_status')} "
                        f"actual={validation.get('prompts_count', 0)} expected={_prompt_estimate(story_dir) or '?'}",
                        flush=True,
                    )
                else:
                    frames_queue.append((story_dir, story_id, title))
                    validation = readiness.get("prompts_validation") if isinstance(readiness.get("prompts_validation"), dict) else {}
                    print(
                        f"{story_id}: OK prompts_status=done validation=ok actual={validation.get('prompts_count', 0)}",
                        flush=True,
                    )
            print(f"RUNPOD_PROMPTS_PREFLIGHT ready={len(frames_queue)} blocked={len(prompts_missing)}", flush=True)

            if prompts_missing:
                print("================ PHASE 3: RUNPOD SKIPPED ================", flush=True)
                print(f"reason=prompts_not_ready missing_prompts={len(prompts_missing)}", flush=True)
                for row in prompts_missing:
                    print(
                        f"- {row.get('story_id')} / {row.get('title')} -> prompts_status={row.get('status')} "
                        f"actual={row.get('actual')} expected={row.get('expected') or '?'}",
                        flush=True,
                    )
            elif options.prompts_only:
                print("================ PHASE 3: SKIPPED BY --prompts-only =====", flush=True)
                print("reason=prompts_only_no_runpod_no_frames", flush=True)
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
    summary = dict(status.get("summary") if isinstance(status.get("summary"), dict) else {})
    preflight_blocked = len(locals().get("prompts_missing") or [])
    if preflight_blocked > 0 or int(summary.get("not_ready_for_runpod") or 0) > 0:
        summary["next_stage_allowed"] = False
        summary["runpod_preflight_blocked"] = max(preflight_blocked, int(summary.get("not_ready_for_runpod") or 0))
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
    print(f"next_stage_allowed: {str(bool(summary.get('next_stage_allowed'))).lower()}", flush=True)
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
        "next_stage_allowed": bool(summary.get("next_stage_allowed")),
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

    completed_at = _now_iso()
    _update_manifest_dict(
        story_dir,
        {
            "status": {"director_done": True},
            "pipeline_stage_status": {"scenes_prompts": "done", "director_prompts": "done"},
            "visual_prompts": {
                "status": "done",
                "started_at": None,
                "updated_at": completed_at,
                "completed_at": completed_at,
                "attempts": 1,
                "worker_id": None,
                "expected_prompts": len(prompts),
                "actual_prompts": len(prompts),
                "validation": "ok",
                "error": None,
                "path": str(prompts_path),
            },
        },
    )

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
