from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.gemini_worker_scheduler import DEFAULT_GEMINI_START_MODE
from orchestrator.isolated_launch_context import isolated_session
from orchestrator.isolated_launch_mode import resolver_if_isolated
from orchestrator.account_capabilities import resolve_gemini_account_indices
from orchestrator.youtube_full_auto.constants import (
    DEFAULT_GEMINI_WORKERS,
    DEFAULT_MAX_WORDS,
    DEFAULT_MIN_WORDS,
    GEMINI_ACCOUNT_STAGES,
    GEMINI_STAGES,
    MAX_GEMINI_ACCOUNTS,
    STAGE_NAMES,
    STAGE_PARALLELISM,
    STAGE_PARALLELISM_REASON,
)
from orchestrator.youtube_full_auto.discovery import pick_site_run_id, run_youtube_full_auto_discover_site_runs
from orchestrator.youtube_full_auto.layout import batch_launch_root, default_youtube_run_id, ensure_batch_layout, safe_slug, utc_now
from orchestrator.youtube_full_auto.queue_store import QueueStore, StoryQueueItem
from orchestrator.youtube_full_auto.stage_runners import (
    build_queue_from_deferred,
    deferred_item_for_story,
    reset_claim_index,
    run_promo_batch,
    run_single_package,
    run_single_promo,
    run_single_safe,
    run_single_selection,
    run_single_tts_export,
    run_single_visuals,
)
from orchestrator.youtube_full_auto.telegram_stage import (
    is_telegram_eligible_item,
    run_single_telegram_assets,
    write_telegram_assets_report,
)
from orchestrator.youtube_full_auto.reporting import write_full_auto_summary
from orchestrator.youtube_full_auto.progress_reporter import FullAutoProgressReporter, get_reporter


def _parse_stages(value: str) -> list[str]:
    out: list[str] = []
    for part in str(value or "").split(","):
        name = part.strip().lower()
        if name and name in STAGE_NAMES and name not in out:
            out.append(name)
    return out


def _parse_gemini_pool(
    *,
    gemini_accounts: str = "",
    accounts: str = "",
    account_start_index: int = 0,
    gemini_workers: int = DEFAULT_GEMINI_WORKERS,
    gemini_active_workers: int = 0,
    stage: str = "",
):
    from orchestrator.youtube_full_auto.gemini_account_pool import resolve_gemini_pool_config

    if stage not in GEMINI_ACCOUNT_STAGES and not str(gemini_accounts or accounts or "").strip():
        return None
    return resolve_gemini_pool_config(
        gemini_accounts=gemini_accounts,
        accounts=accounts,
        account_start_index=account_start_index,
        gemini_workers=gemini_workers,
        gemini_active_workers=gemini_active_workers or None,
        stage=stage,
    )


def _parse_accounts(
    *,
    gemini_accounts: str = "",
    accounts: str = "",
    account_start_index: int = 0,
    gemini_workers: int = DEFAULT_GEMINI_WORKERS,
    gemini_active_workers: int = 0,
    stage: str = "",
) -> list[int]:
    pool_cfg = _parse_gemini_pool(
        gemini_accounts=gemini_accounts,
        accounts=accounts,
        account_start_index=account_start_index,
        gemini_workers=gemini_workers,
        gemini_active_workers=gemini_active_workers,
        stage=stage,
    )
    if pool_cfg is None:
        return []
    return list(pool_cfg.account_pool)


def _should_defer_heartbeat_for_run(*, options: YoutubeFullAutoOptions | YoutubeBatchStageOptions, stages: list[str]) -> bool:
    from orchestrator.gemini_worker_scheduler import normalize_gemini_start_mode

    stage = stages[0] if stages else "selection"
    if stage not in GEMINI_STAGES:
        return False
    workers = int(getattr(options, "gemini_workers", DEFAULT_GEMINI_WORKERS))
    if workers <= 1:
        return False
    mode = normalize_gemini_start_mode(str(getattr(options, "gemini_start_mode", "") or ""))
    return mode != "immediate"


def _sched_debug(reporter: FullAutoProgressReporter | None, message: str, *, stage: str = "") -> None:
    if reporter is not None:
        reporter.log_scheduler_debug(message, stage=stage)


@dataclass
class YoutubeFullAutoOptions:
    site_run_id: str = "auto"
    youtube_run_id: str = ""
    from_site_approved: bool = True
    min_words: int = DEFAULT_MIN_WORDS
    max_words: int = DEFAULT_MAX_WORDS
    gemini_workers: int = DEFAULT_GEMINI_WORKERS
    gemini_active_workers: int = 0
    account_start_index: int = 0
    accounts: str = ""
    gemini_accounts: str = ""
    stages: str = "selection,safe,telegram,promo,tts,visuals,frames,package"
    limit: int = 0
    target_yes: int = 0
    only_story: str = ""
    resume: bool = False
    force_stage: str = ""
    skip_existing: bool = True
    dry_run: bool = False
    execute: bool = False
    stop_after: str = ""
    allow_render: bool = False
    frames_runpod_url: str = ""
    pod_ssh: str = ""
    launch_id: str = ""
    retry_failed: bool = False
    progress_reporter: FullAutoProgressReporter | None = None
    interactive: bool = False
    live_logs: bool = True
    heartbeat_seconds: float = 60.0
    max_story_runtime_minutes: int = 20
    max_page_reloads: int = 2
    max_attach_attempts: int = 3
    gemini_start_mode: str = DEFAULT_GEMINI_START_MODE
    ramp_up_stop_on_system_fail: bool = True
    gemini_session_mode: str = "persistent-account"
    gemini_stories_per_browser: int = 10
    gemini_max_browser_lifetime_minutes: int = 60
    gemini_restart_browser_after_failures: int = 2
    reuse_stage_results: bool = True
    allow_visuals_stub: bool = False
    model_fallback_on_limit: bool = True
    account_cooldown_seconds: int = 60
    max_parallel_browser_launches: int = 1
    dashboard_mode: str = "production"
    dashboard_interval: float = 60.0
    characters_workers: int = 2
    scene_prompts_workers: int = 5
    visuals_dashboard: str = "production"
    visuals_force_staggered_ready: bool = True
    director_chunk_timeout_minutes: int = 45
    allow_immediate_browser_start: bool = False
    safe_max_chunks_per_chat: int = 3
    safe_target_ratio: float = 0.90
    safe_min_chunk_ratio: float = 0.70
    safe_min_story_ratio: float = 0.85
    safe_warn_story_ratio: float = 0.90
    safe_max_chunk_retries: int = 2


@dataclass
class YoutubeBatchStageOptions:
    youtube_run_id: str
    stage: str
    gemini_workers: int = DEFAULT_GEMINI_WORKERS
    gemini_active_workers: int = 0
    account_start_index: int = 0
    accounts: str = ""
    gemini_accounts: str = ""
    resume: bool = True
    skip_existing: bool = True
    execute: bool = False
    limit: int = 0
    retry_failed: bool = False
    frames_runpod_url: str = ""
    progress_reporter: FullAutoProgressReporter | None = None
    live_logs: bool = True
    heartbeat_seconds: float = 60.0
    max_story_runtime_minutes: int = 20
    max_page_reloads: int = 2
    max_attach_attempts: int = 3
    gemini_start_mode: str = DEFAULT_GEMINI_START_MODE
    ramp_up_stop_on_system_fail: bool = True
    gemini_session_mode: str = "persistent-account"
    gemini_stories_per_browser: int = 10
    gemini_max_browser_lifetime_minutes: int = 60
    gemini_restart_browser_after_failures: int = 2
    reuse_stage_results: bool = True
    allow_visuals_stub: bool = False
    model_fallback_on_limit: bool = True
    account_cooldown_seconds: int = 60
    max_parallel_browser_launches: int = 1
    dashboard_mode: str = "production"
    dashboard_interval: float = 60.0
    characters_workers: int = 2
    scene_prompts_workers: int = 5
    visuals_dashboard: str = "production"
    visuals_force_staggered_ready: bool = True
    director_chunk_timeout_minutes: int = 45
    allow_immediate_browser_start: bool = False
    safe_max_chunks_per_chat: int = 3
    safe_target_ratio: float = 0.90
    safe_min_chunk_ratio: float = 0.70
    safe_min_story_ratio: float = 0.85
    safe_warn_story_ratio: float = 0.90
    safe_max_chunk_retries: int = 2


def _init_or_load_queue(config: OrchestratorConfig, options: YoutubeFullAutoOptions) -> tuple[Path, QueueStore, dict[str, Any]]:
    youtube_run_id = str(options.youtube_run_id or "").strip() or default_youtube_run_id()
    batch_root = ensure_batch_layout(config, youtube_run_id)
    store = QueueStore(batch_root, config=config, youtube_run_id=youtube_run_id)
    only_story = str(getattr(options, "only_story", "") or "").strip()

    if options.resume and store.items():
        if only_story:
            _filter_store_to_story(store, only_story=only_story)
            store.save()
        meta = store.meta()
        return batch_root, store, {"youtube_run_id": youtube_run_id, "resumed": True, **meta}

    picked = pick_site_run_id(config, options.site_run_id)
    if picked is None:
        raise FileNotFoundError("No site deferred.json found. Run: youtube full-auto discover-site-runs")

    deferred_path = Path(picked.deferred_path)
    items = build_queue_from_deferred(
        config=config,
        site_run_id=picked.site_run_id,
        deferred_path=deferred_path,
        min_words=int(options.min_words),
        max_words=int(options.max_words),
    )
    for item in items:
        store.upsert(item)
    if only_story:
        _filter_store_to_story(store, only_story=only_story)
    store.set_meta(
        youtube_run_id=youtube_run_id,
        site_run_id=picked.site_run_id,
        deferred_path=str(deferred_path),
        min_words=options.min_words,
        max_words=options.max_words,
        created_at=utc_now(),
        from_site_approved=bool(options.from_site_approved),
    )
    store.save()
    return batch_root, store, {"youtube_run_id": youtube_run_id, "site_run_id": picked.site_run_id, "resumed": False}


def _story_match_keys(item: StoryQueueItem) -> set[str]:
    title = str(item.canonical_basename or "").strip()
    story_key = str(item.story_key or "").strip()
    slug = safe_slug(title)
    cleaned_stem = Path(str(item.cleaned_path or "")).stem
    return {
        title.casefold(),
        story_key.casefold(),
        slug.casefold(),
        cleaned_stem.casefold(),
        safe_slug(cleaned_stem).casefold(),
    }


