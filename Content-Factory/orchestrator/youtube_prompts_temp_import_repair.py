"""Repair temp prompts batch outputs by validating and importing into launch story folders."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.isolated_launch_context import isolated_session
from orchestrator.launch_contract import build_launch_context
from orchestrator.youtube_path_resolver import assert_youtube_production_write_allowed
from orchestrator.youtube_visuals_bridge import (
    YoutubeDirectorPromptsImportOptions,
    _load_prompts,
    run_youtube_director_prompts_import,
)
from orchestrator.youtube_visuals_clean import validate_visual_prompts_file
from orchestrator.youtube_visuals_runner import (
    YoutubePromptsResumeAuditOptions,
    _current_prompt_checkpoint,
    _is_excluded_from_video,
    _iter_launch_story_dirs,
    _load_manifest,
    _now_iso,
    _prompt_estimate,
    _story_identity,
    _update_manifest_dict,
    _write_json,
    reconcile_visuals_progress_from_filesystem,
    run_youtube_prompts_resume_audit,
)
from orchestrator.youtube_prompts_failure_reasons import (
    PROMPTS_COUNT_TOLERANCE_ACCEPTED,
    prompt_count_acceptable,
    prompt_count_accepted_with_tolerance,
)

TEMP_PROMPTS_MISSING = "TEMP_PROMPTS_MISSING"
TEMP_PROMPTS_EMPTY = "TEMP_PROMPTS_EMPTY"
TEMP_PROMPTS_COUNT_MISMATCH = "TEMP_PROMPTS_COUNT_MISMATCH"
TEMP_PROMPTS_STALE_OR_INVALID = "TEMP_PROMPTS_STALE_OR_INVALID"
TEMP_PROMPTS_FORBIDDEN_TERMS = "TEMP_PROMPTS_FORBIDDEN_TERMS"
TEMP_PROMPTS_ADULT_AGE_NORMALIZED = "TEMP_PROMPTS_ADULT_AGE_NORMALIZED"

_SESSION_RE = re.compile(r"^\d{8}_\d{6}$")


@dataclass
class YoutubePromptsTempImportRepairOptions:
    youtube_run_id: str
    execute: bool = False
    run_session_id: str = ""
    normalize_blocked_ages: bool = False


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s_]+", " ", str(value or "").strip()).casefold()


def _story_prompt_targets(story_dir: Path) -> dict[str, Path]:
    return {
        "primary": story_dir / "06_prompts" / "prompts_list.txt",
        "legacy": story_dir / "06_director" / "prompts_list.txt",
        "primary_validation": story_dir / "06_prompts" / "prompts_validation.json",
        "legacy_validation": story_dir / "06_director" / "prompts_validation.json",
    }


def _prompt_temp_root(ctx: Any) -> Path:
    return ctx.launch_root / "10_Временные_файлы" / "visuals_gemini_batch" / "prompts"


def _prompt_session_dirs(ctx: Any, preferred_session_id: str = "") -> list[Path]:
    root = _prompt_temp_root(ctx)
    if not root.is_dir():
        return []
    sessions: list[Path] = []
    if preferred_session_id:
        preferred = root / preferred_session_id
        if preferred.is_dir():
            sessions.append(preferred)
    discovered = [
        path
        for path in root.iterdir()
        if path.is_dir() and _SESSION_RE.match(path.name)
    ]
    discovered.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    for path in discovered:
        if path not in sessions:
            sessions.append(path)
    for path in sorted(
        [p for p in root.iterdir() if p.is_dir() and p.name.startswith("worker_")],
        key=lambda item: item.name,
    ):
        if path not in sessions:
            sessions.append(path)
    return sessions


def _expected_prompts_for_story(story_dir: Path, manifest: dict[str, Any]) -> int | None:
    visual_prompts = manifest.get("visual_prompts") if isinstance(manifest.get("visual_prompts"), dict) else {}
    expected = visual_prompts.get("expected_prompts")
    if expected not in (None, ""):
        try:
            return int(expected)
        except (TypeError, ValueError):
            return None
    return _prompt_estimate(story_dir)


def _materialize_complete_partial(stage_dir: Path) -> Path | None:
    final_path = stage_dir / "prompts_list.txt"
    partial_path = stage_dir / "prompts_list.partial.txt"
    checkpoint_path = stage_dir / "director_checkpoint.json"
    checkpoint = _current_prompt_checkpoint(stage_dir)
    try:
        total_chunks = int(checkpoint.get("total_chunks") or 0)
        next_chunk_index = int(checkpoint.get("current_chunk") or 0)
    except (TypeError, ValueError):
        return None
    if final_path.is_file():
        if total_chunks > 0 and next_chunk_index >= total_chunks:
            partial_path.unlink(missing_ok=True)
            checkpoint_path.unlink(missing_ok=True)
        return final_path
    if not partial_path.is_file() or total_chunks <= 0 or next_chunk_index < total_chunks:
        return None
    raw = partial_path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return None
    temp_path = final_path.with_suffix(".txt.tmp")
    temp_path.write_text(raw, encoding="utf-8")
    temp_path.replace(final_path)
    partial_path.unlink(missing_ok=True)
    checkpoint_path.unlink(missing_ok=True)
    return final_path


def _candidate_prompt_files(session_dir: Path, *, materialize_complete_partials: bool = False) -> list[Path]:
    if materialize_complete_partials:
        worker_dirs = [session_dir] if session_dir.name.startswith("worker_") else [
            path for path in session_dir.iterdir() if path.is_dir() and path.name.startswith("worker_")
        ]
        for worker_dir in worker_dirs:
            for stage_dir in worker_dir.iterdir():
                if stage_dir.is_dir():
                    _materialize_complete_partial(stage_dir)
    candidates: list[Path] = []
    if session_dir.name.startswith("worker_"):
        candidates.extend(path for path in session_dir.glob("*\\prompts_list.txt") if path.is_file())
    else:
        candidates.extend(path for path in session_dir.glob("worker_*\\*\\prompts_list.txt") if path.is_file())
    return sorted(
        candidates,
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )


def _canonical_prompts_ready(story_dir: Path, story_id: str, expected_count: int | None) -> tuple[bool, Path, dict[str, Any], int]:
    targets = _story_prompt_targets(story_dir)
    prompts_path = targets["primary"] if targets["primary"].is_file() else targets["legacy"]
    validation = validate_visual_prompts_file(prompts_path)
    actual = int(validation.get("prompts_count") or len(_load_prompts(prompts_path)) or 0)
    if not validation.get("ok", False):
        return False, prompts_path, validation, actual
    if not prompt_count_acceptable(expected_count, actual):
        validation = dict(validation)
        validation["status"] = "count_mismatch"
        return False, prompts_path, validation, actual
    return True, prompts_path, validation, actual


def _build_temp_index(
    ctx: Any,
    preferred_session_id: str = "",
    *,
    materialize_complete_partials: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    sessions = _prompt_session_dirs(ctx, preferred_session_id=preferred_session_id)
    index: dict[str, list[dict[str, Any]]] = {}
    session_rows: list[dict[str, Any]] = []
    for session_dir in sessions:
        files = _candidate_prompt_files(
            session_dir,
            materialize_complete_partials=materialize_complete_partials,
        )
        session_rows.append(
            {
                "session_id": session_dir.name,
                "path": str(session_dir),
                "prompts_files": len(files),
                "latest_mtime": max((path.stat().st_mtime for path in files), default=0),
            }
        )
        for file_path in files:
            story_folder = file_path.parent.name
            key = _normalize_key(story_folder)
            stage_dir = file_path.parent
            index.setdefault(key, []).append(
                {
                    "session_id": session_dir.name,
                    "session_dir": session_dir,
                    "path": file_path,
                    "story_folder": story_folder,
                    "worker": file_path.parent.parent.name if file_path.parent.parent else "",
                    "checkpoint": _current_prompt_checkpoint(stage_dir),
                    "mtime": file_path.stat().st_mtime if file_path.exists() else 0,
                }
            )
    for rows in index.values():
        rows.sort(key=lambda row: (row["session_id"], row["mtime"]), reverse=True)
    return session_rows, index


def _story_match_ok(story_dir: Path, manifest: dict[str, Any], temp_story_folder: str) -> bool:
    story_id, title = _story_identity(story_dir, manifest)
    aliases = {
        _normalize_key(story_id),
        _normalize_key(title),
        _normalize_key(story_dir.name),
        _normalize_key(str(manifest.get("canonical_basename") or "")),
    }
    return _normalize_key(temp_story_folder) in aliases


def _adult_age_normalized_copy(path: Path) -> Path | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    replacements = (
        (r"\bteenage girls\b", "adult women in their early 20s"),
        (r"\bteenage boys\b", "adult men in their early 20s"),
        (r"\bteenage daughters?\b", "adult daughters in their early 20s"),
        (r"\bteenage sons?\b", "adult sons in their early 20s"),
        (r"\bteenagers?\b", "young adults in their early 20s"),
        (r"\bteens\b", "young adults in their early 20s"),
        (r"\bteenage\b", "adult"),
        (r"\bteen\b", "young adult in their early 20s"),
    )
    normalized = raw
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    if normalized == raw:
        return None
    target = path.with_name("prompts_list.adult_safe.txt")
    target.write_text(normalized, encoding="utf-8")
    return target


def _temp_prompt_validation(
    path: Path,
    *,
    expected_count: int | None,
    normalize_blocked_ages: bool = False,
) -> dict[str, Any]:
    if not path.is_file():
        return {
            "ok": False,
            "reason": TEMP_PROMPTS_MISSING,
            "actual_count": 0,
            "validation": {"ok": False, "status": "missing", "path": str(path)},
        }
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return {
            "ok": False,
            "reason": TEMP_PROMPTS_EMPTY,
            "actual_count": 0,
            "validation": {"ok": False, "status": "empty", "path": str(path)},
        }
    validation = validate_visual_prompts_file(path)
    actual = int(validation.get("prompts_count") or len(_load_prompts(path)) or 0)
    if int(validation.get("forbidden_terms_total") or 0) > 0:
        forbidden_terms = {
            str(term).casefold()
            for finding in validation.get("findings") or []
            for term in finding.get("terms") or []
        }
        if normalize_blocked_ages and forbidden_terms and forbidden_terms <= {"teen", "teenage"}:
            normalized_path = _adult_age_normalized_copy(path)
            normalized_validation = validate_visual_prompts_file(normalized_path) if normalized_path else validation
            normalized_actual = int(
                normalized_validation.get("prompts_count")
                or (len(_load_prompts(normalized_path)) if normalized_path else 0)
                or 0
            )
            if normalized_path and normalized_validation.get("ok", False) and normalized_actual == actual:
                validation = dict(normalized_validation)
                validation["status"] = TEMP_PROMPTS_ADULT_AGE_NORMALIZED
                validation["original_path"] = str(path)
                path = normalized_path
            else:
                return {
                    "ok": False,
                    "reason": TEMP_PROMPTS_FORBIDDEN_TERMS,
                    "actual_count": actual,
                    "validation": normalized_validation,
                }
        else:
            return {
                "ok": False,
                "reason": TEMP_PROMPTS_FORBIDDEN_TERMS,
                "actual_count": actual,
                "validation": validation,
            }
    if not validation.get("ok", False):
        return {
            "ok": False,
            "reason": TEMP_PROMPTS_STALE_OR_INVALID,
            "actual_count": actual,
            "validation": validation,
        }
    accepted_with_tolerance = prompt_count_accepted_with_tolerance(expected_count, actual)
    if not prompt_count_acceptable(expected_count, actual):
        return {
            "ok": False,
            "reason": TEMP_PROMPTS_COUNT_MISMATCH,
            "actual_count": actual,
            "validation": validation,
        }
    if accepted_with_tolerance:
        validation = dict(validation)
        validation["status"] = PROMPTS_COUNT_TOLERANCE_ACCEPTED
        validation["expected_count_original"] = expected_count
        validation["effective_expected_count"] = actual
    return {
        "ok": True,
        "reason": PROMPTS_COUNT_TOLERANCE_ACCEPTED if accepted_with_tolerance else "",
        "actual_count": actual,
        "effective_expected_count": actual if accepted_with_tolerance else expected_count,
        "validated_path": str(path),
        "validation": validation,
    }


def _import_valid_temp_prompts(
    *,
    config: OrchestratorConfig,
    launch_id: str,
    story_id: str,
    story_dir: Path,
    temp_path: Path,
    expected_count: int | None,
    actual_count: int,
    validation: dict[str, Any],
) -> dict[str, Any]:
    targets = _story_prompt_targets(story_dir)
    import_result = run_youtube_director_prompts_import(
        config=config,
        options=YoutubeDirectorPromptsImportOptions(story_id=story_id, source=temp_path, execute=True),
    )
    if not import_result.get("ok", False):
        return {
            "ok": False,
            "action": "rejected",
            "reason": TEMP_PROMPTS_STALE_OR_INVALID,
            "import_result": import_result,
            "canonical_primary_path": str(targets["primary"]),
            "canonical_legacy_path": str(targets["legacy"]),
        }

    legacy_target = assert_youtube_production_write_allowed(
        config,
        targets["legacy"],
        youtube_run_id=launch_id,
        module="orchestrator.youtube_prompts_temp_import_repair",
        function="_import_valid_temp_prompts",
    )
    legacy_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(temp_path, legacy_target)
    metadata = {
        "status": "ok",
        "source": str(temp_path),
        "expected_count": expected_count,
        "actual_count": actual_count,
        "validation": validation,
        "imported_at": _now_iso(),
        "via": "prompts_temp_import_repair",
    }
    for key in ("primary_validation", "legacy_validation"):
        meta_path = assert_youtube_production_write_allowed(
            config,
            targets[key],
            youtube_run_id=launch_id,
            module="orchestrator.youtube_prompts_temp_import_repair",
            function="_import_valid_temp_prompts",
        )
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    _update_manifest_dict(
        story_dir,
        {
            "status": {"director_done": True},
            "pipeline_stage_status": {"scenes_prompts": "done", "director_prompts": "done"},
            "actual_artifacts": {
                "prompts_list_txt": str(targets["primary"]),
                "prompts_list_txt_legacy": str(targets["legacy"]),
            },
            "visual_prompts": {
                "status": "done",
                "updated_at": _now_iso(),
                "completed_at": _now_iso(),
                "attempts": 1,
                "worker_id": None,
                "expected_prompts": expected_count if expected_count not in (None, 0) else actual_count,
                "actual_prompts": actual_count,
                "validation": "ok",
                "error": None,
                "path": str(targets["primary"]),
                "legacy_path": str(targets["legacy"]),
                "source": str(temp_path),
                "repair_imported": True,
            },
            "scenes_prompts": {
                "status": "done",
                "path": str(targets["primary"]),
                "legacy_path": str(targets["legacy"]),
                "source": str(temp_path),
                "prompts_count": actual_count,
                "validation": "ok",
                "imported_at": _now_iso(),
            },
            "director_prompts": {
                "status": "done",
                "path": str(targets["legacy"]),
                "primary_path": str(targets["primary"]),
                "source": str(temp_path),
                "prompts_count": actual_count,
                "validation": "ok",
                "imported_at": _now_iso(),
                "repair_imported": True,
            },
        },
    )
    return {
        "ok": True,
        "action": "imported",
        "reason": "",
        "canonical_primary_path": str(targets["primary"]),
        "canonical_legacy_path": str(targets["legacy"]),
        "import_result": import_result,
    }


def _repair_story_from_temp(
    *,
    config: OrchestratorConfig,
    ctx: Any,
    launch_id: str,
    story_dir: Path,
    temp_index: dict[str, list[dict[str, Any]]],
    execute: bool,
    normalize_blocked_ages: bool = False,
) -> dict[str, Any]:
    manifest = _load_manifest(story_dir)
    story_id, title = _story_identity(story_dir, manifest)
    expected = _expected_prompts_for_story(story_dir, manifest)
    targets = _story_prompt_targets(story_dir)
    if _is_excluded_from_video(manifest):
        return {
            "ok": True,
            "story": story_id,
            "title": title,
            "temp_path": None,
            "canonical_path": str(targets["legacy"]),
            "primary_canonical_path": str(targets["primary"]),
            "expected": expected,
            "actual": 0,
            "validation": "excluded",
            "action": "excluded",
            "final_status": "excluded",
            "reason": "excluded_from_video",
            "session_id": "",
            "candidates_checked": [],
        }
    canonical_ready, canonical_path, canonical_validation, canonical_actual = _canonical_prompts_ready(
        story_dir,
        story_id,
        expected,
    )
    if canonical_ready:
        return {
            "ok": True,
            "story": story_id,
            "title": title,
            "temp_path": None,
            "canonical_path": str(targets["legacy"]),
            "primary_canonical_path": str(canonical_path),
            "expected": expected,
            "actual": canonical_actual,
            "validation": str(canonical_validation.get("status") or "ok"),
            "action": "already_ready",
            "final_status": "done",
            "reason": "",
            "session_id": "",
            "candidates_checked": [],
        }
    aliases = [
        _normalize_key(story_id),
        _normalize_key(title),
        _normalize_key(story_dir.name),
        _normalize_key(str(manifest.get("canonical_basename") or "")),
    ]
    candidates: list[dict[str, Any]] = []
    for alias in aliases:
        candidates.extend(temp_index.get(alias, []))
    dedup: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in candidates:
        key = str(row["path"]).lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        dedup.append(row)
    candidates = sorted(dedup, key=lambda row: (row["session_id"], row["mtime"]), reverse=True)

    row: dict[str, Any] = {
        "ok": False,
        "story": story_id,
        "title": title,
        "temp_path": None,
        "canonical_path": str(targets["legacy"]),
        "primary_canonical_path": str(targets["primary"]),
        "expected": expected,
        "actual": 0,
        "validation": "missing",
        "action": "rejected",
        "final_status": "pending",
        "reason": TEMP_PROMPTS_MISSING,
        "session_id": "",
        "candidates_checked": [],
    }

    if not candidates:
        return row

    for candidate in candidates:
        temp_path = Path(candidate["path"])
        candidate_row = {
            "session_id": candidate["session_id"],
            "temp_path": str(temp_path),
            "worker": candidate.get("worker", ""),
        }
        if not _story_match_ok(story_dir, manifest, str(candidate.get("story_folder") or temp_path.parent.name)):
            candidate_row["validation"] = "story_mismatch"
            candidate_row["reason"] = TEMP_PROMPTS_STALE_OR_INVALID
            row["candidates_checked"].append(candidate_row)
            continue
        checked = _temp_prompt_validation(
            temp_path,
            expected_count=expected,
            normalize_blocked_ages=bool(normalize_blocked_ages and execute),
        )
        candidate_row["validation"] = checked["validation"].get("status", "")
        candidate_row["reason"] = checked["reason"] or ""
        candidate_row["actual"] = checked["actual_count"]
        row["candidates_checked"].append(candidate_row)
        if not checked["ok"]:
            row.update(
                {
                    "temp_path": str(temp_path),
                    "actual": checked["actual_count"],
                    "validation": checked["validation"].get("status", "invalid"),
                    "reason": checked["reason"],
                    "session_id": candidate["session_id"],
                    "final_status": "failed" if checked["reason"] in {TEMP_PROMPTS_STALE_OR_INVALID, TEMP_PROMPTS_FORBIDDEN_TERMS, TEMP_PROMPTS_COUNT_MISMATCH} else "pending",
                }
            )
            continue

        row.update(
            {
                "temp_path": str(temp_path),
                "actual": checked["actual_count"],
                "validation": "ok",
                "session_id": candidate["session_id"],
            }
        )
        if not execute:
            row.update({"ok": True, "action": "would_import", "reason": "", "final_status": "done"})
            return row

        validated_temp_path = Path(str(checked.get("validated_path") or temp_path))
        imported = _import_valid_temp_prompts(
            config=config,
            launch_id=launch_id,
            story_id=story_id,
            story_dir=story_dir,
            temp_path=validated_temp_path,
            expected_count=checked.get("effective_expected_count", expected),
            actual_count=checked["actual_count"],
            validation=checked["validation"],
        )
        row.update(
            {
                "ok": bool(imported.get("ok")),
                "action": str(imported.get("action") or "rejected"),
                "reason": str(imported.get("reason") or ""),
                "final_status": "done" if imported.get("ok") else "failed",
                "canonical_path": str(imported.get("canonical_legacy_path") or row["canonical_path"]),
                "primary_canonical_path": str(imported.get("canonical_primary_path") or row["primary_canonical_path"]),
            }
        )
        return row

    return row


def _write_repair_reports(config: OrchestratorConfig, launch_id: str, payload: dict[str, Any]) -> dict[str, str]:
    ctx = build_launch_context(config, launch_id=launch_id)
    reports_dir = ctx.launch_root / "07_reports" / "gemini_execution"
    json_path = reports_dir / "YOUTUBE_PROMPTS_TEMP_IMPORT_REPAIR.json"
    md_path = reports_dir / "YOUTUBE_PROMPTS_TEMP_IMPORT_REPAIR.md"
    _write_json(json_path, payload)
    lines = [
        "# YOUTUBE_PROMPTS_TEMP_IMPORT_REPAIR",
        "",
        f"- launch_id: {launch_id}",
        f"- execute: {str(bool(payload.get('execute'))).lower()}",
        f"- imported: {payload.get('imported_count', 0)}",
        f"- rejected: {payload.get('rejected_count', 0)}",
        "",
        "## Temp sessions found",
        "",
    ]
    for row in payload.get("temp_sessions_found", []):
        lines.append(f"- `{row.get('session_id')}` -> `{row.get('path')}` files={row.get('prompts_files')}")
    lines.extend(
        [
            "",
            "| story | temp_path | canonical_path | expected | actual | validation | action | final_status | reason |",
            "|---|---|---|---:|---:|---|---|---|---|",
        ]
    )
    for row in payload.get("stories", []):
        lines.append(
            "| "
            + " | ".join(
                str(row.get(key, "")).replace("|", "\\|")
                for key in ("story", "temp_path", "canonical_path", "expected", "actual", "validation", "action", "final_status", "reason")
            )
            + " |"
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def run_youtube_prompts_temp_import_repair(
    *,
    config: OrchestratorConfig,
    options: YoutubePromptsTempImportRepairOptions,
) -> dict[str, Any]:
    launch_id = str(options.youtube_run_id or "").strip()
    if not launch_id:
        return {"ok": False, "message": "--youtube-run-id is required"}

    ctx = build_launch_context(config, launch_id=launch_id)
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        temp_sessions_found, temp_index = _build_temp_index(
            ctx,
            preferred_session_id=str(options.run_session_id or ""),
            materialize_complete_partials=bool(options.execute),
        )
        story_rows = [
            _repair_story_from_temp(
                config=config,
                ctx=ctx,
                launch_id=launch_id,
                story_dir=story_dir,
                temp_index=temp_index,
                execute=bool(options.execute),
                normalize_blocked_ages=bool(options.normalize_blocked_ages),
            )
            for story_dir in _iter_launch_story_dirs(config, launch_id)
        ]
        progress = reconcile_visuals_progress_from_filesystem(config=config, launch_id=launch_id)
        readiness = run_youtube_prompts_resume_audit(
            config=config,
            options=YoutubePromptsResumeAuditOptions(youtube_run_id=launch_id),
        )

    active_story_rows = [row for row in story_rows if row.get("final_status") != "excluded"]
    imported_count = sum(1 for row in active_story_rows if row.get("action") == "imported")
    rejected_count = sum(1 for row in active_story_rows if row.get("action") == "rejected")
    final_done = sum(1 for row in active_story_rows if row.get("final_status") == "done")
    final_pending = sum(1 for row in active_story_rows if row.get("final_status") == "pending")
    final_failed = sum(1 for row in active_story_rows if row.get("final_status") == "failed")
    readiness_summary = {
        "ready_for_runpod": final_done,
        "blocked": 0,
        "pending": final_pending,
        "failed": final_failed,
        "next_stage_allowed": bool(final_pending == 0 and final_failed == 0),
    }
    payload = {
        "ok": True,
        "youtube_run_id": launch_id,
        "execute": bool(options.execute),
        "generated_at": _now_iso(),
        "temp_sessions_found": temp_sessions_found,
        "stories": story_rows,
        "imported_count": imported_count,
        "rejected_count": rejected_count,
        "progress": progress,
        "final_readiness": readiness_summary,
    }
    payload["reports"] = _write_repair_reports(config, launch_id, payload)
    return payload
