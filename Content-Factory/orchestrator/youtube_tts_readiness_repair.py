"""Audit/repair YouTube TTS launch readiness and rebuild partitioned jobs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.launch_contract import (
    build_launch_context,
    guarded_copy2,
    guarded_write_json,
    guarded_write_text,
    story_slug,
)
from orchestrator.voice_contract import (
    _candidate_info_paths,
    resolve_youtube_tts_voice,
)
from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.drive_voice_resolve import resolve_colab_kokoro_voice_id
from orchestrator.site_tts.info_parser import resolve_voice_letter_from_info_content
from orchestrator.youtube_tts_launch_jobs import (
    PrepareLaunchJobsOptions,
    TtsLaunchOptions,
    preflight_launch_jobs,
    prepare_launch_jobs,
    status_launch_jobs,
)

AUDIT_REPORT_JSON = "YOUTUBE_TTS_READINESS_AUDIT.json"
AUDIT_REPORT_MD = "YOUTUBE_TTS_READINESS_AUDIT.md"
REPAIR_REPORT_JSON = "YOUTUBE_TTS_READINESS_REPAIR.json"
REPAIR_REPORT_MD = "YOUTUBE_TTS_READINESS_REPAIR.md"
FINAL_REPORT_JSON = "YOUTUBE_TTS_JOB_FINAL.json"
FINAL_REPORT_MD = "YOUTUBE_TTS_JOB_FINAL.md"

PROMO_OPTIONAL_STATUSES = frozenset(
    {
        "stale_or_missing",
        "stale",
        "skipped",
        "not_configured",
        "missing",
        "blocked",
    }
)


@dataclass(frozen=True)
class RepairReadinessOptions:
    youtube_run_id: str
    workers: int = 5
    execute: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_stem(value: str) -> str:
    return story_slug(value or "story")


def _canonical_tts_input_path(story_dir: Path) -> Path:
    return story_dir / "03_promo" / "text_ready_for_audio.txt"


def _legacy_story_roots(config: OrchestratorConfig, canonical: str, story_dir: Path) -> list[Path]:
    roots = [
        config.root_dir / "output" / "youtube" / canonical,
        config.root_dir / "output" / "youtube" / story_dir.name,
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _latest_rejected_safe(story_dir: Path) -> Path | None:
    rejected_root = story_dir / "02_safe_story" / "rejected_reset"
    if not rejected_root.is_dir():
        return None
    candidates = sorted(rejected_root.glob("*/safe_story.txt"), reverse=True)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 500:
            return path
    return None


def _resolve_manifest_input_path(manifest: dict[str, Any], story_dir: Path) -> Path:
    for container, key in (
        ("text_ready_for_audio", "path"),
        ("actual_artifacts", "text_ready_for_audio"),
        ("promo", "output_path"),
        ("expected_artifacts", "promo_text_ready_for_audio"),
    ):
        block = manifest.get(container)
        if isinstance(block, dict):
            raw = str(block.get(key) or "").strip()
            if raw:
                return Path(raw)
    return _canonical_tts_input_path(story_dir)


def _resolve_manifest_voice(manifest: dict[str, Any]) -> tuple[str, str]:
    for section_name in ("voice_contract", "tts_kokoro_colab"):
        section = manifest.get(section_name)
        if not isinstance(section, dict):
            continue
        voice = str(section.get("kokoro_voice") or section.get("youtube_voice_id") or "").strip()
        label = str(
            section.get("voice_label")
            or section.get("voice_type")
            or section.get("expected_gender")
            or ""
        ).strip().upper()[:1]
        if voice and label in {"M", "F", "U"}:
            return label, voice
    return "", ""


def _local_audio_path(manifest: dict[str, Any], story_dir: Path) -> Path:
    for section_name, key in (("tts_kokoro_colab", "audio_path"), ("audio", "path")):
        section = manifest.get(section_name)
        if isinstance(section, dict):
            raw = str(section.get(key) or "").strip()
            if raw:
                return Path(raw)
    return story_dir / "04_audio" / "narration.mp3"


def _audio_done(local_audio: Path, drive_audio: Path) -> bool:
    return local_audio.is_file() or drive_audio.is_file()


def _promo_optional(manifest: dict[str, Any]) -> bool:
    promo = manifest.get("promo")
    if not isinstance(promo, dict):
        return True
    status = str(promo.get("status") or "").strip().lower()
    if not status:
        return True
    return status in PROMO_OPTIONAL_STATUSES


def _collect_text_candidates(
    *,
    config: OrchestratorConfig,
    story_dir: Path,
    manifest: dict[str, Any],
    drive_root: Path,
) -> list[tuple[str, Path]]:
    canonical = str(manifest.get("canonical_basename") or story_dir.name).strip()
    stem = _safe_stem(canonical)
    candidates: list[tuple[str, Path]] = [
        ("manifest_path", _resolve_manifest_input_path(manifest, story_dir)),
        ("canonical_promo", _canonical_tts_input_path(story_dir)),
        ("launch_tts_input", story_dir / "04_audio" / "tts_input" / "tts_input_with_promo.txt"),
        ("safe_story", story_dir / "02_safe_story" / "safe_story.txt"),
        ("drive_text", drive_root / "texts" / f"{stem}.txt"),
        ("source_cleaned", story_dir / "00_source" / "source_cleaned_story.txt"),
    ]
    rejected_safe = _latest_rejected_safe(story_dir)
    if rejected_safe is not None:
        candidates.append(("rejected_safe_backup", rejected_safe))
    for legacy_root in _legacy_story_roots(config, canonical, story_dir):
        candidates.extend(
            [
                (f"legacy:{legacy_root.name}:promo", legacy_root / "03_promo" / "text_ready_for_audio.txt"),
                (f"legacy:{legacy_root.name}:safe", legacy_root / "02_safe_story" / "safe_story.txt"),
                (f"legacy:{legacy_root.name}:tts_input", legacy_root / "04_audio" / "tts_input" / "tts_input_with_promo.txt"),
            ]
        )
    seen: set[str] = set()
    unique: list[tuple[str, Path]] = []
    for kind, path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append((kind, path))
    return unique


def _audit_story(
    *,
    config: OrchestratorConfig,
    youtube_run_id: str,
    manifest_path: Path,
    drive_root: Path,
) -> dict[str, Any]:
    story_dir = manifest_path.parent
    manifest = _read_json(manifest_path)
    canonical = str(manifest.get("canonical_basename") or manifest.get("story_id") or story_dir.name).strip()
    story_id = str(manifest.get("story_id") or canonical).strip()
    expected_input = _resolve_manifest_input_path(manifest, story_dir)
    voice_label, kokoro_voice = _resolve_manifest_voice(manifest)
    local_audio = _local_audio_path(manifest, story_dir)
    drive_audio = drive_root / "audio" / f"{_safe_stem(canonical)}.mp3"
    text_candidates = _collect_text_candidates(
        config=config,
        story_dir=story_dir,
        manifest=manifest,
        drive_root=drive_root,
    )
    existing_text = [(kind, str(path), path.is_file(), _sha256_file(path) if path.is_file() else "") for kind, path in text_candidates]
    problems: list[str] = []
    if not expected_input.is_file():
        problems.append("missing_input_text")
    if not voice_label or not kokoro_voice:
        problems.append("missing_voice_contract")
    promo = manifest.get("promo") if isinstance(manifest.get("promo"), dict) else {}
    tts = manifest.get("tts_kokoro_colab") if isinstance(manifest.get("tts_kokoro_colab"), dict) else {}
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    return {
        "youtube_run_id": youtube_run_id,
        "story_id": story_id,
        "canonical_basename": canonical,
        "story_slug": story_slug(canonical),
        "story_dir": str(story_dir),
        "story_manifest": str(manifest_path),
        "expected_input_path": str(expected_input),
        "expected_input_exists": expected_input.is_file(),
        "voice_label": voice_label,
        "kokoro_voice": kokoro_voice,
        "voice_ok": bool(voice_label and kokoro_voice),
        "local_audio_path": str(local_audio),
        "drive_audio_path": str(drive_audio),
        "audio_done": _audio_done(local_audio, drive_audio),
        "promo_status": str(promo.get("status") or ""),
        "promo_optional": _promo_optional(manifest),
        "tts_status": str(tts.get("status") or ""),
        "audio_status": str(audio.get("status") or ""),
        "safe_story_exists": (story_dir / "02_safe_story" / "safe_story.txt").is_file(),
        "text_candidates": existing_text,
        "problems": problems,
        "terminal_blocked": False,
        "terminal_reason": "",
        "terminal_missing_path": "",
    }


def _pick_text_source(audit: dict[str, Any]) -> tuple[Path | None, str]:
    if audit.get("expected_input_exists"):
        return Path(str(audit["expected_input_path"])), "existing_expected"
    promo_optional = bool(audit.get("promo_optional"))
    priority = (
        "canonical_promo",
        "launch_tts_input",
        "manifest_path",
        "drive_text",
        "safe_story",
        "rejected_safe_backup",
    )
    if promo_optional:
        priority = (
            "canonical_promo",
            "launch_tts_input",
            "manifest_path",
            "drive_text",
            "safe_story",
            "rejected_safe_backup",
        ) + tuple(
            kind
            for kind, _path, exists, _hash in audit.get("text_candidates", [])
            if exists and str(kind).startswith("legacy:")
        )
    for kind in priority:
        for candidate_kind, path_s, exists, _hash in audit.get("text_candidates", []):
            if candidate_kind != kind or not exists:
                continue
            return Path(path_s), str(candidate_kind)
    for candidate_kind, path_s, exists, _hash in audit.get("text_candidates", []):
        if exists:
            return Path(path_s), str(candidate_kind)
    return None, ""


def _repair_safe_story(
    *,
    config: OrchestratorConfig,
    story_dir: Path,
    launch_root: Path,
    audit: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    safe_path = story_dir / "02_safe_story" / "safe_story.txt"
    if safe_path.is_file():
        return {"action": "none", "path": str(safe_path), "ok": True}
    source, kind = _pick_text_source(audit)
    restore_from: Path | None = None
    if kind == "rejected_safe_backup" and source is not None:
        restore_from = source
    if restore_from is None:
        for candidate_kind, path_s, exists, _hash in audit.get("text_candidates", []):
            if candidate_kind.endswith(":safe") and exists:
                restore_from = Path(path_s)
                kind = candidate_kind
                break
    if restore_from is None or not restore_from.is_file():
        return {"action": "missing_safe_source", "ok": False, "path": str(safe_path)}
    if not execute:
        return {"action": "dry_run_restore_safe", "source": str(restore_from), "path": str(safe_path), "ok": True}
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    guarded_copy2(restore_from, safe_path, launch_root)
    return {"action": "restored_safe_story", "source": str(restore_from), "path": str(safe_path), "ok": True}


def _repair_tts_input(
    *,
    story_dir: Path,
    launch_root: Path,
    audit: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    canonical = _canonical_tts_input_path(story_dir)
    if canonical.is_file():
        return {"action": "none", "path": str(canonical), "ok": True}
    source, kind = _pick_text_source(audit)
    if source is None or not source.is_file():
        return {
            "action": "blocked_missing_text_source",
            "ok": False,
            "path": str(canonical),
            "missing_path": str(canonical),
        }
    if not execute:
        return {
            "action": "dry_run_copy_text",
            "source": str(source),
            "source_kind": kind,
            "path": str(canonical),
            "ok": True,
        }
    canonical.parent.mkdir(parents=True, exist_ok=True)
    guarded_copy2(source, canonical, launch_root)
    return {
        "action": "copied_text_source",
        "source": str(source),
        "source_kind": kind,
        "path": str(canonical),
        "ok": True,
    }


def _pick_voice_from_info_paths(
    *,
    config: OrchestratorConfig,
    manifest: dict[str, Any],
    canonical: str,
) -> dict[str, Any]:
    settings = load_site_tts_settings(config.root_dir)
    for info_path in _candidate_info_paths(
        root=config.root_dir.resolve(),
        canonical_basename=canonical,
        manifest=manifest,
    ):
        try:
            info_text = info_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        label, _line, warn = resolve_voice_letter_from_info_content(info_text)
        if label not in {"M", "F", "U"}:
            continue
        if warn and label == "U":
            continue
        pool = settings.voice_pools.get(label) or []
        if not pool:
            continue
        raw_voice = resolve_colab_kokoro_voice_id(
            raw_voice=str(pool[0]),
            story_id=canonical,
            voice_label=label,
        )
        if not raw_voice:
            continue
        return {
            "ok": True,
            "voice_label": label,
            "kokoro_voice": raw_voice,
            "source_info_path": str(info_path),
            "source": "tts_readiness_repair_info_scan",
        }
    neutral = str(settings.kokoro_voice_neutral or "af_heart").strip()
    return {
        "ok": True,
        "voice_label": "U",
        "kokoro_voice": neutral,
        "source_info_path": "",
        "source": "tts_readiness_repair_default_u",
    }


def _repair_voice(
    *,
    config: OrchestratorConfig,
    manifest_path: Path,
    launch_root: Path,
    execute: bool,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    label, voice = _resolve_manifest_voice(manifest)
    if label and voice:
        if execute:
            manifest = _read_json(manifest_path)
            voice_block = dict(manifest.get("voice_contract") or {})
            voice_block.update(
                {
                    "voice_label": label,
                    "voice_type": label,
                    "kokoro_voice": voice,
                    "youtube_voice_id": voice,
                    "expected_gender": label,
                    "resolved_gender": label,
                    "locked": True,
                    "source": str(voice_block.get("source") or "tts_readiness_repair_manifest"),
                    "updated_at": _now_iso(),
                }
            )
            manifest["voice_contract"] = voice_block
            tts = dict(manifest.get("tts_kokoro_colab") or {})
            tts["voice_label"] = label
            tts["kokoro_voice"] = voice
            manifest["tts_kokoro_colab"] = tts
            guarded_write_json(manifest_path, manifest, launch_root)
            voice_path = manifest_path.parent / "voice_contract.json"
            guarded_write_json(voice_path, voice_block, launch_root)
        return {
            "ok": True,
            "voice_label": label,
            "kokoro_voice": voice,
            "reason_code": "",
            "action": "use_existing_manifest_voice" if execute else "dry_run_use_existing_manifest_voice",
        }

    if execute:
        result = resolve_youtube_tts_voice(
            config=config,
            manifest=manifest,
            manifest_path=manifest_path,
            write_manifest=True,
        )
    else:
        result = resolve_youtube_tts_voice(
            config=config,
            manifest=manifest,
            manifest_path=manifest_path,
            write_manifest=False,
        )
    if not result.get("ok"):
        canonical = str(manifest.get("canonical_basename") or manifest.get("story_id") or manifest_path.parent.name)
        fallback = _pick_voice_from_info_paths(config=config, manifest=manifest, canonical=canonical)
        if fallback.get("ok"):
            label = str(fallback.get("voice_label") or "U")
            voice = str(fallback.get("kokoro_voice") or "")
            if execute:
                manifest = _read_json(manifest_path)
                voice_block = {
                    "source": str(fallback.get("source") or "tts_readiness_repair_fallback"),
                    "expected_gender": label,
                    "resolved_gender": label,
                    "voice_type": label,
                    "kokoro_voice": voice,
                    "voice_label": label,
                    "site_voice_config": str(config.root_dir / "configs" / "site_tts.yaml"),
                    "source_info_path": str(fallback.get("source_info_path") or ""),
                    "youtube_voice_id": voice,
                    "voice_source": "repair_fallback",
                    "locked": True,
                    "updated_at": _now_iso(),
                }
                manifest["voice_contract"] = voice_block
                tts = dict(manifest.get("tts_kokoro_colab") or {})
                tts["voice_label"] = label
                tts["kokoro_voice"] = voice
                manifest["tts_kokoro_colab"] = tts
                guarded_write_json(manifest_path, manifest, launch_root)
                guarded_write_json(manifest_path.parent / "voice_contract.json", voice_block, launch_root)
            return {
                "ok": True,
                "voice_label": label,
                "kokoro_voice": voice,
                "reason_code": str(result.get("reason_code") or ""),
                "action": "fallback_voice_from_info_scan" if execute else "dry_run_fallback_voice_from_info_scan",
            }
    return {
        "ok": bool(result.get("ok")),
        "voice_label": result.get("voice_label"),
        "kokoro_voice": result.get("kokoro_voice"),
        "reason_code": result.get("reason_code") or result.get("message") or "",
        "action": "sync_voice_contract" if execute else "dry_run_sync_voice_contract",
    }


def _repair_manifest_tts_state(
    *,
    manifest_path: Path,
    launch_root: Path,
    text_path: Path,
    execute: bool,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    story_dir = manifest_path.parent
    now = _now_iso()
    text_hash = _sha256_file(text_path) if text_path.is_file() else ""
    promo = dict(manifest.get("promo") or {})
    if promo.get("status") in PROMO_OPTIONAL_STATUSES and text_path.is_file():
        promo.update(
            {
                "status": "done",
                "output_path": str(text_path),
                "source_path": str(story_dir / "02_safe_story" / "safe_story.txt"),
                "output_hash": text_hash,
                "updated_at": now,
                "repair_note": "tts_readiness_repair_safe_fallback",
            }
        )
    manifest["promo"] = promo
    manifest["text_ready_for_audio"] = {
        "status": "done",
        "path": str(text_path),
        "source": "tts_readiness_repair",
        "output_hash": text_hash,
        "updated_at": now,
    }
    actual = dict(manifest.get("actual_artifacts") or {})
    actual["text_ready_for_audio"] = str(text_path)
    manifest["actual_artifacts"] = actual
    pipeline = dict(manifest.get("pipeline_stage_status") or {})
    pipeline["text_ready_for_audio"] = "done"
    if promo.get("status") == "done":
        pipeline["promo"] = "done"
    manifest["pipeline_stage_status"] = pipeline
    tts = dict(manifest.get("tts_kokoro_colab") or {})
    if str(tts.get("status") or "").lower() in {"stale", "failed"}:
        tts["status"] = "pending"
        tts.pop("stale_at", None)
        tts.pop("stale_reason", None)
    tts["current_text_ready_for_audio_hash"] = text_hash
    tts.setdefault("audio_path", str(story_dir / "04_audio" / "narration.mp3"))
    manifest["tts_kokoro_colab"] = tts
    audio = dict(manifest.get("audio") or {})
    if str(audio.get("status") or "").lower() in {"stale", "failed"} and not _audio_done(
        Path(str(tts.get("audio_path") or story_dir / "04_audio" / "narration.mp3")),
        Path(""),
    ):
        audio["status"] = "pending"
        audio.pop("stale_at", None)
        audio.pop("stale_reason", None)
        manifest["audio"] = audio
    manifest["updated_at"] = now
    if not execute:
        return {"action": "dry_run_patch_manifest", "ok": True}
    guarded_write_json(manifest_path, manifest, launch_root)
    return {"action": "patched_manifest_tts_state", "ok": True}


def _repair_story(
    *,
    config: OrchestratorConfig,
    launch_root: Path,
    drive_root: Path,
    audit: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    story_dir = Path(str(audit["story_dir"]))
    manifest_path = Path(str(audit["story_manifest"]))
    actions: list[dict[str, Any]] = []
    if not audit.get("safe_story_exists"):
        safe_result = _repair_safe_story(
            config=config,
            story_dir=story_dir,
            launch_root=launch_root,
            audit=audit,
            execute=execute,
        )
        actions.append({"step": "restore_safe_story", **safe_result})
        if not safe_result.get("ok"):
            return {
                "story_id": audit.get("story_id"),
                "canonical_basename": audit.get("canonical_basename"),
                "ok": False,
                "terminal_blocked": True,
                "terminal_reason": "missing_safe_story_source",
                "terminal_missing_path": str(story_dir / "02_safe_story" / "safe_story.txt"),
                "actions": actions,
            }
        audit = _audit_story(
            config=config,
            youtube_run_id=str(audit.get("youtube_run_id") or ""),
            manifest_path=manifest_path,
            drive_root=drive_root,
        )

    text_result = _repair_tts_input(story_dir=story_dir, launch_root=launch_root, audit=audit, execute=execute)
    actions.append({"step": "repair_tts_input", **text_result})
    if not text_result.get("ok"):
        return {
            "story_id": audit.get("story_id"),
            "canonical_basename": audit.get("canonical_basename"),
            "ok": False,
            "terminal_blocked": True,
            "terminal_reason": "missing_tts_input_source",
            "terminal_missing_path": text_result.get("missing_path") or text_result.get("path"),
            "actions": actions,
        }

    voice_result = _repair_voice(
        config=config,
        manifest_path=manifest_path,
        launch_root=launch_root,
        execute=execute,
    )
    actions.append({"step": "repair_voice", **voice_result})
    if not voice_result.get("ok"):
        return {
            "story_id": audit.get("story_id"),
            "canonical_basename": audit.get("canonical_basename"),
            "ok": False,
            "terminal_blocked": True,
            "terminal_reason": str(voice_result.get("reason_code") or "missing_voice_contract"),
            "terminal_missing_path": str(story_dir / "voice_contract.json"),
            "actions": actions,
        }

    text_path = Path(str(text_result.get("path") or _canonical_tts_input_path(story_dir)))
    manifest_result = _repair_manifest_tts_state(
        manifest_path=manifest_path,
        launch_root=launch_root,
        text_path=text_path,
        execute=execute,
    )
    actions.append({"step": "patch_manifest", **manifest_result})
    return {
        "story_id": audit.get("story_id"),
        "canonical_basename": audit.get("canonical_basename"),
        "ok": True,
        "terminal_blocked": False,
        "actions": actions,
    }


def _report_dir(config: OrchestratorConfig) -> Path:
    return config.root_dir / "reports" / "gemini_execution"


def _write_audit_reports(config: OrchestratorConfig, payload: dict[str, Any]) -> None:
    root = _report_dir(config)
    _write_json(root / AUDIT_REPORT_JSON, payload)
    lines = [
        "# YouTube TTS Readiness Audit",
        "",
        f"- launch: `{payload.get('youtube_run_id')}`",
        f"- total_stories: `{payload.get('total_stories')}`",
        f"- ready: `{payload.get('ready_count')}`",
        f"- problems: `{payload.get('problem_count')}`",
        f"- audio_done: `{payload.get('audio_done_count')}`",
        "",
        "## Stories",
    ]
    for story in payload.get("stories", []) or []:
        problems = ", ".join(story.get("problems") or []) or "ok"
        lines.append(
            f"- **{story.get('canonical_basename')}**: problems={problems}; "
            f"input={story.get('expected_input_exists')}; voice={story.get('voice_ok')}; "
            f"audio_done={story.get('audio_done')}"
        )
    (root / AUDIT_REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_repair_reports(config: OrchestratorConfig, payload: dict[str, Any]) -> None:
    root = _report_dir(config)
    _write_json(root / REPAIR_REPORT_JSON, payload)
    lines = [
        "# YouTube TTS Readiness Repair",
        "",
        f"- launch: `{payload.get('youtube_run_id')}`",
        f"- execute: `{payload.get('execute')}`",
        f"- repaired_ok: `{payload.get('repaired_ok')}`",
        f"- terminal_blocked: `{payload.get('terminal_blocked_count')}`",
        "",
        "## Repairs",
    ]
    for item in payload.get("repairs", []) or []:
        status = "ok" if item.get("ok") else "blocked"
        lines.append(f"- **{item.get('canonical_basename')}**: {status}")
        if item.get("terminal_blocked"):
            lines.append(
                f"  - reason: `{item.get('terminal_reason')}` missing: `{item.get('terminal_missing_path')}`"
            )
        for action in item.get("actions", []) or []:
            lines.append(f"  - {action.get('step')}: {action.get('action')}")
    (root / REPAIR_REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_final_reports(config: OrchestratorConfig, payload: dict[str, Any]) -> None:
    root = _report_dir(config)
    _write_json(root / FINAL_REPORT_JSON, payload)
    accounting = payload.get("accounting") or {}
    lines = [
        "# YouTube TTS Job Final",
        "",
        f"- launch: `{payload.get('youtube_run_id')}`",
        f"- workers: `{payload.get('workers')}`",
        f"- job: `{payload.get('job_path')}`",
        f"- preflight_ok: `{payload.get('preflight_ok')}`",
        "",
        "## Accounting",
        f"- total_launch_stories: `{accounting.get('total_launch_stories')}`",
        f"- accounted_for_tts: `{accounting.get('accounted_for_tts')}`",
        f"- already_done: `{accounting.get('already_done')}`",
        f"- pending_for_tts: `{accounting.get('pending_for_tts')}`",
        f"- skipped_invalid: `{accounting.get('skipped_invalid')}`",
        f"- terminal_blocked: `{accounting.get('terminal_blocked')}`",
        "",
        "## Partitions",
    ]
    for part in payload.get("partitions", []) or []:
        lines.append(f"- worker_{part.get('worker_index')}: {part.get('count')}")
    if payload.get("terminal_blocked_stories"):
        lines.extend(["", "## Terminal blocked"])
        for item in payload["terminal_blocked_stories"]:
            lines.append(
                f"- **{item.get('canonical_basename')}**: `{item.get('terminal_reason')}` "
                f"missing `{item.get('terminal_missing_path')}`"
            )
    (root / FINAL_REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_tts_readiness(config: OrchestratorConfig, youtube_run_id: str) -> dict[str, Any]:
    ctx = build_launch_context(config, launch_id=youtube_run_id)
    stories: list[dict[str, Any]] = []
    if not ctx.youtube_root.is_dir():
        return {"ok": False, "youtube_run_id": youtube_run_id, "message": "launch_youtube_root_missing", "stories": []}
    for manifest_path in sorted(ctx.youtube_root.glob("*/youtube_story_manifest.json")):
        stories.append(
            _audit_story(
                config=config,
                youtube_run_id=youtube_run_id,
                manifest_path=manifest_path,
                drive_root=ctx.drive_mirror_root,
            )
        )
    ready_count = sum(1 for story in stories if not story.get("problems"))
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "launch_root": str(ctx.launch_root),
        "drive_launch_root": str(ctx.drive_mirror_root),
        "total_stories": len(stories),
        "ready_count": ready_count,
        "problem_count": len(stories) - ready_count,
        "audio_done_count": sum(1 for story in stories if story.get("audio_done")),
        "stories": stories,
        "generated_at": _now_iso(),
    }


def repair_tts_readiness(config: OrchestratorConfig, options: RepairReadinessOptions) -> dict[str, Any]:
    audit = audit_tts_readiness(config, options.youtube_run_id)
    _write_audit_reports(config, audit)
    ctx = build_launch_context(config, launch_id=options.youtube_run_id)
    repairs: list[dict[str, Any]] = []
    for story in audit.get("stories", []) or []:
        if not story.get("problems") and story.get("voice_ok") and story.get("expected_input_exists"):
            repairs.append(
                {
                    "story_id": story.get("story_id"),
                    "canonical_basename": story.get("canonical_basename"),
                    "ok": True,
                    "skipped": True,
                    "reason": "already_ready",
                    "actions": [],
                }
            )
            continue
        repairs.append(
            _repair_story(
                config=config,
                launch_root=ctx.launch_root,
                drive_root=ctx.drive_mirror_root,
                audit=story,
                execute=bool(options.execute),
            )
        )
    terminal_blocked = [item for item in repairs if item.get("terminal_blocked")]
    repair_payload = {
        "ok": not terminal_blocked,
        "execute": bool(options.execute),
        "youtube_run_id": options.youtube_run_id,
        "launch_root": str(ctx.launch_root),
        "drive_launch_root": str(ctx.drive_mirror_root),
        "repaired_ok": sum(1 for item in repairs if item.get("ok")),
        "terminal_blocked_count": len(terminal_blocked),
        "repairs": repairs,
        "generated_at": _now_iso(),
    }
    _write_repair_reports(config, repair_payload)

    post_audit = audit_tts_readiness(config, options.youtube_run_id)
    prepare_result: dict[str, Any] = {}
    preflight_result: dict[str, Any] = {}
    status_result: dict[str, Any] = {}
    if options.execute and not terminal_blocked:
        prepare_result = prepare_launch_jobs(
            config,
            PrepareLaunchJobsOptions(
                youtube_run_id=options.youtube_run_id,
                workers=options.workers,
                execute=True,
                account_all_stories=True,
            ),
        )
        preflight_result = preflight_launch_jobs(
            config,
            TtsLaunchOptions(youtube_run_id=options.youtube_run_id, workers=options.workers),
        )
        status_result = status_launch_jobs(
            config,
            TtsLaunchOptions(youtube_run_id=options.youtube_run_id, workers=options.workers),
        )
    elif not options.execute:
        prepare_result = prepare_launch_jobs(
            config,
            PrepareLaunchJobsOptions(
                youtube_run_id=options.youtube_run_id,
                workers=options.workers,
                execute=False,
                account_all_stories=True,
            ),
        )

    accounting = prepare_result.get("accounting") or {}
    final_payload = {
        "ok": bool(prepare_result.get("ok")) and not terminal_blocked,
        "execute": bool(options.execute),
        "youtube_run_id": options.youtube_run_id,
        "workers": options.workers,
        "launch_root": str(ctx.launch_root),
        "drive_launch_root": str(ctx.drive_mirror_root),
        "job_path": prepare_result.get("job_path"),
        "preflight_ok": bool(preflight_result.get("ok")) if preflight_result else None,
        "accounting": accounting,
        "partitions": prepare_result.get("partitions", []),
        "terminal_blocked_stories": terminal_blocked,
        "post_audit": {
            "total_stories": post_audit.get("total_stories"),
            "ready_count": post_audit.get("ready_count"),
            "problem_count": post_audit.get("problem_count"),
        },
        "status": status_result,
        "generated_at": _now_iso(),
    }
    _write_final_reports(config, final_payload)

    summary = {
        "ok": final_payload["ok"],
        "execute": bool(options.execute),
        "youtube_run_id": options.youtube_run_id,
        "total_launch_stories": accounting.get("total_launch_stories", audit.get("total_stories")),
        "accounted_for_tts": accounting.get("accounted_for_tts", 0),
        "already_done": accounting.get("already_done", 0),
        "pending_for_tts": accounting.get("pending_for_tts", 0),
        "skipped_invalid": accounting.get("skipped_invalid", post_audit.get("problem_count")),
        "terminal_blocked": accounting.get("terminal_blocked", len(terminal_blocked)),
        "preflight_ok": final_payload.get("preflight_ok"),
        "job_path": prepare_result.get("job_path"),
        "partition_counts": [part.get("count") for part in prepare_result.get("partitions", []) or []],
        "reports": {
            "audit_json": str(_report_dir(config) / AUDIT_REPORT_JSON),
            "audit_md": str(_report_dir(config) / AUDIT_REPORT_MD),
            "repair_json": str(_report_dir(config) / REPAIR_REPORT_JSON),
            "repair_md": str(_report_dir(config) / REPAIR_REPORT_MD),
            "final_json": str(_report_dir(config) / FINAL_REPORT_JSON),
            "final_md": str(_report_dir(config) / FINAL_REPORT_MD),
        },
    }
    return {
        "audit": audit,
        "repair": repair_payload,
        "prepare": prepare_result,
        "preflight": preflight_result,
        "status": status_result,
        "final": final_payload,
        "summary": summary,
    }
