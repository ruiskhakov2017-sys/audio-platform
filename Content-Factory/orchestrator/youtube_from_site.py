from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)
YOUTUBE_PREFILTER_HEADER_START = "[ORCHESTRATOR_YOUTUBE_PREFILTER]"
YOUTUBE_PREFILTER_HEADER_END = "[/ORCHESTRATOR_YOUTUBE_PREFILTER]"
FIELD_DECISION_RE = re.compile(
    r"(?im)\b(подходит(?:\s+для\s+youtube)?|решение|вердикт|годится)\b\s*[:\-]\s*(yes|no|да|нет)\b"
)
YES_TOKEN_RE = re.compile(r"(?i)\b(yes|да)\b")
NO_TOKEN_RE = re.compile(r"(?i)\b(no|нет)\b")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def strip_youtube_prefilter_header(text: str) -> str:
    """Remove orchestrator-only metadata before passing text to downstream safe rewrite."""
    if not text.startswith(YOUTUBE_PREFILTER_HEADER_START):
        return text
    end_idx = text.find(YOUTUBE_PREFILTER_HEADER_END)
    if end_idx < 0:
        return text
    body_start = end_idx + len(YOUTUBE_PREFILTER_HEADER_END)
    return text[body_start:].lstrip("\r\n")


def _has_youtube_prefilter_header(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:128]
    except Exception:
        return False
    return head.startswith(YOUTUBE_PREFILTER_HEADER_START)


def _resolve_youtube_duration_contract(
    *,
    config: OrchestratorConfig,
    min_minutes: int | None,
    max_minutes: int | None,
    words_per_minute: int | None,
    min_words: int | None,
    max_words: int | None,
) -> dict[str, Any]:
    wpm = max(1, int(words_per_minute or config.youtube_words_per_minute or 150))
    mn = max(1, int(min_minutes or config.youtube_min_minutes or 30))
    mx = max(mn, int(max_minutes or config.youtube_max_minutes or 80))
    derived_min_words = mn * wpm
    derived_max_words = mx * wpm
    explicit_duration = min_minutes is not None or max_minutes is not None or words_per_minute is not None
    default_min_words = derived_min_words if explicit_duration else getattr(config, "youtube_min_words", derived_min_words)
    default_max_words = derived_max_words if explicit_duration else getattr(config, "youtube_max_words", derived_max_words)
    resolved_min_words = max(1, int(min_words if min_words is not None else default_min_words))
    resolved_max_words = max(
        resolved_min_words,
        int(max_words if max_words is not None else default_max_words),
    )
    return {
        "min_minutes": mn,
        "max_minutes": mx,
        "words_per_minute": wpm,
        "min_words": resolved_min_words,
        "max_words": resolved_max_words,
        "derived_min_words": derived_min_words,
        "derived_max_words": derived_max_words,
        "split_long_stories": bool(getattr(config, "youtube_split_long_stories", False)),
    }


def _build_youtube_prefilter_header(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            YOUTUBE_PREFILTER_HEADER_START,
            f"duration_gate: {row.get('duration_gate', '')}",
            f"word_count: {row.get('word_count', 0)}",
            f"estimated_tts_minutes: {row.get('estimated_tts_minutes', row.get('estimated_minutes', 0))}",
            f"words_per_minute: {row.get('words_per_minute', 150)}",
            f"min_minutes: {row.get('min_minutes', 30)}",
            f"max_minutes: {row.get('max_minutes', 80)}",
            f"min_words: {row.get('min_words', 4000)}",
            f"max_words: {row.get('max_words', 12000)}",
            "source: cleaned_text",
            (
                "instruction_to_selection_bot: Do not reject this story for raw duration. "
                "It has already passed the local YouTube duration gate. Judge story quality, "
                "safety risks, plot strength, and whether enough non-explicit narrative remains after safe rewrite."
            ),
            YOUTUBE_PREFILTER_HEADER_END,
            "",
            "",
        ]
    )


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    return cleaned[:120] if cleaned else "story"