def _filter_store_to_story(store: QueueStore, *, only_story: str) -> None:
    target = str(only_story or "").strip()
    if not target:
        return
    target_keys = {target.casefold(), safe_slug(target).casefold()}
    matches = [item for item in store.items() if _story_match_keys(item) & target_keys]
    if not matches:
        raise ValueError(f"only_story did not match any queue item: {target}")
    if len(matches) > 1:
        titles = ", ".join(item.canonical_basename for item in matches[:10])
        raise ValueError(f"only_story matched multiple queue items: {target}: {titles}")
    store.replace_items(matches)
    store.set_meta(only_story=target, only_story_slug=safe_slug(matches[0].canonical_basename))


def _pending_for_stage(stage: str, *, retry_failed: bool, allow_visuals_stub: bool = False) -> list[str]:
    mapping = {
        "selection": ["selection_pending"] + (["failed"] if retry_failed else []),
        "safe": ["safe_pending", "safe_pending_regen"] + (["failed", "safe_failed_quality"] if retry_failed else []),
        "telegram": ["selection_yes", "safe_pending", "safe_done", "telegram_pending", "telegram_failed"]
        + [
            "promo_pending",
            "promo_done",
            "visuals_pending",
            "visuals_done",
            "ready_for_runpod_images",
            "tts_pending",
            "tts_job_exported",
            "tts_waiting_colab",
            "tts_done",
            "frames_pending",
            "frames_done",
            "package_pending",
            "package_done",
            "render_pending",
            "render_done",
            "youtube_ready",
            "blocked_needs_tts",
        ]
        + (["failed"] if retry_failed else []),
        "promo": ["safe_done", "telegram_assets_ready", "promo_pending", "promo_running"]
        + (["failed"] if retry_failed else []),
        "visuals": (
            (["promo_done"] if allow_visuals_stub else ["tts_done"])
            + ["visuals_pending", "blocked_needs_tts"]
            + (["failed"] if retry_failed else [])
        ),
        "tts": ["promo_done", "tts_pending", "tts_job_exported", "tts_waiting_colab"] + (["failed"] if retry_failed else []),
        "frames": ["ready_for_runpod_images", "visuals_done", "frames_pending"] + (["failed"] if retry_failed else []),
        "package": ["tts_done", "frames_done", "package_pending"] + (["failed"] if retry_failed else []),
        "render": ["package_done", "render_pending"] + (["failed"] if retry_failed else []),
    }
    return mapping.get(stage, [])


_VISUALS_DONE_STATUSES = frozenset(
    {
        "visuals_done",
        "ready_for_runpod_images",
        "tts_job_exported",
        "tts_waiting_colab",
        "frames_pending",
        "frames_done",
        "package_pending",
        "package_done",
        "render_pending",
        "render_done",
        "youtube_ready",
    }
)


def _requeue_safe_interrupted_without_output(store: QueueStore) -> int:
    """Restore queue rows that were marked failed during incomplete safe runs."""
    requeued = 0
    for item in store.by_status("failed"):
        safe_row = dict(item.stages.get("safe") or {})
        if not safe_row:
            continue
        if str(safe_row.get("status") or "") == "safe_done":
            continue
        sel_row = dict(item.stages.get("selection") or {})
        sel_status = str(sel_row.get("status") or "")
        if sel_status not in {"selection_yes", "safe_pending"}:
            continue
        item.status = "safe_pending"
        requeued += 1
    return requeued


def _requeue_failed_items_for_stage(store: QueueStore, stage: str) -> int:
    """Move failed queue rows back to stage-pending without deleting stage artifacts."""
    requeued = 0
    for item in store.by_status("failed"):
        if stage == "selection":
            item.status = "selection_pending"
        elif stage == "safe":
            item.status = "safe_pending"
        elif stage == "promo":
            item.status = "promo_pending"
        elif stage == "visuals":
            item.status = "visuals_pending"
        elif stage == "tts":
            item.status = "tts_pending"
        elif stage == "frames":
            item.status = "frames_pending"
        elif stage == "package":
            item.status = "package_pending"
        elif stage == "render":
            item.status = "render_pending"
        else:
            continue
        item.retry_count = int(getattr(item, "retry_count", 0) or 0) + 1
        store.upsert(item)
        requeued += 1
    return requeued


def _compute_stage_eligibility(
    store: QueueStore,
    stage: str,
    *,
    retry_failed: bool,
    limit: int,
    allow_visuals_stub: bool = False,
) -> dict[str, Any]:
    all_items = store.items()
    pending_default = _pending_for_stage(stage, retry_failed=False, allow_visuals_stub=allow_visuals_stub)
    pending_with_retry = _pending_for_stage(stage, retry_failed=True, allow_visuals_stub=allow_visuals_stub)
    pool_default = store.by_status(*pending_default)
    pool_retry = store.by_status(*pending_with_retry)
    failed_items = [item for item in all_items if item.status == "failed"]
    skipped_failed = len(failed_items) if not retry_failed else 0
    retried_failed = sum(1 for item in pool_retry if item.status == "failed") if retry_failed else 0
    skipped_done = 0
    if stage == "visuals":
        skipped_done = sum(1 for item in all_items if item.status in _VISUALS_DONE_STATUSES)
    pool = pool_retry if retry_failed else pool_default
    candidates_total = len(pool_retry)
    eligible_pool = list(pool)
    if limit > 0:
        eligible_pool = eligible_pool[:limit]
    eligible = len(eligible_pool)
    reason = ""
    if eligible == 0:
        if skipped_failed > 0 and not retry_failed:
            reason = "no_eligible_candidates_all_failed_or_done"
        elif skipped_done > 0 and skipped_failed == 0:
            reason = "no_eligible_candidates_all_done"
        else:
            reason = "no_eligible_candidates"
    return {
        "candidates_total": candidates_total,
        "eligible": eligible,
        "skipped_done": skipped_done,
        "skipped_failed": skipped_failed,
        "retried_failed": retried_failed,
        "retry_failed": bool(retry_failed),
        "requeued_failed": 0,
        "reason": reason,
    }


def _resolve_stage_input(
    *,
    stage: str,
    options: YoutubeFullAutoOptions | YoutubeBatchStageOptions,
    reporter: FullAutoProgressReporter | None,
) -> bool:
    """Return False if stage should be skipped (manual input missing)."""
    interactive = bool(getattr(options, "interactive", False))
    if stage == "frames":
        url = str(getattr(options, "frames_runpod_url", "") or "").strip()
        if url:
            return True
        if interactive and reporter is not None:
            value = reporter.log_waiting_for_input(stage="frames", required="frames_runpod_url")
            if value:
                if isinstance(options, YoutubeFullAutoOptions):
                    options.frames_runpod_url = value
                else:
                    options.frames_runpod_url = value  # type: ignore[attr-defined]
                return True
        if reporter is not None:
            reporter._write_line(
                "STAGE_SKIPPED stage=frames status=waiting_or_skipped reason_code=MANUAL_INPUT_NOT_PROVIDED",
                stage="frames",
            )
        return False
    if stage == "render":
        if bool(getattr(options, "allow_render", False)) and str(getattr(options, "pod_ssh", "") or "").strip():
            return True
        if interactive and reporter is not None:
            value = reporter.log_waiting_for_input(stage="render", required="pod_ssh")
            if value:
                if isinstance(options, YoutubeFullAutoOptions):
                    options.pod_ssh = value
                    options.allow_render = True
                return True
        if reporter is not None:
            reporter._write_line(
                "STAGE_SKIPPED stage=render status=waiting_or_skipped reason_code=MANUAL_INPUT_NOT_PROVIDED",
                stage="render",
            )
        return False
    if stage == "tts" and interactive and reporter is not None:
        reporter.log_waiting_for_input(stage="tts", required="colab_jobs_done")
    return True


