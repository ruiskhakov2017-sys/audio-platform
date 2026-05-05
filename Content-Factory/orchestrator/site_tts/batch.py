from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from orchestrator.runtime_modes import load_runtime_modes
from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.contract import SiteTtsPaths, TTSSynthesisResult
from orchestrator.site_tts.info_parser import resolve_voice_letter_from_info_content
from orchestrator.site_tts.registry import get_modular_site_adapter


def normalize_site_story_name(raw: str) -> str:
    """Strip quotes and take last path segment (basename) if user pasted a full folder path."""
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return ""
    return Path(s).name


@dataclass
class SiteTtsBatchItem:
    story_name: str
    paths: SiteTtsPaths


def iter_site_story_dirs(site_root: Path) -> list[Path]:
    if not site_root.is_dir():
        return []
    return sorted([p for p in site_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())


def story_needs_tts(paths: SiteTtsPaths) -> bool:
    if not paths.cleaned_story_txt.is_file():
        return False
    return not paths.output_mp3.is_file()


def voice_type_for_site_folder(paths: SiteTtsPaths) -> str:
    if not paths.info_txt.is_file():
        return "?"
    letter, _, _ = resolve_voice_letter_from_info_content(paths.info_txt.read_text(encoding="utf-8"))
    return letter


def parse_voice_filter_arg(raw: str) -> frozenset[str] | None:
    s = (raw or "").strip()
    if not s:
        return None
    out: set[str] = set()
    for part in s.split(","):
        t = part.strip().upper()[:1]
        if t in {"M", "F", "U"}:
            out.add(t)
    return frozenset(out) if out else None


def folder_suffix_matches(story_name: str, suffix: str | None) -> bool:
    if not suffix:
        return True
    ch = suffix.strip().upper()[:1]
    if ch not in {"M", "F", "U"}:
        return True
    low = story_name.lower()
    return low.endswith(f"_{ch.lower()}")


def collect_batch_items(
    site_root: Path,
    *,
    limit: int | None = None,
    voice_types: frozenset[str] | None = None,
    folder_suffix: str | None = None,
) -> list[SiteTtsBatchItem]:
    items: list[SiteTtsBatchItem] = []
    for folder in iter_site_story_dirs(site_root):
        paths = SiteTtsPaths.from_site_root(site_root, folder.name)
        if not story_needs_tts(paths):
            continue
        if not folder_suffix_matches(folder.name, folder_suffix):
            continue
        if voice_types is not None:
            vt = voice_type_for_site_folder(paths)
            if vt not in voice_types:
                continue
        items.append(SiteTtsBatchItem(story_name=folder.name, paths=paths))
        if limit is not None and len(items) >= limit:
            break
    return items


def scan_site_tts_queue(
    site_root: Path,
    *,
    voice_types: frozenset[str] | None = None,
    folder_suffix: str | None = None,
) -> list[dict[str, str | bool]]:
    rows: list[dict[str, str | bool]] = []
    for folder in iter_site_story_dirs(site_root):
        paths = SiteTtsPaths.from_site_root(site_root, folder.name)
        has_clean = paths.cleaned_story_txt.is_file()
        has_info = paths.info_txt.is_file()
        has_mp3 = paths.output_mp3.is_file()
        voice = voice_type_for_site_folder(paths) if has_info else "?"
        need = story_needs_tts(paths)
        ok_suffix = folder_suffix_matches(folder.name, folder_suffix)
        voice_ok = voice_types is None or voice in voice_types
        in_queue = bool(need and ok_suffix and voice_ok)
        skip = ""
        if not has_clean:
            skip = "no_cleaned_txt"
        elif has_mp3:
            skip = "has_mp3"
        elif not has_info:
            skip = "no_info"
        elif not ok_suffix:
            skip = "folder_suffix"
        elif not voice_ok:
            skip = "voice_filter"
        else:
            skip = "-"
        rows.append(
            {
                "story": folder.name,
                "voice": voice,
                "has_mp3": has_mp3,
                "has_cleaned": has_clean,
                "need_tts": in_queue,
                "skip": skip,
            }
        )
    return rows


def run_site_tts_for_story(
    root_dir: Path,
    *,
    story_name: str,
    modes_config: Path,
    execute: bool,
    force: bool,
    run_id: str | None = None,
) -> TTSSynthesisResult:
    story_key = normalize_site_story_name(story_name)
    if not story_key:
        return TTSSynthesisResult(
            status="error",
            output_path=None,
            duration_sec=None,
            logs_path=None,
            message="empty story name after normalize (use folder name under output\\site, not a full path unless basename is the story id)",
        )

    modes = load_runtime_modes(modes_config)
    engine = str(modes.get("site_tts_engine", "kokoro")).strip().lower()
    settings = load_site_tts_settings(root_dir)
    site_root = (root_dir / "output" / "site").resolve()
    paths = SiteTtsPaths.from_site_root(site_root, story_key)
    rid = run_id or uuid.uuid4().hex
    work = (root_dir / "runs" / "tts" / rid / story_key).resolve()

    adapter = get_modular_site_adapter(engine)
    if adapter is None:
        return TTSSynthesisResult(
            status="error",
            output_path=None,
            duration_sec=None,
            logs_path=None,
            message=f"engine {engine} is not handled by modular site TTS (use orchestrator.run + legacy ElevenLabs)",
        )

    return adapter.synthesize(paths=paths, settings=settings, run_work_dir=work, execute=execute, force=force)
