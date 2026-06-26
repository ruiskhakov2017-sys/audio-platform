#!/usr/bin/env python3
"""Run the YouTube full-auto pipeline through RunPod package creation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.config import DEFAULT_CONFIG_PATH, load_config
from orchestrator.gemini_execution_policy import DEFAULT_GEMINI_ACCOUNTS, DEFAULT_GEMINI_WORKERS
from orchestrator.youtube_tts_launch_wait_import import LaunchWaitImportOptions, run_launch_wait_import
from orchestrator.youtube_full_auto.layout import batch_launch_root, safe_slug
from orchestrator.youtube_full_auto.oneclick import (
    YoutubeFullAutoOneclickOptions,
    run_youtube_full_auto_oneclick,
)
from orchestrator.youtube_full_auto.orchestrator import YoutubeFullAutoOptions, _init_or_load_queue
from orchestrator.youtube_full_auto.queue_store import QueueStore

PACKAGE_STAGES = "selection,safe,promo,visuals,telegram,tts,frames,package"
PRE_TTS_STAGES = "selection,safe,promo,telegram,tts"
POST_TTS_STAGES = "visuals,frames,package"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run YouTube full-auto stages up to prepared video packages."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--site-run-id", default="auto")
    parser.add_argument("--story", default="")
    parser.add_argument("--frames-runpod-url", default="")
    parser.add_argument("--stages", default=PACKAGE_STAGES)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--target-yes", type=int, default=0)
    parser.add_argument("--min-words", type=int, default=0)
    parser.add_argument("--max-words", type=int, default=0)
    parser.add_argument("--force-selection-yes", action="store_true")
    parser.add_argument("--gemini-workers", type=int, default=0)
    parser.add_argument("--gemini-accounts", default="")
    parser.add_argument("--gemini-start-mode", default="staggered-first-result")
    parser.add_argument("--gemini-session-mode", default="persistent-account")
    parser.add_argument("--gemini-stories-per-browser", type=int, default=10)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("--skip-tts-wait-import", action="store_true")
    parser.add_argument("--tts-workers", type=int, default=5)
    parser.add_argument("--tts-poll-minutes", type=float, default=1.0)
    parser.add_argument("--tts-max-hours", type=float, default=12.0)
    parser.add_argument("--tts-start-cmd", default=".\\START_YOUTUBE_TTS_YANDEX_5TABS_PROFILE_PROXY.bat")
    parser.add_argument("--tts-no-start-browser", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--cleanup-browsers-before-run", action="store_true")
    return parser.parse_args()


def resolved_gemini_workers(value: int) -> int:
    raw = int(value or 0)
    return max(1, raw if raw > 0 else int(DEFAULT_GEMINI_WORKERS))


def resolved_gemini_accounts(value: str) -> str:
    raw = str(value or "").strip()
    return raw or str(DEFAULT_GEMINI_ACCOUNTS)


def run_oneclick(*, config, args: argparse.Namespace, stages: str, all_eligible: bool, limit: int, target_yes: int) -> dict:
    return run_youtube_full_auto_oneclick(
        config=config,
        options=YoutubeFullAutoOneclickOptions(
            site_run_id=str(args.site_run_id).strip() or "auto",
            youtube_run_id=str(args.launch_id).strip(),
            all_eligible=all_eligible,
            min_words=max(0, int(args.min_words or 0)),
            max_words=max(0, int(args.max_words or 0)),
            limit=limit,
            target_yes=target_yes,
            only_story=str(args.story or "").strip(),
            gemini_workers=resolved_gemini_workers(int(args.gemini_workers or 0)),
            gemini_accounts=resolved_gemini_accounts(str(args.gemini_accounts or "")),
            gemini_start_mode=str(args.gemini_start_mode or "staggered-first-result"),
            gemini_session_mode=str(args.gemini_session_mode or "persistent-account"),
            gemini_stories_per_browser=max(1, int(args.gemini_stories_per_browser or 1)),
            stages=stages,
            resume=not bool(args.no_resume),
            execute=True,
            interactive=False,
            allow_render=False,
            frames_runpod_url=str(args.frames_runpod_url or "").strip(),
            pod_ssh="",
            cleanup_browsers_before_run=bool(args.cleanup_browsers_before_run),
            heartbeat_seconds=max(5.0, float(args.heartbeat_seconds or 15.0)),
        ),
    )


def force_selection_yes(*, config, args: argparse.Namespace, all_eligible: bool, limit: int, target_yes: int) -> dict:
    options = YoutubeFullAutoOptions(
        site_run_id=str(args.site_run_id).strip() or "auto",
        youtube_run_id=str(args.launch_id).strip(),
        from_site_approved=True,
        limit=0 if all_eligible else int(limit or 0),
        target_yes=0 if all_eligible else int(target_yes or 0),
        only_story=str(args.story or "").strip(),
        resume=not bool(args.no_resume),
        execute=False,
        dry_run=True,
    )
    if int(args.min_words or 0) > 0:
        options.min_words = int(args.min_words)
    if int(args.max_words or 0) > 0:
        options.max_words = int(args.max_words)

    batch_root, store, meta = _init_or_load_queue(config, options)
    items = store.items()
    if not items:
        print(
            "FORCE_SELECTION_YES_FAILED "
            f"reason=no_queue_items launch_id={args.launch_id} story={args.story or '*'}",
            flush=True,
        )
        return {"ok": False, "status": "no_queue_items", "batch_root": str(batch_root), "meta": meta}

    updated = 0
    for item in items:
        store.record_stage(
            item,
            stage="selection",
            status="selection_yes",
            output_path=item.cleaned_path,
            reason_code="forced_for_full_cycle_test",
            finished=True,
        )
        item.status = "safe_pending"
        item.last_error = ""
        store.upsert(item)
        updated += 1
    store.save()
    store.write_stage_status_snapshot()
    print(
        "FORCE_SELECTION_YES_DONE "
        f"stories={updated} launch_id={args.launch_id} batch_root={batch_root}",
        flush=True,
    )
    return {"ok": True, "status": "forced", "updated": updated, "batch_root": str(batch_root), "meta": meta}


def mark_imported_tts_done(*, config, launch_id: str) -> int:
    store = QueueStore(batch_launch_root(config, launch_id), config=config, youtube_run_id=launch_id)
    updated = 0
    for item in store.items():
        slug = safe_slug(item.canonical_basename)
        audio = batch_launch_root(config, launch_id) / "03_youtube" / slug / "04_audio" / "narration.mp3"
        if not audio.is_file() or audio.stat().st_size <= 1000:
            continue
        store.record_stage(
            item,
            stage="tts",
            status="tts_done",
            output_path=str(audio),
            finished=True,
        )
        updated += 1
    if updated:
        store.save()
        store.write_stage_status_snapshot()
    return updated


def main() -> int:
    args = parse_args()
    frames_url = str(args.frames_runpod_url or "").strip()

    limit = max(0, int(args.limit or 0))
    target_yes = max(0, int(args.target_yes or 0))
    all_eligible = limit <= 0 and target_yes <= 0

    print(
        "FULL_AUTO_PACKAGE_STARTED "
        f"launch_id={args.launch_id} "
        f"site_run_id={args.site_run_id} "
        f"story={args.story or '*'} "
        f"stages={args.stages} "
        f"gemini_workers={resolved_gemini_workers(int(args.gemini_workers or 0))} "
        f"gemini_accounts={resolved_gemini_accounts(str(args.gemini_accounts or ''))} "
        f"limit={limit if not all_eligible else 0}",
        flush=True,
    )

    config = load_config(args.config)
    if args.skip_tts_wait_import:
        if args.force_selection_yes and "selection" in str(args.stages or PACKAGE_STAGES).split(","):
            forced = force_selection_yes(
                config=config,
                args=args,
                all_eligible=all_eligible,
                limit=limit,
                target_yes=target_yes,
            )
            if not forced.get("ok"):
                result = forced
                ok = False
                print(
                    "FULL_AUTO_PACKAGE_DONE "
                    f"ok={str(ok).lower()} "
                    f"status={result.get('status')} "
                    f"launch_id={args.launch_id} "
                    f"batch_root={result.get('batch_root')}",
                    flush=True,
                )
                return 1
            args.stages = ",".join(s for s in str(args.stages or PACKAGE_STAGES).split(",") if s.strip().lower() != "selection")
        if not frames_url:
            print("Enter RunPod/ComfyUI URL for images:", flush=True)
            try:
                frames_url = input().strip()
            except EOFError:
                frames_url = ""
            args.frames_runpod_url = frames_url
        if not frames_url and "frames" in str(args.stages or PACKAGE_STAGES).split(","):
            print("FULL_AUTO_PACKAGE_FAILED reason=missing_frames_runpod_url", flush=True)
            return 2
        result = run_oneclick(
            config=config,
            args=args,
            stages=str(args.stages or PACKAGE_STAGES),
            all_eligible=all_eligible,
            limit=limit,
            target_yes=target_yes,
        )
    else:
        pre_stages = PRE_TTS_STAGES
        if args.force_selection_yes:
            forced = force_selection_yes(
                config=config,
                args=args,
                all_eligible=all_eligible,
                limit=limit,
                target_yes=target_yes,
            )
            if not forced.get("ok"):
                result = forced
                pre_stages = ""
            else:
                pre_stages = "safe,promo,telegram,tts"
        if args.force_selection_yes and not pre_stages:
            pre_result = result
        else:
            pre_result = run_oneclick(
                config=config,
                args=args,
                stages=pre_stages,
                all_eligible=all_eligible,
                limit=limit,
                target_yes=target_yes,
            )
        if not pre_result.get("ok"):
            result = pre_result
        else:
            print("FULL_AUTO_TTS_WAIT_IMPORT_STARTED", flush=True)
            tts_result = run_launch_wait_import(
                config,
                LaunchWaitImportOptions(
                    youtube_run_id=str(args.launch_id).strip(),
                    workers=max(1, int(args.tts_workers or 1)),
                    poll_minutes=max(0.1, float(args.tts_poll_minutes or 1.0)),
                    max_hours=max(0.1, float(args.tts_max_hours or 12.0)),
                    execute=True,
                    start_browser=not bool(args.tts_no_start_browser),
                    start_cmd=str(args.tts_start_cmd or ".\\START_YOUTUBE_TTS_YANDEX_5TABS_PROFILE_PROXY.bat"),
                    continue_next_stage=False,
                ),
            )
            tts_complete = bool((tts_result.get("summary") or {}).get("TTS_STAGE_COMPLETE"))
            print(f"FULL_AUTO_TTS_WAIT_IMPORT_DONE ok={str(tts_complete).lower()}", flush=True)
            if not tts_complete:
                result = {
                    "ok": False,
                    "status": "tts_wait_import_failed",
                    "youtube_run_id": str(args.launch_id).strip(),
                    "batch_root": pre_result.get("batch_root"),
                    "tts_result": tts_result,
                }
            else:
                marked = mark_imported_tts_done(config=config, launch_id=str(args.launch_id).strip())
                print(f"FULL_AUTO_TTS_QUEUE_MARKED_DONE stories={marked}", flush=True)
                if not frames_url:
                    print("Enter RunPod/ComfyUI URL for images:", flush=True)
                    try:
                        frames_url = input().strip()
                    except EOFError:
                        frames_url = ""
                    args.frames_runpod_url = frames_url
                if not frames_url:
                    result = {
                        "ok": False,
                        "status": "missing_frames_runpod_url",
                        "youtube_run_id": str(args.launch_id).strip(),
                        "batch_root": pre_result.get("batch_root"),
                    }
                else:
                    result = run_oneclick(
                        config=config,
                        args=args,
                        stages=POST_TTS_STAGES,
                        all_eligible=True,
                        limit=0,
                        target_yes=0,
                    )
    ok = bool(result.get("ok"))
    print(
        "FULL_AUTO_PACKAGE_DONE "
        f"ok={str(ok).lower()} "
        f"status={result.get('status')} "
        f"launch_id={result.get('youtube_run_id')} "
        f"batch_root={result.get('batch_root')}",
        flush=True,
    )
    if not ok:
        print(f"FULL_AUTO_PACKAGE_ERROR message={result.get('message') or result}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
