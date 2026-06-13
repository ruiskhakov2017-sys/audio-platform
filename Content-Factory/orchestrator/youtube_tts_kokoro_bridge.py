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
from orchestrator.launch_contract import (
    build_launch_context,
    drive_paths_for_story,
    require_production_launch_id,
    resolve_youtube_story_dir,
    story_slug,
)
from orchestrator.youtube_audio_stage import audio_production_gate
from orchestrator.youtube_tts_input_forensic import (
    evaluate_tts_import_readiness,
    gate_english_tts_input_for_story,
    deactivate_audio_rejected,
    patch_story_manifest_audio_contract,
    write_audio_import_report,
    _pack_kokoro_chunks,
)
from orchestrator.youtube_video_segments import get_media_duration


from orchestrator.voice_contract import (
    REASON_VOICE_CONTRACT_MISSING,
    REASON_YOUTUBE_TTS_VOICE_MISMATCH,
    genders_compatible,
    kokoro_voice_gender,
    resolve_youtube_tts_voice,
    sync_voice_contract_in_manifest,
)

DEFAULT_YOUTUBE_DRIVE_ROOT = Path(r"G:\Мой диск\ContentFactory_YouTube")
DEFAULT_SPEED = 0.92
DEFAULT_SAMPLE_RATE = 24000


@dataclass
class YoutubeTtsKokoroColabExportOptions:
    youtube_run_id: str
    story_id: str
    execute: bool = False
    drive_root: Path | None = None
    launch_id: str = ""
    launch_root: Path | None = None
    production: bool = False


@dataclass
class YoutubeTtsKokoroColabVerifyOptions:
    youtube_run_id: str
    story_id: str
    drive_root: Path | None = None
    launch_id: str = ""
    launch_root: Path | None = None
    production: bool = False


@dataclass
class YoutubeTtsKokoroColabImportOptions:
    youtube_run_id: str
    story_id: str
    drive_root: Path | None = None
    launch_id: str = ""
    launch_root: Path | None = None
    production: bool = False
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

    launch_root = root_dir / "Запуски" / youtube_run_id
    yt_root = launch_root / "03_youtube"
    launch_fallbacks = [yt_root / story_slug(story_key) / "youtube_story_manifest.json"]
    if yt_root.is_dir():
        key = story_key.strip()
        for child in yt_root.iterdir():
            if not child.is_dir():
                continue
            manifest_path = child / "youtube_story_manifest.json"
            manifest = _load_dict(manifest_path)
            if not manifest:
                continue
            sid = str(manifest.get("story_id", "")).strip()
            canonical = str(manifest.get("canonical_basename", "")).strip()
            if key in {sid, canonical} or sid.casefold() == key.casefold() or canonical.casefold() == key.casefold():
                launch_fallbacks.insert(0, manifest_path)
                break
    for fallback in launch_fallbacks:
        manifest = _load_dict(fallback)
        if manifest:
            return fallback, manifest, ""

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


def _voice_config(*, config: OrchestratorConfig, manifest: dict[str, Any], manifest_path: Path | None) -> dict[str, Any]:
    return resolve_youtube_tts_voice(
        config=config,
        manifest=manifest,
        manifest_path=manifest_path,
        write_manifest=True,
    )


def _build_plan(
    *,
    config: OrchestratorConfig,
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

    ctx = None
    if options.launch_id or options.launch_root:
        ctx = build_launch_context(
            config,
            launch_id=str(options.launch_id or story_key),
            launch_root=options.launch_root,
        )
        dp = drive_paths_for_story(ctx, canonical)
        drive_root = dp["drive_root"]
        drive_text = dp["drive_text"]
        expected_drive_audio = dp["drive_audio"]
        jobs_dir = dp["jobs_dir"]
        manifests_dir = dp["manifests_dir"]
        logs_dir = dp["logs_dir"]
        done_dir = dp["done_dir"]
        failed_dir = dp["failed_dir"]
    else:
        drive_root = (options.drive_root or DEFAULT_YOUTUBE_DRIVE_ROOT).resolve()
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

    voice = _voice_config(config=config, manifest=manifest, manifest_path=manifest_path)
    if not voice.get("ok"):
        return None, str(voice.get("reason_code") or REASON_VOICE_CONTRACT_MISSING)
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
        "expected_gender": voice.get("expected_gender", voice["voice_label"]),
        "source_info_path": voice.get("source_info_path", ""),
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
        "expected_gender": voice.get("expected_gender", voice["voice_label"]),
        "source_info_path": voice.get("source_info_path", ""),
        "voice_contract": voice.get("voice_contract"),
    }, ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _job_item_key(item: dict[str, Any]) -> str:
    for key in ("expected_drive_audio_path", "drive_text_path", "canonical_basename", "story_id"):
        raw = str(item.get(key) or "").strip()
        if raw:
            return f"{key}:{raw.casefold()}"
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _merge_job_items(job_path: Path, new_item: dict[str, Any]) -> list[dict[str, Any]]:
    existing = _load_dict(job_path)
    raw_items = existing.get("items") if isinstance(existing.get("items"), list) else []
    items = [dict(item) for item in raw_items if isinstance(item, dict)]
    new_key = _job_item_key(new_item)
    replaced = False
    for idx, item in enumerate(items):
        if _job_item_key(item) == new_key:
            items[idx] = new_item
            replaced = True
            break
    if not replaced:
        items.append(new_item)
    return items


