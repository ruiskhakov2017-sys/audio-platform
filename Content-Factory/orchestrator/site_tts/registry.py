from __future__ import annotations

from typing import Any


def get_modular_site_adapter(engine: str) -> Any | None:
    """
    Адаптеры модульного site TTS-слоя. ElevenLabs и прочие legacy-движки здесь не регистрируются
    (остаются subprocess в wrapper).

    Импорты ленивые, чтобы preflight/cli не тянули torch/numpy до вызова Kokoro.
    """
    key = (engine or "").strip().lower()
    if key == "kokoro":
        from orchestrator.site_tts.kokoro_adapter import KokoroSiteAdapter

        return KokoroSiteAdapter()
    if key == "vibevoice":
        from orchestrator.site_tts.vibevoice_stub import VibeVoiceSiteAdapterStub

        return VibeVoiceSiteAdapterStub()
    return None


def list_modular_engines() -> tuple[str, ...]:
    return ("kokoro", "vibevoice")
