from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from orchestrator.site_publish.paths import (
    describe_layout,
    is_run_scoped,
    resolve_launch_dir,
    resolve_site_publish_root,
    resolve_to_publish_root,
    site_publish_manifest_path,
)
from orchestrator.site_visual.importer import import_site_visuals


SUPPORTED_AUDIO_EXTS = (".mp3", ".m4a", ".wav")
SUPPORTED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _sanitize_folder_name(name: str) -> str:
    out = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    out = out.replace("\n", "_").replace("\r", "_")
    return out or "story"


def _pick_first_by_ext(files: Iterable[Path], preferred_exts: tuple[str, ...]) -> Path | None:
    allowed = {e.strip().lower() for e in preferred_exts if e and e.strip()}
    items = [p for p in files if p.is_file() and p.suffix.lower() in allowed]
    if not items:
        return None
    items_sorted = sorted(items, key=lambda p: (p.suffix.lower(), p.name.lower()))
    by_ext: dict[str, list[Path]] = {}
    for p in items_sorted:
        by_ext.setdefault(p.suffix.lower(), []).append(p)
    for ext in preferred_exts:
        hit = by_ext.get((ext or "").strip().lower())
        if hit:
            return hit[0]
    return items_sorted[0]


def _find_info(story_dir: Path) -> Path | None:
    p = story_dir / "info.txt"
    return p if p.is_file() else None


def _find_audio(story_dir: Path) -> Path | None:
    return _pick_first_by_ext(story_dir.iterdir(), SUPPORTED_AUDIO_EXTS)


def _find_image(story_dir: Path) -> Path | None:
    return _pick_first_by_ext(story_dir.iterdir(), SUPPORTED_IMAGE_EXTS)


def _find_text(story_dir: Path) -> Path | None:
    txts = [p for p in story_dir.glob("*.txt") if p.is_file() and p.name.lower() != "info.txt"]
    if not txts:
        return None
    preferred_names = [
        f"{story_dir.name}__M.txt",
        f"{story_dir.name}__F.txt",
        f"{story_dir.name}__U.txt",
    ]
    by_lower = {p.name.lower(): p for p in txts}
    for nm in preferred_names:
        hit = by_lower.get(nm.lower())
        if hit:
            return hit
    return sorted(txts, key=lambda p: p.name.lower())[0]


@dataclass
class StoryCheck:
    story_id: str
    source_dir: Path
    to_publish_dir: Path
    info_path: Path | None
    audio_path: Path | None
    image_path: Path | None
    text_path: Path | None
    missing: list[str]


