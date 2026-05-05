from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from orchestrator.config import OrchestratorConfig
from orchestrator.events import EventLogger
from orchestrator.status import StatusStore


WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)


@dataclass
class LengthFilterOptions:
    stories_dir: Path
    short_dir: Path | None
    execute: bool
    words_per_minute: int
    min_minutes: float
    extensions: list[str]
    # When set, CSV/JSON artifacts are written here instead of global .orchestrator/reports.
    artifacts_dir: Path | None = None
    # When True, only stories_dir/*.txt at directory root (matches Phase A intake).
    root_txt_intake_only: bool = False


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _is_under(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_destination(dest: Path) -> Path:
    if not dest.exists():
        return dest
    idx = 1
    while True:
        cand = dest.with_name(f"{dest.stem}_dup{idx}{dest.suffix}")
        if not cand.exists():
            return cand
        idx += 1


def run_length_filter(
    *,
    config: OrchestratorConfig,
    options: LengthFilterOptions,
    pipeline: str = "pre_filter",
    story_id: str = "batch",
) -> Dict[str, Any]:
    stories_dir = options.stories_dir.resolve()
    short_dir_note = ""
    if options.short_dir is not None:
        short_dir = options.short_dir.resolve()
    else:
        preferred = (stories_dir.parent / "short_under_15m").resolve()
        if preferred.exists() and preferred.is_file():
            fallback = (stories_dir / "short_under_15m").resolve()
            short_dir = fallback
            short_dir_note = (
                f"default short dir '{preferred}' is a file, fallback to '{fallback}'"
            )
        else:
            short_dir = preferred
    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in options.extensions}
    artifacts_root = options.artifacts_dir if options.artifacts_dir is not None else config.reports_dir
    artifacts_root.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_root / "length_filter_report.csv"
    manifest_path = artifacts_root / "length_filter_manifest.json"

    logger = EventLogger(config.events_file)
    status = StatusStore(config.status_file)
    stage = "length_filter"

    status.append(
        story_id=story_id,
        pipeline=pipeline,
        stage=stage,
        state="running",
        message="length filter started",
    )

    if not stories_dir.exists() or not stories_dir.is_dir():
        msg = f"stories_dir does not exist or is not a directory: {stories_dir}"
        status.append(
            story_id=story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=msg,
        )
        logger.emit(
            run_id="length-filter",
            story_id=story_id,
            pipeline=pipeline,
            stage=stage,
            action="precheck",
            result="failed",
            message=msg,
        )
        return {"ok": False, "message": msg}

    if options.execute:
        if short_dir.exists() and short_dir.is_file():
            msg = (
                f"short_dir points to file, not directory: {short_dir}. "
                "Use --short-dir with a valid folder path."
            )
            status.append(
                story_id=story_id,
                pipeline=pipeline,
                stage=stage,
                state="failed",
                message=msg,
            )
            logger.emit(
                run_id="length-filter",
                story_id=story_id,
                pipeline=pipeline,
                stage=stage,
                action="precheck",
                result="failed",
                message=msg,
            )
            return {"ok": False, "message": msg}
        short_dir.mkdir(parents=True, exist_ok=True)

    files: List[Path] = []
    if options.root_txt_intake_only:
        for path in sorted(stories_dir.iterdir(), key=lambda x: x.name.lower()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ext_set:
                continue
            if _is_under(path, short_dir):
                continue
            files.append(path)
    else:
        for path in stories_dir.rglob("*"):
            if not path.is_file():
                continue
            if "short_under_15m" in path.parts:
                continue
            if _is_under(path, short_dir):
                continue
            if path.suffix.lower() not in ext_set:
                continue
            files.append(path)

    rows: List[Dict[str, Any]] = []
    planned_moves: List[Dict[str, str]] = []
    kept = 0
    moved = 0
    errors = 0

    for src in files:
        try:
            text = _read_text(src)
            char_count = len(text)
            word_count = len(WORD_RE.findall(text))
            estimated_minutes = round(word_count / options.words_per_minute, 2)
            is_short = estimated_minutes < options.min_minutes

            result = "kept"
            if is_short:
                relative = src.relative_to(stories_dir)
                target = _safe_destination(short_dir / relative)
                planned_moves.append(
                    {
                        "source_path": str(src),
                        "target_path": str(target),
                    }
                )
                if options.execute:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        src.replace(target)
                    except OSError:
                        shutil.move(str(src), str(target))
                    result = "moved_to_short"
                    moved += 1
                else:
                    result = "would_move_to_short"
            else:
                kept += 1

            rows.append(
                {
                    "source_path": str(src),
                    "file_name": src.name,
                    "char_count": char_count,
                    "word_count": word_count,
                    "estimated_minutes": f"{estimated_minutes:.2f}",
                    "result": result,
                }
            )
        except Exception as exc:
            errors += 1
            rows.append(
                {
                    "source_path": str(src),
                    "file_name": src.name,
                    "char_count": "",
                    "word_count": "",
                    "estimated_minutes": "",
                    "result": f"error: {exc}",
                }
            )

    fieldnames = [
        "source_path",
        "file_name",
        "char_count",
        "word_count",
        "estimated_minutes",
        "result",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    manifest = {
        "formula": "estimated_minutes = word_count / words_per_minute",
        "words_per_minute": options.words_per_minute,
        "threshold_minutes": options.min_minutes,
        "execute": options.execute,
        "stories_dir": str(stories_dir),
        "short_dir": str(short_dir),
        "short_dir_note": short_dir_note,
        "processed_files": len(files),
        "kept_count": kept,
        "moved_count": moved,
        "errors_count": errors,
        "planned_moves": planned_moves,
        "report_path": str(report_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = (
        f"formula=word_count/{options.words_per_minute}; threshold={options.min_minutes}m; "
        f"total={len(files)} kept={kept} moved={moved} errors={errors}; "
        f"report={report_path}"
    )
    if short_dir_note:
        summary = f"{summary}; note={short_dir_note}"
    final_state = "succeeded" if errors == 0 else "failed"
    status.append(
        story_id=story_id,
        pipeline=pipeline,
        stage=stage,
        state=final_state,
        message=summary,
    )
    logger.emit(
        run_id="length-filter",
        story_id=story_id,
        pipeline=pipeline,
        stage=stage,
        action="finish",
        result=final_state,
        message=summary,
        payload={
            "report_path": str(report_path),
            "manifest_path": str(manifest_path),
            "total": len(files),
            "kept": kept,
            "moved": moved,
            "errors": errors,
        },
    )
    return {
        "ok": errors == 0,
        "summary": summary,
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
    }
