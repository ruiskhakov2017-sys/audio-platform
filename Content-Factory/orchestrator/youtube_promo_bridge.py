"""Bridge for legacy YouTube promo insertion.

The production insertion logic lives in legacy/youtube_tts/promo_inserter.py.
This module keeps the new output/youtube/<story> contract and invokes the
legacy script against an isolated single-story staging directory.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.phase_a import _load_gemini_registry
from orchestrator.youtube_language import EXPECTED_YOUTUBE_LANGUAGE, build_youtube_safe_status, detect_text_language


SOURCE_FILE_NAME = "text.txt"
READY_FILE_NAME = "text_ready_for_audio.txt"
SNIPPET_FILE_NAME = "climax_snippet.txt"
PROMO_REPORT_NAME = "promo_report.json"
MARKERS = (
    "[YT_PROMO_INTRO_START]",
    "[YT_PROMO_INTRO_END]",
    "[YT_PROMO_MID_START]",
    "[YT_PROMO_MID_END]",
    "[YT_PROMO_OUTRO_START]",
    "[YT_PROMO_OUTRO_END]",
)
_GEMINI_URL_RE = re.compile(
    r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$",
    re.IGNORECASE,
)
_PROMO_BOT_KEY = "youtube_ad_point"
ENGLISH_PROMO_PLACEHOLDERS = {
    "intro_text": (
        "Welcome to Secrets Unlocked. If you enjoy intimate confessions and forbidden stories, "
        "check the private archive linked in the description. Subscribe, settle in, and enjoy the story.\n\n"
    ),
    "mid_text": (
        "\n\nFeeling the tension build? Thousands of more private stories are waiting in my exclusive archive. "
        "The link is in the channel description.\n\n"
    ),
    "outro_text": (
        "\n\nI hope you enjoyed this story. More intimate confessions are waiting in the private archive linked "
        "in the description. Subscribe for the next story."
    ),
}


@dataclass
class YoutubePromoRunOptions:
    story_id: str
    execute: bool = False
    force: bool = False
    fresh_gemini_session: bool = True
    account_index: int = 0
    gemini_registry_path: Path | None = None


@dataclass
class YoutubePromoStatusOptions:
    story_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _legacy_dir(config: OrchestratorConfig) -> Path:
    return (config.root_dir / "legacy" / "youtube_tts").resolve()


def _legacy_script(config: OrchestratorConfig) -> Path:
    return _legacy_dir(config) / "promo_inserter.py"


def _story_manifest_path(story_dir: Path) -> Path:
    return story_dir / "youtube_story_manifest.json"


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    root = (config.root_dir / "output" / "youtube").resolve()
    direct = root / story_id
    if direct.is_dir():
        return direct.resolve()

    key = story_id.strip()
    matches: list[Path] = []
    if root.is_dir():
        for child in root.iterdir():
            manifest = _load_json(child / "youtube_story_manifest.json")
            if not manifest:
                continue
            sid = str(manifest.get("story_id", "")).strip()
            canonical = str(manifest.get("canonical_basename", "")).strip()
            if key in {sid, canonical} or canonical.casefold() == key.casefold() or sid.casefold() == key.casefold():
                matches.append(child)
    if len(matches) == 1:
        return matches[0].resolve()
    return direct.resolve()


def _safe_story_path(story_dir: Path) -> Path:
    return story_dir / "02_safe_story" / "safe_story.txt"


def _promo_dir(story_dir: Path) -> Path:
    return story_dir / "03_promo"


def _fallback_source_path(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / "source_for_promo.txt"


def _promo_output_path(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / READY_FILE_NAME


def _climax_snippet_path(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / SNIPPET_FILE_NAME


def _promo_report_path(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / PROMO_REPORT_NAME


def _narration_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def _legacy_staging_dir(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / "_legacy_staging"


def _fresh_user_data_dir(story_dir: Path) -> Path:
    return _legacy_staging_dir(story_dir) / "user_data_fresh"


def _raw_dir(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / "_raw"


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9А-Яа-яЁё._-]+", "_", value.strip()).strip("._-")
    return stem or "youtube_story"


def _extract_legacy_constant(script: Path, name: str, default: str = "") -> str:
    if not script.is_file():
        return default
    try:
        tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        value = ast.literal_eval(node.value)
                    except Exception:
                        return default
                    return value if isinstance(value, str) else default
    return default


def _legacy_contract(config: OrchestratorConfig) -> dict[str, Any]:
    script = _legacy_script(config)
    return {
        "script_path": str(script),
        "entrypoint": "python promo_inserter.py",
        "start_bat": str(_legacy_dir(config) / "start_promo.bat"),
        "expected_input_root": str(_legacy_dir(config) / "promo_stories"),
        "expected_input_file": SOURCE_FILE_NAME,
        "ready_file_name": READY_FILE_NAME,
        "climax_snippet_file_name": SNIPPET_FILE_NAME,
        "gemini_url": _extract_legacy_constant(script, "DEFAULT_GEMINI_URL"),
        "climax_prompt": _extract_legacy_constant(script, "CLIMAX_PROMPT"),
        "intro_text": _extract_legacy_constant(script, "INTRO_TEXT"),
        "mid_text": _extract_legacy_constant(script, "MID_INSERT_TEXT"),
        "outro_text": _extract_legacy_constant(script, "OUTRO_TEXT"),
        "climax_method": "legacy_gemini_anchor_snippet",
    }


def _effective_contract(config: OrchestratorConfig) -> dict[str, Any]:
    contract = _legacy_contract(config)
    languages = {
        "intro": detect_text_language(str(contract.get("intro_text") or "")),
        "mid": detect_text_language(str(contract.get("mid_text") or "")),
        "outro": detect_text_language(str(contract.get("outro_text") or "")),
    }
    if any(lang not in {EXPECTED_YOUTUBE_LANGUAGE, "unknown"} for lang in languages.values()):
        contract = {**contract, **ENGLISH_PROMO_PLACEHOLDERS}
        contract["placeholder_ads_used"] = True
        contract["legacy_promo_blocks_language"] = languages
        contract["promo_blocks_source"] = "english_placeholders"
    else:
        contract["placeholder_ads_used"] = False
        contract["legacy_promo_blocks_language"] = languages
        contract["promo_blocks_source"] = "legacy"
    return contract


def _registry_candidates(config: OrchestratorConfig, explicit: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit if explicit.is_absolute() else (config.root_dir / explicit).resolve())
    candidates.extend(
        [
            (config.root_dir / "configs" / "gemini_bots_registry.yaml").resolve(),
            (config.root_dir / "configs" / "gemini_bots_registry.example.yaml").resolve(),
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _pick_promo_bot(config: OrchestratorConfig, account_index: int, explicit_registry: Path | None = None) -> dict[str, str]:
    registry_path = ""
    bots: list[dict[str, Any]] = []
    for cand in _registry_candidates(config, explicit_registry):
        if cand.is_file():
            loaded = _load_gemini_registry(cand)
            if loaded:
                registry_path = str(cand)
                bots = loaded
                break
    valid: list[tuple[str, str]] = []
    for bot in bots:
        if not isinstance(bot, dict):
            continue
        url = str(bot.get(_PROMO_BOT_KEY, "")).strip()
        if url and _GEMINI_URL_RE.fullmatch(url):
            valid.append((str(bot.get("email", "")).strip(), url))
    if not valid:
        contract = _legacy_contract(config)
        return {
            "email": "",
            "url": str(contract.get("gemini_url") or ""),
            "registry_path": registry_path,
            "bot_key": "legacy_DEFAULT_GEMINI_URL",
            "account_index": str(account_index),
            "source": "legacy_fallback",
        }
    idx = max(0, min(int(account_index or 0), len(valid) - 1))
    email, url = valid[idx]
    return {
        "email": email,
        "url": url,
        "registry_path": registry_path,
        "bot_key": _PROMO_BOT_KEY,
        "account_index": str(idx),
        "source": "gemini_registry",
    }


def _source_for_promo(story_dir: Path) -> Path:
    safe = _safe_story_path(story_dir)
    if safe.is_file():
        return safe
    return _fallback_source_path(story_dir)


def _contains_legacy_promo(text: str, contract: dict[str, Any]) -> dict[str, bool]:
    intro = str(contract.get("intro_text") or "")
    mid = str(contract.get("mid_text") or "")
    outro = str(contract.get("outro_text") or "")
    return {
        "intro_inserted": bool(intro and text.startswith(intro)),
        "mid_inserted": bool(mid and mid.strip() and mid.strip() in text),
        "outro_inserted": bool(outro and text.rstrip().endswith(outro.strip())),
    }


def _has_markers(text: str) -> bool:
    return any(marker in text for marker in MARKERS)


def _audio_status(story_dir: Path, output_hash: str, manifest: dict[str, Any], promo_done: bool) -> dict[str, Any]:
    audio = _narration_path(story_dir)
    tts = manifest.get("tts_kokoro_colab") if isinstance(manifest.get("tts_kokoro_colab"), dict) else {}
    tts_hash = str(tts.get("text_ready_for_audio_hash") or "")
    tts_status = str(tts.get("status") or "")
    if not audio.is_file():
        return {
            "status": "missing",
            "path": str(audio),
            "exists": False,
            "stale": False,
            "text_ready_for_audio_hash": tts_hash,
            "current_text_ready_for_audio_hash": output_hash,
        }
    stale = (not promo_done) or tts_status != "imported" or (bool(output_hash) and tts_hash != output_hash)
    return {
        "status": "stale" if stale else "done",
        "path": str(audio),
        "exists": True,
        "stale": stale,
        "text_ready_for_audio_hash": tts_hash,
        "current_text_ready_for_audio_hash": output_hash,
        "reason": "youtube_audio_stale_after_promo_change" if stale else "",
    }


def _build_status(config: OrchestratorConfig, story_id: str) -> dict[str, Any]:
    story_dir = _story_dir(config, story_id)
    manifest = _load_json(_story_manifest_path(story_dir))
    contract = _effective_contract(config)
    source = _source_for_promo(story_dir)
    safe = _safe_story_path(story_dir)
    ready = _promo_output_path(story_dir)
    snippet = _climax_snippet_path(story_dir)
    report = _load_json(_promo_report_path(story_dir))

    source_hash = _sha256_file(source)
    output_hash = _sha256_file(ready)
    ready_text = _read_text(ready)
    safe_text = _read_text(safe)
    source_text = _read_text(source)
    language_status = build_youtube_safe_status(config=config, story_id=story_id, expected_language=EXPECTED_YOUTUBE_LANGUAGE)
    source_language = detect_text_language(source_text)
    output_language = detect_text_language(ready_text) if ready.is_file() else "missing"
    promo_block_languages = {
        "intro": detect_text_language(str(contract.get("intro_text") or "")),
        "mid": detect_text_language(str(contract.get("mid_text") or "")),
        "outro": detect_text_language(str(contract.get("outro_text") or "")),
    }
    wrong_promo_blocks_language = any(lang not in {EXPECTED_YOUTUBE_LANGUAGE, "unknown"} for lang in promo_block_languages.values())
    wrong_input_language = source.is_file() and source_language != EXPECTED_YOUTUBE_LANGUAGE
    wrong_output_language = ready.is_file() and output_language != EXPECTED_YOUTUBE_LANGUAGE
    inserted = _contains_legacy_promo(ready_text, contract)
    promo_done = bool(
        ready.is_file()
        and all(inserted.values())
        and not wrong_input_language
        and not wrong_output_language
        and not wrong_promo_blocks_language
    )
    source_hash_matches_report = bool(source_hash and source_hash == str(report.get("source_hash") or ""))
    stale_output = bool(ready.is_file() and report and not source_hash_matches_report)

    if not story_dir.is_dir():
        status = "missing_story_dir"
    elif not source.is_file():
        status = "missing_source"
    elif wrong_input_language or wrong_output_language or wrong_promo_blocks_language:
        status = "wrong_language"
    elif not ready.is_file():
        status = "missing"
    elif stale_output:
        status = "stale"
    elif promo_done:
        status = "done"
    else:
        status = "invalid_no_complete_promo"

    audio = _audio_status(story_dir, output_hash, manifest, promo_done and not stale_output)
    current_blocker = ""
    next_action = "promo is done"
    if status in {"missing_story_dir", "missing_source"}:
        current_blocker = status
        next_action = "create/import 02_safe_story/safe_story.txt first"
    elif status == "wrong_language":
        current_blocker = "youtube_safe_story_wrong_language" if wrong_input_language else "youtube_promo_wrong_language"
        next_action = "run youtube safe-regenerate" if wrong_input_language else "make promo ad blocks English, then rerun promo-run"
    elif status != "done":
        current_blocker = "promo_not_done"
        next_action = "run python -m orchestrator youtube promo-run --story-id \"{}\" --execute".format(story_id)
    elif audio.get("stale"):
        current_blocker = "youtube_audio_stale_after_promo_change"
        next_action = "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt"

    return {
        "ok": status == "done" and not audio.get("stale"),
        "status": status,
        "story_id": str(manifest.get("story_id") or story_id),
        "canonical_basename": str(manifest.get("canonical_basename") or story_dir.name),
        "story_dir": str(story_dir),
        "source_path": str(source),
        "source_exists": source.is_file(),
        "safe_story_path": str(safe),
        "safe_story_exists": safe.is_file(),
        "fallback_source_path": str(_fallback_source_path(story_dir)),
        "fallback_source_exists": _fallback_source_path(story_dir).is_file(),
        "output_path": str(ready),
        "output_exists": ready.is_file(),
        "source_hash": source_hash,
        "output_hash": output_hash,
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "source_language": source_language,
        "output_language": output_language,
        "promo_blocks_language": promo_block_languages,
        "promo_blocks_wrong_language": wrong_promo_blocks_language,
        "language_status": language_status,
        "output_equals_safe_story": bool(ready.is_file() and safe.is_file() and ready_text == safe_text),
        "output_equals_source": bool(ready.is_file() and source.is_file() and ready_text == source_text),
        "has_promo_markers": _has_markers(ready_text),
        "has_legacy_promo_text": any(inserted.values()),
        "intro_inserted": inserted["intro_inserted"],
        "mid_inserted": inserted["mid_inserted"],
        "outro_inserted": inserted["outro_inserted"],
        "climax_method": str(report.get("climax_method") or contract.get("climax_method") or ""),
        "climax_snippet_path": str(snippet),
        "climax_snippet_exists": snippet.is_file(),
        "climax_snippet_chars": len(_read_text(snippet).strip()),
        "placeholder_ads_used": bool(contract.get("placeholder_ads_used")),
        "legacy_promo_blocks_language": contract.get("legacy_promo_blocks_language", {}),
        "promo_blocks_source": contract.get("promo_blocks_source", "legacy"),
        "fallback_used": False,
        "source_hash_matches_report": source_hash_matches_report,
        "stale_output": stale_output,
        "audio": audio,
        "current_blocker": current_blocker,
        "next_action": next_action,
        "report_path": str(_promo_report_path(story_dir)),
        "legacy": {
            k: v
            for k, v in contract.items()
            if k not in {"intro_text", "mid_text", "outro_text", "climax_prompt"}
        },
    }


def run_youtube_promo_status(
    *,
    config: OrchestratorConfig,
    options: YoutubePromoStatusOptions,
) -> dict[str, Any]:
    return _build_status(config, str(options.story_id).strip())


def _write_legacy_runner(
    path: Path,
    *,
    legacy_dir: Path,
    promo_stories_dir: Path,
    user_data_dir: Path,
    log_path: Path,
    fresh_gemini_session: bool,
    promo_texts: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                f"legacy_dir = Path({str(legacy_dir)!r})",
                "sys.path.insert(0, str(legacy_dir))",
                "import promo_inserter as p",
                f"p.PROMO_STORIES_DIR = Path({str(promo_stories_dir)!r})",
                f"p.USER_DATA_DIR = Path({str(user_data_dir)!r})",
                f"p.LOG_FILE_PATH = Path({str(log_path)!r})",
                f"p.INTRO_TEXT = {str(promo_texts.get('intro_text') or '')!r}",
                f"p.MID_INSERT_TEXT = {str(promo_texts.get('mid_text') or '')!r}",
                f"p.OUTRO_TEXT = {str(promo_texts.get('outro_text') or '')!r}",
                "p.CLIMAX_PROMPT = (",
                "    'Read the attached story file. Find a strong climax/tension point for a mid-roll ad. '",
                "    'Return ONLY 1-2 complete consecutive sentences copied EXACTLY from the attached story text. '",
                "    'The answer must be an exact English substring from the story file. '",
                "    'Do not translate. Do not paraphrase. Do not add quotes, labels, comments, email addresses, or explanations. '",
                "    'If unsure, choose any memorable English sentence from the later half of the story and copy it verbatim.'",
                ")",
                "p.CLIMAX_FILE_SUFFIX = (",
                "    '\\n\\nThe full story is attached as a .txt file. Use the attachment, not chat history. '",
                "    'Your response will be rejected unless it appears verbatim in that file.'",
                ")",
                "p.setup_dual_logging()",
                f"print('[INFO] fresh_gemini_session={str(bool(fresh_gemini_session)).lower()}')",
                "print(f'[INFO] isolated_user_data_dir: {p.USER_DATA_DIR}')",
                "print(f'[INFO] effective_gemini_url: {__import__(\"os\").getenv(\"GEMINI_URL\", p.DEFAULT_GEMINI_URL)}')",
                "raise SystemExit(p.main())",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _patch_manifest_after_promo(
    *,
    story_dir: Path,
    promo_result: dict[str, Any],
    audio_stale: bool,
) -> Path:
    manifest_path = _story_manifest_path(story_dir)
    manifest = _load_json(manifest_path)
    now = _now_iso()
    output_path = Path(str(promo_result["output_path"]))
    snippet_path = Path(str(promo_result["climax_snippet_path"]))

    manifest["promo"] = {
        "status": promo_result["status"],
        "source_path": str(promo_result["source_path"]),
        "output_path": str(output_path),
        "source_hash": str(promo_result["source_hash"]),
        "output_hash": str(promo_result["output_hash"]),
        "intro_inserted": bool(promo_result["intro_inserted"]),
        "mid_inserted": bool(promo_result["mid_inserted"]),
        "outro_inserted": bool(promo_result["outro_inserted"]),
        "climax_method": str(promo_result["climax_method"]),
        "climax_snippet_path": str(snippet_path),
        "placeholder_ads_used": False,
        "updated_at": now,
    }
    manifest["text_ready_for_audio"] = {
        "status": "done" if promo_result["status"] == "done" else "blocked",
        "path": str(output_path),
        "source": "02_safe_story/safe_story.txt",
        "source_hash": str(promo_result["source_hash"]),
        "output_hash": str(promo_result["output_hash"]),
        "updated_at": now,
    }
    status = dict(manifest.get("pipeline_stage_status") or {})
    status["promo"] = promo_result["status"]
    status["text_ready_for_audio"] = "done" if promo_result["status"] == "done" else "blocked"
    manifest["pipeline_stage_status"] = status
    actual = dict(manifest.get("actual_artifacts") or {})
    actual["text_ready_for_audio"] = str(output_path)
    actual["climax_snippet"] = str(snippet_path)
    manifest["actual_artifacts"] = actual

    tts = dict(manifest.get("tts_kokoro_colab") or {})
    tts["audio_path"] = str(_narration_path(story_dir))
    tts["current_text_ready_for_audio_hash"] = str(promo_result["output_hash"])
    if audio_stale:
        tts["status"] = "stale"
        tts["stale_reason"] = "youtube_audio_stale_after_promo_change"
        status["tts_kokoro_colab"] = "stale"
        status["audio"] = "stale"
        audio = dict(manifest.get("audio") or {})
        audio["status"] = "stale"
        audio["stale_reason"] = "youtube_audio_stale_after_promo_change"
        manifest["audio"] = audio
    manifest["tts_kokoro_colab"] = tts
    manifest["updated_at"] = now
    _write_json(manifest_path, manifest)
    return manifest_path


def run_youtube_promo_run(
    *,
    config: OrchestratorConfig,
    options: YoutubePromoRunOptions,
) -> dict[str, Any]:
    story_key = str(options.story_id).strip()
    status = _build_status(config, story_key)
    story_dir = Path(str(status["story_dir"]))
    source = Path(str(status["source_path"]))
    ready = _promo_output_path(story_dir)
    snippet = _climax_snippet_path(story_dir)
    staging = _legacy_staging_dir(story_dir)
    raw = _raw_dir(story_dir)
    legacy_story_dir = staging / "promo_stories" / _safe_stem(str(status.get("canonical_basename") or story_key))
    legacy_ready = legacy_story_dir / READY_FILE_NAME
    legacy_snippet = legacy_story_dir / SNIPPET_FILE_NAME
    legacy_log = staging / "promo_inserter.log"
    runner = staging / "run_legacy_promo_inserter.py"
    user_data_dir = _fresh_user_data_dir(story_dir) if options.fresh_gemini_session else _legacy_dir(config) / "user_data"
    promo_bot = _pick_promo_bot(config, options.account_index, options.gemini_registry_path)
    contract = _effective_contract(config)

    plan = {
        **status,
        "execute": bool(options.execute),
        "force": bool(options.force),
        "fresh_gemini_session": bool(options.fresh_gemini_session),
        "user_data_dir": str(user_data_dir),
        "gemini_account_email": promo_bot.get("email", ""),
        "gemini_url": promo_bot.get("url", ""),
        "gemini_registry_path": promo_bot.get("registry_path", ""),
        "gemini_bot_key": promo_bot.get("bot_key", ""),
        "gemini_account_index": promo_bot.get("account_index", str(options.account_index)),
        "gemini_bot_source": promo_bot.get("source", ""),
        "legacy_staging_dir": str(staging),
        "legacy_story_dir": str(legacy_story_dir),
        "legacy_log_path": str(legacy_log),
        "runner_path": str(runner),
        "changed_files": [],
    }

    if status["status"] in {"missing_story_dir", "missing_source"}:
        return {"ok": False, "status": status["status"], "message": "Missing promo source", **plan}

    if status["status"] == "wrong_language" and status.get("source_language") != EXPECTED_YOUTUBE_LANGUAGE:
        return {
            **plan,
            "ok": False,
            "status": "blocked_wrong_language",
            "message": "promo-run blocked: safe_story/text_ready_for_audio must be English for YouTube pipeline",
        }

    if status["status"] == "done" and not options.force:
        return {**plan, "ok": True, "status": "done", "message": "promo already inserted"}

    if not options.execute:
        return {
            **plan,
            "ok": True,
            "status": "would_run",
            "message": "dry-run only; no files written and legacy Gemini was not launched",
        }

    promo_dir = _promo_dir(story_dir)
    promo_dir.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    legacy_story_dir.mkdir(parents=True, exist_ok=True)
    for stale_generated in (
        legacy_story_dir / READY_FILE_NAME,
        legacy_story_dir / SNIPPET_FILE_NAME,
        legacy_story_dir / "climax_snippet.tmp",
    ):
        if stale_generated.exists():
            stale_generated.unlink()
    shutil.copy2(source, legacy_story_dir / SOURCE_FILE_NAME)
    shutil.copy2(source, raw / SOURCE_FILE_NAME)
    _write_legacy_runner(
        runner,
        legacy_dir=_legacy_dir(config),
        promo_stories_dir=staging / "promo_stories",
        user_data_dir=user_data_dir,
        log_path=legacy_log,
        fresh_gemini_session=bool(options.fresh_gemini_session),
        promo_texts={
            "intro_text": str(contract.get("intro_text") or ""),
            "mid_text": str(contract.get("mid_text") or ""),
            "outro_text": str(contract.get("outro_text") or ""),
        },
    )

    env = os.environ.copy()
    if promo_bot.get("url"):
        env["GEMINI_URL"] = str(promo_bot["url"])
    env["PROMO_FRESH_GEMINI_SESSION"] = "1" if options.fresh_gemini_session else "0"
    env["PROMO_USER_DATA_DIR"] = str(user_data_dir)

    proc = subprocess.run(
        [sys.executable, str(runner)],
        cwd=str(_legacy_dir(config)),
        env=env,
        text=True,
    )
    changed_files = [
        str(legacy_story_dir / SOURCE_FILE_NAME),
        str(raw / SOURCE_FILE_NAME),
        str(runner),
        str(legacy_log),
    ]

    if proc.returncode != 0:
        report = {
            **plan,
            "ok": False,
            "status": "failed",
            "message": "legacy promo_inserter failed",
            "returncode": proc.returncode,
            "changed_files": changed_files,
            "updated_at": _now_iso(),
        }
        _write_json(_promo_report_path(story_dir), report)
        report["changed_files"].append(str(_promo_report_path(story_dir)))
        return report

    if not legacy_ready.is_file():
        report = {
            **plan,
            "ok": False,
            "status": "failed",
            "message": "legacy promo_inserter did not produce text_ready_for_audio.txt",
            "returncode": proc.returncode,
            "changed_files": changed_files,
            "updated_at": _now_iso(),
        }
        _write_json(_promo_report_path(story_dir), report)
        report["changed_files"].append(str(_promo_report_path(story_dir)))
        return report

    shutil.copy2(legacy_ready, ready)
    changed_files.append(str(ready))
    if legacy_snippet.is_file():
        shutil.copy2(legacy_snippet, snippet)
        changed_files.append(str(snippet))

    final_status = _build_status(config, story_key)
    final_status["status"] = (
        "done"
        if final_status.get("status") != "wrong_language"
        and all(bool(final_status.get(k)) for k in ("intro_inserted", "mid_inserted", "outro_inserted"))
        else str(final_status.get("status") or "failed_incomplete_promo")
    )
    audio_stale = bool(_narration_path(story_dir).is_file())
    report = {
        **final_status,
        "ok": final_status["status"] == "done",
        "execute": True,
        "force": bool(options.force),
        "fresh_gemini_session": bool(options.fresh_gemini_session),
        "user_data_dir": str(user_data_dir),
        "gemini_account_email": promo_bot.get("email", ""),
        "gemini_url": promo_bot.get("url", ""),
        "gemini_registry_path": promo_bot.get("registry_path", ""),
        "gemini_bot_key": promo_bot.get("bot_key", ""),
        "gemini_account_index": promo_bot.get("account_index", str(options.account_index)),
        "gemini_bot_source": promo_bot.get("source", ""),
        "returncode": proc.returncode,
        "legacy_staging_dir": str(staging),
        "legacy_story_dir": str(legacy_story_dir),
        "legacy_log_path": str(legacy_log),
        "changed_files": changed_files,
        "updated_at": _now_iso(),
    }
    _write_json(_promo_report_path(story_dir), report)
    report["changed_files"].append(str(_promo_report_path(story_dir)))
    manifest_path = _patch_manifest_after_promo(story_dir=story_dir, promo_result=report, audio_stale=audio_stale)
    report["manifest_path"] = str(manifest_path)
    report["changed_files"].append(str(manifest_path))
    if audio_stale:
        report["audio"]["status"] = "stale"
        report["audio"]["stale"] = True
        report["audio"]["reason"] = "youtube_audio_stale_after_promo_change"
        report["current_blocker"] = "youtube_audio_stale_after_promo_change"
        report["next_action"] = "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt"
    return report
