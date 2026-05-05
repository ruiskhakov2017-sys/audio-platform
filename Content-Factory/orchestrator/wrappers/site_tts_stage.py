from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from orchestrator.contracts import StageContract
from orchestrator.runtime_modes import load_runtime_modes
from orchestrator.site_tts.batch import run_site_tts_for_story
from orchestrator.wrappers.base import BaseWrapper, WrapperResult


class SiteTtsStageWrapper(BaseWrapper):
    """
    Site TTS: Kokoro (модульный адаптер) или legacy ElevenLabs через subprocess.
    VibeVoice / неподключённые движки — partial_connected на уровне runner.
    """

    contract = StageContract(
        stage="site_tts",
        description="Site TTS (Kokoro modular или legacy ElevenLabs)",
        branch="site",
        unsafe=True,
        destructive_ops=["writes_audio_files", "writes_runs_tts_workspace"],
        dry_run_only=False,
        external_dependency=True,
        entrypoint="",
    )

    def __init__(
        self,
        entrypoint: Path | None = None,
        *,
        root_dir: Path,
        modes_config: Path | None = None,
    ) -> None:
        super().__init__(entrypoint)
        self._root = root_dir.resolve()
        self._modes_config = (modes_config or (self._root / "configs" / "runtime_modes.yaml")).resolve()

    def validate(self) -> list[str]:
        issues: list[str] = []
        modes = load_runtime_modes(self._modes_config)
        engine = str(modes.get("site_tts_engine", "")).strip().lower()
        if engine == "elevenlabs":
            if self.entrypoint and not self.entrypoint.exists():
                issues.append(f"Entrypoint not found: {self.entrypoint}")
        return issues

    def run(
        self,
        *,
        story_id: str,
        pipeline: str,
        execute: bool,
        allow_real: bool,
    ) -> WrapperResult:
        if execute and not allow_real:
            return WrapperResult(
                ok=True,
                state="blocked_external",
                message="site_tts: execute requested but stage is not whitelisted",
            )
        modes = load_runtime_modes(self._modes_config)
        engine = str(modes.get("site_tts_engine", "")).strip().lower()

        if engine == "elevenlabs":
            if execute and self.entrypoint:
                proc = subprocess.run(
                    [sys.executable, str(self.entrypoint)],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    msg = proc.stderr.strip() or proc.stdout.strip() or "subprocess failed"
                    return WrapperResult(ok=False, state="failed", message=f"site_tts: {msg}")
                return WrapperResult(
                    ok=True,
                    state="done",
                    message="site_tts: legacy ElevenLabs subprocess finished successfully",
                )
            return WrapperResult(
                ok=True,
                state="dry-run",
                message="site_tts: dry-run (elevenlabs subprocess not started)",
            )

        res = run_site_tts_for_story(
            self._root,
            story_name=story_id,
            modes_config=self._modes_config,
            execute=bool(execute and allow_real),
            force=False,
            run_id=uuid.uuid4().hex,
        )
        if res.status == "success":
            if res.details.get("dry_run"):
                return WrapperResult(ok=True, state="dry-run", message=res.message or "site_tts dry-run")
            return WrapperResult(ok=True, state="done", message=res.message or "site_tts ok")
        if res.details.get("skipped"):
            return WrapperResult(ok=True, state="done", message=res.message or "site_tts skipped")
        return WrapperResult(ok=False, state="failed", message=res.message or "site_tts failed")
