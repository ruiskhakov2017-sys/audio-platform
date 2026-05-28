"""
Создание нового site-запуска в Запуски/<имя>/ (каркас + source.txt из каталога историй).

start-site / full-site-cycle без --invoke-legacy-phase-a не вызывают phase_a.
С --invoke-legacy-phase-a: оркестрация subprocess phase-a, preflight, staging (при limit>0), sync.
"""
from __future__ import annotations

import sys
import shutil
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import (
    D01_OBSHCHEE,
    D01_01_ISHODNYE,
    D02_SITE,
    D02_03_VISUAL,
    D05_RASSKAZY,
    D06_OTCHETY,
    D07_LOGI,
    D10_TEMP,
    F_MANIFEST,
    F_STATUS,
    F_ORCHESTRATOR_TRACE,
    F_SOURCE_TXT,
    F_STORY_STATUS,
    S01_OBSHCHEE,
    all_skeleton_relative_paths,
    generated_launch_name,
    human_zapuski_root,
    launch_legacy_runs_root,
    now_iso,
    read_json,
    sanitize_launch_folder_name,
    unique_launch_path,
    write_json,
)
from orchestrator.human_launch_gemini_preflight import run_gemini_preflight
from orchestrator.human_launch_legacy_sync import (
    mirror_legacy_pipeline_to_human,
    write_launch_legacy_binding,
)
from orchestrator.human_launch_lifecycle import (
    build_story_status_payload,
    clear_orchestrator_terminal_override,
    merge_orchestrator_launch_trace,
    refresh_launch_status_file,
    verify_runtime_launch,
)
from orchestrator.human_launch_phase_a_subprocess import run_orchestrator_phase_a_subprocess
from orchestrator.human_launch_proc_probe import count_legacy_hint_processes
from orchestrator.human_launch_staging import prepare_staging_test_input


