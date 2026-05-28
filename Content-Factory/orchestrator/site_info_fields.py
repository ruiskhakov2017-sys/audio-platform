from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Строка начинается с известного поля legacy/Gemini info.txt
INFO_FIELD_LINE_RE = re.compile(
    r"^\s*("
    r"заголовок|альтернативный\s+заголовок|описание|название|жанры|теги|"
    r"озвучка|тип\s+голоса|главный\s+персонаж|"
    r"визуальный\s+промпт|визуал|visual(?:\s+prompt)?"
    r")\s*:\s*(.*)$",
    re.IGNORECASE,
)

_FIELD_KEY_MAP: dict[str, str] = {
    "заголовок": "title",
    "альтернативный заголовок": "alternative_title",
    "описание": "description",
    "название": "name",
    "жанры": "genres",
    "теги": "tags",
    "озвучка": "voice_ozvuchka",
    "тип голоса": "voice_type",
    "главный персонаж": "main_character",
    "визуальный промпт": "visual",
    "визуал": "visual",
    "visual": "visual",
    "visual prompt": "visual",
}

VISUAL_PREVIEW_MAX_LEN = 240
VISUAL_MIN_LEN = 150

GEMINI_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"i\s+can['\u2019]t\s+help\s+with\s+that",
        r"i\s+cannot\s+help\s+with\s+that",
        r"i['\u2019]m\s+unable\s+to",
        r"i\s+can['\u2019]t\s+provide",
        r"i\s+cannot\s+provide",
        r"i['\u2019]m\s+sorry",
        r"as\s+an\s+ai",
        r"\bpolicy\b",
        r"safety\s+guidelines",
        r"не\s+могу\s+помочь",
        r"не\s+могу\s+предоставить",
    )
)

RETRYABLE_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "missing_raw",
        "no_visual_prompt_found",
        "gemini_refusal_or_policy_response",
        "prompt_too_short",
        "truncated_prompt_blocked",
        "title_fallback_blocked",
        # legacy aliases from older exports
        "prompt_truncated_suffix",
        "prompt_equals_canonical_basename",
    }
)


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower())


