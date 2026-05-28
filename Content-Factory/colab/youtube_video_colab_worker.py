"""Portable Google Colab worker for Content-Factory YouTube video segments.

Usage in Colab after mounting Drive and uploading this file:
python /content/youtube_video_colab_worker.py --drive-root "/content/drive/MyDrive/ContentFactory_YouTube" --story-slug "Story_Slug" --worker-id "worker_01"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FPS = 24
WIDTH = 1920
HEIGHT = 1080
UPSCALE_H = 2160
ZOOM_AMOUNT = 0.20
CRF = 24
PRESET = "medium"
GRAIN_STRENGTH = 15
MIN_VIDEO_SIZE_BYTES = 16 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_cmd(cmd: list[str], log_path: Path) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("COMMAND:\n" + " ".join(cmd) + "\n\nSTDERR_STDOUT:\n" + output, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {output[-5000:]}")


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


def render_clip(image: Path, output: Path, duration: float, zoom_in: bool, log_path: Path) -> None:
    frames = max(2, int(math.ceil(duration * FPS)))
    denom = max(1, frames - 1)
    if zoom_in:
        z_expr = f"1+{ZOOM_AMOUNT}*on/{denom}"
    else:
        z_expr = f"{1 + ZOOM_AMOUNT}-{ZOOM_AMOUNT}*on/{denom}"
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
        PRESET,
        "-crf",
        str(CRF),
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(partial),
    ]
    run_cmd(cmd, log_path)
    if not valid_video(partial, duration):
        raise RuntimeError(f"clip failed validation: {partial}")
    partial.replace(output)


def concat_videos(parts: list[Path], output: Path, log_path: Path) -> None:
    if not parts:
        raise RuntimeError("No clip parts to concat")
    if len(parts) == 1:
        shutil.copy2(parts[0], output)
        return
    list_path = output.parent / f"{output.stem}.concat.txt"
    lines = []
    for part in parts:
        safe = part.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(partial)]
    run_cmd(cmd, log_path)
    partial.replace(output)


def apply_optional_effects(video: Path, output: Path, effects_dir: Path, log_path: Path) -> dict[str, Any]:
    film = effects_dir / "film.mp4"
    dust = effects_dir / "dust.mp4"
    has_film = film.is_file()
    has_dust = dust.is_file()
    if not has_film and not has_dust and GRAIN_STRENGTH <= 0:
        shutil.copy2(video, output)
        return {"film": False, "dust": False, "grain": False}

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
    if GRAIN_STRENGTH > 0:
        filters.append(f"[{current}]noise=alls={GRAIN_STRENGTH}:allf=t+u[vout]")
    else:
        filters.append(f"[{current}]null[vout]")

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
            PRESET,
            "-crf",
            str(CRF),
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(partial),
        ]
    )
    run_cmd(cmd, log_path)
    partial.replace(output)
    return {"film": has_film, "dust": has_dust, "grain": GRAIN_STRENGTH > 0}


def update_worker_status(job_root: Path, worker_id: str, **payload: Any) -> None:
    status = {"worker_id": worker_id, "updated_at": utc_now(), **payload}
    write_json(job_root / "status" / f"COLAB_WORKER_STATUS_{worker_id}.json", status)


def claim_job(job_root: Path, worker_id: str) -> tuple[Path | None, Path | None, Path | None]:
    pending_dir = job_root / "segments" / "pending"
    processing_dir = job_root / "segments" / "processing"
    locks_dir = job_root / "locks"
    processing_dir.mkdir(parents=True, exist_ok=True)
    locks_dir.mkdir(parents=True, exist_ok=True)
    for pending in sorted(pending_dir.glob("segment_*.json")):
        segment_id = pending.stem
        lock_dir = locks_dir / f"{segment_id}.lock"
        try:
            os.mkdir(lock_dir)
        except FileExistsError:
            continue
        processing = processing_dir / f"{segment_id}__{worker_id}.json"
        try:
            pending.replace(processing)
        except FileNotFoundError:
            shutil.rmtree(lock_dir, ignore_errors=True)
            continue
        write_json(lock_dir / "lock.json", {"segment_id": segment_id, "worker_id": worker_id, "claimed_at": utc_now(), "state": "processing"})
        return processing, lock_dir, pending
    return None, None, None


def render_segment(job_root: Path, processing_json: Path, lock_dir: Path, worker_id: str) -> dict[str, Any]:
    job = read_json(processing_json)
    segment_id = str(job["segment_id"])
    tmp_root = Path("/content/tmp/content_factory_video") / worker_id / segment_id
    clips_dir = tmp_root / "clips"
    logs_dir = tmp_root / "logs"
    clips_dir.mkdir(parents=True, exist_ok=True)
    done_dir = job_root / "segments" / "done"
    reports_dir = job_root / "reports"
    done_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output = done_dir / f"{segment_id}.mp4"

    started_at = utc_now()
    clip_paths: list[Path] = []
    for idx, frame in enumerate(job.get("frames", [])):
        image = job_root / str(frame["input_frame_path"])
        duration = max(0.001, float(frame.get("duration_sec") or 0.0))
        clip = clips_dir / f"clip_{idx:04d}.mp4"
        clip_paths.append(clip)
        if not valid_video(clip, duration):
            render_clip(image, clip, duration, bool(frame.get("zoom_in", True)), logs_dir / f"clip_{idx:04d}.ffmpeg.log")

    concat_video = tmp_root / f"{segment_id}.raw.mp4"
    expected_duration = float(job.get("expected_duration_sec") or job.get("duration_sec") or 0.0)
    if not valid_video(concat_video, expected_duration):
        concat_videos(clip_paths, concat_video, logs_dir / "concat.ffmpeg.log")

    final_tmp = tmp_root / f"{segment_id}.final.mp4"
    effects = apply_optional_effects(concat_video, final_tmp, job_root / "input" / "effects", logs_dir / "effects.ffmpeg.log")
    if not valid_video(final_tmp, expected_duration):
        raise RuntimeError(f"final segment failed validation: {final_tmp}")
    final_partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    shutil.copy2(final_tmp, final_partial)
    final_partial.replace(output)

    report = {
        "schema_version": 1,
        "status": "done",
        "worker_id": worker_id,
        "segment_id": segment_id,
        "started_at": started_at,
        "finished_at": utc_now(),
        "output_path": str(output),
        "expected_duration_sec": expected_duration,
        "actual_duration_sec": media_duration(output),
        "effects": effects,
    }
    write_json(reports_dir / f"{segment_id}__{worker_id}.json", report)
    lock = read_json(lock_dir / "lock.json")
    lock.update({"state": "done", "done_at": utc_now()})
    write_json(lock_dir / "lock.json", lock)
    processing_json.unlink(missing_ok=True)
    shutil.rmtree(tmp_root, ignore_errors=True)
    return report


def mark_failed(job_root: Path, processing_json: Path, lock_dir: Path | None, worker_id: str, error: str) -> None:
    segment_id = processing_json.name.split("__", 1)[0]
    failed_dir = job_root / "segments" / "failed"
    reports_dir = job_root / "reports"
    failed_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = {}
    if processing_json.is_file():
        try:
            payload = read_json(processing_json)
        except Exception:
            payload = {}
    payload.update({"status": "failed", "worker_id": worker_id, "failed_at": utc_now(), "error": error})
    write_json(failed_dir / f"{segment_id}.json", payload)
    write_json(reports_dir / f"{segment_id}__{worker_id}_error.json", payload)
    processing_json.unlink(missing_ok=True)
    if lock_dir is not None and (lock_dir / "lock.json").is_file():
        lock = read_json(lock_dir / "lock.json")
        lock.update({"state": "failed", "failed_at": utc_now(), "error": error})
        write_json(lock_dir / "lock.json", lock)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drive-root", required=True)
    parser.add_argument("--story-slug", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--max-segments", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--idle-timeout-min", type=float, default=15.0)
    args = parser.parse_args()

    job_root = Path(args.drive_root) / "video_jobs" / args.story_slug
    idle_deadline = time.time() + max(1.0, args.idle_timeout_min) * 60
    processed = 0
    while not (job_root / "VIDEO_JOB_READY.json").is_file():
        update_worker_status(job_root, args.worker_id, state="waiting_for_ready", processed=processed)
        if time.time() > idle_deadline:
            update_worker_status(job_root, args.worker_id, state="idle_timeout_waiting_for_ready", processed=processed)
            return 2
        time.sleep(max(1.0, args.poll_seconds))

    while True:
        if args.max_segments and processed >= args.max_segments:
            update_worker_status(job_root, args.worker_id, state="max_segments_reached", processed=processed)
            return 0
        update_worker_status(job_root, args.worker_id, state="claiming", processed=processed)
        processing_json, lock_dir, _pending = claim_job(job_root, args.worker_id)
        if processing_json is None:
            update_worker_status(job_root, args.worker_id, state="done_no_pending", processed=processed)
            return 0
        try:
            update_worker_status(job_root, args.worker_id, state="rendering", segment=processing_json.name, processed=processed)
            report = render_segment(job_root, processing_json, lock_dir, args.worker_id)
            processed += 1
            update_worker_status(job_root, args.worker_id, state="segment_done", segment=report["segment_id"], processed=processed)
        except Exception as exc:
            error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            mark_failed(job_root, processing_json, lock_dir, args.worker_id, error)
            update_worker_status(job_root, args.worker_id, state="segment_failed", segment=processing_json.name, error=error, processed=processed)


if __name__ == "__main__":
    raise SystemExit(main())