def _run_stage_batch(
    *,
    config: OrchestratorConfig,
    store: QueueStore,
    batch_root: Path,
    youtube_run_id: str,
    stage: str,
    options: YoutubeFullAutoOptions | YoutubeBatchStageOptions,
) -> dict[str, Any]:
    reporter = getattr(options, "progress_reporter", None) or get_reporter()
    if not _resolve_stage_input(stage=stage, options=options, reporter=reporter):
        return {
            "stage": stage,
            "workers": 0,
            "stage_parallelism": STAGE_PARALLELISM.get(stage, 1),
            "stage_parallelism_reason": "skipped_manual_input_not_provided",
            "candidates": 0,
            "processed": 0,
            "ok": 0,
            "failed": 0,
            "skipped": True,
            "status": "waiting_or_skipped",
            "reason_code": "MANUAL_INPUT_NOT_PROVIDED",
        }
    execute = bool(getattr(options, "execute", False)) and not bool(getattr(options, "dry_run", False))
    from orchestrator.gemini_execution_policy import stage_uses_persistent_session

    use_persistent_gemini = stage_uses_persistent_session(
        full_auto_stage=stage,
        session_mode=str(getattr(options, "gemini_session_mode", "") or ""),
    ) and execute
    limit = int(getattr(options, "limit", 0) or 0)
    skip_existing = bool(getattr(options, "skip_existing", True))
    retry_failed = bool(getattr(options, "retry_failed", False))
    requeued_failed = 0
    if retry_failed:
        requeued_failed = _requeue_failed_items_for_stage(store, stage)
        if requeued_failed:
            store.save()
    elif stage == "safe" and bool(getattr(options, "resume", False)):
        requeued_failed = _requeue_safe_interrupted_without_output(store)
        if requeued_failed:
            store.save()
    elif stage == "visuals" and bool(getattr(options, "resume", False)):
        from orchestrator.youtube_full_auto.visuals_stage_requeue import requeue_visuals_interrupted

        vis_requeue = requeue_visuals_interrupted(store)
        requeued_failed = int(vis_requeue.get("requeued_running", 0) or 0) + int(
            vis_requeue.get("requeued_failed", 0) or 0
        )
        if requeued_failed or vis_requeue.get("stale_claims_cleared"):
            store.save()
    eligibility = _compute_stage_eligibility(
        store,
        stage,
        retry_failed=retry_failed,
        limit=limit,
        allow_visuals_stub=bool(getattr(options, "allow_visuals_stub", False)),
    )
    eligibility["requeued_failed"] = requeued_failed
    gemini_workers = int(getattr(options, "gemini_workers", DEFAULT_GEMINI_WORKERS))
    visuals_policy = None
    if stage == "safe":
        from orchestrator.youtube_full_auto.safe_story_ownership import (
            configure_safe_ownership_persist,
            unlock_stale_safe_claims,
        )

        configure_safe_ownership_persist(batch_root=batch_root)
        unlock_stale_safe_claims(stale_sec=300.0)
    if stage == "visuals":
        from orchestrator.youtube_full_auto.visuals_characters_concurrency import configure_characters_concurrency_gate
        from orchestrator.youtube_full_auto.visuals_execution_policy import resolve_visuals_execution_policy
        from orchestrator.youtube_full_auto.visuals_ownership import configure_visuals_ownership_persist

        visuals_policy = resolve_visuals_execution_policy(options)
        gemini_workers = int(visuals_policy.pool_workers)
        configure_characters_concurrency_gate(limit=int(visuals_policy.characters_workers))
        configure_visuals_ownership_persist(batch_root=batch_root)
        from orchestrator.youtube_full_auto.visuals_ownership import unlock_stale_visuals_claims

        unlock_stale_visuals_claims(stale_sec=300.0)
        from orchestrator.youtube_full_auto.visuals_ramp import reset_visuals_ramp_state

        reset_visuals_ramp_state()
    pool_cfg = _parse_gemini_pool(
        gemini_accounts=str(getattr(options, "gemini_accounts", "") or ""),
        accounts=str(getattr(options, "accounts", "") or ""),
        account_start_index=int(getattr(options, "account_start_index", 0)),
        gemini_workers=gemini_workers,
        gemini_active_workers=int(getattr(options, "gemini_active_workers", 0) or 0),
        stage=stage,
    ) if stage in GEMINI_ACCOUNT_STAGES else None
    account_indices = list(pool_cfg.account_pool) if pool_cfg is not None else []
    active_concurrency = int(pool_cfg.active_concurrency) if pool_cfg is not None else int(gemini_workers)
    parallelism = STAGE_PARALLELISM.get(stage, 1)
    workers = 1 if stage not in GEMINI_STAGES else max(1, min(active_concurrency, MAX_GEMINI_ACCOUNTS))

    pending = _pending_for_stage(
        stage,
        retry_failed=retry_failed,
        allow_visuals_stub=bool(getattr(options, "allow_visuals_stub", False)),
    )
    candidates = store.by_status(*pending)
    if stage == "safe":
        seen_keys = {item.story_key for item in candidates}
        for running_item in store.by_status("safe_running"):
            if running_item.story_key not in seen_keys:
                candidates.append(running_item)
                seen_keys.add(running_item.story_key)
    if stage == "telegram":
        candidates = [item for item in candidates if is_telegram_eligible_item(item)]
        seen: set[str] = set()
        deduped: list[StoryQueueItem] = []
        for item in candidates:
            if item.story_key in seen:
                continue
            seen.add(item.story_key)
            deduped.append(item)
        candidates = deduped
    if limit > 0:
        candidates = candidates[:limit]

    if reporter is not None:
        reporter.begin_stage(stage, total=len(candidates))

    yes_counter = {"count": 0}
    target_yes = int(getattr(options, "target_yes", 0) or 0)

    def stop_when() -> bool:
        from orchestrator.youtube_full_auto.gemini_resilient_supervisor import stage_shutdown_requested

        if stage_shutdown_requested():
            return True
        return target_yes > 0 and yes_counter["count"] >= target_yes

    def on_result(item: StoryQueueItem, outcome: dict[str, Any]) -> None:
        from orchestrator.youtube_full_auto.story_outcome_policy import (
            is_story_terminal_outcome,
            should_persist_queue_failure,
        )

        if not outcome.get("ok") and not should_persist_queue_failure(outcome):
            if stage == "safe":
                store.record_stage(
                    item,
                    stage=stage,
                    status="safe_pending",
                    worker=str(outcome.get("worker_id", "")),
                    account_index=int(outcome.get("account_index", -1)),
                    error=str(outcome.get("error", "") or outcome.get("reason_code", "")),
                    reason_code=str(outcome.get("reason_code", "")),
                    bridge_exit_code=outcome.get("bridge_exit_code"),
                    chrome_profile=str(outcome.get("chrome_profile", "")),
                    proxy_enabled=outcome.get("proxy_enabled"),
                    finished=False,
                )
                item.status = "safe_pending"
                store.save()
            elif stage == "visuals":
                store.record_stage(
                    item,
                    stage=stage,
                    status="visuals_pending",
                    worker=str(outcome.get("worker_id", "")),
                    account_index=int(outcome.get("account_index", -1)),
                    error=str(outcome.get("error", "") or outcome.get("reason_code", "")),
                    reason_code=str(outcome.get("reason_code", "")),
                    bridge_exit_code=outcome.get("bridge_exit_code"),
                    chrome_profile=str(outcome.get("chrome_profile", "")),
                    proxy_enabled=outcome.get("proxy_enabled"),
                    finished=False,
                )
                item.status = "visuals_pending"
                store.save()
            elif stage == "promo":
                store.record_stage(
                    item,
                    stage=stage,
                    status="promo_pending",
                    worker=str(outcome.get("worker_id", "")),
                    account_index=int(outcome.get("account_index", -1)),
                    error=str(outcome.get("error", "") or outcome.get("reason_code", "")),
                    reason_code=str(outcome.get("reason_code", "")),
                    bridge_exit_code=outcome.get("bridge_exit_code"),
                    chrome_profile=str(outcome.get("chrome_profile", "")),
                    proxy_enabled=outcome.get("proxy_enabled"),
                    finished=False,
                )
                item.status = "promo_pending"
                store.save()
            elif stage == "tts":
                reason = str(outcome.get("reason_code", "") or outcome.get("error", ""))
                next_pending = "promo_pending" if reason in {"tts_missing_promo_text", "missing_promo_text"} else "tts_pending"
                store.record_stage(
                    item,
                    stage=stage,
                    status=next_pending,
                    worker=str(outcome.get("worker_id", "")),
                    account_index=int(outcome.get("account_index", -1)),
                    error=str(outcome.get("error", "") or outcome.get("reason_code", "")),
                    reason_code=str(outcome.get("reason_code", "")),
                    bridge_exit_code=outcome.get("bridge_exit_code"),
                    chrome_profile=str(outcome.get("chrome_profile", "")),
                    proxy_enabled=outcome.get("proxy_enabled"),
                    finished=False,
                )
                item.status = next_pending
                store.save()
            rep = getattr(options, "progress_reporter", None) or get_reporter()
            if rep is not None:
                rep._persist_live_summary()
            else:
                store.write_stage_status_snapshot()
            return
        preserved_status = item.status
        status = str(outcome.get("status") or ("failed" if not outcome.get("ok") else "done"))
        if status == "selection_yes":
            yes_counter["count"] += 1
            next_status = "safe_pending"
        elif status == "selection_no":
            next_status = "selection_no"
        elif status in {"manual_review", "manual_review_safe", "manual_review_stage_blocked"}:
            next_status = status
        elif status in {"safe_done", "safe_quality_warning"}:
            next_status = "telegram_pending"
        elif status == "safe_failed_quality":
            next_status = "safe_pending_regen"
        elif status == "safe_pending_regen":
            next_status = "safe_pending_regen"
        elif status == "telegram_assets_ready":
            next_status = preserved_status if preserved_status not in {"selection_yes", "safe_pending", "telegram_pending", "telegram_failed", "failed"} else "promo_pending"
        elif status == "promo_done":
            next_status = "tts_pending"
        elif status == "visuals_done":
            next_status = "ready_for_runpod_images"
        elif status == "ready_for_runpod_images":
            next_status = "frames_pending"
        elif status == "blocked_needs_tts":
            next_status = "blocked_needs_tts"
        elif status == "tts_job_exported":
            next_status = "tts_waiting_colab"
        elif status == "tts_done":
            next_status = "visuals_pending"
        elif status == "package_done":
            next_status = "render_pending"
        elif status == "frames_blocked_missing_runpod_url":
            next_status = status
        elif stage == "telegram" and not outcome.get("ok"):
            next_status = preserved_status
        elif stage == "safe" and not outcome.get("ok"):
            if is_story_terminal_outcome(outcome) and outcome.get("queue_persist") is not False:
                next_status = "failed"
            else:
                next_status = "safe_pending"
        elif stage == "visuals" and not outcome.get("ok"):
            if is_story_terminal_outcome(outcome) and outcome.get("queue_persist") is not False:
                next_status = "failed"
            else:
                next_status = "visuals_pending"
        elif not outcome.get("ok"):
            next_status = "failed"
        else:
            next_status = status
        stage_record_status = "telegram_failed" if stage == "telegram" and not outcome.get("ok") else next_status
        if stage == "telegram" and outcome.get("ok"):
            stage_record_status = "telegram_assets_ready"
        if stage == "safe" and outcome.get("ok") and status in {"safe_done", "safe_quality_warning"}:
            stage_record_status = status
        elif stage == "safe" and not outcome.get("ok") and status == "safe_failed_quality":
            stage_record_status = "safe_failed_quality"
        elif stage == "safe" and not outcome.get("ok") and next_status == "safe_pending":
            stage_record_status = "safe_pending"
        elif stage == "visuals" and not outcome.get("ok") and next_status == "visuals_pending":
            stage_record_status = "visuals_pending"
        store.record_stage(
            item,
            stage=stage,
            status=stage_record_status,
            worker=str(outcome.get("worker_id", "")),
            account_index=int(outcome.get("account_index", -1)),
            output_path=str(outcome.get("output_path", "") or outcome.get("output_story_dir", "")),
            error=str(outcome.get("error", "") or outcome.get("reason_code", "")),
            reason_code=str(outcome.get("reason_code", "")),
            bridge_exit_code=outcome.get("bridge_exit_code"),
            chrome_profile=str(outcome.get("chrome_profile", "")),
            proxy_enabled=outcome.get("proxy_enabled"),
            finished=True,
        )
        if stage in {"telegram", "safe"}:
            item.status = next_status
        store.save()
        rep = getattr(options, "progress_reporter", None) or get_reporter()
        if rep is not None:
            rep._persist_live_summary()
        else:
            store.write_stage_status_snapshot()

    def _handle_outcome(item: StoryQueueItem, outcome: dict[str, Any], *, worker_id: str, account_index: int, started_mono: float) -> None:
        on_result(item, outcome)
        if reporter is not None:
            reporter.on_story_done(
                stage=stage,
                item=item,
                outcome={**outcome, "worker_id": worker_id, "account_index": account_index},
                account_index=account_index,
                worker_id=worker_id,
                started_mono=started_mono,
            )

    def process_one(
        item: StoryQueueItem,
        worker_id: str,
        account_index: int,
        *,
        persistent_session: Any | None = None,
    ) -> dict[str, Any]:
        started_mono = 0.0
        if reporter is not None:
            started_mono = reporter.on_story_start(
                stage=stage,
                item=item,
                account_index=account_index,
                worker_id=worker_id,
            )
        if stage == "safe" and execute:
            store.record_stage(
                item,
                stage=stage,
                status="safe_running",
                worker=worker_id,
                account_index=account_index,
                finished=False,
            )
            item.status = "safe_running"
            store.save()
        elif stage == "visuals" and execute:
            store.record_stage(
                item,
                stage=stage,
                status="visuals_running",
                worker=worker_id,
                account_index=account_index,
                finished=False,
            )
            item.status = "visuals_running"
            store.save()
        elif stage == "promo" and execute:
            store.record_stage(
                item,
                stage=stage,
                status="promo_running",
                worker=worker_id,
                account_index=account_index,
                finished=False,
            )
            item.status = "promo_running"
            store.save()
        outcome: dict[str, Any]
        if stage in ("selection", "safe") and persistent_session is not None:
            outcome = persistent_session.process_story(
                item=item,
                deferred_item=deferred_item_for_story(store, item) if stage == "selection" else None,
            )
        elif stage == "visuals" and persistent_session is not None:
            outcome = persistent_session.process_story(item=item)
            outcome["worker_id"] = worker_id
            outcome["account_index"] = account_index
        elif stage == "selection":
            outcome = run_single_selection(
                config=config,
                item=item,
                youtube_run_id=youtube_run_id,
                deferred_item=deferred_item_for_story(store, item),
                account_index=account_index,
                worker_id=worker_id,
                execute=execute,
                live_logs=bool(getattr(options, "live_logs", True)),
                max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                max_page_reloads=int(getattr(options, "max_page_reloads", 3) or 3),
                max_attach_attempts=int(getattr(options, "max_attach_attempts", 3) or 3),
            )
        elif stage == "safe":
            outcome = run_single_safe(
                config=config,
                item=item,
                account_index=account_index,
                worker_id=worker_id,
                execute=execute,
                skip_existing=skip_existing,
                youtube_run_id=youtube_run_id,
            )
            outcome["worker_id"] = worker_id
            outcome["account_index"] = account_index
        elif stage == "telegram":
            site_run_id = str(store.meta().get("site_run_id") or "")
            outcome = run_single_telegram_assets(
                config=config,
                item=item,
                youtube_run_id=youtube_run_id,
                batch_root=batch_root,
                site_run_id=site_run_id,
                deferred_item=deferred_item_for_story(store, item),
                execute=execute,
                skip_existing=skip_existing,
            )
        elif stage == "promo":
            outcome = run_single_promo(
                config=config,
                item=item,
                account_index=account_index,
                worker_id=worker_id,
                execute=execute,
                skip_existing=skip_existing,
                youtube_run_id=youtube_run_id,
                batch_root=batch_root,
            )
        elif stage == "visuals":
            outcome = run_single_visuals(
                config=config,
                item=item,
                execute=execute,
                skip_existing=skip_existing,
                frames_runpod_url=str(getattr(options, "frames_runpod_url", "") or ""),
                youtube_run_id=youtube_run_id,
            )
            outcome["worker_id"] = worker_id
            outcome["account_index"] = account_index
        elif stage == "tts":
            outcome = run_single_tts_export(
                config=config,
                item=item,
                execute=execute,
                youtube_run_id=youtube_run_id,
            )
        elif stage == "package":
            launch_id = str(getattr(options, "launch_id", "") or youtube_run_id)
            outcome = run_single_package(config=config, item=item, execute=execute, launch_id=launch_id)
        elif stage == "render":
            pod_ssh = str(getattr(options, "pod_ssh", "") or "").strip()
            if not pod_ssh or not bool(getattr(options, "allow_render", False)):
                outcome = {
                    "ok": False,
                    "status": "waiting_or_skipped",
                    "reason_code": "MANUAL_INPUT_NOT_PROVIDED",
                    "error": "missing pod_ssh or --allow-render",
                }
            else:
                outcome = {"ok": False, "status": "blocked", "error": "render stage requires --allow-render and pod_ssh"}
        elif stage == "frames":
            url = str(getattr(options, "frames_runpod_url", "") or "").strip()
            if not url:
                outcome = {
                    "ok": False,
                    "status": "frames_blocked_missing_runpod_url",
                    "reason_code": "MANUAL_INPUT_NOT_PROVIDED",
                    "error": "missing --frames-runpod-url",
                }
            else:
                try:
                    from orchestrator.youtube_visuals_bridge import (
                        YoutubeFramesRunpodBridgeOptions,
                        run_youtube_frames_runpod_bridge,
                    )

                    result = run_youtube_frames_runpod_bridge(
                        config=config,
                        options=YoutubeFramesRunpodBridgeOptions(
                            story_id=item.canonical_basename,
                            runpod_url=url,
                            execute=execute,
                        ),
                    )
                    ok = bool(result.get("ok"))
                    outcome = {
                        "ok": ok,
                        "status": "frames_done" if ok else "failed",
                        "error": "" if ok else str(result.get("message") or result),
                    }
                except Exception as exc:
                    outcome = {"ok": False, "status": "failed", "error": repr(exc)}
        else:
            outcome = {"ok": False, "status": "failed", "error": f"unknown stage {stage}"}
        _handle_outcome(item, outcome, worker_id=worker_id, account_index=account_index, started_mono=started_mono)
        return outcome

    stats: dict[str, Any] = {"processed": 0, "ok": 0, "failed": 0, "workers": workers}

    if stage == "promo" and execute:
        account_index = account_indices[0] if account_indices else 0
        worker_id = "promo-batch-w1"
        active_items: list[StoryQueueItem] = []
        started_by_key: dict[str, float] = {}
        for item in candidates:
            if stop_when():
                break
            started_mono = 0.0
            if reporter is not None:
                started_mono = reporter.on_story_start(
                    stage=stage,
                    item=item,
                    account_index=account_index,
                    worker_id=worker_id,
                )
            started_by_key[item.story_key] = started_mono
            store.record_stage(
                item,
                stage=stage,
                status="promo_running",
                worker=worker_id,
                account_index=account_index,
                finished=False,
            )
            item.status = "promo_running"
            active_items.append(item)
        if active_items:
            store.save()
            if reporter is not None:
                reporter._write_line(
                    f"PROMO_BATCH_BEGIN stories={len(active_items)} account={account_index} worker={worker_id}",
                    stage=stage,
                )
            try:
                outcomes_by_key = run_promo_batch(
                    config=config,
                    items=active_items,
                    account_index=account_index,
                    worker_id=worker_id,
                    execute=execute,
                    skip_existing=skip_existing,
                    youtube_run_id=youtube_run_id,
                    batch_root=batch_root,
                )
            except Exception as exc:
                if reporter is not None:
                    reporter._write_line(
                        f"PROMO_BATCH_ERROR reason=exception detail={exc!r}",
                        stage=stage,
                    )
                outcomes_by_key = {
                    item.story_key: {
                        "ok": False,
                        "status": "failed",
                        "reason_code": "promo_batch_exception",
                        "error": repr(exc),
                        "retryable": True,
                        "terminal_story": False,
                        "queue_persist": False,
                        "worker_id": worker_id,
                        "account_index": account_index,
                    }
                    for item in active_items
                }
            for item in active_items:
                outcome = outcomes_by_key.get(item.story_key)
                if outcome is None:
                    outcome = {
                        "ok": False,
                        "status": "failed",
                        "reason_code": "promo_batch_missing_outcome",
                        "error": "promo batch did not return an outcome for this story",
                        "retryable": True,
                        "terminal_story": False,
                        "queue_persist": False,
                        "worker_id": worker_id,
                        "account_index": account_index,
                    }
                _handle_outcome(
                    item,
                    outcome,
                    worker_id=worker_id,
                    account_index=account_index,
                    started_mono=started_by_key.get(item.story_key, 0.0),
                )
                stats["processed"] += 1
                if outcome.get("ok"):
                    stats["ok"] += 1
                else:
                    stats["failed"] += 1
            if reporter is not None:
                reporter._write_line(
                    f"PROMO_BATCH_END processed={stats['processed']} ok={stats['ok']} failed={stats['failed']}",
                    stage=stage,
                )
        elif reporter is not None:
            reporter._write_line("PROMO_BATCH_SKIPPED stories=0", stage=stage)
    elif workers > 1 and stage in GEMINI_STAGES:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from orchestrator.gemini_worker_scheduler import (
            GeminiRampCoordinator,
            INITIAL_WORKER_START_TIMEOUT_SEC,
            StaggeredSchedulerStartupError,
            normalize_gemini_start_mode,
            set_ramp_coordinator,
        )
        from orchestrator.youtube_full_auto.gemini_resilient_supervisor import (
            ResilientStoryQueue,
            ResilientSupervisorPool,
            check_terminal_pool_state,
            clear_stage_shutdown,
            run_resilient_worker_slot,
            set_supervisor_pool,
        )

        start_mode = normalize_gemini_start_mode(str(getattr(options, "gemini_start_mode", "") or ""))
        if stage == "safe":
            from orchestrator.youtube_full_auto.safe_ramp import reset_safe_ramp_state

            reset_safe_ramp_state()
            start_mode = "staggered-first-result"
        elif stage == "visuals":
            from orchestrator.youtube_full_auto.visuals_execution_policy import (
                resolve_visuals_execution_policy,
                resolve_visuals_start_mode,
            )

            vis_pol = resolve_visuals_execution_policy(options)
            start_mode = resolve_visuals_start_mode(base_mode=start_mode, policy=vis_pol)
            if reporter is not None:
                reporter.log_scheduler_debug(
                    f"[VISUALS_POLICY] characters_workers={vis_pol.characters_workers} "
                    f"scene_prompts_workers={vis_pol.scene_prompts_workers} "
                    f"start_mode={start_mode} director_chunk_timeout={vis_pol.director_chunk_timeout_minutes}m",
                    stage=stage,
                )
        coordinator = GeminiRampCoordinator(
            mode=start_mode,
            slot_count=workers,
            stop_on_system_fail=bool(getattr(options, "ramp_up_stop_on_system_fail", True)),
        )
        shared_queue = ResilientStoryQueue(items=list(candidates))
        from orchestrator.youtube_full_auto.gemini_limit_policy import DEFAULT_ACCOUNT_COOLDOWN_SEC

        if execute and stage in {"selection", "safe", "visuals"}:
            from orchestrator.youtube_full_auto.gem_bot_preflight import run_gem_bot_registry_preflight

            preflight = run_gem_bot_registry_preflight(
                config=config,
                batch_root=batch_root,
                youtube_run_id=youtube_run_id,
                stage=stage,
                gemini_accounts=str(getattr(options, "gemini_accounts", "") or ""),
                gemini_workers=workers,
            )
            valid_preflight = [int(a) for a in (preflight.get("valid_accounts") or [])]
            if valid_preflight:
                account_indices = [a for a in account_indices if int(a) in valid_preflight]
            if reporter is not None:
                reporter.log_scheduler_debug(
                    f"[GEM_BOT] registry_preflight valid={valid_preflight} invalid={preflight.get('invalid_accounts', [])}",
                    stage=stage,
                )
            if not account_indices:
                return {
                    "stage": stage,
                    "workers": 0,
                    "processed": 0,
                    "ok": 0,
                    "failed": 0,
                    "skipped": True,
                    "status": "failed",
                    "reason_code": "no_valid_gem_bots_for_stage",
                    "preflight_report": preflight.get("report_path", ""),
                }

        cooldown_sec = int(getattr(options, "account_cooldown_seconds", 0) or 0)
        if cooldown_sec <= 0:
            cooldown_sec = int(DEFAULT_ACCOUNT_COOLDOWN_SEC)
        from orchestrator.gemini_execution_policy import stage_supervisor_policy_applicability

        stage_policy = stage_supervisor_policy_applicability(stage=stage)
        if reporter is not None:
            reporter.log_scheduler_debug(
                f"[EXECUTION_POLICY] stage={stage} applies={str(stage_policy.get('applies')).lower()} "
                f"max_parallel_browser_launches={getattr(options, 'max_parallel_browser_launches', 1)} "
                f"model_fallback_on_limit={getattr(options, 'model_fallback_on_limit', True)} "
                f"account_cooldown_seconds={cooldown_sec}",
                stage=stage,
            )
        from orchestrator.youtube_full_auto.gemini_account_pool import GeminiAccountPool, set_account_pool

        account_pool_mgr: GeminiAccountPool | None = None
        if pool_cfg is not None:
            account_pool_mgr = GeminiAccountPool(pool_cfg)
            set_account_pool(account_pool_mgr)
            if reporter is not None:
                reporter.configure_account_pool(account_pool_mgr.snapshot())
                reporter.log_scheduler_debug(
                    f"[ACCOUNT_POOL] pool={list(pool_cfg.account_pool)} "
                    f"active_concurrency={pool_cfg.active_concurrency} "
                    f"reserve={list(pool_cfg.reserve_accounts)}",
                    stage=stage,
                )
        resilient_supervisor = ResilientSupervisorPool(
            stage=stage,
            account_indices=account_indices,
            gemini_workers=workers,
            batch_root=batch_root,
            queue=shared_queue,
            project_root=config.root_dir,
            account_pool=account_pool_mgr,
        )
        resilient_supervisor.accounts.cooldown_after_limit_sec = float(cooldown_sec)
        clear_stage_shutdown()
        set_supervisor_pool(resilient_supervisor)
        from orchestrator.youtube_full_auto.worker_liveness import init_liveness_registry, set_liveness_registry

        init_liveness_registry(gemini_workers=workers, account_indices=account_indices)
        coordinator.attach_queue(shared_queue)
        set_ramp_coordinator(coordinator)
        coordinator.mark_pool_started()
        if reporter is not None:
            reporter.log_scheduler_debug(
                f"[SCHEDULER] candidates={len(candidates)} queue_size={shared_queue.size()} "
                f"mode={start_mode} workers={workers}",
                stage=stage,
            )
        if not account_indices:
            raise ValueError("ACCOUNT_NOT_GEMINI_CAPABLE: no Gemini account indices for worker pool")
        if reporter is not None:
            reporter.stop_heartbeat()

        def _worker_log_line(message: str) -> None:
            if reporter is not None:
                reporter.log_scheduler_debug(message, stage=stage)

        def worker_slot(slot_idx: int) -> dict[str, int]:
            from orchestrator.youtube_full_auto.gemini_worker_trace import format_tag

            local = {"processed": 0, "ok": 0, "failed": 0}
            coordinator.register_waiting_for_slot(slot_idx)
            try:
                coordinator.wait_for_slot(slot_idx)
            finally:
                coordinator.unregister_waiting_for_slot(slot_idx)
            worker_id = f"gemini-w{slot_idx + 1}"
            if account_pool_mgr is not None:
                account_index = account_pool_mgr.assign_initial_slot(slot_idx)
            else:
                account_index = account_indices[slot_idx % len(account_indices)]
            coordinator.register_slot_thread_started(slot_idx)

            if shared_queue.remaining() <= 0:
                coordinator.on_slot_finished_without_work(slot_idx)
                _worker_log_line(
                    format_tag(
                        "SLOT",
                        event="skipped_no_work",
                        account=account_index,
                        worker=worker_id,
                        slot=slot_idx,
                        mode=start_mode,
                    )
                )
                return local

            first_story = {"flag": True}

            def _make_persistent_session() -> Any | None:
                if not use_persistent_gemini:
                    return None
                if stage == "selection":
                    from orchestrator.youtube_full_auto.persistent_selection_session import PersistentSelectionSession

                    return PersistentSelectionSession(
                        config=config,
                        batch_root=batch_root,
                        youtube_run_id=youtube_run_id,
                        account_index=account_index,
                        worker_id=worker_id,
                        slot_idx=slot_idx,
                        execute=execute,
                        live_logs=bool(getattr(options, "live_logs", True)),
                        max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                        max_page_reloads=int(getattr(options, "max_page_reloads", 2) or 2),
                        max_attach_attempts=int(getattr(options, "max_attach_attempts", 3) or 3),
                        stories_per_browser=int(getattr(options, "gemini_stories_per_browser", 10) or 10),
                        max_browser_lifetime_minutes=int(getattr(options, "gemini_max_browser_lifetime_minutes", 60) or 60),
                        restart_browser_after_failures=int(getattr(options, "gemini_restart_browser_after_failures", 2) or 2),
                        reuse_stage_results=bool(getattr(options, "reuse_stage_results", False)),
                        model_fallback_on_limit=bool(getattr(options, "model_fallback_on_limit", True)),
                        limit_retry_pause_seconds=(
                            min(60, cooldown_sec) if cooldown_sec > 0 else 60
                        ),
                        stage_key=stage,
                    )
                if stage == "safe":
                    from orchestrator.youtube_full_auto.persistent_safe_session import PersistentSafeSession

                    return PersistentSafeSession(
                        config=config,
                        batch_root=batch_root,
                        youtube_run_id=youtube_run_id,
                        account_index=account_index,
                        worker_id=worker_id,
                        slot_idx=slot_idx,
                        execute=execute,
                        live_logs=bool(getattr(options, "live_logs", True)),
                        max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                        max_page_reloads=int(getattr(options, "max_page_reloads", 2) or 2),
                        max_attach_attempts=int(getattr(options, "max_attach_attempts", 3) or 3),
                        stories_per_browser=int(getattr(options, "gemini_stories_per_browser", 10) or 10),
                        max_browser_lifetime_minutes=int(getattr(options, "gemini_max_browser_lifetime_minutes", 60) or 60),
                        restart_browser_after_failures=int(getattr(options, "gemini_restart_browser_after_failures", 2) or 2),
                        reuse_stage_results=bool(getattr(options, "reuse_stage_results", False)),
                        safe_max_chunks_per_chat=int(getattr(options, "safe_max_chunks_per_chat", 3) or 3),
                        safe_target_ratio=float(getattr(options, "safe_target_ratio", 0.90) or 0.90),
                        safe_min_chunk_ratio=float(getattr(options, "safe_min_chunk_ratio", 0.70) or 0.70),
                        safe_min_story_ratio=float(getattr(options, "safe_min_story_ratio", 0.85) or 0.85),
                        safe_warn_story_ratio=float(getattr(options, "safe_warn_story_ratio", 0.90) or 0.90),
                        safe_max_chunk_retries=int(getattr(options, "safe_max_chunk_retries", 2) or 2),
                    )
                if stage == "visuals":
                    from orchestrator.youtube_full_auto.persistent_visuals_gemini_sessions import (
                        PersistentCharactersSession,
                        PersistentDirectorSession,
                        VisualsPersistentBundle,
                    )

                    _ps_kw = dict(
                        config=config,
                        batch_root=batch_root,
                        youtube_run_id=youtube_run_id,
                        account_index=account_index,
                        worker_id=worker_id,
                        slot_idx=slot_idx,
                        execute=execute,
                        live_logs=bool(getattr(options, "live_logs", True)),
                        max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                        stories_per_browser=int(getattr(options, "gemini_stories_per_browser", 10) or 10),
                        max_browser_lifetime_minutes=int(getattr(options, "gemini_max_browser_lifetime_minutes", 60) or 60),
                        restart_browser_after_failures=int(getattr(options, "gemini_restart_browser_after_failures", 2) or 2),
                        reuse_stage_results=bool(getattr(options, "reuse_stage_results", False)),
                        allow_visuals_stub=bool(getattr(options, "allow_visuals_stub", False)),
                        director_chunk_timeout_minutes=int(
                            getattr(options, "director_chunk_timeout_minutes", 45) or 45
                        ),
                    )
                    return VisualsPersistentBundle(
                        characters=PersistentCharactersSession(**_ps_kw),
                        director=PersistentDirectorSession(**_ps_kw),
                    )
                return None

            def _first_story_hook(outcome: dict[str, Any]) -> None:
                if not first_story["flag"]:
                    return
                if stage in {"safe", "visuals"}:
                    first_story["flag"] = False
                    return
                coordinator.on_first_story_outcome(slot_idx, outcome, stage=stage)
                first_story["flag"] = False
                if reporter is not None:
                    stats_now = coordinator.snapshot_stats()
                    reporter.emit_tag(
                        "RAMP",
                        stage=stage,
                        event="first_outcome",
                        slot=slot_idx,
                        account_index=account_index,
                        ramp_slots_allowed=stats_now["allowed_slots"],
                        worker_threads_started=stats_now["started_slots"],
                        status=str(outcome.get("status") or ""),
                        reason=str(outcome.get("reason_code") or ""),
                    )

            local = run_resilient_worker_slot(
                slot_idx=slot_idx,
                account_index=account_index,
                worker_id=worker_id,
                coordinator=coordinator,
                shared_queue=shared_queue,
                supervisor=resilient_supervisor,
                process_one=process_one,
                session_factory=_make_persistent_session if use_persistent_gemini else None,
                stop_when=stop_when,
                first_story_hook=_first_story_hook,
                account_pool=account_pool_mgr,
            )
            if first_story["flag"] and not int(local.get("startup_failed") or 0):
                coordinator.on_slot_finished_without_work(slot_idx)
            return local

        executor: ThreadPoolExecutor | None = None
        futures: list[Any] = []
        startup_failed = False
        try:
            _sched_debug(reporter, "before_executor", stage=stage)
            executor = ThreadPoolExecutor(max_workers=workers)
            coordinator.mark_executor_created()
            _sched_debug(reporter, f"executor_created max_workers={workers}", stage=stage)
            _sched_debug(reporter, "before_submit_workers", stage=stage)

            futures = []
            account0 = account_indices[0]
            futures.append(executor.submit(worker_slot, 0))
            _sched_debug(reporter, f"submitted slot=0 account={account0}", stage=stage)

            if start_mode != "immediate":
                if reporter is not None:
                    reporter.log_scheduler_debug(
                        f"[RAMP] mode={start_mode} workers={workers} ramp_slots_activated=0",
                        stage=stage,
                    )
                    reporter.log_scheduler_debug(
                        f"[RAMP] activating slot=0 account={account0}",
                        stage=stage,
                    )
                coordinator.ensure_initial_slot_activation(account_index=account0)
                coordinator.mark_activate_slot_called()
                _sched_debug(
                    reporter,
                    f"after_activate_slot0 allowed_slots={coordinator.allowed_slots()}",
                    stage=stage,
                )
                _sched_debug(
                    reporter,
                    f"[RAMP] ramp_slots_activated=1 worker_threads_started=0",
                    stage=stage,
                )

            for slot_idx in range(1, workers):
                account_index = account_indices[slot_idx % len(account_indices)]
                futures.append(executor.submit(worker_slot, slot_idx))
                _sched_debug(
                    reporter,
                    f"submitted slot={slot_idx} account={account_index}",
                    stage=stage,
                )
            coordinator.mark_futures_submitted(len(futures))
            _sched_debug(reporter, f"all_futures_submitted count={len(futures)}", stage=stage)

            if start_mode != "immediate":
                _sched_debug(
                    reporter,
                    f"waiting_initial_worker_started timeout={int(INITIAL_WORKER_START_TIMEOUT_SEC)}",
                    stage=stage,
                )
                coordinator.wait_for_initial_worker_started(
                    timeout_sec=INITIAL_WORKER_START_TIMEOUT_SEC,
                    candidates=len(candidates),
                )
                _sched_debug(reporter, "initial_worker_started ok", stage=stage)

            if reporter is not None:
                stats_now = coordinator.snapshot_stats()
                reporter.log_scheduler_debug(
                    f"[SCHEDULER] startup_ok candidates={len(candidates)} "
                    f"queue_size={shared_queue.size()} mode={start_mode} "
                    f"worker_threads_started={stats_now['started_slots']} "
                    f"ramp_slots_activated={stats_now['allowed_slots']}",
                    stage=stage,
                )
                reporter.enable_heartbeat_after_scheduler_ready(store=store)

            for fut in as_completed(futures):
                part = fut.result()
                stats["processed"] += int(part.get("processed", 0))
                stats["ok"] += int(part.get("ok", 0))
                stats["failed"] += int(part.get("failed", 0))
            remaining = shared_queue.remaining()
            terminal, term_reason = check_terminal_pool_state(supervisor=resilient_supervisor)
            try:
                resilient_supervisor.write_report()
            except Exception:
                pass
            if reporter is not None:
                reporter.stop_heartbeat()
                unique_processed = reporter.unique_terminal_processed_count()
                counter_broken = not reporter.dashboard_counter_invariant_ok()
                pending_after_stage: list[StoryQueueItem] = []
                if int(limit or 0) <= 0 and target_yes <= 0:
                    pending_after_stage = store.by_status(*pending)
                    if stage == "safe":
                        seen_after = {item.story_key for item in pending_after_stage}
                        for running_item in store.by_status("safe_running"):
                            if running_item.story_key not in seen_after:
                                pending_after_stage.append(running_item)
                                seen_after.add(running_item.story_key)
                pending_after_count = len(pending_after_stage)
                pending_after_titles = "; ".join(
                    str(item.canonical_basename or item.story_key)[:80]
                    for item in pending_after_stage[:8]
                )
                if counter_broken or unique_processed > len(candidates):
                    from orchestrator.youtube_full_auto.bridge_errors import (
                        REASON_ALL_PERSISTENT_WORKERS_FAILED_BUT_SUPERVISOR_NOT_TERMINATED,
                    )

                    stats["supervisor_failure"] = True
                    stats["reason_code"] = REASON_ALL_PERSISTENT_WORKERS_FAILED_BUT_SUPERVISOR_NOT_TERMINATED
                    stats["queue_remaining"] = remaining
                    stats["processed_unique"] = unique_processed
                    reporter._write_line(
                        f"STAGE_END stage={stage} status=counter_invariant_broken "
                        f"processed_unique={unique_processed} processed_events={stats.get('processed', 0)} "
                        f"ok={stats.get('ok', 0)} failed={stats.get('failed', 0)} "
                        f"queue_remaining={remaining} candidates={len(candidates)}",
                        stage=stage,
                    )
                elif terminal:
                    stats["supervisor_failure"] = True
                    stats["reason_code"] = term_reason
                    stats["queue_remaining"] = remaining
                    reporter._write_line(
                        f"STAGE_END stage={stage} status=terminal reason_code={term_reason} "
                        f"queue_remaining={remaining} processed={unique_processed} "
                        f"ok={stats.get('ok', 0)} failed={stats.get('failed', 0)} candidates={len(candidates)}",
                        stage=stage,
                    )
                elif pending_after_count > 0:
                    reason_code = f"{stage}_pending_remaining_after_stage"
                    stats["supervisor_failure"] = True
                    stats["reason_code"] = reason_code
                    stats["queue_remaining"] = remaining
                    stats["pending_remaining"] = pending_after_count
                    reporter._write_line(
                        f"STAGE_END stage={stage} status=incomplete reason_code={reason_code} "
                        f"pending_remaining={pending_after_count} queue_remaining={remaining} "
                        f"processed={unique_processed} ok={stats.get('ok', 0)} "
                        f"failed={stats.get('failed', 0)} candidates={len(candidates)} "
                        f"pending_titles=\"{pending_after_titles}\"",
                        stage=stage,
                    )
                elif remaining > 0 and unique_processed < len(candidates):
                    from orchestrator.youtube_full_auto.bridge_errors import REASON_NO_ACTIVE_WORKERS_WITH_PENDING_QUEUE

                    stats["supervisor_failure"] = True
                    stats["reason_code"] = REASON_NO_ACTIVE_WORKERS_WITH_PENDING_QUEUE
                    stats["queue_remaining"] = remaining
                    reporter._write_line(
                        f"STAGE_END stage={stage} status=terminal reason_code={REASON_NO_ACTIVE_WORKERS_WITH_PENDING_QUEUE} "
                        f"queue_remaining={remaining} processed={unique_processed} "
                        f"ok={stats.get('ok', 0)} failed={stats.get('failed', 0)} candidates={len(candidates)}",
                        stage=stage,
                    )
                else:
                    reporter._write_line(
                        f"STAGE_END stage={stage} status=completed processed={unique_processed} "
                        f"ok={stats.get('ok', 0)} failed={stats.get('failed', 0)} "
                        f"queue_remaining={remaining} candidates={len(candidates)}",
                        stage=stage,
                    )
        except StaggeredSchedulerStartupError as exc:
            startup_failed = True
            if reporter is not None:
                reporter.log_scheduler_debug(f"startup_failed reason={exc}", stage=stage)
                reporter.emit_tag("FAIL", stage=stage, reason_code=str(exc).split()[0], detail=str(exc))
            raise
        finally:
            if executor is not None:
                executor.shutdown(wait=not startup_failed, cancel_futures=startup_failed)
            set_ramp_coordinator(None)
            set_supervisor_pool(None)
            from orchestrator.youtube_full_auto.worker_liveness import set_liveness_registry

            set_liveness_registry(None)

        paths = store.paths
        paths["worker_assignments"].parent.mkdir(parents=True, exist_ok=True)
        paths["worker_assignments"].write_text(
            json.dumps(
                {
                    "updated_at": utc_now(),
                    "workers": workers,
                    "stage": stage,
                    "mode": start_mode,
                    "queue_mode": "shared_claim_ramp",
                    "account_indices": account_indices,
                    "activated_slots_final": coordinator.activated_slots(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        account_index = account_indices[0] if account_indices else 0
        persistent_session = None
        if use_persistent_gemini:
            if stage == "selection":
                from orchestrator.youtube_full_auto.persistent_selection_session import PersistentSelectionSession

                persistent_session = PersistentSelectionSession(
                    config=config,
                    batch_root=batch_root,
                    youtube_run_id=youtube_run_id,
                    account_index=account_index,
                    worker_id="sequential-w1",
                    slot_idx=0,
                    execute=execute,
                    live_logs=bool(getattr(options, "live_logs", True)),
                    max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                    max_page_reloads=int(getattr(options, "max_page_reloads", 2) or 2),
                    max_attach_attempts=int(getattr(options, "max_attach_attempts", 3) or 3),
                    stories_per_browser=int(getattr(options, "gemini_stories_per_browser", 10) or 10),
                    max_browser_lifetime_minutes=int(getattr(options, "gemini_max_browser_lifetime_minutes", 60) or 60),
                    restart_browser_after_failures=int(getattr(options, "gemini_restart_browser_after_failures", 2) or 2),
                )
            elif stage == "safe":
                from orchestrator.youtube_full_auto.persistent_safe_session import PersistentSafeSession

                persistent_session = PersistentSafeSession(
                    config=config,
                    batch_root=batch_root,
                    youtube_run_id=youtube_run_id,
                    account_index=account_index,
                    worker_id="sequential-w1",
                    slot_idx=0,
                    execute=execute,
                    live_logs=bool(getattr(options, "live_logs", True)),
                    max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                    max_page_reloads=int(getattr(options, "max_page_reloads", 2) or 2),
                    max_attach_attempts=int(getattr(options, "max_attach_attempts", 3) or 3),
                    stories_per_browser=int(getattr(options, "gemini_stories_per_browser", 10) or 10),
                    max_browser_lifetime_minutes=int(getattr(options, "gemini_max_browser_lifetime_minutes", 60) or 60),
                    restart_browser_after_failures=int(getattr(options, "gemini_restart_browser_after_failures", 2) or 2),
                    reuse_stage_results=bool(getattr(options, "reuse_stage_results", False)),
                )
            elif stage == "visuals":
                from orchestrator.youtube_full_auto.persistent_visuals_gemini_sessions import (
                    PersistentCharactersSession,
                    PersistentDirectorSession,
                    VisualsPersistentBundle,
                )

                _ps_kw = dict(
                    config=config,
                    batch_root=batch_root,
                    youtube_run_id=youtube_run_id,
                    account_index=account_index,
                    worker_id="sequential-w1",
                    slot_idx=0,
                    execute=execute,
                    live_logs=bool(getattr(options, "live_logs", True)),
                    max_story_runtime_minutes=int(getattr(options, "max_story_runtime_minutes", 20) or 20),
                    stories_per_browser=int(getattr(options, "gemini_stories_per_browser", 10) or 10),
                    max_browser_lifetime_minutes=int(getattr(options, "gemini_max_browser_lifetime_minutes", 60) or 60),
                    restart_browser_after_failures=int(getattr(options, "gemini_restart_browser_after_failures", 2) or 2),
                    reuse_stage_results=bool(getattr(options, "reuse_stage_results", False)),
                    allow_visuals_stub=bool(getattr(options, "allow_visuals_stub", False)),
                    director_chunk_timeout_minutes=int(
                        getattr(options, "director_chunk_timeout_minutes", 45) or 45
                    ),
                )
                persistent_session = VisualsPersistentBundle(
                    characters=PersistentCharactersSession(**_ps_kw),
                    director=PersistentDirectorSession(**_ps_kw),
                )
        try:
            for item in candidates:
                if stop_when():
                    break
                outcome = process_one(
                    item,
                    "sequential-w1",
                    account_index,
                    persistent_session=persistent_session,
                )
                stats["processed"] += 1
                if outcome.get("ok"):
                    stats["ok"] += 1
                else:
                    stats["failed"] += 1
                if persistent_session is not None and persistent_session.hard_failed:
                    break
        finally:
            if persistent_session is not None:
                persistent_session.close(reason="queue_empty")

    if stage == "telegram":
        report_paths = write_telegram_assets_report(batch_root=batch_root, store=store, stage_stats=stats)
        stats["telegram_assets_report_json"] = report_paths.get("json", "")
        stats["telegram_assets_report_md"] = report_paths.get("md", "")

    if stage == "promo" and execute and int(stats.get("failed", 0) or 0) > 0:
        pending_after_stage = store.by_status("promo_pending", "promo_running")
        pending_after_titles = "; ".join(
            str(item.canonical_basename or item.story_key)[:80]
            for item in pending_after_stage[:8]
        )
        reason_code = "promo_pending_remaining_after_stage" if pending_after_stage else "promo_failed_after_stage"
        stats["supervisor_failure"] = True
        stats["reason_code"] = reason_code
        stats["pending_remaining"] = len(pending_after_stage)
        if reporter is not None:
            reporter._write_line(
                f"STAGE_END stage=promo status=incomplete reason_code={reason_code} "
                f"pending_remaining={len(pending_after_stage)} processed={stats.get('processed', 0)} "
                f"ok={stats.get('ok', 0)} failed={stats.get('failed', 0)} candidates={len(candidates)} "
                f"pending_titles=\"{pending_after_titles}\"",
                stage=stage,
            )

    supervisor_failure = bool(stats.get("supervisor_failure"))
    return {
        "stage": stage,
        "workers": workers,
        "stage_parallelism": parallelism,
        "stage_parallelism_reason": STAGE_PARALLELISM_REASON.get(stage, ""),
        "candidates": len(candidates),
        **stats,
        "eligibility": eligibility,
        "ok": not supervisor_failure,
        "status": "failed" if supervisor_failure else "done",
        "reason_code": str(stats.get("reason_code") or ""),
    }


def run_youtube_full_auto(*, config: OrchestratorConfig, options: YoutubeFullAutoOptions) -> dict[str, Any]:
    batch_root, store, meta = _init_or_load_queue(config, options)
    youtube_run_id = meta["youtube_run_id"]
    resolver = resolver_if_isolated(config, launch_id=youtube_run_id)
    with isolated_session(resolver, batch_launch_id=youtube_run_id):
        return _run_youtube_full_auto_body(
            config=config,
            options=options,
            batch_root=batch_root,
            store=store,
            youtube_run_id=youtube_run_id,
        )


def _run_youtube_full_auto_body(
    *,
    config: OrchestratorConfig,
    options: YoutubeFullAutoOptions,
    batch_root: Path,
    store: QueueStore,
    youtube_run_id: str,
) -> dict[str, Any]:
    stages = _parse_stages(options.stages)
    if not options.allow_render and "render" in stages:
        reporter = options.progress_reporter or get_reporter()
        if reporter is not None:
            reporter._write_line(
                "STAGE_SKIPPED stage=render status=waiting_or_skipped reason_code=MANUAL_INPUT_NOT_PROVIDED"
            )
        stages = [s for s in stages if s != "render"]
    if options.force_stage:
        stages = [_parse_stages(options.force_stage)[0]] if _parse_stages(options.force_stage) else stages
    if options.stop_after:
        stop = options.stop_after.strip().lower()
        if stop in STAGE_NAMES:
            idx = stages.index(stop) + 1 if stop in stages else stages.index(stop) + 1 if stop in stages else len(stages)
            try:
                stages = stages[: stages.index(stop) + 1]
            except ValueError:
                stages = [stop]

    execute = bool(options.execute) and not bool(options.dry_run)
    stage_results: list[dict[str, Any]] = []

    if not execute:
        from orchestrator.gemini_execution_policy import build_execution_policy_report

        counts = store.count_by_status()
        eligible = counts.get("selection_pending", 0)
        policy_report = build_execution_policy_report(policy=vars(options), stages=stages)
        return {
            "ok": True,
            "status": "dry_run",
            "execute": False,
            "youtube_run_id": youtube_run_id,
            "batch_root": str(batch_root),
            "site_run_id": store.meta().get("site_run_id"),
            "stages_planned": stages,
            "total_stories": len(store.items()),
            "length_eligible": eligible,
            "counts": counts,
            "gemini_workers": options.gemini_workers,
            "limit": options.limit,
            "target_yes": options.target_yes,
            "queue_path": str(store.paths["queue_json"]),
            "gemini_execution_policy": policy_report,
        }

    reset_claim_index()
    from orchestrator.gemini_execution_policy import build_execution_policy_report, write_execution_policy_report

    policy_report = build_execution_policy_report(policy=vars(options), stages=stages)
    policy_report_path = write_execution_policy_report(
        batch_root=batch_root,
        policy=vars(options),
        stages=stages,
    )
    reporter = options.progress_reporter or get_reporter()
    owns_reporter = False
    if reporter is None and execute:
        from orchestrator.gemini_colab_proxy import load_colab_proxy_settings
        from orchestrator.youtube_full_auto.progress_reporter import (
            compute_plan_stats,
            create_progress_reporter_for_run,
            set_reporter,
        )

        account_indices = _parse_accounts(
            gemini_accounts=str(options.gemini_accounts or ""),
            accounts=str(options.accounts or ""),
            account_start_index=int(options.account_start_index),
            gemini_workers=int(options.gemini_workers),
            stage=stages[0] if stages else "selection",
        )
        proxy_enabled, proxy_host = False, ""
        try:
            settings = load_colab_proxy_settings(config.root_dir)
            fields = settings.report_fields(proxy_enabled=True, local_bridge_url="", bridge_error="")
            proxy_host = str(fields.get("upstream_proxy_host_port") or fields.get("proxy_host_masked") or "")
            proxy_enabled = True
        except Exception:
            pass
        reporter = create_progress_reporter_for_run(
            config=config,
            batch_root=batch_root,
            youtube_run_id=youtube_run_id,
            site_run_id=str(store.meta().get("site_run_id") or ""),
            gemini_workers=int(options.gemini_workers),
            gemini_accounts=account_indices or list(range(min(5, int(options.gemini_workers)))),
            stages=stages,
            proxy_enabled=proxy_enabled,
            proxy_host=proxy_host,
            live_logs=bool(options.live_logs),
            heartbeat_seconds=float(options.heartbeat_seconds),
            dashboard_mode=str(getattr(options, "dashboard_mode", "production") or "production"),
            dashboard_interval=float(getattr(options, "dashboard_interval", 60.0) or 60.0),
            visuals_dashboard=str(getattr(options, "visuals_dashboard", "production") or "production"),
        )
        set_reporter(reporter)
        owns_reporter = True
        plan = compute_plan_stats(store)
        plan.update(
            {
                "limit_label": str(options.limit) if options.limit else "NONE",
                "target_yes_label": str(options.target_yes) if options.target_yes else "NONE",
                "resume": options.resume,
                "execute": execute,
            }
        )
        reporter.log_plan(plan=plan)
        options.progress_reporter = reporter

    if reporter is not None:
        reporter._write_line(
            "EXECUTION_POLICY "
            + f"report={policy_report_path} "
            + f"start_mode={policy_report['policy'].get('gemini_start_mode')} "
            + f"max_parallel_browser_launches={policy_report['policy'].get('max_parallel_browser_launches')} "
            + f"model_fallback_on_limit={policy_report['policy'].get('model_fallback_on_limit')} "
            + f"account_cooldown_seconds={policy_report['policy'].get('account_cooldown_seconds')} "
            + f"supervisor_stages={','.join(policy_report.get('supervisor_policy_applicable_stages') or [])}"
        )
        reporter.set_store(store)
        if execute:
            if _should_defer_heartbeat_for_run(options=options, stages=stages):
                reporter.defer_heartbeat(store=store, interval_seconds=float(options.heartbeat_seconds))
            else:
                reporter.start_heartbeat(store=store, interval_seconds=float(options.heartbeat_seconds))

    run_failed = False
    interrupted = False
    current_stage = ""
    try:
        for stage in stages:
            current_stage = stage
            stage_options = options
            if stage == "safe" and execute:
                from dataclasses import replace

                from orchestrator.account_capabilities import resolve_gemini_account_indices
                from orchestrator.youtube_full_auto.safe_account_mapping import (
                    build_safe_account_mapping,
                    format_safe_mapping_table,
                )
                from orchestrator.youtube_full_auto.safe_accounts_preflight import run_safe_accounts_preflight

                account_indices, _map_warn = resolve_gemini_account_indices(
                    gemini_accounts=str(getattr(options, "gemini_accounts", "") or ""),
                    gemini_workers=int(getattr(options, "gemini_workers", DEFAULT_GEMINI_WORKERS)),
                    strict_invalid=True,
                )
                mapping_rows = build_safe_account_mapping(config=config, account_indices=account_indices)
                mapping_table = format_safe_mapping_table(mapping_rows)
                if reporter is not None:
                    reporter._write_line(mapping_table, stage="safe")
                else:
                    print(mapping_table, flush=True)

                safe_pending_statuses = _pending_for_stage(
                    "safe",
                    retry_failed=bool(getattr(options, "retry_failed", False)),
                    allow_visuals_stub=bool(getattr(options, "allow_visuals_stub", False)),
                )
                safe_pending_count = len(store.by_status(*safe_pending_statuses))
                if safe_pending_count <= 0:
                    if reporter is not None:
                        reporter._write_line("[SAFE_PREFLIGHT] skipped reason=no_safe_candidates", stage="safe")
                else:
                    requested_workers = int(getattr(options, "gemini_workers", DEFAULT_GEMINI_WORKERS))
                    required_usable = max(1, min(len(account_indices), max(1, requested_workers), safe_pending_count))
                    preflight = run_safe_accounts_preflight(
                        config=config,
                        batch_root=batch_root,
                        youtube_run_id=youtube_run_id,
                        gemini_accounts=str(getattr(options, "gemini_accounts", "") or ""),
                        gemini_workers=requested_workers,
                        execute=True,
                        required_usable=required_usable,
                        per_account_timeout_seconds=90,
                    )
                    if not preflight.get("ok"):
                        run_failed = True
                        stage_results.append(
                            {
                                "stage": "safe",
                                "ok": False,
                                "status": "failed",
                                "reason_code": str(preflight.get("reason_code") or "safe_bot_preflight_failed"),
                                "preflight": preflight,
                                "skipped": True,
                            }
                        )
                        if reporter is not None:
                            reporter.stop_heartbeat()
                        break
                    usable_accounts = [int(a) for a in (preflight.get("usable_accounts") or [])]
                    if usable_accounts:
                        safe_workers = max(1, min(requested_workers, len(usable_accounts), safe_pending_count))
                        stage_options = replace(
                            options,
                            gemini_accounts=",".join(str(a) for a in usable_accounts),
                            gemini_workers=safe_workers,
                        )
                        if reporter is not None:
                            reporter._write_line(
                                "[SAFE_PREFLIGHT] "
                                f"usable_accounts={','.join(str(a) for a in usable_accounts)} "
                                f"required={required_usable} safe_workers={safe_workers}",
                                stage="safe",
                            )
            result = _run_stage_batch(
                config=config,
                store=store,
                batch_root=batch_root,
                youtube_run_id=youtube_run_id,
                stage=stage,
                options=stage_options,
            )
            stage_results.append(result)
            if result.get("supervisor_failure") or result.get("ok") is False:
                run_failed = True
                if reporter is not None:
                    reporter.stop_heartbeat()
                break
            if options.stop_after and stage == options.stop_after.strip().lower():
                break
    except KeyboardInterrupt:
        interrupted = True
        run_failed = True
        from orchestrator.youtube_full_auto.gemini_resilient_supervisor import get_supervisor_pool, write_run_interrupted_by_user

        pool = get_supervisor_pool()
        queue_remaining = int(pool.queue.remaining()) if pool is not None else 0
        write_run_interrupted_by_user(
            batch_root=batch_root,
            youtube_run_id=youtube_run_id,
            stage=current_stage,
            queue_remaining=queue_remaining,
            partial_stats={"stage_results": stage_results},
        )
        if reporter is not None:
            reporter.stop_heartbeat()
            reporter._write_line(f"RUN_INTERRUPTED_BY_USER run={youtube_run_id} stage={current_stage}")
    finally:
        if reporter is not None:
            if owns_reporter:
                from orchestrator.youtube_full_auto.progress_reporter import set_reporter

                set_reporter(None)
            else:
                reporter.shutdown()

    summary = write_full_auto_summary(store, stage_results=stage_results)
    return {
        "ok": not run_failed,
        "status": "interrupted" if interrupted else ("failed" if run_failed else "done"),
        "execute": True,
        "youtube_run_id": youtube_run_id,
        "batch_root": str(batch_root),
        "stage_results": stage_results,
        "counts": store.count_by_status(),
        "summary_path": str(summary.get("summary_json")),
        "gemini_execution_policy_report": str(policy_report_path),
        "gemini_execution_policy": policy_report,
    }


def run_youtube_batch_stage(*, config: OrchestratorConfig, options: YoutubeBatchStageOptions) -> dict[str, Any]:
    batch_root = batch_launch_root(config, options.youtube_run_id)
    if not batch_root.is_dir():
        return {"ok": False, "message": f"batch launch not found: {batch_root}"}
    store = QueueStore(batch_root, config=config, youtube_run_id=options.youtube_run_id)
    stage = str(options.stage).strip().lower()
    if stage not in STAGE_NAMES:
        return {"ok": False, "message": f"invalid stage: {stage}"}
    if stage == "render":
        return {"ok": False, "message": "use full-auto --allow-render for render stage"}

    execute = bool(options.execute)
    reporter = options.progress_reporter or get_reporter()
    owns_reporter = False
    if reporter is None and execute:
        from orchestrator.gemini_colab_proxy import load_colab_proxy_settings
        from orchestrator.youtube_full_auto.progress_reporter import create_progress_reporter_for_run, set_reporter

        account_indices = _parse_accounts(
            gemini_accounts=str(options.gemini_accounts or ""),
            accounts=str(options.accounts or ""),
            account_start_index=int(options.account_start_index),
            gemini_workers=int(options.gemini_workers),
            stage=stage,
        )
        proxy_enabled, proxy_host = False, ""
        try:
            settings = load_colab_proxy_settings(config.root_dir)
            fields = settings.report_fields(proxy_enabled=True, local_bridge_url="", bridge_error="")
            proxy_host = str(fields.get("upstream_proxy_host_port") or fields.get("proxy_host_masked") or "")
            proxy_enabled = True
        except Exception:
            pass
        reporter = create_progress_reporter_for_run(
            config=config,
            batch_root=batch_root,
            youtube_run_id=options.youtube_run_id,
            site_run_id=str(store.meta().get("site_run_id") or ""),
            gemini_workers=int(options.gemini_workers),
            gemini_accounts=account_indices or list(range(min(5, int(options.gemini_workers)))),
            stages=[stage],
            proxy_enabled=proxy_enabled,
            proxy_host=proxy_host,
            live_logs=bool(options.live_logs),
            heartbeat_seconds=float(options.heartbeat_seconds),
            dashboard_mode=str(getattr(options, "dashboard_mode", "production") or "production"),
            dashboard_interval=float(getattr(options, "dashboard_interval", 60.0) or 60.0),
            visuals_dashboard=str(getattr(options, "visuals_dashboard", "production") or "production"),
        )
        set_reporter(reporter)
        options.progress_reporter = reporter
        owns_reporter = True

    try:
        result = _run_stage_batch(
            config=config,
            store=store,
            batch_root=batch_root,
            youtube_run_id=options.youtube_run_id,
            stage=stage,
            options=options,
        )
    finally:
        if owns_reporter and reporter is not None:
            from orchestrator.youtube_full_auto.progress_reporter import set_reporter

            reporter.shutdown()
            set_reporter(None)

    write_full_auto_summary(store, stage_results=[result])
    eligibility = result.get("eligibility") if isinstance(result.get("eligibility"), dict) else {}
    processed = int(result.get("processed", 0) or 0)
    ok_count = int(result.get("ok", 0) or 0)
    failed_count = int(result.get("failed", 0) or 0)
    eligible = int(eligibility.get("eligible", result.get("candidates", 0)) or 0)
    reason = str(eligibility.get("reason") or "")
    if processed == 0 and eligible == 0:
        batch_ok = True
        batch_status = "no_op"
    elif processed > 0 and ok_count == 0 and failed_count > 0:
        batch_ok = False
        batch_status = "failed"
    elif failed_count > 0 and ok_count > 0:
        batch_ok = True
        batch_status = "partial"
    else:
        batch_ok = True
        batch_status = "done"
    return {
        "ok": batch_ok,
        "status": batch_status,
        "stage": stage,
        "result": result,
        "eligibility": eligibility,
        "counts": store.count_by_status(),
        "no_op_reason": reason if batch_status == "no_op" else "",
    }


def run_youtube_full_auto_status(
    *, config: OrchestratorConfig, youtube_run_id: str, human: bool = False
) -> dict[str, Any]:
    batch_root = batch_launch_root(config, youtube_run_id)
    store = QueueStore(batch_root, config=config, youtube_run_id=youtube_run_id)
    counts = store.count_by_status()
    payload = {
        "ok": batch_root.is_dir(),
        "youtube_run_id": youtube_run_id,
        "batch_root": str(batch_root),
        "total": len(store.items()),
        "counts": counts,
        "meta": store.meta(),
        "queue_path": str(store.paths["queue_json"]),
        "logs_dir": str(batch_root / "logs"),
        "reports_dir": str(batch_root / "reports"),
    }
    if human and batch_root.is_dir():
        from orchestrator.youtube_full_auto.progress_reporter import format_status_dashboard

        payload["dashboard"] = format_status_dashboard(
            store=store,
            youtube_run_id=youtube_run_id,
            batch_root=batch_root,
        )
    return payload


def run_youtube_full_auto_report(*, config: OrchestratorConfig, youtube_run_id: str) -> dict[str, Any]:
    batch_root = batch_launch_root(config, youtube_run_id)
    store = QueueStore(batch_root, config=config, youtube_run_id=youtube_run_id)
    summary = write_full_auto_summary(store, stage_results=[])
    return {"ok": True, "summary_path": str(summary.get("summary_json")), "summary_md": str(summary.get("summary_md")), "counts": store.count_by_status()}


def run_youtube_full_auto_retry_failed(
    *, config: OrchestratorConfig, youtube_run_id: str, stage: str, execute: bool
) -> dict[str, Any]:
    batch_root = batch_launch_root(config, youtube_run_id)
    store = QueueStore(batch_root, config=config, youtube_run_id=youtube_run_id)
    _requeue_failed_items_for_stage(store, stage)
    store.save()
    opts = YoutubeBatchStageOptions(youtube_run_id=youtube_run_id, stage=stage, execute=execute, retry_failed=True)
    return run_youtube_batch_stage(config=config, options=opts)
