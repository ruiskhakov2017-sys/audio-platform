"""
Синхронизация legacy артефактов (пути из 10_Временные_файлы/legacy_technical_paths.json)
из runs/.../stories/.../_pipeline и output/site в дерево Запуски/<имя>/.

Не вызывает Gemini, TTS, publish, phase_a — только копирование файлов при --execute.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import (
    D01_OBSHCHEE,
    D01_01_ISHODNYE,
    D01_02_DLINA,
    D01_03_OTBOR_GEMINI,
    D02_SITE,
    D02_01_CLEAN,
    D02_02_SITE_INFO_GEMINI,
    D02_04_TTS,
    D02_05_PUBLISH,
    D05_RASSKAZY,
    D06_OTCHETY,
    D07_LOGI,
    D08_KARANTIN,
    D10_TEMP,
    F_INFO_EN,
    F_LEGACY_PATHS_JSON,
    F_MANIFEST,
    F_RAW_RESPONSE,
    F_RESULT_JSON,
    F_SITE_INFO_JSON,
    F_VALIDATION_JSON,
    S03_01_CLEANED,
    S03_04_TTS,
    launch_legacy_output_root,
    launch_legacy_runs_root,
    launch_site_visual_root,
    read_json,
    render_info_en_txt,
    sanitize_launch_folder_name,
    story_base_paths,
    story_telegram_root,
    write_json,
)
from orchestrator.phase_a import _parse_selection_result


def _legacy_run_paths(root_dir: Path, *, branch: str, run_id: str) -> dict[str, Path]:
    """Глобальный layout под корнем проекта (без Запуски/). Для привязки к запуску см. _legacy_paths_from_binding."""
    b = (branch or "site").strip().lower()
    rid = (run_id or "").strip()
    runs_root = (root_dir / "runs" / b / rid).resolve()
    phase_a = runs_root / "_phase_a"
    return {
        "runs_root": runs_root,
        "phase_a": phase_a,
        "stories": runs_root / "stories",
        "gemini_input_stories": phase_a / "gemini_input" / "stories",
        "gemini_info_stories": phase_a / "gemini_info_stage" / "gemini_input" / "stories",
        "logs": runs_root / "logs",
        "output_site_root": (root_dir / "output" / "site").resolve(),
    }


def _legacy_paths_from_binding(legacy_meta: dict[str, Any], project_root: Path) -> dict[str, Path]:
    """Пути из 10_Временные_файлы/legacy_technical_paths.json (абсолютные)."""
    rr = Path(str(legacy_meta["runs_root"])).resolve()
    ph = Path(str(legacy_meta["_phase_a"])).resolve() if legacy_meta.get("_phase_a") else (rr / "_phase_a").resolve()
    gi = (
        Path(str(legacy_meta["gemini_input_stories"])).resolve()
        if legacy_meta.get("gemini_input_stories")
        else (ph / "gemini_input" / "stories").resolve()
    )
    ginfo = (
        Path(str(legacy_meta["gemini_info_stage_stories"])).resolve()
        if legacy_meta.get("gemini_info_stage_stories")
        else (ph / "gemini_info_stage" / "gemini_input" / "stories").resolve()
    )
    logs = Path(str(legacy_meta["logs"])).resolve() if legacy_meta.get("logs") else (rr / "logs").resolve()
    out_site = (
        Path(str(legacy_meta["output_site_root"])).resolve()
        if legacy_meta.get("output_site_root")
        else (project_root / "output" / "site").resolve()
    )
    return {
        "runs_root": rr,
        "phase_a": ph,
        "stories": rr / "stories",
        "gemini_input_stories": gi,
        "gemini_info_stories": ginfo,
        "logs": logs,
        "output_site_root": out_site,
    }


def _copy_if_missing_or_newer(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    if dst.is_file():
        try:
            if dst.stat().st_mtime >= src.stat().st_mtime:
                return False
        except OSError:
            pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _resolve_cleaned_story_source(ws: Path) -> Path | None:
    """
    Legacy cleaner может писать либо cleaned_story.txt, либо voice-tagged файл (*__[MFU].txt).
    Возвращает лучший кандидат для синка в human cleaned_story.txt.
    """
    direct = ws / "cleaned_story.txt"
    if direct.is_file():
        return direct
    tagged = sorted(
        [p for p in ws.glob("*__?.txt") if p.is_file() and p.stem.endswith(("_M", "_F", "_U"))],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if tagged:
        return tagged[0]
    return None


def _story_id_candidates(raw: str) -> list[str]:
    base = (raw or "").strip()
    if not base:
        return []
    cands = [base]
    cands.append(sanitize_launch_folder_name(base))
    return [x for i, x in enumerate(cands) if x and x not in cands[:i]]


def _resolve_story_id_from_source_name(story_ids: list[str], source_name: str) -> str | None:
    stem = Path(source_name).stem
    if not stem:
        return None
    stem_cands = set(_story_id_candidates(stem))
    for sid in story_ids:
        sid_cands = set(_story_id_candidates(sid))
        if sid in stem_cands or stem in sid_cands:
            return sid
        if stem_cands.intersection(sid_cands):
            return sid
    return None


def _resolve_story_id_from_queue_dir_name(story_ids: list[str], queue_dir_name: str) -> str | None:
    """Пробует сопоставить `foo_000001` -> `foo` -> human story_id."""
    name = (queue_dir_name or "").strip()
    if not name:
        return None
    base = re.sub(r"_\d{6}$", "", name)
    return _resolve_story_id_from_source_name(story_ids, f"{base}.txt")


def _sync_selection_from_gemini_queue(
    *,
    launch: Path,
    legacy: dict[str, Path],
    story_ids: list[str],
    staged_name_to_story_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Fallback sync после timeout/ошибки: забрать info.txt из _phase_a/gemini_input*/stories/**/info.txt.
    Пишет минимум: raw_response.txt, result.json, validation.json.
    """
    queue_map = read_json(legacy["phase_a"] / "gemini_input_queue_map.json") or {}
    items = queue_map.get("items") if isinstance(queue_map, dict) else []
    mapping_by_queue_dir: dict[str, str] = {}
    staged_name_to_story_id = staged_name_to_story_id or {}
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict):
                continue
            qdir = Path(str(row.get("gemini_story_dir", ""))).resolve()
            src = str(row.get("source_path", ""))
            src_name = Path(src).name
            sid = staged_name_to_story_id.get(src_name) or _resolve_story_id_from_source_name(story_ids, src_name)
            if sid:
                mapping_by_queue_dir[str(qdir)] = sid

    # Для selection sync используем только selection-stage info (gemini_input).
    info_roots = [legacy["gemini_input_stories"]]
    found = 0
    synced_raw = 0
    synced_result = 0
    mapping_rows: list[dict[str, str]] = []
    errors: list[str] = []
    matched_story_ids: set[str] = set()
    for root in info_roots:
        if not root.is_dir():
            continue
        for info_p in sorted(root.rglob("info.txt")):
            try:
                raw = info_p.read_text(encoding="utf-8", errors="ignore")
            except OSError as ex:
                errors.append(f"read_error:{info_p}:{ex}")
                continue
            if not raw.strip():
                continue
            found += 1
            qdir = info_p.parent.resolve()
            sid = mapping_by_queue_dir.get(str(qdir))
            if sid is None:
                staged_file = next((p.name for p in qdir.iterdir() if p.is_file() and p.suffix.lower() == ".txt" and p.name.lower() != "info.txt"), "")
                if staged_file:
                    sid = staged_name_to_story_id.get(staged_file)
            if sid is None:
                sid = _resolve_story_id_from_queue_dir_name(story_ids, qdir.name)
            if sid is None:
                errors.append(f"story_mapping_not_found:{qdir}")
                continue
            hp = story_base_paths(launch, sid)
            hp["otbor"].mkdir(parents=True, exist_ok=True)
            raw_dst = hp["otbor"] / F_RAW_RESPONSE
            raw_dst.write_text(raw, encoding="utf-8")
            synced_raw += 1
            parse_ok = False
            parse_reason = "invalid_selection_raw"
            try:
                parsed = _parse_selection_result(sid, raw)
                verdict = str(parsed.get("verdict", "")).strip()
                reason = str(parsed.get("reason", "")).strip()
                if verdict in {"selected", "rejected", "policy_refusal", "manual_review"} and reason != "gemini_ambiguous_or_unparseable_verdict":
                    parse_ok = True
                    parse_reason = "parsed_ok"
                else:
                    parse_reason = reason or "invalid_selection_raw"
                write_json(hp["otbor"] / F_RESULT_JSON, parsed)
                synced_result += 1
            except Exception as ex:
                errors.append(f"selection_parse_error:{sid}:{ex}")
            write_json(
                hp["otbor"] / F_VALIDATION_JSON,
                {
                    "stage": "selection",
                    "ok": bool(parse_ok),
                    "reasons": [] if parse_ok else [parse_reason],
                    "source": str(info_p),
                },
            )
            matched_story_ids.add(sid)
            mapping_rows.append({"source_info": str(info_p), "story_id": sid, "raw_saved": str(raw_dst)})
    return {
        "legacy_selection_info_found": found,
        "synced_selection_raw": synced_raw,
        "synced_selection_result": synced_result,
        "sync_errors": errors,
        "selection_info_mapping": mapping_rows,
        "selection_story_ids_matched": sorted(matched_story_ids),
    }


