"""Assigned-queue Colab worker for Content-Factory YouTube video segments.

Compatible with existing notebooks that run:
%run "/content/drive/MyDrive/ContentFactory_YouTube/scripts/youtube_video_bootstrap_colab.py"

Required environment:
CONTENT_FACTORY_WORKER_EMAIL=<worker email configured in youtube_video_render.yaml>
Optional environment:
CONTENT_FACTORY_YOUTUBE_ROOT=/content/drive/MyDrive/ContentFactory_YouTube
CONTENT_FACTORY_YOUTUBE_FOLDER_ID=<Google Drive folder id for ContentFactory_YouTube>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import sys
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


FPS = 24
WIDTH = 1920
HEIGHT = 1080
UPSCALE_H = 2160
ZOOM_AMOUNT = 0.20
CRF = 24
PRESET = "medium"
GRAIN_STRENGTH = 15
MIN_VIDEO_SIZE_BYTES = 16 * 1024
DEFAULT_ROOT = Path("/content/drive/MyDrive/ContentFactory_YouTube")
DEFAULT_ROOT_NAME = "ContentFactory_YouTube"
LOG_TAIL_CHARS = 3000
DEFAULT_FFMPEG_PROGRESS_INTERVAL_SECONDS = 15
DEFAULT_FFMPEG_STALL_TIMEOUT_SECONDS = 600
DEFAULT_FFMPEG_MAX_STAGE_RUNTIME_MULTIPLIER = 20
DEFAULT_FFMPEG_MIN_STAGE_TIMEOUT_SECONDS = 1800
FFMPEG_OVERRUN_FAIL_FAST_SECONDS = 5.0
EFFECTS_DURATION_TOLERANCE_SECONDS = 1.0
EFFECTS_MAX_OVERRUN_SECONDS = 2.0
STAGE_TOTAL = 5


TIME_RE = re.compile(r"\btime=(?P<time>\d+:\d+:\d+(?:\.\d+)?)")
SPEED_RE = re.compile(r"\bspeed=\s*(?P<speed>[0-9.]+x)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "") or "").strip() or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "") or "").strip() or default)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def ffmpeg_preset() -> str:
    return str(os.environ.get("CONTENT_FACTORY_FFMPEG_PRESET", "") or PRESET).strip() or PRESET


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return ""
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    secs = value % 60
    return f"{hours:02d}:{minutes:02d}:{secs:04.1f}"


def parse_duration(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parts = text.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return float(text)
    except ValueError:
        return None


def mb(size_bytes: int | None) -> float:
    if not size_bytes:
        return 0.0
    return round(float(size_bytes) / (1024 * 1024), 2)


def safe_email(email: str) -> str:
    cleaned = []
    for char in email.strip():
        cleaned.append(char if char.isalnum() or char in "._-" else "_")
    return "".join(cleaned).strip("._-") or "worker"


def print_stage(stage: str, message: str, **fields: Any) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    suffix = f" {details}" if details else ""
    print(f"[{stage}] {message}{suffix}", flush=True)


def _mount_colab_drive_if_available() -> None:
    if Path("/content/drive").exists():
        return
    try:
        from google.colab import drive  # type: ignore
    except Exception:
        return
    drive.mount("/content/drive")


def _create_shortcut_from_folder_id(folder_id: str, shortcut_name: str = DEFAULT_ROOT_NAME) -> Path:
    folder_id = (folder_id or "").strip()
    if not folder_id:
        raise RuntimeError("CONTENT_FACTORY_YOUTUBE_FOLDER_ID is empty")
    _mount_colab_drive_if_available()
    try:
        from google.colab import auth  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.errors import HttpError  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Google Drive API is unavailable in this runtime; set CONTENT_FACTORY_YOUTUBE_ROOT "
            "to an already mounted ContentFactory_YouTube path."
        ) from exc
    try:
        auth.authenticate_user()
        service = build("drive", "v3")
        meta = service.files().get(fileId=folder_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise RuntimeError(f"CONTENT_FACTORY_YOUTUBE_FOLDER_ID does not point to a folder: {meta}")
        escaped_name = shortcut_name.replace("'", "\\'")
        query = f"'root' in parents and trashed=false and name='{escaped_name}'"
        existing = service.files().list(q=query, fields="files(id,name,mimeType,shortcutDetails)", supportsAllDrives=True).execute()
        files = existing.get("files") or []
        has_target_shortcut = any(
            item.get("mimeType") == "application/vnd.google-apps.shortcut"
            and (item.get("shortcutDetails") or {}).get("targetId") == folder_id
            for item in files
        )
        if not has_target_shortcut:
            service.files().create(
                body={
                    "name": shortcut_name,
                    "mimeType": "application/vnd.google-apps.shortcut",
                    "shortcutDetails": {"targetId": folder_id},
                    "parents": ["root"],
                },
                fields="id,name",
                supportsAllDrives=True,
            ).execute()
    except HttpError as exc:
        raise RuntimeError(
            "Cannot access ContentFactory_YouTube by CONTENT_FACTORY_YOUTUBE_FOLDER_ID. "
            "Give this Google account access to the ContentFactory_YouTube Drive folder."
        ) from exc
    return Path("/content/drive/MyDrive") / shortcut_name


def _candidate_roots() -> list[Path]:
    raw_root = os.environ.get("CONTENT_FACTORY_YOUTUBE_ROOT", "").strip()
    candidates = [
        Path(raw_root).expanduser() if raw_root else None,
        DEFAULT_ROOT,
        Path("/content/drive/MyDrive") / DEFAULT_ROOT_NAME,
        Path("/content/drive/Shareddrives") / DEFAULT_ROOT_NAME,
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        if item is None:
            continue
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def resolve_youtube_root(cli_root: str | Path | None = None) -> Path:
    _mount_colab_drive_if_available()
    explicit = os.environ.get("CONTENT_FACTORY_YOUTUBE_ROOT", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return path
        raise RuntimeError(f"CONTENT_FACTORY_YOUTUBE_ROOT is set but does not exist: {path}")

    cli_value = str(cli_root or "").strip()
    if cli_value:
        path = Path(cli_value).expanduser()
        if path.exists():
            return path

    folder_id = os.environ.get("CONTENT_FACTORY_YOUTUBE_FOLDER_ID", "").strip()
    if folder_id:
        shortcut = _create_shortcut_from_folder_id(folder_id)
        for _ in range(20):
            if shortcut.exists():
                os.environ["CONTENT_FACTORY_YOUTUBE_ROOT"] = str(shortcut)
                return shortcut
            time.sleep(1)
        raise RuntimeError(
            f"Created/verified shortcut for ContentFactory_YouTube, but mounted path is not visible yet: {shortcut}. "
            "Reconnect Google Drive in Colab and rerun the cell."
        )

    existing = [path for path in _candidate_roots() if path.exists()]
    if existing:
        os.environ["CONTENT_FACTORY_YOUTUBE_ROOT"] = str(existing[0])
        return existing[0]
    raise RuntimeError(
        "ContentFactory_YouTube root is not accessible in this Colab account. "
        "Set CONTENT_FACTORY_YOUTUBE_ROOT to a mounted folder, or set CONTENT_FACTORY_YOUTUBE_FOLDER_ID "
        "and share the ContentFactory_YouTube folder with this Google account."
    )


FALLBACK_LOG_PATH = Path("/content/tmp/content_factory_video_worker_fallback.log")
DRIVE_DISCONNECT_ERRNOS = {107, 5, 103, 104}  # ENOTCONN, EIO, ECONNABORTED, ECONNRESET


class InputAssetMissing(RuntimeError):
    """Permanent failure: required frame/asset отсутствует на Drive.

    НЕ должен возвращаться в pending через reclaim. Только operator может починить (положить файл).
    """

    def __init__(self, message: str, *, missing_frames: list[str] | None = None, segment_id: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.missing_frames = list(missing_frames or [])
        self.segment_id = segment_id
        self.details = dict(details or {})


class DriveUnavailable(RuntimeError):
    """Transient failure: Google Drive mount недоступен / отвалился.

    Sеgment должен остаться в processing, watcher вернёт его через stale heartbeat.
    """

    def __init__(self, message: str, *, errno: int | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.errno = errno
        self.details = dict(details or {})


def _safe_mkdir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def _safe_print(message: str) -> None:
    try:
        print(message, flush=True)
    except OSError:
        pass


class WorkerLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.fallback_path = FALLBACK_LOG_PATH
        _safe_mkdir(self.log_path.parent)
        _safe_mkdir(self.fallback_path.parent)

    def _write_line(self, line: str) -> None:
        wrote = False
        try:
            _safe_mkdir(self.log_path.parent)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            wrote = True
        except OSError:
            wrote = False
        if not wrote:
            try:
                _safe_mkdir(self.fallback_path.parent)
                with self.fallback_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"[fallback target={self.log_path}] {line}\n")
            except OSError:
                pass

    def log(self, stage: str, message: str, **fields: Any) -> None:
        try:
            details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        except Exception:
            details = ""
        suffix = f" {details}" if details else ""
        line = f"{utc_now()} [{stage}] {message}{suffix}"
        _safe_print(f"[{stage}] {message}{suffix}")
        self._write_line(line)

    def exception(self, stage: str, message: str, error: BaseException) -> None:
        try:
            tb = traceback.format_exc()
        except Exception:
            tb = f"<traceback formatting failed: {error!r}>"
        self.log(stage, message, error=repr(error))
        _safe_print(tb)
        self._write_line(tb)


def tail_text(text: str, limit: int = LOG_TAIL_CHARS) -> str:
    return text[-limit:] if len(text) > limit else text


def get_output_size_bytes(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def inject_ffmpeg_progress(cmd: list[str]) -> list[str]:
    if not cmd or Path(cmd[0]).name != "ffmpeg":
        return cmd
    return [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *cmd[1:]]


def ffmpeg_has_duration_cap(cmd: list[str]) -> bool:
    return any(token in {"-t", "-to", "-frames:v", "-frames"} for token in cmd)


def parse_ffmpeg_progress_line(line: str) -> tuple[float | None, str, str]:
    text = (line or "").strip()
    if not text:
        return None, "", ""
    if text.startswith("out_time_ms="):
        try:
            return int(text.split("=", 1)[1]) / 1_000_000.0, "", ""
        except ValueError:
            return None, "", ""
    if text.startswith("out_time="):
        raw = text.split("=", 1)[1].strip()
        return parse_duration(raw), raw, ""
    if text.startswith("speed="):
        return None, "", text.split("=", 1)[1].strip()
    match = TIME_RE.search(text)
    speed_match = SPEED_RE.search(text)
    parsed_time = parse_duration(match.group("time")) if match else None
    parsed_speed = speed_match.group("speed") if speed_match else ""
    return parsed_time, match.group("time") if match else "", parsed_speed


def read_stream_to_queue(stream: Any, out: "queue.Queue[str]") -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            out.put(line)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_cmd(
    cmd: list[str],
    log_path: Path,
    heartbeat: Callable[[], None] | None = None,
    logger: WorkerLogger | None = None,
    output_path: Path | None = None,
    segment_id: str = "",
    stage_name: str = "ffmpeg",
    stage_index: int = 0,
    stage_total: int = STAGE_TOTAL,
    input_path: Path | str | None = None,
    expected_duration: float | None = None,
    clip_index: int = 0,
    clip_total: int = 0,
) -> None:
    started_at = utc_now()
    stage_started = time.time()
    progress_interval = max(1, env_int("CONTENT_FACTORY_FFMPEG_PROGRESS_INTERVAL_SECONDS", DEFAULT_FFMPEG_PROGRESS_INTERVAL_SECONDS))
    stall_timeout = max(1, env_int("CONTENT_FACTORY_FFMPEG_STALL_TIMEOUT_SECONDS", DEFAULT_FFMPEG_STALL_TIMEOUT_SECONDS))
    runtime_multiplier = max(1.0, env_float("CONTENT_FACTORY_FFMPEG_MAX_STAGE_RUNTIME_MULTIPLIER", DEFAULT_FFMPEG_MAX_STAGE_RUNTIME_MULTIPLIER))
    min_stage_timeout = max(1, env_int("CONTENT_FACTORY_FFMPEG_MIN_STAGE_TIMEOUT_SECONDS", DEFAULT_FFMPEG_MIN_STAGE_TIMEOUT_SECONDS))
    max_stage_runtime = max(float(min_stage_timeout), float(expected_duration or 0) * runtime_multiplier)
    command_has_duration_cap = ffmpeg_has_duration_cap(cmd)
    if logger:
        logger.log(
            "STAGE",
            stage_name,
            segment_id=segment_id,
            stage_name=stage_name,
            stage_index=stage_index,
            stage_total=stage_total,
            clip_index=clip_index or None,
            clip_total=clip_total or None,
            input_path=input_path,
            output_path=output_path,
            expected_duration_seconds=round(float(expected_duration or 0), 3) if expected_duration else "",
            started_at=started_at,
            elapsed_seconds=0,
        )
        logger.log("RENDER", "ffmpeg command", segment_id=segment_id, stage_name=stage_name, command=" ".join(cmd), log_path=log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    progress_cmd = inject_ffmpeg_progress(cmd)
    proc = subprocess.Popen(progress_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines = ["COMMAND:\n" + " ".join(cmd) + "\n\nSTDERR_STDOUT:\n"]
    line_queue: "queue.Queue[str]" = queue.Queue()
    reader = threading.Thread(target=read_stream_to_queue, args=(proc.stdout, line_queue), daemon=True)
    reader.start()
    last_heartbeat = 0.0
    last_progress_log = 0.0
    last_change = time.time()
    last_output_size = get_output_size_bytes(output_path)
    ffmpeg_time_sec: float | None = None
    ffmpeg_time_raw = ""
    speed = ""
    last_line = ""
    overrun_warning_logged = False
    while True:
        try:
            line = line_queue.get(timeout=1.0)
            lines.append(line)
            last_line = line.strip()
            parsed_time, parsed_raw, parsed_speed = parse_ffmpeg_progress_line(line)
            if parsed_time is not None and parsed_time != ffmpeg_time_sec:
                ffmpeg_time_sec = parsed_time
                if parsed_raw:
                    ffmpeg_time_raw = parsed_raw
                last_change = time.time()
            if parsed_speed:
                speed = parsed_speed
            if parsed_raw:
                ffmpeg_time_raw = parsed_raw
        except queue.Empty:
            pass

        now = time.time()
        current_size = get_output_size_bytes(output_path)
        if current_size != last_output_size:
            last_output_size = current_size
            last_change = now
        if heartbeat and now - last_heartbeat >= 20:
            heartbeat()
            last_heartbeat = now
        elapsed = now - stage_started
        if logger and now - last_progress_log >= progress_interval:
            percent = None
            eta = None
            overrun_seconds = None
            if expected_duration and expected_duration > 0 and ffmpeg_time_sec is not None:
                percent = min(100.0, max(0.0, (ffmpeg_time_sec / expected_duration) * 100.0))
                if ffmpeg_time_sec > expected_duration:
                    overrun_seconds = ffmpeg_time_sec - expected_duration
                if percent > 0:
                    eta = max(0, int(elapsed * (100.0 - percent) / percent))
            logger.log(
                "PROGRESS",
                "ffmpeg running",
                segment_id=segment_id,
                stage_name=stage_name,
                elapsed=f"{int(elapsed)}s",
                ffmpeg_time=ffmpeg_time_raw or (format_duration(ffmpeg_time_sec) if ffmpeg_time_sec is not None else ""),
                expected=format_duration(expected_duration) if expected_duration else "",
                percent=f"{percent:.1f}" if percent is not None else "",
                speed=speed,
                eta=f"{eta}s" if eta is not None else "",
                overrun_seconds=round(overrun_seconds, 3) if overrun_seconds is not None else "",
                output_size_mb=mb(current_size),
                last_log_line_tail=tail_text(last_line, 240).replace("\n", "\\n"),
                running=True,
            )
            if overrun_seconds is not None and not overrun_warning_logged:
                logger.log(
                    "WARN",
                    "ffmpeg progress exceeded expected duration",
                    segment_id=segment_id,
                    stage_name=stage_name,
                    ffmpeg_time=ffmpeg_time_raw or format_duration(ffmpeg_time_sec),
                    expected=format_duration(expected_duration),
                    overrun_seconds=round(overrun_seconds, 3),
                    command_has_duration_cap=command_has_duration_cap,
                )
                overrun_warning_logged = True
            if stage_name == "effects":
                logger.log(
                    "SEGMENT PROGRESS",
                    "effects progress",
                    segment_id=segment_id,
                    stage_name="effects",
                    stage_index=stage_index,
                    stage_total=stage_total,
                    percent=f"{percent:.1f}" if percent is not None else "",
                )
            last_progress_log = now
        if expected_duration and ffmpeg_time_sec is not None and ffmpeg_time_sec > expected_duration + FFMPEG_OVERRUN_FAIL_FAST_SECONDS and not command_has_duration_cap:
            proc.kill()
            reason = (
                "duration_overrun_without_cap: "
                f"ffmpeg_time={format_duration(ffmpeg_time_sec)} expected={format_duration(expected_duration)} "
                f"overrun_seconds={round(ffmpeg_time_sec - expected_duration, 3)}"
            )
            log_path.write_text("".join(lines), encoding="utf-8")
            if logger:
                logger.log(
                    "STAGE FAILED",
                    stage_name,
                    segment_id=segment_id,
                    stage_name=stage_name,
                    elapsed=int(elapsed),
                    reason=reason,
                    output_size_mb=mb(current_size),
                )
            raise RuntimeError(reason)
        if now - last_change > stall_timeout:
            proc.kill()
            reason = f"stall_detected: no ffmpeg_time/output_size change for {int(now - last_change)}s"
            log_path.write_text("".join(lines), encoding="utf-8")
            if logger:
                logger.log(
                    "STAGE FAILED",
                    stage_name,
                    segment_id=segment_id,
                    stage_name=stage_name,
                    elapsed=int(elapsed),
                    reason=reason,
                    output_size_mb=mb(current_size),
                )
            raise RuntimeError(reason)
        if elapsed > max_stage_runtime:
            proc.kill()
            reason = f"timeout: elapsed {int(elapsed)}s > max_stage_runtime {int(max_stage_runtime)}s"
            log_path.write_text("".join(lines), encoding="utf-8")
            if logger:
                logger.log(
                    "STAGE FAILED",
                    stage_name,
                    segment_id=segment_id,
                    stage_name=stage_name,
                    elapsed=int(elapsed),
                    reason=reason,
                    output_size_mb=mb(current_size),
                )
            raise RuntimeError(reason)
        if proc.poll() is not None:
            while True:
                try:
                    lines.append(line_queue.get_nowait())
                except queue.Empty:
                    break
            break
    output = "".join(lines)
    log_path.write_text(output, encoding="utf-8")
    output_exists = output_path.is_file() if output_path else None
    output_size_bytes = output_path.stat().st_size if output_path and output_path.is_file() else None
    if logger:
        logger.log(
            "FFMPEG RESULT",
            "ffmpeg finished",
            segment_id=segment_id,
            stage_name=stage_name,
            return_code=proc.returncode,
            output_exists=output_exists,
            output_size=output_size_bytes,
            stdout_tail=tail_text(output).replace("\n", "\\n"),
        )
    if proc.returncode != 0:
        if logger:
            logger.log(
                "STAGE FAILED",
                stage_name,
                segment_id=segment_id,
                stage_name=stage_name,
                elapsed=int(time.time() - stage_started),
                reason=f"ffmpeg_return_code:{proc.returncode}",
                output_size_mb=mb(output_size_bytes),
            )
        raise RuntimeError(f"ffmpeg failed: {output[-5000:]}")
    if logger:
        logger.log(
            "STAGE DONE",
            stage_name,
            segment_id=segment_id,
            stage_name=stage_name,
            elapsed=int(time.time() - stage_started),
            output_size_mb=mb(output_size_bytes),
            ffprobe_ok=valid_video(output_path) if output_path else "",
        )


def media_duration(path: Path) -> float:
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
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr}")
    return float((proc.stdout or "0").strip())


def has_video_stream(path: Path) -> bool:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def valid_video(path: Path, expected_duration: float | None = None, tolerance: float = 2.5) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < MIN_VIDEO_SIZE_BYTES:
            return False
    except OSError:
        return False
    if not has_video_stream(path):
        return False
    if expected_duration is None:
        return True
    try:
        return abs(media_duration(path) - expected_duration) <= tolerance
    except RuntimeError:
        return False


def validate_effects_duration(path: Path, raw_duration: float, logger: WorkerLogger, segment_id: str) -> None:
    duration = media_duration(path)
    delta = duration - raw_duration
    ok = abs(delta) <= EFFECTS_DURATION_TOLERANCE_SECONDS and duration <= raw_duration + EFFECTS_MAX_OVERRUN_SECONDS
    logger.log(
        "FFMPEG RESULT",
        "effects duration validation",
        segment_id=segment_id,
        stage_name="effects",
        output_duration_seconds=round(duration, 3),
        raw_duration_seconds=round(raw_duration, 3),
        delta_seconds=round(delta, 3),
        tolerance_seconds=EFFECTS_DURATION_TOLERANCE_SECONDS,
        max_overrun_seconds=EFFECTS_MAX_OVERRUN_SECONDS,
        ffprobe_ok=ok,
    )
    if not ok:
        raise RuntimeError(
            "effects duration validation failed: "
            f"output={duration:.3f}s raw={raw_duration:.3f}s delta={delta:.3f}s"
        )


def ffprobe_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return {"exists": True, "ok": False, "error": proc.stderr[-1000:]}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {"raw": proc.stdout[-1000:]}
    return {"exists": True, "ok": True, "path": str(path), "size": path.stat().st_size, "ffprobe": data}


def count_json(path: Path) -> int:
    return len(list(path.glob("segment_*.json"))) if path.is_dir() else 0


def queue_counts(dirs: dict[str, Path]) -> dict[str, int]:
    return {
        "pending": count_json(dirs["pending"]),
        "processing": count_json(dirs["processing"]),
        "done": count_json(dirs["done"]),
        "failed": count_json(dirs["failed"]),
    }


def update_status(job_root: Path, worker_email: str, **payload: Any) -> None:
    now = utc_now()
    dirs = assigned_dirs(job_root, worker_email)
    counts = queue_counts(dirs)
    status_value = str(payload.pop("status", "idle"))
    current_segment = str(payload.pop("current_segment_id", payload.pop("current_job", "")) or "")
    processed_count = int(payload.pop("processed_count", payload.pop("rendered", 0)) or 0)
    failed_count = int(payload.pop("failed_count", 0) or 0)
    last_message = str(payload.pop("last_message", "") or "")
    last_error = str(payload.pop("last_error", payload.pop("error", "")) or "")
    status = {
        "worker_email": worker_email,
        "safe_worker_id": safe_email(worker_email),
        "safe_email": safe_email(worker_email),
        "status": status_value,
        "active_job_slug": job_root.name,
        "current_segment_id": current_segment,
        "current_job": current_segment,
        "last_heartbeat_at": now,
        "heartbeat_at": now,
        "updated_at": now,
        "assigned_pending_count": counts["pending"],
        "assigned_processing_count": counts["processing"],
        "assigned_done_count": counts["done"],
        "assigned_failed_count": counts["failed"],
        "processed_count": processed_count,
        "failed_count": failed_count,
        "last_message": last_message,
        "last_error": last_error,
        **payload,
    }
    write_json(job_root / "status" / "workers" / f"{safe_email(worker_email)}.json", status)


def update_processing_heartbeat(processing_job: Path, worker_email: str, current_job: str) -> None:
    payload = read_json(processing_job)
    payload.update(
        {
            "status": "processing",
            "assigned_worker": worker_email,
            "current_job": current_job,
            "heartbeat_at": utc_now(),
            "updated_at": utc_now(),
        }
    )
    write_json(processing_job, payload)


def find_active_job(root: Path, story_slug: str | None) -> Path:
    if story_slug:
        ready = root / "video_jobs" / story_slug / "VIDEO_JOB_READY.json"
        if not ready.is_file():
            raise RuntimeError(f"VIDEO_JOB_READY.json not found for story_slug={story_slug}: {ready}")
        return ready.parent
    ready_files = sorted((root / "video_jobs").glob("*/VIDEO_JOB_READY.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not ready_files:
        raise RuntimeError(f"No active VIDEO_JOB_READY.json found under {root / 'video_jobs'}")
    if len(ready_files) > 1:
        print(f"WARNING: multiple video jobs are ready; using newest: {ready_files[0].parent.name}", flush=True)
    return ready_files[0].parent


def assigned_dirs(job_root: Path, worker_email: str) -> dict[str, Path]:
    base = job_root / "queue" / "assigned" / worker_email
    return {
        "pending": base / "pending",
        "processing": base / "processing",
        "done": base / "done",
        "failed": base / "failed",
    }


def _parse_iso_safe(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _segment_output_mp4(job_root: Path, segment_id: str) -> Path:
    return job_root / "segments" / f"{segment_id}.mp4"


def self_reclaim_own_stale_processing(
    job_root: Path,
    worker_email: str,
    *,
    stale_minutes: int = 10,
    max_attempts: int = 3,
    logger: WorkerLogger | None = None,
) -> dict[str, Any]:
    """Возвращает свои stale-сегменты из processing обратно в global_pending.

    Логика:
    - валидный mp4 + done marker -> переносим json в own done;
    - heartbeat свежее stale_minutes -> не трогаем (worker могла быть перезапущена за это время);
    - иначе attempt+=1, если перешагнули max_attempts -> own failed; иначе -> global_pending,
      а partial mp4 без валидации удаляется.
    """
    summary = {
        "scanned": 0,
        "reclaimed_to_global_pending": 0,
        "moved_to_failed": 0,
        "marked_done": 0,
        "skipped_fresh": 0,
        "details": [],
    }
    dirs = assigned_dirs(job_root, worker_email)
    processing_dir = dirs["processing"]
    if not processing_dir.is_dir():
        return summary
    now = datetime.now(timezone.utc)
    stale_seconds = max(1, int(stale_minutes)) * 60
    global_pending_dir = job_root / "queue" / "global_pending"
    for path in sorted(processing_dir.glob("segment_*.json")):
        summary["scanned"] += 1
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            payload = {}
        segment_id = str(payload.get("segment_id") or path.stem)

        expected_duration = payload.get("expected_duration_sec") or payload.get("duration_sec")
        try:
            expected_duration_float = float(expected_duration) if expected_duration is not None else None
        except (TypeError, ValueError):
            expected_duration_float = None
        out_mp4 = _segment_output_mp4(job_root, segment_id)
        if valid_video(out_mp4, expected_duration_float):
            target = dirs["done"] / path.name
            payload.update(
                {
                    "status": "done",
                    "done_at": utc_now(),
                    "output_segment_path": str(out_mp4),
                    "self_reclaim_recovered_done": True,
                }
            )
            try:
                write_json(target, payload)
                path.unlink(missing_ok=True)
                summary["marked_done"] += 1
                summary["details"].append({"segment_id": segment_id, "action": "marked_done", "reason": "valid_segment_output_present", "to": str(target)})
            except OSError as exc:
                summary["details"].append({"segment_id": segment_id, "action": "marked_done_failed", "error": str(exc)})
            continue

        if str(payload.get("error_kind") or "") == "input_asset_missing":
            summary["details"].append({"segment_id": segment_id, "action": "skipped_input_asset_missing"})
            continue

        heartbeat_str = payload.get("heartbeat_at") or payload.get("updated_at") or payload.get("processing_started_at")
        heartbeat = _parse_iso_safe(heartbeat_str)
        if heartbeat is not None and heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age_seconds = (now - heartbeat).total_seconds() if heartbeat else None
        if age_seconds is not None and age_seconds < stale_seconds:
            summary["skipped_fresh"] += 1
            summary["details"].append({"segment_id": segment_id, "action": "skipped_fresh", "age_seconds": int(age_seconds), "heartbeat_at": heartbeat.isoformat() if heartbeat else ""})
            continue

        try:
            attempt = int(payload.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        try:
            reclaim_count = int(payload.get("reclaim_count") or 0)
        except (TypeError, ValueError):
            reclaim_count = 0

        partial_diag = {"existed": out_mp4.is_file(), "deleted": False, "size_bytes": 0, "path": str(out_mp4)}
        if out_mp4.is_file():
            try:
                partial_diag["size_bytes"] = out_mp4.stat().st_size
            except OSError:
                pass
            try:
                out_mp4.unlink()
                partial_diag["deleted"] = True
            except OSError:
                pass

        reason = "missing_processing_heartbeat" if heartbeat is None else f"heartbeat_stale_{int(age_seconds // 60)}m"
        new_attempt = attempt + 1
        common = {
            "attempt": new_attempt,
            "reclaim_count": reclaim_count + 1,
            "last_reclaimed_at": utc_now(),
            "last_reclaimed_reason": reason,
            "previous_worker_email": worker_email,
            "reclaimed_from_worker": worker_email,
            "reclaimed_age_seconds": int(age_seconds) if age_seconds is not None else None,
            "partial_output_cleanup": partial_diag,
            "self_reclaim": True,
        }

        if new_attempt > max(1, int(max_attempts)):
            target = dirs["failed"] / path.name
            payload.update({"status": "failed", "failed_at": utc_now(), **common})
            try:
                write_json(target, payload)
                path.unlink(missing_ok=True)
                summary["moved_to_failed"] += 1
                summary["details"].append({"segment_id": segment_id, "action": "moved_to_failed", "attempt": new_attempt, "max_attempts": int(max_attempts), "reason": reason, "to": str(target)})
            except OSError as exc:
                summary["details"].append({"segment_id": segment_id, "action": "move_to_failed_failed", "error": str(exc)})
            continue

        payload.update({"status": "pending", **common})
        payload.pop("assigned_worker", None)
        target = global_pending_dir / path.name
        try:
            global_pending_dir.mkdir(parents=True, exist_ok=True)
            write_json(target, payload)
            path.unlink(missing_ok=True)
            summary["reclaimed_to_global_pending"] += 1
            summary["details"].append({"segment_id": segment_id, "action": "reclaimed_to_global_pending", "attempt": new_attempt, "reason": reason, "to": str(target)})
        except OSError as exc:
            summary["details"].append({"segment_id": segment_id, "action": "reclaim_failed", "error": str(exc)})

    if logger is not None:
        logger.log(
            "SELF_RECLAIM",
            "self reclaim summary",
            scanned=summary["scanned"],
            reclaimed_to_global_pending=summary["reclaimed_to_global_pending"],
            moved_to_failed=summary["moved_to_failed"],
            marked_done=summary["marked_done"],
            skipped_fresh=summary["skipped_fresh"],
            details=json.dumps(summary["details"], ensure_ascii=False),
            stale_minutes=int(stale_minutes),
            max_attempts=int(max_attempts),
        )
    return summary


def claim_assigned_job(job_root: Path, worker_email: str) -> tuple[Path | None, Path | None]:
    dirs = assigned_dirs(job_root, worker_email)
    dirs["processing"].mkdir(parents=True, exist_ok=True)
    for pending in sorted(dirs["pending"].glob("segment_*.json")):
        processing = dirs["processing"] / pending.name
        try:
            pending.replace(processing)
        except OSError:
            continue
        payload = read_json(processing)
        payload.update(
            {
                "status": "processing",
                "assigned_worker": worker_email,
                "processing_started_at": utc_now(),
                "heartbeat_at": utc_now(),
            }
        )
        write_json(processing, payload)
        return processing, payload
    return None, None


def source_path(job_root: Path, relative: str, fallback_name: str = "") -> Path:
    rel = relative.strip().replace("\\", "/")
    candidate = job_root / rel
    if candidate.is_file():
        return candidate
    if rel.startswith("input/"):
        candidate = job_root / rel.replace("input/", "assets/", 1)
        if candidate.is_file():
            return candidate
    if fallback_name:
        candidate = job_root / "assets" / "frames" / fallback_name
        if candidate.is_file():
            return candidate
        candidate = job_root / "input" / "frames" / fallback_name
        if candidate.is_file():
            return candidate
    return job_root / rel


def drive_health_check(job_root: Path) -> dict[str, Any]:
    """Проверка доступности Drive mount.

    Возвращает {ok, reason, errno?, details?}. Не бросает исключения.
    """
    try:
        if not job_root.exists():
            return {"ok": False, "reason": "job_root_missing", "path": str(job_root)}
        list(job_root.iterdir())
    except OSError as exc:
        return {"ok": False, "reason": "job_root_listdir_failed", "errno": getattr(exc, "errno", None), "error": str(exc), "path": str(job_root)}
    for candidate in (job_root / "assets" / "frames", job_root / "input" / "frames"):
        try:
            if candidate.is_dir():
                list(candidate.iterdir())
        except OSError as exc:
            return {"ok": False, "reason": f"listdir_failed:{candidate.name}", "errno": getattr(exc, "errno", None), "error": str(exc), "path": str(candidate)}
    logs_dir = job_root / "logs" / "workers"
    if not _safe_mkdir(logs_dir):
        return {"ok": False, "reason": "logs_dir_mkdir_failed", "path": str(logs_dir)}
    return {"ok": True, "reason": "ok"}


def _classify_oserror(exc: OSError) -> str:
    errno_value = getattr(exc, "errno", None)
    if errno_value in DRIVE_DISCONNECT_ERRNOS:
        return "drive_unavailable"
    return "io_error"


def copy_assets(job_root: Path, job: dict[str, Any], work_dir: Path) -> tuple[list[dict[str, Any]], Path, Path]:
    segment_id_for_error = str(job.get("segment_id") or "")
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir)
        frames_dir = work_dir / "frames"
        effects_dir = work_dir / "effects"
        audio_dir = work_dir / "audio"
        frames_dir.mkdir(parents=True, exist_ok=True)
        effects_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if _classify_oserror(exc) == "drive_unavailable":
            raise DriveUnavailable(f"local work_dir prepare failed: {exc!r}", errno=getattr(exc, "errno", None)) from exc
        raise

    try:
        audio_source = job_root / "assets" / "audio" / "narration.mp3"
        if not audio_source.is_file():
            audio_source = job_root / "input" / "audio" / "narration.mp3"
        if audio_source.is_file():
            shutil.copy2(audio_source, audio_dir / "narration.mp3")

        for source in sorted((job_root / "assets" / "effects").glob("*")) if (job_root / "assets" / "effects").is_dir() else []:
            if source.is_file():
                shutil.copy2(source, effects_dir / source.name)
        for source in sorted((job_root / "input" / "effects").glob("*")) if (job_root / "input" / "effects").is_dir() else []:
            if source.is_file() and not (effects_dir / source.name).is_file():
                shutil.copy2(source, effects_dir / source.name)
    except OSError as exc:
        if _classify_oserror(exc) == "drive_unavailable":
            raise DriveUnavailable(f"audio/effects copy failed: {exc!r}", errno=getattr(exc, "errno", None)) from exc
        raise

    local_frames: list[dict[str, Any]] = []
    missing_frames: list[dict[str, Any]] = []
    drive_unavailable_during_scan = False
    drive_diag: dict[str, Any] = {}
    for frame in job.get("frames", []) if isinstance(job.get("frames"), list) else []:
        name = Path(str(frame.get("name") or frame.get("path") or "")).name
        try:
            source = source_path(job_root, str(frame.get("input_frame_path") or ""), name)
            exists = source.is_file()
        except OSError as exc:
            if _classify_oserror(exc) == "drive_unavailable":
                drive_unavailable_during_scan = True
                drive_diag = {"reason": "frame_stat_oserror", "errno": getattr(exc, "errno", None), "error": str(exc), "frame": name}
                break
            raise
        if not exists:
            missing_frames.append({
                "name": name,
                "input_frame_path": str(frame.get("input_frame_path") or ""),
                "resolved_path": str(source),
            })
            continue
        try:
            target = frames_dir / source.name
            shutil.copy2(source, target)
        except OSError as exc:
            if _classify_oserror(exc) == "drive_unavailable":
                raise DriveUnavailable(f"frame copy failed: {exc!r}", errno=getattr(exc, "errno", None), details={"frame": name}) from exc
            raise
        local_frames.append({**frame, "local_path": str(target)})

    if drive_unavailable_during_scan:
        raise DriveUnavailable("drive unavailable during frames scan", errno=drive_diag.get("errno"), details=drive_diag)

    if missing_frames:
        health = drive_health_check(job_root)
        if not health.get("ok"):
            raise DriveUnavailable(
                f"missing frames but drive health check failed: {health.get('reason')}",
                errno=health.get("errno"),
                details={"health": health, "missing_sample": missing_frames[:5]},
            )
        raise InputAssetMissing(
            f"missing input frame assets for segment {segment_id_for_error}: {len(missing_frames)} frame(s)",
            missing_frames=[item["name"] for item in missing_frames],
            segment_id=segment_id_for_error,
            details={
                "frame_start_index": job.get("frame_start_index"),
                "frame_end_index": job.get("frame_end_index"),
                "drive_health": health,
                "missing": missing_frames,
            },
        )
    return local_frames, effects_dir, audio_dir


def render_clip(
    image: Path,
    output: Path,
    duration: float,
    zoom_in: bool,
    log_path: Path,
    heartbeat: Callable[[], None],
    logger: WorkerLogger,
    segment_id: str,
    clip_index: int,
    clip_total: int,
) -> None:
    frames = max(2, int(math.ceil(duration * FPS)))
    denom = max(1, frames - 1)
    z_expr = f"1+{ZOOM_AMOUNT}*on/{denom}" if zoom_in else f"{1 + ZOOM_AMOUNT}-{ZOOM_AMOUNT}*on/{denom}"
    vf = (
        f"scale=-2:{UPSCALE_H},"
        f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "format=yuv420p"
    )
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        ffmpeg_preset(),
        "-crf",
        str(CRF),
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(partial),
    ]
    run_cmd(
        cmd,
        log_path,
        heartbeat=heartbeat,
        logger=logger,
        output_path=partial,
        segment_id=segment_id,
        stage_name="clip_render",
        stage_index=1,
        stage_total=STAGE_TOTAL,
        input_path=image,
        expected_duration=duration,
        clip_index=clip_index,
        clip_total=clip_total,
    )
    if not valid_video(partial, duration):
        raise RuntimeError(f"clip failed validation: {partial}")
    partial.replace(output)
    logger.log("FFMPEG RESULT", "clip validation ok", output=output, size=output.stat().st_size, ffprobe=ffprobe_summary(output))


def concat_videos(parts: list[Path], output: Path, log_path: Path, heartbeat: Callable[[], None], logger: WorkerLogger, segment_id: str, expected_duration: float | None) -> None:
    if not parts:
        raise RuntimeError("No clip parts to concat")
    if len(parts) == 1:
        shutil.copy2(parts[0], output)
        logger.log("STAGE DONE", "concat", segment_id=segment_id, stage_name="concat", elapsed=0, output_size_mb=mb(output.stat().st_size), ffprobe_ok=True)
        return
    list_path = output.parent / f"{output.stem}.concat.txt"
    lines = [f"file '{part.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for part in parts]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    run_cmd(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(partial)],
        log_path,
        heartbeat=heartbeat,
        logger=logger,
        output_path=partial,
        segment_id=segment_id,
        stage_name="concat",
        stage_index=2,
        stage_total=STAGE_TOTAL,
        input_path=list_path,
        expected_duration=expected_duration,
    )
    partial.replace(output)
    logger.log("FFMPEG RESULT", "concat validation", output=output, size=output.stat().st_size, ffprobe=ffprobe_summary(output))


def apply_optional_effects(
    video: Path,
    output: Path,
    effects_dir: Path,
    log_path: Path,
    heartbeat: Callable[[], None],
    logger: WorkerLogger,
    segment_id: str,
    expected_duration: float | None,
    skip_effects: bool,
) -> dict[str, Any]:
    film = effects_dir / "film.mp4"
    dust = effects_dir / "dust.mp4"
    has_film = film.is_file()
    has_dust = dust.is_file()
    stage_started = time.time()
    if skip_effects:
        logger.log(
            "STAGE",
            "effects",
            segment_id=segment_id,
            stage_name="effects",
            stage_index=3,
            stage_total=STAGE_TOTAL,
            input_path=video,
            output_path=output,
            expected_duration_seconds=round(float(expected_duration or 0), 3) if expected_duration else "",
            started_at=utc_now(),
            elapsed_seconds=0,
        )
        logger.log("WARN", "effects skipped by CONTENT_FACTORY_SKIP_EFFECTS=1", segment_id=segment_id, stage_name="effects")
        shutil.copy2(video, output)
        logger.log(
            "STAGE DONE",
            "effects",
            segment_id=segment_id,
            stage_name="effects",
            elapsed=int(time.time() - stage_started),
            output_size_mb=mb(output.stat().st_size),
            ffprobe_ok=valid_video(output, expected_duration),
        )
        return {"film": False, "dust": False, "grain": False, "skipped": True}
    if not has_film and not has_dust and GRAIN_STRENGTH <= 0:
        logger.log(
            "STAGE",
            "effects",
            segment_id=segment_id,
            stage_name="effects",
            stage_index=3,
            stage_total=STAGE_TOTAL,
            input_path=video,
            output_path=output,
            expected_duration_seconds=round(float(expected_duration or 0), 3) if expected_duration else "",
            started_at=utc_now(),
            elapsed_seconds=0,
        )
        shutil.copy2(video, output)
        logger.log(
            "STAGE DONE",
            "effects",
            segment_id=segment_id,
            stage_name="effects",
            elapsed=int(time.time() - stage_started),
            output_size_mb=mb(output.stat().st_size),
            ffprobe_ok=valid_video(output, expected_duration),
        )
        return {"film": False, "dust": False, "grain": False}

    raw_duration = media_duration(video)
    if raw_duration <= 0:
        raise RuntimeError(f"effects input has invalid duration: {video} duration={raw_duration}")

    cmd: list[str] = ["ffmpeg", "-y", "-i", str(video)]
    input_idx = 1
    film_idx = -1
    dust_idx = -1
    if has_film:
        cmd.extend(["-stream_loop", "-1", "-i", str(film)])
        film_idx = input_idx
        input_idx += 1
    if has_dust:
        cmd.extend(["-stream_loop", "-1", "-i", str(dust)])
        dust_idx = input_idx

    current = "0:v"
    filters: list[str] = []
    need_rgb = has_film or has_dust
    if need_rgb:
        filters.append(f"[{current}]format=gbrp[main_rgb]")
        current = "main_rgb"
    if has_film:
        filters.append(f"[{film_idx}:v]scale={WIDTH}:{HEIGHT},format=gbrp[film]")
        filters.append(f"[{current}][film]blend=all_mode=overlay:all_opacity=0.4[vfilm]")
        current = "vfilm"
    if has_dust:
        filters.append(f"[{dust_idx}:v]scale={WIDTH}:{HEIGHT},format=gbrp[dust]")
        filters.append(f"[{current}][dust]blend=all_mode=screen[vdust]")
        current = "vdust"
    if need_rgb:
        filters.append(f"[{current}]format=yuv420p[back_yuv]")
        current = "back_yuv"
    filters.append(f"[{current}]noise=alls={GRAIN_STRENGTH}:allf=t+u[vout]" if GRAIN_STRENGTH > 0 else f"[{current}]null[vout]")

    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-preset",
            ffmpeg_preset(),
            "-crf",
            str(CRF),
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-t",
            f"{raw_duration:.3f}",
            str(partial),
        ]
    )
    run_cmd(
        cmd,
        log_path,
        heartbeat=heartbeat,
        logger=logger,
        output_path=partial,
        segment_id=segment_id,
        stage_name="effects",
        stage_index=3,
        stage_total=STAGE_TOTAL,
        input_path=video,
        expected_duration=raw_duration,
    )
    validate_effects_duration(partial, raw_duration, logger, segment_id)
    partial.replace(output)
    logger.log("FFMPEG RESULT", "effects validation", output=output, size=output.stat().st_size, ffprobe=ffprobe_summary(output))
    return {"film": has_film, "dust": has_dust, "grain": GRAIN_STRENGTH > 0}


def checkpoint_root(job_root: Path, segment_id: str) -> Path:
    return job_root / "work_segments" / segment_id


def checkpoint_paths(job_root: Path, segment_id: str) -> dict[str, Path]:
    root = checkpoint_root(job_root, segment_id)
    return {
        "root": root,
        "clips": root / "clips",
        "raw": root / "raw",
        "effects": root / "effects",
        "logs": root / "logs",
        "status_json": root / "status.json",
        "manifest_json": root / "checkpoint_manifest.json",
    }


def cleanup_orphan_partials(stage_dir: Path) -> list[dict[str, Any]]:
    """Удаляет *.partial.mp4 и mp4 без сопутствующего <name>.done.json."""
    removed: list[dict[str, Any]] = []
    if not stage_dir.is_dir():
        return removed
    for partial in stage_dir.glob("*.partial.mp4"):
        try:
            size = partial.stat().st_size if partial.is_file() else 0
            partial.unlink(missing_ok=True)
            removed.append({"path": str(partial), "kind": "partial", "size": size})
        except OSError as exc:
            removed.append({"path": str(partial), "kind": "partial", "error": str(exc)})
    for mp4 in stage_dir.glob("*.mp4"):
        if mp4.name.endswith(".partial.mp4"):
            continue
        marker = mp4.with_suffix(mp4.suffix + ".done.json")
        if marker.is_file():
            continue
        try:
            size = mp4.stat().st_size if mp4.is_file() else 0
            mp4.unlink(missing_ok=True)
            removed.append({"path": str(mp4), "kind": "orphan_no_done_marker", "size": size})
        except OSError as exc:
            removed.append({"path": str(mp4), "kind": "orphan_no_done_marker", "error": str(exc)})
    return removed


def stage_checkpoint_valid(target: Path, expected_duration: float | None, duration_tolerance: float = 2.5) -> tuple[bool, str]:
    """True, если target + <target>.done.json существуют и mp4 проходит ffprobe.

    expected_duration в секундах; tolerance — допустимое отклонение в обе стороны.
    """
    marker = target.with_suffix(target.suffix + ".done.json")
    if not target.is_file():
        return False, "missing_target_mp4"
    if not marker.is_file():
        return False, "missing_done_marker"
    try:
        if target.stat().st_size < MIN_VIDEO_SIZE_BYTES:
            return False, "target_too_small"
    except OSError:
        return False, "stat_failed"
    if not has_video_stream(target):
        return False, "no_video_stream"
    if expected_duration is None:
        return True, "ok_no_duration_check"
    try:
        duration = media_duration(target)
    except RuntimeError:
        return False, "ffprobe_failed"
    if abs(duration - float(expected_duration)) > duration_tolerance:
        return False, f"duration_mismatch:{duration:.3f}_vs_{expected_duration:.3f}"
    return True, "ok"


def save_stage_artifact(
    local_path: Path,
    drive_target: Path,
    *,
    expected_duration: float | None,
    stage_name: str,
    segment_id: str,
    logger: WorkerLogger,
    extra_marker: dict[str, Any] | None = None,
    duration_tolerance: float = 2.5,
) -> dict[str, Any]:
    """Атомарно копирует валидный local artifact в Drive и пишет <name>.done.json.

    Сначала пишется <name>.partial.mp4, затем replace -> <name>.mp4, затем .done.json.
    """
    drive_target.parent.mkdir(parents=True, exist_ok=True)
    if not local_path.is_file():
        raise RuntimeError(f"local artifact missing before save: {local_path}")
    if not valid_video(local_path, expected_duration, duration_tolerance):
        raise RuntimeError(f"local artifact not valid (expected_duration={expected_duration}): {local_path}")
    partial = drive_target.with_name(f"{drive_target.stem}.partial{drive_target.suffix}")
    if partial.is_file():
        try:
            partial.unlink()
        except OSError:
            pass
    shutil.copy2(local_path, partial)
    partial.replace(drive_target)
    size = drive_target.stat().st_size
    marker = drive_target.with_suffix(drive_target.suffix + ".done.json")
    marker_payload = {
        "stage": stage_name,
        "segment_id": segment_id,
        "target_path": str(drive_target),
        "size_bytes": int(size),
        "expected_duration_sec": float(expected_duration) if expected_duration is not None else None,
        "ffprobe": ffprobe_summary(drive_target),
        "written_at": utc_now(),
    }
    if extra_marker:
        marker_payload.update(extra_marker)
    write_json(marker, marker_payload)
    logger.log(
        "CHECKPOINT",
        f"{stage_name} checkpoint saved",
        segment_id=segment_id,
        stage_name=stage_name,
        target=drive_target,
        size_bytes=int(size),
        marker=marker,
    )
    return marker_payload


def load_stage_artifact_if_valid(
    drive_target: Path,
    local_target: Path,
    *,
    expected_duration: float | None,
    stage_name: str,
    segment_id: str,
    logger: WorkerLogger,
    duration_tolerance: float = 2.5,
) -> bool:
    """Если Drive checkpoint валиден, копирует его в local_target и возвращает True."""
    ok, reason = stage_checkpoint_valid(drive_target, expected_duration, duration_tolerance)
    if not ok:
        if drive_target.is_file() or drive_target.with_suffix(drive_target.suffix + ".done.json").is_file():
            logger.log(
                "CHECKPOINT",
                f"{stage_name} checkpoint invalid, will re-render",
                segment_id=segment_id,
                stage_name=stage_name,
                target=drive_target,
                reason=reason,
            )
        return False
    local_target.parent.mkdir(parents=True, exist_ok=True)
    if local_target.is_file():
        try:
            local_target.unlink()
        except OSError:
            pass
    shutil.copy2(drive_target, local_target)
    logger.log(
        "CHECKPOINT",
        f"{stage_name} checkpoint reused",
        segment_id=segment_id,
        stage_name=stage_name,
        from_path=drive_target,
        to_path=local_target,
        size_bytes=drive_target.stat().st_size,
    )
    return True


def write_segment_checkpoint_status(
    job_root: Path,
    segment_id: str,
    *,
    stage: str,
    state: str,
    clips_done: int,
    clips_total: int,
    raw_done: bool,
    effects_done: bool,
    final_done: bool,
    worker_email: str,
    extra: dict[str, Any] | None = None,
) -> None:
    paths = checkpoint_paths(job_root, segment_id)
    paths["root"].mkdir(parents=True, exist_ok=True)
    payload = {
        "segment_id": segment_id,
        "stage": stage,
        "state": state,
        "clips_done": int(clips_done),
        "clips_total": int(clips_total),
        "raw_done": bool(raw_done),
        "effects_done": bool(effects_done),
        "final_done": bool(final_done),
        "worker_email": worker_email,
        "updated_at": utc_now(),
    }
    if extra:
        payload.update(extra)
    try:
        write_json(paths["status_json"], payload)
    except OSError:
        pass


def render_segment(job_root: Path, worker_email: str, processing_job: Path, job: dict[str, Any], tmp_root: Path, logger: WorkerLogger) -> dict[str, Any]:
    segment_id = str(job.get("segment_id") or processing_job.stem)
    expected_duration = float(job.get("expected_duration_sec") or job.get("duration_sec") or 0)
    output_drive = job_root / "segments" / f"{segment_id}.mp4"
    if valid_video(output_drive, expected_duration if expected_duration > 0 else None):
        logger.log("DONE", "segment mp4 already valid, skipping render", segment_id=segment_id, output=output_drive)
        return {"segment_id": segment_id, "status": "skipped_existing", "output_segment_path": str(output_drive)}

    work_dir = tmp_root / segment_id
    local_frames, effects_dir, _audio_dir = copy_assets(job_root, job, work_dir)
    clips_dir = work_dir / "clips"
    logs_dir = work_dir / "logs"
    clips_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    skip_effects = env_bool("CONTENT_FACTORY_SKIP_EFFECTS", False)
    effects_found = sorted([p.name for p in effects_dir.glob("*") if p.is_file()])
    effects_enabled = (not skip_effects) and (bool(effects_found) or GRAIN_STRENGTH > 0)

    cp_paths = checkpoint_paths(job_root, segment_id)
    cp_paths["root"].mkdir(parents=True, exist_ok=True)
    cp_paths["clips"].mkdir(parents=True, exist_ok=True)
    cp_paths["raw"].mkdir(parents=True, exist_ok=True)
    cp_paths["effects"].mkdir(parents=True, exist_ok=True)
    cp_paths["logs"].mkdir(parents=True, exist_ok=True)
    use_checkpoints = not env_bool("CONTENT_FACTORY_DISABLE_CHECKPOINTS", False)
    cleanup_clips = cleanup_orphan_partials(cp_paths["clips"]) if use_checkpoints else []
    cleanup_raw = cleanup_orphan_partials(cp_paths["raw"]) if use_checkpoints else []
    cleanup_effects = cleanup_orphan_partials(cp_paths["effects"]) if use_checkpoints else []
    if cleanup_clips or cleanup_raw or cleanup_effects:
        logger.log(
            "CHECKPOINT",
            "cleaned orphan partial/no-marker checkpoint files",
            segment_id=segment_id,
            cleanup_clips=len(cleanup_clips),
            cleanup_raw=len(cleanup_raw),
            cleanup_effects=len(cleanup_effects),
            details=json.dumps({"clips": cleanup_clips, "raw": cleanup_raw, "effects": cleanup_effects}, ensure_ascii=False),
        )

    logger.log(
        "PLAN",
        "segment render plan",
        segment_id=segment_id,
        clips_total=len(local_frames),
        stages="clip_render,concat,effects,final_validate,move_to_done",
        expected_raw_duration=round(expected_duration, 3) if expected_duration else "",
        effects_enabled=effects_enabled,
        skip_effects=skip_effects,
        ffmpeg_preset=ffmpeg_preset(),
        progress_interval_seconds=env_int("CONTENT_FACTORY_FFMPEG_PROGRESS_INTERVAL_SECONDS", DEFAULT_FFMPEG_PROGRESS_INTERVAL_SECONDS),
        stall_timeout_seconds=env_int("CONTENT_FACTORY_FFMPEG_STALL_TIMEOUT_SECONDS", DEFAULT_FFMPEG_STALL_TIMEOUT_SECONDS),
        checkpoints_enabled=use_checkpoints,
        checkpoint_root=cp_paths["root"],
    )

    def heartbeat() -> None:
        update_processing_heartbeat(processing_job, worker_email, segment_id)
        update_status(
            job_root,
            worker_email,
            status="processing",
            current_segment_id=segment_id,
            assigned_queue=str(assigned_dirs(job_root, worker_email)["pending"]),
            last_message="ffmpeg heartbeat",
        )
        logger.log("RENDER", "heartbeat", segment_id=segment_id)

    logger.log(
        "RENDER",
        "starting segment render",
        segment_id=segment_id,
        temp_dir=work_dir,
        input_frames_count=len(local_frames),
        effects_found=",".join(effects_found) if effects_found else "none",
        expected_output=output_drive,
    )

    write_segment_checkpoint_status(
        job_root,
        segment_id,
        stage="clip_render",
        state="starting",
        clips_done=0,
        clips_total=len(local_frames),
        raw_done=False,
        effects_done=False,
        final_done=False,
        worker_email=worker_email,
    )

    parts: list[Path] = []
    clips_total = len(local_frames)
    clips_reused = 0
    for idx, frame in enumerate(local_frames, start=1):
        duration = float(frame.get("duration_sec") or 0)
        if duration <= 0:
            continue
        clip = clips_dir / f"{segment_id}_clip_{idx:04d}.mp4"
        cp_clip = cp_paths["clips"] / f"clip_{idx:04d}.mp4"
        clip_log = cp_paths["logs"] / f"clip_{idx:04d}.ffmpeg.log"
        clip_log_alias = logs_dir / f"clip_{idx:04d}.ffmpeg.log"
        reused = False
        if use_checkpoints and load_stage_artifact_if_valid(
            cp_clip,
            clip,
            expected_duration=duration,
            stage_name="clip_render",
            segment_id=segment_id,
            logger=logger,
        ):
            reused = True
            clips_reused += 1
        if not reused:
            render_clip(
                Path(str(frame["local_path"])),
                clip,
                duration,
                bool(frame.get("zoom_in", True)),
                clip_log_alias,
                heartbeat,
                logger,
                segment_id,
                idx,
                clips_total,
            )
            if use_checkpoints:
                try:
                    if clip_log_alias.is_file():
                        shutil.copy2(clip_log_alias, clip_log)
                except OSError:
                    pass
                save_stage_artifact(
                    clip,
                    cp_clip,
                    expected_duration=duration,
                    stage_name="clip_render",
                    segment_id=segment_id,
                    logger=logger,
                    extra_marker={"clip_index": idx, "clip_total": clips_total, "duration_sec": duration},
                )
        parts.append(clip)
        logger.log(
            "SEGMENT PROGRESS",
            "clip completed",
            segment_id=segment_id,
            clips_done=len(parts),
            clips_total=clips_total,
            stage_name="clip_render",
            percent=f"{(len(parts) / max(1, clips_total)) * 100:.1f}",
            reused_checkpoint=reused,
        )
        write_segment_checkpoint_status(
            job_root,
            segment_id,
            stage="clip_render",
            state="in_progress",
            clips_done=len(parts),
            clips_total=clips_total,
            raw_done=False,
            effects_done=False,
            final_done=False,
            worker_email=worker_email,
            extra={"last_clip_reused": reused, "clips_reused": clips_reused},
        )
    if not parts:
        raise RuntimeError(f"segment has no renderable frames: {segment_id}")

    raw_segment = work_dir / f"{segment_id}.raw.mp4"
    final_local = work_dir / f"{segment_id}.mp4"

    cp_raw = cp_paths["raw"] / f"{segment_id}.raw.mp4"
    raw_concat_log = cp_paths["logs"] / "concat.ffmpeg.log"
    raw_concat_log_alias = logs_dir / "concat.ffmpeg.log"
    raw_reused = False
    if use_checkpoints and load_stage_artifact_if_valid(
        cp_raw,
        raw_segment,
        expected_duration=expected_duration if expected_duration > 0 else None,
        stage_name="concat_raw",
        segment_id=segment_id,
        logger=logger,
    ):
        raw_reused = True
    if not raw_reused:
        concat_videos(parts, raw_segment, raw_concat_log_alias, heartbeat, logger, segment_id, expected_duration if expected_duration > 0 else None)
        if use_checkpoints:
            try:
                if raw_concat_log_alias.is_file():
                    shutil.copy2(raw_concat_log_alias, raw_concat_log)
            except OSError:
                pass
            save_stage_artifact(
                raw_segment,
                cp_raw,
                expected_duration=expected_duration if expected_duration > 0 else None,
                stage_name="concat_raw",
                segment_id=segment_id,
                logger=logger,
                extra_marker={"clip_total": clips_total},
            )
    raw_duration = media_duration(raw_segment)
    write_segment_checkpoint_status(
        job_root,
        segment_id,
        stage="concat_raw",
        state="completed",
        clips_done=len(parts),
        clips_total=clips_total,
        raw_done=True,
        effects_done=False,
        final_done=False,
        worker_email=worker_email,
        extra={"raw_duration_sec": round(raw_duration, 3), "raw_reused": raw_reused},
    )

    cp_effects = cp_paths["effects"] / f"{segment_id}.effects.mp4"
    effects_log = cp_paths["logs"] / "effects.ffmpeg.log"
    effects_log_alias = logs_dir / "effects.ffmpeg.log"
    effects: dict[str, Any] = {}
    effects_reused = False
    if use_checkpoints and load_stage_artifact_if_valid(
        cp_effects,
        final_local,
        expected_duration=raw_duration,
        stage_name="effects",
        segment_id=segment_id,
        logger=logger,
        duration_tolerance=EFFECTS_DURATION_TOLERANCE_SECONDS + EFFECTS_MAX_OVERRUN_SECONDS,
    ):
        effects_reused = True
        marker_path = cp_effects.with_suffix(cp_effects.suffix + ".done.json")
        try:
            saved_marker = read_json(marker_path)
            effects = saved_marker.get("effects_config") if isinstance(saved_marker.get("effects_config"), dict) else {}
        except (OSError, json.JSONDecodeError):
            effects = {}
        if not effects:
            effects = {"reused_checkpoint": True}
    if not effects_reused:
        effects = apply_optional_effects(raw_segment, final_local, effects_dir, effects_log_alias, heartbeat, logger, segment_id, raw_duration, skip_effects)
        if use_checkpoints:
            try:
                if effects_log_alias.is_file():
                    shutil.copy2(effects_log_alias, effects_log)
            except OSError:
                pass
            save_stage_artifact(
                final_local,
                cp_effects,
                expected_duration=raw_duration,
                stage_name="effects",
                segment_id=segment_id,
                logger=logger,
                extra_marker={"effects_config": effects, "raw_duration_sec": round(raw_duration, 3)},
                duration_tolerance=EFFECTS_DURATION_TOLERANCE_SECONDS + EFFECTS_MAX_OVERRUN_SECONDS,
            )
    write_segment_checkpoint_status(
        job_root,
        segment_id,
        stage="effects",
        state="completed",
        clips_done=len(parts),
        clips_total=clips_total,
        raw_done=True,
        effects_done=True,
        final_done=False,
        worker_email=worker_email,
        extra={"effects_reused": effects_reused, "effects_config": effects},
    )
    final_started = time.time()
    logger.log(
        "STAGE",
        "final_validate",
        segment_id=segment_id,
        stage_name="final_validate",
        stage_index=4,
        stage_total=STAGE_TOTAL,
        input_path=final_local,
        output_path=output_drive,
        expected_duration_seconds=round(expected_duration, 3) if expected_duration else "",
        started_at=utc_now(),
        elapsed_seconds=0,
    )
    if not valid_video(final_local, expected_duration if expected_duration > 0 else None):
        logger.log("STAGE FAILED", "final_validate", segment_id=segment_id, stage_name="final_validate", elapsed=int(time.time() - final_started), reason="ffprobe_validation_failed", output_size_mb=mb(get_output_size_bytes(final_local)))
        raise RuntimeError(f"segment failed validation: {final_local}")
    logger.log("STAGE DONE", "final_validate", segment_id=segment_id, stage_name="final_validate", elapsed=int(time.time() - final_started), output_size_mb=mb(get_output_size_bytes(final_local)), ffprobe_ok=True)
    output_drive.parent.mkdir(parents=True, exist_ok=True)
    tmp_drive = output_drive.with_suffix(output_drive.suffix + ".tmp")
    shutil.copy2(final_local, tmp_drive)
    tmp_drive.replace(output_drive)
    final_marker = output_drive.with_suffix(output_drive.suffix + ".done.json")
    final_marker_payload = {
        "stage": "final_segment",
        "segment_id": segment_id,
        "target_path": str(output_drive),
        "size_bytes": int(output_drive.stat().st_size),
        "expected_duration_sec": float(expected_duration) if expected_duration else None,
        "ffprobe": ffprobe_summary(output_drive),
        "worker_email": worker_email,
        "written_at": utc_now(),
    }
    try:
        write_json(final_marker, final_marker_payload)
    except OSError:
        pass
    write_segment_checkpoint_status(
        job_root,
        segment_id,
        stage="final_segment",
        state="completed",
        clips_done=len(parts),
        clips_total=clips_total,
        raw_done=True,
        effects_done=True,
        final_done=True,
        worker_email=worker_email,
        extra={"output_segment_path": str(output_drive), "effects_config": effects},
    )
    try:
        manifest = {
            "segment_id": segment_id,
            "story_slug": job_root.name,
            "expected_duration_sec": float(expected_duration) if expected_duration else None,
            "clips_total": clips_total,
            "clips_reused": int(clips_reused),
            "raw_reused": bool(raw_reused),
            "effects_reused": bool(effects_reused),
            "effects_config": effects,
            "output_segment_path": str(output_drive),
            "final_marker": str(final_marker),
            "worker_email": worker_email,
            "completed_at": utc_now(),
        }
        write_json(cp_paths["manifest_json"], manifest)
    except OSError:
        pass
    logger.log("DONE", "segment copied to Drive", segment_id=segment_id, mp4_path=output_drive, size=output_drive.stat().st_size, ffprobe=ffprobe_summary(output_drive))
    return {
        "segment_id": segment_id,
        "status": "rendered",
        "output_segment_path": str(output_drive),
        "expected_duration_sec": expected_duration,
        "effects": effects,
        "clips_reused": int(clips_reused),
        "raw_reused": bool(raw_reused),
        "effects_reused": bool(effects_reused),
    }


def mark_done(job_root: Path, worker_email: str, processing_job: Path, payload: dict[str, Any], render_report: dict[str, Any], logger: WorkerLogger) -> None:
    segment_id = str(payload.get("segment_id") or processing_job.stem)
    stage_started = time.time()
    logger.log(
        "STAGE",
        "move_to_done",
        segment_id=segment_id,
        stage_name="move_to_done",
        stage_index=5,
        stage_total=STAGE_TOTAL,
        input_path=processing_job,
        output_path=assigned_dirs(job_root, worker_email)["done"] / processing_job.name,
        started_at=utc_now(),
        elapsed_seconds=0,
    )
    done_path = assigned_dirs(job_root, worker_email)["done"] / processing_job.name
    payload.update({"status": "done", "done_at": utc_now(), "render_report": render_report})
    write_json(done_path, payload)
    processing_job.unlink(missing_ok=True)
    report_path = job_root / "reports" / f"{segment_id}__{safe_email(worker_email)}.json"
    write_json(report_path, {"ok": True, "worker_email": worker_email, **render_report, "written_at": utc_now()})
    logger.log("STAGE DONE", "move_to_done", segment_id=segment_id, stage_name="move_to_done", elapsed=int(time.time() - stage_started), output_size_mb=0, ffprobe_ok=True)
    logger.log("DONE", "moved job json to done", segment_id=segment_id, mp4_path=render_report.get("output_segment_path"), report_path=report_path, done_json=done_path)


def mark_failed(
    job_root: Path,
    worker_email: str,
    processing_job: Path,
    payload: dict[str, Any],
    error: BaseException,
    logger: WorkerLogger,
    *,
    error_kind: str = "runtime_error",
    extra_payload: dict[str, Any] | None = None,
) -> None:
    segment_id = str(payload.get("segment_id") or processing_job.stem)
    failed_path = assigned_dirs(job_root, worker_email)["failed"] / processing_job.name
    update_fields = {
        "status": "failed",
        "failed_at": utc_now(),
        "error": str(error),
        "error_kind": error_kind,
    }
    if extra_payload:
        update_fields.update(extra_payload)
    payload.update(update_fields)
    try:
        write_json(failed_path, payload)
    except OSError as exc:
        logger.log("FAILED", "drive write_json failed_path failed", error=repr(exc), failed_path=failed_path)
    try:
        processing_job.unlink(missing_ok=True)
    except OSError as exc:
        logger.log("FAILED", "processing_job unlink failed", error=repr(exc), processing_job=processing_job)
    report_path = job_root / "reports" / f"{segment_id}__{safe_email(worker_email)}_error.json"
    try:
        write_json(
            report_path,
            {
                "ok": False,
                "worker_email": worker_email,
                "segment_id": segment_id,
                "error": str(error),
                "error_kind": error_kind,
                "extra": extra_payload or {},
                "traceback": traceback.format_exc(),
                "written_at": utc_now(),
            },
        )
    except OSError as exc:
        logger.log("FAILED", "drive write_json report_path failed", error=repr(exc), report_path=report_path)
    logger.exception("FAILED", "segment failed", error)
    logger.log("FAILED", "moved job json to failed", segment_id=segment_id, error_kind=error_kind, failed_json=failed_path, report_path=report_path)


def main() -> int:
    started_at = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("--drive-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--story-slug", default="")
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--idle-timeout-min", type=float, default=15)
    parser.add_argument("--max-segments", type=int, default=0, help="0 means unlimited")
    parser.add_argument("--tmp-root", default="/content/tmp/content_factory_video")
    parser.add_argument("--skip-effects", action="store_true", help="Debug only: skip heavy effects pass")
    args, _unknown = parser.parse_known_args()
    if args.skip_effects:
        os.environ["CONTENT_FACTORY_SKIP_EFFECTS"] = "1"

    worker_email = os.environ.get("CONTENT_FACTORY_WORKER_EMAIL", "").strip()
    content_factory_env = {key: value for key, value in sorted(os.environ.items()) if key.startswith("CONTENT_FACTORY_")}
    root = resolve_youtube_root(args.drive_root)
    candidates = sorted((root / "video_jobs").glob("*/VIDEO_JOB_READY.json"), key=lambda p: p.stat().st_mtime, reverse=True) if (root / "video_jobs").is_dir() else []
    print_stage(
        "BOOT",
        "worker starting",
        started_at=utc_now(),
        python=sys.version.replace("\n", " "),
        cwd=Path.cwd(),
        WORKER_EMAIL=worker_email or "MISSING",
        ROOT=root,
        ROOT_exists=root.exists(),
        env=json.dumps(content_factory_env, ensure_ascii=False),
        active_job_candidates=json.dumps([str(path) for path in candidates], ensure_ascii=False),
    )
    if not worker_email:
        raise RuntimeError("CONTENT_FACTORY_WORKER_EMAIL is required; this worker only reads its assigned queue.")
    job_root = find_active_job(root, args.story_slug.strip() or None)
    logger = WorkerLogger(job_root / "logs" / "workers" / f"{safe_email(worker_email)}.log")
    logger.log(
        "BOOT",
        "worker reached root script",
        started_at=utc_now(),
        python=sys.version.replace("\n", " "),
        cwd=Path.cwd(),
        WORKER_EMAIL=worker_email,
        ROOT=root,
        ROOT_exists=root.exists(),
        env=json.dumps(content_factory_env, ensure_ascii=False),
        active_job_candidates=json.dumps([str(path) for path in candidates], ensure_ascii=False),
    )
    logger.log(
        "JOB",
        "active job selected",
        story_slug=job_root.name,
        job_root=job_root,
        VIDEO_JOB_READY_exists=(job_root / "VIDEO_JOB_READY.json").is_file(),
        VIDEO_JOB_MANIFEST_exists=(job_root / "VIDEO_JOB_MANIFEST.json").is_file() or (job_root / "manifests" / "video_job_manifest.json").is_file(),
    )
    dirs = assigned_dirs(job_root, worker_email)
    dirs["pending"].mkdir(parents=True, exist_ok=True)
    dirs["processing"].mkdir(parents=True, exist_ok=True)
    dirs["done"].mkdir(parents=True, exist_ok=True)
    dirs["failed"].mkdir(parents=True, exist_ok=True)
    counts = queue_counts(dirs)
    logger.log(
        "QUEUE",
        "assigned queue paths",
        pending_path=dirs["pending"],
        processing_path=dirs["processing"],
        done_path=dirs["done"],
        failed_path=dirs["failed"],
        pending_count=counts["pending"],
        processing_count=counts["processing"],
        done_count=counts["done"],
        failed_count=counts["failed"],
    )
    update_status(job_root, worker_email, status="booting", current_segment_id="", assigned_queue=str(dirs["pending"]), last_message="worker booted")

    self_reclaim_stale_minutes = env_int("CONTENT_FACTORY_SELF_RECLAIM_STALE_MINUTES", 10)
    self_reclaim_max_attempts = env_int("CONTENT_FACTORY_SELF_RECLAIM_MAX_ATTEMPTS", 3)
    if env_bool("CONTENT_FACTORY_DISABLE_SELF_RECLAIM", default=False):
        logger.log("SELF_RECLAIM", "self reclaim disabled by CONTENT_FACTORY_DISABLE_SELF_RECLAIM")
    else:
        try:
            self_reclaim_own_stale_processing(
                job_root,
                worker_email,
                stale_minutes=self_reclaim_stale_minutes,
                max_attempts=self_reclaim_max_attempts,
                logger=logger,
            )
        except Exception as exc:
            logger.log("SELF_RECLAIM", "self reclaim failed", error=repr(exc))

    processed_count = 0
    failed_count = 0
    idle_started = time.time()
    idle_timeout = max(0.1, float(args.idle_timeout_min)) * 60
    poll_seconds = max(1, int(args.poll_seconds))
    max_jobs_env = os.environ.get("CONTENT_FACTORY_MAX_JOBS_PER_RUN", "").strip()
    max_segments = max(0, int(max_jobs_env or args.max_segments))
    logger.log("LOOP", "loop configured", poll_seconds=poll_seconds, idle_timeout_seconds=idle_timeout, max_segments=max_segments)
    while True:
        counts = queue_counts(dirs)
        idle_seconds = int(time.time() - idle_started)
        logger.log("LOOP", "tick", timestamp=utc_now(), pending_count=counts["pending"], idle_seconds=idle_seconds, last_message="checking assigned pending")
        if max_segments and (processed_count + failed_count) >= max_segments:
            logger.log("EXIT", "max jobs reached", reason="finished_max_segments", processed_count=processed_count, failed_count=failed_count, runtime_seconds=int(time.time() - started_at))
            update_status(job_root, worker_email, status="exited", current_segment_id="", processed_count=processed_count, failed_count=failed_count, last_message="finished max jobs per run")
            return 0
        processing_job, payload = claim_assigned_job(job_root, worker_email)
        if processing_job is None or payload is None:
            if time.time() - idle_started >= idle_timeout:
                logger.log("EXIT", "idle timeout", reason="idle_timeout", processed_count=processed_count, failed_count=failed_count, runtime_seconds=int(time.time() - started_at))
                update_status(job_root, worker_email, status="exited", current_segment_id="", processed_count=processed_count, failed_count=failed_count, last_message="idle timeout")
                return 0
            update_status(job_root, worker_email, status="idle", current_segment_id="", processed_count=processed_count, failed_count=failed_count, assigned_queue=str(dirs["pending"]), last_message="no assigned pending job")
            time.sleep(poll_seconds)
            continue
        idle_started = time.time()
        segment_id = str(payload.get("segment_id") or processing_job.stem)
        logger.log(
            "CLAIM",
            "claimed assigned segment",
            segment_id=segment_id,
            source_pending_json=dirs["pending"] / processing_job.name,
            processing_json=processing_job,
            frame_start=payload.get("frame_start_index"),
            frame_end=payload.get("frame_end_index"),
            frame_count=len(payload.get("frames") or []),
            output_mp4=job_root / "segments" / f"{segment_id}.mp4",
        )
        try:
            update_status(job_root, worker_email, status="processing", current_segment_id=segment_id, processed_count=processed_count, failed_count=failed_count, last_message="rendering segment")
            report = render_segment(job_root, worker_email, processing_job, payload, Path(args.tmp_root), logger)
            mark_done(job_root, worker_email, processing_job, payload, report, logger)
            processed_count += 1
            update_status(job_root, worker_email, status="done", current_segment_id="", last_segment_id=segment_id, processed_count=processed_count, failed_count=failed_count, last_message="segment done")
        except KeyboardInterrupt as exc:
            failed_count += 1
            try:
                mark_failed(job_root, worker_email, processing_job, payload, exc, logger, error_kind="keyboard_interrupt")
            except OSError as oserr:
                logger.log("FAILED", "mark_failed drive write failed", error=repr(oserr))
            logger.log("EXIT", "keyboard interrupt", reason="KeyboardInterrupt", processed_count=processed_count, failed_count=failed_count, runtime_seconds=int(time.time() - started_at))
            try:
                update_status(job_root, worker_email, status="exited", current_segment_id="", last_segment_id=segment_id, processed_count=processed_count, failed_count=failed_count, last_message="KeyboardInterrupt", last_error=repr(exc))
            except OSError:
                pass
            return 130
        except DriveUnavailable as exc:
            logger.exception("FAILED_TRANSIENT", "drive unavailable; leaving segment in processing for watcher reclaim", exc)
            try:
                update_status(
                    job_root,
                    worker_email,
                    status="drive_unavailable",
                    current_segment_id=segment_id,
                    last_segment_id=segment_id,
                    processed_count=processed_count,
                    failed_count=failed_count,
                    last_message="drive unavailable; segment left in processing",
                    last_error=repr(exc),
                    drive_error_errno=exc.errno,
                )
            except OSError:
                pass
            logger.log(
                "EXIT",
                "drive unavailable transient exit",
                reason="drive_unavailable",
                errno=exc.errno,
                processed_count=processed_count,
                failed_count=failed_count,
                runtime_seconds=int(time.time() - started_at),
            )
            return 75
        except InputAssetMissing as exc:
            failed_count += 1
            try:
                mark_failed(
                    job_root,
                    worker_email,
                    processing_job,
                    payload,
                    exc,
                    logger,
                    error_kind="input_asset_missing",
                    extra_payload={
                        "missing_frames": exc.missing_frames,
                        "missing_frames_count": len(exc.missing_frames),
                        "frame_start_index": payload.get("frame_start_index"),
                        "frame_end_index": payload.get("frame_end_index"),
                        "asset_error_details": exc.details,
                        "permanent_failure": True,
                    },
                )
            except OSError as oserr:
                logger.log("FAILED", "mark_failed drive write failed", error=repr(oserr))
            try:
                update_status(
                    job_root,
                    worker_email,
                    status="failed",
                    current_segment_id="",
                    last_segment_id=segment_id,
                    processed_count=processed_count,
                    failed_count=failed_count,
                    last_message=f"input_asset_missing (missing_frames={len(exc.missing_frames)})",
                    last_error=str(exc),
                )
            except OSError:
                pass
        except OSError as exc:
            if _classify_oserror(exc) == "drive_unavailable":
                logger.exception("FAILED_TRANSIENT", "OSError classified as drive_unavailable; leaving processing for reclaim", exc)
                try:
                    update_status(
                        job_root,
                        worker_email,
                        status="drive_unavailable",
                        current_segment_id=segment_id,
                        last_segment_id=segment_id,
                        processed_count=processed_count,
                        failed_count=failed_count,
                        last_message="drive unavailable OSError",
                        last_error=repr(exc),
                        drive_error_errno=getattr(exc, "errno", None),
                    )
                except OSError:
                    pass
                return 75
            failed_count += 1
            try:
                mark_failed(job_root, worker_email, processing_job, payload, exc, logger, error_kind="io_error")
            except OSError as oserr:
                logger.log("FAILED", "mark_failed drive write failed", error=repr(oserr))
            try:
                update_status(job_root, worker_email, status="failed", current_segment_id="", last_segment_id=segment_id, processed_count=processed_count, failed_count=failed_count, last_message="segment failed (io_error)", last_error=repr(exc))
            except OSError:
                pass
        except Exception as exc:
            failed_count += 1
            try:
                mark_failed(job_root, worker_email, processing_job, payload, exc, logger, error_kind="runtime_error")
            except OSError as oserr:
                logger.log("FAILED", "mark_failed drive write failed", error=repr(oserr))
            try:
                update_status(job_root, worker_email, status="failed", current_segment_id="", last_segment_id=segment_id, processed_count=processed_count, failed_count=failed_count, last_message="segment failed", last_error=repr(exc))
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
