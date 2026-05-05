from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class StageContract:
    stage: str
    description: str
    branch: str = "common"
    unsafe: bool = False
    destructive_ops: List[str] = field(default_factory=list)
    dry_run_only: bool = True
    external_dependency: bool = False
    entrypoint: str = ""
