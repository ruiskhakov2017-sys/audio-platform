"""One-click YouTube full-auto production entry (preflight → all stages → report)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.account_capabilities import resolve_gemini_account_indices
from orchestrator.config import OrchestratorConfig
from orchestrator.gemini_execution_policy import (
    DEFAULT_GEMINI_ACCOUNTS,
    DEFAULT_GEMINI_STORIES_PER_BROWSER,
    DEFAULT_GEMINI_WORKERS,
)
from orchestrator.gemini_colab_proxy import load_colab_proxy_settings
from orchestrator.youtube_full_auto.browser_cleanup import cleanup_gemini_browser_profiles
from orchestrator.youtube_full_auto.constants import STAGE_NAMES
from orchestrator.youtube_full_auto.gemini_preflight import (
    YoutubeGeminiPreflightOptions,
    run_youtube_full_auto_preflight,
)
from orchestrator.youtube_full_auto.layout import batch_launch_root, default_youtube_run_id, ensure_batch_layout
from orchestrator.youtube_full_auto.orchestrator import (
    YoutubeFullAutoOptions,
    _init_or_load_queue,
    run_youtube_full_auto,
)
from orchestrator.youtube_full_auto.progress_reporter import (
    FullAutoProgressReporter,
    compute_plan_stats,
    create_progress_reporter_for_run,
    set_reporter,
)
from orchestrator.youtube_full_auto.queue_store import QueueStore

DEFAULT_ONECLICK_STAGES = "selection,safe,promo,visuals,telegram,tts,frames,package,render"


@dataclass
class YoutubeFullAutoOneclickOptions:
    site_run_id: str = "auto"
    youtube_run_id: str = ""
    from_site_approved: bool = True
    min_words: int = 0
    max_words: int = 0
    all_eligible: bool = True
    gemini_workers: int = DEFAULT_GEMINI_WORKERS
    gemini_accounts: str = DEFAULT_GEMINI_ACCOUNTS
    gemini_start_mode: str = "staggered-first-result"
    gemini_session_mode: str = "persistent-account"
    gemini_stories_per_browser: int = DEFAULT_GEMINI_STORIES_PER_BROWSER
    reuse_stage_results: bool = True
    live_logs: bool = True
    heartbeat_seconds: float = 15.0
    ramp_up_stop_on_system_fail: bool = True
    stages: str = DEFAULT_ONECLICK_STAGES
    limit: int = 0
    target_yes: int = 0
    only_story: str = ""
    resume: bool = True
    execute: bool = True
    cleanup_browsers_before_run: bool = False
    interactive: bool = False
    allow_render: bool = False
    frames_runpod_url: str = ""
    pod_ssh: str = ""
    proxy_config: Path | None = None


def _proxy_host_label(config: OrchestratorConfig, proxy_config: Path | None) -> tuple[bool, str]:
    try:
        settings = load_colab_proxy_settings(config.root_dir, config_path=proxy_config)
        fields = settings.report_fields(proxy_enabled=True, local_bridge_url="", bridge_error="")
        host = str(fields.get("upstream_proxy_host_port") or fields.get("proxy_host_masked") or "")
        return True, host
    except Exception:
        return False, ""


def run_youtube_full_auto_oneclick(
    *,
    config: OrchestratorConfig,
    options: YoutubeFullAutoOneclickOptions,
) -> dict[str, Any]:
    youtube_run_id = str(options.youtube_run_id or "").strip() or default_youtube_run_id()
    account_indices, _warnings = resolve_gemini_account_indices(
        gemini_accounts=options.gemini_accounts,
        gemini_workers=options.gemini_workers,
        strict_invalid=True,
    )
    stages = [s.strip().lower() for s in options.stages.split(",") if s.strip().lower() in STAGE_NAMES]
    if not stages:
        stages = [s for s in DEFAULT_ONECLICK_STAGES.split(",")]

    batch_root = ensure_batch_layout(config, youtube_run_id)
    proxy_enabled, proxy_host = _proxy_host_label(config, options.proxy_config)

    reporter = create_progress_reporter_for_run(
        batch_root=batch_root,
        youtube_run_id=youtube_run_id,
        config=config,
        site_run_id=options.site_run_id,
        gemini_workers=options.gemini_workers,
        gemini_accounts=account_indices,
        stages=stages,
        proxy_enabled=proxy_enabled,
        proxy_host=proxy_host,
        live_logs=True,
        heartbeat_seconds=15.0,
    )
    set_reporter(reporter)

    preflight_result: dict[str, Any] = {}
    cleanup_result: dict[str, Any] = {}
    profiles_unlocked = True

    if options.execute:
        reporter._write_line("ONCLICK_STEP step=preflight")
        preflight_result = run_youtube_full_auto_preflight(
            config=config,
            options=YoutubeGeminiPreflightOptions(
                stage="selection",
                gemini_workers=options.gemini_workers,
                gemini_accounts=options.gemini_accounts,
                check_proxy=True,
                proxy_config=options.proxy_config,
            ),
        )
        if not preflight_result.get("can_run_selection"):
            set_reporter(None)
            return {
                "ok": False,
                "message": "preflight failed: no working Gemini accounts",
                "preflight": preflight_result,
            }

        from orchestrator.voice_contract import run_voice_preflight_guard, voice_mapping_plan_lines
        from orchestrator.site_tts.config import load_site_tts_settings

        voice_guard = run_voice_preflight_guard(
            config=config,
            youtube_run_id=youtube_run_id,
            site_run_id=options.site_run_id,
        )
        try:
            for line in voice_mapping_plan_lines(load_site_tts_settings(config.root_dir)):
                reporter._write_line(line)
        except FileNotFoundError:
            reporter._write_line("VOICE MAPPING unavailable: configs/site_tts.yaml missing")
        if not voice_guard.get("can_run_full_corpus"):
            set_reporter(None)
            return {
                "ok": False,
                "message": voice_guard.get("reason_code") or "voice preflight failed",
                "voice_guard": voice_guard,
                "preflight": preflight_result,
            }
        reporter._write_line(f"VOICE_GUARD ok can_run_full_corpus=true")
        proxy_enabled = bool(preflight_result.get("proxy_enabled"))
        proxy_host = str(preflight_result.get("upstream_proxy_host_port") or preflight_result.get("proxy_host_masked") or proxy_host)
        reporter.proxy_enabled = proxy_enabled
        reporter.proxy_host = proxy_host

        if options.cleanup_browsers_before_run:
            reporter._write_line("ONCLICK_STEP step=cleanup_browsers")
            cleanup_result = cleanup_gemini_browser_profiles(
                config=config,
                gemini_accounts=options.gemini_accounts,
                stop_processes=True,
            )
            profiles_unlocked = bool(cleanup_result.get("ok"))

    init_opts = YoutubeFullAutoOptions(
        site_run_id=options.site_run_id,
        youtube_run_id=youtube_run_id,
        from_site_approved=options.from_site_approved,
        min_words=int(options.min_words or YoutubeFullAutoOptions.min_words),
        max_words=int(options.max_words or YoutubeFullAutoOptions.max_words),
        gemini_workers=options.gemini_workers,
        gemini_accounts=options.gemini_accounts,
        stages=",".join(stages),
        limit=0 if options.all_eligible else int(options.limit or 0),
        target_yes=0 if options.all_eligible else int(options.target_yes or 0),
        only_story=options.only_story,
        resume=options.resume,
        execute=False,
        dry_run=True,
        frames_runpod_url=options.frames_runpod_url,
        pod_ssh=options.pod_ssh,
        allow_render=options.allow_render,
        progress_reporter=reporter,
        interactive=options.interactive,
    )
    batch_root, store, meta = _init_or_load_queue(config, init_opts)
    reporter.site_run_id = str(meta.get("site_run_id") or options.site_run_id)
    reporter.set_store(store)
    plan = compute_plan_stats(store)
    plan.update(
        {
            "limit_label": "NONE" if options.all_eligible else str(options.limit or 0),
            "target_yes_label": "NONE" if options.all_eligible else str(options.target_yes or 0),
            "resume": options.resume,
            "execute": options.execute,
            "profiles_unlocked": profiles_unlocked,
        }
    )
    reporter.log_plan(plan=plan)

    if not options.execute:
        set_reporter(None)
        return {
            "ok": True,
            "status": "plan_only",
            "youtube_run_id": youtube_run_id,
            "batch_root": str(batch_root),
            "plan": plan,
            "stages": stages,
        }

    frames_url = str(options.frames_runpod_url or "").strip()
    pod_ssh = str(options.pod_ssh or "").strip()
    allow_render = bool(options.allow_render)

    if options.interactive and "frames" in stages and not frames_url:
        frames_url = reporter.log_waiting_for_input(stage="frames", required="frames_runpod_url") or ""

    if options.interactive and "render" in stages:
        if not pod_ssh:
            pod_ssh = reporter.log_waiting_for_input(stage="render", required="pod_ssh") or ""
        if pod_ssh:
            allow_render = True

    if options.interactive and "tts" in stages:
        reporter.log_waiting_for_input(stage="tts", required="colab_jobs_done")

    run_opts = YoutubeFullAutoOptions(
        site_run_id=options.site_run_id,
        youtube_run_id=youtube_run_id,
        from_site_approved=options.from_site_approved,
        gemini_workers=options.gemini_workers,
        gemini_accounts=options.gemini_accounts,
        stages=",".join(stages),
        limit=0,
        target_yes=0,
        only_story=options.only_story,
        resume=True,
        execute=True,
        dry_run=False,
        frames_runpod_url=frames_url,
        pod_ssh=pod_ssh,
        allow_render=allow_render,
        progress_reporter=reporter,
        interactive=options.interactive,
        live_logs=options.live_logs,
        heartbeat_seconds=options.heartbeat_seconds,
        gemini_start_mode=options.gemini_start_mode,
        gemini_session_mode=options.gemini_session_mode,
        gemini_stories_per_browser=options.gemini_stories_per_browser,
        reuse_stage_results=options.reuse_stage_results,
        ramp_up_stop_on_system_fail=options.ramp_up_stop_on_system_fail,
    )

    reporter._overall_total = max(plan.get("queued", 0), plan.get("eligible_4000_12000", 0))

    try:
        result = run_youtube_full_auto(config=config, options=run_opts)
    finally:
        set_reporter(None)

    store = QueueStore(batch_root, config=config, youtube_run_id=youtube_run_id)
    reporter.set_store(store)
    reporter.log_final_report(store=store, plan=plan)

    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "youtube_run_id": youtube_run_id,
        "batch_root": str(batch_root),
        "preflight": preflight_result,
        "cleanup": cleanup_result,
        "plan": plan,
        "result": result,
        "logs_dir": str(batch_root / "logs"),
        "reports_dir": str(batch_root / "reports"),
    }


def run_youtube_full_auto_watch(
    *,
    config: OrchestratorConfig,
    youtube_run_id: str,
    refresh_seconds: float = 15.0,
    max_iterations: int = 0,
) -> dict[str, Any]:
    from orchestrator.youtube_full_auto.progress_reporter import format_status_dashboard

    batch_root = batch_launch_root(config, youtube_run_id)
    if not batch_root.is_dir():
        return {"ok": False, "message": f"batch not found: {batch_root}"}
    store = QueueStore(batch_root, config=config, youtube_run_id=youtube_run_id)
    iteration = 0
    try:
        while True:
            iteration += 1
            print(format_status_dashboard(store=store, youtube_run_id=youtube_run_id, batch_root=batch_root), flush=True)
            print(f"--- refresh in {refresh_seconds}s (Ctrl+C to stop) ---", flush=True)
            if max_iterations > 0 and iteration >= max_iterations:
                break
            import time

            time.sleep(max(5.0, float(refresh_seconds)))
    except KeyboardInterrupt:
        pass
    return {"ok": True, "youtube_run_id": youtube_run_id, "iterations": iteration}