def _append_status(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _resolve_cleaned_path(root_dir: Path, cleaned_path: str, run_story_dir: str, canonical_basename: str) -> tuple[Path | None, str]:
    candidate = Path(str(cleaned_path).strip()) if str(cleaned_path).strip() else None
    if candidate and candidate.exists():
        return candidate, "deferred.cleaned_path"

    story_dir = Path(str(run_story_dir).strip()) if str(run_story_dir).strip() else None
    if story_dir and story_dir.exists():
        direct = story_dir / "cleaned_story.txt"
        if direct.exists():
            return direct, "run_story_dir.cleaned_story"

    output_site_cleaned = (root_dir / "output" / "site" / canonical_basename / "cleaned_story.txt").resolve()
    if output_site_cleaned.exists():
        return output_site_cleaned, "output_site.cleaned_story"

    # Optional stable fallback: phase-a finalized voice-tagged cleaned text.
    if story_dir and story_dir.exists():
        voice_candidates = sorted(story_dir.glob("*__[MFU].txt"), key=lambda p: p.name.lower())
        if len(voice_candidates) == 1:
            return voice_candidates[0], "run_story_dir.voice_variant"

    return None, "missing"


def _collect_diagnostic_row(root_dir: Path, site_run_id: str, item: dict[str, Any]) -> dict[str, Any]:
    source_path = str(item.get("source_path", "")).strip()
    cleaned_path = str(item.get("cleaned_path", "")).strip()
    run_story_dir = str(item.get("run_story_dir", "")).strip()
    canonical = str(item.get("canonical_basename", "")).strip() or _safe_name(Path(source_path).stem)
    cleaned_file, cleaned_source = _resolve_cleaned_path(root_dir, cleaned_path, run_story_dir, canonical)

    story_dir = Path(run_story_dir) if run_story_dir else None
    output_story_dir = (root_dir / "output" / "site" / canonical).resolve()
    output_cleaned = output_story_dir / "cleaned_story.txt"

    run_story_probable: list[str] = []
    output_story_probable: list[str] = []
    if story_dir and story_dir.exists():
        for pat in ("*clean*.txt", "*__[MFU].txt", "*.txt"):
            for p in sorted(story_dir.glob(pat), key=lambda x: x.name.lower()):
                if str(p) not in run_story_probable:
                    run_story_probable.append(str(p))
    if output_story_dir.exists():
        for pat in ("*clean*.txt", "*__[MFU].txt", "*.txt"):
            for p in sorted(output_story_dir.glob(pat), key=lambda x: x.name.lower()):
                if str(p) not in output_story_probable:
                    output_story_probable.append(str(p))

    return {
        "site_run_id": site_run_id,
        "canonical_basename": canonical,
        "source_path": source_path,
        "cleaned_path": cleaned_path,
        "cleaned_path_exists": bool(cleaned_path and Path(cleaned_path).exists()),
        "run_story_dir": run_story_dir,
        "run_story_dir_exists": bool(story_dir and story_dir.exists()),
        "run_story_dir_cleaned_story_exists": bool(story_dir and (story_dir / "cleaned_story.txt").exists()),
        "run_story_dir_info_exists": bool(story_dir and (story_dir / "info.txt").exists()),
        "output_site_story_dir": str(output_story_dir),
        "output_site_story_dir_exists": output_story_dir.exists(),
        "output_site_cleaned_story_exists": output_cleaned.exists(),
        "resolved_cleaned_path": str(cleaned_file) if cleaned_file else "",
        "cleaned_path_source": cleaned_source,
        "run_story_dir_probable_cleaned_files": run_story_probable,
        "output_site_probable_cleaned_files": output_story_probable,
    }


def run_youtube_diagnose_cleaned_paths(*, config: OrchestratorConfig, site_run_id: str, youtube_run_id: str) -> dict[str, Any]:
    deferred_json = _deferred_path(config.root_dir, site_run_id)
    if not deferred_json.exists():
        return {"ok": False, "message": f"site deferred manifest not found: {deferred_json}"}
    payload = _read_json(deferred_json)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    diag_rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            diag_rows.append(_collect_diagnostic_row(config.root_dir, site_run_id, item))

    diag_dir = _youtube_run_root(config.root_dir, youtube_run_id) / "_diagnostics"
    _write_json(diag_dir / "cleaned_path_diagnostics.json", {"items": diag_rows})
    lines: list[str] = [
        f"site_run_id={site_run_id}",
        f"youtube_run_id={youtube_run_id}",
        f"deferred_manifest={deferred_json}",
        f"items_total={len(diag_rows)}",
        "",
    ]
    for idx, row in enumerate(diag_rows, start=1):
        lines.extend(
            [
                f"[{idx}] canonical_basename={row['canonical_basename']}",
                f"source_path={row['source_path']}",
                f"cleaned_path={row['cleaned_path']}",
                f"exists(cleaned_path)={row['cleaned_path_exists']}",
                f"run_story_dir={row['run_story_dir']}",
                f"exists(run_story_dir)={row['run_story_dir_exists']}",
                f"run_story_dir.cleaned_story.txt={row['run_story_dir_cleaned_story_exists']}",
                f"run_story_dir.info.txt={row['run_story_dir_info_exists']}",
                f"output_site_dir={row['output_site_story_dir']}",
                f"exists(output_site_dir)={row['output_site_story_dir_exists']}",
                f"output_site.cleaned_story.txt={row['output_site_cleaned_story_exists']}",
                f"resolved_cleaned_path={row['resolved_cleaned_path']}",
                f"cleaned_path_source={row['cleaned_path_source']}",
                "run_story_dir_probable_cleaned_files:",
            ]
        )
        lines.extend([f"  - {p}" for p in row["run_story_dir_probable_cleaned_files"][:20]])
        lines.append("output_site_probable_cleaned_files:")
        lines.extend([f"  - {p}" for p in row["output_site_probable_cleaned_files"][:20]])
        lines.append("")
    report_path = diag_dir / "cleaned_path_diagnostics.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "ok": True,
        "site_run_id": site_run_id,
        "youtube_run_id": youtube_run_id,
        "items_total": len(diag_rows),
        "diagnostics_txt": str(report_path),
        "diagnostics_json": str(diag_dir / "cleaned_path_diagnostics.json"),
        "resolved_sources": {
            k: sum(1 for r in diag_rows if r.get("cleaned_path_source") == k)
            for k in ("deferred.cleaned_path", "run_story_dir.cleaned_story", "output_site.cleaned_story", "run_story_dir.voice_variant", "missing")
        },
    }


def _deferred_path(root_dir: Path, site_run_id: str) -> Path:
    return (root_dir / "runs" / "site" / site_run_id / "_phase_a" / "ready_queues" / "deferred.json").resolve()


def _youtube_run_root(root_dir: Path, youtube_run_id: str) -> Path:
    return (root_dir / "runs" / "youtube" / youtube_run_id).resolve()


def _selection_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id) / "_selection"


def _gemini_selection_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id) / "_gemini_selection"


def _gemini_safe_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id) / "_gemini_safe"


def _youtube_selection_report_path(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id) / "youtube_selection_workflow_report.txt"


