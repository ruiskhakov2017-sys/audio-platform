from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class AutopublisherWrapper(BaseWrapper):
    contract = StageContract(
        stage="autopublisher",
        description="Legacy cloud upload and DB publish",
        branch="site",
        unsafe=True,
        destructive_ops=["upload", "external_db_write", "archive_move"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="autopublisher/publish_stories.py",
    )
