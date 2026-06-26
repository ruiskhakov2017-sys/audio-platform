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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.account_capabilities import assert_gemini_capable
from orchestrator.config import OrchestratorConfig
from orchestrator.gemini_colab_proxy import apply_gemini_colab_proxy_env, gemini_colab_proxy_session
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
    youtube_run_id: str = ""
    gemini_registry_path: Path | None = None
    user_data_dir: Path | None = None


@dataclass
class YoutubePromoBatchRunOptions:
    story_ids: list[str]
    execute: bool = False
    force: bool = False
    account_index: int = 0
    youtube_run_id: str = ""
    gemini_registry_path: Path | None = None
    user_data_dir: Path | None = None
    batch_staging_dir: Path | None = None


@dataclass
class YoutubePromoStatusOptions:
    story_id: str
    youtube_run_id: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(config: OrchestratorConfig, path: Path, payload: Any) -> None:
    from orchestrator.isolated_io import write_json as isolated_write_json

    isolated_write_json(
        config,
        path,
        payload,
        module="orchestrator.youtube_promo_bridge",
        function="_write_json",
    )


def _legacy_write_path(
    config: OrchestratorConfig,
    story_id: str,
    legacy_relative: str,
    fallback: Path,
) -> Path:
    from orchestrator.youtube_path_resolver import resolve_bridge_legacy_write_path

    return resolve_bridge_legacy_write_path(config, story_id, legacy_relative, fallback)


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
    from orchestrator.youtube_path_resolver import resolve_bridge_story_dir

    return resolve_bridge_story_dir(config, story_id)


def _safe_story_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(config, story_id, "02_safe_story/safe_story.txt", story_dir / "02_safe_story" / "safe_story.txt")


def _promo_dir(story_dir: Path) -> Path:
    return story_dir / "03_promo"


