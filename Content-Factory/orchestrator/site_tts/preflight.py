from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from orchestrator.check_result import CheckResult
from orchestrator.config import OrchestratorConfig
from orchestrator.runtime_modes import load_runtime_modes
from orchestrator.site_tts.config import load_site_tts_settings


def run_site_tts_preflight(
    config: OrchestratorConfig,
    *,
    modes_config: Path,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    modes = load_runtime_modes(modes_config)
    engine = str(modes.get("site_tts_engine", "")).strip().lower()
    runtime = str(modes.get("site_tts_runtime", "")).strip().lower()

    if engine != "kokoro":
        results.append(CheckResult(True, f"site_tts preflight: engine={engine} (kokoro-only checks skipped)"))
        return results

    spec = importlib.util.find_spec("kokoro")
    results.append(
        CheckResult(
            spec is not None,
            "kokoro: python package importable" if spec else "kokoro: python package not found (pip install kokoro soundfile)",
        )
    )

    def _which(name: str) -> str | None:
        return shutil.which(name)

    es = _which("espeak-ng") or _which("espeak-ng.exe")
    results.append(CheckResult(bool(es), f"espeak-ng in PATH: {es or 'MISSING'}"))

    ff = _which("ffmpeg")
    results.append(CheckResult(bool(ff), f"ffmpeg in PATH: {ff or 'MISSING'}"))

    site_out = (config.root_dir / "output" / "site").resolve()
    ok_site = site_out.is_dir()
    results.append(CheckResult(ok_site, f"output/site exists: {site_out}"))

    runs_tts = (config.root_dir / "runs" / "tts").resolve()
    writable = False
    try:
        runs_tts.mkdir(parents=True, exist_ok=True)
        probe = runs_tts / ".write_probe_delete_me"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError as exc:
        results.append(CheckResult(False, f"runs/tts not writable: {runs_tts} ({exc})"))
    else:
        results.append(CheckResult(writable, f"runs/tts writable: {runs_tts}"))

    try:
        load_site_tts_settings(config.root_dir)
        results.append(CheckResult(True, "configs/site_tts.yaml readable"))
    except Exception as exc:
        results.append(CheckResult(False, f"configs/site_tts.yaml: {exc}"))

    if runtime != "local":
        results.append(
            CheckResult(
                True,
                f"[WARN] site_tts_runtime={runtime} при engine=kokoro ожидается local для локального inference",
            )
        )

    return results
