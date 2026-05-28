from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


@dataclass
class LibrarySamplerOptions:
    source_dir: Path
    target_dir: Path
    per_folder: int
    execute: bool
    seed: str | None = None
    allow_nonempty_target: bool = False
    copy_mode: bool = False


def _hash8(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


def _safe_stem(stem: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in stem)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:120] if cleaned else "story"


def _resolve_unique_target(target_dir: Path, src_file: Path) -> tuple[Path, bool, str | None]:
    original_name = src_file.name
    candidate = target_dir / original_name
    if not candidate.exists():
        return candidate, False, None
    stem = _safe_stem(src_file.stem)
    suffix = src_file.suffix.lower()
    collisions = 1
    while True:
        collision_hash = _hash8(f"{src_file.resolve()}::{collisions}")
        alt = target_dir / f"{stem}__dup_{collision_hash}{suffix}"
        if not alt.exists():
            return alt, True, collision_hash
        collisions += 1


def run_library_sampler(*, config: OrchestratorConfig, options: LibrarySamplerOptions) -> dict[str, Any]:
    source_dir = options.source_dir.resolve()
    target_dir = options.target_dir.resolve()
    per_folder = max(0, int(options.per_folder))
    if per_folder <= 0:
        return {"ok": False, "message": "--per-folder must be > 0"}
    if not source_dir.exists() or not source_dir.is_dir():
        return {"ok": False, "message": f"source_dir does not exist or is not a directory: {source_dir}"}
    if options.execute:
        existing_txt = sorted([p.name for p in target_dir.glob("*.txt")]) if target_dir.exists() else []
        if existing_txt and not options.allow_nonempty_target:
            sample = existing_txt[:5]
            return {
                "ok": False,
                "message": (
                    "target-dir already contains .txt files; refusing to mix batches. "
                    "Archive or clean input first, or pass --allow-nonempty-target explicitly."
                ),
                "existing_txt_count": len(existing_txt),
                "existing_txt_examples": sample,
                "target_dir": str(target_dir),
            }

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    seed_used = str(options.seed).strip() if options.seed is not None and str(options.seed).strip() else batch_id
    rng_global = random.Random(seed_used)

    manifests_dir = (config.service_dir / "manifests").resolve()
    reports_dir = config.reports_dir.resolve()
    manifests_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    if options.execute:
        target_dir.mkdir(parents=True, exist_ok=True)

    # Basenames already in the queue (stories/input root *.txt): do not take the same story twice.
    reserved_names: set[str] = set()
    if target_dir.exists():
        for p in target_dir.glob("*.txt"):
            reserved_names.add(p.name.casefold())

    top_folders = sorted([p for p in source_dir.iterdir() if p.is_dir() and p.name != "_series"], key=lambda p: p.name.lower())

    rows: list[dict[str, Any]] = []
    selected_by_source_folder: dict[str, int] = {}
    skipped_queue_by_folder: dict[str, int] = {}
    renamed_due_to_collision_total = 0
    skipped_queue_total = 0
    errors: list[str] = []
    selected_total = 0
    moved_total = 0

    for top in top_folders:
        txt_files = sorted([p for p in top.iterdir() if p.is_file() and p.suffix.lower() == ".txt"], key=lambda p: p.name.lower())
        if not txt_files:
            selected_by_source_folder[top.name] = 0
            skipped_queue_by_folder[top.name] = 0
            continue

        eligible = [p for p in txt_files if p.name.casefold() not in reserved_names]
        skipped_here = len(txt_files) - len(eligible)
        skipped_queue_by_folder[top.name] = skipped_here
        skipped_queue_total += skipped_here

        folder_rng_seed = f"{seed_used}::{top.name}::{rng_global.randint(0, 2**31 - 1)}"
        folder_rng = random.Random(folder_rng_seed)
        files_pool = list(eligible)
        folder_rng.shuffle(files_pool)
        selected = files_pool[:per_folder]
        selected_by_source_folder[top.name] = len(selected)

        for src_file in selected:
            selected_total += 1
            target_path, renamed_due_to_collision, collision_hash = _resolve_unique_target(target_dir, src_file)
            if renamed_due_to_collision:
                renamed_due_to_collision_total += 1
            action = "would_copy" if options.copy_mode else "would_move"
            err_text = ""
            if options.execute:
                try:
                    if options.copy_mode:
                        shutil.copy2(src_file, target_path)
                    else:
                        src_file.rename(target_path)
                    moved_total += 1
                    action = "copied" if options.copy_mode else "moved"
                    reserved_names.add(target_path.name.casefold())
                except Exception as exc:
                    action = "error"
                    err_text = str(exc)
                    errors.append(f"{src_file} -> {target_path}: {exc}")
            else:
                # Dry-run: pretend basename is consumed so later folders match execute semantics.
                reserved_names.add(target_path.name.casefold())
            rows.append(
                {
                    "source_path_original": str(src_file),
                    "target_path": str(target_path),
                    "source_folder": top.name,
                    "original_filename": src_file.name,
                    "selected_filename": target_path.name,
                    "renamed_due_to_collision": renamed_due_to_collision,
                    "collision_hash": collision_hash,
                    "action": action,
                    "error": err_text,
                }
            )

    manifest_payload: dict[str, Any] = {
        "ok": True,
        "batch_id": batch_id,
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "per_folder": per_folder,
        "operation_mode": "copy" if options.copy_mode else "move",
        "seed": seed_used,
        "execute": bool(options.execute),
        "total_top_folders": len(top_folders),
        "total_selected": selected_total,
        "moved_total": moved_total,
        "selected_by_source_folder": selected_by_source_folder,
        "skipped_existing_target": renamed_due_to_collision_total,
        "renamed_due_to_collision_total": renamed_due_to_collision_total,
        "skipped_queue_basename_total": skipped_queue_total,
        "skipped_queue_by_folder": skipped_queue_by_folder,
        "errors": errors,
        "files": rows,
        "series_scan_excluded": True,
    }

    manifest_path = manifests_dir / f"library_sample_{batch_id}.json"
    report_csv_path = reports_dir / f"library_sample_{batch_id}.csv"
    target_manifest_path = target_dir / "_batch_manifest.json"

    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with report_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "source_path_original",
                "target_path",
                "source_folder",
                "original_filename",
                "selected_filename",
                "renamed_due_to_collision",
                "collision_hash",
                "action",
                "error",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    if options.execute:
        target_manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "batch_id": batch_id,
        "seed": seed_used,
        "execute": bool(options.execute),
        "top_folders_found": len(top_folders),
        "would_move_total": selected_total if not options.execute else 0,
        "moved_total": moved_total,
        "selected_by_source_folder": selected_by_source_folder,
        "skipped_existing_target": renamed_due_to_collision_total,
        "renamed_due_to_collision_total": renamed_due_to_collision_total,
        "skipped_queue_basename_total": skipped_queue_total,
        "skipped_queue_by_folder": skipped_queue_by_folder,
        "errors_count": len(errors),
        "manifest_path": str(manifest_path),
        "report_csv_path": str(report_csv_path),
        "target_manifest_path": str(target_manifest_path),
        "series_scan_excluded": True,
        "files": rows,
    }