def _write_selection_workflow_report(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass
class YoutubePrefilterFromSiteOptions:
    site_run_id: str
    youtube_run_id: str
    min_words: int | None = None
    max_words: int | None = None
    min_minutes: int | None = None
    max_minutes: int | None = None
    words_per_minute: int | None = None
    force: bool = False
    auto_prepare_selection_input: bool = True


@dataclass
class YoutubeParseGeminiSelectionOptions:
    youtube_run_id: str
    force: bool = False


@dataclass
class YoutubePrepareGeminiSelectionInputOptions:
    youtube_run_id: str
    force: bool = False


@dataclass
class YoutubePrepareSafeInputOptions:
    youtube_run_id: str
    force: bool = False


@dataclass
class YoutubeSelectionFromSiteOptions:
    site_run_id: str
    youtube_run_id: str
    min_words: int | None = None
    max_words: int | None = None
    min_minutes: int | None = None
    max_minutes: int | None = None
    words_per_minute: int | None = None
    force: bool = False


@dataclass
class YoutubeContinueAfterSelectionOptions:
    youtube_run_id: str
    force: bool = False


def run_youtube_prefilter_from_site(*, config: OrchestratorConfig, options: YoutubePrefilterFromSiteOptions) -> dict[str, Any]:
    site_run_id = str(options.site_run_id).strip()
    youtube_run_id = str(options.youtube_run_id).strip()
    if not site_run_id:
        return {"ok": False, "message": "site_run_id is required"}
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}
    duration_contract = _resolve_youtube_duration_contract(
        config=config,
        min_minutes=options.min_minutes,
        max_minutes=options.max_minutes,
        words_per_minute=options.words_per_minute,
        min_words=options.min_words,
        max_words=options.max_words,
    )
    min_minutes = int(duration_contract["min_minutes"])
    max_minutes = int(duration_contract["max_minutes"])
    words_per_minute = int(duration_contract["words_per_minute"])
    min_words = int(duration_contract["min_words"])
    max_words = int(duration_contract["max_words"])

    deferred_json = _deferred_path(config.root_dir, site_run_id)
    if not deferred_json.exists():
        return {"ok": False, "message": f"site deferred manifest not found: {deferred_json}"}

    payload = _read_json(deferred_json)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not items:
        return {"ok": False, "message": f"deferred manifest is empty: {deferred_json}"}

    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    selection_dir = _selection_dir(config.root_dir, youtube_run_id)
    gem_sel = _gemini_selection_dir(config.root_dir, youtube_run_id)
    gem_safe = _gemini_safe_dir(config.root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"

    all_rows: list[dict[str, Any]] = []
    yes_rows: list[dict[str, Any]] = []
    no_rows: list[dict[str, Any]] = []
    missing_cleaned_count = 0

    diag_rows: list[dict[str, Any]] = []

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("source_path", "")).strip()
        cleaned_path = str(item.get("cleaned_path", "")).strip()
        run_story_dir = str(item.get("run_story_dir", "")).strip()
        canonical = str(item.get("canonical_basename", "")).strip() or _safe_name(Path(source_path).stem)
        diag = _collect_diagnostic_row(config.root_dir, site_run_id, item)
        diag_rows.append(diag)
        cleaned_file = Path(diag["resolved_cleaned_path"]) if diag["resolved_cleaned_path"] else None
        cleaned_path_source = str(diag["cleaned_path_source"])

        word_count = 0
        estimated_minutes = 0.0
        status = "no"
        reject_reason = ""
        read_error = ""

        if cleaned_file is None:
            status = "no"
            reject_reason = "missing_cleaned_path"
            missing_cleaned_count += 1
        else:
            try:
                text = _read_text(cleaned_file)
                stripped = text.strip()
                word_count = len(WORD_RE.findall(text))
                estimated_minutes = round(word_count / words_per_minute, 2)
                if not stripped:
                    status = "no"
                    reject_reason = "empty_text"
                elif word_count < min_words:
                    status = "no"
                    reject_reason = "too_short"
                elif word_count > max_words:
                    status = "no"
                    reject_reason = "too_long"
                else:
                    status = "yes"
                    reject_reason = ""
            except Exception as exc:
                status = "no"
                reject_reason = "read_error"
                read_error = str(exc)

        row = {
            "item_id": f"yt_{idx:05d}",
            "canonical_basename": canonical,
            "source_path": source_path,
            "cleaned_path": str(cleaned_file.resolve()) if cleaned_file is not None else cleaned_path,
            "resolved_cleaned_path": str(cleaned_file.resolve()) if cleaned_file is not None else "",
            "cleaned_path_source": cleaned_path_source,
            "site_run_story_dir": run_story_dir,
            "site_run_id": site_run_id,
            "youtube_run_id": youtube_run_id,
            "word_count": int(word_count),
            "estimated_minutes": estimated_minutes,
            "estimated_tts_minutes": estimated_minutes,
            "words_per_minute": words_per_minute,
            "min_minutes": min_minutes,
            "max_minutes": max_minutes,
            "min_words": min_words,
            "max_words": max_words,
            "duration_gate": "PASS" if status == "yes" else "FAIL",
            "pass": status == "yes",
            "fail_reason": reject_reason,
            "split_long_stories": bool(duration_contract["split_long_stories"]),
            "youtube_size_status": status,
            "reject_reason": reject_reason,
            "error": read_error,
        }
        all_rows.append(row)
        if status == "yes":
            yes_rows.append(row)
        else:
            no_rows.append(row)

    top_longest = sorted(all_rows, key=lambda x: int(x.get("word_count", 0)), reverse=True)[:10]
    top_shortest = sorted(all_rows, key=lambda x: int(x.get("word_count", 0)))[:10]

    _write_json(selection_dir / "youtube_size_filter.json", {"duration_contract": duration_contract, "items": all_rows})
    diagnostics_dir = run_root / "_diagnostics"
    _write_json(diagnostics_dir / "cleaned_path_diagnostics.json", {"items": diag_rows})
    diagnostics_lines = [
        f"site_run_id={site_run_id}",
        f"youtube_run_id={youtube_run_id}",
        f"deferred_manifest={deferred_json}",
        f"items_total={len(diag_rows)}",
        "",
    ]
    for i, d in enumerate(diag_rows, start=1):
        diagnostics_lines.append(
            f"[{i}] {d['canonical_basename']} | cleaned_path_exists={d['cleaned_path_exists']} | "
            f"run_story_dir_exists={d['run_story_dir_exists']} | output_site_exists={d['output_site_story_dir_exists']} | "
            f"resolved={d['cleaned_path_source']} -> {d['resolved_cleaned_path']}"
        )
    (diagnostics_dir / "cleaned_path_diagnostics.txt").write_text("\n".join(diagnostics_lines), encoding="utf-8")

    _write_json(selection_dir / "youtube_size_yes.json", {"duration_contract": duration_contract, "items": yes_rows})
    _write_json(selection_dir / "youtube_size_no.json", {"duration_contract": duration_contract, "items": no_rows})

    report_lines = [
        f"site_run_id={site_run_id}",
        f"youtube_run_id={youtube_run_id}",
        f"deferred_manifest={deferred_json}",
        f"total_in_site_deferred={len(all_rows)}",
        f"size_yes={len(yes_rows)}",
        f"size_no={len(no_rows)}",
        f"too_short={sum(1 for r in no_rows if r.get('reject_reason') == 'too_short')}",
        f"too_long={sum(1 for r in no_rows if r.get('reject_reason') == 'too_long')}",
        f"empty_text={sum(1 for r in no_rows if r.get('reject_reason') == 'empty_text')}",
        f"missing_cleaned_path={missing_cleaned_count}",
        f"duration_contract={min_minutes}-{max_minutes} min @{words_per_minute} wpm",
        f"min_minutes={min_minutes}",
        f"max_minutes={max_minutes}",
        f"words_per_minute={words_per_minute}",
        f"min_words={min_words}",
        f"max_words={max_words}",
        f"derived_min_words={duration_contract['derived_min_words']}",
        f"derived_max_words={duration_contract['derived_max_words']}",
        f"split_long_stories={str(duration_contract['split_long_stories']).lower()}",
        "",
        "top_longest:",
    ]
    for r in top_longest:
        report_lines.append(
            f"- {r['canonical_basename']} | pass={r['duration_gate']} | reason={r['fail_reason']} | "
            f"words={r['word_count']} | est_minutes={r['estimated_tts_minutes']} | wpm={r['words_per_minute']}"
        )
    report_lines.append("")
    report_lines.append("top_shortest:")
    for r in top_shortest:
        report_lines.append(
            f"- {r['canonical_basename']} | pass={r['duration_gate']} | reason={r['fail_reason']} | "
            f"words={r['word_count']} | est_minutes={r['estimated_tts_minutes']} | wpm={r['words_per_minute']}"
        )
    (selection_dir / "youtube_size_filter_report.txt").write_text("\n".join(report_lines), encoding="utf-8")

    gem_sel_input = gem_sel / "input"
    gem_sel_output = gem_sel / "output"
    gem_sel_parsed = gem_sel / "parsed"
    gem_sel_raw = gem_sel / "raw"
    gem_sel_input.mkdir(parents=True, exist_ok=True)
    gem_sel_output.mkdir(parents=True, exist_ok=True)
    gem_sel_parsed.mkdir(parents=True, exist_ok=True)
    gem_sel_raw.mkdir(parents=True, exist_ok=True)

    prep_sel: dict[str, Any] = {
        "ok": True,
        "prepared": 0,
        "created_input_files": 0,
        "skipped_input_files": 0,
    }
    if options.auto_prepare_selection_input:
        prep_sel = run_youtube_prepare_gemini_selection_input(
            config=config,
            options=YoutubePrepareGeminiSelectionInputOptions(youtube_run_id=youtube_run_id, force=options.force),
        )
        if not prep_sel.get("ok", False):
            return prep_sel

    gem_safe_input = gem_safe / "input"
    gem_safe_output = gem_safe / "output"
    gem_safe_parsed = gem_safe / "parsed"
    gem_safe_input.mkdir(parents=True, exist_ok=True)
    gem_safe_output.mkdir(parents=True, exist_ok=True)
    gem_safe_parsed.mkdir(parents=True, exist_ok=True)

    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_size_filter",
            "state": "done",
            "size_yes": len(yes_rows),
            "size_no": len(no_rows),
            "duration_contract": duration_contract,
            "message": "prefilter from site deferred completed",
        },
    )

    return {
        "ok": True,
        "site_run_id": site_run_id,
        "youtube_run_id": youtube_run_id,
        "deferred_manifest": str(deferred_json),
        "total": len(all_rows),
        "size_yes": len(yes_rows),
        "size_no": len(no_rows),
        "too_short": sum(1 for r in no_rows if r.get("reject_reason") == "too_short"),
        "too_long": sum(1 for r in no_rows if r.get("reject_reason") == "too_long"),
        "empty_text": sum(1 for r in no_rows if r.get("reject_reason") == "empty_text"),
        "missing_cleaned_path": missing_cleaned_count,
        "duration_contract": duration_contract,
        "min_minutes": min_minutes,
        "max_minutes": max_minutes,
        "words_per_minute": words_per_minute,
        "min_words": min_words,
        "max_words": max_words,
        "selection_dir": str(selection_dir),
        "gemini_selection_input_dir": str(gem_sel_input),
        "gemini_selection_output_dir": str(gem_sel_output),
        "gemini_selection_parsed_dir": str(gem_sel_parsed),
        "gemini_selection_raw_dir": str(gem_sel_raw),
        "gemini_safe_input_dir": str(gem_safe_input),
        "gemini_safe_output_dir": str(gem_safe_output),
        "gemini_safe_parsed_dir": str(gem_safe_parsed),
        "gemini_selection_inputs_created": prep_sel.get("created_input_files", 0),
        "gemini_selection_inputs_skipped": prep_sel.get("skipped_input_files", 0),
        "gemini_selection_inputs_prepared": prep_sel.get("prepared", 0),
        "selection_input_prepared": bool(options.auto_prepare_selection_input),
        "status_jsonl": str(status_jsonl),
    }


