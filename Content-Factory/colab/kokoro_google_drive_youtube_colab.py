"""YouTube Kokoro Google Drive Colab worker (legacy global + launch-scoped jobs).

Legacy global mode (default):
  !python .../scripts/kokoro_google_drive_youtube_colab.py
  Reads: ContentFactory_YouTube/jobs/youtube_tts_job.json
  Writes: ContentFactory_YouTube/audio/<story>.mp3

Launch-scoped sample TTS (Alma recovery):
  !python .../scripts/kokoro_google_drive_youtube_colab.py \\
    --launch-id YT_ALMA_RECOVERY_20260604 --job-type sample \\
    --story-slug Becoming_A_Slut_Wife_Alma

Launch-scoped full TTS:
  !python .../scripts/kokoro_google_drive_youtube_colab.py \\
    --launch-id YT_ALMA_RECOVERY_20260604 --job-type full \\
    --story-slug Becoming_A_Slut_Wife_Alma

Colab one-cell (set variables then run same script path on Drive):
  LAUNCH_ID = "YT_ALMA_RECOVERY_20260604"
  JOB_TYPE = "sample"  # or "full"
  STORY_SLUG = "Becoming_A_Slut_Wife_Alma"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from inspect import signature
from pathlib import Path
from typing import Any

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

FORCE = False
WORKER_MODE = "legacy"  # legacy | launch
LAUNCH_ID = ""
JOB_TYPE = ""  # sample | full
STORY_SLUG = ""
WORKER_INDEX = 0
WORKER_COUNT = 1

GLOBAL_YOUTUBE_ROOT = Path("/content/drive/MyDrive/ContentFactory_YouTube")
LAUNCHES_SUBDIR = "launches"

BASE_DIR = GLOBAL_YOUTUBE_ROOT
TEXTS_DIR = BASE_DIR / "texts"
AUDIO_DIR = BASE_DIR / "audio"
JOBS_DIR = BASE_DIR / "jobs"
LOGS_DIR = BASE_DIR / "logs"
DONE_DIR = BASE_DIR / "done"
FAILED_DIR = BASE_DIR / "failed"
MANIFESTS_DIR = BASE_DIR / "manifests"

JOB_JSON = JOBS_DIR / "youtube_tts_job.json"
EXPECTED_FILES_TXT = JOBS_DIR / "EXPECTED_FILES.txt"
EXPECTED_COUNT_TXT = JOBS_DIR / "EXPECTED_COUNT.txt"
STATUS_JSON = LOGS_DIR / "COLAB_STATUS.json"
LOG_JSONL = LOGS_DIR / "youtube_tts_colab_log.jsonl"
DONE_TXT = DONE_DIR / "COLAB_DONE.txt"
DONE_JSON = DONE_DIR / "COLAB_DONE.json"
FAILED_JSONL = FAILED_DIR / "failed_items.jsonl"
FAILED_JSON = FAILED_DIR / "COLAB_FAILED.json"

DEFAULT_VOICE_LABEL = "U"
DEFAULT_KOKORO_VOICE = "af_bella"
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"
DEFAULT_SPEED = 0.92
DEFAULT_SAMPLE_RATE = 24000
CHUNK_MAX_CHARS = 480
TINY_CHUNK_MIN_CHARS = 25
MIN_MP3_BYTES = 256


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file_bytes(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_duration_sec(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _apply_launch_paths(launch_id: str, job_type: str) -> None:
    """Rebind module path globals to launches/<LAUNCH_ID>/."""
    global BASE_DIR, TEXTS_DIR, AUDIO_DIR, JOBS_DIR, LOGS_DIR, DONE_DIR, FAILED_DIR
    global MANIFESTS_DIR, JOB_JSON, EXPECTED_FILES_TXT, EXPECTED_COUNT_TXT
    global STATUS_JSON, LOG_JSONL, DONE_TXT, DONE_JSON, FAILED_JSONL, FAILED_JSON

    BASE_DIR = GLOBAL_YOUTUBE_ROOT / LAUNCHES_SUBDIR / launch_id
    TEXTS_DIR = BASE_DIR / "texts"
    AUDIO_DIR = BASE_DIR / "audio"
    JOBS_DIR = BASE_DIR / "jobs"
    LOGS_DIR = BASE_DIR / "logs"
    DONE_DIR = BASE_DIR / "done"
    FAILED_DIR = BASE_DIR / "failed"
    MANIFESTS_DIR = BASE_DIR / "manifests"
    if job_type == "sample":
        JOB_JSON = JOBS_DIR / "sample_tts_job.json"
        STATUS_JSON = LOGS_DIR / "SAMPLE_TTS_STATUS.json"
        LOG_JSONL = LOGS_DIR / "sample_tts_colab_log.jsonl"
        DONE_JSON = DONE_DIR / "SAMPLE_TTS_DONE.json"
        FAILED_JSON = FAILED_DIR / "SAMPLE_TTS_FAILED.json"
    else:
        JOB_JSON = JOBS_DIR / "full_tts_job.json"
        STATUS_JSON = LOGS_DIR / "FULL_TTS_STATUS.json"
        LOG_JSONL = LOGS_DIR / "full_tts_colab_log.jsonl"
        DONE_JSON = DONE_DIR / "FULL_TTS_DONE.json"
        FAILED_JSON = FAILED_DIR / "FULL_TTS_FAILED.json"
    EXPECTED_FILES_TXT = JOBS_DIR / "EXPECTED_FILES.txt"
    EXPECTED_COUNT_TXT = JOBS_DIR / "EXPECTED_COUNT.txt"
    DONE_TXT = DONE_DIR / "COLAB_DONE.txt"


def _path_must_be_launch_scoped(path: Path, launch_id: str) -> None:
    resolved = path.resolve()
    raw = str(resolved).replace("\\", "/")
    if launch_id not in raw:
        raise ValueError(f"path must contain launch_id={launch_id}: {path}")
    try:
        rel = resolved.relative_to(GLOBAL_YOUTUBE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"path must be under ContentFactory_YouTube: {path}") from exc
    if not rel.parts or rel.parts[0] != LAUNCHES_SUBDIR:
        raise ValueError(f"refusing global Drive folder in launch mode (use launches/<id>/): {path}")
    if len(rel.parts) < 2 or rel.parts[1] != launch_id:
        raise ValueError(f"path launch folder mismatch: {path}")


_DRIVE_SYNC_RETRIES = 12
_DRIVE_SYNC_WAIT_SEC = 10


def _launch_text_audio_paths(job: dict[str, Any], job_type: str, story_slug: str) -> tuple[Path, Path]:
    slug = str(job.get("story_slug") or story_slug).strip() or story_slug
    launch_id = str(job.get("launch_id") or LAUNCH_ID).strip()
    root = GLOBAL_YOUTUBE_ROOT / LAUNCHES_SUBDIR / launch_id
    if job_type == "sample":
        text_path = root / "texts" / "samples" / f"{slug}_sample.txt"
        audio_path = root / "audio" / "samples" / f"{slug}_sample_60s.mp3"
    else:
        input_colab = str(job.get("input_text_path") or "").strip()
        if input_colab:
            text_path = Path(input_colab)
        elif WORKER_MODE == "launch":
            raise ValueError("FULL_TTS_INPUT_TEXT_PATH_MISSING_IN_JOB")
        else:
            text_path = root / "texts" / f"{slug}_full.txt"
        output_colab = str(job.get("output_audio_path") or "").strip()
        if output_colab:
            audio_path = Path(output_colab)
        else:
            audio_path = root / "audio" / f"{slug}_full.mp3"
    return text_path, audio_path


def _validate_launch_job_preflight(
    job: dict[str, Any],
    *,
    job_type: str,
    launch_id: str,
    story_slug: str,
    text_path: Path,
) -> None:
    if str(job.get("launch_id") or "").strip() and str(job.get("launch_id")).strip() != launch_id:
        raise ValueError(f"job launch_id mismatch: {job.get('launch_id')} != {launch_id}")
    job_slug = str(job.get("story_slug") or "").strip()
    if job_slug and job_slug != story_slug:
        raise ValueError(f"job story_slug mismatch: {job_slug} != {story_slug}")

    expected_lang = str(job.get("expected_language") or "en").strip().lower()
    if not text_path.is_file():
        raise FileNotFoundError(f"input text missing: {text_path}")

    text = text_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ValueError(f"input text empty: {text_path}")

    if expected_lang == "en" and CYRILLIC_RE.search(text):
        raise ValueError("input text contains Cyrillic but expected_language=en")

    hash_key = "sample_text_hash" if job_type == "sample" else "current_text_hash"
    expected_hash = str(job.get(hash_key) or job.get("full_text_hash") or job.get("text_hash") or "").strip()
    if expected_hash:
        actual = _sha256_file_bytes(text_path)
        if actual != expected_hash:
            raise ValueError(f"text hash mismatch: expected={expected_hash[:16]}... actual={actual[:16]}...")


def _validate_launch_job_preflight_with_drive_sync(
    job: dict[str, Any],
    *,
    job_type: str,
    launch_id: str,
    story_slug: str,
    text_path: Path,
) -> None:
    last_exc: BaseException | None = None
    for attempt in range(_DRIVE_SYNC_RETRIES):
        try:
            _validate_launch_job_preflight(
                job,
                job_type=job_type,
                launch_id=launch_id,
                story_slug=story_slug,
                text_path=text_path,
            )
            if attempt > 0:
                logging.info("Drive sync preflight OK after attempt %s: %s", attempt + 1, text_path)
            return
        except FileNotFoundError as exc:
            last_exc = exc
        except ValueError as exc:
            if "hash mismatch" not in str(exc):
                raise
            last_exc = exc
        if attempt < _DRIVE_SYNC_RETRIES - 1:
            logging.warning(
                "Drive sync wait (%ss) before preflight retry %s/%s: %s",
                _DRIVE_SYNC_WAIT_SEC,
                attempt + 2,
                _DRIVE_SYNC_RETRIES,
                text_path,
            )
            time.sleep(_DRIVE_SYNC_WAIT_SEC)
    if last_exc is not None:
        raise last_exc


def normalize_launch_job_item(
    job: dict[str, Any],
    *,
    text_path: Path,
    audio_path: Path,
    story_slug: str,
) -> dict[str, Any]:
    voice = str(job.get("kokoro_voice") or "").strip()
    voice_label = str(job.get("voice_label") or "").strip()
    if not voice or voice_label not in {"M", "F", "U"}:
        raise ValueError("YOUTUBE_TTS_JOB_VOICE_MISSING: job must contain voice_label M/F/U and kokoro_voice")
    try:
        speed = float(job.get("speed") if job.get("speed") is not None else DEFAULT_SPEED)
    except (TypeError, ValueError):
        speed = DEFAULT_SPEED
    return {
        "youtube_run_id": str(job.get("launch_id") or LAUNCH_ID),
        "story_id": str(job.get("story_id") or story_slug),
        "canonical_basename": str(job.get("story_id") or story_slug),
        "text_name": text_path.name,
        "audio_name": audio_path.name,
        "text_path": str(text_path),
        "audio_path": str(audio_path),
        "voice_label": voice_label,
        "kokoro_voice": voice,
        "expected_gender": voice_label,
        "used_gender": voice_label,
        "used_kokoro_voice": voice,
        "lang_code": lang_from_voice(voice),
        "speed": speed,
        "sample_rate": DEFAULT_SAMPLE_RATE,
        "launch_id": LAUNCH_ID,
        "job_type": JOB_TYPE,
        "story_slug": story_slug,
    }


def _match_launch_story_slug(wanted: str, item: dict[str, Any]) -> bool:
    wanted_raw = wanted.strip()
    if not wanted_raw:
        return True
    wanted_safe = re.sub(r"[^A-Za-z0-9]+", "_", wanted_raw).strip("_").casefold()
    candidates = {
        str(item.get("story_slug") or "").strip(),
        str(item.get("story_id") or "").strip(),
        str(item.get("canonical_basename") or "").strip(),
    }
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == wanted_raw:
            return True
        candidate_safe = re.sub(r"[^A-Za-z0-9]+", "_", candidate).strip("_").casefold()
        if candidate_safe == wanted_safe:
            return True
    return False


def load_launch_job_items(launch_id: str, job_type: str, story_slug: str) -> list[dict[str, Any]]:
    job_path = JOB_JSON
    if not job_path.is_file() and job_type == "full":
        batch_job = JOBS_DIR / "youtube_tts_job.json"
        if batch_job.is_file():
            job_path = batch_job
    if not job_path.is_file():
        raise FileNotFoundError(f"launch job missing: {JOB_JSON}")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if not isinstance(job, dict):
        raise ValueError(f"invalid job json: {job_path}")

    raw_items = job.get("items") if isinstance(job.get("items"), list) else None
    if raw_items is not None:
        normalized: list[dict[str, Any]] = []
        wanted = story_slug.strip()
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item = normalize_job_item(raw_item)
            if wanted and not _match_launch_story_slug(wanted, item):
                continue
            text_path = Path(str(item["text_path"]))
            audio_path = Path(str(item["audio_path"]))
            _path_must_be_launch_scoped(text_path, launch_id)
            _path_must_be_launch_scoped(audio_path, launch_id)
            _validate_launch_job_preflight_with_drive_sync(
                {
                    **raw_item,
                    "current_text_hash": str(raw_item.get("source_text_hash") or raw_item.get("current_text_hash") or ""),
                    "expected_language": str(raw_item.get("expected_language") or "en"),
                },
                job_type=job_type,
                launch_id=launch_id,
                story_slug=str(item.get("story_slug") or item.get("canonical_basename") or ""),
                text_path=text_path,
            )
            item["launch_id"] = launch_id
            item["job_type"] = job_type
            item["story_slug"] = str(item.get("story_slug") or item.get("canonical_basename") or "")
            normalized.append(item)
        if normalized:
            return normalized
        raise ValueError(f"no matching launch job items in {job_path} for story_slug={story_slug!r}")

    text_path, audio_path = _launch_text_audio_paths(job, job_type, story_slug)
    _path_must_be_launch_scoped(text_path, launch_id)
    _path_must_be_launch_scoped(audio_path, launch_id)
    _validate_launch_job_preflight_with_drive_sync(
        job,
        job_type=job_type,
        launch_id=launch_id,
        story_slug=story_slug,
        text_path=text_path,
    )
    return [normalize_launch_job_item(job, text_path=text_path, audio_path=audio_path, story_slug=story_slug)]


def load_launch_partition_items(launch_id: str, worker_index: int, worker_count: int) -> list[dict[str, Any]]:
    if worker_index < 0 or worker_index >= worker_count:
        raise ValueError(f"--worker-index must be 0..{worker_count - 1}; got {worker_index}")
    job_path = JOBS_DIR / "youtube_tts_job.json"
    partition_path = JOBS_DIR / "partitions" / f"worker_{worker_index}.json"
    if not job_path.is_file():
        raise FileNotFoundError(f"launch job missing: {job_path}")
    if not partition_path.is_file():
        raise FileNotFoundError(f"launch partition missing: {partition_path}")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    part = json.loads(partition_path.read_text(encoding="utf-8"))
    if not isinstance(job, dict) or str(job.get("kind") or "") != "youtube_tts_launch_partitioned_v1":
        raise ValueError(f"invalid launch job kind: {job_path}")
    if not isinstance(part, dict) or str(part.get("kind") or "") != "youtube_tts_launch_partitioned_v1_partition":
        raise ValueError(f"invalid launch partition kind: {partition_path}")
    if str(job.get("youtube_run_id") or "") != launch_id or str(part.get("youtube_run_id") or "") != launch_id:
        raise ValueError(f"launch job mismatch for {launch_id}: {job_path} / {partition_path}")
    raw_items = part.get("items") if isinstance(part.get("items"), list) else []
    normalized: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = normalize_job_item(raw_item)
        text_path = Path(str(item["text_path"]))
        audio_path = Path(str(item["audio_path"]))
        _path_must_be_launch_scoped(text_path, launch_id)
        _path_must_be_launch_scoped(audio_path, launch_id)
        if not text_path.is_file():
            raise FileNotFoundError(f"partition input text missing: {text_path}")
        item["launch_id"] = launch_id
        item["job_type"] = "full"
        item["worker_index"] = worker_index
        normalized.append(item)
    return normalized


def write_launch_done_marker(*, item: dict[str, Any], audio_path: Path, job: dict[str, Any]) -> None:
    duration = _probe_duration_sec(audio_path)
    payload = {
        "status": "done",
        "launch_id": LAUNCH_ID,
        "job_type": JOB_TYPE,
        "story_slug": STORY_SLUG,
        "output_path": str(audio_path),
        "output_size_bytes": audio_path.stat().st_size if audio_path.is_file() else 0,
        "duration_sec": round(duration, 3) if duration is not None else None,
        "audio_hash": _sha256_file(audio_path),
        "input_text_hash": str(job.get("sample_text_hash") or job.get("current_text_hash") or ""),
        "finished_at": now_utc(),
    }
    DONE_JSON.parent.mkdir(parents=True, exist_ok=True)
    DONE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_launch_failed_marker(*, error: str, item: dict[str, Any] | None = None) -> None:
    payload = {
        "status": "failed",
        "launch_id": LAUNCH_ID,
        "job_type": JOB_TYPE,
        "story_slug": STORY_SLUG,
        "error": error,
        "item": item,
        "failed_at": now_utc(),
    }
    FAILED_JSON.parent.mkdir(parents=True, exist_ok=True)
    FAILED_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def configure_worker_from_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube Kokoro Colab Drive worker")
    parser.add_argument("--youtube-run-id", default=os.environ.get("CF_YOUTUBE_RUN_ID", ""))
    parser.add_argument("--launch-id", default=os.environ.get("CF_LAUNCH_ID", ""))
    parser.add_argument("--job-type", choices=["sample", "full", "legacy"], default=os.environ.get("CF_JOB_TYPE", "legacy"))
    parser.add_argument("--story-slug", default=os.environ.get("CF_STORY_SLUG", ""))
    parser.add_argument("--worker-index", type=int, default=int(os.environ.get("CF_WORKER_INDEX", "0") or 0))
    parser.add_argument("--worker-count", type=int, default=int(os.environ.get("CF_WORKER_COUNT", "1") or 1))
    parser.add_argument("--force", action="store_true", help="Re-render even if output mp3 exists")
    return parser.parse_args(argv)


def init_worker(argv: list[str] | None = None) -> None:
    global WORKER_MODE, LAUNCH_ID, JOB_TYPE, STORY_SLUG, WORKER_INDEX, WORKER_COUNT, FORCE
    args = configure_worker_from_args(argv)
    if args.force:
        FORCE = True
    WORKER_COUNT = max(1, int(args.worker_count or 1))
    WORKER_INDEX = int(args.worker_index or 0)
    if WORKER_INDEX < 0:
        WORKER_INDEX = 0
    if WORKER_INDEX > 0 and WORKER_INDEX > WORKER_COUNT:
        raise ValueError(f"--worker-index must be between 1 and --worker-count ({WORKER_COUNT}); got {WORKER_INDEX}")

    launch_id = str(args.youtube_run_id or args.launch_id or os.environ.get("LAUNCH_ID", "")).strip()
    job_type = str(args.job_type or os.environ.get("JOB_TYPE", "")).strip().lower()
    story_slug = str(args.story_slug or os.environ.get("STORY_SLUG", "")).strip()

    if args.youtube_run_id:
        WORKER_MODE = "launch"
        LAUNCH_ID = launch_id
        JOB_TYPE = "full"
        STORY_SLUG = story_slug
        _apply_launch_paths(launch_id, "full")
        print(
            f"[INFO] production launch partition mode youtube_run_id={launch_id} "
            f"worker_index={WORKER_INDEX} worker_count={WORKER_COUNT} base_dir={BASE_DIR}",
            flush=True,
        )
        return

    if launch_id and job_type in {"sample", "full"}:
        if not story_slug and job_type == "sample":
            raise ValueError("--story-slug is required for launch mode")
        WORKER_MODE = "launch"
        LAUNCH_ID = launch_id
        JOB_TYPE = job_type
        STORY_SLUG = story_slug
        _apply_launch_paths(launch_id, job_type)
        print(
            f"[INFO] launch mode launch_id={launch_id} job_type={job_type} story_slug={story_slug} "
            f"worker_index={WORKER_INDEX} worker_count={WORKER_COUNT} base_dir={BASE_DIR}",
            flush=True,
        )
        return

    WORKER_MODE = "legacy"
    print(f"[INFO] legacy global mode base_dir={BASE_DIR}", flush=True)


def ensure_dirs() -> None:
    for folder in (TEXTS_DIR, AUDIO_DIR, JOBS_DIR, LOGS_DIR, DONE_DIR, FAILED_DIR, MANIFESTS_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def traceback_tail(exc: BaseException, *, lines: int = 8) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    parts = [line for line in tb.strip().splitlines() if line.strip()]
    return "\n".join(parts[-lines:])


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp": now_utc(), **payload}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_status(**payload: Any) -> None:
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(
        json.dumps({"updated_at": now_utc(), **payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def log_stdout(message: str, **event: Any) -> None:
    print(message, flush=True)
    payload = {"message": message, **event}
    if "event" not in payload:
        payload["event"] = "log"
    append_jsonl(LOG_JSONL, payload)


def partition_items_for_worker(items: list[dict[str, Any]], *, worker_index: int, worker_count: int) -> list[dict[str, Any]]:
    if worker_index <= 0 or worker_count <= 1:
        return items
    return [item for index, item in enumerate(items, start=1) if ((index - 1) % worker_count) + 1 == worker_index]


def read_status() -> dict[str, Any]:
    if not STATUS_JSON.is_file():
        return {}
    try:
        data = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def configure_colab_logging() -> None:
    """Keep Colab stdout readable; detailed worker events still go to JSONL."""
    for logger_name in ("phonemizer", "phonemizer.backend", "phonemizer.separator"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.propagate = False


def detect_device() -> str:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    except Exception:
        return "unknown"


def make_pipeline(KPipeline: Any, *, lang_code: str, device: str) -> Any:
    try:
        params = signature(KPipeline).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs: dict[str, Any] = {"lang_code": lang_code}
    if "repo_id" in params:
        kwargs["repo_id"] = KOKORO_REPO_ID
    pipeline = KPipeline(**kwargs)
    model = getattr(pipeline, "model", None)
    if device == "cuda" and hasattr(model, "to"):
        model.to(device)
    return pipeline


def basename_from_path(raw: Any) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    return Path(value).name


def lang_from_voice(voice: str) -> str:
    first = (voice or "").strip().lower()[:1]
    return first if first in "abefhijpz" else "a"


def split_oversized_paragraph(paragraph: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(paragraph):
        end = min(start + max_chars, len(paragraph))
        piece = paragraph[start:end]
        if end < len(paragraph):
            cut = piece.rfind(". ")
            if cut > max_chars // 2:
                piece = piece[: cut + 1]
                end = start + len(piece)
        cleaned = piece.strip()
        if cleaned:
            parts.append(cleaned)
        if end <= start:
            end = min(start + max_chars, len(paragraph))
        start = end
    return parts


def pack_paragraph_chunks(text: str, max_chars: int) -> list[str]:
    max_chars = max(200, int(max_chars))
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return split_oversized_paragraph(text.strip(), max_chars) if text.strip() else []

    chunks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if buffer:
            chunks.append("\n\n".join(buffer))
            buffer = []
            buffer_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            chunks.extend(split_oversized_paragraph(paragraph, max_chars))
            continue
        add_len = len(paragraph) + (2 if buffer else 0)
        if buffer_len + add_len > max_chars:
            flush()
        buffer.append(paragraph)
        buffer_len += add_len
    flush()
    return chunks


def chunk_has_text(chunk: str) -> bool:
    return any(ch.isalnum() for ch in chunk)


def normalize_tts_chunks(chunks: list[str], *, min_chars: int = TINY_CHUNK_MIN_CHARS) -> tuple[list[str], dict[str, int]]:
    normalized: list[str] = []
    pending_tiny: list[str] = []
    stats = {
        "chunks_before_normalize": len(chunks),
        "chunks_after_normalize": 0,
        "tiny_chunks_merged": 0,
        "empty_chunks_skipped": 0,
        "junk_chunks_skipped": 0,
    }

    for raw_chunk in chunks:
        chunk = raw_chunk.strip()
        if not chunk:
            stats["empty_chunks_skipped"] += 1
            continue
        if not chunk_has_text(chunk):
            stats["junk_chunks_skipped"] += 1
            continue
        if pending_tiny:
            chunk = " ".join([*pending_tiny, chunk]).strip()
            stats["tiny_chunks_merged"] += len(pending_tiny)
            pending_tiny = []
        if len(chunk) < min_chars:
            if normalized:
                normalized[-1] = f"{normalized[-1]} {chunk}".strip()
                stats["tiny_chunks_merged"] += 1
            else:
                pending_tiny.append(chunk)
            continue
        normalized.append(chunk)

    if pending_tiny:
        tail = " ".join(pending_tiny).strip()
        if normalized:
            normalized[-1] = f"{normalized[-1]} {tail}".strip()
            stats["tiny_chunks_merged"] += len(pending_tiny)
        elif tail:
            normalized.append(tail)

    stats["chunks_after_normalize"] = len(normalized)
    return normalized, stats


def silence_chunk(np: Any, sample_rate: int, *, duration_seconds: float = 0.15) -> Any:
    return np.zeros(max(1, int(sample_rate * duration_seconds)), dtype=np.float32)


def read_expected_files() -> list[str]:
    if not EXPECTED_FILES_TXT.is_file():
        return []
    return [
        Path(line.strip()).name
        for line in EXPECTED_FILES_TXT.read_text(encoding="utf-8").splitlines()
        if line.strip().lower().endswith(".mp3")
    ]


def read_expected_count(expected_files: list[str]) -> int:
    if not EXPECTED_COUNT_TXT.is_file():
        return len(expected_files)
    try:
        return int(EXPECTED_COUNT_TXT.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return len(expected_files)


def normalize_job_item(item: dict[str, Any]) -> dict[str, Any]:
    canonical = str(item.get("canonical_basename") or "").strip() or "youtube_story"
    text_name = basename_from_path(item.get("drive_text_path")) or f"{canonical}.txt"
    audio_name = basename_from_path(item.get("expected_drive_audio_path")) or Path(text_name).with_suffix(".mp3").name
    voice = str(item.get("kokoro_voice") or "").strip()
    voice_label = str(item.get("voice_label") or "").strip().upper()[:1]
    if not voice or voice_label not in {"M", "F", "U"}:
        raise ValueError("YOUTUBE_TTS_JOB_VOICE_MISSING")
    try:
        speed = float(item.get("speed") if item.get("speed") is not None else DEFAULT_SPEED)
    except (TypeError, ValueError):
        speed = DEFAULT_SPEED
    try:
        sample_rate = int(item.get("sample_rate") if item.get("sample_rate") is not None else DEFAULT_SAMPLE_RATE)
    except (TypeError, ValueError):
        sample_rate = DEFAULT_SAMPLE_RATE
    return {
        "youtube_run_id": str(item.get("youtube_run_id") or "").strip(),
        "story_id": str(item.get("story_id") or "").strip(),
        "canonical_basename": canonical,
        "text_name": text_name,
        "audio_name": audio_name,
        "text_path": str(TEXTS_DIR / text_name),
        "audio_path": str(AUDIO_DIR / audio_name),
        "voice_label": voice_label,
        "kokoro_voice": voice,
        "lang_code": lang_from_voice(voice),
        "speed": speed,
        "sample_rate": sample_rate,
    }


def load_job_items() -> list[dict[str, Any]]:
    if WORKER_MODE == "launch":
        if WORKER_COUNT > 1:
            return load_launch_partition_items(LAUNCH_ID, WORKER_INDEX, WORKER_COUNT)
        return load_launch_job_items(LAUNCH_ID, JOB_TYPE, STORY_SLUG)

    if JOB_JSON.is_file():
        payload = json.loads(JOB_JSON.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else None
        out = [normalize_job_item(item) for item in items or [] if isinstance(item, dict)]
        if out:
            return out

    expected_files = read_expected_files()
    if expected_files:
        raise FileNotFoundError(
            "YOUTUBE_TTS_JOB_VOICE_MISSING: legacy EXPECTED_FILES mode requires youtube_tts_job.json with voice"
        )
    return []


def mp3_valid(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_MP3_BYTES
    except OSError:
        return False


def cleanup_partial(audio_path: Path) -> None:
    audio_dir = audio_path.parent
    stem = audio_path.stem
    for path in (audio_dir / f"{stem}.partial.mp3", audio_dir / f"{stem}.mp3.partial", audio_dir / f"{stem}.wav", audio_path):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def tail_text(value: str, limit: int = 5000) -> str:
    text = value or ""
    return text[-limit:] if len(text) > limit else text


def write_ffmpeg_log(
    *,
    log_path: Path,
    attempts: list[dict[str, Any]],
    wav_path: Path,
    mp3_path: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"created_at={now_utc()}",
        f"wav_path={wav_path}",
        f"wav_exists={wav_path.is_file()}",
        f"wav_size_bytes={wav_path.stat().st_size if wav_path.is_file() else 0}",
        f"mp3_path={mp3_path}",
        "",
    ]
    for attempt in attempts:
        lines.extend(
            [
                f"--- attempt={attempt.get('label')} ---",
                "command=" + json.dumps(attempt.get("command") or [], ensure_ascii=False),
                f"returncode={attempt.get('returncode')}",
                "--- stdout ---",
                str(attempt.get("stdout") or ""),
                "--- stderr ---",
                str(attempt.get("stderr") or ""),
                "",
            ]
        )
    log_path.write_text("\n".join(lines), encoding="utf-8")


def render_item(
    item: dict[str, Any],
    *,
    index: int,
    total: int,
    pipeline: Any,
    np: Any,
    sf: Any,
    device: str,
) -> tuple[str, str, str]:
    text_path = Path(str(item["text_path"]))
    audio_path = Path(str(item["audio_path"]))
    audio_dir = audio_path.parent
    job_stem = audio_path.stem
    partial_path = audio_dir / f"{job_stem}.partial.mp3"
    wav_tmp = audio_dir / f"{job_stem}.wav"
    ffmpeg_log_path = LOGS_DIR / f"ffmpeg_{job_stem}.log"

    if mp3_valid(audio_path) and not FORCE:
        write_status(
            state="running",
            stage="skipped_existing",
            current_item=str(item["canonical_basename"]),
            current_chunk=0,
            total_chunks=0,
            chunk_progress_percent=100.0,
            device=device,
        )
        return item["audio_name"], "skipped_existing", "mp3 already exists"

    if not text_path.is_file():
        raise FileNotFoundError(f"text missing: {text_path}")

    log_stdout(
        f"[youtube-tts] item_start item={item['canonical_basename']} voice={item['kokoro_voice']} speed={item['speed']}",
        event="item_start",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        voice=item["kokoro_voice"],
        speed=item["speed"],
        device=device,
    )
    cleanup_partial(audio_path)
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"text is empty: {text_path}")

    chunks_before = pack_paragraph_chunks(text, CHUNK_MAX_CHARS)
    chunks, chunk_stats = normalize_tts_chunks(chunks_before)
    log_stdout(
        "[youtube-tts] "
        f"chunks_before_normalize={chunk_stats['chunks_before_normalize']} "
        f"chunks_after_normalize={chunk_stats['chunks_after_normalize']} "
        f"tiny_chunks_merged={chunk_stats['tiny_chunks_merged']} "
        f"empty_chunks_skipped={chunk_stats['empty_chunks_skipped']} "
        f"junk_chunks_skipped={chunk_stats['junk_chunks_skipped']}",
        event="chunks_normalized",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        **chunk_stats,
        device=device,
    )
    if chunk_stats["junk_chunks_skipped"]:
        log_stdout(
            f"[youtube-tts] warning junk_chunks_skipped={chunk_stats['junk_chunks_skipped']} item={item['canonical_basename']}",
            event="junk_chunks_skipped",
            item=item["canonical_basename"],
            audio_name=item["audio_name"],
            junk_chunks_skipped=chunk_stats["junk_chunks_skipped"],
            device=device,
        )
    if not chunks:
        raise ValueError(f"no chunks after split: {text_path}")

    log_stdout(
        f"[youtube-tts] render_start item={item['canonical_basename']} chunks={len(chunks)}",
        event="render_start",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        chunks=len(chunks),
        device=device,
    )
    print(
        f"[{index}/{total}] {item['canonical_basename']} voice={item['kokoro_voice']} "
        f"speed={item['speed']} chunks={len(chunks)}",
        flush=True,
    )
    append_jsonl(LOG_JSONL, {"event": "item_started", "item": item, "chunks": len(chunks)})
    write_status(
        state="running",
        stage="synthesis",
        current_item=str(item["canonical_basename"]),
        current_chunk=0,
        total_chunks=len(chunks),
        chunk_progress_percent=0.0,
        device=device,
    )

    merged_parts: list[Any] = []
    chunk_total = len(chunks)
    for chunk_index, chunk in enumerate(chunks, start=1):
        chunk_percent_before = round(((chunk_index - 1) / chunk_total) * 100, 2)
        print(
            f"[youtube-tts] chunk {chunk_index}/{chunk_total} start "
            f"chars={len(chunk)} item={item['canonical_basename']}",
            flush=True,
        )
        write_status(
            state="running",
            stage="synthesis",
            current_item=str(item["canonical_basename"]),
            current_chunk=chunk_index,
            total_chunks=chunk_total,
            chunk_progress_percent=chunk_percent_before,
            device=device,
        )
        local_parts: list[Any] = []
        for attempt in range(2):
            local_parts = []
            for _gs, _ps, audio in pipeline(
                chunk,
                voice=str(item["kokoro_voice"]),
                speed=float(item["speed"]),
                split_pattern=r"\n+",
            ):
                arr = np.asarray(audio, dtype=np.float32).reshape(-1)
                if arr.size:
                    local_parts.append(arr)
            if local_parts:
                break
            log_stdout(
                f"[youtube-tts] empty_audio_chunk chunk={chunk_index}/{chunk_total} "
                f"chars={len(chunk)} attempt={attempt + 1} item={item['canonical_basename']}",
                event="empty_audio_chunk",
                item=item["canonical_basename"],
                audio_name=item["audio_name"],
                chunk_index=chunk_index,
                total_chunks=chunk_total,
                chars=len(chunk),
                attempt=attempt + 1,
                device=device,
            )
            if len(chunk.strip()) < TINY_CHUNK_MIN_CHARS:
                local_parts = [silence_chunk(np, int(item["sample_rate"]))]
                log_stdout(
                    f"[youtube-tts] empty_audio_skipped_or_silence chunk={chunk_index}/{chunk_total} "
                    f"chars={len(chunk)} action=silence item={item['canonical_basename']}",
                    event="empty_audio_skipped_or_silence",
                    item=item["canonical_basename"],
                    audio_name=item["audio_name"],
                    chunk_index=chunk_index,
                    total_chunks=chunk_total,
                    chars=len(chunk),
                    action="silence",
                    device=device,
                )
                break
            if attempt == 0:
                log_stdout(
                    f"[youtube-tts] empty_audio_chunk chunk={chunk_index}/{chunk_total} "
                    f"chars={len(chunk)} action=retry item={item['canonical_basename']}",
                    event="empty_audio_retry",
                    item=item["canonical_basename"],
                    audio_name=item["audio_name"],
                    chunk_index=chunk_index,
                    total_chunks=chunk_total,
                    chars=len(chunk),
                    device=device,
                )
        if not local_parts:
            raise RuntimeError(f"kokoro returned empty audio for chunk {chunk_index}/{len(chunks)}")
        merged_parts.append(np.concatenate(local_parts) if len(local_parts) > 1 else local_parts[0])
        chunk_percent_after = round((chunk_index / chunk_total) * 100, 2)
        print(
            f"[youtube-tts] chunk {chunk_index}/{chunk_total} done progress={chunk_percent_after}%",
            flush=True,
        )
        write_status(
            state="running",
            stage="synthesis",
            current_item=str(item["canonical_basename"]),
            current_chunk=chunk_index,
            total_chunks=chunk_total,
            chunk_progress_percent=chunk_percent_after,
            device=device,
        )

    if not merged_parts:
        raise RuntimeError("no audio generated")

    merged = np.concatenate(merged_parts) if len(merged_parts) > 1 else merged_parts[0]
    write_status(
        state="running",
        stage="write_wav",
        current_item=str(item["canonical_basename"]),
        current_chunk=chunk_total,
        total_chunks=chunk_total,
        chunk_progress_percent=100.0,
        device=device,
    )
    sf.write(str(wav_tmp), merged, int(item["sample_rate"]))
    ffmpeg_command = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_tmp),
        "-f",
        "mp3",
        "-codec:a",
        "libmp3lame",
        "-qscale:a",
        "2",
        str(partial_path),
    ]
    log_stdout(
        f"[youtube-tts] ffmpeg_start wav_path={wav_tmp} partial_mp3_path={partial_path} ffmpeg_log_path={ffmpeg_log_path}",
        event="ffmpeg_start",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        wav_path=str(wav_tmp),
        partial_mp3_path=str(partial_path),
        ffmpeg_log_path=str(ffmpeg_log_path),
        device=device,
    )
    proc = subprocess.run(ffmpeg_command, capture_output=True, text=True)
    write_ffmpeg_log(
        log_path=ffmpeg_log_path,
        attempts=[
            {
                "label": "legacy_libmp3lame_q2",
                "command": ffmpeg_command,
                "returncode": proc.returncode,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
            }
        ],
        wav_path=wav_tmp,
        mp3_path=audio_path,
    )
    wav_size_bytes = wav_tmp.stat().st_size if wav_tmp.is_file() else 0
    if proc.returncode != 0:
        combined_output = (proc.stderr or "") + "\n" + (proc.stdout or "")
        write_status(
            state="failed",
            stage="ffmpeg_failed",
            current_item=str(item["canonical_basename"]),
            current_chunk=chunk_total,
            total_chunks=chunk_total,
            chunk_progress_percent=100.0,
            device=device,
            wav_path=str(wav_tmp),
            wav_size_bytes=wav_size_bytes,
            partial_mp3_path=str(partial_path),
            partial_mp3_size_bytes=partial_path.stat().st_size if partial_path.is_file() else 0,
            mp3_path=str(audio_path),
            ffmpeg_log_path=str(ffmpeg_log_path),
            error_tail=tail_text(combined_output, 5000),
        )
        raise RuntimeError(
            "ffmpeg failed: "
            f"wav_path={wav_tmp} partial_mp3_path={partial_path} ffmpeg_log_path={ffmpeg_log_path}\n"
            f"{tail_text(combined_output, 5000)}"
        )
    if not mp3_valid(partial_path):
        write_status(
            state="failed",
            stage="ffmpeg_invalid_partial",
            current_item=str(item["canonical_basename"]),
            current_chunk=chunk_total,
            total_chunks=chunk_total,
            chunk_progress_percent=100.0,
            device=device,
            wav_path=str(wav_tmp),
            wav_size_bytes=wav_size_bytes,
            partial_mp3_path=str(partial_path),
            partial_mp3_size_bytes=partial_path.stat().st_size if partial_path.is_file() else 0,
            mp3_path=str(audio_path),
            ffmpeg_log_path=str(ffmpeg_log_path),
        )
        raise RuntimeError(
            "partial mp3 missing or too small after ffmpeg: "
            f"wav_path={wav_tmp} partial_mp3_path={partial_path} ffmpeg_log_path={ffmpeg_log_path}"
        )
    if audio_path.is_file():
        try:
            audio_path.unlink()
        except OSError:
            pass
    partial_path.rename(audio_path)
    try:
        wav_tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
    except OSError:
        pass
    log_stdout(
        f"[youtube-tts] render_done item={item['canonical_basename']}",
        event="render_done",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        path=str(audio_path),
        device=device,
    )
    if not mp3_valid(audio_path):
        raise RuntimeError(f"final mp3 missing or too small after legacy render: {audio_path}")
    log_stdout(
        f"[youtube-tts] mp3_validated path={audio_path} size={audio_path.stat().st_size}",
        event="mp3_validated",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        path=str(audio_path),
        size_bytes=audio_path.stat().st_size,
        device=device,
    )
    write_status(
        state="running",
        stage="item_done",
        current_item=str(item["canonical_basename"]),
        current_chunk=chunk_total,
        total_chunks=chunk_total,
        chunk_progress_percent=100.0,
        device=device,
        wav_path=str(wav_tmp),
        wav_size_bytes=wav_size_bytes,
        mp3_path=str(audio_path),
        ffmpeg_log_path=str(ffmpeg_log_path),
    )
    log_stdout(
        f"[youtube-tts] item_done item={item['canonical_basename']}",
        event="item_done",
        item=item["canonical_basename"],
        audio_name=item["audio_name"],
        path=str(audio_path),
        device=device,
    )
    return item["audio_name"], "done", f"written {audio_path.name}"


def main(argv: list[str] | None = None) -> int:
    init_worker(argv)
    configure_colab_logging()
    try:
        from google.colab import drive  # type: ignore

        drive.mount("/content/drive")
    except Exception:
        print("[INFO] google.colab drive mount skipped/unavailable; assuming Drive is already mounted.", flush=True)

    ensure_dirs()
    if DONE_TXT.is_file():
        DONE_TXT.unlink()

    device = detect_device()
    all_items = load_job_items()
    items = all_items if WORKER_MODE == "launch" else partition_items_for_worker(all_items, worker_index=WORKER_INDEX, worker_count=WORKER_COUNT)
    expected_files = [str(item["audio_name"]) for item in all_items]
    expected_count = read_expected_count(expected_files)
    if expected_count <= 0:
        expected_count = len(all_items)
    if not items:
        write_status(
            state="finished",
            stage="no_partition_items",
            total_expected=expected_count,
            completed=0,
            failed=0,
            skipped_existing=0,
            current_item="",
            current_chunk=0,
            total_chunks=0,
            chunk_progress_percent=0.0,
            device=device,
            worker_index=WORKER_INDEX,
            worker_count=WORKER_COUNT,
            total_loaded=len(all_items),
            errors=[],
        )
        print(
            f"[DONE] no items assigned to worker_index={WORKER_INDEX} worker_count={WORKER_COUNT} "
            f"loaded={len(all_items)} job={JOB_JSON}",
            flush=True,
        )
        return 0

    write_status(
        state="starting",
        stage="startup",
        total_expected=expected_count,
        completed=0,
        failed=0,
        skipped_existing=0,
        current_item="",
        current_chunk=0,
        total_chunks=0,
        chunk_progress_percent=0.0,
        device=device,
        worker_index=WORKER_INDEX,
        worker_count=WORKER_COUNT,
        worker_items=len(items),
        total_loaded=len(all_items),
        errors=[],
    )

    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
        from kokoro import KPipeline  # type: ignore
    except Exception as exc:
        traceback.print_exc()
        append_jsonl(
            LOG_JSONL,
            {
                "event": "failed",
                "stage": "dependency_import",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            },
        )
        write_status(
            state="failed",
            stage="dependency_import",
            total_expected=expected_count,
            completed=0,
            failed=0,
            skipped_existing=0,
            current_item="",
            current_chunk=0,
            total_chunks=0,
            chunk_progress_percent=0.0,
            device=device,
            worker_index=WORKER_INDEX,
            worker_count=WORKER_COUNT,
            worker_items=len(items),
            total_loaded=len(all_items),
            errors=[str(exc)],
        )
        raise RuntimeError("Install dependencies in Colab: `pip install kokoro soundfile numpy`; ffmpeg is also required.") from exc

    completed = 0
    failed = 0
    skipped_existing = 0
    errors: list[str] = []
    pipeline_cache: dict[str, Any] = {}

    for index, item in enumerate(items, start=1):
        current = str(item["canonical_basename"])
        write_status(
            state="running",
            stage="item_start",
            total_expected=expected_count,
            completed=completed,
            failed=failed,
            skipped_existing=skipped_existing,
            current_item=current,
            current_chunk=0,
            total_chunks=0,
            chunk_progress_percent=0.0,
            device=device,
            errors=errors[-10:],
        )
        try:
            lang = str(item["lang_code"])
            if lang not in pipeline_cache:
                pipeline_cache[lang] = make_pipeline(KPipeline, lang_code=lang, device=device)
            render_started = time.time()
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(
                    render_item,
                    item,
                    index=index,
                    total=len(items),
                    pipeline=pipeline_cache[lang],
                    np=np,
                    sf=sf,
                    device=device,
                )
                last_heartbeat = 0
                while not fut.done():
                    elapsed = int(time.time() - render_started)
                    if elapsed - last_heartbeat >= 30:
                        last_heartbeat = elapsed
                        log_stdout(
                            f"[youtube-tts] render_running item={current} elapsed={elapsed}s",
                            event="render_running",
                            item=current,
                            elapsed_sec=elapsed,
                            device=device,
                        )
                        write_status(
                            state="running",
                            stage="render_running",
                            total_expected=expected_count,
                            completed=completed,
                            failed=failed,
                            skipped_existing=skipped_existing,
                            current_item=current,
                            current_chunk=0,
                            total_chunks=0,
                            chunk_progress_percent=0.0,
                            elapsed_sec=elapsed,
                            device=device,
                            errors=errors[-10:],
                        )
                    time.sleep(1)
                audio_name, status, message = fut.result()
            if status == "done":
                completed += 1
            elif status == "skipped_existing":
                skipped_existing += 1
            append_jsonl(LOG_JSONL, {"event": "item_finished", "audio_name": audio_name, "status": status, "message": message})
        except Exception as exc:
            traceback.print_exc()
            failed += 1
            error = f"{current}: {exc}"
            errors.append(error)
            current_status = read_status()
            if current_status.get("stage") == "ffmpeg_failed":
                current_status.update(
                    {
                        "state": "failed",
                        "completed": completed,
                        "failed": failed,
                        "skipped_existing": skipped_existing,
                        "current_item": current,
                        "device": device,
                        "errors": errors[-20:],
                        "traceback_tail": traceback_tail(exc, lines=12),
                    }
                )
                current_status.pop("updated_at", None)
                write_status(**current_status)
            else:
                write_status(
                    state="failed",
                    stage="item_failed",
                    total_expected=expected_count,
                    completed=completed,
                    failed=failed,
                    skipped_existing=skipped_existing,
                    current_item=current,
                    current_chunk=0,
                    total_chunks=0,
                    chunk_progress_percent=0.0,
                    device=device,
                    errors=errors[-20:],
                    traceback_tail=traceback_tail(exc, lines=12),
                )
            append_jsonl(
                FAILED_JSONL,
                {
                    "item": item,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback_tail": traceback_tail(exc),
                },
            )
            append_jsonl(
                LOG_JSONL,
                {
                    "event": "failed",
                    "stage": "item_failed",
                    "item": item,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    "traceback_tail": traceback_tail(exc, lines=12),
                },
            )

    state = "finished_with_errors" if failed else "finished"
    final_status = {
        "state": state,
        "stage": "finished",
        "mode": WORKER_MODE,
        "launch_id": LAUNCH_ID,
        "job_type": JOB_TYPE,
        "story_slug": STORY_SLUG,
        "total_expected": expected_count,
        "completed": completed,
        "failed": failed,
        "skipped_existing": skipped_existing,
        "current_item": "",
        "current_chunk": 0,
        "total_chunks": 0,
        "chunk_progress_percent": 100.0 if failed == 0 else 0.0,
        "device": device,
        "worker_index": WORKER_INDEX,
        "worker_count": WORKER_COUNT,
        "worker_items": len(items),
        "total_loaded": len(all_items),
        "errors": errors[-20:],
        "finished_at": now_utc(),
    }
    write_status(**final_status)
    DONE_TXT.write_text(json.dumps(final_status, ensure_ascii=False, indent=2), encoding="utf-8")

    if WORKER_MODE == "launch" and JOB_JSON.is_file():
        job_payload = json.loads(JOB_JSON.read_text(encoding="utf-8"))
        if failed == 0 and completed > 0 and items:
            audio_path = Path(str(items[0]["audio_path"]))
            if mp3_valid(audio_path):
                write_launch_done_marker(item=items[0], audio_path=audio_path, job=job_payload)
        elif failed > 0:
            write_launch_failed_marker(error="; ".join(errors[-5:]), item=items[0] if items else None)

    print(
        f"[DONE] mode={WORKER_MODE} completed={completed} skipped_existing={skipped_existing} failed={failed} "
        f"audio_dir={AUDIO_DIR}",
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
