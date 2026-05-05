from __future__ import annotations

from pathlib import Path
from typing import List

from orchestrator.check_result import CheckResult
from orchestrator.config import OrchestratorConfig
from orchestrator.phase_a import _scan_txt, count_ignored_nested_txt
from orchestrator.runtime_modes import load_runtime_modes
from orchestrator.site_tts.preflight import run_site_tts_preflight
from orchestrator.wrappers import build_wrappers_for_pipeline


_MODEL_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".gguf", ".onnx")


def _dir_size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def _fish_s2_pro_model_check(config: OrchestratorConfig) -> CheckResult:
    """
    Informational only: missing weights must not fail site preparation / preflight.
    """
    rel = config.models_paths.get("fish_audio_s2_pro", "").strip() or "models/fish_audio/fish-s2-pro"
    model_dir = (config.root_dir / rel).resolve()
    if not model_dir.is_dir():
        return CheckResult(
            True,
            f"[WARN] Fish Audio S2 Pro: папка модели не найдена (ожидалось {model_dir}). "
            "Скачайте веса в models/fish_audio/fish-s2-pro/ (см. configs/paths.yaml). TTS на RunPod пока не подключён.",
        )
    size_b = _dir_size_bytes(model_dir)
    size_gb = size_b / (1024**3)
    model_files = [
        p
        for p in model_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _MODEL_SUFFIXES
    ]
    n_files = len(model_files)
    exts_found = sorted({p.suffix.lower() for p in model_files})
    sample = ", ".join(exts_found[:12]) if exts_found else "нет известных расширений"
    if n_files == 0:
        return CheckResult(
            True,
            f"[WARN] Fish Audio S2 Pro: {model_dir} (~{size_gb:.2f} GiB) без типичных weight-файлов "
            f"({_MODEL_SUFFIXES}). Проверьте содержимое.",
        )
    return CheckResult(
        True,
        f"Fish Audio S2 Pro: {model_dir} (~{size_gb:.2f} GiB), weight-файлов: {n_files}, расширения: {sample}",
    )


def run_preflight(
    config: OrchestratorConfig,
    *,
    pipeline: str = "full",
    execute: bool = False,
    run_profile: str = "dry-run-all",
    allow_real_stages: list[str] | None = None,
    stories_dir: Path | None = None,
    story_extensions: list[str] | None = None,
) -> List[CheckResult]:
    results: List[CheckResult] = []

    results.append(CheckResult(config.root_dir.exists(), f"root_dir exists: {config.root_dir}"))
    results.append(CheckResult(True, f"service_dir: {config.service_dir}"))
    results.append(CheckResult(True, f"status file: {config.status_file}"))
    results.append(CheckResult(True, f"events file: {config.events_file}"))
    results.append(CheckResult(config.paths_registry_file.exists(), f"paths registry: {config.paths_registry_file}"))
    required_data_dirs = ["input_stories", "runs", "output", "logs", "archive"]
    for key in required_data_dirs:
        rel = config.data_dirs.get(key, "")
        ok = bool(rel) and (config.root_dir / rel).exists()
        results.append(CheckResult(ok, f"data_dir[{key}]: {config.root_dir / rel if rel else 'missing in paths.yaml'}"))
    if config.legacy_modules:
        for key, rel in sorted(config.legacy_modules.items()):
            results.append(CheckResult((config.root_dir / rel).exists(), f"legacy_module[{key}]: {config.root_dir / rel}"))

    input_rel = str(config.data_dirs.get("input_stories", "stories/input") or "stories/input").strip()
    active_input = (stories_dir or (config.root_dir / input_rel)).resolve()
    exts = story_extensions if story_extensions is not None else list(config.pre_filter_extensions)
    root_txt = _scan_txt(active_input, exts)
    nested_ignored = count_ignored_nested_txt(active_input, exts)
    results.append(CheckResult(active_input.exists() and active_input.is_dir(), f"active_input_dir: {active_input}"))
    results.append(CheckResult(True, f"intake_root_txt_count: {len(root_txt)}"))
    results.append(CheckResult(True, f"intake_nested_txt_ignored_count: {nested_ignored}"))

    results.append(_fish_s2_pro_model_check(config))

    if pipeline in {"site", "full", "all"}:
        modes_path = (config.root_dir / "configs" / "runtime_modes.yaml").resolve()
        rm = load_runtime_modes(modes_path)
        if str(rm.get("site_tts_engine", "")).strip().lower() == "kokoro":
            results.extend(run_site_tts_preflight(config, modes_config=modes_path))

    allowed_real = set(config.real_stage_whitelist + (allow_real_stages or []))
    wrappers = build_wrappers_for_pipeline(pipeline, config.root_dir, config.legacy_entrypoints)
    for w in wrappers:
        issues = w.validate()
        if issues:
            for issue in issues:
                results.append(CheckResult(False, f"{w.contract.stage}: {issue}"))
        else:
            results.append(CheckResult(True, f"{w.contract.stage}: entrypoint ok"))
        if w.contract.unsafe:
            results.append(CheckResult(True, f"{w.contract.stage}: marked unsafe"))
        if execute and run_profile != "dry-run-all" and w.contract.stage in allowed_real and w.contract.dry_run_only:
            results.append(
                CheckResult(
                    True,
                    f"{w.contract.stage}: stage whitelisted for real but currently partial_connected (dry-run-only)",
                )
            )
        if execute and w.contract.stage not in allowed_real:
            results.append(CheckResult(True, f"{w.contract.stage}: real execution blocked (not whitelisted)"))

    if not execute:
        results.append(CheckResult(True, "execute mode not enabled (safe default)"))
    else:
        results.append(CheckResult(True, f"execute flag enabled with run_profile={run_profile}"))
    return results