def run_youtube_prepare_gemini_selection_input(
    *, config: OrchestratorConfig, options: YoutubePrepareGeminiSelectionInputOptions
) -> dict[str, Any]:
    youtube_run_id = str(options.youtube_run_id).strip()
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}

    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    selection_dir = _selection_dir(config.root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"
    yes_json = selection_dir / "youtube_size_yes.json"
    if not yes_json.exists():
        return {"ok": False, "message": f"missing file: {yes_json}"}
    payload = _read_json(yes_json)
    yes_rows = payload.get("items", []) if isinstance(payload, dict) else []

    gem_sel = _gemini_selection_dir(config.root_dir, youtube_run_id)
    gem_sel_input = gem_sel / "input"
    gem_sel_output = gem_sel / "output"
    gem_sel_parsed = gem_sel / "parsed"
    gem_sel_raw = gem_sel / "raw"
    gem_sel_input.mkdir(parents=True, exist_ok=True)
    gem_sel_output.mkdir(parents=True, exist_ok=True)
    gem_sel_parsed.mkdir(parents=True, exist_ok=True)
    gem_sel_raw.mkdir(parents=True, exist_ok=True)

    gem_sel_manifest_items: list[dict[str, Any]] = []
    created_input = 0
    skipped_input = 0
    for row in yes_rows:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id", "")).strip()
        canonical = str(row.get("canonical_basename", "")).strip() or "story"
        cleaned_file = Path(str(row.get("resolved_cleaned_path", "")).strip() or str(row.get("cleaned_path", "")).strip())
        if not cleaned_file.exists():
            continue
        input_text_path = gem_sel_input / f"{_safe_name(canonical)}.txt"
        output_hint_path = gem_sel_output / f"{item_id}__result.txt"
        if input_text_path.exists() and not options.force:
            skipped_input += 1
        else:
            # YouTube selection Gem must receive plain cleaned story text only.
            # Duration metrics stay in JSON/reports; no orchestrator instructions are injected into the story body.
            story_text = _read_text(cleaned_file)
            input_text_path.write_text(story_text, encoding="utf-8", newline="\n")
            created_input += 1
        header_present = _has_youtube_prefilter_header(input_text_path)
        gem_sel_manifest_items.append(
            {
                **row,
                "input_txt_path": str(input_text_path),
                "resolved_cleaned_path": str(cleaned_file),
                "gemini_selection_input_text": str(input_text_path),
                "expected_gemini_output_text": str(output_hint_path),
                "metadata_header_present": header_present,
                "metadata_header_type": "ORCHESTRATOR_YOUTUBE_PREFILTER" if header_present else "",
            }
        )

    manifest_path = gem_sel_input / "gemini_selection_input_manifest.json"
    _write_json(manifest_path, {"items": gem_sel_manifest_items})
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_selection_input_prepare",
            "state": "done",
            "prepared": len(gem_sel_manifest_items),
            "created_input_files": created_input,
            "skipped_input_files": skipped_input,
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "prepared": len(gem_sel_manifest_items),
        "created_input_files": created_input,
        "skipped_input_files": skipped_input,
        "manifest_path": str(manifest_path),
        "input_dir": str(gem_sel_input),
        "output_dir": str(gem_sel_output),
        "parsed_dir": str(gem_sel_parsed),
        "raw_dir": str(gem_sel_raw),
    }


