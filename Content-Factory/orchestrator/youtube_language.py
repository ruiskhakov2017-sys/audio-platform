"""Language contract checks for the English YouTube pipeline."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


EXPECTED_YOUTUBE_LANGUAGE = "en"


@dataclass
class YoutubeSafeStatusOptions:
    story_id: str
    expected_language: str = EXPECTED_YOUTUBE_LANGUAGE


@dataclass
class YoutubeSafeRegenerateOptions:
    story_id: str
    execute: bool = False
    expected_language: str = EXPECTED_YOUTUBE_LANGUAGE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def detect_text_language(text: str) -> str:
    """Tiny heuristic: lots of Cyrillic => ru; Latin with little Cyrillic => en."""
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text or "")
    if not letters:
        return "unknown"
    cyr = sum(1 for ch in letters if re.match(r"[А-Яа-яЁё]", ch))
    lat = sum(1 for ch in letters if re.match(r"[A-Za-z]", ch))
    total = max(1, cyr + lat)
    if cyr / total >= 0.15:
        return "ru"
    if lat > 0 and cyr / total < 0.05:
        return "en"
    return "mixed"


def detect_path_language(path: Path) -> str:
    return detect_text_language(_read_text(path)) if path.is_file() else "missing"


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    root = (config.root_dir / "output" / "youtube").resolve()
    direct = root / story_id
    if direct.is_dir():
        return direct.resolve()
    key = story_id.strip()
    matches: list[Path] = []
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            manifest = _read_json(child / "youtube_story_manifest.json")
            sid = str(manifest.get("story_id", "")).strip()
            canonical = str(manifest.get("canonical_basename", "")).strip()
            if key in {sid, canonical} or sid.casefold() == key.casefold() or canonical.casefold() == key.casefold():
                matches.append(child)
    if len(matches) == 1:
        return matches[0].resolve()
    return direct.resolve()


def _manifest_path(story_dir: Path) -> Path:
    return story_dir / "youtube_story_manifest.json"


def _source_path(story_dir: Path) -> Path:
    return story_dir / "00_source" / "source_cleaned_story.txt"


def _safe_path(story_dir: Path) -> Path:
    return story_dir / "02_safe_story" / "safe_story.txt"


def _promo_path(story_dir: Path) -> Path:
    return story_dir / "03_promo" / "text_ready_for_audio.txt"


def _audio_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def build_youtube_safe_status(
    *,
    config: OrchestratorConfig,
    story_id: str,
    expected_language: str = EXPECTED_YOUTUBE_LANGUAGE,
) -> dict[str, Any]:
    story_dir = _story_dir(config, story_id)
    manifest = _read_json(_manifest_path(story_dir))
    source = _source_path(story_dir)
    safe = _safe_path(story_dir)
    promo = _promo_path(story_dir)
    audio = _audio_path(story_dir)
    tts_manifest = manifest.get("tts_kokoro_colab") if isinstance(manifest.get("tts_kokoro_colab"), dict) else {}
    source_lang = detect_path_language(source)
    safe_lang = detect_path_language(safe)
    promo_lang = detect_path_language(promo)
    promo_manifest = manifest.get("promo") if isinstance(manifest.get("promo"), dict) else {}

    safe_status = "missing"
    if safe.is_file():
        safe_status = "done" if safe_lang == expected_language else "wrong_language"
    promo_status = "missing"
    if promo.is_file():
        if str(promo_manifest.get("status") or "") == "stale_or_missing":
            promo_status = "stale_or_missing"
        elif safe_status == "wrong_language":
            promo_status = "wrong_language"
        else:
            promo_status = "done" if promo_lang == expected_language else "wrong_language"
    tts_status = "missing"
    if audio.is_file():
        if promo_status == "done" and str(tts_manifest.get("status") or "") == "imported":
            tts_status = "done"
        elif safe_status == "done" and promo_status in {"missing", "stale_or_missing"}:
            tts_status = "stale"
        elif promo_status == "done":
            tts_status = "stale"
        else:
            tts_status = "stale_wrong_language"

    current_blocker = ""
    next_action = "language contract ok"
    if safe_status == "wrong_language":
        current_blocker = "youtube_safe_story_wrong_language"
        next_action = "regenerate English safe story from 00_source/source_cleaned_story.txt"
    elif promo_status == "wrong_language":
        current_blocker = "youtube_promo_wrong_language"
        next_action = "rerun promo after English safe story exists"
    elif promo_status in {"missing", "stale_or_missing"} and safe_status == "done":
        current_blocker = "youtube_promo_missing_or_audio_stale"
        next_action = "run promo-run, then rerun YouTube TTS"
    elif tts_status == "stale":
        current_blocker = "youtube_audio_stale_after_promo_change"
        next_action = "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt, then import narration.mp3"
    elif tts_status == "stale_wrong_language":
        current_blocker = "youtube_audio_stale_after_language_change"
        next_action = "rerun YouTube TTS from updated English 03_promo/text_ready_for_audio.txt"

    return {
        "ok": not current_blocker and safe_status == "done",
        "story_id": str(manifest.get("story_id") or story_id),
        "canonical_basename": str(manifest.get("canonical_basename") or story_dir.name),
        "story_dir": str(story_dir),
        "expected_language": expected_language,
        "source_path": str(source),
        "source_exists": source.is_file(),
        "source_language": source_lang,
        "safe_story_path": str(safe),
        "safe_story_exists": safe.is_file(),
        "safe_story_language": safe_lang,
        "safe_story_status": safe_status,
        "promo_path": str(promo),
        "promo_exists": promo.is_file(),
        "promo_language": promo_lang,
        "promo_status": promo_status,
        "text_ready_for_audio_language": promo_lang,
        "audio_path": str(audio),
        "audio_exists": audio.is_file(),
        "tts_status": tts_status,
        "current_blocker": current_blocker,
        "next_action": next_action,
        "story_manifest": str(_manifest_path(story_dir)),
    }


def run_youtube_safe_status(
    *,
    config: OrchestratorConfig,
    options: YoutubeSafeStatusOptions,
) -> dict[str, Any]:
    return build_youtube_safe_status(
        config=config,
        story_id=str(options.story_id).strip(),
        expected_language=str(options.expected_language or EXPECTED_YOUTUBE_LANGUAGE).strip() or EXPECTED_YOUTUBE_LANGUAGE,
    )


def mark_wrong_language_manifest(config: OrchestratorConfig, story_id: str, expected_language: str = EXPECTED_YOUTUBE_LANGUAGE) -> Path:
    status = build_youtube_safe_status(config=config, story_id=story_id, expected_language=expected_language)
    manifest_path = Path(str(status["story_manifest"]))
    manifest = _read_json(manifest_path)
    now = _now_iso()
    manifest["safe_story"] = {
        "status": status["safe_story_status"],
        "path": status["safe_story_path"],
        "expected_language": expected_language,
        "detected_language": status["safe_story_language"],
        "updated_at": now,
    }
    if status["safe_story_status"] == "wrong_language":
        manifest["promo"] = {
            **(manifest.get("promo") if isinstance(manifest.get("promo"), dict) else {}),
            "status": "wrong_language",
            "reason": "safe_story_wrong_language",
            "expected_language": expected_language,
            "detected_language": status["promo_language"],
            "updated_at": now,
        }
        tts = dict(manifest.get("tts_kokoro_colab") or {})
        tts.update({"status": "stale", "reason": "source_language_wrong / promo_language_wrong", "updated_at": now})
        manifest["tts_kokoro_colab"] = tts
        visuals = dict(manifest.get("visuals") or {})
        visuals.update({"status": "blocked", "reason": "youtube_safe_story_wrong_language", "updated_at": now})
        manifest["visuals"] = visuals
        status_block = dict(manifest.get("status") or {})
        status_block.update({"safe_done": False, "promo_done": False, "audio_done": False, "frames_done": False, "video_done": False})
        manifest["status"] = status_block
        stage = dict(manifest.get("pipeline_stage_status") or {})
        stage.update({"safe_story": "wrong_language", "promo": "wrong_language", "tts_kokoro_colab": "stale", "audio": "stale", "visuals": "blocked"})
        manifest["pipeline_stage_status"] = stage
    manifest["updated_at"] = now
    _write_json(manifest_path, manifest)
    return manifest_path


def run_youtube_safe_regenerate(
    *,
    config: OrchestratorConfig,
    options: YoutubeSafeRegenerateOptions,
) -> dict[str, Any]:
    from orchestrator.youtube_safe_english_bridge import YoutubeSafeEnglishRunOptions, run_youtube_safe_english_run

    return run_youtube_safe_english_run(
        config=config,
        options=YoutubeSafeEnglishRunOptions(
            story_id=str(options.story_id).strip(),
            execute=bool(options.execute),
            force=True,
        ),
    )
