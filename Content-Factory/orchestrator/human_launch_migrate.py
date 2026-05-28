from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import (
    D01_OBSHCHEE,
    D01_02_DLINA,
    D01_03_OTBOR_GEMINI,
    D02_SITE,
    D02_02_SITE_INFO_GEMINI,
    D05_RASSKAZY,
    D06_OTCHETY,
    D10_TEMP,
    top_level_dirs,
    F_INFO_EN,
    F_LEGACY_PATHS_JSON,
    F_MANIFEST,
    F_MIGRATION_MANIFEST_CSV,
    F_OTCHET_ETAPA,
    F_RAW_RESPONSE,
    F_RESULT_JSON,
    F_SITE_INFO_JSON,
    F_SOURCE_TXT,
    F_STATUS,
    F_STORY_STATUS,
    F_VALIDATION_JSON,
    S01_OBSHCHEE,
    S02_OTBOR,
    S03_01_CLEANED,
    S03_02_INFO,
    S03_03_VISUAL,
    S03_04_TTS,
    S03_05_PUBLISH,
    S03_SITE,
    all_skeleton_relative_paths,
    append_migration_csv_row,
    human_zapuski_root,
    now_iso,
    read_json,
    render_info_en_txt,
    story_base_paths,
    sanitize_launch_folder_name,
    unique_launch_path,
    write_json,
)
from orchestrator.phase_a import _parse_selection_result, _parse_site_info_result


def legacy_run_paths(config: OrchestratorConfig, *, branch: str, run_id: str) -> dict[str, Path]:
    b = (branch or "site").strip().lower()
    rid = (run_id or "").strip()
    runs_root = (config.root_dir / "runs" / b / rid).resolve()
    phase_a = runs_root / "_phase_a"
    return {
        "runs_root": runs_root,
        "phase_a": phase_a,
        "stories": runs_root / "stories",
        "gemini_input_stories": phase_a / "gemini_input" / "stories",
        "gemini_info_stories": phase_a / "gemini_info_stage" / "gemini_input" / "stories",
        "logs": runs_root / "logs",
    }


def planned_legacy_technical_paths(legacy: dict[str, Path]) -> dict[str, str]:
    """Содержимое 10_Временные_файлы/legacy_technical_paths.json после migrate --execute."""
    return {
        "runs_root": str(legacy["runs_root"]),
        "_phase_a": str(legacy["phase_a"]),
        "gemini_input_stories": str(legacy["gemini_input_stories"]),
        "gemini_info_stage_stories": str(legacy["gemini_info_stories"]),
        "logs": str(legacy["logs"]),
    }


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _nonempty(s: str | None) -> bool:
    return bool((s or "").strip())


