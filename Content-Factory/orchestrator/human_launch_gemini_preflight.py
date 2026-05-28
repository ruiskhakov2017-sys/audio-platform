"""
Preflight перед реальным запуском Gemini (phase-a): профили, registry, конфликт процессов, intake.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_proc_probe import (
    count_gemini_auto_processes,
    find_phase_a_conflicts,
    list_python_processes_windows,
)
from orchestrator.phase_a import _load_gemini_registry, _read_profile_email, _scan_txt


def _gemini_module_dir(config: OrchestratorConfig) -> Path:
    gemini_rel = str(config.legacy_entrypoints.get("gemini_auto", "legacy/Gemini_Auto/gemini_auto.py")).strip()
    return (config.root_dir / gemini_rel).resolve().parent


def run_gemini_preflight(
    config: OrchestratorConfig,
    *,
    stories_dir_for_intake: Path,
    queue_file_count: int,
    story_run_id: str,
    gemini_registry_path: Path,
    extensions: list[str],
    gemini_workers: int = 5,
    gemini_profiles_total: int = 5,
    gemini_target_active_workers: int = 3,
    gemini_stage_key: str = "general_selection",
    strict_gemini_auto_running: bool = False,
) -> dict[str, Any]:
    """
    Возвращает ok=True только если можно безопасно стартовать phase-a с реальным UI.
    """
    reasons: list[str] = []
    notes: list[str] = []
    rid = (story_run_id or "").strip()

    if str(os.getenv("CF_GEMINI_DRY_MOCK", "")).strip() == "1":
        notes.append("CF_GEMINI_DRY_MOCK=1: preflight пропускает проверку логина Chrome (mock mode).")
        return {
            "ok": True,
            "reasons": [],
            "notes": notes,
            "profiles_checked": 0,
            "profiles_ready": 0,
            "intake_txt_count": queue_file_count,
            "target_active_workers": gemini_target_active_workers,
            "registry_bots": 0,
        }

    sd = stories_dir_for_intake.resolve()
    if not sd.is_dir():
        reasons.append(f"stories input dir missing: {sd}")
        return {"ok": False, "reasons": reasons, "notes": notes}

    intake_files = _scan_txt(sd, extensions)
    if len(intake_files) != queue_file_count:
        notes.append(f"intake scan count={len(intake_files)} expected_queue_hint={queue_file_count}")

    reg = (config.root_dir / gemini_registry_path).resolve() if not gemini_registry_path.is_absolute() else gemini_registry_path
    if not reg.is_file():
        reasons.append(f"gemini registry not found: {reg}")
        return {"ok": False, "reasons": reasons, "notes": notes, "intake_txt_count": len(intake_files)}

    bots = _load_gemini_registry(reg)
    if not bots:
        reasons.append(f"gemini registry empty or unreadable: {reg}")
        return {"ok": False, "reasons": reasons, "notes": notes, "intake_txt_count": len(intake_files)}

    gem_mod = _gemini_module_dir(config)
    gemini_script = gem_mod / "gemini_auto.py"
    if not gemini_script.is_file():
        reasons.append(f"legacy gemini_auto.py not found: {gemini_script}")
        return {"ok": False, "reasons": reasons, "notes": notes, "intake_txt_count": len(intake_files)}

    profiles_total = max(1, min(5, int(gemini_profiles_total)))
    target = max(1, min(int(gemini_target_active_workers), profiles_total))

    conflicts = find_phase_a_conflicts(story_run_id=rid)
    if conflicts:
        reasons.append(
            "already running: orchestrator phase-a with same --story-id "
            + ", ".join(str(c.get("pid")) for c in conflicts[:5])
        )

    n_auto = count_gemini_auto_processes()
    if strict_gemini_auto_running and n_auto > 0:
        reasons.append(f"strict mode: {n_auto} gemini_auto.py process(es) already running")
    elif n_auto > 0:
        notes.append(f"info: {n_auto} gemini_auto.py process(es) running (not same-run enforced)")

    profiles_ready = 0
    profile_reports: list[dict[str, Any]] = []
    for idx in range(profiles_total):
        user_data_dir = gem_mod / f"user_data_{idx}"
        email = _read_profile_email(user_data_dir)
        url_ok = False
        if email:
            bot = next((b for b in bots if str(b.get("email", "")).strip().lower() == email.lower()), {}) or {}
            url_ok = bool(str(bot.get(gemini_stage_key, "")).strip())
        ok_p = bool(email and url_ok)
        if ok_p:
            profiles_ready += 1
        profile_reports.append(
            {
                "profile_index": idx,
                "user_data_dir": str(user_data_dir),
                "email_found": bool(email),
                "registry_url_for_stage": url_ok,
            }
        )

    if profiles_ready < 1:
        reasons.append(
            "no Chrome profile ready for Gemini (нужен логин в user_data_* и email в registry + URL этапа); "
            "см. profile_reports в отчёте preflight"
        )
    elif profiles_ready < target:
        notes.append(
            f"warn: только {profiles_ready} готовых профилей при target_active={target} — пул может деградировать"
        )

    if len(intake_files) < 1:
        reasons.append("intake: zero .txt files in stories dir for phase-a")

    ok = len(reasons) == 0
    return {
        "ok": ok,
        "reasons": reasons,
        "notes": notes,
        "profiles_checked": profiles_total,
        "profiles_ready": profiles_ready,
        "intake_txt_count": len(intake_files),
        "target_active_workers": target,
        "workers_cap": max(1, min(5, int(gemini_workers))),
        "registry_bots": len(bots),
        "registry_path": str(reg),
        "gemini_script": str(gemini_script),
        "profile_reports": profile_reports,
        "python_processes_sample": list_python_processes_windows()[:8],
    }


def run_gemini_preflight_for_human_launch(
    config: OrchestratorConfig,
    *,
    human_name: str,
    stories_dir: Path | None,
    limit: int,
    gemini_registry_path: Path,
) -> dict[str, Any]:
    """CLI `launch gemini-preflight`: выбор stories-dir (staging test_input приоритет)."""
    from orchestrator.human_launch_layout import F_MANIFEST, human_zapuski_root, read_json, sanitize_launch_folder_name
    from orchestrator.human_launch_staging import list_sorted_story_txt, staging_test_input_dir

    launch = (human_zapuski_root(config.root_dir) / str(human_name).strip()).resolve()
    if not launch.is_dir():
        return {"ok": False, "message": f"launch not found: {launch}"}
    staging = staging_test_input_dir(launch)
    if staging.is_dir() and list_sorted_story_txt(staging):
        sd = staging
        queue_hint = len(list_sorted_story_txt(staging))
    elif stories_dir is not None and stories_dir.is_dir():
        sd = stories_dir.resolve()
        tx = list_sorted_story_txt(sd)
        if limit > 0:
            queue_hint = min(int(limit), len(tx))
        else:
            queue_hint = len(tx)
    else:
        man = read_json(launch / F_MANIFEST) or {}
        ip = str(man.get("input_stories_dir", "") or "").strip()
        if not ip:
            return {"ok": False, "message": "нет --stories-dir и manifest.input_stories_dir пуст"}
        sd = Path(ip).resolve()
        sids = man.get("story_ids") or []
        queue_hint = len(sids) if isinstance(sids, list) else 0
    rid = sanitize_launch_folder_name(launch.name)
    ext = list(config.pre_filter_extensions)
    return run_gemini_preflight(
        config,
        stories_dir_for_intake=sd,
        queue_file_count=int(queue_hint),
        story_run_id=rid,
        gemini_registry_path=gemini_registry_path,
        extensions=ext,
    )
