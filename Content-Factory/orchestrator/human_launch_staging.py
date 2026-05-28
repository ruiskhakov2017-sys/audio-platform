"""
Staging входных .txt для безопасного phase-a (без скана всей библиотеки).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from orchestrator.human_launch_layout import D10_STAGING_TEST_INPUT, D10_TEMP, now_iso, write_json


def list_sorted_story_txt(stories_dir: Path) -> list[Path]:
    if not stories_dir.is_dir():
        return []
    out = [p for p in stories_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
    return sorted(out, key=lambda x: x.name.lower())


def staging_test_input_dir(launch: Path) -> Path:
    return (launch / D10_TEMP / D10_STAGING_TEST_INPUT).resolve()


def prepare_staging_test_input(
    launch: Path,
    source_stories_dir: Path,
    limit: int,
    *,
    execute: bool,
    name_suffix: str = "",
) -> dict[str, Any]:
    """
    Копирует первые ``limit`` .txt из source в Запуски/<name>/10_Временные_файлы/test_input/.
    """
    sd = source_stories_dir.resolve()
    if not sd.is_dir():
        return {"ok": False, "message": f"stories-dir not found: {sd}"}
    if limit <= 0:
        return {"ok": False, "message": "limit must be > 0 for staging"}
    txts = list_sorted_story_txt(sd)[:limit]
    dest_root = staging_test_input_dir(launch)
    meta_path = launch / D10_TEMP / "test_input_manifest.json"
    payload: dict[str, Any] = {
        "created_at": now_iso(),
        "source_stories_dir": str(sd),
        "limit": limit,
        "files_copied": [p.name for p in txts],
        "staging_dir": str(dest_root),
        "name_suffix": name_suffix,
    }
    if not txts:
        payload["ok"] = False
        payload["message"] = f"no .txt in {sd}"
        if execute:
            (launch / D10_TEMP).mkdir(parents=True, exist_ok=True)
            write_json(meta_path, payload)
        return {"ok": False, "message": payload["message"], **payload}

    if not execute:
        return {
            "ok": True,
            "dry_run": True,
            "staging_dir": str(dest_root),
            "file_count": len(txts),
            "files": [str(p) for p in txts],
        }

    (launch / D10_TEMP).mkdir(parents=True, exist_ok=True)
    if dest_root.exists():
        shutil.rmtree(dest_root, ignore_errors=True)
    dest_root.mkdir(parents=True, exist_ok=True)
    mapped: list[dict[str, str]] = []
    for p in txts:
        staged_name = p.name
        if name_suffix:
            staged_name = f"{p.stem}{name_suffix}{p.suffix}"
        shutil.copy2(p, dest_root / staged_name)
        mapped.append({"original_name": p.name, "staged_name": staged_name})
    payload["ok"] = True
    payload["name_mapping"] = mapped
    write_json(meta_path, payload)
    return {
        "ok": True,
        "dry_run": False,
        "staging_dir": str(dest_root),
        "file_count": len(txts),
        "files": [str(p) for p in txts],
        "manifest_path": str(meta_path),
        "name_mapping": mapped,
    }