def _count_nonempty_info(root: Path) -> int:
    if not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("info.txt"):
        try:
            if p.is_file() and p.read_text(encoding="utf-8", errors="ignore").strip():
                n += 1
        except OSError:
            continue
    return n


def load_launch_legacy_paths(launch: Path, project_root: Path) -> dict[str, Path] | None:
    """Читает legacy_technical_paths.json и возвращает пути legacy или None."""
    meta = read_json(launch / D10_TEMP / F_LEGACY_PATHS_JSON)
    if not isinstance(meta, dict) or not meta.get("runs_root"):
        return None
    return _legacy_paths_from_binding(meta, project_root)


def manifest_story_maps(manifest: dict[str, Any]) -> dict[str, Any]:
    """Данные из manifest.json для сопоставления canonical (output/site) ↔ human story_id."""
    story_ids_manifest = manifest.get("story_ids") if isinstance(manifest.get("story_ids"), list) else []
    story_ids_manifest = [str(x) for x in story_ids_manifest]
    staged_name_to_story_id: dict[str, str] = {}
    story_id_to_legacy_sid: dict[str, str] = {}
    for row in manifest.get("phase_a_staging_name_mapping") or []:
        if not isinstance(row, dict):
            continue
        original = str(row.get("original_name", "")).strip()
        staged = str(row.get("staged_name", "")).strip()
        if not original or not staged:
            continue
        sid = _resolve_story_id_from_source_name(story_ids_manifest, original)
        if sid:
            staged_name_to_story_id[staged] = sid
            story_id_to_legacy_sid[sid] = Path(staged).stem
    return {
        "story_ids_manifest": story_ids_manifest,
        "staged_name_to_story_id": staged_name_to_story_id,
        "story_id_to_legacy_sid": story_id_to_legacy_sid,
    }