def _fallback_source_path(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / "source_for_promo.txt"


def _promo_output_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(
        config,
        story_id,
        f"03_promo/{READY_FILE_NAME}",
        _promo_dir(story_dir) / READY_FILE_NAME,
    )


def _climax_snippet_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(
        config,
        story_id,
        f"03_promo/{SNIPPET_FILE_NAME}",
        _promo_dir(story_dir) / SNIPPET_FILE_NAME,
    )


def _promo_report_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    return _legacy_write_path(
        config,
        story_id,
        f"03_promo/{PROMO_REPORT_NAME}",
        _promo_dir(story_dir) / PROMO_REPORT_NAME,
    )


def _narration_path(story_dir: Path) -> Path:
    return story_dir / "04_audio" / "narration.mp3"


def _legacy_staging_dir(story_dir: Path) -> Path:
    return _promo_dir(story_dir) / "_legacy_staging"


def _fresh_user_data_dir(story_dir: Path) -> Path:
    return _legacy_staging_dir(story_dir) / "user_data_fresh"


def _promo_proxy_enabled() -> bool:
    raw = os.getenv("GEMINI_PROMO_USE_PROXY") or os.getenv("PROMO_USE_PROXY") or "1"
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _promo_browser_closed_error(log_text: str) -> bool:
    low = str(log_text or "").lower()
    return any(
        marker in low
        for marker in (
            "target page, context or browser has been closed",
            "targetclosederror",
            "browser has been closed",
            "context has been closed",
            "page.goto: target page",
        )
    )


def _classify_promo_subprocess_failure(log_text: str, returncode: int) -> tuple[str, bool]:
    low = str(log_text or "").lower()
    if _promo_browser_closed_error(low):
        return "promo_browser_closed", True
    if "gemini browser launch blocked" in low or "gemini_proxy_required=1" in low:
        return "promo_proxy_missing", True
    if "старый ответ" in low or "old response" in low or "stale response" in low:
        return "promo_stale_response", True
    if "не удалось прикрепить" in low or "без вложения insert_text обрежет" in low:
        return "promo_attach_failed", True
    if "ui gemini не готов" in low or "не удалось открыть gem" in low or "не найдено поле ввода gemini" in low:
        return "promo_ui_not_ready", True
    if "лимит gem-бота" in low or "quota" in low or "rate limit" in low:
        return "promo_gemini_limit", True
    if returncode == 0:
        return "missing_promo_output", False
    return "legacy_promo_failed", False


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


def _config_promo_blocks(config: OrchestratorConfig) -> dict[str, str] | None:
    from orchestrator.youtube_promo_contract import english_promo_texts, load_promo_config

    try:
        cfg = load_promo_config(config)
    except Exception:
        return None
    en = english_promo_texts(cfg)
    intro = str(en.get("intro") or "").strip()
    mid = str(en.get("mid") or "").strip()
    outro = str(en.get("outro") or "").strip()
    if not intro or not mid or not outro:
        return None
    return {
        "intro_text": f"{intro}\n\n",
        "mid_text": f"\n\n{mid}\n\n",
        "outro_text": f"\n\n{outro}",
    }


def _effective_contract(config: OrchestratorConfig) -> dict[str, Any]:
    contract = _legacy_contract(config)
    languages = {
        "intro": detect_text_language(str(contract.get("intro_text") or "")),
        "mid": detect_text_language(str(contract.get("mid_text") or "")),
        "outro": detect_text_language(str(contract.get("outro_text") or "")),
    }
    if any(lang not in {EXPECTED_YOUTUBE_LANGUAGE, "unknown"} for lang in languages.values()):
        config_blocks = _config_promo_blocks(config)
        if config_blocks:
            contract = {**contract, **config_blocks}
            contract["placeholder_ads_used"] = False
            contract["legacy_promo_blocks_language"] = languages
            contract["promo_blocks_source"] = "youtube_promo_config"
        else:
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
    assert_gemini_capable(int(account_index))
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
    idx = int(account_index)
    if idx >= len(valid):
        raise ValueError(f"ACCOUNT_NOT_GEMINI_CAPABLE: account_index={idx}")
    email, url = valid[idx]
    return {
        "email": email,
        "url": url,
        "registry_path": registry_path,
        "bot_key": _PROMO_BOT_KEY,
        "account_index": str(idx),
        "source": "gemini_registry",
    }


def _source_for_promo(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    safe = _safe_story_path(config, story_id, story_dir)
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
    source = _source_for_promo(config, story_id, story_dir)
    safe = _safe_story_path(config, story_id, story_dir)
    ready = _promo_output_path(config, story_id, story_dir)
    snippet = _climax_snippet_path(config, story_id, story_dir)
    report = _load_json(_promo_report_path(config, story_id, story_dir))

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
        "report_path": str(_promo_report_path(config, story_id, story_dir)),
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
    from orchestrator.isolated_launch_context import get_batch_launch_id, isolated_session

    batch_id = str(options.youtube_run_id or get_batch_launch_id() or "").strip()
    if batch_id and get_batch_launch_id() != batch_id:
        with isolated_session(None, batch_launch_id=batch_id, config=config):
            return _build_status(config, str(options.story_id).strip())
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
    config: OrchestratorConfig,
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
    _write_json(config, manifest_path, manifest)
    return manifest_path


def run_youtube_promo_run(
    *,
    config: OrchestratorConfig,
    options: YoutubePromoRunOptions,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import get_batch_launch_id, isolated_launch_context, isolated_session
    from orchestrator.isolated_launch_mode import is_isolated_launch

    batch_id = str(options.youtube_run_id or get_batch_launch_id() or "").strip()
    if batch_id and is_isolated_launch(config, launch_id=batch_id):
        with isolated_launch_context(config, batch_id):
            return _run_youtube_promo_run_body(config=config, options=options)
    if batch_id:
        with isolated_session(None, batch_launch_id=batch_id, config=config):
            return _run_youtube_promo_run_body(config=config, options=options)
    return _run_youtube_promo_run_body(config=config, options=options)


def _run_youtube_promo_run_body(
    *,
    config: OrchestratorConfig,
    options: YoutubePromoRunOptions,
) -> dict[str, Any]:
    story_key = str(options.story_id).strip()
    status = _build_status(config, story_key)
    story_dir = Path(str(status["story_dir"]))
    source = Path(str(status["source_path"]))
    ready = _promo_output_path(config, story_key, story_dir)
    snippet = _climax_snippet_path(config, story_key, story_dir)
    staging = _legacy_staging_dir(story_dir)
    raw = _raw_dir(story_dir)
    legacy_story_dir = staging / "promo_stories" / _safe_stem(str(status.get("canonical_basename") or story_key))
    legacy_ready = legacy_story_dir / READY_FILE_NAME
    legacy_snippet = legacy_story_dir / SNIPPET_FILE_NAME
    legacy_log = staging / "promo_inserter.log"
    runner = staging / "run_legacy_promo_inserter.py"
    user_data_dir = (
        Path(options.user_data_dir).resolve()
        if options.user_data_dir is not None
        else _fresh_user_data_dir(story_dir)
        if options.fresh_gemini_session
        else _legacy_dir(config) / "user_data"
    )
    promo_bot = _pick_promo_bot(config, options.account_index, options.gemini_registry_path)
    contract = _effective_contract(config)

    plan = {
        **status,
        "execute": bool(options.execute),
        "force": bool(options.force),
        "fresh_gemini_session": bool(options.fresh_gemini_session),
        "user_data_dir": str(user_data_dir),
        "user_data_source": "explicit" if options.user_data_dir is not None else "fresh" if options.fresh_gemini_session else "legacy",
        "promo_proxy_enabled": _promo_proxy_enabled(),
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
    from orchestrator.isolated_io import copy2 as iso_copy2, is_active_isolated

    iso = is_active_isolated(config)
    copy_fn = lambda s, d: iso_copy2(config, s, d, module="orchestrator.youtube_promo_bridge", function="run_promo") if iso else __import__("shutil").copy2(s, d)
    copy_fn(source, legacy_story_dir / SOURCE_FILE_NAME)
    copy_fn(source, raw / SOURCE_FILE_NAME)
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

    max_launch_attempts = max(1, int(os.getenv("PROMO_BROWSER_LAUNCH_ATTEMPTS") or "2"))
    proc: subprocess.CompletedProcess[str] | None = None
    for launch_attempt in range(1, max_launch_attempts + 1):
        print(
            f"[PROMO_BROWSER_LAUNCH] story={story_key} attempt={launch_attempt}/{max_launch_attempts} "
            f"user_data_dir=\"{user_data_dir}\" proxy_enabled={str(_promo_proxy_enabled()).lower()}",
            flush=True,
        )
        attempt_env = dict(env)
        if _promo_proxy_enabled():
            with gemini_colab_proxy_session(config.root_dir) as proxy_session:
                attempt_env = apply_gemini_colab_proxy_env(attempt_env, proxy_session)
                print(
                    f"[PROMO_PROXY] story={story_key} server={attempt_env.get('GEMINI_PROXY_SERVER', '')}",
                    flush=True,
                )
                proc = subprocess.run(
                    [sys.executable, str(runner)],
                    cwd=str(_legacy_dir(config)),
                    env=attempt_env,
                    text=True,
                )
        else:
            attempt_env["GEMINI_PROXY_REQUIRED"] = "0"
            proc = subprocess.run(
                [sys.executable, str(runner)],
                cwd=str(_legacy_dir(config)),
                env=attempt_env,
                text=True,
            )
        if proc.returncode == 0:
            break
        log_tail = _read_text(legacy_log)[-5000:] if legacy_log.is_file() else ""
        if launch_attempt < max_launch_attempts and _promo_browser_closed_error(log_tail):
            print(
                f"[PROMO_BROWSER_RETRY] story={story_key} reason=browser_closed "
                f"next_attempt={launch_attempt + 1}/{max_launch_attempts}",
                flush=True,
            )
            time.sleep(3.0)
            continue
        break
    if proc is None:
        raise RuntimeError("promo subprocess did not start")
    changed_files = [
        str(legacy_story_dir / SOURCE_FILE_NAME),
        str(raw / SOURCE_FILE_NAME),
        str(runner),
        str(legacy_log),
    ]

    if proc.returncode != 0:
        log_tail = _read_text(legacy_log)[-5000:] if legacy_log.is_file() else ""
        reason_code, retryable = _classify_promo_subprocess_failure(log_tail, int(proc.returncode))
        report = {
            **plan,
            "ok": False,
            "status": "failed",
            "message": "legacy promo_inserter failed",
            "reason_code": reason_code,
            "returncode": proc.returncode,
            "retryable": retryable,
            "terminal_story": not retryable,
            "queue_persist": not retryable,
            "changed_files": changed_files,
            "updated_at": _now_iso(),
        }
        if log_tail:
            report["legacy_log_tail"] = log_tail
        _write_json(config, _promo_report_path(config, story_key, story_dir), report)
        report["changed_files"].append(str(_promo_report_path(config, story_key, story_dir)))
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
        _write_json(config, _promo_report_path(config, story_key, story_dir), report)
        report["changed_files"].append(str(_promo_report_path(config, story_key, story_dir)))
        return report

    copy_fn(legacy_ready, ready)
    changed_files.append(str(ready))
    if legacy_snippet.is_file():
        copy_fn(legacy_snippet, snippet)
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
    _write_json(config, _promo_report_path(config, story_key, story_dir), report)
    report["changed_files"].append(str(_promo_report_path(config, story_key, story_dir)))
    manifest_path = _patch_manifest_after_promo(
        config=config,
        story_dir=story_dir,
        promo_result=report,
        audio_stale=audio_stale,
    )
    report["manifest_path"] = str(manifest_path)
    report["changed_files"].append(str(manifest_path))
    if audio_stale:
        report["audio"]["status"] = "stale"
        report["audio"]["stale"] = True
        report["audio"]["reason"] = "youtube_audio_stale_after_promo_change"
        report["current_blocker"] = "youtube_audio_stale_after_promo_change"
        report["next_action"] = "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt"
    return report


def run_youtube_promo_batch_run(
    *,
    config: OrchestratorConfig,
    options: YoutubePromoBatchRunOptions,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import get_batch_launch_id, isolated_launch_context, isolated_session
    from orchestrator.isolated_launch_mode import is_isolated_launch

    batch_id = str(options.youtube_run_id or get_batch_launch_id() or "").strip()
    if batch_id and is_isolated_launch(config, launch_id=batch_id):
        with isolated_launch_context(config, batch_id):
            return _run_youtube_promo_batch_run_body(config=config, options=options)
    if batch_id:
        with isolated_session(None, batch_launch_id=batch_id, config=config):
            return _run_youtube_promo_batch_run_body(config=config, options=options)
    return _run_youtube_promo_batch_run_body(config=config, options=options)


def _run_youtube_promo_batch_run_body(
    *,
    config: OrchestratorConfig,
    options: YoutubePromoBatchRunOptions,
) -> dict[str, Any]:
    story_ids: list[str] = []
    seen: set[str] = set()
    for raw_story_id in options.story_ids:
        story_id = str(raw_story_id).strip()
        key = story_id.casefold()
        if story_id and key not in seen:
            story_ids.append(story_id)
            seen.add(key)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    staging = (
        Path(options.batch_staging_dir).resolve()
        if options.batch_staging_dir is not None
        else (config.root_dir / "runs" / "youtube_promo_batch" / f"promo_batch_{timestamp}").resolve()
    )
    promo_stories_dir = staging / "promo_stories"
    legacy_log = staging / "promo_inserter.log"
    runner = staging / "run_legacy_promo_inserter.py"
    user_data_dir = (
        Path(options.user_data_dir).resolve()
        if options.user_data_dir is not None
        else _legacy_dir(config) / "user_data"
    )
    promo_bot = _pick_promo_bot(config, options.account_index, options.gemini_registry_path)
    contract = _effective_contract(config)
    results: dict[str, dict[str, Any]] = {}
    launch_items: list[dict[str, Any]] = []
    used_folder_names: dict[str, int] = {}

    from orchestrator.isolated_io import copy2 as iso_copy2, is_active_isolated

    iso = is_active_isolated(config)

    def copy_fn(src: Path, dst: Path) -> Path:
        if iso:
            return iso_copy2(
                config,
                src,
                dst,
                module="orchestrator.youtube_promo_bridge",
                function="run_promo_batch",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst.resolve()

    for story_key in story_ids:
        status = _build_status(config, story_key)
        story_dir = Path(str(status["story_dir"]))
        source = Path(str(status["source_path"]))
        ready = _promo_output_path(config, story_key, story_dir)
        snippet = _climax_snippet_path(config, story_key, story_dir)
        raw = _raw_dir(story_dir)
        plan = {
            **status,
            "execute": bool(options.execute),
            "force": bool(options.force),
            "fresh_gemini_session": False,
            "user_data_dir": str(user_data_dir),
            "user_data_source": "explicit" if options.user_data_dir is not None else "legacy",
            "promo_proxy_enabled": _promo_proxy_enabled(),
            "gemini_account_email": promo_bot.get("email", ""),
            "gemini_url": promo_bot.get("url", ""),
            "gemini_registry_path": promo_bot.get("registry_path", ""),
            "gemini_bot_key": promo_bot.get("bot_key", ""),
            "gemini_account_index": promo_bot.get("account_index", str(options.account_index)),
            "gemini_bot_source": promo_bot.get("source", ""),
            "legacy_staging_dir": str(staging),
            "legacy_log_path": str(legacy_log),
            "runner_path": str(runner),
            "changed_files": [],
        }

        if status["status"] in {"missing_story_dir", "missing_source"}:
            results[story_key] = {
                **plan,
                "ok": False,
                "status": status["status"],
                "message": "Missing promo source",
                "reason_code": status["status"],
            }
            continue
        if status["status"] == "wrong_language" and status.get("source_language") != EXPECTED_YOUTUBE_LANGUAGE:
            results[story_key] = {
                **plan,
                "ok": False,
                "status": "blocked_wrong_language",
                "message": "promo-run blocked: safe_story/text_ready_for_audio must be English for YouTube pipeline",
                "reason_code": "blocked_wrong_language",
            }
            continue
        if status["status"] == "done" and not options.force:
            results[story_key] = {
                **plan,
                "ok": True,
                "status": "done",
                "message": "promo already inserted",
                "output_path": str(ready),
            }
            continue
        if not options.execute:
            results[story_key] = {
                **plan,
                "ok": True,
                "status": "would_run",
                "message": "dry-run only; no files written and legacy Gemini was not launched",
            }
            continue

        stem = _safe_stem(str(status.get("canonical_basename") or story_key))
        used_folder_names[stem] = used_folder_names.get(stem, 0) + 1
        folder_name = stem if used_folder_names[stem] == 1 else f"{stem}_{used_folder_names[stem]}"
        legacy_story_dir = promo_stories_dir / folder_name
        legacy_ready = legacy_story_dir / READY_FILE_NAME
        legacy_snippet = legacy_story_dir / SNIPPET_FILE_NAME

        _promo_dir(story_dir).mkdir(parents=True, exist_ok=True)
        raw.mkdir(parents=True, exist_ok=True)
        legacy_story_dir.mkdir(parents=True, exist_ok=True)
        for stale_generated in (
            legacy_ready,
            legacy_snippet,
            legacy_story_dir / "climax_snippet.tmp",
        ):
            if stale_generated.exists():
                stale_generated.unlink()
        copy_fn(source, legacy_story_dir / SOURCE_FILE_NAME)
        copy_fn(source, raw / SOURCE_FILE_NAME)
        changed_files = [
            str(legacy_story_dir / SOURCE_FILE_NAME),
            str(raw / SOURCE_FILE_NAME),
            str(runner),
            str(legacy_log),
        ]
        launch_items.append(
            {
                "story_key": story_key,
                "plan": {
                    **plan,
                    "legacy_story_dir": str(legacy_story_dir),
                    "changed_files": changed_files,
                },
                "story_dir": story_dir,
                "ready": ready,
                "snippet": snippet,
                "legacy_ready": legacy_ready,
                "legacy_snippet": legacy_snippet,
                "changed_files": changed_files,
            }
        )

    if not launch_items:
        return {
            "ok": all(bool(result.get("ok")) for result in results.values()),
            "status": "done",
            "stories": results,
            "processed": len(results),
            "launched": 0,
            "legacy_staging_dir": str(staging),
        }

    _write_legacy_runner(
        runner,
        legacy_dir=_legacy_dir(config),
        promo_stories_dir=promo_stories_dir,
        user_data_dir=user_data_dir,
        log_path=legacy_log,
        fresh_gemini_session=False,
        promo_texts={
            "intro_text": str(contract.get("intro_text") or ""),
            "mid_text": str(contract.get("mid_text") or ""),
            "outro_text": str(contract.get("outro_text") or ""),
        },
    )

    env = os.environ.copy()
    if promo_bot.get("url"):
        env["GEMINI_URL"] = str(promo_bot["url"])
    env["PROMO_FRESH_GEMINI_SESSION"] = "0"
    env["PROMO_USER_DATA_DIR"] = str(user_data_dir)

    max_launch_attempts = max(1, int(os.getenv("PROMO_BROWSER_LAUNCH_ATTEMPTS") or "2"))
    proc: subprocess.CompletedProcess[str] | None = None
    print(
        f"[PROMO_BATCH_BEGIN] stories={len(launch_items)} user_data_dir=\"{user_data_dir}\" "
        f"proxy_enabled={str(_promo_proxy_enabled()).lower()}",
        flush=True,
    )
    for launch_attempt in range(1, max_launch_attempts + 1):
        print(
            f"[PROMO_BATCH_BROWSER_LAUNCH] attempt={launch_attempt}/{max_launch_attempts} "
            f"stories={len(launch_items)} staging=\"{staging}\"",
            flush=True,
        )
        attempt_env = dict(env)
        if _promo_proxy_enabled():
            with gemini_colab_proxy_session(config.root_dir) as proxy_session:
                attempt_env = apply_gemini_colab_proxy_env(attempt_env, proxy_session)
                print(
                    f"[PROMO_BATCH_PROXY] server={attempt_env.get('GEMINI_PROXY_SERVER', '')}",
                    flush=True,
                )
                proc = subprocess.run(
                    [sys.executable, str(runner)],
                    cwd=str(_legacy_dir(config)),
                    env=attempt_env,
                    text=True,
                )
        else:
            attempt_env["GEMINI_PROXY_REQUIRED"] = "0"
            proc = subprocess.run(
                [sys.executable, str(runner)],
                cwd=str(_legacy_dir(config)),
                env=attempt_env,
                text=True,
            )
        if proc.returncode == 0:
            break
        log_tail = _read_text(legacy_log)[-5000:] if legacy_log.is_file() else ""
        if launch_attempt < max_launch_attempts and _promo_browser_closed_error(log_tail):
            print(
                f"[PROMO_BATCH_BROWSER_RETRY] reason=browser_closed "
                f"next_attempt={launch_attempt + 1}/{max_launch_attempts}",
                flush=True,
            )
            time.sleep(3.0)
            continue
        break
    if proc is None:
        raise RuntimeError("promo batch subprocess did not start")

    log_tail = _read_text(legacy_log)[-5000:] if legacy_log.is_file() else ""
    browser_closed = _promo_browser_closed_error(log_tail)

    for meta in launch_items:
        story_key = str(meta["story_key"])
        plan = dict(meta["plan"])
        story_dir = Path(meta["story_dir"])
        ready = Path(meta["ready"])
        snippet = Path(meta["snippet"])
        legacy_ready = Path(meta["legacy_ready"])
        legacy_snippet = Path(meta["legacy_snippet"])
        changed_files = list(meta["changed_files"])

        if legacy_ready.is_file():
            copy_fn(legacy_ready, ready)
            changed_files.append(str(ready))
            if legacy_snippet.is_file():
                copy_fn(legacy_snippet, snippet)
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
                "fresh_gemini_session": False,
                "user_data_dir": str(user_data_dir),
                "gemini_account_email": promo_bot.get("email", ""),
                "gemini_url": promo_bot.get("url", ""),
                "gemini_registry_path": promo_bot.get("registry_path", ""),
                "gemini_bot_key": promo_bot.get("bot_key", ""),
                "gemini_account_index": promo_bot.get("account_index", str(options.account_index)),
                "gemini_bot_source": promo_bot.get("source", ""),
                "returncode": proc.returncode,
                "legacy_staging_dir": str(staging),
                "legacy_story_dir": str(plan.get("legacy_story_dir") or ""),
                "legacy_log_path": str(legacy_log),
                "changed_files": changed_files,
                "updated_at": _now_iso(),
            }
            _write_json(config, _promo_report_path(config, story_key, story_dir), report)
            report["changed_files"].append(str(_promo_report_path(config, story_key, story_dir)))
            manifest_path = _patch_manifest_after_promo(
                config=config,
                story_dir=story_dir,
                promo_result=report,
                audio_stale=audio_stale,
            )
            report["manifest_path"] = str(manifest_path)
            report["changed_files"].append(str(manifest_path))
            if audio_stale:
                report["audio"]["status"] = "stale"
                report["audio"]["stale"] = True
                report["audio"]["reason"] = "youtube_audio_stale_after_promo_change"
                report["current_blocker"] = "youtube_audio_stale_after_promo_change"
                report["next_action"] = "rerun YouTube TTS from updated 03_promo/text_ready_for_audio.txt"
            results[story_key] = report
            continue

        reason_code, retryable = _classify_promo_subprocess_failure(log_tail, int(proc.returncode))
        report = {
            **plan,
            "ok": False,
            "status": "failed",
            "message": "legacy promo_inserter did not produce text_ready_for_audio.txt",
            "reason_code": reason_code,
            "returncode": proc.returncode,
            "retryable": retryable,
            "terminal_story": not retryable,
            "queue_persist": not retryable,
            "changed_files": changed_files,
            "updated_at": _now_iso(),
        }
        if log_tail:
            report["legacy_log_tail"] = log_tail
        _write_json(config, _promo_report_path(config, story_key, story_dir), report)
        report["changed_files"].append(str(_promo_report_path(config, story_key, story_dir)))
        results[story_key] = report

    ok_count = sum(1 for result in results.values() if result.get("ok"))
    failed_count = len(results) - ok_count
    print(f"[PROMO_BATCH_END] processed={len(results)} ok={ok_count} failed={failed_count}", flush=True)
    return {
        "ok": failed_count == 0,
        "status": "done" if failed_count == 0 else "partial",
        "stories": results,
        "processed": len(results),
        "ok_count": ok_count,
        "failed_count": failed_count,
        "returncode": proc.returncode,
        "legacy_staging_dir": str(staging),
        "legacy_log_path": str(legacy_log),
    }
