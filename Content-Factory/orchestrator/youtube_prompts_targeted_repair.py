"""Forensic and targeted rerun/repair for specific YouTube visual prompt stories."""

from __future__ import annotations

import time
import shutil
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.isolated_launch_context import isolated_session
from orchestrator.launch_contract import build_launch_context
from orchestrator.youtube_prompts_failure_reasons import (
    GEMINI_NO_RESPONSE,
    NO_TEMP_OUTPUT,
    PROMPTS_GENERATION_INCOMPLETE,
    TEMP_IMPORT_FAILED,
    UNKNOWN_PROMPTS_FAILURE,
    VALIDATION_MISSING,
    build_story_prompts_forensic,
    classify_stage_prompts_failure,
    normalize_failure_reason,
    validate_prompts_terminal_results,
    validate_prompts_worker_lifecycle,
)
from orchestrator.youtube_prompts_runtime_quarantine import (
    RuntimeQuarantineRegistry,
    read_last_successful_chunk,
    worker_batch_runtime_reason,
    write_runtime_blocker_evidence,
)
from orchestrator.youtube_prompts_temp_import_repair import (
    YoutubePromptsTempImportRepairOptions,
    _canonical_prompts_ready,
    _expected_prompts_for_story,
    _prompt_temp_root,
    run_youtube_prompts_temp_import_repair,
)
from orchestrator.youtube_visuals_bridge import YoutubeGeminiBatchStory
from orchestrator.youtube_visuals_runner import (
    YoutubePromptsResumeAuditOptions,
    _fmt_elapsed,
    _initialize_prompts_progress,
    _iter_launch_story_dirs,
    _load_manifest,
    _now_iso,
    _prompt_estimate,
    _prompts_batch_manifest_path,
    _prompts_progress_path,
    _ready_prompt_worker_indexes,
    _render_prompts_progress,
    _run_prompts_worker_batch,
    _story_dir,
    _story_identity,
    _story_visual_readiness,
    _update_manifest_dict,
    _visual_prompts_status_row,
    _write_json,
    reconcile_visuals_progress_from_filesystem,
    run_youtube_prompts_resume_audit,
    run_youtube_visuals_launch_status,
    YoutubeVisualsStatusOptions,
)


@dataclass
class YoutubePromptsTargetedRepairOptions:
    youtube_run_id: str
    story_ids: list[str] = field(default_factory=list)
    workers: int = 1
    execute: bool = False
    preferred_session_id: str = "20260618_082047"
    accept_known_promo_issues: bool = False


def _story_prompt_targets(story_dir: Path) -> dict[str, Path]:
    return {
        "primary": story_dir / "06_prompts" / "prompts_list.txt",
        "legacy": story_dir / "06_director" / "prompts_list.txt",
    }


def _resolve_story_dirs(config: OrchestratorConfig, launch_id: str, story_ids: list[str]) -> tuple[list[Path], list[str]]:
    requested = [str(item).strip() for item in story_ids if str(item).strip()]
    if not requested:
        return [], ["--story-id is required at least once"]
    resolved: list[Path] = []
    errors: list[str] = []
    for story_id in requested:
        try:
            resolved.append(_story_dir(config, story_id, youtube_run_id=launch_id))
        except Exception as exc:
            errors.append(f"{story_id}: {exc}")
    return resolved, errors


def _canonical_snapshot(story_dir: Path, story_id: str, expected: int | None) -> dict[str, Any]:
    targets = _story_prompt_targets(story_dir)
    ready, path, validation, actual = _canonical_prompts_ready(story_dir, story_id, expected)
    return {
        "ready": ready,
        "primary_path": str(targets["primary"]),
        "legacy_path": str(targets["legacy"]),
        "primary_exists": targets["primary"].is_file(),
        "legacy_exists": targets["legacy"].is_file(),
        "canonical_path": str(path),
        "actual": actual,
        "expected": expected,
        "validation": validation,
    }


