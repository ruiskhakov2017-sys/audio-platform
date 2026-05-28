from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_from_site import (
    WORD_RE,
    YoutubeParseGeminiSelectionOptions,
    YoutubeSelectionFromSiteOptions,
    _deferred_path,
    _read_json,
    _read_text,
    _resolve_youtube_duration_contract,
    _write_json,
    _youtube_run_root,
    run_youtube_parse_gemini_selection,
    run_youtube_selection_from_site,
)
from orchestrator.youtube_selection_bridge import (
    YoutubeRunSelectionBridgeOptions,
    run_youtube_run_selection_bridge,
)


@dataclass
class YoutubeSelectionBatchFromSiteOptions:
    site_run_id: str
    youtube_run_id: str
    min_words: int | None = None
    max_words: int | None = None
    min_minutes: int | None = None
    max_minutes: int | None = None
    words_per_minute: int | None = None
    max_attempts: int = 0
    target_yes: int = 1
    workers: int = 1
    account_start_index: int = 0
    execute: bool = False
    retry_failed: bool = False
    seed: int | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    cleaned = cleaned.strip("_")
    return cleaned[:80] or "batch"


def _resolve_site_deferred(root_dir: Path, site_run_id: str) -> Path:
    standard = _deferred_path(root_dir, site_run_id)
    if standard.is_file():
        return standard

    launch_candidate = (
        root_dir
        / "Запуски"
        / "SITE_FULL_20260513_1309"
        / "10_Временные_файлы"
        / "legacy"
        / "runs"
        / "site"
        / site_run_id
        / "_phase_a"
        / "ready_queues"
        / "deferred.json"
    )
    if launch_candidate.is_file():
        return launch_candidate.resolve()

    matches = sorted(
        root_dir.rglob(f"runs/site/{site_run_id}/_phase_a/ready_queues/deferred.json"),
        key=lambda p: str(p).lower(),
    )
    if matches:
        return matches[0].resolve()
    return standard


def _batch_root(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id)


def _results_paths(root_dir: Path, youtube_run_id: str) -> dict[str, Path]:
    root = _batch_root(root_dir, youtube_run_id)
    return {
        "results_jsonl": root / "selection_batch_results.jsonl",
        "summary_json": root / "selection_batch_summary.json",
        "yes_json": root / "selection_yes.json",
        "no_json": root / "selection_no.json",
        "failed_json": root / "selection_failed.json",
        "plan_json": root / "selection_batch_plan.json",
    }


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _word_count(path: Path) -> tuple[int, bool, str]:
    if not path.is_file():
        return 0, False, "missing_cleaned_path"
    try:
        text = _read_text(path)
    except Exception as exc:
        return 0, False, f"read_error:{exc}"
    if not text.strip():
        return 0, False, "empty_text"
    return len(WORD_RE.findall(text)), True, ""


def _existing_clean_selection_results(root_dir: Path) -> set[str]:
    """Stories already selected with plain-clean input. Header-era runs are ignored."""
    tested: set[str] = set()
    for manifest in (root_dir / "runs" / "youtube").glob("*/_gemini_selection/input/gemini_selection_input_manifest.json"):
        try:
            data = _read_json(manifest)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("selection_stats_valid") is False:
            continue
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            continue
        run_dir = manifest.parents[2]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("selection_stats_valid") is False:
                continue
            canonical = str(item.get("canonical_basename", "")).strip()
            if not canonical:
                continue
            input_txt = Path(str(item.get("input_txt_path", "")).strip())
            header_used = bool(item.get("metadata_header_present", False))
            if not header_used and input_txt.is_file():
                try:
                    header_used = _read_text(input_txt).startswith("[ORCHESTRATOR_YOUTUBE_PREFILTER]")
                except Exception:
                    header_used = False
            if header_used:
                continue
            item_id = str(item.get("item_id", "")).strip()
            result_path = Path(str(item.get("expected_gemini_output_text", "")).strip())
            raw_path = run_dir / "_gemini_selection" / "raw" / f"{item_id}__raw.txt"
            if result_path.is_file() or raw_path.is_file():
                tested.add(canonical.casefold())
    return tested