def resolve_human_story_id_for_canonical(
    launch: Path,
    legacy: dict[str, Path],
    *,
    canonical_folder_name: str,
    story_ids_manifest: list[str],
    story_id_to_legacy_sid: dict[str, str],
) -> str | None:
    """
    Сопоставляет имя каталога в legacy output/site (<canonical>) с папкой 05_Рассказы/<human_story_id>/.
    """
    canon = (canonical_folder_name or "").strip()
    if not canon:
        return None
    stories_h = launch / D05_RASSKAZY
    if stories_h.is_dir():
        for sid in sorted(p.name for p in stories_h.iterdir() if p.is_dir()):
            legacy_sid = story_id_to_legacy_sid.get(sid, sid)
            ws = legacy["stories"] / legacy_sid
            if not ws.exists() and legacy_sid != sid:
                ws = legacy["stories"] / sid
            mapping = read_json(ws / "_pipeline" / "mapping.json") or {}
            c = str(mapping.get("canonical_basename", sid)).strip() or sid
            if c == canon:
                return sid
    for sid in story_ids_manifest:
        sid = (sid or "").strip()
        if not sid:
            continue
        legacy_sid = story_id_to_legacy_sid.get(sid, sid)
        ws = legacy["stories"] / legacy_sid
        if not ws.exists() and legacy_sid != sid:
            ws = legacy["stories"] / sid
        if not ws.is_dir():
            continue
        mapping = read_json(ws / "_pipeline" / "mapping.json") or {}
        c = str(mapping.get("canonical_basename", sid)).strip() or sid
        if c == canon:
            return sid
    return None