def _write_expected_audio_files(path: Path, items: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item.get("expected_drive_audio_path") or "").strip()
        name = Path(raw).name if raw else ""
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        names.append(name)
    _write_text(path, "".join(f"{name}\n" for name in names))
    return names


def _bridge_copyfile(
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
            module="orchestrator.youtube_tts_kokoro_bridge",
            function=function,
        )
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    return target.resolve()


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


def _missing_source_reason(plan: dict[str, Any]) -> tuple[str, str]:
    missing = [str(p) for p in (plan.get("missing") or [])]
    promo_missing = any(
        "03_promo" in p.replace("/", "\\").lower()
        and p.replace("/", "\\").lower().endswith("\\text_ready_for_audio.txt")
        for p in missing
    )
    if promo_missing:
        return "blocked_missing_promo_text", "tts_missing_promo_text"
    return "blocked_missing_required_paths", "tts_missing_required_paths"


def _drive_mount_blocker(drive_root: Path) -> str:
    try:
        anchor = drive_root.anchor
        if drive_root.drive and anchor and not Path(anchor).exists():
            return anchor
    except OSError:
        return drive_root.anchor or str(drive_root)
    return ""


def _patch_story_manifest(plan: dict[str, Any]) -> None:
    path = Path(str(plan["story_manifest"]))
    data = _load_dict(path)
    if plan.get("voice_contract"):
        data["voice_contract"] = plan["voice_contract"]
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
    require_production_launch_id(
        options.launch_id or None,
        production=bool(options.production),
        command="youtube tts-kokoro-colab export",
    )
    root_dir = config.root_dir.resolve()
    plan, err = _build_plan(config=config, root_dir=root_dir, options=options)
    if err:
        return {"ok": False, "message": err}
    assert plan is not None
    if plan.get("missing"):
        status, reason_code = _missing_source_reason(plan)
        return {
            "ok": False,
            "status": status,
            "reason_code": reason_code,
            "message": "Missing required paths",
            "retryable": True,
            "terminal_story": False,
            "queue_persist": False,
            **plan,
        }
    if plan.get("wrong_language"):
        return {
            "ok": False,
            "status": "blocked_wrong_language",
            "reason_code": "tts_source_wrong_language",
            "message": "YouTube TTS export blocked: text_ready_for_audio must be English",
            "retryable": True,
            "terminal_story": False,
            "queue_persist": False,
            **plan,
        }

    gate = gate_english_tts_input_for_story(
        config=config,
        story_id=str(plan.get("canonical_basename") or options.story_id),
        drive_root=options.drive_root,
        launch_id=str(options.launch_id or ""),
        launch_root=options.launch_root,
        check_audio_timeline=False,
        check_chunk_fingerprint=False,
    )
    if not gate.get("ok"):
        return {
            "ok": False,
            "message": "YouTube TTS export blocked: English TTS input preflight failed",
            "status": "preflight_failed",
            "reason_code": str(gate.get("reason_code") or "tts_input_preflight_failed"),
            "retryable": True,
            "terminal_story": False,
            "queue_persist": False,
            "preflight": gate,
            **plan,
        }

    manifest_path = Path(str(plan["story_manifest"]))
    manifest_gate = audio_production_gate(_load_dict(manifest_path))
    if options.production and not manifest_gate.get("ok"):
        return {
            "ok": False,
            "message": "YouTube TTS export blocked: audio production gate failed",
            "status": "audio_production_gate_failed",
            "reason_code": str(manifest_gate.get("reason_code") or "tts_audio_production_gate_failed"),
            "retryable": True,
            "terminal_story": False,
            "queue_persist": False,
            "gate": manifest_gate,
            **plan,
        }

    if not options.execute:
        return {"ok": True, "status": "dry_run", **plan}

    drive_root = Path(str(plan["drive_root"]))
    missing_mount = _drive_mount_blocker(drive_root)
    if missing_mount:
        return {
            "ok": False,
            "status": "blocked_drive_missing",
            "reason_code": "tts_drive_root_missing",
            "message": f"YouTube TTS export blocked: Google Drive root is not mounted: {missing_mount}",
            "retryable": True,
            "terminal_story": False,
            "queue_persist": False,
            **plan,
        }

    source_text = Path(str(plan["source_text_path"]))
    drive_text = Path(str(plan["drive_text_path"]))
    for key in ("jobs_dir", "texts_dir", "audio_dir", "manifests_dir", "logs_dir", "done_dir", "failed_dir"):
        Path(str(plan[key])).mkdir(parents=True, exist_ok=True)
    Path(str(plan["expected_local_audio_path"])).parent.mkdir(parents=True, exist_ok=True)

    _bridge_copyfile(config, source_text, drive_text, function="run_youtube_tts_kokoro_colab_export")

    job_path = Path(str(plan["youtube_tts_job_path"]))
    items = _merge_job_items(job_path, plan["job_item"])
    expected_audio_names = _write_expected_audio_files(Path(str(plan["expected_files_path"])), items)
    _write_text(Path(str(plan["expected_count_path"])), f"{len(expected_audio_names)}\n")

    job_payload = {
        "version": 1,
        "kind": "youtube_tts_kokoro_colab",
        "drive_root": str(plan["drive_root"]),
        "created_at": str(plan["created_at"]),
        "items": items,
    }
    _write_json(job_path, job_payload)
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
    require_production_launch_id(
        options.launch_id or None,
        production=bool(options.production),
        command="youtube tts-kokoro-colab import",
    )
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

    if not options.force:
        return {
            "ok": False,
            "message": "YouTube TTS import requires --force after validated new audio",
            "status": "force_required",
            **plan,
        }

    readiness = evaluate_tts_import_readiness(
        config=config,
        story_id=str(plan.get("canonical_basename") or options.story_id),
        drive_root=options.drive_root,
        launch_id=str(options.launch_id or ""),
        launch_root=options.launch_root,
        require_drive_audio=True,
    )
    if not readiness.get("ok"):
        return {
            "ok": False,
            "message": (
                "YouTube TTS import blocked: run "
                '`python -m orchestrator youtube audio inspect-tts-input --story-id "..." --for-import` '
                "and ensure exit code 0"
            ),
            "status": "preflight_failed",
            "preflight": readiness,
            "next_cmd": (
                f'python -m orchestrator youtube audio inspect-tts-input --story-id '
                f'"{plan.get("canonical_basename") or options.story_id}" --for-import'
            ),
            **plan,
        }

    source = Path(str(plan["expected_drive_audio_path"]))
    if not source.is_file() or source.stat().st_size <= 0:
        return {"ok": False, "message": "expected drive audio missing", "status": "missing", **plan}

    target = Path(str(plan["expected_local_audio_path"]))
    if target.exists() and not options.force:
        return {"ok": False, "message": f"local audio already exists: {target}", "status": "target_exists", **plan}

    target.parent.mkdir(parents=True, exist_ok=True)
    _bridge_copyfile(config, source, target, function="run_youtube_tts_kokoro_colab_import")
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

    voice = str(tts.get("kokoro_voice") or "")
    if not voice:
        return {"ok": False, "message": "missing kokoro_voice in manifest", "status": "voice_contract_missing"}
    lang_code = voice.strip().lower()[:1] if voice else "a"
    text_path = Path(str(plan["source_text_path"]))
    text_hash = str(plan.get("source_text_hash") or "")
    drive_text_hash = readiness.get("drive_text_hash") or ""
    chunks = _pack_kokoro_chunks(text_path.read_text(encoding="utf-8", errors="replace")) if text_path.is_file() else []
    duration_sec: float | None = None
    try:
        duration_sec = float(get_media_duration(target))
    except (OSError, RuntimeError):
        duration_sec = None

    validation_ok = bool(readiness.get("ok")) and bool(text_hash) and text_hash == drive_text_hash
    imported_at = tts["imported_at"]
    audio_report = {
        "imported_at": imported_at,
        "audio_created_at": _modified_iso(source),
        "current_text_hash": text_hash,
        "drive_text_hash": drive_text_hash,
        "current_audio_hash": _sha256_file(target),
        "chunks_count": len(chunks),
        "voice": voice,
        "expected_gender": str(manifest.get("voice_contract", {}).get("expected_gender") or tts.get("voice_label") or ""),
        "used_gender": kokoro_voice_gender(voice),
        "used_kokoro_voice": voice,
        "lang_code": lang_code,
        "duration_sec": round(duration_sec, 3) if duration_sec is not None else None,
        "validation_ok": validation_ok,
        "drive_audio_path": str(source),
        "local_audio_path": str(target),
        "preflight": readiness,
    }
    write_audio_import_report(story_dir=story_dir, payload=audio_report)

    _write_json(manifest_path, manifest)

    from orchestrator.youtube_audio_recovery import invalidate_downstream_for_new_audio

    invalidate_downstream_for_new_audio(story_dir=story_dir, manifest=manifest)

    deactivate_audio_rejected(story_dir, note="import passed import-readiness gate")
    patch_story_manifest_audio_contract(
        manifest_path=manifest_path,
        story_dir=story_dir,
        drive_root=options.drive_root,
        audio_rejected=False,
        audio_matches_text=True,
        runpod_package_stale=True,
        current_audio_hash=audio_report["current_audio_hash"],
    )

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
        "audio_import_report": str(story_dir / "04_audio" / "reports" / "audio_import_report.json"),
        "validation_ok": validation_ok,
        **plan,
    }
