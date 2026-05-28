"""YouTube safe-rewrite bridge: staging under legacy/youtube_tts/stories_from_orchestrator + import *_clean.txt (single-story, manifest-driven)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_language import EXPECTED_YOUTUBE_LANGUAGE, detect_path_language
from orchestrator.youtube_bridge_manifest import _legacy_bridge_paths
from orchestrator.youtube_from_site import (
    _append_status,
    _now_iso,
    _read_json,
    _read_text,
    _write_json,
    _youtube_run_root,
    strip_youtube_prefilter_header,
)


def _stories_input_root(root_dir: Path) -> Path:
    return (root_dir / "stories" / "input").resolve()


def _is_under_dir(child: Path, parent: Path) -> bool:
    try:
        c = child.resolve()
        p = parent.resolve()
        c.relative_to(p)
        return True
    except (ValueError, OSError):
        return False


def _is_forbidden_stories_input(root_dir: Path, candidate: Path) -> bool:
    return _is_under_dir(candidate, _stories_input_root(root_dir))


def _run_manifest_path(root_dir: Path, youtube_run_id: str) -> Path:
    return _youtube_run_root(root_dir, youtube_run_id) / "youtube_bridge_manifest.json"


def _load_run_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _load_story_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _story_entry_matches(entry: dict[str, Any], story_key: str) -> bool:
    key = story_key.strip()
    if not key:
        return False
    sid = str(entry.get("story_id", "")).strip()
    can = str(entry.get("canonical_basename", "")).strip()
    if sid and sid == key:
        return True
    if can and can == key:
        return True
    if can and can.casefold() == key.casefold():
        return True
    return False


def _find_single_story_entry(run_manifest: dict[str, Any], story_key: str) -> tuple[dict[str, Any] | None, str]:
    stories = [x for x in run_manifest.get("stories", []) if isinstance(x, dict)]
    matches = [s for s in stories if _story_entry_matches(s, story_key)]
    if len(matches) == 0:
        return None, f"История не найдена в youtube_bridge_manifest.json по story_id/canonical: {story_key!r}"
    if len(matches) > 1:
        return None, f"Найдено больше одной истории ({len(matches)}) по ключу {story_key!r}; уточните story_id."
    return matches[0], ""


def _story_dir_from_manifest(manifest: dict[str, Any]) -> Path | None:
    yo = manifest.get("youtube_outputs")
    if isinstance(yo, dict):
        sd = str(yo.get("story_dir", "")).strip()
        if sd:
            return Path(sd)
    return None


def _verify_guards(manifest: dict[str, Any]) -> str | None:
    g = manifest.get("guards")
    if not isinstance(g, dict):
        return "В story manifest отсутствует блок guards (пересоберите build-bridge-manifest)."
    if g.get("single_story_only") is not True:
        return "guards.single_story_only должен быть true."
    if g.get("forbidden_root_scan") is not True:
        return "guards.forbidden_root_scan должен быть true."
    if int(g.get("expected_story_count", 0) or 0) != 1:
        return "guards.expected_story_count должен быть 1."
    return None


def _resolve_safe_input_file(*, root_dir: Path, story_dir: Path, manifest: dict[str, Any]) -> tuple[Path | None, str]:
    src_block = manifest.get("source")
    resolved_str = ""
    if isinstance(src_block, dict):
        resolved_str = str(src_block.get("resolved_cleaned_path", "")).strip()

    cleaned_in_story = story_dir / "00_source" / "source_cleaned_story.txt"
    if cleaned_in_story.is_file():
        if _is_forbidden_stories_input(root_dir, cleaned_in_story):
            return None, "00_source/source_cleaned_story.txt указывает под stories/input — запрещено."
        return cleaned_in_story, "00_source/source_cleaned_story.txt"

    if resolved_str:
        p = Path(resolved_str)
        if not p.is_file():
            return None, f"manifest.source.resolved_cleaned_path не существует: {p}"
        if _is_forbidden_stories_input(root_dir, p):
            return None, "manifest.source.resolved_cleaned_path попадает под stories/input — запрещено (используйте site cleaned / 00_source)."
        return p, "manifest.source.resolved_cleaned_path"

    return None, "Нет входа: отсутствует 00_source/source_cleaned_story.txt и resolved_cleaned_path."


def _staging_dir_from_manifest(manifest: dict[str, Any], root_dir: Path, youtube_run_id: str, story_id: str) -> Path:
    lb = manifest.get("legacy_bridge")
    if isinstance(lb, dict):
        raw = str(lb.get("youtube_safe_story_dir", "")).strip()
        if raw:
            return Path(raw).resolve()
    return Path(_legacy_bridge_paths(root_dir, youtube_run_id, story_id)["youtube_safe_story_dir"]).resolve()


def _safe_name_for_file(story_id: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (story_id or "story").strip())
    return s[:120] or "story"


def _list_clean_outputs(staging_dir: Path) -> list[Path]:
    if not staging_dir.is_dir():
        return []
    return sorted(staging_dir.glob("*_clean.txt"), key=lambda p: p.name.lower())


def _pick_clean_file(staging_dir: Path, story_id: str) -> Path | None:
    files = _list_clean_outputs(staging_dir)
    if not files:
        return None
    preferred = staging_dir / f"{_safe_name_for_file(story_id)}_clean.txt"
    if preferred in files:
        return preferred
    return files[0]


def _list_staging_source_txt(staging_dir: Path) -> list[Path]:
    """Один «исходник» для gemini_auto: те же правила, что list_story_source_files."""
    return _list_gemini_style_story_txts(staging_dir)


def _verify_staging_parent_single_story(staging_dir: Path) -> str | None:
    """GEMINI_STORIES_DIR = родитель staging; в нём должна быть ровно одна подпапка — наша staging."""
    parent = staging_dir.resolve().parent
    if not parent.is_dir():
        return f"Родитель staging не существует: {parent}"
    subdirs = [p for p in parent.iterdir() if p.is_dir()]
    if len(subdirs) != 1:
        return (
            "Для single-story GEMINI_STORIES_DIR (родитель staging) должен содержать ровно одну подпапку; "
            f"сейчас подпапок: {len(subdirs)} ({[str(s) for s in subdirs]})"
        )
    if subdirs[0].resolve() != staging_dir.resolve():
        return f"Единственная подпапка {subdirs[0]} не совпадает с youtube_safe_story_dir {staging_dir}"
    return None


def _patch_story_manifest_status(sm_path: Path, **status_updates: Any) -> None:
    m = _load_story_manifest(sm_path)
    st = m.get("status") if isinstance(m.get("status"), dict) else {}
    for k, v in status_updates.items():
        st[k] = v
    m["status"] = st
    _write_json(sm_path, m)


def _refresh_run_safe_summary(*, root_dir: Path, youtube_run_id: str) -> None:
    run_path = _run_manifest_path(root_dir, youtube_run_id)
    run_m = _load_run_manifest(run_path)
    if not run_m:
        return

    staged = 0
    done = 0
    missing = 0
    bot_failed = 0
    needs_manual = 0
    for entry in run_m.get("stories", []):
        if not isinstance(entry, dict):
            continue
        sm_path = str(entry.get("story_manifest", "")).strip()
        if not sm_path:
            continue
        sm = _load_story_manifest(Path(sm_path))
        st = sm.get("status") if isinstance(sm.get("status"), dict) else {}
        if st.get("safe_staged") is True:
            staged += 1
        if st.get("safe_done") is True:
            done += 1
        log_p = Path(sm_path).parent / "logs" / "safe_bridge_status.json"
        if log_p.is_file():
            try:
                row = _read_json(log_p)
                if isinstance(row, dict):
                    stt = str(row.get("status", "")).strip()
                    if stt == "missing_safe_output":
                        missing += 1
                    if stt == "safe_bot_failed":
                        bot_failed += 1
                    if stt == "safe_bot_needs_manual_interaction":
                        needs_manual += 1
            except Exception:
                pass

    summary = run_m.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    summary = {
        **summary,
        "safe_bridge": {
            "safe_staged": staged,
            "safe_done": done,
            "missing_safe_output": missing,
            "safe_bot_failed": bot_failed,
            "safe_bot_needs_manual_interaction": needs_manual,
        },
    }
    run_m["summary"] = summary
    _write_json(run_path, run_m)


def _write_safe_bridge_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)


@dataclass
class YoutubePrepareSafeBridgeOptions:
    youtube_run_id: str
    story_id: str
    force: bool = False


def run_youtube_prepare_safe_bridge(*, config: OrchestratorConfig, options: YoutubePrepareSafeBridgeOptions) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    youtube_run_id = str(options.youtube_run_id).strip()
    story_key = str(options.story_id).strip()
    if not youtube_run_id or not story_key:
        return {"ok": False, "message": "Нужны --youtube-run-id и --story-id"}

    run_path = _run_manifest_path(root_dir, youtube_run_id)
    run_m = _load_run_manifest(run_path)
    if not run_m:
        return {"ok": False, "message": f"Нет run manifest: {run_path}"}

    entry, err = _find_single_story_entry(run_m, story_key)
    if entry is None:
        return {"ok": False, "message": err}

    sm_path = Path(str(entry.get("story_manifest", "")).strip())
    if not sm_path.is_file():
        return {"ok": False, "message": f"Story manifest не найден: {sm_path}"}

    manifest = _load_story_manifest(sm_path)
    if not manifest:
        return {"ok": False, "message": f"Пустой или битый story manifest: {sm_path}"}

    gerr = _verify_guards(manifest)
    if gerr:
        return {"ok": False, "message": gerr}

    story_dir = _story_dir_from_manifest(manifest)
    if story_dir is None:
        return {"ok": False, "message": "В manifest нет youtube_outputs.story_dir"}

    sid = str(manifest.get("story_id", "")).strip() or str(entry.get("story_id", "")).strip()
    if not sid:
        return {"ok": False, "message": "В manifest отсутствует story_id"}

    inp, inp_reason = _resolve_safe_input_file(root_dir=root_dir, story_dir=story_dir, manifest=manifest)
    if inp is None:
        return {"ok": False, "message": inp_reason}

    staging_dir = _staging_dir_from_manifest(manifest, root_dir, youtube_run_id, sid)
    staging_dir.mkdir(parents=True, exist_ok=True)

    stem = _safe_name_for_file(sid)
    staging_input = staging_dir / f"{stem}.txt"
    if staging_input.exists() and not options.force:
        return {"ok": False, "message": f"Уже есть staging input {staging_input} (используйте --force)."}

    text = strip_youtube_prefilter_header(_read_text(inp))
    staging_input.write_text(text, encoding="utf-8", newline="\n")

    expected_clean = staging_dir / f"{stem}_clean.txt"
    expected_info = staging_dir / "info.txt"
    status_path = story_dir / "logs" / "safe_bridge_status.json"
    status_payload = {
        "status": "staged",
        "staging_dir": str(staging_dir),
        "input_txt_path": str(staging_input),
        "input_resolved_from": str(inp),
        "input_resolution": inp_reason,
        "expected_clean_output": str(expected_clean),
        "expected_info_output": str(expected_info),
        "safe_done": False,
        "staged_at": _now_iso(),
        "manual_safe_bot_required": True,
        "legacy_gemini_auto_note": "Для запуска бота: python -m orchestrator youtube run-safe-bridge --youtube-run-id ... --story-id ... --execute "
        "(или вручную: GEMINI_STORIES_DIR=родитель(staging), GEMINI_USER_DATA_DIR=user_data_from_orchestrator/..., GEMINI_NON_INTERACTIVE=1).",
    }
    _write_safe_bridge_status(status_path, status_payload)

    st = manifest.get("status") if isinstance(manifest.get("status"), dict) else {}
    st = {**st, "safe_staged": True, "safe_done": False}
    manifest["status"] = st
    lb = manifest.get("legacy_bridge") if isinstance(manifest.get("legacy_bridge"), dict) else {}
    manifest["legacy_bridge"] = {**lb, "youtube_safe_story_dir": str(staging_dir)}
    manifest["expected_artifacts"] = _expected_artifacts_block(story_dir)
    _write_json(sm_path, manifest)

    _refresh_run_safe_summary(root_dir=root_dir, youtube_run_id=youtube_run_id)
    _append_status(
        _youtube_run_root(root_dir, youtube_run_id) / "youtube_status.jsonl",
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "stage": "youtube_prepare_safe_bridge",
            "state": "staged",
            "staging_dir": str(staging_dir),
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "story_id": sid,
        "staging_dir": str(staging_dir),
        "staging_input_txt": str(staging_input),
        "input_resolved_from": str(inp),
        "safe_bridge_status": str(status_path),
        "story_manifest": str(sm_path),
    }


def _expected_artifacts_block(story_dir: Path) -> dict[str, str]:
    sd = story_dir.resolve()
    return {
        "safe_story": str(sd / "02_safe_story" / "safe_story.txt"),
        "promo_text_ready_for_audio": str(sd / "03_promo" / "text_ready_for_audio.txt"),
        "final_narration_text": str(sd / "03_promo" / "text_ready_for_audio.txt"),
        "audio_mp3": str(sd / "04_audio" / "narration.mp3"),
        "characters_txt": str(sd / "05_characters" / "characters.txt"),
        "prompts_list_txt": str(sd / "06_director" / "prompts_list.txt"),
        "frames_dir": str(sd / "07_frames"),
        "final_video_mp4": str(sd / "08_video" / "final_video.mp4"),
    }


@dataclass
class YoutubeImportSafeResultOptions:
    youtube_run_id: str
    story_id: str
    force: bool = False


def run_youtube_import_safe_result(*, config: OrchestratorConfig, options: YoutubeImportSafeResultOptions) -> dict[str, Any]:
    root_dir = config.root_dir.resolve()
    youtube_run_id = str(options.youtube_run_id).strip()
    story_key = str(options.story_id).strip()
    if not youtube_run_id or not story_key:
        return {"ok": False, "message": "Нужны --youtube-run-id и --story-id"}

    run_path = _run_manifest_path(root_dir, youtube_run_id)
    run_m = _load_run_manifest(run_path)
    if not run_m:
        return {"ok": False, "message": f"Нет run manifest: {run_path}"}

    entry, err = _find_single_story_entry(run_m, story_key)
    if entry is None:
        return {"ok": False, "message": err}

    sm_path = Path(str(entry.get("story_manifest", "")).strip())
    manifest = _load_story_manifest(sm_path)
    if not manifest:
        return {"ok": False, "message": f"Story manifest не найден или пуст: {sm_path}"}

    gerr = _verify_guards(manifest)
    if gerr:
        return {"ok": False, "message": gerr}

    story_dir = _story_dir_from_manifest(manifest)
    if story_dir is None:
        return {"ok": False, "message": "В manifest нет youtube_outputs.story_dir"}

    sid = str(manifest.get("story_id", "")).strip() or str(entry.get("story_id", "")).strip()
    staging_dir = _staging_dir_from_manifest(manifest, root_dir, youtube_run_id, sid)
    status_path = story_dir / "logs" / "safe_bridge_status.json"

    if not staging_dir.is_dir():
        payload = {
            "status": "missing_safe_output",
            "reason": f"staging_dir отсутствует: {staging_dir}",
            "safe_done": False,
            "checked_at": _now_iso(),
        }
        _write_safe_bridge_status(status_path, payload)
        _refresh_run_safe_summary(root_dir=root_dir, youtube_run_id=youtube_run_id)
        return {
            "ok": True,
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "import_status": "missing_safe_output",
            "message": "Нет staging-папки; сначала prepare-safe-bridge.",
            "safe_bridge_status": str(status_path),
        }

    clean_src = _pick_clean_file(staging_dir, sid)
    if clean_src is None:
        payload = {
            "status": "missing_safe_output",
            "staging_dir": str(staging_dir),
            "safe_done": False,
            "checked_at": _now_iso(),
            "message": "Не найден ни один *_clean.txt в staging (fake safe_story не создаём).",
        }
        _write_safe_bridge_status(status_path, payload)
        _refresh_run_safe_summary(root_dir=root_dir, youtube_run_id=youtube_run_id)
        return {
            "ok": True,
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "import_status": "missing_safe_output",
            "message": payload["message"],
            "safe_bridge_status": str(status_path),
        }

    clean_language = detect_path_language(clean_src)
    if clean_language != EXPECTED_YOUTUBE_LANGUAGE:
        payload = {
            "status": "wrong_language",
            "safe_done": False,
            "staging_dir": str(staging_dir),
            "clean_output": str(clean_src),
            "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
            "detected_language": clean_language,
            "checked_at": _now_iso(),
            "message": "safe import blocked: legacy safe output is not English.",
        }
        _write_safe_bridge_status(status_path, payload)
        return {
            "ok": False,
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "import_status": "wrong_language",
            "message": payload["message"],
            "safe_bridge_status": str(status_path),
            "expected_language": EXPECTED_YOUTUBE_LANGUAGE,
            "detected_language": clean_language,
            "imported_from": str(clean_src),
        }

    safe_dir = story_dir / "02_safe_story"
    raw_dir = safe_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_out = safe_dir / "safe_story.txt"
    if safe_out.exists() and not options.force:
        return {"ok": False, "message": f"Уже есть {safe_out} (используйте --force для перезаписи)."}

    safe_out.write_text(strip_youtube_prefilter_header(_read_text(clean_src)), encoding="utf-8", newline="\n")
    raw_paths: list[str] = []
    shutil.copy2(clean_src, raw_dir / clean_src.name)
    raw_paths.append(str(raw_dir / clean_src.name))
    info_staging = staging_dir / "info.txt"
    if info_staging.is_file():
        dst_info = raw_dir / "info.txt"
        shutil.copy2(info_staging, dst_info)
        raw_paths.append(str(dst_info))

    cn = clean_src.name
    if cn.endswith("_clean.txt"):
        base_name = cn[: -len("_clean.txt")]
        tmp_path = staging_dir / f"{base_name}_clean.tmp"
    else:
        tmp_path = staging_dir / f"{clean_src.stem}_clean.tmp"
    if tmp_path.is_file():
        dst = raw_dir / tmp_path.name
        shutil.copy2(tmp_path, dst)
        raw_paths.append(str(dst))
    for report_name in ("result_report.txt", "genre_report.txt"):
        rp = staging_dir / report_name
        if rp.is_file():
            dst = raw_dir / report_name
            shutil.copy2(rp, dst)
            raw_paths.append(str(dst))

    now = _now_iso()
    status_payload = {
        "status": "safe_done",
        "safe_done": True,
        "imported_from": str(clean_src),
        "safe_story_path": str(safe_out),
        "raw_paths": raw_paths,
        "imported_at": now,
        "staging_dir": str(staging_dir),
    }
    _write_safe_bridge_status(status_path, status_payload)

    st = manifest.get("status") if isinstance(manifest.get("status"), dict) else {}
    st = {**st, "safe_staged": True, "safe_done": True}
    manifest["status"] = st
    manifest["expected_artifacts"] = _expected_artifacts_block(story_dir)
    manifest["actual_artifacts"] = {"safe_story": str(safe_out.resolve())}
    manifest["safe"] = {"imported_from": str(clean_src.resolve()), "imported_at": now}
    _write_json(sm_path, manifest)

    _refresh_run_safe_summary(root_dir=root_dir, youtube_run_id=youtube_run_id)
    _append_status(
        _youtube_run_root(root_dir, youtube_run_id) / "youtube_status.jsonl",
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "stage": "youtube_import_safe_result",
            "state": "safe_done",
            "safe_story_path": str(safe_out),
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "story_id": sid,
        "import_status": "safe_done",
        "safe_story_path": str(safe_out),
        "imported_from": str(clean_src),
        "raw_paths": raw_paths,
        "safe_bridge_status": str(status_path),
    }


def _legacy_youtube_tts_dir(root_dir: Path) -> Path:
    return (root_dir / "legacy" / "youtube_tts").resolve()


def _legacy_user_data_dir(root_dir: Path) -> Path:
    """Основной Chrome user_data legacy (уже залогиненный)."""
    return (_legacy_youtube_tts_dir(root_dir) / "user_data").resolve()


_GEMINI_RESULT_REPORT_RE = re.compile(r"^result_report(-\d+)?\.txt$", re.IGNORECASE)


def _list_gemini_style_story_txts(folder: Path) -> list[Path]:
    """Совпадает с list_story_source_files в gemini_auto (без импорта playwright)."""

    def is_generated(name: str) -> bool:
        n = name.lower()
        if n == "info.txt":
            return True
        if n == "genre_report.txt":
            return True
        if n.endswith("_clean.txt") or n.endswith("_clean.tmp"):
            return True
        return _GEMINI_RESULT_REPORT_RE.fullmatch(name) is not None

    return sorted(
        [p for p in folder.glob("*.txt") if p.is_file() and not is_generated(p.name)],
        key=lambda x: x.name.lower(),
    )


def _pick_gemini_style_story_file(folder: Path) -> Path | None:
    xs = _list_gemini_style_story_txts(folder)
    return xs[0] if xs else None


def _collect_story_folders_preview(stories_dir: Path) -> list[Path]:
    """Тот же отбор leaf-папок, что collect_story_folders в gemini_auto (без print)."""
    if not stories_dir.is_dir():
        return []
    all_dirs = [path for path in stories_dir.rglob("*") if path.is_dir()]
    with_source = [folder for folder in all_dirs if _pick_gemini_style_story_file(folder) is not None]
    with_source_set = set(with_source)
    leaf_story_folders = [
        folder for folder in with_source if folder not in {child.parent for child in with_source_set}
    ]
    return sorted(leaf_story_folders, key=lambda path: str(path.relative_to(stories_dir)).lower())


def _gemini_bot_chain_len(ltd: Path) -> int:
    p = ltd / "gemini_bots.json"
    if not p.is_file():
        return 1
    try:
        raw = _read_json(p)
        if isinstance(raw, list) and raw:
            n = 0
            for item in raw:
                if isinstance(item, dict) and str(item.get("url", "")).strip():
                    n += 1
            return max(1, n)
    except Exception:
        pass
    return 1


def _preview_start_bot_index(bot_chain_len: int) -> tuple[int, str]:
    """Как choose_start_bot_idx при GEMINI_NON_INTERACTIVE=1 (без загрузки Playwright)."""
    if bot_chain_len <= 1:
        return 0, "единственный бот в цепочке (индекс 0)"
    raw_idx = (os.getenv("GEMINI_START_BOT_INDEX") or "").strip()
    if raw_idx.isdigit():
        k = int(raw_idx)
        if 0 <= k < bot_chain_len:
            return k, f"GEMINI_START_BOT_INDEX={k} (в цепочке {bot_chain_len} ботов)"
    return 0, f"индекс 0 по умолчанию (GEMINI_NON_INTERACTIVE=1, ботов в цепочке: {bot_chain_len}; GEMINI_START_BOT_INDEX не задан или вне диапазона)"


def _user_data_orchestrator_dir(root_dir: Path, youtube_run_id: str, story_id: str) -> Path:
    return (
        root_dir / "legacy" / "youtube_tts" / "user_data_from_orchestrator" / youtube_run_id / _safe_name_for_file(story_id)
    ).resolve()


def _build_manual_gemini_command(
    *,
    root_dir: Path,
    gemini_stories_dir: str,
    gemini_user_data_dir: str,
) -> str:
    ltd = _legacy_youtube_tts_dir(root_dir)
    py = sys.executable
    ga = ltd / "gemini_auto.py"
    return (
        f'cd /d "{ltd}" && set "GEMINI_STORIES_DIR={gemini_stories_dir}" && '
        f'set "GEMINI_USER_DATA_DIR={gemini_user_data_dir}" && set "GEMINI_NON_INTERACTIVE=1" && '
        f'set "PYTHONIOENCODING=utf-8" && set "PYTHONUTF8=1" && '
        f'"{py}" "{ga}"'
    )


@dataclass
class YoutubeRunSafeBridgeOptions:
    youtube_run_id: str
    story_id: str
    execute: bool = False
    force: bool = False
    reuse_legacy_user_data: bool = False


def run_youtube_run_safe_bridge(*, config: OrchestratorConfig, options: YoutubeRunSafeBridgeOptions) -> dict[str, Any]:
    """Подготовка env + опционально subprocess gemini_auto, затем import-safe-result (single-story)."""
    root_dir = config.root_dir.resolve()
    youtube_run_id = str(options.youtube_run_id).strip()
    story_key = str(options.story_id).strip()
    if not youtube_run_id or not story_key:
        return {"ok": False, "message": "Нужны --youtube-run-id и --story-id"}

    run_path = _run_manifest_path(root_dir, youtube_run_id)
    run_m = _load_run_manifest(run_path)
    if not run_m:
        return {"ok": False, "message": f"Нет run manifest: {run_path}"}

    entry, err = _find_single_story_entry(run_m, story_key)
    if entry is None:
        return {"ok": False, "message": err}

    sm_path = Path(str(entry.get("story_manifest", "")).strip())
    manifest = _load_story_manifest(sm_path)
    if not manifest:
        return {"ok": False, "message": f"Story manifest не найден: {sm_path}"}

    gerr = _verify_guards(manifest)
    if gerr:
        return {"ok": False, "message": gerr}

    story_dir = _story_dir_from_manifest(manifest)
    if story_dir is None:
        return {"ok": False, "message": "В manifest нет youtube_outputs.story_dir"}

    sid = str(manifest.get("story_id", "")).strip() or str(entry.get("story_id", "")).strip()
    staging_dir = _staging_dir_from_manifest(manifest, root_dir, youtube_run_id, sid)
    status_path = story_dir / "logs" / "safe_bridge_status.json"
    logs_dir = story_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    bot_log = logs_dir / "safe_bot_run.log"

    if not staging_dir.is_dir():
        return {"ok": False, "message": f"Staging не найден: {staging_dir}. Сначала prepare-safe-bridge."}

    perr = _verify_staging_parent_single_story(staging_dir)
    if perr:
        return {"ok": False, "message": perr}

    sources = _list_staging_source_txt(staging_dir)
    if len(sources) == 0:
        return {"ok": False, "message": "В staging нет ни одного исходного .txt для safe-бота."}
    if len(sources) > 1:
        return {
            "ok": False,
            "message": f"В staging больше одного исходного .txt ({len(sources)}): {[str(s) for s in sources]} — остановка.",
        }

    gemini_stories_dir = str(staging_dir.parent.resolve())
    if options.reuse_legacy_user_data:
        user_data_dir = _legacy_user_data_dir(root_dir)
        gemini_user_data_dir = str(user_data_dir)
    else:
        user_data_dir = _user_data_orchestrator_dir(root_dir, youtube_run_id, sid)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        gemini_user_data_dir = str(user_data_dir)

    ltd = _legacy_youtube_tts_dir(root_dir)
    gemini_auto = ltd / "gemini_auto.py"
    if not gemini_auto.is_file():
        return {"ok": False, "message": f"Не найден legacy: {gemini_auto}"}

    manual_cmd = _build_manual_gemini_command(
        root_dir=root_dir,
        gemini_stories_dir=gemini_stories_dir,
        gemini_user_data_dir=gemini_user_data_dir,
    )

    preview_folders = _collect_story_folders_preview(Path(gemini_stories_dir))
    if len(preview_folders) != 1:
        return {
            "ok": False,
            "message": (
                "Для single-story в GEMINI_STORIES_DIR должна быть ровно одна leaf story-папка (как у gemini_auto); "
                f"найдено {len(preview_folders)}: {[str(f) for f in preview_folders]}"
            ),
        }

    b_len = _gemini_bot_chain_len(ltd)
    bot_i, bot_note = _preview_start_bot_index(b_len)

    if not options.execute:
        print("[run-safe-bridge] preflight (без --execute):", flush=True)
        print(f"  GEMINI_USER_DATA_DIR={gemini_user_data_dir}", flush=True)
        print(f"  GEMINI_STORIES_DIR={gemini_stories_dir}", flush=True)
        print(f"  story_folders (preview)={len(preview_folders)} -> {[str(f) for f in preview_folders]}", flush=True)
        print(f"  start_bot_index={bot_i} ({bot_note})", flush=True)
        print(f"  reuse_legacy_user_data={bool(options.reuse_legacy_user_data)}", flush=True)

        payload = {
            "status": "safe_bot_needs_manual_interaction",
            "safe_done": False,
            "staging_dir": str(staging_dir),
            "gemini_stories_dir": gemini_stories_dir,
            "gemini_user_data_dir": gemini_user_data_dir,
            "reuse_legacy_user_data": bool(options.reuse_legacy_user_data),
            "manual_cmd_windows": manual_cmd,
            "note": "Без --execute оркестратор не запускает Playwright. Запустите команду вручную или повторите с --execute.",
            "updated_at": _now_iso(),
        }
        _write_safe_bridge_status(status_path, payload)
        _patch_story_manifest_status(
            sm_path,
            safe_bot_needs_manual_interaction=True,
            safe_bot_started=False,
            safe_bot_finished=False,
            safe_bot_failed=False,
        )
        _refresh_run_safe_summary(root_dir=root_dir, youtube_run_id=youtube_run_id)
        _append_status(
            _youtube_run_root(root_dir, youtube_run_id) / "youtube_status.jsonl",
            {
                "timestamp": _now_iso(),
                "youtube_run_id": youtube_run_id,
                "story_id": sid,
                "stage": "youtube_run_safe_bridge",
                "state": "needs_manual_interaction",
            },
        )
        return {
            "ok": True,
            "skipped_subprocess": True,
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "gemini_stories_dir": gemini_stories_dir,
            "gemini_user_data_dir": gemini_user_data_dir,
            "reuse_legacy_user_data": bool(options.reuse_legacy_user_data),
            "story_folders_preview_count": len(preview_folders),
            "story_folders_preview": [str(f) for f in preview_folders],
            "start_bot_index": bot_i,
            "start_bot_index_note": bot_note,
            "manual_cmd_windows": manual_cmd,
            "safe_bot_log": str(bot_log),
            "safe_bridge_status": str(status_path),
            "message": "Реальный gemini_auto не запускался (нет --execute). Дальше только import при наличии *_clean.txt — не вызывается автоматически в этом режиме.",
        }

    print("[run-safe-bridge] preflight:", flush=True)
    print(f"  GEMINI_USER_DATA_DIR={gemini_user_data_dir}", flush=True)
    print(f"  GEMINI_STORIES_DIR={gemini_stories_dir}", flush=True)
    print(f"  story_folders (gemini collect preview)={len(preview_folders)} -> {[str(f) for f in preview_folders]}", flush=True)
    print(f"  start_bot_index={bot_i} ({bot_note})", flush=True)
    print(f"  reuse_legacy_user_data={bool(options.reuse_legacy_user_data)}", flush=True)

    _patch_story_manifest_status(
        sm_path,
        safe_bot_started=True,
        safe_bot_finished=False,
        safe_bot_failed=False,
        safe_bot_needs_manual_interaction=False,
    )
    started = _now_iso()
    _write_safe_bridge_status(
        status_path,
        {
            "status": "safe_bot_started",
            "staging_dir": str(staging_dir),
            "gemini_stories_dir": gemini_stories_dir,
            "gemini_user_data_dir": gemini_user_data_dir,
            "reuse_legacy_user_data": bool(options.reuse_legacy_user_data),
            "story_folders_preview_count": len(preview_folders),
            "start_bot_index": bot_i,
            "start_bot_index_note": bot_note,
            "safe_bot_log": str(bot_log),
            "started_at": started,
            "safe_done": False,
        },
    )
    _append_status(
        _youtube_run_root(root_dir, youtube_run_id) / "youtube_status.jsonl",
        {
            "timestamp": started,
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "stage": "youtube_run_safe_bridge",
            "state": "safe_bot_started",
            "gemini_stories_dir": gemini_stories_dir,
        },
    )

    env = os.environ.copy()
    env["GEMINI_STORIES_DIR"] = gemini_stories_dir
    env["GEMINI_USER_DATA_DIR"] = gemini_user_data_dir
    env["GEMINI_NON_INTERACTIVE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    exit_code: int | None = None
    try:
        with bot_log.open("w", encoding="utf-8", errors="replace") as logf:
            logf.write(f"[orchestrator] cwd={ltd}\n")
            logf.write(f"[orchestrator] GEMINI_STORIES_DIR={gemini_stories_dir}\n")
            logf.write(f"[orchestrator] GEMINI_USER_DATA_DIR={gemini_user_data_dir}\n")
            logf.write(f"[orchestrator] GEMINI_NON_INTERACTIVE=1\n")
            logf.write(f"[orchestrator] reuse_legacy_user_data={bool(options.reuse_legacy_user_data)}\n")
            logf.write(f"[orchestrator] story_folders_preview_count={len(preview_folders)}\n")
            logf.write(f"[orchestrator] start_bot_index={bot_i} ({bot_note})\n")
            logf.flush()
            proc = subprocess.run(
                [sys.executable, str(gemini_auto)],
                cwd=str(ltd),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=None,
            )
            exit_code = int(proc.returncode) if proc.returncode is not None else 0
    except Exception as exc:
        exit_code = -1
        with bot_log.open("a", encoding="utf-8", errors="replace") as logf:
            logf.write(f"\n[orchestrator] subprocess exception: {exc}\n")

    finished = _now_iso()
    failed = exit_code != 0

    _write_safe_bridge_status(
        status_path,
        {
            "status": "safe_bot_failed" if failed else "safe_bot_finished",
            "staging_dir": str(staging_dir),
            "gemini_stories_dir": gemini_stories_dir,
            "gemini_user_data_dir": gemini_user_data_dir,
            "safe_bot_log": str(bot_log),
            "started_at": started,
            "finished_at": finished,
            "gemini_auto_exit_code": exit_code,
            "safe_done": False,
        },
    )
    imp = run_youtube_import_safe_result(
        config=config,
        options=YoutubeImportSafeResultOptions(
            youtube_run_id=youtube_run_id,
            story_id=story_key,
            force=bool(options.force),
        ),
    )

    m2 = _load_story_manifest(sm_path)
    st2 = dict(m2.get("status") or {})
    st2["safe_bot_started"] = True
    st2["safe_bot_finished"] = True
    st2["safe_bot_failed"] = bool(failed)
    st2["safe_bot_needs_manual_interaction"] = False
    m2["status"] = st2
    _write_json(sm_path, m2)

    if status_path.is_file():
        cur = _read_json(status_path)
        if isinstance(cur, dict):
            cur["gemini_auto_exit_code"] = exit_code
            cur["gemini_auto_log"] = str(bot_log)
            if failed and str(cur.get("status", "")).strip() == "missing_safe_output":
                cur["gemini_auto_failed_before_import"] = True
            _write_json(status_path, cur)

    _refresh_run_safe_summary(root_dir=root_dir, youtube_run_id=youtube_run_id)
    _append_status(
        _youtube_run_root(root_dir, youtube_run_id) / "youtube_status.jsonl",
        {
            "timestamp": finished,
            "youtube_run_id": youtube_run_id,
            "story_id": sid,
            "stage": "youtube_run_safe_bridge",
            "state": "import_after_gemini",
            "gemini_auto_exit_code": exit_code,
            "import_status": imp.get("import_status"),
        },
    )

    bridge_ok = bool(imp.get("ok", False))
    if failed and str(imp.get("import_status", "")).strip() != "safe_done":
        bridge_ok = False

    return {
        "ok": bridge_ok,
        "skipped_subprocess": False,
        "youtube_run_id": youtube_run_id,
        "story_id": sid,
        "gemini_stories_dir": gemini_stories_dir,
        "gemini_user_data_dir": gemini_user_data_dir,
        "reuse_legacy_user_data": bool(options.reuse_legacy_user_data),
        "story_folders_preview_count": len(preview_folders),
        "start_bot_index": bot_i,
        "start_bot_index_note": bot_note,
        "gemini_auto_exit_code": exit_code,
        "gemini_auto_failed": bool(failed),
        "safe_bot_log": str(bot_log),
        "import": imp,
        "safe_bridge_status": str(status_path),
    }
