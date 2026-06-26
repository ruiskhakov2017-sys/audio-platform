"""Exact failure reason codes for YouTube visual prompts pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

NO_TEMP_OUTPUT = "NO_TEMP_OUTPUT"
GEMINI_NO_RESPONSE = "GEMINI_NO_RESPONSE"
GEMINI_RESPONSE_NOT_SAVED = "GEMINI_RESPONSE_NOT_SAVED"
RAW_RESPONSE_MISSING_BUT_CHUNK_PARSED = "RAW_RESPONSE_MISSING_BUT_CHUNK_PARSED"
TEMP_IMPORT_FAILED = "TEMP_IMPORT_FAILED"
CANONICAL_COMMIT_FAILED = "CANONICAL_COMMIT_FAILED"
CANONICAL_COMMIT_SKIPPED = "CANONICAL_COMMIT_SKIPPED"
VALIDATION_MISSING = "VALIDATION_MISSING"
PROMPTS_GENERATION_INCOMPLETE = "PROMPTS_GENERATION_INCOMPLETE"
PARTIAL_CHECKPOINT_CREATED_NO_FINAL_COMMIT = "PARTIAL_CHECKPOINT_CREATED_NO_FINAL_COMMIT"
FINAL_PROMPTS_NOT_BUILT_FROM_VALID_PARTIAL = "FINAL_PROMPTS_NOT_BUILT_FROM_VALID_PARTIAL"
TEMP_VALIDATION_FAILED = "TEMP_VALIDATION_FAILED"
STATUS_UPDATED_WITHOUT_ARTIFACT = "STATUS_UPDATED_WITHOUT_ARTIFACT"
ARTIFACT_EXISTS_STATUS_NOT_UPDATED = "ARTIFACT_EXISTS_STATUS_NOT_UPDATED"
STAGED_MARKER_MISSING = "STAGED_MARKER_MISSING"
PROCESSED_MARKER_SKIPPED = "PROCESSED_MARKER_SKIPPED"
COUNT_MISMATCH = "COUNT_MISMATCH"
FORBIDDEN_TERMS = "FORBIDDEN_TERMS"
GEM_BOT_DELETED = "GEM_BOT_DELETED"
BROWSER_SESSION_DIED = "BROWSER_SESSION_DIED"
WORKER_RUNTIME_UI_BLOCKED = "WORKER_RUNTIME_UI_BLOCKED"
GEMINI_UI_OVERLAY_BLOCKED = "GEMINI_UI_OVERLAY_BLOCKED"
GEMINI_GENERATION_TIMEOUT = "GEMINI_GENERATION_TIMEOUT"
GEMINI_INPUT_CLICK_BLOCKED = "GEMINI_INPUT_CLICK_BLOCKED"
GEMINI_BROWSER_STUCK = "GEMINI_BROWSER_STUCK"
GEMINI_UI_NOT_READY = "GEMINI_UI_NOT_READY"
GEMINI_RATE_LIMIT = "GEMINI_RATE_LIMIT"
RUNTIME_UNHEALTHY = "runtime_unhealthy"
NO_HEALTHY_GEMINI_WORKERS_AFTER_RUNTIME_QUARANTINE = "NO_HEALTHY_GEMINI_WORKERS_AFTER_RUNTIME_QUARANTINE"
WORKER_THREAD_EXITED = "WORKER_THREAD_EXITED"
SUPERVISOR_STOPPED_EARLY = "SUPERVISOR_STOPPED_EARLY"
QUEUE_DRAINED_WHILE_IN_PROGRESS = "QUEUE_DRAINED_WHILE_IN_PROGRESS"
WORKER_EXITED_BEFORE_TERMINAL_OUTCOME = "WORKER_EXITED_BEFORE_TERMINAL_OUTCOME"
ASSIGNED_BUT_IDLE_STATE_BUG = "ASSIGNED_BUT_IDLE_STATE_BUG"
OK_TRUE_WITH_TERMINAL_FAILED = "OK_TRUE_WITH_TERMINAL_FAILED"
DONE_WITHOUT_CANONICAL_ARTIFACT = "DONE_WITHOUT_CANONICAL_ARTIFACT"
TEMP_ONLY_READY_BUG = "TEMP_ONLY_READY_BUG"
INVALID_CHECKPOINT_CLEAN_RERUN = "INVALID_CHECKPOINT_CLEAN_RERUN"
UNKNOWN_PROMPTS_FAILURE = "UNKNOWN_PROMPTS_FAILURE"
PROMPTS_COUNT_TOLERANCE_ACCEPTED = "PROMPTS_COUNT_TOLERANCE_ACCEPTED"

_COUNT_TOLERANCE_MIN_EXPECTED = 20
_COUNT_TOLERANCE_MAX_ABSOLUTE_MISSING = 2
_COUNT_TOLERANCE_MAX_RATIO = 0.03

_SESSION_RE = re.compile(r"^\d{8}_\d{6}$")
_VAGUE_FAILURES = frozenset({"", "failed", "none", "null", "unknown"})
TERMINAL_FAILURE_STATUSES = frozenset({"failed", "terminal_failed", "blocked"})
NON_TERMINAL_STATUSES = frozenset({"queued", "assigned", "pending", "in_progress", "partial"})
ALLOWED_PROMPTS_WORKER_STATES = frozenset(
    {
        "queued",
        "assigned",
        "browser_starting",
        "waiting_input",
        "submitted",
        "waiting_response",
        "response_seen",
        "parsing_response",
        "checkpoint_saved",
        "partial_saved",
        "finalizing",
        "committing",
        "done",
        "terminal_failed",
        "blocked",
    }
)


def normalize_failure_reason(value: str | None, *, fallback: str = UNKNOWN_PROMPTS_FAILURE) -> str:
    reason = str(value or "").strip()
    if reason.casefold() in _VAGUE_FAILURES:
        return fallback
    blob = reason.casefold()
    if (
        "gem_bot_deleted" in blob
        or ("gem bot" in blob and "deleted" in blob)
        or ("gem-бот" in blob and ("удален" in blob or "удалён" in blob))
        or ("создайте другого gem" in blob)
        or ("начните новый чат" in blob)
    ):
        return GEM_BOT_DELETED
    if "overlay intercepts pointer events" in blob or ("overlay" in blob and "pointer events" in blob):
        return GEMINI_UI_OVERLAY_BLOCKED
    if "generation did not finish in time" in blob:
        return GEMINI_GENERATION_TIMEOUT
    if "input click blocked" in blob:
        return GEMINI_INPUT_CLICK_BLOCKED
    if "browser has been closed" in blob or "target page, context or browser has been closed" in blob:
        return GEMINI_BROWSER_STUCK
    if "gemini ui not ready" in blob or "prompt input not found" in blob:
        return GEMINI_UI_NOT_READY
    if "geminilimiterror" in blob or "gemini rate limit reached" in blob:
        return GEMINI_RATE_LIMIT
    if "no_healthy_gemini_workers_after_runtime_quarantine" in blob:
        return NO_HEALTHY_GEMINI_WORKERS_AFTER_RUNTIME_QUARANTINE
    return reason


def is_exact_reason(value: str | None) -> bool:
    return normalize_failure_reason(value, fallback="") != ""


def _read_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"invalid": True}
    return data if isinstance(data, dict) else {"invalid": True}


def _count_prompts_file(path: Path) -> int:
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return 0
    return len([part for part in re.split(r"\n\s*\n", text) if part.strip()])


def _stage_artifact_flags(stage_dir: Path) -> dict[str, Any]:
    partial = stage_dir / "prompts_list.partial.txt"
    final = stage_dir / "prompts_list.txt"
    checkpoint = stage_dir / "director_checkpoint.json"
    staged = stage_dir / "ORCHESTRATOR_STAGED.json"
    processed = stage_dir / "ORCHESTRATOR_PROCESSED.json"
    raw_responses = sorted(
        path
        for pattern in ("*.raw_response.txt", "*.raw_response.*", "raw_response.txt", "chunk_*_raw.txt")
        for path in stage_dir.glob(pattern)
        if path.is_file()
    )
    chunk_files = sorted(
        path
        for pattern in ("chunk_*.txt", "chunk_*_response.txt", "chunk_*_parsed.txt")
        for path in stage_dir.glob(pattern)
        if path.is_file()
    )
    browser_logs = sorted(stage_dir.glob("*.log"))
    checkpoint_data = _read_checkpoint(checkpoint)
    current_chunk = int(checkpoint_data.get("next_chunk_index") or 0) if not checkpoint_data.get("invalid") else 0
    total_chunks = int(checkpoint_data.get("total_chunks") or 0) if not checkpoint_data.get("invalid") else 0
    partial_prompts = _count_prompts_file(partial)
    final_prompts = _count_prompts_file(final)
    parsed_chunks_exist = bool(current_chunk > 0 or partial_prompts > 0 or chunk_files)
    return {
        "stage_dir": str(stage_dir),
        "final_exists": final.is_file(),
        "partial_exists": partial.is_file(),
        "checkpoint_exists": checkpoint.is_file(),
        "checkpoint_invalid": bool(checkpoint_data.get("invalid")),
        "current_chunk": current_chunk,
        "total_chunks": total_chunks,
        "last_successful_chunk": max(0, current_chunk),
        "partial_prompts": partial_prompts,
        "final_prompts": final_prompts,
        "staged_marker_exists": staged.is_file(),
        "processed_marker_exists": processed.is_file(),
        "raw_response_count": len(raw_responses),
        "raw_response_paths": [str(path) for path in raw_responses[:5]],
        "chunk_file_count": len(chunk_files),
        "browser_log_count": len(browser_logs),
        "has_any_output": final.is_file() or partial.is_file() or bool(raw_responses) or bool(chunk_files),
        "has_browser_inputs": any(
            (stage_dir / name).is_file()
            for name in ("story.txt", "characters.txt", "narration.mp3", "ORCHESTRATOR_STAGED.json")
        ),
        "parsed_chunks_exist": parsed_chunks_exist,
        "raw_response_missing_but_chunk_parsed": parsed_chunks_exist and not raw_responses,
        "resume_action": checkpoint_resume_action(
            checkpoint_invalid=bool(checkpoint_data.get("invalid")),
            partial_exists=partial.is_file(),
            current_chunk=current_chunk,
            total_chunks=total_chunks,
        ),
    }


def prompt_count_acceptable(expected_count: int | None, actual_count: int) -> bool:
    actual = int(actual_count or 0)
    if actual <= 0:
        return False
    if expected_count in (None, 0):
        return True
    expected = int(expected_count or 0)
    if actual == expected:
        return True
    if actual > expected or expected < _COUNT_TOLERANCE_MIN_EXPECTED:
        return False
    missing = expected - actual
    allowed_missing = max(_COUNT_TOLERANCE_MAX_ABSOLUTE_MISSING, int(expected * _COUNT_TOLERANCE_MAX_RATIO))
    return 0 < missing <= allowed_missing


def prompt_count_accepted_with_tolerance(expected_count: int | None, actual_count: int) -> bool:
    if expected_count in (None, 0):
        return False
    return int(actual_count or 0) != int(expected_count or 0) and prompt_count_acceptable(expected_count, actual_count)


def can_build_final_prompts(*, expected_count: int | None, actual_count: int, current_chunk: int, total_chunks: int) -> bool:
    if total_chunks <= 0 or current_chunk < total_chunks:
        return False
    return prompt_count_acceptable(expected_count, actual_count)


def checkpoint_resume_action(
    *,
    checkpoint_invalid: bool,
    partial_exists: bool,
    current_chunk: int,
    total_chunks: int,
) -> str:
    if checkpoint_invalid:
        return INVALID_CHECKPOINT_CLEAN_RERUN
    if partial_exists and total_chunks > 0 and 0 < current_chunk < total_chunks:
        return f"resume_from_chunk_{current_chunk + 1}"
    if partial_exists and total_chunks > 0 and current_chunk >= total_chunks:
        return "finalize_from_complete_checkpoint"
    return "clean_rerun_required"


def classify_stage_prompts_failure(*, stage_dir: Path | None, canonical_ready: bool = False) -> str:
    if canonical_ready:
        return "ALREADY_READY"
    if stage_dir is None or not stage_dir.is_dir():
        return VALIDATION_MISSING
    flags = _stage_artifact_flags(stage_dir)
    if flags["final_exists"]:
        if not flags["processed_marker_exists"]:
            return PROCESSED_MARKER_SKIPPED
        return CANONICAL_COMMIT_SKIPPED
    if flags["raw_response_missing_but_chunk_parsed"]:
        if flags["checkpoint_invalid"]:
            return INVALID_CHECKPOINT_CLEAN_RERUN
        return RAW_RESPONSE_MISSING_BUT_CHUNK_PARSED
    if flags["partial_exists"] or flags["checkpoint_exists"]:
        if flags["checkpoint_invalid"]:
            return INVALID_CHECKPOINT_CLEAN_RERUN
        if flags["total_chunks"] and flags["current_chunk"] >= flags["total_chunks"]:
            return FINAL_PROMPTS_NOT_BUILT_FROM_VALID_PARTIAL
        return PARTIAL_CHECKPOINT_CREATED_NO_FINAL_COMMIT
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
        "processed_marker_found": any(row.get("processed_marker_exists") for row in stage_rows),
        "last_successful_chunk": max((int(row.get("last_successful_chunk") or 0) for row in stage_rows), default=0),
    }


def validate_prompts_worker_lifecycle(progress: dict[str, Any]) -> list[dict[str, Any]]:
    workers = progress.get("workers") if isinstance(progress.get("workers"), dict) else {}
    stories = progress.get("stories") if isinstance(progress.get("stories"), dict) else {}
    violations: list[dict[str, Any]] = []
    for worker_name, worker in workers.items():
        assigned = [
            row
            for row in stories.values()
            if isinstance(row, dict) and str(row.get("assigned_worker") or "") == worker_name
        ]
        assigned_total = int(worker.get("assigned_total") or len(assigned) or 0)
        done = int(worker.get("done") or 0)
        failed = int(worker.get("failed") or 0)
        partial = int(worker.get("partial") or 0)
        unresolved = [
            row
            for row in assigned
            if str(row.get("status") or "") in NON_TERMINAL_STATUSES
        ]
        current_story = str(worker.get("current_story_id") or "")
        remaining = max(0, assigned_total - done - failed)
        if assigned_total > 0 and remaining > 0 and not current_story and unresolved:
            violations.append(
                {
                    "code": ASSIGNED_BUT_IDLE_STATE_BUG,
                    "worker": worker_name,
                    "message": "worker has unresolved assigned stories but no active current_story_id",
                    "assigned_total": assigned_total,
                    "remaining": remaining,
                    "stories": [str(row.get("story_id") or "") for row in unresolved],
                }
            )
        if partial > 0 and not current_story:
            violations.append(
                {
                    "code": WORKER_EXITED_BEFORE_TERMINAL_OUTCOME,
                    "worker": worker_name,
                    "message": "worker stopped with partial outputs and no terminal outcome",
                    "partial": partial,
                    "stories": [str(row.get("story_id") or "") for row in unresolved],
                }
            )
    return violations


def validate_prompts_terminal_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for row in results:
        status = str(row.get("status") or "")
        ok = bool(row.get("ok"))
        reason = normalize_failure_reason(str(row.get("reason") or row.get("next_action") or row.get("error") or ""))
        story_id = str(row.get("story_id") or "")
        if ok and status in TERMINAL_FAILURE_STATUSES:
            violations.append({"code": OK_TRUE_WITH_TERMINAL_FAILED, "story_id": story_id, "status": status})
        if status in TERMINAL_FAILURE_STATUSES and reason == UNKNOWN_PROMPTS_FAILURE:
            violations.append({"code": UNKNOWN_PROMPTS_FAILURE, "story_id": story_id, "status": status})
        if status == "partial":
            violations.append(
                {
                    "code": PROMPTS_GENERATION_INCOMPLETE,
                    "story_id": story_id,
                    "status": status,
                    "message": "partial is recoverable state, not terminal success",
                }
            )
    return violations


def assert_no_prompt_state_violations(*, progress: dict[str, Any] | None = None, results: list[dict[str, Any]] | None = None) -> None:
    violations: list[dict[str, Any]] = []
    if progress is not None:
        violations.extend(validate_prompts_worker_lifecycle(progress))
    if results is not None:
        violations.extend(validate_prompts_terminal_results(results))
    if violations:
        raise RuntimeError("PROMPTS_STATE_MACHINE_VIOLATION " + "; ".join(str(row.get("code")) for row in violations))
