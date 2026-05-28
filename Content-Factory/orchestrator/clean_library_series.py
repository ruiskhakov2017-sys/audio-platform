"""
Перенос серийных .txt из корня жанровой папки библиотеки в <genre>/_series/.

Скан только: <library_root>/<genre>/*.txt (без рекурсии, без захода в _series).
Классификация: normalize_story_base_title + stem_serial_signal + логика audit (_classify_group / _finalize_singleton).

Dry-run по умолчанию; --execute — реальный MOVE, без удаления и без перезаписи (suffix _duplicate_NNN).

Важно: config.reports_dir не должен оказаться обычной подпапкой library-root (иначе mkdir отчётов
создаст лишнюю «жанровую» директорию). Обычно reports_dir = .orchestrator/reports в проекте CF.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.series_title_audit_all import (
    _classify_group,
    _confidence_for,
    _finalize_singleton,
    extended_series_markers,
    extract_part_marker_number,
)
from orchestrator.stories_input_series_return import normalize_story_base_title, stem_serial_signal


def _discover_genre_dirs(library_root: Path) -> list[Path]:
    if not library_root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(library_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name == "_series":
            continue
        if child.name.startswith("."):
            continue
        out.append(child)
    return out


def _genre_root_txt_files(genre_dir: Path) -> list[Path]:
    return sorted(
        [p for p in genre_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt"],
        key=lambda p: p.name.lower(),
    )


def _serial_reasons(stem: str, strip_tags: list[str]) -> str:
    _ok, tags = extended_series_markers(stem)
    parts: list[str] = list(tags)
    if strip_tags:
        parts.append("strip:" + "+".join(strip_tags))
    return ";".join(parts) if parts else ""


def _allocate_target_in_series(series_dir: Path, filename: str, batch_reserved: set[str] | None = None) -> tuple[Path, int]:
    """
    Цель в _series. Если файл с таким именем уже есть — имя с _duplicate_001, …
    Возвращает (path, duplicate_index) где duplicate_index 0 = без суффикса.

    batch_reserved: имена файлов (не пути), уже занятые в этом прогоне в series_dir — меньше лишних .exists().
    """
    reserved = batch_reserved if batch_reserved is not None else set()
    stem = Path(filename).stem
    ext = Path(filename).suffix
    base_name = filename
    if base_name not in reserved and not (series_dir / base_name).exists():
        reserved.add(base_name)
        return series_dir / base_name, 0
    for i in range(1, 10000):
        name = f"{stem}_duplicate_{i:03d}{ext}"
        if name not in reserved and not (series_dir / name).exists():
            reserved.add(name)
            return series_dir / name, i
    raise RuntimeError("clean_library_series: duplicate suffix exhausted")


@dataclass
class CleanLibrarySeriesOptions:
    library_root: Path
    execute: bool


def run_clean_library_series(*, config: OrchestratorConfig, options: CleanLibrarySeriesOptions) -> dict[str, Any]:
    library_root = options.library_root.resolve()
    execute = options.execute
    reports_dir = config.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    if execute:
        report_csv = reports_dir / "clean_library_series_execute_manifest.csv"
        report_json = reports_dir / "clean_library_series_execute_manifest.json"
    else:
        report_csv = reports_dir / "clean_library_series_dry_run.csv"
        report_json = reports_dir / "clean_library_series_dry_run.json"

    started = time.perf_counter()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    print(f"clean-library-series: scanning {library_root} …", flush=True)
    genre_dirs = _discover_genre_dirs(library_root)
    genres_checked = len(genre_dirs)

    sig_cache: dict[str, tuple[bool, str]] = {}
    part_cache: dict[str, tuple[str, int | None]] = {}

    def _cached_sig(s: str) -> tuple[bool, str]:
        if s not in sig_cache:
            sig_cache[s] = stem_serial_signal(s)
        return sig_cache[s]

    def _cached_part(s: str) -> tuple[str, int | None]:
        if s not in part_cache:
            part_cache[s] = extract_part_marker_number(s)
        return part_cache[s]

    rows_out: list[dict[str, Any]] = []

    for gd in genre_dirs:
        genre_name = gd.name
        txt_files = _genre_root_txt_files(gd)
        groups: dict[str, list[Path]] = defaultdict(list)
        for p in txt_files:
            norm0, _ = normalize_story_base_title(p.stem)
            groups[norm0].append(p)

        marker_by_norm: dict[str, bool] = {
            norm: any(_cached_sig(x.stem)[0] for x in grp) for norm, grp in groups.items()
        }
        reserved_names: set[str] = set()

        for p in txt_files:
            stem = p.stem
            norm, strip_tags = normalize_story_base_title(stem)
            group = groups[norm]
            group_has_marker = marker_by_norm.get(norm, False)
            group_size = len(group)
            singleton_explicit, _tag = _cached_sig(stem)
            d = _classify_group(group_size, group_has_marker, singleton_explicit)
            d = _finalize_singleton(d, stem, strip_tags)
            has_m = group_has_marker if group_size > 1 else singleton_explicit
            part_marker, part_num = _cached_part(stem)
            confidence = _confidence_for(d, group_size, bool(has_m))
            reasons = _serial_reasons(stem, strip_tags)

            series_dir = p.parent / "_series"
            target_path_str = ""
            action = "keep"
            dup_idx = 0
            if d == "serial":
                target, dup_idx = _allocate_target_in_series(series_dir, p.name, reserved_names)
                target_path_str = str(target)
                action = "move_to_series" if execute else "plan_move"
            elif d == "probable_serial":
                action = "keep_probable_serial"
            elif d == "uncertain":
                action = "keep_uncertain"
            else:
                action = "keep_standalone_ok"

            result = "planned" if d == "serial" and not execute else ("pending" if d == "serial" and execute else "unchanged")

            rows_out.append(
                {
                    "genre": genre_name,
                    "original_path": str(p),
                    "target_path": target_path_str,
                    "filename": p.name,
                    "base_title": norm,
                    "part_marker": part_marker,
                    "part_number": "" if part_num is None else str(part_num),
                    "decision": d,
                    "confidence": confidence,
                    "serial_reasons": reasons,
                    "action": action,
                    "result": result,
                    "_dup_suffix": dup_idx,
                    "_src_for_move": str(p) if d == "serial" else "",
                }
            )

    txt_total = len(rows_out)

    serial_rows = [r for r in rows_out if r["decision"] == "serial"]
    probable_rows = [r for r in rows_out if r["decision"] == "probable_serial"]
    uncertain_rows = [r for r in rows_out if r["decision"] == "uncertain"]
    standalone_rows = [r for r in rows_out if r["decision"] == "standalone_ok"]

    suffix_conflicts = sum(1 for r in serial_rows if int(r.get("_dup_suffix", 0) or 0) > 0)
    skipped_already_in_series = 0

    moved = 0
    errors: list[str] = []

    if execute:
        for r in serial_rows:
            src = Path(r["_src_for_move"])
            target = Path(r["target_path"])
            series_dir = target.parent
            try:
                series_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                errors.append(f"mkdir {series_dir}: {exc}")
                r["result"] = f"error:{exc}"
                continue
            try:
                if target.resolve() == src.resolve():
                    r["result"] = "skipped_same_path"
                    continue
                shutil.move(str(src), str(target))
                moved += 1
                r["result"] = "moved"
                r["action"] = "move_to_series"
            except OSError as exc:
                errors.append(f"{src} -> {target}: {exc}")
                r["result"] = f"error:{exc}"

    elapsed = max(0.0001, time.perf_counter() - started)

    genre_roots_txt_after = 0
    serial_remaining_after = 0
    if execute:
        for gd in _discover_genre_dirs(library_root):
            roots = _genre_root_txt_files(gd)
            genre_roots_txt_after += len(roots)
            groups2: dict[str, list[Path]] = defaultdict(list)
            for p2 in roots:
                n2, _ = normalize_story_base_title(p2.stem)
                groups2[n2].append(p2)
            m2 = {n: any(_cached_sig(x.stem)[0] for x in g) for n, g in groups2.items()}
            for p2 in roots:
                n2, st2 = normalize_story_base_title(p2.stem)
                g2 = groups2[n2]
                gs = len(g2)
                ghm = m2.get(n2, False)
                se, _ = _cached_sig(p2.stem)
                d2 = _classify_group(gs, ghm, se)
                d2 = _finalize_singleton(d2, p2.stem, st2)
                if d2 == "serial":
                    serial_remaining_after += 1

    fieldnames = [
        "genre",
        "original_path",
        "target_path",
        "filename",
        "base_title",
        "part_marker",
        "part_number",
        "decision",
        "confidence",
        "serial_reasons",
        "action",
        "result",
    ]
    csv_rows: list[dict[str, Any]] = []
    for r in rows_out:
        row = {k: r[k] for k in fieldnames if k in r}
        csv_rows.append(row)

    with report_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(csv_rows)

    summary: dict[str, Any] = {
        "genres_checked": genres_checked,
        "txt_total": txt_total,
        "serial_count": len(serial_rows),
        "probable_serial_count": len(probable_rows),
        "uncertain_count": len(uncertain_rows),
        "standalone_ok_count": len(standalone_rows),
        "files_moved_to_series": moved if execute else 0,
        "files_planned_move": len(serial_rows) if not execute else len(serial_rows),
        "skipped_already_in_series": skipped_already_in_series,
        "suffix_duplicate_resolved": suffix_conflicts,
        "errors_count": len(errors),
        "elapsed_sec": round(elapsed, 4),
    }
    if execute:
        summary["genre_root_txt_after_execute"] = genre_roots_txt_after
        summary["serial_detected_remaining_in_genre_roots"] = serial_remaining_after

    max_json_rows = 8000
    rows_json = csv_rows[:max_json_rows]
    payload: dict[str, Any] = {
        "ok": (not execute) or (len(errors) == 0),
        "mode": "execute" if execute else "dry-run",
        "library_root": str(library_root),
        "summary": summary,
        "rows": rows_json,
        "rows_total": len(csv_rows),
        "rows_json_truncated": len(csv_rows) > len(rows_json),
        "rows_full_csv": str(report_csv),
        "errors": errors,
        "report_csv": str(report_csv),
        "report_json": str(report_json),
        "first_100_serial": [{k: r[k] for k in fieldnames if k in r} for r in serial_rows[:100]],
        "first_100_uncertain": [{k: r[k] for k in fieldnames if k in r} for r in uncertain_rows[:100]],
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"clean-library-series: {'execute' if execute else 'dry-run'} library_root={library_root}\n"
        f"summary: genres_checked={summary['genres_checked']} txt_total={summary['txt_total']}\n"
        f"  serial={summary['serial_count']} probable_serial={summary['probable_serial_count']} "
        f"uncertain={summary['uncertain_count']} standalone_ok={summary['standalone_ok_count']}\n"
        f"  files_moved_to_series={summary['files_moved_to_series']} "
        f"skipped_already_in_series={summary['skipped_already_in_series']} "
        f"suffix_duplicate_resolved={summary['suffix_duplicate_resolved']} errors={summary['errors_count']}",
        flush=True,
    )
    if execute:
        print(
            f"  after_execute: genre_root_txt={summary.get('genre_root_txt_after_execute', 'n/a')} "
            f"serial_still_in_roots={summary.get('serial_detected_remaining_in_genre_roots', 'n/a')}",
            flush=True,
        )
    print(f"report_csv={report_csv}", flush=True)
    print(f"report_json={report_json}", flush=True)
    print("--- first 100 serial (planned or moved) ---", flush=True)
    for r in serial_rows[:100]:
        print(
            f"{r['genre']}\t{r['filename']}\t{r['decision']}\t{r['serial_reasons']}\t"
            f"{r['original_path']}\t->\t{r['target_path']}",
            flush=True,
        )
    print("--- first 100 uncertain ---", flush=True)
    for r in uncertain_rows[:100]:
        print(f"{r['genre']}\t{r['filename']}\t{r['serial_reasons']}\t{r['original_path']}", flush=True)

    return {
        "ok": payload["ok"],
        "summary": summary,
        "report_csv": str(report_csv),
        "report_json": str(report_json),
        "errors": errors,
    }
