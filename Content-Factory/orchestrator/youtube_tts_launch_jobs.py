"""Production YouTube TTS launch job contract for multi-worker Colab."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator import launch_contract
from orchestrator.launch_contract import story_slug

JOB_KIND = "youtube_tts_launch_partitioned_v1"
STATUS_FILE = "YOUTUBE_TTS_STATUS.json"


@dataclass(frozen=True)
class PrepareLaunchJobsOptions:
    youtube_run_id: str
    workers: int = 5
    execute: bool = False
    dry_run: bool = False
    retry_failed: bool = False
    force: bool = False
    account_all_stories: bool = False


@dataclass(frozen=True)
class TtsLaunchOptions:
    youtube_run_id: str
    workers: int = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_stem(value: str) -> str:
    return story_slug(value or "story")


def _resolve_input_text(manifest: dict[str, Any], story_dir: Path) -> Path:
    candidates: list[str] = []
    for container, key in (
        ("text_ready_for_audio", "path"),
        ("actual_artifacts", "text_ready_for_audio"),
        ("promo", "output_path"),
        ("expected_artifacts", "promo_text_ready_for_audio"),
    ):
        raw = manifest.get(container)
        if isinstance(raw, dict):
            value = str(raw.get(key) or "").strip()
            if value:
                candidates.append(value)
    candidates.extend(
        [
            str(story_dir / "03_promo" / "text_ready_for_audio.txt"),
            str(story_dir / "04_audio" / "tts_input" / "tts_input_with_promo.txt"),
            str(story_dir / "02_safe_story" / "safe_story.txt"),
        ]
    )
    raw_manifest = str(manifest.get("tts_input_path") or "").strip()
    if raw_manifest:
        candidates.append(raw_manifest)
    for raw in candidates:
        path = Path(raw)
        if path.is_file():
            return path
    return Path(candidates[0]) if candidates else story_dir / "03_promo" / "text_ready_for_audio.txt"


def _resolve_voice(manifest: dict[str, Any]) -> tuple[str, str]:
    for section_name in ("voice_contract", "tts_kokoro_colab"):
        section = manifest.get(section_name)
        if not isinstance(section, dict):
            continue
        voice = str(section.get("kokoro_voice") or section.get("youtube_voice_id") or "").strip()
        label = str(
            section.get("voice_label")
            or section.get("voice_type")
            or section.get("expected_gender")
            or ""
        ).strip().upper()[:1]
        if voice and label in {"M", "F", "U"}:
            return label, voice
    return "", ""


def _local_audio_path(manifest: dict[str, Any], story_dir: Path) -> Path:
    for section_name, key in (("tts_kokoro_colab", "audio_path"), ("audio", "path")):
        section = manifest.get(section_name)
        if isinstance(section, dict):
            raw = str(section.get(key) or "").strip()
            if raw:
                return Path(raw)
    return story_dir / "04_audio" / "narration.mp3"


def _audio_counts_as_done(audio_path: Path, *, manifest: dict[str, Any], story_dir: Path) -> bool:
    if not audio_path.is_file():
        return False
    if audio_path.stat().st_size < 32_000:
        return False
    try:
        from orchestrator.youtube_video_segments import get_media_duration

        duration = float(get_media_duration(audio_path))
    except Exception:
        return True
    if duration < 45.0:
        return False
    text_path = _resolve_input_text(manifest, story_dir)
    if text_path.is_file():
        import re

        words = len(re.findall(r"[A-Za-z]{2,}", text_path.read_text(encoding="utf-8", errors="replace")))
        if words >= 500 and duration < (words / 150 * 60 * 0.08):
            return False
    return True


def _story_state(manifest: dict[str, Any], local_audio: Path, drive_audio: Path, *, story_dir: Path | None = None) -> str:
    resolved_story_dir = story_dir or local_audio.parent.parent
    if _audio_counts_as_done(local_audio, manifest=manifest, story_dir=resolved_story_dir) or _audio_counts_as_done(
        drive_audio, manifest=manifest, story_dir=resolved_story_dir
    ):
        return "done"
    for section_name in ("tts_kokoro_colab", "audio"):
        section = manifest.get(section_name)
        if isinstance(section, dict):
            status = str(section.get("status") or "").strip().lower()
            if status in {"failed", "stale", "pending", "done", "running"}:
                return status
    return "pending"


def _load_story_rows(config: OrchestratorConfig, youtube_run_id: str, drive_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ctx = launch_contract.build_launch_context(config, launch_id=youtube_run_id)
    yt_root = ctx.youtube_root
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if not yt_root.is_dir():
        return [], [{"reason": "launch_youtube_root_missing", "path": str(yt_root)}]
    for manifest_path in sorted(yt_root.glob("*/youtube_story_manifest.json")):
        story_dir = manifest_path.parent
        try:
            manifest = _read_json(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append({"reason": "invalid_manifest", "path": str(manifest_path), "error": repr(exc)})
            continue
        if not isinstance(manifest, dict):
            skipped.append({"reason": "invalid_manifest_type", "path": str(manifest_path)})
            continue
        canonical = str(manifest.get("canonical_basename") or story_dir.name).strip() or story_dir.name
        sid = str(manifest.get("story_id") or canonical).strip() or canonical
        input_text = _resolve_input_text(manifest, story_dir)
        voice_label, kokoro_voice = _resolve_voice(manifest)
        stem = _safe_stem(canonical)
        drive_text = drive_root / "texts" / f"{stem}.txt"
        drive_audio = drive_root / "audio" / f"{stem}.mp3"
        local_audio = _local_audio_path(manifest, story_dir)
        state = _story_state(manifest, local_audio, drive_audio, story_dir=story_dir)
        problems: list[str] = []
        if not input_text.is_file():
            problems.append("missing_input_text")
        if not voice_label or not kokoro_voice:
            problems.append("missing_voice_contract")
        row = {
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "story_slug": story_slug(canonical),
            "canonical_basename": canonical,
            "story_manifest": str(manifest_path),
            "story_dir": str(story_dir),
            "source_text_path": str(input_text),
            "source_text_hash": _sha256_file(input_text),
            "drive_text_path": str(drive_text),
            "expected_drive_audio_path": str(drive_audio),
            "expected_local_audio_path": str(local_audio),
            "expected_output_filename": drive_audio.name,
            "voice_label": voice_label,
            "kokoro_voice": kokoro_voice,
            "speed": 0.92,
            "sample_rate": 24000,
            "status": state,
            "problems": problems,
        }
        if problems:
            skipped.append({"reason": ",".join(problems), **row})
            continue
        rows.append(row)
    return rows, skipped


def _eligible(
    rows: list[dict[str, Any]],
    *,
    retry_failed: bool,
    force: bool,
    account_all_stories: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    accounted_done: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "pending")
        if status == "done" and not force:
            if account_all_stories:
                accounted = dict(row)
                accounted["job_status"] = "already_done"
                accounted_done.append(accounted)
            else:
                skipped.append({"reason": "done_audio_exists", **row})
            continue
        if status == "failed" and not retry_failed and not force:
            skipped.append({"reason": "failed_requires_retry_failed", **row})
            continue
        selected.append(row)
    return selected, skipped, accounted_done


def _partition(items: list[dict[str, Any]], workers: int) -> list[list[dict[str, Any]]]:
    parts = [[] for _ in range(max(1, workers))]
    for idx, item in enumerate(items):
        parts[idx % len(parts)].append(item)
    return parts


def _job_item(row: dict[str, Any], *, worker_index: int | None = None) -> dict[str, Any]:
    item = dict(row)
    item.pop("problems", None)
    if worker_index is not None:
        item["worker_index"] = worker_index
    item.setdefault("job_status", "pending")
    item["kind"] = "youtube_tts_item_v1"
    return item


def _report_paths(config: OrchestratorConfig, mode: str) -> tuple[Path, Path]:
    root = config.root_dir / "reports" / "gemini_execution"
    base = root / f"YOUTUBE_TTS_LAUNCH_JOB_PREPARE_{mode.upper()}"
    return base.with_suffix(".json"), base.with_suffix(".md")


def _write_prepare_report(config: OrchestratorConfig, mode: str, payload: dict[str, Any]) -> None:
    json_path, md_path = _report_paths(config, mode)
    _write_json(json_path, payload)
    lines = [
        f"# YouTube TTS Launch Job Prepare ({mode})",
        "",
        f"- launch: `{payload.get('youtube_run_id')}`",
        f"- workers: `{payload.get('workers')}`",
        f"- eligible: `{payload.get('eligible_count')}`",
        f"- skipped: `{payload.get('skipped_count')}`",
        f"- job: `{payload.get('job_path')}`",
        "",
        "## Partitions",
    ]
    for part in payload.get("partitions", []) or []:
        lines.append(f"- worker_{part.get('worker_index')}: {part.get('count')} tasks")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_launch_jobs(config: OrchestratorConfig, options: PrepareLaunchJobsOptions) -> dict[str, Any]:
    workers = max(1, int(options.workers or 1))
    ctx = launch_contract.build_launch_context(config, launch_id=options.youtube_run_id)
    drive_root = ctx.drive_mirror_root
    rows, invalid = _load_story_rows(config, options.youtube_run_id, drive_root)
    selected, skipped_by_status, accounted_done = _eligible(
        rows,
        retry_failed=options.retry_failed,
        force=options.force,
        account_all_stories=bool(options.account_all_stories),
    )
    partitions = _partition(selected, workers)
    now = _now_iso()
    jobs_dir = drive_root / "jobs"
    partition_dir = jobs_dir / "partitions"
    payload_items = [_job_item(row) for row in selected] + [_job_item(row) for row in accounted_done]
    accounting = {
        "total_launch_stories": len(rows) + len(invalid),
        "accounted_for_tts": len(rows),
        "already_done": len(accounted_done),
        "pending_for_tts": len(selected),
        "skipped_invalid": len(invalid),
        "terminal_blocked": len(invalid),
        "status_skipped": len(skipped_by_status),
    }
    job_payload = {
        "version": 1,
        "kind": JOB_KIND,
        "youtube_run_id": options.youtube_run_id,
        "launch_root": str(ctx.launch_root),
        "drive_launch_root": str(drive_root),
        "workers": workers,
        "created_at": now,
        "accounting": accounting,
        "items": payload_items,
    }
    partition_reports: list[dict[str, Any]] = []
    for worker_index, items in enumerate(partitions):
        partition_reports.append(
            {
                "worker_index": worker_index,
                "path": str(partition_dir / f"worker_{worker_index}.json"),
                "count": len(items),
                "stories": [str(item.get("canonical_basename") or "") for item in items],
            }
        )
    report = {
        "ok": True,
        "execute": bool(options.execute),
        "youtube_run_id": options.youtube_run_id,
        "workers": workers,
        "launch_root": str(ctx.launch_root),
        "drive_launch_root": str(drive_root),
        "job_path": str(jobs_dir / "youtube_tts_job.json"),
        "eligible_count": len(selected),
        "accounted_count": len(payload_items),
        "already_done_count": len(accounted_done),
        "skipped_count": len(invalid) + len(skipped_by_status),
        "invalid_count": len(invalid),
        "status_skipped_count": len(skipped_by_status),
        "accounting": accounting,
        "partitions": partition_reports,
        "skipped": invalid + skipped_by_status,
        "accounted_done": accounted_done,
    }
    _write_prepare_report(config, "execute" if options.execute else "dry_run", report)
    if not options.execute:
        return report
    for folder in (jobs_dir, partition_dir, drive_root / "texts", drive_root / "audio", drive_root / "logs", drive_root / "done", drive_root / "failed"):
        folder.mkdir(parents=True, exist_ok=True)
    for row in selected:
        shutil.copy2(Path(str(row["source_text_path"])), Path(str(row["drive_text_path"])))
    _write_json(jobs_dir / "youtube_tts_job.json", job_payload)
    (jobs_dir / "EXPECTED_FILES.txt").write_text(
        "".join(f"{Path(str(row['expected_drive_audio_path'])).name}\n" for row in selected),
        encoding="utf-8",
    )
    (jobs_dir / "EXPECTED_COUNT.txt").write_text(f"{len(selected)}\n", encoding="utf-8")
    for worker_index, items in enumerate(partitions):
        _write_json(
            partition_dir / f"worker_{worker_index}.json",
            {
                "version": 1,
                "kind": f"{JOB_KIND}_partition",
                "youtube_run_id": options.youtube_run_id,
                "worker_index": worker_index,
                "worker_count": workers,
                "created_at": now,
                "items": [_job_item(row, worker_index=worker_index) for row in items],
            },
        )
    _write_json(
        drive_root / "logs" / STATUS_FILE,
        {
            "youtube_run_id": options.youtube_run_id,
            "state": "prepared",
            "workers": workers,
            "total": len(payload_items),
            "accounted": len(payload_items),
            "pending": len(selected),
            "already_done": len(accounted_done),
            "running": 0,
            "done": len(accounted_done),
            "failed": 0,
            "updated_at": now,
        },
    )
    return report


def preflight_launch_jobs(config: OrchestratorConfig, options: TtsLaunchOptions) -> dict[str, Any]:
    workers = max(1, int(options.workers or 1))
    ctx = launch_contract.build_launch_context(config, launch_id=options.youtube_run_id)
    drive_root = ctx.drive_mirror_root
    jobs_dir = drive_root / "jobs"
    errors: list[str] = []
    warnings: list[str] = []
    job_path = jobs_dir / "youtube_tts_job.json"
    if not ctx.launch_root.is_dir():
        errors.append(f"missing_launch_root:{ctx.launch_root}")
    if not jobs_dir.is_dir():
        errors.append(f"missing_jobs_dir:{jobs_dir}")
    if not job_path.is_file():
        errors.append(f"missing_job:{job_path}")
        job_items: list[dict[str, Any]] = []
    else:
        job = _read_json(job_path)
        if not isinstance(job, dict) or job.get("kind") != JOB_KIND:
            errors.append(f"invalid_job_kind:{job_path}")
        job_items = [x for x in (job.get("items") if isinstance(job, dict) else []) or [] if isinstance(x, dict)]
    partition_counts: list[int] = []
    for worker_index in range(workers):
        part_path = jobs_dir / "partitions" / f"worker_{worker_index}.json"
        if not part_path.is_file():
            errors.append(f"missing_partition:{part_path}")
            partition_counts.append(0)
            continue
        part = _read_json(part_path)
        if not isinstance(part, dict) or part.get("kind") != f"{JOB_KIND}_partition":
            errors.append(f"invalid_partition_kind:{part_path}")
        items = [x for x in (part.get("items") if isinstance(part, dict) else []) or [] if isinstance(x, dict)]
        partition_counts.append(len(items))
        if len(items) == 0:
            warnings.append(f"empty_partition:worker_{worker_index}")
        for item in items:
            for key in ("drive_text_path", "expected_drive_audio_path"):
                raw = str(item.get(key) or "")
                if not raw:
                    errors.append(f"partition_item_missing_{key}:{part_path}")
            text_path = Path(str(item.get("drive_text_path") or ""))
            if not text_path.is_file():
                errors.append(f"missing_input_text:{text_path}")
    for folder in (drive_root / "audio", drive_root / "logs", drive_root / "done", drive_root / "failed"):
        if not folder.is_dir():
            errors.append(f"missing_dir:{folder}")

    promo_guard_failures: list[dict[str, Any]] = []
    from orchestrator.youtube_tts_promo_guards import evaluate_tts_input_promo_guards

    for item in job_items:
        story_id = str(item.get("story_id") or item.get("canonical_basename") or "").strip()
        text_path = Path(str(item.get("drive_text_path") or item.get("source_text_path") or ""))
        if not text_path.is_file():
            continue
        story_dir = Path(str(item.get("story_dir") or ""))
        safe_path = story_dir / "02_safe_story" / "safe_story.txt" if story_dir.is_dir() else Path()
        tts_text = text_path.read_text(encoding="utf-8", errors="replace")
        safe_text = safe_path.read_text(encoding="utf-8", errors="replace") if safe_path.is_file() else ""
        guard = evaluate_tts_input_promo_guards(
            config=config,
            tts_text=tts_text,
            safe_story_text=safe_text,
        )
        if not guard.get("ok"):
            promo_guard_failures.append(
                {
                    "story_id": story_id,
                    "errors": guard.get("errors") or [],
                    "text_path": str(text_path),
                }
            )
            for err in guard.get("errors") or []:
                errors.append(f"promo_guard:{story_id}:{err}")

    return {
        "ok": not errors,
        "youtube_run_id": options.youtube_run_id,
        "workers": workers,
        "launch_root": str(ctx.launch_root),
        "drive_launch_root": str(drive_root),
        "job_path": str(job_path),
        "total_items": len(job_items),
        "partition_counts": partition_counts,
        "promo_guard_failures": promo_guard_failures,
        "errors": errors,
        "warnings": warnings,
    }


def status_launch_jobs(config: OrchestratorConfig, options: TtsLaunchOptions) -> dict[str, Any]:
    workers = max(1, int(options.workers or 1))
    preflight = preflight_launch_jobs(config, options)
    ctx = launch_contract.build_launch_context(config, launch_id=options.youtube_run_id)
    drive_root = ctx.drive_mirror_root
    job_path = drive_root / "jobs" / "youtube_tts_job.json"
    items: list[dict[str, Any]] = []
    if job_path.is_file():
        payload = _read_json(job_path)
        if isinstance(payload, dict):
            items = [x for x in payload.get("items", []) if isinstance(x, dict)]
    done = 0
    failed = 0
    pending = 0
    audio_missing = 0
    for item in items:
        audio_path = Path(str(item.get("expected_drive_audio_path") or ""))
        failed_marker = drive_root / "failed" / f"{Path(str(item.get('expected_output_filename') or audio_path.name)).stem}.json"
        if audio_path.is_file():
            done += 1
        elif failed_marker.is_file():
            failed += 1
            audio_missing += 1
        else:
            pending += 1
            audio_missing += 1
    worker_progress = []
    for worker_index in range(workers):
        part_path = drive_root / "jobs" / "partitions" / f"worker_{worker_index}.json"
        part_items = []
        if part_path.is_file():
            part = _read_json(part_path)
            if isinstance(part, dict):
                part_items = [x for x in part.get("items", []) if isinstance(x, dict)]
        part_done = sum(1 for item in part_items if Path(str(item.get("expected_drive_audio_path") or "")).is_file())
        worker_progress.append({"worker_index": worker_index, "total": len(part_items), "done": part_done, "pending": len(part_items) - part_done})
    return {
        "ok": bool(preflight.get("ok")),
        "youtube_run_id": options.youtube_run_id,
        "total": len(items),
        "pending": pending,
        "running": 0,
        "done": done,
        "failed": failed,
        "audio_missing": audio_missing,
        "missing_jobs": [err for err in preflight.get("errors", []) if "missing_job" in err or "missing_partition" in err],
        "stale_jobs": [],
        "worker_progress": worker_progress,
        "preflight": preflight,
    }
