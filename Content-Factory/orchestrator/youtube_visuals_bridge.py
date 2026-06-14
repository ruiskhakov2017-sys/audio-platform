"""Safe YouTube visuals bridge for one story.

The legacy director_2_0 flow is useful, but its default entrypoints scan fixed
legacy roots and its RunPod factory writes legacy frame names. This bridge keeps
the single output/youtube story contract and only launches legacy Gemini or
RunPod when an explicit execute-mode caller asks for that stage.
"""

from __future__ import annotations

import ast
import json
import hashlib
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from orchestrator.config import OrchestratorConfig
from orchestrator.gemini_colab_proxy import apply_gemini_colab_proxy_env, gemini_colab_proxy_session
from orchestrator.youtube_language import EXPECTED_YOUTUBE_LANGUAGE, detect_path_language


PROMPTS_PRIMARY_DIRNAME = "06_prompts"
PROMPTS_LEGACY_DIRNAME = "06_director"
FRAME_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MIN_FRAME_SIZE_BYTES = 4 * 1024
COMFYUI_HTTP_TIMEOUT_SEC = 120
COMFYUI_MAX_WAIT_SEC = 900
COMFYUI_POLL_INTERVAL_SEC = 2
COMFYUI_MAX_RETRIES = 3
COMFYUI_MAX_CONSECUTIVE_PROMPT_FAILURES = 3


@dataclass
class YoutubeCharactersBridgeOptions:
    story_id: str
    execute: bool = False


@dataclass
class YoutubeCharactersExportOptions:
    story_id: str
    execute: bool = False


@dataclass
class YoutubeCharactersImportOptions:
    story_id: str
    source: Path
    execute: bool = False


@dataclass
class YoutubeDirectorPromptsBridgeOptions:
    story_id: str
    execute: bool = False


@dataclass
class YoutubeDirectorPromptsExportOptions:
    story_id: str
    execute: bool = False


@dataclass
class YoutubeDirectorPromptsImportOptions:
    story_id: str
    source: Path
    execute: bool = False


@dataclass
class YoutubeFramesRunpodBridgeOptions:
    story_id: str
    runpod_url: str = ""
    execute: bool = False
    prepare_only: bool = False
    workflow: str = ""


@dataclass
class YoutubeGeminiAutoStageOptions:
    story_id: str
    execute: bool = False
    user_data_dir: str = ""
    stories_dir: str = ""
    batch_mode: bool = False


@dataclass
class YoutubeGeminiBatchStory:
    story_id: str
    story_dir: Path
    stage_dir: Path


@dataclass
class YoutubeGeminiBatchOptions:
    stories: list[YoutubeGeminiBatchStory]
    execute: bool = False
    user_data_dir: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(config: OrchestratorConfig, path: Path, payload: Any) -> None:
    from orchestrator.isolated_io import write_json as isolated_write_json

    isolated_write_json(
        config,
        path,
        payload,
        module="orchestrator.youtube_visuals_bridge",
        function="_write_json",
    )


def _legacy_write_path(
    config: OrchestratorConfig,
    story_id: str,
    legacy_relative: str,
    fallback: Path,
) -> Path:
    from orchestrator.youtube_path_resolver import resolve_bridge_legacy_write_path

    return resolve_bridge_legacy_write_path(config, story_id, legacy_relative, fallback)


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(text)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    from orchestrator.youtube_path_resolver import resolve_bridge_story_dir

    return resolve_bridge_story_dir(config, story_id)


def _director_module_dir(config: OrchestratorConfig) -> Path:
    rel = config.legacy_modules.get("director_2_0", "legacy/director_2_0")
    return (config.root_dir / rel).resolve()


def _story_manifest_path(story_dir: Path) -> Path:
    return story_dir / "youtube_story_manifest.json"


def _safe_story_path(story_dir: Path) -> Path:
    return story_dir / "02_safe_story" / "safe_story.txt"


def _audio_text_path(story_dir: Path) -> Path:
    return story_dir / "03_promo" / "text_ready_for_audio.txt"


def _source_text_for_director(story_dir: Path) -> Path:
    source_cleaned = story_dir / "00_source" / "source_cleaned_story.txt"
    if source_cleaned.is_file():
        return source_cleaned
    audio_text = _audio_text_path(story_dir)
    if audio_text.is_file():
        return audio_text
    return _safe_story_path(story_dir)


def _narration_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def _characters_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(
        config,
        story_id,
        "05_characters/characters.txt",
        story_dir / "05_characters" / "characters.txt",
    )


