"""Clean per-story YouTube visual artifacts into quarantine."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


VISUAL_STALE_TOKENS = (
    "handsome and hot",
    "beautiful and sexy",
    "very attractive face",
    "attractive face",
    "smooth perfect skin",
    "model-like appearance",
    "post-plastic surgery",
    "stunning hourglass",
    "perfect skin",
    "smooth skin",
    "youthful",
    "young-looking",
    "college-age",
    "teenage",
    "teen",
    "boyish",
    "girlish",
)

PROMPT_REFUSAL_TOKENS = (
    "todo",
    "error",
    "i can't",
    "i cannot",
    "as an ai",
    "cannot comply",
    "i’m sorry",
    "i am sorry",
    "sorry, but",
)

_CACHE_KEYWORDS = ("character", "characters", "prompt", "prompts", "director", "scene", "frame", "frames", "runpod")
_CACHE_EXTS = {".txt", ".json", ".jsonl", ".log"}
_SCAN_EXTS = {".txt", ".json", ".jsonl", ".log"}
_EXCLUDED_SCAN_PARTS = ("_backup", "_bad", "_stale", "quarantine")
_BLOCKED_AGE_WORD_RE = re.compile(r"\b(teen|teenage|young-looking|youthful)\b", re.IGNORECASE)
_BROAD_ADULT_AGE_RE = re.compile(r"\b(adult|young adult|middle-aged|older adult|elderly|senior)\b", re.IGNORECASE)
_DECADE_AGE_RE = re.compile(
    r"\b((early|mid|late)\s+)?[2-9]0s\b|"
    r"\b(early|mid|late)\s*[-–—]?\s*to\s*[-–—]?\s*(early|mid|late)\s+[2-9]0s\b",
    re.IGNORECASE,
)
_EXACT_ADULT_AGE_RE = re.compile(r"\b(?:age(?:d)?\s+)?(\d{2})\s*(?:years?\s*old|[-–—]year[-–—]old)\b", re.IGNORECASE)
_ADULT_AGE_RANGE_RE = re.compile(
    r"\b(?:ages?\s+|aged\s+)?(\d{2})"
    r"(?:\s*[-–—]\s*to\s*[-–—]\s*|\s+to\s+|\s*[-–—]\s*)"
    r"(\d{2})(?:\s*years?\s*old|\s*[-–—]\s*year\s*[-–—]\s*old)?\b",
    re.IGNORECASE,
)


@dataclass
class YoutubeVisualsCleanOptions:
    story_id: str
    execute: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    return (config.root_dir / "output" / "youtube" / story_id).resolve()


def _director_dir(config: OrchestratorConfig) -> Path:
    rel = config.legacy_modules.get("director_2_0", "legacy/director_2_0")
    return (config.root_dir / rel).resolve()


def _safe_story_name(story_id: str) -> str:
    return re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", story_id).strip(" .") or "youtube_story"


def _legacy_story_dirs(config: OrchestratorConfig, story_id: str) -> dict[str, Path]:
    director_dir = _director_dir(config)
    safe = _safe_story_name(story_id)
    return {
        "legacy_stories": director_dir / "stories" / story_id,
        "legacy_orchestrator": director_dir / "stories_from_orchestrator" / safe,
    }


def _protected_paths(story_dir: Path) -> list[str]:
    return [
        str(story_dir / "00_source"),
        str(story_dir / "01_safe_text"),
        str(story_dir / "02_safe_story"),
        str(story_dir / "02_audio"),
        str(story_dir / "03_promo"),
        str(story_dir / "03_promo" / "text_ready_for_audio.txt"),
        str(story_dir / "04_audio"),
        str(story_dir / "04_audio" / "narration.mp3"),
        str(story_dir / "youtube_story_manifest.json"),
    ]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _protected_roots(story_dir: Path) -> list[Path]:
    return [
        story_dir / "00_source",
        story_dir / "01_safe_text",
        story_dir / "02_safe_story",
        story_dir / "02_audio",
        story_dir / "03_promo",
        story_dir / "04_audio",
        story_dir / "logs",
    ]


def _is_protected_output_path(path: Path, story_dir: Path) -> bool:
    return any(_is_under(path, root) for root in _protected_roots(story_dir))


def _fixed_cleanup_files(config: OrchestratorConfig, story_id: str) -> list[tuple[str, Path, str]]:
    story_dir = _story_dir(config, story_id)
    items: list[tuple[str, Path, str]] = [
        ("output", story_dir / "05_characters" / "characters.txt", "active output characters can be reused by visuals-run"),
        ("output", story_dir / "06_prompts" / "prompts_list.txt", "active output prompts can be reused by frames-runpod"),
        ("output", story_dir / "06_director" / "prompts_list.txt", "legacy output prompt fallback can be reused if 06_prompts is missing"),
        ("output", story_dir / "07_frames" / "frame_jobs.json", "stale frame job manifest can reuse old prompts"),
        ("output", story_dir / "07_frames" / "failed_frames.json", "stale failed frame state belongs to old frame run"),
    ]
    legacy_names = (
        "characters.txt",
        "prompts_list.txt",
        "scene_prompts.txt",
        "director_prompts.txt",
        "frames.json",
        "frame_jobs.json",
        "failed_frames.json",
    )
    for label, root in _legacy_story_dirs(config, story_id).items():
        for name in legacy_names:
            items.append((label, root / name, f"{label} visual artifact can be picked up by legacy director flow"))
    return items


def _output_cache_files(story_dir: Path) -> list[Path]:
    if not story_dir.is_dir():
        return []
    files: list[Path] = []
    for path in story_dir.rglob("*"):
        if not path.is_file():
            continue
        if _contains_excluded_part(path):
            continue
        if _is_protected_output_path(path, story_dir):
            continue
        if path.suffix.lower() not in _CACHE_EXTS:
            continue
        name = path.name.lower()
        parent_text = str(path.parent).lower()
        if any(keyword in name or keyword in parent_text for keyword in _CACHE_KEYWORDS):
            files.append(path)
    return sorted(files)


def _legacy_cache_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _contains_excluded_part(path):
            continue
        name = path.name.lower()
        if path.suffix.lower() not in _CACHE_EXTS:
            continue
        if any(keyword in name for keyword in _CACHE_KEYWORDS):
            files.append(path)
    return sorted(files)


def _quarantine_target(quarantine_dir: Path, label: str, source: Path) -> Path:
    clean_parts = [part for part in source.parts if part not in {"", "\\"}]
    basename = "__".join([label, *clean_parts[-3:]])
    basename = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", basename)
    target = quarantine_dir / basename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for idx in range(2, 1000):
        candidate = quarantine_dir / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
    return quarantine_dir / f"{stem}_{datetime.now().timestamp():.0f}{suffix}"


def _contains_excluded_part(path: Path, current_quarantine: Path | None = None) -> bool:
    text = str(path).lower()
    if current_quarantine and str(current_quarantine).lower() in text:
        return True
    return any(part in text for part in _EXCLUDED_SCAN_PARTS)


def _line_findings(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() not in _SCAN_EXTS:
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    findings: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        lowered = line.lower()
        terms = [token for token in VISUAL_STALE_TOKENS if token in lowered]
        if terms:
            findings.append({"path": str(path), "line": line_no, "terms": terms, "text": line.strip()[:500]})
    return findings


def _is_adult_age(value: str) -> bool:
    try:
        age = int(value)
    except ValueError:
        return False
    return 18 <= age <= 99


def _has_stable_adult_age_band(text: str) -> bool:
    if _BLOCKED_AGE_WORD_RE.search(text):
        return False
    if _BROAD_ADULT_AGE_RE.search(text):
        return True
    if _DECADE_AGE_RE.search(text):
        return True
    if any(_is_adult_age(match.group(1)) for match in _EXACT_ADULT_AGE_RE.finditer(text)):
        return True
    for match in _ADULT_AGE_RANGE_RE.finditer(text):
        start = int(match.group(1))
        end = int(match.group(2))
        if 18 <= start <= end <= 99:
            return True
    return False


def scan_visual_stale_tokens(
    *,
    roots: list[Path],
    current_quarantine: Path | None = None,
    excluded_roots: list[Path] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scanned_files = 0
    excluded = excluded_roots or []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(_is_under(path, excluded_root) for excluded_root in excluded):
                continue
            if _contains_excluded_part(path, current_quarantine=current_quarantine):
                continue
            if path.suffix.lower() not in _SCAN_EXTS:
                continue
            if "logs" in [part.lower() for part in path.parts]:
                continue
            scanned_files += 1
            findings.extend(_line_findings(path))
    return {"ok": not findings, "scanned_files": scanned_files, "findings": findings, "findings_count": len(findings)}


def validate_visual_prompts_file(path: Path) -> dict[str, Any]:
    missing = not path.is_file()
    findings = [] if missing else _line_findings(path)
    partial_path = path.with_name("prompts_list.partial.txt")
    checkpoint_path = path.with_name("director_checkpoint.json")
    prompts: list[str] = []
    prompt_findings: list[dict[str, Any]] = []
    if not missing:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            raw = ""
        prompts = [item.strip() for item in re.split(r"\n\s*\n+", raw) if item.strip()]
        for idx, prompt in enumerate(prompts, start=1):
            lowered = prompt.lower()
            if not prompt:
                prompt_findings.append({"index": idx, "reason": "empty_prompt"})
            if any(token in lowered for token in PROMPT_REFUSAL_TOKENS):
                prompt_findings.append({"index": idx, "reason": "refusal_or_placeholder_text", "text": prompt[:300]})
            if prompt.endswith(("...", "…")):
                prompt_findings.append({"index": idx, "reason": "truncated_prompt", "text": prompt[:300]})
    partial_exists = partial_path.is_file() or checkpoint_path.is_file()
    status = "missing" if missing else ("partial" if partial_exists else ("stale_or_invalid" if findings or prompt_findings or not prompts else "ok"))
    return {
        "ok": not missing and not partial_exists and not findings and not prompt_findings and bool(prompts),
        "status": status,
        "path": str(path),
        "prompts_count": len(prompts),
        "partial_path": str(partial_path),
        "partial_exists": partial_path.is_file(),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_exists": checkpoint_path.is_file(),
        "findings": findings,
        "prompt_findings": prompt_findings,
        "forbidden_terms_total": sum(len(item["terms"]) for item in findings),
    }


def validate_visual_characters_file(path: Path) -> dict[str, Any]:
    missing = not path.is_file()
    findings = [] if missing else _line_findings(path)
    adult_band_findings: list[dict[str, Any]] = []
    characters_count = 0
    if not missing:
        try:
            data = _read_json(path)
        except Exception:
            data = None
        characters = data.get("characters") if isinstance(data, dict) else []
        if isinstance(characters, list):
            characters_count = len(characters)
            for idx, item in enumerate(characters, start=1):
                if not isinstance(item, dict):
                    continue
                anchor = str(item.get("anchor", "") or "")
                if not _has_stable_adult_age_band(anchor):
                    adult_band_findings.append(
                        {
                            "id": str(item.get("id", f"CHAR_{idx}") or f"CHAR_{idx}"),
                            "role": str(item.get("role", "") or ""),
                            "reason": "missing stable adult age band",
                            "anchor": anchor,
                        }
                    )
    ok = not missing and not findings and not adult_band_findings
    return {
        "ok": ok,
        "status": "missing" if missing else ("stale_or_invalid" if not ok else "ok"),
        "path": str(path),
        "characters_count": characters_count,
        "findings": findings,
        "adult_band_findings": adult_band_findings,
        "forbidden_terms_total": sum(len(item["terms"]) for item in findings),
    }


def scan_legacy_visual_stale_sources(config: OrchestratorConfig, story_id: str) -> dict[str, Any]:
    return scan_visual_stale_tokens(roots=list(_legacy_story_dirs(config, story_id).values()))


def _patch_manifest_after_clean(story_dir: Path, quarantine_dir: Path) -> str:
    path = story_dir / "youtube_story_manifest.json"
    try:
        manifest = _read_json(path) if path.is_file() else {}
    except Exception:
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("status", {})
    if isinstance(manifest["status"], dict):
        manifest["status"].update({"characters_done": False, "director_done": False, "frames_done": False, "video_done": False})
    manifest.setdefault("pipeline_stage_status", {})
    if isinstance(manifest["pipeline_stage_status"], dict):
        manifest["pipeline_stage_status"].update(
            {"characters": "missing", "scenes_prompts": "missing", "director_prompts": "missing", "frames": "missing", "visuals": "blocked"}
        )
    manifest["characters"] = {"status": "missing", "path": str(story_dir / "05_characters" / "characters.txt"), "cleaned_at": _now_iso()}
    manifest["director_prompts"] = {"status": "missing", "path": str(story_dir / "06_prompts" / "prompts_list.txt"), "cleaned_at": _now_iso()}
    manifest["scenes_prompts"] = {"status": "missing", "path": str(story_dir / "06_prompts" / "prompts_list.txt"), "cleaned_at": _now_iso()}
    manifest["frames"] = {"status": "missing", "archived_to": str(quarantine_dir), "cleaned_at": _now_iso()}
    actual = manifest.get("actual_artifacts")
    if isinstance(actual, dict):
        for key in ("characters_txt", "prompts_list_txt", "frame_jobs_json", "failed_frames_json"):
            actual.pop(key, None)
    manifest["updated_at"] = _now_iso()
    _write_json(path, manifest)
    return str(path)


def run_youtube_visuals_clean(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualsCleanOptions,
) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, story_id)
    quarantine_dir = story_dir / f"_visuals_clean_quarantine_{_timestamp()}"
    fixed = _fixed_cleanup_files(config, story_id)
    candidates: dict[str, tuple[str, Path, str]] = {}
    for label, path, reason in fixed:
        candidates[str(path.resolve()).lower()] = (label, path, reason)
    for path in _output_cache_files(story_dir):
        candidates[str(path.resolve()).lower()] = ("output", path, "output visual cache/temp file matched cleanup keywords")
    for label, root in _legacy_story_dirs(config, story_id).items():
        for path in _legacy_cache_files(root):
            candidates[str(path.resolve()).lower()] = (label, path, f"{label} visual cache/temp file matched cleanup keywords")

    existing = [(label, path, reason) for label, path, reason in candidates.values() if path.exists()]
    skipped_missing = [str(path) for _, path, _ in candidates.values() if not path.exists()]
    protected = _protected_paths(story_dir)
    result: dict[str, Any] = {
        "story_id": story_id,
        "mode": "execute" if options.execute else "dry_run",
        "status": "dry_run" if not options.execute else "cleaning",
        "ok": story_dir.is_dir(),
        "story_dir": str(story_dir),
        "quarantine_dir": str(quarantine_dir),
        "cleanup_candidates": [{"path": str(path), "source": label, "reason": reason} for label, path, reason in existing],
        "moved_files": [],
        "skipped_missing_files": skipped_missing,
        "protected_paths": protected,
        "verification": {},
        "stale_token_scan": {},
        "blockers": [] if story_dir.is_dir() else ["missing_story_dir"],
        "next_action": "review cleanup candidates, then rerun with --execute" if not options.execute else "",
    }
    if not story_dir.is_dir():
        result["status"] = "missing_story"
        return result
    if not options.execute:
        return result

    quarantine_dir.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, str]] = []
    for label, source, reason in existing:
        if not source.exists():
            continue
        target = _quarantine_target(quarantine_dir, label, source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append({"source": str(source), "target": str(target), "reason": reason})

    manifest_path = _patch_manifest_after_clean(story_dir, quarantine_dir)
    verification_paths = [
        story_dir / "05_characters" / "characters.txt",
        story_dir / "06_prompts" / "prompts_list.txt",
        story_dir / "07_frames" / "frame_jobs.json",
        story_dir / "07_frames" / "failed_frames.json",
        _legacy_story_dirs(config, story_id)["legacy_stories"] / "characters.txt",
        _legacy_story_dirs(config, story_id)["legacy_stories"] / "prompts_list.txt",
        _legacy_story_dirs(config, story_id)["legacy_orchestrator"] / "characters.txt",
        _legacy_story_dirs(config, story_id)["legacy_orchestrator"] / "prompts_list.txt",
    ]
    still_exists = [str(path) for path in verification_paths if path.exists()]
    scan = scan_visual_stale_tokens(
        roots=[story_dir, *_legacy_story_dirs(config, story_id).values()],
        current_quarantine=quarantine_dir,
        excluded_roots=_protected_roots(story_dir),
    )
    blockers: list[str] = []
    if still_exists:
        blockers.append("youtube_visuals_clean_failed_remaining_files")
    if not scan.get("ok", False):
        blockers.append("youtube_visuals_clean_failed_remaining_stale_tokens")
    result.update(
        {
            "status": "clean" if not blockers else "blocked",
            "ok": not blockers,
            "moved_files": moved,
            "removed_files_count": len(moved),
            "manifest_path": manifest_path,
            "verification": {"ok": not still_exists, "checked_paths": [str(path) for path in verification_paths], "still_exists": still_exists},
            "stale_token_scan": scan,
            "blockers": blockers,
            "remaining_blockers": blockers,
            "next_action": (
                "run legacy Characters Bot from a clean story state"
                if not blockers
                else "inspect remaining blocker paths before regenerating characters"
            ),
        }
    )
    report_path = story_dir / "logs" / "youtube_visuals_clean_report.json"
    _write_json(report_path, {**result, "written_at": _now_iso()})
    result["report_path"] = str(report_path)
    return result