def _forensic_story(
    *,
    config: OrchestratorConfig,
    ctx: Any,
    story_dir: Path,
    preferred_session_id: str,
) -> dict[str, Any]:
    manifest = _load_manifest(story_dir)
    story_id, title = _story_identity(story_dir, manifest)
    expected = _expected_prompts_for_story(story_dir, manifest)
    canonical = _canonical_snapshot(story_dir, story_id, expected)
    visual_prompts = manifest.get("visual_prompts") if isinstance(manifest.get("visual_prompts"), dict) else {}
    progress = _read_progress(ctx)
    progress_row = progress.get("stories", {}).get(story_id, {}) if isinstance(progress.get("stories"), dict) else {}
    assigned_worker = str(progress_row.get("assigned_worker") or visual_prompts.get("worker_id") or "")
    if assigned_worker and not str(assigned_worker).startswith("worker_"):
        assigned_worker = f"worker_{assigned_worker}"
    forensic = build_story_prompts_forensic(
        temp_root=_prompt_temp_root(ctx),
        story_id=story_id,
        assigned_worker=assigned_worker,
        preferred_session_id=preferred_session_id,
        canonical_ready=bool(canonical["ready"]),
        canonical_paths={
            "primary_path": canonical["primary_path"],
            "legacy_path": canonical["legacy_path"],
            "primary_exists": canonical["primary_exists"],
            "legacy_exists": canonical["legacy_exists"],
            "actual": canonical["actual"],
            "expected": canonical["expected"],
        },
    )
    manifest_error = normalize_failure_reason(
        str(visual_prompts.get("error") or ""),
        fallback=forensic["exact_reason"],
    )
    progress_error = normalize_failure_reason(
        str(progress_row.get("error") or ""),
        fallback=manifest_error,
    )
    why_no_canonical = forensic["exact_reason"]
    if canonical["ready"]:
        why_no_canonical = "canonical_exists"
    elif forensic["temp_prompts_list_found"]:
        why_no_canonical = TEMP_IMPORT_FAILED
    elif forensic["temp_partial_found"]:
        why_no_canonical = PROMPTS_GENERATION_INCOMPLETE
    elif forensic["raw_gemini_response_found"] and not forensic["temp_partial_found"]:
        why_no_canonical = GEMINI_NO_RESPONSE
    elif not forensic["browser_output_received"]:
        why_no_canonical = NO_TEMP_OUTPUT if not forensic["stage_artifacts"] else GEMINI_NO_RESPONSE
    return {
        "story_id": story_id,
        "title": title,
        "story_dir": str(story_dir),
        "assigned_worker": assigned_worker,
        "expected_prompts": expected,
        "canonical": canonical,
        "forensic": forensic,
        "manifest_visual_prompts_error": manifest_error,
        "progress_error": progress_error,
        "why_no_canonical_prompts_list": why_no_canonical,
        "needs_rerun": not canonical["ready"],
    }


