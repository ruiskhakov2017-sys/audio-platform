from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SITE_TTS_REL = Path("configs/site_tts.yaml")


@dataclass(frozen=True)
class SiteTtsSettings:
    site_tts_engine: str
    site_tts_runtime: str
    kokoro_lang_code: str
    kokoro_voice_male: str
    kokoro_voice_female: str
    kokoro_voice_neutral: str
    kokoro_speed: float
    kokoro_chunk_max_chars: int
    kokoro_pause_between_chunks_sec: float
    keep_tts_chunks: bool
    vibevoice_enabled: bool
    google_drive_texts_dir: str
    google_drive_mp3_dir: str


def _minimal_yaml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("\"'")
        if val.lower() in {"true", "false"}:
            data[key] = val.lower() == "true"
            continue
        try:
            if "." in val and any(c.isdigit() for c in val):
                data[key] = float(val)
            elif val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
                data[key] = int(val)
            else:
                data[key] = val
        except ValueError:
            data[key] = val
    return data


def load_site_tts_settings(root: Path, path: Path | None = None) -> SiteTtsSettings:
    cfg_path = (path or (root / DEFAULT_SITE_TTS_REL)).resolve()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"site_tts config not found: {cfg_path}")
    raw = cfg_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(raw) or {}
    except Exception:
        parsed = _minimal_yaml(raw)
    if not isinstance(parsed, dict):
        parsed = {}

    def g(key: str, default: Any) -> Any:
        return parsed.get(key, default)

    return SiteTtsSettings(
        site_tts_engine=str(g("site_tts_engine", "kokoro")).strip().lower(),
        site_tts_runtime=str(g("site_tts_runtime", "local")).strip().lower(),
        kokoro_lang_code=str(g("kokoro_lang_code", "") or "").strip(),
        kokoro_voice_male=str(g("kokoro_voice_male", "am_michael")).strip(),
        kokoro_voice_female=str(g("kokoro_voice_female", "af_bella")).strip(),
        kokoro_voice_neutral=str(g("kokoro_voice_neutral", "af_heart")).strip(),
        kokoro_speed=float(g("kokoro_speed", 0.92)),
        kokoro_chunk_max_chars=int(g("kokoro_chunk_max_chars", 480)),
        kokoro_pause_between_chunks_sec=float(g("kokoro_pause_between_chunks_sec", 0.35)),
        keep_tts_chunks=bool(g("keep_tts_chunks", False)),
        vibevoice_enabled=bool(g("vibevoice_enabled", False)),
        google_drive_texts_dir=str(g("google_drive_texts_dir", "") or "").strip(),
        google_drive_mp3_dir=str(g("google_drive_mp3_dir", "") or "").strip(),
    )
