"""Exact failure reason codes for YouTube visual prompts pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

NO_TEMP_OUTPUT = "NO_TEMP_OUTPUT"
GEMINI_NO_RESPONSE = "GEMINI_NO_RESPONSE"
TEMP_IMPORT_FAILED = "TEMP_IMPORT_FAILED"
CANONICAL_COMMIT_FAILED = "CANONICAL_COMMIT_FAILED"
VALIDATION_MISSING = "VALIDATION_MISSING"
PROMPTS_GENERATION_INCOMPLETE = "PROMPTS_GENERATION_INCOMPLETE"
UNKNOWN_PROMPTS_FAILURE = "UNKNOWN_PROMPTS_FAILURE"

_SESSION_RE = re.compile(r"^\d{8}_\d{6}$")
_VAGUE_FAILURES = frozenset({"", "failed", "none", "null", "unknown"})


def normalize_failure_reason(value: str | None, *, fallback: str = UNKNOWN_PROMPTS_FAILURE) -> str:
    reason = str(value or "").strip()
    if reason.casefold() in _VAGUE_FAILURES:
        return fallback
    return reason


def _stage_artifact_flags(stage_dir: Path) -> dict[str, Any]:
    partial = stage_dir / "prompts_list.partial.txt"
    final = stage_dir / "prompts_list.txt"
    checkpoint = stage_dir / "director_checkpoint.json"
    staged = stage_dir / "ORCHESTRATOR_STAGED.json"
    raw_responses = sorted(stage_dir.glob("*.raw_response.txt"))
    chunk_files = sorted(stage_dir.glob("chunk_*.txt"))
    browser_logs = sorted(stage_dir.glob("*.log"))
    return {
        "stage_dir": str(stage_dir),
        "final_exists": final.is_file(),
        "partial_exists": partial.is_file(),
        "checkpoint_exists": checkpoint.is_file(),
        "staged_marker_exists": staged.is_file(),
        "raw_response_count": len(raw_responses),
        "raw_response_paths": [str(path) for path in raw_responses[:5]],
        "chunk_file_count": len(chunk_files),
        "browser_log_count": len(browser_logs),
        "has_any_output": final.is_file() or partial.is_file() or bool(raw_responses) or bool(chunk_files),
        "has_browser_inputs": any(
            (stage_dir / name).is_file()
            for name in ("story.txt", "characters.txt", "narration.mp3", "ORCHESTRATOR_STAGED.json")
        ),
    }


def classify_stage_prompts_failure(*, stage_dir: Path | None, canonical_ready: bool = False) -> str:
    if canonical_ready:
        return "ALREADY_READY"
    if stage_dir is None or not stage_dir.is_dir():
        return VALIDATION_MISSING
    flags = _stage_artifact_flags(stage_dir)
    if flags["final_exists"]:
        return TEMP_IMPORT_FAILED
    if flags["partial_exists"] or flags["checkpoint_exists"]:
        return PROMPTS_GENERATION_INCOMPLETE
    if flags["staged_marker_exists"] or flags["raw_response_count"] or flags["chunk_file_count"]:
        return GEMINI_NO_RESPONSE
    if flags["has_browser_inputs"]:
        return GEMINI_NO_RESPONSE
    return NO_TEMP_OUTPUT


def discover_story_stage_dirs(temp_root: Path, story_id: str) -> list[Path]:
    if not temp_root.is_dir():
        return []
    found: list[Path] = []
    story_key = story_id.casefold()
    for path in temp_root.rglob("*"):
        if not path.is_dir():
            continue
        if path.name.casefold() != story_key:
            continue
        if not path.parent.name.startswith("worker_"):
            continue
        found.append(path)
    found.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def build_story_prompts_forensic(
    *,
    temp_root: Path,
    story_id: str,
    assigned_worker: str = "",
    preferred_session_id: str = "",
    canonical_ready: bool = False,
    canonical_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    stage_dirs = discover_story_stage_dirs(temp_root, story_id)
    preferred_dirs = [
        path
        for path in stage_dirs
        if preferred_session_id and preferred_session_id in str(path)
    ]
    ranked = preferred_dirs + [path for path in stage_dirs if path not in preferred_dirs]
    best_stage = ranked[0] if ranked else None
    session_hits = sorted(
        {
            path.parts[path.parts.index("prompts") + 1]
            for path in stage_dirs
            if "prompts" in path.parts and _SESSION_RE.match(path.parts[path.parts.index("prompts") + 1])
        }
    )
    stage_rows = [_stage_artifact_flags(path) for path in ranked[:8]]
    reason = classify_stage_prompts_failure(stage_dir=best_stage, canonical_ready=canonical_ready)
    if canonical_ready:
        exact_reason = "ALREADY_READY"
    elif not (canonical_paths or {}).get("primary_exists") and not (canonical_paths or {}).get("legacy_exists"):
        exact_reason = VALIDATION_MISSING if reason == VALIDATION_MISSING else reason
    else:
        exact_reason = reason
    return {
        "story_id": story_id,
        "assigned_worker": assigned_worker,
        "preferred_session_id": preferred_session_id,
        "sessions_with_stage_dir": session_hits,
        "best_stage_dir": str(best_stage) if best_stage else "",
        "stage_artifacts": stage_rows,
        "canonical": canonical_paths or {},
        "exact_reason": exact_reason,
        "browser_output_received": any(row.get("has_any_output") for row in stage_rows),
        "raw_gemini_response_found": any(int(row.get("raw_response_count") or 0) > 0 for row in stage_rows),
        "temp_prompts_list_found": any(row.get("final_exists") for row in stage_rows),
        "temp_partial_found": any(row.get("partial_exists") for row in stage_rows),
    }
