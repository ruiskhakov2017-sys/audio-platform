"""
Жизненный цикл папки запуска: status.json, resume-plan, cleanup/archive/delete (dry-run по умолчанию).

Не вызывает phase_a, Gemini, TTS, publish — только файлы в Запуски/ и История_запусков/.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_proc_probe import count_legacy_hint_processes
from orchestrator.human_launch_layout import (
    D05_RASSKAZY,
    D06_OTCHETY,
    D07_LOGI,
    D10_TEMP,
    F_INFO_EN,
    F_MANIFEST,
    F_ORCHESTRATOR_TRACE,
    F_RAW_RESPONSE,
    F_RESULT_JSON,
    F_SITE_INFO_JSON,
    F_STATUS,
    F_STORY_STATUS,
    F_VALIDATION_JSON,
    S03_SITE,
    S03_05_PUBLISH,
    STORY_TMP,
    human_zapuski_root,
    now_iso,
    read_json,
    sanitize_launch_folder_name,
    story_base_paths,
    write_json,
)

F_RECOVERY_QUEUE_MAP = "recovery_queue_map.json"

DIR_ISTORIYA = "История_запусков"
DIR_ZAPUSKI_ARCHIVE = "_Архив"

LAUNCH_STATUSES = frozenset(
    {
        "created",
        "in_progress",
        "paused",
        "failed",
        "failed_preflight",
        "phase_a_timeout",
        "phase_a_failed",
        "partially_completed",
        "completed",
        "archived",
        "cleanup_ready",
        "deleted_manually",
    }
)

ORCHESTRATOR_TERMINAL_OVERRIDE_STATUSES = frozenset(
    {"failed_preflight", "phase_a_timeout", "phase_a_failed"},
)

STAGE_DONE = "done"
STAGE_PENDING = "pending"
STAGE_PROCESSING = "processing"
STAGE_FAILED = "failed"
STAGE_DEFERRED = "deferred"
STAGE_SKIPPED = "skipped"

STORY_ROOT_STATUS = frozenset({"created", "in_progress", "paused", "failed", "completed", "published"})


def istoriya_zapuskov_root(root_dir: Path) -> Path:
    return (root_dir / DIR_ISTORIYA).resolve()


def zapuski_archive_root(root_dir: Path) -> Path:
    return (human_zapuski_root(root_dir) / DIR_ZAPUSKI_ARCHIVE).resolve()


def _dir_size(path: Path) -> tuple[int, int]:
    """Возвращает (байты, количество файлов)."""
    total = 0
    nfiles = 0
    if not path.exists():
        return 0, 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
            nfiles += 1
    return total, nfiles


def _read_validation_ok(path: Path) -> bool | None:
    j = read_json(path)
    if not j:
        return None
    return bool(j.get("ok"))


def infer_primary_selection_status(launch: Path, story_id: str) -> str:
    p = story_base_paths(launch, story_id)
    v_sel = _read_validation_ok(p["otbor"] / F_VALIDATION_JSON)
    has_sel = (p["otbor"] / F_RESULT_JSON).is_file()
    if has_sel and v_sel is True:
        return STAGE_DONE
    if v_sel is False:
        return STAGE_FAILED
    return STAGE_PENDING


def infer_story_site_substatuses(launch: Path, story_id: str) -> dict[str, str]:
    """Эвристика по файлам внутри папки запуска (без legacy)."""
    p = story_base_paths(launch, story_id)
    sc = p["site_cleaned"]
    si = p["site_info"]
    sv = p["site_visual"]
    st = p["site_tts"]
    sp = p["site_publish"]

    def any_file(d: Path) -> bool:
        return d.is_dir() and any(x.is_file() for x in d.iterdir())

    text_cleaning = STAGE_DONE if any_file(sc) else STAGE_PENDING

    v_site = _read_validation_ok(si / F_VALIDATION_JSON)
    has_site_json = (si / F_SITE_INFO_JSON).is_file()
    has_info_en = (si / F_INFO_EN).is_file()
    if has_site_json and has_info_en and v_site is True:
        site_info = STAGE_DONE
    elif v_site is False:
        site_info = STAGE_FAILED
    else:
        site_info = STAGE_PENDING

    visual = STAGE_DONE if any_file(sv) else STAGE_PENDING
    audio = STAGE_DONE if any_file(st) else STAGE_PENDING
    publish = STAGE_DONE if (sp / ".published_ok").is_file() else STAGE_PENDING
    return {
        "text_cleaning": text_cleaning,
        "site_info": site_info,
        "visual": visual,
        "audio": audio,
        "publish": publish,
    }


def infer_next_stage(*, primary_selection: str, site: dict[str, str]) -> str:
    order: list[tuple[str, str]] = [
        (primary_selection, "01_Общее/03_Первичный_отбор_Gemini"),
        (site.get("text_cleaning", STAGE_PENDING), "02_Сайт/01_Очистка_текста"),
        (site.get("site_info", STAGE_PENDING), "02_Сайт/02_Информация_для_сайта_Gemini"),
        (site.get("visual", STAGE_PENDING), "02_Сайт/03_Визуал_для_сайта"),
        (site.get("audio", STAGE_PENDING), "02_Сайт/04_Озвучка_для_сайта"),
        (site.get("publish", STAGE_PENDING), "02_Сайт/05_Публикация_на_сайт"),
    ]
    for st, human in order:
        if st in (STAGE_PENDING, STAGE_PROCESSING, STAGE_FAILED, STAGE_DEFERRED):
            return human
    return "completed"


def infer_last_completed_stage(*, primary_selection: str, site: dict[str, str]) -> str:
    order = [
        ("primary", "01_Общее/03_Первичный_отбор_Gemini", primary_selection),
        ("text_cleaning", "02_Сайт/01_Очистка_текста", site.get("text_cleaning", STAGE_PENDING)),
        ("site_info", "02_Сайт/02_Информация_для_сайта_Gemini", site.get("site_info", STAGE_PENDING)),
        ("visual", "02_Сайт/03_Визуал_для_сайта", site.get("visual", STAGE_PENDING)),
        ("audio", "02_Сайт/04_Озвучка_для_сайта", site.get("audio", STAGE_PENDING)),
        ("publish", "02_Сайт/05_Публикация_на_сайт", site.get("publish", STAGE_PENDING)),
    ]
    last = "00_created"
    for _k, human, st in order:
        if st == STAGE_DONE:
            last = human
        else:
            break
    return last


def build_story_status_payload(
    launch: Path,
    story_id: str,
    *,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    primary = infer_primary_selection_status(launch, story_id)
    site = infer_story_site_substatuses(launch, story_id)
    next_stage = infer_next_stage(primary_selection=primary, site=site)
    last_done = infer_last_completed_stage(primary_selection=primary, site=site)
    overall = "in_progress"
    if next_stage == "completed":
        overall = "completed"
    if any(site.get(k) == STAGE_FAILED for k in site):
        overall = "failed"
    if primary == STAGE_FAILED:
        overall = "failed"

    return {
        "story_id": story_id,
        "status": overall,
        "last_completed_stage": last_done,
        "next_stage": next_stage,
        "can_resume": overall in ("in_progress", "failed", "paused") and next_stage != "completed",
        "primary_selection": primary,
        "site": {
            "text_cleaning": site["text_cleaning"],
            "site_info": site["site_info"],
            "visual": site["visual"],
            "audio": site["audio"],
            "publish": site["publish"],
        },
        "youtube": {
            "top_selection": STAGE_PENDING,
            "safe_version": STAGE_PENDING,
            "frames": STAGE_PENDING,
            "video": STAGE_PENDING,
            "publish": STAGE_PENDING,
        },
        "telegram": {
            "snapshot": STAGE_PENDING,
            "post": STAGE_PENDING,
            "publish": STAGE_PENDING,
        },
        "errors": list(errors or []),
        "updated_at": now_iso(),
        "resume_signals": {
            "selection_done_marker": f"{D05_RASSKAZY}/{story_id}/02_Отбор/{F_RESULT_JSON} + validation ok",
            "site_info_done_marker": f"{D05_RASSKAZY}/{story_id}/03_Сайт/02_Информация_для_сайта/{F_SITE_INFO_JSON} + {F_INFO_EN} + validation ok",
            "not_used": "корневой legacy info.txt не используется как done-marker",
        },
    }


def aggregate_launch_status(launch: Path, manifest: dict[str, Any] | None) -> dict[str, Any]:
    manifest = manifest or read_json(launch / F_MANIFEST) or {}
    story_ids, _scope_src, _recovery_items = launch_story_scope_bundle(launch, manifest)
    total = len(story_ids)
    completed = 0
    pending = 0
    failed = 0
    deferred = 0
    published_site = 0
    current_stage_guess = ""

    for sid in story_ids:
        st = build_story_status_payload(launch, sid)
        site = st.get("site") or {}
        if st.get("status") == "failed":
            failed += 1
        elif st.get("next_stage") == "completed":
            completed += 1
            if site.get("publish") == STAGE_DONE:
                published_site += 1
        else:
            pending += 1
        if not current_stage_guess and st.get("next_stage") not in ("", "completed"):
            current_stage_guess = str(st.get("next_stage"))

    prev = read_json(launch / F_STATUS) or {}
    prev_status = str(prev.get("status", "created"))
    if total == 0:
        agg_status = "created"
    elif failed == total and total > 0:
        agg_status = "failed"
    elif completed == total and total > 0 and failed == 0:
        agg_status = "completed"
    elif completed > 0 and (pending > 0 or failed > 0):
        agg_status = "partially_completed"
    elif failed > 0 and completed == 0:
        agg_status = "failed"
    else:
        agg_status = prev_status if prev_status in LAUNCH_STATUSES else "in_progress"

    can_resume = (
        agg_status in ("created", "in_progress", "paused", "failed", "partially_completed") and (pending + failed > 0)
    ) or agg_status in ("failed_preflight", "phase_a_timeout", "phase_a_failed")
    can_cleanup = agg_status in ("completed", "partially_completed", "cleanup_ready")
    can_delete = bool(
        can_cleanup
        and agg_status == "completed"
        and (launch / D06_OTCHETY / "ФИНАЛЬНЫЙ_ОТЧЁТ.json").is_file()
        and (launch / D06_OTCHETY / "cleanup_manifest.json").is_file()
        and failed == 0
        and pending == 0
    )

    trace_path = launch / D10_TEMP / F_ORCHESTRATOR_TRACE
    trace = read_json(trace_path) if trace_path.is_file() else {}
    term = str(trace.get("terminal_status", "")).strip()
    if term in ORCHESTRATOR_TERMINAL_OVERRIDE_STATUSES:
        agg_status = term
    elif term == "site_flow_site_run_failed":
        # subprocess orchestrator run --pipeline site вернул ошибку (в т.ч. failed stage)
        agg_status = "failed"

    return {
        "launch_name": launch.name,
        "source_legacy_run_id": str(manifest.get("source_run_id", "")),
        "created_at": str(manifest.get("created_at", "")),
        "run_mode": str(manifest.get("run_mode", "site")),
        "status": agg_status,
        "current_stage": current_stage_guess or "01_Общее/03_Первичный_отбор_Gemini",
        "total_stories": total,
        "completed_stories": completed,
        "published_site": published_site,
        "published_youtube": int(prev.get("published_youtube", 0)),
        "published_telegram": int(prev.get("published_telegram", 0)),
        "failed": failed,
        "deferred": deferred,
        "can_resume": can_resume,
        "can_cleanup": can_cleanup,
        "can_delete_launch_folder": can_delete,
        "updated_at": now_iso(),
        "notes": [
            "Удаление папки запуска допустимо только после публикации в постоянные места и явного delete --execute.",
            "Временные файлы только в 10_Временные_файлы/ или 05_Рассказы/<id>/tmp/.",
        ],
    }


def refresh_launch_status_file(launch: Path) -> dict[str, Any]:
    m = read_json(launch / F_MANIFEST)
    payload = aggregate_launch_status(launch, m)
    write_json(launch / F_STATUS, payload)
    return payload


def merge_orchestrator_launch_trace(launch: Path, updates: dict[str, Any]) -> dict[str, Any]:
    """Объединяет orchestrator_launch_trace.json (10_Временные_файлы/)."""
    path = launch / D10_TEMP / F_ORCHESTRATOR_TRACE
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = read_json(path) or {}
    merged = {**prev, **updates, "updated_at": now_iso()}
    write_json(path, merged)
    return merged


def clear_orchestrator_terminal_override(launch: Path) -> None:
    """Убирает terminal_status из trace после успешного завершения (агрегация снова по историям)."""
    path = launch / D10_TEMP / F_ORCHESTRATOR_TRACE
    if not path.is_file():
        return
    j = read_json(path) or {}
    j.pop("terminal_status", None)
    j.pop("terminal_detail", None)
    j["updated_at"] = now_iso()
    write_json(path, j)


def _recovery_story_folder_id(source_filename: str, stable_story_key: str) -> str:
    """Имя папки рассказа в 05_Рассказы/ после recovery_execute (совпадает с recovery execute)."""
    stem = sanitize_launch_folder_name(Path(source_filename).stem)
    stem = stem or "story"
    sk = str(stable_story_key).strip()
    if not sk:
        return stem
    return f"{stem}__{sk[:8]}"


def _recovery_queue_map_path(launch: Path, manifest: dict[str, Any] | None) -> Path | None:
    """
    Путь к recovery_queue_map.json: manifest.recovery_execute.queue_map (если есть),
    иначе 10_Временные_файлы/recovery_queue_map.json при наличии файла.
    """
    manifest = manifest or {}
    candidates: list[Path] = []
    rec = manifest.get("recovery_execute")
    if isinstance(rec, dict):
        qm = rec.get("queue_map")
        if qm:
            candidates.append(Path(str(qm)).expanduser())
    candidates.append((launch / D10_TEMP / F_RECOVERY_QUEUE_MAP).resolve())
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    return None


def launch_story_scope_bundle(
    launch: Path, manifest: dict[str, Any] | None
) -> tuple[list[str], str, list[dict[str, Any]] | None]:
    """
    Список story_id (имён папок в 05_Рассказы/) для агрегации и resume.

    Для recovery_execute: только канонические элементы recovery_queue_map.json
    (ядро recovery), не полный скан 05_Рассказы/ (там могут остаться старые папки).
    """
    manifest = manifest or read_json(launch / F_MANIFEST) or {}
    qm_path = _recovery_queue_map_path(launch, manifest)
    if qm_path is None:
        stories_dir = launch / D05_RASSKAZY
        ids = sorted([p.name for p in stories_dir.iterdir() if p.is_dir()]) if stories_dir.is_dir() else []
        return ids, "filesystem_scan", None
    data = read_json(qm_path) or {}
    items_raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items_raw, list):
        items_raw = []
    items: list[dict[str, Any]] = [x for x in items_raw if isinstance(x, dict)]
    seen: set[str] = set()
    ids: list[str] = []
    for it in items:
        fn = str(it.get("source_filename", "")).strip()
        sk = str(it.get("stable_story_key", "")).strip()
        if not fn or not sk:
            continue
        sid = _recovery_story_folder_id(fn, sk)
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return sorted(ids), "recovery_queue_map", items


def recovery_queue_resume_counters(items: list[dict[str, Any]]) -> dict[str, int]:
    """Счётчики по recovery_queue_map (канонические строки, duplicate_ignored — сумма по полю)."""
    total = len(items)
    duplicate_ignored = 0
    already_done = 0
    pending_to_process = 0
    invalid_or_stub = 0
    tts_done = 0
    publish_log_success = 0
    will_send_to_gemini = 0
    will_send_to_tts = 0
    will_publish = 0
    for it in items:
        duplicate_ignored += int(it.get("duplicate_ignored_count") or 0)
        best = str(it.get("best_status", ""))
        m = it.get("markers") if isinstance(it.get("markers"), dict) else {}
        sel_done = bool(m.get("selection_done"))
        site_done = bool(m.get("site_info_done"))
        td = bool(m.get("tts_done"))
        pd = bool(m.get("publish_done"))
        pls = bool(m.get("publish_log_success"))
        if best == "invalid_or_stub":
            invalid_or_stub += 1
        if best in ("no_result", "invalid_or_stub"):
            pending_to_process += 1
        else:
            already_done += 1
        if td:
            tts_done += 1
        if pls:
            publish_log_success += 1
        if not sel_done:
            will_send_to_gemini += 1
        elif sel_done and site_done and not td:
            will_send_to_tts += 1
        elif td and not pd:
            will_publish += 1
    return {
        "total_from_queue_map": total,
        "already_done": already_done,
        "pending_to_process": pending_to_process,
        "invalid_or_stub": invalid_or_stub,
        "duplicate_ignored": duplicate_ignored,
        "tts_done": tts_done,
        "publish_log_success": publish_log_success,
        "will_send_to_gemini": will_send_to_gemini,
        "will_send_to_tts": will_send_to_tts,
        "will_publish": will_publish,
    }


def recovery_wide_site_pipeline_counters(items: list[dict[str, Any]]) -> dict[str, int]:
    """
    Recovery-wide счётчики для gate до TTS/publish (по recovery_queue_map items).
    """
    total = len(items)
    selection_done = 0
    site_info_done = 0
    missing_selection = 0
    missing_site_info = 0
    tts_done = 0
    publish_done = 0
    ready_for_tts = 0
    ready_for_publish = 0
    skip_site = frozenset(
        {
            "invalid_or_stub",
            "skipped",
            "skipped_for_site",
            "not_for_site",
            "rejected",
        }
    )
    for it in items:
        if not isinstance(it, dict):
            continue
        best = str(it.get("best_status", "")).strip()
        m = it.get("markers") if isinstance(it.get("markers"), dict) else {}
        dup_n = int(it.get("duplicate_ignored_count") or 0)
        sel_done = bool(m.get("selection_done"))
        site_done = bool(m.get("site_info_done"))
        td = bool(m.get("tts_done"))
        pd = bool(m.get("publish_done"))
        if sel_done:
            selection_done += 1
        else:
            if best != "invalid_or_stub":
                missing_selection += 1
        if site_done:
            site_info_done += 1
        elif sel_done and dup_n == 0 and best not in skip_site:
            missing_site_info += 1
        if td:
            tts_done += 1
        if pd:
            publish_done += 1
        if sel_done and site_done and not td:
            ready_for_tts += 1
        if td and not pd:
            ready_for_publish += 1
    return {
        "total_from_queue_map": total,
        "selection_done": selection_done,
        "missing_selection": missing_selection,
        "site_info_done": site_info_done,
        "missing_site_info": missing_site_info,
        "tts_done": tts_done,
        "ready_for_tts": ready_for_tts,
        "publish_done": publish_done,
        "ready_for_publish": ready_for_publish,
    }


def evaluate_recovery_site_pipeline_gate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Возвращает block=True если нельзя запускать полный site pipeline (TTS/publish).
    """
    c = recovery_wide_site_pipeline_counters(items)
    if c["missing_selection"] > 0:
        return {
            "block": True,
            "reason": "missing_selection",
            "missing_selection": c["missing_selection"],
            "missing_site_info": c["missing_site_info"],
            "next_step": "continue_selection",
            "counters": c,
        }
    if c["missing_site_info"] > 0:
        return {
            "block": True,
            "reason": "missing_site_info",
            "missing_selection": c["missing_selection"],
            "missing_site_info": c["missing_site_info"],
            "next_step": "continue_site_info",
            "counters": c,
        }
    return {"block": False, "reason": "", "missing_selection": 0, "missing_site_info": 0, "next_step": "site_tts", "counters": c}


