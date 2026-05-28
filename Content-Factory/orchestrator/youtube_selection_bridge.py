"""YouTube selection bridge: single-input thin facade around legacy/youtube_selection/gemini_auto.py.

Design (legacy-first, минимальный adapter):
- Не патчит логику selection-runner-а: subprocess legacy/youtube_selection/gemini_auto.py.
- Использует env-overrides, добавленные минимальным паритетным патчем legacy:
  GEMINI_STORIES_DIR / GEMINI_TRASH_DIR / GEMINI_USER_DATA_DIR / GEMINI_LOG_FILE /
  GEMINI_PARALLEL_STATE_DIR / GEMINI_ACCOUNTS_FILE / GEMINI_URL.
- Изолированный staging под legacy/youtube_selection/_orchestrator_runs/<youtube_run_id>/...
  (никогда не пересекает production stories/trash/user_data/accounts.txt).
- Single-input: ровно одна leaf story-папка с одним .txt.
- Output контракт legacy/youtube_selection:
    * YES  → story_dir/info.txt (со строкой "подходит для YouTube: да")
    * NO   → story_dir переезжает в TRASH_DIR/<rel>/, внутри info.txt со статусом "нет"
- Bridge копирует info.txt в _gemini_selection/raw/<input_id>__raw.txt и в expected_gemini_output_text.
- Default — preflight (без --execute). С --execute — реальный Playwright/Chrome через legacy.
- Mini chain json (один бот youtube_selection из registry) сохраняется в _gemini_selection/_chain/
  для воспроизводимости; selection-runner его не читает (использует GEMINI_URL напрямую).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.phase_a import _load_gemini_registry, _read_profile_email
from orchestrator.youtube_from_site import (
    _append_status,
    _gemini_selection_dir,
    _has_youtube_prefilter_header,
    _now_iso,
    _read_json,
    _read_text,
    _safe_name,
    _write_json,
    _youtube_run_root,
)


_GEMINI_URL_RE = re.compile(
    r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$",
    re.IGNORECASE,
)
_SELECTION_BOT_KEY = "youtube_selection"
_LEGACY_RUNNER_SUBDIR = "legacy/youtube_selection"
_BRIDGE_ROOT_SUBDIR = "_orchestrator_runs"
_CHAIN_FILE = "gemini_bots_selection.json"
_INFO_FILE_NAME = "info.txt"
_NO_ACCOUNTS_SENTINEL = "_no_accounts.txt"


def _safe_name_for_file(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (value or "story").strip())
    return cleaned[:120] or "story"


def _legacy_runner_dir(root_dir: Path) -> Path:
    return (root_dir / _LEGACY_RUNNER_SUBDIR).resolve()


def _bridge_run_root(root_dir: Path, youtube_run_id: str) -> Path:
    """Каталог под legacy/youtube_selection/_orchestrator_runs/<youtube_run_id>/ — все артефакты bridge живут тут."""
    return (root_dir / _LEGACY_RUNNER_SUBDIR / _BRIDGE_ROOT_SUBDIR / youtube_run_id).resolve()


def _bridge_stories_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return _bridge_run_root(root_dir, youtube_run_id) / "stories"


def _bridge_trash_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return _bridge_run_root(root_dir, youtube_run_id) / "trash"


def _bridge_user_data_dir(root_dir: Path, youtube_run_id: str, story_id: str) -> Path:
    return _bridge_run_root(root_dir, youtube_run_id) / "user_data" / _safe_name_for_file(story_id)


def _bridge_log_path(root_dir: Path, youtube_run_id: str, story_id: str) -> Path:
    return _bridge_run_root(root_dir, youtube_run_id) / "logs" / f"legacy_selection__{_safe_name_for_file(story_id)}.log"


def _bridge_parallel_state_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return _bridge_run_root(root_dir, youtube_run_id) / "parallel_state"


def _bridge_accounts_sentinel(root_dir: Path, youtube_run_id: str) -> Path:
    return _bridge_run_root(root_dir, youtube_run_id) / _NO_ACCOUNTS_SENTINEL


def _legacy_user_data_dir(root_dir: Path) -> Path:
    return (_legacy_runner_dir(root_dir) / "user_data").resolve()


def _find_user_data_for_email(root_dir: Path, email: str) -> tuple[Path | None, str]:
    """Подбирает user_data*-папку в legacy/youtube_selection/ под нужный email (по account_info из Chrome Preferences)."""
    target = (email or "").strip().lower()
    if not target:
        return None, "empty_email"
    base = _legacy_runner_dir(root_dir)
    candidates: list[Path] = []
    if base.is_dir():
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and (entry.name == "user_data" or entry.name.startswith("user_data_")):
                candidates.append(entry)
    for ud in candidates:
        try:
            em = _read_profile_email(ud).lower()
        except Exception:
            em = ""
        if em == target:
            return ud, em
    return None, "no_match"


def _staging_story_dir(root_dir: Path, youtube_run_id: str, story_id: str) -> Path:
    """Leaf story folder, ровно одна на bridge-run. legacy/youtube_selection/_orchestrator_runs/<run>/stories/<story>/"""
    return _bridge_stories_dir(root_dir, youtube_run_id) / _safe_name_for_file(story_id)


def _chain_dir(root_dir: Path, youtube_run_id: str) -> Path:
    return (_gemini_selection_dir(root_dir, youtube_run_id) / "_chain").resolve()


def _bridge_status_path(root_dir: Path, youtube_run_id: str) -> Path:
    return (_gemini_selection_dir(root_dir, youtube_run_id) / "selection_bridge_status.json").resolve()


def _registry_candidates(root_dir: Path) -> list[Path]:
    return [
        (root_dir / "configs" / "gemini_bots_registry.yaml").resolve(),
        (root_dir / "configs" / "gemini_bots_registry.example.yaml").resolve(),
    ]


def _pick_selection_bot(root_dir: Path, account_index: int) -> tuple[str, str, str, str]:
    """(email, url, registry_path, key). account_index — индекс валидного аккаунта (0-based)."""
    registry_path = ""
    bots: list[dict[str, Any]] = []
    for cand in _registry_candidates(root_dir):
        if cand.is_file():
            loaded = _load_gemini_registry(cand)
            if loaded:
                registry_path = str(cand)
                bots = loaded
                break
    if not bots:
        raise RuntimeError(
            "Не найден или пуст gemini registry: configs/gemini_bots_registry.yaml / .example.yaml"
        )

    valid: list[tuple[str, str]] = []
    for bot in bots:
        if not isinstance(bot, dict):
            continue
        url = str(bot.get(_SELECTION_BOT_KEY, "")).strip()
        if url and _GEMINI_URL_RE.fullmatch(url):
            email = str(bot.get("email", "")).strip()
            valid.append((email, url))

    if not valid:
        raise RuntimeError(
            f"В {registry_path or 'registry'} нет ни одного валидного URL для ключа '{_SELECTION_BOT_KEY}'."
        )

    idx = max(0, min(account_index, len(valid) - 1))
    email, url = valid[idx]
    return email, url, registry_path, _SELECTION_BOT_KEY


def _load_selection_input_manifest(root_dir: Path, youtube_run_id: str) -> tuple[Path, list[dict[str, Any]], str]:
    gs_dir = _gemini_selection_dir(root_dir, youtube_run_id)
    manifest = gs_dir / "input" / "gemini_selection_input_manifest.json"
    if not manifest.is_file():
        return manifest, [], f"manifest not found: {manifest}"
    try:
        data = _read_json(manifest)
    except Exception as exc:
        return manifest, [], f"manifest unreadable: {exc}"
    items = data.get("items", []) if isinstance(data, dict) else []
    items = [it for it in items if isinstance(it, dict)]
    return manifest, items, ""


def _match_item(items: list[dict[str, Any]], *, input_id: str, story_key: str) -> tuple[dict[str, Any] | None, str]:
    iid = input_id.strip()
    sk = story_key.strip()
    if iid:
        matches = [it for it in items if str(it.get("item_id", "")).strip() == iid]
        if not matches:
            return None, f"input-id={iid!r} не найден в gemini_selection_input_manifest.json"
        if len(matches) > 1:
            return None, f"input-id={iid!r}: найдено больше одного совпадения ({len(matches)})"
        return matches[0], ""
    if sk:
        key = sk.casefold()
        matches: list[dict[str, Any]] = []
        for it in items:
            can = str(it.get("canonical_basename", "")).strip()
            sp = str(it.get("source_path", "")).strip()
            stem = Path(sp).stem if sp else ""
            if (
                can.casefold() == key
                or stem.casefold() == key
                or str(it.get("item_id", "")).casefold() == key
            ):
                matches.append(it)
        if not matches:
            return None, f"story-id={story_key!r} не найден ни по item_id, ни по canonical_basename"
        if len(matches) > 1:
            return None, f"story-id={story_key!r}: больше одного совпадения ({len(matches)}), уточните --input-id"
        return matches[0], ""
    return None, "нужно --input-id или --story-id"


def _write_bridge_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)


def _write_chain_json(chain_dir: Path, email: str, url: str) -> Path:
    chain_dir.mkdir(parents=True, exist_ok=True)
    entry = {"email": email, "url": url}
    out = chain_dir / _CHAIN_FILE
    out.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
    return out.resolve()


def _build_manual_cmd(
    *,
    runner_dir: Path,
    stories_dir: str,
    trash_dir: str,
    user_data_dir: str,
    log_file: str,
    parallel_state_dir: str,
    accounts_file: str,
    gemini_url: str,
) -> str:
    py = sys.executable
    ga = runner_dir / "gemini_auto.py"
    return (
        f'cd /d "{runner_dir}" && '
        f'set "GEMINI_STORIES_DIR={stories_dir}" && '
        f'set "GEMINI_TRASH_DIR={trash_dir}" && '
        f'set "GEMINI_USER_DATA_DIR={user_data_dir}" && '
        f'set "GEMINI_LOG_FILE={log_file}" && '
        f'set "GEMINI_PARALLEL_STATE_DIR={parallel_state_dir}" && '
        f'set "GEMINI_ACCOUNTS_FILE={accounts_file}" && '
        f'set "GEMINI_URL={gemini_url}" && '
        f'"{py}" "{ga}"'
    )


_LEGACY_GENERATED_TXT_RE = re.compile(r"^result_report(-\d+)?\.txt$", re.IGNORECASE)


def _is_legacy_generated_txt(name: str) -> bool:
    n = (name or "").strip().lower()
    if n in ("info.txt", "genre_report.txt"):
        return True
    return bool(_LEGACY_GENERATED_TXT_RE.fullmatch(name or ""))


def _prepare_staging(
    *, root_dir: Path, item: dict[str, Any], staging_dir: Path, force: bool
) -> tuple[Path | None, Path | None, str]:
    """Кладёт ровно один story.txt и возвращает (input_txt_path, effective_leaf_dir, error).

    После прошлого запуска legacy normalize_top_level_genre_txt_files мог переместить файл
    в staging_dir/<stem>/<stem>.txt — это нормально, используем как effective_leaf_dir.
    Служебные файлы legacy (info.txt/result_report*/genre_report.txt) допустимы.
    """
    source_text_path = Path(str(item.get("input_txt_path", "")).strip())
    if not source_text_path.is_file():
        alt = Path(str(item.get("resolved_cleaned_path", "")).strip())
        if not alt.is_file():
            return None, None, f"Не найден input txt: {source_text_path} (и resolved_cleaned_path тоже отсутствует)"
        source_text_path = alt

    staging_dir.mkdir(parents=True, exist_ok=True)
    canonical = str(item.get("canonical_basename", "")).strip() or "story"
    stem = _safe_name_for_file(canonical)
    target_flat = staging_dir / f"{stem}.txt"
    nested_dir = staging_dir / stem
    target_nested = nested_dir / f"{stem}.txt"

    def _existing_story_txts(folder: Path) -> list[Path]:
        if not folder.is_dir():
            return []
        return sorted(
            [p for p in folder.glob("*.txt") if p.is_file() and not _is_legacy_generated_txt(p.name)],
            key=lambda x: x.name.lower(),
        )

    if target_nested.is_file() and not force:
        extras = [p for p in _existing_story_txts(nested_dir) if p != target_nested]
        if extras:
            return None, None, f"В nested staging уже есть лишние story .txt: {[p.name for p in extras]} (используйте --force)"
        extras_flat = [p for p in _existing_story_txts(staging_dir) if p != target_flat]
        if extras_flat:
            return None, None, f"В staging есть лишние story .txt в корне: {[p.name for p in extras_flat]} (используйте --force)"
        return target_nested, nested_dir, ""

    if target_flat.is_file() and not force:
        extras = [p for p in _existing_story_txts(staging_dir) if p != target_flat]
        if extras:
            return None, None, f"В staging уже есть лишние story .txt: {[p.name for p in extras]} (используйте --force)"
        return target_flat, staging_dir, ""

    for p in _existing_story_txts(staging_dir):
        if p != target_flat:
            return None, None, (
                f"В staging есть посторонние story .txt ({p.name}). "
                f"Удалите вручную или укажите другой --youtube-run-id."
            )

    shutil.copy2(source_text_path, target_flat)
    return target_flat, staging_dir, ""


def _collect_leaf_story_folders(stories_dir: Path) -> list[Path]:
    """Тот же отбор leaf-папок, что collect_story_folders в legacy/youtube_selection (без playwright)."""
    if not stories_dir.is_dir():
        return []
    all_dirs = [p for p in stories_dir.rglob("*") if p.is_dir()]
    with_txt = [
        d
        for d in all_dirs
        if any(c.is_file() and c.suffix.lower() == ".txt" and not c.name.lower().startswith(("info.", "result_report"))
               for c in d.glob("*.txt"))
    ]
    with_set = set(with_txt)
    leafs = [d for d in with_txt if d not in {c.parent for c in with_set}]
    return sorted(leafs, key=lambda x: str(x).lower())


def _find_info_after_run(
    *, staging_dir: Path, trash_dir: Path, story_safe_name: str
) -> tuple[Path | None, str]:
    """info.txt либо остался в staging (YES), либо уехал в trash (NO). Возвращает (info_path, location_tag)."""
    in_staging = staging_dir / _INFO_FILE_NAME
    if in_staging.is_file():
        return in_staging, "staging"
    nested = staging_dir / story_safe_name / _INFO_FILE_NAME
    if nested.is_file():
        return nested, "staging_nested"
    under_staging = sorted(
        [p for p in staging_dir.rglob(_INFO_FILE_NAME) if p.is_file()],
        key=lambda x: str(x).lower(),
    )
    if len(under_staging) == 1:
        return under_staging[0], "staging_rglob"
    if len(under_staging) > 1:
        for p in under_staging:
            if p.parent.name == story_safe_name:
                return p, "staging_rglob_match"
    if trash_dir.is_dir():
        candidates = sorted(
            [p for p in trash_dir.rglob(_INFO_FILE_NAME) if p.is_file()],
            key=lambda x: str(x).lower(),
        )
        for c in candidates:
            if c.parent.name == story_safe_name:
                return c, "trash_match_by_name"
        if candidates:
            return candidates[0], "trash_first_info"
    return None, "missing"


def _extract_verdict(info_text: str) -> str:
    """Best-effort: «подходит для YouTube: да|нет» → 'yes'|'no'|'unknown'. Подробный парс делает downstream parse-gemini-selection."""
    if not info_text:
        return "unknown"
    yt_re = re.compile(
        r"^\s*\**\s*подходит\s+для\s+youtube[\s:]+\**\s*(да|нет)\b",
        re.IGNORECASE | re.MULTILINE,
    )
    m = yt_re.search(info_text.replace("\r\n", "\n"))
    if not m:
        return "unknown"
    return "yes" if m.group(1).lower() == "да" else "no"


def _import_selection_output(
    *,
    root_dir: Path,
    youtube_run_id: str,
    item: dict[str, Any],
    staging_dir: Path,
    trash_dir: Path,
    story_safe_name: str,
) -> tuple[bool, str, Path | None, str]:
    """Ищет info.txt в staging или trash, копирует в raw + expected_gemini_output_text. (ok, msg, out_path, verdict)."""
    info_path, location = _find_info_after_run(
        staging_dir=staging_dir, trash_dir=trash_dir, story_safe_name=story_safe_name
    )
    if info_path is None:
        return False, "missing_info_txt", None, "unknown"

    info_text = _read_text(info_path)
    verdict = _extract_verdict(info_text)

    raw_dir = _gemini_selection_dir(root_dir, youtube_run_id) / "raw"
    out_dir = _gemini_selection_dir(root_dir, youtube_run_id) / "output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    item_id = str(item.get("item_id", "")).strip() or "yt_00000"
    raw_path = raw_dir / f"{item_id}__raw.txt"
    out_path = out_dir / f"{item_id}__result.txt"
    expected = str(item.get("expected_gemini_output_text", "")).strip()
    if expected:
        out_path = Path(expected).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

    raw_path.write_text(info_text, encoding="utf-8")
    out_path.write_text(info_text, encoding="utf-8")
    return True, f"imported_from_{location}", out_path, verdict


@dataclass
class YoutubeRunSelectionBridgeOptions:
    youtube_run_id: str
    input_id: str = ""
    story_id: str = ""
    execute: bool = False
    force: bool = False
    reuse_legacy_user_data: bool = False
    account_index: int = 0
    user_data_dir: str = ""


def run_youtube_run_selection_bridge(
    *, config: OrchestratorConfig, options: YoutubeRunSelectionBridgeOptions
) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    youtube_run_id = options.youtube_run_id.strip()
    if not youtube_run_id:
        return {"ok": False, "message": "--youtube-run-id обязателен"}
    if not options.input_id.strip() and not options.story_id.strip():
        return {"ok": False, "message": "Нужен один из: --input-id или --story-id"}
    if options.input_id.strip() and options.story_id.strip():
        return {"ok": False, "message": "Используйте только один из: --input-id ИЛИ --story-id"}

    run_root = _youtube_run_root(root_dir, youtube_run_id)
    status_jsonl = run_root / "youtube_status.jsonl"
    bridge_status_path = _bridge_status_path(root_dir, youtube_run_id)

    manifest_path, items, err = _load_selection_input_manifest(root_dir, youtube_run_id)
    if err:
        return {"ok": False, "message": err}
    if not items:
        return {"ok": False, "message": f"В {manifest_path} нет items"}

    item, match_err = _match_item(items, input_id=options.input_id, story_key=options.story_id)
    if item is None:
        return {"ok": False, "message": match_err}

    item_id = str(item.get("item_id", "")).strip()
    canonical = str(item.get("canonical_basename", "")).strip() or "story"
    story_id = canonical
    story_safe_name = _safe_name_for_file(story_id)
    expected_output = str(item.get("expected_gemini_output_text", "")).strip()
    duration_gate = str(item.get("duration_gate", "") or "").strip().upper()
    youtube_size_status = str(item.get("youtube_size_status", "") or "").strip().lower()
    if duration_gate != "PASS" or youtube_size_status != "yes":
        return {
            "ok": False,
            "message": "story failed local youtube duration gate; not sending to Gemini",
            "item_id": item_id,
            "canonical_basename": canonical,
            "duration_gate": duration_gate or "UNKNOWN",
            "youtube_size_status": youtube_size_status or "unknown",
            "fail_reason": item.get("fail_reason") or item.get("reject_reason") or "",
            "word_count": item.get("word_count", 0),
            "estimated_tts_minutes": item.get("estimated_tts_minutes", item.get("estimated_minutes", 0)),
            "words_per_minute": item.get("words_per_minute", 150),
            "duration_contract": (
                f"{item.get('min_minutes', '')}-{item.get('max_minutes', '')} min "
                f"@{item.get('words_per_minute', 150)} wpm"
            ),
        }

    try:
        email, url, registry_path, bot_key = _pick_selection_bot(root_dir, options.account_index)
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    runner_dir = _legacy_runner_dir(root_dir)
    gemini_auto = runner_dir / "gemini_auto.py"
    if not gemini_auto.is_file():
        return {"ok": False, "message": f"Не найден legacy runner: {gemini_auto}"}

    bridge_run_root = _bridge_run_root(root_dir, youtube_run_id)
    bridge_stories = _bridge_stories_dir(root_dir, youtube_run_id)
    bridge_trash = _bridge_trash_dir(root_dir, youtube_run_id)
    bridge_parallel_state = _bridge_parallel_state_dir(root_dir, youtube_run_id)
    bridge_accounts = _bridge_accounts_sentinel(root_dir, youtube_run_id)
    bridge_log = _bridge_log_path(root_dir, youtube_run_id, story_id)
    bridge_run_root.mkdir(parents=True, exist_ok=True)
    bridge_stories.mkdir(parents=True, exist_ok=True)
    bridge_trash.mkdir(parents=True, exist_ok=True)
    bridge_parallel_state.mkdir(parents=True, exist_ok=True)
    bridge_log.parent.mkdir(parents=True, exist_ok=True)

    user_data_source = "isolated"
    if options.user_data_dir.strip():
        user_data = Path(options.user_data_dir).expanduser().resolve()
        if not user_data.is_dir():
            return {"ok": False, "message": f"--user-data-dir не существует: {user_data}"}
        user_data_source = "explicit"
    elif options.reuse_legacy_user_data:
        user_data = _legacy_user_data_dir(root_dir)
        user_data_source = "legacy_default"
    else:
        auto, _why = _find_user_data_for_email(root_dir, email)
        if auto is not None:
            user_data = auto
            user_data_source = f"auto_match_email:{user_data.name}"
        else:
            user_data = _bridge_user_data_dir(root_dir, youtube_run_id, story_id)
            user_data.mkdir(parents=True, exist_ok=True)
            user_data_source = "isolated_fallback"

    staging = _staging_story_dir(root_dir, youtube_run_id, story_id)
    staged, effective_leaf, prep_err = _prepare_staging(
        root_dir=root_dir, item=item, staging_dir=staging, force=bool(options.force)
    )
    if staged is None or effective_leaf is None:
        return {"ok": False, "message": prep_err}

    leafs = _collect_leaf_story_folders(bridge_stories)
    if len(leafs) != 1:
        return {
            "ok": False,
            "message": (
                "Для single-input GEMINI_STORIES_DIR должен содержать ровно одну leaf-папку с одним .txt. "
                f"Сейчас leafs={[str(p) for p in leafs]}."
            ),
        }
    detected_leaf = leafs[0].resolve()
    if detected_leaf != effective_leaf.resolve() and detected_leaf != staging.resolve():
        return {
            "ok": False,
            "message": (
                f"Detected leaf {detected_leaf} не совпадает ни с effective_leaf {effective_leaf}, "
                f"ни со staging_dir {staging}."
            ),
        }
    effective_leaf = detected_leaf

    chain_dir = _chain_dir(root_dir, youtube_run_id)
    chain_json = _write_chain_json(chain_dir, email or "", url)

    gemini_stories_dir = str(bridge_stories.resolve())
    gemini_trash_dir = str(bridge_trash.resolve())
    gemini_user_data_dir = str(user_data.resolve())
    gemini_log_file = str(bridge_log.resolve())
    gemini_parallel_state_dir = str(bridge_parallel_state.resolve())
    gemini_accounts_file = str(bridge_accounts.resolve())  # sentinel, не существует → legacy уйдёт на GEMINI_URL
    gemini_url = url
    input_txt_path = Path(str(item.get("input_txt_path", "")).strip())
    metadata_header_present = _has_youtube_prefilter_header(input_txt_path)

    manual_cmd = _build_manual_cmd(
        runner_dir=runner_dir,
        stories_dir=gemini_stories_dir,
        trash_dir=gemini_trash_dir,
        user_data_dir=gemini_user_data_dir,
        log_file=gemini_log_file,
        parallel_state_dir=gemini_parallel_state_dir,
        accounts_file=gemini_accounts_file,
        gemini_url=gemini_url,
    )

    common_payload: dict[str, Any] = {
        "youtube_run_id": youtube_run_id,
        "item_id": item_id,
        "canonical_basename": canonical,
        "story_id": story_id,
        "story_safe_name": story_safe_name,
        "registry_path": registry_path,
        "bot_key": bot_key,
        "bot_account_email": email,
        "bot_url": url,
        "staging_input_txt": str(staged.resolve()),
        "staging_dir": str(staging.resolve()),
        "effective_leaf_dir": str(effective_leaf.resolve()),
        "bridge_run_root": str(bridge_run_root),
        "gemini_stories_dir": gemini_stories_dir,
        "gemini_trash_dir": gemini_trash_dir,
        "gemini_user_data_dir": gemini_user_data_dir,
        "gemini_log_file": gemini_log_file,
        "gemini_parallel_state_dir": gemini_parallel_state_dir,
        "gemini_accounts_file": gemini_accounts_file,
        "gemini_bots_config": str(chain_json),
        "gemini_url": gemini_url,
        "expected_gemini_output_text": expected_output,
        "word_count": item.get("word_count", 0),
        "estimated_tts_minutes": item.get("estimated_tts_minutes", item.get("estimated_minutes", 0)),
        "estimated_minutes": item.get("estimated_minutes", 0),
        "duration_gate": duration_gate,
        "duration_contract": (
            f"{item.get('min_minutes', '')}-{item.get('max_minutes', '')} min "
            f"@{item.get('words_per_minute', 150)} wpm"
        ),
        "min_minutes": item.get("min_minutes", 30),
        "max_minutes": item.get("max_minutes", 80),
        "words_per_minute": item.get("words_per_minute", 150),
        "min_words": item.get("min_words", 4000),
        "max_words": item.get("max_words", 12000),
        "metadata_header_present": metadata_header_present,
        "manual_cmd_windows": manual_cmd,
        "reuse_legacy_user_data": bool(options.reuse_legacy_user_data),
        "user_data_source": user_data_source,
        "selection_bot_log": str(bridge_log),
        "legacy_runner": str(gemini_auto),
    }

    if not options.execute:
        payload = {
            **common_payload,
            "status": "selection_needs_manual_interaction",
            "execute": False,
            "result_exists": Path(expected_output).is_file() if expected_output else False,
            "note": (
                "Без --execute оркестратор не запускает Playwright. "
                "Либо запустите manual_cmd_windows, либо повторите команду с --execute."
            ),
            "updated_at": _now_iso(),
        }
        _write_bridge_status(bridge_status_path, payload)
        _append_status(
            status_jsonl,
            {
                "timestamp": _now_iso(),
                "youtube_run_id": youtube_run_id,
                "item_id": item_id,
                "story_id": story_id,
                "stage": "youtube_run_selection_bridge",
                "state": "preflight_ok_needs_manual_interaction",
            },
        )
        return {
            "ok": True,
            "skipped_subprocess": True,
            **common_payload,
            "selection_bridge_status": str(bridge_status_path),
            "message": (
                "Реальный gemini_auto (legacy/youtube_selection) не запускался (нет --execute). "
                "Чтобы запустить single-input — повторите с --execute или выполните manual_cmd_windows."
            ),
        }

    started = _now_iso()
    _write_bridge_status(
        bridge_status_path,
        {
            **common_payload,
            "status": "selection_bot_started",
            "execute": True,
            "started_at": started,
            "selection_done": False,
        },
    )
    _append_status(
        status_jsonl,
        {
            "timestamp": started,
            "youtube_run_id": youtube_run_id,
            "item_id": item_id,
            "story_id": story_id,
            "stage": "youtube_run_selection_bridge",
            "state": "selection_bot_started",
        },
    )

    env = os.environ.copy()
    env["GEMINI_STORIES_DIR"] = gemini_stories_dir
    env["GEMINI_TRASH_DIR"] = gemini_trash_dir
    env["GEMINI_USER_DATA_DIR"] = gemini_user_data_dir
    env["GEMINI_LOG_FILE"] = gemini_log_file
    env["GEMINI_PARALLEL_STATE_DIR"] = gemini_parallel_state_dir
    env["GEMINI_ACCOUNTS_FILE"] = gemini_accounts_file
    env["GEMINI_URL"] = gemini_url
    env["FILES_PER_DIALOG"] = "999"
    env["FILES_PER_ACCOUNT"] = "0"
    # selection-runner не читает GEMINI_NON_INTERACTIVE; subprocess.stdin=DEVNULL ниже превращает все
    # prompt_user в EOF → безопасный fallback.

    exit_code: int | None = None
    try:
        with bridge_log.open("w", encoding="utf-8", errors="replace") as logf:
            logf.write(f"[orchestrator] cwd={runner_dir}\n")
            logf.write(f"[orchestrator] GEMINI_STORIES_DIR={gemini_stories_dir}\n")
            logf.write(f"[orchestrator] GEMINI_TRASH_DIR={gemini_trash_dir}\n")
            logf.write(f"[orchestrator] GEMINI_USER_DATA_DIR={gemini_user_data_dir}\n")
            logf.write(f"[orchestrator] GEMINI_LOG_FILE={gemini_log_file}\n")
            logf.write(f"[orchestrator] GEMINI_PARALLEL_STATE_DIR={gemini_parallel_state_dir}\n")
            logf.write(f"[orchestrator] GEMINI_ACCOUNTS_FILE={gemini_accounts_file}\n")
            logf.write(f"[orchestrator] GEMINI_URL={gemini_url}\n")
            logf.write(f"[orchestrator] bot_account_email={email}\n")
            logf.write(f"[orchestrator] staging_input={staged}\n")
            logf.flush()
            proc = subprocess.run(
                [sys.executable, str(gemini_auto)],
                cwd=str(runner_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=None,
            )
            exit_code = int(proc.returncode) if proc.returncode is not None else 0
    except Exception as exc:
        exit_code = -1
        with bridge_log.open("a", encoding="utf-8", errors="replace") as logf:
            logf.write(f"\n[orchestrator] subprocess exception: {exc}\n")

    finished = _now_iso()
    failed = exit_code != 0

    post_leafs = _collect_leaf_story_folders(bridge_stories)
    import_staging = post_leafs[0] if len(post_leafs) == 1 else effective_leaf
    ok, import_msg, out_path, verdict = _import_selection_output(
        root_dir=root_dir,
        youtube_run_id=youtube_run_id,
        item=item,
        staging_dir=import_staging,
        trash_dir=bridge_trash,
        story_safe_name=story_safe_name,
    )

    if ok and not failed:
        status_label = "selection_done"
    elif ok and failed:
        status_label = "selection_done_with_subprocess_warning"
    elif failed and not ok:
        status_label = "selection_bot_failed"
    else:
        status_label = "selection_missing_output"

    status_payload = {
        **common_payload,
        "status": status_label,
        "execute": True,
        "started_at": started,
        "finished_at": finished,
        "gemini_auto_exit_code": exit_code,
        "selection_done": bool(ok),
        "imported_to": str(out_path) if out_path else "",
        "import_message": import_msg,
        "verdict": verdict,
    }
    _write_bridge_status(bridge_status_path, status_payload)
    _append_status(
        status_jsonl,
        {
            "timestamp": finished,
            "youtube_run_id": youtube_run_id,
            "item_id": item_id,
            "story_id": story_id,
            "stage": "youtube_run_selection_bridge",
            "state": status_label,
            "gemini_auto_exit_code": exit_code,
            "verdict": verdict,
        },
    )
    return {
        "ok": ok and not failed,
        "skipped_subprocess": False,
        **common_payload,
        "gemini_auto_exit_code": exit_code,
        "imported_to": str(out_path) if out_path else "",
        "import_message": import_msg,
        "verdict": verdict,
        "selection_bridge_status": str(bridge_status_path),
        "message": (
            "Selection bridge завершён."
            if ok and not failed
            else "Selection bridge завершён с ошибкой или без output (см. selection_bot_log)."
        ),
    }
