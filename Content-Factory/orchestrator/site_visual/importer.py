from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PREFERRED_EXT_ORDER = [".jpg", ".jpeg", ".png", ".webp"]


@dataclass(frozen=True)
class ImageCandidate:
    path: Path
    stem: str
    ext: str
    normalized_stem: str


def _safe_name(name: str) -> str:
    out = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    out = out.replace("\n", "_").replace("\r", "_")
    return out or "story"


def _norm_key(name: str) -> str:
    return _safe_name(name).strip().lower()


def _rank_ext(ext: str) -> int:
    e = (ext or "").strip().lower()
    try:
        return PREFERRED_EXT_ORDER.index(e)
    except ValueError:
        return len(PREFERRED_EXT_ORDER)


def _collect_story_dirs(site_root: Path) -> list[Path]:
    if not site_root.is_dir():
        return []
    return sorted([p for p in site_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())


def _collect_images(import_root: Path) -> list[ImageCandidate]:
    if not import_root.is_dir():
        return []
    out: list[ImageCandidate] = []
    for p in sorted([x for x in import_root.iterdir() if x.is_file()], key=lambda x: x.name.lower()):
        ext = p.suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTS:
            continue
        stem = p.stem
        out.append(
            ImageCandidate(
                path=p.resolve(),
                stem=stem,
                ext=ext,
                normalized_stem=_norm_key(stem),
            )
        )
    return out


def import_site_visuals(
    root_dir: Path,
    *,
    execute: bool = False,
    force: bool = False,
    import_dir: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    site_root = (root / "output" / "site").resolve()
    incoming = ((import_dir if import_dir.is_absolute() else (root / import_dir)).resolve() if import_dir else (root / "input" / "site_visual_import").resolve())
    report = (
        (report_path if report_path.is_absolute() else (root / report_path)).resolve()
        if report_path
        else (root / ".orchestrator" / "site_visual_import_report.json").resolve()
    )

    story_dirs = _collect_story_dirs(site_root)
    stories_by_norm: dict[str, list[Path]] = {}
    for d in story_dirs:
        stories_by_norm.setdefault(_norm_key(d.name), []).append(d)

    images = _collect_images(incoming)
    grouped: dict[str, list[ImageCandidate]] = {}
    for img in images:
        grouped.setdefault(img.normalized_stem, []).append(img)
    for key in grouped:
        grouped[key] = sorted(grouped[key], key=lambda x: (_rank_ext(x.ext), x.path.name.lower()))

    imported_count = 0
    already_exists_count = 0
    unmatched_images_count = 0
    duplicate_images_count = 0
    items: list[dict[str, Any]] = []
    image_done_by_story: dict[str, bool] = {d.name: (d / f"{d.name}.jpg").is_file() for d in story_dirs}

    for norm, group in sorted(grouped.items(), key=lambda kv: kv[0]):
        selected = group[0]
        extras = group[1:]
        for dup in extras:
            duplicate_images_count += 1
            items.append(
                {
                    "source_image_path": str(dup.path),
                    "matched_story_dir": "",
                    "dest_image_path": "",
                    "status": "duplicate_image",
                    "reason": f"duplicate_stem:{dup.stem}",
                }
            )

        matches = stories_by_norm.get(norm, [])
        if not matches:
            unmatched_images_count += 1
            items.append(
                {
                    "source_image_path": str(selected.path),
                    "matched_story_dir": "",
                    "dest_image_path": "",
                    "status": "unmatched",
                    "reason": f"story_not_found_for_image:{selected.stem}",
                }
            )
            continue
        if len(matches) > 1:
            unmatched_images_count += 1
            items.append(
                {
                    "source_image_path": str(selected.path),
                    "matched_story_dir": "",
                    "dest_image_path": "",
                    "status": "unmatched",
                    "reason": f"ambiguous_story_match:{selected.stem}",
                }
            )
            continue

        story_dir = matches[0]
        dst = (story_dir / f"{story_dir.name}.jpg").resolve()
        exists = dst.is_file()
        if exists and not force:
            already_exists_count += 1
            items.append(
                {
                    "source_image_path": str(selected.path),
                    "matched_story_dir": str(story_dir),
                    "dest_image_path": str(dst),
                    "status": "already_exists",
                    "reason": "destination_exists_use_force",
                }
            )
            continue

        if execute:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(selected.path, dst)
            imported_count += 1
            image_done_by_story[story_dir.name] = True
            st = "imported"
            rs = "copied"
        else:
            st = "planned_import"
            rs = "dry_run_no_copy"

        items.append(
            {
                "source_image_path": str(selected.path),
                "matched_story_dir": str(story_dir),
                "dest_image_path": str(dst),
                "status": st,
                "reason": rs,
            }
        )

    # Re-scan image status after execute branch
    story_image_status: list[dict[str, str]] = []
    missing_count = 0
    for d in story_dirs:
        final_cover = d / f"{d.name}.jpg"
        status = "image_done" if final_cover.is_file() else "missing_image"
        if status == "missing_image":
            missing_count += 1
        story_image_status.append(
            {
                "story_dir": str(d),
                "final_cover_path": str(final_cover),
                "status": status,
            }
        )

    payload = {
        "ok": True,
        "mode": "execute" if execute else "dry-run",
        "force": bool(force),
        "input_dir": str(incoming),
        "site_root": str(site_root),
        "imported_count": imported_count,
        "already_exists_count": already_exists_count,
        "missing_count": missing_count,
        "unmatched_images_count": unmatched_images_count,
        "duplicate_images_count": duplicate_images_count,
        "items": items,
        "story_image_status": story_image_status,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["report_path"] = str(report)
    return payload
