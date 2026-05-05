from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class Director20Wrapper(BaseWrapper):
    contract = StageContract(
        stage="director20",
        description="Legacy visual direction and frame generation",
        branch="youtube",
        unsafe=True,
        destructive_ops=["frame_generation", "remote_api_calls"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="Режиссер 2.0/main.py",
    )
