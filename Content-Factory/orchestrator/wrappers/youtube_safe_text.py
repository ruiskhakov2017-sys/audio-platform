from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class YoutubeSafeTextWrapper(BaseWrapper):
    contract = StageContract(
        stage="youtube_safe_text",
        description="Legacy safe text preparation for YouTube",
        branch="youtube",
        unsafe=True,
        destructive_ops=["rewrite_text", "intermediate_outputs"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="Озвучка для YouTube/gemini_auto.py",
    )
