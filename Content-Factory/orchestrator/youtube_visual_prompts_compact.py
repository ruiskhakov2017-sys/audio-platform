"""Compact prompt formatter for YouTube visuals.

This is a local, mechanical formatter. It never calls Gemini, ComfyUI, RunPod,
or rewrites the raw director output.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


COMPACT_PROMPTS_NAME = "prompts_list_compact.txt"
COMPACT_FRAME_JOBS_NAME = "frame_jobs_compact.json"
COMPACT_REPORT_NAME = "youtube_visual_prompts_compact_report.json"
NOFACE_PROMPTS_NAME = "prompts_list_noface_compact.txt"
NOFACE_FRAME_JOBS_NAME = "frame_jobs_noface_compact.json"
NOFACE_REPORT_NAME = "youtube_visual_prompts_noface_compact_report.json"
BALANCED_PROMPTS_NAME = "prompts_list_balanced_compact.txt"
BALANCED_FRAME_JOBS_NAME = "frame_jobs_balanced_compact.json"
BALANCED_REPORT_NAME = "youtube_visual_prompts_balanced_compact_report.json"
PAYLOAD_DEBUG_NAME = "youtube_comfyui_payload_debug.json"
VALID_COMPACT_MODES = {"compact", "noface", "balanced"}

BEAUTY_RE = re.compile(
    r"\b(beautiful|handsome|hot|very attractive|attractive face|model-like|perfect face|pretty face|sexy|seductive)\b",
    re.IGNORECASE,
)
FACE_CENTRIC_RE = re.compile(
    r"\b(face as main subject|close-up face|macro portrait|front-facing portrait|front-facing|"
    r"looking at camera|looking into camera|direct eye contact with camera|portrait framing|portrait)\b",
    re.IGNORECASE,
)
CAR_RE = re.compile(r"\b(car|vehicle|sedan|parking|driveway|road|windshield|headlights|taillights|tire|wheel|dashboard)\b", re.IGNORECASE)
LOCATION_RE = re.compile(r"\b(house|home|kitchen|bedroom|living room|bathroom|office|apartment|hallway)\b", re.IGNORECASE)
SHOT_RE = re.compile(
    r"\b(wide shot|extreme wide shot|from behind|side view|side profile|profile view|over-the-shoulder|"
    r"silhouette|reflection|partial shadow|low-angle|high-angle|establishing shot)\b",
    re.IGNORECASE,
)


@dataclass
class YoutubeVisualPromptsCompactOptions:
    story_id: str
    execute: bool = False
    mode: str = "compact"


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


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _strip_prompt_number(text: str) -> str:
    return re.sub(r"^\s*\d{1,5}[.)]\s*", "", text).strip()


def _load_prompts(path: Path) -> list[str]:
    raw = _read_text(path).strip()
    if not raw:
        return []
    if "\n\n" in raw:
        return [_strip_prompt_number(part) for part in re.split(r"\n\s*\n", raw) if part.strip()]
    return [_strip_prompt_number(line) for line in raw.splitlines() if line.strip()]


def _load_characters(path: Path) -> dict[str, Any]:
    raw = _read_text(path).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {}


def _anchors(characters: dict[str, Any]) -> list[dict[str, str]]:
    chars = characters.get("characters")
    if not isinstance(chars, list):
        return []
    out: list[dict[str, str]] = []
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


def _prompt_has_anchor(prompt: str, anchor: str) -> bool:
    return anchor.strip().lower() in prompt.lower()


def _matched_roles(prompt: str, anchors: list[dict[str, str]]) -> set[str]:
    roles: set[str] = set()
    for item in anchors:
        role = item.get("role", "").lower()
        if _prompt_has_anchor(prompt, item["anchor"]):
            roles.add(role)
    return roles


def _clean_text(text: str) -> tuple[str, int]:
    before_terms = len(BEAUTY_RE.findall(text))
    cleaned = re.sub(r"\bhandsome and hot\s+", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbeautiful and sexy\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bvery attractive face,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\battractive face,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmodel-like appearance,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = BEAUTY_RE.sub("", cleaned)
    cleaned = FACE_CENTRIC_RE.sub("", cleaned)
    cleaned = re.sub(r"\bsmooth perfect skin,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bsmooth skin,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b[a-z]+(?:\s+[a-z]+){0,2}\s+eyes,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bface,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bappearance,?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bof\s+and\s+(male|female)\b", r"of \1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwhere\s+and\s+(male|female)\b", r"where \1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\band\s+and\s+", "and ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return cleaned, before_terms


def _join_style_text(characters: dict[str, Any]) -> str:
    style = str(characters.get("style_prompt_prefix", "") or "").strip()
    suffix = str(characters.get("global_consistency_suffix", "") or "").strip()
    if style and suffix:
        return f"{style}, {suffix}"
    return style or suffix


def _style_phrase_score(phrase: str) -> int:
    lower = phrase.lower()
    score = 0
    if any(term in lower for term in ("aesthetic", "arri", "alexa", "anamorphic", "50mm", "35mm", "color grading", "palette", "lighting", "halation", "film grain", "cinematography", "lens", "shadows")):
        score += 3
    if any(term in lower for term in ("sharp focus", "8k", "highly detailed", "masterpiece", "realistic skin", "depth of field", "clean composition", "balanced lighting", "photorealistic")):
        score -= 1
    return score


def _balanced_style_sentence(characters: dict[str, Any]) -> str:
    raw_style = _join_style_text(characters)
    phrases = [re.sub(r"\s+", " ", part).strip(" ,.") for part in raw_style.split(",") if part.strip()]
    if not phrases:
        return "Style: cinematic realistic story frame, muted palette, natural light, varied composition."
    selected: list[str] = []
    for phrase in phrases:
        if _style_phrase_score(phrase) <= 0 and len(selected) >= 3:
            continue
        if phrase.lower() == "arri alexa 65":
            phrase = "ARRI Alexa 65 look"
        selected.append(phrase)
        if len(selected) >= 5:
            break
    if not selected:
        selected = phrases[:4]
    return "Style: " + _shorten(", ".join(selected), 190) + "."


def _style_preserved(raw_style: str, balanced_style: str) -> bool:
    raw_lower = raw_style.lower()
    balanced_lower = balanced_style.lower()
    markers = [
        "premium streaming",
        "arri alexa",
        "neo noir",
        "anamorphic",
        "cold blue",
        "amber",
        "soft diffused",
        "halation",
        "50mm",
        "desaturated",
        "static camera",
        "practical lighting",
        "muted color",
    ]
    relevant = [marker for marker in markers if marker in raw_lower]
    if not relevant:
        return bool(raw_style.strip() and balanced_style.strip())
    return any(marker in balanced_lower for marker in relevant)


def _first_sentence(prompt: str) -> str:
    return re.split(r"(?<=[.!?])\s+", prompt.strip(), maxsplit=1)[0].strip()


def _sentences(prompt: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", prompt.strip()) if part.strip()]


def _shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)].rsplit(" ", 1)[0].strip(" ,")
    cut = re.sub(r"\b(wearing|with|while|where|looking|towards|and|or|a|an|the|as|in|on|at|of)$", "", cut, flags=re.IGNORECASE).strip(" ,")
    return cut + "."


def _continuity_sentences(prompt: str, anchors: list[dict[str, str]]) -> list[str]:
    roles = _matched_roles(prompt, anchors)
    out: list[str] = []
    if any(role in roles for role in ("husband", "male", "father-in-law")) or re.search(r"\b(male|man|husband|father-in-law)\b", prompt, re.IGNORECASE):
        out.append("The same recurring male lead, shown from behind or side view, not facing the camera.")
    if any(role in roles for role in ("wife", "female", "mother-in-law")) or re.search(r"\b(female|woman|wife|mother-in-law)\b", prompt, re.IGNORECASE):
        out.append("The same recurring female lead, shown from behind or side view, not facing the camera.")
    return out


def _object_sentences(prompt: str) -> list[str]:
    out: list[str] = []
    if CAR_RE.search(prompt):
        out.append("The same recurring car throughout the story, consistent color and body shape.")
    if LOCATION_RE.search(prompt):
        out.append("Consistent recurring location/environment details, no random set redesign.")
    return out


def _noface_object_sentences(prompt: str) -> list[str]:
    out: list[str] = []
    if CAR_RE.search(prompt):
        out.append("Same recurring car; consistent color/body shape, do not change vehicle identity.")
    if LOCATION_RE.search(prompt):
        out.append("Same recurring location/environment, consistent layout and set dressing.")
    return out


def _balanced_object_sentences(prompt: str) -> list[str]:
    out: list[str] = []
    if CAR_RE.search(prompt):
        out.append("Keep the same recurring car with consistent color and body shape.")
    if LOCATION_RE.search(prompt):
        out.append("Keep recurring location details consistent without redesigning the set.")
    return out


def _scene_sentence(prompt: str) -> str:
    cleaned, _ = _clean_text(_first_sentence(prompt))
    shot_terms = [match.group(0).lower() for match in SHOT_RE.finditer(prompt)]
    if shot_terms and shot_terms[0] not in cleaned.lower():
        cleaned = f"{shot_terms[0]}. {cleaned}"
    return "Scene: " + _shorten(cleaned, 260)


def compact_prompt(prompt: str, anchors: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    continuity = _continuity_sentences(prompt, anchors)
    objects = _object_sentences(prompt)
    scene = _scene_sentence(prompt)
    style = "Style: Cinematic realistic film still, consistent muted color palette, natural lighting, environmental composition."
    avoidance = "Avoid frontal face close-ups, portrait framing, direct eye contact with camera, changing character identity."
    parts = [*continuity, *objects, scene, style, avoidance]
    compact = " ".join(part for part in parts if part)
    _, beauty_removed = _clean_text(prompt)
    if len(compact) > 900:
        scene = "Scene: " + _shorten(_clean_text(_first_sentence(prompt))[0], 170)
        compact = " ".join([*continuity, *objects, scene, style, avoidance])
    return compact[:900].rstrip(), {
        "beauty_bias_removed_count": beauty_removed,
        "character_continuity_added_count": len(continuity),
        "car_continuity_added_count": 1 if any("recurring car" in item for item in objects) else 0,
        "location_continuity_added_count": 1 if any("location/environment" in item for item in objects) else 0,
    }


def noface_compact_prompt(prompt: str, anchors: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    roles = _matched_roles(prompt, anchors)
    has_male = any(role in roles for role in ("husband", "male", "father-in-law")) or re.search(
        r"\b(male|man|husband|father-in-law)\b", prompt, re.IGNORECASE
    )
    has_female = any(role in roles for role in ("wife", "female", "mother-in-law")) or re.search(
        r"\b(female|woman|wife|mother-in-law)\b", prompt, re.IGNORECASE
    )
    continuity = [
        "Same recurring characters, faces not clearly visible, no frontal portraits.",
        "Use the same recurring anonymous protagonists throughout the sequence.",
    ]
    if has_male:
        continuity.append("Same recurring male lead, back or side profile, face hidden or turned away.")
    if has_female:
        continuity.append("Same recurring female lead, back or side profile, face hidden or turned away.")
    objects = _noface_object_sentences(prompt)
    scene = "Scene: " + _shorten(_clean_text(_first_sentence(prompt))[0], 75)
    style = "Style: realistic film still, muted palette, natural light."
    avoidance = "Avoid front-facing faces, close-up portraits, direct eye contact, changing ethnicity or car model."
    parts = [*continuity, *objects, scene, style, avoidance]
    compact = " ".join(part for part in parts if part)
    _, beauty_removed = _clean_text(prompt)
    if len(compact) > 700:
        scene = "Scene: " + _shorten(_clean_text(_first_sentence(prompt))[0], 60)
        compact = " ".join([*continuity, *objects, scene, style, avoidance])
    return compact[:700].rstrip(), {
        "beauty_bias_removed_count": beauty_removed,
        "character_continuity_added_count": len(continuity),
        "car_continuity_added_count": 1 if any("recurring car" in item for item in objects) else 0,
        "location_continuity_added_count": 1 if any("recurring location" in item for item in objects) else 0,
    }


def balanced_compact_prompt(prompt: str, anchors: list[dict[str, str]], balanced_style: str) -> tuple[str, dict[str, Any]]:
    roles = _matched_roles(prompt, anchors)
    has_people = bool(roles) or re.search(
        r"\b(male|female|man|woman|husband|wife|student|father-in-law|mother-in-law)\b",
        prompt,
        re.IGNORECASE,
    )
    continuity: list[str] = []
    if has_people:
        continuity.append("Keep recurring protagonists visually consistent through clothing silhouette and scene role; faces are secondary.")
    objects = _balanced_object_sentences(prompt)
    prompt_sentences = _sentences(prompt)
    cleaned_first, beauty_removed = _clean_text(prompt_sentences[0] if prompt_sentences else prompt)
    scene = "Scene: " + _shorten(cleaned_first, 330)
    context = ""
    if len(prompt_sentences) > 1:
        cleaned_second, _ = _clean_text(prompt_sentences[1])
        context = "Environment: " + _shorten(cleaned_second, 170)
    style = balanced_style
    avoidance = "Avoid frontal face close-ups, portrait-first framing, direct eye contact, changing identity."
    parts = [*continuity, *objects, scene, context, style, avoidance]
    compact = " ".join(part for part in parts if part)
    if len(compact) > 850:
        scene = "Scene: " + _shorten(cleaned_first, 250)
        context = "Environment: " + _shorten(_clean_text(prompt_sentences[1])[0], 120) if len(prompt_sentences) > 1 else ""
        compact = " ".join([*continuity, *objects, scene, context, style, avoidance])
    return compact[:850].rstrip(), {
        "beauty_bias_removed_count": beauty_removed,
        "character_continuity_added_count": len(continuity),
        "car_continuity_added_count": 1 if any("recurring car" in item for item in objects) else 0,
        "location_continuity_added_count": 1 if any("location details" in item for item in objects) else 0,
    }


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "compact").strip().lower()
    return normalized if normalized in VALID_COMPACT_MODES else "compact"


def compact_paths(config: OrchestratorConfig, story_id: str, mode: str = "compact") -> dict[str, Path]:
    mode = _normalize_mode(mode)
    story_dir = _story_dir(config, story_id)
    prompt_name = {"balanced": BALANCED_PROMPTS_NAME, "noface": NOFACE_PROMPTS_NAME}.get(mode, COMPACT_PROMPTS_NAME)
    jobs_name = {"balanced": BALANCED_FRAME_JOBS_NAME, "noface": NOFACE_FRAME_JOBS_NAME}.get(mode, COMPACT_FRAME_JOBS_NAME)
    report_name = {"balanced": BALANCED_REPORT_NAME, "noface": NOFACE_REPORT_NAME}.get(mode, COMPACT_REPORT_NAME)
    return {
        "story_dir": story_dir,
        "characters": story_dir / "05_characters" / "characters.txt",
        "raw_prompts": story_dir / "06_prompts" / "prompts_list.txt",
        "compact_prompts": story_dir / "06_prompts" / prompt_name,
        "raw_frame_jobs": story_dir / "07_frames" / "frame_jobs.json",
        "compact_frame_jobs": story_dir / "07_frames" / jobs_name,
        "audit_report": story_dir / "logs" / "youtube_visual_prompts_audit.json",
        "compact_report": story_dir / "logs" / report_name,
        "payload_debug": story_dir / "logs" / PAYLOAD_DEBUG_NAME,
    }


def load_compact_prompts(config: OrchestratorConfig, story_id: str, mode: str = "compact") -> list[str]:
    return _load_prompts(compact_paths(config, story_id, mode)["compact_prompts"])


def compact_prompt_status(config: OrchestratorConfig, story_id: str) -> dict[str, Any]:
    paths = compact_paths(config, story_id, "compact")
    noface_paths = compact_paths(config, story_id, "noface")
    balanced_paths = compact_paths(config, story_id, "balanced")
    compact = _load_prompts(paths["compact_prompts"])
    noface = _load_prompts(noface_paths["compact_prompts"])
    balanced = _load_prompts(balanced_paths["compact_prompts"])
    raw_exists = paths["raw_prompts"].is_file()
    compact_exists = paths["compact_prompts"].is_file()
    noface_exists = noface_paths["compact_prompts"].is_file()
    balanced_exists = balanced_paths["compact_prompts"].is_file()
    audit = _read_json(paths["audit_report"])
    audit_diag = audit.get("diagnosis", {}) if isinstance(audit, dict) else {}
    avg_chars = round(sum(len(p) for p in compact) / len(compact), 2) if compact else 0
    noface_avg_chars = round(sum(len(p) for p in noface) / len(noface), 2) if noface else 0
    balanced_avg_chars = round(sum(len(p) for p in balanced) / len(balanced), 2) if balanced else 0
    return {
        "raw_exists": raw_exists,
        "compact_exists": compact_exists,
        "noface_compact_exists": noface_exists,
        "balanced_compact_exists": balanced_exists,
        "compact_prompts_count": len(compact),
        "compact_avg_chars": avg_chars,
        "compact_report_path": str(paths["compact_report"]),
        "noface_compact_prompts_count": len(noface),
        "noface_compact_avg_chars": noface_avg_chars,
        "noface_compact_report_path": str(noface_paths["compact_report"]),
        "balanced_compact_prompts_count": len(balanced),
        "balanced_compact_avg_chars": balanced_avg_chars,
        "balanced_compact_report_path": str(balanced_paths["compact_report"]),
        "available_prompt_modes": ["raw"]
        + (["compact"] if compact_exists else [])
        + (["balanced_compact"] if balanced_exists else [])
        + (["noface_compact"] if noface_exists else []),
        "recommended_prompt_mode": "balanced_compact"
        if balanced_exists
        else ("compact" if audit_diag.get("compact_mode_recommended") else "raw"),
    }


def _frame_jobs_payload(story_id: str, prompts: list[str], frames_dir: Path, mode: str) -> dict[str, Any]:
    prompt_file = {"balanced": BALANCED_PROMPTS_NAME, "noface": NOFACE_PROMPTS_NAME}.get(mode, COMPACT_PROMPTS_NAME)
    prompt_mode = {"balanced": "balanced_compact", "noface": "noface_compact"}.get(mode, "compact")
    jobs = [
        {
            "prompt_index": idx,
            "prompt": prompt,
            "output_frame_path": str(frames_dir / f"frame_{idx:04d}.png"),
            "status": "pending",
        }
        for idx, prompt in enumerate(prompts, start=1)
    ]
    return {
        "schema_version": 1,
        "updated_at": _now_iso(),
        "story_id": story_id,
        "prompt_mode": prompt_mode,
        "compact_mode": mode,
        "prompts_path": str(frames_dir.parent / "06_prompts" / prompt_file),
        "frames_dir": str(frames_dir),
        "jobs": jobs,
    }


def run_youtube_visual_prompts_compact(
    *,
    config: OrchestratorConfig,
    options: YoutubeVisualPromptsCompactOptions,
) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    mode = _normalize_mode(options.mode)
    paths = compact_paths(config, story_id, mode)
    raw_prompts = _load_prompts(paths["raw_prompts"])
    characters = _load_characters(paths["characters"])
    anchors = _anchors(characters)
    raw_style_text = _join_style_text(characters)
    balanced_style_text = _balanced_style_sentence(characters)
    generic_style_text = "Style: cinematic realistic story frame, muted palette, natural light, varied composition."
    style_was_generic_replacement = mode == "balanced" and balanced_style_text.strip().lower() == generic_style_text.lower()
    style_preserved = _style_preserved(raw_style_text, balanced_style_text) if mode == "balanced" else True
    missing = [str(path) for path in (paths["story_dir"], paths["characters"], paths["raw_prompts"]) if not path.exists()]
    compact_prompts: list[str] = []
    per_prompt: list[dict[str, Any]] = []
    totals = {
        "beauty_bias_removed_count": 0,
        "car_continuity_added_count": 0,
        "character_continuity_added_count": 0,
        "location_continuity_added_count": 0,
    }
    for idx, prompt in enumerate(raw_prompts, start=1):
        if mode == "balanced":
            compact, metrics = balanced_compact_prompt(prompt, anchors, balanced_style_text)
        elif mode == "noface":
            compact, metrics = noface_compact_prompt(prompt, anchors)
        else:
            compact, metrics = compact_prompt(prompt, anchors)
        compact_prompts.append(compact)
        for key in totals:
            totals[key] += int(metrics.get(key, 0))
        per_prompt.append(
            {
                "prompt_index": idx,
                "raw_chars": len(prompt),
                "compact_chars": len(compact),
                **metrics,
            }
        )

    raw_avg = round(sum(len(p) for p in raw_prompts) / len(raw_prompts), 2) if raw_prompts else 0
    compact_avg = round(sum(len(p) for p in compact_prompts) / len(compact_prompts), 2) if compact_prompts else 0
    report = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("written" if options.execute else "dry_run"),
        "execute": bool(options.execute),
        "story_id": story_id,
        "story_dir": str(paths["story_dir"]),
        "prompt_mode": {"balanced": "balanced_compact", "noface": "noface_compact"}.get(mode, "compact"),
        "compact_mode": mode,
        "selected_style_name": characters.get("style_name") if mode == "balanced" else "",
        "raw_style_text": raw_style_text if mode == "balanced" else "",
        "balanced_style_text": balanced_style_text if mode == "balanced" else "",
        "style_preserved": style_preserved,
        "style_was_generic_replacement": style_was_generic_replacement,
        "written_at": _now_iso(),
        "missing": missing,
        "paths": {key: str(value) for key, value in paths.items()},
        "summary": {
            "total_prompts": len(raw_prompts),
            "avg_prompt_chars_before": raw_avg,
            "avg_prompt_chars_after": compact_avg,
            "max_prompt_chars_before": max((len(p) for p in raw_prompts), default=0),
            "max_prompt_chars_after": max((len(p) for p in compact_prompts), default=0),
            **totals,
            "hard_max_violations": sum(1 for prompt in compact_prompts if len(prompt) > (700 if mode == "noface" else (850 if mode == "balanced" else 900))),
            "target_range_count": sum(1 for prompt in compact_prompts if 380 <= len(prompt) <= (680 if mode == "balanced" else (550 if mode == "noface" else 650))),
        },
        "sample_first_10": compact_prompts[:10],
        "per_prompt": per_prompt,
        "changed_files": [],
        "note": "Dry-run writes only this report. Execute writes compact prompts and compact frame jobs; raw prompts/jobs are untouched.",
    }
    _write_json(paths["compact_report"], report)
    report["changed_files"].append(str(paths["compact_report"]))
    if options.execute and not missing:
        numbered = [f"{idx}. {prompt}" for idx, prompt in enumerate(compact_prompts, start=1)]
        _write_text(paths["compact_prompts"], "\n\n".join(numbered) + "\n")
        _write_json(paths["compact_frame_jobs"], _frame_jobs_payload(story_id, compact_prompts, paths["story_dir"] / "07_frames", mode))
        report["changed_files"].extend([str(paths["compact_prompts"]), str(paths["compact_frame_jobs"])])
        _write_json(paths["compact_report"], report)
    return report


def write_comfyui_payload_debug(
    *,
    config: OrchestratorConfig,
    story_id: str,
    jobs: list[dict[str, Any]],
    workflow: dict[str, Any],
    mode: str = "compact",
) -> Path:
    mode = _normalize_mode(mode)
    prompt_mode = {"balanced": "balanced_compact", "noface": "noface_compact"}.get(mode, "compact")
    paths = compact_paths(config, story_id, mode)
    workflow_path = Path(str(workflow.get("path", "")))
    text_node_id = str(workflow.get("text_node_id", "") or "")
    seed_node_id = str(workflow.get("seed_node_id", "") or "")
    workflow_data = _read_json(workflow_path)
    samples: list[dict[str, Any]] = []
    for job in jobs[:3]:
        prompt = str(job.get("prompt", "") or "")
        text_node_before_exists = False
        after_preview = prompt[:700]
        if isinstance(workflow_data, dict) and isinstance(workflow_data.get(text_node_id), dict):
            text_node_before_exists = True
            draft = copy.deepcopy(workflow_data)
            draft[text_node_id].setdefault("inputs", {})
            draft[text_node_id]["inputs"]["text"] = prompt
            after_preview = str(draft[text_node_id].get("inputs", {}).get("text", ""))[:700]
        samples.append(
            {
                "frame_id": job.get("prompt_index"),
                "prompt_mode": prompt_mode,
                "compact_mode": mode,
                "prompt_text": prompt,
                "prompt_chars": len(prompt),
                "workflow_path": str(workflow_path),
                "text_node_id": text_node_id,
                "seed_node_id": seed_node_id,
                "text_node_before_exists": text_node_before_exists,
                "text_node_after_preview": after_preview,
                "prompt_hash": hashlib.sha1(prompt.encode("utf-8")).hexdigest(),
            }
        )
    payload = {
        "written_at": _now_iso(),
        "story_id": story_id,
        "prompt_mode": prompt_mode,
        "compact_mode": mode,
        "samples": samples,
    }
    _write_json(paths["payload_debug"], payload)
    return paths["payload_debug"]
