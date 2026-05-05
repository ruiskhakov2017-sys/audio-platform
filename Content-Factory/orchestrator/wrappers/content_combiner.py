from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class ContentCombinerWrapper(BaseWrapper):
    contract = StageContract(
        stage="content_combiner",
        description="Legacy content packaging for site route",
        branch="site",
        unsafe=True,
        destructive_ops=["move", "rename", "csv_write"],
        dry_run_only=False,
        entrypoint="content_combiner/content_combiner.py",
    )