def resume_plan(config: OrchestratorConfig, *, human_name: str) -> dict[str, Any]:
    launch = (human_zapuski_root(config.root_dir) / human_name.strip()).resolve()
    if not launch.is_dir():
        msg = f"launch not found: {launch}"
        print(msg)
        return {"ok": False, "message": msg}
    manifest = read_json(launch / F_MANIFEST) or {}
    story_ids, story_scope, recovery_items = launch_story_scope_bundle(launch, manifest)
    ls = refresh_launch_status_file(launch)
    recovery_counters: dict[str, int] | None = None
    if recovery_items is not None:
        recovery_counters = recovery_queue_resume_counters(recovery_items)

    rows: list[dict[str, Any]] = []
    need_continue = 0
    stuck = 0
    failed_n = 0
    retryable = 0

    for sid in story_ids:
        st_path = launch / D05_RASSKAZY / sid / F_STORY_STATUS
        st = read_json(st_path)
        if not isinstance(st, dict) or "next_stage" not in st:
            st = build_story_status_payload(launch, sid)
        site = st.get("site") or {}
        ns = st.get("next_stage", "")
        stat = st.get("status", "")
        if stat == "failed":
            failed_n += 1
        if st.get("can_resume"):
            need_continue += 1
        if site.get("site_info") in (STAGE_PROCESSING, STAGE_DEFERRED):
            stuck += 1
        if site.get("site_info") in (STAGE_FAILED, STAGE_DEFERRED):
            retryable += 1
        rows.append(
            {
                "story_id": sid,
                "status": stat,
                "next_stage": ns,
                "last_completed": st.get("last_completed_stage"),
                "site_info": site.get("site_info"),
            }
        )

    lines = [
        "=== resume-plan (read-only) ===",
        f"launch: {launch}",
    ]
    if recovery_counters is not None:
        rc = recovery_counters
        lines.extend(
            [
                "source_of_truth=recovery_queue_map",
                f"total_from_queue_map={rc['total_from_queue_map']}",
                f"already_done={rc['already_done']}",
                f"pending_to_process={rc['pending_to_process']}",
                f"will_send_to_gemini={rc['will_send_to_gemini']}",
                f"invalid_or_stub={rc.get('invalid_or_stub', 0)}",
                "",
            ]
        )
    lines.extend(
        [
        f"story_scope: {story_scope}",
        f"stories_in_scope: {len(story_ids)}",
        f"launch_status: {ls.get('status')}",
        f"current_stage (aggregate): {ls.get('current_stage')}",
        f"total_stories: {ls.get('total_stories')}",
        f"completed_stories: {ls.get('completed_stories')}",
        f"pending (aggregate): {ls.get('total_stories', 0) - ls.get('completed_stories', 0)}",
        f"failed: {ls.get('failed')}",
        f"deferred: {ls.get('deferred')}",
        f"can_resume (launch): {ls.get('can_resume')}",
        "",
        ]
    )
    if recovery_counters is not None:
        rc = recovery_counters
        mrec = manifest.get("recovery_execute") if isinstance(manifest.get("recovery_execute"), dict) else {}
        mc = mrec.get("counts") if isinstance(mrec.get("counts"), dict) else {}
        lines.extend(
            [
                "recovery_queue_map (source of truth for this launch):",
                f"  total_from_queue_map: {rc['total_from_queue_map']}",
                f"  already_done (best_status not no_result/invalid_or_stub): {rc['already_done']}",
                f"  pending_to_process (no_result + invalid_or_stub): {rc['pending_to_process']}",
                f"  invalid_or_stub (best_status==invalid_or_stub): {rc.get('invalid_or_stub', 0)}",
                f"  duplicate_ignored (sum duplicate_ignored_count): {rc['duplicate_ignored']}",
                f"  tts_done (markers.tts_done): {rc['tts_done']}",
                f"  publish_log_success (markers): {rc['publish_log_success']}",
                f"  will_send_to_gemini (not markers.selection_done): {rc['will_send_to_gemini']}",
                f"  will_send_to_tts (selection+site_info done, not tts): {rc['will_send_to_tts']}",
                f"  will_publish (tts_done, not publish_done): {rc['will_publish']}",
                "",
                "manifest.recovery_execute.counts (written at execute, cross-check):",
                f"  registered_as_already_done: {mc.get('registered_as_already_done', '')}",
                f"  pending: {mc.get('pending', '')}",
                f"  no_result: {mc.get('no_result', '')}",
                f"  duplicate_ignored: {mc.get('duplicate_ignored', '')}",
                f"  mp3_done: {mc.get('mp3_done', '')}",
                f"  publish_log_success: {mc.get('publish_log_success', '')}",
                "",
                "Guarantees (recovery queue row = один канонический рассказ; дубликаты только в duplicate_ignored):",
                "  already_done с selection_done=true не попадут в will_send_to_gemini.",
                "  tts_done=true не попадут в will_send_to_tts.",
                "  duplicate_ignored — отдельные legacy-папки, в items очереди не строки и в scope не входят.",
                "",
            ]
        )
    lines.extend(
        [
            "Signals (not info.txt):",
            f"  - {launch / F_STATUS}",
            f"  - {launch / D05_RASSKAZY}/*/status.json",
            f"  - */02_Отбор/{F_RESULT_JSON} + {F_VALIDATION_JSON}",
            f"  - */03_Сайт/02_Информация_для_сайта/{F_SITE_INFO_JSON} + {F_INFO_EN} + {F_VALIDATION_JSON}",
            "",
            f"stories_to_continue (can_resume): {need_continue}",
            f"stuck_processing_or_deferred_site_info: {stuck}",
            f"retryable_failed_or_deferred: {retryable}",
            "",
            "On future resume execute:",
            "  1) Read launch status.json",
            "  2) For each story with can_resume, start at next_stage only",
            "  3) Skip stages with site.* == done",
            "  4) Do not use root info.txt as done-marker",
            "",
            "Sample (first 15 stories):",
        ]
    )
    for r in rows[:15]:
        lines.append(f"  {r['story_id']}: next={r['next_stage']} status={r['status']} site_info={r['site_info']}")
    if len(rows) > 15:
        lines.append(f"  ... +{len(rows) - 15} stories")

    text = "\n".join(lines)
    print(text)
    out: dict[str, Any] = {
        "ok": True,
        "launch_status": ls,
        "rows": rows,
        "text": text,
        "story_scope": story_scope,
        "stories_in_scope": len(story_ids),
    }
    if recovery_counters is not None:
        out["recovery_queue_counters"] = recovery_counters
    return out


