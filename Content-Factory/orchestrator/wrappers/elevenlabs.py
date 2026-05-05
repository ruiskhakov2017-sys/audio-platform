from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class SiteElevenLabsWrapper(BaseWrapper):
    contract = StageContract(
        stage="site_tts",
        description="Legacy TTS pipeline for site route",
        branch="site",
        unsafe=True,
        destructive_ops=["api_calls", "writes_audio_files", "updates_key_state"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="ElevenLabs/main.py",
    )


class YoutubeElevenLabsWrapper(BaseWrapper):
    contract = StageContract(
        stage="youtube_tts",
        description="Legacy TTS pipeline for YouTube route",
        branch="youtube",
        unsafe=True,
        destructive_ops=["api_calls", "writes_audio_files", "updates_key_state"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="ElevenLabs/main.py",
    )