def _default_launch_name(phase_a: Path) -> str:
    kept = 0
    man = phase_a / "length_filter_manifest.json"
    if man.is_file():
        data = read_json(man) or {}
        kept = int(data.get("kept_count") or 0)
    date_s = ""
    if man.is_file():
        try:
            date_s = datetime.fromtimestamp(man.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            date_s = ""
    if not date_s:
        date_s = now_iso()[:10]
    return f"{date_s}_Сайт_{kept}_рассказов"


def validate_selection(*, story_id: str, raw: str | None, result: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[str] = []
    if not result:
        reasons.append("no_result_json")
        return {"ok": False, "reasons": reasons}
    verdict = str(result.get("verdict", ""))
    if verdict not in {"selected", "rejected", "manual_review", "policy_refusal"}:
        reasons.append("invalid_verdict")
    if not str(result.get("story_id", story_id)).strip():
        reasons.append("missing_story_id_in_result")
    if raw and verdict:
        rep = _parse_selection_result(story_id, raw)
        if str(rep.get("verdict")) != verdict:
            reasons.append("verdict_mismatch_raw_vs_json")
    return {"ok": len(reasons) == 0, "reasons": reasons}


def validate_site_info(*, site_info: dict[str, Any] | None, info_en_body: str) -> dict[str, Any]:
    reasons: list[str] = []
    if not site_info:
        reasons.append("no_site_info_json")
        return {"ok": False, "reasons": reasons}
    desc = str(site_info.get("description", "")).strip()
    if len(desc) < 3:
        reasons.append("description_too_short")
    if not _nonempty(info_en_body):
        reasons.append("empty_info_en")
    return {"ok": len(reasons) == 0, "reasons": reasons}


def _find_gemini_info_txt_for_story(
    gemini_info_root: Path,
    *,
    cleaned_basename: str,
) -> Path | None:
    """Ищем info.txt в gemini_info_stage: в той же папке есть cleaned .txt по canonical."""
    if not gemini_info_root.is_dir():
        return None
    best: tuple[float, Path] | None = None
    for info_p in gemini_info_root.rglob("info.txt"):
        parent = info_p.parent
        hit = False
        for ch in parent.iterdir():
            if not ch.is_file() or ch.suffix.lower() != ".txt":
                continue
            if ch.name == f"{cleaned_basename}.txt" or ch.stem == cleaned_basename:
                hit = True
                break
        if not hit:
            continue
        mt = info_p.stat().st_mtime if info_p.is_file() else 0.0
        cand = (mt, info_p)
        if best is None or cand[0] >= best[0]:
            best = cand
    return best[1] if best else None


def _find_selection_info_txt(
    gemini_input_stories: Path,
    original_filename: str,
) -> Path | None:
    if not gemini_input_stories.is_dir():
        return None
    best: tuple[float, Path] | None = None
    for info_p in gemini_input_stories.rglob("info.txt"):
        if (info_p.parent / original_filename).is_file():
            mt = info_p.stat().st_mtime
            cand = (mt, info_p)
            if best is None or cand[0] >= best[0]:
                best = cand
    return best[1] if best else None


def _index_gemini_selection_info_txt(gemini_input_stories: Path) -> dict[str, Path]:
    """Один проход rglob: original_filename -> лучший info.txt (по mtime)."""
    if not gemini_input_stories.is_dir():
        return {}
    best: dict[str, tuple[float, Path]] = {}
    for info_p in gemini_input_stories.rglob("info.txt"):
        parent = info_p.parent
        try:
            mt = info_p.stat().st_mtime
        except OSError:
            continue
        for ch in parent.iterdir():
            if not ch.is_file() or ch.suffix.lower() != ".txt":
                continue
            key = ch.name
            prev = best.get(key)
            if prev is None or mt >= prev[0]:
                best[key] = (mt, info_p)
    return {k: v[1] for k, v in best.items()}


def _index_gemini_site_info_txt(gemini_info_stories: Path) -> dict[str, Path]:
    """Один проход: stem cleaned txt -> лучший info.txt."""
    if not gemini_info_stories.is_dir():
        return {}
    best: dict[str, tuple[float, Path]] = {}
    for info_p in gemini_info_stories.rglob("info.txt"):
        parent = info_p.parent
        try:
            mt = info_p.stat().st_mtime
        except OSError:
            continue
        for ch in parent.iterdir():
            if not ch.is_file() or ch.suffix.lower() != ".txt":
                continue
            for key in (ch.stem, ch.name.replace(".txt", "")):
                prev = best.get(key)
                if prev is None or mt >= prev[0]:
                    best[key] = (mt, info_p)
    return {k: v[1] for k, v in best.items()}


@dataclass
class StoryInspect:
    story_id: str
    selection_raw_source: str
    site_raw_source: str
    selection_status: str
    site_info_status: str
    legacy_root_info: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class LaunchInspectReport:
    human_launch_path: Path | None
    legacy: dict[str, Path]
    story_count: int
    stories: list[StoryInspect]
    counts: dict[str, int]
    problems: list[str]


def inspect_human_structure(
    config: OrchestratorConfig,
    *,
    human_name: str | None = None,
    from_run_id: str | None = None,
    branch: str = "site",
) -> LaunchInspectReport:
    human_path: Path | None = None
    resolved_run_id = (from_run_id or "").strip()
    resolved_branch = (branch or "site").strip().lower() or "site"
    if human_name:
        hp = (human_zapuski_root(config.root_dir) / human_name.strip()).resolve()
        if hp.is_dir():
            human_path = hp
            m = read_json(hp / F_MANIFEST)
            if m:
                resolved_run_id = str(m.get("source_run_id", resolved_run_id)).strip()
                resolved_branch = str(m.get("source_branch", resolved_branch)).strip().lower() or "site"
    if resolved_run_id and human_path is None:
        root = human_zapuski_root(config.root_dir)
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                m = read_json(child / F_MANIFEST)
                if m and str(m.get("source_run_id", "")).strip() == resolved_run_id:
                    human_path = child
                    break

    if not resolved_run_id:
        empty_legacy = legacy_run_paths(config, branch="site", run_id="__invalid__")
        return LaunchInspectReport(
            human_launch_path=human_path,
            legacy=empty_legacy,
            story_count=0,
            stories=[],
            counts={
                "stories": 0,
                "unknown_bundle": 0,
            },
            problems=[
                "Не удалось определить source_run_id: укажите --from-run-id "
                "или папку Запуски/<имя>/ с файлом manifest.json после migrate."
            ],
        )

    legacy = legacy_run_paths(config, branch=resolved_branch, run_id=resolved_run_id)

    stories_root = legacy["stories"]
    story_ids = sorted([p.name for p in stories_root.iterdir() if p.is_dir()]) if stories_root.is_dir() else []

    items: list[StoryInspect] = []
    counts = {
        "stories": len(story_ids),
        "selection_result": 0,
        "selection_raw": 0,
        "site_info_json": 0,
        "site_info_raw": 0,
        "legacy_root_info": 0,
        "can_migrate_site_without_gemini": 0,
        "site_pending": 0,
        "site_invalid": 0,
        "selection_ok": 0,
        "selection_invalid": 0,
        "unknown_bundle": 0,
    }
    problems: list[str] = []
    g1_idx = _index_gemini_selection_info_txt(legacy["gemini_input_stories"])
    g2_idx = _index_gemini_site_info_txt(legacy["gemini_info_stories"])
    qm = read_json(legacy["phase_a"] / "gemini_input_queue_map.json")

    for sid in story_ids:
        ws = stories_root / sid
        pipe = ws / "_pipeline"
        mapping = read_json(pipe / "mapping.json") or {}
        can = str(mapping.get("canonical_basename", sid))
        sel_raw_p = pipe / "selection_raw.txt"
        sel_res_p = pipe / "selection_result.json"
        site_raw_p = pipe / "site_info_raw.txt"
        site_json_p = pipe / "site_info.json"
        root_info = ws / "info.txt"

        sel_raw = _read_text(sel_raw_p)
        sel_res = read_json(sel_res_p)
        site_raw = _read_text(site_raw_p)
        site_json = read_json(site_json_p)

        if sel_res:
            counts["selection_result"] += 1
        if _nonempty(sel_raw):
            counts["selection_raw"] += 1
        if site_json:
            counts["site_info_json"] += 1
        if _nonempty(site_raw):
            counts["site_info_raw"] += 1
        if root_info.is_file():
            counts["legacy_root_info"] += 1

        raw_sel = sel_raw
        sel_src = "selection_raw.txt" if _nonempty(raw_sel) else ""
        if not _nonempty(raw_sel):
            m = qm
            orig = ""
            if isinstance(m, dict):
                for it in m.get("items") or []:
                    if isinstance(it, dict) and sid in str(it.get("source_path", "")):
                        orig = Path(str(it["source_path"])).name
                        break
            orig = orig or str(mapping.get("original_filename", ""))
            if orig:
                ginfo = g1_idx.get(orig) or _find_selection_info_txt(legacy["gemini_input_stories"], orig)
                if ginfo:
                    raw_sel = _read_text(ginfo)
                    sel_src = str(ginfo)

        v_sel = validate_selection(story_id=sid, raw=raw_sel, result=sel_res)
        if v_sel["ok"]:
            counts["selection_ok"] += 1
        else:
            counts["selection_invalid"] += 1

        site_raw_final = site_raw
        site_src = "site_info_raw.txt" if _nonempty(site_raw) else ""
        if not _nonempty(site_raw_final):
            g2 = g2_idx.get(can) or _find_gemini_info_txt_for_story(legacy["gemini_info_stories"], cleaned_basename=can)
            if g2:
                site_raw_final = _read_text(g2)
                site_src = str(g2)

        site_st = "unknown"
        can_i = can
        if not _nonempty(site_raw_final) and not site_json:
            site_st = "pending"
            counts["site_pending"] += 1
        elif not _nonempty(site_raw_final) and site_json:
            v_site = validate_site_info(site_info=site_json, info_en_body=render_info_en_txt(site_json))
            site_st = "ok" if v_site["ok"] else "invalid"
            if site_st == "ok":
                counts["can_migrate_site_without_gemini"] += 1
            else:
                counts["site_invalid"] += 1
        else:
            try:
                parsed = _parse_site_info_result(sid, can_i, site_raw_final or "")
                merged = {**parsed, **(site_json or {})}
                v_site = validate_site_info(site_info=merged, info_en_body=render_info_en_txt(merged))
                if v_site["ok"]:
                    site_st = "ok"
                    counts["can_migrate_site_without_gemini"] += 1
                else:
                    site_st = "invalid"
                    counts["site_invalid"] += 1
            except Exception as exc:
                site_st = "invalid"
                counts["site_invalid"] += 1
                problems.append(f"{sid}: site parse error: {exc}")

        notes: list[str] = []
        if not v_sel["ok"]:
            notes.append("selection:" + ",".join(v_sel.get("reasons") or []))
        if not v_sel["ok"] and site_st == "pending":
            counts["unknown_bundle"] += 1

        items.append(
            StoryInspect(
                story_id=sid,
                selection_raw_source=sel_src or ("gemini_input" if _nonempty(raw_sel) else ""),
                site_raw_source=site_src,
                selection_status="ok" if v_sel["ok"] else "invalid",
                site_info_status=site_st,
                legacy_root_info=root_info.is_file(),
                notes=notes,
            )
        )

    for it in items:
        if it.site_info_status == "invalid" or it.selection_status == "invalid":
            problems.append(f"{it.story_id}: selection={it.selection_status} site={it.site_info_status}")

    return LaunchInspectReport(
        human_launch_path=human_path,
        legacy=legacy,
        story_count=len(story_ids),
        stories=items,
        counts=counts,
        problems=problems,
    )


def _backup_if_exists(dst: Path, backup_dir: Path) -> None:
    if not dst.is_file():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "-")
    bak = backup_dir / f"{dst.name}.{stamp}.bak"
    shutil.copy2(dst, bak)


def _write_story_artifacts(
    launch: Path,
    story_id: str,
    *,
    mapping: dict[str, Any],
    selection_raw: str | None,
    selection_result: dict[str, Any] | None,
    site_raw: str | None,
    site_info_existing: dict[str, Any] | None,
    legacy_info_path: Path | None,
    execute: bool,
    migration_csv: Path,
    backup_dir: Path,
    log: Callable[[str], None],
) -> dict[str, Any]:
    paths = story_base_paths(launch, story_id)

    def record(action: str, frm: str, to: str, reason: str, sel_st: str, site_st: str) -> None:
        if not execute:
            return
        append_migration_csv_row(
            migration_csv,
            {
                "story_id": story_id,
                "action": action,
                "from_path": frm,
                "to_path": to,
                "reason": reason,
                "selection_status": sel_st,
                "site_info_status": site_st,
            },
        )

    raw_sel = selection_raw or ""
    res = selection_result
    if not res and _nonempty(raw_sel):
        res = _parse_selection_result(story_id, raw_sel)
    sel_val = validate_selection(story_id=story_id, raw=raw_sel if _nonempty(raw_sel) else None, result=res)
    if sel_val["ok"]:
        sel_st = "ok"
    elif res is not None or _nonempty(raw_sel):
        sel_st = "invalid"
    else:
        sel_st = "pending"

    can = str(mapping.get("canonical_basename", story_id))
    site_raw_f = site_raw or ""
    site_info: dict[str, Any] | None = dict(site_info_existing) if site_info_existing else None
    site_val: dict[str, Any]
    site_st: str

    if _nonempty(site_raw_f):
        try:
            parsed = _parse_site_info_result(story_id, can, site_raw_f)
        except Exception as exc:
            site_info = None
            site_val = {"ok": False, "reasons": [f"parse_error:{exc}"]}
            site_st = "invalid"
        else:
            if site_info:
                site_info = {**parsed, **site_info}
            else:
                site_info = parsed
            body_en = render_info_en_txt(site_info)
            site_val = validate_site_info(site_info=site_info, info_en_body=body_en)
            site_st = "ok" if site_val["ok"] else "invalid"
    elif site_info:
        body_en = render_info_en_txt(site_info)
        site_val = validate_site_info(site_info=site_info, info_en_body=body_en)
        site_st = "ok" if site_val["ok"] else "invalid"
    else:
        site_val = {"ok": False, "reasons": ["no_raw_second_gemini_no_json"]}
        site_st = "pending"

    otbor = paths["otbor"]
    dst_raw = otbor / F_RAW_RESPONSE
    dst_res = otbor / F_RESULT_JSON
    dst_vsel = otbor / F_VALIDATION_JSON
    info_dir = paths["site_info"]

    if execute:
        paths["root"].mkdir(parents=True, exist_ok=True)
        otbor.mkdir(parents=True, exist_ok=True)
        paths["obshchee"].mkdir(parents=True, exist_ok=True)
        paths["site"].mkdir(parents=True, exist_ok=True)
        for sd in (S03_01_CLEANED, S03_02_INFO, S03_03_VISUAL, S03_04_TTS, S03_05_PUBLISH):
            (paths["site"] / sd).mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)
        if _nonempty(raw_sel):
            _backup_if_exists(dst_raw, backup_dir / story_id)
            dst_raw.write_text(raw_sel, encoding="utf-8")
            record("write", "(selection_raw)", str(dst_raw), "первичный отбор: сырой ответ", sel_st, site_st)
            log(f"  {story_id}: {dst_raw.name}")
        if res:
            _backup_if_exists(dst_res, backup_dir / story_id)
            write_json(dst_res, res)
            record("write", "(selection_result)", str(dst_res), "первичный отбор: result.json", sel_st, site_st)
        write_json(dst_vsel, {"stage": "selection", "ok": sel_val["ok"], "reasons": sel_val.get("reasons", [])})

        if _nonempty(site_raw_f):
            _backup_if_exists(info_dir / F_RAW_RESPONSE, backup_dir / story_id)
            (info_dir / F_RAW_RESPONSE).write_text(site_raw_f, encoding="utf-8")
            record("write", "(site_raw)", str(info_dir / F_RAW_RESPONSE), "site-info: сырой ответ Gemini", sel_st, site_st)
        if site_info:
            _backup_if_exists(info_dir / F_SITE_INFO_JSON, backup_dir / story_id)
            write_json(info_dir / F_SITE_INFO_JSON, site_info)
            record("write", "(site_info)", str(info_dir / F_SITE_INFO_JSON), "site_info.json", sel_st, site_st)
        if site_info and site_val.get("ok"):
            body = render_info_en_txt(site_info)
            _backup_if_exists(info_dir / F_INFO_EN, backup_dir / story_id)
            (info_dir / F_INFO_EN).write_text(body, encoding="utf-8")
            record("write", "(render)", str(info_dir / F_INFO_EN), "info.en.txt", sel_st, site_st)
        write_json(
            info_dir / F_VALIDATION_JSON,
            {"stage": "site_info", "ok": bool(site_val.get("ok")), "reasons": site_val.get("reasons", [])},
        )
        from orchestrator.human_launch_legacy_sync import ensure_telegram_story_scaffold, write_telegram_snapshot_metadata

        ensure_telegram_story_scaffold(launch, story_id)
        write_telegram_snapshot_metadata(launch, story_id)

    legacy_note = str(legacy_info_path) if legacy_info_path and legacy_info_path.is_file() else ""
    errs: list[str] = []
    if legacy_note:
        errs.append(f"legacy_root_info.txt present (not resume marker): {legacy_note}")

    if execute:
        from orchestrator.human_launch_lifecycle import build_story_status_payload, ensure_story_tmp

        ensure_story_tmp(launch, story_id)
        final_st = build_story_status_payload(launch, story_id, errors=errs or None)
        write_json(paths["story_status"], final_st)
        return final_st

    return {
        "story_id": story_id,
        "dry_run": True,
        "selection_gate": {"status": sel_st},
        "site_info_gate": {"status": site_st},
        "legacy_info": {"path": legacy_note, "used_for_resume": False},
    }


