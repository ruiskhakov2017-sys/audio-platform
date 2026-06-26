"""English YouTube-safe rewrite adapter.

This bridge reuses the legacy Gemini/Playwright helpers, but keeps a separate
English prompt, isolated staging, and language validation before importing.
"""

from __future__ import annotations

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

from orchestrator.account_capabilities import assert_gemini_capable
from orchestrator.config import OrchestratorConfig
from orchestrator.gemini_colab_proxy import apply_gemini_colab_proxy_env, gemini_colab_proxy_session
from orchestrator.phase_a import _load_gemini_registry
from orchestrator.youtube_language import EXPECTED_YOUTUBE_LANGUAGE, detect_path_language, detect_text_language


PROMPT_PATH = Path("configs/prompts/youtube_safe_rewrite_en.txt")
BOT_KEY = "youtube_safe_text"
DEFAULT_CHUNK_MIN_CHARS = 3000
DEFAULT_CHUNK_MAX_CHARS = 4000
MIN_LONG_OUTPUT_RATIO = 0.35


@dataclass
class YoutubeSafeEnglishRunOptions:
    story_id: str
    execute: bool = False
    force: bool = False
    account_index: int = 0
    gemini_registry_path: Path = Path("configs/gemini_bots_registry.example.yaml")
    reuse_legacy_user_data: bool = False


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


