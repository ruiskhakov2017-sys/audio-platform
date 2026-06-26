"""YouTube video segment MVP built as a safe facade over AutoVideo ideas.

This module intentionally does not import legacy/AutoVideo/main.py: that file has
global runtime defaults for Colab paths. The helpers below mirror the small,
side-effect-free ffprobe/ffmpeg/validation patterns needed for segment MVP.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
FPS = 24
WIDTH = 1920
HEIGHT = 1080
UPSCALE_H = 2160
ZOOM_AMOUNT = 0.20
CRF = 24
PRESET = "medium"
MIN_VIDEO_SIZE_BYTES = 16 * 1024
DURATION_TOLERANCE_SEC = 2.5
BOUNDARY_EPSILON_SEC = 0.001
RENDER_MODE_VIDEO_ONLY = "video_only"


@dataclass
class YoutubeVideoPrepareSegmentsOptions:
    story_id: str
    segment_sec: float = 180.0
    execute: bool = False
    force: bool = False


@dataclass
class YoutubeVideoRenderSegmentOptions:
    story_id: str
    segment_id: str
    execute: bool = False


@dataclass
class YoutubeVideoSegmentStatusOptions:
    story_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    from orchestrator.youtube_path_resolver import resolve_youtube_technical_story_dir

    return resolve_youtube_technical_story_dir(config, story_id)


def _video_dirs(story_dir: Path) -> dict[str, Path]:
    root = story_dir / "08_video"
    return {
        "root": root,
        "manifests": root / "manifests",
        "segments": root / "segments",
        "logs": root / "logs",
        "reports": root / "reports",
    }


def _audio_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def _frames_dir(story_dir: Path) -> Path:
    return story_dir / "07_frames"


def _timeline_path(story_dir: Path) -> Path:
    return _video_dirs(story_dir)["manifests"] / "video_timeline.json"


def _segment_jobs_path(story_dir: Path) -> Path:
    return _video_dirs(story_dir)["manifests"] / "segment_jobs.json"


def _collect_frames(frames_dir: Path) -> list[Path]:
    if not frames_dir.is_dir():
        return []
    return sorted(p for p in frames_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def get_media_duration(media_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}: {(proc.stderr or '').strip()}")
    try:
        return float((proc.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned invalid duration for {media_path}: {proc.stdout!r}") from exc


def _has_stream(media_path: Path, stream_selector: str) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        stream_selector,
        "-show_entries",
        "stream=codec_name",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def is_valid_video_file(
    path: Path,
    *,
    expected_duration_sec: float | None = None,
    duration_tolerance_sec: float = DURATION_TOLERANCE_SEC,
    require_audio: bool = False,
    min_size_bytes: int = MIN_VIDEO_SIZE_BYTES,
) -> tuple[bool, dict[str, Any]]:
    details: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "min_size_bytes": min_size_bytes,
        "expected_duration_sec": expected_duration_sec,
        "duration_tolerance_sec": duration_tolerance_sec,
        "require_audio": require_audio,
    }
    if not path.exists():
        details["reason"] = "missing"
        return False, details
    try:
        size = path.stat().st_size
    except OSError as exc:
        details["reason"] = f"stat_failed: {exc}"
        return False, details
    details["size_bytes"] = size
    if size < min_size_bytes:
        details["reason"] = "too_small"
        return False, details
    if not _has_stream(path, "v:0"):
        details["reason"] = "missing_video_stream"
        return False, details
    if require_audio and not _has_stream(path, "a:0"):
        details["reason"] = "missing_audio_stream"
        return False, details
    try:
        actual_duration = get_media_duration(path)
    except RuntimeError as exc:
        details["reason"] = f"duration_probe_failed: {exc}"
        return False, details
    details["actual_duration_sec"] = actual_duration
    if expected_duration_sec is not None:
        delta = abs(actual_duration - expected_duration_sec)
        details["duration_delta_sec"] = delta
        if delta > duration_tolerance_sec:
            details["reason"] = "duration_mismatch"
            return False, details
    details["reason"] = "ok"
    return True, details


def _run_ffmpeg(cmd: list[str], *, log_path: Path | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "COMMAND:\n"
            + " ".join(cmd)
            + "\n\nSTDERR_STDOUT:\n"
            + combined,
            encoding="utf-8",
        )
    if proc.returncode != 0:
        tail = combined[-5000:] if combined else "(no ffmpeg output)"
        raise RuntimeError(f"ffmpeg failed, log={log_path}\n{tail}")


def run_ffmpeg_safe(cmd: list[str], final_output: Path, *, partial_output: Path, log_path: Path) -> None:
    cmd2 = list(cmd)
    cmd2[-1] = str(partial_output)
    _run_ffmpeg(cmd2, log_path=log_path)
    valid, details = is_valid_video_file(partial_output, require_audio=False)
    if not valid:
        raise RuntimeError(f"partial output failed validation: {json.dumps(details, ensure_ascii=True)}")
    partial_output.replace(final_output)


def _render_clip(image: Path, output: Path, duration: float, *, zoom_in: bool, log_path: Path) -> None:
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
        str(output),
    ]
    run_ffmpeg_safe(cmd, output, partial_output=partial, log_path=log_path)


def concat_copy_mp4(parts: list[Path], output: Path, *, log_path: Path) -> None:
    if not parts:
        raise ValueError("No clip parts to concat")
    if len(parts) == 1:
        shutil.copyfile(parts[0], output)
        return
    list_path = output.parent / "concat_list.txt"
    lines = []
    for part in parts:
        safe_path = part.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output),
    ]
    run_ffmpeg_safe(cmd, output, partial_output=partial, log_path=log_path)


def _concat_video_only_segment(
    parts: list[Path],
    output: Path,
    *,
    partial_output: Path,
    expected_duration_sec: float,
    log_path: Path,
) -> dict[str, Any]:
    if not parts:
        raise ValueError("No clip parts to concat")
    if partial_output.exists():
        try:
            partial_output.unlink()
        except OSError:
            pass
    if len(parts) == 1:
        shutil.copyfile(parts[0], partial_output)
    else:
        list_path = partial_output.parent / f"{output.stem}.concat_list.txt"
        lines = []
        for part in parts:
            safe_path = part.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{safe_path}'")
        list_path.write_text("\n".join(lines), encoding="utf-8")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(partial_output),
        ]
        _run_ffmpeg(cmd, log_path=log_path)
    valid, details = is_valid_video_file(
        partial_output,
        expected_duration_sec=expected_duration_sec,
        require_audio=False,
    )
    if not valid:
        raise RuntimeError(f"video-only partial failed validation: {json.dumps(details, ensure_ascii=True)}")
    partial_output.replace(output)
    final_valid, final_details = is_valid_video_file(
        output,
        expected_duration_sec=expected_duration_sec,
        require_audio=False,
    )
    if not final_valid:
        raise RuntimeError(f"video-only output failed validation: {json.dumps(final_details, ensure_ascii=True)}")
    return final_details


def _build_frame_timeline(frames: list[Path], audio_duration: float) -> list[dict[str, Any]]:
    frame_duration = audio_duration / max(1, len(frames))
    frame_timeline: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        start = index * frame_duration
        end = audio_duration if index == len(frames) - 1 else (index + 1) * frame_duration
        frame_timeline.append(
            {
                "frame_index": index,
                "path": str(frame),
                "name": frame.name,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(max(0.001, end - start), 3),
            }
        )
    return frame_timeline


def _pick_nearest_frame_boundary(
    *,
    frame_boundaries: list[float],
    segment_start: float,
    target_end: float,
    audio_duration: float,
) -> float:
    if target_end >= audio_duration:
        return audio_duration
    candidates = [b for b in frame_boundaries if b > segment_start + BOUNDARY_EPSILON_SEC and b < audio_duration]
    if not candidates:
        return audio_duration
    return min(candidates, key=lambda b: (abs(b - target_end), b < target_end, b))


def _build_segment_jobs_from_frame_timeline(
    *,
    story_id: str,
    dirs: dict[str, Path],
    frame_timeline: list[dict[str, Any]],
    audio_duration: float,
    segment_target_sec: float,
) -> list[dict[str, Any]]:
    frame_boundaries = [float(frame["end_sec"]) for frame in frame_timeline]
    jobs: list[dict[str, Any]] = []
    rules = {
        "min_size_bytes": MIN_VIDEO_SIZE_BYTES,
        "duration_tolerance_sec": DURATION_TOLERANCE_SEC,
        "video_stream_required": True,
        "audio_stream_required": False,
        "render_mode": RENDER_MODE_VIDEO_ONLY,
        "resolution": f"{WIDTH}x{HEIGHT}",
        "fps": FPS,
        "codec": "libx264",
        "pix_fmt": "yuv420p",
        "crf": CRF,
        "preset": PRESET,
    }

    segment_start = 0.0
    segment_index = 0
    while segment_start < audio_duration - BOUNDARY_EPSILON_SEC:
        target_end = segment_start + segment_target_sec
        segment_end = _pick_nearest_frame_boundary(
            frame_boundaries=frame_boundaries,
            segment_start=segment_start,
            target_end=target_end,
            audio_duration=audio_duration,
        )
        if segment_end <= segment_start + BOUNDARY_EPSILON_SEC:
            segment_end = audio_duration

        segment_frames: list[dict[str, Any]] = []
        for frame in frame_timeline:
            frame_start = float(frame["start_sec"])
            frame_end = float(frame["end_sec"])
            if frame_start + BOUNDARY_EPSILON_SEC < segment_start:
                continue
            if frame_end > segment_end + BOUNDARY_EPSILON_SEC:
                continue
            segment_frames.append(
                {
                    "frame_index": frame["frame_index"],
                    "path": frame["path"],
                    "name": frame["name"],
                    "global_start_sec": round(frame_start, 3),
                    "global_end_sec": round(frame_end, 3),
                    "segment_start_sec": round(frame_start - segment_start, 3),
                    "segment_end_sec": round(frame_end - segment_start, 3),
                    "duration_sec": round(max(0.001, frame_end - frame_start), 3),
                    "zoom_in": bool(int(frame["frame_index"]) % 2 == 0),
                }
            )

        if not segment_frames:
            # A single frame can be longer than target_sec; assign the next frame whole.
            next_frame = next(
                frame for frame in frame_timeline if float(frame["end_sec"]) > segment_start + BOUNDARY_EPSILON_SEC
            )
            segment_end = float(next_frame["end_sec"])
            segment_frames.append(
                {
                    "frame_index": next_frame["frame_index"],
                    "path": next_frame["path"],
                    "name": next_frame["name"],
                    "global_start_sec": round(float(next_frame["start_sec"]), 3),
                    "global_end_sec": round(float(next_frame["end_sec"]), 3),
                    "segment_start_sec": 0.0,
                    "segment_end_sec": round(float(next_frame["end_sec"]) - segment_start, 3),
                    "duration_sec": round(max(0.001, float(next_frame["end_sec"]) - float(next_frame["start_sec"])), 3),
                    "zoom_in": bool(int(next_frame["frame_index"]) % 2 == 0),
                }
            )

        duration = max(0.001, segment_end - segment_start)
        segment_id = f"segment_{segment_index + 1:04d}"
        jobs.append(
            {
                "story_id": story_id,
                "segment_id": segment_id,
                "segment_index": segment_index,
                "render_mode": RENDER_MODE_VIDEO_ONLY,
                "start_sec": round(segment_start, 3),
                "end_sec": round(segment_end, 3),
                "duration_sec": round(duration, 3),
                "target_duration_sec": round(segment_target_sec, 3),
                "boundary_policy": "nearest_frame_end_to_target",
                "frames": segment_frames,
                "frame_start_index": segment_frames[0]["frame_index"],
                "frame_end_index": segment_frames[-1]["frame_index"],
                "output_segment_path": str(dirs["segments"] / f"{segment_id}.mp4"),
                "expected_duration_sec": round(duration, 3),
                "status": "pending",
                "validation_rules": rules,
                "colab_worker_contract": {
                    "render_in_tmp": True,
                    "tmp_root": "/content/tmp/content_factory_video",
                    "copy_to_drive_after_ffprobe": True,
                    "final_segment_is_video_only": True,
                },
            }
        )
        segment_start = segment_end
        segment_index += 1

    return jobs


def _build_plan(config: OrchestratorConfig, story_id: str, segment_sec: float) -> tuple[dict[str, Any] | None, str]:
    story_key = str(story_id).strip()
    if not story_key:
        return None, "Нужен --story-id"
    story_dir = _story_dir(config, story_key)
    audio_path = _audio_path(story_dir)
    frames_dir = _frames_dir(story_dir)
    frames = _collect_frames(frames_dir)
    missing: list[str] = []
    if not story_dir.is_dir():
        missing.append(str(story_dir))
    if not audio_path.is_file():
        missing.append(str(audio_path))
    if not frames_dir.is_dir():
        missing.append(str(frames_dir))
    if frames_dir.is_dir() and not frames:
        missing.append(f"{frames_dir}/*.png|jpg|jpeg|webp")
    if missing:
        return None, "Missing inputs: " + ", ".join(missing)

    audio_duration = get_media_duration(audio_path)
    seg_len = max(1.0, float(segment_sec or 180.0))
    frame_timeline = _build_frame_timeline(frames, audio_duration)
    dirs = _video_dirs(story_dir)
    jobs = _build_segment_jobs_from_frame_timeline(
        story_id=story_key,
        dirs=dirs,
        frame_timeline=frame_timeline,
        audio_duration=audio_duration,
        segment_target_sec=seg_len,
    )

    return (
        {
            "story_id": story_key,
            "story_dir": str(story_dir),
            "audio_path": str(audio_path),
            "frames_dir": str(frames_dir),
            "audio_duration_sec": round(audio_duration, 3),
            "segment_sec": seg_len,
            "segment_target_sec": seg_len,
            "segment_boundary_policy": "nearest_frame_end_to_target",
            "render_mode": RENDER_MODE_VIDEO_ONLY,
            "total_segments": len(jobs),
            "total_frames": len(frames),
            "total_segment_duration_sec": round(sum(float(job["duration_sec"]) for job in jobs), 3),
            "timeline_path": str(_timeline_path(story_dir)),
            "segment_jobs_path": str(_segment_jobs_path(story_dir)),
            "segments_dir": str(dirs["segments"]),
            "logs_dir": str(dirs["logs"]),
            "reports_dir": str(dirs["reports"]),
            "frame_timeline": frame_timeline,
            "jobs": jobs,
        },
        "",
    )


def run_youtube_video_prepare_segments(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoPrepareSegmentsOptions,
) -> dict[str, Any]:
    plan, error = _build_plan(config, options.story_id, options.segment_sec)
    if error:
        return {"ok": False, "message": error, "execute": bool(options.execute)}
    assert plan is not None
    timeline_path = Path(str(plan["timeline_path"]))
    segment_jobs_path = Path(str(plan["segment_jobs_path"]))
    manifests_exist = timeline_path.is_file() and segment_jobs_path.is_file()

    if manifests_exist and not options.force:
        existing_jobs = _read_json(segment_jobs_path)
        existing_total = len(existing_jobs.get("jobs", [])) if isinstance(existing_jobs, dict) and isinstance(existing_jobs.get("jobs"), list) else 0
        return {
            "ok": True,
            "status": "already_prepared",
            "execute": bool(options.execute),
            **{k: v for k, v in plan.items() if k not in {"frame_timeline", "jobs"}},
            "total_segments": existing_total or plan["total_segments"],
            "force": bool(options.force),
        }

    if not options.execute:
        return {
            "ok": True,
            "status": "dry_run",
            "execute": False,
            **{k: v for k, v in plan.items() if k not in {"frame_timeline", "jobs"}},
            "first_5_segments": plan["jobs"][:5],
            "force": bool(options.force),
        }

    story_dir = Path(str(plan["story_dir"]))
    dirs = _video_dirs(story_dir)
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    created_at = _now_iso()
    timeline = {
        "schema_version": 1,
        "created_at": created_at,
        "story_id": plan["story_id"],
        "story_dir": plan["story_dir"],
        "audio_path": plan["audio_path"],
        "audio_duration_sec": plan["audio_duration_sec"],
        "frames_dir": plan["frames_dir"],
        "total_frames": plan["total_frames"],
        "segment_sec": plan["segment_sec"],
        "segment_target_sec": plan["segment_target_sec"],
        "segment_boundary_policy": plan["segment_boundary_policy"],
        "render_mode": plan["render_mode"],
        "total_segments": plan["total_segments"],
        "total_segment_duration_sec": plan["total_segment_duration_sec"],
        "frame_timeline": plan["frame_timeline"],
    }
    segment_jobs = {
        "schema_version": 1,
        "created_at": created_at,
        "story_id": plan["story_id"],
        "story_dir": plan["story_dir"],
        "audio_path": plan["audio_path"],
        "audio_duration_sec": plan["audio_duration_sec"],
        "segment_sec": plan["segment_sec"],
        "segment_target_sec": plan["segment_target_sec"],
        "segment_boundary_policy": plan["segment_boundary_policy"],
        "render_mode": plan["render_mode"],
        "total_segments": plan["total_segments"],
        "total_segment_duration_sec": plan["total_segment_duration_sec"],
        "jobs": plan["jobs"],
    }
    _write_json(timeline_path, timeline)
    _write_json(segment_jobs_path, segment_jobs)
    return {
        "ok": True,
        "status": "prepared",
        "execute": True,
        **{k: v for k, v in plan.items() if k not in {"frame_timeline", "jobs"}},
        "first_5_segments": plan["jobs"][:5],
        "force": bool(options.force),
    }


def _load_segment_jobs(config: OrchestratorConfig, story_id: str) -> tuple[Path, dict[str, Any] | None, str]:
    story_dir = _story_dir(config, story_id)
    path = _segment_jobs_path(story_dir)
    if not path.is_file():
        return path, None, f"segment_jobs.json не найден: {path}"
    data = _read_json(path)
    if not isinstance(data, dict):
        return path, None, f"segment_jobs.json имеет неверный формат: {path}"
    return path, data, ""


def _find_job(segment_jobs: dict[str, Any], segment_id: str) -> dict[str, Any] | None:
    for job in segment_jobs.get("jobs", []) if isinstance(segment_jobs.get("jobs"), list) else []:
        if isinstance(job, dict) and str(job.get("segment_id", "")).strip() == segment_id:
            return job
    return None


def _save_job_status(segment_jobs_path: Path, segment_jobs: dict[str, Any], segment_id: str, patch: dict[str, Any]) -> None:
    jobs = segment_jobs.get("jobs", [])
    if not isinstance(jobs, list):
        return
    for job in jobs:
        if isinstance(job, dict) and str(job.get("segment_id", "")) == segment_id:
            job.update(patch)
            break
    segment_jobs["updated_at"] = _now_iso()
    _write_json(segment_jobs_path, segment_jobs)


def _mux_audio_segment(
    *,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    partial_path: Path,
    start_sec: float,
    duration_sec: float,
    log_path: Path,
) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(output_path),
    ]
    cmd[-1] = str(partial_path)
    _run_ffmpeg(cmd, log_path=log_path)
    valid, details = is_valid_video_file(
        partial_path,
        expected_duration_sec=duration_sec,
        require_audio=True,
    )
    if not valid:
        raise RuntimeError(f"final partial failed validation: {json.dumps(details, ensure_ascii=True)}")
    partial_path.replace(output_path)


def run_youtube_video_render_segment(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoRenderSegmentOptions,
) -> dict[str, Any]:
    story_key = str(options.story_id).strip()
    segment_key = str(options.segment_id).strip()
    if not story_key or not segment_key:
        return {"ok": False, "message": "Нужны --story-id и --segment-id", "execute": bool(options.execute)}
    segment_jobs_path, segment_jobs, error = _load_segment_jobs(config, story_key)
    if error:
        return {"ok": False, "message": error, "execute": bool(options.execute)}
    assert segment_jobs is not None
    job = _find_job(segment_jobs, segment_key)
    if job is None:
        return {"ok": False, "message": f"segment_id не найден: {segment_key}", "execute": bool(options.execute)}

    output_path = Path(str(job.get("output_segment_path", ""))).resolve()
    expected_duration = float(job.get("expected_duration_sec") or job.get("duration_sec") or 0.0)
    validation_rules = job.get("validation_rules") if isinstance(job.get("validation_rules"), dict) else {}
    require_audio = bool(validation_rules.get("audio_stream_required", True))
    render_mode = str(job.get("render_mode") or validation_rules.get("render_mode") or "").strip() or "audio_segment"
    valid, validation = is_valid_video_file(
        output_path,
        expected_duration_sec=expected_duration,
        require_audio=require_audio,
    )
    if valid:
        return {
            "ok": True,
            "status": "already_done",
            "execute": bool(options.execute),
            "story_id": story_key,
            "segment_id": segment_key,
            "output_segment_path": str(output_path),
            "validation": validation,
        }

    story_dir = _story_dir(config, story_key)
    dirs = _video_dirs(story_dir)
    audio_path = Path(str(segment_jobs.get("audio_path") or _audio_path(story_dir))).resolve()
    frames = [f for f in job.get("frames", []) if isinstance(f, dict)]
    if not frames:
        return {"ok": False, "message": f"У сегмента нет frames: {segment_key}", "execute": bool(options.execute)}
    plan = {
        "story_id": story_key,
        "segment_id": segment_key,
        "start_sec": job.get("start_sec"),
        "end_sec": job.get("end_sec"),
        "duration_sec": job.get("duration_sec"),
        "render_mode": render_mode,
        "frames_count": len(frames),
        "audio_path": str(audio_path),
        "audio_required": require_audio,
        "output_segment_path": str(output_path),
        "partial_output_path": str(output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")),
        "current_validation": validation,
    }
    if not options.execute:
        return {"ok": True, "status": "dry_run", "execute": False, **plan}

    t0 = time.time()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dirs["logs"].mkdir(parents=True, exist_ok=True)
    dirs["reports"].mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / "_work" / segment_key
    work_dir.mkdir(parents=True, exist_ok=True)
    render_log_path = dirs["logs"] / f"{segment_key}_render_log.json"
    ffmpeg_log_dir = dirs["logs"] / segment_key
    partial_output = output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
    events: list[dict[str, Any]] = []

    def add_event(event: str, **payload: Any) -> None:
        events.append({"ts": _now_iso(), "event": event, **payload})
        _write_json(render_log_path, {"schema_version": 1, "story_id": story_key, "segment_id": segment_key, "events": events})

    try:
        _save_job_status(
            segment_jobs_path,
            segment_jobs,
            segment_key,
            {"status": "processing", "started_at": _now_iso(), "last_error": ""},
        )
        add_event("render_start", **plan)
        clip_paths: list[Path] = []
        for idx, frame in enumerate(frames):
            image_path = Path(str(frame.get("path", ""))).resolve()
            duration = max(0.001, float(frame.get("duration_sec") or 0.0))
            clip_path = work_dir / f"clip_{idx:04d}.mp4"
            clip_paths.append(clip_path)
            if is_valid_video_file(clip_path, expected_duration_sec=duration, require_audio=False)[0]:
                add_event("clip_skip_valid", clip_index=idx, image_path=str(image_path), clip_path=str(clip_path))
                continue
            add_event("clip_render_start", clip_index=idx, image_path=str(image_path), duration_sec=duration)
            _render_clip(
                image_path,
                clip_path,
                duration,
                zoom_in=bool(frame.get("zoom_in", True)),
                log_path=ffmpeg_log_dir / f"clip_{idx:04d}.ffmpeg.log",
            )
            add_event("clip_render_done", clip_index=idx, clip_path=str(clip_path))

        concat_video = work_dir / f"{segment_key}.video.mp4"
        if not is_valid_video_file(concat_video, expected_duration_sec=expected_duration, require_audio=False)[0]:
            add_event("concat_start", clips=len(clip_paths), concat_video=str(concat_video))
            concat_copy_mp4(clip_paths, concat_video, log_path=ffmpeg_log_dir / "concat.ffmpeg.log")
            add_event("concat_done", concat_video=str(concat_video))

        if render_mode == RENDER_MODE_VIDEO_ONLY:
            add_event("video_only_finalize_start", partial_output_path=str(partial_output))
            final_validation = _concat_video_only_segment(
                [concat_video],
                output_path,
                partial_output=partial_output,
                expected_duration_sec=expected_duration,
                log_path=ffmpeg_log_dir / "video_only_finalize.ffmpeg.log",
            )
            final_valid = True
        else:
            add_event("mux_start", partial_output_path=str(partial_output))
            _mux_audio_segment(
                video_path=concat_video,
                audio_path=audio_path,
                output_path=output_path,
                partial_path=partial_output,
                start_sec=float(job.get("start_sec") or 0.0),
                duration_sec=expected_duration,
                log_path=ffmpeg_log_dir / "mux_audio.ffmpeg.log",
            )
            final_valid, final_validation = is_valid_video_file(
                output_path,
                expected_duration_sec=expected_duration,
                require_audio=require_audio,
            )
        if not final_valid:
            raise RuntimeError(f"final output failed validation: {json.dumps(final_validation, ensure_ascii=True)}")
        elapsed = round(time.time() - t0, 3)
        add_event("render_done", elapsed_sec=elapsed, validation=final_validation)
        _save_job_status(
            segment_jobs_path,
            segment_jobs,
            segment_key,
            {
                "status": "done",
                "done_at": _now_iso(),
                "failed_at": "",
                "elapsed_sec": elapsed,
                "actual_duration_sec": final_validation.get("actual_duration_sec"),
                "last_error": "",
            },
        )
        report_path = dirs["reports"] / f"{segment_key}_report.json"
        _write_json(report_path, {"schema_version": 1, "status": "done", **plan, "validation": final_validation})
        return {
            "ok": True,
            "status": "rendered",
            "execute": True,
            **plan,
            "render_log_path": str(render_log_path),
            "report_path": str(report_path),
            "validation": final_validation,
        }
    except Exception as exc:
        error_text = str(exc)
        add_event("render_failed", error=error_text, partial_output_path=str(partial_output))
        _save_job_status(
            segment_jobs_path,
            segment_jobs,
            segment_key,
            {"status": "failed", "failed_at": _now_iso(), "last_error": error_text},
        )
        report_path = dirs["reports"] / f"{segment_key}_report.json"
        _write_json(report_path, {"schema_version": 1, "status": "failed", **plan, "error": error_text})
        return {
            "ok": False,
            "status": "failed",
            "execute": True,
            "message": error_text,
            **plan,
            "render_log_path": str(render_log_path),
            "report_path": str(report_path),
        }


def run_youtube_video_segment_status(
    *,
    config: OrchestratorConfig,
    options: YoutubeVideoSegmentStatusOptions,
) -> dict[str, Any]:
    story_key = str(options.story_id).strip()
    segment_jobs_path, segment_jobs, error = _load_segment_jobs(config, story_key)
    if error:
        return {"ok": False, "message": error, "story_id": story_key, "segment_jobs_path": str(segment_jobs_path)}
    assert segment_jobs is not None
    jobs = [j for j in segment_jobs.get("jobs", []) if isinstance(j, dict)]
    done: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    invalid: list[dict[str, Any]] = []
    total_segment_duration = 0.0
    for job in jobs:
        segment_id = str(job.get("segment_id", ""))
        expected_duration = float(job.get("expected_duration_sec") or job.get("duration_sec") or 0.0)
        total_segment_duration += expected_duration
        output_path = Path(str(job.get("output_segment_path", ""))).resolve()
        validation_rules = job.get("validation_rules") if isinstance(job.get("validation_rules"), dict) else {}
        require_audio = bool(validation_rules.get("audio_stream_required", True))
        valid, details = is_valid_video_file(output_path, expected_duration_sec=expected_duration, require_audio=require_audio)
        if valid:
            done.append(segment_id)
            continue
        status = str(job.get("status", "pending"))
        if output_path.exists():
            invalid.append({"segment_id": segment_id, "reason": details.get("reason"), "path": str(output_path)})
        elif status == "failed":
            failed.append(segment_id)
        else:
            missing.append(segment_id)
            pending.append(segment_id)
    return {
        "ok": True,
        "story_id": story_key,
        "audio_duration_sec": segment_jobs.get("audio_duration_sec"),
        "total_segments": len(jobs),
        "done_segments": len(done),
        "pending_segments": len(pending),
        "failed_segments": len(failed),
        "missing_segments": len(missing),
        "invalid_segments": len(invalid),
        "total_segment_duration_sec": round(total_segment_duration, 3),
        "first_10_pending": pending[:10],
        "first_10_invalid": invalid[:10],
        "segment_jobs_path": str(segment_jobs_path),
    }