def _candidate_rows(*, config: OrchestratorConfig, options: YoutubeSelectionBatchFromSiteOptions) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    root_dir = config.root_dir.resolve()
    deferred_path = _resolve_site_deferred(root_dir, options.site_run_id.strip())
    if not deferred_path.is_file():
        raise FileNotFoundError(f"site deferred manifest not found: {deferred_path}")
    payload = _read_json(deferred_path)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    duration = _resolve_youtube_duration_contract(
        config=config,
        min_minutes=options.min_minutes,
        max_minutes=options.max_minutes,
        words_per_minute=options.words_per_minute,
        min_words=options.min_words,
        max_words=options.max_words,
    )
    min_words = int(duration["min_words"])
    max_words = int(duration["max_words"])
    wpm = int(duration["words_per_minute"])
    already_clean = _existing_clean_selection_results(root_dir)

    candidates: list[dict[str, Any]] = []
    excluded = {
        "already_checked_clean_input": 0,
        "missing_cleaned_path": 0,
        "empty_text": 0,
        "too_short": 0,
        "too_long": 0,
        "read_error": 0,
    }
    seen: set[str] = set()
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical_basename", "")).strip() or Path(str(item.get("source_path", ""))).stem
        if not canonical:
            continue
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        if key in already_clean:
            excluded["already_checked_clean_input"] += 1
            continue

        cleaned = Path(str(item.get("cleaned_path", "")).strip())
        wc, ok, fail = _word_count(cleaned)
        if not ok:
            if fail.startswith("read_error"):
                excluded["read_error"] += 1
            else:
                excluded[fail] = int(excluded.get(fail, 0)) + 1
            continue
        if wc < min_words:
            excluded["too_short"] += 1
            continue
        if wc > max_words:
            excluded["too_long"] += 1
            continue
        candidates.append(
            {
                "source_index": idx,
                "canonical_basename": canonical,
                "word_count": wc,
                "estimated_minutes": round(wc / wpm, 2),
                "cleaned_path": str(cleaned.resolve()),
                "item": item,
            }
        )

    summary = {
        "total_items": len(items),
        "deferred_path": str(deferred_path),
        "duration_contract": duration,
        "candidate_count": len(candidates),
        "excluded": excluded,
        "already_checked_clean_input_count": len(already_clean),
    }
    return candidates, summary, deferred_path


def _write_mini_deferred(root_dir: Path, site_run_id: str, item: dict[str, Any]) -> Path:
    out = root_dir / "runs" / "site" / site_run_id / "_phase_a" / "ready_queues" / "deferred.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"queue": "deferred", "items": [item]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _extract_reason(raw_text: str) -> str:
    text = " ".join((raw_text or "").split())
    if not text:
        return ""
    for marker in ("Что придется вырезать:", "What придется вырезать:", "Главный хук", "Оценка сюжета"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx : idx + 500]
    return text[:500]


def _selection_output_paths(root_dir: Path, mini_youtube_run_id: str) -> tuple[Path, Path]:
    raw_dir = _youtube_run_root(root_dir, mini_youtube_run_id) / "_gemini_selection" / "raw"
    out_dir = _youtube_run_root(root_dir, mini_youtube_run_id) / "_gemini_selection" / "output"
    raw = next(iter(sorted(raw_dir.glob("*__raw.txt"))), raw_dir / "yt_00001__raw.txt") if raw_dir.is_dir() else raw_dir / "yt_00001__raw.txt"
    out = next(iter(sorted(out_dir.glob("*__result.txt"))), out_dir / "yt_00001__result.txt") if out_dir.is_dir() else out_dir / "yt_00001__result.txt"
    return raw, out


