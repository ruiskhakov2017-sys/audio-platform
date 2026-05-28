"""Google Drive Kokoro Colab runner (job-aware, wait-for-texts, fault-tolerant batch)."""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import traceback
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


# Set True if you want to regenerate existing mp3 files.
FORCE = False

ROOT_DIR = "/content/drive/MyDrive/ContentFactory_TTS"
TEXTS_DIR = f"{ROOT_DIR}/texts"
MP3_DIR = f"{ROOT_DIR}/mp3"
LOGS_DIR = f"{ROOT_DIR}/logs"
JOB_DIR = f"{ROOT_DIR}/job"
REPORT_CSV = f"{LOGS_DIR}/results_report.csv"
FAILED_LOG_JSONL = f"{LOGS_DIR}/KOKORO_FAILED_FILES.jsonl"
EXPECTED_COUNT_TXT = f"{JOB_DIR}/EXPECTED_COUNT.txt"
EXPECTED_FILES_TXT = f"{JOB_DIR}/EXPECTED_FILES.txt"
SKIP_FILES_TXT = f"{JOB_DIR}/SKIP_FILES.txt"
VOICES_JOB_JSON = f"{JOB_DIR}/kokoro_voices_job.json"
COLAB_STATUS_JSON = f"{JOB_DIR}/COLAB_STATUS.json"
COLAB_DONE_TXT = f"{JOB_DIR}/COLAB_DONE.txt"
QUEUE_ROOT = f"{ROOT_DIR}/queue/site_tts"
QUEUE_PENDING_DIR = f"{QUEUE_ROOT}/pending"
QUEUE_GLOBAL_PENDING_DIR = f"{QUEUE_ROOT}/global_pending"
QUEUE_ASSIGNED_DIR = f"{QUEUE_ROOT}/assigned"
QUEUE_LEASES_DIR = f"{QUEUE_ROOT}/leases"
QUEUE_PROCESSING_DIR = f"{QUEUE_ROOT}/processing"
QUEUE_DONE_DIR = f"{QUEUE_ROOT}/done"
QUEUE_FAILED_DIR = f"{QUEUE_ROOT}/failed"
QUEUE_STALE_DIR = f"{QUEUE_ROOT}/stale"
QUEUE_INVALID_DIR = f"{QUEUE_ROOT}/invalid"
QUEUE_LOCKS_DIR = f"{QUEUE_ROOT}/locks"
QUEUE_EVENTS_DIR = f"{QUEUE_ROOT}/events"
WORKERS_DIR = f"{ROOT_DIR}/workers"

# Fallback если нет kokoro_voices_job.json (старый job): по суффиксу __M/__F/__U в имени txt
VOICE_M = "am_adam"
VOICE_F = "af_heart"
VOICE_U = "af_heart"
SPEED = 1.0
CHUNK_MAX_CHARS = 480
TINY_CHUNK_MIN_CHARS = 25
MIN_MP3_BYTES = 256
MAX_WORKERS = 1
WAIT_FOR_TEXTS = True
WAIT_TEXTS_INTERVAL_SECONDS = 60
WAIT_TEXTS_MAX_MINUTES = 240
REQUIRE_STABLE_FILES = True
STABLE_CHECKS = 2

TERMINAL_STATUSES = frozenset({"done", "skipped_existing", "failed", "manual_skipped"})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _traceback_tail(exc: BaseException, *, lines: int = 8) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    parts = [ln for ln in tb.strip().splitlines() if ln.strip()]
    return "\n".join(parts[-lines:])