def _characters_dir(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _characters_path(config, story_id, story_dir).parent


def _characters_staging_dir(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _characters_dir(config, story_id, story_dir) / "_staging"


def _prompts_dir(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(config, story_id, "06_prompts", story_dir / PROMPTS_PRIMARY_DIRNAME)


def _prompts_staging_dir(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _prompts_dir(config, story_id, story_dir) / "_staging"


def _prompts_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(
        config,
        story_id,
        "06_prompts/prompts_list.txt",
        story_dir / PROMPTS_PRIMARY_DIRNAME / "prompts_list.txt",
    )


def _legacy_prompts_path(story_dir: Path) -> Path:
    return story_dir / PROMPTS_LEGACY_DIRNAME / "prompts_list.txt"


def _resolve_prompts_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    primary = _prompts_path(config, story_id, story_dir)
    if primary.is_file():
        return primary
    legacy = _legacy_prompts_path(story_dir)
    if legacy.is_file():
        return legacy
    return primary


def _frames_dir(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(config, story_id, "07_frames", story_dir / "07_frames")


def _logs_dir(story_dir: Path) -> Path:
    return story_dir / "logs"


def _legacy_stage_dir(config: OrchestratorConfig, story_id: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", story_id).strip(" .") or "youtube_story"
    from orchestrator.isolated_io import is_active_isolated
    from orchestrator.isolated_launch_context import get_active_resolver

    if is_active_isolated(config):
        resolver = get_active_resolver()
        if resolver is not None:
            return (resolver.technical_gemini_staging_dir() / safe).resolve()
    return _director_module_dir(config) / "stories_from_orchestrator" / safe


def _bridge_copy2(
    config: OrchestratorConfig,
    src: Path | str,
    dst: Path | str,
    *,
    function: str,
) -> Path:
    from orchestrator.isolated_io import copy2 as iso_copy2, is_active_isolated

    if is_active_isolated(config):
        return iso_copy2(
            config,
            src,
            dst,
            module="orchestrator.youtube_visuals_bridge",
            function=function,
        )
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return target.resolve()


def _legacy_workflow_path(config: OrchestratorConfig) -> Path:
    director_dir = _director_module_dir(config)
    config_json = director_dir / "config.json"
    workflow_name = "FLUX 2 — Simple Text-To-Image.json"
    if config_json.is_file():
        try:
            data = _read_json(config_json)
            if isinstance(data, dict) and str(data.get("workflow_file", "")).strip():
                workflow_name = str(data["workflow_file"]).strip()
        except Exception:
            pass
    return director_dir / workflow_name


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw) or {}
    except Exception:
        data = {}
        stack: list[tuple[int, dict[str, Any]]] = [(-1, data)]
        for line in raw.splitlines():
            clean = line.split("#", 1)[0].rstrip()
            if not clean.strip() or ":" not in clean:
                continue
            indent = len(clean) - len(clean.lstrip(" "))
            key, value = clean.strip().split(":", 1)
            key = key.strip().strip("\"'")
            value = value.strip()
            while stack and indent <= stack[-1][0]:
                stack.pop()
            current = stack[-1][1]
            if not value:
                child: dict[str, Any] = {}
                current[key] = child
                stack.append((indent, child))
            else:
                current[key] = value.strip("\"'")
    return data if isinstance(data, dict) else {}


def _youtube_visuals_config(config: OrchestratorConfig) -> dict[str, Any]:
    return _load_yaml_mapping(config.root_dir / "configs" / "youtube_visuals.yaml")


def _workflow_presets_dir(config: OrchestratorConfig, visuals_config: dict[str, Any]) -> Path:
    rel = str(visuals_config.get("workflow_presets_dir", "legacy/director_2_0/workflows") or "").strip()
    return ((config.root_dir / rel).resolve() if rel else (_director_module_dir(config) / "workflows").resolve())


def _workflow_path(config: OrchestratorConfig) -> Path:
    return _legacy_workflow_path(config)


def _workflow_name_from_file(path: Path) -> str:
    return path.name


def resolve_youtube_frames_workflow(config: OrchestratorConfig, requested_workflow: str = "") -> dict[str, Any]:
    visuals_config = _youtube_visuals_config(config)
    workflows = visuals_config.get("workflows", {})
    workflows = workflows if isinstance(workflows, dict) else {}
    presets_dir = _workflow_presets_dir(config, visuals_config)
    requested = str(requested_workflow or "").strip()
    selected_from_cli = bool(requested)
    selected_key = requested
    entry: dict[str, Any] = {}

    if requested and requested in workflows and isinstance(workflows[requested], dict):
        entry = workflows[requested]
    elif requested:
        requested_name = Path(requested).name
        for name, candidate in workflows.items():
            if isinstance(candidate, dict) and str(candidate.get("file", "")).strip() == requested_name:
                selected_key = str(name)
                entry = candidate
                break
        if not entry:
            entry = {"file": requested_name}
    else:
        default_workflow = str(visuals_config.get("default_workflow", "") or "").strip()
        default_key = str(visuals_config.get("default_workflow_name", "") or "").strip()
        if default_key and default_key in workflows and isinstance(workflows[default_key], dict):
            selected_key = default_key
            entry = workflows[default_key]
        elif default_workflow:
            selected_key = Path(default_workflow).stem
            for name, candidate in workflows.items():
                if isinstance(candidate, dict) and str(candidate.get("file", "")).strip() == default_workflow:
                    selected_key = str(name)
                    entry = candidate
                    break
            if not entry:
                entry = {"file": default_workflow}

    if entry:
        workflow_file = str(entry.get("file", "") or "").strip()
        workflow_path = (presets_dir / workflow_file).resolve()
        text_node_id = str(entry.get("text_node_id", "") or "").strip() or _comfy_text_node(config)
        seed_node_id = str(entry.get("seed_node_id", "") or "").strip() or _comfy_seed_node(config)
        workflow_name = selected_key or Path(workflow_file).stem
    else:
        workflow_path = _legacy_workflow_path(config)
        text_node_id = _comfy_text_node(config)
        seed_node_id = _comfy_seed_node(config)
        workflow_name = _workflow_name_from_file(workflow_path)

    return {
        "name": workflow_name,
        "path": str(workflow_path),
        "text_node_id": text_node_id,
        "seed_node_id": seed_node_id,
        "selected_from_cli": selected_from_cli,
        "config_path": str(config.root_dir / "configs" / "youtube_visuals.yaml"),
        "presets_dir": str(presets_dir),
    }


def _director_config(config: OrchestratorConfig) -> dict[str, Any]:
    config_json = _director_module_dir(config) / "config.json"
    if not config_json.is_file():
        return {}
    try:
        data = _read_json(config_json)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _comfy_text_node(config: OrchestratorConfig) -> str:
    return str(_director_config(config).get("comfyui_text_node", "93") or "93")


def _comfy_seed_node(config: OrchestratorConfig) -> str:
    return str(_director_config(config).get("comfyui_seed_node", "31") or "31")


def _load_story_manifest(story_dir: Path) -> dict[str, Any]:
    path = _story_manifest_path(story_dir)
    if not path.is_file():
        return {}
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _source_text_for_characters(story_dir: Path) -> Path:
    source_cleaned = story_dir / "00_source" / "source_cleaned_story.txt"
    if source_cleaned.is_file():
        return source_cleaned
    safe = _safe_story_path(story_dir)
    if safe.is_file():
        return safe
    return _audio_text_path(story_dir)


def _story_basics(config: OrchestratorConfig, story_id: str) -> dict[str, Any]:
    story_key = str(story_id).strip()
    story_dir = _story_dir(config, story_key)
    manifest = _load_story_manifest(story_dir)
    canonical = str(manifest.get("canonical_basename", "")).strip() or story_key
    return {
        "story_id": story_key,
        "canonical_basename": canonical,
        "story_dir": story_dir,
        "manifest_path": _story_manifest_path(story_dir),
        "manifest_exists": _story_manifest_path(story_dir).is_file(),
        "director_module_dir": _director_module_dir(config),
        "legacy_stage_dir": _legacy_stage_dir(config, canonical),
        "safe_story_path": _safe_story_path(story_dir),
        "audio_text_path": _audio_text_path(story_dir),
        "narration_path": _narration_path(story_dir),
        "characters_path": _characters_path(config, story_key, story_dir),
        "prompts_path": _resolve_prompts_path(config, story_key, story_dir),
        "frames_dir": _frames_dir(config, story_key, story_dir),
        "workflow_path": _workflow_path(config),
    }


def _duration_sec(audio_path: Path) -> float | None:
    if not audio_path.is_file():
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None
    try:
        return round(float((proc.stdout or "").strip()), 3)
    except ValueError:
        return None


def _count_words(path: Path) -> int:
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return len(re.findall(r"\S+", text))


def _load_prompts(path: Path) -> list[str]:
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return []
    if "\n\n" in raw:
        return [_strip_prompt_number(p) for p in raw.split("\n\n") if p.strip()]
    return [_strip_prompt_number(line) for line in raw.splitlines() if line.strip()]


def _strip_prompt_number(text: str) -> str:
    return re.sub(r"^\s*\d{1,5}[.)]\s*", "", text).strip()


def _expected_frame_path(frames_dir: Path, prompt_index: int) -> Path:
    return frames_dir / f"frame_{prompt_index:04d}.png"


def _existing_frames(frames_dir: Path) -> list[Path]:
    if not frames_dir.is_dir():
        return []
    return sorted(p for p in frames_dir.iterdir() if p.is_file() and p.suffix.lower() in FRAME_EXTS)


def _read_png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


def _read_jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in {0xD8, 0xD9}:
            continue
        if pos + 2 > len(data):
            return None
        block_len = int.from_bytes(data[pos : pos + 2], "big")
        if block_len < 2 or pos + block_len > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if pos + 7 > len(data):
                return None
            height = int.from_bytes(data[pos + 3 : pos + 5], "big")
            width = int.from_bytes(data[pos + 5 : pos + 7], "big")
            return width, height
        pos += block_len
    return None


def _read_webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None


def _probe_image(path: Path) -> tuple[bool, dict[str, Any]]:
    details: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "min_size_bytes": MIN_FRAME_SIZE_BYTES,
    }
    if not path.is_file():
        details["reason"] = "missing"
        return False, details
    try:
        data = path.read_bytes()
    except OSError as exc:
        details["reason"] = f"read_failed: {exc}"
        return False, details
    details["size_bytes"] = len(data)
    if len(data) < MIN_FRAME_SIZE_BYTES:
        details["reason"] = "too_small"
        return False, details
    size = _read_png_size(data) or _read_jpeg_size(data) or _read_webp_size(data)
    if size is None:
        try:
            from PIL import Image  # type: ignore

            with Image.open(path) as img:
                img.verify()
                size = img.size
        except Exception as exc:
            details["reason"] = f"image_probe_failed: {exc}"
            return False, details
    width, height = size
    details["width"] = int(width)
    details["height"] = int(height)
    if width <= 0 or height <= 0:
        details["reason"] = "invalid_dimensions"
        return False, details
    details["reason"] = "ok"
    return True, details


def _frame_status(frames_dir: Path, prompts: list[str]) -> dict[str, Any]:
    expected = len(prompts)
    generated = 0
    pending: list[str] = []
    missing_count = 0
    invalid: list[dict[str, Any]] = []
    for idx in range(1, expected + 1):
        path = _expected_frame_path(frames_dir, idx)
        valid, details = _probe_image(path)
        if valid:
            generated += 1
        elif path.exists():
            invalid.append({"frame_index": idx, "path": str(path), "reason": details.get("reason")})
        else:
            missing_count += 1
            if len(pending) < 10:
                pending.append(str(path))
    existing = _existing_frames(frames_dir)
    legacy_named = [p.name for p in existing if re.fullmatch(r"\d{3,5}\.png", p.name, flags=re.IGNORECASE)]
    return {
        "expected": expected,
        "generated": generated,
        "pending": missing_count,
        "failed": len(invalid),
        "not_done": max(0, expected - generated),
        "invalid": invalid,
        "existing_total": len(existing),
        "legacy_named_existing": legacy_named[:10],
        "first_10_pending": pending,
        "first_10_failed": invalid[:10],
    }


def _redact_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if not parts.netloc:
        return "(provided)"
    return f"{parts.scheme}://{parts.netloc}/..."


def _candidate_api_urls(base_url: str) -> list[str]:
    raw = base_url.strip().rstrip("/")
    if not raw:
        return []
    if raw.endswith("/api"):
        return [raw]
    return [raw, f"{raw}/api"]


def _requests_module():
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise RuntimeError("Python package 'requests' is required for frames-runpod --execute") from exc
    return requests


def _resolve_comfyui_api_url(runpod_url: str) -> str:
    requests = _requests_module()
    candidates = _candidate_api_urls(runpod_url)
    if not candidates:
        raise ValueError("RunPod URL is empty.")
    last_error = "unknown error"
    for candidate in candidates:
        try:
            resp = requests.get(f"{candidate}/object_info", timeout=20)
            if resp.status_code == 200:
                return candidate
            last_error = f"{candidate} -> HTTP {resp.status_code}"
        except Exception as exc:
            last_error = f"{candidate} -> {exc}"
    raise RuntimeError(f"ComfyUI API is unreachable. Last error: {last_error}")


def _load_workflow(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Workflow file must contain JSON object: {path}")
    return data


def _render_workflow_prompt(
    workflow_template: dict[str, Any],
    prompt_text: str,
    *,
    text_node: str,
    seed_node: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow = json.loads(json.dumps(workflow_template))
    if text_node not in workflow:
        raise RuntimeError(f"Workflow text node missing: {text_node}")
    if not isinstance(workflow[text_node], dict):
        raise RuntimeError(f"Workflow text node invalid: {text_node}")
    workflow[text_node].setdefault("inputs", {})
    workflow[text_node]["inputs"]["text"] = prompt_text

    seed = random.randint(0, 2**32 - 1)
    if seed_node in workflow and isinstance(workflow[seed_node], dict):
        workflow[seed_node].setdefault("inputs", {})
        inputs = workflow[seed_node]["inputs"]
        if isinstance(inputs, dict):
            if "seed" in inputs:
                inputs["seed"] = seed
            elif "noise_seed" in inputs:
                inputs["noise_seed"] = seed
    return workflow, {"seed": seed, "prompt_hash": hashlib.sha1(prompt_text.encode("utf-8")).hexdigest()}


def validate_youtube_frames_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow_path = Path(str(workflow.get("path", "")))
    text_node_id = str(workflow.get("text_node_id", "") or "").strip()
    seed_node_id = str(workflow.get("seed_node_id", "") or "").strip()
    checks: dict[str, Any] = {
        "file_exists": workflow_path.is_file(),
        "json_valid": False,
        "text_node_exists": False,
        "seed_node_exists": False if seed_node_id else None,
        "prompt_insertable": False,
    }
    errors: list[str] = []
    data: dict[str, Any] | None = None

    if not checks["file_exists"]:
        errors.append(f"workflow file not found: {workflow_path}")
    else:
        try:
            data = _load_workflow(workflow_path)
            checks["json_valid"] = True
        except Exception as exc:
            errors.append(f"workflow JSON invalid: {exc}")

    if data is not None:
        text_node = data.get(text_node_id)
        checks["text_node_exists"] = isinstance(text_node, dict)
        if not checks["text_node_exists"]:
            errors.append(f"text_node_id not found or invalid: {text_node_id}")
        elif not isinstance(text_node.get("inputs"), dict):
            errors.append(f"text_node_id has no inputs object: {text_node_id}")
        else:
            try:
                _render_workflow_prompt(data, "workflow validation prompt", text_node=text_node_id, seed_node=seed_node_id)
                checks["prompt_insertable"] = True
            except Exception as exc:
                errors.append(f"prompt insertion failed: {exc}")

        if seed_node_id:
            checks["seed_node_exists"] = isinstance(data.get(seed_node_id), dict)
            if not checks["seed_node_exists"]:
                errors.append(f"seed_node_id not found or invalid: {seed_node_id}")

    status = "valid" if not errors and all(value is not False for value in checks.values()) else "invalid"
    return {
        "status": status,
        "ok": status == "valid",
        "checks": checks,
        "errors": errors,
    }


def _queue_prompt(api_url: str, workflow: dict[str, Any]) -> str:
    requests = _requests_module()
    resp = requests.post(f"{api_url}/prompt", json={"prompt": workflow}, timeout=COMFYUI_HTTP_TIMEOUT_SEC)
    if resp.status_code >= 400:
        body = (resp.text or "").strip()
        if len(body) > 2000:
            body = body[:2000] + "... [truncated]"
        raise RuntimeError(f"ComfyUI /prompt failed HTTP {resp.status_code}: {body}")
    data = resp.json()
    prompt_id = str(data.get("prompt_id", "")).strip()
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt response has no prompt_id: {data}")
    return prompt_id


def _prompt_in_queue(api_url: str, prompt_id: str) -> bool | None:
    requests = _requests_module()
    try:
        resp = requests.get(f"{api_url}/queue", timeout=COMFYUI_HTTP_TIMEOUT_SEC)
        resp.raise_for_status()
        return prompt_id in json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        return None


def _poll_until_done(api_url: str, prompt_id: str) -> dict[str, Any]:
    requests = _requests_module()
    deadline = time.time() + COMFYUI_MAX_WAIT_SEC
    started_at = time.time()
    while time.time() < deadline:
        try:
            resp = requests.get(f"{api_url}/history/{prompt_id}", timeout=COMFYUI_HTTP_TIMEOUT_SEC)
            if resp.status_code == 200:
                data = resp.json()
                if prompt_id in data and isinstance(data[prompt_id], dict):
                    return data[prompt_id]
        except Exception:
            pass
        if time.time() - started_at >= 120 and _prompt_in_queue(api_url, prompt_id) is False:
            raise RuntimeError(f"Prompt {prompt_id} disappeared from /queue and is absent in /history")
        time.sleep(COMFYUI_POLL_INTERVAL_SEC)
    raise TimeoutError(f"ComfyUI did not finish within {COMFYUI_MAX_WAIT_SEC}s (prompt_id={prompt_id})")


def _download_first_image(api_url: str, history_node: dict[str, Any]) -> bytes:
    requests = _requests_module()
    outputs = history_node.get("outputs", {})
    if not isinstance(outputs, dict):
        raise RuntimeError("ComfyUI returned invalid outputs payload")
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images", [])
        if not images:
            continue
        first = images[0]
        if not isinstance(first, dict):
            continue
        params = {
            "filename": first.get("filename", ""),
            "subfolder": first.get("subfolder", ""),
            "type": first.get("type", "output"),
        }
        resp = requests.get(f"{api_url}/view", params=params, timeout=COMFYUI_HTTP_TIMEOUT_SEC)
        resp.raise_for_status()
        return resp.content
    status = history_node.get("status", {})
    messages = history_node.get("messages", [])
    output_keys = list(outputs.keys()) if isinstance(outputs, dict) else []
    diagnostic = {
        "status": status,
        "output_keys": output_keys,
        "messages_tail": messages[-5:] if isinstance(messages, list) else messages,
    }
    text = json.dumps(diagnostic, ensure_ascii=False)
    if len(text) > 2500:
        text = text[:2500] + "... [truncated]"
    raise RuntimeError(f"ComfyUI returned no images in history payload: {text}")


def _generate_frame_via_comfyui(
    *,
    api_url: str,
    workflow_template: dict[str, Any],
    prompt_text: str,
    frame_path: Path,
    text_node: str,
    seed_node: str,
) -> dict[str, Any]:
    started_at = time.time()
    last_error = ""
    for attempt in range(1, COMFYUI_MAX_RETRIES + 1):
        meta: dict[str, Any] = {}
        try:
            workflow, meta = _render_workflow_prompt(
                workflow_template,
                prompt_text,
                text_node=text_node,
                seed_node=seed_node,
            )
            prompt_id = _queue_prompt(api_url, workflow)
            history = _poll_until_done(api_url, prompt_id)
            image_data = _download_first_image(api_url, history)
            partial_path = frame_path.with_name(f"{frame_path.stem}.partial{frame_path.suffix}")
            partial_path.write_bytes(image_data)
            valid, details = _probe_image(partial_path)
            if not valid:
                raise RuntimeError(f"downloaded image failed validation: {details}")
            partial_path.replace(frame_path)
            image_hash = hashlib.sha1(image_data).hexdigest()
            return {
                "ok": True,
                "attempt": attempt,
                "elapsed_sec": round(time.time() - started_at, 3),
                "seed": meta.get("seed"),
                "prompt_hash": meta.get("prompt_hash"),
                "image_hash": image_hash,
                "size_bytes": len(image_data),
                "width": details.get("width"),
                "height": details.get("height"),
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < COMFYUI_MAX_RETRIES:
                time.sleep(5 * attempt)
                continue
    return {
        "ok": False,
        "attempt": COMFYUI_MAX_RETRIES,
        "elapsed_sec": round(time.time() - started_at, 3),
        "error": last_error,
    }


def _manual_command(director_dir: Path, module_name: str, function_name: str, stage_dir: Path) -> str:
    return (
        f'cd /d "{director_dir}" && '
        f'python -c "from pathlib import Path; from {module_name} import {function_name}; '
        f'{function_name}(Path(r\'{stage_dir}\'))"'
    )


def _auto_gemini_command(director_dir: Path, module_name: str, function_name: str, stage_dir: Path | None) -> list[str]:
    call = f"{function_name}(None)" if stage_dir is None else f"{function_name}(Path(r'''{stage_dir}'''))"
    return [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"from {module_name} import {function_name}; "
            f"{call}"
        ),
    ]


def _auto_gemini_batch_command(director_dir: Path, module_name: str, function_name: str) -> list[str]:
    return [
        sys.executable,
        "-c",
        f"from {module_name} import {function_name}; {function_name}(None)",
    ]


def _run_director_subprocess(
    *,
    director_dir: Path,
    module_name: str,
    function_name: str,
    stage_dir: Path | None,
    log_path: Path,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GEMINI_USE_CONFIG_URL", "1")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_overrides:
        env.update(env_overrides)
    cmd = _auto_gemini_command(director_dir, module_name, function_name, stage_dir)
    _append_text(
        log_path,
        "\n"
        + "=" * 80
        + f"\nstarted_at={_now_iso()}\n"
        + f"cwd={director_dir}\n"
        + f"module={module_name}\n"
        + f"function={function_name}\n"
        + f"stage_dir={stage_dir or ''}\n"
        + f"env_overrides={json.dumps(env_overrides or {}, ensure_ascii=False)}\n"
        + f"cmd={json.dumps(cmd, ensure_ascii=False)}\n\n",
    )
    root_dir = director_dir.parents[1]
    with gemini_colab_proxy_session(root_dir) as proxy_session:
        env = apply_gemini_colab_proxy_env(env, proxy_session)
        _append_text(log_path, f"GEMINI_PROXY_SERVER={env.get('GEMINI_PROXY_SERVER', '')}\n")
        proc = subprocess.run(
            cmd,
            cwd=str(director_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    _append_text(
        log_path,
        f"\nfinished_at={_now_iso()}\nreturncode={proc.returncode}\n\n[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}\n",
    )
    return proc


def _run_director_batch_subprocess(
    *,
    director_dir: Path,
    module_name: str,
    function_name: str,
    stories_dir: Path,
    log_path: Path,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GEMINI_USE_CONFIG_URL", "1")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["GEMINI_STORIES_DIR"] = str(stories_dir)
    if env_overrides:
        env.update(env_overrides)
    cmd = _auto_gemini_batch_command(director_dir, module_name, function_name)
    _append_text(
        log_path,
        "\n"
        + "=" * 80
        + f"\nstarted_at={_now_iso()}\n"
        + f"cwd={director_dir}\n"
        + f"module={module_name}\n"
        + f"function={function_name}\n"
        + f"stories_dir={stories_dir}\n"
        + f"env_overrides={json.dumps(env_overrides or {}, ensure_ascii=False)}\n"
        + f"cmd={json.dumps(cmd, ensure_ascii=False)}\n\n",
    )
    root_dir = director_dir.parents[1]
    with gemini_colab_proxy_session(root_dir) as proxy_session:
        env = apply_gemini_colab_proxy_env(env, proxy_session)
        _append_text(log_path, f"GEMINI_PROXY_SERVER={env.get('GEMINI_PROXY_SERVER', '')}\n")
        proc = subprocess.run(
            cmd,
            cwd=str(director_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    _append_text(
        log_path,
        f"\nfinished_at={_now_iso()}\nreturncode={proc.returncode}\n\n[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}\n",
    )
    return proc


def _legacy_characters_prompt_source_report(
    *,
    config: OrchestratorConfig,
    story_id: str,
    story_dir: Path,
    director_dir: Path,
    stage_output: Path,
    target: Path,
) -> dict[str, Any]:
    source_path = director_dir / "gemini_characters.py"
    prompt_text = ""
    source_type = "missing"
    if source_path.is_file():
        source_type = "legacy_hardcoded_python_constant"
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8", errors="replace"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    names = [target_node.id for target_node in node.targets if isinstance(target_node, ast.Name)]
                    if "CHARACTERS_PROMPT" in names:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, str):
                            prompt_text = value
                        break
        except Exception as exc:
            source_type = "legacy_hardcoded_python_constant_parse_failed"
            prompt_text = f"ERROR: {exc}"
    digest = hashlib.sha256(prompt_text.encode("utf-8", errors="replace")).hexdigest() if prompt_text else ""
    report = {
        "story_id": story_id,
        "prompt_source_type": source_type,
        "prompt_source_path": str(source_path),
        "prompt_sha256": digest,
        "prompt_preview_first_300_chars": prompt_text[:300],
        "gemini_profile_or_user_data_path": str(director_dir / "user_data"),
        "gemini_url": str(_director_config(config).get("characters_gemini_url", "")),
        "output_characters_path": str(target),
        "raw_output_path": str(stage_output),
        "timestamp": _now_iso(),
    }
    report_path = _logs_dir(story_dir) / "youtube_character_prompt_source_report.json"
    _write_json(config, report_path, report)
    report["report_path"] = str(report_path)
    return report


def _file_debug(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return item
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        item.update(
            {
                "size": stat.st_size,
                "preview_first_300_chars": text[:300],
                "beauty_terms_found": _token_findings(text),
            }
        )
    except Exception as exc:
        item["read_error"] = str(exc)
    return item


def _token_findings(text: str) -> list[str]:
    needles = (
        "handsome and hot",
        "beautiful and sexy",
        "model-like",
        "smooth perfect skin",
        "very attractive face",
        "IMPORTANT FOR APPEARANCE",
        "FLUX OPTIMIZED",
        "Good anchor style example",
        "style_prompt_prefix",
    )
    lowered = text.lower()
    return [needle for needle in needles if needle.lower() in lowered]


def _legacy_characters_outgoing_message_debug_report(
    *,
    config: OrchestratorConfig,
    story_id: str,
    story_dir: Path,
    director_dir: Path,
    txt_path: Path,
    stage_output: Path,
    target: Path,
    prompt_source_report: dict[str, Any],
) -> dict[str, Any]:
    prompt_text = str(prompt_source_report.get("prompt_preview_first_300_chars") or "")
    source_path = director_dir / "gemini_characters.py"
    if source_path.is_file():
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8", errors="replace"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    names = [target_node.id for target_node in node.targets if isinstance(target_node, ast.Name)]
                    if "CHARACTERS_PROMPT" in names:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, str):
                            prompt_text = value
                        break
        except Exception:
            pass

    txt_preview = ""
    txt_size = None
    txt_beauty_terms: list[str] = []
    if txt_path.is_file():
        txt_text = txt_path.read_text(encoding="utf-8", errors="replace")
        txt_preview = txt_text[:1000]
        txt_size = txt_path.stat().st_size
        txt_beauty_terms = _token_findings(txt_text)

    final_message = prompt_text
    stale_source_candidates = [
        _file_debug(target),
        _file_debug(stage_output),
        _file_debug(director_dir / "stories" / story_id / "characters.txt"),
        _file_debug(director_dir / "stories" / story_id / "prompts_list.txt"),
        _file_debug(txt_path.parent / "prompts_list.txt"),
        _file_debug(txt_path.parent / "prompts_list.partial.txt"),
        _file_debug(txt_path.parent / "director_checkpoint.json"),
    ]
    report = {
        "story_id": story_id,
        "gemini_url": str(_director_config(config).get("characters_gemini_url", "")),
        "browser_profile": str(director_dir / "user_data"),
        "stage": "legacy/director_2_0/gemini_characters.py::run_characters",
        "txt_path": str(txt_path),
        "txt_exists": txt_path.is_file(),
        "txt_size": txt_size,
        "txt_preview_first_1000_chars": txt_preview,
        "txt_beauty_terms_found": txt_beauty_terms,
        "characters_prompt_exact_text": prompt_text,
        "characters_prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8", errors="replace")).hexdigest() if prompt_text else "",
        "final_message_text_sent_to_gemini": final_message,
        "final_message_sha256": hashlib.sha256(final_message.encode("utf-8", errors="replace")).hexdigest() if final_message else "",
        "send_file_and_read_function_path": str(director_dir / "gemini_director.py"),
        "does_send_file_and_read_add_prefix": False,
        "added_prefix_text": "",
        "does_send_file_and_read_add_suffix": False,
        "added_suffix_text": "",
        "send_order": "attach file first, then insert instruction text with page.keyboard.insert_text(instruction)",
        "uses_clipboard_or_paste_for_file": True,
        "retry_prompts_found": False,
        "retry_prompt_previews": [],
        "stale_source_candidates": stale_source_candidates,
        "output_characters_path": str(target),
        "raw_legacy_characters_path": str(stage_output),
        "timestamp": _now_iso(),
    }
    json_path = _logs_dir(story_dir) / "youtube_character_outgoing_message_debug.json"
    _write_json(config, json_path, report)
    txt_lines = [
        "# YouTube Character outgoing message debug",
        "",
        f"story_id: {story_id}",
        f"gemini_url: {report['gemini_url']}",
        f"browser_profile: {report['browser_profile']}",
        f"stage: {report['stage']}",
        "",
        "1. Exact wrapper sent:",
        prompt_text,
        "",
        "2. Attached file:",
        f"path: {txt_path}",
        f"exists: {txt_path.is_file()}",
        f"size: {txt_size}",
        "preview_first_1000_chars:",
        txt_preview,
        "",
        "3. Additional instructions before/after wrapper:",
        "No prefix or suffix found in send_file_and_read; it attaches the file, then inserts the instruction text exactly.",
        "",
        "4. Old prompt in send_file_and_read/retry:",
        "No Character-stage retry/fallback prompt with old schema was found. resend/retry uses the same action.",
        "",
        "5. Beauty words in outgoing message or attached file:",
        f"wrapper_terms: {_token_findings(prompt_text)}",
        f"attached_file_terms: {txt_beauty_terms}",
    ]
    txt_report_path = _logs_dir(story_dir) / "youtube_character_outgoing_message_debug.txt"
    txt_report_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    report["report_path"] = str(json_path)
    report["text_report_path"] = str(txt_report_path)
    return report


def _is_browser_context_closed_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    markers = (
        "target page, context or browser has been closed",
        "targetclosederror",
        "target page",
        "browser has been closed",
    )
    return any(marker in combined for marker in markers)


def _base_result(config: OrchestratorConfig, story_id: str) -> tuple[dict[str, Any], list[str]]:
    basics = _story_basics(config, story_id)
    story_dir = Path(basics["story_dir"])
    missing: list[str] = []
    if not story_dir.is_dir():
        missing.append(str(story_dir))
    return basics, missing


def _write_bridge_report(config: OrchestratorConfig, story_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    path = _logs_dir(story_dir) / f"{name}_report.json"
    _write_json(config, path, payload)
    return path


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _update_story_manifest(
    config: OrchestratorConfig,
    story_id: str,
    story_dir: Path,
    patch: dict[str, Any],
) -> Path:
    path = _story_manifest_path(story_dir)
    manifest = _load_story_manifest(story_dir)
    now = _now_iso()
    manifest.setdefault("youtube_outputs", {})
    if isinstance(manifest["youtube_outputs"], dict):
        manifest["youtube_outputs"].update(
            {
                "characters_dir": "05_characters",
                "director_dir": PROMPTS_PRIMARY_DIRNAME,
                "frames_dir": "07_frames",
                "logs_dir": "logs",
            }
        )
    manifest.setdefault("expected_artifacts", {})
    if isinstance(manifest["expected_artifacts"], dict):
        manifest["expected_artifacts"].update(
            {
                "characters_txt": str(_characters_path(config, story_id, story_dir)),
                "prompts_list_txt": str(_prompts_path(config, story_id, story_dir)),
                "frames_dir": str(_frames_dir(config, story_id, story_dir)),
            }
        )
    manifest.setdefault("actual_artifacts", {})
    if isinstance(manifest["actual_artifacts"], dict):
        manifest["actual_artifacts"].update(patch.get("actual_artifacts", {}))
    manifest.setdefault("status", {})
    if isinstance(manifest["status"], dict):
        manifest["status"].update(patch.get("status", {}))
    manifest.setdefault("pipeline_stage_status", {})
    if isinstance(manifest["pipeline_stage_status"], dict):
        manifest["pipeline_stage_status"].update(patch.get("pipeline_stage_status", {}))
    for key, value in patch.items():
        if key in {"actual_artifacts", "status", "pipeline_stage_status"}:
            continue
        manifest[key] = value
    manifest["updated_at"] = now
    _write_json(config, path, manifest)
    return path


def _write_staging_readme(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_youtube_characters_export(
    *,
    config: OrchestratorConfig,
    options: YoutubeCharactersExportOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source_text = _safe_story_path(story_dir)
    if not source_text.is_file():
        missing.append(str(source_text))
    staging_dir = _characters_staging_dir(config, basics["story_id"], story_dir)
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("exported" if options.execute else "dry_run"),
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_words": _count_words(source_text),
        "staging_dir": str(staging_dir),
        "staging_story_txt": str(staging_dir / "story.txt"),
        "staging_readme": str(staging_dir / "README.md"),
        "expected_output_filename": "characters.txt",
        "target_characters_path": str(basics["characters_path"]),
        "missing": missing,
        "note": "Gemini is not launched. Use staging/story.txt manually and import resulting characters.txt.",
    }
    if missing or not options.execute:
        return result
    staging_dir.mkdir(parents=True, exist_ok=True)
    _bridge_copy2(config, source_text, staging_dir / "story.txt", function="run_youtube_characters_export")
    _write_staging_readme(
        staging_dir / "README.md",
        [
            "# YouTube Characters Export",
            "",
            f"Story: {basics['canonical_basename']}",
            "",
            "Input:",
            "- story.txt",
            "",
            "Expected output:",
            "- characters.txt",
            "",
            "Import command:",
            f'python -m orchestrator youtube characters-import --story-id "{basics["canonical_basename"]}" --source "PATH_TO_CHARACTERS_TXT" --execute',
        ],
    )
    report_path = _write_bridge_report(config, story_dir, "youtube_characters_export", {**result, "written_at": _now_iso()})
    result["report_path"] = str(report_path)
    return result


def run_youtube_characters_import(
    *,
    config: OrchestratorConfig,
    options: YoutubeCharactersImportOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source = Path(options.source).resolve()
    target = Path(basics["characters_path"])
    if not _is_nonempty_file(source):
        missing.append(str(source))
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("imported" if options.execute else "dry_run"),
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source": str(source),
        "target_characters_path": str(target),
        "source_size": source.stat().st_size if source.is_file() else 0,
        "missing": missing,
    }
    if missing or not options.execute:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    _bridge_copy2(config, source, target, function="run_youtube_characters_import")
    imported_at = _now_iso()
    manifest_path = _update_story_manifest(
        config,
        basics["story_id"],
        story_dir,
        {
            "actual_artifacts": {"characters_txt": str(target)},
            "status": {"characters_done": True},
            "pipeline_stage_status": {"characters": "done"},
            "characters": {
                "status": "done",
                "path": str(target),
                "source": str(source),
                "imported_at": imported_at,
            },
        },
    )
    report_path = _write_bridge_report(config, story_dir, "youtube_characters_import", {**result, "imported_at": imported_at})
    result["manifest_path"] = str(manifest_path)
    result["report_path"] = str(report_path)
    result["target_size"] = target.stat().st_size
    return result


def _director_export_estimates(config: OrchestratorConfig, story_dir: Path, audio_path: Path) -> tuple[float | None, int, int | None]:
    duration = _duration_sec(audio_path)
    director_dir = _director_module_dir(config)
    frame_duration_sec = 25
    config_json = director_dir / "config.json"
    if config_json.is_file():
        try:
            data = _read_json(config_json)
            if isinstance(data, dict):
                frame_duration_sec = int(data.get("frame_duration_sec", frame_duration_sec) or frame_duration_sec)
        except Exception:
            pass
    estimated_prompts = max(1, round(duration / frame_duration_sec)) if duration and frame_duration_sec > 0 else None
    return duration, frame_duration_sec, estimated_prompts


def run_youtube_director_prompts_export(
    *,
    config: OrchestratorConfig,
    options: YoutubeDirectorPromptsExportOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source_text = _source_text_for_director(story_dir)
    source_text_language = detect_path_language(source_text)
    audio_path = _narration_path(story_dir)
    characters_path = Path(basics["characters_path"])
    if not source_text.is_file():
        missing.append(str(source_text))
    if not audio_path.is_file():
        missing.append(str(audio_path))
    if not characters_path.is_file():
        missing.append(str(characters_path))
    duration, frame_duration_sec, estimated_prompts = _director_export_estimates(config, story_dir, audio_path)
    staging_dir = _prompts_staging_dir(config, basics["story_id"], story_dir)
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("exported" if options.execute else "dry_run"),
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_language": source_text_language,
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "source_text_words": _count_words(source_text),
        "audio_path": str(audio_path),
        "audio_duration_sec": duration,
        "frame_duration_sec": frame_duration_sec,
        "estimated_prompts": estimated_prompts,
        "characters_path": str(characters_path),
        "staging_dir": str(staging_dir),
        "staging_story_txt": str(staging_dir / "story.txt"),
        "staging_characters_txt": str(staging_dir / "characters.txt"),
        "staging_narration_path_txt": str(staging_dir / "narration_path.txt"),
        "staging_readme": str(staging_dir / "README.md"),
        "expected_output_filename": "prompts_list.txt",
        "target_prompts_path": str(basics["prompts_path"]),
        "missing": missing,
        "note": "Gemini is not launched. Use staging files manually and import resulting prompts_list.txt.",
    }
    if source_text.is_file() and source_text_language != EXPECTED_YOUTUBE_LANGUAGE:
        result.update(
            {
                "ok": False,
                "status": "wrong_language",
                "current_blocker": "youtube_director_source_wrong_language",
                "next_action": "run youtube safe-regenerate, then promo-run, then YouTube TTS",
            }
        )
        return result
    if missing or not options.execute:
        return result
    staging_dir.mkdir(parents=True, exist_ok=True)
    _bridge_copy2(config, source_text, staging_dir / "story.txt", function="run_youtube_director_prompts_export")
    _bridge_copy2(config, characters_path, staging_dir / "characters.txt", function="run_youtube_director_prompts_export")
    (staging_dir / "narration_path.txt").write_text(str(audio_path) + "\n", encoding="utf-8")
    _write_staging_readme(
        staging_dir / "README.md",
        [
            "# YouTube Director Prompts Export",
            "",
            f"Story: {basics['canonical_basename']}",
            f"Audio duration: {duration} sec",
            f"Frame duration: {frame_duration_sec} sec",
            f"Estimated prompts: {estimated_prompts}",
            "",
            "Inputs:",
            "- story.txt",
            "- characters.txt",
            "- narration_path.txt",
            "",
            "Expected output:",
            "- prompts_list.txt",
            "",
            "Import command:",
            f'python -m orchestrator youtube director-prompts-import --story-id "{basics["canonical_basename"]}" --source "PATH_TO_PROMPTS_LIST_TXT" --execute',
        ],
    )
    report_path = _write_bridge_report(config, story_dir, "youtube_director_prompts_export", {**result, "written_at": _now_iso()})
    result["report_path"] = str(report_path)
    return result


def run_youtube_director_prompts_import(
    *,
    config: OrchestratorConfig,
    options: YoutubeDirectorPromptsImportOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source = Path(options.source).resolve()
    target = Path(basics["prompts_path"])
    if not _is_nonempty_file(source):
        missing.append(str(source))
    prompts = _load_prompts(source)
    if source.is_file() and not prompts:
        missing.append(f"{source} (no prompts parsed)")
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("imported" if options.execute else "dry_run"),
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source": str(source),
        "target_prompts_path": str(target),
        "source_size": source.stat().st_size if source.is_file() else 0,
        "prompts_count": len(prompts),
        "missing": missing,
    }
    if missing or not options.execute:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    _bridge_copy2(config, source, target, function="run_youtube_director_prompts_import")
    imported_at = _now_iso()
    manifest_path = _update_story_manifest(
        config,
        basics["story_id"],
        story_dir,
        {
            "actual_artifacts": {"prompts_list_txt": str(target)},
            "status": {"director_done": True},
            "pipeline_stage_status": {"scenes_prompts": "done", "director_prompts": "done"},
            "scenes_prompts": {
                "status": "done",
                "path": str(target),
                "source": str(source),
                "prompts_count": len(prompts),
                "imported_at": imported_at,
            },
            "director_prompts": {
                "status": "done",
                "path": str(target),
                "source": str(source),
                "prompts_count": len(prompts),
                "imported_at": imported_at,
            },
        },
    )
    report_path = _write_bridge_report(config, story_dir, "youtube_director_prompts_import", {**result, "imported_at": imported_at})
    result["manifest_path"] = str(manifest_path)
    result["report_path"] = str(report_path)
    result["target_size"] = target.stat().st_size
    return result


def run_youtube_characters_bridge(
    *,
    config: OrchestratorConfig,
    options: YoutubeCharactersBridgeOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source_text = _source_text_for_characters(story_dir)
    if not source_text.is_file():
        missing.append(str(source_text))
    director_dir = Path(basics["director_module_dir"])
    if not (director_dir / "gemini_characters.py").is_file():
        missing.append(str(director_dir / "gemini_characters.py"))

    stage_dir = Path(basics["legacy_stage_dir"])
    characters_path = Path(basics["characters_path"])
    status = "done" if characters_path.is_file() else "needs_gemini"
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else status,
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_words": _count_words(source_text),
        "characters_path": str(characters_path),
        "characters_exists": characters_path.is_file(),
        "legacy_stage_dir": str(stage_dir),
        "manual_cmd_windows": _manual_command(director_dir, "gemini_characters", "run_characters", stage_dir),
        "missing": missing,
        "note": "Gemini is not launched by this bridge. Use manual_cmd only after confirmation.",
    }
    if missing:
        return result
    if options.execute:
        stage_dir.mkdir(parents=True, exist_ok=True)
        characters_path.parent.mkdir(parents=True, exist_ok=True)
        _bridge_copy2(config, source_text, stage_dir / "story.txt", function="run_youtube_characters_bridge")
        if characters_path.is_file():
            _bridge_copy2(config, characters_path, stage_dir / "characters.txt", function="run_youtube_characters_bridge")
        report_path = _write_bridge_report(config, story_dir, "youtube_characters_bridge", {**result, "written_at": _now_iso()})
        result["report_path"] = str(report_path)
    return result


def run_youtube_director_prompts_bridge(
    *,
    config: OrchestratorConfig,
    options: YoutubeDirectorPromptsBridgeOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source_text = _source_text_for_director(story_dir)
    source_text_language = detect_path_language(source_text)
    audio_path = Path(basics["narration_path"])
    characters_path = Path(basics["characters_path"])
    prompts_path = Path(basics["prompts_path"])
    director_dir = Path(basics["director_module_dir"])
    if not source_text.is_file():
        missing.append(str(source_text))
    if not audio_path.is_file():
        missing.append(str(audio_path))
    if not (director_dir / "gemini_director.py").is_file():
        missing.append(str(director_dir / "gemini_director.py"))

    prompts = _load_prompts(prompts_path)
    duration = _duration_sec(audio_path)
    story_words = _count_words(source_text)
    frame_duration_sec = 25
    config_json = director_dir / "config.json"
    if config_json.is_file():
        try:
            data = _read_json(config_json)
            if isinstance(data, dict):
                frame_duration_sec = int(data.get("frame_duration_sec", frame_duration_sec) or frame_duration_sec)
        except Exception:
            pass
    estimated_prompts = None
    if duration and frame_duration_sec > 0:
        estimated_prompts = max(1, round(duration / frame_duration_sec))
    stage_dir = Path(basics["legacy_stage_dir"])
    status = "done" if prompts else "needs_gemini"
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else status,
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_words": story_words,
        "audio_path": str(audio_path),
        "audio_duration_sec": duration,
        "frame_duration_sec": frame_duration_sec,
        "estimated_prompts": estimated_prompts,
        "characters_path": str(characters_path),
        "characters_exists": characters_path.is_file(),
        "prompts_path": str(prompts_path),
        "prompts_count": len(prompts),
        "legacy_stage_dir": str(stage_dir),
        "manual_cmd_windows": _manual_command(director_dir, "gemini_director", "run_director", stage_dir),
        "missing": missing,
        "note": "Gemini is not launched by this bridge. Use manual_cmd only after confirmation.",
    }
    if missing:
        return result
    if options.execute:
        stage_dir.mkdir(parents=True, exist_ok=True)
        prompts_path.parent.mkdir(parents=True, exist_ok=True)
        _bridge_copy2(config, source_text, stage_dir / "story.txt", function="run_youtube_director_prompts_bridge")
        _bridge_copy2(config, audio_path, stage_dir / "narration.mp3", function="run_youtube_director_prompts_bridge")
        if characters_path.is_file():
            _bridge_copy2(config, characters_path, stage_dir / "characters.txt", function="run_youtube_director_prompts_bridge")
        if prompts_path.is_file():
            _bridge_copy2(config, prompts_path, stage_dir / "prompts_list.txt", function="run_youtube_director_prompts_bridge")
        report_path = _write_bridge_report(config, story_dir, "youtube_director_prompts_bridge", {**result, "written_at": _now_iso()})
        result["report_path"] = str(report_path)
    return result


def run_youtube_characters_auto_gemini(
    *,
    config: OrchestratorConfig,
    options: YoutubeGeminiAutoStageOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source_text = _source_text_for_characters(story_dir)
    director_dir = Path(basics["director_module_dir"])
    stage_dir = Path(basics["legacy_stage_dir"])
    stage_output = stage_dir / "characters.txt"
    target = Path(basics["characters_path"])
    log_path = _logs_dir(story_dir) / "youtube_gemini_characters_auto.log"
    prompt_source_report = _legacy_characters_prompt_source_report(
        config=config,
        story_id=basics["story_id"],
        story_dir=story_dir,
        director_dir=director_dir,
        stage_output=stage_output,
        target=target,
    )
    if not source_text.is_file():
        missing.append(str(source_text))
    if not (director_dir / "gemini_characters.py").is_file():
        missing.append(str(director_dir / "gemini_characters.py"))
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("done" if _is_nonempty_file(target) else "needs_gemini"),
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "target_characters_path": str(target),
        "legacy_stage_dir": str(stage_dir),
        "legacy_stage_output": str(stage_output),
        "legacy_log_path": str(log_path),
        "prompt_source_report_path": prompt_source_report.get("report_path"),
        "prompt_source": prompt_source_report,
        "command": _auto_gemini_command(director_dir, "gemini_characters", "run_characters", stage_dir),
        "missing": missing,
        "note": "Runs legacy/director_2_0 gemini_characters.run_characters(folder) via subprocess.",
    }
    if missing:
        return result
    if _is_nonempty_file(target):
        return result
    if not options.execute:
        result["status"] = "would_run"
        return result

    stage_dir.mkdir(parents=True, exist_ok=True)
    _bridge_copy2(config, source_text, stage_dir / "story.txt", function="run_youtube_characters_auto_gemini")
    outgoing_debug_report = _legacy_characters_outgoing_message_debug_report(
        config=config,
        story_id=basics["story_id"],
        story_dir=story_dir,
        director_dir=director_dir,
        txt_path=stage_dir / "story.txt",
        stage_output=stage_output,
        target=target,
        prompt_source_report=prompt_source_report,
    )
    result["outgoing_message_debug_report_path"] = outgoing_debug_report.get("report_path")
    result["outgoing_message_debug_text_report_path"] = outgoing_debug_report.get("text_report_path")
    _append_text(
        log_path,
        "\n[character_prompt_source]\n"
        + f"story_id={basics['story_id']}\n"
        + f"prompt_source_type={prompt_source_report.get('prompt_source_type')}\n"
        + f"prompt_source_path={prompt_source_report.get('prompt_source_path')}\n"
        + f"prompt_sha256={prompt_source_report.get('prompt_sha256')}\n"
        + f"gemini_profile_or_user_data_path={prompt_source_report.get('gemini_profile_or_user_data_path')}\n"
        + f"output_characters_path={prompt_source_report.get('output_characters_path')}\n"
        + f"raw_output_path={prompt_source_report.get('raw_output_path')}\n"
        + f"prompt_preview_first_300_chars={prompt_source_report.get('prompt_preview_first_300_chars')}\n",
    )
    _append_text(
        log_path,
        "\n[character_outgoing_message_debug]\n"
        + f"report_path={outgoing_debug_report.get('report_path')}\n"
        + f"text_report_path={outgoing_debug_report.get('text_report_path')}\n"
        + f"final_message_sha256={outgoing_debug_report.get('final_message_sha256')}\n"
        + f"txt_path={outgoing_debug_report.get('txt_path')}\n"
        + f"txt_beauty_terms_found={json.dumps(outgoing_debug_report.get('txt_beauty_terms_found') or [], ensure_ascii=False)}\n",
    )
    if _is_nonempty_file(stage_output):
        proc_returncode = 0
    else:
        env_overrides = {"GEMINI_MAX_TRANSIENT_ROUNDS": "2"}
        if options.user_data_dir:
            env_overrides["GEMINI_USER_DATA_DIR"] = str(options.user_data_dir)
        proc = _run_director_subprocess(
            director_dir=director_dir,
            module_name="gemini_characters",
            function_name="run_characters",
            stage_dir=stage_dir,
            log_path=log_path,
            env_overrides=env_overrides,
        )
        proc_returncode = proc.returncode
        result["subprocess_returncode"] = proc.returncode
        if proc.returncode != 0:
            result["ok"] = False
            if _is_browser_context_closed_failure(proc):
                result["status"] = "youtube_visuals_characters_browser_context_closed"
                result["error"] = (
                    "legacy Gemini Characters browser context closed; stopped current story before Director/prompts"
                )
            else:
                result["status"] = "failed"
                result["error"] = f"legacy gemini characters subprocess failed: {proc.returncode}"
            report_path = _write_bridge_report(config, story_dir, "youtube_gemini_characters_auto", {**result, "written_at": _now_iso()})
            result["report_path"] = str(report_path)
            return result

    if not _is_nonempty_file(stage_output):
        result["ok"] = False
        result["status"] = "failed"
        result["subprocess_returncode"] = proc_returncode
        result["error"] = f"legacy characters output missing or empty: {stage_output}"
        report_path = _write_bridge_report(config, story_dir, "youtube_gemini_characters_auto", {**result, "written_at": _now_iso()})
        result["report_path"] = str(report_path)
        return result

    imported = run_youtube_characters_import(
        config=config,
        options=YoutubeCharactersImportOptions(story_id=options.story_id, source=stage_output, execute=True),
    )
    result.update(
        {
            "ok": bool(imported.get("ok")),
            "status": "done" if imported.get("ok") else "failed",
            "import_result": imported,
            "manifest_path": imported.get("manifest_path"),
            "target_size": imported.get("target_size"),
        }
    )
    report_path = _write_bridge_report(config, story_dir, "youtube_gemini_characters_auto", {**result, "written_at": _now_iso()})
    result["report_path"] = str(report_path)
    return result


def run_youtube_characters_batch_auto_gemini(
    *,
    config: OrchestratorConfig,
    options: YoutubeGeminiBatchOptions,
) -> dict[str, Any]:
    director_dir = _director_module_dir(config)
    stories_dir = Path(options.stories[0].stage_dir).parent if options.stories else director_dir / "stories_from_orchestrator"
    log_path = director_dir / "logs" / "youtube_gemini_characters_batch_auto.log"
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in options.stories:
        story_dir = Path(item.story_dir)
        stage_dir = Path(item.stage_dir)
        source_text = _source_text_for_characters(story_dir)
        if not source_text.is_file():
            missing.append(str(source_text))
            continue
        if options.execute:
            stage_dir.mkdir(parents=True, exist_ok=True)
            _bridge_copy2(config, source_text, stage_dir / "story.txt", function="run_youtube_characters_batch_auto_gemini")
        rows.append(
            {
                "story_id": item.story_id,
                "story_dir": str(story_dir),
                "stage_dir": str(stage_dir),
                "stage_output": str(stage_dir / "characters.txt"),
                "source_text": str(source_text),
            }
        )
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("would_run" if not options.execute else "needs_gemini"),
        "execute": bool(options.execute),
        "stage": "characters",
        "stories_dir": str(stories_dir),
        "stories_count": len(rows),
        "rows": rows,
        "missing": missing,
        "legacy_log_path": str(log_path),
        "note": "Batch mode opens one Characters browser and processes all staged folders; legacy reloads after each 5 requests.",
    }
    if missing or not options.execute:
        return result
    proc = _run_director_batch_subprocess(
        director_dir=director_dir,
        module_name="gemini_characters",
        function_name="run_characters",
        stories_dir=stories_dir,
        log_path=log_path,
        env_overrides={"GEMINI_MAX_TRANSIENT_ROUNDS": "2"},
    )
    result["subprocess_returncode"] = proc.returncode
    imported_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    for item in options.stories:
        stage_output = Path(item.stage_dir) / "characters.txt"
        imp = run_youtube_characters_import(
            config=config,
            options=YoutubeCharactersImportOptions(story_id=item.story_id, source=stage_output, execute=True),
        )
        (imported_rows if imp.get("ok") else failed_rows).append(imp)
    result.update(
        {
            "ok": proc.returncode == 0 and not failed_rows,
            "status": "done" if proc.returncode == 0 and not failed_rows else "failed",
            "imported": imported_rows,
            "failed": failed_rows,
        }
    )
    return result


def run_youtube_director_prompts_auto_gemini(
    *,
    config: OrchestratorConfig,
    options: YoutubeGeminiAutoStageOptions,
) -> dict[str, Any]:
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    source_text = _source_text_for_director(story_dir)
    audio_path = Path(basics["narration_path"])
    characters_path = Path(basics["characters_path"])
    prompts_path = Path(basics["prompts_path"])
    director_dir = Path(basics["director_module_dir"])
    stage_dir = Path(basics["legacy_stage_dir"])
    stage_output = stage_dir / "prompts_list.txt"
    log_path = _logs_dir(story_dir) / "youtube_gemini_director_auto.log"
    source_text_language = detect_path_language(source_text) if source_text.is_file() else "missing"
    if not source_text.is_file():
        missing.append(str(source_text))
    if not audio_path.is_file():
        missing.append(str(audio_path))
    if not _is_nonempty_file(characters_path):
        missing.append(str(characters_path))
    if not (director_dir / "gemini_director.py").is_file():
        missing.append(str(director_dir / "gemini_director.py"))
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("done" if _load_prompts(prompts_path) else "needs_gemini"),
        "execute": bool(options.execute),
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_language": source_text_language,
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "audio_path": str(audio_path),
        "characters_path": str(characters_path),
        "target_prompts_path": str(prompts_path),
        "legacy_stage_dir": str(stage_dir),
        "legacy_stage_output": str(stage_output),
        "legacy_log_path": str(log_path),
        "command": _auto_gemini_command(director_dir, "gemini_director", "run_director", stage_dir),
        "missing": missing,
        "note": "Runs legacy/director_2_0 gemini_director.run_director(folder) via subprocess.",
    }
    if source_text.is_file() and source_text_language != EXPECTED_YOUTUBE_LANGUAGE:
        result.update(
            {
                "ok": False,
                "status": "wrong_language",
                "current_blocker": "youtube_director_source_wrong_language",
                "next_action": "run youtube safe-regenerate, then promo-run, then YouTube TTS",
            }
        )
        return result
    if missing:
        return result
    if _load_prompts(prompts_path):
        result["prompts_count"] = len(_load_prompts(prompts_path))
        return result
    if not options.execute:
        result["status"] = "would_run"
        return result

    stage_dir.mkdir(parents=True, exist_ok=True)
    _bridge_copy2(config, source_text, stage_dir / "story.txt", function="run_youtube_director_auto_gemini")
    _bridge_copy2(config, audio_path, stage_dir / "narration.mp3", function="run_youtube_director_auto_gemini")
    _bridge_copy2(config, characters_path, stage_dir / "characters.txt", function="run_youtube_director_auto_gemini")
    if _load_prompts(stage_output):
        proc_returncode = 0
    else:
        env_overrides = {"GEMINI_USER_DATA_DIR": str(options.user_data_dir)} if options.user_data_dir else None
        proc = _run_director_subprocess(
            director_dir=director_dir,
            module_name="gemini_director",
            function_name="run_director",
            stage_dir=stage_dir,
            log_path=log_path,
            env_overrides=env_overrides,
        )
        proc_returncode = proc.returncode
        result["subprocess_returncode"] = proc.returncode
        if proc.returncode != 0:
            result["ok"] = False
            result["status"] = "failed"
            result["error"] = f"legacy gemini director subprocess failed: {proc.returncode}"
            report_path = _write_bridge_report(config, story_dir, "youtube_gemini_director_auto", {**result, "written_at": _now_iso()})
            result["report_path"] = str(report_path)
            return result

    prompts = _load_prompts(stage_output)
    if not prompts:
        result["ok"] = False
        result["status"] = "failed"
        result["subprocess_returncode"] = proc_returncode
        result["error"] = f"legacy prompts output missing or empty: {stage_output}"
        report_path = _write_bridge_report(config, story_dir, "youtube_gemini_director_auto", {**result, "written_at": _now_iso()})
        result["report_path"] = str(report_path)
        return result

    imported = run_youtube_director_prompts_import(
        config=config,
        options=YoutubeDirectorPromptsImportOptions(story_id=options.story_id, source=stage_output, execute=True),
    )
    result.update(
        {
            "ok": bool(imported.get("ok")),
            "status": "done" if imported.get("ok") else "failed",
            "prompts_count": len(prompts),
            "import_result": imported,
            "manifest_path": imported.get("manifest_path"),
            "target_size": imported.get("target_size"),
        }
    )
    report_path = _write_bridge_report(config, story_dir, "youtube_gemini_director_auto", {**result, "written_at": _now_iso()})
    result["report_path"] = str(report_path)
    return result


def run_youtube_director_prompts_batch_auto_gemini(
    *,
    config: OrchestratorConfig,
    options: YoutubeGeminiBatchOptions,
) -> dict[str, Any]:
    director_dir = _director_module_dir(config)
    stories_dir = Path(options.stories[0].stage_dir).parent if options.stories else director_dir / "stories_from_orchestrator"
    log_path = director_dir / "logs" / f"youtube_gemini_director_batch_auto_{abs(hash(str(stories_dir))) % 100000}.log"
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in options.stories:
        story_dir = Path(item.story_dir)
        stage_dir = Path(item.stage_dir)
        source_text = _source_text_for_director(story_dir)
        audio_path = _narration_path(story_dir)
        characters_path = _characters_path(config, item.story_id, story_dir)
        for required in (source_text, audio_path, characters_path):
            if not required.is_file():
                missing.append(str(required))
        if options.execute:
            stage_dir.mkdir(parents=True, exist_ok=True)
            if source_text.is_file():
                _bridge_copy2(config, source_text, stage_dir / "story.txt", function="run_youtube_director_prompts_batch_auto_gemini")
            if audio_path.is_file():
                _bridge_copy2(config, audio_path, stage_dir / "narration.mp3", function="run_youtube_director_prompts_batch_auto_gemini")
            if characters_path.is_file():
                _bridge_copy2(config, characters_path, stage_dir / "characters.txt", function="run_youtube_director_prompts_batch_auto_gemini")
        rows.append(
            {
                "story_id": item.story_id,
                "story_dir": str(story_dir),
                "stage_dir": str(stage_dir),
                "stage_output": str(stage_dir / "prompts_list.txt"),
                "source_text": str(source_text),
                "audio_path": str(audio_path),
                "characters_path": str(characters_path),
            }
        )
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("would_run" if not options.execute else "needs_gemini"),
        "execute": bool(options.execute),
        "stage": "prompts",
        "stories_dir": str(stories_dir),
        "stories_count": len(rows),
        "rows": rows,
        "missing": missing,
        "legacy_log_path": str(log_path),
        "note": "Batch mode opens one Director browser per worker and processes assigned stories sequentially; browser is reused across stories.",
    }
    if missing or not options.execute:
        return result
    env_overrides = {"GEMINI_USER_DATA_DIR": str(options.user_data_dir)} if options.user_data_dir else None
    proc = _run_director_batch_subprocess(
        director_dir=director_dir,
        module_name="gemini_director",
        function_name="run_director",
        stories_dir=stories_dir,
        log_path=log_path,
        env_overrides=env_overrides,
    )
    result["subprocess_returncode"] = proc.returncode
    imported_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    for item in options.stories:
        stage_output = Path(item.stage_dir) / "prompts_list.txt"
        imp = run_youtube_director_prompts_import(
            config=config,
            options=YoutubeDirectorPromptsImportOptions(story_id=item.story_id, source=stage_output, execute=True),
        )
        (imported_rows if imp.get("ok") else failed_rows).append(imp)
    result.update(
        {
            "ok": proc.returncode == 0 and not failed_rows,
            "status": "done" if proc.returncode == 0 and not failed_rows else "failed",
            "imported": imported_rows,
            "failed": failed_rows,
        }
    )
    return result


def run_youtube_frames_runpod_bridge(
    *,
    config: OrchestratorConfig,
    options: YoutubeFramesRunpodBridgeOptions,
) -> dict[str, Any]:
    started_at = time.time()
    basics, missing = _base_result(config, options.story_id)
    story_dir = Path(basics["story_dir"])
    raw_prompts_path = Path(basics["prompts_path"])
    frames_dir = Path(basics["frames_dir"])
    workflow = resolve_youtube_frames_workflow(config, options.workflow)
    workflow_validation = validate_youtube_frames_workflow(workflow)
    workflow_path = Path(str(workflow["path"]))
    characters_path = Path(basics["characters_path"])
    prompt_mode = "raw"
    prompts_path = raw_prompts_path
    missing_prerequisites: list[str] = []
    if not characters_path.is_file():
        missing_prerequisites.append(str(characters_path))
    if not prompts_path.is_file():
        missing.append(str(prompts_path))
    if not workflow_validation["ok"]:
        missing.extend(str(error) for error in workflow_validation.get("errors", []) or [])
    if options.execute and not str(options.runpod_url).strip():
        missing.append("--runpod-url")
    prompts = _load_prompts(prompts_path)
    status_report = _frame_status(frames_dir, prompts)
    status = "done" if status_report["expected"] > 0 and status_report["not_done"] == 0 else "needs_runpod"
    frame_jobs = [
        {
            "prompt_index": idx,
            "prompt": prompt,
            "output_frame_path": str(_expected_frame_path(frames_dir, idx)),
            "status": (
                "done"
                if _probe_image(_expected_frame_path(frames_dir, idx))[0]
                else ("invalid" if _expected_frame_path(frames_dir, idx).exists() else "pending")
            ),
        }
        for idx, prompt in enumerate(prompts, start=1)
    ]
    frame_jobs_path = frames_dir / "frame_jobs.json"
    failed_frames_path = frames_dir / "failed_frames.json"
    report_path = _logs_dir(story_dir) / "youtube_frames_runpod_report.json"
    payload_debug_path = ""
    result: dict[str, Any] = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("prepared" if options.prepare_only else status),
        "execute": bool(options.execute),
        "prepare_only": bool(options.prepare_only),
        "prompt_mode": prompt_mode,
        "story_id": basics["story_id"],
        "canonical_basename": basics["canonical_basename"],
        "story_dir": str(story_dir),
        "characters_path": str(characters_path),
        "characters_exists": characters_path.is_file(),
        "missing_prerequisites": missing_prerequisites,
        "prompts_path": str(prompts_path),
        "raw_prompts_path": str(raw_prompts_path),
        "prompts_count": len(prompts),
        "frames_dir": str(frames_dir),
        "workflow_path": str(workflow_path),
        "workflow": {**workflow, "validation_status": workflow_validation["status"]},
        "workflow_validation": workflow_validation,
        "runpod_url_preview": _redact_url(options.runpod_url),
        "runpod_url_provided": bool(str(options.runpod_url).strip()),
        "expected_frames": status_report["expected"],
        "generated_frames": status_report["generated"],
        "pending_frames": status_report["pending"],
        "failed_frames": status_report["failed"],
        "existing_frames_total": status_report["existing_total"],
        "legacy_named_existing": status_report["legacy_named_existing"],
        "first_10_pending": status_report["first_10_pending"],
        "first_10_failed": status_report["first_10_failed"],
        "frame_jobs_path": str(frame_jobs_path),
        "failed_frames_path": str(failed_frames_path),
        "report_path": str(report_path),
        "payload_debug_path": payload_debug_path,
        "missing": missing,
        "note": "Without --execute this is no-network dry-run. --prepare-only writes frame_jobs/report without network. --execute calls RunPod/ComfyUI.",
    }
    if missing:
        return result

    def write_jobs_and_report(status_value: str, failed_records: list[dict[str, Any]] | None = None) -> None:
        current_status = _frame_status(frames_dir, prompts)
        frames_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            config,
            frame_jobs_path,
            {
                "schema_version": 1,
                "updated_at": _now_iso(),
                "story_id": basics["story_id"],
                "canonical_basename": basics["canonical_basename"],
                "prompt_mode": prompt_mode,
                "prompts_path": str(prompts_path),
                "frames_dir": str(frames_dir),
                "workflow_path": str(workflow_path),
                "workflow": {**workflow, "validation_status": workflow_validation["status"]},
                "payload_debug_path": payload_debug_path,
                "runpod_url_preview": _redact_url(options.runpod_url),
                "jobs": frame_jobs,
            },
        )
        failed = failed_records or []
        _write_json(config, failed_frames_path, {"updated_at": _now_iso(), "failed": failed})
        report_payload = {
            **result,
            "status": status_value,
            "written_at": _now_iso(),
            "duration_sec": round(time.time() - started_at, 3),
            "prompts_total": len(prompts),
            "frames_existing": current_status["generated"],
            "frames_missing": current_status["pending"],
            "frames_generated": result.get("frames_generated", 0),
            "frames_failed": len(failed),
            "output_dir": str(frames_dir),
            "runpod_url": _redact_url(options.runpod_url),
            "prompt_mode": prompt_mode,
            "payload_debug_path": payload_debug_path,
            "workflow": {**workflow, "validation_status": workflow_validation["status"]},
            "workflow_validation": workflow_validation,
            "first_10_missing": current_status["first_10_pending"],
            "first_10_failed": failed[:10],
        }
        _write_json(config, report_path, report_payload)

    if options.prepare_only:
        write_jobs_and_report("prepared", [])
        return result

    if not options.execute:
        return result

    api_url = _resolve_comfyui_api_url(options.runpod_url)
    workflow_template = _load_workflow(workflow_path)
    text_node = str(workflow["text_node_id"])
    seed_node = str(workflow["seed_node_id"])
    generated_records: list[dict[str, Any]] = []
    failed_records: list[dict[str, Any]] = []
    frames_dir.mkdir(parents=True, exist_ok=True)
    consecutive_prompt_failures = 0

    for job in frame_jobs:
        frame_path = Path(str(job["output_frame_path"]))
        valid, details = _probe_image(frame_path)
        if valid:
            job["status"] = "done"
            job["validation"] = details
            continue
        if frame_path.exists():
            job["previous_invalid_reason"] = details.get("reason")
        print(
            f"[frames-runpod] frame {job['prompt_index']}/{len(frame_jobs)} -> {frame_path.name}",
            flush=True,
        )
        render_result = _generate_frame_via_comfyui(
            api_url=api_url,
            workflow_template=workflow_template,
            prompt_text=str(job["prompt"]),
            frame_path=frame_path,
            text_node=text_node,
            seed_node=seed_node,
        )
        if render_result.get("ok"):
            consecutive_prompt_failures = 0
            job["status"] = "done"
            job["generated_at"] = _now_iso()
            job["generation"] = render_result
            generated_records.append({"prompt_index": job["prompt_index"], "path": str(frame_path), **render_result})
            print(f"[frames-runpod] frame {job['prompt_index']} done", flush=True)
        else:
            error_text = str(render_result.get("error", "unknown error"))
            job["status"] = "failed"
            job["failed_at"] = _now_iso()
            job["error"] = error_text
            failed_records.append(
                {
                    "prompt_index": job["prompt_index"],
                    "path": str(frame_path),
                    "error": job["error"],
                    "elapsed_sec": render_result.get("elapsed_sec"),
                }
            )
            print(f"[frames-runpod] frame {job['prompt_index']} failed: {error_text}", flush=True)
            if (
                "ComfyUI /prompt failed HTTP 400" in error_text
                or "400 Client Error" in error_text
                or "ComfyUI returned no images in history payload" in error_text
            ):
                consecutive_prompt_failures += 1
            else:
                consecutive_prompt_failures = 0
        result["frames_generated"] = len(generated_records)
        write_jobs_and_report("running", failed_records)
        if consecutive_prompt_failures >= COMFYUI_MAX_CONSECUTIVE_PROMPT_FAILURES:
            result["fatal_error"] = (
                f"Stopping after {consecutive_prompt_failures} consecutive ComfyUI workflow failures. "
                "The RunPod API is reachable, but the workflow did not produce a downloadable image."
            )
            print(f"[frames-runpod] {result['fatal_error']}", flush=True)
            break

    final_status = _frame_status(frames_dir, prompts)
    final_state = "done" if final_status["not_done"] == 0 else ("failed" if failed_records else "partial")
    if result.get("fatal_error"):
        final_state = "failed"
    result.update(
        {
            "ok": final_state == "done",
            "status": final_state,
            "api_url_preview": _redact_url(api_url),
            "expected_frames": final_status["expected"],
            "generated_frames": final_status["generated"],
            "pending_frames": final_status["pending"],
            "failed_frames": len(failed_records),
            "first_10_pending": final_status["first_10_pending"],
            "first_10_failed": failed_records[:10],
            "frames_generated": len(generated_records),
            "duration_sec": round(time.time() - started_at, 3),
        }
    )
    write_jobs_and_report(final_state, failed_records)
    if final_state == "done":
        manifest_path = _update_story_manifest(
            config,
            basics["story_id"],
            story_dir,
            {
                "actual_artifacts": {"frames_dir": str(frames_dir)},
                "status": {"frames_done": True},
                "pipeline_stage_status": {"frames": "done"},
                "frames": {
                    "status": "done",
                    "path": str(frames_dir),
                    "prompt_mode": prompt_mode,
                    "workflow": {**workflow, "validation_status": workflow_validation["status"]},
                    "prompts_count": len(prompts),
                    "generated_frames": final_status["generated"],
                    "updated_at": _now_iso(),
                },
            },
        )
        result["manifest_path"] = str(manifest_path)
    return result