def parse_info_fields(info_text: str) -> dict[str, str]:
    """
    Разбор info.txt / site_info_raw: многострочные значения до следующей метки поля.
    При повторе ключа (два «Описание:») значения склеиваются через пустую строку.
    """
    fields: dict[str, list[str]] = {}
    current_key: str | None = None

    for raw_line in (info_text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped and current_key is None:
            continue

        match = INFO_FIELD_LINE_RE.match(stripped)
        if match:
            label = _normalize_label(match.group(1))
            current_key = _FIELD_KEY_MAP.get(label, label)
            first_part = (match.group(2) or "").strip()
            fields.setdefault(current_key, [])
            fields[current_key].append(first_part)
            continue

        if current_key:
            fields.setdefault(current_key, [])
            if fields[current_key] and fields[current_key][-1] != "":
                fields[current_key].append(stripped)
            elif stripped:
                fields[current_key].append(stripped)

    out: dict[str, str] = {}
    for key, parts in fields.items():
        merged = "\n".join(p for p in parts if p is not None).strip()
        if merged:
            out[key] = merged
    return out


def extract_visual_prompt_full(info_text: str) -> str:
    """Полный визуальный промпт из текста info (без silent fallback на title)."""
    fields = parse_info_fields(info_text)
    for key in ("visual",):
        value = (fields.get(key) or "").strip()
        if value:
            return value
    return ""


def make_visual_prompt_preview(full: str, *, max_len: int = VISUAL_PREVIEW_MAX_LEN) -> str:
    text = (full or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def detect_gemini_refusal(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return False
    return any(p.search(blob) for p in GEMINI_REFUSAL_PATTERNS)


def _normalize_title_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _looks_truncated_suffix(text: str) -> bool:
    tail = (text or "").rstrip()
    if not tail:
        return False
    if tail.endswith("..."):
        return True
    if tail.endswith("…"):
        return True
    if len(tail) >= VISUAL_MIN_LEN and tail[-1].isalnum():
        return False
    return False


def _normalize_failure_reason(reason: str) -> str:
    mapping = {
        "prompt_equals_canonical_basename": "title_fallback_blocked",
        "prompt_truncated_suffix": "truncated_prompt_blocked",
    }
    return mapping.get(reason, reason)


def validate_visual_prompt(
    visual_prompt: str | None,
    *,
    canonical_basename: str,
    min_len: int = VISUAL_MIN_LEN,
    raw_text_for_refusal: str | None = None,
) -> tuple[bool, str, str]:
    """
    Returns: (is_valid, visual_prompt_status, failure_reason)
    status: ok | missing | invalid
    """
    if raw_text_for_refusal and detect_gemini_refusal(raw_text_for_refusal):
        return False, "invalid", "gemini_refusal_or_policy_response"

    raw = visual_prompt
    if raw is None:
        return False, "missing", "no_visual_prompt_found"
    text = str(raw).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return False, "missing", "no_visual_prompt_found"

    canon_norm = _normalize_title_token(canonical_basename.replace("_", " "))
    prompt_norm = _normalize_title_token(text)
    if prompt_norm == canon_norm:
        return False, "invalid", "title_fallback_blocked"
    if len(text) < min_len:
        return False, "invalid", "prompt_too_short"
    if _looks_truncated_suffix(text):
        return False, "invalid", "truncated_prompt_blocked"

    return True, "ok", ""


def is_site_info_workspace_valid(
    *,
    canonical_basename: str,
    raw_path: Path | None,
    json_path: Path | None,
    info_path: Path | None,
) -> tuple[bool, str, str]:
    """
    Считает site_info рабочей истории валидным только если из raw/json/info извлекается
    нормальный визуальный промпт. Возвращает (is_valid, failure_reason, extraction_source).

    Никаких silent fallback: если raw отсутствует и json/info не дают валидного `Визуал:`,
    считаем historie invalid (это значит её нужно повторно прогнать через Gemini).
    """
    raw_text = ""
    if raw_path is not None and raw_path.is_file():
        try:
            raw_text = raw_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw_text = ""
    fallback_json: dict[str, Any] | None = None
    if json_path is not None and json_path.is_file():
        try:
            import json as _json  # local import to avoid module-level cost

            data = _json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, dict):
                fallback_json = data
        except Exception:
            fallback_json = None
    info_text = ""
    if info_path is not None and info_path.is_file():
        try:
            info_text = info_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            info_text = ""

    _, extraction_source, status, failure_reason, _ = validate_story_visual_from_raw(
        canonical_basename=canonical_basename,
        raw_path=raw_path if raw_path and raw_path.is_file() else None,
        raw_text=raw_text,
        fallback_json=fallback_json,
        fallback_info_text=info_text,
    )
    return status == "ok", failure_reason, extraction_source


def validate_story_visual_from_raw(
    *,
    canonical_basename: str,
    raw_path: Path | None,
    raw_text: str,
    fallback_json: dict[str, Any] | None = None,
    fallback_info_text: str = "",
) -> tuple[str, str, str, str, str]:
    """
    Production validation: raw обязателен.
    Returns: visual_full, extraction_source, status, failure_reason, raw_excerpt_preview
    """
    excerpt = (raw_text or "")[:500]

    if raw_path is None or not raw_path.is_file():
        return "", "missing_raw", "invalid", "missing_raw", excerpt

    if detect_gemini_refusal(raw_text):
        return "", "site_info_raw.txt", "invalid", "gemini_refusal_or_policy_response", excerpt

    visual_full = extract_visual_prompt_full(raw_text)
    extraction_source = "site_info_raw.txt"

    if not visual_full.strip():
        if fallback_json:
            vp = (fallback_json.get("visual_prompt") or fallback_json.get("visual_prompt_full") or "").strip()
            if vp:
                visual_full = vp
                extraction_source = "site_info.json"
        if not visual_full.strip() and fallback_info_text:
            visual_full = extract_visual_prompt_full(fallback_info_text)
            if visual_full:
                extraction_source = "info.txt"

    if not visual_full.strip():
        return "", extraction_source, "invalid", "no_visual_prompt_found", excerpt

    is_valid, status, failure_reason = validate_visual_prompt(
        visual_full,
        canonical_basename=canonical_basename,
        raw_text_for_refusal=None,
    )
    failure_reason = _normalize_failure_reason(failure_reason)
    if not is_valid:
        return visual_full, extraction_source, status, failure_reason, excerpt
    return visual_full, extraction_source, "ok", "", excerpt


def build_validation_report_counters() -> dict[str, int]:
    return {
        "total_stories_seen": 0,
        "valid_prompts": 0,
        "invalid_prompts": 0,
        "missing_raw": 0,
        "no_visual_prompt_found": 0,
        "title_fallback_blocked": 0,
        "truncated_prompt_blocked": 0,
        "prompt_too_short": 0,
        "gemini_refusal_or_policy_response": 0,
    }


def bump_report_counter(counters: dict[str, int], failure_reason: str, status: str) -> None:
    counters["total_stories_seen"] += 1
    if status == "ok":
        counters["valid_prompts"] += 1
        return
    counters["invalid_prompts"] += 1
    key = failure_reason if failure_reason in counters else ""
    if key:
        counters[key] = counters.get(key, 0) + 1


# Backward-compatible aliases used by visual_stage (legacy counter names)
def build_validation_counters() -> dict[str, int]:
    c = build_validation_report_counters()
    c["prompts_ok"] = 0
    c["prompts_missing"] = 0
    c["prompts_title_fallback_blocked"] = 0
    c["prompts_truncated_blocked"] = 0
    c["prompts_too_short_blocked"] = 0
    c["prompts_other_invalid_blocked"] = 0
    return c


def bump_validation_counter(counters: dict[str, int], failure_reason: str, status: str) -> None:
    bump_report_counter(counters, failure_reason, status)
    counters["total_stories_seen"] = counters.get("total_stories_seen", 0)
    if status == "ok":
        counters["prompts_ok"] = counters.get("prompts_ok", 0) + 1
        counters["valid_prompts"] = counters.get("valid_prompts", 0) + 1
        return
    counters["prompts_missing"] = counters.get("prompts_missing", 0) + (
        1 if failure_reason in {"no_visual_prompt_found", "missing_raw"} else 0
    )
    if failure_reason in {"title_fallback_blocked", "prompt_equals_canonical_basename"}:
        counters["prompts_title_fallback_blocked"] = counters.get("prompts_title_fallback_blocked", 0) + 1
    elif failure_reason in {"truncated_prompt_blocked", "prompt_truncated_suffix"}:
        counters["prompts_truncated_blocked"] = counters.get("prompts_truncated_blocked", 0) + 1
    elif failure_reason == "prompt_too_short":
        counters["prompts_too_short_blocked"] = counters.get("prompts_too_short_blocked", 0) + 1
    else:
        counters["prompts_other_invalid_blocked"] = counters.get("prompts_other_invalid_blocked", 0) + 1


def resolve_visual_prompt_for_story(
    *,
    canonical_basename: str,
    info_text: str,
    site_info_raw_text: str | None = None,
    site_info_json: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Deprecated path-based resolver; prefer validate_story_visual_from_raw."""
    if site_info_raw_text:
        full = extract_visual_prompt_full(site_info_raw_text)
        if full.strip():
            return full.strip(), "site_info_raw.txt"
    if site_info_json:
        vp = (site_info_json.get("visual_prompt") or site_info_json.get("visual_prompt_full") or "").strip()
        if vp:
            return vp, "site_info.json"
    if info_text:
        full = extract_visual_prompt_full(info_text)
        if full.strip():
            return full.strip(), "info.txt"
    return "", "missing"