def _parse_binary_decision(text: str) -> tuple[str, str, str]:
    body = (text or "").strip()
    if not body:
        return "no", "ambiguous_or_empty", "empty_or_unknown"

    field_match = FIELD_DECISION_RE.search(body)
    if field_match:
        value = field_match.group(2).strip().lower()
        if value in {"yes", "да"}:
            return "yes", "", "field_value_ru" if value == "да" else "field_value_en"
        if value in {"no", "нет"}:
            return "no", "explicit_ru_no" if value == "нет" else "explicit_no", "field_value_ru" if value == "нет" else "field_value_en"

    has_yes = bool(YES_TOKEN_RE.search(body))
    has_no = bool(NO_TOKEN_RE.search(body))
    if has_yes and has_no:
        return "no", "ambiguous_yes_no", "ambiguous_tokens"
    if has_yes:
        token_match = YES_TOKEN_RE.search(body)
        token = token_match.group(1).lower() if token_match else "yes"
        return "yes", "", "token_ru" if token == "да" else "token_en"
    if has_no:
        token_match = NO_TOKEN_RE.search(body)
        token = token_match.group(1).lower() if token_match else "no"
        return "no", "explicit_ru_no" if token == "нет" else "explicit_no", "token_ru" if token == "нет" else "token_en"
    return "no", "ambiguous_or_empty", "empty_or_unknown"


