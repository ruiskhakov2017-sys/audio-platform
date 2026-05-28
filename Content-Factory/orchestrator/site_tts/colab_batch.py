from __future__ import annotations

import csv
import html
import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.human_launch_layout import (
    D02_04_TTS,
    D02_SITE,
    D05_RASSKAZY,
    D06_OTCHETY,
)
from orchestrator.site_tts.cleaned_text_guard import validate_cleaned_text_for_tts
from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.batch import iter_human_launch_story_dirs
from orchestrator.site_tts.contract import SiteTtsPaths
from orchestrator.site_tts.drive_voice_resolve import (
    VOICE_MANIFEST_FILENAME,
    build_kokoro_drive_voice_item_for_existing_txt,
    collect_voice_ids_from_pools,
    write_kokoro_voices_job_json,
    write_kokoro_voices_job_payload,
)
from orchestrator.site_tts.info_parser import resolve_voice_letter_from_info_content
from orchestrator.site_tts.text_chunking import pack_paragraph_chunks
from orchestrator.text_cleaning.literotica_header import (
    literotica_header_remnant_warning,
    strip_literotica_source_header,
)

_HANDOFF_ROOT = "_COLAB_EXPORTS"
_HANDOFF_RESULTS_DIR = "results_drop_here"
_CURRENT_ROOT = "COLAB_TTS_CURRENT"
_CURRENT_TEXTS = "TEXTS_TO_COLAB"
_CURRENT_MP3 = "MP3_FROM_COLAB"
_EXPORT_DIAG_JSONL = "EXPORT_DIAG.jsonl"
_DRIVE_EXPORT_CLEAN_STAGE = "drive_tts_final_cleaner_v5_literotica_header_strip"

# URL-like: schemes, messengers, reddit/discord, forum.* hosts, common TLD hosts (not bare .txt/.pdf etc. as “domains”).
_URL_TLD = (
    r"com|net|org|io|ru|co|ai|app|dev|info|biz|me|tv|xyz|site|online|store|blog|pro|live|link|cc|"
    r"uk|de|fr|jp|cn|gov|edu|mil|int|su|by|kz|ua|pl|nl|se|no|fi|dk|ch|at|be|cz|sk|hu|ro|bg|hr|rs|si|"
    r"lt|lv|ee|ie|is|in|br|au|ca|us|nz|mx|ar|cl|es|pt|it|gr|tr|il|ae|za|ng|vn|th|id|ph|my|sg|hk|tw|kr|"
    r"pk|bd|ge|am|az|tm|ws|to|im|gg|je|sh|ac|cx|tk|ml|ga|cf|gq|pw|top|club|space|host|tech|news|world|"
    r"today|click|help|support|wiki|cat|museum"
)
_URL_LIKE_RE = re.compile(
    rf"(?i)(?:https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|\b(?:old\.)?reddit\.com/\S+|\bdiscord\.(?:gg|com)/\S+"
    rf"|\bforum\.[a-z0-9.-]+\.[a-z]{{2,24}}\b"
    rf"|\b[a-z0-9][a-z0-9-]{{0,62}}\.(?:{_URL_TLD})(?:/\S*)?)"
)

# Legacy one-line UI noise (anchored; avoid matching “source” inside prose).
_NOISE_LINE_RE = re.compile(
    r"(?i)^(read\s*more|click\s*here|navigation|footer|comments?|subscribe|archive|breadcrumbs?|"
    r"log\s*in|login)\b[\s:.,!-]*$"
)

# RU/EN meta / forum boilerplate: whole trimmed line.
_SERVICE_META_LINE_RE = re.compile(
    r"(?i)^(?:"
    r"источник\s*:.+|ссылка\s*:.+|форум\s*:.+|читать\s+дальше\s*:.+|читать\s+полностью\s*:.+|"
    r"опубликовано\s*:.+|автор\s*:.+|комментарии\s*:.+|обсуждение\s*:.+|тема\s+форума\s*:.+|"
    r"скопировано\s+с\s*:.+|взято\s+с\s*:.+|продолжение\s+на\s+сайте\s*:.+|полная\s+версия\s*:.+|"
    r"зарегистрируйтесь\b.*|войдите,\s*чтобы\s+оставить\s+комментарий\s*$|"
    r"(?:original\s+)?source\s*:.+|forum\s*:.+|read\s+more\s*:.+|continue\s+reading\s*:.+|"
    r"full\s+version\s*:.+|posted\s+by\s*:.+|posted\s+on\s*:.+|comments?\s*:.+|reply\s*:.+|thread\s*:.+|"
    r"login\s+to\s+comment\s*:.+|register\s+to\s+continue\s*:.+|more\s+stories\s*:.+|visit\s*:.*|"
    r"login\s+to\s+comment\s*$|register\s+to\s+continue\s*$"
    r")$"
)

# Leading-only chapter/part markers (trimmed line, whole line).
_LEADING_SERVICE_HEADER_RE = re.compile(
    r"(?i)^(?:"
    r"глава\s+(?:\d+|0\d+|первая|вторая|третья|четвёртая|четвертая|пят(?:ая|ой)|шест(?:ая|ой)|"
    r"седьм(?:ая|ой)|восьм(?:ая|ой)|девят(?:ая|ой)|десят(?:ая|ой))\s*"
    r"|часть\s+(?:\d+|0\d+|первая|вторая|третья|четвёртая|четвертая|пят(?:ая|ой))\s*"
    r"|раздел\s+\d+\s*|эпизод\s+\d+\s*|продолжение\s*|начало\s*"
    r"|chapter\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"|ch\.\s*\d+\s*|part\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"|episode\s+\d+\s*|section\s+\d+\s*"
    r")$"
)

_TTS_PARA_MAX = 1400
_TTS_PARA_SOFT_MIN = 700

_HTML_TAG_RE = re.compile(r"(?is)<[^>]+>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MARKDOWN_FENCE_RE = re.compile(r"(?ms)^\s*```.*?^\s*```\s*")
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_SEPARATOR_LINE_RE = re.compile(r"^\s*[-_=*]{3,}\s*$")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.,!?:;])")
_NEEDS_SPACE_AFTER_PUNCT_RE = re.compile(r"([.,!?:;])(?=[A-Za-zА-Яа-яЁё0-9])")


def _drive_tts_drop_boilerplate_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(_NOISE_LINE_RE.match(s) or _SERVICE_META_LINE_RE.match(s))


def _strip_leading_service_headers(text: str) -> tuple[str, int]:
    if not (text or "").strip():
        return text or "", 0
    lines = text.split("\n")
    removed = 0
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "":
            i += 1
            continue
        if _LEADING_SERVICE_HEADER_RE.match(s):
            removed += 1
            i += 1
            continue
        break
    return "\n".join(lines[i:]).lstrip("\n"), removed


def _split_sentences_for_tts(para: str) -> list[str]:
    p = para.strip()
    if not p:
        return []
    parts = re.split(r"(?<=[.!?…])(?:\s+|$)", p)
    return [x.strip() for x in parts if x.strip()]


def _hard_wrap_plain_text(segment: str, max_chars: int) -> list[str]:
    if len(segment) <= max_chars:
        return [segment] if segment.strip() else []
    out: list[str] = []
    start = 0
    while start < len(segment):
        end = min(start + max_chars, len(segment))
        chunk = segment[start:end]
        if end < len(segment):
            cut = chunk.rfind(" ")
            if cut > max_chars // 2:
                chunk = chunk[:cut]
                end = start + len(chunk)
        piece = chunk.strip()
        if piece:
            out.append(piece)
        if end <= start:
            end = start + max_chars
        start = end
    return out


def _pack_sentences_into_paragraphs(sentences: list[str]) -> list[str]:
    if not sentences:
        return []
    paras: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            paras.append(" ".join(buf))
            buf.clear()

    for s in sentences:
        if len(s) > _TTS_PARA_MAX:
            flush()
            paras.extend(_hard_wrap_plain_text(s, _TTS_PARA_MAX))
            continue
        add_len = len(s) + (1 if buf else 0)
        buf_len = len(" ".join(buf)) if buf else 0
        if buf and buf_len + add_len > _TTS_PARA_MAX:
            flush()
        buf.append(s)
    flush()
    return paras


def _reshape_oversized_paragraph_block(block: str) -> list[str]:
    b = block.strip()
    if not b:
        return []
    if len(b) <= _TTS_PARA_MAX:
        return [b]
    sents = _split_sentences_for_tts(b)
    if len(sents) <= 1:
        return _hard_wrap_plain_text(b, _TTS_PARA_MAX)
    return _pack_sentences_into_paragraphs(sents)