def _classify_root_info_txt(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "empty"
    if "Заголовок:" in t or "Описание:" in t or "Альтернативный заголовок:" in t:
        return "legacy_ru_info"
    if "Title:" in t and "Description:" in t:
        return "maybe_en_info"
    return "unknown"


def _nonempty_info_txt(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _count_nonempty_gemini_info_txt(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("info.txt") if _nonempty_info_txt(p))


def _legacy_manifest_scan(phase_a: Path, runs_root: Path) -> dict[str, Any]:
    names = [
        "length_filter_manifest.json",
        "intake_manifest.json",
        "selection_gate_manifest.json",
        "site_info_manifest.json",
        "clean_manifest.json",
        "gemini_input_queue_map.json",
        "phase_a_summary.json",
        "visual_manifest.json",
    ]
    out: dict[str, Any] = {"_phase_a": str(phase_a), "runs_root": str(runs_root), "files": {}}
    for n in names:
        p = phase_a / n
        out["files"][n] = {"path": str(p), "exists": p.is_file()}
    extra: list[str] = []
    if phase_a.is_dir():
        for p in sorted(phase_a.glob("*.json")):
            if p.name not in names:
                extra.append(p.name)
    out["extra_json_in_phase_a"] = extra[:40]
    return out


def _analyze_story_migration_row(
    legacy: dict[str, Path],
    sid: str,
    ws: Path,
    *,
    gem1_index: dict[str, Path] | None = None,
    gem2_index: dict[str, Path] | None = None,
    gemini_input_queue_map: Any | None = None,
) -> dict[str, Any]:
    pipe = ws / "_pipeline"
    mapping = read_json(pipe / "mapping.json") or {"story_id": sid, "original_filename": "", "canonical_basename": sid}
    can = str(mapping.get("canonical_basename", sid))
    orig = str(mapping.get("original_filename", ""))

    sel_raw_p = pipe / "selection_raw.txt"
    sel_res_p = pipe / "selection_result.json"
    site_raw_p = pipe / "site_info_raw.txt"
    site_json_p = pipe / "site_info.json"
    root_info_p = ws / "info.txt"
    cleaned_p = ws / "cleaned_story.txt"

    sel_raw = _read_text(sel_raw_p)
    sel_res = read_json(sel_res_p)
    site_raw = _read_text(site_raw_p)
    site_json = read_json(site_json_p)

    sel_src = "pipeline/selection_raw.txt" if _nonempty(sel_raw) else ""
    raw_sel = sel_raw
    if not _nonempty(raw_sel):
        qm = gemini_input_queue_map
        if qm is None:
            qm = read_json(legacy["phase_a"] / "gemini_input_queue_map.json")
        if isinstance(qm, dict):
            for it in qm.get("items") or []:
                if isinstance(it, dict) and sid in str(it.get("source_path", "")):
                    orig = orig or Path(str(it.get("source_path", ""))).name
                    break
        orig = orig or str(mapping.get("original_filename", ""))
        if orig:
            g1 = (gem1_index or {}).get(orig) or _find_selection_info_txt(legacy["gemini_input_stories"], orig)
            if g1:
                raw_sel = _read_text(g1)
                sel_src = str(g1)

    v_sel = validate_selection(story_id=sid, raw=raw_sel if _nonempty(raw_sel) else None, result=sel_res)
    sel_status = "ok" if v_sel["ok"] else ("invalid" if (sel_res is not None or _nonempty(raw_sel)) else "pending")

    site_src = "pipeline/site_info_raw.txt" if _nonempty(site_raw) else ""
    site_raw_final = site_raw
    if not _nonempty(site_raw_final):
        g2 = (gem2_index or {}).get(can) or _find_gemini_info_txt_for_story(
            legacy["gemini_info_stories"],
            cleaned_basename=can,
        )
        if g2:
            site_raw_final = _read_text(g2)
            site_src = str(g2)

    site_st = "unknown"
    site_st_note = ""
    if not _nonempty(site_raw_final) and not site_json:
        site_st = "pending"
    elif not _nonempty(site_raw_final) and site_json:
        v_site = validate_site_info(site_info=site_json, info_en_body=render_info_en_txt(site_json))
        site_st = "ok" if v_site["ok"] else "invalid"
    else:
        try:
            parsed = _parse_site_info_result(sid, can, site_raw_final or "")
            merged = {**parsed, **(site_json or {})}
            v_site = validate_site_info(site_info=merged, info_en_body=render_info_en_txt(merged))
            site_st = "ok" if v_site["ok"] else "invalid"
        except Exception as exc:
            site_st = "invalid"
            site_st_note = str(exc)

    root_cls = "n/a"
    root_txt = ""
    if root_info_p.is_file():
        root_txt = _read_text(root_info_p) or ""
        root_cls = _classify_root_info_txt(root_txt)

    src_candidates = [ws / str(mapping.get("text_file", ""))] if mapping.get("text_file") else []
    src_candidates += [ws / f"{can}.txt", ws / f"{sid}.txt"]
    src_path = next((p for p in src_candidates if p.is_file()), None)

    fully_ok = bool(v_sel["ok"] and site_st == "ok")
    return {
        "story_id": sid,
        "canonical_basename": can,
        "original_filename": orig,
        "selection_raw_source": sel_src,
        "selection_raw_nonempty": _nonempty(raw_sel),
        "selection_result_json": sel_res is not None,
        "selection_status": sel_status,
        "selection_valid": v_sel["ok"],
        "selection_reasons": list(v_sel.get("reasons") or []),
        "site_raw_source": site_src,
        "site_raw_nonempty": _nonempty(site_raw_final),
        "site_json_exists": site_json is not None,
        "site_info_status": site_st,
        "site_info_valid": site_st == "ok",
        "info_en_renderable": site_st == "ok",
        "root_info_exists": root_info_p.is_file(),
        "root_info_class": root_cls,
        "source_txt_path": str(src_path) if src_path else "",
        "cleaned_story_exists": cleaned_p.is_file(),
        "fully_migratable": fully_ok,
        "site_parse_error": site_st_note if site_st == "invalid" else "",
    }


def build_migrate_dry_run_diagnostics(
    config: OrchestratorConfig,
    *,
    legacy: dict[str, Path],
    phase_a: Path,
    launch: Path,
    desired_clean: str,
    branch: str,
    verbose: bool,
) -> dict[str, Any]:
    runs_root = legacy["runs_root"]
    paths_checked = [
        str(runs_root),
        str(phase_a),
        str(legacy["stories"]),
        str(legacy["gemini_input_stories"]),
        str(legacy["gemini_info_stories"]),
        str(legacy["logs"]),
    ]
    diag: dict[str, Any] = {
        "paths_checked": paths_checked,
        "legacy_exists": runs_root.is_dir(),
        "phase_a_exists": phase_a.is_dir(),
        "stories_dir_exists": legacy["stories"].is_dir(),
    }
    if not runs_root.is_dir():
        diag["error"] = "runs_root does not exist; check --from-run-id and --run-branch"
        return diag

    manifest_scan = _legacy_manifest_scan(phase_a, runs_root)
    diag["manifests"] = manifest_scan

    lf = read_json(phase_a / "length_filter_manifest.json") or {}
    intake = read_json(phase_a / "intake_manifest.json") or {}
    diag["kept_count"] = int(lf.get("kept_count") or 0)
    diag["intake_total_files"] = int(intake.get("total_files") or 0)

    stories_root = legacy["stories"]
    story_ids = sorted([p.name for p in stories_root.iterdir() if p.is_dir()]) if stories_root.is_dir() else []
    diag["story_folder_count"] = len(story_ids)

    gem1 = _count_nonempty_gemini_info_txt(legacy["gemini_input_stories"])
    gem2 = _count_nonempty_gemini_info_txt(legacy["gemini_info_stories"])
    diag["gemini_queue_info_txt_nonempty_stage1"] = gem1
    diag["gemini_queue_info_txt_nonempty_stage2"] = gem2

    cleaned_n = 0
    source_n = 0
    if stories_root.is_dir():
        for sid in story_ids:
            ws = stories_root / sid
            if (ws / "cleaned_story.txt").is_file():
                cleaned_n += 1
            pipe = ws / "_pipeline"
            m = read_json(pipe / "mapping.json") or {}
            can = str(m.get("canonical_basename", sid))
            cands = [ws / str(m.get("text_file", ""))] if m.get("text_file") else []
            cands += [ws / f"{can}.txt", ws / f"{sid}.txt"]
            if any(p.is_file() for p in cands):
                source_n += 1

    g1_idx = _index_gemini_selection_info_txt(legacy["gemini_input_stories"])
    g2_idx = _index_gemini_site_info_txt(legacy["gemini_info_stories"])
    qm_cache = read_json(phase_a / "gemini_input_queue_map.json")
    rows = [
        _analyze_story_migration_row(
            legacy,
            sid,
            stories_root / sid,
            gem1_index=g1_idx,
            gem2_index=g2_idx,
            gemini_input_queue_map=qm_cache,
        )
        for sid in story_ids
    ]

    sel_res_n = sum(1 for r in rows if r["selection_result_json"])
    site_json_n = sum(1 for r in rows if r["site_json_exists"])
    root_info_n = sum(1 for r in rows if r["root_info_exists"])
    ru_n = sum(1 for r in rows if r["root_info_class"] == "legacy_ru_info")
    en_n = sum(1 for r in rows if r["root_info_class"] == "maybe_en_info")
    unk_n = sum(1 for r in rows if r["root_info_class"] == "unknown" and r["root_info_exists"])

    fully = sum(1 for r in rows if r["fully_migratable"])
    sel_inv = sum(1 for r in rows if r["selection_status"] == "invalid")
    sel_pend = sum(1 for r in rows if r["selection_status"] == "pending")
    site_pend = sum(1 for r in rows if r["site_info_status"] == "pending")
    site_inv = sum(1 for r in rows if r["site_info_status"] == "invalid")
    site_ok = sum(1 for r in rows if r["site_info_status"] == "ok")
    sel_parse_ok = sum(1 for r in rows if r["selection_valid"])
    sel_parse_fail = len(rows) - sel_parse_ok
    site_parse_ok = sum(1 for r in rows if r["site_info_valid"])
    site_parse_fail = sum(1 for r in rows if (r["site_raw_nonempty"] or r["site_json_exists"]) and not r["site_info_valid"])

    unknown_bundle = sum(1 for r in rows if not r["selection_valid"] and r["site_info_status"] == "pending")

    cand = human_zapuski_root(config.root_dir) / desired_clean
    will_v2 = cand.exists() and launch.resolve() != cand.resolve()

    diag.update(
        {
            "rows": rows,
            "stats": {
                "total_stories": len(rows),
                "source_txt_resolvable": source_n,
                "cleaned_story_txt": cleaned_n,
                "selection_result_json": sel_res_n,
                "site_info_json": site_json_n,
                "root_legacy_info_txt": root_info_n,
                "fully_migratable": fully,
                "selection_pending": sel_pend,
                "selection_invalid": sel_inv,
                "site_pending": site_pend,
                "site_invalid": site_inv,
                "site_ok": site_ok,
                "unknown_bundle": unknown_bundle,
                "selection_parse_ok": sel_parse_ok,
                "selection_parse_fail": sel_parse_fail,
                "site_parse_ok": site_parse_ok,
                "site_info_en_renderable": site_ok,
                "gemini_stage1_output_info_txt": gem1,
                "gemini_stage2_output_info_txt": gem2,
            },
            "root_info_breakdown": {"legacy_ru_info": ru_n, "maybe_en_info": en_n, "unknown": unk_n},
            "target_launch_path": str(launch.resolve()),
            "target_exists": launch.exists(),
            "target_will_use_v2_suffix": will_v2,
            "verbose": verbose,
        }
    )
    return diag


def print_migrate_dry_run_report(
    *,
    legacy: dict[str, Path],
    launch: Path,
    desired_clean: str,
    mkdir_total: int,
    diag: dict[str, Any] | None,
    verbose: bool,
) -> None:
    runs_root = legacy["runs_root"]
    phase_a = legacy["phase_a"]

    print("")
    print("========== migrate-to-human-structure DRY-RUN REPORT ==========")
    print("")
    print("0. SUMMARY (migrate preview, stdout)")
    st = (diag or {}).get("stats") or {}
    rows = list((diag or {}).get("rows") or [])
    sel_ok = sum(1 for r in rows if r.get("selection_valid"))
    site_ok_n = int(st.get("site_ok", 0))
    sel_res_creatable = sum(1 for r in rows if r.get("selection_result_json") or r.get("selection_raw_nonempty"))
    site_json_creatable = sum(1 for r in rows if r.get("site_json_exists") or r.get("site_raw_nonempty"))
    info_en_n = site_ok_n
    pl = planned_legacy_technical_paths(legacy)
    print(f"  stories_found: {st.get('total_stories', 0)}")
    print(f"  source_txt_resolvable: {st.get('source_txt_resolvable', 0)}")
    print(f"  cleaned_story.txt (legacy workspace): {st.get('cleaned_story_txt', 0)}")
    print(f"  gemini stage1 nonempty queue info.txt: {st.get('gemini_stage1_output_info_txt', 0)}")
    print(f"  gemini stage2 nonempty queue info.txt: {st.get('gemini_stage2_output_info_txt', 0)}")
    print(f"  selection: stories with result.json or nonempty raw (inputs to migrate): {sel_res_creatable}")
    print(f"  site: stories with site_info.json or nonempty site raw: {site_json_creatable}")
    print(f"  info.en.txt renderable after migrate (valid site_info): {info_en_n}")
    print(f"  story status: selection.done (valid): {sel_ok}  pending: {st.get('selection_pending', 0)}  invalid: {st.get('selection_invalid', 0)}")
    print(f"  story status: site_info.done (valid): {site_ok_n}  pending: {st.get('site_pending', 0)}  invalid: {st.get('site_invalid', 0)}")
    print(f"  unknown_bundle (invalid selection + site pending): {st.get('unknown_bundle', 0)}")
    print(f"  root legacy info.txt in story folder: {st.get('root_legacy_info_txt', 0)}")
    print("  planned legacy_technical_paths.json (keys -> paths):")
    for k, v in pl.items():
        print(f"    {k}: {v}")
    print("  human launch primary tree (artifacts on --execute):")
    print(f"    {launch.resolve()}")
    print(f"    {launch.resolve() / D05_RASSKAZY} /<story_id> / 02_Отбор / 03_Сайт / ...")
    print(f"    {launch.resolve() / D10_TEMP / F_LEGACY_PATHS_JSON}")
    print("")

    print("A. Source legacy run")
    print(f"  path: {runs_root}")
    print(f"  exists: {runs_root.is_dir()}")
    if not runs_root.is_dir():
        print("  checked paths:")
        for p in (diag or {}).get("paths_checked") or []:
            print(f"    - {p}")
        print(f"  reason: {(diag or {}).get('error', 'unknown')}")
        print("")
        print("I. Safety")
        print("  DRY-RUN: no files written")
        print("  Gemini / pipeline / resume: not invoked")
        print("  legacy files: not deleted, not overwritten")
        print("========== end report ==========")
        return

    ms = (diag or {}).get("manifests") or {}
    print("  manifest scan (_phase_a):")
    for name, info in (ms.get("files") or {}).items():
        ex = info.get("exists", False)
        print(f"    - {name}: exists={ex}")
    extras = ms.get("extra_json_in_phase_a") or []
    if extras:
        print(f"  other json in _phase_a (sample): {', '.join(extras[:15])}")
    print(f"  kept_count (length_filter_manifest): {(diag or {}).get('kept_count', 0)}")
    print(f"  intake total_files (if present): {(diag or {}).get('intake_total_files', 0)}")
    print(f"  story folders under runs/.../stories: {(diag or {}).get('story_folder_count', 0)}")

    print("")
    print("B. Target human launch")
    print(f"  planned path: {launch}")
    print(f"  exists now: {(diag or {}).get('target_exists', launch.exists())}")
    print(f"  will_add_v2_suffix: {(diag or {}).get('target_will_use_v2_suffix', False)}")
    print("  top-level dirs to create (sample):")
    for d in top_level_dirs()[:12]:
        print(f"    - {d}/")
    print(f"  total mkdir paths in skeleton: {mkdir_total}")

    st = (diag or {}).get("stats") or {}
    print("")
    print("C. Aggregate stats")
    for k in sorted(st.keys()):
        print(f"  {k}: {st[k]}")
    rib = (diag or {}).get("root_info_breakdown") or {}
    print(f"  root info.txt classification: ru={rib.get('legacy_ru_info', 0)} en_hint={rib.get('maybe_en_info', 0)} unknown={rib.get('unknown', 0)}")

    rows: list[dict[str, Any]] = list((diag or {}).get("rows") or [])
    sel_ok = sum(1 for r in rows if r.get("selection_valid"))
    sel_bad = len(rows) - sel_ok
    site_render = sum(1 for r in rows if r.get("info_en_renderable"))

    print("")
    print("D. First Gemini / selection -> 05_Рассказы/<id>/02_Отбор/")
    print("  dest: raw_response.txt, result.json, validation.json")
    print(f"  stories with valid selection (parse): {sel_ok}")
    print(f"  stories not valid / missing: {sel_bad}")
    print("  examples (up to 10):")
    for r in rows[:10]:
        ssrc = str(r.get("selection_raw_source", ""))[:80]
        print(f"    - {r.get('story_id')}: src={ssrc!r} valid={r.get('selection_valid')} status={r.get('selection_status')}")

    print("")
    print("E. Second Gemini / site info -> 05_Рассказы/<id>/03_Сайт/02_Информация_для_сайта/")
    print("  dest: raw_response.txt, site_info.json, info.en.txt, validation.json")
    print(f"  site_info ok (parse + info.en): {site_render}")
    print(f"  site pending: {st.get('site_pending', 0)} invalid: {st.get('site_invalid', 0)}")
    print("  examples (up to 10):")
    for r in rows[:10]:
        wsrc = str(r.get("site_raw_source", ""))[:80]
        print(f"    - {r.get('story_id')}: src={wsrc!r} status={r.get('site_info_status')} valid={r.get('site_info_valid')}")

    print("")
    print("F. Legacy root info.txt (workspace)")
    print(f"  count: {st.get('root_legacy_info_txt', 0)}")
    print("  used as done-marker in human launch: NO (resume_signals in story status)")
    print("  action: keep for legacy compatibility only")

    print("")
    print("G. status.json (predicted on --execute)")
    n = len(rows)
    print(f"  launch status.json: 1 (under {launch})")
    print(f"  per-story status.json: {n}")
    print(f"  selection.done (valid): {sel_ok} pending: {st.get('selection_pending', 0)} invalid: {st.get('selection_invalid', 0)}")
    print(f"  site_info.done: {site_render} pending: {st.get('site_pending', 0)} invalid: {st.get('site_invalid', 0)}")

    print("")
    print("H. Files to create on --execute")
    print("  - manifest.json, status.json (launch)")
    print(f"  - 05_Рассказы/<story_id>/status.json x {n}")
    print("  - 06_Отчёты/migration_manifest.csv")
    print("  - 10_Временные_файлы/legacy_technical_paths.json")
    print(f"  - skeleton dirs total: {mkdir_total}")

    print("")
    print("I. Safety")
    print("  DRY-RUN: no files written")
    print("  Gemini: not started")
    print("  pipeline / resume: not started")
    print("  legacy: not deleted; legacy info.txt not overwritten by this command")
    print("  TTS / publish / YouTube / Telegram: not touched")
    print("  worker pool: not touched")

    if verbose and rows:
        print("")
        print("--- VERBOSE ---")
        good = [r for r in rows if r.get("fully_migratable")]
        sel_pend = [r for r in rows if r.get("selection_status") == "pending"]
        sel_inv = [r for r in rows if r.get("selection_status") == "invalid"]
        site_pend = [r for r in rows if r.get("site_info_status") == "pending"]
        site_inv = [r for r in rows if r.get("site_info_status") == "invalid"]
        print(f"fully_migratable (first 20 of {len(good)}):")
        for r in good[:20]:
            print(f"  {r.get('story_id')}")
        print(f"selection pending (first 20 of {len(sel_pend)}):")
        for r in sel_pend[:20]:
            print(f"  {r.get('story_id')}: reasons={r.get('selection_reasons')}")
        print(f"selection invalid (first 20 of {len(sel_inv)}):")
        for r in sel_inv[:20]:
            print(f"  {r.get('story_id')}: reasons={r.get('selection_reasons')}")
        print(f"site_info pending (first 20 of {len(site_pend)}):")
        for r in site_pend[:20]:
            print(f"  {r.get('story_id')}: site_parse_error={r.get('site_parse_error', '')!r}")
        print(f"site_info invalid (first 20 of {len(site_inv)}):")
        for r in site_inv[:20]:
            print(f"  {r.get('story_id')}: err={r.get('site_parse_error', '')!r}")
        print("per-story copy hints (first 20, legacy -> human on --execute):")
        for r in rows[:20]:
            sid = r.get("story_id")
            sp = r.get("source_txt_path") or "(no source txt)"
            print(f"  {sid}: source.txt <- {sp}")
            if r.get("selection_raw_source"):
                print(f"    -> {launch / D05_RASSKAZY / sid / S02_OTBOR / F_RAW_RESPONSE}  (from {r.get('selection_raw_source')})")
            if r.get("site_raw_source"):
                print(f"    -> {launch / D05_RASSKAZY / sid / S03_SITE / S03_02_INFO / F_RAW_RESPONSE}  (from {r.get('site_raw_source')})")

    print("")
    print("========== end report ==========")


def migrate_to_human_structure(
    config: OrchestratorConfig,
    *,
    from_run_id: str,
    launch_name: str | None,
    branch: str = "site",
    execute: bool = False,
    verbose: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = log or (lambda m: None)
    legacy = legacy_run_paths(config, branch=branch, run_id=from_run_id)
    phase_a = legacy["phase_a"]
    desired = (launch_name or "").strip() or _default_launch_name(phase_a)
    desired_clean = sanitize_launch_folder_name(desired)
    if execute:
        launch = unique_launch_path(config.root_dir, desired_clean)
    else:
        cand = human_zapuski_root(config.root_dir) / desired_clean
        launch = cand if not cand.exists() else unique_launch_path(config.root_dir, desired_clean)

    if not legacy["runs_root"].is_dir():
        diag = build_migrate_dry_run_diagnostics(
            config,
            legacy=legacy,
            phase_a=phase_a,
            launch=launch,
            desired_clean=desired_clean,
            branch=branch,
            verbose=verbose,
        )
        return {
            "ok": False,
            "message": f"legacy run not found: {legacy['runs_root']}",
            "dry_run_diagnostics": diag,
            "legacy": legacy,
            "planned_launch_path": str(launch.resolve()),
            "mkdir_actions_total": len(all_skeleton_relative_paths()),
            "desired_clean": desired_clean,
        }

    actions: list[str] = []
    for rel in all_skeleton_relative_paths():
        p = launch / rel
        actions.append(f"mkdir {p}")
        if execute:
            p.mkdir(parents=True, exist_ok=True)

    # manifest at launch root (preview)
    kept = 0
    lf_man = phase_a / "length_filter_manifest.json"
    if lf_man.is_file():
        kept = int((read_json(lf_man) or {}).get("kept_count") or 0)

    manifest = {
        "human_launch_name": launch.name,
        "requested_name": desired_clean,
        "created_at": now_iso(),
        "source_run_id": from_run_id,
        "source_branch": branch,
        "run_mode": "site",
        "legacy_runs_root": str(legacy["runs_root"]),
        "legacy_phase_a": str(phase_a),
        "kept_count_from_manifest": kept,
        "primary_human_root": str(launch.resolve()),
        "note": "Главная человекочитаемая структура — эта папка; legacy пути в legacy_technical_paths.json",
    }
    if execute:
        human_zapuski_root(config.root_dir).mkdir(parents=True, exist_ok=True)
        launch.mkdir(parents=True, exist_ok=True)
        write_json(launch / F_MANIFEST, manifest)
        write_json(
            launch / F_STATUS,
            {"updated_at": now_iso(), "migration": "executed" if execute else "dry_run", "source_run_id": from_run_id},
        )
        legacy_paths = {
            "runs_root": str(legacy["runs_root"]),
            "_phase_a": str(phase_a),
            "gemini_input_stories": str(legacy["gemini_input_stories"]),
            "gemini_info_stage_stories": str(legacy["gemini_info_stories"]),
            "logs": str(legacy["logs"]),
        }
        write_json(launch / D10_TEMP / F_LEGACY_PATHS_JSON, legacy_paths)
        if lf_man.is_file():
            shutil.copy2(lf_man, launch / D01_OBSHCHEE / D01_02_DLINA / "Вход" / "length_filter_manifest.json")

    migration_csv = launch / D06_OTCHETY / F_MIGRATION_MANIFEST_CSV
    if execute:
        (launch / D06_OTCHETY).mkdir(parents=True, exist_ok=True)
        if migration_csv.exists():
            _backup_if_exists(migration_csv, launch / D10_TEMP / "backup_before_migration")
        migration_csv.unlink(missing_ok=True)

    backup_dir = launch / D10_TEMP

    report = inspect_human_structure(config, from_run_id=from_run_id, branch=branch)
    stories_root = legacy["stories"]
    mig_g1 = _index_gemini_selection_info_txt(legacy["gemini_input_stories"])
    mig_g2 = _index_gemini_site_info_txt(legacy["gemini_info_stories"])

    for sid in sorted([p.name for p in stories_root.iterdir() if p.is_dir()]):
        ws = stories_root / sid
        pipe = ws / "_pipeline"
        mapping = read_json(pipe / "mapping.json") or {"story_id": sid, "original_filename": "", "canonical_basename": sid}
        sel_raw = _read_text(pipe / "selection_raw.txt")
        sel_res = read_json(pipe / "selection_result.json")
        site_raw = _read_text(pipe / "site_info_raw.txt")
        site_json = read_json(pipe / "site_info.json")

        orig = str(mapping.get("original_filename", ""))
        if not _nonempty(sel_raw) and orig:
            g1 = mig_g1.get(orig) or _find_selection_info_txt(legacy["gemini_input_stories"], orig)
            if g1:
                sel_raw = _read_text(g1)

        can = str(mapping.get("canonical_basename", sid))
        if not _nonempty(site_raw):
            g2 = mig_g2.get(can) or _find_gemini_info_txt_for_story(legacy["gemini_info_stories"], cleaned_basename=can)
            if g2:
                site_raw = _read_text(g2)

        # source.txt
        src_candidates = [ws / str(mapping.get("text_file", ""))] if mapping.get("text_file") else []
        src_candidates += [ws / f"{can}.txt", ws / f"{sid}.txt"]
        src_path = next((p for p in src_candidates if p.is_file()), None)
        if execute and src_path:
            dest = launch / D05_RASSKAZY / sid / S01_OBSHCHEE
            dest.mkdir(parents=True, exist_ok=True)
            _backup_if_exists(dest / F_SOURCE_TXT, backup_dir / sid)
            shutil.copy2(src_path, dest / F_SOURCE_TXT)
            append_migration_csv_row(
                migration_csv,
                {
                    "story_id": sid,
                    "action": "copy",
                    "from_path": str(src_path),
                    "to_path": str(dest / F_SOURCE_TXT),
                    "reason": "исходный текст рассказа",
                    "selection_status": "",
                    "site_info_status": "",
                },
            )
            log(f"{sid}: source.txt <- {src_path.name}")

        _write_story_artifacts(
            launch,
            sid,
            mapping=mapping,
            selection_raw=sel_raw,
            selection_result=sel_res,
            site_raw=site_raw,
            site_info_existing=site_json,
            legacy_info_path=ws / "info.txt",
            execute=execute,
            migration_csv=migration_csv,
            backup_dir=backup_dir,
            log=log,
        )

    if execute:
        n_legacy_stories = len([p for p in stories_root.iterdir() if p.is_dir()]) if stories_root.is_dir() else 0
        sel_gate = phase_a / "selection_gate_manifest.json"
        if sel_gate.is_file():
            dsel = launch / D01_OBSHCHEE / D01_03_OTBOR_GEMINI / "Результат"
            dsel.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sel_gate, dsel / "selection_gate_manifest.json")
        site_m = phase_a / "site_info_manifest.json"
        if site_m.is_file():
            dsite = launch / D02_SITE / D02_02_SITE_INFO_GEMINI / "Результат"
            dsite.mkdir(parents=True, exist_ok=True)
            shutil.copy2(site_m, dsite / "site_info_manifest.json")
        write_json(
            launch / D01_OBSHCHEE / D01_03_OTBOR_GEMINI / F_OTCHET_ETAPA,
            {
                "этап": "Первичный_отбор_Gemini",
                "updated_at": now_iso(),
                "stories_in_legacy": n_legacy_stories,
                "source": str(phase_a / "selection_gate_manifest.json"),
            },
        )
        write_json(
            launch / D02_SITE / D02_02_SITE_INFO_GEMINI / F_OTCHET_ETAPA,
            {
                "этап": "Информация_для_сайта_Gemini",
                "updated_at": now_iso(),
                "stories_in_legacy": n_legacy_stories,
                "source": str(phase_a / "site_info_manifest.json"),
            },
        )
        from orchestrator.human_launch_lifecycle import refresh_launch_status_file

        refresh_launch_status_file(launch)

    if not execute:
        diag = build_migrate_dry_run_diagnostics(
            config,
            legacy=legacy,
            phase_a=phase_a,
            launch=launch,
            desired_clean=desired_clean,
            branch=branch,
            verbose=verbose,
        )
        return {
            "ok": True,
            "dry_run": True,
            "planned_launch_path": str(launch.resolve()),
            "mkdir_actions_sample": actions[:25],
            "mkdir_actions_total": len(actions),
            "inspect": report,
            "dry_run_diagnostics": diag,
            "legacy": legacy,
            "desired_clean": desired_clean,
            "message": "dry-run: no writes",
        }

    return {
        "ok": True,
        "dry_run": False,
        "launch_path": str(launch.resolve()),
        "migration_csv": str(migration_csv.resolve()),
        "inspect": report,
    }


def print_inspect_report(rep: LaunchInspectReport, *, title: str) -> None:
    print(title)
    print(f"legacy_runs_root: {rep.legacy['runs_root']}")
    print(f"legacy_phase_a:   {rep.legacy['phase_a']}")
    if rep.human_launch_path:
        print(f"human_launch:     {rep.human_launch_path}")
    else:
        print("human_launch:     (not found under Запуски/ for this run)")
    print(f"stories_found:    {rep.story_count}")
    for k, v in rep.counts.items():
        print(f"  {k}: {v}")
    if rep.problems:
        print("problems:")
        for p in rep.problems[:80]:
            print(f"  - {p}")
        if len(rep.problems) > 80:
            print(f"  ... +{len(rep.problems) - 80} more")
    print("unknown_bundle (invalid selection + site pending):", rep.counts.get("unknown_bundle", 0))
    print("Note: legacy technical dirs = worker/orchestrator artifacts; human-readable root = Запуски/<name>/")
    print("  After migrate --execute: see manifest + 10_Временные_файлы/legacy_technical_paths.json")


def cleanup_plan(config: OrchestratorConfig, *, human_name: str) -> dict[str, Any]:
    from orchestrator.human_launch_lifecycle import cleanup_plan_detailed

    return cleanup_plan_detailed(config, human_name=human_name)