def run_youtube_parse_gemini_selection(
    *, config: OrchestratorConfig, options: YoutubeParseGeminiSelectionOptions
) -> dict[str, Any]:
    youtube_run_id = str(options.youtube_run_id).strip()
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}
    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    selection_dir = _selection_dir(config.root_dir, youtube_run_id)
    gem_sel = _gemini_selection_dir(config.root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"

    manifest_path = gem_sel / "input" / "gemini_selection_input_manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "message": f"missing manifest: {manifest_path}"}
    manifest = _read_json(manifest_path)
    items = manifest.get("items", []) if isinstance(manifest, dict) else []

    raw_dir = gem_sel / "raw"
    output_dir = gem_sel / "output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    yes_rows: list[dict[str, Any]] = []
    no_rows: list[dict[str, Any]] = []
    parsed_rows: list[dict[str, Any]] = []
    missing_outputs = 0

    for row in items:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id", "")).strip()
        output_hint = str(row.get("expected_gemini_output_text", "")).strip()
        normalized_output_path = Path(output_hint) if output_hint else (output_dir / f"{item_id}__result.txt")
        raw_output_path = raw_dir / f"{item_id}__raw.txt"
        source_path = raw_output_path if raw_output_path.exists() else normalized_output_path

        if not source_path.exists():
            decision = "no"
            reject_reason = "missing_gemini_output"
            parse_rule = "missing_output"
            raw_text = ""
            missing_outputs += 1
        else:
            raw_text = _read_text(source_path)
            raw_output_path.write_text(raw_text, encoding="utf-8")
            decision, reject_reason, parse_rule = _parse_binary_decision(raw_text)

        normalized_output_path.write_text("YES\n" if decision == "yes" else "NO\n", encoding="utf-8")

        parsed = {
            **row,
            "youtube_selection_status": decision,
            "selection_reject_reason": reject_reason,
            "reject_reason": reject_reason,
            "raw_result_path": str(raw_output_path),
            "normalized_result_path": str(normalized_output_path),
            "parse_rule": parse_rule,
            "gemini_output_path": str(normalized_output_path),
        }
        parsed_rows.append(parsed)
        if decision == "yes":
            yes_rows.append(parsed)
        else:
            no_rows.append(parsed)

    _write_json(gem_sel / "parsed" / "selection_results.json", {"items": parsed_rows})
    _write_json(selection_dir / "youtube_selected_yes.json", {"items": yes_rows})
    _write_json(selection_dir / "youtube_selected_no.json", {"items": no_rows})

    report_lines = [
        f"youtube_run_id={youtube_run_id}",
        f"total_inputs={len(parsed_rows)}",
        f"selection_yes={len(yes_rows)}",
        f"selection_no={len(no_rows)}",
        f"missing_gemini_output={missing_outputs}",
    ]
    (selection_dir / "youtube_gemini_selection_report.txt").write_text("\n".join(report_lines), encoding="utf-8")

    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_gemini_selection_parse",
            "state": "done",
            "selection_yes": len(yes_rows),
            "selection_no": len(no_rows),
            "missing_output": missing_outputs,
        },
    )
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_selection_yes",
            "state": "done",
            "count": len(yes_rows),
        },
    )
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_selection_no",
            "state": "done",
            "count": len(no_rows),
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "total_inputs": len(parsed_rows),
        "selection_yes": len(yes_rows),
        "selection_no": len(no_rows),
        "missing_gemini_output": missing_outputs,
        "selection_yes_json": str(selection_dir / "youtube_selected_yes.json"),
        "selection_no_json": str(selection_dir / "youtube_selected_no.json"),
        "report_txt": str(selection_dir / "youtube_gemini_selection_report.txt"),
    }


def run_youtube_prepare_safe_input(*, config: OrchestratorConfig, options: YoutubePrepareSafeInputOptions) -> dict[str, Any]:
    youtube_run_id = str(options.youtube_run_id).strip()
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}
    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    selection_dir = _selection_dir(config.root_dir, youtube_run_id)
    gem_safe = _gemini_safe_dir(config.root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"
    selected_yes_path = selection_dir / "youtube_selected_yes.json"
    if not selected_yes_path.exists():
        return {"ok": False, "message": f"missing file: {selected_yes_path}"}

    payload = _read_json(selected_yes_path)
    items = payload.get("items", []) if isinstance(payload, dict) else []

    safe_input_dir = gem_safe / "input"
    safe_output_dir = gem_safe / "output"
    safe_parsed_dir = gem_safe / "parsed"
    safe_input_dir.mkdir(parents=True, exist_ok=True)
    safe_output_dir.mkdir(parents=True, exist_ok=True)
    safe_parsed_dir.mkdir(parents=True, exist_ok=True)

    prepared_items: list[dict[str, Any]] = []
    created = 0
    skipped = 0
    output_youtube_root = (config.root_dir / "output" / "youtube").resolve()

    for row in items:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id", "")).strip()
        canonical = str(row.get("canonical_basename", "")).strip() or "story"
        cleaned_path = Path(str(row.get("cleaned_path", "")).strip())
        if not cleaned_path.exists():
            continue
        safe_input_text = safe_input_dir / f"{item_id}__{_safe_name(canonical)}.txt"
        if safe_input_text.exists() and not options.force:
            skipped += 1
        else:
            safe_input_text.write_text(
                strip_youtube_prefilter_header(_read_text(cleaned_path)),
                encoding="utf-8",
                newline="\n",
            )
            created += 1

        story_root = output_youtube_root / canonical
        # Bridge layout (фактическая цепочка); старые 03_chunks…07_video не удаляем, только добавляем новые имена.
        for rel in (
            "00_source",
            "01_selection",
            "02_safe_story",
            "03_promo",
            "04_audio",
            "05_characters",
            "06_director",
            "07_frames",
            "08_video",
            "logs",
        ):
            (story_root / rel).mkdir(parents=True, exist_ok=True)

        source_ref = story_root / "00_source" / "source_ref.json"
        if (not source_ref.exists()) or options.force:
            _write_json(
                source_ref,
                {
                    "item_id": item_id,
                    "canonical_basename": canonical,
                    "source_path": row.get("source_path", ""),
                    "cleaned_path": str(cleaned_path),
                    "site_run_story_dir": row.get("site_run_story_dir", ""),
                    "site_run_id": row.get("site_run_id", ""),
                    "youtube_run_id": youtube_run_id,
                },
            )
        source_cleaned = story_root / "00_source" / "source_cleaned_story.txt"
        if (not source_cleaned.exists()) or options.force:
            source_cleaned.write_text(
                strip_youtube_prefilter_header(_read_text(cleaned_path)),
                encoding="utf-8",
                newline="\n",
            )
        src_info = Path(str(row.get("site_run_story_dir", "")).strip()) / "info.txt"
        dst_info = story_root / "00_source" / "source_info.txt"
        if src_info.exists() and ((not dst_info.exists()) or options.force):
            dst_info.write_text(_read_text(src_info), encoding="utf-8")

        selection_json = story_root / "01_selection" / "size_filter.json"
        if (not selection_json.exists()) or options.force:
            _write_json(
                selection_json,
                {
                    "youtube_size_status": row.get("youtube_size_status", ""),
                    "word_count": row.get("word_count", 0),
                    "estimated_minutes": row.get("estimated_minutes", 0),
                    "estimated_tts_minutes": row.get("estimated_tts_minutes", row.get("estimated_minutes", 0)),
                    "words_per_minute": row.get("words_per_minute", 150),
                    "min_minutes": row.get("min_minutes", 30),
                    "max_minutes": row.get("max_minutes", 80),
                    "min_words": row.get("min_words", 4000),
                    "max_words": row.get("max_words", 12000),
                    "duration_gate": row.get("duration_gate", ""),
                    "youtube_selection_status": row.get("youtube_selection_status", ""),
                    "reject_reason": row.get("reject_reason", ""),
                    "fail_reason": row.get("fail_reason", ""),
                },
            )

        prepared_items.append(
            {
                **row,
                "safe_input_text": str(safe_input_text),
                "safe_output_text_expected": str(safe_output_dir / f"{item_id}__safe_story.txt"),
                "output_story_dir": str(story_root),
            }
        )

    _write_json(safe_input_dir / "gemini_safe_input_manifest.json", {"items": prepared_items})
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_safe_input_prepare",
            "state": "done",
            "prepared": len(prepared_items),
            "created_input_files": created,
            "skipped_input_files": skipped,
        },
    )
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_safe_pending",
            "state": "done",
            "count": len(prepared_items),
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "prepared": len(prepared_items),
        "created_input_files": created,
        "skipped_input_files": skipped,
        "safe_input_manifest": str(safe_input_dir / "gemini_safe_input_manifest.json"),
        "safe_input_dir": str(safe_input_dir),
        "safe_output_dir": str(safe_output_dir),
        "safe_parsed_dir": str(safe_parsed_dir),
        "output_youtube_root": str(output_youtube_root),
        "status_jsonl": str(status_jsonl),
    }


