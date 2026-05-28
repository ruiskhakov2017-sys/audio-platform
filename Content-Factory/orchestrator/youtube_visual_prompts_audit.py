"""Read-only diagnostics for YouTube visual prompts.

This module audits prompt length, dilution risks, continuity markers, and
prompt/job consistency. It never calls Gemini, ComfyUI, RunPod, or rewrites
production prompt artifacts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


REPORT_JSON_NAME = "youtube_visual_prompts_audit.json"
REPORT_TXT_NAME = "youtube_visual_prompts_audit.txt"
COMPACT_PREVIEW_NAME = "youtube_visual_prompts_compact_preview.txt"

BEAUTY_BIAS_RE = re.compile(
    r"\b(beautiful|handsome|attractive|hot|model-like|perfect face|very attractive face)\b",
    re.IGNORECASE,
)
NEGATIVE_WORDS_RE = re.compile(
    r"\b(front-facing|portrait|close-up face|looking at camera)\b",
    re.IGNORECASE,
)
FACE_AVOIDANCE_RE = re.compile(
    r"\b(avoid(?:ing)? (?:the )?face|without (?:showing )?(?:the )?face|"
    r"face (?:obscured|hidden|covered|turned away)|no face|do not show (?:the )?face)\b",
    re.IGNORECASE,
)
SAME_CHARACTER_RE = re.compile(r"\b(same character|same person|consistent character|character consistency)\b", re.IGNORECASE)
SAME_OBJECT_RE = re.compile(
    r"\b(same car|same vehicle|same object|same sedan|consistent car|consistent vehicle|object consistency)\b",
    re.IGNORECASE,
)
CAR_RE = re.compile(r"\b(car|sedan|vehicle|windshield|headlights|taillights|tire|wheel|dashboard)\b", re.IGNORECASE)
CHARACTER_RE = re.compile(r"\b(male|female|man|woman|husband|wife|father-in-law|mother-in-law)\b", re.IGNORECASE)
AVOID_FRONTAL_RE = re.compile(r"\b(avoid frontal|no frontal|without frontal|front-facing)\b", re.IGNORECASE)
LOOKING_AT_CAMERA_RE = re.compile(r"\b(looking at camera|looking into camera|staring at camera)\b", re.IGNORECASE)
CONTINUITY_RE = re.compile(
    r"\b(continuity|same character|same person|same car|same vehicle|same object|consistent character|"
    r"consistent car|consistent vehicle|global consistency|photorealistic rendering|clean composition|balanced lighting)\b",
    re.IGNORECASE,
)


@dataclass
class YoutubeVisualPromptsAuditOptions:
    story_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    return (config.root_dir / "output" / "youtube" / story_id).resolve()


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _strip_prompt_number(text: str) -> str:
    return re.sub(r"^\s*\d{1,5}[.)]\s*", "", text).strip()


def _load_prompts(path: Path) -> list[str]:
    raw = _read_text(path).strip()
    if not raw:
        return []
    if "\n\n" in raw:
        return [_strip_prompt_number(part) for part in re.split(r"\n\s*\n", raw) if part.strip()]
    return [_strip_prompt_number(line) for line in raw.splitlines() if line.strip()]


def _load_frame_jobs(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [job for job in jobs if isinstance(job, dict)]


def _load_characters(path: Path) -> dict[str, Any]:
    raw = _read_text(path).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {}


def _character_anchors(characters: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    chars = characters.get("characters")
    if not isinstance(chars, list):
        return out
    for idx, item in enumerate(chars, start=1):
        if not isinstance(item, dict):
            continue
        anchor = str(item.get("anchor", "") or "").strip()
        if not anchor:
            continue
        out.append(
            {
                "id": str(item.get("id", f"CHAR_{idx}") or f"CHAR_{idx}").strip(),
                "role": str(item.get("role", "") or "").strip(),
                "anchor": anchor,
            }
        )
    return out


def _approx_tokens(text: str) -> int:
    words = len(re.findall(r"\S+", text))
    return max(1, int(math.ceil(max(len(text) / 4.0, words * 1.33))))


def _position_bucket(text: str, match_start: int) -> str:
    if match_start < 0:
        return "none"
    ratio = match_start / max(len(text), 1)
    if ratio <= 0.25:
        return "first_25_percent"
    if ratio >= 0.75:
        return "last_25_percent"
    return "middle"


def _continuity_position(text: str) -> str:
    match = CONTINUITY_RE.search(text)
    return _position_bucket(text, match.start() if match else -1)


def _starts_with_style_or_scene(prompt: str, style_prefix: str) -> str:
    start = prompt.strip().lower()
    style_start = style_prefix.strip().lower()[:40]
    if style_start and start.startswith(style_start):
        return "style"
    if re.match(r"^(a|an|the)\s+|^(wide|extreme|over-the-shoulder|side|profile|silhouette|reflection|low-angle)", start):
        return "scene"
    return "unknown"


def _matching_anchors(prompt: str, anchors: list[dict[str, str]]) -> list[str]:
    matched: list[str] = []
    normalized_prompt = prompt.lower()
    for item in anchors:
        anchor = item["anchor"].strip().lower()
        if anchor and anchor in normalized_prompt:
            matched.append(item["id"])
    return matched


def _matched_terms(pattern: re.Pattern[str], text: str) -> list[str]:
    return sorted({match.group(0) for match in pattern.finditer(text)}, key=str.lower)


def _prompt_metrics(index: int, prompt: str, *, style_prefix: str, anchors: list[dict[str, str]]) -> dict[str, Any]:
    contains_anchor_ids = _matching_anchors(prompt, anchors)
    contains_face_avoidance = bool(FACE_AVOIDANCE_RE.search(prompt))
    contains_same_character = bool(SAME_CHARACTER_RE.search(prompt))
    contains_same_object = bool(SAME_OBJECT_RE.search(prompt))
    contains_car = bool(CAR_RE.search(prompt))
    contains_character = bool(contains_anchor_ids or CHARACTER_RE.search(prompt))
    beauty_terms = _matched_terms(BEAUTY_BIAS_RE, prompt)
    negative_terms = _matched_terms(NEGATIVE_WORDS_RE, prompt)
    conflicts = {
        "avoid_face_plus_attractive_face": bool(contains_face_avoidance and beauty_terms),
        "avoid_frontal_plus_looking_at_camera": bool(AVOID_FRONTAL_RE.search(prompt) and LOOKING_AT_CAMERA_RE.search(prompt)),
        "same_car_missing_when_car_appears": bool(contains_car and not contains_same_object),
        "same_character_missing_when_character_appears": bool(contains_character and not contains_same_character),
    }
    return {
        "prompt_index": index,
        "char_count": len(prompt),
        "word_count": len(re.findall(r"\S+", prompt)),
        "approximate_token_count": _approx_tokens(prompt),
        "line_count": max(1, prompt.count("\n") + 1),
        "starts_with_style_or_scene": _starts_with_style_or_scene(prompt, style_prefix),
        "contains_character_anchor": bool(contains_anchor_ids),
        "character_anchor_ids": contains_anchor_ids,
        "contains_face_avoidance": contains_face_avoidance,
        "contains_same_character": contains_same_character,
        "contains_same_car_same_vehicle_same_object": contains_same_object,
        "contains_car_or_vehicle": contains_car,
        "contains_negative_words": bool(negative_terms),
        "negative_words": negative_terms,
        "contains_beauty_bias_words": bool(beauty_terms),
        "beauty_bias_words": beauty_terms,
        "continuity_rules_position": _continuity_position(prompt),
        "conflicting_instructions": conflicts,
    }


def _clean_anchor(anchor: str) -> str:
    cleaned = re.sub(r"\bhandsome and hot\s+", "", anchor, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbeautiful and sexy\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bvery attractive face,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmodel-like appearance,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bsmooth perfect skin,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bsmooth skin,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = BEAUTY_BIAS_RE.sub("", cleaned)
    cleaned = re.sub(r"\bvery\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return cleaned


def _clean_compact_text(text: str) -> str:
    cleaned = text
    for pattern in (
        r"\bhandsome and hot\s+",
        r"\bbeautiful and sexy\s+",
        r"\bvery attractive face,?\s*",
        r"\bmodel-like appearance,?\s*",
        r"\bsmooth perfect skin,?\s*",
        r"\bsmooth skin,?\s*",
    ):
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = BEAUTY_BIAS_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return cleaned


def _shorten_sentence(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)].rsplit(" ", 1)[0].strip()
    return cut + "."


def _shot_fragment(prompt: str) -> str:
    first_sentence = re.split(r"(?<=[.!?])\s+", prompt.strip(), maxsplit=1)[0]
    return _shorten_sentence(_clean_compact_text(first_sentence), 150)


def _compact_candidate(prompt: str, characters: dict[str, Any], anchors: list[dict[str, str]]) -> str:
    style = str(characters.get("style_prompt_prefix", "") or "").strip()
    suffix = str(characters.get("global_consistency_suffix", "") or "").strip()
    matched = [item for item in anchors if item["id"] in _matching_anchors(prompt, anchors)]
    anchor_bits = [
        _shorten_sentence(f"{item['role'] or item['id']}: {_clean_anchor(item['anchor'])}", 95)
        for item in matched[:2]
    ]
    character_sentence = "Character continuity: " + "; ".join(anchor_bits) + "." if anchor_bits else "Character continuity: keep the same recurring people from the story."
    object_sentence = ""
    if CAR_RE.search(prompt):
        object_sentence = "Object continuity: keep the same dark sedan/old car design across this road sequence."
    shot_sentence = "Shot: " + _shot_fragment(prompt)
    style_sentence = "Style: " + _shorten_sentence(", ".join(x for x in (style, suffix) if x), 140)
    avoidance_sentence = "Avoid: frontal portraits, close-up faces, looking at camera."
    parts = [character_sentence, object_sentence, shot_sentence, style_sentence, avoidance_sentence]
    candidate = " ".join(part for part in parts if part)
    if len(candidate) > 500:
        candidate = " ".join(
            part
            for part in [
                _shorten_sentence(character_sentence, 200),
                _shorten_sentence(object_sentence, 90) if object_sentence else "",
                _shorten_sentence(shot_sentence, 120),
                _shorten_sentence(style_sentence, 80),
                avoidance_sentence,
            ]
            if part
        )
    return _shorten_sentence(candidate, 500)


def _compare_jobs_to_prompts(prompts: list[str], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for idx, prompt in enumerate(prompts, start=1):
        job = jobs[idx - 1] if idx - 1 < len(jobs) else {}
        job_prompt = str(job.get("prompt", "") or "")
        if job_prompt != prompt:
            mismatches.append(
                {
                    "prompt_index": idx,
                    "prompt_chars": len(prompt),
                    "job_prompt_chars": len(job_prompt),
                    "prompt_hash": hashlib.sha1(prompt.encode("utf-8")).hexdigest(),
                    "job_prompt_hash": hashlib.sha1(job_prompt.encode("utf-8")).hexdigest(),
                }
            )
    return {
        "jobs_count": len(jobs),
        "mismatch_count": len(mismatches),
        "first_10_mismatches": mismatches[:10],
    }


def _summary(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(metrics)
    chars = [int(item["char_count"]) for item in metrics]
    words = [int(item["word_count"]) for item in metrics]
    conflict = lambda key: sum(1 for item in metrics if item["conflicting_instructions"].get(key))
    worst = sorted(
        metrics,
        key=lambda item: (
            int(item["char_count"]),
            len(item.get("beauty_bias_words", [])),
            int(item["approximate_token_count"]),
        ),
        reverse=True,
    )[:10]
    return {
        "total_prompts": total,
        "avg_chars": round(sum(chars) / total, 2) if total else 0,
        "max_chars": max(chars) if chars else 0,
        "avg_words": round(sum(words) / total, 2) if total else 0,
        "max_words": max(words) if words else 0,
        "prompts_over_1000_chars": sum(1 for value in chars if value > 1000),
        "prompts_over_1500_chars": sum(1 for value in chars if value > 1500),
        "prompts_over_2000_chars": sum(1 for value in chars if value > 2000),
        "prompts_with_face_conflict": conflict("avoid_face_plus_attractive_face"),
        "prompts_with_beauty_bias": sum(1 for item in metrics if item["contains_beauty_bias_words"]),
        "prompts_with_car_but_no_same_car": conflict("same_car_missing_when_car_appears"),
        "prompts_with_character_but_no_same_character": conflict("same_character_missing_when_character_appears"),
        "first_10_worst_prompts": [
            {
                "prompt_index": item["prompt_index"],
                "char_count": item["char_count"],
                "word_count": item["word_count"],
                "approximate_token_count": item["approximate_token_count"],
                "beauty_bias_words": item["beauty_bias_words"],
                "conflicting_instructions": item["conflicting_instructions"],
                "preview": _shorten_sentence(str(item.get("prompt", "")), 260),
            }
            for item in worst
        ],
    }


def _txt_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    files = report["files"]
    lines = [
        "YouTube Visual Prompts Audit",
        f"story_id: {report['story_id']}",
        f"written_at: {report['written_at']}",
        "",
        "Files:",
        f"- characters: {files['characters_path']}",
        f"- prompts: {files['prompts_path']}",
        f"- frame_jobs: {files['frame_jobs_path']}",
        "",
        "Summary:",
        f"- total_prompts: {summary['total_prompts']}",
        f"- avg_chars: {summary['avg_chars']}",
        f"- max_chars: {summary['max_chars']}",
        f"- avg_words: {summary['avg_words']}",
        f"- max_words: {summary['max_words']}",
        f"- prompts_over_1000_chars: {summary['prompts_over_1000_chars']}",
        f"- prompts_over_1500_chars: {summary['prompts_over_1500_chars']}",
        f"- prompts_over_2000_chars: {summary['prompts_over_2000_chars']}",
        f"- prompts_with_face_conflict: {summary['prompts_with_face_conflict']}",
        f"- prompts_with_beauty_bias: {summary['prompts_with_beauty_bias']}",
        f"- prompts_with_car_but_no_same_car: {summary['prompts_with_car_but_no_same_car']}",
        f"- prompts_with_character_but_no_same_character: {summary['prompts_with_character_but_no_same_character']}",
        "",
        "Diagnosis:",
        f"- prompt_overload_risk: {report['diagnosis']['prompt_overload_risk']}",
        f"- face_anchor_conflict_risk: {report['diagnosis']['face_anchor_conflict_risk']}",
        f"- object_continuity_gap: {report['diagnosis']['object_continuity_gap']}",
        f"- compact_mode_recommended: {report['diagnosis']['compact_mode_recommended']}",
        "",
        "Worst prompts:",
    ]
    for item in summary["first_10_worst_prompts"]:
        lines.append(
            f"- #{item['prompt_index']}: chars={item['char_count']} words={item['word_count']} "
            f"tokens~={item['approximate_token_count']} preview={item['preview']}"
        )
    lines.append("")
    return "\n".join(lines)


def _preview_text(prompts: list[str], characters: dict[str, Any], anchors: list[dict[str, str]]) -> str:
    blocks: list[str] = ["YouTube Visual Prompts Compact Preview", ""]
    for idx, prompt in enumerate(prompts[:5], start=1):
        blocks.extend(
            [
                "=" * 80,
                f"Prompt {idx}",
                "",
                "ORIGINAL:",
                prompt,
                "",
                "COMPACT CANDIDATE:",
                _compact_candidate(prompt, characters, anchors),
                "",
            ]
        )
    return "\n".join(blocks).rstrip() + "\n"


def run_youtube_visual_prompts_audit(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualPromptsAuditOptions,
) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, story_id)
    logs_dir = story_dir / "logs"
    characters_path = story_dir / "05_characters" / "characters.txt"
    prompts_path = story_dir / "06_prompts" / "prompts_list.txt"
    frame_jobs_path = story_dir / "07_frames" / "frame_jobs.json"
    manifest_path = story_dir / "youtube_story_manifest.json"
    report_json_path = logs_dir / REPORT_JSON_NAME
    report_txt_path = logs_dir / REPORT_TXT_NAME
    compact_preview_path = logs_dir / COMPACT_PREVIEW_NAME

    missing = [str(path) for path in (story_dir, characters_path, prompts_path) if not path.exists()]
    characters = _load_characters(characters_path)
    anchors = _character_anchors(characters)
    prompts = _load_prompts(prompts_path)
    jobs = _load_frame_jobs(frame_jobs_path)
    style_prefix = str(characters.get("style_prompt_prefix", "") or "")
    metrics = [
        {**_prompt_metrics(idx, prompt, style_prefix=style_prefix, anchors=anchors), "prompt": prompt}
        for idx, prompt in enumerate(prompts, start=1)
    ]
    summary = _summary(metrics)
    job_compare = _compare_jobs_to_prompts(prompts, jobs) if jobs else {"jobs_count": 0, "mismatch_count": None, "first_10_mismatches": []}
    diagnosis = {
        "prompt_overload_risk": bool(summary["avg_chars"] > 800 or summary["prompts_over_1000_chars"] > 0),
        "face_anchor_conflict_risk": bool(summary["prompts_with_beauty_bias"] > 0),
        "object_continuity_gap": bool(summary["prompts_with_car_but_no_same_car"] > 0),
        "compact_mode_recommended": bool(
            summary["prompts_over_1000_chars"] > 0
            or summary["prompts_with_beauty_bias"] > 0
            or summary["prompts_with_car_but_no_same_car"] > 0
        ),
        "production_files_modified": False,
    }
    metrics_for_json = [{k: v for k, v in item.items() if k != "prompt"} for item in metrics]
    report = {
        "ok": not missing,
        "status": "missing_inputs" if missing else "done",
        "mode": "read_only_diagnostic",
        "story_id": story_id,
        "story_dir": str(story_dir),
        "written_at": _now_iso(),
        "files": {
            "characters_path": str(characters_path),
            "prompts_path": str(prompts_path),
            "frame_jobs_path": str(frame_jobs_path),
            "manifest_path": str(manifest_path),
            "report_json_path": str(report_json_path),
            "report_txt_path": str(report_txt_path),
            "compact_preview_path": str(compact_preview_path),
        },
        "missing": missing,
        "characters": {
            "anchors_count": len(anchors),
            "style_name": characters.get("style_name"),
            "style_prompt_prefix": characters.get("style_prompt_prefix"),
            "global_consistency_suffix": characters.get("global_consistency_suffix"),
            "anchor_ids": [item["id"] for item in anchors],
        },
        "summary": summary,
        "frame_jobs_compare": job_compare,
        "manifest_exists": manifest_path.is_file(),
        "prompt_metrics": metrics_for_json,
        "diagnosis": diagnosis,
    }

    _write_json(report_json_path, report)
    _write_text(report_txt_path, _txt_report(report))
    _write_text(compact_preview_path, _preview_text(prompts, characters, anchors))
    report["changed_files"] = [str(report_json_path), str(report_txt_path), str(compact_preview_path)]
    return report