def _split_oversized_paragraph(para: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(para):
        end = min(start + max_chars, len(para))
        slice_ = para[start:end]
        if end < len(para):
            cut = slice_.rfind(". ")
            if cut > max_chars // 2:
                slice_ = slice_[: cut + 1]
                end = start + len(slice_)
        piece = slice_.strip()
        if piece:
            parts.append(piece)
        if end <= start:
            end = min(start + max_chars, len(para))
        start = end
    return parts


def _pack_paragraph_chunks(text: str, max_chars: int) -> list[str]:
    if max_chars < 200:
        max_chars = 200
    raw_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not raw_paras:
        t = text.strip()
        return _split_oversized_paragraph(t, max_chars) if t else []

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0

    for para in raw_paras:
        if len(para) > max_chars:
            flush()
            chunks.extend(_split_oversized_paragraph(para, max_chars))
            continue
        add_len = len(para) + (2 if buf else 0)
        if buf_len + add_len > max_chars:
            flush()
        buf.append(para)
        buf_len += add_len
    flush()
    return chunks


def _chunk_has_text(chunk: str) -> bool:
    return any(ch.isalnum() for ch in chunk)


def _normalize_tts_chunks(chunks: list[str], *, min_chars: int = TINY_CHUNK_MIN_CHARS) -> tuple[list[str], dict[str, int]]:
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
        if not _chunk_has_text(chunk):
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


def _silence_chunk(np: Any, sample_rate: int, *, duration_seconds: float = 0.15) -> Any:
    return np.zeros(max(1, int(sample_rate * duration_seconds)), dtype=np.float32)


def _read_expected() -> list[str]:
    p = Path(EXPECTED_FILES_TXT)
    if not p.is_file():
        return []
    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if name and name.lower().endswith(".mp3"):
            out.append(Path(name).name)
    return out


def _read_expected_count(expected_files: list[str]) -> int:
    p = Path(EXPECTED_COUNT_TXT)
    if p.is_file():
        try:
            return int(p.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            return len(expected_files)
    return len(expected_files)


def _load_skip_files() -> set[str]:
    p = Path(SKIP_FILES_TXT)
    if not p.is_file():
        return set()
    out: set[str] = set()
    for raw in p.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        out.add(Path(name).name)
    return out


def _append_failed_log(entry: dict[str, Any]) -> None:
    p = Path(FAILED_LOG_JSONL)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp": _now(), **entry}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_status(*, file_status: dict[str, str] | None = None, **data: object) -> None:
    p = Path(COLAB_STATUS_JSON)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"updated_at": _now(), **data}
    if file_status is not None:
        payload["file_status"] = dict(file_status)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_terminal(file_status: dict[str, str], expected_mp3: list[str]) -> dict[str, int]:
    expected_set = set(expected_mp3)
    counts = {"total": len(expected_set), "done": 0, "skipped_existing": 0, "manual_skipped": 0, "failed": 0, "remaining": 0}
    for name in expected_set:
        st = file_status.get(name, "")
        if st == "done":
            counts["done"] += 1
        elif st == "skipped_existing":
            counts["skipped_existing"] += 1
        elif st == "manual_skipped":
            counts["manual_skipped"] += 1
        elif st == "failed":
            counts["failed"] += 1
        else:
            counts["remaining"] += 1
    return counts


def _wait_for_expected_texts(texts_dir: Path, expected_mp3: list[str], expected_count: int) -> list[Path]:
    expected_txt = [Path(x).with_suffix(".txt").name for x in expected_mp3]
    expected_set = set(expected_txt)
    if not WAIT_FOR_TEXTS:
        return sorted([p for p in texts_dir.glob("*.txt") if p.is_file() and p.name in expected_set])
    deadline = time.time() + WAIT_TEXTS_MAX_MINUTES * 60
    stable_hits = 0
    prev_sizes: dict[str, int] = {}
    while True:
        files = {p.name: p for p in texts_dir.glob("*.txt") if p.is_file()}
        present = sorted(expected_set.intersection(files.keys()))
        zero_size = [n for n in present if files[n].stat().st_size <= 0]
        current_sizes = {n: files[n].stat().st_size for n in present}
        stable_now = (current_sizes == prev_sizes) if REQUIRE_STABLE_FILES else True
        stable_hits = stable_hits + 1 if stable_now else 0
        _write_status(
            state="waiting_texts",
            expected_count=expected_count,
            expected_files=len(expected_set),
            found_texts=len(present),
            zero_size_texts=len(zero_size),
            stable_hits=stable_hits,
            stable_required=STABLE_CHECKS,
        )
        print(
            f"[WAIT_TEXTS] expected={expected_count} found={len(present)} zero_size={len(zero_size)} "
            f"stable_hits={stable_hits}/{STABLE_CHECKS}",
            flush=True,
        )
        if len(present) == expected_count and not zero_size and stable_hits >= STABLE_CHECKS:
            return [files[n] for n in present]
        if time.time() >= deadline:
            raise RuntimeError("Timed out waiting for expected txt files")
        prev_sizes = current_sizes
        time.sleep(WAIT_TEXTS_INTERVAL_SECONDS)


def _pick_voice_from_stem(stem: str) -> str:
    if "__" in stem:
        suffix = stem.rsplit("__", 1)[-1].strip().upper()[:1]
        if suffix == "M":
            return VOICE_M
        if suffix == "F":
            return VOICE_F
        if suffix == "U":
            return VOICE_U
    return VOICE_U


def _lang_from_voice(voice: str) -> str:
    v = (voice or "").strip().lower()
    c = v[:1] if v else ""
    return c if c in "abefhijpz" else "a"


def _load_voices_job() -> tuple[dict[str, dict[str, Any]], float]:
    """Маппинг txt_name -> item из kokoro_voices_job.json (как при локальном site TTS)."""
    p = Path(VOICES_JOB_JSON)
    if not p.is_file():
        return {}, float(SPEED)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, float(SPEED)
    try:
        default_speed = float(data.get("default_speed", SPEED))
    except (TypeError, ValueError):
        default_speed = float(SPEED)
    out: dict[str, dict[str, Any]] = {}
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        name = it.get("txt_name")
        if isinstance(name, str) and name.strip():
            out[name.strip()] = it
    return out, default_speed


def _mp3_valid(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_MP3_BYTES
    except OSError:
        return False


def _cleanup_partial(mp3_dir: Path, stem: str) -> None:
    for pattern in (f"{stem}.partial.mp3", f"{stem}.mp3.partial", f"{stem}.wav", f"{stem}.mp3"):
        p = mp3_dir / pattern
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


def _tail_text(value: str, limit: int = 5000) -> str:
    text = value or ""
    return text[-limit:] if len(text) > limit else text


def _write_ffmpeg_log(
    *,
    log_path: Path,
    command: list[str],
    returncode: int,
    stdout: str,
    stderr: str,
    wav_path: Path,
    partial_mp3_path: Path,
    final_mp3_path: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"created_at={_now()}",
        "command=" + json.dumps(command, ensure_ascii=False),
        f"returncode={returncode}",
        f"wav_path={wav_path}",
        f"wav_exists={wav_path.is_file()}",
        f"wav_size_bytes={wav_path.stat().st_size if wav_path.is_file() else 0}",
        f"partial_mp3_path={partial_mp3_path}",
        f"partial_mp3_exists={partial_mp3_path.is_file()}",
        f"partial_mp3_size_bytes={partial_mp3_path.stat().st_size if partial_mp3_path.is_file() else 0}",
        f"final_mp3_path={final_mp3_path}",
        "--- stdout ---",
        stdout or "",
        "--- stderr ---",
        stderr or "",
    ]
    log_path.write_text("\n".join(lines), encoding="utf-8")


def _resolve_voice_lang_speed(
    txt: Path,
    voices_map: dict[str, dict[str, Any]],
    default_job_speed: float,
) -> tuple[str, str, float]:
    item = voices_map.get(txt.name)
    raw_v = item.get("kokoro_voice") if item else None
    if isinstance(raw_v, str) and raw_v.strip():
        voice = raw_v.strip()
    else:
        voice = _pick_voice_from_stem(txt.stem)
    raw_lc = item.get("lang_code") if item else None
    if isinstance(raw_lc, str) and raw_lc.strip():
        lang = raw_lc.strip().lower()[:1]
        if lang not in "abefhijpz":
            lang = _lang_from_voice(voice)
    else:
        lang = _lang_from_voice(voice)
    speed = default_job_speed
    if item and "speed" in item and item["speed"] is not None:
        try:
            speed = float(item["speed"])
        except (TypeError, ValueError):
            speed = default_job_speed
    return voice, lang, speed


def _log_failed(
    *,
    txt_name: str,
    mp3_name: str,
    voice: str,
    lang_code: str,
    chunk_index: int | None,
    chunk_total: int | None,
    exc: BaseException,
) -> None:
    _append_failed_log(
        {
            "txt_name": txt_name,
            "mp3_name": mp3_name,
            "voice": voice,
            "lang_code": lang_code,
            "chunk_index": chunk_index,
            "chunk_total": chunk_total,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback_tail": _traceback_tail(exc),
        }
    )


def _render_txt_to_mp3_legacy(
    *,
    txt: Path,
    mp3_name: str,
    mp3_dir: Path,
    voices_map: dict[str, dict[str, Any]],
    default_job_speed: float,
    np: Any,
    sf: Any,
    KPipeline: Any,
    batch_idx: int,
    total: int,
    skip_files: set[str] | None = None,
    log_event: Any | None = None,
) -> tuple[str, str, str]:
    """The proven single-worker render path used for the 229+ successful Drive MP3s."""
    skip_files = skip_files or set()
    mp3_out = mp3_dir / mp3_name
    partial_out = mp3_dir / f"{txt.stem}.partial.mp3"
    ffmpeg_log_path = Path(LOGS_DIR) / f"ffmpeg_{txt.stem}.log"

    def log_warning(line: str, **payload: Any) -> None:
        print(line, flush=True)
        if log_event is not None:
            log_event(line, **payload)

    if txt.name in skip_files:
        print(f"[SKIP_MANUAL] {txt.name}", flush=True)
        return (txt.name, "manual_skipped", "listed in SKIP_FILES.txt")

    if _mp3_valid(mp3_out) and not FORCE:
        return (txt.name, "skipped_existing", "mp3 already exists")

    voice, lang, speed = _resolve_voice_lang_speed(txt, voices_map, default_job_speed)
    _cleanup_partial(mp3_dir, txt.stem)

    try:
        text = txt.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("empty txt")

        chunks_before = _pack_paragraph_chunks(text, CHUNK_MAX_CHARS)
        chunks, chunk_stats = _normalize_tts_chunks(chunks_before)
        log_warning(
            "[site-tts] "
            f"chunks_before_normalize={chunk_stats['chunks_before_normalize']} "
            f"chunks_after_normalize={chunk_stats['chunks_after_normalize']} "
            f"tiny_chunks_merged={chunk_stats['tiny_chunks_merged']} "
            f"empty_chunks_skipped={chunk_stats['empty_chunks_skipped']} "
            f"junk_chunks_skipped={chunk_stats['junk_chunks_skipped']}",
            event="chunks_normalized",
            txt_name=txt.name,
            mp3_name=mp3_name,
            **chunk_stats,
        )
        if chunk_stats["junk_chunks_skipped"]:
            log_warning(
                f"[site-tts] warning junk_chunks_skipped={chunk_stats['junk_chunks_skipped']}",
                event="junk_chunks_skipped",
                txt_name=txt.name,
                mp3_name=mp3_name,
                junk_chunks_skipped=chunk_stats["junk_chunks_skipped"],
            )
        if not chunks:
            raise ValueError("no chunks after split")

        chunk_total = len(chunks)
        print(f"[{batch_idx}/{total}] {txt.name}", flush=True)
        print(f"voice={voice}", flush=True)
        print(f"lang={lang}", flush=True)

        pipe = KPipeline(lang_code=lang)
        merged_parts: list[Any] = []

        for chunk_index, ch in enumerate(chunks, start=1):
            print(
                f"chunk {chunk_index}/{chunk_total} chars={len(ch)} voice={voice} lang={lang}",
                flush=True,
            )
            try:
                local_parts: list[Any] = []
                for attempt in range(2):
                    local_parts = []
                    for _gs, _ps, audio in pipe(ch, voice=voice, speed=float(speed), split_pattern=r"\n+"):
                        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
                        if arr.size:
                            local_parts.append(arr)
                    if local_parts:
                        break
                    log_warning(
                        f"[site-tts] empty_audio_chunk chunk={chunk_index}/{chunk_total} "
                        f"chars={len(ch)} attempt={attempt + 1}",
                        event="empty_audio_chunk",
                        txt_name=txt.name,
                        mp3_name=mp3_name,
                        chunk_index=chunk_index,
                        total_chunks=chunk_total,
                        chars=len(ch),
                        attempt=attempt + 1,
                    )
                    if len(ch.strip()) < TINY_CHUNK_MIN_CHARS:
                        local_parts = [_silence_chunk(np, 24000)]
                        log_warning(
                            f"[site-tts] empty_audio_skipped_or_silence chunk={chunk_index}/{chunk_total} "
                            f"chars={len(ch)} action=silence",
                            event="empty_audio_skipped_or_silence",
                            txt_name=txt.name,
                            mp3_name=mp3_name,
                            chunk_index=chunk_index,
                            total_chunks=chunk_total,
                            chars=len(ch),
                            action="silence",
                        )
                        break
                    if attempt == 0:
                        log_warning(
                            f"[site-tts] empty_audio_chunk chunk={chunk_index}/{chunk_total} "
                            f"chars={len(ch)} action=retry",
                            event="empty_audio_retry",
                            txt_name=txt.name,
                            mp3_name=mp3_name,
                            chunk_index=chunk_index,
                            total_chunks=chunk_total,
                            chars=len(ch),
                        )
                if not local_parts:
                    raise RuntimeError(f"kokoro returned empty audio for chunk {chunk_index}/{chunk_total}")
                chunk_audio = np.concatenate(local_parts) if len(local_parts) > 1 else local_parts[0]
                merged_parts.append(chunk_audio)
            except Exception as chunk_exc:
                _log_failed(
                    txt_name=txt.name,
                    mp3_name=mp3_name,
                    voice=voice,
                    lang_code=lang,
                    chunk_index=chunk_index,
                    chunk_total=chunk_total,
                    exc=chunk_exc,
                )
                _cleanup_partial(mp3_dir, txt.stem)
                return (txt.name, "failed", str(chunk_exc))

        if not merged_parts:
            raise RuntimeError("no audio generated")

        merged = np.concatenate(merged_parts) if len(merged_parts) > 1 else merged_parts[0]
        wav_tmp = mp3_dir / f"{txt.stem}.wav"
        sf.write(str(wav_tmp), merged, 24000)

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
            str(partial_out),
        ]
        log_warning(
            f"[site-tts] ffmpeg_start wav_path={wav_tmp} partial_mp3_path={partial_out} ffmpeg_log_path={ffmpeg_log_path}",
            event="ffmpeg_start",
            txt_name=txt.name,
            mp3_name=mp3_name,
            wav_path=str(wav_tmp),
            partial_mp3_path=str(partial_out),
            ffmpeg_log_path=str(ffmpeg_log_path),
        )
        proc = subprocess.run(ffmpeg_command, capture_output=True, text=True)
        _write_ffmpeg_log(
            log_path=ffmpeg_log_path,
            command=ffmpeg_command,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            wav_path=wav_tmp,
            partial_mp3_path=partial_out,
            final_mp3_path=mp3_out,
        )
        if proc.returncode != 0:
            combined_output = (proc.stderr or "") + "\n" + (proc.stdout or "")
            raise RuntimeError(
                "ffmpeg failed: "
                f"wav_path={wav_tmp} partial_mp3_path={partial_out} ffmpeg_log_path={ffmpeg_log_path}\n"
                f"{_tail_text(combined_output, 5000)}"
            )
        if not _mp3_valid(partial_out):
            raise RuntimeError(
                "partial mp3 missing or too small after ffmpeg: "
                f"wav_path={wav_tmp} partial_mp3_path={partial_out} ffmpeg_log_path={ffmpeg_log_path}"
            )

        if mp3_out.is_file():
            try:
                mp3_out.unlink()
            except OSError:
                pass
        partial_out.rename(mp3_out)
        try:
            wav_tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        return (txt.name, "done", f"written {mp3_out.name}")

    except Exception as exc:
        _log_failed(
            txt_name=txt.name,
            mp3_name=mp3_name,
            voice=voice,
            lang_code=lang,
            chunk_index=None,
            chunk_total=None,
            exc=exc,
        )
        if "ffmpeg failed" not in str(exc) and "partial mp3 missing" not in str(exc):
            _cleanup_partial(mp3_dir, txt.stem)
        return (txt.name, "failed", str(exc))


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "") or "").strip() or str(default))
    except ValueError:
        return default