def _print_console_safe(text: str) -> None:
    """Не ронять оркестратор на Windows (cp1251) из‑за символов в путях/именах файлов."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _list_story_txt_files(stories_dir: Path) -> list[Path]:
    if not stories_dir.is_dir():
        return []
    out = [p for p in stories_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
    return sorted(out, key=lambda x: x.name.lower())


def _resolve_effective_launch_name(name: str, *, smoke: bool) -> str:
    cleaned = sanitize_launch_folder_name(name)
    return cleaned if cleaned else generated_launch_name(smoke=smoke)


def _write_launch_readme(launch: Path) -> None:
    readme = launch / "README_СТРУКТУРА_ЗАПУСКА.md"
    text = "\n".join(
        [
            "# Структура запуска",
            "",
            "- `01_Общее` — входные тексты и служебные этапы подготовки.",
            "- `02_Сайт` — человекочитаемые артефакты сайта (очистка, info, визуал, озвучка, публикация).",
            "- `03_YouTube` — каркас YouTube-ветки (заполняется отдельным потоком).",
            "- `05_Рассказы` — главные per-story результаты запуска.",
            "- `06_Отчёты` — итоговые отчёты и постчеки.",
            "- `07_Логи` — логи запуска; часть legacy-логов может быть в `10_Временные_файлы/legacy/.../logs`.",
            "- `08_Карантин` — проблемные артефакты/файлы.",
            "- `09_Архив` — архив внутри запуска.",
            "- `10_Временные_файлы` — техническая кухня legacy-скриптов.",
            "",
            "Где смотреть результат сайта:",
            "- `05_Рассказы/<story>/03_Сайт/...`",
            "- `02_Сайт/...`",
            "",
            "Visual Excel:",
            "- `02_Сайт/03_Визуал_для_сайта/visual_prompts.xlsx`",
            "",
            "MP3:",
            "- `05_Рассказы/<story>/03_Сайт/04_Озвучка/audio.mp3`",
            "",
            "Если запуск плохой — удаляйте целиком папку этого запуска в `Запуски/<имя>`.",
            "",
        ]
    )
    readme.write_text(text, encoding="utf-8")


def _canonical_from_story_path(story_path: Path) -> str:
    return story_path.stem.strip()


def _scan_output_conflicts(config: OrchestratorConfig, stories_dir: Path) -> list[dict[str, str]]:
    out_site = (config.root_dir / "output" / "site").resolve()
    conflicts: list[dict[str, str]] = []
    for p in _list_story_txt_files(stories_dir):
        canonical = _canonical_from_story_path(p)
        if not canonical:
            continue
        dst = out_site / canonical
        if dst.exists():
            conflicts.append({"canonical": canonical, "output_path": str(dst), "source_story": str(p)})
    return conflicts


def _archive_existing_output_conflicts(config: OrchestratorConfig, conflicts: list[dict[str, str]]) -> list[dict[str, str]]:
    archived: list[dict[str, str]] = []
    if not conflicts:
        return archived
    stamp = now_iso().replace(":", "-")
    base_archive = (config.root_dir / "output" / "site" / "_conflict_archive" / stamp).resolve()
    base_archive.mkdir(parents=True, exist_ok=True)
    for row in conflicts:
        src = Path(str(row.get("output_path", "")))
        if not src.exists():
            continue
        dst = base_archive / src.name
        idx = 2
        while dst.exists():
            dst = base_archive / f"{src.name}_v{idx}"
            idx += 1
        shutil.move(str(src), str(dst))
        archived.append({"old_path": str(src), "archive_path": str(dst), "reason": "output_conflict", "timestamp": now_iso()})
    return archived


def _resolve_pipeline_stories_dir(launch: Path, fallback: Path) -> Path:
    manifest = read_json(launch / F_MANIFEST) or {}
    if bool(manifest.get("use_input_snapshot")):
        snap = str(manifest.get("input_stories_dir") or "").strip()
        if snap and Path(snap).is_dir():
            return Path(snap).resolve()
    return fallback.resolve()


def launch_resume_is_blocked(manifest: dict[str, Any] | None) -> str | None:
    """
    Если manifest помечен cancelled / resume_blocked — не продолжать run-site-flow на этом имени.
    """
    m = manifest or {}
    if str(m.get("launch_status", "")).strip().lower() == "cancelled":
        return "launch_status=cancelled"
    if m.get("resume_blocked") is True:
        return "resume_blocked=true"
    return None


def start_site_launch(
    config: OrchestratorConfig,
    *,
    name: str,
    stories_dir: Path,
    limit: int,
    execute: bool = False,
    output_conflict_policy: str = "fail",
    use_input_snapshot: bool = False,
) -> dict[str, Any]:
    desired = _resolve_effective_launch_name(name, smoke=False)
    cand = (human_zapuski_root(config.root_dir) / desired).resolve()
    if cand.exists():
        # Resume-friendly: если папка запуска уже есть и содержит базовые файлы, переиспользуем её.
        manifest_ok = (cand / F_MANIFEST).is_file()
        status_ok = (cand / F_STATUS).is_file()
        if manifest_ok or status_ok:
            launch = cand
        else:
            launch = unique_launch_path(config.root_dir, desired)
    else:
        launch = cand
    sd = stories_dir.resolve()
    if not sd.is_dir():
        msg = f"stories-dir not found: {sd}"
        print(msg)
        return {"ok": False, "message": msg}
    txts = _list_story_txt_files(sd)
    if limit > 0:
        txts = txts[:limit]
    story_ids = [sanitize_launch_folder_name(p.stem) or p.stem for p in txts]

    plan_actions: list[str] = []
    for rel in all_skeleton_relative_paths():
        plan_actions.append(f"mkdir {launch / rel}")
    vchod = launch / D01_OBSHCHEE / D01_01_ISHODNYE / "Вход"
    for p, sid in zip(txts, story_ids):
        plan_actions.append(f"copy {p} -> {vchod / p.name}")
        plan_actions.append(f"copy {p} -> {launch / D05_RASSKAZY / sid / S01_OBSHCHEE / F_SOURCE_TXT}")
        plan_actions.append(f"write {launch / D05_RASSKAZY / sid / F_STORY_STATUS}")

    if not story_ids:
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "launch_path": str(launch.resolve()),
                "stories_input": str(sd),
                "story_count": 0,
                "story_ids": [],
                "plan_actions_sample": plan_actions[:40],
                "plan_actions_total": len(plan_actions),
                "note": "нет .txt в stories-dir — в плане только mkdir каркаса",
            }
        msg = f"нет .txt в {sd}"
        print(msg)
        return {"ok": False, "message": msg}

    if not execute:
        return {
            "ok": True,
            "dry_run": True,
            "launch_path": str(launch.resolve()),
            "stories_input": str(sd),
            "story_count": len(story_ids),
            "story_ids": story_ids,
            "plan_actions_sample": plan_actions[:40],
            "plan_actions_total": len(plan_actions),
            "note": "Без --execute файлы не создаются. Gemini/phase_a не вызываются.",
        }

    human_zapuski_root(config.root_dir).mkdir(parents=True, exist_ok=True)
    launch.mkdir(parents=True, exist_ok=True)
    for rel in all_skeleton_relative_paths():
        (launch / rel).mkdir(parents=True, exist_ok=True)
    vchod.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "human_launch_name": launch.name,
        "requested_name": desired,
        "created_at": now_iso(),
        "run_mode": "site",
        "source_run_id": "",
        "source_branch": "site",
        "input_stories_dir": str(stories_dir.resolve()),
        "primary_human_root": str(launch.resolve()),
        "story_ids": story_ids,
        "output_conflict_policy": str(output_conflict_policy or "fail").strip().lower() or "fail",
        "use_input_snapshot": bool(use_input_snapshot),
        "note": "Новый запуск в Запуски/; phase_a legacy при необходимости — отдельно (full-site-cycle --invoke-legacy-phase-a).",
    }
    if use_input_snapshot:
        snap = (launch / D01_OBSHCHEE / "input_snapshot").resolve()
        snap.mkdir(parents=True, exist_ok=True)
        for p in txts:
            shutil.copy2(p, snap / p.name)
        manifest["source_stories_dir"] = str(sd.resolve())
        manifest["input_snapshot_dir"] = str(snap)
        manifest["snapshot_file_count"] = len(txts)
        manifest["snapshot_created_at"] = now_iso()
        manifest["input_stories_dir"] = str(snap)
    write_json(launch / F_MANIFEST, manifest)
    _write_launch_readme(launch)

    for p, sid in zip(txts, story_ids):
        dest_in = vchod / p.name
        shutil.copy2(p, dest_in)
        sroot = launch / D05_RASSKAZY / sid
        (sroot / S01_OBSHCHEE).mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, sroot / S01_OBSHCHEE / F_SOURCE_TXT)
        write_json(sroot / F_STORY_STATUS, build_story_status_payload(launch, sid))

    refresh_launch_status_file(launch)

    return {
        "ok": True,
        "dry_run": False,
        "launch_path": str(launch.resolve()),
        "story_count": len(story_ids),
        "story_ids": story_ids,
    }


def full_site_cycle_plan(
    config: OrchestratorConfig,
    *,
    name: str,
    stories_dir: Path,
    limit: int,
    invoke_legacy_phase_a: bool,
    execute: bool,
) -> dict[str, Any]:
    """План полного site cycle; реальный phase_a только с --execute и --invoke-legacy-phase-a."""
    boot = start_site_launch(config, name=name, stories_dir=stories_dir, limit=limit, execute=False)
    if not boot.get("ok"):
        print(f"[ERROR] {boot.get('message', 'start-site preview failed')}")
        return {"ok": False, "message": boot.get("message")}
    lines = [
        "=== full-site-cycle (план) ===",
        f"1) start-site -> {boot.get('launch_path')}",
        f"   stories: {boot.get('story_count')} из {stories_dir}",
        f"2) Каркас 01..10, manifest.json, status.json, 05_Рассказы/<id>/source.txt",
        "3) Этапы length / Gemini / clean / TTS / publish — в новой структуре (подключение phase_a — следующий PR).",
    ]
    if invoke_legacy_phase_a:
        lines.append(
            "4) С --execute --invoke-legacy-phase-a: subprocess `python -m orchestrator phase-a ...` "
            "(Gemini); перед стартом — gemini-preflight; при --limit>0 intake только из "
            "Запуски/.../10_Временные_файлы/test_input/."
        )
        if limit <= 0:
            lines.append(
                "[WARN] --limit=0: phase-a сканирует весь stories-dir; для теста 1–2 файлов — "
                "`launch smoke-site-cycle` или `--limit N`."
            )
    else:
        lines.append("4) Без --invoke-legacy-phase-a: phase_a / Gemini не вызываются.")
    if not execute:
        lines.append("Сейчас DRY-RUN: ничего не записано (кроме этого текста).")
    text = "\n".join(lines)
    print(text)
    return {"ok": True, "text": text, "bootstrap_preview": boot, "invoke_legacy_phase_a": invoke_legacy_phase_a}


def _print_runtime_story_summary(config: OrchestratorConfig, launch: Path) -> dict[str, Any]:
    stories = (
        sorted([p.name for p in (launch / D05_RASSKAZY).iterdir() if p.is_dir()])
        if (launch / D05_RASSKAZY).is_dir()
        else []
    )
    sel_done = 0
    clean_done = 0
    site_info_done = 0
    pending = 0
    invalid = 0
    for sid in stories:
        st = build_story_status_payload(launch, sid)
        if str(st.get("primary_selection")) == "done":
            sel_done += 1
        site = st.get("site") or {}
        if str(site.get("text_cleaning")) == "done":
            clean_done += 1
        if str(site.get("site_info")) == "done":
            site_info_done += 1
        if str(st.get("status")) == "failed":
            invalid += 1
        elif str(st.get("next_stage")) != "completed":
            pending += 1
        write_json((launch / D05_RASSKAZY / sid / F_STORY_STATUS), st)
    print("=== runtime story summary ===")
    print(f"launch: {launch}")
    print(f"stories: {len(stories)}")
    print(f"selection.done: {sel_done}")
    print(f"cleaning.done: {clean_done}")
    print(f"site_info.done: {site_info_done}")
    print(f"pending: {pending} invalid: {invalid}")
    for sid in stories[:5]:
        print(f"story path: {launch / D05_RASSKAZY / sid}")
    return {
        "stories": stories,
        "sel_done": sel_done,
        "clean_done": clean_done,
        "site_info_done": site_info_done,
        "pending": pending,
        "invalid": invalid,
    }


def _invoke_site_phase_a_bundle(
    config: OrchestratorConfig,
    *,
    launch: Path,
    rid: str,
    stories_dir_original: Path,
    limit: int,
    gemini_registry_path: Path,
    extensions: list[str],
    max_runtime_minutes: float,
    force_staging: bool,
    flow_label: str,
    gemini_target_active_workers: int = 3,
    output_conflict_policy: str = "fail",
) -> dict[str, Any]:
    write_launch_legacy_binding(config, launch, run_id=rid, branch="site")

    policy = str(output_conflict_policy or "fail").strip().lower() or "fail"
    use_staging = bool(force_staging or limit > 0)
    staging_meta: dict[str, Any] = {}
    if use_staging:
        cap = max(1, int(limit)) if limit > 0 else 2
        staging_suffix = ""
        if flow_label == "smoke-site-cycle" and policy == "test-suffix":
            staging_suffix = f"__SMOKE_{rid}"
        staging_meta = prepare_staging_test_input(
            launch,
            stories_dir_original,
            cap,
            execute=True,
            name_suffix=staging_suffix,
        )
        if not staging_meta.get("ok"):
            merge_orchestrator_launch_trace(
                launch,
                {
                    "terminal_status": "failed_preflight",
                    "terminal_detail": staging_meta.get("message", "staging_failed"),
                    "flow": flow_label,
                },
            )
            sync = mirror_legacy_pipeline_to_human(config, launch, execute=True)
            refresh_launch_status_file(launch)
            return {
                "ok": False,
                "phase_a": "skipped_staging",
                "staging": staging_meta,
                "sync": sync,
                "phase_a_cmd": [],
                "phase_a_exit": None,
                "preflight": None,
            }
        stories_for_phase_a = Path(str(staging_meta["staging_dir"])).resolve()
        queue_hint = int(staging_meta.get("file_count") or 0)
        manifest = read_json(launch / F_MANIFEST) or {}
        manifest["phase_a_stories_dir"] = str(stories_for_phase_a)
        manifest["phase_a_staging"] = True
        manifest["phase_a_staging_name_mapping"] = staging_meta.get("name_mapping", [])
        manifest["output_conflict_policy"] = policy
        write_json(launch / F_MANIFEST, manifest)
        print(f"{flow_label}: staging intake -> {stories_for_phase_a} files={queue_hint}")
    else:
        stories_for_phase_a = stories_dir_original.resolve()
        queue_hint = len(_list_story_txt_files(stories_for_phase_a))
        print(f"[WARN] {flow_label}: phase-a intake = полный каталог {stories_for_phase_a} (файлов ~{queue_hint})")
        manifest = read_json(launch / F_MANIFEST) or {}
        manifest["phase_a_stories_dir"] = str(stories_for_phase_a)
        manifest["phase_a_staging"] = False
        manifest["output_conflict_policy"] = policy
        write_json(launch / F_MANIFEST, manifest)

    cap = max(1, int(limit)) if limit > 0 else 50
    if limit <= 0 and not force_staging:
        print("[WARN] --limit=0: для phase-a используется --max-stories 50 (потолок).")

    conflicts_before = _scan_output_conflicts(config, stories_for_phase_a)
    print(f"{flow_label}: output_conflicts_before: {len(conflicts_before)}")
    if conflicts_before:
        for row in conflicts_before[:20]:
            print(
                f"{flow_label}: output_conflict canonical={row.get('canonical')} "
                f"path={row.get('output_path')} policy={policy}"
            )
    if conflicts_before and policy == "fail":
        merge_orchestrator_launch_trace(
            launch,
            {
                "terminal_status": "failed_preflight_output_conflict",
                "terminal_detail": f"output conflicts before phase-a: {len(conflicts_before)}",
                "conflicts": conflicts_before[:30],
                "suggested_policy": "test-suffix (smoke) or skip-existing (full-site-cycle)",
                "flow": flow_label,
            },
        )
        sync = mirror_legacy_pipeline_to_human(config, launch, execute=True)
        write_json(launch / D10_TEMP / "last_sync_report.json", sync)
        refresh_launch_status_file(launch)
        return {
            "ok": False,
            "phase_a": "skipped_preflight_output_conflict",
            "preflight": None,
            "sync": sync,
            "phase_a_cmd": [],
            "phase_a_exit": None,
            "staging": staging_meta,
            "output_conflicts_before": conflicts_before,
        }
    if conflicts_before and policy == "skip-existing":
        non_conflicting = [p for p in _list_story_txt_files(stories_for_phase_a) if not (config.root_dir / "output" / "site" / _canonical_from_story_path(p)).exists()]
        conflicting_canon = {_canonical_from_story_path(Path(str(x.get("source_story", "")))) for x in conflicts_before}
        stories_root = launch / D05_RASSKAZY
        for sid_dir in sorted([p for p in stories_root.iterdir() if p.is_dir()], key=lambda x: x.name.lower()) if stories_root.is_dir() else []:
            sid = sid_dir.name
            if sid not in conflicting_canon:
                continue
            st = build_story_status_payload(launch, sid)
            st["status"] = "failed"
            st["next_stage"] = "repair_required"
            st.setdefault("errors", [])
            st["errors"].append("skipped_existing_output")
            st["output_conflict"] = {
                "policy": "skip-existing",
                "state": "skipped_existing",
                "canonical": sid,
                "output_path": str((config.root_dir / "output" / "site" / sid).resolve()),
            }
            write_json(sid_dir / F_STORY_STATUS, st)
        filtered_dir = (launch / D10_TEMP / "phase_a_input_filtered").resolve()
        if filtered_dir.exists():
            shutil.rmtree(filtered_dir, ignore_errors=True)
        filtered_dir.mkdir(parents=True, exist_ok=True)
        for p in non_conflicting:
            shutil.copy2(p, filtered_dir / p.name)
        stories_for_phase_a = filtered_dir
        queue_hint = len(non_conflicting)
        cap = min(cap, queue_hint) if queue_hint > 0 else 0
        merge_orchestrator_launch_trace(
            launch,
            {
                "output_conflicts_before": len(conflicts_before),
                "output_conflicts_skipped": conflicts_before[:30],
                "output_conflict_policy": policy,
                "phase_a_filtered_input_dir": str(filtered_dir),
            },
        )
        print(f"{flow_label}: policy=skip-existing -> phase-a input reduced to {queue_hint} file(s)")
        if queue_hint == 0:
            sync = mirror_legacy_pipeline_to_human(config, launch, execute=True)
            write_json(launch / D10_TEMP / "last_sync_report.json", sync)
            refresh_launch_status_file(launch)
            return {
                "ok": True,
                "phase_a": "skipped_all_existing_output",
                "preflight": None,
                "sync": sync,
                "phase_a_cmd": [],
                "phase_a_exit": 0,
                "staging": staging_meta,
                "output_conflicts_before": conflicts_before,
            }
    if conflicts_before and policy == "archive-existing":
        archived = _archive_existing_output_conflicts(config, conflicts_before)
        write_json(launch / D10_TEMP / "output_conflict_archive_manifest.json", {"items": archived, "policy": policy})
        print(f"{flow_label}: archive-existing moved={len(archived)}")

    preflight = run_gemini_preflight(
        config,
        stories_dir_for_intake=stories_for_phase_a,
        queue_file_count=queue_hint,
        story_run_id=rid,
        gemini_registry_path=gemini_registry_path,
        extensions=extensions,
        gemini_target_active_workers=max(1, min(5, int(gemini_target_active_workers))),
    )
    (launch / D10_TEMP).mkdir(parents=True, exist_ok=True)
    write_json(launch / D10_TEMP / "gemini_preflight_last.json", preflight)
    print(f"{flow_label}: gemini-preflight ok={preflight.get('ok')} reasons={preflight.get('reasons')}")
    if not preflight.get("ok"):
        merge_orchestrator_launch_trace(
            launch,
            {
                "terminal_status": "failed_preflight",
                "terminal_detail": ("; ".join(preflight.get("reasons") or []))[:2000],
                "preflight": {k: v for k, v in preflight.items() if k != "python_processes_sample"},
                "flow": flow_label,
            },
        )
        sync = mirror_legacy_pipeline_to_human(config, launch, execute=True)
        write_json(launch / D10_TEMP / "last_sync_report.json", sync)
        refresh_launch_status_file(launch)
        return {
            "ok": False,
            "phase_a": "skipped_preflight",
            "preflight": preflight,
            "sync": sync,
            "phase_a_cmd": [],
            "phase_a_exit": None,
            "staging": staging_meta,
        }

    reg_abs = (
        gemini_registry_path.resolve()
        if gemini_registry_path.is_absolute()
        else (config.root_dir / gemini_registry_path).resolve()
    )
    cmd = [
        sys.executable,
        "-m",
        "orchestrator",
        "phase-a",
        "--stories-dir",
        str(stories_for_phase_a),
        "--story-id",
        rid,
        "--max-stories",
        str(cap),
        "--gemini-target-active-workers",
        str(max(1, min(5, int(gemini_target_active_workers)))),
        "--gemini-registry",
        str(reg_abs),
        "--execute",
        "--launch-dir",
        str(launch.resolve()),
    ]
    timeout_sec = float(max_runtime_minutes) * 60.0 if float(max_runtime_minutes) > 0 else None
    merge_orchestrator_launch_trace(
        launch,
        {
            "phase_a_subprocess_attempted": True,
            "phase_a_stories_dir": str(stories_for_phase_a),
            "flow": flow_label,
            "max_runtime_minutes": float(max_runtime_minutes),
        },
    )
    print(f"{flow_label}: invoking legacy phase-a:", " ".join(cmd))
    run_out = run_orchestrator_phase_a_subprocess(config, cmd, timeout_seconds=timeout_sec)
    rc = int(run_out.get("returncode", 1))
    outcome = str(run_out.get("outcome", "completed"))

    if outcome == "timeout":
        merge_orchestrator_launch_trace(
            launch,
            {
                "terminal_status": "phase_a_timeout",
                "terminal_detail": str(run_out.get("kill_detail", "")),
                "phase_a_exit": rc,
                "flow": flow_label,
            },
        )
    elif rc != 0:
        run_log_legacy = (launch_legacy_runs_root(launch, "site", rid) / "run.log").resolve()
        run_log_root = (config.root_dir / "runs" / "site" / rid / "run.log").resolve()
        run_log = run_log_legacy if run_log_legacy.is_file() else run_log_root
        conflict_line = ""
        conflicting_canonical = ""
        conflicting_path = ""
        if run_log.is_file():
            txt = run_log.read_text(encoding="utf-8", errors="ignore")
            for ln in txt.splitlines()[::-1]:
                if "Output already exists for canonical_basename" in ln:
                    conflict_line = ln.strip()
                    try:
                        # Output already exists for canonical_basename='foo': C:\...\output\site\foo.
                        part = ln.split("canonical_basename='", 1)[1]
                        conflicting_canonical = part.split("'", 1)[0].strip()
                        conflicting_path = ln.split(":", 1)[1].strip().rstrip(".")
                    except Exception:
                        pass
                    break
        merge_orchestrator_launch_trace(
            launch,
            {
                "terminal_status": "phase_a_failed",
                "terminal_detail": conflict_line or f"phase_a exit={rc}",
                "phase_a_exit": rc,
                "reason": "output_conflict" if conflicting_canonical else "phase_a_error",
                "conflicting_canonical": conflicting_canonical,
                "conflicting_path": conflicting_path,
                "suggested_policy": "test-suffix (smoke) or skip-existing (full-site-cycle)",
                "flow": flow_label,
            },
        )
    else:
        clear_orchestrator_terminal_override(launch)
        merge_orchestrator_launch_trace(
            launch,
            {"phase_a_subprocess_exit": rc, "phase_a_outcome": outcome, "flow": flow_label},
        )

    sync = mirror_legacy_pipeline_to_human(config, launch, execute=True)
    write_json(launch / D10_TEMP / "last_sync_report.json", sync)
    refresh_launch_status_file(launch)
    mapping_rows = sync.get("selection_info_mapping") or []
    for row in mapping_rows:
        if not isinstance(row, dict):
            continue
        _print_console_safe(f"sync mapping: {row.get('source_info')} -> {row.get('story_id')}")

    return {
        "ok": rc == 0 and outcome == "completed",
        "phase_a_exit": rc,
        "phase_a_outcome": outcome,
        "phase_a_cmd": cmd,
        "sync": sync,
        "preflight": preflight,
        "staging": staging_meta,
        "run_out": run_out,
    }


def _print_honest_footer(
    *,
    launch: Path,
    flow_label: str,
    bundle: dict[str, Any],
    summ: dict[str, Any],
    vr: dict[str, Any],
    alive: int,
) -> None:
    stats = (vr or {}).get("stats") or {}
    trace = read_json(launch / D10_TEMP / F_ORCHESTRATOR_TRACE) or {}
    phase_started = bool(bundle.get("phase_a_cmd"))
    pre = bundle.get("preflight") or {}
    print(f"=== {flow_label} honest report ===")
    print(f"phase_a_subprocess_started: {phase_started}")
    print(f"gemini_preflight_ok: {pre.get('ok') if pre else 'n/a'}")
    print(f"phase_a_exit: {bundle.get('phase_a_exit')} outcome: {bundle.get('phase_a_outcome')}")
    print(f"completed_selection.done (heuristic): {summ.get('sel_done', 0)}")
    print(f"sync copied files (pipeline): {bundle.get('sync', {}).get('copied', 0)}")
    print(f"legacy_selection_info_found: {bundle.get('sync', {}).get('legacy_selection_info_found', 0)}")
    print(f"synced_selection_raw: {bundle.get('sync', {}).get('synced_selection_raw', 0)}")
    print(f"synced_selection_result: {bundle.get('sync', {}).get('synced_selection_result', 0)}")
    print(f"sync_errors: {len(bundle.get('sync', {}).get('sync_errors', []) or [])}")
    print(f"selection_raw (human): {stats.get('selection_raw', 0)}")
    print(f"selection_result (human): {stats.get('selection_result', 0)}")
    print(f"site_info_json: {stats.get('site_info_json', 0)} info_en_txt: {stats.get('info_en_txt', 0)}")
    term = str(trace.get("terminal_status", "")).strip()
    failed_reason = term
    if not failed_reason:
        if bundle.get("phase_a") == "skipped_preflight":
            failed_reason = "failed_preflight"
        elif str(bundle.get("phase_a_outcome")) == "timeout":
            failed_reason = "timeout"
        elif not bundle.get("ok"):
            failed_reason = "Gemini UI/browser failure or phase_a error"
        else:
            failed_reason = "ok"
    can_continue = alive == 0 and bool(summ.get("stories"))
    print(f"failed_reason: {failed_reason}")
    print(f"can_continue: {can_continue}")
    print(
        "next_action: частичные артефакты — `launch sync-legacy --execute`; диагностика — "
        "`launch verify-runtime`; smoke — `launch smoke-site-cycle --execute`."
    )
    print(f"active_legacy_hint_processes_after: {alive}")


def _run_full_flow_postcheck(
    config: OrchestratorConfig,
    *,
    launch: Path,
) -> dict[str, Any]:
    stories_root = (launch / D05_RASSKAZY).resolve()
    stories = sorted([p for p in stories_root.iterdir() if p.is_dir()], key=lambda x: x.name.lower()) if stories_root.is_dir() else []
    source_count = 0
    raw_sel_count = 0
    cleaned_count = 0
    site_info_count = 0
    for sdir in stories:
        if (sdir / "01_Общее" / "source.txt").is_file():
            source_count += 1
        if (sdir / "02_Отбор" / "result.json").is_file() or (sdir / "02_Отбор" / "raw_response.txt").is_file():
            raw_sel_count += 1
        if (sdir / "03_Сайт" / "01_Очищенный_текст" / "cleaned_story.txt").is_file():
            cleaned_count += 1
        if (sdir / "03_Сайт" / "02_Информация_для_сайта" / "site_info.json").is_file() or (
            sdir / "03_Сайт" / "02_Информация_для_сайта" / "info.en.txt"
        ).is_file():
            site_info_count += 1

    visual_xlsx = (launch / D02_SITE / D02_03_VISUAL / "visual_prompts.xlsx").resolve()
    visual_csv = (launch / D02_SITE / D02_03_VISUAL / "visual_prompts.csv").resolve()
    legacy_runs = launch / D10_TEMP / "legacy" / "runs" / "site"
    visual_stage_ran = False
    if legacy_runs.is_dir():
        for p in legacy_runs.rglob("visual_prompts.xlsx"):
            if p.is_file():
                visual_stage_ran = True
                break
        if not visual_stage_ran:
            for p in legacy_runs.rglob("visual_manifest.json"):
                if p.is_file():
                    visual_stage_ran = True
                    break

    report_dir = (launch / D06_OTCHETY).resolve()
    logs_dir = (launch / D07_LOGI).resolve()
    report_files = [p for p in report_dir.rglob("*") if p.is_file()] if report_dir.is_dir() else []
    logs_files = [p for p in logs_dir.rglob("*") if p.is_file()] if logs_dir.is_dir() else []
    legacy_logs = launch / D10_TEMP / "legacy" / "runs"
    legacy_logs_files = [p for p in legacy_logs.rglob("*.log")] if legacy_logs.is_dir() else []

    root_runs = (config.root_dir / "runs").resolve()
    root_output = (config.root_dir / "output").resolve()
    root_runs_nonempty = root_runs.is_dir() and any(root_runs.iterdir())
    root_output_nonempty = root_output.is_dir() and any(root_output.iterdir())

    checks = {
        "stories_dir_exists": stories_root.is_dir(),
        "stories_nonempty": len(stories) > 0,
        "source_present": source_count > 0,
        "selection_present": raw_sel_count > 0,
        "cleaned_present": cleaned_count > 0,
        "site_info_present": site_info_count > 0,
        "visual_prompts_xlsx_present_if_stage_ran": (not visual_stage_ran) or visual_xlsx.is_file(),
        "reports_nonempty": len(report_files) > 0,
        "logs_nonempty_or_legacy_logs_present": len(logs_files) > 0 or len(legacy_logs_files) > 0,
    }
    ok = all(checks.values())
    payload = {
        "ok": ok,
        "launch": str(launch),
        "story_folders": len(stories),
        "source_txt_count": source_count,
        "selection_count": raw_sel_count,
        "cleaned_story_count": cleaned_count,
        "site_info_count": site_info_count,
        "visual_prompts_xlsx": str(visual_xlsx),
        "visual_prompts_xlsx_exists": visual_xlsx.is_file(),
        "report_files_count": len(report_files),
        "logs_files_count": len(logs_files),
        "legacy_logs_count": len(legacy_logs_files),
        "root_runs_nonempty": root_runs_nonempty,
        "root_output_nonempty": root_output_nonempty,
        "checks": checks,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    if ok:
        write_json(report_dir / "full_flow_postcheck_ok.json", payload)
    else:
        payload["error"] = "FULL FLOW FAILED: legacy artifacts were not synchronized into human launch folders"
        write_json(report_dir / "full_flow_postcheck_failed.json", payload)
    print(
        f"postcheck: launch={launch.name} stories={len(stories)} cleaned={cleaned_count} "
        f"site_info={site_info_count} visual_xlsx={visual_xlsx.is_file()} "
        f"reports={len(report_files)} logs={len(logs_files)} "
        f"root_runs_nonempty={root_runs_nonempty} root_output_nonempty={root_output_nonempty}",
        flush=True,
    )
    return payload


def full_site_cycle_execute(
    config: OrchestratorConfig,
    *,
    name: str,
    stories_dir: Path,
    limit: int,
    invoke_legacy_phase_a: bool,
    max_runtime_minutes: float = 0,
    gemini_registry_path: Path | None = None,
    story_extensions: list[str] | None = None,
    output_conflict_policy: str = "fail",
) -> dict[str, Any]:
    effective_name = _resolve_effective_launch_name(name, smoke=False)
    existing = (human_zapuski_root(config.root_dir) / effective_name).resolve()
    if (existing / F_MANIFEST).is_file() and (existing / F_STATUS).is_file():
        launch = existing
        r1 = {"ok": True, "launch_path": str(launch), "reused_existing_launch": True}
    else:
        r1 = start_site_launch(
            config,
            name=effective_name,
            stories_dir=stories_dir,
            limit=limit,
            execute=True,
            output_conflict_policy=output_conflict_policy,
        )
        if not r1.get("ok"):
            return r1
        launch = Path(r1["launch_path"])
    pipeline_sd = _resolve_pipeline_stories_dir(launch, stories_dir)
    if not invoke_legacy_phase_a:
        print("full-site-cycle: start-site выполнен; phase_a не вызывался (нет --invoke-legacy-phase-a).")
        return {**r1, "phase_a": "skipped", "pipeline_stories_dir": str(pipeline_sd)}

    rid = sanitize_launch_folder_name(launch.name)
    reg = gemini_registry_path or Path("configs/gemini_bots_registry.example.yaml")
    ext = story_extensions if story_extensions is not None else list(config.pre_filter_extensions)

    bundle = _invoke_site_phase_a_bundle(
        config,
        launch=launch,
        rid=rid,
        stories_dir_original=pipeline_sd,
        limit=limit,
        gemini_registry_path=reg,
        extensions=ext,
        max_runtime_minutes=float(max_runtime_minutes),
        force_staging=False,
        flow_label="full-site-cycle",
        output_conflict_policy=output_conflict_policy,
    )
    summ = _print_runtime_story_summary(config, launch)
    sync = bundle.get("sync") or {}
    print(f"sync copied: {sync.get('copied', 0)} tts_synced: {sync.get('tts_synced', 0)} publish_synced: {sync.get('publish_synced', 0)}")
    vr = verify_runtime_launch(config, human_name=launch.name)
    alive = count_legacy_hint_processes(story_run_id=rid)
    print(f"active_legacy_hint_processes(run-related): {alive}")
    _print_honest_footer(
        launch=launch,
        flow_label="full-site-cycle",
        bundle=bundle,
        summ=summ,
        vr=vr,
        alive=alive,
    )
    ok = bool(bundle.get("ok")) and int(bundle.get("phase_a_exit") or 0) == 0
    return {**r1, **bundle, "pipeline_stories_dir": str(pipeline_sd), "runtime_story_summary": summ, "verify_runtime": vr, "ok": ok}


def smoke_site_cycle_execute(
    config: OrchestratorConfig,
    *,
    name: str,
    stories_dir: Path,
    limit: int,
    max_runtime_minutes: float,
    gemini_registry_path: Path | None = None,
    story_extensions: list[str] | None = None,
    output_conflict_policy: str = "test-suffix",
) -> dict[str, Any]:
    """Безопасный тест: staging, preflight, phase-a, таймаут, sync, verify-runtime."""
    effective_name = _resolve_effective_launch_name(name, smoke=True)
    existing = (human_zapuski_root(config.root_dir) / effective_name).resolve()
    if (existing / F_MANIFEST).is_file() and (existing / F_STATUS).is_file():
        launch = existing
        r1 = {"ok": True, "launch_path": str(launch), "reused_existing_launch": True}
    else:
        r1 = start_site_launch(
            config,
            name=effective_name,
            stories_dir=stories_dir,
            limit=limit,
            execute=True,
            output_conflict_policy=output_conflict_policy,
        )
        if not r1.get("ok"):
            return r1
        launch = Path(r1["launch_path"])
    pipeline_sd = _resolve_pipeline_stories_dir(launch, stories_dir)
    rid = sanitize_launch_folder_name(launch.name)
    reg = gemini_registry_path or Path("configs/gemini_bots_registry.example.yaml")
    ext = story_extensions if story_extensions is not None else list(config.pre_filter_extensions)
    lim = max(1, int(limit) or 2)
    configured_active_workers = 3
    if lim <= 2:
        smoke_active_workers = 1
        smoke_override_reason = f"limit={lim}"
    elif lim <= 10:
        smoke_active_workers = min(2, configured_active_workers)
        smoke_override_reason = f"limit={lim}"
    else:
        smoke_active_workers = configured_active_workers
        smoke_override_reason = "limit>10"
    print(f"smoke active_workers override: {smoke_active_workers}")
    print(f"reason: {smoke_override_reason}")

    bundle = _invoke_site_phase_a_bundle(
        config,
        launch=launch,
        rid=rid,
        stories_dir_original=pipeline_sd,
        limit=lim,
        gemini_registry_path=reg,
        extensions=ext,
        max_runtime_minutes=float(max_runtime_minutes),
        force_staging=True,
        flow_label="smoke-site-cycle",
        gemini_target_active_workers=smoke_active_workers,
        output_conflict_policy=output_conflict_policy,
    )
    summ = _print_runtime_story_summary(config, launch)
    sync = bundle.get("sync") or {}
    print(f"sync copied: {sync.get('copied', 0)} tts_synced: {sync.get('tts_synced', 0)} publish_synced: {sync.get('publish_synced', 0)}")
    vr = verify_runtime_launch(config, human_name=launch.name)
    alive = count_legacy_hint_processes(story_run_id=rid)
    print(f"active_legacy_hint_processes(run-related): {alive}")
    _print_honest_footer(
        launch=launch,
        flow_label="smoke-site-cycle",
        bundle=bundle,
        summ=summ,
        vr=vr,
        alive=alive,
    )
    ok = bool(bundle.get("ok")) and int(bundle.get("phase_a_exit") or 0) == 0
    return {**r1, **bundle, "pipeline_stories_dir": str(pipeline_sd), "runtime_story_summary": summ, "verify_runtime": vr, "ok": ok}


def smoke_site_cycle_plan(
    config: OrchestratorConfig,
    *,
    name: str,
    stories_dir: Path,
    limit: int,
    max_runtime_minutes: float,
) -> dict[str, Any]:
    lim = max(1, int(limit) or 2)
    boot = start_site_launch(
        config,
        name=_resolve_effective_launch_name(name, smoke=True),
        stories_dir=stories_dir,
        limit=lim,
        execute=False,
    )
    if not boot.get("ok"):
        return boot
    lines = [
        "=== smoke-site-cycle (план, без --execute) ===",
        f"1) start-site -> {boot.get('launch_path')} (stories={boot.get('story_count')})",
        f"2) staging -> Запуски/<имя>/10_Временные_файлы/test_input/ ({lim} .txt из {stories_dir})",
        "3) launch gemini-preflight (профили Chrome, registry, конфликт phase-a)",
        f"4) subprocess phase-a на staging с --max-runtime-minutes={max_runtime_minutes}",
        "5) partial sync -> Запуски (всегда) + verify-runtime + honest report",
        "Для реального запуска: добавьте --execute",
    ]
    text = "\n".join(lines)
    print(text)
    return {"ok": True, "text": text, "bootstrap_preview": boot}


def run_site_flow_execute(
    config: OrchestratorConfig,
    *,
    name: str,
    stories_dir: Path,
    limit: int,
    execute: bool,
    site_run_id: str | None,
    bat_profile: str,
    gemini_workers: int,
    gemini_registry_path: Path,
    phase_b_allow_scaffold: bool | None,
    phase_b_branch: str | None,
    output_conflict_policy: str = "skip-existing",
    max_runtime_minutes: float = 0.0,
    use_input_snapshot: bool = False,
) -> dict[str, Any]:
    """
    Каркас Запуски/<имя> + тот же subprocess-контур, что Content-Factory-Запуск.bat (site / kokoro-drive).
    """
    from orchestrator.human_launch_site_flow_bat import (
        default_site_run_base_from_launch_name,
        run_site_flow_bat_execute,
    )

    lim = max(0, int(limit or 0))
    effective_name = _resolve_effective_launch_name(name, smoke=False)
    # Recovery resume: if the launch already exists and has recovery_queue_map.json,
    # do NOT rebuild from stories/input and do NOT fall back to global runs/site.
    existing = (human_zapuski_root(config.root_dir) / effective_name).resolve()
    if (existing / D10_TEMP / "recovery_queue_map.json").is_file():
        launch = existing
        blocked = launch_resume_is_blocked(read_json(launch / F_MANIFEST))
        if blocked:
            msg = f"run-site-flow: launch resume blocked ({blocked}) for {launch}"
            print(msg, flush=True)
            return {"ok": False, "message": msg, "launch_path": str(launch)}
        # Recovery: run_site_flow_bat_execute сам строит staging из recovery_queue_map и --launch-dir.
        r1 = {"ok": True, "launch_path": str(launch), "recovery_detected": True}
    elif (existing / F_MANIFEST).is_file() and (existing / F_STATUS).is_file():
        launch = existing
        blocked = launch_resume_is_blocked(read_json(launch / F_MANIFEST))
        if blocked:
            msg = f"run-site-flow: launch resume blocked ({blocked}) for {launch}"
            print(msg, flush=True)
            return {"ok": False, "message": msg, "launch_path": str(launch)}
        r1 = {"ok": True, "launch_path": str(launch), "reused_existing_launch": True}
    else:
        r1 = start_site_launch(
            config,
            name=effective_name,
            stories_dir=stories_dir,
            limit=lim,
            execute=bool(execute),
            output_conflict_policy=output_conflict_policy,
            use_input_snapshot=bool(use_input_snapshot),
        )
        if not r1.get("ok"):
            return r1
        launch = Path(str(r1["launch_path"])).resolve()
    pipeline_sd = _resolve_pipeline_stories_dir(launch, stories_dir)
    base = (site_run_id or "").strip() or default_site_run_base_from_launch_name(launch.name)
    profile = (bat_profile or "classic").strip().lower()
    kokoro = profile == "kokoro-drive"
    pb_branch = (phase_b_branch or ("site" if kokoro else "all")).strip().lower() or "all"
    if phase_b_allow_scaffold is None:
        pb_scaffold = bool(kokoro)
    else:
        pb_scaffold = bool(phase_b_allow_scaffold)

    bundle = run_site_flow_bat_execute(
        config,
        launch=launch,
        stories_dir=pipeline_sd,
        limit=int(lim),
        execute=bool(execute),
        site_run_base=base,
        gemini_workers=int(gemini_workers),
        gemini_registry=gemini_registry_path,
        kokoro_drive_profile=kokoro,
        phase_b_allow_scaffold=pb_scaffold,
        phase_b_branch=pb_branch,
        max_runtime_minutes=float(max_runtime_minutes or 0.0),
    )
    if not execute:
        print("=== run-site-flow (план) ===")
        for k in (
            "source_of_truth",
            "recovery_queue_counters",
            "actual_phase_a_artifacts_dir",
            "global_runs_site_used",
            "site_run_base",
            "kokoro_drive_profile",
            "phase_b_allow_scaffold",
            "phase_b_branch",
            "kokoro_set_mode_cmd",
            "gemini_caps",
            "phase_a_cmd",
            "phase_a_cwd",
            "phase_b_cmd",
            "site_run_cmd",
        ):
            if k in bundle:
                print(f"{k}: {bundle.get(k)}")
        return {**r1, **bundle, "pipeline_stories_dir": str(pipeline_sd), "ok": True}

    summ = _print_runtime_story_summary(config, launch)
    vr = verify_runtime_launch(config, human_name=launch.name)
    rid = sanitize_launch_folder_name(launch.name)
    alive = count_legacy_hint_processes(story_run_id=rid)
    print(f"run-site-flow: verify-runtime ok={vr.get('ok')} active_legacy_hint_processes={alive}")
    post = _run_full_flow_postcheck(config, launch=launch)
    ok = bool(bundle.get("ok")) and bool(post.get("ok"))
    if ok:
        print("[OK] run-site-flow completed", flush=True)
    else:
        print("[FAILED] run-site-flow failed", flush=True)
        fs = bundle.get("failed_step")
        if fs:
            print(f"[FAILED] failed_step={fs}", flush=True)
        if bundle.get("message"):
            print(f"[FAILED] message={bundle.get('message')}", flush=True)
    return {
        **r1,
        **bundle,
        "pipeline_stories_dir": str(pipeline_sd),
        "runtime_story_summary": summ,
        "verify_runtime": vr,
        "postcheck": post,
        "ok": ok,
    }