def _mirror_launch_level_artifacts_from_legacy(launch: Path, legacy: dict[str, Path]) -> dict[str, Any]:
    """
    Копии отчётов/логов/фильтра длины/визуала из legacy run → человекочитаемые папки запуска
    (без новых имён каталогов верхнего уровня).
    """
    copied = 0
    run_root = legacy["runs_root"]
    phase_a = legacy["phase_a"]
    logs = legacy["logs"]
    notes: list[str] = []

    def touch_copy(src: Path, dst: Path) -> None:
        nonlocal copied
        if _copy_if_missing_or_newer(src, dst):
            copied += 1
            notes.append(f"{src.name}→{dst.relative_to(launch)}")

    # --- length filter (общий этап 01) ---
    lf_res = launch / D01_OBSHCHEE / D01_02_DLINA / "Результат"
    lf_log = launch / D01_OBSHCHEE / D01_02_DLINA / "Логи"
    lf_in = launch / D01_OBSHCHEE / D01_02_DLINA / "Вход"
    for name in ("length_filter_report.csv", "length_filter_manifest.json"):
        touch_copy(phase_a / name, lf_res / name)
        if not (phase_a / name).is_file():
            touch_copy(run_root / name, lf_in / name)
    # --- Gemini common (сырые/отчётные JSON в корне phase_a) ---
    gem_in = launch / D01_OBSHCHEE / D01_03_OTBOR_GEMINI / "Вход"
    gem_log = launch / D01_OBSHCHEE / D01_03_OTBOR_GEMINI / "Логи"
    for name in ("selection_index.json", "site_pipeline_manifest.json", "gemini_input_queue_map.json"):
        touch_copy(phase_a / name, gem_in / name)
    for log_name in ("gemini_supervisor_state.json",):
        touch_copy(logs / log_name, gem_log / log_name)

    # --- visual (Excel + CSV + manifest из run/visual) ---
    vis_src = run_root / "visual"
    vis_dst = launch_site_visual_root(launch)
    if vis_src.is_dir():
        for p in sorted(vis_src.iterdir()):
            if p.is_file():
                touch_copy(p, vis_dst / p.name)
    for vis_name in ("visual_manifest.json",):
        touch_copy(phase_a / vis_name, launch / D06_OTCHETY / vis_name)

    # --- logs (общие) ---
    log_dst = launch / D07_LOGI
    if logs.is_dir():
        for p in sorted(logs.glob("*.log")):
            touch_copy(p, log_dst / p.name)
    for top_log in ("run.log",):
        touch_copy(run_root / top_log, log_dst / top_log)

    # --- reports (общие) ---
    rep_dst = launch / D06_OTCHETY
    rep_dst.mkdir(parents=True, exist_ok=True)
    for rep_name in ("REPORT.md", "migration_manifest.csv"):
        touch_copy(run_root / rep_name, rep_dst / rep_name)

    # --- short under 15m ---
    short_src = run_root / "stories" / "short_under_15m"
    short_dst = launch / D01_OBSHCHEE / "short_under_15m"
    if short_src.is_dir():
        short_dst.mkdir(parents=True, exist_ok=True)
        for p in short_src.rglob("*"):
            if p.is_file():
                rel = p.relative_to(short_src)
                dst = short_dst / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                touch_copy(p, dst)

    # --- site level TTS aggregate (без создания новых имён: только 02_Сайт/04_Озвучка_для_сайта) ---
    tts_agg = launch / D02_SITE / D02_04_TTS
    tts_agg.mkdir(parents=True, exist_ok=True)
    for p in phase_a.glob("EXPECTED_*.txt"):
        touch_copy(p, tts_agg / p.name)

    # --- publish (общие верхнеуровневые JSON из output site root parent если есть) ---
    pub_dst = launch / D02_SITE / D02_05_PUBLISH
    pub_dst.mkdir(parents=True, exist_ok=True)
    out_site = legacy["output_site_root"]
    if out_site.is_dir():
        for name in ("last_publish_report.json", "site_publish_trace.json"):
            touch_copy(out_site / name, pub_dst / name)

    # --- карантин: вложенные json/csv с ошибками отбора без стабильного sid (корень rejected) ---
    rej_root = run_root / "rejected"
    k_st = launch / D08_KARANTIN
    if rej_root.is_dir():
        k_st.mkdir(parents=True, exist_ok=True)
        for p in rej_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".json", ".csv", ".txt", ".log"}:
                rel = p.relative_to(rej_root)
                dst = k_st / ("rejected__" + rel.as_posix().replace("/", "__"))
                dst.parent.mkdir(parents=True, exist_ok=True)
                touch_copy(p, dst)

    return {"copied_extra": copied, "notes": notes[:200]}


def write_launch_legacy_binding(
    config: OrchestratorConfig,
    launch: Path,
    *,
    run_id: str,
    branch: str = "site",
) -> None:
    """
    Привязка Запуски/<имя> к техническому legacy: manifest + legacy_technical_paths.json
    (runs и output под 10_Временные_файлы/legacy/...).
    Нужна для partial sync после ошибки/таймаута (mirror читает source_run_id и JSON).
    """
    manifest_path = launch / F_MANIFEST
    manifest = read_json(manifest_path) or {}
    manifest["source_run_id"] = str(run_id).strip()
    manifest["source_branch"] = str(branch or "site").strip().lower() or "site"
    write_json(manifest_path, manifest)
    rid = str(run_id).strip()
    br = str(manifest["source_branch"]).strip().lower() or "site"
    launch_path = launch.resolve()
    runs_root_path = launch_legacy_runs_root(launch_path, br, rid)
    out_site = launch_legacy_output_root(launch_path, branch=br)
    legacy_paths = {
        "runs_root": str(runs_root_path),
        "_phase_a": str((runs_root_path / "_phase_a").resolve()),
        "gemini_input_stories": str((runs_root_path / "_phase_a" / "gemini_input" / "stories").resolve()),
        "gemini_info_stage_stories": str(
            (runs_root_path / "_phase_a" / "gemini_info_stage" / "gemini_input" / "stories").resolve()
        ),
        "logs": str((runs_root_path / "logs").resolve()),
        "output_site_root": str(out_site),
    }
    (launch / D10_TEMP).mkdir(parents=True, exist_ok=True)
    write_json(launch / D10_TEMP / F_LEGACY_PATHS_JSON, legacy_paths)


