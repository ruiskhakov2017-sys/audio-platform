from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.phase_a_gemini_supervisor import count_pending_gemini_folders, merge_worker_telemetry_from_logs


LOCK_NAME = ".cf_worker.lock"
ACTIVE_LOCK_MAX_AGE_S = 600.0


def _length_filter_manifest_path(run_root: Path) -> Path | None:
    """Манифест пишется в корень run; старые прогоны могли класть под _phase_a."""
    for p in (
        run_root / "length_filter_manifest.json",
        run_root / "_phase_a" / "length_filter_manifest.json",
    ):
        if p.is_file():
            return p
    return None


def _gemini_selection_stories_dir(run_root: Path) -> Path:
    """Актуальный путь: run/gemini_input/stories; fallback legacy run/_phase_a/gemini_input/stories."""
    primary = run_root / "gemini_input" / "stories"
    if primary.is_dir():
        return primary
    legacy = run_root / "_phase_a" / "gemini_input" / "stories"
    if legacy.is_dir():
        return legacy
    return primary


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_nonempty_info_txt(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def print_phase_a_selection_resume_preview(
    *,
    run_id: str,
    branch: str,
    run_root: Path,
    target_active_workers: int,
    stories_dir: Path | None = None,
    extensions: list[str] | None = None,
    logs_dir: Path | None = None,
) -> None:
    """Перед resume: сводка length filter / info.txt / locks (без запуска Gemini)."""
    phase_a_run_root = run_root.resolve()
    gemini_sel = _gemini_selection_stories_dir(phase_a_run_root)
    logs_dir_run = phase_a_run_root / "logs"
    lf_path = _length_filter_manifest_path(phase_a_run_root)
    lf = _read_json(lf_path) if lf_path is not None else None
    total_after = int(lf.get("kept_count", 0) or 0) if isinstance(lf, dict) else 0
    done_by_info = 0
    if gemini_sel.is_dir():
        for info in gemini_sel.rglob("info.txt"):
            if _is_nonempty_info_txt(info):
                done_by_info += 1
    pending_folders = count_pending_gemini_folders(gemini_sel) if gemini_sel.is_dir() else 0
    remaining = max(0, total_after - done_by_info)
    now = time.time()
    active_locks = 0
    all_locks = 0
    if gemini_sel.is_dir():
        for lk in gemini_sel.rglob(LOCK_NAME):
            try:
                age = now - lk.stat().st_mtime
            except OSError:
                continue
            all_locks += 1
            if age < ACTIVE_LOCK_MAX_AGE_S:
                active_locks += 1
    state_path = logs_dir_run / "gemini_supervisor_state.json"
    sup_target = target_active_workers
    if state_path.is_file():
        try:
            sup = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(sup, dict) and sup.get("target_active_workers") is not None:
                sup_target = int(sup["target_active_workers"])
        except Exception:
            pass
    print("--- Phase A / Gemini selection — resume preview ---", flush=True)
    print(f"run_id={run_id} branch={branch}", flush=True)
    print(f"total_after_length_filter={total_after}", flush=True)
    print(f"done_by_nonempty_info_txt={done_by_info}", flush=True)
    print(f"skipped_as_done_by_info={done_by_info}", flush=True)
    print(
        "remaining_to_process_estimate_legacy=intake_minus_nonempty_info_txt "
        f"(может расходиться с папками при дублях)={remaining}",
        flush=True,
    )
    print(f"pending_gemini_folders_no_nonempty_info={pending_folders}", flush=True)
    if stories_dir is not None and extensions is not None and stories_dir.is_dir():
        try:
            from orchestrator.gemini_resume_audit import (
                build_gemini_resume_audit,
                render_gemini_resume_audit_md,
                selection_progress_snapshot,
                write_gemini_resume_audit_reports,
            )

            snap = selection_progress_snapshot(
                stories_dir=stories_dir,
                phase_a_root=phase_a_run_root,
                extensions=extensions,
            )
            print("progress_scope=intake_unique", flush=True)
            print(f"intake_unique_total={snap['intake_unique_total']}", flush=True)
            print(f"intake_unique_done={snap['intake_unique_done']}", flush=True)
            print(f"intake_unique_remaining={snap['intake_unique_remaining']}", flush=True)
            print("progress_scope=gemini_folders", flush=True)
            print(f"gemini_folder_total={snap['gemini_folder_total']}", flush=True)
            print(f"gemini_folder_done={snap['gemini_folder_done']}", flush=True)
            print(f"gemini_folder_remaining={snap['gemini_folder_remaining']}", flush=True)
            print("duplicates", flush=True)
            print(f"duplicate_source_keys={snap['duplicate_source_keys']}", flush=True)
            print(f"duplicate_extra_folders={snap['duplicate_extra_folders']}", flush=True)
            if logs_dir is not None:
                audit = build_gemini_resume_audit(
                    stories_dir=stories_dir,
                    phase_a_root=phase_a_run_root,
                    extensions=extensions,
                )
                jp, mp = write_gemini_resume_audit_reports(logs_dir=logs_dir, payload=audit)
                print(f"gemini_resume_audit_json={jp}", flush=True)
                print(f"gemini_resume_audit_md={mp}", flush=True)
                # дублируем md в phase_a/logs для удобства рядом с run
                try:
                    side = logs_dir_run / "gemini_resume_audit.md"
                    side.write_text(render_gemini_resume_audit_md(audit), encoding="utf-8")
                    print(f"gemini_resume_audit_md_copy={side}", flush=True)
                except OSError:
                    pass
        except Exception as ex:
            print(f"split_progress_audit_failed={ex!r}", flush=True)
    print(f"active_lock_files_lt_{int(ACTIVE_LOCK_MAX_AGE_S)}s={active_locks}", flush=True)
    print(f"all_lock_files={all_locks}", flush=True)
    print(f"target_active_workers={sup_target}", flush=True)
    print("--- end preview ---", flush=True)


def _runs_roots(config: OrchestratorConfig, run_id: str, branch: str) -> tuple[Path, Path, Path, Path]:
    br = "youtube" if branch.strip().lower() == "youtube" else "site"
    runs_root = (config.root_dir / "runs" / br / run_id).resolve()
    gemini_sel = _gemini_selection_stories_dir(runs_root)
    logs_dir = runs_root / "logs"
    stories_run = runs_root / "stories"
    return runs_root, gemini_sel, logs_dir, stories_run


def print_phase_a_gemini_progress(config: OrchestratorConfig, *, run_id: str, branch: str = "site") -> None:
    runs_root, gemini_sel, logs_dir, _stories_run = _runs_roots(config, run_id, branch)
    phase_a_root = runs_root / "_phase_a" if (runs_root / "_phase_a").is_dir() else runs_root
    state_path = logs_dir / "gemini_supervisor_state.json"
    lf_path = _length_filter_manifest_path(runs_root)
    lf = _read_json(lf_path) if lf_path is not None else None
    total_after_filter = int(lf.get("kept_count", 0) or 0) if isinstance(lf, dict) else 0
    done_info = 0
    pending_sel = count_pending_gemini_folders(gemini_sel) if gemini_sel.is_dir() else 0
    if gemini_sel.is_dir():
        for info in gemini_sel.rglob("info.txt"):
            if _is_nonempty_info_txt(info):
                done_info += 1
    skipped_done = done_info
    remaining_est = max(0, total_after_filter - done_info)

    locks: list[tuple[str, float]] = []
    stale_locks = 0
    active_locks = 0
    now = time.time()
    if gemini_sel.is_dir():
        for lk in gemini_sel.rglob(LOCK_NAME):
            try:
                age = now - lk.stat().st_mtime
            except OSError:
                continue
            locks.append((str(lk.relative_to(gemini_sel)), age))
            if age < ACTIVE_LOCK_MAX_AGE_S:
                active_locks += 1
    for _, age in locks:
        if age > ACTIVE_LOCK_MAX_AGE_S:
            stale_locks += 1

    sup: dict[str, Any] = {}
    if state_path.is_file():
        try:
            sup = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            sup = {}
    merge_worker_telemetry_from_logs(logs_dir, 5, "general_selection")
    merge_worker_telemetry_from_logs(logs_dir, 5, "site_info_builder")

    print(f"run_id={run_id} branch={branch}")
    print(f"runs_root={runs_root}")
    print(f"gemini_selection_input={gemini_sel}")
    print(f"total_after_length_filter={total_after_filter}")
    print(f"done_by_nonempty_info_txt={done_info}")
    print(f"skipped_as_done_by_info={skipped_done}")
    print(
        "remaining_to_process_estimate_legacy=intake_minus_nonempty_info_txt "
        f"(может расходиться с папками при дублях)={remaining_est}",
        flush=True,
    )
    print(f"pending_gemini_folders_no_nonempty_info={pending_sel}")
    im = phase_a_root / "intake_manifest.json"
    if im.is_file():
        try:
            raw = json.loads(im.read_text(encoding="utf-8"))
            sd = raw.get("extensions")
            ext_list = sd if isinstance(sd, list) else None
            if ext_list is None and isinstance(raw.get("extensions"), str):
                ext_list = [x.strip() for x in str(raw.get("extensions")).split(",") if x.strip()]
            if ext_list is None:
                ext_list = [".txt"]
            stories_dir_s = raw.get("stories_dir")
            if isinstance(stories_dir_s, str) and stories_dir_s.strip():
                from orchestrator.gemini_resume_audit import selection_progress_snapshot

                snap = selection_progress_snapshot(
                    stories_dir=Path(stories_dir_s),
                    phase_a_root=phase_a_root,
                    extensions=ext_list,
                )
                print("progress_scope=intake_unique", flush=True)
                print(f"intake_unique_total={snap['intake_unique_total']}", flush=True)
                print(f"intake_unique_done={snap['intake_unique_done']}", flush=True)
                print(f"intake_unique_remaining={snap['intake_unique_remaining']}", flush=True)
                print("progress_scope=gemini_folders", flush=True)
                print(f"gemini_folder_total={snap['gemini_folder_total']}", flush=True)
                print(f"gemini_folder_done={snap['gemini_folder_done']}", flush=True)
                print(f"gemini_folder_remaining={snap['gemini_folder_remaining']}", flush=True)
                print("duplicates", flush=True)
                print(f"duplicate_source_keys={snap['duplicate_source_keys']}", flush=True)
                print(f"duplicate_extra_folders={snap['duplicate_extra_folders']}", flush=True)
        except Exception as ex:
            print(f"split_progress_audit_failed={ex!r}", flush=True)
    print(f"active_lock_files_lt_{int(ACTIVE_LOCK_MAX_AGE_S)}s={active_locks}")
    print(f"all_lock_files={len(locks)} stale_locks_gt_{int(ACTIVE_LOCK_MAX_AGE_S)}s={stale_locks}")
    if sup:
        print("supervisor_state:", json.dumps(sup, ensure_ascii=False, indent=2))
    else:
        print("supervisor_state: (no gemini_supervisor_state.json yet)")


def repair_gemini_stale_locks(
    config: OrchestratorConfig,
    *,
    run_id: str,
    branch: str = "site",
    older_than_minutes: int = 60,
    execute: bool = False,
) -> tuple[int, list[str]]:
    _, gemini_sel, _, _ = _runs_roots(config, run_id, branch)
    if not gemini_sel.is_dir():
        return 0, [f"not a directory: {gemini_sel}"]
    threshold = time.time() - max(1, int(older_than_minutes)) * 60
    actions: list[str] = []
    touched = 0
    for lk in gemini_sel.rglob(LOCK_NAME):
        try:
            mtime = lk.stat().st_mtime
        except OSError:
            continue
        if mtime >= threshold:
            continue
        rel = str(lk.relative_to(gemini_sel))
        if execute:
            try:
                lk.unlink()
                touched += 1
                actions.append(f"removed {rel}")
            except OSError as ex:
                actions.append(f"failed {rel}: {ex}")
        else:
            actions.append(f"would_remove {rel} age_s={int(time.time() - mtime)}")
    return touched, actions
