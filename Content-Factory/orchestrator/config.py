from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


DEFAULT_CONFIG_PATH = Path("configs/orchestrator.example.yaml")
DEFAULT_PATHS_REGISTRY = Path("configs/paths.yaml")


@dataclass
class OrchestratorConfig:
    root_dir: Path
    service_dir: Path
    logs_dir: Path
    status_file: Path
    events_file: Path
    reports_dir: Path
    pre_filter_min_minutes: float
    pre_filter_words_per_minute: int
    pre_filter_extensions: list[str]
    default_run_profile: str
    real_stage_whitelist: list[str]
    legacy_entrypoints: Dict[str, str]
    legacy_modules: Dict[str, str]
    data_dirs: Dict[str, str]
    """Relative paths under root_dir for bundled model weights (Fish, etc.)."""
    models_paths: Dict[str, str]
    paths_registry_file: Path


def _minimal_yaml_parse(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    stack = [(0, data)]
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        value = value.strip()
        while stack and indent < stack[-1][0]:
            stack.pop()
        current = stack[-1][1]
        if not value:
            current[key] = {}
            stack.append((indent + 2, current[key]))
            continue
        if value.lower() in {"true", "false"}:
            current[key] = value.lower() == "true"
        else:
            current[key] = value.strip("\"'")
    return data


def load_config(path: Path | None = None) -> OrchestratorConfig:
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = cfg_path.read_text(encoding="utf-8")
    parsed: Dict[str, Any]

    if cfg_path.suffix.lower() == ".json":
        parsed = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore

            parsed = yaml.safe_load(raw)
        except Exception:
            parsed = _minimal_yaml_parse(raw)

    root = Path(parsed.get("root_dir", ".")).resolve()
    service_rel = parsed.get("orchestrator", {}).get("service_dir", ".orchestrator")
    service_dir = (root / service_rel).resolve()
    logs_dir = service_dir / "logs"
    status_file = service_dir / "status.jsonl"
    events_file = service_dir / "events.jsonl"
    reports_dir = service_dir / "reports"
    pre_filter = parsed.get("pre_filter", {})
    min_minutes = float(pre_filter.get("min_minutes", 15))
    words_per_minute = int(pre_filter.get("words_per_minute", 150))
    extensions_raw = pre_filter.get("story_extensions", ".txt")
    if isinstance(extensions_raw, str):
        extensions = [x.strip() for x in extensions_raw.split(",") if x.strip()]
    else:
        extensions = [str(x).strip() for x in (extensions_raw or [".txt"]) if str(x).strip()]
    run_policy = parsed.get("run_policy", {})
    default_profile = str(run_policy.get("default_profile", "dry-run-all"))
    whitelist_raw = run_policy.get("real_stage_whitelist", [])
    if isinstance(whitelist_raw, str):
        whitelist = [x.strip() for x in whitelist_raw.split(",") if x.strip()]
    else:
        whitelist = [str(x).strip() for x in (whitelist_raw or []) if str(x).strip()]
    paths_file = (root / DEFAULT_PATHS_REGISTRY).resolve()
    paths_registry: Dict[str, Any] = {}
    if paths_file.exists():
        try:
            import yaml  # type: ignore

            paths_registry = yaml.safe_load(paths_file.read_text(encoding="utf-8")) or {}
        except Exception:
            paths_registry = _minimal_yaml_parse(paths_file.read_text(encoding="utf-8"))
    legacy_modules_raw = paths_registry.get("legacy_modules", {}) if isinstance(paths_registry, dict) else {}
    legacy_modules = {
        str(k).strip(): str(v).strip() for k, v in (legacy_modules_raw or {}).items() if str(k).strip() and str(v).strip()
    }
    data_dirs_raw = paths_registry.get("data_dirs", {}) if isinstance(paths_registry, dict) else {}
    data_dirs = {str(k).strip(): str(v).strip() for k, v in (data_dirs_raw or {}).items() if str(k).strip() and str(v).strip()}
    models_raw = paths_registry.get("models", {}) if isinstance(paths_registry, dict) else {}
    models_paths = {str(k).strip(): str(v).strip() for k, v in (models_raw or {}).items() if str(k).strip() and str(v).strip()}

    module_entry_rel = {
        "bulk_text_cleaner": "clean_stories.py",
        "gemini_auto": "gemini_auto.py",
        "site_tts": "main.py",
        "youtube_tts": "main.py",
        "content_combiner": "content_combiner.py",
        "youtube_selection": "gemini_auto.py",
        "youtube_safe_text": "gemini_auto.py",
        "director20": "main.py",
        "autovideo": "main.py",
        "autopublisher": "publish_stories.py",
    }
    stage_to_module_key = {
        "bulk_text_cleaner": "cleaner",
        "gemini_auto": "gemini_auto",
        "site_tts": "elevenlabs",
        "youtube_tts": "elevenlabs",
        "content_combiner": "content_combiner",
        "youtube_selection": "youtube_selection",
        "youtube_safe_text": "youtube_tts",
        "director20": "director_2_0",
        "autovideo": "autovideo",
        "autopublisher": "autopublisher",
    }
    legacy: Dict[str, str] = {}
    for stage, rel_entry in module_entry_rel.items():
        module_key = stage_to_module_key[stage]
        module_rel = legacy_modules.get(module_key, "")
        if module_rel:
            legacy[stage] = f"{module_rel}/{rel_entry}".replace("\\", "/")
    legacy_overrides = parsed.get("legacy_entrypoints", {})
    for k, v in (legacy_overrides or {}).items():
        if str(v).strip():
            legacy[str(k).strip()] = str(v).strip()

    return OrchestratorConfig(
        root_dir=root,
        service_dir=service_dir,
        logs_dir=logs_dir,
        status_file=status_file,
        events_file=events_file,
        reports_dir=reports_dir,
        pre_filter_min_minutes=min_minutes,
        pre_filter_words_per_minute=words_per_minute,
        pre_filter_extensions=extensions or [".txt"],
        default_run_profile=default_profile,
        real_stage_whitelist=whitelist,
        legacy_entrypoints=legacy,
        legacy_modules=legacy_modules,
        data_dirs=data_dirs,
        models_paths=models_paths,
        paths_registry_file=paths_file,
    )
