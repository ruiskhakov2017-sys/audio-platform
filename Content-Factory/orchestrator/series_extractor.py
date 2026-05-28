from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


SERIES_MARKER_RE = re.compile(
    r"(?ix)"
    r"^(?P<title>.+?)\s*"
    r"(?:"
    r"ch\.?|chapter|part|pt\.?|episode|ep\.?|\#"
    r")\s*"
    r"(?P<number>\d{1,3})$"
)


@dataclass
class SeriesExtractorOptions:
    source_dir: Path
    execute: bool
    progress_every: int = 5000
    top_folders_limit: int | None = None
    only_folders: list[str] | None = None
    max_files_per_folder: int | None = None
    stop_after_files: int | None = None


def _safe_series_dir_name(title: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()
    safe = re.sub(r"\s+", " ", safe)
    safe = safe.rstrip(" .")
    return safe or "untitled_series"


def _parse_series_candidate(file_path: Path) -> tuple[str, int] | None:
    stem = file_path.stem.strip()
    match = SERIES_MARKER_RE.match(stem)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group("title").strip())
    number = int(match.group("number"))
    return title, number


def _safe_destination_path(dest: Path, src: Path) -> Path:
    if not dest.exists():
        return dest
    digest = hashlib.sha1(str(src.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:8]
    candidate = dest.with_name(f"{dest.stem}__{digest}{dest.suffix}")
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        fallback = dest.with_name(f"{dest.stem}__{digest}_{idx}{dest.suffix}")
        if not fallback.exists():
            return fallback
        idx += 1


def _compute_missing_part_numbers(part_numbers: list[int]) -> list[int]:
    if not part_numbers:
        return []
    uniq = sorted(set(part_numbers))
    missing: list[int] = []
    for n in range(uniq[0], uniq[-1] + 1):
        if n not in uniq:
            missing.append(n)
    return missing


def _compute_duplicate_part_numbers(part_numbers: list[int]) -> list[int]:
    counts: dict[int, int] = {}
    for n in part_numbers:
        counts[n] = counts.get(n, 0) + 1
    return sorted([n for n, c in counts.items() if c > 1])


def run_series_extraction(*, config: OrchestratorConfig, options: SeriesExtractorOptions) -> dict[str, Any]:
    source_dir = options.source_dir.resolve()
    reports_dir = config.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_json = reports_dir / "series_extraction_report.json"
    report_csv = reports_dir / "series_extraction_report.csv"

    if not source_dir.exists() or not source_dir.is_dir():
        return {"ok": False, "message": f"source_dir does not exist or is not a directory: {source_dir}"}

    started_at = time.perf_counter()
    progress_every = max(1, int(options.progress_every or 5000))
    stop_after = options.stop_after_files if options.stop_after_files and options.stop_after_files > 0 else None
    max_per_folder = options.max_files_per_folder if options.max_files_per_folder and options.max_files_per_folder > 0 else None
    only_folders_set = {x.strip() for x in (options.only_folders or []) if x and x.strip()}

    rows: list[dict[str, Any]] = []
    confirmed_series: list[dict[str, Any]] = []
    moved_count = 0
    single_candidates_moved = 0
    series_files_found = 0
    processed_files = 0
    processed_top_folders = 0
    skipped_due_to_limit = 0
    interrupted_or_limited = False

    all_top_folders = sorted([p for p in source_dir.iterdir() if p.is_dir() and p.name != "_series"], key=lambda x: x.name.lower())
    selected_top_folders: list[Path] = []
    for folder in all_top_folders:
        if only_folders_set and folder.name not in only_folders_set:
            continue
        selected_top_folders.append(folder)
    if options.top_folders_limit and options.top_folders_limit > 0:
        selected_top_folders = selected_top_folders[: int(options.top_folders_limit)]

    total_top_folders = len(selected_top_folders)

    def _print_progress(current_top: str) -> None:
        elapsed = max(0.0001, time.perf_counter() - started_at)
        speed = processed_files / elapsed
        print(
            "[extract-series] "
            f"folder='{current_top}' "
            f"top_folders={processed_top_folders}/{total_top_folders} "
            f"txt_scanned={processed_files} "
            f"confirmed_series={len(confirmed_series)} "
            f"series_files={series_files_found} "
            f"single_candidates={len([r for r in rows if r['classification']=='single_part_candidate'])} "
            f"elapsed_sec={elapsed:.1f} "
            f"files_per_sec={speed:.2f}",
            flush=True,
        )

    for top in selected_top_folders:
        if stop_after is not None and processed_files >= stop_after:
            interrupted_or_limited = True
            break

        txt_iter = sorted((p for p in top.iterdir() if p.is_file() and p.suffix.lower() == ".txt"), key=lambda x: x.name.lower())
        txt_files: list[Path] = []
        for p in txt_iter:
            if max_per_folder is not None and len(txt_files) >= max_per_folder:
                interrupted_or_limited = True
                skipped_due_to_limit += 1
                break
            if stop_after is not None and processed_files + len(txt_files) >= stop_after:
                interrupted_or_limited = True
                skipped_due_to_limit += 1
                break
            txt_files.append(p)

        candidates_by_title: dict[str, list[dict[str, Any]]] = {}
        for txt in txt_files:
            processed_files += 1
            if processed_files % progress_every == 0:
                _print_progress(top.name)

            parsed = _parse_series_candidate(txt)
            if not parsed:
                rows.append(
                    {
                        "top_folder": top.name,
                        "file_name": txt.name,
                        "classification": "normal_story",
                        "series_title": "",
                        "part_number": "",
                        "status": "kept",
                        "target_path": "",
                    }
                )
                continue
            series_title, part_number = parsed
            candidates_by_title.setdefault(series_title, []).append({"path": txt, "part_number": part_number})

        for series_title, members in sorted(candidates_by_title.items(), key=lambda x: x[0].lower()):
            if len(members) < 2:
                member = members[0]
                single_target_dir = top / "_series" / "_single_part_candidates"
                single_target = single_target_dir / member["path"].name
                final_single_target = _safe_destination_path(single_target, member["path"])
                status = "kept"
                if options.execute:
                    single_target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(member["path"]), str(final_single_target))
                    status = "moved_to_single_part_candidates"
                    single_candidates_moved += 1
                else:
                    status = "would_move_to_single_part_candidates"
                rows.append(
                    {
                        "top_folder": top.name,
                        "file_name": member["path"].name,
                        "classification": "single_part_candidate",
                        "series_title": series_title,
                        "part_number": member["part_number"],
                        "status": status,
                        "target_path": str(final_single_target),
                    }
                )
                continue

            part_numbers = [int(m["part_number"]) for m in members]
            uniq_sorted = sorted(set(part_numbers))
            min_part = uniq_sorted[0] if uniq_sorted else 0
            max_part = uniq_sorted[-1] if uniq_sorted else 0
            duplicate_part_numbers = _compute_duplicate_part_numbers(part_numbers)
            starts_after_1 = bool(min_part > 1) if uniq_sorted else False
            missing_from_1 = list(range(1, min_part)) if starts_after_1 else []
            missing_between_min_max = _compute_missing_part_numbers(part_numbers)
            safe_series_title = _safe_series_dir_name(series_title)
            target_series_dir = top / "_series" / safe_series_title
            series_files_found += len(members)

            confirmed_series.append(
                {
                    "top_folder": top.name,
                    "series_title": series_title,
                    "safe_series_title": safe_series_title,
                    "parts_found": sorted(part_numbers),
                    "parts_found_count": len(members),
                    "duplicate_part_numbers": duplicate_part_numbers,
                    "starts_after_1": starts_after_1,
                    "missing_from_1": missing_from_1,
                    "missing_between_min_max": missing_between_min_max,
                    "missing_part_numbers": missing_between_min_max,
                    "target_dir": str(target_series_dir),
                }
            )

            for member in sorted(members, key=lambda m: (int(m["part_number"]), m["path"].name.lower())):
                src_path = Path(member["path"])
                target_path = target_series_dir / src_path.name
                status = "would_move_to_series"
                if options.execute:
                    target_series_dir.mkdir(parents=True, exist_ok=True)
                    final_target_path = _safe_destination_path(target_path, src_path)
                    shutil.move(str(src_path), str(final_target_path))
                    target_path = final_target_path
                    status = "moved_to_series"
                    moved_count += 1
                rows.append(
                    {
                        "top_folder": top.name,
                        "file_name": src_path.name,
                        "classification": "confirmed_series_member",
                        "series_title": series_title,
                        "part_number": int(member["part_number"]),
                        "status": status,
                        "target_path": str(target_path),
                    }
                )

        processed_top_folders += 1
        _print_progress(top.name)

    fieldnames = ["top_folder", "file_name", "classification", "series_title", "part_number", "status", "target_path"]
    with report_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    elapsed_sec = max(0.0001, time.perf_counter() - started_at)
    partial_run = bool(
        (options.top_folders_limit and options.top_folders_limit > 0)
        or only_folders_set
        or (max_per_folder is not None)
        or (stop_after is not None)
    )

    payload = {
        "ok": True,
        "mode": "execute" if options.execute else "dry-run",
        "source_dir": str(source_dir),
        "partial_run": partial_run,
        "limits": {
            "top_folders_limit": options.top_folders_limit,
            "only_folder": sorted(only_folders_set),
            "max_files_per_folder": max_per_folder,
            "stop_after_files": stop_after,
        },
        "interrupted_or_limited": interrupted_or_limited,
        "processed_top_folders": processed_top_folders,
        "skipped_due_to_limit": skipped_due_to_limit,
        "summary": {
            "top_folders_scanned": total_top_folders,
            "txt_rows_total": processed_files,
            "confirmed_series_count": len(confirmed_series),
            "confirmed_series_member_rows": len([r for r in rows if r["classification"] == "confirmed_series_member"]),
            "series_files_found": series_files_found,
            "single_part_candidates": len([r for r in rows if r["classification"] == "single_part_candidate"]),
            "single_part_candidates_moved": single_candidates_moved,
            "normal_stories": len([r for r in rows if r["classification"] == "normal_story"]),
            "moved_count": moved_count,
            "moved_total": moved_count + single_candidates_moved,
            "elapsed_sec": round(elapsed_sec, 3),
            "files_per_sec": round(processed_files / elapsed_sec, 3),
        },
        "confirmed_series": confirmed_series,
        "report_csv": str(report_csv),
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "mode": payload["mode"],
        "summary": payload["summary"],
        "partial_run": payload["partial_run"],
        "interrupted_or_limited": payload["interrupted_or_limited"],
        "processed_top_folders": payload["processed_top_folders"],
        "skipped_due_to_limit": payload["skipped_due_to_limit"],
        "report_json": str(report_json),
        "report_csv": str(report_csv),
    }

