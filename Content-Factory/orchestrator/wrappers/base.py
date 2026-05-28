from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from orchestrator.contracts import StageContract


@dataclass
class WrapperResult:
    ok: bool
    state: str
    message: str


class BaseWrapper:
    contract: StageContract

    def __init__(self, entrypoint: Path | None = None) -> None:
        self.entrypoint = entrypoint

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.contract.entrypoint and self.entrypoint and not self.entrypoint.exists():
            issues.append(f"Entrypoint not found: {self.entrypoint}")
        return issues

    def plan(self, story_id: str, pipeline: str) -> Dict[str, str]:
        return {
            "story_id": story_id,
            "pipeline": pipeline,
            "stage": self.contract.stage,
            "branch": self.contract.branch,
            "unsafe": str(self.contract.unsafe),
            "dry_run_only": str(self.contract.dry_run_only),
            "external_dependency": str(self.contract.external_dependency),
            "entrypoint": str(self.entrypoint or self.contract.entrypoint),
        }

    def run(
        self,
        *,
        story_id: str,
        pipeline: str,
        execute: bool,
        allow_real: bool,
        stories_dir: Path | None = None,
    ) -> WrapperResult:
        if execute and not allow_real:
            return WrapperResult(
                ok=True,
                state="blocked_external",
                message=f"{self.contract.stage}: execute requested but stage is not whitelisted",
            )
        if execute and self.contract.dry_run_only:
            return WrapperResult(
                ok=True,
                state="partial_connected",
                message=f"{self.contract.stage}: stage is dry-run-only in V1",
            )
        if execute and self.entrypoint:
            proc = subprocess.run(
                [sys.executable, str(self.entrypoint)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                msg = proc.stderr.strip() or proc.stdout.strip() or "subprocess failed"
                return WrapperResult(ok=False, state="failed", message=f"{self.contract.stage}: {msg}")
            return WrapperResult(
                ok=True,
                state="done",
                message=f"{self.contract.stage}: subprocess finished successfully",
            )
        return WrapperResult(
            ok=True,
            state="dry-run",
            message=f"{self.contract.stage}: dry-run contract check passed",
        )
