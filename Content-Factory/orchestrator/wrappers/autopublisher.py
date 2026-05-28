from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from orchestrator.contracts import StageContract
from orchestrator.site_publish.env_doctor import _read_env_file, run_site_publish_env_doctor
from orchestrator.wrappers.base import WrapperResult
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

    def __init__(
        self,
        entrypoint: Path | None = None,
        *,
        root_dir: Path | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        super().__init__(entrypoint)
        if root_dir is not None:
            self._root = root_dir.resolve()
        elif entrypoint is not None:
            # .../<root>/legacy/autopublisher/publish_stories.py
            self._root = entrypoint.resolve().parents[2]
        else:
            self._root = Path(".").resolve()
        self._artifact_root = (artifact_root if artifact_root is not None else self._root).resolve()

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
                message="autopublisher: execute requested but stage is not whitelisted",
            )
        if not execute:
            return WrapperResult(
                ok=True,
                state="dry-run",
                message=(
                    "autopublisher: dry-run (headless publish not started). "
                    "Will bridge output/site -> To_Publish and publish only stories with info+mp3+jpg when execute is enabled."
                ),
            )
        if self.entrypoint is None:
            return WrapperResult(ok=False, state="failed", message="autopublisher: entrypoint is not configured")

        env_doc = run_site_publish_env_doctor(
            content_factory_root=self._root,
            dirtysecrets_root=None,
            write_env_file=False,
        )
        if not bool(env_doc.get("ok")):
            bl = ", ".join(sorted(str(x) for x in (env_doc.get("blockers") or [])))
            return WrapperResult(
                ok=False,
                state="failed",
                message=f"autopublisher: publish env preflight failed blockers=[{bl}] report={env_doc.get('report_path', '')}",
            )

        output_site = (self._artifact_root / "output" / "site").resolve()
        if self._artifact_root.resolve() != self._root.resolve():
            to_publish_dir = (self._artifact_root / "_autopublisher_To_Publish").resolve()
        else:
            to_publish_dir = (self.entrypoint.resolve().parent / "To_Publish").resolve()
        to_publish_dir.mkdir(parents=True, exist_ok=True)
        stub = (self._root / "orchestrator" / "site_tts" / "_silent_stub.mp3").resolve()
        if stub.is_file():
            try:
                stub_bytes = stub.read_bytes()
            except OSError:
                stub_bytes = b""
            if stub_bytes:
                stub_hits: list[str] = []
                story_dirs = [p for p in output_site.iterdir() if p.is_dir()] if output_site.is_dir() else []
                for story_dir in sorted(story_dirs, key=lambda p: p.name.lower()):
                    mp3 = story_dir / f"{story_dir.name}.mp3"
                    if not mp3.is_file():
                        continue
                    try:
                        if mp3.read_bytes() == stub_bytes:
                            stub_hits.append(story_dir.name)
                    except OSError:
                        continue
                if stub_hits:
                    return WrapperResult(
                        ok=False,
                        state="failed",
                        message=(
                            "publish blocked: stub audio; real_audio_required=true; "
                            f"stories={stub_hits[:20]}"
                        ),
                    )
        result_jsonl = (self._root / ".orchestrator" / "logs" / "site_publish_results.jsonl").resolve()
        cmd = [
            sys.executable,
            str(self.entrypoint),
            "--headless",
            "--bridge-output-site",
            str(output_site),
            "--to-publish-dir",
            str(to_publish_dir),
            "--result-jsonl",
            str(result_jsonl),
        ]
        env = os.environ.copy()
        site_publish_file = (self._root / ".env.site_publish").resolve()
        env.update(_read_env_file(site_publish_file))
        if not (env.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip():
            sk = (env.get("SUPABASE_SECRET_KEY", "") or "").strip()
            if sk:
                env["SUPABASE_SERVICE_ROLE_KEY"] = sk
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self._root),
            env=env,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            if msg:
                print(f"[autopublisher] exit={proc.returncode} log:\n{msg}", flush=True)
            return WrapperResult(
                ok=False,
                state="failed",
                message=f"autopublisher: exit={proc.returncode} {msg[:2000] or 'subprocess failed'}",
            )
        return WrapperResult(
            ok=True,
            state="done",
            message=f"autopublisher: published from output/site via headless bridge; results={result_jsonl}",
        )
