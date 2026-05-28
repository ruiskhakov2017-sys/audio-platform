from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from orchestrator.contracts import StageContract
from orchestrator.phase_a import _load_gemini_registry
from orchestrator.wrappers.base import BaseWrapper, WrapperResult

_GEM_URL_RE = re.compile(
    r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$",
    re.IGNORECASE,
)


def _pick_first_gemini_bot_url(repo_root: Path) -> tuple[str, str]:
    """Первая валидная ссылка из registry (чтобы дочерний gemini_auto не зависел от мусора в GEMINI_STAGE_KEY в окружении ОС)."""
    for fname in ("gemini_bots_registry.yaml", "gemini_bots_registry.example.yaml"):
        path = (repo_root / "configs" / fname).resolve()
        if not path.is_file():
            continue
        bots = _load_gemini_registry(path)
        for stage_key in ("general_selection", "site_info_builder"):
            for bot in bots:
                if not isinstance(bot, dict):
                    continue
                u = str(bot.get(stage_key, "")).strip()
                if u and _GEM_URL_RE.fullmatch(u):
                    return u, stage_key
    raise RuntimeError(
        f"Не найден ни один Gem-URL в configs/gemini_bots_registry.yaml "
        f"или gemini_bots_registry.example.yaml (проверь файл и ключи general_selection / site_info_builder)."
    )


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
                message=f"{self.contract.stage}: execute requested but stage is not whitelisted",
            )
        if execute and self.contract.dry_run_only:
            return WrapperResult(
                ok=True,
                state="partial_connected",
                message=f"{self.contract.stage}: stage is dry-run-only in V1",
            )
        if execute and self.entrypoint:
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            if pipeline == "site":
                site_root = (self._artifact_root / "output" / "site").resolve()
                site_root.mkdir(parents=True, exist_ok=True)
                env["GEMINI_STORIES_DIR"] = str(site_root)
                gem_url, stage_key = _pick_first_gemini_bot_url(self._root)
                env["GEMINI_URL"] = gem_url
                env["GEMINI_STAGE_KEY"] = stage_key
            elif stories_dir is not None:
                env["GEMINI_STORIES_DIR"] = str(stories_dir.resolve())
            proc = subprocess.run(
                [sys.executable, str(self.entrypoint)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
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