def print_resume_contract() -> None:
    print(
        "\n".join(
            [
                "=== launch resume (dry-run / contract) ===",
                "Сейчас без --execute: ничего не копируется.",
                "С `launch resume --name X --execute`:",
                "  1) mirror_legacy_pipeline_to_human — копирование selection/site/cleaned из legacy _pipeline в Запуски/",
                "  2) пересборка status.json каждого рассказа и запуска (по файлам, не по root info.txt)",
                "  3) Gemini / phase_a / TTS / publish не вызываются",
                "Полный запуск этапов из next_stage — отдельное подключение к phase_a/site runner.",
            ]
        )
    )


def cleanup_plan_detailed(config: OrchestratorConfig, *, human_name: str) -> dict[str, Any]:
    launch = (human_zapuski_root(config.root_dir) / human_name.strip()).resolve()
    if not launch.is_dir():
        return {"ok": False, "message": f"launch not found: {launch}"}
    ls = read_json(launch / F_STATUS) or aggregate_launch_status(launch, read_json(launch / F_MANIFEST))
    size_b, nfiles = _dir_size(launch)
    tmp_b, tmp_n = _dir_size(launch / D10_TEMP)
    logs_b, logs_n = _dir_size(launch / D07_LOGI)

    can_del = bool(ls.get("can_delete_launch_folder"))
    reasons: list[str] = []
    if not (launch / D06_OTCHETY / "ФИНАЛЬНЫЙ_ОТЧЁТ.json").is_file():
        reasons.append("нет 06_Отчёты/ФИНАЛЬНЫЙ_ОТЧЁТ.json")
    if not (launch / D06_OTCHETY / "cleanup_manifest.json").is_file():
        reasons.append("нет 06_Отчёты/cleanup_manifest.json")
    if ls.get("failed", 0) > 0:
        reasons.append("есть failed рассказы")
    if ls.get("status") not in ("completed", "cleanup_ready"):
        reasons.append(f"статус запуска не completed/cleanup_ready: {ls.get('status')}")

    lines = [
        "=== cleanup-plan (detailed, no deletes) ===",
        f"launch: {launch}",
        f"total_size_bytes: {size_b} files: {nfiles}",
        f"10_Временные_файлы: bytes={tmp_b} files={tmp_n}",
        f"07_Логи: bytes={logs_b} files={logs_n}",
        "",
        f"can_delete_launch_folder: {can_del}",
        "why not:" if reasons else "why: all preconditions met (synthetic check)",
    ]
    lines.extend(f"  - {r}" for r in reasons)
    lines.extend(
        [
            "",
            "Final artifacts (human tree):",
            f"  - {launch / F_MANIFEST}",
            f"  - {launch / F_STATUS}",
            f"  - {launch / D05_RASSKAZY}/*/02_Отбор/{F_RESULT_JSON}",
            f"  - {launch / D05_RASSKAZY}/*/03_Сайт/02_Информация_для_сайта/{F_INFO_EN}",
            "",
            "Temporary (must stay inside launch):",
            f"  - {launch / D10_TEMP}",
            f"  - {launch / D05_RASSKAZY}/*/tmp/",
            "",
            "Published outside launch (do not rely on launch folder after delete):",
            "  - output/site/<canonical>/ (after publish)",
            "  - CDN / object storage URLs if used",
            "",
            "If delete --execute: summary written to История_запусков/ before rmtree.",
        ]
    )
    text = "\n".join(lines)
    print(text)
    return {"ok": True, "text": text, "can_delete_launch_folder": can_del, "size_bytes": size_b, "file_count": nfiles}


