"""
Обёртка вокруг site-пайплайна из Content-Factory-Запуск.bat (:RUN_SITE_PIPELINE / :RUN_SITE_PIPELINE_KOKORO_DRIVE).

Не дублирует бизнес-логику phase_a / phase_b / run site — только subprocess + sync в Запуски/<имя>/.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import (
    D10_LEGACY,
    D10_TEMP,
    F_MANIFEST,
    launch_legacy_runs_root,
    read_json,
    sanitize_launch_folder_name,
    write_json,
)
from orchestrator.human_launch_legacy_sync import (
    mirror_legacy_pipeline_to_human,
    mirror_phase_a_progress_to_human,
    write_launch_legacy_binding,
)
from orchestrator.human_launch_lifecycle import (
    evaluate_recovery_site_pipeline_gate,
    launch_story_scope_bundle,
    merge_orchestrator_launch_trace,
    recovery_queue_resume_counters,
    refresh_launch_status_file,
)
from orchestrator.human_launch_phase_a_subprocess import run_orchestrator_phase_a_subprocess
from orchestrator.runtime_modes import load_runtime_modes

F_SITE_FLOW_STATE = "site_flow_bat_state.json"


def _recovery_items_need_gemini_selection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Строки recovery_queue_map, для которых ещё нужен этап отбора (selection) в phase-a."""
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        m = it.get("markers") if isinstance(it.get("markers"), dict) else {}
        if not bool(m.get("selection_done")):
            out.append(it)
    return out


