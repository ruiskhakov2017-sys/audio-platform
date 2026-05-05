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
    def from_site_root(cls, site_output: Path, story_name: str) -> SiteTtsPaths:
        folder = (site_output / story_name).resolve()
        cleaned = resolve_cleaned_story_txt_path(folder, story_name)
        return cls(
            story_folder=folder,
            cleaned_story_txt=cleaned,
            info_txt=folder / "info.txt",
            output_mp3=folder / f"{story_name}.mp3",
        )


@dataclass
class TTSSynthesisResult:
    status: Literal["success", "error"]
    output_path: Path | None
    duration_sec: float | None
    logs_path: Path | None
    message: str = ""
    details: dict[str, object] = field(default_factory=dict)
