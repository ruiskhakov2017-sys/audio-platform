from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CleanupItem:
    path: Path
    item_type: str
    size_bytes: int
    reason: str
    group: str
    can_move_to_quarantine: bool = True

    def to_dict(self, root: Path) -> dict[str, Any]:
        rel = str(self.path.relative_to(root)) if self.path.is_absolute() else str(self.path)
        rel_cmd = rel.replace("\\", "/")
        return {
            "path": rel,
            "type": self.item_type,
            "size_bytes": self.size_bytes,
            "reason": self.reason,
            "group": self.group,
            "safe_to_quarantine": "yes" if self.can_move_to_quarantine else "no",
            "command_hint": (
                f'python -m orchestrator cleanup-move --root "." --paths "{rel_cmd}"'
                if self.can_move_to_quarantine
                else ""
            ),
        }


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    size = 0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        try:
            size += f.stat().st_size
        except OSError:
            continue
    return size


def _collect_known_artifacts(root: Path) -> list[CleanupItem]:
    candidates: list[tuple[Path, str, str]] = [
        (root / ".orchestrator" / "reports", "legacy stage outputs outside runs", "legacy_orchestrator_reports"),
        (root / ".orchestrator" / "status.jsonl", "orchestrator status log", "old_status_logs"),
        (root / ".orchestrator" / "events.jsonl", "orchestrator event log", "old_status_logs"),
        (root / "stories" / "_results", "legacy exported results", "legacy_story_results"),
        (root / "tmp_menu_input.txt", "temporary menu input file", "temp_files"),
    ]
    items: list[CleanupItem] = []
    for path, reason, group in candidates:
        if not path.exists():
            continue
        item_type = "file" if path.is_file() else "dir"
        items.append(
            CleanupItem(
                path=path,
                item_type=item_type,
                size_bytes=_dir_size(path),
                reason=reason,
                group=group,
            )
        )

    # runs/ never shown as a single removable root: only per run_id entries.
    runs_dir = root / "runs"
    if runs_dir.exists():
        for child in runs_dir.iterdir():
            if not child.is_dir():
                continue
            # runs/site and runs/youtube are containers; expose only concrete run_id folders.
            if child.name in {"site", "youtube"}:
                for run_dir in child.iterdir():
                    if not run_dir.is_dir():
                        continue
                    items.append(
                        CleanupItem(
                            path=run_dir,
                            item_type="dir",
                            size_bytes=_dir_size(run_dir),
                            reason="old run directory",
                            group="old_runs",
                        )
                    )
                continue
            items.append(
                CleanupItem(
                    path=child,
                    item_type="dir",
                    size_bytes=_dir_size(child),
                    reason="old run directory",
                    group="old_runs",
                )
            )

    # Never treat bundled weights / VCS as disposable scan targets
    _skip_root_names = {"models", ".git", ".cursor"}
    for child in root.iterdir():
        if child.name in _skip_root_names:
            continue
        if child.name.lower().startswith("tmp") and child.name not in {".tmp"}:
            items.append(
                CleanupItem(
                    path=child,
                    item_type="file" if child.is_file() else "dir",
                    size_bytes=_dir_size(child),
                    reason="temporary/generated artifact",
                    group="temp_files",
                )
            )
    dedup: dict[str, CleanupItem] = {}
    for item in items:
        dedup[str(item.path.resolve())] = item
    out = list(dedup.values())
    # If parent path exists in the set, hide nested children.
    abs_paths = [x.path.resolve() for x in out]
    filtered: list[CleanupItem] = []
    for item in out:
        current = item.path.resolve()
        has_parent = False
        for parent in abs_paths:
            if parent == current:
                continue
            try:
                current.relative_to(parent)
                has_parent = True
                break
            except ValueError:
                continue
        if not has_parent:
            filtered.append(item)
    out = filtered
    out.sort(key=lambda x: x.size_bytes, reverse=True)
    return out


def scan_generated_artifacts(root: Path) -> dict[str, Any]:
    items = _collect_known_artifacts(root)
    grouped: dict[str, list[dict[str, Any]]] = {
        "legacy_orchestrator_reports": [],
        "old_status_logs": [],
        "temp_files": [],
        "old_runs": [],
        "legacy_story_results": [],
    }
    for item in items:
        grouped.setdefault(item.group, []).append(item.to_dict(root))
    return {
        "root": str(root),
        "count": len(items),
        "items": [x.to_dict(root) for x in items],
        "groups": grouped,
    }


def move_items_to_quarantine(root: Path, relative_paths: list[str], timestamp: str | None = None) -> dict[str, Any]:
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    quarantine_root = root / "_quarantine_old_runs" / ts
    quarantine_root.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for rel in relative_paths:
        src = (root / rel).resolve()
        try:
            src.relative_to(root.resolve())
        except ValueError:
            skipped.append({"path": rel, "reason": "outside_workspace"})
            continue
        if not src.exists():
            skipped.append({"path": rel, "reason": "not_found"})
            continue
        dst = quarantine_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append({"from": rel, "to": str(dst.relative_to(root))})
    return {
        "ok": True,
        "quarantine_dir": str(quarantine_root),
        "moved": moved,
        "skipped": skipped,
    }


def move_run_to_quarantine(root: Path, run_id: str, timestamp: str | None = None) -> dict[str, Any]:
    return move_items_to_quarantine(root, [f"runs/{run_id}"], timestamp=timestamp)


def print_scan(scan: dict[str, Any]) -> None:
    print(json.dumps(scan, ensure_ascii=False, indent=2))