def _normalize_tts_paragraphs(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    if not t:
        return ""
    blocks = re.split(r"\n\n+", t)
    rebuilt: list[str] = []
    for raw_block in blocks:
        bk = raw_block.strip()
        if not bk:
            continue
        rebuilt.extend(_reshape_oversized_paragraph_block(bk))
    return "\n\n".join(rebuilt).strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel_posix(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _pick_voice(settings: Any, voice_type: str) -> str:
    vt = (voice_type or "U").upper()[:1]
    if vt == "M":
        return settings.kokoro_voice_male
    if vt == "F":
        return settings.kokoro_voice_female
    return settings.kokoro_voice_neutral


def _lang_code(settings: Any, voice: str) -> str:
    if settings.kokoro_lang_code:
        return settings.kokoro_lang_code.strip().lower()[:1]
    if voice:
        c = voice.strip().lower()[:1]
        if c in "abefhijpz":
            return c
    return "a"


@dataclass(frozen=True)
class StoryTtsSource:
    story_id: str
    story_folder: Path
    tts_text_path: Path
    voice_type: str
    has_mp3: bool
    expected_output_mp3: Path


def _site_has_final_mp3(story_folder: Path, story_id: str) -> bool:
    """
    True if this site story already has a non-empty canonical mp3 (import/Kokoro contract).
    Accepts both raw folder name and _safe_name() variants — export uses _safe_name for Drive keys.
    """
    safe_id = _safe_name(story_id)
    for name in {story_id, safe_id}:
        if not name:
            continue
        p = story_folder / f"{name}.mp3"
        if p.is_file() and p.stat().st_size > 0:
            return True
    folder_key = safe_id
    for p in story_folder.glob("*.mp3"):
        if not p.is_file() or p.stat().st_size <= 0:
            continue
        base, _v = _split_story_voice(p.stem)
        if _safe_name(base) == folder_key:
            return True
    return False


def _resolve_story_tts_source(story_folder: Path) -> tuple[StoryTtsSource | None, str | None]:
    story_id = story_folder.name
    expected_mp3 = story_folder / f"{story_id}.mp3"
    candidates = []
    for vt in ("M", "F", "U"):
        p = story_folder / f"{story_id}__{vt}.txt"
        if p.is_file():
            candidates.append((vt, p))
    if not candidates:
        return None, "missing_tts_text_file"
    if len(candidates) > 1:
        names = ",".join(p.name for _, p in candidates)
        return None, f"multiple_tts_text_files:{names}"
    vt, path = candidates[0]
    return (
        StoryTtsSource(
            story_id=story_id,
            story_folder=story_folder,
            tts_text_path=path,
            voice_type=vt,
            has_mp3=_site_has_final_mp3(story_folder, story_id),
            expected_output_mp3=expected_mp3,
        ),
        None,
    )


def _iter_story_dirs(site_root: Path) -> list[Path]:
    if not site_root.is_dir():
        return []
    return sorted([p for p in site_root.iterdir() if p.is_dir()], key=lambda x: x.name.lower())


def _resolve_story_human_colab_source(launch: Path, story_dir: Path) -> tuple[StoryTtsSource | None, str | None]:
    """Human-launch: cleaned_story.txt + info.txt → Colab job (без story__M.txt в output/site)."""
    story_id = story_dir.name
    paths = SiteTtsPaths.from_human_launch_story(launch, story_id, ensure_dirs=False)
    if not paths.cleaned_story_txt.is_file():
        return None, "missing_cleaned_txt"
    if not paths.info_txt.is_file():
        return None, "no_info"
    try:
        has_mp3 = paths.output_mp3.is_file() and paths.output_mp3.stat().st_size > 0
    except OSError:
        has_mp3 = False
    if has_mp3:
        return None, "has_mp3"
    try:
        txt = paths.cleaned_story_txt.read_text(encoding="utf-8")
    except OSError:
        return None, "cleaned_read_error"
    ok_txt, bad_reason = validate_cleaned_text_for_tts(txt)
    if not ok_txt:
        return None, f"cleaned_text_rejected:{bad_reason}"
    letter, _, _ = resolve_voice_letter_from_info_content(paths.info_txt.read_text(encoding="utf-8"))
    human_story_root = paths.story_folder.parent.parent
    return (
        StoryTtsSource(
            story_id=story_id,
            story_folder=human_story_root,
            tts_text_path=paths.cleaned_story_txt,
            voice_type=letter,
            has_mp3=False,
            expected_output_mp3=paths.output_mp3,
        ),
        None,
    )


def _copy_colab_report_to_launch_sidecars(launch: Path, batch_id: str, report_src: Path, stem: str) -> None:
    """Дубли отчёта в 06_Отчёты и 02_Сайт/04_Озвучка_для_сайта."""
    name = f"{stem}_{batch_id}.json"
    for sub in (Path(D06_OTCHETY), Path(D02_SITE) / D02_04_TTS):
        dst = (launch.resolve() / sub / name).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(report_src, dst)


def _mirror_human_drive_export_to_colab_current(
    root: Path,
    drive_texts_dir: Path,
    index_csv: Path,
    exported_rows: list[list[str]],
    *,
    job_batch_id: str,
) -> None:
    """Дублировать выгрузку на Drive в COLAB_TTS_CURRENT (debug / import --current без zip-batch)."""
    if not exported_rows:
        return
    current_dir, texts_dir, mp3_dir = _current_paths(root)
    drive_texts_res = drive_texts_dir.resolve()
    if drive_texts_res == texts_dir.resolve():
        return
    mp3_dir.mkdir(parents=True, exist_ok=True)
    _clear_dir(texts_dir)
    mapping: list[dict[str, str]] = []
    for row in exported_rows:
        if len(row) < 5:
            continue
        sid, txt_name, mp3_name, dest_cell = row[1], row[2], row[3], row[4]
        src_txt = drive_texts_res / txt_name
        if src_txt.is_file():
            shutil.copy2(src_txt, texts_dir / txt_name)
        voice = row[5] if len(row) > 5 else "U"
        mapping.append(
            {
                "story_folder": str(sid),
                "source_txt": str(txt_name),
                "expected_mp3": str(mp3_name),
                "expected_output_mp3": str(dest_cell),
                "voice": str(voice),
            }
        )
    if index_csv.is_file():
        shutil.copy2(index_csv, current_dir / "STORIES_INDEX.csv")
    internal = {
        "schema_version": 1,
        "batch_id": job_batch_id,
        "batch_dir": "",
        "created_at": _utc_now_iso(),
        "items_count": len(mapping),
        "mapping": mapping,
    }
    (current_dir / "internal_manifest.json").write_text(json.dumps(internal, ensure_ascii=False, indent=2), encoding="utf-8")


def _human_story_dir_for_drive_mp3_name(launch: Path, mp3_basename: str) -> Path | None:
    """Сопоставить имя mp3 на Drive (safeName__V.mp3) с папкой рассказа в 05_Рассказы."""
    stem = Path(mp3_basename).stem
    if "__" not in stem:
        return None
    safe_story, _vt = stem.rsplit("__", 1)
    safe_story = (safe_story or "").strip()
    if not safe_story:
        return None
    for folder in iter_human_launch_story_dirs(launch):
        if _safe_name(folder.name) == safe_story:
            return folder
    return None


def _safe_name(name: str) -> str:
    out = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    out = out.replace("\n", "_").replace("\r", "_")
    return out or "story"


def _split_story_voice(stem: str) -> tuple[str, str]:
    s = (stem or "").strip()
    if "__" in s:
        story, voice = s.rsplit("__", 1)
        v = voice.strip().upper()[:1]
        if v in {"M", "F", "U"}:
            return story, v
    return s, "U"


def _resolve_drive_dir(root: Path, cli_dir: Path | None, cfg_dir: str) -> Path:
    if cli_dir is not None:
        return (cli_dir if cli_dir.is_absolute() else (root / cli_dir)).resolve()
    raw = (cfg_dir or "").strip()
    if not raw:
        raise ValueError("drive directory is not configured; pass via CLI or configs/site_tts.yaml")
    p = Path(raw)
    return (p if p.is_absolute() else (root / p)).resolve()


def _drive_root(root: Path, settings: Any) -> Path | None:
    raw = str(getattr(settings, "google_drive_root_dir", "") or "").strip()
    if not raw:
        return None
    p = Path(raw)
    return (p if p.is_absolute() else (root / p)).resolve()


def _drive_dir_from(root: Path, settings: Any, key: str, default_sub: str) -> Path:
    cli = ""
    if key == "texts":
        cli = str(getattr(settings, "google_drive_texts_dir", "") or "").strip()
    elif key == "mp3":
        cli = str(getattr(settings, "google_drive_mp3_dir", "") or "").strip()
    elif key == "scripts":
        cli = str(getattr(settings, "google_drive_scripts_dir", "") or "").strip()
    elif key == "cache":
        cli = str(getattr(settings, "google_drive_cache_dir", "") or "").strip()
    elif key == "logs":
        cli = str(getattr(settings, "google_drive_logs_dir", "") or "").strip()
    elif key == "job":
        cli = str(getattr(settings, "google_drive_job_dir", "") or "").strip()
    if cli:
        p = Path(cli)
        return (p if p.is_absolute() else (root / p)).resolve()
    dr = _drive_root(root, settings)
    if dr is not None:
        return (dr / default_sub).resolve()
    raise ValueError(
        f"google drive path for {key} is not configured; set google_drive_tts.root_dir or explicit {key}_dir"
    )


def _count_url_like(text: str) -> int:
    return len(_URL_LIKE_RE.findall(text or ""))


def _clean_text_for_drive_tts(raw_text: str) -> tuple[str, int, int, int, dict[str, Any]]:
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    url_before = _count_url_like(text)

    text, lit_pre = strip_literotica_source_header(text)
    lit_diag: dict[str, Any] = {
        "removed_literotica_header_lines_count": int(lit_pre.get("removed_literotica_header_lines_count", 0) or 0),
        "removed_literotica_header_lines_sample": list(lit_pre.get("removed_literotica_header_lines_sample") or []),
    }

    text = html.unescape(text)
    text = _MARKDOWN_FENCE_RE.sub(" ", text)
    text = _MARKDOWN_IMAGE_RE.sub(" ", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKDOWN_INLINE_CODE_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")

    kept_lines: list[str] = []
    removed_lines = 0
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            kept_lines.append("")
            continue
        if _SEPARATOR_LINE_RE.match(line):
            removed_lines += 1
            continue
        if _URL_LIKE_RE.search(line):
            removed_lines += 1
            continue
        if _drive_tts_drop_boilerplate_line(line):
            removed_lines += 1
            continue
        cleaned_line = _URL_LIKE_RE.sub(" ", line)
        cleaned_line = re.sub(r"\s+", " ", cleaned_line).strip()
        if cleaned_line:
            kept_lines.append(cleaned_line)
        else:
            removed_lines += 1

    text = "\n".join(kept_lines)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _NEEDS_SPACE_AFTER_PUNCT_RE.sub(r"\1 ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    text, hdr_rm = _strip_leading_service_headers(text)
    removed_lines += hdr_rm

    text = _normalize_tts_paragraphs(text)
    text = text.strip()

    text, lit_post = strip_literotica_source_header(text)
    lit_diag["removed_literotica_header_lines_count"] = int(lit_diag["removed_literotica_header_lines_count"]) + int(
        lit_post.get("removed_literotica_header_lines_count", 0) or 0
    )
    post_samples = list(lit_post.get("removed_literotica_header_lines_sample") or [])
    combined = list(lit_diag["removed_literotica_header_lines_sample"]) + post_samples
    lit_diag["removed_literotica_header_lines_sample"] = combined[:12]
    remnant = literotica_header_remnant_warning(text)
    if remnant:
        lit_diag["literotica_header_warning"] = remnant

    url_after = _count_url_like(text)
    return text, url_before, url_after, removed_lines, lit_diag


def _append_export_diag(diag_path: Path, payload: dict[str, Any]) -> None:
    diag_path.parent.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {"clean_stage": _DRIVE_EXPORT_CLEAN_STAGE}
    row.update(payload)
    with diag_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_expected_files(job_dir: Path) -> list[str]:
    p = job_dir / "EXPECTED_FILES.txt"
    if not p.is_file():
        return []
    names: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        t = raw.strip()
        if t and t.lower().endswith(".mp3"):
            names.append(Path(t).name)
    return names


_COLAB_TERMINAL_NON_MP3 = frozenset({"failed", "manual_skipped"})
_COLAB_MIN_MP3_BYTES = 256
_MANUAL_SKIPPED_JSON = "manual_skipped.json"
_MANUAL_SKIPPED_TXT = "MANUAL_SKIPPED_FILES.txt"
_TTS_TERMINAL_STATUS_JSON = "TTS_BATCH_TERMINAL_STATUS.json"


def _read_colab_status(job_dir: Path) -> dict[str, Any]:
    p = job_dir / "COLAB_STATUS.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _local_reports_dir(root_dir: Path) -> Path:
    return (root_dir.resolve() / "reports").resolve()


def _read_manual_skipped(job_dir: Path) -> dict[str, dict[str, Any]]:
    path = job_dir / _MANUAL_SKIPPED_JSON
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = Path(str(item.get("expected_mp3_name") or item.get("name") or "")).name
        if name.lower().endswith(".mp3"):
            out[name] = item
    return out


def _story_title_from_mp3_name(mp3_name: str) -> str:
    story, _voice = _split_story_voice(Path(mp3_name).stem)
    return story


def _terminal_status_payload(
    *,
    expected_set: set[str],
    ready_set: set[str],
    manual_skipped: list[str],
    failed_terminal: list[str],
    real_missing: list[str],
    extra: list[str],
    zero_size: list[str],
    mp3_dir: Path,
    job_dir: Path,
) -> dict[str, Any]:
    ready_count = len(ready_set & expected_set)
    manual_skipped_count = len(set(manual_skipped))
    failed_terminal_count = len(set(failed_terminal))
    resolved_total = ready_count + manual_skipped_count + failed_terminal_count
    can_continue = resolved_total >= len(expected_set) and not real_missing
    return {
        "updated_at": _utc_now_iso(),
        "expected": len(expected_set),
        "ready": ready_count,
        "ready_to_publish_count": ready_count,
        "manual_skipped": manual_skipped_count,
        "skipped_count": manual_skipped_count,
        "failed_terminal": failed_terminal_count,
        "terminal_failed_count": failed_terminal_count,
        "missing": len(real_missing),
        "real_missing_count": len(real_missing),
        "effective_missing": len(real_missing),
        "zero_size": len(zero_size),
        "extra": len(extra),
        "extra_mp3_count": len(extra),
        "can_continue": can_continue,
        "can_continue_to_publish": can_continue,
        "can_continue_to_site_publish": can_continue,
        "completed": can_continue,
        "mp3_dir": str(mp3_dir),
        "job_dir": str(job_dir),
        "manual_skipped_files": sorted(manual_skipped),
        "failed_terminal_files": sorted(failed_terminal),
        "real_missing_files": sorted(real_missing),
        "extra_mp3_files": extra[:50],
        "zero_size_files": zero_size[:50],
    }


def _write_terminal_reports(
    *,
    root_dir: Path,
    job_dir: Path,
    payload: dict[str, Any],
) -> None:
    _write_json(job_dir / _TTS_TERMINAL_STATUS_JSON, payload)
    _write_json(_local_reports_dir(root_dir) / "site_tts_drive_wait_report.json", payload)
    _write_json(_local_reports_dir(root_dir) / "site_publish_tts_availability_report.json", payload)


def _drive_mp3_ready(mp3_dir: Path, mp3_name: str) -> bool:
    p = mp3_dir / mp3_name
    try:
        return p.is_file() and p.stat().st_size >= _COLAB_MIN_MP3_BYTES
    except OSError:
        return False


def _colab_expected_resolution(
    *,
    expected_set: set[str],
    mp3_dir: Path,
    job_dir: Path,
) -> dict[str, Any]:
    """
  Для каждого expected mp3: resolved если валидный mp3 на Drive или Colab пометил failed/manual_skipped
  (после COLAB_DONE.txt).
    """
    colab_done = (job_dir / "COLAB_DONE.txt").is_file()
    status = _read_colab_status(job_dir)
    file_status = status.get("file_status") if isinstance(status.get("file_status"), dict) else {}
    manual_marker = _read_manual_skipped(job_dir)
    resolved: list[str] = []
    unresolved: list[str] = []
    failed_terminal: list[str] = []
    manual_skipped: list[str] = []
    for name in sorted(expected_set):
        if _drive_mp3_ready(mp3_dir, name):
            resolved.append(name)
            continue
        if name in manual_marker:
            resolved.append(name)
            manual_skipped.append(name)
            continue
        st = str(file_status.get(name, "") or "").strip()
        if colab_done and st in _COLAB_TERMINAL_NON_MP3:
            resolved.append(name)
            if st == "failed":
                failed_terminal.append(name)
            elif st == "manual_skipped":
                manual_skipped.append(name)
            continue
        unresolved.append(name)
    return {
        "colab_done": colab_done,
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "failed_terminal": failed_terminal,
        "manual_skipped": manual_skipped,
        "manual_skipped_marker": manual_marker,
        "colab_status": status,
    }


def mark_drive_expected_skipped(
    root_dir: Path,
    *,
    names: list[str],
    reason: str,
    execute: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    job_dir = _drive_dir_from(root, settings, "job", "job")
    mp3_dir = _drive_dir_from(root, settings, "mp3", "mp3")
    expected = _load_expected_files(job_dir)
    expected_set = set(expected)
    cleaned_names = []
    for raw in names:
        name = Path(str(raw).strip()).name
        if name.lower().endswith(".mp3"):
            cleaned_names.append(name)
    cleaned_names = sorted(dict.fromkeys(cleaned_names), key=str.lower)
    now = _utc_now_iso()
    existing = _read_manual_skipped(job_dir)
    items_by_name = dict(existing)
    planned: list[dict[str, Any]] = []
    skipped_not_expected: list[str] = []
    for name in cleaned_names:
        if expected_set and name not in expected_set:
            skipped_not_expected.append(name)
            continue
        item = {
            "expected_mp3_name": name,
            "story": _story_title_from_mp3_name(name),
            "title": _story_title_from_mp3_name(name),
            "reason": reason,
            "timestamp": now,
            "source": "manual_skip",
            "can_retry_later": True,
        }
        items_by_name[name] = item
        planned.append(item)
    payload = {
        "version": 1,
        "updated_at": now,
        "source": "manual_skip",
        "reason": reason,
        "can_retry_later": True,
        "items": [items_by_name[name] for name in sorted(items_by_name, key=str.lower)],
    }
    report = {
        "ok": True,
        "execute": bool(execute),
        "job_dir": str(job_dir),
        "expected_count": len(expected_set),
        "requested_count": len(cleaned_names),
        "marked_count": len(planned),
        "already_or_total_manual_skipped": len(items_by_name),
        "marked": planned,
        "skipped_not_expected": skipped_not_expected,
        "manual_skipped_json": str(job_dir / _MANUAL_SKIPPED_JSON),
        "manual_skipped_txt": str(job_dir / _MANUAL_SKIPPED_TXT),
        "report_path": str(_local_reports_dir(root) / "site_tts_manual_skipped_report.json"),
        "written_at": now,
    }
    if execute:
        _write_json(job_dir / _MANUAL_SKIPPED_JSON, payload)
        txt_lines = [str(item["expected_mp3_name"]) for item in payload["items"]]
        (job_dir / _MANUAL_SKIPPED_TXT).write_text("\n".join(txt_lines) + ("\n" if txt_lines else ""), encoding="utf-8")
        expected_set = set(_load_expected_files(job_dir))
        found_files = [p for p in mp3_dir.glob("*.mp3") if p.is_file()] if mp3_dir.is_dir() else []
        valid_set = {p.name for p in found_files if p.stat().st_size >= _COLAB_MIN_MP3_BYTES}
        zero_size = [p.name for p in found_files if p.stat().st_size < _COLAB_MIN_MP3_BYTES]
        resolution = _colab_expected_resolution(expected_set=expected_set, mp3_dir=mp3_dir, job_dir=job_dir)
        terminal_payload = _terminal_status_payload(
            expected_set=expected_set,
            ready_set=valid_set & expected_set,
            manual_skipped=list(resolution["manual_skipped"]),
            failed_terminal=list(resolution["failed_terminal"]),
            real_missing=list(resolution["unresolved"]),
            extra=sorted(valid_set - expected_set),
            zero_size=zero_size,
            mp3_dir=mp3_dir,
            job_dir=job_dir,
        )
        _write_terminal_reports(root_dir=root, job_dir=job_dir, payload=terminal_payload)
    _write_json(_local_reports_dir(root) / "site_tts_manual_skipped_report.json", report)
    return report


def mark_missing_drive_expected_skipped(
    root_dir: Path,
    *,
    reason: str = "manual skip missing expected mp3",
    execute: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    mp3_dir = _drive_dir_from(root, settings, "mp3", "mp3")
    job_dir = _drive_dir_from(root, settings, "job", "job")
    expected = _load_expected_files(job_dir)
    expected_set = set(expected)
    resolution = _colab_expected_resolution(expected_set=expected_set, mp3_dir=mp3_dir, job_dir=job_dir)
    missing = sorted(list(resolution["unresolved"]), key=str.lower)
    result = mark_drive_expected_skipped(root, names=missing, reason=reason, execute=execute)
    result["missing_before_mark"] = missing
    result["missing_before_mark_count"] = len(missing)
    _write_json(_local_reports_dir(root) / "site_tts_manual_skipped_report.json", result)
    return result


def _read_local_job_status(job_dir: Path) -> dict[str, Any] | None:
    p = job_dir / "LOCAL_STATUS.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _list_drive_text_files(texts_dir: Path) -> list[Path]:
    """Фактические TXT на Drive: точные имена, без перезаписи."""
    by_name: dict[str, Path] = {}
    if not texts_dir.is_dir():
        return []
    for pattern in ("*.txt", "*.TXT"):
        for p in texts_dir.glob(pattern):
            if p.is_file():
                by_name[p.name] = p
    return sorted(by_name.values(), key=lambda x: x.name.lower())


def _find_site_folder_for_drive_story_part(site_root: Path, story_part: str) -> Path | None:
    if not site_root.is_dir() or not (story_part or "").strip():
        return None
    safe = _safe_name(story_part)
    for folder in site_root.iterdir():
        if not folder.is_dir():
            continue
        if folder.name == story_part or _safe_name(folder.name) == safe:
            return folder
    return None


def rebuild_drive_voice_job(
    root_dir: Path,
    *,
    texts_dir: Path | None = None,
    job_dir: Path | None = None,
    site_root: Path | None = None,
    human_launch: Path | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """
    Пересобрать только job/kokoro_voices_job.json (и EXPECTED_*) по уже лежащим TXT на Drive.
    Не читает и не пишет texts/ и mp3/.
    """
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    target_texts = (
        (texts_dir if texts_dir.is_absolute() else (root / texts_dir)).resolve()
        if texts_dir is not None
        else _drive_dir_from(root, settings, "texts", "texts")
    )
    target_job = (
        (job_dir if job_dir.is_absolute() else (root / job_dir)).resolve()
        if job_dir is not None
        else _drive_dir_from(root, settings, "job", "job")
    )
    site_output_root = (site_root if site_root is not None else (root / "output" / "site")).resolve()
    hl = human_launch.resolve() if human_launch is not None else None

    txt_paths = _list_drive_text_files(target_texts)
    label_counts: dict[str, int] = {"F": 0, "M": 0, "U": 0}
    voice_counts: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    empty_txt: list[str] = []
    no_suffix_label: list[str] = []
    site_matched = 0
    human_matched = 0

    for txt_path in txt_paths:
        txt_name = txt_path.name
        if txt_path.stat().st_size <= 0:
            empty_txt.append(txt_name)
        story_part, filename_label = _split_story_voice(txt_path.stem)
        if "__" not in txt_path.stem:
            no_suffix_label.append(txt_name)
        story_id = story_part
        paths: SiteTtsPaths | None = None

        if hl is not None:
            folder = _human_story_dir_for_drive_mp3_name(hl, Path(txt_name).with_suffix(".mp3").name)
            if folder is not None:
                human_matched += 1
                story_id = folder.name
                paths = SiteTtsPaths.from_human_launch_story(hl, story_id, ensure_dirs=False)
        elif site_output_root.is_dir():
            site_folder = _find_site_folder_for_drive_story_part(site_output_root, story_part)
            if site_folder is not None:
                site_matched += 1
                story_id = site_folder.name
                paths = SiteTtsPaths.for_site_output_folder(root, site_output_root, story_id)

        item = build_kokoro_drive_voice_item_for_existing_txt(
            txt_name=txt_name,
            settings=settings,
            story_id=story_id,
            paths=paths,
            filename_voice_label=filename_label,
        )
        items.append(item)
        lbl = str(item.get("voice_label") or "U").strip().upper()[:1] or "U"
        if lbl in label_counts:
            label_counts[lbl] += 1
        voice_id = str(item.get("kokoro_voice") or "").strip()
        if voice_id:
            voice_counts[voice_id] = voice_counts.get(voice_id, 0) + 1
        else:
            warnings.append(f"empty_kokoro_voice:{txt_name}")

    expected_mp3 = [str(it.get("mp3_name") or "").strip() for it in items if str(it.get("mp3_name") or "").strip()]
    pool_voice_ids = collect_voice_ids_from_pools(settings)
    missing_pool_voices = sorted(v for v in pool_voice_ids if voice_counts.get(v, 0) == 0)
    min_items_for_pool_warn = max(30, len(pool_voice_ids) * 5)
    pool_coverage_warn = bool(missing_pool_voices and len(items) >= min_items_for_pool_warn)

    if pool_coverage_warn:
        warnings.append(f"pool_voices_never_assigned:{','.join(missing_pool_voices)}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_stem = f"VOICE_JOB_REBUILD_{ts}"
    report_json = target_job / f"{report_stem}.json"
    report_csv = target_job / f"{report_stem}.csv"

    note = (
        "Пересобрано rebuild_drive_voice_job из фактических TXT на Drive (texts/ не изменялись). "
        "Источник голосов: configs/site_tts.yaml (voice_pools, deterministic_pool, info.txt при сопоставлении с output/site)."
    )
    if hl is not None:
        note = (
            "Пересобрано rebuild_drive_voice_job (human-launch) из TXT на Drive. "
            "Источник: configs/site_tts.yaml + info.txt из 05_Рассказы при совпадении имени."
        )

    payload: dict[str, Any] = {
        "version": 1,
        "default_speed": float(settings.kokoro_speed),
        "note": note,
        "items": items,
    }

    summary: dict[str, Any] = {
        "ok": not pool_coverage_warn,
        "dry_run": not execute,
        "texts_dir": str(target_texts),
        "job_dir": str(target_job),
        "txt_found": len(txt_paths),
        "items_written": len(items) if execute else 0,
        "items_planned": len(items),
        "label_counts": label_counts,
        "voice_counts": dict(sorted(voice_counts.items(), key=lambda x: (-x[1], x[0]))),
        "voice_selection_strategy": settings.voice_selection_strategy,
        "pool_voice_ids_expected": sorted(pool_voice_ids),
        "pool_voices_missing_in_job": missing_pool_voices,
        "pool_coverage_warning": pool_coverage_warn,
        "site_story_matches": site_matched,
        "human_story_matches": human_matched,
        "empty_txt_count": len(empty_txt),
        "no_suffix_label_count": len(no_suffix_label),
        "warnings": warnings,
        "texts_touched": False,
        "mp3_touched": False,
        "execute": execute,
    }

    if execute:
        write_kokoro_voices_job_payload(job_dir=target_job, payload=payload)
        target_job.mkdir(parents=True, exist_ok=True)
        (target_job / "EXPECTED_COUNT.txt").write_text(str(len(expected_mp3)) + "\n", encoding="utf-8")
        (target_job / "EXPECTED_FILES.txt").write_text(
            "\n".join(expected_mp3) + ("\n" if expected_mp3 else ""),
            encoding="utf-8",
        )
        summary["items_written"] = len(items)
        summary["voices_job_json"] = str((target_job / VOICE_MANIFEST_FILENAME).resolve())
        summary["expected_files"] = str((target_job / "EXPECTED_FILES.txt").resolve())
    else:
        summary["voices_job_json"] = str((target_job / VOICE_MANIFEST_FILENAME).resolve())

    report_body = {**summary, "empty_txt_sample": empty_txt[:20], "no_suffix_label_sample": no_suffix_label[:20]}
    if execute:
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(json.dumps(report_body, ensure_ascii=False, indent=2), encoding="utf-8")
        with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["txt_name", "voice_label", "kokoro_voice", "voice_source", "speed", "story_folder"])
            for it in items:
                w.writerow(
                    [
                        it.get("txt_name", ""),
                        it.get("voice_label", ""),
                        it.get("kokoro_voice", ""),
                        it.get("voice_source", ""),
                        it.get("speed", ""),
                        it.get("story_folder", ""),
                    ]
                )
        summary["report_json"] = str(report_json)
        summary["report_csv"] = str(report_csv)

    summary["message"] = (
        "dry-run: job не записан (добавьте --execute)"
        if not execute
        else (
            "voice job пересобран"
            if summary["ok"]
            else "voice job пересобран, но не все голоса из voice_pools встретились в назначениях"
        )
    )
    return summary


def _print_rebuild_voice_job_summary(res: dict[str, Any]) -> None:
    print(res.get("message", ""))
    print(f"texts_dir={res.get('texts_dir')}")
    print(f"job_dir={res.get('job_dir')}")
    print(f"TXT found on Drive: {res.get('txt_found')}")
    print(f"items planned: {res.get('items_planned')}")
    print(f"items written to kokoro_voices_job.json: {res.get('items_written')}")
    print(f"dry_run={res.get('dry_run')}")
    print(f"texts_touched={res.get('texts_touched')} mp3_touched={res.get('mp3_touched')}")
    lc = res.get("label_counts") or {}
    print(f"label counts F/M/U: F={lc.get('F', 0)} M={lc.get('M', 0)} U={lc.get('U', 0)}")
    print("voice counts by kokoro_voice:")
    for vid, cnt in (res.get("voice_counts") or {}).items():
        print(f"  {vid}: {cnt}")
    missing = res.get("pool_voices_missing_in_job") or []
    print(f"pool voices missing in job (0 assignments): {len(missing)}")
    if missing:
        print(f"  missing: {', '.join(missing)}")
    if res.get("pool_coverage_warning"):
        print("WARN: not all voices from voice_pools appear in assignments — check configs/site_tts.yaml")
    warns = res.get("warnings") or []
    if warns:
        print(f"warnings ({len(warns)}):")
        for w in warns[:15]:
            print(f"  - {w}")
        if len(warns) > 15:
            print(f"  ... and {len(warns) - 15} more")
    if res.get("voices_job_json"):
        print(f"voices_job_json={res.get('voices_job_json')}")
    if res.get("report_json"):
        print(f"report_json={res.get('report_json')}")
    if res.get("report_csv"):
        print(f"report_csv={res.get('report_csv')}")


def drive_kokoro_job_pending_on_drive(root_dir: Path) -> tuple[bool, dict[str, Any]]:
    """
    True, если на Drive уже есть активный Kokoro job (ожидание mp3), даже если повторный export не нужен.
    Используется site_tts_stage, чтобы не выходить после export с exported=0.
    """
    root = root_dir.resolve()
    try:
        settings = load_site_tts_settings(root)
        job_dir = _drive_dir_from(root, settings, "job", "job")
    except ValueError as exc:
        return False, {"reason": str(exc)}
    st = _read_local_job_status(job_dir) or {}
    state = str(st.get("state", "")).strip()
    expected = _load_expected_files(job_dir)
    if state == "imported_success":
        return False, {"reason": "already_imported_success", "job_dir": str(job_dir)}
    if not expected:
        return False, {"reason": "no_expected_files", "job_dir": str(job_dir)}
    if state == "exported_waiting_mp3":
        return True, {
            "reason": "local_status_exported_waiting_mp3",
            "job_dir": str(job_dir),
            "expected_count": len(expected),
        }
    try:
        texts_dir = _drive_dir_from(root, settings, "texts", "texts")
    except ValueError:
        texts_dir = None
    if texts_dir is not None and texts_dir.is_dir():
        present = 0
        for name in expected[:50]:
            txt = texts_dir / Path(name).with_suffix(".txt").name
            if txt.is_file() and txt.stat().st_size > 0:
                present += 1
        if present > 0:
            return True, {
                "reason": "drive_txt_and_expected_job",
                "job_dir": str(job_dir),
                "expected_count": len(expected),
                "txt_sample_present": present,
            }
    return False, {"reason": "no_pending_markers", "job_state": state, "job_dir": str(job_dir)}


def _should_skip_redundant_drive_export(*, texts_dir: Path, job_dir: Path, site_root: Path) -> tuple[bool, str]:
    """
    If the previous batch is still waiting for mp3 and all expected txt are already on the synced
    Drive texts folder, do not re-copy dozens of files on every orchestrator run.
    If the user removed txt from Drive, this returns (False, ...) so export runs again.
    """
    if os.environ.get("CONTENT_FACTORY_FORCE_DRIVE_REEXPORT", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False, ""
    st = _read_local_job_status(job_dir)
    if not st or str(st.get("state", "")).strip() != "exported_waiting_mp3":
        return False, ""
    expected_mp3 = _load_expected_files(job_dir)
    if not expected_mp3:
        return False, ""
    all_txt_ok = True
    any_missing_local_mp3 = False
    for m in expected_mp3:
        txt_path = texts_dir / Path(m).with_suffix(".txt").name
        if not txt_path.is_file() or txt_path.stat().st_size <= 0:
            all_txt_ok = False
            break
        story_raw, _v = _split_story_voice(Path(m).stem)
        story = _safe_name(story_raw)
        out_mp3 = site_root / story / f"{story}.mp3"
        if not out_mp3.is_file() or out_mp3.stat().st_size <= 0:
            any_missing_local_mp3 = True
    if all_txt_ok and any_missing_local_mp3:
        return True, "pending_job_txts_still_on_drive"
    return False, ""


def _run_combiner_distribute_images(
    root_dir: Path,
    *,
    site_output_root: Path,
    artifact_root: Path | None,
) -> tuple[bool, str]:
    from orchestrator.wrappers.content_combiner import run_content_combiner_modes

    ar = artifact_root if artifact_root is not None else root_dir
    return run_content_combiner_modes(
        root_dir=root_dir,
        modes=["distribute-images"],
        site_stories_dir=site_output_root.resolve(),
        artifact_root=ar.resolve(),
        capture_output=False,
    )


def _human_mirror_site_artifacts_after_import(site_output_root: Path, expected_mp3_basenames: list[str]) -> None:
    """Дубликаты mp3/info/txt в Запуски/<имя>/02_Сайт/01…02…04 (если site под изолированным запуском)."""
    from orchestrator.human_launch_layout import launch_dir_from_site_output_root, mirror_site_story_outputs_from_legacy_site

    launch_h = launch_dir_from_site_output_root(site_output_root)
    if launch_h is None:
        return
    ordered: list[str] = []
    seen: set[str] = set()
    for name in expected_mp3_basenames:
        story_raw, _v = _split_story_voice(Path(name).stem)
        story = _safe_name(story_raw)
        if story and story not in seen:
            seen.add(story)
            ordered.append(story)
    if not ordered:
        return
    mirror_site_story_outputs_from_legacy_site(launch_h, site_output_root, ordered)
    print(f"[site_tts] зеркало артефактов в {launch_h / '02_Сайт'} (01_Очистка, 02_Информация, 04_Озвучка)", flush=True)


def export_drive_texts(
    root_dir: Path,
    *,
    texts_dir: Path | None = None,
    limit: int | None = None,
    stories_filter_dir: Path | None = None,
    site_root: Path | None = None,
    human_launch: Path | None = None,
    job_only: bool = False,
    execute: bool = True,
) -> dict[str, Any]:
    """
    stories_filter_dir: если задан и не пуст по *.txt, экспортируются только папки output/site,
    чьё normalize_site_story_name(folder) совпадает с одним из стемов в этом каталоге
    (тот же контракт, что у site-tts batch для *-site). Иначе — вся очередь без mp3 (старое поведение).
    job_only: только пересборка kokoro_voices_job.json по TXT на Drive (texts/mp3 не трогать).
    """
    root = root_dir.resolve()
    if job_only:
        return rebuild_drive_voice_job(
            root,
            texts_dir=texts_dir,
            site_root=site_root,
            human_launch=human_launch,
            execute=execute,
        )
    settings = load_site_tts_settings(root)
    hl = human_launch.resolve() if human_launch is not None else None
    target = (
        (texts_dir if texts_dir.is_absolute() else (root / texts_dir)).resolve()
        if texts_dir is not None
        else _drive_dir_from(root, settings, "texts", "texts")
    )
    target.mkdir(parents=True, exist_ok=True)
    index_csv = target.parent / "STORIES_INDEX.csv"
    drive_root = target.parent
    scripts_dir = _drive_dir_from(root, settings, "scripts", "scripts")
    cache_dir = _drive_dir_from(root, settings, "cache", "cache")
    logs_dir = _drive_dir_from(root, settings, "logs", "logs")
    job_dir = _drive_dir_from(root, settings, "job", "job")
    mp3_dir = _drive_dir_from(root, settings, "mp3", "mp3")
    scripts_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    mp3_dir.mkdir(parents=True, exist_ok=True)

    if hl is not None and not (hl / D05_RASSKAZY).is_dir():
        return {
            "ok": False,
            "texts_dir": str(target),
            "mp3_dir": str(mp3_dir),
            "drive_root": str(drive_root),
            "job_dir": str(job_dir),
            "index_csv": str(index_csv),
            "exported": 0,
            "skipped": 0,
            "stories_filter_applied": False,
            "voices_job_json": "",
            "message": f"human launch has no {D05_RASSKAZY}: {hl}",
        }

    site_output_root = (site_root if site_root is not None else (root / "output" / "site")).resolve()
    allowed_story_keys: frozenset[str] | None = None
    _normalize_site_story_name = None
    if hl is None and stories_filter_dir is not None:
        sfd = (stories_filter_dir if stories_filter_dir.is_absolute() else (root / stories_filter_dir)).resolve()
        if sfd.is_dir():
            from orchestrator.site_tts.batch import normalize_site_story_name as _normalize_site_story_name

            tmp_keys = {
                _normalize_site_story_name(p.stem).lower()
                for p in sfd.iterdir()
                if p.is_file() and p.suffix.lower() == ".txt"
            }
            if tmp_keys:
                allowed_story_keys = frozenset(tmp_keys)

    skip = False
    skip_reason = ""
    if hl is None and allowed_story_keys is None:
        skip, skip_reason = _should_skip_redundant_drive_export(texts_dir=target, job_dir=job_dir, site_root=site_output_root)
    if skip:
        from orchestrator.wrappers.content_combiner import run_content_combiner_modes

        ok_pr_h, err_pr_h = run_content_combiner_modes(
            root_dir=root,
            modes=["export-prompts"],
            site_stories_dir=site_output_root,
            artifact_root=None,
            capture_output=False,
        )
        if not ok_pr_h:
            print(f"[WARN] export-prompts (resume, без повторного export): {err_pr_h}", flush=True)
        else:
            print("[site_tts] stories_export.csv/xlsx обновлены (export-prompts) при resume ожидания mp3.", flush=True)
        return {
            "ok": True,
            "texts_dir": str(target),
            "mp3_dir": str(mp3_dir),
            "drive_root": str(drive_root),
            "job_dir": str(job_dir),
            "index_csv": str(index_csv),
            "exported": 0,
            "skipped": 0,
            "resume_wait_for_pending_job": True,
            "skipped_reason": skip_reason,
            "voices_job_json": "",
            "message": "Пакет txt уже на Drive; повторный export пропущен — продолжаем ожидание mp3/import.",
            "stories_filter_applied": bool(allowed_story_keys),
        }

    from orchestrator.wrappers.content_combiner import run_content_combiner_modes

    if hl is None:
        ok_pr, err_pr = run_content_combiner_modes(
            root_dir=root,
            modes=["export-prompts"],
            site_stories_dir=site_output_root,
            artifact_root=None,
            capture_output=False,
        )
        if not ok_pr:
            return {
                "ok": False,
                "texts_dir": str(target),
                "mp3_dir": str(mp3_dir),
                "drive_root": str(drive_root),
                "job_dir": str(job_dir),
                "index_csv": str(index_csv),
                "exported": 0,
                "skipped": 0,
                "stories_filter_applied": bool(allowed_story_keys),
                "voices_job_json": "",
                "message": f"content_combiner export-prompts failed: {err_pr}",
            }
        print(
            "[site_tts] stories_export.csv/xlsx обновлены (export-prompts) перед выгрузкой txt на Drive. "
            "Пути к Excel и к папке для обложек — в файлах ЧИТАЙ_МЕНЯ_*.txt рядом с таблицей и в IMAGES_IN.",
            flush=True,
        )
    else:
        print(
            "[site_tts] human-launch export-drive: export-prompts пропущен (источник — 05_Рассказы, не legacy output/site).",
            flush=True,
        )

    export_diag_path = job_dir / _EXPORT_DIAG_JSONL
    export_diag_path.write_text("", encoding="utf-8")
    exported_rows: list[list[str]] = []
    skipped_rows: list[list[str]] = []
    lim = None if limit is None or int(limit) <= 0 else int(limit)
    exported = 0
    _story_iter = iter_human_launch_story_dirs(hl) if hl is not None else _iter_story_dirs(site_output_root)
    for i, story_folder in enumerate(_story_iter, start=1):
        if hl is not None:
            src, err = _resolve_story_human_colab_source(hl, story_folder)
        else:
            src, err = _resolve_story_tts_source(story_folder)
        if src is None:
            skipped_rows.append([f"{i:03d}", story_folder.name, "", "", "", f"skip:{err or 'unknown'}"])
            _append_export_diag(
                export_diag_path,
                {
                    "story_id": story_folder.name,
                    "source_path": "",
                    "dest_path": "",
                    "raw_chars": 0,
                    "cleaned_chars": 0,
                    "url_like_count_before": 0,
                    "url_like_count_after": 0,
                    "removed_lines_count": 0,
                    "preview_500": "",
                    "expected_files_entry": "",
                    "status": "error",
                    "reason": f"resolve_source_failed:{err or 'unknown'}",
                },
            )
            continue
        if hl is None and allowed_story_keys is not None and _normalize_site_story_name is not None:
            key = _normalize_site_story_name(src.story_id).lower()
            if key not in allowed_story_keys:
                skipped_rows.append(
                    [f"{i:03d}", src.story_id, src.tts_text_path.name, "", "", "skip:not_in_stories_filter_dir"]
                )
                _append_export_diag(
                    export_diag_path,
                    {
                        "story_id": src.story_id,
                        "source_path": str(src.tts_text_path),
                        "dest_path": "",
                        "raw_chars": 0,
                        "cleaned_chars": 0,
                        "url_like_count_before": 0,
                        "url_like_count_after": 0,
                        "removed_lines_count": 0,
                        "preview_500": "",
                        "expected_files_entry": "",
                        "status": "skipped",
                        "reason": "skip:not_in_stories_filter_dir",
                    },
                )
                continue
        if hl is None and src.has_mp3:
            skipped_rows.append([f"{i:03d}", src.story_id, src.tts_text_path.name, f"{src.story_id}__{src.voice_type}.mp3", str(src.expected_output_mp3), "skip:has_mp3"])
            _append_export_diag(
                export_diag_path,
                {
                    "story_id": src.story_id,
                    "source_path": str(src.tts_text_path),
                    "dest_path": "",
                    "raw_chars": 0,
                    "cleaned_chars": 0,
                    "url_like_count_before": 0,
                    "url_like_count_after": 0,
                    "removed_lines_count": 0,
                    "preview_500": "",
                    "expected_files_entry": "",
                    "status": "error",
                    "reason": "skip:has_mp3",
                },
            )
            continue
        txt_name = f"{_safe_name(src.story_id)}__{src.voice_type}.txt"
        mp3_name = f"{_safe_name(src.story_id)}__{src.voice_type}.mp3"
        if Path(txt_name).stem != Path(mp3_name).stem:
            skipped_rows.append([f"{i:03d}", src.story_id, src.tts_text_path.name, mp3_name, str(src.expected_output_mp3), "skip:stem_mismatch"])
            _append_export_diag(
                export_diag_path,
                {
                    "story_id": src.story_id,
                    "source_path": str(src.tts_text_path),
                    "dest_path": "",
                    "raw_chars": 0,
                    "cleaned_chars": 0,
                    "url_like_count_before": 0,
                    "url_like_count_after": 0,
                    "removed_lines_count": 0,
                    "preview_500": "",
                    "expected_files_entry": mp3_name,
                    "status": "error",
                    "reason": "txt_mp3_stem_mismatch",
                },
            )
            print(
                f"[WARN] export-drive skipped: story={src.story_id} reason=txt_mp3_stem_mismatch txt={txt_name} mp3={mp3_name}",
                flush=True,
            )
            continue
        dst = target / txt_name
        raw_text = src.tts_text_path.read_text(encoding="utf-8")
        cleaned_text, url_before, url_after, removed_lines, lit_diag = _clean_text_for_drive_tts(raw_text)
        if url_after > 0:
            skipped_rows.append([f"{i:03d}", src.story_id, src.tts_text_path.name, mp3_name, str(src.expected_output_mp3), "skip:dirty_after_clean"])
            _append_export_diag(
                export_diag_path,
                {
                    "story_id": src.story_id,
                    "source_path": str(src.tts_text_path),
                    "dest_path": str(dst),
                    "raw_chars": len(raw_text),
                    "cleaned_chars": len(cleaned_text),
                    "url_like_count_before": url_before,
                    "url_like_count_after": url_after,
                    "removed_lines_count": removed_lines,
                    "preview_500": cleaned_text[:500],
                    "expected_files_entry": mp3_name,
                    "status": "skipped_dirty",
                    "reason": "url_or_domain_patterns_left_after_clean",
                },
            )
            print(
                f"[WARN] export-drive skipped dirty text: story={src.story_id} source={src.tts_text_path} remaining_url_like={url_after}",
                flush=True,
            )
            continue
        if not cleaned_text.strip():
            skipped_rows.append([f"{i:03d}", src.story_id, src.tts_text_path.name, mp3_name, str(src.expected_output_mp3), "skip:empty_after_clean"])
            _append_export_diag(
                export_diag_path,
                {
                    "story_id": src.story_id,
                    "source_path": str(src.tts_text_path),
                    "dest_path": str(dst),
                    "raw_chars": len(raw_text),
                    "cleaned_chars": 0,
                    "url_like_count_before": url_before,
                    "url_like_count_after": 0,
                    "removed_lines_count": removed_lines,
                    "preview_500": "",
                    "expected_files_entry": mp3_name,
                    "status": "error",
                    "reason": "empty_after_clean",
                },
            )
            print(
                f"[WARN] export-drive skipped empty text after clean: story={src.story_id} source={src.tts_text_path}",
                flush=True,
            )
            continue
        dst.write_text(cleaned_text, encoding="utf-8")
        exported += 1
        dest_cell = str(src.expected_output_mp3) if hl is None else _rel_posix(
            SiteTtsPaths.from_human_launch_story(hl, src.story_id, ensure_dirs=False).output_mp3, root
        )
        exported_rows.append([f"{exported:03d}", src.story_id, txt_name, mp3_name, dest_cell, src.voice_type])
        _append_export_diag(
            export_diag_path,
            {
                "story_id": src.story_id,
                "source_path": str(src.tts_text_path),
                "dest_path": str(dst),
                "raw_chars": len(raw_text),
                "cleaned_chars": len(cleaned_text),
                "url_like_count_before": url_before,
                "url_like_count_after": 0,
                "removed_lines_count": removed_lines,
                "removed_literotica_header_lines_count": lit_diag.get("removed_literotica_header_lines_count", 0),
                "removed_literotica_header_lines_sample": lit_diag.get("removed_literotica_header_lines_sample", []),
                "literotica_header_warning": lit_diag.get("literotica_header_warning"),
                "preview_500": cleaned_text[:500],
                "expected_files_entry": mp3_name,
                "status": "exported",
            },
        )
        if lim is not None and exported >= lim:
            break

    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["number", "story_folder", "source_txt", "expected_mp3", "final_output_path", "voice"])
        w.writerows(exported_rows)
        if skipped_rows:
            w.writerow([])
            w.writerow(["number", "story_folder", "source_txt", "expected_mp3", "final_output_path", "note"])
            w.writerows(skipped_rows[:200])

    expected_files = [r[3] for r in exported_rows]
    write_kokoro_voices_job_json(
        project_root=root,
        job_dir=job_dir,
        site_root=site_output_root,
        exported_rows=exported_rows,
        settings=settings,
        human_launch=hl,
    )
    job = {
        "job_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "created_at": _utc_now_iso(),
        "expected_count": len(expected_files),
        "expected_files": expected_files,
        "texts_dir": str(target),
        "mp3_dir": str(mp3_dir),
        "state": "exported_waiting_mp3",
    }
    (job_dir / "current_job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "EXPECTED_COUNT.txt").write_text(str(len(expected_files)) + "\n", encoding="utf-8")
    (job_dir / "EXPECTED_FILES.txt").write_text("\n".join(expected_files) + ("\n" if expected_files else ""), encoding="utf-8")
    (job_dir / "LOCAL_STATUS.json").write_text(
        json.dumps(
            {
                "state": "exported_waiting_mp3",
                "expected_count": len(expected_files),
                "found_mp3": 0,
                "updated_at": _utc_now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if hl is not None and exported_rows:
        try:
            _mirror_human_drive_export_to_colab_current(
                root, target, index_csv, exported_rows, job_batch_id=str(job["job_id"])
            )
        except OSError as exc:
            print(f"[WARN] COLAB_TTS_CURRENT mirror (human Drive export): {exc}", flush=True)

    if hl is not None and exported_rows:
        side = {
            "mode": "human_launch_drive_export",
            "launch": str(hl),
            "job_id": job["job_id"],
            "texts_dir": str(target),
            "mp3_dir": str(mp3_dir),
            "job_dir": str(job_dir),
            "exported": len(exported_rows),
            "expected_files": expected_files,
        }
        tmp = hl / D06_OTCHETY / f"kokoro_drive_export_{job['job_id']}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(side, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            _copy_colab_report_to_launch_sidecars(hl, str(job["job_id"]), tmp, "kokoro_drive_export")
        except OSError:
            pass

    launch_h = None
    try:
        from orchestrator.human_launch_layout import launch_dir_from_site_output_root, mirror_exported_drive_txt_to_human_clean

        launch_h = hl if hl is not None else launch_dir_from_site_output_root(site_output_root)
        if launch_h is not None and exported_rows:
            for row in exported_rows:
                if len(row) < 3:
                    continue
                _n, sid, txt_name = row[0], row[1], row[2]
                mirror_exported_drive_txt_to_human_clean(
                    launch_h,
                    drive_texts_dir=target,
                    story_folder_name=str(sid),
                    txt_name=str(txt_name),
                )
            print(f"[site_tts] копии txt для Colab → {launch_h / '02_Сайт' / '01_Очистка_текста'}", flush=True)
    except OSError as exc:
        print(f"[WARN] human mirror export txt: {exc}", flush=True)

    out_ret: dict[str, Any] = {
        "ok": True,
        "texts_dir": str(target),
        "mp3_dir": str(mp3_dir),
        "drive_root": str(drive_root),
        "job_dir": str(job_dir),
        "index_csv": str(index_csv),
        "exported": exported,
        "skipped": len(skipped_rows),
        "stories_filter_applied": bool(allowed_story_keys),
        "voices_job_json": str((job_dir / VOICE_MANIFEST_FILENAME).resolve()) if exported_rows else "",
        "message": "TXT copied to Google Drive texts folder",
    }
    if hl is not None:
        cdir, ctexts, cmp3 = _current_paths(root)
        out_ret["colab_current_dir"] = str(cdir)
        out_ret["colab_current_texts"] = str(ctexts)
        out_ret["colab_current_mp3"] = str(cmp3)
    return out_ret


def import_drive_mp3(
    root_dir: Path,
    *,
    mp3_dir: Path | None = None,
    force: bool = False,
    site_root: Path | None = None,
    human_launch: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    source = (
        (mp3_dir if mp3_dir.is_absolute() else (root / mp3_dir)).resolve()
        if mp3_dir is not None
        else _drive_dir_from(root, settings, "mp3", "mp3")
    )
    source.mkdir(parents=True, exist_ok=True)
    job_dir = _drive_dir_from(root, settings, "job", "job")
    expected = set(_load_expected_files(job_dir))
    resolution = _colab_expected_resolution(expected_set=expected, mp3_dir=source, job_dir=job_dir) if expected else {}
    terminal_non_mp3 = set(resolution.get("failed_terminal") or []) | set(resolution.get("manual_skipped") or [])
    site_output_root = (site_root if site_root is not None else (root / "output" / "site")).resolve()
    hl = human_launch.resolve() if human_launch is not None else None

    imported = 0
    skipped_existing = 0
    missing_story = 0
    invalid_mp3 = 0
    errors = 0
    details: list[dict[str, str]] = []
    for mp3 in sorted(source.glob("*.mp3")):
        if expected and mp3.name not in expected:
            missing_story += 1
            details.append({"status": "extra_mp3", "file": mp3.name, "reason": "not_in_expected_files"})
            continue
        try:
            size = mp3.stat().st_size
        except OSError as exc:
            errors += 1
            details.append({"status": "error", "file": mp3.name, "reason": str(exc)})
            continue
        if size <= 0:
            invalid_mp3 += 1
            details.append({"status": "invalid_mp3", "file": mp3.name, "reason": "empty_file"})
            continue
        if hl is not None:
            hf = _human_story_dir_for_drive_mp3_name(hl, mp3.name)
            if hf is None:
                missing_story += 1
                details.append({"status": "extra_mp3", "file": mp3.name, "reason": "human_story_folder_not_found"})
                continue
            story_folder_for_detail = hf.name
            dst = SiteTtsPaths.from_human_launch_story(hl, hf.name, ensure_dirs=True).output_mp3
        else:
            story, _voice = _split_story_voice(mp3.stem)
            story = _safe_name(story)
            story_folder_for_detail = story
            folder = site_output_root / story
            if not folder.is_dir():
                missing_story += 1
                details.append({"status": "extra_mp3", "file": mp3.name, "reason": "story_folder_not_found"})
                continue
            dst = folder / f"{story}.mp3"
        need_write = True
        if dst.is_file() and not force:
            try:
                if dst.stat().st_size == size:
                    need_write = False
            except OSError:
                need_write = True
        if not need_write:
            skipped_existing += 1
            details.append(
                {"status": "skipped_identical", "file": mp3.name, "path": str(dst), "story_folder": story_folder_for_detail}
            )
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(mp3.read_bytes())
            imported += 1
            details.append(
                {"status": "imported", "file": mp3.name, "path": str(dst), "story_folder": story_folder_for_detail}
            )
        except OSError as exc:
            errors += 1
            details.append({"status": "error", "file": mp3.name, "reason": str(exc)})
    handled = {d["file"] for d in details if d.get("status") in ("imported", "skipped_identical")}
    missing_after: list[str] = []
    if expected:
        missing_after = sorted(expected - handled - terminal_non_mp3)
    ok_import = errors == 0
    out: dict[str, Any] = {
        "ok": ok_import,
        "mp3_dir": str(source),
        "job_dir": str(job_dir),
        "imported": imported,
        "skipped_existing": skipped_existing,
        "missing_story": missing_story,
        "invalid_mp3": invalid_mp3,
        "errors": errors,
        "details": details,
        "missing_after_import": missing_after,
        "manual_skipped": sorted(resolution.get("manual_skipped") or []),
        "failed_terminal": sorted(resolution.get("failed_terminal") or []),
        "terminal_non_mp3_count": len(terminal_non_mp3),
        "partial_import": bool(missing_after),
        "can_continue_to_site_publish": errors == 0,
    }
    _write_json(_local_reports_dir(root) / "site_tts_drive_import_report.json", out)
    if hl is not None:
        try:
            tag = _utc_now_iso().replace(":", "-")
            p = hl / D06_OTCHETY / f"kokoro_drive_import_{tag}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            _copy_colab_report_to_launch_sidecars(hl, tag, p, "kokoro_drive_import")
            imp_items = [d for d in details if d.get("status") == "imported" and d.get("story_folder")]
            if imp_items:
                st_path = hl / D06_OTCHETY / f"kokoro_drive_imported_stories_{tag}.json"
                st_payload = {"updated_at": _utc_now_iso(), "items": imp_items}
                st_path.write_text(json.dumps(st_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                _copy_colab_report_to_launch_sidecars(hl, tag, st_path, "kokoro_drive_imported_stories")
                latest = hl / D06_OTCHETY / "kokoro_drive_imported_stories_latest.json"
                latest.write_text(json.dumps(st_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    _copy_colab_report_to_launch_sidecars(hl, "state", latest, "kokoro_drive_imported_stories_latest")
                except OSError:
                    pass
        except OSError:
            pass
    return out


def verify_drive_status(
    root_dir: Path,
    *,
    texts_dir: Path | None = None,
    mp3_dir: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    texts = (
        (texts_dir if texts_dir.is_absolute() else (root / texts_dir)).resolve()
        if texts_dir is not None
        else _drive_dir_from(root, settings, "texts", "texts")
    )
    mp3 = (
        (mp3_dir if mp3_dir.is_absolute() else (root / mp3_dir)).resolve()
        if mp3_dir is not None
        else _drive_dir_from(root, settings, "mp3", "mp3")
    )
    job_dir = _drive_dir_from(root, settings, "job", "job")
    texts.mkdir(parents=True, exist_ok=True)
    mp3.mkdir(parents=True, exist_ok=True)

    txt_files = sorted([p for p in texts.glob("*.txt") if p.is_file()])
    mp3_files = sorted([p for p in mp3.glob("*.mp3") if p.is_file()])
    valid_mp3_stems: set[str] = set()
    invalid_mp3_names: list[str] = []
    for p in mp3_files:
        try:
            if p.stat().st_size > 0:
                valid_mp3_stems.add(p.stem)
            else:
                invalid_mp3_names.append(p.name)
        except OSError:
            invalid_mp3_names.append(p.name)
    txt_set = {p.stem for p in txt_files}
    expected_mp3 = set(_load_expected_files(job_dir))
    if expected_mp3:
        expected_stems = {Path(x).stem for x in expected_mp3}
    else:
        expected_stems = {p.stem for p in txt_files}
    missing = sorted(expected_stems - valid_mp3_stems)
    extra = sorted(valid_mp3_stems - expected_stems)
    can_import = sorted(expected_stems & valid_mp3_stems)
    return {
        "ok": True,
        "texts_dir": str(texts),
        "mp3_dir": str(mp3),
        "job_dir": str(job_dir),
        "texts_count": len(txt_set),
        "mp3_count": len(mp3_files),
        "valid_mp3_count": len(valid_mp3_stems),
        "invalid_mp3_count": len(invalid_mp3_names),
        "can_import": len(can_import),
        "missing_mp3": len(missing),
        "extra_mp3": len(extra),
        "first_missing": missing[:20],
        "first_extra": extra[:20],
        "first_invalid": invalid_mp3_names[:20],
    }


_SILENT_STUB_MP3 = Path(__file__).resolve().parent / "_silent_stub.mp3"


def drive_mp3_wait_skip_requested() -> bool:
    """
    True — не ждать mp3 на Drive: локальные stub + снять pending (см. resolve_pending_drive_mp3_job_with_local_stub).

    Одноразово при зависшем job: CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT=1 (или флаг рядом с bat).
    По умолчанию без этой переменной — обычное ожидание Colab/Drive и import.
    """
    v = (os.environ.get("CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT") or "").strip().lower()
    return v in {"1", "true", "yes", "on", "y"}


def resolve_pending_drive_mp3_job_with_local_stub(
    root_dir: Path,
    *,
    site_root: Path,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """
    Обход зависшего ожидания Colab/Drive: для каждой строки в job/EXPECTED_FILES.txt кладём локальный
    короткий silent .mp3 в output/site/<story>/<story>.mp3 и снимаем pending на Drive (удаляем EXPECTED_FILES.txt).

    Вызывается только при CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT=1 (см. SiteTtsStageWrapper).
    """
    root = root_dir.resolve()
    site_output_root = site_root.resolve()
    stub = _SILENT_STUB_MP3.resolve()
    if not stub.is_file():
        return {
            "ok": False,
            "message": (
                f"stub mp3 missing: {stub} "
                "(ffmpeg: ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 0.05 -q:a 9 -acodec libmp3lame _silent_stub.mp3)"
            ),
        }
    settings = load_site_tts_settings(root)
    job_dir = _drive_dir_from(root, settings, "job", "job")
    expected = _load_expected_files(job_dir)
    if not expected:
        return {
            "ok": True,
            "skipped_drive_wait": True,
            "stories": [],
            "message": "EXPECTED_FILES пуст или отсутствует — обход не нужен",
        }
    stub_bytes = stub.read_bytes()
    placed: list[str] = []
    for name in expected:
        story_raw, _v = _split_story_voice(Path(name).stem)
        story = _safe_name(story_raw)
        folder = site_output_root / story
        if not folder.is_dir():
            return {
                "ok": False,
                "message": f"нет папки истории для {name!r}: {folder}",
            }
        dst = folder / f"{story}.mp3"
        dst.write_bytes(stub_bytes)
        placed.append(story)
    exp_path = job_dir / "EXPECTED_FILES.txt"
    try:
        if exp_path.is_file():
            exp_path.unlink()
    except OSError:
        pass
    try:
        (job_dir / "LOCAL_STATUS.json").write_text(
            json.dumps(
                {
                    "state": "skipped_drive_wait_local_stub_mp3",
                    "expected_count": len(expected),
                    "stories": placed,
                    "updated_at": _utc_now_iso(),
                    "note": "CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT — локальный stub без mp3 с Drive; при необходимости замени аудио.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        return {"ok": False, "message": f"не удалось записать LOCAL_STATUS.json: {exc}"}
    print(
        "[WARN] CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT: локальные stub .mp3 для "
        f"{len(placed)} историй; EXPECTED_FILES.txt на Drive удалён. Список: {placed}",
        flush=True,
    )
    ok_di, err_di = _run_combiner_distribute_images(root, site_output_root=site_output_root, artifact_root=artifact_root)
    if not ok_di:
        return {
            "ok": False,
            "skipped_drive_wait": True,
            "stories": placed,
            "message": f"stub mp3 ok, distribute-images failed: {err_di}",
        }
    _human_mirror_site_artifacts_after_import(site_output_root, list(expected))
    return {
        "ok": True,
        "skipped_drive_wait": True,
        "stories": placed,
        "message": "локальные stub mp3; ожидание Drive пропущено; distribute-images выполнен",
    }


def setup_drive_workspace(root_dir: Path) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    drive_root = _drive_root(root, settings)
    if drive_root is None:
        raise ValueError("google_drive_tts.root_dir is not configured in configs/site_tts.yaml")
    texts = _drive_dir_from(root, settings, "texts", "texts")
    mp3 = _drive_dir_from(root, settings, "mp3", "mp3")
    scripts = _drive_dir_from(root, settings, "scripts", "scripts")
    cache = _drive_dir_from(root, settings, "cache", "cache")
    logs = _drive_dir_from(root, settings, "logs", "logs")
    job = _drive_dir_from(root, settings, "job", "job")
    for d in (texts, mp3, scripts, cache, logs, job):
        d.mkdir(parents=True, exist_ok=True)
    src_runner = (root / "colab" / "kokoro_google_drive_colab.py").resolve()
    copied = False
    if src_runner.is_file():
        (scripts / "kokoro_google_drive_colab.py").write_text(src_runner.read_text(encoding="utf-8"), encoding="utf-8")
        copied = True
    return {
        "ok": True,
        "drive_root": str(drive_root),
        "texts_dir": str(texts),
        "mp3_dir": str(mp3),
        "scripts_dir": str(scripts),
        "cache_dir": str(cache),
        "logs_dir": str(logs),
        "job_dir": str(job),
        "runner_copied": copied,
        "colab_cmd": "python /content/drive/MyDrive/ContentFactory_TTS/scripts/kokoro_google_drive_colab.py",
    }


def wait_drive_mp3_and_import(
    root_dir: Path,
    *,
    mp3_dir: Path | None = None,
    wait_interval_minutes: int | None = None,
    max_wait_hours: int | None = None,
    force: bool = False,
    site_root: Path | None = None,
    artifact_root: Path | None = None,
    human_launch: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    source = (
        (mp3_dir if mp3_dir.is_absolute() else (root / mp3_dir)).resolve()
        if mp3_dir is not None
        else _drive_dir_from(root, settings, "mp3", "mp3")
    )
    job_dir = _drive_dir_from(root, settings, "job", "job")
    source.mkdir(parents=True, exist_ok=True)
    expected = _load_expected_files(job_dir)
    if not expected:
        return {"ok": False, "message": f"EXPECTED_FILES.txt is empty or missing in {job_dir}"}
    interval = max(1, int(wait_interval_minutes or settings.google_drive_wait_interval_minutes))
    max_hours = max(1, int(max_wait_hours or settings.google_drive_max_wait_hours))
    started = time.time()
    deadline = started + max_hours * 3600
    expected_set = set(expected)
    hl = human_launch.resolve() if human_launch is not None else None

    print("Waiting for Kokoro MP3 on Google Drive...", flush=True)
    print(f"mp3_dir={source}", flush=True)
    print(f"job_dir={job_dir}", flush=True)
    print(f"expected_total={len(expected_set)}", flush=True)
    print(f"wait_interval_minutes={interval}", flush=True)
    print(f"timeout_hours={max_hours}", flush=True)

    last_status: dict[str, Any] = {}
    while True:
        found_files = [p for p in source.glob("*.mp3") if p.is_file()]
        valid_set = {p.name for p in found_files if p.stat().st_size >= _COLAB_MIN_MP3_BYTES}
        zero_size = [p.name for p in found_files if p.stat().st_size < _COLAB_MIN_MP3_BYTES]
        resolution = _colab_expected_resolution(expected_set=expected_set, mp3_dir=source, job_dir=job_dir)
        missing = list(resolution["unresolved"])
        ready_set = valid_set & expected_set
        failed_terminal = list(resolution["failed_terminal"])
        manual_skipped = list(resolution["manual_skipped"])
        extra = sorted(valid_set - expected_set)
        elapsed_h = (time.time() - started) / 3600.0
        colab_st = resolution.get("colab_status") if isinstance(resolution.get("colab_status"), dict) else {}
        terminal_payload = _terminal_status_payload(
            expected_set=expected_set,
            ready_set=ready_set,
            manual_skipped=manual_skipped,
            failed_terminal=failed_terminal,
            real_missing=missing,
            extra=extra,
            zero_size=zero_size,
            mp3_dir=source,
            job_dir=job_dir,
        )
        _write_terminal_reports(root_dir=root, job_dir=job_dir, payload=terminal_payload)
        last_status = {
            "expected": len(expected_set),
            "ready": len(ready_set),
            "found": int(resolution["resolved_count"]),
            "missing": len(missing),
            "real_missing": len(missing),
            "effective_missing": len(missing),
            "zero_size": len(zero_size),
            "extra": len(extra),
            "colab_done": bool(resolution["colab_done"]),
            "failed_terminal": len(failed_terminal),
            "manual_skipped": len(manual_skipped),
            "colab_failed_count": int(colab_st.get("failed_count", 0) or 0),
            "completed_with_failed": bool(colab_st.get("completed_with_failed", False)),
            "completed": bool(terminal_payload["completed"]),
            "can_continue": bool(terminal_payload["can_continue"]),
            "can_continue_to_publish": bool(terminal_payload["can_continue_to_publish"]),
            "can_continue_to_site_publish": bool(terminal_payload["can_continue_to_site_publish"]),
            "ready_to_publish_count": int(terminal_payload["ready_to_publish_count"]),
            "real_missing_count": int(terminal_payload["real_missing_count"]),
            "extra_mp3_count": int(terminal_payload["extra_mp3_count"]),
            "next_check_in_minutes": interval,
            "elapsed_hours": round(elapsed_h, 2),
            "timeout_hours": max_hours,
            "mp3_dir": str(source),
            "report_path": str(_local_reports_dir(root) / "site_tts_drive_wait_report.json"),
        }
        print(
            f"expected={last_status['expected']} ready={last_status['ready']} missing={last_status['missing']} "
            f"zero_size={last_status['zero_size']} extra={last_status['extra']} "
            f"colab_done={last_status['colab_done']} failed_terminal={last_status['failed_terminal']} "
            f"manual_skipped={last_status['manual_skipped']} "
            f"can_continue={last_status['can_continue']} completed={last_status['completed']} "
            f"elapsed_h={last_status['elapsed_hours']:.2f} timeout_h={max_hours} "
            f"next_check_in={interval}_min",
            flush=True,
        )
        if missing:
            print(f"missing_sample={missing[:5]}", flush=True)
        if terminal_payload["can_continue_to_publish"]:
            break
        if time.time() >= deadline:
            return {
                "ok": False,
                "message": "max wait time exceeded",
                "status": last_status,
                "missing_files": missing[:50],
                "zero_size_files": zero_size[:50],
            }
        time.sleep(interval * 60)

    site_output_root = (site_root if site_root is not None else (root / "output" / "site")).resolve()
    imp = import_drive_mp3(
        root,
        mp3_dir=source,
        force=force,
        site_root=site_output_root,
        human_launch=hl,
    )
    if not imp.get("ok", False) or int(imp.get("errors", 0) or 0) > 0:
        return {"ok": False, "message": "import-drive failed", "import": imp, "status": last_status}

    ok_di, err_di = _run_combiner_distribute_images(root, site_output_root=site_output_root, artifact_root=artifact_root)
    if not ok_di:
        return {"ok": False, "message": f"distribute-images failed: {err_di}", "import": imp, "status": last_status}
    print("[site_tts] distribute-images выполнен после import mp3 с Drive.", flush=True)

    # post-check: expected output files exist and non-zero (failed/manual_skipped on Colab — не блокируют)
    resolution_final = _colab_expected_resolution(expected_set=expected_set, mp3_dir=source, job_dir=job_dir)
    terminal_non_mp3 = set(resolution_final["failed_terminal"]) | set(resolution_final["manual_skipped"])
    failed_local: list[str] = []
    for name in expected:
        if name in terminal_non_mp3:
            continue
        story, _v = _split_story_voice(Path(name).stem)
        story = _safe_name(story)
        out_mp3 = site_output_root / story / f"{story}.mp3"
        if not out_mp3.is_file() or out_mp3.stat().st_size <= 0:
            failed_local.append(story)
    if failed_local:
        print(
            "[WARN] partial TTS import: some expected non-terminal stories still have no local mp3; "
            f"ready stories continue. sample={failed_local[:20]}",
            flush=True,
        )

    _human_mirror_site_artifacts_after_import(site_output_root, list(expected))

    cleaned = {"texts_deleted": 0, "mp3_deleted": 0}
    if settings.google_drive_cleanup_after_success:
        texts = _drive_dir_from(root, settings, "texts", "texts")
        for name in expected:
            txt_name = Path(name).with_suffix(".txt").name
            p = texts / txt_name
            if p.is_file():
                try:
                    p.unlink()
                    cleaned["texts_deleted"] += 1
                except OSError:
                    pass
            p2 = source / name
            if p2.is_file():
                try:
                    p2.unlink()
                    cleaned["mp3_deleted"] += 1
                except OSError:
                    pass

    (job_dir / "LOCAL_STATUS.json").write_text(
        json.dumps(
            {
                "state": "imported_success",
                "expected_count": len(expected),
                "imported": int(imp.get("imported", 0) or 0),
                "colab_failed_terminal": len(resolution_final["failed_terminal"]),
                "colab_manual_skipped": len(resolution_final["manual_skipped"]),
                "completed_with_failed": bool(
                    (_read_colab_status(job_dir) or {}).get("completed_with_failed", False)
                ),
                "cleanup": cleaned,
                "updated_at": _utc_now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "status": last_status,
        "import": imp,
        "cleanup": cleaned,
        "colab_failed_terminal": resolution_final["failed_terminal"][:50],
        "colab_manual_skipped": resolution_final["manual_skipped"][:50],
    }


def _current_paths(root: Path) -> tuple[Path, Path, Path]:
    current = (root / _CURRENT_ROOT).resolve()
    texts = (current / _CURRENT_TEXTS).resolve()
    mp3 = (current / _CURRENT_MP3).resolve()
    return current, texts, mp3


def _clear_dir(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _latest_handoff_dir(root: Path) -> Path | None:
    base = (root / _HANDOFF_ROOT).resolve()
    if not base.is_dir():
        return None
    candidates = [p for p in base.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _resolve_handoff_dir(root: Path, handoff_dir: Path | None, latest: bool) -> Path | None:
    if handoff_dir is not None:
        return (handoff_dir if handoff_dir.is_absolute() else (root / handoff_dir)).resolve()
    if latest:
        return _latest_handoff_dir(root)
    return None


def _read_handoff_manifest(handoff: Path) -> dict[str, Any]:
    path = handoff / "internal_manifest.json"
    if not path.is_file():
        raise ValueError(f"internal_manifest.json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read handoff manifest: {path} ({exc})") from exc


def _create_handoff_package(root: Path, batch_root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    batch_id = str(manifest.get("batch_id", batch_root.name))
    total_items = int(manifest.get("total_items", 0) or 0)
    handoff_name = f"{batch_id}__site_tts__{total_items}_stories"
    handoff_dir = (root / _HANDOFF_ROOT / handoff_name).resolve()
    handoff_dir.mkdir(parents=True, exist_ok=False)

    index_csv = handoff_dir / "01_STORIES_INDEX.csv"
    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "human_number",
                "story_id",
                "story_title_or_folder",
                "voice",
                "chunks_count",
                "expected_result_filename",
                "expected_output_mp3",
                "status",
            ]
        )
        for i, item in enumerate(list(manifest.get("items", [])), start=1):
            exp = str(item.get("expected_result_mp3", "")).strip()
            w.writerow(
                [
                    f"{i:03d}",
                    item.get("story_id", ""),
                    item.get("story_folder", ""),
                    item.get("voice_type", ""),
                    item.get("chunks_count", ""),
                    Path(exp).name if exp else "",
                    item.get("expected_output_mp3", ""),
                    item.get("status", ""),
                ]
            )

    internal_manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "batch_dir": str(batch_root),
        "created_at": manifest.get("created_at", _utc_now_iso()),
        "items_count": total_items,
    }
    (handoff_dir / "internal_manifest.json").write_text(
        json.dumps(internal_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (handoff_dir / "00_README_START_HERE.txt").write_text(
        "\n".join(
            [
                "Kokoro Colab Site TTS handoff (START HERE)",
                "",
                "1) Upload 02_UPLOAD_THIS_TO_COLAB.zip to Colab and run generation there.",
                "2) Download produced MP3 files from Colab.",
                "3) Put resulting MP3 files into results_drop_here/ in this folder.",
                f"4) Run import:",
                f"   python -m orchestrator site-tts kokoro-colab import --handoff-dir \"{handoff_dir}\"",
                "5) Verify status:",
                f"   python -m orchestrator site-tts kokoro-colab verify --handoff-dir \"{handoff_dir}\"",
                "",
                "Colab runner in repository:",
                "- Content-Factory/colab/kokoro_colab_runner.py",
                "- Content-Factory/colab/README_KOKORO_COLAB.md",
                "",
                "Notes:",
                "- Keep MP3 names matching item_id (example: item_000001.mp3).",
                "- No local sync --execute is required for this flow.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (handoff_dir / _HANDOFF_RESULTS_DIR).mkdir(parents=True, exist_ok=True)

    upload_zip = handoff_dir / "02_UPLOAD_THIS_TO_COLAB.zip"
    with zipfile.ZipFile(upload_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in ("manifest.json", "README_COLAB.md"):
            p = batch_root / rel
            if p.is_file():
                zf.write(p, arcname=rel)
        for sub in ("stories", "chunks"):
            base = batch_root / sub
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(batch_root)).replace("\\", "/"))

    return {
        "handoff_dir": str(handoff_dir),
        "handoff_index_csv": str(index_csv),
        "handoff_upload_zip": str(upload_zip),
        "handoff_results_drop": str(handoff_dir / _HANDOFF_RESULTS_DIR),
        "handoff_internal_manifest": str(handoff_dir / "internal_manifest.json"),
    }


def _build_current_folder(root: Path, batch_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    current_dir, texts_dir, mp3_dir = _current_paths(root)
    _clear_dir(texts_dir)
    mp3_dir.mkdir(parents=True, exist_ok=True)

    items = list(manifest.get("items", []))
    index_csv = current_dir / "STORIES_INDEX.csv"
    mapping: list[dict[str, str]] = []
    collisions: set[str] = set()
    used_names: set[str] = set()
    rows: list[list[str]] = []

    for i, item in enumerate(items, start=1):
        story_id = str(item.get("story_id", "")).strip()
        voice = str(item.get("voice_type", "U")).strip().upper()[:1] or "U"
        source_rel = str(item.get("source_text_path", "")).strip()
        source_path = (root / source_rel).resolve() if source_rel else None
        base = _safe_name(story_id)
        txt_name = f"{base}__{voice}.txt"
        if txt_name in used_names:
            txt_name = f"{base}__{voice}__{i:03d}.txt"
            collisions.add(story_id)
        used_names.add(txt_name)
        mp3_name = Path(txt_name).with_suffix(".mp3").name
        out_rel = str(item.get("expected_output_mp3", "")).strip()

        if source_path is not None and source_path.is_file():
            (texts_dir / txt_name).write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            (texts_dir / txt_name).write_text("", encoding="utf-8")

        rows.append(
            [
                f"{i:03d}",
                story_id,
                txt_name,
                mp3_name,
                out_rel,
                voice,
                str(item.get("status", "pending")),
            ]
        )
        mapping.append(
            {
                "story_folder": story_id,
                "source_txt": txt_name,
                "expected_mp3": mp3_name,
                "expected_output_mp3": out_rel,
                "voice": voice,
            }
        )

    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["number", "story_folder", "source_txt", "expected_mp3", "final_output_path", "voice", "status"])
        w.writerows(rows)

    readme = current_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "COLAB_TTS_CURRENT quick flow",
                "",
                "1) Upload all TXT files from TEXTS_TO_COLAB/ into Colab.",
                "2) Generate MP3 with the same basenames.",
                "3) Put MP3 files into MP3_FROM_COLAB/.",
                "4) Run import:",
                "   python -m orchestrator site-tts kokoro-colab import --current",
                "5) Run verify:",
                "   python -m orchestrator site-tts kokoro-colab verify --current",
                "",
                "Important:",
                "- Keep filenames unchanged between TXT and MP3.",
                "- Local sync --execute is NOT required for this flow.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    py_cmd = "py -3"
    import_bat = current_dir / "IMPORT_MP3.bat"
    import_bat.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal EnableExtensions DisableDelayedExpansion",
                "cd /d \"%~dp0\\..\"",
                f"set \"PY_CMD={py_cmd}\"",
                "where py >nul 2>nul || set \"PY_CMD=python\"",
                "%PY_CMD% -m orchestrator site-tts kokoro-colab import --current",
                "pause",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    verify_bat = current_dir / "VERIFY_MP3.bat"
    verify_bat.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal EnableExtensions DisableDelayedExpansion",
                "cd /d \"%~dp0\\..\"",
                f"set \"PY_CMD={py_cmd}\"",
                "where py >nul 2>nul || set \"PY_CMD=python\"",
                "%PY_CMD% -m orchestrator site-tts kokoro-colab verify --current",
                "pause",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    internal = {
        "schema_version": 1,
        "batch_id": str(manifest.get("batch_id", batch_root.name)),
        "batch_dir": str(batch_root),
        "created_at": manifest.get("created_at", _utc_now_iso()),
        "items_count": len(mapping),
        "mapping": mapping,
    }
    (current_dir / "internal_manifest.json").write_text(json.dumps(internal, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "current_dir": str(current_dir),
        "texts_dir": str(texts_dir),
        "mp3_dir": str(mp3_dir),
        "index_csv": str(index_csv),
        "collisions": sorted(collisions),
        "items_count": len(mapping),
    }


def export_kokoro_colab_batch(
    root_dir: Path,
    *,
    limit: int | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    site_root = (root / "output" / "site").resolve()
    if not site_root.is_dir():
        return {"ok": False, "message": f"site output not found: {site_root}"}

    settings = load_site_tts_settings(root)
    chunk_max = int(settings.kokoro_chunk_max_chars)
    speed = float(settings.kokoro_speed)
    batch = (batch_id or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    batches_parent = (root / "runs" / "tts_colab_batches").resolve()
    batch_root = (batches_parent / batch).resolve()
    if batch_root.exists():
        return {"ok": False, "message": f"batch already exists: {batch_root}"}

    stories_dir = batch_root / "stories"
    chunks_root = batch_root / "chunks"
    results_dir = batch_root / "results"
    stories_dir.mkdir(parents=True, exist_ok=True)
    chunks_root.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    skipped: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    exported = 0
    lim = None if limit is None or int(limit) <= 0 else int(limit)

    for story_folder in _iter_story_dirs(site_root):
        src, err = _resolve_story_tts_source(story_folder)
        if src is None:
            skipped.append({"story_id": story_folder.name, "reason": err or "unknown"})
            continue
        if src.has_mp3:
            skipped.append({"story_id": src.story_id, "reason": "has_mp3"})
            continue
        text = src.tts_text_path.read_text(encoding="utf-8")
        chunks = pack_paragraph_chunks(text, chunk_max)
        if not chunks:
            skipped.append({"story_id": src.story_id, "reason": "empty_tts_text"})
            continue

        item_id = f"item_{exported + 1:06d}"
        voice = _pick_voice(settings, src.voice_type)
        lang = _lang_code(settings, voice)
        item_story_rel = Path("stories") / f"{item_id}.txt"
        item_chunks_rel = Path("chunks") / item_id
        item_story_path = batch_root / item_story_rel
        item_chunks_path = batch_root / item_chunks_rel
        item_story_path.parent.mkdir(parents=True, exist_ok=True)
        item_chunks_path.mkdir(parents=True, exist_ok=True)
        item_story_path.write_text(text, encoding="utf-8")
        for idx, ch in enumerate(chunks):
            (item_chunks_path / f"chunk_{idx:04d}.txt").write_text(ch, encoding="utf-8")

        items.append(
            {
                "item_id": item_id,
                "story_id": src.story_id,
                "story_folder": _rel_posix(src.story_folder, root),
                "source_text_path": _rel_posix(src.tts_text_path, root),
                "original_tts_filename": src.tts_text_path.name,
                "batch_text_path": str(item_story_rel).replace("\\", "/"),
                "chunks_dir": str(item_chunks_rel).replace("\\", "/"),
                "expected_result_mp3": f"results/{item_id}.mp3",
                "expected_output_mp3": _rel_posix(src.expected_output_mp3, root),
                "voice_type": src.voice_type,
                "kokoro_voice": voice,
                "kokoro_lang_code": lang,
                "speed": speed,
                "text_chars": len(text),
                "chunks_count": len(chunks),
                "hash_text_sha256": _sha256_text(text),
                "status": "pending",
            }
        )
        exported += 1
        if lim is not None and exported >= lim:
            break

    manifest = {
        "schema_version": 1,
        "batch_id": batch,
        "created_at": _utc_now_iso(),
        "source_root": "output/site",
        "total_items": len(items),
        "settings": {
            "kokoro_lang_code": (settings.kokoro_lang_code.strip().lower()[:1] if settings.kokoro_lang_code else ""),
            "speed": speed,
            "chunk_max_chars": chunk_max,
        },
        "items": items,
    }
    (batch_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    export_report = {
        "batch_id": batch,
        "created_at": manifest["created_at"],
        "site_root": _rel_posix(site_root, root),
        "exported": len(items),
        "skipped": skipped,
    }
    (batch_root / "export_report.json").write_text(
        json.dumps(export_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    readme_lines = [
        "# Kokoro Colab Batch",
        "",
        "Эта папка создана командой:",
        "- `python -m orchestrator site-tts kokoro-colab export --limit <N>`",
        "",
        "Human-launch + Google Drive: `python -m orchestrator site-tts --launch-name <L> kokoro-colab export` (экспорт на Drive, не эта zip-папка).",
        "",
        "Цель: сгенерировать MP3 в Colab (GPU) и импортировать их обратно в `output/site` без локального TTS.",
        "",
        "## Что внутри",
        "- `manifest.json` — список item-ов и куда вернуть итоговые mp3.",
        "- `stories/` — полный текст item-а (удобно для отладки).",
        "- `chunks/<item_id>/chunk_*.txt` — чанки в правильном порядке.",
        "- `results/` — сюда Colab должен сохранить `item_XXXXXX.mp3`.",
        "",
        "## Локальный импорт и проверка",
        "1. Положите `results/*.mp3` в этот batch-каталог локально.",
        "2. `python -m orchestrator site-tts kokoro-colab import --batch-id <batch_id>`",
        "",
        "Важно: этот flow НЕ запускает локальный `sync --execute`.",
    ]
    (batch_root / "README_COLAB.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    handoff = _create_handoff_package(root, batch_root, manifest)
    current = _build_current_folder(root, batch_root, manifest)
    return {
        "ok": True,
        "batch_id": batch,
        "batch_dir": str(batch_root),
        "exported": len(items),
        "skipped": len(skipped),
        "manifest_path": str(batch_root / "manifest.json"),
        **handoff,
        **current,
    }


def _resolve_batch_dir(root: Path, batch_id: str | None, batch_dir: Path | None, handoff: Path | None = None) -> Path:
    if handoff is not None:
        meta = _read_handoff_manifest(handoff)
        bdir_meta = str(meta.get("batch_dir", "")).strip()
        if bdir_meta:
            p = Path(bdir_meta).resolve()
            if p.is_dir():
                return p
        bid = str(meta.get("batch_id", "")).strip()
        if bid:
            return (root / "runs" / "tts_colab_batches" / bid).resolve()
        raise ValueError("handoff manifest does not contain batch_id/batch_dir")
    if batch_dir is not None:
        return (batch_dir if batch_dir.is_absolute() else (root / batch_dir)).resolve()
    bid = (batch_id or "").strip()
    if not bid:
        raise ValueError("either --batch-id or --batch-dir is required")
    return (root / "runs" / "tts_colab_batches" / bid).resolve()


def import_kokoro_colab_results(
    root_dir: Path,
    *,
    batch_id: str | None = None,
    batch_dir: Path | None = None,
    handoff_dir: Path | None = None,
    latest: bool = False,
    current: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    if current:
        current_dir, _texts_dir, mp3_dir = _current_paths(root)
        internal_path = current_dir / "internal_manifest.json"
        if not internal_path.is_file():
            return {"ok": False, "message": f"current manifest not found: {internal_path}"}
        try:
            meta = json.loads(internal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "message": f"failed to read {internal_path}: {exc}"}
        mapping = list(meta.get("mapping", []))
        imported = 0
        skipped_existing = 0
        missing_result = 0
        errors = 0
        details: list[dict[str, str]] = []
        for m in mapping:
            expected_mp3 = str(m.get("expected_mp3", "")).strip()
            out_rel = str(m.get("expected_output_mp3", "")).strip()
            if not expected_mp3 or not out_rel:
                errors += 1
                details.append({"status": "error", "reason": "mapping missing expected_mp3/output", "story": str(m.get("story_folder", ""))})
                continue
            src = (mp3_dir / expected_mp3).resolve()
            dst = (root / out_rel).resolve()
            if not src.is_file():
                missing_result += 1
                details.append({"status": "missing_result", "path": str(src), "story": str(m.get("story_folder", ""))})
                continue
            if dst.is_file() and not force:
                skipped_existing += 1
                details.append({"status": "skipped_existing", "path": str(dst), "story": str(m.get("story_folder", ""))})
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                imported += 1
                details.append({"status": "imported", "path": str(dst), "story": str(m.get("story_folder", ""))})
            except OSError as exc:
                errors += 1
                details.append({"status": "error", "reason": str(exc), "story": str(m.get("story_folder", ""))})
        return {
            "ok": True,
            "mode": "current",
            "current_dir": str(current_dir),
            "results_drop_dir": str(mp3_dir),
            "batch_dir": str(meta.get("batch_dir", "")),
            "imported": imported,
            "skipped_existing": skipped_existing,
            "missing_result": missing_result,
            "errors": errors,
            "force": bool(force),
            "details": details,
        }

    handoff = _resolve_handoff_dir(root, handoff_dir, latest)
    try:
        if handoff is not None:
            bdir = _resolve_batch_dir(root, batch_id, batch_dir, handoff=handoff)
        elif batch_dir is not None:
            bdir = (batch_dir if batch_dir.is_absolute() else (root / batch_dir)).resolve()
        else:
            bdir = _resolve_batch_dir(root, batch_id, None, handoff=None)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "message": f"manifest not found: {manifest_path}"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest.get("items", []))
    handoff_results = (handoff / _HANDOFF_RESULTS_DIR).resolve() if handoff is not None else None

    imported = 0
    skipped_existing = 0
    missing_result = 0
    errors = 0
    details: list[dict[str, str]] = []

    for item in items:
        item_id = str(item.get("item_id", "")).strip()
        result_rel = str(item.get("expected_result_mp3", "")).strip() or f"results/{item_id}.mp3"
        result_candidates = [(bdir / result_rel).resolve()]
        if handoff_results is not None:
            result_candidates.append((handoff_results / Path(result_rel).name).resolve())
            result_candidates.append((handoff_results / f"{item_id}.mp3").resolve())
        result_mp3 = next((p for p in result_candidates if p.is_file()), None)
        out_rel = str(item.get("expected_output_mp3", "")).strip()
        out_mp3 = (root / out_rel).resolve() if out_rel else None
        if out_mp3 is None:
            errors += 1
            item["status"] = "error"
            details.append({"item_id": item_id, "status": "error", "reason": "missing_expected_output_mp3"})
            continue
        if result_mp3 is None:
            missing_result += 1
            item["status"] = "missing_result"
            details.append(
                {
                    "item_id": item_id,
                    "status": "missing_result",
                    "searched": [str(p) for p in result_candidates],
                }
            )
            continue
        if out_mp3.is_file() and not force:
            skipped_existing += 1
            item["status"] = "skipped_existing"
            details.append({"item_id": item_id, "status": "skipped_existing", "path": str(out_mp3)})
            continue
        try:
            out_mp3.parent.mkdir(parents=True, exist_ok=True)
            out_mp3.write_bytes(result_mp3.read_bytes())
            imported += 1
            item["status"] = "imported"
            details.append({"item_id": item_id, "status": "imported", "path": str(out_mp3)})
        except OSError as exc:
            errors += 1
            item["status"] = "error"
            details.append({"item_id": item_id, "status": "error", "reason": str(exc)})

    manifest["items"] = items
    manifest["updated_at"] = _utc_now_iso()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "batch_id": manifest.get("batch_id", bdir.name),
        "updated_at": manifest["updated_at"],
        "imported": imported,
        "skipped_existing": skipped_existing,
        "missing_result": missing_result,
        "errors": errors,
        "force": bool(force),
        "details": details,
    }
    (bdir / "import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if hl is not None:
        try:
            bdir.resolve().relative_to(hl.resolve())
            _copy_colab_report_to_launch_sidecars(
                hl,
                str(manifest.get("batch_id", bdir.name)),
                bdir / "import_report.json",
                "kokoro_colab_import",
            )
        except (ValueError, OSError):
            pass
    out: dict[str, Any] = {"ok": True, "batch_dir": str(bdir), **report}
    if handoff is not None:
        out["handoff_dir"] = str(handoff)
        out["results_drop_dir"] = str(handoff_results) if handoff_results is not None else ""
    return out


def verify_mp3_coverage(
    root_dir: Path,
    *,
    batch_id: str | None = None,
    handoff_dir: Path | None = None,
    latest: bool = False,
    current: bool = False,
    human_launch: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    hl = human_launch.resolve() if human_launch is not None else None

    total_story_dirs = 0
    with_tts_text = 0
    with_mp3 = 0
    missing_mp3 = 0
    skipped_no_tts = 0
    ambiguous_tts = 0

    if hl is not None:
        site_root = hl
        for folder in iter_human_launch_story_dirs(hl):
            total_story_dirs += 1
            paths_h = SiteTtsPaths.from_human_launch_story(hl, folder.name, ensure_dirs=False)
            has_clean = paths_h.cleaned_story_txt.is_file()
            has_info = paths_h.info_txt.is_file()
            try:
                has_out = paths_h.output_mp3.is_file() and paths_h.output_mp3.stat().st_size > 0
            except OSError:
                has_out = False
            if not has_clean or not has_info:
                skipped_no_tts += 1
                if has_out:
                    with_mp3 += 1
                continue
            with_tts_text += 1
            if has_out:
                with_mp3 += 1
            else:
                missing_mp3 += 1
    else:
        site_root = (root / "output" / "site").resolve()
        for folder in _iter_story_dirs(site_root):
            total_story_dirs += 1
            src, err = _resolve_story_tts_source(folder)
            if src is None:
                if err and err.startswith("multiple_tts_text_files"):
                    ambiguous_tts += 1
                else:
                    skipped_no_tts += 1
                if (folder / f"{folder.name}.mp3").is_file():
                    with_mp3 += 1
                continue
            with_tts_text += 1
            if src.expected_output_mp3.is_file():
                with_mp3 += 1
            else:
                missing_mp3 += 1

    out: dict[str, Any] = {
        "ok": True,
        "source_root": str(site_root),
        "total_story_dirs": total_story_dirs,
        "with_tts_text_file": with_tts_text,
        "with_mp3": with_mp3,
        "missing_mp3": missing_mp3,
        "skipped_no_tts_file": skipped_no_tts,
        "ambiguous_tts_files": ambiguous_tts,
    }

    if current:
        current_dir, texts_dir, mp3_dir = _current_paths(root)
        internal_path = current_dir / "internal_manifest.json"
        if not internal_path.is_file():
            out["current"] = {"error": f"current manifest not found: {internal_path}"}
            return out
        try:
            meta = json.loads(internal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            out["current"] = {"error": f"failed to read {internal_path}: {exc}"}
            return out
        mapping = list(meta.get("mapping", []))
        txt_count = len([p for p in texts_dir.glob("*.txt") if p.is_file()])
        expected_mp3 = {str(m.get("expected_mp3", "")).strip() for m in mapping if str(m.get("expected_mp3", "")).strip()}
        found_mp3 = {p.name for p in mp3_dir.glob("*.mp3") if p.is_file()}
        can_import = 0
        missing: list[str] = []
        for m in mapping:
            name = str(m.get("expected_mp3", "")).strip()
            out_rel = str(m.get("expected_output_mp3", "")).strip()
            if not name:
                continue
            if name in found_mp3:
                can_import += 1
            else:
                missing.append(name)
        extra = sorted(found_mp3 - expected_mp3)
        out["current"] = {
            "current_dir": str(current_dir),
            "texts_exported": txt_count,
            "mapping_items": len(mapping),
            "mp3_found": len(found_mp3),
            "can_import": can_import,
            "missing_mp3": len(missing),
            "extra_mp3": len(extra),
            "first_missing": missing[:10],
            "first_extra": extra[:10],
            "batch_id": str(meta.get("batch_id", "")),
        }
        return out

    handoff = _resolve_handoff_dir(root, handoff_dir, latest)
    if (batch_id or "").strip() or handoff is not None:
        try:
            if handoff is not None:
                bdir = _resolve_batch_dir(root, batch_id, None, handoff=handoff)
            else:
                bdir = _resolve_batch_dir(root, batch_id, None, handoff=None)
        except ValueError as exc:
            out["batch"] = {"error": str(exc)}
            return out
        manifest_path = bdir / "manifest.json"
        handoff_results = (handoff / _HANDOFF_RESULTS_DIR).resolve() if handoff is not None else None
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            items = list(manifest.get("items", []))
            results_found_in_batch = 0
            results_found_in_handoff = 0
            already_imported = 0
            missing_item_ids: list[str] = []
            for it in items:
                item_id = str(it.get("item_id", "")).strip()
                rid = str(it.get("expected_result_mp3", "")).strip() or f"results/{item_id}.mp3"
                out_rel = str(it.get("expected_output_mp3", "")).strip()
                in_batch = bool(rid and (bdir / rid).is_file())
                in_handoff = bool(
                    handoff_results is not None
                    and (
                        (handoff_results / Path(rid).name).is_file()
                        or (handoff_results / f"{item_id}.mp3").is_file()
                    )
                )
                if in_batch:
                    results_found_in_batch += 1
                if in_handoff:
                    results_found_in_handoff += 1
                if not in_batch and not in_handoff:
                    missing_item_ids.append(item_id)
                if out_rel and (root / out_rel).is_file():
                    already_imported += 1
            results_found = max(results_found_in_batch, len(items) - len(missing_item_ids))
            waiting_mp3 = max(0, len(items) - already_imported)
            out["batch"] = {
                "batch_id": manifest.get("batch_id", bdir.name),
                "batch_dir": str(bdir),
                "handoff_dir": str(handoff) if handoff is not None else "",
                "exported_items": len(items),
                "results_found": results_found,
                "results_found_in_batch_results": results_found_in_batch,
                "results_found_in_handoff_drop": results_found_in_handoff,
                "already_imported": already_imported,
                "missing_results": max(0, len(items) - results_found),
                "waiting_mp3": waiting_mp3,
                "first_problems": missing_item_ids[:10],
            }
        else:
            out["batch"] = {"batch_id": str(batch_id), "error": f"manifest not found: {manifest_path}"}
    return out
