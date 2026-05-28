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
    google_drive_root_dir: str
    google_drive_texts_dir: str
    google_drive_mp3_dir: str
    google_drive_scripts_dir: str
    google_drive_cache_dir: str
    google_drive_logs_dir: str
    google_drive_job_dir: str
    google_drive_wait_interval_minutes: int
    google_drive_max_wait_hours: int
    google_drive_cleanup_after_success: bool
    voice_selection_strategy: str
    voice_selection_save_selected_voice_to_story_metadata: bool
    voice_selection_fallback_label: str
    voice_selection_fallback_voice: str
    voice_pools: dict[str, list[str]]
    default_voice: str


def _parse_scalar(raw: str) -> Any:
    val = raw.strip().strip("\"'")
    if val.lower() in {"true", "false"}:
        return val.lower() == "true"
    if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
        try:
            return int(val)
        except ValueError:
            return val
    if "." in val and any(c.isdigit() for c in val):
        try:
            return float(val)
        except ValueError:
            return val
    return val


def _minimal_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    def _ensure_parent(level: int) -> Any:
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        return stack[-1][1]

    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        parent = _ensure_parent(indent)

        if line.startswith("- "):
            item = _parse_scalar(line[2:].strip())
            if isinstance(parent, list):
                parent.append(item)
            continue

        if ":" not in line:
            continue

        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()

        if not isinstance(parent, dict):
            continue

        if val:
            parent[key] = _parse_scalar(val)
            continue

        next_sig = ""
        for j in range(i + 1, len(lines)):
            s = lines[j].strip()
            if not s or s.startswith("#"):
                continue
            next_sig = s
            break
        container: Any = [] if next_sig.startswith("- ") else {}
        parent[key] = container
        stack.append((indent, container))

    return root


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

    gd = parsed.get("google_drive_tts", {}) if isinstance(parsed, dict) else {}
    if not isinstance(gd, dict):
        gd = {}

    def gdv(key: str, fallback_key: str, default: Any) -> Any:
        if key in gd and gd.get(key) not in (None, ""):
            return gd.get(key)
        return g(fallback_key, default)

    vs = parsed.get("voice_selection", {}) if isinstance(parsed, dict) else {}
    if not isinstance(vs, dict):
        vs = {}
    pools_raw = parsed.get("voice_pools", {}) if isinstance(parsed, dict) else {}
    if not isinstance(pools_raw, dict):
        pools_raw = {}
    pools: dict[str, list[str]] = {}
    for k, v in pools_raw.items():
        label = str(k or "").strip().upper()[:1]
        if label not in {"M", "F", "U"}:
            continue
        if isinstance(v, list):
            vals = [str(x).strip() for x in v if str(x).strip()]
        else:
            vals = [str(v).strip()] if str(v).strip() else []
        if vals:
            pools[label] = vals

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
        google_drive_root_dir=str(gdv("root_dir", "google_drive_root_dir", "") or "").strip(),
        google_drive_texts_dir=str(gdv("texts_dir", "google_drive_texts_dir", "") or "").strip(),
        google_drive_mp3_dir=str(gdv("mp3_dir", "google_drive_mp3_dir", "") or "").strip(),
        google_drive_scripts_dir=str(gdv("scripts_dir", "google_drive_scripts_dir", "") or "").strip(),
        google_drive_cache_dir=str(gdv("cache_dir", "google_drive_cache_dir", "") or "").strip(),
        google_drive_logs_dir=str(gdv("logs_dir", "google_drive_logs_dir", "") or "").strip(),
        google_drive_job_dir=str(gdv("job_dir", "google_drive_job_dir", "") or "").strip(),
        google_drive_wait_interval_minutes=int(gdv("wait_interval_minutes", "google_drive_wait_interval_minutes", 60)),
        google_drive_max_wait_hours=int(gdv("max_wait_hours", "google_drive_max_wait_hours", 1000)),
        google_drive_cleanup_after_success=bool(gdv("cleanup_after_success", "google_drive_cleanup_after_success", True)),
        voice_selection_strategy=str(vs.get("strategy", "single")).strip().lower(),
        voice_selection_save_selected_voice_to_story_metadata=bool(
            vs.get("save_selected_voice_to_story_metadata", True)
        ),
        voice_selection_fallback_label=str(vs.get("fallback_label", "U")).strip().upper()[:1] or "U",
        voice_selection_fallback_voice=str(vs.get("fallback_voice", "af_bella")).strip() or "af_bella",
        voice_pools=pools,
        default_voice=str(g("default_voice", "af_bella")).strip() or "af_bella",
    )