def run_youtube_selection_from_site(
    *, config: OrchestratorConfig, options: YoutubeSelectionFromSiteOptions
) -> dict[str, Any]:
    site_run_id = str(options.site_run_id).strip()
    youtube_run_id = str(options.youtube_run_id).strip()
    if not site_run_id:
        return {"ok": False, "message": "site_run_id is required"}
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}

    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"
    report_path = _youtube_selection_report_path(config.root_dir, youtube_run_id)

    prefilter = run_youtube_prefilter_from_site(
        config=config,
        options=YoutubePrefilterFromSiteOptions(
            site_run_id=site_run_id,
            youtube_run_id=youtube_run_id,
            min_words=options.min_words,
            max_words=options.max_words,
            min_minutes=options.min_minutes,
            max_minutes=options.max_minutes,
            words_per_minute=options.words_per_minute,
            force=bool(options.force),
            auto_prepare_selection_input=False,
        ),
    )
    if not prefilter.get("ok", False):
        return prefilter

    size_yes = int(prefilter.get("size_yes", 0) or 0)
    size_no = int(prefilter.get("size_no", 0) or 0)
    if size_yes <= 0:
        _write_selection_workflow_report(
            report_path,
            [
                f"youtube_run_id={youtube_run_id}",
                f"site_run_id={site_run_id}",
                "stage=selection_from_site",
                "status=stopped_no_size_yes",
                "message=Нет рассказов подходящей длины для YouTube",
                f"size_yes={size_yes}",
                f"size_no={size_no}",
                f"duration_contract={prefilter.get('min_minutes')}-{prefilter.get('max_minutes')} min @{prefilter.get('words_per_minute')} wpm",
                f"min_words={prefilter.get('min_words')}",
                f"max_words={prefilter.get('max_words')}",
                f"selection_dir={prefilter.get('selection_dir', '')}",
                "gemini_selection_handoff=not_started",
            ],
        )
        _append_status(
            status_jsonl,
            {
                "timestamp": _now_iso(),
                "youtube_run_id": youtube_run_id,
                "stage": "youtube_selection_from_site",
                "state": "stopped",
                "message": "no stories with accepted size for youtube",
                "size_yes": size_yes,
                "size_no": size_no,
            },
        )
        return {
            "ok": True,
            "youtube_run_id": youtube_run_id,
            "site_run_id": site_run_id,
            "status": "stopped_no_size_yes",
            "message": "Нет рассказов подходящей длины для YouTube",
            "size_yes": size_yes,
            "size_no": size_no,
            "duration_contract": prefilter.get("duration_contract", {}),
            "min_minutes": prefilter.get("min_minutes"),
            "max_minutes": prefilter.get("max_minutes"),
            "words_per_minute": prefilter.get("words_per_minute"),
            "min_words": prefilter.get("min_words"),
            "max_words": prefilter.get("max_words"),
            "report_path": str(report_path),
            "selection_dir": prefilter.get("selection_dir", ""),
        }

    prepare = run_youtube_prepare_gemini_selection_input(
        config=config,
        options=YoutubePrepareGeminiSelectionInputOptions(
            youtube_run_id=youtube_run_id,
            force=bool(options.force),
        ),
    )
    if not prepare.get("ok", False):
        return prepare

    _write_selection_workflow_report(
        report_path,
        [
            f"youtube_run_id={youtube_run_id}",
            f"site_run_id={site_run_id}",
            "stage=selection_from_site",
            "status=handoff_pending_real_gemini_selection",
            "message=Подготовка завершена. Ожидаются реальные ответы Gemini #1 в raw/output",
            f"size_yes={size_yes}",
            f"size_no={size_no}",
            f"duration_contract={prefilter.get('min_minutes')}-{prefilter.get('max_minutes')} min @{prefilter.get('words_per_minute')} wpm",
            f"min_words={prefilter.get('min_words')}",
            f"max_words={prefilter.get('max_words')}",
            f"gemini_selection_input_manifest={prepare.get('manifest_path', '')}",
            f"gemini_selection_input_dir={prepare.get('input_dir', '')}",
            f"gemini_selection_output_dir={prepare.get('output_dir', '')}",
            f"gemini_selection_raw_dir={prepare.get('raw_dir', '')}",
            "next_step=python -m orchestrator youtube continue-after-selection --youtube-run-id <id>",
        ],
    )
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_selection_from_site",
            "state": "handoff_pending_real_gemini_selection",
            "size_yes": size_yes,
            "size_no": size_no,
            "prepared": int(prepare.get("prepared", 0) or 0),
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "site_run_id": site_run_id,
        "status": "handoff_pending_real_gemini_selection",
        "size_yes": size_yes,
        "size_no": size_no,
        "duration_contract": prefilter.get("duration_contract", {}),
        "min_minutes": prefilter.get("min_minutes"),
        "max_minutes": prefilter.get("max_minutes"),
        "words_per_minute": prefilter.get("words_per_minute"),
        "min_words": prefilter.get("min_words"),
        "max_words": prefilter.get("max_words"),
        "prepared": int(prepare.get("prepared", 0) or 0),
        "created_input_files": int(prepare.get("created_input_files", 0) or 0),
        "skipped_input_files": int(prepare.get("skipped_input_files", 0) or 0),
        "manifest_path": str(prepare.get("manifest_path", "")),
        "input_dir": str(prepare.get("input_dir", "")),
        "output_dir": str(prepare.get("output_dir", "")),
        "raw_dir": str(prepare.get("raw_dir", "")),
        "report_path": str(report_path),
    }


