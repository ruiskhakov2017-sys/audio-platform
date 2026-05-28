from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.site_tts.batch import iter_human_launch_story_dirs, iter_site_story_dirs, voice_type_for_site_folder
from orchestrator.site_tts.colab_batch import _clean_text_for_drive_tts, _drive_dir_from, _drive_root, _safe_name
from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.contract import SiteTtsPaths
from orchestrator.site_tts.drive_voice_resolve import build_kokoro_drive_voice_item_from_paths


MIN_MP3_BYTES = 256
QUEUE_KIND = "site_tts"
WORKER_EMAILS = (
    "ru.iskhakov2017@gmail.com",
    "isi.cordeiro@gmail.com",
    "iheuko119@gmail.com",
    "goegoeseijin@gmail.com",
    "suteadodesun6@gmail.com",
)


@dataclass(frozen=True)
class SiteTtsQueueRecord:
    story_id: str
    source_text_path: Path
    local_target_path: Path
    drive_root: Path
    texts_dir: Path
    mp3_dir: Path
    job_id: str
    text_name: str
    audio_name: str
    voice_label: str
    kokoro_voice: str
    lang_code: str
    speed: float
    sample_rate: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _valid_file(path: Path, *, min_bytes: int = MIN_MP3_BYTES) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _rel_drive(path: Path, drive_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(drive_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _queue_root(drive_root: Path) -> Path:
    return drive_root / "queue" / QUEUE_KIND


def _queue_dirs(drive_root: Path) -> dict[str, Path]:
    root = _queue_root(drive_root)
    return {
        "root": root,
        "pending": root / "pending",
        "global_pending": root / "global_pending",
        "assigned": root / "assigned",
        "leases": root / "leases",
        "processing": root / "processing",
        "done": root / "done",
        "failed": root / "failed",
        "stale": root / "stale",
        "invalid": root / "invalid",
        "locks": root / "locks",
        "events": root / "events",
    }


def _ensure_queue_dirs(drive_root: Path) -> None:
    for path in _queue_dirs(drive_root).values():
        path.mkdir(parents=True, exist_ok=True)
    for email in WORKER_EMAILS:
        base = drive_root / "workers" / email
        (base / "logs").mkdir(parents=True, exist_ok=True)
        (base / "tmp").mkdir(parents=True, exist_ok=True)
        assigned = _queue_dirs(drive_root)["assigned"] / email
        for name in ("pending", "processing", "done", "failed"):
            (assigned / name).mkdir(parents=True, exist_ok=True)


def _append_event(drive_root: Path, event: dict[str, Any]) -> None:
    path = _queue_dirs(drive_root)["events"] / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp": _utc_now_iso(), **event}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _done_marker_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["done"] / f"{job_id}.done.json"


def _pending_job_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["pending"] / f"{job_id}.json"


def _global_pending_job_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["global_pending"] / f"{job_id}.json"


def _failed_marker_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["failed"] / f"{job_id}.failed.json"


def _invalid_marker_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["invalid"] / f"{job_id}.invalid.json"


def _processing_marker_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["processing"] / f"{job_id}.processing.json"


def _lock_dir_path(drive_root: Path, job_id: str) -> Path:
    return _queue_dirs(drive_root)["locks"] / f"{job_id}.lock"


def _assigned_worker_dir(drive_root: Path, worker_email: str) -> Path:
    return _queue_dirs(drive_root)["assigned"] / worker_email


def _assigned_job_path(drive_root: Path, worker_email: str, state: str, job_id: str) -> Path:
    return _assigned_worker_dir(drive_root, worker_email) / state / f"{job_id}.json"


def _done_marker_from_job_payload(
    job: dict[str, Any],
    *,
    mp3_path: Path,
    claim_path: Path | None = None,
) -> dict[str, Any]:
    detected_at = _utc_now_iso()
    payload = dict(job)
    payload.update(
        {
            "status": "done",
            "done_at": detected_at,
            "adopted_from_existing_drive_mp3": True,
            "mp3_path": str(mp3_path),
            "size_bytes": _file_size(mp3_path),
            "detected_at": detected_at,
        }
    )
    if claim_path is not None:
        payload["claim_path"] = str(claim_path)
    return payload


def _resolve_drive_layout(project_root: Path) -> tuple[Any, Path, Path, Path]:
    settings = load_site_tts_settings(project_root)
    drive_root = _drive_root(project_root, settings)
    if drive_root is None:
        raise ValueError("google_drive_tts.root_dir is not configured in configs/site_tts.yaml")
    texts_dir = _drive_dir_from(project_root, settings, "texts", "texts")
    mp3_dir = _drive_dir_from(project_root, settings, "mp3", "mp3")
    return settings, drive_root, texts_dir, mp3_dir


def _resolve_records(
    project_root: Path,
    *,
    site_root: Path,
    human_launch: Path | None,
) -> tuple[list[SiteTtsQueueRecord], dict[str, Any]]:
    settings, drive_root, texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    story_dirs = iter_human_launch_story_dirs(human_launch) if human_launch is not None else iter_site_story_dirs(site_root)
    records: list[SiteTtsQueueRecord] = []
    skipped_no_clean = 0
    skipped_no_info = 0
    for folder in story_dirs:
        paths = (
            SiteTtsPaths.from_human_launch_story(human_launch, folder.name, ensure_dirs=False)
            if human_launch is not None
            else SiteTtsPaths.for_site_output_folder(project_root, site_root, folder.name)
        )
        if not paths.cleaned_story_txt.is_file():
            skipped_no_clean += 1
            continue
        voice_label = voice_type_for_site_folder(paths)
        if voice_label not in {"M", "F", "U"}:
            skipped_no_info += 1
            voice_label = str(settings.voice_selection_fallback_label or "U").strip().upper()[:1] or "U"
        safe_story = _safe_name(folder.name)
        job_id = f"{safe_story}__{voice_label}"
        text_name = f"{job_id}.txt"
        audio_name = f"{job_id}.mp3"
        voice_item = build_kokoro_drive_voice_item_from_paths(
            paths=paths,
            txt_name=text_name,
            mp3_name=audio_name,
            story_id=folder.name,
            settings=settings,
            filename_voice_label=voice_label,
        )
        records.append(
            SiteTtsQueueRecord(
                story_id=folder.name,
                source_text_path=paths.cleaned_story_txt,
                local_target_path=paths.output_mp3,
                drive_root=drive_root,
                texts_dir=texts_dir,
                mp3_dir=mp3_dir,
                job_id=job_id,
                text_name=text_name,
                audio_name=audio_name,
                voice_label=str(voice_item.get("voice_label") or voice_label),
                kokoro_voice=str(voice_item.get("kokoro_voice") or settings.default_voice),
                lang_code=str(voice_item.get("lang_code") or ""),
                speed=float(voice_item.get("speed") or settings.kokoro_speed),
                sample_rate=24000,
            )
        )
    diag = {
        "story_dirs": len(story_dirs),
        "skipped_no_clean": skipped_no_clean,
        "skipped_no_info_or_voice_fallback": skipped_no_info,
    }
    return records, diag


def _job_payload(record: SiteTtsQueueRecord, *, status: str = "pending", created_at: str | None = None) -> dict[str, Any]:
    created = created_at or _utc_now_iso()
    return {
        "schema_version": 1,
        "job_id": record.job_id,
        "kind": QUEUE_KIND,
        "story_id": record.story_id,
        "text_name": record.text_name,
        "audio_name": record.audio_name,
        "drive_text_path": _rel_drive(record.texts_dir / record.text_name, record.drive_root),
        "expected_drive_audio_path": _rel_drive(record.mp3_dir / record.audio_name, record.drive_root),
        "local_target_hint": str(record.local_target_path),
        "local_target_path": str(record.local_target_path),
        "source_text_path": str(record.source_text_path),
        "voice_label": record.voice_label,
        "kokoro_voice": record.kokoro_voice,
        "lang_code": record.lang_code,
        "speed": record.speed,
        "sample_rate": record.sample_rate,
        "status": status,
        "attempts": 0,
        "max_attempts": 3,
        "created_at": created,
        "idempotency_key": f"site_tts:{record.job_id}",
    }


def _done_marker_payload(record: SiteTtsQueueRecord, *, adopted: bool, detected_at: str | None = None) -> dict[str, Any]:
    audio = record.mp3_dir / record.audio_name
    payload = _job_payload(record, status="done", created_at=detected_at or _utc_now_iso())
    payload.update(
        {
            "done_at": detected_at or _utc_now_iso(),
            "adopted_from_existing_drive_mp3": adopted,
            "mp3_path": str(audio),
            "size_bytes": _file_size(audio),
            "detected_at": detected_at or _utc_now_iso(),
        }
    )
    return payload


def _scan_drive_mp3(mp3_dir: Path) -> dict[str, Any]:
    mp3_files = sorted([p for p in mp3_dir.glob("*.mp3") if p.is_file()], key=lambda p: p.name.lower()) if mp3_dir.is_dir() else []
    valid = [p for p in mp3_files if _valid_file(p)]
    invalid = [p for p in mp3_files if not _valid_file(p)]
    by_lower: dict[str, list[str]] = {}
    for p in mp3_files:
        by_lower.setdefault(p.name.casefold(), []).append(p.name)
    duplicates = {k: v for k, v in by_lower.items() if len(v) > 1}
    partials: list[str] = []
    if mp3_dir.is_dir():
        for pattern in ("*.tmp", "*.partial", "*.mp3.partial"):
            partials.extend(str(p) for p in mp3_dir.glob(pattern) if p.is_file())
    return {
        "mp3_count": len(mp3_files),
        "valid_mp3_count": len(valid),
        "invalid_mp3_count": len(invalid),
        "valid_names": {p.name for p in valid},
        "invalid_names": [p.name for p in invalid],
        "partial_or_tmp": sorted(set(partials)),
        "duplicates": duplicates,
    }


def _active_lease_count(drive_root: Path, *, stale_minutes: int = 60) -> tuple[int, int]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ms = max(1, stale_minutes) * 60 * 1000
    active = 0
    stale = 0
    leases_dir = _queue_dirs(drive_root)["leases"]
    if not leases_dir.is_dir():
        return active, stale
    for path in leases_dir.glob("*.claim.json"):
        data = _read_json(path)
        if str(data.get("state", "")).lower() in {"done", "released", "lost"} or data.get("released"):
            continue
        hb = data.get("heartbeat_epoch_ms") or data.get("claimed_at_epoch_ms") or 0
        try:
            hb_ms = int(hb)
        except (TypeError, ValueError):
            hb_ms = 0
        if hb_ms and now_ms - hb_ms <= stale_ms:
            active += 1
        else:
            stale += 1
    return active, stale


def _lease_heartbeat_ms(data: dict[str, Any]) -> int:
    value = data.get("heartbeat_epoch_ms") or data.get("claimed_at_epoch_ms") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _lease_is_released(data: dict[str, Any]) -> bool:
    return str(data.get("state", "") or "").strip().lower() in {"done", "released", "lost", "stale"} or bool(data.get("released"))


def _filename_invalid(value: str) -> bool:
    return not value.strip() or any(ch in value for ch in ("/", "\\", "\x00"))


def _filename_mojibake(value: str) -> bool:
    lowered = value.lower()
    return "Ã" in value or "�" in value or "ã" in lowered


def _lock_status(drive_root: Path, job_id: str, *, stale_minutes: int = 60) -> tuple[str, dict[str, Any]]:
    lock_dir = _lock_dir_path(drive_root, job_id)
    if not lock_dir.exists():
        return "missing", {}
    data = _read_json(lock_dir / "lock.json")
    state = str(data.get("state", "") or "").strip().lower()
    if state in {"done", "released", "failed"} or data.get("released"):
        return "released", data
    heartbeat_ms = _lease_heartbeat_ms(data)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if heartbeat_ms and now_ms - heartbeat_ms <= max(1, stale_minutes) * 60 * 1000:
        return "active", data
    return "stale", data


def _active_lock_count(drive_root: Path, *, stale_minutes: int = 60) -> tuple[int, int]:
    locks_dir = _queue_dirs(drive_root)["locks"]
    if not locks_dir.is_dir():
        return 0, 0
    active = 0
    stale = 0
    for lock_dir in locks_dir.glob("*.lock"):
        if not lock_dir.is_dir():
            continue
        job_id = lock_dir.name.removesuffix(".lock")
        state, _data = _lock_status(drive_root, job_id, stale_minutes=stale_minutes)
        if state == "active":
            active += 1
        elif state == "stale":
            stale += 1
    return active, stale


def _job_id_from_lease(path: Path, data: dict[str, Any]) -> str:
    raw_job_id = str(data.get("job_id") or "").strip()
    if raw_job_id:
        return raw_job_id
    return path.name.split("__", 1)[0].removesuffix(".claim.json")


def _pending_payload_for_job(drive_root: Path, job_id: str) -> dict[str, Any]:
    global_payload = _read_json(_global_pending_job_path(drive_root, job_id))
    if global_payload:
        return global_payload
    payload = _read_json(_pending_job_path(drive_root, job_id))
    if payload:
        return payload
    return {"job_id": job_id, "audio_name": f"{job_id}.mp3", "status": "pending"}


def _pending_job_path_for_id(drive_root: Path, job_id: str) -> Path | None:
    global_exact = _global_pending_job_path(drive_root, job_id)
    if global_exact.is_file():
        return global_exact
    exact = _pending_job_path(drive_root, job_id)
    if exact.is_file():
        return exact
    pending_dir = _queue_dirs(drive_root)["pending"]
    if not pending_dir.is_dir():
        return None
    for path in pending_dir.glob("*.json"):
        data = _read_json(path)
        if str(data.get("job_id") or "").strip() == job_id:
            return path
    return None


def _iter_source_pending_files(drive_root: Path) -> list[Path]:
    qd = _queue_dirs(drive_root)
    global_files = sorted(qd["global_pending"].glob("*.json"), key=lambda p: p.name.lower()) if qd["global_pending"].is_dir() else []
    if global_files:
        return global_files
    return sorted(qd["pending"].glob("*.json"), key=lambda p: p.name.lower()) if qd["pending"].is_dir() else []


def _iter_assigned_files(drive_root: Path, states: tuple[str, ...] = ("pending", "processing")) -> list[tuple[str, str, Path, dict[str, Any]]]:
    assigned_root = _queue_dirs(drive_root)["assigned"]
    rows: list[tuple[str, str, Path, dict[str, Any]]] = []
    if not assigned_root.is_dir():
        return rows
    for worker_dir in sorted([p for p in assigned_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        for state in states:
            folder = worker_dir / state
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json"), key=lambda p: p.name.lower()):
                rows.append((worker_dir.name, state, path, _read_json(path)))
    return rows


def _assigned_job_locations(drive_root: Path) -> dict[str, list[str]]:
    locations: dict[str, list[str]] = {}
    for worker, state, path, data in _iter_assigned_files(drive_root, states=("pending", "processing")):
        job_id = str(data.get("job_id") or path.stem).strip()
        if job_id:
            locations.setdefault(job_id, []).append(f"{worker}/{state}/{path.name}")
    return locations


def _assignment_duplicate_jobs(drive_root: Path, *, state_filter: tuple[str, ...]) -> list[str]:
    seen: dict[str, int] = {}
    for _worker, _state, path, data in _iter_assigned_files(drive_root, states=state_filter):
        job_id = str(data.get("job_id") or path.stem).strip()
        if job_id:
            seen[job_id] = seen.get(job_id, 0) + 1
    return sorted([job_id for job_id, count in seen.items() if count > 1])


def _job_should_not_assign(drive_root: Path, mp3_dir: Path, job: dict[str, Any], assigned_locations: dict[str, list[str]]) -> str:
    job_id = str(job.get("job_id") or "").strip()
    text_name = Path(str(job.get("text_name") or "")).name
    audio_name = Path(str(job.get("audio_name") or f"{job_id}.mp3")).name
    if not job_id:
        return "invalid_job_json"
    if _filename_invalid(job_id) or _filename_invalid(text_name) or _filename_invalid(audio_name):
        return "invalid_filename"
    if _filename_mojibake(job_id) or _filename_mojibake(text_name) or _filename_mojibake(audio_name):
        return "mojibake_filename"
    if _invalid_marker_path(drive_root, job_id).is_file():
        return "invalid_marker_exists"
    if _done_marker_path(drive_root, job_id).is_file():
        return "done_marker_exists"
    if _valid_file(mp3_dir / audio_name):
        return "final_mp3_exists"
    if assigned_locations.get(job_id):
        return "already_assigned"
    if _processing_marker_path(drive_root, job_id).is_file():
        return "legacy_processing_marker_exists"
    if not (drive_root / "texts" / text_name).is_file():
        return "missing_text"
    return ""


def _claimability_for_pending(
    *,
    drive_root: Path,
    mp3_dir: Path,
    path: Path,
    stale_minutes: int = 60,
) -> dict[str, Any]:
    job = _read_json(path)
    readable = bool(job)
    job_id = str(job.get("job_id") or path.stem).strip()
    text_name = Path(str(job.get("text_name") or "")).name
    audio_name = Path(str(job.get("audio_name") or f"{job_id}.mp3")).name
    text_path = drive_root / "texts" / text_name
    final_mp3 = mp3_dir / audio_name
    done_marker = _done_marker_path(drive_root, job_id)
    invalid_marker = _invalid_marker_path(drive_root, job_id)
    processing_marker = _processing_marker_path(drive_root, job_id)
    active_leases = []
    released_leases = []
    stale_leases = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ms = max(1, stale_minutes) * 60 * 1000
    leases_dir = _queue_dirs(drive_root)["leases"]
    lease_paths = [p for p in leases_dir.glob("*.claim.json") if p.name.startswith(f"{job_id}__")] if leases_dir.is_dir() else []
    for lease_path in lease_paths:
        data = _read_json(lease_path)
        if _lease_is_released(data):
            released_leases.append(str(lease_path))
            continue
        hb = _lease_heartbeat_ms(data)
        if hb and now_ms - hb <= stale_ms:
            active_leases.append(str(lease_path))
        else:
            stale_leases.append(str(lease_path))
    lock_state, lock_data = _lock_status(drive_root, job_id, stale_minutes=stale_minutes)
    reject_reason = ""
    if not readable:
        reject_reason = "invalid_job_json"
    elif _filename_invalid(job_id) or _filename_invalid(text_name) or _filename_invalid(audio_name):
        reject_reason = "invalid_filename"
    elif _filename_mojibake(job_id) or _filename_mojibake(text_name) or _filename_mojibake(audio_name):
        reject_reason = "mojibake_filename"
    elif invalid_marker.is_file():
        reject_reason = "invalid_job_json"
    elif done_marker.is_file():
        reject_reason = "done_marker_exists"
    elif _valid_file(final_mp3):
        reject_reason = "final_mp3_exists"
    elif not text_path.is_file():
        reject_reason = "missing_text"
    elif lock_state in {"active", "stale"}:
        reject_reason = "active_lease_exists"
    elif active_leases:
        reject_reason = "active_lease_exists"
    return {
        "pending_json_path": str(path),
        "pending_json_readable": readable,
        "job_id": job_id,
        "text_name": text_name,
        "drive_text_path": str(text_path),
        "text_exists": text_path.is_file(),
        "text_size": _file_size(text_path),
        "audio_name": audio_name,
        "expected_mp3_path": str(final_mp3),
        "final_mp3_exists": final_mp3.is_file(),
        "final_mp3_valid": _valid_file(final_mp3),
        "done_marker_path": str(done_marker),
        "done_marker_exists": done_marker.is_file(),
        "invalid_marker_path": str(invalid_marker),
        "invalid_marker_exists": invalid_marker.is_file(),
        "processing_marker_path": str(processing_marker),
        "processing_marker_exists": processing_marker.is_file(),
        "active_leases": active_leases,
        "released_leases": released_leases,
        "stale_leases": stale_leases,
        "lock_path": str(_lock_dir_path(drive_root, job_id)),
        "lock_state": lock_state,
        "lock": lock_data,
        "can_claim": not reject_reason,
        "reject_reason": reject_reason,
    }


def _lease_requeue_action(
    *,
    drive_root: Path,
    mp3_dir: Path,
    lease_path: Path,
    lease: dict[str, Any],
    stale_minutes: int,
    now_ms: int,
) -> dict[str, Any]:
    job_id = _job_id_from_lease(lease_path, lease)
    job_payload = _pending_payload_for_job(drive_root, job_id)
    audio_name = str(job_payload.get("audio_name") or f"{job_id}.mp3").strip()
    final_mp3 = mp3_dir / Path(audio_name).name
    done_marker = _done_marker_path(drive_root, job_id)
    processing_marker = _processing_marker_path(drive_root, job_id)
    heartbeat_ms = _lease_heartbeat_ms(lease)
    age_minutes = round((now_ms - heartbeat_ms) / 60000, 2) if heartbeat_ms else None
    stale = True
    if heartbeat_ms and now_ms - heartbeat_ms <= max(1, stale_minutes) * 60 * 1000:
        stale = False
    released = _lease_is_released(lease)
    final_mp3_valid = _valid_file(final_mp3)
    done_exists = done_marker.is_file()
    processing_exists = processing_marker.is_file()
    if released:
        planned_action = "skip_released"
    elif not stale:
        planned_action = "skip_active"
    elif final_mp3_valid and not done_exists:
        planned_action = "adopt_done_marker"
    elif final_mp3_valid and done_exists:
        planned_action = "mark_lease_released_done"
    elif not final_mp3_valid and not done_exists:
        planned_action = "mark_stale_release_processing"
    else:
        planned_action = "mark_stale_release"
    return {
        "lease_path": str(lease_path),
        "lease_name": lease_path.name,
        "worker_email": str(lease.get("worker_email") or ""),
        "job_id": job_id,
        "audio_name": audio_name,
        "claimed_at": lease.get("claimed_at", ""),
        "heartbeat_at": lease.get("heartbeat_at", ""),
        "age_minutes": age_minutes,
        "state": str(lease.get("state") or ""),
        "released": released,
        "active": not stale and not released,
        "stale": stale and not released,
        "final_mp3": str(final_mp3),
        "final_mp3_valid": final_mp3_valid,
        "done_marker": str(done_marker),
        "done_marker_exists": done_exists,
        "processing_marker": str(processing_marker),
        "processing_marker_exists": processing_exists,
        "planned_action": planned_action,
    }


def requeue_stale_leases(
    project_root: Path,
    *,
    stale_minutes: int = 60,
    execute: bool = False,
) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    if execute:
        _ensure_queue_dirs(drive_root)
    qd = _queue_dirs(drive_root)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    lease_files = sorted(qd["leases"].glob("*.claim.json"), key=lambda p: p.name.lower()) if qd["leases"].is_dir() else []
    rows: list[dict[str, Any]] = []
    adopted_done = 0
    stale_released = 0
    processing_removed = 0
    skipped_active = 0
    skipped_released = 0
    stale_locks_released = 0
    skipped_active_locks = 0
    errors: list[str] = []

    for lease_path in lease_files:
        lease = _read_json(lease_path)
        row = _lease_requeue_action(
            drive_root=drive_root,
            mp3_dir=mp3_dir,
            lease_path=lease_path,
            lease=lease,
            stale_minutes=stale_minutes,
            now_ms=now_ms,
        )
        rows.append(row)
        action = str(row["planned_action"])
        if action == "skip_active":
            skipped_active += 1
            continue
        if action == "skip_released":
            skipped_released += 1
            continue
        if not execute:
            continue

        try:
            job_id = str(row["job_id"])
            stale_snapshot = qd["stale"] / row["lease_name"]
            stale_snapshot.parent.mkdir(parents=True, exist_ok=True)
            updated_lease = dict(lease)
            updated_lease.update(
                {
                    "state": "stale",
                    "released": True,
                    "released_at": _utc_now_iso(),
                    "stale_marked_at": _utc_now_iso(),
                    "requeue_stale": True,
                    "requeue_reason": "stale lease cleanup before smoke",
                }
            )
            _write_json(lease_path, updated_lease)
            _write_json(stale_snapshot, {**updated_lease, "source_lease_path": str(lease_path)})
            stale_released += 1

            final_mp3 = Path(str(row["final_mp3"]))
            done_marker = Path(str(row["done_marker"]))
            processing_marker = Path(str(row["processing_marker"]))
            if row["final_mp3_valid"] and not row["done_marker_exists"]:
                pending_payload = _pending_payload_for_job(drive_root, job_id)
                _write_json(done_marker, _done_marker_from_job_payload(pending_payload, mp3_path=final_mp3, claim_path=lease_path))
                adopted_done += 1
                _append_event(
                    drive_root,
                    {
                        "event": "requeue_stale_adopt_done_marker",
                        "job_id": job_id,
                        "lease_path": str(lease_path),
                        "mp3_path": str(final_mp3),
                    },
                )
            if (not row["final_mp3_valid"]) and (not row["done_marker_exists"]) and processing_marker.is_file():
                processing_payload = _read_json(processing_marker)
                processing_stale = qd["stale"] / processing_marker.name
                _write_json(
                    processing_stale,
                    {
                        **processing_payload,
                        "state": "stale",
                        "stale_marked_at": _utc_now_iso(),
                        "source_processing_path": str(processing_marker),
                    },
                )
                processing_marker.unlink()
                processing_removed += 1
            _append_event(
                drive_root,
                {
                    "event": "requeue_stale_lease_released",
                    "job_id": job_id,
                    "worker_email": row.get("worker_email", ""),
                    "lease_path": str(lease_path),
                    "planned_action": action,
                    "final_mp3_valid": row["final_mp3_valid"],
                    "done_marker_exists": row["done_marker_exists"],
                    "processing_marker_exists": row["processing_marker_exists"],
                },
            )
        except OSError as exc:
            errors.append(f"{lease_path}: {exc}")

    locks_dir = qd["locks"]
    lock_rows: list[dict[str, Any]] = []
    if locks_dir.is_dir():
        for lock_dir in sorted([p for p in locks_dir.glob("*.lock") if p.is_dir()], key=lambda p: p.name.lower()):
            job_id = lock_dir.name.removesuffix(".lock")
            state, lock_data = _lock_status(drive_root, job_id, stale_minutes=stale_minutes)
            lock_row = {
                "job_id": job_id,
                "lock_path": str(lock_dir),
                "state": state,
                "worker_email": lock_data.get("worker_email", ""),
                "heartbeat_at": lock_data.get("heartbeat_at", ""),
            }
            lock_rows.append(lock_row)
            if state == "active":
                skipped_active_locks += 1
                continue
            if state not in {"stale", "released"}:
                continue
            if not execute:
                continue
            try:
                stale_snapshot = qd["stale"] / f"{lock_dir.name}.json"
                _write_json(
                    stale_snapshot,
                    {
                        **lock_data,
                        "job_id": job_id,
                        "state": "stale",
                        "stale_marked_at": _utc_now_iso(),
                        "source_lock_path": str(lock_dir),
                    },
                )
                lock_json = lock_dir / "lock.json"
                if lock_json.is_file():
                    lock_json.unlink()
                lock_dir.rmdir()
                stale_locks_released += 1
                _append_event(
                    drive_root,
                    {
                        "event": "requeue_stale_lock_released",
                        "job_id": job_id,
                        "lock_path": str(lock_dir),
                        "state": state,
                    },
                )
            except OSError as exc:
                errors.append(f"{lock_dir}: {exc}")

    return {
        "ok": not errors,
        "execute": execute,
        "drive_root": str(drive_root),
        "queue_root": str(_queue_root(drive_root)),
        "stale_lease_minutes": stale_minutes,
        "leases_count": len(rows),
        "planned_touch_count": sum(1 for row in rows if str(row["planned_action"]) not in {"skip_active", "skip_released"}),
        "skipped_active": skipped_active,
        "skipped_released": skipped_released,
        "stale_released": stale_released,
        "stale_locks_released": stale_locks_released,
        "skipped_active_locks": skipped_active_locks,
        "adopted_done_markers": adopted_done,
        "processing_markers_removed": processing_removed,
        "rows": rows,
        "lock_rows": lock_rows,
        "errors": errors,
    }


def _record_state(record: SiteTtsQueueRecord) -> dict[str, Any]:
    drive_audio = record.mp3_dir / record.audio_name
    local_audio = record.local_target_path
    done_marker = _done_marker_path(record.drive_root, record.job_id)
    pending_job = _pending_job_path(record.drive_root, record.job_id)
    failed_marker = _failed_marker_path(record.drive_root, record.job_id)
    processing_marker = _processing_marker_path(record.drive_root, record.job_id)
    drive_valid = _valid_file(drive_audio)
    local_valid = _valid_file(local_audio)
    drive_invalid = drive_audio.is_file() and not drive_valid
    return {
        "drive_valid": drive_valid,
        "local_valid": local_valid,
        "drive_invalid": drive_invalid,
        "done_marker": done_marker.is_file(),
        "pending_job": pending_job.is_file(),
        "failed_marker": failed_marker.is_file(),
        "processing_marker": processing_marker.is_file(),
        "pending_needed": not drive_valid and not local_valid,
    }


def reconcile_drive_queue(
    project_root: Path,
    *,
    site_root: Path,
    human_launch: Path | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    records, diag = _resolve_records(project_root, site_root=site_root, human_launch=human_launch)
    settings, drive_root, texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    _ = settings, texts_dir
    if execute:
        _ensure_queue_dirs(drive_root)

    scan = _scan_drive_mp3(mp3_dir)
    expected_audio_names = {r.audio_name for r in records}
    detected_at = _utc_now_iso()
    existing_drive = 0
    existing_local = 0
    adopted = 0
    pending_needed = 0
    partial_or_invalid = 0
    done_markers_existing = 0
    samples_pending: list[str] = []
    samples_adopted: list[str] = []

    for record in records:
        state = _record_state(record)
        if state["drive_valid"]:
            existing_drive += 1
            marker = _done_marker_path(drive_root, record.job_id)
            if marker.is_file():
                done_markers_existing += 1
            else:
                if execute:
                    _write_json(marker, _done_marker_payload(record, adopted=True, detected_at=detected_at))
                    _append_event(drive_root, {"event": "adopt_done_marker", "job_id": record.job_id, "audio_name": record.audio_name})
                adopted += 1
                if len(samples_adopted) < 10:
                    samples_adopted.append(record.job_id)
            continue
        if state["local_valid"]:
            existing_local += 1
            continue
        if state["drive_invalid"]:
            partial_or_invalid += 1
        pending_needed += 1
        if len(samples_pending) < 10:
            samples_pending.append(record.job_id)

    extra_drive_mp3 = sorted(set(scan["valid_names"]) - expected_audio_names)
    return {
        "ok": True,
        "execute": execute,
        "drive_root": str(drive_root),
        "texts_dir": str(texts_dir),
        "mp3_dir": str(mp3_dir),
        "queue_root": str(_queue_root(drive_root)),
        "total_expected": len(records),
        "story_dirs": diag["story_dirs"],
        "skipped_no_clean": diag["skipped_no_clean"],
        "existing_drive_mp3": existing_drive,
        "existing_local_mp3": existing_local,
        "adopted_done_markers": adopted,
        "done_markers_existing": done_markers_existing,
        "pending_needed": pending_needed,
        "partial_or_invalid": partial_or_invalid + len(scan["partial_or_tmp"]),
        "drive_mp3_total": scan["mp3_count"],
        "drive_valid_mp3_total": scan["valid_mp3_count"],
        "drive_invalid_mp3_total": scan["invalid_mp3_count"],
        "extra_drive_mp3_without_expected_story": len(extra_drive_mp3),
        "duplicates": scan["duplicates"],
        "sample_pending_needed": samples_pending,
        "sample_adopted": samples_adopted,
    }


def export_drive_queue(
    project_root: Path,
    *,
    site_root: Path,
    human_launch: Path | None = None,
    limit: int | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    records, diag = _resolve_records(project_root, site_root=site_root, human_launch=human_launch)
    settings, drive_root, texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    _ = settings
    if execute:
        _ensure_queue_dirs(drive_root)
        texts_dir.mkdir(parents=True, exist_ok=True)
        mp3_dir.mkdir(parents=True, exist_ok=True)

    selected: list[SiteTtsQueueRecord] = []
    skipped_already_done = 0
    skipped_drive_done = 0
    skipped_local_done = 0
    skipped_pending_existing = 0
    skipped_failed_existing = 0
    invalid_or_partial = 0
    created_pending = 0
    copied_texts = 0
    text_existing = 0
    errors: list[str] = []

    for record in records:
        state = _record_state(record)
        if state["drive_valid"] or (state["done_marker"] and _valid_file(record.mp3_dir / record.audio_name)):
            skipped_already_done += 1
            skipped_drive_done += 1
            continue
        if state["local_valid"]:
            skipped_already_done += 1
            skipped_local_done += 1
            continue
        if state["drive_invalid"]:
            invalid_or_partial += 1
        if state["pending_job"]:
            skipped_pending_existing += 1
            continue
        if state["failed_marker"]:
            skipped_failed_existing += 1
            continue
        selected.append(record)
        if limit is not None and len(selected) >= limit:
            break

    if execute:
        created_at = _utc_now_iso()
        for record in selected:
            try:
                raw_text = record.source_text_path.read_text(encoding="utf-8")
                cleaned_text, _url_before, url_after, _removed, _lit = _clean_text_for_drive_tts(raw_text)
                if url_after > 0 or not cleaned_text.strip():
                    errors.append(f"{record.job_id}: clean_text_invalid url_after={url_after} empty={not cleaned_text.strip()}")
                    continue
                dst_text = record.texts_dir / record.text_name
                if dst_text.is_file() and dst_text.stat().st_size > 0:
                    text_existing += 1
                else:
                    dst_text.parent.mkdir(parents=True, exist_ok=True)
                    dst_text.write_text(cleaned_text, encoding="utf-8")
                    copied_texts += 1
                pending_path = _pending_job_path(drive_root, record.job_id)
                if pending_path.is_file():
                    skipped_pending_existing += 1
                    continue
                _write_json(pending_path, _job_payload(record, status="pending", created_at=created_at))
                _append_event(drive_root, {"event": "pending_created", "job_id": record.job_id, "audio_name": record.audio_name})
                created_pending += 1
            except OSError as exc:
                errors.append(f"{record.job_id}: {exc}")

    return {
        "ok": not errors,
        "execute": execute,
        "drive_root": str(drive_root),
        "texts_dir": str(texts_dir),
        "mp3_dir": str(mp3_dir),
        "queue_root": str(_queue_root(drive_root)),
        "total_expected": len(records),
        "story_dirs": diag["story_dirs"],
        "skipped_no_clean": diag["skipped_no_clean"],
        "limit": limit or 0,
        "planned_pending_jobs": len(selected),
        "created_pending_jobs": created_pending,
        "copied_texts": copied_texts,
        "text_existing": text_existing,
        "skipped_already_done": skipped_already_done,
        "skipped_drive_done": skipped_drive_done,
        "skipped_local_done": skipped_local_done,
        "skipped_pending_existing": skipped_pending_existing,
        "skipped_failed_existing": skipped_failed_existing,
        "invalid_or_partial": invalid_or_partial,
        "first_job_ids": [r.job_id for r in selected[:10]],
        "errors": errors,
    }


def queue_status(project_root: Path, *, stale_minutes: int = 60) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    qd = _queue_dirs(drive_root)
    pending = list(qd["pending"].glob("*.json")) if qd["pending"].is_dir() else []
    global_pending = list(qd["global_pending"].glob("*.json")) if qd["global_pending"].is_dir() else []
    processing = list(qd["processing"].glob("*.processing.json")) if qd["processing"].is_dir() else []
    done = list(qd["done"].glob("*.done.json")) if qd["done"].is_dir() else []
    failed = list(qd["failed"].glob("*.failed.json")) if qd["failed"].is_dir() else []
    invalid = list(qd["invalid"].glob("*.invalid.json")) if qd["invalid"].is_dir() else []
    active_leases, stale_leases = _active_lease_count(drive_root, stale_minutes=stale_minutes)
    active_locks, stale_locks = _active_lock_count(drive_root, stale_minutes=stale_minutes)
    scan = _scan_drive_mp3(mp3_dir)
    done_names = {str(_read_json(p).get("audio_name") or "").strip() for p in done}
    done_names.discard("")
    existing_without_marker = sorted(set(scan["valid_names"]) - done_names)
    pending_claimable = 0
    pending_already_done = 0
    pending_invalid = 0
    pending_blocked_by_active_lock = 0
    pending_reasons: dict[str, int] = {}
    for path in (global_pending if global_pending else pending):
        info = _claimability_for_pending(drive_root=drive_root, mp3_dir=mp3_dir, path=path, stale_minutes=stale_minutes)
        reason = str(info.get("reject_reason") or "")
        if not reason:
            pending_claimable += 1
        elif reason in {"done_marker_exists", "final_mp3_exists"}:
            pending_already_done += 1
        elif reason in {"invalid_job_json", "invalid_filename", "mojibake_filename", "missing_text"}:
            pending_invalid += 1
        elif reason == "active_lease_exists":
            pending_blocked_by_active_lock += 1
        pending_reasons[reason or "claimable"] = pending_reasons.get(reason or "claimable", 0) + 1
    assigned_pending_by_worker: dict[str, int] = {}
    assigned_processing_by_worker: dict[str, int] = {}
    assigned_done_by_worker: dict[str, int] = {}
    workers_current_job: dict[str, str] = {}
    for worker, state, path, data in _iter_assigned_files(drive_root, states=("pending", "processing", "done")):
        if state == "pending":
            assigned_pending_by_worker[worker] = assigned_pending_by_worker.get(worker, 0) + 1
        elif state == "processing":
            assigned_processing_by_worker[worker] = assigned_processing_by_worker.get(worker, 0) + 1
            workers_current_job[worker] = str(data.get("job_id") or path.stem)
        elif state == "done":
            assigned_done_by_worker[worker] = assigned_done_by_worker.get(worker, 0) + 1
    duplicate_assigned_jobs = _assignment_duplicate_jobs(drive_root, state_filter=("pending", "processing"))
    duplicate_processing_jobs = _assignment_duplicate_jobs(drive_root, state_filter=("processing",))
    workers: list[dict[str, Any]] = []
    workers_root = drive_root / "workers"
    if workers_root.is_dir():
        for worker_dir in sorted([p for p in workers_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            status = _read_json(worker_dir / "status.json")
            workers.append(
                {
                    "worker_email": worker_dir.name,
                    "state": status.get("state", "unknown"),
                    "current_job": status.get("current_job", ""),
                    "heartbeat_at": status.get("heartbeat_at") or status.get("updated_at", ""),
                    "completed": status.get("completed", 0),
                    "failed": status.get("failed", 0),
                }
            )
    return {
        "ok": True,
        "drive_root": str(drive_root),
        "queue_root": str(_queue_root(drive_root)),
        "pending_count": len(pending),
        "global_pending_count": len(global_pending),
        "processing_count": len(processing),
        "done_count": len(done),
        "failed_count": len(failed),
        "invalid_count": len(invalid),
        "active_leases_count": active_leases,
        "stale_leases_count": stale_leases,
        "active_locks_count": active_locks,
        "stale_locks_count": stale_locks,
        "pending_claimable": pending_claimable,
        "pending_already_done": pending_already_done,
        "pending_invalid": pending_invalid,
        "pending_blocked_by_active_lock": pending_blocked_by_active_lock,
        "pending_reasons": pending_reasons,
        "assigned_pending_by_worker": assigned_pending_by_worker,
        "assigned_processing_by_worker": assigned_processing_by_worker,
        "assigned_done_by_worker": assigned_done_by_worker,
        "workers_current_job": workers_current_job,
        "duplicate_assigned_jobs": duplicate_assigned_jobs,
        "duplicate_processing_jobs": duplicate_processing_jobs,
        "duplicate_assigned_jobs_count": len(duplicate_assigned_jobs),
        "duplicate_processing_jobs_count": len(duplicate_processing_jobs),
        "mp3_done_count": scan["valid_mp3_count"],
        "existing_mp3_without_done_marker": len(existing_without_marker),
        "partial_or_invalid_mp3": scan["invalid_mp3_count"] + len(scan["partial_or_tmp"]),
        "duplicates": scan["duplicates"],
        "workers": workers,
    }


def inspect_queue_job(project_root: Path, *, job_id: str, stale_minutes: int = 60) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    pending_path = _pending_job_path_for_id(drive_root, job_id)
    if pending_path is None:
        pending_path = _pending_job_path(drive_root, job_id)
        return {
            "ok": False,
            "job_id": job_id,
            "pending_json_path": str(pending_path),
            "pending_json_readable": False,
            "can_claim": False,
            "reject_reason": "missing_pending_job",
        }
    info = _claimability_for_pending(drive_root=drive_root, mp3_dir=mp3_dir, path=pending_path, stale_minutes=stale_minutes)
    info["ok"] = True
    return info


def quarantine_queue_job(
    project_root: Path,
    *,
    job_id: str,
    reason: str,
    execute: bool = False,
) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, _mp3_dir = _resolve_drive_layout(project_root)
    qd = _queue_dirs(drive_root)
    if execute:
        _ensure_queue_dirs(drive_root)
    pending_path = _pending_job_path_for_id(drive_root, job_id)
    invalid_path = _invalid_marker_path(drive_root, job_id)
    pending_payload = _read_json(pending_path) if pending_path is not None else {}
    marker = {
        **pending_payload,
        "job_id": job_id,
        "status": "invalid",
        "invalid": True,
        "reason": reason,
        "marked_at": _utc_now_iso(),
        "pending_json_path": str(pending_path) if pending_path is not None else "",
    }
    if execute:
        _write_json(invalid_path, marker)
        if pending_path is not None and pending_path.is_file():
            snapshot_path = qd["invalid"] / pending_path.name
            _write_json(snapshot_path, {**pending_payload, "invalid_snapshot_at": _utc_now_iso(), "reason": reason})
        _append_event(
            drive_root,
            {
                "event": "quarantine_job",
                "job_id": job_id,
                "reason": reason,
                "pending_json_path": str(pending_path) if pending_path is not None else "",
                "invalid_marker_path": str(invalid_path),
            },
        )
    return {
        "ok": True,
        "execute": execute,
        "job_id": job_id,
        "reason": reason,
        "pending_json_path": str(pending_path) if pending_path is not None else "",
        "pending_json_exists": pending_path.is_file() if pending_path is not None else False,
        "invalid_marker_path": str(invalid_path),
        "invalid_marker_written": execute,
        "action": "write_disabled_invalid_marker",
    }


def migrate_to_assigned_queue(project_root: Path, *, execute: bool = False) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    qd = _queue_dirs(drive_root)
    if execute:
        _ensure_queue_dirs(drive_root)
    source_files = sorted(qd["pending"].glob("*.json"), key=lambda p: p.name.lower()) if qd["pending"].is_dir() else []
    copied_to_global = 0
    skipped_done = 0
    skipped_invalid = 0
    skipped_existing_global = 0
    errors: list[str] = []
    for path in source_files:
        data = _read_json(path)
        job_id = str(data.get("job_id") or path.stem).strip()
        audio_name = Path(str(data.get("audio_name") or f"{job_id}.mp3")).name
        if not job_id:
            skipped_invalid += 1
            continue
        if _invalid_marker_path(drive_root, job_id).is_file():
            skipped_invalid += 1
            continue
        if _done_marker_path(drive_root, job_id).is_file() or _valid_file(mp3_dir / audio_name):
            skipped_done += 1
            continue
        dst = _global_pending_job_path(drive_root, job_id)
        if dst.is_file():
            skipped_existing_global += 1
            continue
        if execute:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                payload = dict(data)
                payload.update({"status": "global_pending", "migrated_at": _utc_now_iso(), "source_pending_path": str(path)})
                _write_json(dst, payload)
                _append_event(drive_root, {"event": "migrate_global_pending", "job_id": job_id, "source": str(path), "target": str(dst)})
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
        copied_to_global += 1
    return {
        "ok": not errors,
        "execute": execute,
        "drive_root": str(drive_root),
        "queue_root": str(_queue_root(drive_root)),
        "source_pending_count": len(source_files),
        "copied_to_global_pending": copied_to_global,
        "skipped_done_or_mp3": skipped_done,
        "skipped_invalid": skipped_invalid,
        "skipped_existing_global": skipped_existing_global,
        "errors": errors,
    }


def dispatch_drive_queue(
    project_root: Path,
    *,
    workers: list[str],
    target_per_worker: int = 2,
    max_total_assigned: int = 10,
    execute: bool = False,
) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    if execute:
        _ensure_queue_dirs(drive_root)
    workers = [w.strip() for w in workers if w.strip()]
    assigned_locations = _assigned_job_locations(drive_root)
    source_files = _iter_source_pending_files(drive_root)
    assigned_count_by_worker: dict[str, int] = {worker: 0 for worker in workers}
    for worker, state, _path, _data in _iter_assigned_files(drive_root, states=("pending", "processing")):
        if worker in assigned_count_by_worker and state in {"pending", "processing"}:
            assigned_count_by_worker[worker] += 1
    assigned = 0
    skipped_by_reason: dict[str, int] = {}
    assignments: list[dict[str, str]] = []
    worker_index = 0

    for path in source_files:
        if assigned >= max_total_assigned:
            break
        data = _read_json(path)
        job_id = str(data.get("job_id") or path.stem).strip()
        reason = _job_should_not_assign(drive_root, mp3_dir, data, assigned_locations)
        if reason:
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
            continue
        available_workers = [worker for worker in workers if assigned_count_by_worker.get(worker, 0) < target_per_worker]
        if not available_workers:
            break
        worker = available_workers[worker_index % len(available_workers)]
        worker_index += 1
        assignment_id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{worker.replace('@', '_at_')}_{job_id}"
        dst = _assigned_job_path(drive_root, worker, "pending", job_id)
        payload = dict(data)
        payload.update(
            {
                "status": "assigned_pending",
                "assigned_worker": worker,
                "assigned_at": _utc_now_iso(),
                "assignment_id": assignment_id,
                "source_pending_path": str(path),
            }
        )
        if execute:
            _write_json(dst, payload)
            _append_event(drive_root, {"event": "dispatch_assigned", "job_id": job_id, "worker_email": worker, "assignment_path": str(dst)})
        assigned_locations.setdefault(job_id, []).append(f"{worker}/pending/{dst.name}")
        assigned_count_by_worker[worker] = assigned_count_by_worker.get(worker, 0) + 1
        assigned += 1
        assignments.append({"job_id": job_id, "worker_email": worker, "assignment_path": str(dst)})
    return {
        "ok": True,
        "execute": execute,
        "drive_root": str(drive_root),
        "queue_root": str(_queue_root(drive_root)),
        "workers": workers,
        "target_per_worker": target_per_worker,
        "max_total_assigned": max_total_assigned,
        "source_pending_count": len(source_files),
        "assigned": assigned,
        "assigned_count_by_worker": assigned_count_by_worker,
        "skipped_by_reason": skipped_by_reason,
        "assignments": assignments,
    }


def reclaim_stale_assigned(project_root: Path, *, stale_minutes: int = 120, execute: bool = False) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    qd = _queue_dirs(drive_root)
    if execute:
        _ensure_queue_dirs(drive_root)
    now = datetime.now(timezone.utc)
    reclaimed = 0
    skipped_done = 0
    skipped_fresh = 0
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    for worker, _state, path, data in _iter_assigned_files(drive_root, states=("processing",)):
        job_id = str(data.get("job_id") or path.stem).strip()
        audio_name = Path(str(data.get("audio_name") or f"{job_id}.mp3")).name
        done_marker = _done_marker_path(drive_root, job_id)
        final_mp3 = mp3_dir / audio_name
        age_minutes = max(0.0, (now.timestamp() - path.stat().st_mtime) / 60.0) if path.is_file() else 0.0
        row = {"worker_email": worker, "job_id": job_id, "path": str(path), "age_minutes": round(age_minutes, 2)}
        rows.append(row)
        if done_marker.is_file() or _valid_file(final_mp3):
            skipped_done += 1
            continue
        if age_minutes < stale_minutes:
            skipped_fresh += 1
            continue
        target = _global_pending_job_path(drive_root, job_id)
        if execute:
            try:
                payload = dict(data)
                payload.update({"status": "global_pending", "reclaimed_at": _utc_now_iso(), "reclaimed_from_worker": worker})
                _write_json(target, payload)
                stale_snapshot = qd["stale"] / f"{worker}_{path.name}"
                _write_json(stale_snapshot, {**data, "status": "stale_assigned", "stale_marked_at": _utc_now_iso(), "source_assignment_path": str(path)})
                path.unlink()
                _append_event(drive_root, {"event": "reclaim_stale_assigned", "job_id": job_id, "worker_email": worker, "target": str(target)})
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
        reclaimed += 1
    return {
        "ok": not errors,
        "execute": execute,
        "drive_root": str(drive_root),
        "queue_root": str(_queue_root(drive_root)),
        "stale_minutes": stale_minutes,
        "reclaimed": reclaimed,
        "skipped_done_or_mp3": skipped_done,
        "skipped_fresh": skipped_fresh,
        "rows": rows,
        "errors": errors,
    }


def verify_drive_queue(project_root: Path, *, stale_minutes: int = 60) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    qd = _queue_dirs(drive_root)
    pending_files = list(qd["pending"].glob("*.json")) if qd["pending"].is_dir() else []
    done_files = list(qd["done"].glob("*.done.json")) if qd["done"].is_dir() else []
    failed_files = list(qd["failed"].glob("*.failed.json")) if qd["failed"].is_dir() else []
    active_leases, stale_leases = _active_lease_count(drive_root, stale_minutes=stale_minutes)
    done_ready = 0
    done_missing = 0
    pending_ready_without_done = 0
    pending_missing = 0
    for path in done_files:
        data = _read_json(path)
        name = str(data.get("audio_name") or "").strip()
        if name and _valid_file(mp3_dir / name):
            done_ready += 1
        else:
            done_missing += 1
    for path in pending_files:
        data = _read_json(path)
        name = str(data.get("audio_name") or "").strip()
        if name and _valid_file(mp3_dir / name):
            pending_ready_without_done += 1
        else:
            pending_missing += 1
    scan = _scan_drive_mp3(mp3_dir)
    return {
        "ok": True,
        "drive_root": str(drive_root),
        "queue_root": str(_queue_root(drive_root)),
        "pending": len(pending_files),
        "done": len(done_files),
        "failed": len(failed_files),
        "done_ready": done_ready,
        "done_missing_output": done_missing,
        "pending_ready_without_done": pending_ready_without_done,
        "pending_missing_output": pending_missing,
        "active_leases": active_leases,
        "stale_leases": stale_leases,
        "mp3_done_count": scan["valid_mp3_count"],
        "partial_or_invalid_mp3": scan["invalid_mp3_count"] + len(scan["partial_or_tmp"]),
    }


def import_drive_queue(project_root: Path, *, execute: bool = False, force: bool = False) -> dict[str, Any]:
    _settings, drive_root, _texts_dir, mp3_dir = _resolve_drive_layout(project_root)
    qd = _queue_dirs(drive_root)
    job_files: dict[str, Path] = {}
    for folder, pattern in ((qd["done"], "*.done.json"), (qd["pending"], "*.json")):
        if not folder.is_dir():
            continue
        for path in folder.glob(pattern):
            data = _read_json(path)
            jid = str(data.get("job_id") or path.stem.replace(".done", "")).strip()
            if jid and jid not in job_files:
                job_files[jid] = path

    imported = 0
    skipped_existing = 0
    missing_mp3 = 0
    invalid_mp3 = 0
    missing_target = 0
    errors: list[str] = []
    planned: list[str] = []
    for _jid, path in sorted(job_files.items()):
        data = _read_json(path)
        audio_name = str(data.get("audio_name") or "").strip()
        local_raw = str(data.get("local_target_path") or data.get("local_target_hint") or "").strip()
        if not audio_name:
            errors.append(f"{path}: missing audio_name")
            continue
        src = mp3_dir / audio_name
        if not src.is_file():
            missing_mp3 += 1
            continue
        if not _valid_file(src):
            invalid_mp3 += 1
            continue
        if not local_raw:
            missing_target += 1
            continue
        dst = Path(local_raw)
        if dst.is_file() and not force:
            skipped_existing += 1
            continue
        planned.append(str(dst))
        if execute:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                if not _valid_file(dst):
                    errors.append(f"{dst}: copied file is invalid")
                    continue
                imported += 1
            except OSError as exc:
                errors.append(f"{dst}: {exc}")

    report = {
        "ok": not errors,
        "execute": execute,
        "drive_root": str(drive_root),
        "mp3_dir": str(mp3_dir),
        "queue_root": str(_queue_root(drive_root)),
        "job_files": len(job_files),
        "planned_import": len(planned),
        "imported": imported,
        "skipped_existing": skipped_existing,
        "missing_mp3": missing_mp3,
        "invalid_mp3": invalid_mp3,
        "missing_target": missing_target,
        "errors": errors,
        "first_targets": planned[:10],
    }
    if execute:
        report_path = qd["events"] / f"import_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        _write_json(report_path, report)
        report["report_path"] = str(report_path)
    return report