def _collect_story_dirs(site_root: Path, *, story_name: str | None) -> list[Path]:
    if story_name:
        p = (site_root / story_name).resolve()
        return [p] if p.is_dir() else []
    if not site_root.is_dir():
        return []
    return sorted([p for p in site_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())


def _check_story(story_dir: Path, to_publish_root: Path) -> StoryCheck:
    info = _find_info(story_dir)
    audio = _find_audio(story_dir)
    image = _find_image(story_dir)
    text = _find_text(story_dir)
    missing: list[str] = []
    if info is None:
        missing.append("info")
    if audio is None:
        missing.append("audio")
    if image is None:
        missing.append("image")
    if text is None:
        missing.append("text")
    story_id = story_dir.name
    to_publish_dir = (to_publish_root / _sanitize_folder_name(story_id)).resolve()
    return StoryCheck(
        story_id=story_id,
        source_dir=story_dir.resolve(),
        to_publish_dir=to_publish_dir,
        info_path=info.resolve() if info else None,
        audio_path=audio.resolve() if audio else None,
        image_path=image.resolve() if image else None,
        text_path=text.resolve() if text else None,
        missing=missing,
    )


def prepare_site_publish(
    root_dir: Path,
    *,
    execute: bool = False,
    force: bool = False,
    story: str = "",
    allow_partial_tts: bool = False,
    report_path: Path | None = None,
    launch_name: str = "",
    launch_dir: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    service_dir = (root / ".orchestrator").resolve()
    report = (
        (report_path if report_path.is_absolute() else (root / report_path)).resolve()
        if report_path
        else (service_dir / "site_publish_prepare_report.json").resolve()
    )

    run_scoped_requested = is_run_scoped(launch_name=launch_name, launch_dir=launch_dir)
    launch = resolve_launch_dir(root, launch_name=launch_name, launch_dir=launch_dir)
    layout_info = describe_layout(root, launch_name=launch_name, launch_dir=launch_dir)
    manifest_path = site_publish_manifest_path(root, launch)

    if run_scoped_requested and launch is None:
        payload_err: dict[str, Any] = {
            "ok": False,
            "mode": "execute" if execute else "dry-run",
            "force": bool(force),
            "allow_partial_tts": bool(allow_partial_tts),
            "story": (story or "").strip(),
            "reason": "launch_not_found_for_run_scoped_request",
            "launch_name": (launch_name or "").strip(),
            "layout": layout_info,
            "report_path": str(report),
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload_err, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload_err

    # Step 1: visual import bridge (before readiness checks).
    # Legacy bridge всё ещё работает с output/site и launch-обложками; safe для run-scoped.
    _ = import_site_visuals(root, execute=execute, force=force)

    site_root = resolve_site_publish_root(root, launch)
    to_publish_root = resolve_to_publish_root(root, launch)
    to_publish_root.mkdir(parents=True, exist_ok=True)

    story_name = (story or "").strip()
    story_dirs = _collect_story_dirs(site_root, story_name=(story_name or None))
    terminal_tts_by_story: dict[str, dict[str, Any]] = {}
    terminal_manual_skipped: list[str] = []
    terminal_failed: list[str] = []
    try:
        from orchestrator.site_tts.colab_batch import _drive_dir_from, _load_expected_files, _read_colab_status, _read_manual_skipped, _split_story_voice
        from orchestrator.site_tts.config import load_site_tts_settings

        tts_settings = load_site_tts_settings(root)
        job_dir = _drive_dir_from(root, tts_settings, "job", "job")
        expected_files = _load_expected_files(job_dir)
        manual_marker = _read_manual_skipped(job_dir)
        colab_status = _read_colab_status(job_dir)
        file_status = colab_status.get("file_status") if isinstance(colab_status.get("file_status"), dict) else {}
        colab_done = (job_dir / "COLAB_DONE.txt").is_file()
        for mp3_name in expected_files:
            story_part, _voice = _split_story_voice(Path(mp3_name).stem)
            story_key = _sanitize_folder_name(story_part)
            if mp3_name in manual_marker:
                terminal_manual_skipped.append(mp3_name)
                terminal_tts_by_story[story_key] = {
                    "status": "manual_skipped",
                    "expected_mp3_name": mp3_name,
                    "reason": str(manual_marker[mp3_name].get("reason") or "manual_skip"),
                    "can_retry_later": True,
                }
                continue
            if colab_done and str(file_status.get(mp3_name, "") or "").strip() == "failed":
                terminal_failed.append(mp3_name)
                terminal_tts_by_story[story_key] = {
                    "status": "terminal_failed",
                    "expected_mp3_name": mp3_name,
                    "reason": "colab_failed_terminal",
                    "can_retry_later": True,
                }
    except Exception:
        terminal_tts_by_story = {}
        terminal_manual_skipped = []
        terminal_failed = []

    total_stories = len(story_dirs)
    ready_count = 0
    skipped_count = 0
    prepared_count = 0
    missing_audio_count = 0
    missing_image_count = 0
    missing_info_count = 0
    missing_text_count = 0

    items: list[dict[str, Any]] = []

    for story_dir in story_dirs:
        story_id = story_dir.name
        base: dict[str, Any] = {
            "story_id": story_id,
            "status": "error",
            "missing": [],
            "source_dir": str(story_dir.resolve()),
            "to_publish_dir": str((to_publish_root / _sanitize_folder_name(story_id)).resolve()),
            "info_path": "",
            "audio_path": "",
            "image_path": "",
            "text_path": "",
            "reason": "",
        }
        try:
            chk = _check_story(story_dir, to_publish_root)
            base["info_path"] = str(chk.info_path) if chk.info_path else ""
            base["audio_path"] = str(chk.audio_path) if chk.audio_path else ""
            base["image_path"] = str(chk.image_path) if chk.image_path else ""
            base["text_path"] = str(chk.text_path) if chk.text_path else ""
            base["missing"] = list(chk.missing)

            if "audio" in chk.missing:
                missing_audio_count += 1
            if "image" in chk.missing:
                missing_image_count += 1
            if "info" in chk.missing:
                missing_info_count += 1
            if "text" in chk.missing:
                missing_text_count += 1

            if chk.missing:
                skipped_count += 1
                terminal_info = terminal_tts_by_story.get(story_id)
                if terminal_info and "audio" in chk.missing:
                    base["status"] = "skipped_tts_" + str(terminal_info.get("status") or "terminal")
                    base["reason"] = str(terminal_info.get("reason") or "tts_terminal_skip")
                    base["tts_terminal"] = terminal_info
                    items.append(base)
                    continue
                if chk.missing == ["audio"]:
                    base["status"] = "skipped_missing_audio"
                elif chk.missing == ["image"]:
                    base["status"] = "skipped_missing_image"
                elif chk.missing == ["info"]:
                    base["status"] = "skipped_missing_info"
                elif chk.missing == ["text"]:
                    base["status"] = "skipped_missing_text"
                else:
                    base["status"] = "skipped_incomplete"
                base["reason"] = "missing:" + ",".join(chk.missing)
                items.append(base)
                continue

            ready_count += 1

            dst = chk.to_publish_dir
            if dst.exists() and not force:
                base["status"] = "already_exists"
                base["reason"] = "destination_exists_use_force"
                items.append(base)
                continue

            base["status"] = "ready_to_publish"
            base["reason"] = "dry_run_no_copy" if not execute else "prepared"

            if execute:
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                dst.mkdir(parents=True, exist_ok=True)
                assert chk.info_path and chk.audio_path and chk.image_path and chk.text_path
                shutil.copy2(chk.info_path, dst / chk.info_path.name)
                shutil.copy2(chk.audio_path, dst / chk.audio_path.name)
                shutil.copy2(chk.image_path, dst / chk.image_path.name)
                shutil.copy2(chk.text_path, dst / chk.text_path.name)
                prepared_count += 1
                base["status"] = "prepared"
            items.append(base)
        except Exception as exc:
            base["status"] = "error"
            base["reason"] = f"{type(exc).__name__}: {exc}"
            items.append(base)
            continue

    payload: dict[str, Any] = {
        "ok": True,
        "mode": "execute" if execute else "dry-run",
        "force": bool(force),
        "allow_partial_tts": bool(allow_partial_tts),
        "story": story_name,
        "launch_name": (launch_name or "").strip(),
        "launch_dir": str(launch) if launch else "",
        "layout": layout_info,
        "site_root": str(site_root),
        "to_publish_root": str(to_publish_root),
        "manifest_path": str(manifest_path),
        "total_stories": total_stories,
        "ready_count": ready_count,
        "skipped_count": skipped_count,
        "prepared_count": prepared_count,
        "missing_audio_count": missing_audio_count,
        "missing_image_count": missing_image_count,
        "missing_info_count": missing_info_count,
        "missing_text_count": missing_text_count,
        "tts_manual_skipped_count": len(terminal_manual_skipped),
        "tts_terminal_failed_count": len(terminal_failed),
        "items": items,
    }

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["report_path"] = str(report)
    reports_dir = (root / "reports").resolve()
    availability = {
        "ok": True,
        "written_at": "execute" if execute else "dry-run",
        "allow_partial_tts": bool(allow_partial_tts),
        "total_stories": total_stories,
        "ready_to_publish_count": ready_count,
        "skipped_count": skipped_count,
        "skipped_tts_manual_count": len(terminal_manual_skipped),
        "skipped_tts_failed_count": len(terminal_failed),
        "missing_audio_count": missing_audio_count,
        "manual_skipped_files": terminal_manual_skipped,
        "terminal_failed_files": terminal_failed,
        "can_continue_to_site_publish": True,
        "note": "prepare publishes/copies only stories that already have audio; TTS terminal skipped stories are not copied.",
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "site_publish_tts_availability_report.json").write_text(
        json.dumps(availability, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "site_publish_skip_report.json").write_text(
        json.dumps({"items": [it for it in items if str(it.get("status", "")).startswith("skipped")]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if launch is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_payload: dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest_payload, dict):
                    manifest_payload = {}
            except (OSError, json.JSONDecodeError):
                manifest_payload = {}
        manifest_payload.update(
            {
                "stage": "prepare",
                "launch_name": (launch_name or "").strip() or launch.name,
                "launch_dir": str(launch),
                "site_publish_root": str(site_root),
                "to_publish_root": str(to_publish_root),
                "stories_count": total_stories,
                "ready_count": ready_count,
                "prepared_count": prepared_count,
                "skipped_count": skipped_count,
                "missing_assets_count": int(missing_audio_count + missing_image_count + missing_info_count + missing_text_count),
                "tts_manual_skipped_count": len(terminal_manual_skipped),
                "tts_terminal_failed_count": len(terminal_failed),
                "mode": "execute" if execute else "dry-run",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "report_path": str(report),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return payload

