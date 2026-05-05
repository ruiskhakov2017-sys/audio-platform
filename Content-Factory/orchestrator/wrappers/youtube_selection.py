from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class YoutubeSelectionWrapper(BaseWrapper):
    contract = StageContract(
        stage="youtube_selection",
        description="Legacy selection pass/fail for YouTube branch",
        branch="youtube",
        unsafe=True,
        destructive_ops=["trash_move", "claim_files"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="Отбор для YouTube/gemini_auto.py",
    )
