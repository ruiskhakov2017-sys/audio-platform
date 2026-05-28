from __future__ import annotations

import csv
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.series_extractor import _safe_destination_path


# --- Normalization: strip serial-ish blocks (order matters: tail first, then tokens, then junk). ---

_QUEUE_TAIL_RE = re.compile(r"(?i)_\d{5,}$")
_NUMERIC_RANGE_RE = re.compile(r"(?<![0-9])\d{2,}-\d{2,}(?![0-9])")
_CHAPTER_RE = re.compile(r"(?i)(?<![\w])(?:ch\.?|chapter|chap\.?)\s*\d{1,4}(?![\w0-9])")
_EPISODE_RE = re.compile(r"(?i)(?<![\w])(?:ep\.?|episode)\s*\d{1,4}(?![\w0-9])")
_PART_RE = re.compile(r"(?i)(?<![\w])(?:part|pt\.?)\s*\d{1,4}(?![\w0-9])")
_BOOK_RE = re.compile(r"(?i)(?<![\w])(?:book|bk\.?)\s*\d{1,4}(?![\w0-9])")
_VOL_RE = re.compile(r"(?i)(?<![\w])(?:vol\.?|volume)\s*\d{1,4}(?![\w0-9])")
_HASH_NUM_RE = re.compile(r"(?i)(?<![\w])\#\s*\d{1,4}(?![\w0-9])")

_STRIP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("queue_tail", _QUEUE_TAIL_RE),
    ("numeric_range", _NUMERIC_RANGE_RE),
    ("chapter", _CHAPTER_RE),
    ("episode", _EPISODE_RE),
    ("part", _PART_RE),
    ("book", _BOOK_RE),
    ("volume", _VOL_RE),
    ("hash_number", _HASH_NUM_RE),
)

_JUNK_EDGES_RE = re.compile(r"[\s.\-_]+")


def _collapse_junk(s: str) -> str:
    t = _JUNK_EDGES_RE.sub(" ", s).strip(" .-_")
    t = re.sub(r"\s+", " ", t, flags=re.UNICODE).strip()
    return t


def normalize_story_base_title(stem: str) -> tuple[str, list[str]]:
    """
    Build grouping key: strip serial markers / ranges / queue tail, collapse junk, lowercase.
    Returns (normalized_base_title, list of strip rule names applied at least once).
    """
    applied: list[str] = []
    current = stem.strip()
    changed = True
    guard = 0
    while changed and guard < 48:
        changed = False
        guard += 1
        for name, rx in _STRIP_RULES:
            new_s, n = rx.subn(" ", current)
            if n:
                applied.append(name)
                current = new_s
                changed = True
    collapsed = _collapse_junk(current)
    seen: set[str] = set()
    applied_unique: list[str] = []
    for name in applied:
        if name not in seen:
            seen.add(name)
            applied_unique.append(name)
    return (collapsed.casefold(), applied_unique)


def stem_serial_signal(stem: str) -> tuple[bool, str]:
    """
    Есть ли в stem признаки серии (для return-series-from-input).
    Сначала расширенные маркеры (day/page/s01e01/…) из series_title_audit_all, затем legacy Ch/Ep/Part.
    """
    try:
        from orchestrator.series_title_audit_all import extended_series_markers

        ok, tags = extended_series_markers(stem)
        if ok:
            return True, (tags[0] if tags else "extended")
    except Exception:
        pass
    return stem_has_explicit_serial_marker(stem)


def stem_has_explicit_serial_marker(stem: str) -> tuple[bool, str]:
    """
    True if stem (after removing queue tail only) still matches chapter/episode/… markers.
    Queue tail alone never counts (rule 7).
    """
    wo_tail = _QUEUE_TAIL_RE.sub("", stem.strip()).strip()
    checks: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("numeric_range", _NUMERIC_RANGE_RE),
        ("chapter", _CHAPTER_RE),
        ("episode", _EPISODE_RE),
        ("part", _PART_RE),
        ("book", _BOOK_RE),
        ("volume", _VOL_RE),
        ("hash_number", _HASH_NUM_RE),
    )
    for label, rx in checks:
        if rx.search(wo_tail):
            return True, label
    return False, ""


def _format_matched_pattern(strip_tags: list[str], explicit_tag: str, group_serial: bool) -> str:
    parts: list[str] = []
    if strip_tags:
        parts.append("strip:" + "+".join(strip_tags))
    if explicit_tag:
        parts.append(f"explicit:{explicit_tag}")
    if group_serial:
        parts.append("group_serial")
    return "|".join(parts) if parts else "none"


