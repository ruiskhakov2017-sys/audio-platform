from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from orchestrator.contracts import StageContract
from orchestrator.runtime_modes import load_runtime_modes
from orchestrator.site_tts.batch import collect_batch_items, normalize_site_story_name, run_site_tts_for_story
from orchestrator.site_tts.colab_batch import (
    drive_kokoro_job_pending_on_drive,
    drive_mp3_wait_skip_requested,
    export_drive_texts,
    resolve_pending_drive_mp3_job_with_local_stub,
    wait_drive_mp3_and_import,
)
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
        artifact_root: Path | None = None,
    ) -> None:
        super().__init__(entrypoint)
        self._root = root_dir.resolve()
        self._artifact_root = (artifact_root if artifact_root is not None else self._root).resolve()
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
        stories_dir: Path | None = None,
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

        if engine == "kokoro_colab_drive":
            if not execute or not allow_real:
                return WrapperResult(
                    ok=True,
                    state="dry-run",
                    message="site_tts: dry-run (kokoro_colab_drive export/wait/import not started)",
                )
            sid_k = str(story_id or "").strip()
            stories_filter: Path | None = None
            if (
                stories_dir is not None
                and stories_dir.is_dir()
                and sid_k.lower().endswith("-site")
            ):
                stories_filter = stories_dir.resolve()
            site_out = (self._artifact_root / "output" / "site").resolve()
            human_launch = None
            try:
                from orchestrator.human_launch_layout import launch_dir_from_site_output_root

                human_launch = launch_dir_from_site_output_root(site_out)
            except Exception:
                human_launch = None
            try:
                exp = export_drive_texts(
                    self._root,
                    limit=None,
                    stories_filter_dir=stories_filter,
                    site_root=site_out,
                    human_launch=human_launch,
                )
            except Exception as exc:
                return WrapperResult(ok=False, state="failed", message=f"site_tts drive export failed: {exc}")
            resume = bool(exp.get("resume_wait_for_pending_job"))
            exported_n = int(exp.get("exported", 0) or 0)
            pending_drive, pending_info = drive_kokoro_job_pending_on_drive(self._root)
            should_wait = exported_n > 0 or resume or pending_drive
            if not should_wait:
                return WrapperResult(ok=True, state="done", message="site_tts drive: nothing to export/import")
            if pending_drive and exported_n <= 0 and not resume:
                print(
                    "[site_tts] Kokoro Drive: job уже на Drive (exported=0, skip re-export) — переходим к ожиданию mp3.",
                    flush=True,
                )
                print(f"[site_tts] pending_reason={pending_info.get('reason', '')} expected={pending_info.get('expected_count', 0)}", flush=True)
            else:
                print(
                    f"[site_tts] Kokoro Drive: export завершён (exported={exported_n}, resume_pending={resume}) — ожидание mp3 на Drive…",
                    flush=True,
                )
            try:
                if drive_mp3_wait_skip_requested():
                    allow_stub = str(os.environ.get("CF_ALLOW_STUB_AUDIO_TEST", "")).strip().lower() in {"1", "true", "yes", "on"}
                    if not allow_stub:
                        return WrapperResult(
                            ok=False,
                            state="failed",
                            message=(
                                "site_tts drive: CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT is forbidden for clean/full/recovery; "
                                "real_audio_required=true"
                            ),
                        )
                    res = resolve_pending_drive_mp3_job_with_local_stub(
                        self._root, site_root=site_out, artifact_root=self._artifact_root
                    )
                else:
                    print(
                        "[HINT] Kokoro Drive: waiting for mp3 on Drive. Stuck? One-shot: "
                        "CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT=1 or empty SKIP_DRIVE_MP3_WAIT.flag next to Content-Factory-Запуск.bat",
                        flush=True,
                    )
                    res = wait_drive_mp3_and_import(
                        self._root,
                        site_root=site_out,
                        artifact_root=self._artifact_root,
                        human_launch=human_launch,
                    )
            except Exception as exc:
                return WrapperResult(ok=False, state="failed", message=f"site_tts drive wait/import failed: {exc}")
            if not res.get("ok", False):
                return WrapperResult(
                    ok=False,
                    state="failed",
                    message=f"site_tts drive wait/import failed: {res.get('message', 'unknown error')}",
                )
            return WrapperResult(
                ok=True,
                state="done",
                message=(
                    f"site_tts drive cycle done (exported={exp.get('exported')}, "
                    f"resume_pending={bool(exp.get('resume_wait_for_pending_job'))}, "
                    f"stories_filter={bool(exp.get('stories_filter_applied'))})"
                ),
            )

        sid = str(story_id or "").strip()
        site_root = (self._artifact_root / "output" / "site").resolve()
        if (
            sid.lower().endswith("-site")
            and stories_dir is not None
            and stories_dir.is_dir()
            and site_root.is_dir()
        ):
            allowed = {
                normalize_site_story_name(p.stem).lower()
                for p in stories_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".txt"
            }
            if allowed:
                queued = collect_batch_items(site_root, project_root=self._root, limit=None)
                items = [
                    it
                    for it in queued
                    if normalize_site_story_name(it.story_name).lower() in allowed
                ]
                if not items:
                    return WrapperResult(
                        ok=True,
                        state="done",
                        message="site_tts: batch (-site): в output/site нет очереди TTS для .txt из stories-dir",
                    )
                batch_rid = uuid.uuid4().hex
                for it in items:
                    res = run_site_tts_for_story(
                        self._root,
                        story_name=it.story_name,
                        modes_config=self._modes_config,
                        execute=bool(execute and allow_real),
                        force=False,
                        run_id=batch_rid,
                        site_output_root=site_root,
                    )
                    if res.status == "success":
                        continue
                    if res.details.get("skipped"):
                        continue
                    return WrapperResult(
                        ok=False,
                        state="failed",
                        message=f"site_tts[{it.story_name}]: {res.message or 'failed'}",
                    )
                return WrapperResult(
                    ok=True,
                    state="done",
                    message=f"site_tts: batch ok engine={engine} stories={len(items)}",
                )

        res = run_site_tts_for_story(
            self._root,
            story_name=story_id,
            modes_config=self._modes_config,
            execute=bool(execute and allow_real),
            force=False,
            run_id=uuid.uuid4().hex,
            site_output_root=site_root,
        )
        if res.status == "success":
            if res.details.get("dry_run"):
                return WrapperResult(ok=True, state="dry-run", message=res.message or "site_tts dry-run")
            return WrapperResult(ok=True, state="done", message=res.message or "site_tts ok")
        if res.details.get("skipped"):
            return WrapperResult(ok=True, state="done", message=res.message or "site_tts skipped")
        return WrapperResult(ok=False, state="failed", message=res.message or "site_tts failed")
