from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class BulkTextCleanerWrapper(BaseWrapper):
    contract = StageContract(
        stage="bulk_text_cleaner",
        description="Legacy text cleanup pipeline",
        branch="common",
        unsafe=False,
        dry_run_only=False,
        entrypoint="bulk-text-cleaner/clean_stories.py",
    )