def load_original_source_paths(batch_manifest: Path) -> dict[str, Path]:
    """
    Map queue basename (casefold) -> original source file path from _batch_manifest.json.
    Accepts source_path_original or original_source_path; keys selected_filename / target_path basename.
    """
    out: dict[str, Path] = {}
    if not batch_manifest.is_file():
        return out
    try:
        data = json.loads(batch_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    rows = data.get("files")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        orig_raw = row.get("original_source_path") or row.get("source_path_original")
        if not orig_raw:
            continue
        orig = Path(str(orig_raw))
        sel = row.get("selected_filename") or row.get("original_filename")
        keys: list[str] = []
        if isinstance(sel, str) and sel.strip():
            keys.append(Path(sel.strip()).name.casefold())
        tp = row.get("target_path")
        if isinstance(tp, str) and tp.strip():
            keys.append(Path(tp.strip()).name.casefold())
        for k in keys:
            if k:
                out[k] = orig
    return out


@dataclass
class StoriesInputSeriesReturnOptions:
    input_dir: Path
    execute: bool


def run_stories_input_series_return(*, config: OrchestratorConfig, options: StoriesInputSeriesReturnOptions) -> dict[str, Any]:
    input_dir = options.input_dir.resolve()
    reports_dir = config.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_csv = reports_dir / "stories_input_series_return.csv"
    report_json = reports_dir / "stories_input_series_return.json"

    if not input_dir.exists() or not input_dir.is_dir():
        return {"ok": False, "message": f"input_dir does not exist or is not a directory: {input_dir}"}

    unknown_dir = (input_dir.parent / "_series_return_unknown").resolve()
    manifest_path = input_dir / "_batch_manifest.json"
    orig_map = load_original_source_paths(manifest_path)

    txt_files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt"], key=lambda p: p.name.lower())
    started = time.perf_counter()

    groups: dict[str, list[Path]] = {}
    meta_by_path: dict[str, dict[str, Any]] = {}

    for p in txt_files:
        stem = p.stem
        norm, strip_tags = normalize_story_base_title(stem)
        explicit, explicit_tag = stem_serial_signal(stem)
        groups.setdefault(norm, []).append(p)
        meta_by_path[str(p.resolve())] = {
            "normalized_base_title": norm,
            "strip_tags": strip_tags,
            "explicit": explicit,
            "explicit_tag": explicit_tag,
        }

    rows_out: list[dict[str, Any]] = []
    moved = 0

    for p in txt_files:
        key = str(p.resolve())
        meta = meta_by_path[key]
        norm = meta["normalized_base_title"]
        strip_tags: list[str] = meta["strip_tags"]
        explicit = bool(meta["explicit"])
        explicit_tag = str(meta["explicit_tag"])
        g = groups.get(norm, [])
        group_size = len(g)
        group_has_marker = any(stem_serial_signal(x.stem)[0] for x in g)
        is_serial = (group_size > 1 and group_has_marker) or (group_size == 1 and explicit)
        group_serial_flag = bool(group_size > 1 and group_has_marker)
        matched_pattern = _format_matched_pattern(strip_tags, explicit_tag if explicit else "", group_serial_flag)

        if not is_serial:
            rows_out.append(
                {
                    "filename": p.name,
                    "normalized_base_title": norm,
                    "matched_pattern": matched_pattern,
                    "group_size": group_size,
                    "decision": "keep_non_serial",
                    "planned_target": "",
                    "_src_path": str(p.resolve()),
                }
            )
            continue

        orig = orig_map.get(p.name.casefold())
        if orig is not None and orig.parent.is_dir():
            dest = orig
            decision = "move_serial_to_original"
        else:
            dest = unknown_dir / p.name
            decision = "move_serial_unknown_destination"

        final_dest = _safe_destination_path(dest, p)
        planned = str(final_dest)

        rows_out.append(
            {
                "filename": p.name,
                "normalized_base_title": norm,
                "matched_pattern": matched_pattern,
                "group_size": group_size,
                "decision": decision,
                "planned_target": planned,
                "_src_path": str(p.resolve()),
            }
        )

    fieldnames = ["filename", "normalized_base_title", "matched_pattern", "group_size", "decision", "planned_target"]
    csv_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows_out]
    with report_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(csv_rows)

    errors: list[str] = []
    if options.execute:
        unknown_dir.mkdir(parents=True, exist_ok=True)
        for row in rows_out:
            if not str(row.get("decision", "")).startswith("move_serial"):
                continue
            src = Path(str(row["_src_path"]))
            dst = Path(str(row["planned_target"]))
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                final_dst = _safe_destination_path(dst, src)
                if final_dst.resolve() == src.resolve():
                    continue
                shutil.move(str(src), str(final_dst))
                moved += 1
            except Exception as exc:
                errors.append(f"{src} -> {dst}: {exc}")

    elapsed = max(0.0001, time.perf_counter() - started)
    serial_rows = [r for r in rows_out if r["decision"].startswith("move_serial")]
    payload: dict[str, Any] = {
        "ok": True,
        "mode": "execute" if options.execute else "dry-run",
        "input_dir": str(input_dir),
        "unknown_dir": str(unknown_dir),
        "manifest_path": str(manifest_path),
        "manifest_loaded": bool(orig_map),
        "elapsed_sec": round(elapsed, 4),
        "summary": {
            "txt_total": len(txt_files),
            "serial_planned": len(serial_rows),
            "keep_non_serial": len([r for r in rows_out if r["decision"] == "keep_non_serial"]),
            "moved_count": moved,
            "errors_count": len(errors),
        },
        "rows": csv_rows,
        "errors": errors,
        "report_csv": str(report_csv),
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not options.execute:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
        print(
            f"{'filename':<56} {'normalized_base_title':<36} {'matched_pattern':<40} "
            f"{'group':>5} {'decision':<32} {'planned_target'}"
        )
        print("-" * 200)
        for r in rows_out:
            print(
                f"{r['filename']:<56} {r['normalized_base_title']:<36} {r['matched_pattern']:<40} "
                f"{r['group_size']:>5} {r['decision']:<32} {r['planned_target']}"
            )

    return {
        "ok": True,
        "mode": payload["mode"],
        "summary": payload["summary"],
        "report_csv": str(report_csv),
        "report_json": str(report_json),
        "errors": errors,
    }
