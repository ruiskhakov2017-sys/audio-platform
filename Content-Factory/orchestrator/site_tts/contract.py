from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from orchestrator.site_tts.info_parser import resolve_cleaned_story_txt_path


@dataclass
class SiteTtsPaths:
    """Контракт путей для одного рассказа на сайте."""

    story_folder: Path
    cleaned_story_txt: Path
    info_txt: Path
    output_mp3: Path

    @classmethod
    def from_isolated_resolver(cls, resolver, story_name: str) -> SiteTtsPaths:
        """Isolated launch: human audio under 01_Сайт/…/06_Аудио; technical contract under 04_Технические_файлы/output/site."""
        from orchestrator.isolated_launch_paths import LaunchPathResolver

        r: LaunchPathResolver = resolver
        sid = (story_name or "").strip()
        tech_story = (r.technical_output_site_dir() / sid).resolve()
        audio_dir = r.site_human_audio_dir(sid)
        return cls(
            story_folder=audio_dir,
            cleaned_story_txt=tech_story / "cleaned_story.txt",
            info_txt=tech_story / "info.txt",
            output_mp3=audio_dir / "audio.mp3",
        )

    @classmethod
    def voice_lock_path(cls, paths: SiteTtsPaths) -> Path:
        return paths.story_folder / "voice_lock.json"

    @classmethod
    def tts_result_path(cls, paths: SiteTtsPaths) -> Path:
        return paths.story_folder / "tts_result.json"

    @classmethod
    def from_site_root(cls, site_output: Path, story_name: str) -> SiteTtsPaths:
        folder = (site_output / story_name).resolve()
        cleaned = resolve_cleaned_story_txt_path(folder, story_name)
        return cls(
            story_folder=folder,
            cleaned_story_txt=cleaned,
            info_txt=folder / "info.txt",
            output_mp3=folder / f"{story_name}.mp3",
        )

    @classmethod
    def from_human_launch_story(
        cls, launch: Path, human_story_id: str, *, ensure_dirs: bool = True
    ) -> SiteTtsPaths:
        """
        Канон для human-launch: только cleaned_story.txt в 05_Рассказы/<id>/03_Сайт/01_Очищенный_текст/,
        озвучка и метаданные голоса — в 03_Сайт/04_Озвучка/ (audio.mp3).
        """
        from orchestrator.human_launch_layout import story_base_paths

        launch = launch.resolve()
        sid = (human_story_id or "").strip()
        hp = story_base_paths(launch, sid)
        if ensure_dirs:
            hp["site_tts"].mkdir(parents=True, exist_ok=True)
            hp["site_info"].mkdir(parents=True, exist_ok=True)
            hp["site_cleaned"].mkdir(parents=True, exist_ok=True)
        return cls(
            story_folder=hp["site_tts"],
            cleaned_story_txt=hp["site_cleaned"] / "cleaned_story.txt",
            info_txt=hp["site_info"] / "info.txt",
            output_mp3=hp["site_tts"] / "audio.mp3",
        )

    @classmethod
    def for_site_output_folder(cls, project_root: Path, site_output: Path, story_folder_name: str) -> SiteTtsPaths:
        """
        Если site_output указывает на …/Запуски/<n>/10_…/legacy/output/site —
        вход TTS и выход mp3 привязываются к дереву Запуски (05_Рассказы/…).
        Иначе — прежнее поведение output/site/<story>/.
        """
        from orchestrator.human_launch_layout import (
            D05_RASSKAZY,
            F_MANIFEST,
            launch_dir_from_site_output_root,
            read_json,
        )
        from orchestrator.human_launch_legacy_sync import (
            load_launch_legacy_paths,
            manifest_story_maps,
            resolve_human_story_id_for_canonical,
        )

        site_output = site_output.resolve()
        project_root = project_root.resolve()
        launch = launch_dir_from_site_output_root(site_output)
        if launch is None:
            return cls.from_site_root(site_output, story_folder_name)
        legacy = load_launch_legacy_paths(launch, project_root)
        if not legacy:
            return cls.from_site_root(site_output, story_folder_name)
        manifest = read_json(launch / F_MANIFEST) or {}
        maps = manifest_story_maps(manifest)
        sid = resolve_human_story_id_for_canonical(
            launch,
            legacy,
            canonical_folder_name=str(story_folder_name),
            story_ids_manifest=maps["story_ids_manifest"],
            story_id_to_legacy_sid=maps["story_id_to_legacy_sid"],
        )
        if sid:
            return cls.from_human_launch_story(launch, sid)
        if (launch / D05_RASSKAZY / str(story_folder_name)).is_dir():
            return cls.from_human_launch_story(launch, str(story_folder_name))
        return cls.from_site_root(site_output, story_folder_name)


@dataclass
class TTSSynthesisResult:
    status: Literal["success", "error"]
    output_path: Path | None
    duration_sec: float | None
    logs_path: Path | None
    message: str = ""
    details: dict[str, object] = field(default_factory=dict)
