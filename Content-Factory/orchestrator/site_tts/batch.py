from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from orchestrator.human_launch_layout import D05_RASSKAZY, human_zapuski_root
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


def iter_human_launch_story_dirs(launch: Path) -> list[Path]:
    base = launch.resolve() / D05_RASSKAZY
    if not base.is_dir():
        return []
    return sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name.lower())


def resolve_site_tts_human_launch_root(
    project_root: Path,
    *,
    launch_name: str = "",
    launch_dir: Path | None = None,
) -> Path | None:
    """
    Корень Запуски/<name> для site-tts в human-launch режиме.
    Явный --launch-dir перекрывает --launch-name.
    """
    project_root = project_root.resolve()
    raw_dir = launch_dir
    if raw_dir is not None and str(raw_dir).strip():
        p = Path(raw_dir)
        out = (p if p.is_absolute() else (project_root / p)).resolve()
    else:
        name = (launch_name or "").strip()
        if not name:
            return None
        out = (human_zapuski_root(project_root) / name).resolve()
    if not out.is_dir():
        return None
    if not (out / D05_RASSKAZY).is_dir():
        return None
    return out


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
    project_root: Path | None = None,
    limit: int | None = None,
    voice_types: frozenset[str] | None = None,
    folder_suffix: str | None = None,
) -> list[SiteTtsBatchItem]:
    items: list[SiteTtsBatchItem] = []
    for folder in iter_site_story_dirs(site_root):
        if project_root is not None:
            paths = SiteTtsPaths.for_site_output_folder(project_root, site_root, folder.name)
        else:
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
    project_root: Path | None = None,
    voice_types: frozenset[str] | None = None,
    folder_suffix: str | None = None,
) -> list[dict[str, str | bool]]:
    rows: list[dict[str, str | bool]] = []
    for folder in iter_site_story_dirs(site_root):
        if project_root is not None:
            paths = SiteTtsPaths.for_site_output_folder(project_root, site_root, folder.name)
        else:
            paths = SiteTtsPaths.from_site_root(site_root, folder.name)
        has_clean = paths.cleaned_story_txt.is_file()
        has_info = paths.info_txt.is_file()
        try:
            has_mp3 = paths.output_mp3.is_file() and paths.output_mp3.stat().st_size > 0
        except OSError:
            has_mp3 = False
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


def scan_human_launch_tts_queue(
    launch: Path,
    *,
    project_root: Path,
    voice_types: frozenset[str] | None = None,
    folder_suffix: str | None = None,
) -> list[dict[str, str | bool]]:
    _ = project_root
    rows: list[dict[str, str | bool]] = []
    for folder in iter_human_launch_story_dirs(launch):
        paths = SiteTtsPaths.from_human_launch_story(launch, folder.name, ensure_dirs=False)
        has_clean = paths.cleaned_story_txt.is_file()
        has_info = paths.info_txt.is_file()
        try:
            has_mp3 = paths.output_mp3.is_file() and paths.output_mp3.stat().st_size > 0
        except OSError:
            has_mp3 = False
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


def collect_human_launch_tts_items(
    launch: Path,
    *,
    project_root: Path,
    limit: int | None = None,
    voice_types: frozenset[str] | None = None,
    folder_suffix: str | None = None,
) -> list[SiteTtsBatchItem]:
    _ = project_root
    items: list[SiteTtsBatchItem] = []
    for folder in iter_human_launch_story_dirs(launch):
        paths = SiteTtsPaths.from_human_launch_story(launch, folder.name, ensure_dirs=False)
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


def run_site_tts_for_story(
    root_dir: Path,
    *,
    story_name: str,
    modes_config: Path,
    execute: bool,
    force: bool,
    run_id: str | None = None,
    site_output_root: Path | None = None,
    human_launch: Path | None = None,
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
    if human_launch is not None:
        paths = SiteTtsPaths.from_human_launch_story(human_launch.resolve(), story_key, ensure_dirs=True)
    else:
        site_root = (site_output_root or (root_dir / "output" / "site")).resolve()
        paths = SiteTtsPaths.for_site_output_folder(root_dir, site_root, story_key)
    rid = run_id or uuid.uuid4().hex
    work = (root_dir / "runs" / "tts" / rid / story_key).resolve()

    adapter = get_modular_site_adapter(engine)
    if adapter is None:
        if engine == "kokoro_colab_drive":
            cl = paths.cleaned_story_txt
            mp = paths.output_mp3
            msg_lines = [
                "engine kokoro_colab_drive: локальный `site-tts one` не вызывает Colab — используйте kokoro-colab export/import.",
                "Один рассказ (human-launch): txt + job на Google Drive (путь в configs/site_tts.yaml → google_drive_tts), затем Colab → mp3 на Drive → import:",
                "  python -m orchestrator site-tts --launch-name <LAUNCH> kokoro-colab export --limit 1",
                "  … после Colab …",
                "  python -m orchestrator site-tts --launch-name <LAUNCH> kokoro-colab import",
                "Тот же экспорт явно: `… kokoro-colab export-drive --limit 1`.",
                "Очередь legacy output/site:",
                "  python -m orchestrator site-tts kokoro-colab export --limit 1",
                f"Ожидаемый вход (cleaned): {cl}",
                f"Целевой mp3: {mp}",
            ]
            return TTSSynthesisResult(
                status="success",
                output_path=None,
                duration_sec=None,
                logs_path=None,
                message="\n".join(msg_lines),
                details={"hint_engine_colab_drive": True},
            )
        return TTSSynthesisResult(
            status="error",
            output_path=None,
            duration_sec=None,
            logs_path=None,
            message=f"engine {engine} is not handled by modular site TTS (use orchestrator.run + legacy ElevenLabs)",
        )

    return adapter.synthesize(paths=paths, settings=settings, run_work_dir=work, execute=execute, force=force)