def _queue_now_ms() -> int:
    return int(time.time() * 1000)


def _queue_read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _queue_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _queue_append_event(payload: dict[str, Any], *, worker_log: Path | None = None) -> None:
    row = {"timestamp": _now(), **payload}
    events_path = Path(QUEUE_EVENTS_DIR) / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if worker_log is not None:
        worker_log.parent.mkdir(parents=True, exist_ok=True)
        with worker_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _queue_stdout(worker_log: Path | None, line: str, **event: Any) -> None:
    print(line, flush=True)
    payload = {"line": line, **event}
    payload.setdefault("message", line)
    if "event" not in payload:
        payload["event"] = "log"
    _queue_append_event(payload, worker_log=worker_log)


def _queue_configure_logging() -> None:
    for logger_name in ("phonemizer", "phonemizer.backend", "phonemizer.separator"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.propagate = False


def _queue_setup_dirs(worker_email: str) -> tuple[Path, Path, Path, Path]:
    for folder in (
        QUEUE_PENDING_DIR,
        QUEUE_GLOBAL_PENDING_DIR,
        QUEUE_ASSIGNED_DIR,
        QUEUE_LEASES_DIR,
        QUEUE_PROCESSING_DIR,
        QUEUE_DONE_DIR,
        QUEUE_FAILED_DIR,
        QUEUE_STALE_DIR,
        QUEUE_INVALID_DIR,
        QUEUE_LOCKS_DIR,
        QUEUE_EVENTS_DIR,
    ):
        Path(folder).mkdir(parents=True, exist_ok=True)
    worker_dir = Path(WORKERS_DIR) / worker_email
    logs_dir = worker_dir / "logs"
    tmp_dir = worker_dir / "tmp"
    logs_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    assigned_dir = Path(QUEUE_ASSIGNED_DIR) / worker_email
    for name in ("pending", "processing", "done", "failed"):
        (assigned_dir / name).mkdir(parents=True, exist_ok=True)
    return worker_dir, logs_dir, tmp_dir, worker_dir / "status.json"


def _queue_write_worker_status(
    status_path: Path,
    *,
    worker_email: str,
    state: str,
    current_job: str,
    completed: int,
    failed: int,
    message: str = "",
    **extra: Any,
) -> None:
    payload = {
        "worker_email": worker_email,
        "state": state,
        "current_job": current_job,
        "completed": completed,
        "failed": failed,
        "heartbeat_at": _now(),
        "heartbeat_epoch_ms": _queue_now_ms(),
        "message": message,
    }
    payload.update(extra)
    _queue_write_json(status_path, payload)


def _queue_mp3_valid(path: Path) -> bool:
    return _mp3_valid(path)


def _queue_done_marker(job_id: str) -> Path:
    return Path(QUEUE_DONE_DIR) / f"{job_id}.done.json"


def _queue_failed_marker(job_id: str) -> Path:
    return Path(QUEUE_FAILED_DIR) / f"{job_id}.failed.json"


def _queue_invalid_marker(job_id: str) -> Path:
    return Path(QUEUE_INVALID_DIR) / f"{job_id}.invalid.json"


def _queue_processing_marker(job_id: str) -> Path:
    return Path(QUEUE_PROCESSING_DIR) / f"{job_id}.processing.json"


def _queue_lock_dir(job_id: str) -> Path:
    return Path(QUEUE_LOCKS_DIR) / f"{job_id}.lock"


def _queue_lock_json(job_id: str) -> Path:
    return _queue_lock_dir(job_id) / "lock.json"


def _queue_claim_files(job_id: str) -> list[Path]:
    prefix = f"{job_id}__"
    return sorted(
        [p for p in Path(QUEUE_LEASES_DIR).glob("*.claim.json") if p.name.startswith(prefix)],
        key=lambda p: p.name,
    )


def _queue_filename_invalid(value: str) -> bool:
    return not value.strip() or any(ch in value for ch in ('/', '\\', '\x00'))


def _queue_filename_mojibake(value: str) -> bool:
    lowered = value.lower()
    return "Ã" in value or "�" in value or "ã" in lowered


def _queue_lock_state(job_id: str, stale_minutes: int) -> tuple[str, dict[str, Any]]:
    lock_dir = _queue_lock_dir(job_id)
    if not lock_dir.exists():
        return "missing", {}
    data = _queue_read_json(lock_dir / "lock.json")
    state = str(data.get("state", "") or "").strip().lower()
    if state in {"done", "released", "failed"} or data.get("released"):
        return "released", data
    try:
        heartbeat_ms = int(data.get("heartbeat_epoch_ms") or data.get("claimed_at_epoch_ms") or 0)
    except (TypeError, ValueError):
        heartbeat_ms = 0
    if heartbeat_ms and _queue_now_ms() - heartbeat_ms <= max(1, stale_minutes) * 60 * 1000:
        return "active", data
    return "stale", data


def _queue_acquire_lock(
    *,
    job: dict[str, Any],
    worker_email: str,
    claim_id: str,
    stale_minutes: int,
) -> tuple[Path | None, str]:
    job_id = str(job.get("job_id") or "").strip()
    lock_dir = _queue_lock_dir(job_id)
    state, _data = _queue_lock_state(job_id, stale_minutes)
    if state == "active":
        return None, "active_lease_exists"
    if state == "stale":
        return None, "active_lease_exists"
    if state == "released":
        try:
            lock_json = lock_dir / "lock.json"
            if lock_json.is_file():
                lock_json.unlink()
            lock_dir.rmdir()
        except OSError:
            return None, "path_error"
    payload = {
        "job_id": job_id,
        "claim_id": claim_id,
        "worker_email": worker_email,
        "state": "active",
        "claimed_at": _now(),
        "claimed_at_epoch_ms": _queue_now_ms(),
        "heartbeat_at": _now(),
        "heartbeat_epoch_ms": _queue_now_ms(),
    }
    try:
        lock_dir.mkdir(parents=False, exist_ok=False)
        _queue_write_json(lock_dir / "lock.json", payload)
    except FileExistsError:
        return None, "active_lease_exists"
    except OSError:
        return None, "path_error"
    return lock_dir, ""


def _queue_update_lock(lock_dir: Path | None, *, state: str, current_stage: str = "", output_path: str = "") -> None:
    if lock_dir is None:
        return
    data = _queue_read_json(lock_dir / "lock.json")
    data.update(
        {
            "state": state,
            "heartbeat_at": _now(),
            "heartbeat_epoch_ms": _queue_now_ms(),
            "current_stage": current_stage,
            "output_path": output_path,
        }
    )
    _queue_write_json(lock_dir / "lock.json", data)


def _queue_release_lock(lock_dir: Path | None, *, state: str) -> None:
    if lock_dir is None:
        return
    try:
        data = _queue_read_json(lock_dir / "lock.json")
        data.update({"state": state, "released": True, "released_at": _now()})
        _queue_write_json(lock_dir / "lock.json", data)
        (lock_dir / "lock.json").unlink(missing_ok=True)  # type: ignore[arg-type]
        lock_dir.rmdir()
    except OSError:
        pass


def _queue_has_active_lease(job_id: str, stale_minutes: int) -> bool:
    stale_ms = max(1, stale_minutes) * 60 * 1000
    now_ms = _queue_now_ms()
    for claim in _queue_claim_files(job_id):
        data = _queue_read_json(claim)
        state = str(data.get("state", "") or "").strip().lower()
        if state in {"done", "released", "lost"} or data.get("released"):
            continue
        try:
            heartbeat_ms = int(data.get("heartbeat_epoch_ms") or data.get("claimed_at_epoch_ms") or 0)
        except (TypeError, ValueError):
            heartbeat_ms = 0
        if heartbeat_ms and now_ms - heartbeat_ms <= stale_ms:
            return True
    return False


def _queue_job_reject_reason(job: dict[str, Any], job_path: Path, stale_minutes: int) -> str:
    if not job:
        return "invalid_job_json"
    job_id = str(job.get("job_id") or job_path.stem).strip()
    if not job_id:
        return "invalid_job_json"
    text_name = Path(str(job.get("text_name") or "")).name
    audio_name = Path(str(job.get("audio_name") or f"{job_id}.mp3")).name
    if _queue_filename_invalid(job_id) or _queue_filename_invalid(text_name) or _queue_filename_invalid(audio_name):
        return "invalid_filename"
    if _queue_filename_mojibake(job_id) or _queue_filename_mojibake(text_name) or _queue_filename_mojibake(audio_name):
        return "mojibake_filename"
    if _queue_invalid_marker(job_id).is_file():
        return "invalid_job_json"
    if _queue_done_marker(job_id).is_file():
        return "done_marker_exists"
    if _queue_mp3_valid(Path(MP3_DIR) / audio_name):
        return "final_mp3_exists"
    if not (Path(TEXTS_DIR) / text_name).is_file():
        return "missing_text"
    lock_state, _lock = _queue_lock_state(job_id, stale_minutes)
    if lock_state in {"active", "stale"}:
        return "active_lease_exists"
    if _queue_has_active_lease(job_id, stale_minutes):
        return "active_lease_exists"
    return ""


def _queue_claim_job(
    *,
    job: dict[str, Any],
    job_path: Path,
    worker_email: str,
    stale_minutes: int,
    worker_log: Path,
) -> tuple[Path | None, Path | None, str]:
    reject_reason = _queue_job_reject_reason(job, job_path, stale_minutes)
    job_id = str(job.get("job_id") or job_path.stem).strip()
    if reject_reason:
        return None, None, reject_reason

    claim_id = uuid.uuid4().hex
    claimed_at_ms = _queue_now_ms()
    safe_worker = worker_email.replace("/", "_").replace("\\", "_")
    lock_dir, lock_reject = _queue_acquire_lock(
        job=job,
        worker_email=worker_email,
        claim_id=claim_id,
        stale_minutes=stale_minutes,
    )
    if lock_dir is None:
        return None, None, lock_reject or "claim_error"
    claim_path = Path(QUEUE_LEASES_DIR) / f"{job_id}__{claimed_at_ms}__{safe_worker}__{claim_id}.claim.json"
    claim_payload = {
        "claim_id": claim_id,
        "job_id": job_id,
        "worker_email": worker_email,
        "state": "won",
        "claimed_at": _now(),
        "claimed_at_epoch_ms": claimed_at_ms,
        "heartbeat_at": _now(),
        "heartbeat_epoch_ms": claimed_at_ms,
        "lock_path": str(lock_dir),
    }
    _queue_write_json(claim_path, claim_payload)
    claim_payload["won_at"] = _now()
    _queue_write_json(claim_path, claim_payload)
    _queue_write_json(
        _queue_processing_marker(job_id),
        {
            "job_id": job_id,
            "worker_email": worker_email,
            "claim_id": claim_id,
            "started_at": _now(),
            "state": "processing",
        },
    )
    _queue_append_event({"event": "claim_won", "job_id": job_id, "claim_id": claim_id, "worker_email": worker_email}, worker_log=worker_log)
    return claim_path, lock_dir, ""


def _queue_update_claim(
    claim_path: Path,
    *,
    state: str,
    current_stage: str = "",
    current_chunk: int | None = None,
    total_chunks: int | None = None,
    chunk_progress_percent: float | None = None,
    output_temp_path: str = "",
) -> None:
    data = _queue_read_json(claim_path)
    data.update(
        {
            "state": state,
            "heartbeat_at": _now(),
            "heartbeat_epoch_ms": _queue_now_ms(),
            "current_stage": current_stage,
            "output_temp_path": output_temp_path,
        }
    )
    if current_chunk is not None:
        data["current_chunk"] = current_chunk
    if total_chunks is not None:
        data["total_chunks"] = total_chunks
    if chunk_progress_percent is not None:
        data["chunk_progress_percent"] = chunk_progress_percent
    _queue_write_json(claim_path, data)


def _queue_assigned_dirs(worker_email: str) -> tuple[Path, Path, Path, Path]:
    base = Path(QUEUE_ASSIGNED_DIR) / worker_email
    return base / "pending", base / "processing", base / "done", base / "failed"


def _queue_assignment_reject_reason(job: dict[str, Any], job_path: Path) -> str:
    if not job:
        return "invalid_job_json"
    job_id = str(job.get("job_id") or job_path.stem).strip()
    text_name = Path(str(job.get("text_name") or "")).name
    audio_name = Path(str(job.get("audio_name") or f"{job_id}.mp3")).name
    if not job_id:
        return "invalid_job_json"
    if _queue_filename_invalid(job_id) or _queue_filename_invalid(text_name) or _queue_filename_invalid(audio_name):
        return "invalid_filename"
    if _queue_invalid_marker(job_id).is_file():
        return "invalid_job_json"
    if _queue_done_marker(job_id).is_file():
        return "done_marker_exists"
    if _queue_mp3_valid(Path(MP3_DIR) / audio_name):
        return "final_mp3_exists"
    if not (Path(TEXTS_DIR) / text_name).is_file():
        return "missing_text"
    return ""


def _queue_render_one(
    *,
    job: dict[str, Any],
    tmp_dir: Path,
    claim_path: Path,
    lock_dir: Path | None,
    pipeline_cache: dict[str, Any],
    np: Any,
    sf: Any,
    KPipeline: Any,
    worker_log: Path,
    status_path: Path,
    worker_email: str,
    completed: int,
    failed: int,
) -> Path:
    _ = tmp_dir, pipeline_cache
    job_id = str(job.get("job_id") or "").strip()
    text_name = Path(str(job.get("text_name") or "")).name
    audio_name = Path(str(job.get("audio_name") or "")).name
    text_path = Path(TEXTS_DIR) / text_name
    final_audio = Path(MP3_DIR) / audio_name
    if _queue_mp3_valid(final_audio):
        return final_audio
    if not text_path.is_file():
        raise FileNotFoundError(f"text missing: {text_path}")

    raw_voice = str(job.get("kokoro_voice") or "").strip()
    voice = raw_voice or _pick_voice_from_stem(Path(text_name).stem)
    lang = str(job.get("lang_code") or "").strip().lower()[:1] or _lang_from_voice(voice)
    if lang not in "abefhijpz":
        lang = _lang_from_voice(voice)
    try:
        speed = float(job.get("speed") if job.get("speed") is not None else SPEED)
    except (TypeError, ValueError):
        speed = float(SPEED)

    _queue_stdout(
        worker_log,
        f"[site-tts] job_start job={job_id} voice={voice} speed={speed}",
        event="job_start",
        job_id=job_id,
        voice=voice,
        speed=speed,
        lang=lang,
    )
    _queue_write_worker_status(
        status_path,
        worker_email=worker_email,
        state="running",
        current_job=job_id,
        completed=completed,
        failed=failed,
        stage="text_read",
        voice=voice,
        speed=speed,
    )
    _queue_stdout(
        worker_log,
        f"[site-tts] render_start job={job_id}",
        event="render_start",
        job_id=job_id,
    )
    _queue_update_claim(
        claim_path,
        state="processing",
        current_stage="render",
        output_temp_path=str(final_audio),
    )
    _queue_update_lock(lock_dir, state="active", current_stage="render", output_path=str(final_audio))
    render_started = time.time()
    voices_map = {
        text_name: {
            "kokoro_voice": voice,
            "lang_code": lang,
            "speed": speed,
        }
    }

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(
            _render_txt_to_mp3_legacy,
            txt=text_path,
            mp3_name=audio_name,
            mp3_dir=Path(MP3_DIR),
            voices_map=voices_map,
            default_job_speed=speed,
            np=np,
            sf=sf,
            KPipeline=KPipeline,
            batch_idx=1,
            total=1,
            skip_files=set(),
            log_event=lambda line, **payload: _queue_append_event({"line": line, "message": line, **payload}, worker_log=worker_log),
        )
        while not fut.done():
            elapsed = int(time.time() - render_started)
            if elapsed > 0 and elapsed % 30 == 0:
                _queue_update_claim(
                    claim_path,
                    state="processing",
                    current_stage="render",
                    output_temp_path=str(final_audio),
                )
                _queue_update_lock(lock_dir, state="active", current_stage="render", output_path=str(final_audio))
                _queue_write_worker_status(
                    status_path,
                    worker_email=worker_email,
                    state="running",
                    current_job=job_id,
                    completed=completed,
                    failed=failed,
                    stage="render",
                    elapsed_sec=elapsed,
                    voice=voice,
                    speed=speed,
                    mp3_path=str(final_audio),
                )
                _queue_stdout(
                    worker_log,
                    f"[site-tts] render_running job={job_id} elapsed={elapsed}s",
                    event="render_running",
                    job_id=job_id,
                    elapsed_sec=elapsed,
                )
            time.sleep(1)
        rendered_txt, status, message = fut.result()

    elapsed = time.time() - render_started
    _queue_stdout(
        worker_log,
        f"[site-tts] render_done job={job_id} status={status} elapsed={elapsed:.2f}s",
        event="render_done",
        job_id=job_id,
        text_name=rendered_txt,
        status=status,
        message=message,
        elapsed_sec=round(elapsed, 3),
    )
    if status not in {"done", "skipped_existing"}:
        raise RuntimeError(message)
    if not _queue_mp3_valid(final_audio):
        raise RuntimeError(f"final mp3 missing or too small after legacy render: {final_audio}")
    _queue_write_worker_status(
        status_path,
        worker_email=worker_email,
        state="running",
        current_job=job_id,
        completed=completed,
        failed=failed,
        stage="mp3_validated",
        voice=voice,
        speed=speed,
        mp3_path=str(final_audio),
        size_bytes=final_audio.stat().st_size,
    )
    _queue_stdout(
        worker_log,
        f"[site-tts] mp3_validated path={final_audio} size={final_audio.stat().st_size}",
        event="mp3_validated",
        job_id=job_id,
        path=str(final_audio),
        size_bytes=final_audio.stat().st_size,
    )
    return final_audio


def _queue_worker_main() -> int:
    from google.colab import drive  # type: ignore

    _queue_configure_logging()
    print("Mounting Google Drive...")
    drive.mount("/content/drive")

    worker_email = str(os.environ.get("CONTENT_FACTORY_WORKER_EMAIL") or "").strip()
    if not worker_email:
        raise RuntimeError("CONTENT_FACTORY_WORKER_EMAIL is required in queue mode")
    max_jobs = _env_int("CONTENT_FACTORY_MAX_JOBS_PER_RUN", 0)
    stale_minutes = _env_int("CONTENT_FACTORY_STALE_LEASE_MINUTES", 60)
    idle_exit_seconds = _env_int("CONTENT_FACTORY_IDLE_EXIT_SECONDS", 60)
    worker_dir, logs_dir, tmp_dir, status_path = _queue_setup_dirs(worker_email)
    worker_log = logs_dir / "worker_log.jsonl"
    completed = 0
    failed = 0
    attempted_jobs = 0
    idle_since: float | None = None

    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
        from kokoro import KPipeline  # type: ignore
    except Exception as exc:
        _queue_write_worker_status(
            status_path,
            worker_email=worker_email,
            state="error",
            current_job="",
            completed=completed,
            failed=failed,
            message=str(exc),
        )
        raise RuntimeError(
            "Install dependencies in Colab first: `pip install kokoro soundfile numpy` and ensure ffmpeg is available."
        ) from exc

    _queue_stdout(
        worker_log,
        f"[site-tts] worker_start email={worker_email}",
        event="worker_start",
        worker_email=worker_email,
        worker_dir=str(worker_dir),
    )
    pipeline_cache: dict[str, Any] = {}
    assigned_pending_dir, assigned_processing_dir, assigned_done_dir, assigned_failed_dir = _queue_assigned_dirs(worker_email)
    while True:
        if max_jobs > 0 and attempted_jobs >= max_jobs:
            break
        _queue_write_worker_status(
            status_path,
            worker_email=worker_email,
            state="scanning",
            current_job="",
            completed=completed,
            failed=failed,
        )
        pending_files = sorted(assigned_pending_dir.glob("*.json"), key=lambda p: p.name.lower())
        _queue_stdout(
            worker_log,
            f"[site-tts] queue_scan assigned_pending={len(pending_files)} email={worker_email}",
            event="queue_scan",
            worker_email=worker_email,
            pending=len(pending_files),
            assigned_pending=len(pending_files),
        )
        claimed: tuple[dict[str, Any], Path, Path] | None = None
        skipped_by_reason: dict[str, int] = {}
        first_10_reasons: list[dict[str, str]] = []
        for job_path in pending_files:
            job = _queue_read_json(job_path)
            job_id = str(job.get("job_id") or job_path.stem).strip()
            _queue_stdout(
                worker_log,
                f"[site-tts] assigned_job_check job={job_id} email={worker_email}",
                event="assigned_job_check",
                worker_email=worker_email,
                job_id=job_id,
                assignment_path=str(job_path),
            )
            reject_reason = _queue_assignment_reject_reason(job, job_path)
            if not reject_reason:
                processing_path = assigned_processing_dir / job_path.name
                assignment = dict(job)
                assignment.update(
                    {
                        "status": "assigned_processing",
                        "processing_worker": worker_email,
                        "processing_started_at": _now(),
                        "assignment_pending_path": str(job_path),
                    }
                )
                _queue_write_json(processing_path, assignment)
                try:
                    job_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
                _queue_stdout(
                    worker_log,
                    f"[site-tts] assigned_job_start job={job_id} email={worker_email}",
                    event="assigned_job_start",
                    worker_email=worker_email,
                    job_id=job_id,
                    assignment_path=str(processing_path),
                )
                claimed = (assignment, processing_path, job_path)
                break
            reason = reject_reason or "unknown_claim_reject_reason"
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
            if len(first_10_reasons) < 10:
                first_10_reasons.append({"job_id": job_id, "reason": reason})
            _queue_stdout(
                worker_log,
                f"[site-tts] assigned_job_skip job={job_id} reason={reason}",
                event=reason if reason in {
                    "final_mp3_exists",
                    "done_marker_exists",
                    "missing_text",
                    "invalid_job_json",
                    "claim_error",
                    "path_error",
                    "invalid_filename",
                    "mojibake_filename",
                    "unknown_claim_reject_reason",
                } else "unknown_claim_reject_reason",
                worker_email=worker_email,
                job_id=job_id,
                reason=reason,
                job_path=str(job_path),
            )

        if claimed is None:
            _queue_stdout(
                worker_log,
                f"[site-tts] no_claimable_assigned_jobs assigned_pending={len(pending_files)} skipped_by_reason={json.dumps(skipped_by_reason, ensure_ascii=False)}",
                event="no_claimable_jobs",
                worker_email=worker_email,
                pending_count=len(pending_files),
                assigned_pending=len(pending_files),
                skipped_by_reason=skipped_by_reason,
                first_10_reasons=first_10_reasons,
            )
            if idle_since is None:
                idle_since = time.time()
            _queue_write_worker_status(
                status_path,
                worker_email=worker_email,
                state="idle",
                current_job="",
                completed=completed,
                failed=failed,
                message="no claimable jobs",
            )
            if time.time() - idle_since >= idle_exit_seconds:
                break
            time.sleep(5)
            continue

        idle_since = None
        job, claim_path, original_pending_path = claimed
        job_id = str(job.get("job_id") or "").strip()
        attempted_jobs += 1
        try:
            _queue_write_worker_status(
                status_path,
                worker_email=worker_email,
                state="running",
                current_job=job_id,
                completed=completed,
                failed=failed,
                attempted_jobs=attempted_jobs,
                max_jobs=max_jobs,
            )
            final_audio = _queue_render_one(
                job=job,
                tmp_dir=tmp_dir,
                claim_path=claim_path,
                lock_dir=None,
                pipeline_cache=pipeline_cache,
                np=np,
                sf=sf,
                KPipeline=KPipeline,
                worker_log=worker_log,
                status_path=status_path,
                worker_email=worker_email,
                completed=completed,
                failed=failed,
            )
            done_payload = dict(job)
            done_payload.update(
                {
                    "status": "done",
                    "worker_email": worker_email,
                    "done_at": _now(),
                    "mp3_path": str(final_audio),
                    "size_bytes": final_audio.stat().st_size,
                    "claim_path": str(claim_path),
                }
            )
            _queue_write_json(_queue_done_marker(job_id), done_payload)
            _queue_update_claim(claim_path, state="done", current_stage="done", output_temp_path=str(final_audio))
            done_assignment = _queue_read_json(claim_path)
            done_assignment.update({"state": "done", "status": "assigned_done", "done_at": _now(), "mp3_path": str(final_audio)})
            _queue_write_json(assigned_done_dir / claim_path.name, done_assignment)
            try:
                claim_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass
            completed += 1
            _queue_stdout(
                worker_log,
                f"[site-tts] job_done job={job_id} path={final_audio} size={final_audio.stat().st_size}",
                event="job_done_log",
                worker_email=worker_email,
                job_id=job_id,
                path=str(final_audio),
                size_bytes=final_audio.stat().st_size,
            )
            _queue_append_event({"event": "job_done", "job_id": job_id, "worker_email": worker_email}, worker_log=worker_log)
        except Exception as exc:
            traceback.print_exc()
            failed += 1
            payload = dict(job)
            payload.update(
                {
                    "status": "failed",
                    "worker_email": worker_email,
                    "failed_at": _now(),
                    "claim_path": str(claim_path),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback_tail": _traceback_tail(exc),
                }
            )
            _queue_write_json(_queue_failed_marker(job_id), payload)
            _queue_update_claim(claim_path, state="failed", current_stage="failed")
            failed_assignment = _queue_read_json(claim_path)
            failed_assignment.update({"state": "failed", "status": "assigned_failed", "failed_at": _now(), "error_message": str(exc)})
            _queue_write_json(assigned_failed_dir / claim_path.name, failed_assignment)
            try:
                claim_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass
            _queue_write_worker_status(
                status_path,
                worker_email=worker_email,
                state="failed",
                current_job=job_id,
                completed=completed,
                failed=failed,
                attempted_jobs=attempted_jobs,
                max_jobs=max_jobs,
                stage="failed",
                error=str(exc),
                traceback_tail=_traceback_tail(exc),
            )
            _queue_stdout(
                worker_log,
                f"[site-tts] job_failed job={job_id} error={str(exc)[:500]}",
                event="job_failed",
                job_id=job_id,
                worker_email=worker_email,
                error=str(exc),
                traceback_tail=_traceback_tail(exc),
            )

    _queue_write_worker_status(
        status_path,
        worker_email=worker_email,
        state="finished",
        current_job="",
        completed=completed,
        failed=failed,
        attempted_jobs=attempted_jobs,
        max_jobs=max_jobs,
        message="queue worker finished",
    )
    _queue_stdout(
        worker_log,
        f"[site-tts] worker_stop email={worker_email} attempted={attempted_jobs} completed={completed} failed={failed}",
        event="worker_stop",
        worker_email=worker_email,
        attempted_jobs=attempted_jobs,
        completed=completed,
        failed=failed,
    )
    _queue_append_event(
        {
            "event": "worker_finished",
            "worker_email": worker_email,
            "attempted_jobs": attempted_jobs,
            "completed": completed,
            "failed": failed,
        },
        worker_log=worker_log,
    )
    print(f"[QUEUE_DONE] worker={worker_email} attempted={attempted_jobs} completed={completed} failed={failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    if _env_bool("CONTENT_FACTORY_QUEUE_MODE"):
        return _queue_worker_main()

    from google.colab import drive  # type: ignore

    print("Mounting Google Drive...")
    drive.mount("/content/drive")

    texts_dir = Path(TEXTS_DIR)
    mp3_dir = Path(MP3_DIR)
    logs_dir = Path(LOGS_DIR)
    report_csv = Path(REPORT_CSV)
    job_dir = Path(JOB_DIR)
    colab_done = Path(COLAB_DONE_TXT)
    texts_dir.mkdir(parents=True, exist_ok=True)
    mp3_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    if colab_done.is_file():
        try:
            colab_done.unlink()
        except OSError:
            pass

    expected_mp3 = _read_expected()
    expected_count = _read_expected_count(expected_mp3)
    if expected_count <= 0:
        _write_status(state="error", message="expected_count is empty/zero")
        raise RuntimeError(f"Invalid expected count in {EXPECTED_COUNT_TXT}")
    if not expected_mp3:
        _write_status(state="error", message="EXPECTED_FILES is empty")
        raise RuntimeError(f"EXPECTED_FILES is empty: {EXPECTED_FILES_TXT}")

    mp3_by_txt = {Path(m).with_suffix(".txt").name: m for m in expected_mp3}
    file_status: dict[str, str] = {m: "" for m in expected_mp3}
    skip_files = _load_skip_files()

    voices_map, default_job_speed = _load_voices_job()
    print(
        f"[VOICES] job_json={Path(VOICES_JOB_JSON).is_file()} mapped={len(voices_map)} default_speed={default_job_speed}",
        flush=True,
    )
    if skip_files:
        print(f"[SKIP] manual skip list: {len(skip_files)} file(s) from {SKIP_FILES_TXT}", flush=True)

    txt_files = _wait_for_expected_texts(texts_dir, expected_mp3, expected_count)
    if not txt_files:
        counts = _count_terminal(file_status, expected_mp3)
        _write_status(
            state="finished",
            file_status=file_status,
            completed_with_failed=False,
            failed_count=0,
            **counts,
            finished_at=_now(),
        )
        colab_done.write_text(
            json.dumps({"completed_with_failed": False, "failed_count": 0, "finished_at": _now()}, indent=2),
            encoding="utf-8",
        )
        with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "status", "message"])
        return 0

    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
        from kokoro import KPipeline  # type: ignore
    except Exception:
        raise RuntimeError(
            "Install dependencies in Colab first: `pip install kokoro soundfile numpy` "
            "and ensure ffmpeg is available."
        )

    rows: list[list[str]] = []
    started_at = _now()
    total = len(txt_files)
    counts = _count_terminal(file_status, expected_mp3)
    _write_status(
        state="running",
        started_at=started_at,
        expected_count=expected_count,
        total_txt=total,
        **counts,
        file_status=file_status,
    )

    def _render_one(txt: Path, batch_idx: int) -> tuple[str, str, str]:
        mp3_name = mp3_by_txt.get(txt.name, f"{txt.stem}.mp3")
        file_name, status, message = _render_txt_to_mp3_legacy(
            txt=txt,
            mp3_name=mp3_name,
            mp3_dir=mp3_dir,
            voices_map=voices_map,
            default_job_speed=default_job_speed,
            np=np,
            sf=sf,
            KPipeline=KPipeline,
            batch_idx=batch_idx,
            total=total,
            skip_files=skip_files,
        )
        file_status[mp3_name] = status
        return (file_name, status, message)

    max_workers = max(1, int(MAX_WORKERS))
    done_idx = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_render_one, txt, idx): txt.name for idx, txt in enumerate(txt_files, start=1)}
        for fut in as_completed(futures):
            done_idx += 1
            try:
                file_name, status, message = fut.result()
            except Exception as exc:
                txt_name = futures[fut]
                mp3_name = mp3_by_txt.get(txt_name, f"{Path(txt_name).stem}.mp3")
                _log_failed(
                    txt_name=txt_name,
                    mp3_name=mp3_name,
                    voice="",
                    lang_code="",
                    chunk_index=None,
                    chunk_total=None,
                    exc=exc,
                )
                file_name, status, message = txt_name, "failed", str(exc)
                file_status[mp3_name] = "failed"

            rows.append([file_name, status, message])
            counts = _count_terminal(file_status, expected_mp3)
            print(f"[{done_idx}/{total}] {file_name}: {status}", flush=True)
            _write_status(
                state="running",
                started_at=started_at,
                expected_count=expected_count,
                processed=done_idx,
                total_txt=total,
                **counts,
                file_status=file_status,
            )

    # Mark any expected mp3 without txt in this run as remaining (unchanged) or failed if txt missing
    for mp3_name in expected_mp3:
        if file_status.get(mp3_name):
            continue
        txt_name = Path(mp3_name).with_suffix(".txt").name
        if _mp3_valid(mp3_dir / mp3_name):
            file_status[mp3_name] = "skipped_existing"
        elif not (texts_dir / txt_name).is_file():
            file_status[mp3_name] = "failed"

    counts = _count_terminal(file_status, expected_mp3)
    failed_count = counts["failed"]
    completed_with_failed = failed_count > 0

    with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "message"])
        w.writerows(rows)

    _write_status(
        state="finished",
        started_at=started_at,
        completed_with_failed=completed_with_failed,
        failed_count=failed_count,
        finished_at=_now(),
        **counts,
        file_status=file_status,
    )
    colab_done.write_text(
        json.dumps(
            {
                "completed_with_failed": completed_with_failed,
                "failed_count": failed_count,
                "finished_at": _now(),
                **counts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"[DONE] total={counts['total']} done={counts['done']} skipped_existing={counts['skipped_existing']} "
        f"manual_skipped={counts['manual_skipped']} failed={counts['failed']} remaining={counts['remaining']}"
    )
    print(f"[DONE] completed_with_failed={completed_with_failed} failed_count={failed_count}")
    print(f"[DONE] mp3_dir={mp3_dir}")
    print(f"[DONE] report_csv={report_csv}")
    print(f"[DONE] failed_log={FAILED_LOG_JSONL}")
    print(f"[DONE] colab_done={colab_done}")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