def plan_mirror_legacy_pipeline_to_human(
    config: OrchestratorConfig,
    launch: Path,
) -> dict[str, Any]:
    manifest = read_json(launch / F_MANIFEST) or {}
    legacy_meta = read_json(launch / D10_TEMP / F_LEGACY_PATHS_JSON)
    run_id = str(manifest.get("source_run_id", "")).strip()
    branch = str(manifest.get("source_branch", "site")).strip().lower() or "site"
    actions: list[dict[str, str]] = []
    if not run_id:
        return {"ok": False, "reason": "manifest.json: нет source_run_id", "actions": actions}
    if not isinstance(legacy_meta, dict) or not legacy_meta.get("runs_root"):
        return {
            "ok": False,
            "reason": f"нет {D10_TEMP}/{F_LEGACY_PATHS_JSON} (нужен migrate --execute с legacy)",
            "actions": actions,
        }
    legacy = _legacy_paths_from_binding(legacy_meta, config.root_dir)
    stories_h = launch / D05_RASSKAZY
    if not stories_h.is_dir():
        return {"ok": False, "reason": "нет папки 05_Рассказы/", "actions": actions}

    for sid in sorted(p.name for p in stories_h.iterdir() if p.is_dir()):
        ws = legacy["stories"] / sid
        pipe = ws / "_pipeline"
        hp = story_base_paths(launch, sid)
        cleaned_src = _resolve_cleaned_story_source(ws)
        for src, dst in [
            (pipe / "selection_raw.txt", hp["otbor"] / F_RAW_RESPONSE),
            (pipe / "selection_result.json", hp["otbor"] / F_RESULT_JSON),
            (pipe / "site_info_raw.txt", hp["site_info"] / F_RAW_RESPONSE),
            (pipe / "site_info.json", hp["site_info"] / F_SITE_INFO_JSON),
            (cleaned_src or (ws / "cleaned_story.txt"), hp["site_cleaned"] / "cleaned_story.txt"),
        ]:
            if src.is_file():
                actions.append({"story_id": sid, "from": str(src), "to": str(dst), "kind": src.name})

    return {"ok": True, "legacy_runs_root": str(legacy["runs_root"]), "actions": actions, "run_id": run_id}


