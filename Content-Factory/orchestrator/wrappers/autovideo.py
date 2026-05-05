from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class AutoVideoWrapper(BaseWrapper):
    contract = StageContract(
        stage="autovideo",
        description="Legacy video assembly via ffmpeg",
        branch="youtube",
        unsafe=False,
        destructive_ops=["temp_file_cleanup"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="AutoVideo/main.py",
    )
