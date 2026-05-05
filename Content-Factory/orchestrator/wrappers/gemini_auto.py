from orchestrator.contracts import StageContract
from orchestrator.wrappers.base import BaseWrapper


class GeminiAutoWrapper(BaseWrapper):
    contract = StageContract(
        stage="gemini_auto",
        description="Legacy Gemini metadata pipeline",
        branch="common",
        unsafe=True,
        destructive_ops=["writes_metadata_files"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="Gemini_Auto/gemini_auto.py",
    )