def _resolve_recovery_source_txt(
    item: dict[str, Any],
    *,
    stories_dir: Path,
    launch: Path,
    config_root: Path,
) -> Path | None:
    fn = str(item.get("source_filename", "")).strip()
    if not fn:
        return None
    raw = Path(fn)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append((config_root / raw).resolve())
    candidates.append((stories_dir / Path(fn).name).resolve())
    candidates.append((stories_dir / fn).resolve())
    seen: set[Path] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _materialize_recovery_selection_staging(
    staging_dir: Path,
    items: list[dict[str, Any]],
    *,
    stories_dir: Path,
    launch: Path,
    config_root: Path,
) -> int:
    """Копирует .txt для intake phase-a (только recovery, без глобального runs/site)."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    used_stems: set[str] = set()
    n = 0
    for it in items:
        src = _resolve_recovery_source_txt(it, stories_dir=stories_dir, launch=launch, config_root=config_root)
        if src is None or not src.is_file():
            continue
        stem = sanitize_launch_folder_name(src.stem) or "story"
        sk = str(it.get("stable_story_key", "")).strip()[:12]
        base = stem
        if base in used_stems and sk:
            base = f"{stem}__{sk}"
        suf = 0
        cand = base
        while cand in used_stems:
            suf += 1
            cand = f"{base}_{suf}"
        used_stems.add(cand)
        shutil.copy2(src, staging_dir / f"{cand}.txt")
        n += 1
    return n


def run_site_flow_gemini_caps(
    *,
    limit: int,
    requested_gemini_workers: int,
) -> tuple[int, int, str]:
    """
    Ограничение параллельных Gemini-воркеров для ограниченного run-site-flow (как smoke-site-cycle).

    - limit <= 0: production-режим по числу воркеров — target_active=min(3, requested), pool=requested (cap 5).
    - limit <= 2: target_active=1, pool=max(1, min(5, requested)).
    - limit <= 10: target_active=min(2, min(3, requested)), pool=max(2, min(5, requested)).
    - иначе: target_active=min(3, requested), pool=max(1, min(5, requested)).
    """
    req = max(1, min(5, int(requested_gemini_workers)))
    lim = int(limit or 0)
    if lim <= 0:
        ta = min(3, req)
        reason = "limit=0 (full intake / production-style): use configured worker cap"
        return req, ta, reason
    if lim <= 2:
        return req, 1, "limited test run (limit<=2): single active Gemini worker"
    if lim <= 10:
        ta = min(2, min(3, req))
        return max(2, req), ta, "limited test run (limit<=10): at most 2 active Gemini workers"
    ta = min(3, req)
    return req, ta, f"limit={lim}: default supervised pool (target_active={ta})"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_modes_path(config: OrchestratorConfig) -> Path:
    return (config.root_dir / "configs" / "runtime_modes.yaml").resolve()


def _resolve_site_visual_for_phase_a(config: OrchestratorConfig) -> tuple[str, str]:
    """
    Как :RESOLVE_SITE_VISUAL_MODE в Content-Factory-Запуск.bat: читаем site_visual из runtime_modes.yaml.
    auto без URL -> manual (неинтерактивно).
    """
    modes = load_runtime_modes(_runtime_modes_path(config))
    mode = str(modes.get("site_visual", "manual")).strip().lower() or "manual"
    if mode not in {"auto", "manual"}:
        mode = "manual"
    pod = (os.environ.get("SITE_VISUAL_POD_URL") or "").strip()
    if mode == "auto" and not pod:
        print("[run-site-flow] site_visual=auto но нет SITE_VISUAL_POD_URL -> visual-mode manual", flush=True)
        mode = "manual"
    return mode, pod


def _state_path(launch: Path) -> Path:
    return launch / D10_TEMP / F_SITE_FLOW_STATE


def _load_flow_state(launch: Path) -> dict[str, Any]:
    p = _state_path(launch)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_flow_state(launch: Path, state: dict[str, Any]) -> None:
    (launch / D10_TEMP).mkdir(parents=True, exist_ok=True)
    write_json(_state_path(launch), state)


def _flow_log_dir(config: OrchestratorConfig) -> Path:
    d = (config.root_dir / ".orchestrator" / "logs").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append_site_flow_log(config: OrchestratorConfig, launch_name: str, row: dict[str, Any]) -> None:
    p = _flow_log_dir(config) / "site_flow_bat.jsonl"
    payload = {"ts": _now_iso(), "launch": launch_name, **row}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _phase_a_complete(runs_phase_a_root: Path) -> bool:
    summary = runs_phase_a_root / "_phase_a" / "phase_a_summary.json"
    deferred = runs_phase_a_root / "_phase_a" / "ready_queues" / "deferred.json"
    return summary.is_file() and deferred.is_file()


def _human_all_stories_published(launch: Path) -> bool:
    root = launch / "05_Рассказы"
    if not root.is_dir():
        return False
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return False
    for d in dirs:
        ok = d / "03_Сайт" / "05_Публикация" / ".published_ok"
        if not ok.is_file():
            return False
    return True


def _sync_after_step(config: OrchestratorConfig, launch: Path, launch_name: str, step: str) -> dict[str, Any]:
    sync = mirror_phase_a_progress_to_human(config, launch, execute=True)
    write_json(launch / D10_TEMP / "last_sync_report.json", sync)
    refresh_launch_status_file(launch)
    _append_site_flow_log(
        config,
        launch_name,
        {
            "action": "sync",
            "step": step,
            "copied": sync.get("copied"),
            "tts_synced": sync.get("tts_synced"),
            "publish_synced": sync.get("publish_synced"),
        },
    )
    return sync


def _sync_is_ok(sync: dict[str, Any]) -> bool:
    if not sync:
        return False
    if not bool(sync.get("ok", True)):
        return False
    errs = sync.get("sync_errors")
    if isinstance(errs, list) and errs:
        # Ошибки записи Отчёт_этапа.json не блокируют пайплайн (копирование артефактов уже прошло).
        critical = [e for e in errs if not str(e).startswith("stage_report:")]
        if critical:
            return False
    return True


def _phase_a_live_sync_interval_seconds() -> float:
    raw = str(os.environ.get("CONTENT_FACTORY_PHASE_A_SYNC_INTERVAL_SEC", "") or "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return max(30.0, val)
        except ValueError:
            pass
    return 120.0


def _extract_phase_a_progress(sync: dict[str, Any]) -> dict[str, int | float]:
    """
    Нормализованный прогресс selection-этапа для печати в терминал.
    total: сколько историй прошло в очередь Gemini (после length filter),
    processed: сколько уже имеют selection info/result,
    remaining: сколько ещё ждёт.
    """
    stage_reports = sync.get("stage_reports") if isinstance(sync.get("stage_reports"), dict) else {}
    intake = stage_reports.get("intake") if isinstance(stage_reports.get("intake"), dict) else {}
    length = stage_reports.get("length_filter") if isinstance(stage_reports.get("length_filter"), dict) else {}

    intake_counts = intake.get("counts") if isinstance(intake.get("counts"), dict) else {}
    length_counts = length.get("counts") if isinstance(length.get("counts"), dict) else {}

    intake_total = int(intake_counts.get("input", 0) or 0)
    selected_pending = int(length_counts.get("passed", 0) or 0)
    total = selected_pending if selected_pending > 0 else intake_total

    processed_candidates = [
        int(sync.get("synced_selection_result", 0) or 0),
        int(sync.get("legacy_selection_info_found", 0) or 0),
    ]
    processed = max(processed_candidates) if processed_candidates else 0
    if total > 0:
        processed = min(processed, total)
    remaining = max(0, total - processed)
    percent = round((processed * 100.0 / total), 2) if total > 0 else 0.0
    return {
        "intake_total": intake_total,
        "total": total,
        "processed": processed,
        "remaining": remaining,
        "percent": percent,
    }


def _build_kokoro_set_mode_cmd(config: OrchestratorConfig) -> list[str]:
    return [
        sys.executable,
        "-m",
        "orchestrator",
        "set-mode",
        "--key",
        "site_tts_engine",
        "--value",
        "kokoro_colab_drive",
    ]


def _build_phase_a_cmd(
    config: OrchestratorConfig,
    stories_dir: Path,
    phase_a_id: str,
    limit: int,
    gemini_workers: int,
    registry: Path,
    launch_dir: Path | None = None,
) -> list[str]:
    visual_mode, pod = _resolve_site_visual_for_phase_a(config)
    cmd: list[str] = [
        sys.executable,
        "-m",
        "orchestrator",
        "phase-a",
        "--stories-dir",
        str(stories_dir.resolve()),
        "--story-id",
        phase_a_id,
        "--run-branch",
        "site",
        "--gemini-workers",
        str(max(1, min(5, int(gemini_workers)))),
        "--gemini-registry",
        str(registry.resolve()),
        "--resume",
        "--execute",
    ]
    if limit > 0:
        cmd.extend(["--max-stories", str(int(limit))])
    if visual_mode == "auto" and pod:
        cmd.extend(["--visual-mode", "auto", "--visual-pod-url", pod])
    else:
        cmd.extend(["--visual-mode", "manual"])
    if launch_dir is not None:
        cmd.extend(["--launch-dir", str(Path(launch_dir).resolve())])
    return cmd


def _build_phase_b_cmd(
    config: OrchestratorConfig,
    phase_b_id: str,
    deferred_manifest: Path,
    registry: Path,
    branch: str,
    allow_scaffold: bool,
    launch_dir: Path | None = None,
) -> list[str]:
    _ = config
    cmd = [
        sys.executable,
        "-m",
        "orchestrator",
        "phase-b",
        "--story-id",
        phase_b_id,
        "--deferred-manifest",
        str(deferred_manifest.resolve()),
        "--gemini-registry",
        str(registry.resolve()),
        "--branch",
        str(branch or "all").strip().lower() or "all",
    ]
    if allow_scaffold:
        cmd.append("--allow-scaffold")
    if launch_dir is not None:
        cmd.extend(["--launch-dir", str(Path(launch_dir).resolve())])
    return cmd


def _build_site_run_cmd(
    config: OrchestratorConfig,
    site_run_id: str,
    stories_dir: Path,
    *,
    launch_dir: Path | None = None,
) -> list[str]:
    _ = config
    cmd: list[str] = [
        sys.executable,
        "-m",
        "orchestrator",
        "run",
        "--pipeline",
        "site",
        "--story-id",
        site_run_id,
        "--stories-dir",
        str(stories_dir.resolve()),
    ]
    if launch_dir is not None:
        cmd.extend(["--launch-dir", str(Path(launch_dir).resolve())])
    cmd.append("--execute")
    return cmd


def run_site_flow_plan(
    config: OrchestratorConfig,
    *,
    launch: Path,
    stories_dir: Path,
    limit: int,
    site_run_base: str,
    gemini_workers: int,
    gemini_registry: Path,
    kokoro_drive_profile: bool,
    phase_b_allow_scaffold: bool,
    phase_b_branch: str,
    max_runtime_minutes: float = 0.0,
) -> dict[str, Any]:
    phase_a_id = f"{site_run_base}-a"
    phase_b_id = f"{site_run_base}-b"
    site_run_id = f"{site_run_base}-site"
    runs_local = launch_legacy_runs_root(launch, "site", phase_a_id)
    deferred_local = runs_local / "_phase_a" / "ready_queues" / "deferred.json"
    reg = gemini_registry if gemini_registry.is_absolute() else (config.root_dir / gemini_registry).resolve()
    legacy_root = (launch / D10_TEMP / D10_LEGACY).resolve()
    staging_dir = (legacy_root / "test_input_recovery").resolve()

    out: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "site_run_base": site_run_base,
        "phase_a_story_id": phase_a_id,
        "phase_b_story_id": phase_b_id,
        "site_run_story_id": site_run_id,
        "deferred_manifest": str(deferred_local.resolve()),
        "kokoro_drive_profile": bool(kokoro_drive_profile),
        "phase_b_allow_scaffold": bool(phase_b_allow_scaffold),
        "phase_b_branch": str(phase_b_branch or "all").strip().lower() or "all",
        "global_runs_site_used": False,
        "actual_phase_a_artifacts_dir": str((runs_local / "_phase_a").resolve()),
    }
    manifest = read_json(launch / F_MANIFEST) or {}
    _story_ids, story_scope, recovery_items = launch_story_scope_bundle(launch, manifest)
    if recovery_items is None:
        qm_fallback = (launch / D10_TEMP / "recovery_queue_map.json").resolve()
        if qm_fallback.is_file():
            data = read_json(qm_fallback) or {}
            items_raw = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items_raw, list):
                items_raw = []
            recovery_items = [x for x in items_raw if isinstance(x, dict)]
            story_scope = "recovery_queue_map"

    stories_effective = stories_dir.resolve()
    if bool(manifest.get("use_input_snapshot")):
        sp = str(manifest.get("input_snapshot_dir") or "").strip()
        if sp and Path(sp).is_dir():
            stories_effective = Path(sp).resolve()

    stories_pa = stories_effective
    if recovery_items is not None:
        rc = recovery_queue_resume_counters(recovery_items)
        pending_sel = _recovery_items_need_gemini_selection(recovery_items)
        stories_pa = staging_dir if pending_sel else stories_effective
        out.update(
            {
                "source_of_truth": "recovery_queue_map",
                "recovery_queue_counters": rc,
                "recovery_wide_gate": evaluate_recovery_site_pipeline_gate(recovery_items),
                "recovery_staging_dir": str(staging_dir),
                "notes": [
                    "Артефакты phase-a/b и output/site — только под Запуски/<name>/10_Временные_файлы/legacy/.",
                    "Recovery: при pending selection intake из staging; иначе intake из --stories-dir (без полного stories/input).",
                ],
            }
        )
    else:
        out["notes"] = [
            "Артефакты phase-a/b и output/site — только под Запуски/<name>/10_Временные_файлы/legacy/.",
        ]

    if kokoro_drive_profile:
        out["kokoro_set_mode_cmd"] = _build_kokoro_set_mode_cmd(config)
    out["phase_a_cmd"] = _build_phase_a_cmd(
        config,
        stories_pa,
        phase_a_id,
        limit,
        gemini_workers,
        reg,
        launch_dir=launch,
    )
    out["phase_a_cwd"] = str(config.root_dir.resolve())
    out["phase_b_cmd"] = _build_phase_b_cmd(
        config, phase_b_id, deferred_local, reg, phase_b_branch, phase_b_allow_scaffold, launch_dir=launch
    )
    out["site_run_cmd"] = _build_site_run_cmd(config, site_run_id, stories_effective, launch_dir=launch)
    out["max_runtime_minutes_phase_a"] = float(max_runtime_minutes or 0.0)
    return out


def run_site_flow_bat_execute(
    config: OrchestratorConfig,
    *,
    launch: Path,
    stories_dir: Path,
    limit: int,
    execute: bool,
    site_run_base: str,
    gemini_workers: int,
    gemini_registry: Path,
    kokoro_drive_profile: bool,
    phase_b_allow_scaffold: bool,
    phase_b_branch: str,
    max_runtime_minutes: float = 0.0,
) -> dict[str, Any]:
    if not execute:
        return run_site_flow_plan(
            config,
            launch=launch,
            stories_dir=stories_dir,
            limit=limit,
            site_run_base=site_run_base,
            gemini_workers=gemini_workers,
            gemini_registry=gemini_registry,
            kokoro_drive_profile=kokoro_drive_profile,
            phase_b_allow_scaffold=phase_b_allow_scaffold,
            phase_b_branch=phase_b_branch,
            max_runtime_minutes=max_runtime_minutes,
        )

    launch_name = launch.name
    manifest = read_json(launch / F_MANIFEST) or {}
    _story_ids, _story_scope, recovery_items = launch_story_scope_bundle(launch, manifest)
    if recovery_items is None:
        qm_fallback = (launch / D10_TEMP / "recovery_queue_map.json").resolve()
        if qm_fallback.is_file():
            data = read_json(qm_fallback) or {}
            items_raw = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items_raw, list):
                items_raw = []
            recovery_items = [x for x in items_raw if isinstance(x, dict)]

    orig_sd = stories_dir.resolve()
    pipeline_sd = orig_sd
    if bool(manifest.get("use_input_snapshot")):
        sp = str(manifest.get("input_snapshot_dir") or "").strip()
        if sp and Path(sp).is_dir():
            pipeline_sd = Path(sp).resolve()

    phase_a_id = f"{site_run_base}-a"
    phase_b_id = f"{site_run_base}-b"
    site_run_id = f"{site_run_base}-site"
    reg = gemini_registry if gemini_registry.is_absolute() else (config.root_dir / gemini_registry).resolve()

    recovery_mode = recovery_items is not None
    legacy_root = (launch / D10_TEMP / D10_LEGACY).resolve()
    legacy_root.mkdir(parents=True, exist_ok=True)
    runs_phase_a_root = launch_legacy_runs_root(launch, "site", phase_a_id)
    deferred_manifest = runs_phase_a_root / "_phase_a" / "ready_queues" / "deferred.json"
    launch_dir_arg = launch.resolve()
    stories_for_phase_a = pipeline_sd
    if recovery_mode:
        staging_dir = (legacy_root / "test_input_recovery").resolve()
        pending_sel = _recovery_items_need_gemini_selection(recovery_items)
        if pending_sel:
            n_copy = _materialize_recovery_selection_staging(
                staging_dir,
                pending_sel,
                stories_dir=orig_sd,
                launch=launch,
                config_root=config.root_dir.resolve(),
            )
            if n_copy <= 0:
                return {
                    "ok": False,
                    "failed_step": "recovery_staging_empty",
                    "message": (
                        "recovery_queue_map: есть элементы без selection_done, но ни один source_filename "
                        "не разрешился в существующий .txt (проверьте пути и --stories-dir)."
                    ),
                    "source_of_truth": "recovery_queue_map",
                }
            stories_for_phase_a = staging_dir
        else:
            if not (_phase_a_complete(runs_phase_a_root) or deferred_manifest.is_file()):
                return {
                    "ok": False,
                    "failed_step": "recovery_phase_a_state",
                    "message": (
                        "recovery: нет строк без selection_done, но в launch legacy нет завершённого phase_a "
                        "(summary+deferred) — не подставляем весь stories/input."
                    ),
                    "source_of_truth": "recovery_queue_map",
                }

    write_launch_legacy_binding(config, launch, run_id=phase_a_id, branch="site")
    manifest = read_json(launch / F_MANIFEST) or {}
    manifest["site_flow_bat"] = {
        "site_run_base": site_run_base,
        "phase_a_story_id": phase_a_id,
        "phase_b_story_id": phase_b_id,
        "site_run_story_id": site_run_id,
        "kokoro_drive_profile": bool(kokoro_drive_profile),
        "phase_b_allow_scaffold": bool(phase_b_allow_scaffold),
        "phase_b_branch": str(phase_b_branch or "all").strip().lower() or "all",
    }
    write_json(launch / F_MANIFEST, manifest)

    state = _load_flow_state(launch)
    state.setdefault("steps", {})
    state["site_run_base"] = site_run_base
    state["updated_at"] = _now_iso()

    merge_orchestrator_launch_trace(
        launch,
        {
            "flow": "run-site-flow",
            "site_flow_bat_started": True,
            "site_run_base": site_run_base,
        },
    )

    if kokoro_drive_profile:
        sm = _build_kokoro_set_mode_cmd(config)
        r0 = subprocess.run(sm, cwd=str(config.root_dir), text=True, encoding="utf-8", errors="replace")
        state["steps"]["set_mode_kokoro_drive"] = {"cmd": sm, "returncode": r0.returncode, "at": _now_iso()}
        _save_flow_state(launch, state)
        _append_site_flow_log(config, launch_name, {"action": "set_mode", "cmd": sm, "returncode": r0.returncode})
        if r0.returncode != 0:
            _sync_after_step(config, launch, launch_name, "after_set_mode_fail")
            return {"ok": False, "failed_step": "set_mode_kokoro_drive", "returncode": r0.returncode, "state": state}

    a_ok = state.get("steps", {}).get("phase_a", {}).get("returncode") == 0
    run_phase_a = not (a_ok and _phase_a_complete(runs_phase_a_root))
    if run_phase_a:
        state["steps"].pop("phase_b", None)
        state["steps"].pop("site_run", None)

    if run_phase_a:
        cmd_a = _build_phase_a_cmd(
            config,
            stories_for_phase_a,
            phase_a_id,
            limit,
            gemini_workers,
            reg,
            launch_dir=launch_dir_arg,
        )
        timeout_sec = float(max_runtime_minutes) * 60.0 if float(max_runtime_minutes or 0) > 0 else None
        phase_a_sync_interval_sec = _phase_a_live_sync_interval_seconds()
        print(
            f"[run-site-flow] phase-a live human sync every {int(phase_a_sync_interval_sec)}s "
            f"(override: CONTENT_FACTORY_PHASE_A_SYNC_INTERVAL_SEC)",
            flush=True,
        )
        last_live_sync_at = 0.0

        def _phase_a_on_poll(_pid: int, _elapsed: float) -> None:
            nonlocal last_live_sync_at
            now_ts = time.time()
            if now_ts - last_live_sync_at < phase_a_sync_interval_sec:
                return
            live_sync = _sync_after_step(config, launch, launch_name, "during_phase_a")
            if _sync_is_ok(live_sync):
                copied = int(live_sync.get("copied", 0) or 0)
                pg = _extract_phase_a_progress(live_sync)
                print(
                    "[run-site-flow] phase-a progress: "
                    f"intake_total={pg['intake_total']} "
                    f"queue_total={pg['total']} processed={pg['processed']} "
                    f"remaining={pg['remaining']} done={pg['percent']}% "
                    f"copied={copied}",
                    flush=True,
                )
            else:
                print(
                    f"[WARN] live sync during phase-a failed: {live_sync.get('sync_errors', [])}",
                    flush=True,
                )
            last_live_sync_at = now_ts

        run_out = run_orchestrator_phase_a_subprocess(
            config,
            cmd_a,
            timeout_seconds=timeout_sec,
            poll_interval_seconds=min(5.0, phase_a_sync_interval_sec),
            on_poll=_phase_a_on_poll,
        )
        rc = int(run_out.get("returncode", 1))
        outcome = str(run_out.get("outcome", "completed"))
        state["steps"]["phase_a"] = {
            "cmd": cmd_a,
            "returncode": rc,
            "outcome": outcome,
            "at": _now_iso(),
            **({"kill_detail": run_out.get("kill_detail")} if outcome == "timeout" else {}),
        }
        if outcome == "timeout":
            merge_orchestrator_launch_trace(
                launch,
                {
                    "terminal_status": "phase_a_timeout",
                    "terminal_detail": str(run_out.get("kill_detail", "")),
                    "phase_a_exit": rc,
                    "flow": "run-site-flow",
                },
            )
        _save_flow_state(launch, state)
        _append_site_flow_log(config, launch_name, {"action": "phase_a", "cmd": cmd_a, "returncode": rc, "outcome": outcome})
        sync_phase_a = _sync_after_step(config, launch, launch_name, "after_phase_a")
        if not _sync_is_ok(sync_phase_a):
            merge_orchestrator_launch_trace(
                launch,
                {
                    "terminal_status": "site_flow_sync_failed",
                    "terminal_detail": "sync after phase_a failed",
                    "sync_errors": sync_phase_a.get("sync_errors", []),
                    "flow": "run-site-flow",
                },
            )
            return {"ok": False, "failed_step": "sync_after_phase_a", "state": state, "sync": sync_phase_a}
        if rc != 0 or outcome == "timeout":
            if outcome != "timeout":
                merge_orchestrator_launch_trace(
                    launch,
                    {"terminal_status": "site_flow_phase_a_failed", "phase_a_exit": rc, "flow": "run-site-flow"},
                )
            return {"ok": False, "failed_step": "phase_a", "returncode": rc, "state": state, "phase_a_outcome": outcome}
    else:
        print("[run-site-flow] skip phase_a (state ok + phase_a summary/deferred на диске)", flush=True)

    b_ok = state.get("steps", {}).get("phase_b", {}).get("returncode") == 0
    run_phase_b = not (b_ok and deferred_manifest.is_file())
    if run_phase_b:
        state["steps"].pop("site_run", None)

    if run_phase_b:
        cmd_b = _build_phase_b_cmd(
            config,
            phase_b_id,
            deferred_manifest,
            reg,
            phase_b_branch,
            phase_b_allow_scaffold,
            launch_dir=launch_dir_arg,
        )
        r2 = subprocess.run(cmd_b, cwd=str(config.root_dir), text=True, encoding="utf-8", errors="replace")
        state["steps"]["phase_b"] = {"cmd": cmd_b, "returncode": r2.returncode, "at": _now_iso()}
        _save_flow_state(launch, state)
        _append_site_flow_log(config, launch_name, {"action": "phase_b", "cmd": cmd_b, "returncode": r2.returncode})
        sync_phase_b = _sync_after_step(config, launch, launch_name, "after_phase_b")
        if not _sync_is_ok(sync_phase_b):
            merge_orchestrator_launch_trace(
                launch,
                {
                    "terminal_status": "site_flow_sync_failed",
                    "terminal_detail": "sync after phase_b failed",
                    "sync_errors": sync_phase_b.get("sync_errors", []),
                    "flow": "run-site-flow",
                },
            )
            return {"ok": False, "failed_step": "sync_after_phase_b", "state": state, "sync": sync_phase_b}
        if r2.returncode != 0:
            merge_orchestrator_launch_trace(
                launch,
                {"terminal_status": "site_flow_phase_b_failed", "phase_b_exit": r2.returncode, "flow": "run-site-flow"},
            )
            return {"ok": False, "failed_step": "phase_b", "returncode": r2.returncode, "state": state}
    else:
        print("[run-site-flow] skip phase_b (state ok + deferred.json существует)", flush=True)

    if _human_all_stories_published(launch):
        print("[run-site-flow] skip site run (все .published_ok в Запуски)", flush=True)
        state["steps"]["site_run"] = {**(state.get("steps", {}).get("site_run") or {}), "skipped": True, "reason": "all_published_ok", "at": _now_iso()}
        _save_flow_state(launch, state)
        return {"ok": True, "failed_step": None, "returncode": 0, "state": state, "skipped_site_run": True}

    if recovery_items is not None:
        gate = evaluate_recovery_site_pipeline_gate(recovery_items)
        if gate.get("block"):
            c = gate.get("counters") or {}
            print("recovery gate blocked", flush=True)
            print(f"missing_selection={c.get('missing_selection', gate.get('missing_selection'))}", flush=True)
            print(f"missing_site_info={c.get('missing_site_info', gate.get('missing_site_info'))}", flush=True)
            print(f"next_step={gate.get('next_step', '')}", flush=True)
            merge_orchestrator_launch_trace(
                launch,
                {
                    "recovery_gate_blocked": True,
                    "missing_selection": c.get("missing_selection"),
                    "missing_site_info": c.get("missing_site_info"),
                    "next_step": gate.get("next_step"),
                    "flow": "run-site-flow",
                },
            )
            refresh_launch_status_file(launch)
            return {
                "ok": False,
                "failed_step": "recovery_site_pipeline_gate",
                "state": state,
                "recovery_gate": gate,
            }

    cmd_s = _build_site_run_cmd(config, site_run_id, pipeline_sd, launch_dir=launch_dir_arg)
    r3 = subprocess.run(cmd_s, cwd=str(config.root_dir), text=True, encoding="utf-8", errors="replace")
    state["steps"]["site_run"] = {"cmd": cmd_s, "returncode": r3.returncode, "at": _now_iso()}
    _save_flow_state(launch, state)
    _append_site_flow_log(config, launch_name, {"action": "site_run", "cmd": cmd_s, "returncode": r3.returncode})
    sync_site_run = _sync_after_step(config, launch, launch_name, "after_site_run")
    if not _sync_is_ok(sync_site_run):
        merge_orchestrator_launch_trace(
            launch,
            {
                "terminal_status": "site_flow_sync_failed",
                "terminal_detail": "sync after site_run failed",
                "sync_errors": sync_site_run.get("sync_errors", []),
                "flow": "run-site-flow",
            },
        )
        refresh_launch_status_file(launch)
        return {"ok": False, "failed_step": "sync_after_site_run", "state": state, "sync": sync_site_run}
    if r3.returncode != 0:
        merge_orchestrator_launch_trace(
            launch,
            {"terminal_status": "site_flow_site_run_failed", "site_run_exit": r3.returncode, "flow": "run-site-flow"},
        )
        refresh_launch_status_file(launch)
        return {"ok": False, "failed_step": "site_run", "returncode": r3.returncode, "state": state}

    merge_orchestrator_launch_trace(launch, {"site_flow_bat_done": True, "flow": "run-site-flow"})
    return {"ok": True, "failed_step": None, "returncode": 0, "state": state}


def default_site_run_base_from_launch_name(name: str) -> str:
    return sanitize_launch_folder_name(name).replace(" ", "_")