def archive_launch(
    config: OrchestratorConfig,
    *,
    human_name: str,
    execute: bool = False,
) -> dict[str, Any]:
    root = config.root_dir.resolve()
    launch = (human_zapuski_root(root) / human_name.strip()).resolve()
    if not launch.is_dir():
        return {"ok": False, "message": f"launch not found: {launch}"}
    ar = zapuski_archive_root(root)
    ts = now_iso().replace(":", "-").replace("+", "_")
    dest = ar / f"{launch.name}_{ts}"
    plan = {"action": "move", "from": str(launch), "to": str(dest), "execute": execute}
    print("=== archive (dry-run unless --execute) ===")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if execute:
        ar.mkdir(parents=True, exist_ok=True)
        shutil.move(str(launch), str(dest))
        ls = read_json(dest / F_STATUS) or {}
        ls["status"] = "archived"
        ls["archived_path"] = str(dest)
        ls["updated_at"] = now_iso()
        write_json(dest / F_STATUS, ls)
        return {"ok": True, "archived_to": str(dest)}
    return {"ok": True, "dry_run": True, "planned": plan}


def delete_launch(
    config: OrchestratorConfig,
    *,
    human_name: str,
    execute: bool = False,
) -> dict[str, Any]:
    root = config.root_dir.resolve()
    launch = (human_zapuski_root(root) / human_name.strip()).resolve()
    if not launch.is_dir():
        return {"ok": False, "message": f"launch not found: {launch}"}
    size_b, nfiles = _dir_size(launch)
    hist = istoriya_zapuskov_root(root)
    summary_json = hist / f"{launch.name}_summary.json"
    summary_csv = hist / f"{launch.name}_summary.csv"
    deleted_csv = hist / f"{launch.name}_deleted_manifest.csv"

    ls = read_json(launch / F_STATUS) or {}
    manifest = read_json(launch / F_MANIFEST) or {}
    story_ids = sorted([p.name for p in (launch / D05_RASSKAZY).iterdir() if p.is_dir()]) if (launch / D05_RASSKAZY).is_dir() else []

    summary_payload: dict[str, Any] = {
        "launch_name": launch.name,
        "created_at": manifest.get("created_at"),
        "deleted_at_utc": now_iso(),
        "total_stories": len(story_ids),
        "published_site": ls.get("published_site", 0),
        "published_youtube": ls.get("published_youtube", 0),
        "published_telegram": ls.get("published_telegram", 0),
        "published_story_ids": [],
        "publication_ids": {},
        "errors": [],
        "deleted_bytes": size_b,
        "deleted_file_count": nfiles,
        "note": "Минимальный отчёт перед удалением; расширять при подключении publish API.",
    }

    print("=== delete (dry-run unless --execute) ===")
    print(f"would free ~{size_b} bytes in {nfiles} files")
    print(f"history dir: {hist}")
    print(f"would write: {summary_json.name}, {summary_csv.name}, {deleted_csv.name}")

    if not execute:
        return {"ok": True, "dry_run": True, "summary": summary_payload}

    hist.mkdir(parents=True, exist_ok=True)
    write_json(summary_json, summary_payload)
    with summary_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["launch_name", "total_stories", "published_site", "deleted_at"])
        w.writerow([launch.name, len(story_ids), ls.get("published_site", 0), summary_payload["deleted_at_utc"]])
    with deleted_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "kind"])
        for p in launch.rglob("*"):
            if p.is_file():
                w.writerow([str(p.relative_to(launch)), "file"])
    shutil.rmtree(launch)
    return {"ok": True, "deleted": True, "history": str(hist)}