def mirror_legacy_pipeline_to_human(
    config: OrchestratorConfig,
    launch: Path,
    *,
    execute: bool,
) -> dict[str, Any]:
    plan = plan_mirror_legacy_pipeline_to_human(config, launch)
    if not plan.get("ok"):
        return {**plan, "copied": 0, "execute": execute}
    if not execute:
        return {**plan, "copied": 0, "execute": False, "dry_run": True}

    manifest = read_json(launch / F_MANIFEST) or {}
    run_id = str(manifest.get("source_run_id", "")).strip()
    branch = str(manifest.get("source_branch", "site")).strip().lower() or "site"
    legacy_meta_exec = read_json(launch / D10_TEMP / F_LEGACY_PATHS_JSON) or {}
    legacy = _legacy_paths_from_binding(legacy_meta_exec, config.root_dir)
    stories_h = launch / D05_RASSKAZY
    maps = manifest_story_maps(manifest)
    staged_name_to_story_id: dict[str, str] = maps["staged_name_to_story_id"]
    story_id_to_legacy_sid: dict[str, str] = maps["story_id_to_legacy_sid"]
    story_ids_manifest: list[str] = maps["story_ids_manifest"]
    copied = 0
    updated_validation = 0
    updated_site_render = 0
    tts_synced = 0
    publish_synced = 0
    sync_errors: list[str] = []
    for sid in sorted(p.name for p in stories_h.iterdir() if p.is_dir()):
        legacy_sid = story_id_to_legacy_sid.get(sid, sid)
        ws = legacy["stories"] / legacy_sid
        if not ws.exists() and legacy_sid != sid:
            ws = legacy["stories"] / sid
        pipe = ws / "_pipeline"
        mapping = read_json(pipe / "mapping.json") or {}
        canonical = str(mapping.get("canonical_basename", sid)).strip() or sid
        legacy_site = (legacy["output_site_root"] / canonical).resolve()
        hp = story_base_paths(launch, sid)
        hp["root"].mkdir(parents=True, exist_ok=True)
        hp["otbor"].mkdir(parents=True, exist_ok=True)
        hp["site_info"].mkdir(parents=True, exist_ok=True)
        hp["site_cleaned"].mkdir(parents=True, exist_ok=True)
        cleaned_src = _resolve_cleaned_story_source(ws)
        for src, dst in [
            (pipe / "selection_raw.txt", hp["otbor"] / F_RAW_RESPONSE),
            (pipe / "selection_result.json", hp["otbor"] / F_RESULT_JSON),
            (pipe / "site_info_raw.txt", hp["site_info"] / F_RAW_RESPONSE),
            (pipe / "site_info.json", hp["site_info"] / F_SITE_INFO_JSON),
            (cleaned_src or (ws / "cleaned_story.txt"), hp["site_cleaned"] / "cleaned_story.txt"),
        ]:
            if _copy_if_missing_or_newer(src, dst):
                copied += 1

        _copy_if_missing_or_newer(legacy_site / "info.txt", hp["site_info"] / "info.txt")

        if cleaned_src and cleaned_src.is_file():
            site_cl = launch / D02_SITE / D02_01_CLEAN / sid
            site_cl.mkdir(parents=True, exist_ok=True)
            _copy_if_missing_or_newer(cleaned_src, site_cl / cleaned_src.name)

        # selection validation
        sel_res = read_json(hp["otbor"] / F_RESULT_JSON) or {}
        verdict = str(sel_res.get("verdict", "")).strip()
        valid_sel = verdict in {"selected", "rejected", "manual_review", "policy_refusal"}
        sel_reasons: list[str] = []
        if not (hp["otbor"] / F_RESULT_JSON).is_file():
            sel_reasons.append("no_result_json")
        elif not valid_sel:
            sel_reasons.append("invalid_verdict")
        write_json(hp["otbor"] / F_VALIDATION_JSON, {"stage": "selection", "ok": bool(valid_sel), "reasons": sel_reasons})
        updated_validation += 1

        # site info validation + render
        site_json = read_json(hp["site_info"] / F_SITE_INFO_JSON) or {}
        if site_json:
            body = render_info_en_txt(site_json)
            info_en = hp["site_info"] / F_INFO_EN
            if not info_en.is_file() or not info_en.read_text(encoding="utf-8", errors="replace").strip():
                info_en.write_text(body, encoding="utf-8")
                updated_site_render += 1
            ok_site = bool(site_json.get("description", "") and info_en.is_file())
            site_reasons = [] if ok_site else ["description_too_short_or_empty_info_en"]
            write_json(
                hp["site_info"] / F_VALIDATION_JSON,
                {"stage": "site_info", "ok": ok_site, "reasons": site_reasons},
            )
            updated_validation += 1

        # minimal TTS sync from legacy output/site/<canonical>
        for audio_name in ("audio.mp3", "folder.mp3", f"{canonical}.mp3"):
            legacy_mp3 = legacy_site / audio_name
            if _copy_if_missing_or_newer(legacy_mp3, hp["site_tts"] / "audio.mp3"):
                legacy_tts_meta = legacy_site / "tts_result.json"
                if legacy_tts_meta.is_file():
                    _copy_if_missing_or_newer(legacy_tts_meta, hp["site_tts"] / "tts_result.json")
                else:
                    write_json(
                        hp["site_tts"] / "tts_result.json",
                        {"source": str(legacy_mp3), "synced_at": "now", "status": "done"},
                    )
                tts_synced += 1
                break

        # minimal publish sync from legacy output/site/<canonical>
        ok_src = legacy_site / ".published_ok"
        if ok_src.is_file():
            (hp["site_publish"] / ".published_ok").write_text("ok\n", encoding="utf-8")
            publish_synced += 1
        for src_name, dst_name in (
            ("payload.json", "payload.json"),
            ("result.json", "result.json"),
            ("publish_payload.json", "payload.json"),
            ("publish_result.json", "result.json"),
        ):
            _copy_if_missing_or_newer(legacy_site / src_name, hp["site_publish"] / dst_name)

    wide = _mirror_launch_level_artifacts_from_legacy(launch, legacy)

    fallback_sel = _sync_selection_from_gemini_queue(
        launch=launch,
        legacy=legacy,
        story_ids=sorted(p.name for p in stories_h.iterdir() if p.is_dir()),
        staged_name_to_story_id=staged_name_to_story_id,
    )
    sync_errors.extend([str(x) for x in fallback_sel.get("sync_errors", []) if str(x)])

    legacy_site_info_found = _count_nonempty_info(legacy["gemini_info_stories"])
    summary = {
        "ok": True,
        "copied": copied,
        "execute": True,
        "actions": plan.get("actions", []),
        "validations_written": updated_validation,
        "info_en_written": updated_site_render,
        "tts_synced": tts_synced,
        "publish_synced": publish_synced,
        "legacy_selection_info_found": int(fallback_sel.get("legacy_selection_info_found") or 0),
        "legacy_site_info_found": legacy_site_info_found,
        "synced_selection_raw": int(fallback_sel.get("synced_selection_raw") or 0),
        "synced_selection_result": int(fallback_sel.get("synced_selection_result") or 0),
        "selection_info_mapping": fallback_sel.get("selection_info_mapping", []),
        "sync_errors": sync_errors,
        "wide_sync": wide,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    rep_dir = launch / D06_OTCHETY
    rep_dir.mkdir(parents=True, exist_ok=True)
    write_json(rep_dir / "legacy_human_routing_sync.json", summary)
    return summary


def _copy_logs_incremental(launch: Path, legacy_logs: Path) -> int:
    copied = 0
    dst_root = launch / D07_LOGI
    dst_root.mkdir(parents=True, exist_ok=True)
    if not legacy_logs.is_dir():
        return copied
    for p in sorted(legacy_logs.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(legacy_logs)
        if _copy_if_missing_or_newer(p, dst_root / rel):
            copied += 1
    return copied


def _write_stage_reports_incremental(launch: Path, legacy: dict[str, Path]) -> dict[str, Any]:
    phase_a = legacy["phase_a"]
    reports: dict[str, Any] = {}
    write_errors: list[str] = []

    def _safe_write_json(target: Path, payload: Any) -> None:
        try:
            write_json(target, payload)
        except (PermissionError, OSError) as ex:
            try:
                rel = target.resolve().relative_to(launch.resolve())
            except ValueError:
                rel = Path(target.name)
            fallback = (launch / D10_TEMP / "stage_report_fallback" / rel).resolve()
            try:
                write_json(fallback, payload)
                write_errors.append(f"{target}: {ex} (fallback={fallback})")
            except (PermissionError, OSError) as ex2:
                write_errors.append(f"{target}: {ex}; fallback_failed: {ex2}")

    intake_report = {
        "stage": "intake",
        "status": "in_progress",
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(phase_a),
        "counts": {},
    }
    qmap = read_json(phase_a / "gemini_input_queue_map.json") or {}
    items = qmap.get("items") if isinstance(qmap, dict) and isinstance(qmap.get("items"), list) else []
    if items:
        intake_report["status"] = "done"
        intake_report["counts"] = {"input": len(items)}
    _safe_write_json(launch / D01_OBSHCHEE / D01_01_ISHODNYE / "Отчёт_этапа.json", intake_report)
    reports["intake"] = intake_report

    length_summary = phase_a / "length_filter_manifest.json"
    length_csv = phase_a / "length_filter_report.csv"
    length_counts = {}
    lf = read_json(length_summary) or {}
    st = lf.get("stats") if isinstance(lf, dict) and isinstance(lf.get("stats"), dict) else {}
    if st:
        length_counts = {
            "input": int(st.get("intake_total", 0) or 0),
            "passed": int(st.get("selected_pending_gemini", 0) or 0),
            "rejected": int(st.get("short_rejected_total", 0) or 0),
        }
    length_report = {
        "stage": "length_filter",
        "status": "done" if (length_summary.is_file() or length_csv.is_file()) else "in_progress",
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(phase_a),
        "counts": length_counts,
    }
    _safe_write_json(launch / D01_OBSHCHEE / D01_02_DLINA / "Отчёт_этапа.json", length_report)
    if length_summary.is_file():
        _safe_write_json(
            launch / D01_OBSHCHEE / D01_02_DLINA / "Результат" / "length_filter_summary.json",
            {"source": str(length_summary), "counts": length_counts, "updated_at": length_report["updated_at"]},
        )
    reports["length_filter"] = length_report

    prog_counts = {
        "legacy_selection_info_found": _count_nonempty_info(legacy["gemini_input_stories"]),
        "legacy_site_info_found": _count_nonempty_info(legacy["gemini_info_stories"]),
    }
    a3_status = "done" if (phase_a / "phase_a_summary.json").is_file() else "in_progress"
    a3_report = {
        "stage": "gemini_selection",
        "status": a3_status,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(legacy["gemini_input_stories"]),
        "counts": prog_counts,
    }
    _safe_write_json(launch / D01_OBSHCHEE / D01_03_OTBOR_GEMINI / "Отчёт_этапа.json", a3_report)
    _safe_write_json(launch / D01_OBSHCHEE / D01_03_OTBOR_GEMINI / "Результат" / "progress.json", a3_report)
    reports["gemini_selection"] = a3_report

    cleaned_count = 0
    info_count = 0
    stories_root = launch / D05_RASSKAZY
    if stories_root.is_dir():
        for sdir in stories_root.iterdir():
            if not sdir.is_dir():
                continue
            if (sdir / "03_Сайт" / S03_01_CLEANED / "cleaned_story.txt").is_file():
                cleaned_count += 1
            if (sdir / "03_Сайт" / "02_Информация_для_сайта" / "site_info.json").is_file() or (
                sdir / "03_Сайт" / "02_Информация_для_сайта" / "info.en.txt"
            ).is_file():
                info_count += 1
    clean_rep = {
        "stage": "cleaner",
        "status": "in_progress",
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(legacy["stories"]),
        "counts": {"cleaned_story_txt": cleaned_count},
    }
    info_rep = {
        "stage": "site_info",
        "status": "in_progress",
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(legacy["stories"]),
        "counts": {"site_info_files": info_count},
    }
    _safe_write_json(launch / D02_SITE / D02_01_CLEAN / "Отчёт_этапа.json", clean_rep)
    _safe_write_json(launch / D02_SITE / D02_02_SITE_INFO_GEMINI / "Отчёт_этапа.json", info_rep)
    reports["cleaner"] = clean_rep
    reports["site_info"] = info_rep
    if write_errors:
        reports["_write_errors"] = write_errors
    return reports


def mirror_phase_a_progress_to_human(
    config: OrchestratorConfig,
    launch: Path,
    *,
    execute: bool,
) -> dict[str, Any]:
    """
    Инкрементальный safe-sync во время running phase-a:
    копирует только уже доступные артефакты из legacy в human-папки и пишет stage reports.
    """
    if not execute:
        return {"ok": True, "execute": False, "message": "dry-run: no files copied"}
    sync = mirror_legacy_pipeline_to_human(config, launch, execute=True)
    legacy_meta = read_json(launch / D10_TEMP / F_LEGACY_PATHS_JSON) or {}
    if not isinstance(legacy_meta, dict) or not legacy_meta.get("runs_root"):
        out = {**sync, "ok": False, "message": "legacy_technical_paths.json not found"}
        write_json(launch / D10_TEMP / "last_progress_sync_report.json", out)
        write_json(launch / D06_OTCHETY / "incremental_progress_sync.json", out)
        return out
    legacy = _legacy_paths_from_binding(legacy_meta, config.root_dir)
    copied_logs = _copy_logs_incremental(launch, legacy["logs"])
    stage_reports = _write_stage_reports_incremental(launch, legacy)
    report_write_errors = stage_reports.get("_write_errors") if isinstance(stage_reports, dict) else None
    sync_errs = list(sync.get("sync_errors", [])) if isinstance(sync.get("sync_errors"), list) else []
    if isinstance(report_write_errors, list) and report_write_errors:
        sync_errs.extend([f"stage_report: {e}" for e in report_write_errors])
    out = {
        "ok": bool(sync.get("ok", True)),
        "execute": True,
        "copied": int(sync.get("copied", 0) or 0),
        "copied_logs": copied_logs,
        "sync_errors": sync_errs,
        "legacy_selection_info_found": int(sync.get("legacy_selection_info_found", 0) or 0),
        "synced_selection_result": int(sync.get("synced_selection_result", 0) or 0),
        "legacy_site_info_found": int(sync.get("legacy_site_info_found", 0) or 0),
        "stage_reports": stage_reports,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    write_json(launch / D10_TEMP / "last_progress_sync_report.json", out)
    write_json(launch / D06_OTCHETY / "incremental_progress_sync.json", out)
    return out


def ensure_telegram_story_scaffold(launch: Path, story_id: str) -> Path:
    """Каркас будущего Telegram внутри YouTube-ветки: 05_Рассказы/<id>/04_YouTube/08_Telegram/..."""
    root = story_telegram_root(launch, story_id)
    for sub in ("01_Текст", "02_Информация", "03_Озвучка", "04_Визуал", "05_Пост", "06_Публикация"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def write_telegram_snapshot_metadata(launch: Path, story_id: str) -> None:
    root = ensure_telegram_story_scaffold(launch, story_id)
    meta = root / "metadata.json"
    payload: dict[str, Any] = {
        "story_id": story_id,
        "site_cleaned_story_relative": f"{D05_RASSKAZY}/{story_id}/03_Сайт/{S03_01_CLEANED}/cleaned_story.txt",
        "site_audio_dir_relative": f"{D05_RASSKAZY}/{story_id}/03_Сайт/{S03_04_TTS}/",
        "note": "Snapshot для будущей YouTube/Telegram-ветки: site cleaned + audio.",
    }
    write_json(meta, payload)
