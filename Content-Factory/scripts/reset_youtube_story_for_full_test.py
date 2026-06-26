#!/usr/bin/env python3
"""Move one YouTube story's generated artifacts aside and reset its queue row."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.config import load_config
from orchestrator.youtube_full_auto.layout import batch_launch_root, safe_slug
from orchestrator.youtube_full_auto.queue_store import QueueStore, StoryQueueItem


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset one YouTube story for a clean full-cycle test.")
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--story", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--skip-global-output", action="store_true")
    return parser.parse_args()


def story_match_keys(item: StoryQueueItem) -> set[str]:
    title = str(item.canonical_basename or "").strip()
    story_key = str(item.story_key or "").strip()
    cleaned_stem = Path(str(item.cleaned_path or "")).stem
    return {
        title.casefold(),
        story_key.casefold(),
        safe_slug(title).casefold(),
        cleaned_stem.casefold(),
        safe_slug(cleaned_stem).casefold(),
    }


def find_story(store: QueueStore, story: str) -> StoryQueueItem:
    target = str(story or "").strip()
    keys = {target.casefold(), safe_slug(target).casefold()}
    matches = [item for item in store.items() if story_match_keys(item) & keys]
    if not matches:
        raise RuntimeError(f"story not found in queue: {story}")
    if len(matches) > 1:
        titles = ", ".join(item.canonical_basename for item in matches[:10])
        raise RuntimeError(f"story matched multiple queue rows: {story}: {titles}")
    return matches[0]


def unique_existing(paths: list[Path]) -> list[Path]:
    existing: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if not path.exists() or resolved in seen:
            continue
        seen.add(resolved)
        existing.append(path)
    parents = []
    for path in sorted(existing, key=lambda p: len(p.parts)):
        if any(path != parent and path.resolve().is_relative_to(parent.resolve()) for parent in parents):
            continue
        parents.append(path)
    return parents


def collect_matching_dirs(root: Path, names: set[str]) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    lowered = {name.casefold() for name in names if name}
    for path in root.rglob("*"):
        if path.is_dir() and path.name.casefold() in lowered:
            out.append(path)
    return out


def quarantine_destination(quarantine_root: Path, source: Path) -> Path:
    try:
        rel = source.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        rel = Path(source.name)
    dest = quarantine_root / rel
    if not dest.exists():
        return dest
    suffix = 1
    while True:
        candidate = dest.with_name(f"{dest.name}.{suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def move_to_quarantine(source: Path, quarantine_root: Path, *, execute: bool) -> str:
    dest = quarantine_destination(quarantine_root, source)
    if not execute:
        return f"MOVE_PLAN source={source} dest={dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return f"MOVED source={source} dest={dest}"


def main() -> int:
    args = parse_args()
    config = load_config()
    launch_root = batch_launch_root(config, args.launch_id)
    if not launch_root.is_dir():
        raise FileNotFoundError(f"launch not found: {launch_root}")

    store = QueueStore(launch_root, config=config, youtube_run_id=args.launch_id)
    item = find_story(store, args.story)
    title = str(item.canonical_basename or args.story).strip()
    slug = safe_slug(title)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    quarantine_root = launch_root / "10_Временные_файлы" / "story_reset_quarantine" / stamp / slug

    names = {slug, title, str(item.story_key or ""), Path(str(item.cleaned_path or "")).stem}
    candidates: list[Path] = [
        launch_root / "03_youtube" / slug,
        launch_root / "gemini_stage_markers" / slug,
        launch_root / "10_Временные_файлы" / "runpod_autonomous_upload" / f"{slug}.tar",
        launch_root / "10_Временные_файлы" / "runpod_autonomous_upload" / f"{slug}.tar.tmp",
    ]
    temp_visuals = launch_root / "10_Временные_файлы" / "visuals_gemini_batch"
    candidates.extend(collect_matching_dirs(temp_visuals, names))

    persistent_root = launch_root / "gemini_persistent"
    for path in collect_matching_dirs(persistent_root, names):
        if "trash" not in {part.casefold() for part in path.parts}:
            candidates.append(path)

    if not args.skip_global_output:
        candidates.extend(
            [
                PROJECT_ROOT / "output" / "youtube" / title,
                PROJECT_ROOT / "output" / "youtube" / slug,
            ]
        )

    move_paths = unique_existing(candidates)
    print(
        "RESET_STORY_PLAN "
        f"launch_id={args.launch_id} story={title} slug={slug} "
        f"execute={str(bool(args.execute)).lower()} moves={len(move_paths)}",
        flush=True,
    )

    if args.execute:
        queue_backup = quarantine_root / "queue" / "youtube_batch_queue.before_reset.json"
        queue_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(store.paths["queue_json"], queue_backup)
        item.status = "selection_pending"
        item.stages = {}
        item.retry_count = 0
        item.last_error = ""
        item.last_worker = ""
        item.last_account_index = -1
        item.output_story_dir = ""
        store.upsert(item)
        store.save()
        store.write_stage_status_snapshot()
        print(f"QUEUE_RESET story={title} backup={queue_backup}", flush=True)
    else:
        print("QUEUE_RESET_PLAN status=selection_pending stages=cleared", flush=True)

    manifest = {
        "launch_id": args.launch_id,
        "story": title,
        "slug": slug,
        "execute": bool(args.execute),
        "moves": [str(path) for path in move_paths],
    }
    if args.execute:
        quarantine_root.mkdir(parents=True, exist_ok=True)
        (quarantine_root / "reset_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    for path in move_paths:
        print(move_to_quarantine(path, quarantine_root, execute=bool(args.execute)), flush=True)

    print(
        "RESET_STORY_DONE "
        f"launch_id={args.launch_id} story={title} slug={slug} execute={str(bool(args.execute)).lower()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
