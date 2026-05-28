from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


@dataclass
class ArchiveInputOptions:
    input_dir: Path
    execute: bool


def run_archive_input(*, config: OrchestratorConfig, options: ArchiveInputOptions) -> dict[str, Any]:
    input_dir = options.input_dir.resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        return {"ok": False, "message": f"input_dir does not exist or is not a directory: {input_dir}"}

    txt_files = sorted([p for p in input_dir.glob("*.txt") if p.is_file()], key=lambda p: p.name.lower())
    batch_manifest = input_dir / "_batch_manifest.json"
    batch_manifest_exists = batch_manifest.exists() and batch_manifest.is_file()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = (config.root_dir / "archive" / "stories_input" / timestamp).resolve()
    planned: list[dict[str, str]] = []
    moved_total = 0
    errors: list[str] = []

    for src in txt_files:
        planned.append({"source_path": str(src), "target_path": str(archive_dir / src.name), "kind": "txt"})
    if batch_manifest_exists:
        planned.append(
            {
                "source_path": str(batch_manifest),
                "target_path": str(archive_dir / batch_manifest.name),
                "kind": "batch_manifest",
            }
        )

    if options.execute:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for row in planned:
            src = Path(row["source_path"])
            dst = Path(row["target_path"])
            try:
                src.rename(dst)
                moved_total += 1
            except Exception as exc:
                errors.append(f"{src} -> {dst}: {exc}")

        archived_manifest_path = archive_dir / "archived_files.json"
        archived_manifest_path.write_text(
            json.dumps(
                {
                    "input_dir": str(input_dir),
                    "archive_dir": str(archive_dir),
                    "timestamp": timestamp,
                    "moved_total": moved_total,
                    "errors": errors,
                    "files": planned,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        archived_manifest_path = archive_dir / "archived_files.json"

    return {
        "ok": True,
        "mode": "execute" if options.execute else "dry-run",
        "input_dir": str(input_dir),
        "archive_dir": str(archive_dir),
        "txt_count": len(txt_files),
        "batch_manifest_exists": batch_manifest_exists,
        "planned_total": len(planned),
        "moved_total": moved_total,
        "errors_count": len(errors),
        "errors": errors,
        "archived_manifest_path": str(archived_manifest_path),
        "planned": planned,
    }