def _read_progress(ctx: Any) -> dict[str, Any]:
    path = _prompts_progress_path(ctx)
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_targeted_reports(ctx: Any, payload: dict[str, Any]) -> dict[str, str]:
    reports_dir = ctx.launch_root / "07_reports" / "gemini_execution"
    json_path = reports_dir / "YOUTUBE_PROMPTS_TARGETED_REPAIR.json"
    md_path = reports_dir / "YOUTUBE_PROMPTS_TARGETED_REPAIR.md"
    _write_json(json_path, payload)
    lines = [
        "# YOUTUBE_PROMPTS_TARGETED_REPAIR",
        "",
        f"- launch_id: {payload.get('launch_id')}",
        f"- execute: {str(bool(payload.get('execute'))).lower()}",
        f"- workers: {payload.get('workers')}",
        f"- preferred_session_id: {payload.get('preferred_session_id')}",
        "",
        "## Forensic",
        "",
        "| story | worker | session dirs | temp final | temp partial | raw gemini | exact_reason | why no canonical |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload.get("stories", []):
        forensic = row.get("forensic") if isinstance(row.get("forensic"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                str(value).replace("|", "\\|")
                for value in (
                    row.get("story_id"),
                    row.get("assigned_worker"),
                    ", ".join(forensic.get("sessions_with_stage_dir") or []),
                    str(forensic.get("temp_prompts_list_found")),
                    str(forensic.get("temp_partial_found")),
                    str(forensic.get("raw_gemini_response_found")),
                    forensic.get("exact_reason"),
                    row.get("why_no_canonical_prompts_list"),
                )
            )
            + " |"
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def _worker_stage_dir(ctx: Any, run_session_id: str, worker_index: int, story_id: str) -> Path:
    return (
        ctx.launch_root
        / "10_Временные_файлы"
        / "visuals_gemini_batch"
        / "prompts"
        / run_session_id
        / f"worker_{worker_index}"
        / story_id
    )


def _seed_resume_stage(
    *,
    target_stage: Path,
    source_stage: Path | None,
    worker_index: int,
    story_id: str,
) -> None:
    if not source_stage or not source_stage.is_dir():
        return
    target_stage.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in ("prompts_list.partial.txt", "director_checkpoint.json"):
        source_file = source_stage / name
        if source_file.is_file():
            shutil.copy2(source_file, target_stage / name)
            copied.append(name)
    for pattern in ("chunk_*.txt", "chunk_*_raw.txt", "*.raw_response.txt"):
        for source_file in sorted(source_stage.glob(pattern)):
            if source_file.is_file():
                shutil.copy2(source_file, target_stage / source_file.name)
                if source_file.name not in copied:
                    copied.append(source_file.name)
    if copied:
        print(
            f"[worker {worker_index}] RESUME seed: {story_id} | source={source_stage} | files={','.join(copied)}",
            flush=True,
        )


def _distribute_jobs(
    jobs: list[tuple[int, Path, str, str]],
    worker_ids: list[int],
) -> list[tuple[int, list[tuple[int, Path, str, str]]]]:
    if not jobs or not worker_ids:
        return []
    batches: list[tuple[int, list[tuple[int, Path, str, str]]]] = []
    worker_count = len(worker_ids)
    worker_batches: list[list[tuple[int, Path, str, str]]] = [[] for _ in range(worker_count)]
    for job_number, job in enumerate(jobs):
        worker_batches[job_number % worker_count].append(job)
    for slot_index, worker_jobs in enumerate(worker_batches, start=1):
        if worker_jobs:
            batches.append((worker_ids[slot_index - 1], worker_jobs))
    return batches


def _run_targeted_prompt_generation(
    *,
    config: OrchestratorConfig,
    ctx: Any,
    launch_id: str,
    story_dirs: list[Path],
    workers: int,
    accept_known_promo_issues: bool,
    resume_stage_sources: dict[str, Path] | None = None,
) -> dict[str, Any]:
    run_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    prompt_jobs: list[tuple[int, Path, str, str]] = []
    for index, story_dir in enumerate(story_dirs, start=1):
        manifest = _load_manifest(story_dir)
        story_id, title = _story_identity(story_dir, manifest)
        before = _story_visual_readiness(config, story_dir, story_id)
        if before["prompts_ready"]:
            continue
        prompt_jobs.append((index, story_dir, story_id, title))
    if not prompt_jobs:
        return {"ok": True, "status": "already_ready", "run_session_id": run_session_id, "results": []}

    preflight_worker_count = max(1, int(workers or 1))
    initial_worker_ids = _ready_prompt_worker_indexes(config, preflight_worker_count, require_all=False)
    if not initial_worker_ids:
        return {
            "ok": False,
            "status": "terminal_failed",
            "run_session_id": run_session_id,
            "results": [],
            "runtime_unhealthy_workers": [],
            "runtime_quarantine_reasons": {},
            "stories_requeued_from_worker": [],
            "reassigned_to_worker": [],
        }

    skipped = [
        row
        for story_dir in _iter_launch_story_dirs(config, launch_id)
        for row in (
            {
                "story_id": _story_identity(story_dir, _load_manifest(story_dir))[0],
                "reason": "not_selected",
            },
        )
        if story_dir not in story_dirs
    ]
    quarantine = RuntimeQuarantineRegistry()
    resume_sources: dict[str, Path] = dict(resume_stage_sources or {})
    pending_jobs = list(prompt_jobs)
    results: list[dict[str, Any]] = []
    max_reassign_rounds = max(3, len(prompt_jobs) * max(1, len(initial_worker_ids)))

    for reassign_round in range(max_reassign_rounds + 1):
        if not pending_jobs:
            break
        available_worker_ids = quarantine.available_workers(initial_worker_ids)
        if not available_worker_ids:
            terminal_reason = quarantine.terminal_reason_for_pending()
            for _index, story_dir, story_id, title in pending_jobs:
                stage_dir = resume_sources.get(story_id) or _worker_stage_dir(ctx, run_session_id, 0, story_id)
                results.append(
                    {
                        "story_id": story_id,
                        "title": title,
                        "ok": False,
                        "status": "terminal_failed",
                        "reason": terminal_reason,
                        "elapsed": "0s",
                        "stage_dir": str(stage_dir),
                        "last_successful_chunk": read_last_successful_chunk(Path(stage_dir)),
                    }
                )
            pending_jobs = []
            break

        worker_batches = _distribute_jobs(pending_jobs, available_worker_ids)
        pending_jobs = []
        requeue_jobs: list[tuple[tuple[int, Path, str, str], Path]] = []

        progress_batches = [jobs for _worker_id, jobs in worker_batches]
        progress_payload = _initialize_prompts_progress(
            config=config,
            ctx=ctx,
            launch_id=launch_id,
            run_session_id=run_session_id,
            selected=story_dirs,
            skipped=skipped,
            worker_batches=progress_batches,
            worker_ids=[worker_id for worker_id, _jobs in worker_batches],
        )
        _render_prompts_progress(progress_payload)

        future_map: dict[Any, tuple[int, list[tuple[int, Path, str, str]], float]] = {}
        with ThreadPoolExecutor(max_workers=len(worker_batches)) as executor:
            for slot_index, (worker_index, worker_jobs) in enumerate(worker_batches, start=1):
                if slot_index > 1:
                    time.sleep(45)
                batch_stories = [
                    YoutubeGeminiBatchStory(
                        story_id=story_id,
                        story_dir=story_dir,
                        stage_dir=_worker_stage_dir(ctx, run_session_id, worker_index, story_id),
                    )
                    for _index, story_dir, story_id, _title in worker_jobs
                ]
                for batch_story in batch_stories:
                    if reassign_round > 0:
                        quarantine.record_reassignment(
                            story_id=batch_story.story_id,
                            to_worker_index=worker_index,
                        )
                    _seed_resume_stage(
                        target_stage=Path(batch_story.stage_dir),
                        source_stage=resume_sources.get(batch_story.story_id),
                        worker_index=worker_index,
                        story_id=batch_story.story_id,
                    )
                for _index, story_dir, story_id, _title in worker_jobs:
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
                            "pipeline_stage_status": {
                                "scenes_prompts": "in_progress",
                                "director_prompts": "in_progress",
                            },
                        },
                    )
                future = executor.submit(
                    _run_prompts_worker_batch,
                    config=config,
                    launch_id=launch_id,
                    stories=batch_stories,
                    execute=True,
                    worker_index=worker_index,
                )
                future_map[future] = (worker_index, worker_jobs, time.monotonic())

            pending_futures = set(future_map)
            while pending_futures:
                done_futures, pending_futures = wait(pending_futures, timeout=30, return_when=FIRST_COMPLETED)
                if not done_futures:
                    progress_payload = reconcile_visuals_progress_from_filesystem(
                        config=config,
                        launch_id=launch_id,
                        run_session_id=run_session_id,
                        accept_known_promo_issues=accept_known_promo_issues,
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
                    log_path = Path(str(batch_result.get("legacy_log_path") or ""))
                    batch_ok = bool(batch_result.get("ok"))
                    runtime_reason = (
                        worker_batch_runtime_reason(batch_result, log_path if log_path.is_file() else None)
                        if not batch_ok
                        else None
                    )
                    log_excerpt = ""
                    if log_path.is_file():
                        log_excerpt = log_path.read_text(encoding="utf-8", errors="replace")[-20_000:]

                    if runtime_reason:
                        quarantine.mark_unhealthy(worker_index, runtime_reason)
                        for _index, story_dir, story_id, title in worker_jobs:
                            stage_dir = _worker_stage_dir(ctx, run_session_id, worker_index, story_id)
                            after = _story_visual_readiness(config, story_dir, story_id)
                            if after["prompts_ready"]:
                                results.append(
                                    {
                                        "story_id": story_id,
                                        "title": title,
                                        "ok": True,
                                        "status": "done",
                                        "reason": None,
                                        "elapsed": elapsed,
                                        "stage_dir": str(stage_dir),
                                    }
                                )
                                continue
                            if (stage_dir / "prompts_list.txt").is_file():
                                results.append(
                                    {
                                        "story_id": story_id,
                                        "title": title,
                                        "ok": True,
                                        "status": "done",
                                        "reason": None,
                                        "elapsed": elapsed,
                                        "stage_dir": str(stage_dir),
                                        "pending_import": True,
                                    }
                                )
                                continue
                            last_chunk = read_last_successful_chunk(stage_dir)
                            evidence = write_runtime_blocker_evidence(
                                stage_dir,
                                worker_index=worker_index,
                                reason=runtime_reason,
                                log_excerpt=log_excerpt,
                            )
                            quarantine.record_requeue(
                                story_id=story_id,
                                from_worker_index=worker_index,
                                reason=runtime_reason,
                                last_successful_chunk=last_chunk,
                                stage_dir=stage_dir,
                                evidence=evidence,
                            )
                            resume_sources[story_id] = stage_dir
                            requeue_jobs.append(((_index, story_dir, story_id, title), stage_dir))
                        continue

                    for _index, story_dir, story_id, title in worker_jobs:
                        stage_dir = _worker_stage_dir(ctx, run_session_id, worker_index, story_id)
                        after = _story_visual_readiness(config, story_dir, story_id)
                        ok = bool(after["prompts_ready"])
                        reason = normalize_failure_reason(
                            str(batch_result.get("next_action") or ""),
                            fallback=classify_stage_prompts_failure(stage_dir=stage_dir, canonical_ready=ok),
                        )
                        if ok:
                            reason = None
                        elif after["prompts_status"] == "partial":
                            reason = PROMPTS_GENERATION_INCOMPLETE
                        results.append(
                            {
                                "story_id": story_id,
                                "title": title,
                                "ok": ok,
                                "status": "done" if ok else str(batch_result.get("status") or "failed"),
                                "reason": reason,
                                "elapsed": elapsed,
                                "stage_dir": str(stage_dir),
                                "last_successful_chunk": read_last_successful_chunk(stage_dir),
                            }
                        )

        if requeue_jobs:
            pending_jobs = [job for job, _stage in requeue_jobs]
        else:
            break

    progress_after = reconcile_visuals_progress_from_filesystem(
        config=config,
        launch_id=launch_id,
        run_session_id=run_session_id,
        accept_known_promo_issues=accept_known_promo_issues,
    )
    state_violations = validate_prompts_worker_lifecycle(progress_after)
    state_violations.extend(validate_prompts_terminal_results(results))
    quarantine_report = quarantine.as_report()
    return {
        "ok": bool(results) and all(row.get("ok") for row in results) and not state_violations,
        "status": "done" if results and all(row.get("ok") for row in results) and not state_violations else "terminal_failed",
        "run_session_id": run_session_id,
        "results": results,
        "state_violations": state_violations,
        "progress_path": str(_prompts_progress_path(ctx)),
        "batch_manifest_path": str(_prompts_batch_manifest_path(ctx)),
        **quarantine_report,
    }


def run_youtube_prompts_targeted_repair(
    *,
    config: OrchestratorConfig,
    options: YoutubePromptsTargetedRepairOptions,
) -> dict[str, Any]:
    launch_id = str(options.youtube_run_id or "").strip()
    if not launch_id:
        return {"ok": False, "message": "--youtube-run-id is required"}
    story_dirs, resolve_errors = _resolve_story_dirs(config, launch_id, options.story_ids)
    if resolve_errors:
        return {"ok": False, "message": "; ".join(resolve_errors)}

    ctx = build_launch_context(config, launch_id=launch_id)
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        forensic_rows = [
            _forensic_story(
                config=config,
                ctx=ctx,
                story_dir=story_dir,
                preferred_session_id=str(options.preferred_session_id or "").strip(),
            )
            for story_dir in story_dirs
        ]
        generation: dict[str, Any] | None = None
        import_result: dict[str, Any] | None = None
        if options.execute:
            pending_dirs = [Path(row["story_dir"]) for row in forensic_rows if row.get("needs_rerun")]
            if pending_dirs:
                generation = _run_targeted_prompt_generation(
                    config=config,
                    ctx=ctx,
                    launch_id=launch_id,
                    story_dirs=pending_dirs,
                    workers=max(1, int(options.workers or 1)),
                    accept_known_promo_issues=bool(options.accept_known_promo_issues),
                )
            import_result = run_youtube_prompts_temp_import_repair(
                config=config,
                options=YoutubePromptsTempImportRepairOptions(
                    youtube_run_id=launch_id,
                    run_session_id=str(generation.get("run_session_id") if generation else ""),
                    normalize_blocked_ages=True,
                    execute=True,
                ),
            )
            forensic_rows = [
                _forensic_story(
                    config=config,
                    ctx=ctx,
                    story_dir=Path(row["story_dir"]),
                    preferred_session_id=str(options.preferred_session_id or "").strip(),
                )
                for row in forensic_rows
            ]

        readiness = run_youtube_prompts_resume_audit(
            config=config,
            options=YoutubePromptsResumeAuditOptions(
                youtube_run_id=launch_id,
                accept_known_promo_issues=bool(options.accept_known_promo_issues),
            ),
        )
        launch_status = run_youtube_visuals_launch_status(
            config=config,
            options=YoutubeVisualsStatusOptions(
                youtube_run_id=launch_id,
                accept_known_promo_issues=bool(options.accept_known_promo_issues),
            ),
        )
        summary = launch_status.get("summary") if isinstance(launch_status.get("summary"), dict) else {}
        selected_ids = {row["story_id"] for row in forensic_rows}
        selected_not_ready = sum(
            1
            for row in readiness.get("stories", [])
            if row.get("story_id") in selected_ids and not row.get("ready_for_runpod")
        )

    payload = {
        "ok": selected_not_ready == 0,
        "execute": bool(options.execute),
        "launch_id": launch_id,
        "workers": max(1, int(options.workers or 1)),
        "preferred_session_id": options.preferred_session_id,
        "story_ids": [row["story_id"] for row in forensic_rows],
        "stories": forensic_rows,
        "generation": generation,
        "import_result": {
            "imported_count": (import_result or {}).get("imported_count"),
            "rejected_count": (import_result or {}).get("rejected_count"),
        }
        if import_result
        else None,
        "final_readiness": {
            "ready_for_runpod": readiness.get("ready_for_runpod"),
            "prompts_missing": readiness.get("prompts_missing"),
            "prompts_invalid": readiness.get("prompts_invalid"),
            "selected_not_ready": selected_not_ready,
            "next_stage_allowed": bool(summary.get("next_stage_allowed")),
        },
        "generated_at": _now_iso(),
    }
    payload["reports"] = _write_targeted_reports(ctx, payload)
    return payload
