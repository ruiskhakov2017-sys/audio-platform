"""Canonical launch-scoped paths, manifest, and write guard for production flows."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig

LAUNCHES_DIR_NAME = "Запуски"
LAUNCH_MANIFEST_NAME = "launch_manifest.json"
STORY_MANIFEST_YOUTUBE = "story_manifest.json"
LEGACY_YOUTUBE_GLOBAL_DRIVE = Path(r"G:\Мой диск\ContentFactory_YouTube")
DRIVE_LAUNCHES_SUBDIR = "launches"
RUNPOD_LAUNCHES_PREFIX = "/workspace/content_factory_youtube/launches"

PRODUCTION_REQUIRES_LAUNCH_ID = "PRODUCTION_REQUIRES_LAUNCH_ID"
WRITE_OUTSIDE_LAUNCH_ROOT_BLOCKED = "WRITE_OUTSIDE_LAUNCH_ROOT_BLOCKED"


class ProductionLaunchRequiredError(RuntimeError):
    """Raised when a production command runs without launch_id."""


class WriteOutsideLaunchRootError(RuntimeError):
    """Raised when a write targets a path outside launch_root."""


@dataclass(frozen=True)
class LaunchContext:
    launch_id: str
    launch_root: Path
    drive_mirror_root: Path
    runpod_mirror_root: str
    temp_root: Path
    logs_root: Path
    reports_root: Path

    @property
    def youtube_root(self) -> Path:
        return self.launch_root / "03_youtube"

    @property
    def site_root(self) -> Path:
        return self.launch_root / "02_site"

    @property
    def shared_assets_root(self) -> Path:
        return self.launch_root / "04_shared_assets"


CANONICAL_LAUNCH_DIRS = (
    "00_launch_manifest",
    "01_input_snapshot",
    "02_site",
    "03_youtube",
    "04_shared_assets",
    "05_drive_mirror",
    "06_runpod_mirror",
    "07_reports",
    "08_logs",
    "09_tmp",
)

YOUTUBE_STORY_DIRS = (
    "00_source",
    "01_selection",
    "02_safe_story",
    "03_promo",
    "04_audio",
    "05_visuals",
    "06_telegram",
    "07_runpod_package",
    "08_video",
    "09_youtube_ready",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def launches_root(config: OrchestratorConfig) -> Path:
    return (config.root_dir / LAUNCHES_DIR_NAME).resolve()


def sanitize_launch_id(value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("_")
    return raw or "launch_unnamed"


def story_slug(value: str) -> str:
    return sanitize_launch_id(value).replace(" ", "_")


def require_production_launch_id(
    launch_id: str | None,
    *,
    production: bool = False,
    command: str = "",
) -> None:
    if production and not str(launch_id or "").strip():
        msg = PRODUCTION_REQUIRES_LAUNCH_ID
        if command:
            msg += f" (command={command})"
        raise ProductionLaunchRequiredError(msg)


def resolve_launch_root(
    config: OrchestratorConfig,
    *,
    launch_id: str | None = None,
    launch_root: Path | str | None = None,
) -> Path:
    if launch_root:
        root = Path(launch_root).resolve()
        if root.is_dir():
            return root
    lid = sanitize_launch_id(str(launch_id or ""))
    if not lid:
        raise ValueError("launch_id or launch_root required")
    candidate = launches_root(config) / lid
    return candidate.resolve()


def default_drive_mirror_base() -> Path:
    return LEGACY_YOUTUBE_GLOBAL_DRIVE / DRIVE_LAUNCHES_SUBDIR


def drive_mirror_root_for_launch(launch_id: str, *, base: Path | None = None) -> Path:
    root = (base or default_drive_mirror_base()).resolve()
    return (root / sanitize_launch_id(launch_id)).resolve()


def runpod_mirror_root_for_launch(launch_id: str) -> str:
    return f"{RUNPOD_LAUNCHES_PREFIX}/{sanitize_launch_id(launch_id)}"


def build_launch_context(
    config: OrchestratorConfig,
    *,
    launch_id: str,
    launch_root: Path | str | None = None,
    drive_mirror_base: Path | None = None,
) -> LaunchContext:
    lid = sanitize_launch_id(launch_id)
    root = resolve_launch_root(config, launch_id=lid, launch_root=launch_root)
    drive = drive_mirror_root_for_launch(lid, base=drive_mirror_base)
    return LaunchContext(
        launch_id=lid,
        launch_root=root,
        drive_mirror_root=drive,
        runpod_mirror_root=runpod_mirror_root_for_launch(lid),
        temp_root=root / "09_tmp",
        logs_root=root / "08_logs",
        reports_root=root / "07_reports",
    )


def ensure_canonical_launch_scaffold(
    launch_root: Path,
    *,
    execute: bool = False,
) -> list[str]:
    created: list[str] = []
    targets = [launch_root, *[launch_root / d for d in CANONICAL_LAUNCH_DIRS]]
    for path in targets:
        if execute:
            path.mkdir(parents=True, exist_ok=True)
        created.append(str(path))
    return created


def youtube_story_dir(launch_root: Path, story_key: str) -> Path:
    return (launch_root / "03_youtube" / story_slug(story_key)).resolve()


def ensure_youtube_story_scaffold(story_dir: Path, *, execute: bool = False) -> None:
    targets = [story_dir, *[story_dir / d for d in YOUTUBE_STORY_DIRS], story_dir / "04_audio" / "reports"]
    for path in targets:
        if execute:
            path.mkdir(parents=True, exist_ok=True)


def path_is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_write_allowed(
    path: Path | str,
    launch_root: Path,
    *,
    allow_readonly_external: bool = False,
) -> Path:
    """Resolve path and fail if a write would land outside launch_root."""
    resolved = Path(path).resolve()
    root = launch_root.resolve()
    if path_is_inside(resolved, root):
        return resolved
    if allow_readonly_external:
        return resolved
    raise WriteOutsideLaunchRootError(
        f"{WRITE_OUTSIDE_LAUNCH_ROOT_BLOCKED}: forbidden_path={resolved} allowed_launch_root={root}"
    )


def guarded_write_text(path: Path, text: str, launch_root: Path) -> None:
    target = assert_write_allowed(path, launch_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def guarded_write_json(path: Path, payload: Any, launch_root: Path) -> None:
    guarded_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2), launch_root)


def guarded_copy2(src: Path, dst: Path, launch_root: Path) -> None:
    assert_write_allowed(dst, launch_root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def guarded_move(src: Path, dst: Path, launch_root: Path) -> None:
    assert_write_allowed(dst, launch_root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def read_launch_manifest(launch_root: Path) -> dict[str, Any]:
    path = launch_root / LAUNCH_MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_launch_manifest(launch_root: Path, payload: dict[str, Any]) -> Path:
    payload.setdefault("launch_id", payload.get("launch_id") or launch_root.name)
    payload.setdefault("launch_root", str(launch_root))
    payload["updated_at"] = _now_iso()
    path = launch_root / LAUNCH_MANIFEST_NAME
    guarded_write_json(path, payload, launch_root)
    guarded_write_json(launch_root / "00_launch_manifest" / LAUNCH_MANIFEST_NAME, payload, launch_root)
    return path


def init_launch_manifest(
    *,
    launch_root: Path,
    launch_id: str,
    launch_type: str = "youtube_only",
    youtube_stories: list[str] | None = None,
    site_stories: list[str] | None = None,
    source_input_dirs: list[str] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    if execute:
        ensure_canonical_launch_scaffold(launch_root, execute=True)
    ctx = LaunchContext(
        launch_id=sanitize_launch_id(launch_id),
        launch_root=launch_root.resolve(),
        drive_mirror_root=drive_mirror_root_for_launch(launch_id),
        runpod_mirror_root=runpod_mirror_root_for_launch(launch_id),
        temp_root=launch_root / "09_tmp",
        logs_root=launch_root / "08_logs",
        reports_root=launch_root / "07_reports",
    )
    payload: dict[str, Any] = {
        "launch_id": ctx.launch_id,
        "launch_type": launch_type,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "source_input_dirs": source_input_dirs or [],
        "selected_stories": list(dict.fromkeys([*(youtube_stories or []), *(site_stories or [])])),
        "site_stories": site_stories or [],
        "youtube_stories": youtube_stories or [],
        "launch_root": str(ctx.launch_root),
        "drive_mirror_root": str(ctx.drive_mirror_root),
        "runpod_mirror_root": ctx.runpod_mirror_root,
        "temp_root": str(ctx.temp_root),
        "logs_root": str(ctx.logs_root),
        "reports_root": str(ctx.reports_root),
        "status": "initialized",
        "stages": {},
        "errors": [],
        "warnings": [],
    }
    if execute:
        write_launch_manifest(launch_root, payload)
    return payload


def resolve_youtube_story_dir(
    config: OrchestratorConfig,
    story_id: str,
    *,
    launch_id: str | None = None,
    launch_root: Path | str | None = None,
) -> tuple[Path, LaunchContext | None]:
    """Canonical launch story dir if launch_id set; else legacy output/youtube."""
    if launch_id or launch_root:
        ctx = build_launch_context(config, launch_id=str(launch_id or ""), launch_root=launch_root)
        return youtube_story_dir(ctx.launch_root, story_id), ctx
    return _legacy_youtube_story_dir(config, story_id), None


def _legacy_youtube_story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    root = (config.root_dir / "output" / "youtube").resolve()
    direct = root / story_id
    if direct.is_dir():
        return direct.resolve()
    key = story_id.strip()
    matches: list[Path] = []
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            manifest = child / "youtube_story_manifest.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            sid = str(data.get("story_id", "")).strip()
            canonical = str(data.get("canonical_basename", "")).strip()
            if key in {sid, canonical} or sid.casefold() == key.casefold() or canonical.casefold() == key.casefold():
                matches.append(child)
    if len(matches) == 1:
        return matches[0].resolve()
    return direct.resolve()


def drive_paths_for_story(ctx: LaunchContext, story_key: str) -> dict[str, Path]:
    stem = story_slug(story_key)
    base = ctx.drive_mirror_root
    return {
        "drive_root": base,
        "texts_dir": base / "texts",
        "audio_dir": base / "audio",
        "jobs_dir": base / "jobs",
        "manifests_dir": base / "manifests",
        "logs_dir": base / "logs",
        "done_dir": base / "done",
        "failed_dir": base / "failed",
        "drive_text": base / "texts" / f"{stem}.txt",
        "drive_audio": base / "audio" / f"{stem}.mp3",
        "audio_rejected_dir": base / "audio" / "rejected" / "legacy",
        "youtube_tts_job": base / "jobs" / "youtube_tts_job.json",
    }


def patch_story_manifest_launch_fields(
    *,
    story_manifest_path: Path,
    ctx: LaunchContext,
    story_id: str,
    legacy_external_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not story_manifest_path.is_file():
        return
    data = json.loads(story_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    story_dir = youtube_story_dir(ctx.launch_root, story_id)
    data["launch_id"] = ctx.launch_id
    data["launch_root"] = str(ctx.launch_root)
    data["launch_manifest"] = str(ctx.launch_root / LAUNCH_MANIFEST_NAME)
    data["canonical_story_dir"] = str(story_dir)
    data["drive_mirror_root"] = str(ctx.drive_mirror_root)
    data["runpod_mirror_root"] = ctx.runpod_mirror_root
    if legacy_external_path:
        data["legacy_external_path"] = legacy_external_path
        data["legacy_external_readonly"] = True
    outputs = dict(data.get("youtube_outputs") or {})
    outputs["story_dir"] = str(story_dir)
    data["youtube_outputs"] = outputs
    if extra:
        data.update(extra)
    data["updated_at"] = _now_iso()
    guarded_write_json(story_manifest_path, data, ctx.launch_root)


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sync_youtube_story_from_legacy(
    config: OrchestratorConfig,
    *,
    ctx: LaunchContext,
    story_id: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Copy legacy output/youtube/<story> tree into launch 03_youtube/<slug> (one-time bridge)."""
    legacy_dir, _ = resolve_youtube_story_dir(config, story_id)
    target_dir = youtube_story_dir(ctx.launch_root, story_id)
    actions: list[str] = []
    if not legacy_dir.is_dir():
        return {"ok": False, "message": f"legacy story dir missing: {legacy_dir}", "actions": actions}
    if execute:
        ensure_youtube_story_scaffold(target_dir, execute=True)
        for item in legacy_dir.rglob("*"):
            if item.is_dir():
                continue
            rel = item.relative_to(legacy_dir)
            dest = target_dir / rel
            assert_write_allowed(dest, ctx.launch_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or _sha256_file(dest) != _sha256_file(item):
                shutil.copy2(item, dest)
            actions.append(f"copy:{item} -> {dest}")
        legacy_manifest = legacy_dir / "youtube_story_manifest.json"
        if legacy_manifest.is_file():
            dest_manifest = target_dir / "youtube_story_manifest.json"
            shutil.copy2(legacy_manifest, dest_manifest)
            patch_story_manifest_launch_fields(
                story_manifest_path=dest_manifest,
                ctx=ctx,
                story_id=story_id,
                legacy_external_path=str(legacy_dir),
            )
            actions.append(f"manifest:{dest_manifest}")
    return {
        "ok": True,
        "execute": execute,
        "legacy_dir": str(legacy_dir),
        "target_dir": str(target_dir),
        "actions": actions,
    }