def _write_batch_indexes(paths: dict[str, Path], rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    yes = [r for r in rows if str(r.get("verdict", "")).lower() == "yes"]
    no = [r for r in rows if str(r.get("verdict", "")).lower() == "no"]
    failed = [r for r in rows if str(r.get("status", "")).lower() == "failed"]
    _write_json(paths["yes_json"], {"items": yes})
    _write_json(paths["no_json"], {"items": no})
    _write_json(paths["failed_json"], {"items": failed})
    _write_json(paths["summary_json"], summary)


def run_youtube_selection_batch_from_site(
    *, config: OrchestratorConfig, options: YoutubeSelectionBatchFromSiteOptions
) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    site_run_id = options.site_run_id.strip()
    youtube_run_id = options.youtube_run_id.strip()
    if not site_run_id or not youtube_run_id:
        return {"ok": False, "message": "--site-run-id and --youtube-run-id are required"}
    if options.workers != 1:
        return {"ok": False, "message": "selection-batch-from-site v1 supports only --workers 1"}
    if options.execute and int(options.max_attempts or 0) <= 0:
        return {"ok": False, "message": "--execute requires --max-attempts > 0"}
    if options.execute and int(options.target_yes or 0) <= 0:
        return {"ok": False, "message": "--execute requires --target-yes > 0"}

    try:
        candidates, plan_summary, deferred_path = _candidate_rows(config=config, options=options)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    max_attempts = min(int(options.max_attempts or len(candidates)), len(candidates))
    target_yes = max(1, int(options.target_yes or 1))
    planned = candidates[:max_attempts]
    paths = _results_paths(root_dir, youtube_run_id)
    batch_root = _batch_root(root_dir, youtube_run_id)
    batch_root.mkdir(parents=True, exist_ok=True)

    existing_rows = _load_jsonl(paths["results_jsonl"])
    existing_by_title = {
        str(row.get("canonical_basename", "")).casefold(): row
        for row in existing_rows
        if str(row.get("canonical_basename", "")).strip()
    }

    plan_payload = {
        "youtube_run_id": youtube_run_id,
        "site_run_id": site_run_id,
        "deferred_path": str(deferred_path),
        "execute": bool(options.execute),
        "max_attempts": max_attempts,
        "target_yes": target_yes,
        "workers": options.workers,
        "account_start_index": options.account_start_index,
        "candidate_count": len(candidates),
        "planned_count": len(planned),
        "summary": plan_summary,
        "first_20": [
            {
                "rank": i,
                "canonical_basename": row["canonical_basename"],
                "word_count": row["word_count"],
                "estimated_minutes": row["estimated_minutes"],
                "cleaned_path": row["cleaned_path"],
            }
            for i, row in enumerate(candidates[:20], start=1)
        ],
        "input_format": "plain_cleaned_story_text",
        "metadata_header_enabled": False,
        "created_at": _now_iso(),
    }
    _write_json(paths["plan_json"], plan_payload)

    if not options.execute:
        summary = {
            **plan_payload,
            "status": "dry_run",
            "attempts": 0,
            "yes_count": 0,
            "no_count": 0,
            "failed_count": 0,
        }
        _write_batch_indexes(paths, existing_rows, summary)
        return {
            "ok": True,
            "status": "dry_run",
            "youtube_run_id": youtube_run_id,
            "site_run_id": site_run_id,
            "candidate_count": len(candidates),
            "planned_count": len(planned),
            "excluded": plan_summary["excluded"],
            "already_checked_clean_input_count": plan_summary["already_checked_clean_input_count"],
            "first_20": plan_payload["first_20"],
            "plan_path": str(paths["plan_json"]),
            "summary_path": str(paths["summary_json"]),
            "input_format": "plain_cleaned_story_text",
            "metadata_header_enabled": False,
        }

    rows = existing_rows[:]
    yes_count = sum(1 for row in rows if str(row.get("verdict", "")).lower() == "yes")
    no_count = sum(1 for row in rows if str(row.get("verdict", "")).lower() == "no")
    failed_count = sum(1 for row in rows if str(row.get("status", "")).lower() == "failed")
    attempted_now = 0
    batch_started = time.time()

    for rank, candidate in enumerate(planned, start=1):
        title = candidate["canonical_basename"]
        existing = existing_by_title.get(title.casefold())
        if existing and not (options.retry_failed and existing.get("status") == "failed"):
            continue
        if attempted_now >= max_attempts or yes_count >= target_yes:
            break

        attempted_now += 1
        mini_suffix = f"{rank:05d}"
        safe_batch = _safe_id(youtube_run_id)
        mini_site_id = f"YT_{safe_batch}_{mini_suffix}"
        mini_youtube_run_id = f"{youtube_run_id}-item-{mini_suffix}"
        start = time.time()
        ts = _now_iso()
        print(
            f"[selection-batch] attempt={attempted_now}/{max_attempts} "
            f"yes={yes_count}/{target_yes} title={title}",
            flush=True,
        )
        _write_mini_deferred(root_dir, mini_site_id, candidate["item"])

        status = "failed"
        verdict = "unknown"
        exit_code = 0
        reason = ""
        result_path = ""
        raw_path = ""
        bot_email = ""
        try:
            sel = run_youtube_selection_from_site(
                config=config,
                options=YoutubeSelectionFromSiteOptions(
                    site_run_id=mini_site_id,
                    youtube_run_id=mini_youtube_run_id,
                    min_words=options.min_words,
                    max_words=options.max_words,
                    min_minutes=options.min_minutes,
                    max_minutes=options.max_minutes,
                    words_per_minute=options.words_per_minute,
                    force=True,
                ),
            )
            if not sel.get("ok", False):
                raise RuntimeError(str(sel.get("message", "selection-from-site failed")))
            bridge = run_youtube_run_selection_bridge(
                config=config,
                options=YoutubeRunSelectionBridgeOptions(
                    youtube_run_id=mini_youtube_run_id,
                    story_id=title,
                    execute=True,
                    force=True,
                    account_index=int(options.account_start_index or 0),
                ),
            )
            exit_code = int(bridge.get("gemini_auto_exit_code", 0) or 0)
            bot_email = str(bridge.get("bot_account_email", ""))
            if not bridge.get("ok", False):
                raise RuntimeError(str(bridge.get("message", "run-selection-bridge failed")))
            parse = run_youtube_parse_gemini_selection(
                config=config,
                options=YoutubeParseGeminiSelectionOptions(youtube_run_id=mini_youtube_run_id),
            )
            if not parse.get("ok", False):
                raise RuntimeError(str(parse.get("message", "parse failed")))
            raw, out = _selection_output_paths(root_dir, mini_youtube_run_id)
            raw_path = str(raw)
            result_path = str(out)
            raw_text = _read_text(raw) if raw.is_file() else ""
            reason = _extract_reason(raw_text)
            yes_json = _youtube_run_root(root_dir, mini_youtube_run_id) / "_selection" / "youtube_selected_yes.json"
            yes_items = _read_json(yes_json).get("items", []) if yes_json.is_file() else []
            verdict = "yes" if yes_items else "no"
            status = "done"
            if verdict == "yes":
                yes_count += 1
            else:
                no_count += 1
        except Exception as exc:
            status = "failed"
            verdict = "unknown"
            reason = str(exc)
            failed_count += 1

        row = {
            "canonical_basename": title,
            "word_count": candidate["word_count"],
            "estimated_minutes": candidate["estimated_minutes"],
            "cleaned_path": candidate["cleaned_path"],
            "mini_site_id": mini_site_id,
            "mini_youtube_run_id": mini_youtube_run_id,
            "account_index": int(options.account_start_index or 0),
            "bot_email": bot_email,
            "result_path": result_path,
            "raw_path": raw_path,
            "verdict": verdict,
            "reason": reason,
            "exit_code": exit_code,
            "duration_seconds": round(time.time() - start, 2),
            "timestamp": ts,
            "status": status,
            "selection_stats_valid": True,
            "metadata_header_used": False,
        }
        rows.append(row)
        print(
            f"[selection-batch] done title={title} verdict={verdict} status={status} "
            f"duration={row['duration_seconds']}s",
            flush=True,
        )
        _append_jsonl(paths["results_jsonl"], row)
        existing_by_title[title.casefold()] = row
        summary = {
            **plan_payload,
            "status": "running",
            "attempts": attempted_now,
            "yes_count": yes_count,
            "no_count": no_count,
            "failed_count": failed_count,
            "elapsed_seconds": round(time.time() - batch_started, 2),
            "updated_at": _now_iso(),
        }
        _write_batch_indexes(paths, rows, summary)
        if yes_count >= target_yes:
            break

    final_summary = {
        **plan_payload,
        "status": "done",
        "attempts": attempted_now,
        "yes_count": yes_count,
        "no_count": no_count,
        "failed_count": failed_count,
        "conversion": round(yes_count / max(1, yes_count + no_count), 4),
        "elapsed_seconds": round(time.time() - batch_started, 2),
        "finished_at": _now_iso(),
    }
    _write_batch_indexes(paths, rows, final_summary)
    return {
        "ok": True,
        "status": "done",
        "youtube_run_id": youtube_run_id,
        "attempts": attempted_now,
        "yes_count": yes_count,
        "no_count": no_count,
        "failed_count": failed_count,
        "conversion": final_summary["conversion"],
        "summary_path": str(paths["summary_json"]),
        "results_path": str(paths["results_jsonl"]),
        "yes_path": str(paths["yes_json"]),
        "no_path": str(paths["no_json"]),
        "failed_path": str(paths["failed_json"]),
    }