def run_youtube_continue_after_selection(
    *, config: OrchestratorConfig, options: YoutubeContinueAfterSelectionOptions
) -> dict[str, Any]:
    youtube_run_id = str(options.youtube_run_id).strip()
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}

    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"
    report_path = _youtube_selection_report_path(config.root_dir, youtube_run_id)

    parsed = run_youtube_parse_gemini_selection(
        config=config,
        options=YoutubeParseGeminiSelectionOptions(
            youtube_run_id=youtube_run_id,
            force=bool(options.force),
        ),
    )
    if not parsed.get("ok", False):
        return parsed

    selection_yes = int(parsed.get("selection_yes", 0) or 0)
    selection_no = int(parsed.get("selection_no", 0) or 0)
    if selection_yes <= 0:
        _write_selection_workflow_report(
            report_path,
            [
                f"youtube_run_id={youtube_run_id}",
                "stage=continue_after_selection",
                "status=stopped_no_selected_yes",
                "message=Gemini #1 не выбрал ни одного рассказа для YouTube",
                f"selection_yes={selection_yes}",
                f"selection_no={selection_no}",
                "safe_input_prepared=0",
                "downstream=stopped",
            ],
        )
        _append_status(
            status_jsonl,
            {
                "timestamp": _now_iso(),
                "youtube_run_id": youtube_run_id,
                "stage": "youtube_continue_after_selection",
                "state": "stopped",
                "message": "gemini selection has zero yes items",
                "selection_yes": selection_yes,
                "selection_no": selection_no,
            },
        )
        return {
            "ok": True,
            "youtube_run_id": youtube_run_id,
            "status": "stopped_no_selected_yes",
            "message": "Gemini #1 не выбрал ни одного рассказа для YouTube",
            "selection_yes": selection_yes,
            "selection_no": selection_no,
            "prepared_safe_items": 0,
            "report_path": str(report_path),
            "selection_yes_json": parsed.get("selection_yes_json", ""),
            "selection_no_json": parsed.get("selection_no_json", ""),
        }

    safe = run_youtube_prepare_safe_input(
        config=config,
        options=YoutubePrepareSafeInputOptions(
            youtube_run_id=youtube_run_id,
            force=bool(options.force),
        ),
    )
    if not safe.get("ok", False):
        return safe

    prepared = int(safe.get("prepared", 0) or 0)
    _write_selection_workflow_report(
        report_path,
        [
            f"youtube_run_id={youtube_run_id}",
            "stage=continue_after_selection",
            "status=safe_input_prepared",
            "message=Подготовлен вход для Gemini #2 safe",
            f"selection_yes={selection_yes}",
            f"selection_no={selection_no}",
            f"safe_prepared={prepared}",
            f"safe_input_manifest={safe.get('safe_input_manifest', '')}",
            f"safe_input_dir={safe.get('safe_input_dir', '')}",
            f"safe_output_dir={safe.get('safe_output_dir', '')}",
        ],
    )
    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_continue_after_selection",
            "state": "safe_input_prepared",
            "selection_yes": selection_yes,
            "selection_no": selection_no,
            "safe_prepared": prepared,
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "status": "safe_input_prepared",
        "selection_yes": selection_yes,
        "selection_no": selection_no,
        "prepared_safe_items": prepared,
        "safe_input_manifest": safe.get("safe_input_manifest", ""),
        "safe_input_dir": safe.get("safe_input_dir", ""),
        "safe_output_dir": safe.get("safe_output_dir", ""),
        "report_path": str(report_path),
    }