def _write_json(path: Path, payload: Any, *, config: OrchestratorConfig | None = None) -> None:
    from orchestrator.isolated_io import is_active_isolated, write_json as isolated_write_json
    from orchestrator.isolated_launch_context import get_active_config

    cfg = config or get_active_config()
    if cfg is not None and is_active_isolated(cfg):
        isolated_write_json(
            cfg,
            path,
            payload,
            module="orchestrator.youtube_safe_english_bridge",
            function="_write_json",
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", text or ""))


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    from orchestrator.youtube_path_resolver import resolve_bridge_story_dir

    return resolve_bridge_story_dir(config, story_id)


def _manifest_path(story_dir: Path) -> Path:
    return story_dir / "youtube_story_manifest.json"


def _source_path(story_dir: Path) -> Path:
    return story_dir / "00_source" / "source_cleaned_story.txt"


def _safe_path(config: OrchestratorConfig, story_id: str, story_dir: Path) -> Path:
    from orchestrator.isolated_launch_context import get_batch_launch_id
    from orchestrator.isolated_launch_mode import is_isolated_launch
    from orchestrator.youtube_path_resolver import resolve_youtube_story_write_path

    batch_id = get_batch_launch_id()
    if batch_id and is_isolated_launch(config, launch_id=batch_id):
        return resolve_youtube_story_write_path(
            config,
            story_id,
            "02_safe_story/safe_story.txt",
            launch_id=batch_id,
        )
    return story_dir / "02_safe_story" / "safe_story.txt"


def _adapter_dir(story_dir: Path) -> Path:
    return story_dir / "02_safe_story" / "_english_adapter"


def _staging_story_dir(story_dir: Path) -> Path:
    return _adapter_dir(story_dir) / "staging" / "story"


def _raw_outputs_dir(story_dir: Path) -> Path:
    return _adapter_dir(story_dir) / "raw_outputs"


def _runner_path(story_dir: Path) -> Path:
    return _adapter_dir(story_dir) / "run_english_safe_gemini.py"


def _bot_config_path(story_dir: Path) -> Path:
    return _adapter_dir(story_dir) / "gemini_bots.english_safe.json"


def _log_path(story_dir: Path) -> Path:
    return story_dir / "logs" / "youtube_safe_english_run.log"


def _report_path(story_dir: Path) -> Path:
    return story_dir / "logs" / "youtube_safe_english_report.json"


def _stale_backup_path(safe_path: Path, detected_language: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lang = detected_language if detected_language not in {"missing", "unknown"} else "wrong_language"
    return safe_path.parent / "_stale_wrong_language" / f"safe_story.{lang}.{stamp}.txt"


def _split_text_for_plan(text: str, min_chars: int = DEFAULT_CHUNK_MIN_CHARS, max_chars: int = DEFAULT_CHUNK_MAX_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars or len(current) < min_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _registry_candidates(config: OrchestratorConfig, explicit: Path) -> list[Path]:
    explicit_path = explicit if explicit.is_absolute() else (config.root_dir / explicit).resolve()
    candidates = [
        explicit_path,
        (config.root_dir / "configs" / "gemini_bots_registry.yaml").resolve(),
        (config.root_dir / "configs" / "gemini_bots_registry.example.yaml").resolve(),
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _pick_safe_bot(config: OrchestratorConfig, registry: Path, account_index: int) -> dict[str, str]:
    assert_gemini_capable(int(account_index))
    registry_path = ""
    bots: list[dict[str, Any]] = []
    for candidate in _registry_candidates(config, registry):
        if candidate.is_file():
            loaded = _load_gemini_registry(candidate)
            if loaded:
                registry_path = str(candidate)
                bots = loaded
                break
    valid: list[tuple[str, str]] = []
    for bot in bots:
        url = str(bot.get(BOT_KEY, "")).strip()
        if url.startswith("https://gemini.google.com/"):
            valid.append((str(bot.get("email", "")).strip(), url))
    if not valid:
        return {"email": "", "url": "", "registry_path": registry_path, "account_index": str(account_index), "bot_key": BOT_KEY}
    idx = int(account_index)
    if idx >= len(valid):
        raise ValueError(f"ACCOUNT_NOT_GEMINI_CAPABLE: account_index={idx}")
    email, url = valid[idx]
    return {"email": email, "url": url, "registry_path": registry_path, "account_index": str(idx), "bot_key": BOT_KEY}


def _validate_output(source_text: str, output_text: str) -> dict[str, Any]:
    detected = detect_text_language(output_text)
    source_words = _count_words(source_text)
    output_words = _count_words(output_text)
    min_words = max(400, int(source_words * MIN_LONG_OUTPUT_RATIO)) if source_words >= 1200 else max(50, int(source_words * 0.25))
    suspicious_summary = output_words < min_words
    starts_like_commentary = bool(
        re.match(r"^\s*(here is|here's|summary|rewritten version|the rewritten story|i('| a)m sorry)\b", output_text, re.I)
    )
    ok = bool(output_text.strip()) and detected == EXPECTED_YOUTUBE_LANGUAGE and not suspicious_summary and not starts_like_commentary
    reason = ""
    if not output_text.strip():
        reason = "empty_output"
    elif detected != EXPECTED_YOUTUBE_LANGUAGE:
        reason = "wrong_language"
    elif suspicious_summary:
        reason = "suspiciously_small_output"
    elif starts_like_commentary:
        reason = "model_returned_commentary"
    return {
        "ok": ok,
        "validation_status": "ok" if ok else "failed",
        "detected_language": detected,
        "source_words": source_words,
        "output_words": output_words,
        "min_expected_words": min_words,
        "suspicious_summary": suspicious_summary,
        "starts_like_commentary": starts_like_commentary,
        "reason": reason,
    }


def _write_runner(path: Path, *, legacy_dir: Path, prompt_path: Path, bot_config_path: Path, stories_root: Path, story_dir: Path, user_data_dir: Path, raw_outputs_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import sys",
                "from pathlib import Path",
                f"legacy_dir = Path({str(legacy_dir)!r})",
                "sys.path.insert(0, str(legacy_dir))",
                "import gemini_auto as ga",
                f"ga.STORIES_DIR = Path({str(stories_root)!r})",
                f"ga.USER_DATA_DIR = Path({str(user_data_dir)!r})",
                f"ga.GEMINI_BOTS_CONFIG = {str(bot_config_path)!r}",
                "ga.CHUNK_PAUSE_MIN_SEC = 1",
                "ga.CHUNK_PAUSE_MAX_SEC = 3",
                f"prompt_path = Path({str(prompt_path)!r})",
                f"story_dir = Path({str(story_dir)!r})",
                f"raw_outputs_dir = Path({str(raw_outputs_dir)!r})",
                "raw_outputs_dir.mkdir(parents=True, exist_ok=True)",
                "source_file = ga.pick_story_source_file(story_dir)",
                "if source_file is None:",
                "    raise RuntimeError('No source txt in staging story dir')",
                "clean_path = ga.clean_output_path(source_file)",
                "tmp_path = ga.clean_tmp_path(source_file)",
                "prog_path = ga.progress_file_path(source_file)",
                "for p in (clean_path, prog_path):",
                "    if p.exists():",
                "        p.unlink()",
                "source_text = source_file.read_text(encoding='utf-8', errors='replace')",
                "chunks = ga.split_text_into_boundary_chunks(source_text, ga.CHUNK_MIN_CHARS, ga.CHUNK_MAX_CHARS)",
                "prompt_text = prompt_path.read_text(encoding='utf-8').strip()",
                "existing_chunks = []",
                "for i in range(1, len(chunks) + 1):",
                "    p = raw_outputs_dir / f'chunk_{i:04d}.txt'",
                "    if not p.is_file() or not p.read_text(encoding='utf-8', errors='replace').strip():",
                "        break",
                "    existing_chunks.append(p)",
                "if existing_chunks:",
                "    tmp_path.write_text('\\n\\n'.join(p.read_text(encoding='utf-8', errors='replace').strip() for p in existing_chunks).strip() + '\\n', encoding='utf-8')",
                "elif tmp_path.exists():",
                "    tmp_path.unlink()",
                "manifest = {'chunks_total': len(chunks), 'chunks_done': len(existing_chunks), 'raw_outputs_dir': str(raw_outputs_dir), 'final_output_path': str(clean_path)}",
                "(raw_outputs_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')",
                "bot_chain = ga.load_gem_bot_chain()",
                "bot_idx = 0",
                "gemini_url = bot_chain[bot_idx].url",
                "hub_url = bot_chain[bot_idx].hub_url",
                "with ga.sync_playwright() as playwright:",
                "    context = playwright.chromium.launch_persistent_context(user_data_dir=str(ga.USER_DATA_DIR), channel='chrome', headless=False, slow_mo=ga.SLOW_MO_MS, viewport=None, chromium_sandbox=True, args=['--disable-blink-features=AutomationControlled'])",
                "    page = context.pages[0] if context.pages else context.new_page()",
                "    context.grant_permissions(['clipboard-read', 'clipboard-write'], origin='https://gemini.google.com')",
                "    for _ in range(3):",
                "        if ga.ensure_on_bot_page(page, gemini_url, hub_url):",
                "            break",
                "        ga.wait_with_status(30, 'Retry opening English-safe Gem')",
                "    if not ga.ensure_logged_in(page, bot_entry=bot_chain[bot_idx]):",
                "        raise RuntimeError('Gemini login/UI is not ready')",
                "    ga.ensure_thinking_mode(page)",
                "    def send_rules() -> None:",
                "        rules_body = prompt_text + '\\n\\nAcknowledge briefly that you will rewrite every chunk in English only.'",
                "        ga._exchange_message_and_read(page, story_dir, rules_body)",
                "    def recover_chat() -> None:",
                "        for _ in range(3):",
                "            if ga.recover_session_state(page, gemini_url, hub_url=hub_url, resume_same_chat=False, bot_entry=bot_chain[bot_idx]):",
                "                ga.ensure_thinking_mode(page)",
                "                send_rules()",
                "                return",
                "            ga.wait_with_status(30, 'Retry recovering English-safe Gem chat')",
                "        raise RuntimeError('Unable to recover Gemini chat input for English-safe rewrite')",
                "    if not existing_chunks:",
                "        send_rules()",
                "    with tmp_path.open('a', encoding='utf-8') as out:",
                "        for i, chunk in enumerate(chunks, start=1):",
                "            if i <= len(existing_chunks):",
                "                continue",
                "            user_msg = f'Chunk {i} of {len(chunks)}. Rewrite ONLY this chunk into English YouTube-safe narrative. Output only the rewritten English text. Do not summarize. No Russian.\\n\\n{chunk}'",
                "            try:",
                "                response = ga._exchange_message_and_read(page, story_dir, user_msg).strip()",
                "            except Exception as exc:",
                "                print(f'[WARN] chunk {i}: Gemini exchange failed once: {exc}', flush=True)",
                "                recover_chat()",
                "                response = ga._exchange_message_and_read(page, story_dir, user_msg).strip()",
                "            (raw_outputs_dir / f'chunk_{i:04d}.txt').write_text(response + '\\n', encoding='utf-8')",
                "            if out.tell() > 0:",
                "                out.write('\\n\\n')",
                "            out.write(response)",
                "            out.write('\\n')",
                "            out.flush()",
                "            manifest['chunks_done'] = i",
                "            (raw_outputs_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')",
                "    context.close()",
                "tmp_path.rename(clean_path)",
                "manifest['final_output_path'] = str(clean_path)",
                "(raw_outputs_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')",
                "print(json.dumps(manifest, ensure_ascii=False))",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _patch_manifest_running(manifest_path: Path, payload: dict[str, Any]) -> None:
    manifest = _read_json(manifest_path)
    now = _now_iso()
    manifest["safe_story"] = {
        **(manifest.get("safe_story") if isinstance(manifest.get("safe_story"), dict) else {}),
        "status": payload["status"],
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "source_path": payload.get("source_path", ""),
        "output_path": payload.get("final_output_path", ""),
        "runner": "english_safe_adapter",
        "updated_at": now,
    }
    manifest["updated_at"] = now
    _write_json(manifest_path, manifest)


def _patch_manifest_success(manifest_path: Path, payload: dict[str, Any]) -> None:
    manifest = _read_json(manifest_path)
    now = _now_iso()
    manifest["safe_story"] = {
        "status": "done",
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "detected_language": payload.get("detected_language", ""),
        "source_path": payload.get("source_path", ""),
        "output_path": payload.get("final_output_path", ""),
        "source_hash": payload.get("source_hash", ""),
        "output_hash": payload.get("output_hash", ""),
        "runner": "english_safe_adapter",
        "chunks_total": payload.get("chunks_total", 0),
        "chunks_done": payload.get("chunks_done", 0),
        "raw_outputs_dir": payload.get("raw_outputs_dir", ""),
        "updated_at": now,
    }
    manifest["safe"] = {
        **(manifest.get("safe") if isinstance(manifest.get("safe"), dict) else {}),
        "status": "done",
        "runner": "english_safe_adapter",
        "imported_at": now,
    }
    manifest["promo"] = {
        **(manifest.get("promo") if isinstance(manifest.get("promo"), dict) else {}),
        "status": "stale_or_missing",
        "reason": "safe_story_changed",
        "updated_at": now,
    }
    tts = dict(manifest.get("tts_kokoro_colab") or {})
    tts.update({"status": "stale", "reason": "safe_story_changed_requires_new_promo_and_tts", "updated_at": now})
    manifest["tts_kokoro_colab"] = tts
    visuals = dict(manifest.get("visuals") or {})
    visuals.update({"status": "blocked_until_promo_and_audio", "reason": "youtube_promo_missing_or_audio_stale", "updated_at": now})
    manifest["visuals"] = visuals
    status = dict(manifest.get("status") or {})
    status.update({"safe_done": True, "promo_done": False, "audio_done": False, "frames_done": False, "video_done": False})
    manifest["status"] = status
    stage = dict(manifest.get("pipeline_stage_status") or {})
    stage.update({"safe_story": "done", "promo": "stale_or_missing", "tts_kokoro_colab": "stale", "audio": "stale", "visuals": "blocked"})
    manifest["pipeline_stage_status"] = stage
    manifest["updated_at"] = now
    _write_json(manifest_path, manifest)


def _patch_manifest_failure(manifest_path: Path, payload: dict[str, Any]) -> None:
    manifest = _read_json(manifest_path)
    now = _now_iso()
    manifest["safe_story"] = {
        **(manifest.get("safe_story") if isinstance(manifest.get("safe_story"), dict) else {}),
        "status": payload.get("status", "failed"),
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "detected_language": payload.get("detected_language", ""),
        "source_path": payload.get("source_path", ""),
        "output_path": payload.get("candidate_output_path", ""),
        "runner": "english_safe_adapter",
        "reason": payload.get("reason", ""),
        "updated_at": now,
    }
    manifest["updated_at"] = now
    _write_json(manifest_path, manifest)


def run_youtube_safe_english_run(*, config: OrchestratorConfig, options: YoutubeSafeEnglishRunOptions) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import get_batch_launch_id, isolated_launch_context
    from orchestrator.isolated_launch_mode import is_isolated_launch

    batch_id = get_batch_launch_id()
    if batch_id and is_isolated_launch(config, launch_id=batch_id):
        with isolated_launch_context(config, batch_id):
            return _run_youtube_safe_english_run_body(config=config, options=options)
    return _run_youtube_safe_english_run_body(config=config, options=options)


def _run_youtube_safe_english_run_body(*, config: OrchestratorConfig, options: YoutubeSafeEnglishRunOptions) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, story_id)
    manifest_path = _manifest_path(story_dir)
    source = _source_path(story_dir)
    safe = _safe_path(config, story_id, story_dir)
    prompt = (config.root_dir / PROMPT_PATH).resolve()
    adapter_dir = _adapter_dir(story_dir)
    staging_story = _staging_story_dir(story_dir)
    staging_source = staging_story / "source_cleaned_story.txt"
    staging_clean = staging_story / "source_cleaned_story_clean.txt"
    raw_outputs = _raw_outputs_dir(story_dir)
    runner = _runner_path(story_dir)
    bot_config = _bot_config_path(story_dir)
    log_path = _log_path(story_dir)
    user_data = adapter_dir / "user_data"
    if options.reuse_legacy_user_data:
        user_data = (config.root_dir / "legacy" / "youtube_tts" / "user_data").resolve()
    bot = _pick_safe_bot(config, options.gemini_registry_path, options.account_index)
    source_text = _read_text(source)
    chunks = _split_text_for_plan(source_text)
    source_language = detect_text_language(source_text) if source.is_file() else "missing"
    safe_language = detect_path_language(safe)
    safe_story_status = "done" if safe_language == EXPECTED_YOUTUBE_LANGUAGE else "wrong_language" if safe.is_file() else "missing"
    backup_path = _stale_backup_path(safe, safe_language)
    missing = []
    if not story_dir.is_dir():
        missing.append(str(story_dir))
    if not source.is_file():
        missing.append(str(source))
    if not prompt.is_file():
        missing.append(str(prompt))
    if not bot.get("url"):
        missing.append(f"{BOT_KEY} in {options.gemini_registry_path}")

    base = {
        "ok": not missing,
        "status": "missing_inputs" if missing else ("would_run" if not options.execute else "running"),
        "execute": bool(options.execute),
        "story_id": story_id,
        "canonical_basename": story_dir.name,
        "story_dir": str(story_dir),
        "source_path": str(source),
        "source_language": source_language,
        "source_hash": _sha256_file(source),
        "safe_story_path": str(safe),
        "safe_story_language": safe_language,
        "safe_story_status": safe_story_status,
        "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
        "prompt_path": str(prompt),
        "runner": "english_safe_adapter",
        "adapter_dir": str(adapter_dir),
        "staging_story_dir": str(staging_story),
        "staging_source_path": str(staging_source),
        "candidate_output_path": str(staging_clean),
        "final_output_path": str(safe),
        "raw_outputs_dir": str(raw_outputs),
        "runner_path": str(runner),
        "log_path": str(log_path),
        "bot_config_path": str(bot_config),
        "gemini_account_email": bot.get("email", ""),
        "gemini_account_index": bot.get("account_index", str(options.account_index)),
        "gemini_bot_key": bot.get("bot_key", BOT_KEY),
        "gemini_url": bot.get("url", ""),
        "gemini_registry_path": bot.get("registry_path", ""),
        "user_data_dir": str(user_data),
        "reuse_legacy_user_data": bool(options.reuse_legacy_user_data),
        "chunks_total": len(chunks),
        "chunks_done": 0,
        "backup_path": str(backup_path),
        "current_blocker": "youtube_safe_story_wrong_language" if safe_story_status != "done" else "",
        "next_action": "run youtube safe-regenerate --execute" if safe_story_status != "done" else "safe story is already English",
        "missing": missing,
        "changed_files": [],
    }
    if missing:
        return {**base, "ok": False}
    if safe.is_file() and safe_language == EXPECTED_YOUTUBE_LANGUAGE and not options.force:
        return {**base, "ok": True, "status": "done", "message": "English safe_story already exists"}
    if not options.execute:
        return {**base, "ok": True, "message": "dry-run only; Gemini was not launched and no files were written"}

    adapter_dir.mkdir(parents=True, exist_ok=True)
    staging_story.mkdir(parents=True, exist_ok=True)
    raw_outputs.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from orchestrator.isolated_io import copy2 as iso_copy2, is_active_isolated

    iso = is_active_isolated(config)
    if iso:
        iso_copy2(config, source, staging_source, module="orchestrator.youtube_safe_english_bridge", function="run")
    else:
        shutil.copy2(source, staging_source)
    _write_json(
        bot_config,
        [{"email": bot.get("email", ""), "url": bot.get("url", ""), "app": ""}],
    )
    _write_runner(
        runner,
        legacy_dir=(config.root_dir / "legacy" / "youtube_tts").resolve(),
        prompt_path=prompt,
        bot_config_path=bot_config,
        stories_root=staging_story.parent,
        story_dir=staging_story,
        user_data_dir=user_data,
        raw_outputs_dir=raw_outputs,
    )
    changed_files = [str(staging_source), str(bot_config), str(runner)]
    running_payload = {**base, "status": "running", "final_output_path": str(safe)}
    _patch_manifest_running(manifest_path, running_payload)
    with log_path.open("w", encoding="utf-8", errors="replace") as logf:
        logf.write(f"[orchestrator] story_id={story_id}\n")
        logf.write(f"[orchestrator] prompt_path={prompt}\n")
        logf.write(f"[orchestrator] gemini_email={bot.get('email', '')}\n")
        logf.write(f"[orchestrator] gemini_url={bot.get('url', '')}\n")
        logf.flush()
        with gemini_colab_proxy_session(config.root_dir) as proxy_session:
            run_env = apply_gemini_colab_proxy_env(
                {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "GEMINI_NON_INTERACTIVE": "1"},
                proxy_session,
            )
            logf.write(f"[orchestrator] GEMINI_PROXY_SERVER={run_env.get('GEMINI_PROXY_SERVER', '')}\n")
            logf.flush()
            proc = subprocess.run(
                [sys.executable, str(runner)],
                cwd=str(adapter_dir),
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                env=run_env,
                timeout=None,
            )
    changed_files.append(str(log_path))
    if proc.returncode != 0:
        result = {**base, "ok": False, "status": "english_safe_failed", "returncode": proc.returncode, "changed_files": changed_files}
        _patch_manifest_failure(manifest_path, result)
        _write_json(_report_path(story_dir), result)
        return result
    if not staging_clean.is_file():
        result = {**base, "ok": False, "status": "english_safe_failed_missing_output", "returncode": proc.returncode, "changed_files": changed_files}
        _patch_manifest_failure(manifest_path, result)
        _write_json(_report_path(story_dir), result)
        return result

    output_text = _read_text(staging_clean)
    validation = _validate_output(source_text, output_text)
    raw_manifest = _read_json(raw_outputs / "manifest.json")
    result = {
        **base,
        **validation,
        "status": "done" if validation["ok"] else "english_safe_failed_wrong_language" if validation["reason"] == "wrong_language" else "english_safe_failed_validation",
        "ok": bool(validation["ok"]),
        "returncode": proc.returncode,
        "chunks_total": int(raw_manifest.get("chunks_total") or len(chunks)),
        "chunks_done": int(raw_manifest.get("chunks_done") or 0),
        "candidate_output_path": str(staging_clean),
        "output_hash": _sha256_file(staging_clean),
        "final_output_path": str(safe),
        "changed_files": changed_files,
    }
    if not validation["ok"]:
        _patch_manifest_failure(manifest_path, result)
        _write_json(_report_path(story_dir), result)
        result["changed_files"].append(str(_report_path(story_dir)))
        return result

    if safe.is_file() and safe_language != EXPECTED_YOUTUBE_LANGUAGE:
        if iso:
            iso_copy2(config, safe, backup_path, module="orchestrator.youtube_safe_english_bridge", function="run")
        else:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe, backup_path)
        result["backup_path"] = str(backup_path)
        changed_files.append(str(backup_path))
    if iso:
        iso_copy2(config, staging_clean, safe, module="orchestrator.youtube_safe_english_bridge", function="run")
    else:
        safe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staging_clean, safe)
    result["output_hash"] = _sha256_file(safe)
    changed_files.append(str(safe))
    _patch_manifest_success(manifest_path, result)
    changed_files.append(str(manifest_path))
    _write_json(_report_path(story_dir), result)
    changed_files.append(str(_report_path(story_dir)))
    result["changed_files"] = changed_files
    return result
