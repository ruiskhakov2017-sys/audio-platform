from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_MODES: dict[str, str] = {
    "site_visual": "auto",
    "youtube_publish": "api",
    # Site TTS: по умолчанию Kokoro local; Fish S2 Pro / RunPod — отдельный режим.
    "site_tts_runtime": "local",
    "site_tts_engine": "kokoro",
    "youtube_tts_runtime": "local",
    "youtube_tts_engine": "elevenlabs",
    "elevenlabs_mode": "normal",
    "video_build": "local",
}

ALLOWED_VALUES: dict[str, set[str]] = {
    "site_visual": {"auto", "manual"},
    "youtube_publish": {"api", "manual"},
    "site_tts_runtime": {"disabled", "runpod", "local"},
    "site_tts_engine": {
        "fish_audio_s2_pro",
        "elevenlabs",
        "kokoro",
        "vibevoice",
        "edge_tts",
        "fish_audio",
    },
    "youtube_tts_runtime": {"local", "colab"},
    "youtube_tts_engine": {"elevenlabs", "fish_audio", "edge_tts"},
    "elevenlabs_mode": {"normal", "free_keys"},
    "video_build": {"local", "colab", "runpod"},
}


def _minimal_yaml_dict(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    current_section = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                current_section = key
                out[current_section] = {}
            else:
                out[key] = value
            continue
        if current_section and ":" in line:
            sub_key, _, sub_val = line.strip().partition(":")
            out[current_section][sub_key.strip()] = sub_val.strip()
    return out


def load_runtime_modes(path: Path) -> dict[str, str]:
    if not path.exists():
        return dict(DEFAULT_MODES)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text) or {}
    except Exception:
        parsed = _minimal_yaml_dict(text)
    modes = dict(DEFAULT_MODES)
    raw_modes = parsed.get("modes", {}) if isinstance(parsed, dict) else {}
    if isinstance(raw_modes, dict):
        for k, v in raw_modes.items():
            if k in DEFAULT_MODES:
                modes[k] = str(v).strip()
    return modes


def validate_modes(modes: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key, default in DEFAULT_MODES.items():
        if key not in modes:
            errors.append(f"missing mode key: {key}")
            continue
        val = modes[key]
        if val not in ALLOWED_VALUES[key]:
            errors.append(
                f"invalid mode value: {key}={val}, allowed={sorted(ALLOWED_VALUES[key])}"
            )
    return errors


def save_runtime_modes(path: Path, modes: dict[str, str]) -> None:
    errors = validate_modes(modes)
    if errors:
        raise RuntimeError("; ".join(errors))
    payload = {"modes": {k: modes[k] for k in DEFAULT_MODES}}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore

        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return
    except Exception:
        lines = ["modes:"]
        for k in DEFAULT_MODES:
            lines.append(f"  {k}: {modes[k]}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_runtime_mode(path: Path, key: str, value: str) -> dict[str, str]:
    if key not in DEFAULT_MODES:
        raise RuntimeError(f"unknown mode key: {key}")
    value = value.strip()
    if value not in ALLOWED_VALUES[key]:
        raise RuntimeError(f"invalid value for {key}: {value}")
    modes = load_runtime_modes(path)
    modes[key] = value
    save_runtime_modes(path, modes)
    return modes


def save_runtime_snapshot(path: Path, modes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"modes": modes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
