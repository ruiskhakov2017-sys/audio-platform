"""
Fish Audio S2 Pro — RunPod / remote GPU adapter (stub).

Production rule: S2 Pro is not run locally; weights live under ``models/fish_audio/fish-s2-pro/``
(see ``configs/paths.yaml`` key ``fish_audio_s2_pro``). Actual HTTP/gRPC client to RunPod will be
implemented here when site TTS is wired to ``site_tts_runtime: runpod`` + ``site_tts_engine: fish_audio_s2_pro``.
"""

from __future__ import annotations

from pathlib import Path


def resolve_model_dir(root_dir: Path, models_paths: dict[str, str]) -> Path | None:
    rel = models_paths.get("fish_audio_s2_pro", "").strip()
    if not rel:
        return None
    p = (root_dir / rel).resolve()
    return p if p.is_dir() else None