def ensure_story_tmp(launch: Path, story_id: str) -> Path:
    t = launch / D05_RASSKAZY / story_id / STORY_TMP
    t.mkdir(parents=True, exist_ok=True)
    return t


def generate_final_report_launch(
    config: OrchestratorConfig,
    *,
    human_name: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Финальный отчёт + cleanup_manifest в 06_Отчёты/ (без удалений)."""
    launch = (human_zapuski_root(config.root_dir) / human_name.strip()).resolve()
    if not launch.is_dir():
        msg = f"launch not found: {launch}"
        print(msg)
        return {"ok": False, "message": msg}
    manifest = read_json(launch / F_MANIFEST) or {}
    ls = refresh_launch_status_file(launch)
    stories_root = launch / D05_RASSKAZY
    story_ids = sorted([p.name for p in stories_root.iterdir() if p.is_dir()]) if stories_root.is_dir() else []
    published_ids: list[str] = []
    publish_result_paths: list[str] = []
    errors: list[str] = []
    for sid in story_ids:
        p = story_base_paths(launch, sid)
        okf = p["site_publish"] / ".published_ok"
        rj = p["site_publish"] / F_RESULT_JSON
        if okf.is_file():
            published_ids.append(sid)
            if rj.is_file():
                publish_result_paths.append(str(rj))

    final_json = launch / D06_OTCHETY / "ФИНАЛЬНЫЙ_ОТЧЁТ.json"
    final_csv = launch / D06_OTCHETY / "ФИНАЛЬНЫЙ_ОТЧЁТ.csv"
    cleanup_path = launch / D06_OTCHETY / "cleanup_manifest.json"

    payload: dict[str, Any] = {
        "launch_name": launch.name,
        "generated_at": now_iso(),
        "total_stories": len(story_ids),
        "published_site_count": len(published_ids),
        "published_story_ids": published_ids,
        "publish_result_json_paths": publish_result_paths[:200],
        "launch_status": ls.get("status"),
        "failed_stories": int(ls.get("failed", 0)),
        "deferred_stories": int(ls.get("deferred", 0)),
        "errors_sample": errors,
        "cleanup_delete_allowed_preconditions": {
            "final_report_exists": False,
            "cleanup_manifest_exists": False,
            "launch_status_completed": ls.get("status") == "completed",
            "can_delete_launch_folder_flag": bool(ls.get("can_delete_launch_folder")),
        },
    }

    cleanup_manifest: dict[str, Any] = {
        "launch_name": launch.name,
        "generated_at": now_iso(),
        "can_delete_launch_folder": bool(ls.get("can_delete_launch_folder")),
        "reasons_if_not": [],
        "totals": {
            "stories": len(story_ids),
            "published_site": len(published_ids),
            "failed": int(ls.get("failed", 0)),
        },
        "final_artifacts": [
            str(launch / F_MANIFEST),
            str(launch / F_STATUS),
            str(final_json),
        ],
        "legacy_note": "Технические пути legacy — в 10_Временные_файлы/legacy_technical_paths.json после migrate.",
    }
    if not final_json.is_file():
        cleanup_manifest["reasons_if_not"].append("нет ФИНАЛЬНЫЙ_ОТЧЁТ.json")
    if not cleanup_path.is_file():
        cleanup_manifest["reasons_if_not"].append("нет cleanup_manifest.json (создаётся этой командой с --execute)")

    lines = [
        "=== final-report ===",
        f"launch: {launch}",
        f"stories: {len(story_ids)}",
        f"published_site (.published_ok): {len(published_ids)}",
        f"launch_status: {ls.get('status')}",
        f"would write: {final_json.name}, {final_csv.name}, {cleanup_path.name}",
    ]
    print("\n".join(lines))

    if not execute:
        payload["cleanup_delete_allowed_preconditions"]["note"] = "Повторите с --execute для записи JSON/CSV/manifest."
        return {"ok": True, "dry_run": True, "payload": payload, "cleanup_manifest": cleanup_manifest}

    (launch / D06_OTCHETY).mkdir(parents=True, exist_ok=True)
    payload["cleanup_delete_allowed_preconditions"]["final_report_exists"] = True
    payload["cleanup_delete_allowed_preconditions"]["cleanup_manifest_exists"] = True
    write_json(final_json, payload)
    with final_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["story_id", "published_site"])
        for sid in story_ids:
            w.writerow([sid, "yes" if sid in published_ids else "no"])
    refresh_launch_status_file(launch)
    ls3 = read_json(launch / F_STATUS) or {}
    cleanup_manifest["can_delete_launch_folder"] = bool(ls3.get("can_delete_launch_folder"))
    cleanup_manifest["reasons_if_not"] = [x for x in cleanup_manifest.get("reasons_if_not", []) if x]
    if not ls3.get("can_delete_launch_folder"):
        if ls3.get("status") != "completed":
            cleanup_manifest["reasons_if_not"].append(f"статус запуска: {ls3.get('status')}")
    write_json(cleanup_path, cleanup_manifest)
    return {"ok": True, "written": [str(final_json), str(final_csv), str(cleanup_path)]}


def resume_launch_execute(config: OrchestratorConfig, *, human_name: str) -> dict[str, Any]:
    """
    Ральный resume без Gemini: копирование новых legacy-артефактов в дерево Запуски/ + пересборка status.json.
    """
    from orchestrator.human_launch_legacy_sync import mirror_legacy_pipeline_to_human

    launch = (human_zapuski_root(config.root_dir) / human_name.strip()).resolve()
    if not launch.is_dir():
        msg = f"launch not found: {launch}"
        print(msg)
        return {"ok": False, "message": msg}
    mir = mirror_legacy_pipeline_to_human(config, launch, execute=True)
    if not mir.get("ok"):
        print(f"[warn] mirror: {mir.get('reason', mir.get('message', ''))}")
    manifest = read_json(launch / F_MANIFEST) or {}
    story_ids, story_scope, _ = launch_story_scope_bundle(launch, manifest)
    stories_root = launch / D05_RASSKAZY
    supported_next = {
        "01_Общее/03_Первичный_отбор_Gemini",
        "02_Сайт/01_Очистка_текста",
        "02_Сайт/02_Информация_для_сайта_Gemini",
        "02_Сайт/03_Визуал_для_сайта",
        "02_Сайт/04_Озвучка_для_сайта",
        "02_Сайт/05_Публикация_на_сайт",
        "completed",
    }
    unsupported: dict[str, str] = {}
    for sid in story_ids:
        payload = build_story_status_payload(launch, sid)
        ns = str(payload.get("next_stage", ""))
        if ns not in supported_next:
            payload.setdefault("errors", [])
            payload["errors"].append(f"unsupported_next_stage:{ns}")
            unsupported[sid] = ns
        write_json(stories_root / sid / F_STORY_STATUS, payload)
    refresh_launch_status_file(launch)
    print("=== resume --execute (sync-only) ===")
    print(f"story_scope: {story_scope}")
    print(f"mirror copied files (approx): {mir.get('copied', 0)}")
    print(f"stories refreshed: {len(story_ids)}")
    print(f"validations_written: {mir.get('validations_written', 0)} info_en_written: {mir.get('info_en_written', 0)}")
    print(f"tts_synced: {mir.get('tts_synced', 0)} publish_synced: {mir.get('publish_synced', 0)}")
    print(f"unsupported_next_stage_count: {len(unsupported)}")
    for sid, ns in list(unsupported.items())[:20]:
        print(f"  - {sid}: {ns}")
    print("Gemini / phase_a / TTS / publish: not invoked.")
    return {"ok": True, "mirror": mir, "stories": len(story_ids), "unsupported_next_stage": unsupported}


def verify_runtime_launch(config: OrchestratorConfig, *, human_name: str) -> dict[str, Any]:
    launch = (human_zapuski_root(config.root_dir) / human_name.strip()).resolve()
    if not launch.is_dir():
        msg = f"launch not found: {launch}"
        print(msg)
        return {"ok": False, "message": msg}

    stories_dir = launch / D05_RASSKAZY
    story_ids = sorted([p.name for p in stories_dir.iterdir() if p.is_dir()]) if stories_dir.is_dir() else []
    stats = {
        "story_folders": len(story_ids),
        "story_status_json": 0,
        "source_txt": 0,
        "selection_raw": 0,
        "selection_result": 0,
        "cleaned_story": 0,
        "site_info_json": 0,
        "info_en_txt": 0,
        "audio_mp3": 0,
        "publish_result_json": 0,
        "published_ok_marker": 0,
        "pending": 0,
        "failed": 0,
        "invalid": 0,
        "legacy_selection_info_found": 0,
        "legacy_site_info_found": 0,
        "synced_selection_raw": 0,
        "synced_selection_result": 0,
        "sync_errors": 0,
    }
    supported_resume = [
        "01_Общее/03_Первичный_отбор_Gemini",
        "02_Сайт/01_Очистка_текста",
        "02_Сайт/02_Информация_для_сайта_Gemini",
        "02_Сайт/03_Визуал_для_сайта",
        "02_Сайт/04_Озвучка_для_сайта",
        "02_Сайт/05_Публикация_на_сайт",
    ]
    unsupported: dict[str, str] = {}

    for sid in story_ids:
        p = story_base_paths(launch, sid)
        if p["story_status"].is_file():
            stats["story_status_json"] += 1
        if (p["obshchee"] / "source.txt").is_file():
            stats["source_txt"] += 1
        if (p["otbor"] / F_RAW_RESPONSE).is_file():
            stats["selection_raw"] += 1
        if (p["otbor"] / F_RESULT_JSON).is_file():
            stats["selection_result"] += 1
        if (p["site_cleaned"] / "cleaned_story.txt").is_file():
            stats["cleaned_story"] += 1
        if (p["site_info"] / F_SITE_INFO_JSON).is_file():
            stats["site_info_json"] += 1
        if (p["site_info"] / F_INFO_EN).is_file():
            stats["info_en_txt"] += 1
        if (p["site_tts"] / "audio.mp3").is_file():
            stats["audio_mp3"] += 1
        if (p["site_publish"] / F_RESULT_JSON).is_file():
            stats["publish_result_json"] += 1
        if (p["site_publish"] / ".published_ok").is_file():
            stats["published_ok_marker"] += 1

        st = read_json(p["story_status"]) or build_story_status_payload(launch, sid)
        status_v = str(st.get("status", ""))
        next_stage = str(st.get("next_stage", ""))
        if status_v == "failed":
            stats["failed"] += 1
        elif next_stage == "completed":
            pass
        else:
            stats["pending"] += 1
        if str(st.get("primary_selection")) == STAGE_FAILED or str((st.get("site") or {}).get("site_info")) == STAGE_FAILED:
            stats["invalid"] += 1
        if next_stage and next_stage not in supported_resume and next_stage != "completed":
            unsupported[sid] = next_stage

    ls = refresh_launch_status_file(launch)
    sync_rep = read_json(launch / D10_TEMP / "last_sync_report.json") or {}
    stats["legacy_selection_info_found"] = int(sync_rep.get("legacy_selection_info_found") or 0)
    stats["legacy_site_info_found"] = int(sync_rep.get("legacy_site_info_found") or 0)
    stats["synced_selection_raw"] = int(sync_rep.get("synced_selection_raw") or 0)
    stats["synced_selection_result"] = int(sync_rep.get("synced_selection_result") or 0)
    stats["sync_errors"] = len(sync_rep.get("sync_errors") or [])
    rid_hint = str(ls.get("source_legacy_run_id", "") or "").strip() or human_name.strip()
    active_legacy = count_legacy_hint_processes(story_run_id=rid_hint)
    print("=== verify-runtime ===")
    print(f"launch: {launch}")
    print(f"manifest.json exists: {(launch / F_MANIFEST).is_file()}")
    print(f"status.json exists: {(launch / F_STATUS).is_file()}")
    for k, v in stats.items():
        print(f"{k}: {v}")
    print(f"launch_status: {ls.get('status')} can_resume={ls.get('can_resume')}")
    print(f"active_legacy_hint_processes: {active_legacy}")
    print("ready_stage_counts:")
    print(f"  selection.done: {sum(1 for sid in story_ids if infer_primary_selection_status(launch, sid) == STAGE_DONE)}")
    print(
        f"  text_cleaning.done: {sum(1 for sid in story_ids if infer_story_site_substatuses(launch, sid).get('text_cleaning') == STAGE_DONE)}"
    )
    print(f"  site_info.done: {sum(1 for sid in story_ids if infer_story_site_substatuses(launch, sid).get('site_info') == STAGE_DONE)}")
    print(f"  audio.done: {sum(1 for sid in story_ids if infer_story_site_substatuses(launch, sid).get('audio') == STAGE_DONE)}")
    print(f"  publish.done: {sum(1 for sid in story_ids if infer_story_site_substatuses(launch, sid).get('publish') == STAGE_DONE)}")
    if story_ids:
        s0 = story_ids[0]
        p0 = story_base_paths(launch, s0)
        print("sample_paths_first_story:")
        print(f"  source: {p0['obshchee'] / 'source.txt'}")
        print(f"  selection.result: {p0['otbor'] / F_RESULT_JSON}")
        print(f"  cleaned_story: {p0['site_cleaned'] / 'cleaned_story.txt'}")
        print(f"  site_info: {p0['site_info'] / F_SITE_INFO_JSON}")
        print(f"  info.en: {p0['site_info'] / F_INFO_EN}")
        print(f"  audio: {p0['site_tts'] / 'audio.mp3'}")
        print(f"  publish.result: {p0['site_publish'] / F_RESULT_JSON}")
        print(f"  publish.ok: {p0['site_publish'] / '.published_ok'}")
    print("resume_supported_stages:")
    for x in supported_resume:
        print(f"  - {x}")
    print(f"resume_unsupported_next_stage_count: {len(unsupported)}")
    for sid, ns in list(unsupported.items())[:20]:
        print(f"  - {sid}: {ns}")
    return {
        "ok": True,
        "launch": str(launch),
        "stats": stats,
        "unsupported_next_stage": unsupported,
        "active_legacy_hint_processes": active_legacy,
    }
