"""YouTube Kokoro Colab Drive export bridge (single-story, export-only)."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_from_site import _append_status, _now_iso, _read_json, _write_json, _youtube_run_root
from orchestrator.youtube_language import EXPECTED_YOUTUBE_LANGUAGE, detect_path_language


DEFAULT_YOUTUBE_DRIVE_ROOT = Path(r"G:\Мой диск\ContentFactory_YouTube")
DEFAULT_VOICE_LABEL = "U"
DEFAULT_KOKORO_VOICE = "af_bella"
DEFAULT_SPEED = 0.92
DEFAULT_SAMPLE_RATE = 24000


@dataclass
class YoutubeTtsKokoroColabExportOptions:
    youtube_run_id: str
    story_id: str
    execute: bool = False
    drive_root: Path = DEFAULT_YOUTUBE_DRIVE_ROOT


@dataclass
class YoutubeTtsKokoroColabVerifyOptions:
    youtube_run_id: str
    story_id: str
    drive_root: Path = DEFAULT_YOUTUBE_DRIVE_ROOT


@dataclass
class YoutubeTtsKokoroColabImportOptions:
    youtube_run_id: str
    story_id: str
    drive_root: Path = DEFAULT_YOUTUBE_DRIVE_ROOT
    force: bool = False


def _safe_file_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_")
    return stem or "youtube_story"


def _run_manifest_path(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id) / "youtube_bridge_manifest.json"


def _load_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _story_entry_matches(entry: dict[str, Any], story_key: str) -> bool:
    key = story_key.strip()
    if not key:
        return False
    sid = str(entry.get("story_id", "")).strip()
    canonical = str(entry.get("canonical_basename", "")).strip()
    return key in {sid, canonical} or bool(canonical and canonical.casefold() == key.casefold())


def _resolve_story_manifest(
    *,
    root_dir: Path,
    youtube_run_id: str,
    story_key: str,
) -> tuple[Path | None, dict[str, Any], str]:
    run_manifest = _load_dict(_run_manifest_path(root_dir, youtube_run_id))
    if run_manifest:
        stories = [x for x in run_manifest.get("stories", []) if isinstance(x, dict)]
        matches = [x for x in stories if _story_entry_matches(x, story_key)]
        if len(matches) == 1:
            sm_path = Path(str(matches[0].get("story_manifest", "")).strip())
            manifest = _load_dict(sm_path)
            if manifest:
                return sm_path, manifest, ""
            return None, {}, f"Story manifest не найден или пуст: {sm_path}"
        if len(matches) > 1:
            return None, {}, f"Найдено несколько stories по ключу {story_key!r}; используйте точный story_id."

    fallback = root_dir / "output" / "youtube" / story_key / "youtube_story_manifest.json"
    manifest = _load_dict(fallback)
    if manifest:
        if str(manifest.get("youtube_run_id", "")).strip() != youtube_run_id:
            return None, {}, f"Fallback manifest найден, но youtube_run_id не совпадает: {fallback}"
        return fallback, manifest, ""

    return None, {}, f"История не найдена по story_id/canonical: {story_key!r}"


def _story_dir_from_manifest(manifest: dict[str, Any], manifest_path: Path) -> Path:
    outputs = manifest.get("youtube_outputs")
    if isinstance(outputs, dict):
        raw = str(outputs.get("story_dir", "")).strip()
        if raw:
            return Path(raw)
    return manifest_path.parent


def _resolve_audio_text_path(story_dir: Path, manifest: dict[str, Any]) -> Path:
    tta = manifest.get("text_ready_for_audio")
    if isinstance(tta, dict):
        raw = str(tta.get("path", "")).strip()
        if raw:
            return Path(raw)
    expected = manifest.get("expected_artifacts")
    if isinstance(expected, dict):
        raw = str(expected.get("promo_text_ready_for_audio", "")).strip()
        if raw:
            return Path(raw)
    return story_dir / "03_promo" / "text_ready_for_audio.txt"


def _voice_config(manifest: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for key in ("tts_kokoro_colab", "tts", "voice"):
        block = manifest.get(key)
        if isinstance(block, dict):
            candidates.append(block)
    voice_label = DEFAULT_VOICE_LABEL
    kokoro_voice = DEFAULT_KOKORO_VOICE
    speed = DEFAULT_SPEED
    for block in candidates:
        voice_label = str(block.get("voice_label") or voice_label).strip() or DEFAULT_VOICE_LABEL
        kokoro_voice = str(block.get("kokoro_voice") or block.get("voice") or kokoro_voice).strip() or DEFAULT_KOKORO_VOICE
        try:
            speed = float(block.get("speed") or speed)
        except (TypeError, ValueError):
            speed = DEFAULT_SPEED
    return {
        "voice_label": voice_label,
        "kokoro_voice": kokoro_voice,
        "speed": speed,
        "sample_rate": DEFAULT_SAMPLE_RATE,
    }


def _build_plan(
    *,
    root_dir: Path,
    options: YoutubeTtsKokoroColabExportOptions,
) -> tuple[dict[str, Any] | None, str]:
    youtube_run_id = str(options.youtube_run_id).strip()
    story_key = str(options.story_id).strip()
    if not youtube_run_id or not story_key:
        return None, "Нужны --youtube-run-id и --story-id"

    manifest_path, manifest, err = _resolve_story_manifest(
        root_dir=root_dir,
        youtube_run_id=youtube_run_id,
        story_key=story_key,
    )
    if err:
        return None, err
    assert manifest_path is not None

    story_dir = _story_dir_from_manifest(manifest, manifest_path)
    source_text = _resolve_audio_text_path(story_dir, manifest)
    source_text_language = detect_path_language(source_text)
    expected_local_audio = story_dir / "04_audio" / "narration.mp3"
    canonical = str(manifest.get("canonical_basename", "")).strip() or story_dir.name
    story_id = str(manifest.get("story_id", "")).strip() or story_key
    stem = _safe_file_stem(canonical)
    source_text_hash = _sha256_file(source_text)

    drive_root = options.drive_root.resolve()
    drive_text = drive_root / "texts" / f"{stem}.txt"
    expected_drive_audio = drive_root / "audio" / f"{stem}.mp3"
    jobs_dir = drive_root / "jobs"
    manifests_dir = drive_root / "manifests"
    logs_dir = drive_root / "logs"
    done_dir = drive_root / "done"
    failed_dir = drive_root / "failed"
    expected_files = jobs_dir / "EXPECTED_FILES.txt"
    expected_count = jobs_dir / "EXPECTED_COUNT.txt"
    job_json = jobs_dir / "youtube_tts_job.json"
    job_manifest = manifests_dir / "youtube_tts_job_manifest.json"
    local_report = story_dir / "logs" / "youtube_tts_kokoro_export_report.json"

    voice = _voice_config(manifest)
    created_at = _now_iso()
    item = {
        "youtube_run_id": youtube_run_id,
        "story_id": story_id,
        "canonical_basename": canonical,
        "source_text_path": str(source_text),
        "source_text_hash": source_text_hash,
        "source_text_language": source_text_language,
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "drive_text_path": str(drive_text),
        "expected_drive_audio_path": str(expected_drive_audio),
        "expected_local_audio_path": str(expected_local_audio),
        "voice_label": voice["voice_label"],
        "kokoro_voice": voice["kokoro_voice"],
        "speed": voice["speed"],
        "sample_rate": voice["sample_rate"],
        "created_at": created_at,
    }
    missing = []
    if not manifest_path.is_file():
        missing.append(str(manifest_path))
    if not source_text.is_file():
        missing.append(str(source_text))
    wrong_language = source_text.is_file() and source_text_language != EXPECTED_YOUTUBE_LANGUAGE

    return {
        "ok": not missing and not wrong_language,
        "status": "wrong_language" if wrong_language else "",
        "execute": bool(options.execute),
        "youtube_run_id": youtube_run_id,
        "story_id": story_id,
        "canonical_basename": canonical,
        "story_manifest": str(manifest_path),
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_exists": source_text.is_file(),
        "source_text_hash": source_text_hash,
        "expected_local_audio_path": str(expected_local_audio),
        "drive_root": str(drive_root),
        "drive_text_path": str(drive_text),
        "expected_drive_audio_path": str(expected_drive_audio),
        "jobs_dir": str(jobs_dir),
        "texts_dir": str(drive_root / "texts"),
        "audio_dir": str(drive_root / "audio"),
        "manifests_dir": str(manifests_dir),
        "logs_dir": str(logs_dir),
        "done_dir": str(done_dir),
        "failed_dir": str(failed_dir),
        "expected_files_path": str(expected_files),
        "expected_count_path": str(expected_count),
        "youtube_tts_job_path": str(job_json),
        "youtube_tts_job_manifest_path": str(job_manifest),
        "local_export_report": str(local_report),
        "voice_label": voice["voice_label"],
        "kokoro_voice": voice["kokoro_voice"],
        "speed": voice["speed"],
        "sample_rate": voice["sample_rate"],
        "expected_audio_filename": expected_drive_audio.name,
        "drive_text_filename": drive_text.name,
        "missing": missing,
        "wrong_language": wrong_language,
        "current_blocker": "youtube_tts_source_wrong_language" if wrong_language else "",
        "next_action": "run youtube safe-regenerate, then promo-run, then export YouTube TTS again" if wrong_language else "",
        "job_item": item,
        "created_at": created_at,
    }, ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _modified_iso(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _relative_to_story(path: Path, story_dir: Path) -> str:
    try:
        return str(path.relative_to(story_dir)).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_drive_audio_path(
    *,
    manifest: dict[str, Any],
    drive_root: Path,
    canonical_basename: str,
) -> Path:
    tts = manifest.get("tts_kokoro_colab")
    if isinstance(tts, dict):
        raw = str(tts.get("expected_drive_audio") or tts.get("expected_drive_audio_path") or "").strip()
        if raw:
            return Path(raw)
    return drive_root.resolve() / "audio" / f"{_safe_file_stem(canonical_basename)}.mp3"


def _resolve_local_audio_path(*, story_dir: Path, manifest: dict[str, Any]) -> Path:
    tts = manifest.get("tts_kokoro_colab")
    if isinstance(tts, dict):
        raw = str(tts.get("expected_local_audio") or tts.get("expected_local_audio_path") or "").strip()
        if raw:
            return Path(raw)
    audio = manifest.get("audio")
    if isinstance(audio, dict):
        raw = str(audio.get("path", "")).strip()
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else story_dir / p
    return story_dir / "04_audio" / "narration.mp3"


def _patch_story_manifest(plan: dict[str, Any]) -> None:
    path = Path(str(plan["story_manifest"]))
    data = _load_dict(path)
    tts = dict(data.get("tts_kokoro_colab") or {})
    tts.update(
        {
            "status": "exported",
            "drive_root": "ContentFactory_YouTube",
            "drive_root_path": str(plan["drive_root"]),
            "source_text_path": str(plan["source_text_path"]),
            "drive_text_path": str(plan["drive_text_path"]),
            "expected_local_audio": str(plan["expected_local_audio_path"]),
            "expected_drive_audio": str(plan["expected_drive_audio_path"]),
            "exported_text_ready_for_audio_hash": str(plan.get("source_text_hash") or ""),
            "current_text_ready_for_audio_hash": str(plan.get("source_text_hash") or ""),
            "voice_label": str(plan["voice_label"]),
            "kokoro_voice": str(plan["kokoro_voice"]),
            "speed": float(plan["speed"]),
            "sample_rate": int(plan["sample_rate"]),
            "youtube_tts_job": str(plan["youtube_tts_job_path"]),
            "youtube_tts_job_manifest": str(plan["youtube_tts_job_manifest_path"]),
            "exported_at": str(plan["created_at"]),
        }
    )
    data["tts_kokoro_colab"] = tts
    audio = dict(data.get("audio") or {})
    if audio.get("path") or Path(str(plan["expected_local_audio_path"])).is_file():
        audio.update(
            {
                "status": "stale",
                "path": audio.get("path") or _relative_to_story(Path(str(plan["expected_local_audio_path"])), Path(str(plan["story_dir"]))),
                "stale_reason": "youtube_tts_exported_new_text_waiting_for_import",
            }
        )
        data["audio"] = audio
    status = dict(data.get("pipeline_stage_status") or {})
    status["tts_kokoro_colab"] = "exported"
    status["audio"] = "stale"
    data["pipeline_stage_status"] = status
    actual = dict(data.get("actual_artifacts") or {})
    actual["tts_export_source_text"] = str(plan["source_text_path"])
    data["actual_artifacts"] = actual
    _write_json(path, data)


def run_youtube_tts_kokoro_colab_export(
    *,
    config: OrchestratorConfig,
    options: YoutubeTtsKokoroColabExportOptions,
) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    plan, err = _build_plan(root_dir=root_dir, options=options)
    if err:
        return {"ok": False, "message": err}
    assert plan is not None
    if plan.get("missing"):
        return {"ok": False, "message": "Missing required paths", **plan}
    if plan.get("wrong_language"):
        return {"ok": False, "message": "YouTube TTS export blocked: text_ready_for_audio must be English", **plan}

    if not options.execute:
        return {"ok": True, "status": "dry_run", **plan}

    source_text = Path(str(plan["source_text_path"]))
    drive_text = Path(str(plan["drive_text_path"]))
    for key in ("jobs_dir", "texts_dir", "audio_dir", "manifests_dir", "logs_dir", "done_dir", "failed_dir"):
        Path(str(plan[key])).mkdir(parents=True, exist_ok=True)
    Path(str(plan["expected_local_audio_path"])).parent.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(source_text, drive_text)

    expected_audio_name = str(plan["expected_audio_filename"])
    _write_text(Path(str(plan["expected_files_path"])), expected_audio_name + "\n")
    _write_text(Path(str(plan["expected_count_path"])), "1\n")

    job_payload = {
        "version": 1,
        "kind": "youtube_tts_kokoro_colab",
        "drive_root": str(plan["drive_root"]),
        "created_at": str(plan["created_at"]),
        "items": [plan["job_item"]],
    }
    _write_json(Path(str(plan["youtube_tts_job_path"])), job_payload)
    _write_json(Path(str(plan["youtube_tts_job_manifest_path"])), job_payload)

    report = {
        "status": "exported",
        "execute": True,
        "copied_text": True,
        **plan,
    }
    _write_json(Path(str(plan["local_export_report"])), report)
    _patch_story_manifest(plan)
    _append_status(
        _youtube_run_root(root_dir, str(plan["youtube_run_id"])) / "youtube_status.jsonl",
        {
            "timestamp": _now_iso(),
            "youtube_run_id": str(plan["youtube_run_id"]),
            "story_id": str(plan["story_id"]),
            "stage": "youtube_tts_kokoro_colab_export",
            "state": "exported",
            "drive_text_path": str(plan["drive_text_path"]),
            "expected_drive_audio_path": str(plan["expected_drive_audio_path"]),
        },
    )
    return {"ok": True, "status": "exported", **report}


def _resolve_verify_import_plan(
    *,
    root_dir: Path,
    youtube_run_id: str,
    story_id: str,
    drive_root: Path,
) -> tuple[dict[str, Any] | None, str]:
    manifest_path, manifest, err = _resolve_story_manifest(
        root_dir=root_dir,
        youtube_run_id=youtube_run_id,
        story_key=story_id,
    )
    if err:
        return None, err
    assert manifest_path is not None
    story_dir = _story_dir_from_manifest(manifest, manifest_path)
    canonical = str(manifest.get("canonical_basename", "")).strip() or story_dir.name
    source_text = _resolve_audio_text_path(story_dir, manifest)
    source_text_language = detect_path_language(source_text)
    expected_drive_audio = _resolve_drive_audio_path(
        manifest=manifest,
        drive_root=drive_root,
        canonical_basename=canonical,
    )
    expected_local_audio = _resolve_local_audio_path(story_dir=story_dir, manifest=manifest)
    return {
        "youtube_run_id": youtube_run_id,
        "story_id": str(manifest.get("story_id", "")).strip() or story_id,
        "canonical_basename": canonical,
        "story_manifest": str(manifest_path),
        "story_dir": str(story_dir),
        "source_text_path": str(source_text),
        "source_text_hash": _sha256_file(source_text),
        "source_text_language": source_text_language,
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "wrong_language": source_text.is_file() and source_text_language != EXPECTED_YOUTUBE_LANGUAGE,
        "current_blocker": "youtube_tts_source_wrong_language" if source_text.is_file() and source_text_language != EXPECTED_YOUTUBE_LANGUAGE else "",
        "expected_drive_audio_path": str(expected_drive_audio),
        "expected_local_audio_path": str(expected_local_audio),
    }, ""


def run_youtube_tts_kokoro_colab_verify(
    *,
    config: OrchestratorConfig,
    options: YoutubeTtsKokoroColabVerifyOptions,
) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    plan, err = _resolve_verify_import_plan(
        root_dir=root_dir,
        youtube_run_id=str(options.youtube_run_id).strip(),
        story_id=str(options.story_id).strip(),
        drive_root=options.drive_root,
    )
    if err:
        return {"ok": False, "message": err}
    assert plan is not None
    if plan.get("wrong_language"):
        return {"ok": False, "message": "YouTube TTS verify blocked: text_ready_for_audio must be English", "status": "wrong_language", **plan}
    audio_path = Path(str(plan["expected_drive_audio_path"]))
    exists = audio_path.is_file()
    size = audio_path.stat().st_size if exists else 0
    return {
        "ok": True,
        "status": "ready" if exists and size > 0 else "missing",
        "exists": exists,
        "size": size,
        "modified_time": _modified_iso(audio_path) if exists else "",
        **plan,
    }


def run_youtube_tts_kokoro_colab_import(
    *,
    config: OrchestratorConfig,
    options: YoutubeTtsKokoroColabImportOptions,
) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    plan, err = _resolve_verify_import_plan(
        root_dir=root_dir,
        youtube_run_id=str(options.youtube_run_id).strip(),
        story_id=str(options.story_id).strip(),
        drive_root=options.drive_root,
    )
    if err:
        return {"ok": False, "message": err}
    assert plan is not None
    if plan.get("wrong_language"):
        return {"ok": False, "message": "YouTube TTS import blocked: text_ready_for_audio must be English", "status": "wrong_language", **plan}

    source = Path(str(plan["expected_drive_audio_path"]))
    if not source.is_file() or source.stat().st_size <= 0:
        return {"ok": False, "message": "expected drive audio missing", "status": "missing", **plan}

    target = Path(str(plan["expected_local_audio_path"]))
    if target.exists() and not options.force:
        return {"ok": False, "message": f"local audio already exists: {target}", "status": "target_exists", **plan}

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if not target.is_file() or target.stat().st_size <= 0:
        return {"ok": False, "message": f"import copied empty/missing target: {target}", "status": "copy_failed", **plan}

    manifest_path = Path(str(plan["story_manifest"]))
    manifest = _load_dict(manifest_path)
    story_dir = Path(str(plan["story_dir"]))
    audio_rel = _relative_to_story(target, story_dir)

    tts = dict(manifest.get("tts_kokoro_colab") or {})
    tts.update(
        {
            "status": "imported",
            "expected_drive_audio": str(source),
            "expected_local_audio": str(target),
            "text_ready_for_audio_hash": str(plan.get("source_text_hash") or ""),
            "imported_at": _now_iso(),
        }
    )
    manifest["tts_kokoro_colab"] = tts
    manifest["audio"] = {
        "status": "done",
        "path": audio_rel,
        "source": str(source),
        "imported_at": tts["imported_at"],
    }
    status = dict(manifest.get("pipeline_stage_status") or {})
    status["tts_kokoro_colab"] = "imported"
    status["audio"] = "done"
    manifest["pipeline_stage_status"] = status
    actual = dict(manifest.get("actual_artifacts") or {})
    actual["narration_audio"] = str(target)
    manifest["actual_artifacts"] = actual
    _write_json(manifest_path, manifest)

    _append_status(
        _youtube_run_root(root_dir, str(plan["youtube_run_id"])) / "youtube_status.jsonl",
        {
            "timestamp": _now_iso(),
            "youtube_run_id": str(plan["youtube_run_id"]),
            "story_id": str(plan["story_id"]),
            "stage": "youtube_tts_kokoro_colab_import",
            "state": "imported",
            "expected_drive_audio_path": str(source),
            "local_audio_path": str(target),
        },
    )
    return {
        "ok": True,
        "status": "imported",
        "source_size": source.stat().st_size,
        "target_size": target.stat().st_size,
        "audio_manifest_path": audio_rel,
        **plan,
    }
