from __future__ import annotations

import json
import os
import re
import sys
import shutil
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import launch_legacy_output_root, launch_legacy_runs_root
from orchestrator.length_filter import LengthFilterOptions, run_length_filter
from orchestrator.site_tts.info_parser import resolve_cleaned_story_txt_path, resolve_voice_letter_from_info_content
from orchestrator.status import StatusStore
from orchestrator.phase_a_gemini_supervisor import (
    GeminiSupervisorOptions,
    count_pending_gemini_folders,
    gemini_worker_subprocess_creationflags,
    maybe_print_gemini_queue_progress,
    run_supervised_gemini_workers,
)
from orchestrator.site_info_fields import extract_visual_prompt_full, make_visual_prompt_preview, parse_info_fields
from orchestrator.visual_stage import run_visual_stage


YOUTUBE_FIT_RE = re.compile(r"^\s*подходит\s+для\s+youtube\s*:\s*(да|нет)\s*$", re.IGNORECASE | re.MULTILINE)
SELECTION_EXPLICIT_RE = re.compile(
    r"(?im)^\s*(?:verdict|decision|result|итог|вердикт|решение)\s*[:\-]\s*"
    r"(accept|accepted|approve|approved|selected|reject|rejected|decline|declined|not\s+selected|yes|no|да|нет|подходит|не\s+подходит)\s*[.!]?\s*$"
)
SELECTION_BARE_RE = re.compile(
    r"(?im)^\s*(accept|accepted|selected|reject|rejected|yes|no|да|нет|подходит|не\s+подходит)\s*[.!]?\s*$"
)
SELECTION_VERDICT_LINE_RE = re.compile(r"(?im)^\s*(?:мой\s+)?(?:итог(?:овое)?\s+)?(?:вердикт|решение|decision|result)\s*:\s*(.+)$")
SELECTION_ACCEPT_TOKEN_RE = re.compile(r"(?i)\b(accept|accepted|approve|approved|selected|yes|да|подходит|принято|принят)\b")
SELECTION_REJECT_TOKEN_RE = re.compile(r"(?i)\b(reject|rejected|decline|declined|not\s+selected|no|нет|не\s+подходит|отклонено|отклонен)\b")
POLICY_REFUSAL_RE = re.compile(
    r"(?is)(may\s+go\s+against\s+my\s+guidelines|can't\s+help\s+with\s+that|cannot\s+help\s+with\s+that|не\s+могу\s+помочь\s+с\s+этим|противоречит\s+моим?\s+правилам|не\s+могу\s+выполнить\s+этот\s+запрос)"
)


@dataclass
class PhaseAOptions:
    stories_dir: Path
    short_dir: Path | None
    execute: bool
    story_id: str
    words_per_minute: int
    min_minutes: float
    min_words: int
    extensions: list[str]
    gemini_workers: int = 1
    max_stories: int = 0
    run_branch: str = "site"
    gemini_registry_path: Path = Path("configs/gemini_bots_registry.example.yaml")
    gemini_stage_key: str = "general_selection"
    gemini_info_stage_key: str = "site_info_builder"
    resume: bool = False
    visual_mode: str = "manual"
    visual_pod_url: str = ""
    # Пул Chrome-профилей (Gemini): не более ``gemini_target_active_workers`` процессов одновременно.
    gemini_target_active_workers: int = 3
    gemini_profiles_total: int = 5
    gemini_max_restarts_per_profile: int = 3
    gemini_profile_cooldown_seconds: float = 900.0
    gemini_supervised_workers: bool = True
    # Если задан — корень Запуски/<name>/; runs/ и output/ в 10_Временные_файлы/legacy/...
    launch_dir: Path | None = None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_nonempty_info_txt(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def _iter_gemini_dirs_having_source_txt(cat_dir: Path, src_basename: str) -> list[Path]:
    if not cat_dir.is_dir():
        return []
    out: list[Path] = []
    for story_dir in cat_dir.iterdir():
        if not story_dir.is_dir():
            continue
        if (story_dir / src_basename).is_file():
            out.append(story_dir)
    return out


def _pick_gemini_dir_for_done_sync(candidates: list[Path]) -> Path | None:
    """Папка с непустым info.txt; при нескольких — с самым новым mtime info."""
    best: tuple[Path, float] | None = None
    for d in candidates:
        ip = d / "info.txt"
        if not _is_nonempty_info_txt(ip):
            continue
        try:
            mt = ip.stat().st_mtime
        except OSError:
            mt = 0.0
        if best is None or mt > best[1]:
            best = (d, mt)
    return best[0] if best else None


def _pick_gemini_dir_for_incomplete_work(candidates: list[Path]) -> Path | None:
    """Папки без готового info — детерминированно самое маленькое имя (canonical для resume)."""
    incomplete = [d for d in candidates if not _is_nonempty_info_txt(d / "info.txt")]
    if not incomplete:
        return None
    incomplete.sort(key=lambda p: p.name.lower())
    return incomplete[0]


def _find_gemini_story_dir_for_source(
    gemini_stories_root: Path,
    stories_dir: Path,
    src: Path,
) -> Path | None:
    """
    Найти папку рассказа под gemini_input/stories: сначала с готовым info (для resume-синка),
    иначе незавершённую (для продолжения без создания дублей).
    """
    try:
        rel = src.resolve().relative_to(stories_dir.resolve())
    except ValueError:
        return None
    category = rel.parts[0] if len(rel.parts) > 1 else "uncategorized"
    cat_dir = gemini_stories_root / category
    cands = _iter_gemini_dirs_having_source_txt(cat_dir, src.name)
    if not cands:
        return None
    done = _pick_gemini_dir_for_done_sync(cands)
    if done is not None:
        return done
    inc = _pick_gemini_dir_for_incomplete_work(cands)
    if inc is not None:
        return inc
    return sorted(cands, key=lambda p: p.name.lower())[0]


def _next_gemini_numeric_suffix(cat_dir: Path, story_name: str) -> int:
    """Следующий свободный N для имени папки ``{story_name}_{N:06d}`` по уже существующим."""
    prefix = f"{story_name}_"
    max_n = 0
    try:
        for d in cat_dir.iterdir():
            if not d.is_dir():
                continue
            if not d.name.startswith(prefix):
                continue
            suf = d.name[len(prefix) :]
            if len(suf) == 6 and suf.isdigit():
                max_n = max(max_n, int(suf))
    except OSError:
        pass
    return max_n + 1


def _sync_selection_from_gemini_info(
    *,
    src: str,
    info_path: Path,
    workspace: dict[str, Path | str],
    state_map: dict[str, dict[str, str]],
    runs_rejected_by_selection_root: Path,
    runs_policy_refusal_root: Path,
    runs_manual_root: Path,
    selected_pending: list[str],
    rejected_gemini: list[str],
    policy_refusal_gemini: list[str],
    review_gemini: list[str],
) -> None:
    """Записать selection_* из уже существующего info.txt (resume без повторной отправки в Gemini)."""
    raw_text = info_path.read_text(encoding="utf-8", errors="ignore") if info_path.is_file() else ""
    Path(workspace["selection_raw_path"]).write_text(raw_text, encoding="utf-8")
    sel = _parse_selection_result(str(workspace["story_id"]), raw_text)
    _write_json(Path(workspace["selection_result_path"]), sel)
    verdict = str(sel["verdict"])
    if verdict == "rejected":
        state_map[src] = {"state": "rejected_gemini", "reason": "gemini_gate_no"}
        rejected_gemini.append(src)
        _relocate_workspace(workspace, runs_rejected_by_selection_root)
        _write_status(workspace, "rejected", "selection_gate")
        return
    if verdict == "selected":
        state_map[src] = {"state": "selected_pending_gemini", "reason": "gemini_gate_yes"}
        selected_pending.append(src)
        _write_status(workspace, "selected", "selection_gate")
        return
    if verdict == "policy_refusal":
        state_map[src] = {"state": "policy_refusal", "reason": "gemini_policy_refusal"}
        policy_refusal_gemini.append(src)
        _relocate_workspace(workspace, runs_policy_refusal_root)
        _write_status(workspace, "policy_refusal", "selection_gate")
        return
    state_map[src] = {"state": "manual_review", "reason": "gemini_unparseable_verdict"}
    review_gemini.append(src)
    _relocate_workspace(workspace, runs_manual_root)
    _write_status(workspace, "manual_review", "selection_gate")


def _scan_txt(stories_dir: Path, extensions: list[str]) -> list[Path]:
    """Only `stories/input/*.txt` at the directory root (no recursion, no subfolders)."""
    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    out: list[Path] = []
    if not stories_dir.is_dir():
        return out
    for p in sorted(stories_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ext_set:
            continue
        out.append(p)
    return out


def count_ignored_nested_txt(stories_dir: Path, extensions: list[str]) -> int:
    """Count .txt under subfolders (ignored by non-recursive intake)."""
    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    if not stories_dir.is_dir():
        return 0
    n = 0
    for p in stories_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ext_set:
            continue
        try:
            rel = p.relative_to(stories_dir)
        except ValueError:
            continue
        if len(rel.parts) <= 1:
            continue
        n += 1
    return n


def _build_cleaner_input(
    selected_files: list[Path],
    stories_dir: Path,
    root: Path,
) -> tuple[Path, list[dict[str, str]]]:
    clean_input_root = root / "clean_input"
    mapping: list[dict[str, str]] = []
    for idx, src in enumerate(selected_files, start=1):
        rel = src.relative_to(stories_dir)
        category = rel.parts[0] if len(rel.parts) > 1 else "uncategorized"
        story_name = rel.parent.name if rel.parent != Path(".") else src.stem
        story_dir = clean_input_root / category / f"{story_name}_{idx:06d}"
        story_dir.mkdir(parents=True, exist_ok=True)
        dst = story_dir / src.name
        shutil.copy2(src, dst)
        mapping.append(
            {
                "source_path": str(src),
                "relative_path": str(rel),
                "category": category,
                "clean_story_dir": str(story_dir),
                "clean_story_file": str(dst),
                "story_key": f"{story_name}_{idx:06d}",
            }
        )
    return clean_input_root, mapping


def _build_gemini_input(
    selected_files: list[Path],
    stories_dir: Path,
    root: Path,
) -> tuple[Path, list[dict[str, str]]]:
    """
    Idempotent: для каждого исходника переиспользуем существующую gemini-папку с тем же basename .txt,
    если там ещё нет готового info.txt; иначе обновляем копию в уже завершённой папке без mkdir-дублей.
    Новая папка создаётся только если ни одной с этим исходным .txt ещё нет; суффикс _NNNNNN — max+1 по категории.
    """
    gemini_root = root / "gemini_input" / "stories"
    mapping: list[dict[str, str]] = []
    warn_multi: list[str] = []
    for src in selected_files:
        rel = src.relative_to(stories_dir)
        category = rel.parts[0] if len(rel.parts) > 1 else "uncategorized"
        story_name = rel.parent.name if rel.parent != Path(".") else src.stem
        cat_dir = gemini_root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        cands = _iter_gemini_dirs_having_source_txt(cat_dir, src.name)
        if len(cands) > 1:
            warn_multi.append(f"{src.name}: {len(cands)} папок {[d.name for d in sorted(cands, key=lambda x: x.name.lower())]}")
        incomplete = [d for d in cands if not _is_nonempty_info_txt(d / "info.txt")]
        if incomplete:
            story_dir = _pick_gemini_dir_for_incomplete_work(cands)
            assert story_dir is not None
        elif cands:
            story_dir = _pick_gemini_dir_for_done_sync(cands) or sorted(cands, key=lambda p: p.name.lower())[0]
        else:
            n = _next_gemini_numeric_suffix(cat_dir, story_name)
            story_dir = cat_dir / f"{story_name}_{n:06d}"
            story_dir.mkdir(parents=True, exist_ok=True)
        dst = story_dir / src.name
        shutil.copy2(src, dst)
        mapping.append(
            {
                "source_path": str(src),
                "relative_path": str(rel),
                "gemini_story_dir": str(story_dir),
                "gemini_story_file": str(dst),
            }
        )
    if warn_multi:
        print(
            f"[gemini_input] WARNING: несколько gemini-папок с одним исходником ({len(warn_multi)}), "
            f"используется canonical незавершённая или с info",
            flush=True,
        )
        for line in warn_multi[:30]:
            print(f"  {line}", flush=True)
        if len(warn_multi) > 30:
            print(f"  ... и ещё {len(warn_multi) - 30}", flush=True)
    return gemini_root, mapping


def _load_gemini_registry(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(raw) or {}
        bots = payload.get("gemini_bots", []) if isinstance(payload, dict) else []
        if isinstance(bots, list):
            return [x for x in bots if isinstance(x, dict)]
    except Exception:
        pass
    # Fallback parser for environments without PyYAML.
    bots: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current:
                bots.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip().strip("'\"")
            continue
        if current is None:
            continue
        if stripped == "gemini_bots:":
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = value.strip().strip("'\"")
    if current:
        bots.append(current)
    return [x for x in bots if isinstance(x, dict) and x.get("email")]


def _read_profile_email(user_data_dir: Path) -> str:
    prefs_path = user_data_dir / "Default" / "Preferences"
    if not prefs_path.exists():
        return ""
    try:
        payload = json.loads(prefs_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return ""
    account_info = payload.get("account_info", [])
    if not isinstance(account_info, list):
        return ""
    for item in account_info:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email", "")).strip().lower()
        if email:
            return email
    return ""


def _run_legacy_gemini_gate(
    config: OrchestratorConfig,
    gemini_stories_root: Path,
    registry_path: Path,
    stage_key: str,
    workers: int = 1,
    logs_dir: Path | None = None,
    *,
    events_file: Path | None = None,
    run_id: str = "",
    target_active_workers: int = 3,
    pending_count: int | None = None,
    profiles_total: int = 5,
    max_restarts_per_profile: int = 3,
    profile_cooldown_seconds: float = 900.0,
    supervised_workers: bool = True,
    profile_index_override: int | None = None,
) -> tuple[bool, str]:
    def _build_error_bundle(failed_workers: list[int]) -> str:
        if logs_dir is None:
            return ""
        logs_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = (logs_dir / f"{stage_key}_error_bundle.txt").resolve()
        lines: list[str] = []
        lines.append(f"stage={stage_key}")
        lines.append(f"failed_workers={','.join(str(i+1) for i in failed_workers)}")
        lines.append("")
        for idx in failed_workers:
            worker_no = idx + 1
            worker_log = (logs_dir / f"{stage_key}_worker_{worker_no}.log").resolve()
            lines.append("=" * 80)
            lines.append(f"worker={worker_no}")
            lines.append(f"log_file={worker_log}")
            lines.append("=" * 80)
            if worker_log.exists():
                try:
                    tail = worker_log.read_text(encoding="utf-8", errors="ignore").splitlines()[-250:]
                    lines.extend(tail)
                except Exception as ex:
                    lines.append(f"[ERROR] failed to read log: {ex}")
            else:
                lines.append("[WARN] worker log file not found")
            lines.append("")
        bundle_path.write_text("\n".join(lines), encoding="utf-8")
        return str(bundle_path)

    if str(os.getenv("CF_GEMINI_DRY_MOCK", "")).strip() == "1":
        story_dirs = sorted([p for p in gemini_stories_root.rglob("*") if p.is_dir() and any(x.is_file() for x in p.iterdir())])
        for idx, sdir in enumerate(story_dirs, start=1):
            info_path = sdir / "info.txt"
            if stage_key == "general_selection":
                mod = idx % 3
                if mod == 1:
                    txt = "Подходит для YouTube: да\nПричина: mock selected\n"
                elif mod == 2:
                    txt = "Подходит для YouTube: нет\nПричина: mock rejected\n"
                else:
                    txt = "Невозможно определить однозначно.\n"
            else:
                txt = (
                    f"Заголовок: {sdir.name}\n"
                    "Описание: mock site info generated in test mode.\n"
                    "Жанры: drama\n"
                    "Теги: test,mock\n"
                )
            info_path.write_text(txt, encoding="utf-8")
        return True, "legacy Gemini gate completed in mock mode (CF_GEMINI_DRY_MOCK=1)"
    gemini_rel = config.legacy_entrypoints.get("gemini_auto", "legacy/Gemini_Auto/gemini_auto.py")
    gemini = config.root_dir / gemini_rel
    gemini_module_dir = gemini.parent
    if not gemini.exists():
        return False, f"legacy gemini entrypoint not found: {gemini}"
    workers = max(1, min(5, workers))
    effective_pending = int(pending_count) if pending_count is not None else count_pending_gemini_folders(gemini_stories_root)
    effective_pending = max(0, effective_pending)
    if effective_pending <= 0:
        return True, "legacy Gemini gate skipped: pending_count=0"
    profiles_cap = max(1, min(5, int(profiles_total)))
    target_active = max(1, min(int(target_active_workers), profiles_cap))
    target_active = min(target_active, effective_pending)
    if effective_pending <= 2:
        target_active = 1
    legacy_parallel_all = str(os.getenv("CF_GEMINI_LEGACY_PARALLEL_ALL", "")).strip() == "1"
    use_supervisor = bool(supervised_workers) and profiles_cap > 1 and not legacy_parallel_all
    bots = _load_gemini_registry(registry_path)
    if not bots:
        return False, f"gemini registry is empty or unreadable: {registry_path}"
    print(
        f"[A3] registry loaded: path={registry_path} bots={len(bots)} stage_key={stage_key}",
        flush=True,
    )
    registry_by_email: dict[str, dict[str, str]] = {}
    for bot in bots:
        bot_email = str(bot.get("email", "")).strip().lower()
        if bot_email:
            registry_by_email[bot_email] = bot

    def _select_url(worker_idx: int) -> tuple[bool, str, str, str]:
        user_data_dir = gemini_module_dir / f"user_data_{worker_idx}"
        profile_email = _read_profile_email(user_data_dir)
        if not profile_email:
            return (
                False,
                "",
                "",
                (
                    f"worker {worker_idx+1}: профиль не залогинен (email не найден в {user_data_dir / 'Default' / 'Preferences'}). "
                    "Зайдите в Gemini/Google в этом user_data и запустите снова."
                ),
            )
        if profile_email and profile_email in registry_by_email:
            bot = registry_by_email[profile_email]
        else:
            return (
                False,
                "",
                profile_email,
                (
                    f"worker {worker_idx+1}: email профиля ({profile_email}) отсутствует в registry ({registry_path}). "
                    "Исправьте configs/gemini_bots_registry или залогиньте нужный аккаунт."
                ),
            )
        val = str(bot.get(stage_key, "")).strip()
        selected_email = str(bot.get("email", "")).strip().lower()
        if not val:
            return False, "", selected_email, f"missing stage key '{stage_key}' for email={selected_email or 'n/a'}"
        # Use registry URL as-is. Each worker already has its own Chrome profile
        # (user_data_N), so forcing /u/N can redirect to wrong slot (/u/0).
        return True, val, selected_email, ""

    print(
        f"[A3] running Gemini gate: workers={workers} supervised={use_supervisor} "
        f"profiles_total={profiles_cap} pending_count={effective_pending} target_active={target_active}",
        flush=True,
    )
    if workers == 1:
        env = dict(os.environ)
        env["GEMINI_STORIES_DIR"] = str(gemini_stories_root)
        env["GEMINI_STAGE_KEY"] = stage_key
        env["GEMINI_DYNAMIC_QUEUE"] = "1"
        if logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            env["GEMINI_LOG_FILE"] = str((logs_dir / f"{stage_key}_worker_1.log").resolve())
        single_idx = int(profile_index_override) if profile_index_override is not None else 0
        single_idx = max(0, min(int(profiles_cap) - 1, single_idx))
        user_data_dir = gemini_module_dir / f"user_data_{single_idx}"
        env["GEMINI_USER_DATA_DIR"] = str(user_data_dir)
        ok_url, selected_url, selected_email, err = _select_url(single_idx)
        if not ok_url:
            return False, err
        env.pop("GEMINI_URL", None)
        env["GEMINI_URL"] = selected_url
        env["PARALLEL_WORKERS"] = "1"
        env["WORKER_INDEX"] = str(single_idx)
        print(
            f"[A3] worker 1/{workers} gem_stage={stage_key} profile=user_data_{single_idx} "
            f"registry_email={selected_email or 'n/a'} gem_url={env['GEMINI_URL']}",
            flush=True,
        )
        progress_state: dict[str, Any] = {}
        proc = subprocess.Popen(
            [sys.executable, str(gemini)],
            env=env,
            creationflags=gemini_worker_subprocess_creationflags(),
        )
        poll_iv = 0.75
        while proc.poll() is None:
            maybe_print_gemini_queue_progress(
                stage_key=stage_key,
                gemini_stories_root=gemini_stories_root,
                pending=None,
                expected_total=effective_pending,
                progress_state=progress_state,
            )
            time.sleep(poll_iv)
        maybe_print_gemini_queue_progress(
            stage_key=stage_key,
            gemini_stories_root=gemini_stories_root,
            pending=None,
            expected_total=effective_pending,
            progress_state=progress_state,
        )
        if int(proc.returncode or 0) != 0:
            return False, "legacy Gemini gate failed (see console output above)"
        return True, "legacy Gemini gate completed"

    if use_supervisor:

        def _build_proc_env(idx: int, parallel_slice: int) -> dict[str, str] | None:
            ok_url, selected_url, selected_email, err = _select_url(idx)
            if not ok_url:
                print(f"[A3] supervisor skip profile {idx+1}: {err}", flush=True)
                return None
            env = dict(os.environ)
            env["GEMINI_STORIES_DIR"] = str(gemini_stories_root)
            env["GEMINI_STAGE_KEY"] = stage_key
            # Под supervisor разделяем очередь по WORKER_INDEX (см. parallel_slice), а не по
            # индексу Chrome-профиля: иначе профили 0 и 2 при target=2 дают одинаковый idx%2.
            env["GEMINI_DYNAMIC_QUEUE"] = "0"
            if logs_dir is not None:
                logs_dir.mkdir(parents=True, exist_ok=True)
                env["GEMINI_LOG_FILE"] = str((logs_dir / f"{stage_key}_worker_{idx+1}.log").resolve())
            user_data_dir = gemini_module_dir / f"user_data_{idx}"
            env.pop("GEMINI_URL", None)
            env["GEMINI_URL"] = selected_url
            ta = max(1, int(target_active))
            env["PARALLEL_WORKERS"] = str(ta)
            slot = max(0, min(ta - 1, int(parallel_slice)))
            env["WORKER_INDEX"] = str(slot)
            env["GEMINI_USER_DATA_DIR"] = str(user_data_dir)
            return env

        return run_supervised_gemini_workers(
            gemini_script=gemini,
            gemini_stories_root=gemini_stories_root,
            logs_dir=logs_dir or (gemini_stories_root.parent / "logs"),
            stage_key=stage_key,
            options=GeminiSupervisorOptions(
                target_active_workers=target_active,
                profiles_total=profiles_cap,
                max_restarts_per_profile=max(1, int(max_restarts_per_profile)),
                profile_cooldown_seconds=float(profile_cooldown_seconds),
                max_supervisor_seconds=600.0 if effective_pending <= 2 else 0.0,
                run_id=str(run_id or "").strip(),
            ),
            build_proc_env=_build_proc_env,
            events_file=events_file,
            expected_gemini_pending_total=effective_pending,
        )

    procs: list[tuple[int, subprocess.Popen[bytes]]] = []
    skipped_workers: list[tuple[int, str]] = []
    for idx in range(workers):
        env = dict(os.environ)
        env["GEMINI_STORIES_DIR"] = str(gemini_stories_root)
        env["GEMINI_STAGE_KEY"] = stage_key
        # Несколько процессов с dynamic_queue=1 тянут одну очередь; при сбое локов возможен дубль.
        env["GEMINI_DYNAMIC_QUEUE"] = "0" if int(workers) > 1 else "1"
        if logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            env["GEMINI_LOG_FILE"] = str((logs_dir / f"{stage_key}_worker_{idx+1}.log").resolve())
        user_data_dir = gemini_module_dir / f"user_data_{idx}"
        ok_url, selected_url, selected_email, err = _select_url(idx)
        if not ok_url:
            skipped_workers.append((idx, err))
            print(f"[A3] worker {idx+1}/{workers} skipped: {err}", flush=True)
            continue
        env.pop("GEMINI_URL", None)
        env["GEMINI_URL"] = selected_url
        env["PARALLEL_WORKERS"] = str(workers)
        env["WORKER_INDEX"] = str(idx)
        env["GEMINI_USER_DATA_DIR"] = str(user_data_dir)
        cmd = [sys.executable, str(gemini)]
        print(
            f"[A3] worker {idx+1}/{workers} started: {' '.join(cmd)} "
            f"gem_stage={stage_key} registry_email={selected_email or 'n/a'} gem_url={env['GEMINI_URL']}",
            flush=True,
        )
        creationflags = gemini_worker_subprocess_creationflags()
        procs.append((idx, subprocess.Popen(cmd, env=env, creationflags=creationflags)))

    if not procs:
        reasons = "; ".join([f"worker {i+1}: {msg}" for i, msg in skipped_workers]) or "no valid workers"
        return False, f"legacy Gemini gate failed: no active workers. {reasons}"

    progress_state: dict[str, Any] = {}
    poll_iv = 0.75
    while any(proc.poll() is None for _, proc in procs):
        maybe_print_gemini_queue_progress(
            stage_key=stage_key,
            gemini_stories_root=gemini_stories_root,
            pending=None,
            expected_total=effective_pending,
            progress_state=progress_state,
        )
        time.sleep(poll_iv)
    maybe_print_gemini_queue_progress(
        stage_key=stage_key,
        gemini_stories_root=gemini_stories_root,
        pending=None,
        expected_total=effective_pending,
        progress_state=progress_state,
    )

    failed: list[int] = []
    succeeded: list[int] = []
    for idx, proc in procs:
        code = int(proc.returncode or 0)
        if code != 0:
            failed.append(idx)
        else:
            succeeded.append(idx)
        print(f"[A3] worker {idx+1}/{workers} finished with code={code}", flush=True)

    skipped_note = ""
    if skipped_workers:
        skipped_note = "; skipped_workers=" + ",".join(str(i + 1) for i, _ in skipped_workers)

    if failed and not succeeded:
        bundle = _build_error_bundle(failed)
        hint = f"; error_bundle: {bundle}" if bundle else ""
        return False, (
            f"legacy Gemini gate failed in workers: {', '.join(str(i+1) for i in failed)}"
            f"{hint}{skipped_note}"
        )
    if failed:
        bundle = _build_error_bundle(failed)
        hint = f"; error_bundle: {bundle}" if bundle else ""
        return True, (
            f"legacy Gemini gate completed with partial worker failures: "
            f"ok={','.join(str(i+1) for i in succeeded)} "
            f"failed={','.join(str(i+1) for i in failed)}{hint}{skipped_note}"
        )
    return True, (
        f"legacy Gemini gate completed ({len(procs)} active workers"
        f"{skipped_note})"
    )


def _gemini_fit(info_path: Path) -> str | None:
    if not info_path.exists():
        return None
    text = info_path.read_text(encoding="utf-8", errors="ignore")
    match = YOUTUBE_FIT_RE.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


def _run_legacy_cleaner(config: OrchestratorConfig, clean_input_root: Path) -> tuple[bool, str]:
    cleaner_rel = config.legacy_entrypoints.get("bulk_text_cleaner", "legacy/bulk-text-cleaner/clean_stories.py")
    cleaner = config.root_dir / cleaner_rel
    if not cleaner.exists():
        return False, f"legacy cleaner entrypoint not found: {cleaner}"
    print(f"[A4] running legacy cleaner: {sys.executable} {cleaner} {clean_input_root}", flush=True)
    proc = subprocess.run(
        [sys.executable, str(cleaner), str(clean_input_root)],
    )
    if proc.returncode != 0:
        return False, "legacy cleaner failed (see console output above)"
    return True, "legacy cleaner completed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _looks_like_selection_output(text: str) -> bool:
    return YOUTUBE_FIT_RE.search(text or "") is not None


def _slug_from_source(src: Path) -> str:
    # Windows-safe basename for folders/files created from story title.
    # Prevents crashes on names ending with dot/space and reserved characters.
    base = src.stem
    base = re.sub(r'[<>:"/\\|?*]+', "_", base)
    base = re.sub(r"\s+", " ", base).strip()
    base = base.rstrip(" .")
    if not base:
        base = "story"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if base.upper() in reserved:
        base = f"{base}_story"
    return base


def _collect_existing_story_dirs(runs_root: Path) -> dict[str, Path]:
    roots = [
        runs_root / "stories",
        runs_root / "rejected" / "by_length",
        runs_root / "rejected" / "by_selection",
        runs_root / "manual_review",
        runs_root / "policy_refusal",
    ]
    out: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for d in root.iterdir():
            if d.is_dir():
                out[d.name] = d
    return out


def _build_story_workspaces(
    run_stories_dir: Path,
    source_files: list[Path],
    existing_story_dirs: dict[str, Path] | None = None,
) -> dict[str, dict[str, Path | str]]:
    workspaces: dict[str, dict[str, Path | str]] = {}
    existing_story_dirs = existing_story_dirs or {}
    used: dict[str, int] = {}
    for src in source_files:
        base = _slug_from_source(src)
        idx = used.get(base, 0)
        used[base] = idx + 1
        story_id = base if idx == 0 else f"{base}__{idx+1}"
        story_dir = existing_story_dirs.get(story_id, run_stories_dir / story_id)
        pipeline_dir = story_dir / "_pipeline"
        story_dir.mkdir(parents=True, exist_ok=True)
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        text_name = f"{base}.txt"
        mapping = {
            "story_id": story_id,
            "original_filename": src.name,
            "canonical_basename": base,
            "story_folder": str(story_dir),
            "text_file": text_name,
            "legacy_info_file": "info.txt",
            "expected_image_file": f"{base}.jpg",
            "expected_audio_file": f"{base}.mp3",
        }
        _write_json(pipeline_dir / "mapping.json", mapping)
        workspaces[str(src)] = {
            "story_id": story_id,
            "canonical_basename": base,
            "story_dir": story_dir,
            "pipeline_dir": pipeline_dir,
            "legacy_text_path": story_dir / text_name,
            "legacy_info_path": story_dir / "info.txt",
            "cleaned_story_path": story_dir / "cleaned_story.txt",
            "selection_raw_path": pipeline_dir / "selection_raw.txt",
            "selection_result_path": pipeline_dir / "selection_result.json",
            "site_info_raw_path": pipeline_dir / "site_info_raw.txt",
            "site_info_path": pipeline_dir / "site_info.json",
            "status_path": pipeline_dir / "status.json",
            "mapping_path": pipeline_dir / "mapping.json",
        }
    return workspaces


def _write_status(workspace: dict[str, Path | str], status: str, last_stage: str, error: str | None = None) -> None:
    payload = {
        "story_id": workspace["story_id"],
        "status": status,
        "last_stage": last_stage,
        "updated_at": _now_iso(),
        "error": error,
    }
    _write_json(Path(workspace["status_path"]), payload)


def _relocate_workspace(workspace: dict[str, Path | str], destination_root: Path) -> None:
    old_story_dir = Path(workspace["story_dir"])
    new_story_dir = destination_root / str(workspace["story_id"])
    destination_root.mkdir(parents=True, exist_ok=True)
    if old_story_dir.resolve() != new_story_dir.resolve():
        if new_story_dir.exists():
            shutil.rmtree(new_story_dir, ignore_errors=True)
        shutil.move(str(old_story_dir), str(new_story_dir))
    pipeline_dir = new_story_dir / "_pipeline"
    canonical_basename = str(workspace["canonical_basename"])
    workspace["story_dir"] = new_story_dir
    workspace["pipeline_dir"] = pipeline_dir
    workspace["legacy_text_path"] = new_story_dir / f"{canonical_basename}.txt"
    workspace["legacy_info_path"] = new_story_dir / "info.txt"
    workspace["cleaned_story_path"] = new_story_dir / "cleaned_story.txt"
    workspace["selection_raw_path"] = pipeline_dir / "selection_raw.txt"
    workspace["selection_result_path"] = pipeline_dir / "selection_result.json"
    workspace["site_info_raw_path"] = pipeline_dir / "site_info_raw.txt"
    workspace["site_info_path"] = pipeline_dir / "site_info.json"
    workspace["status_path"] = pipeline_dir / "status.json"
    workspace["mapping_path"] = pipeline_dir / "mapping.json"


def _append_run_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{_now_iso()}] {message}\n")


def _parse_selection_result(story_id: str, raw_text: str) -> dict[str, Any]:
    text = raw_text or ""
    if POLICY_REFUSAL_RE.search(text):
        return {
            "story_id": story_id,
            "verdict": "policy_refusal",
            "reason": "gemini_policy_refusal",
            "score": None,
            "source": "gemini_selection",
            "created_at": _now_iso(),
        }
    fit = None
    m = YOUTUBE_FIT_RE.search(text)
    if m:
        fit = m.group(1).strip().lower()
    explicit = None
    m_explicit = SELECTION_EXPLICIT_RE.search(text)
    if m_explicit:
        explicit = m_explicit.group(1).strip().lower()
    if not explicit:
        m_bare = SELECTION_BARE_RE.search(text)
        if m_bare:
            explicit = m_bare.group(1).strip().lower()
    if not explicit:
        verdict_line = SELECTION_VERDICT_LINE_RE.search(text)
        if verdict_line:
            line = verdict_line.group(1).strip()
            has_accept = bool(SELECTION_ACCEPT_TOKEN_RE.search(line))
            has_reject = bool(SELECTION_REJECT_TOKEN_RE.search(line))
            if has_accept and not has_reject:
                explicit = "accept"
            elif has_reject and not has_accept:
                explicit = "reject"
    if not explicit:
        # Fallback for common explicit labels like "(ACCEPT)" / "(REJECT)" in conclusions.
        tail = "\n".join(text.splitlines()[-12:])
        has_accept = bool(re.search(r"(?i)\((accept|accepted)\)", tail))
        has_reject = bool(re.search(r"(?i)\((reject|rejected)\)", tail))
        if has_accept and not has_reject:
            explicit = "accept"
        elif has_reject and not has_accept:
            explicit = "reject"
    accept_values = {"accept", "accepted", "approve", "approved", "selected", "yes", "да", "подходит"}
    reject_values = {"reject", "rejected", "decline", "declined", "not selected", "no", "нет", "не подходит"}
    if fit == "да" or (explicit in accept_values):
        verdict = "selected"
        reason = "gemini_explicit_accept"
    elif fit == "нет" or (explicit in reject_values):
        verdict = "rejected"
        reason = "gemini_explicit_reject"
    else:
        verdict = "manual_review"
        reason = "gemini_ambiguous_or_unparseable_verdict"
    return {
        "story_id": story_id,
        "verdict": verdict,
        "reason": reason,
        "score": None,
        "source": "gemini_selection",
        "created_at": _now_iso(),
    }


def _parse_site_info_result(story_id: str, canonical_basename: str, raw_text: str) -> dict[str, Any]:
    fields = parse_info_fields(raw_text or "")
    title = (fields.get("title") or canonical_basename.replace("_", " ")).strip() or canonical_basename
    alt_title = (fields.get("alternative_title") or title).strip() or title
    description = (fields.get("description") or "").strip() or "Описание не получено"
    description_preview = description
    if len(description_preview) > 1200:
        description_preview = description_preview[:1200].rstrip() + "..."
    visual_prompt = extract_visual_prompt_full(raw_text or "")
    visual_prompt_preview = make_visual_prompt_preview(visual_prompt) if visual_prompt else ""
    genres_raw = fields.get("genres", "")
    tags_raw = fields.get("tags", "")
    genres = [g.strip() for g in genres_raw.split(",") if g.strip()] if genres_raw else []
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    voice_letter, _, _ = resolve_voice_letter_from_info_content(raw_text or "")
    return {
        "story_id": story_id,
        "title": title,
        "alternative_title": alt_title,
        "description": description,
        "description_preview": description_preview,
        "genres": genres,
        "tags": tags,
        "voice_type": voice_letter,
        "main_character": (fields.get("main_character") or "").strip(),
        "visual_prompt": visual_prompt,
        "visual_prompt_preview": visual_prompt_preview,
        "source": "gemini_site_info",
        "created_at": _now_iso(),
    }


def _render_legacy_info(site_info: dict[str, Any]) -> str:
    genres = site_info.get("genres") or []
    tags = site_info.get("tags") or []
    return (
        f"Заголовок: {site_info.get('title', '')}\n"
        f"Альтернативный заголовок: {site_info.get('alternative_title', '')}\n"
        f"Описание: {site_info.get('description', '')}\n"
        f"Жанры: {', '.join(str(x) for x in genres)}\n"
        f"Теги: {', '.join(str(x) for x in tags)}\n"
        f"Тип голоса: {site_info.get('voice_type', 'U')}\n"
        f"Главный персонаж: {site_info.get('main_character', '')}\n"
        f"Визуальный промпт: {site_info.get('visual_prompt') or site_info.get('visual_prompt_full', '')}\n"
    )


def _finalize_cleaned_txt_voice_rename(
    *,
    story_id: str,
    canonical: str,
    info_path: Path,
    out_story_dir: Path,
    workspace_story_dir: Path,
    run_log: Path,
) -> None:
    """
    После info.txt: cleaned_story.txt → {canonical}__[MFU].txt (и то же в run workspace).
    Буква из «Озвучка:» / «Тип голоса:» (см. orchestrator.site_tts.info_parser).
    """
    letter = "U"
    matched_line: str | None = None
    warn_extra = ""
    if not info_path.is_file():
        warn_extra = f"WARN: нет info.txt, берётся U; path={info_path}"
    else:
        txt = info_path.read_text(encoding="utf-8", errors="replace")
        letter, matched_line, warn_extra = resolve_voice_letter_from_info_content(txt)

    new_name = f"{canonical}__{letter}.txt"
    new_out = out_story_dir / new_name
    old_out = out_story_dir / "cleaned_story.txt"
    ws_new = workspace_story_dir / new_name
    ws_old = workspace_story_dir / "cleaned_story.txt"

    _append_run_log(
        run_log,
        "clean_voice_rename "
        f"story_id={story_id} canonical={canonical} info_path={info_path} "
        f"letter={letter} ozvuchka_or_voice_line={matched_line!r} note={warn_extra or '-'}",
    )

    def _do_rename(old: Path, new: Path, label: str) -> None:
        if not old.is_file():
            return
        if new.exists():
            new.unlink()
        old.rename(new)
        _append_run_log(
            run_log,
            f"clean_voice_rename story_id={story_id} {label}: old_clean={old.name} new_clean={new.name}",
        )

    _do_rename(old_out, new_out, "output_site")
    _do_rename(ws_old, ws_new, "run_workspace")

    for base in (out_story_dir, workspace_story_dir):
        for c in ("M", "F", "U"):
            if c == letter:
                continue
            stale = base / f"{canonical}__{c}.txt"
            if stale.is_file():
                stale.unlink()
                _append_run_log(
                    run_log,
                    f"clean_voice_rename story_id={story_id} stale_removed {base.name}/{stale.name}",
                )

    if not resolve_cleaned_story_txt_path(out_story_dir, canonical).is_file():
        _append_run_log(
            run_log,
            f"WARN clean_voice_rename story_id={story_id}: нет очищенного .txt в {out_story_dir} после шага",
        )


def _write_stage_stop_report(
    report_md: Path,
    run_id: str,
    branch: str,
    stage_stop: str,
    message: str,
    runs_root: Path,
    output_dir: Path,
) -> None:
    report_md.write_text(
        "\n".join(
            [
                f"# {'YouTube' if branch == 'youtube' else 'Site'} Pipeline Report: {run_id}",
                "",
                "## Pipeline metadata",
                "- scaffold_used: false",
                "- production_ready: false",
                "",
                "## На каком этапе остановились",
                f"- {stage_stop}",
                "",
                "## Причина остановки",
                f"- {message}",
                "",
                "## Пути",
                f"- technical_run: {runs_root}",
                f"- working_output: {output_dir}",
            ]
        ),
        encoding="utf-8",
    )


def run_phase_a(config: OrchestratorConfig, options: PhaseAOptions) -> dict[str, Any]:
    pipeline = "phase-a"
    stage = "phase_a"
    stories_dir = options.stories_dir.resolve()
    status = StatusStore(config.status_file)
    status.append(
        story_id=options.story_id,
        pipeline=pipeline,
        stage=stage,
        state="running",
        message="phase A started",
    )

    branch = "youtube" if str(options.run_branch).strip().lower() == "youtube" else "site"
    if options.launch_dir is not None:
        launch_base = options.launch_dir.resolve()
        runs_root = launch_legacy_runs_root(launch_base, branch, options.story_id)
    else:
        runs_root = config.root_dir / "runs" / branch / options.story_id
    if runs_root.exists() and not options.resume:
        shutil.rmtree(runs_root, ignore_errors=True)
    runs_stories_root = runs_root / "stories"
    runs_rejected_root = runs_root / "rejected"
    runs_rejected_by_length_root = runs_rejected_root / "by_length"
    runs_rejected_by_selection_root = runs_rejected_root / "by_selection"
    runs_policy_refusal_root = runs_root / "policy_refusal"
    runs_manual_root = runs_root / "manual_review"
    runs_logs_root = runs_root / "logs"
    runs_stories_root.mkdir(parents=True, exist_ok=True)
    runs_rejected_root.mkdir(parents=True, exist_ok=True)
    runs_rejected_by_length_root.mkdir(parents=True, exist_ok=True)
    runs_rejected_by_selection_root.mkdir(parents=True, exist_ok=True)
    runs_policy_refusal_root.mkdir(parents=True, exist_ok=True)
    runs_manual_root.mkdir(parents=True, exist_ok=True)
    runs_logs_root.mkdir(parents=True, exist_ok=True)
    if options.launch_dir is not None:
        output_dir = launch_legacy_output_root(options.launch_dir.resolve(), branch=branch)
    else:
        output_dir = config.root_dir / "output" / ("youtube" if branch == "youtube" else "site")
    run_root = runs_root / "_phase_a"
    if run_root.exists() and not options.resume:
        shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    run_log = runs_root / "run.log"
    _append_run_log(run_log, f"phase_a started run_id={options.story_id}")
    print(f"[PHASE A] started: story_id={options.story_id}", flush=True)
    if options.launch_dir is not None:
        print(f"[PHASE A] launch_dir (legacy under 10_Временные_файлы): {options.launch_dir.resolve()}", flush=True)
    print(f"[PHASE A] phase_a artifacts dir: {run_root}", flush=True)
    print(
        f"[PHASE A] gemini workers requested={max(1, min(5, int(options.gemini_workers)))} "
        f"profiles_total={max(1, min(5, int(options.gemini_profiles_total)))} "
        f"target_active={max(1, min(5, int(options.gemini_target_active_workers)))} "
        f"supervised={'on' if options.gemini_supervised_workers else 'off'} "
        f"selection_stage={options.gemini_stage_key} info_stage={options.gemini_info_stage_key} "
        f"max_stories={max(0, int(options.max_stories)) or 'all'} "
        f"resume={'on' if options.resume else 'off'}",
        flush=True,
    )

    # A1 Intake
    print("[A1] intake scan started", flush=True)
    intake_files = _scan_txt(stories_dir, options.extensions)
    intake_manifest = {
        "stage": "intake",
        "stories_dir": str(stories_dir),
        "intake_mode": "root_txt_only",
        "extensions": options.extensions,
        "max_stories": max(0, int(options.max_stories)),
        "total_files": len(intake_files),
        "nested_txt_ignored_count": count_ignored_nested_txt(stories_dir, options.extensions),
        "files": [str(p) for p in intake_files],
    }
    _write_json(run_root / "intake_manifest.json", intake_manifest)
    _append_run_log(run_log, f"intake done files={len(intake_files)}")
    if not intake_files:
        msg = "В stories/input/ нет рассказов для обработки. Добавьте .txt файлы и запустите снова."
        status.append(
            story_id=options.story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=msg,
        )
        return {"ok": False, "message": msg}
    existing_story_dirs = _collect_existing_story_dirs(runs_root) if options.resume else {}
    workspaces = _build_story_workspaces(runs_stories_root, intake_files, existing_story_dirs=existing_story_dirs)
    for src in intake_files:
        ws = workspaces[str(src)]
        if options.resume and Path(ws["selection_result_path"]).exists():
            continue
        shutil.copy2(src, Path(ws["legacy_text_path"]))
        _write_status(ws, "pending_selection", "intake")
    print(f"[A1] intake done: files={len(intake_files)}", flush=True)

    # A2 LengthFilter
    print("[A2] length filter started", flush=True)
    lf = run_length_filter(
        config=config,
        options=LengthFilterOptions(
            stories_dir=stories_dir,
            short_dir=options.short_dir,
            # Keep input immutable in production pipeline: no file moves from stories/input.
            execute=False,
            words_per_minute=options.words_per_minute,
            min_minutes=options.min_minutes,
            min_words=options.min_words,
            extensions=options.extensions,
            artifacts_dir=run_root,
            root_txt_intake_only=True,
        ),
        pipeline=pipeline,
        story_id=options.story_id,
    )
    if not lf.get("ok", False):
        status.append(
            story_id=options.story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=lf.get("message", "length filter failed"),
        )
        return {"ok": False, "message": lf.get("message", "length filter failed")}
    print("[A2] length filter done", flush=True)
    lf_manifest = Path(str(lf["manifest_path"]))
    lf_data = _read_json(lf_manifest)

    # Build state map from length filter
    state_map: dict[str, dict[str, str]] = {}
    for path in _scan_txt(stories_dir, options.extensions):
        state_map[str(path)] = {"state": "length_passed", "reason": ">= threshold"}
    for moved in lf_data.get("planned_moves", []):
        src = moved.get("source_path")
        if not src:
            continue
        reject_reason = str(
            moved.get("reason")
            or (
                f"word_count={moved.get('word_count', '?')} "
                f"< min_words={moved.get('min_words', options.min_words)}"
            )
        )
        if options.execute:
            state_map[src] = {"state": "short_rejected", "reason": reject_reason}
        else:
            state_map[src] = {"state": "short_rejected", "reason": reject_reason}
    for src, meta in state_map.items():
        ws = workspaces.get(src)
        if not ws:
            continue
        if meta["state"] == "short_rejected":
            _relocate_workspace(ws, runs_rejected_by_length_root)
            _write_status(ws, "rejected", "length_filter", error=meta["reason"])
        else:
            _write_status(ws, "length_passed", "length_filter")

    # A3 Real Gemini gate (required before cleanup)
    print("[A3] Gemini selection gate started", flush=True)
    length_passed = sorted([Path(p) for p, s in state_map.items() if s["state"] == "length_passed"])
    max_stories = max(0, int(options.max_stories))
    skipped_by_limit: list[str] = []
    if max_stories and len(length_passed) > max_stories:
        limited_out = length_passed[max_stories:]
        length_passed = length_passed[:max_stories]
        for path in limited_out:
            state_map[str(path)] = {"state": "skipped_test_limit", "reason": f"test_limit_{max_stories}"}
            skipped_by_limit.append(str(path))
        print(
            f"[A3] test story limit applied: selected_first={len(length_passed)} skipped={len(skipped_by_limit)}",
            flush=True,
        )
    selected_pending: list[str] = []
    rejected_gemini: list[str] = []
    policy_refusal_gemini: list[str] = []
    review_gemini: list[str] = []
    pending_for_selection: list[Path] = []
    for src_path in length_passed:
        src = str(src_path)
        ws = workspaces[src]
        sel_path = Path(ws["selection_result_path"])
        if options.resume and sel_path.exists():
            try:
                sel = _read_json(sel_path)
            except Exception:
                pending_for_selection.append(src_path)
                continue
            verdict = str(sel.get("verdict", "manual_review"))
            if verdict == "rejected":
                state_map[src] = {"state": "rejected_gemini", "reason": "gemini_gate_no"}
                rejected_gemini.append(src)
                _relocate_workspace(ws, runs_rejected_by_selection_root)
                _write_status(ws, "rejected", "selection_gate")
            elif verdict == "selected":
                state_map[src] = {"state": "selected_pending_gemini", "reason": "gemini_gate_yes"}
                selected_pending.append(src)
                _write_status(ws, "selected", "selection_gate")
            elif verdict == "policy_refusal":
                state_map[src] = {"state": "policy_refusal", "reason": "gemini_policy_refusal"}
                policy_refusal_gemini.append(src)
                _relocate_workspace(ws, runs_policy_refusal_root)
                _write_status(ws, "policy_refusal", "selection_gate")
            else:
                state_map[src] = {"state": "manual_review", "reason": "gemini_unparseable_verdict"}
                review_gemini.append(src)
                _relocate_workspace(ws, runs_manual_root)
                _write_status(ws, "manual_review", "selection_gate")
            continue
        pending_for_selection.append(src_path)

    gemini_mapping: list[dict[str, str]] = []
    gemini_root = run_root / "gemini_input" / "stories"
    gemini_msg = "selection reused from existing artifacts"
    gemini_input_stories = run_root / "gemini_input" / "stories"
    skipped_done_info = 0
    if options.resume:
        from orchestrator.phase_a_ops import print_phase_a_selection_resume_preview

        print_phase_a_selection_resume_preview(
            run_id=options.story_id,
            branch=branch,
            run_root=run_root,
            target_active_workers=max(1, min(5, int(options.gemini_target_active_workers))),
            stories_dir=stories_dir,
            extensions=options.extensions,
            logs_dir=config.logs_dir,
        )
    if pending_for_selection:
        res_filtered: list[Path] = []
        for src_path in pending_for_selection:
            src = str(src_path)
            ws = workspaces[src]
            found_dir = _find_gemini_story_dir_for_source(gemini_input_stories, stories_dir, src_path)
            info_p = (found_dir / "info.txt") if found_dir is not None else None
            if found_dir is not None and info_p is not None and _is_nonempty_info_txt(info_p):
                _sync_selection_from_gemini_info(
                    src=src,
                    info_path=info_p,
                    workspace=ws,
                    state_map=state_map,
                    runs_rejected_by_selection_root=runs_rejected_by_selection_root,
                    runs_policy_refusal_root=runs_policy_refusal_root,
                    runs_manual_root=runs_manual_root,
                    selected_pending=selected_pending,
                    rejected_gemini=rejected_gemini,
                    policy_refusal_gemini=policy_refusal_gemini,
                    review_gemini=review_gemini,
                )
                skipped_done_info += 1
                continue
            res_filtered.append(src_path)
        pending_for_selection = res_filtered
        if skipped_done_info:
            print(
                f"[A3] resume: пропущено как уже готовые (непустой info.txt в gemini_input): {skipped_done_info}",
                flush=True,
            )
    if pending_for_selection:
        gemini_root, gemini_mapping = _build_gemini_input(pending_for_selection, stories_dir, run_root)
        _write_json(
            run_root / "gemini_input_queue_map.json",
            {
                "stage": "general_selection",
                "items": [
                    {"source_path": item["source_path"], "gemini_story_dir": item["gemini_story_dir"]}
                    for item in gemini_mapping
                ],
            },
        )
        ok_gemini, gemini_msg = _run_legacy_gemini_gate(
            config,
            gemini_root,
            options.gemini_registry_path,
            options.gemini_stage_key,
            options.gemini_workers,
            runs_logs_root,
            events_file=config.events_file,
            run_id=options.story_id,
            target_active_workers=max(1, min(5, int(options.gemini_target_active_workers))),
            pending_count=len(gemini_mapping),
            profiles_total=max(1, min(5, int(options.gemini_profiles_total))),
            max_restarts_per_profile=max(1, int(options.gemini_max_restarts_per_profile)),
            profile_cooldown_seconds=float(options.gemini_profile_cooldown_seconds),
            supervised_workers=bool(options.gemini_supervised_workers),
        )
        if not ok_gemini:
            status.append(
                story_id=options.story_id,
                pipeline=pipeline,
                stage=stage,
                state="failed",
                message=gemini_msg,
            )
            _write_json(
                run_root / "selection_gate_manifest.json",
                {"stage": "selection_gate", "ok": False, "message": gemini_msg},
            )
            return {"ok": False, "message": gemini_msg}

    for item in gemini_mapping:
        src = item["source_path"]
        info_path = Path(item["gemini_story_dir"]) / "info.txt"
        ws = workspaces[src]
        raw_text = info_path.read_text(encoding="utf-8", errors="ignore") if info_path.exists() else ""
        Path(ws["selection_raw_path"]).write_text(raw_text, encoding="utf-8")
        sel = _parse_selection_result(str(ws["story_id"]), raw_text)
        _write_json(Path(ws["selection_result_path"]), sel)
        verdict = str(sel["verdict"])
        if verdict == "rejected":
            state_map[src] = {"state": "rejected_gemini", "reason": "gemini_gate_no"}
            rejected_gemini.append(src)
            _relocate_workspace(ws, runs_rejected_by_selection_root)
            _write_status(ws, "rejected", "selection_gate")
            continue
        if verdict == "selected":
            state_map[src] = {"state": "selected_pending_gemini", "reason": "gemini_gate_yes"}
            selected_pending.append(src)
            _write_status(ws, "selected", "selection_gate")
            continue
        if verdict == "policy_refusal":
            state_map[src] = {"state": "policy_refusal", "reason": "gemini_policy_refusal"}
            policy_refusal_gemini.append(src)
            _relocate_workspace(ws, runs_policy_refusal_root)
            _write_status(ws, "policy_refusal", "selection_gate")
            continue
        state_map[src] = {"state": "manual_review", "reason": "gemini_unparseable_verdict"}
        review_gemini.append(src)
        _relocate_workspace(ws, runs_manual_root)
        _write_status(ws, "manual_review", "selection_gate")

    selection_stats = {
        "length_passed_considered": len(length_passed),
        "selected_pending_gemini": len(selected_pending),
        "rejected_gemini": len(rejected_gemini),
        "policy_refusal_gemini": len(policy_refusal_gemini),
        "manual_review_gemini": len(review_gemini),
        "skipped_test_limit": len(skipped_by_limit),
        "skipped_resume_nonempty_info_txt": int(skipped_done_info),
    }
    selection_manifest = {
        "stage": "selection_gate",
        "ok": True,
        "mode": "legacy_gemini_gate",
        "resume_skipped_nonempty_info": int(skipped_done_info),
        "message": gemini_msg,
        "selected_pending_gemini": selected_pending,
        "rejected_gemini": rejected_gemini,
        "policy_refusal_gemini": policy_refusal_gemini,
        "manual_review_gemini": review_gemini,
        "skipped_test_limit": skipped_by_limit,
        "stats": selection_stats,
        "rejected_short": [p for p, s in state_map.items() if s["state"] == "short_rejected"],
        "gemini_input_root": str(gemini_root),
    }
    _write_json(run_root / "selection_gate_manifest.json", selection_manifest)
    _append_run_log(
        run_log,
        f"selection done selected={len(selected_pending)} rejected={len(rejected_gemini)} policy_refusal={len(policy_refusal_gemini)} manual_review={len(review_gemini)}",
    )
    print(
        "[A3] selection stats: "
        f"considered={selection_stats['length_passed_considered']} "
        f"yes={selection_stats['selected_pending_gemini']} "
        f"no={selection_stats['rejected_gemini']} "
        f"policy_refusal={selection_stats['policy_refusal_gemini']} "
        f"review={selection_stats['manual_review_gemini']} "
        f"skipped_limit={selection_stats['skipped_test_limit']}",
        flush=True,
    )
    print(
        f"[A3] Gemini selection gate done: selected={len(selected_pending)} rejected={len(rejected_gemini)} policy_refusal={len(policy_refusal_gemini)} review={len(review_gemini)}",
        flush=True,
    )

    if not selected_pending:
        msg = (
            "После этапа отбора нет ни одного selected-рассказа. "
            "Переход к site_info_builder запрещен."
        )
        status.append(
            story_id=options.story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=msg,
        )
        _append_run_log(run_log, msg)
        _write_json(
            run_root / "phase_a_summary.json",
            {
                "ok": False,
                "message": msg,
                "stats": {
                    "intake_total": len(intake_files),
                    "selected_pending_gemini": 0,
                    "rejected_gemini": len(rejected_gemini),
                    "policy_refusal_gemini": len(policy_refusal_gemini),
                    "manual_review_gemini": len(review_gemini),
                    "short_rejected_total": len([1 for _, s in state_map.items() if s["state"] == "short_rejected"]),
                    "cleaned_total": 0,
                    "deferred_total": 0,
                    "skipped_test_limit": len(skipped_by_limit),
                },
                "run_root": str(run_root),
            },
        )
        _write_stage_stop_report(
            runs_root / "REPORT.md",
            options.story_id,
            branch,
            "selection_gate_no_selected",
            msg,
            runs_root,
            output_dir,
        )
        return {"ok": False, "message": msg}

    output_dir.mkdir(parents=True, exist_ok=True)
    output_story_dirs: list[str] = []
    for src in selected_pending:
        ws = workspaces.get(src)
        if not ws:
            continue
        canonical = str(ws["canonical_basename"])
        output_story_dir = output_dir / canonical
        if output_story_dir.exists():
            if options.resume:
                ws["output_story_dir"] = output_story_dir
                output_story_dirs.append(str(output_story_dir))
                canonical_txt = output_story_dir / f"{canonical}.txt"
                if not canonical_txt.exists():
                    shutil.copy2(Path(ws["legacy_text_path"]), canonical_txt)
                continue
            msg = (
                f"Output already exists for canonical_basename='{canonical}': {output_story_dir}. "
                "Удалите/переименуйте папку и запустите снова."
            )
            status.append(
                story_id=options.story_id,
                pipeline=pipeline,
                stage=stage,
                state="failed",
                message=msg,
            )
            _write_stage_stop_report(
                runs_root / "REPORT.md",
                options.story_id,
                branch,
                "output_conflict",
                msg,
                runs_root,
                output_dir,
            )
            return {"ok": False, "message": msg}
    for src in selected_pending:
        ws = workspaces.get(src)
        if not ws:
            continue
        canonical = str(ws["canonical_basename"])
        existing_output_dir = ws.get("output_story_dir")
        if isinstance(existing_output_dir, Path) and existing_output_dir.exists():
            continue
        output_story_dir = output_dir / canonical
        output_story_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(Path(ws["legacy_text_path"]), output_story_dir / f"{canonical}.txt")
        ws["output_story_dir"] = output_story_dir
        output_story_dirs.append(str(output_story_dir))

    # A4 CleanPassedOnly via legacy cleaner
    print("[A4] clean passed stories started", flush=True)
    selected_files = [Path(p) for p in selected_pending]

    # A4.pre — инвалидируем мусорный site_info (битый info.txt / отказ / нет `Визуал:` / title fallback).
    # Источник правды: validate_story_visual_from_raw (см. orchestrator/site_info_fields.py).
    # Это гарантирует, что наличие info.txt больше не считается «готовой историей».
    if options.resume:
        try:
            from orchestrator.site_info_fields import is_site_info_workspace_valid

            invalidated = 0
            invalidated_reasons: dict[str, int] = {}
            for src in selected_pending:
                ws = workspaces.get(src)
                if not ws:
                    continue
                raw_p = Path(ws.get("site_info_raw_path") or "")
                json_p = Path(ws.get("site_info_path") or "")
                info_p = Path(ws.get("legacy_info_path") or "")
                canonical = str(ws.get("canonical_basename") or Path(src).stem)
                has_any = raw_p.is_file() or json_p.is_file() or info_p.is_file()
                if not has_any:
                    continue
                is_valid, reason, _ = is_site_info_workspace_valid(
                    canonical_basename=canonical,
                    raw_path=raw_p if raw_p.is_file() else None,
                    json_path=json_p if json_p.is_file() else None,
                    info_path=info_p if info_p.is_file() else None,
                )
                if is_valid:
                    continue
                # Удаляем мусор, чтобы стандартная resume-логика отправила историю в Gemini.
                for p in (info_p, json_p, raw_p):
                    try:
                        if p.is_file():
                            p.unlink()
                    except OSError:
                        pass
                # И info.txt в output/site/<canonical> тоже — он битый.
                out_story_dir = ws.get("output_story_dir")
                if isinstance(out_story_dir, Path):
                    out_info = out_story_dir / "info.txt"
                    try:
                        if out_info.is_file():
                            out_info.unlink()
                    except OSError:
                        pass
                invalidated += 1
                invalidated_reasons[reason] = invalidated_reasons.get(reason, 0) + 1
                print(
                    f"[A4.pre] invalidated stale site_info: {canonical} reason={reason}",
                    flush=True,
                )
            if invalidated:
                print(
                    f"[A4.pre] stale site_info invalidated: {invalidated} "
                    f"reasons={invalidated_reasons}",
                    flush=True,
                )
            _write_json(
                run_root / "site_info_invalidation_pre.json",
                {
                    "stage": "site_info_invalidation_pre",
                    "invalidated": invalidated,
                    "reasons": invalidated_reasons,
                },
            )
        except Exception as exc:
            print(f"[A4.pre] WARN: invalidation pass failed: {exc}", flush=True)

    reused_ready_sources: list[str] = []
    pending_clean_files: list[Path] = []
    for src in selected_pending:
        ws = workspaces[src]
        can = str(ws["canonical_basename"])
        wdir = Path(ws["story_dir"])
        has_clean = resolve_cleaned_story_txt_path(wdir, can).is_file()
        has_info_json = Path(ws["site_info_path"]).exists()
        has_info_txt = Path(ws["legacy_info_path"]).exists()
        if options.resume and has_clean and has_info_json and has_info_txt:
            reused_ready_sources.append(src)
            state_map[src] = {"state": "cleaned", "reason": "resume_reuse_existing_clean_and_info"}
            _write_status(ws, "site_info_done", "resume_reuse")
            out_story_dir = ws.get("output_story_dir")
            if isinstance(out_story_dir, Path):
                out_clean = resolve_cleaned_story_txt_path(out_story_dir, can)
                if not out_clean.is_file():
                    src_clean = resolve_cleaned_story_txt_path(wdir, can)
                    if src_clean.is_file():
                        shutil.copy2(src_clean, out_story_dir / src_clean.name)
                if not (out_story_dir / "info.txt").exists():
                    shutil.copy2(Path(ws["legacy_info_path"]), out_story_dir / "info.txt")
            continue
        pending_clean_files.append(Path(src))

    mapping: list[dict[str, str]] = []
    clean_msg = "cleaner skipped (resume reuse)"
    if pending_clean_files:
        clean_input_root, mapping = _build_cleaner_input(pending_clean_files, stories_dir, run_root)
        ok_clean, clean_msg = _run_legacy_cleaner(config, clean_input_root)
        if not ok_clean:
            status.append(
                story_id=options.story_id,
                pipeline=pipeline,
                stage=stage,
                state="failed",
                message=clean_msg,
            )
            _write_json(run_root / "clean_manifest.json", {"stage": "clean_passed_only", "ok": False, "message": clean_msg})
            _write_stage_stop_report(
                runs_root / "REPORT.md",
                options.story_id,
                branch,
                "clean_passed_only_failed",
                clean_msg,
                runs_root,
                output_dir,
            )
            return {"ok": False, "message": clean_msg}

    cleaned_out_dir = run_root / "cleaned_texts"
    cleaned_out_dir.mkdir(parents=True, exist_ok=True)
    cleaned_files: list[str] = []
    clean_records: list[dict[str, str]] = []
    cleaned_by_source: dict[str, str] = {}
    for src in reused_ready_sources:
        ws = workspaces[src]
        can = str(ws["canonical_basename"])
        wdir = Path(ws["story_dir"])
        rp = resolve_cleaned_story_txt_path(wdir, can)
        reused_cleaned_path = rp.resolve() if rp.is_file() else Path(ws["cleaned_story_path"]).resolve()
        cleaned_by_source[src] = str(reused_cleaned_path)
        cleaned_files.append(str(reused_cleaned_path))
        clean_records.append(
            {
                "source_path": src,
                "cleaned_path": str(reused_cleaned_path),
                "state_after_clean": "reused",
            }
        )

    for item in mapping:
        story_dir = Path(item["clean_story_dir"])
        src_file = Path(item["clean_story_file"])
        cleaned_file = story_dir / src_file.name
        if not cleaned_file.exists():
            continue
        rel = Path(item["relative_path"])
        dst = cleaned_out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cleaned_file, dst)
        cleaned_files.append(str(dst))
        clean_records.append(
            {
                "source_path": item["source_path"],
                "cleaned_path": str(dst),
                "state_after_clean": "cleaned",
            }
        )
        cleaned_by_source[item["source_path"]] = str(dst)
        state_map[item["source_path"]] = {"state": "cleaned", "reason": "legacy cleaner completed"}
        ws = workspaces[item["source_path"]]
        shutil.copy2(cleaned_file, Path(ws["cleaned_story_path"]))
        shutil.copy2(cleaned_file, Path(ws["legacy_text_path"]))
        out_story_dir = ws.get("output_story_dir")
        if isinstance(out_story_dir, Path):
            shutil.copy2(cleaned_file, out_story_dir / "cleaned_story.txt")
        _write_status(ws, "cleaned", "clean_passed_only")
    _write_json(
        run_root / "clean_manifest.json",
        {
            "stage": "clean_passed_only",
            "ok": True,
            "message": clean_msg,
            "cleaned_total": len(cleaned_files),
            "records": clean_records,
        },
    )
    print(f"[A4] clean done: cleaned={len(cleaned_files)}", flush=True)

    if not cleaned_files:
        msg = (
            "После cleaner не получено ни одного cleaned_story. "
            "Переход к site_info_builder запрещен."
        )
        status.append(
            story_id=options.story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=msg,
        )
        _append_run_log(run_log, msg)
        _write_stage_stop_report(
            runs_root / "REPORT.md",
            options.story_id,
            branch,
            "clean_passed_only_empty",
            msg,
            runs_root,
            output_dir,
        )
        return {"ok": False, "message": msg}

    # A4.1 Info builder via dedicated Gemini stage key
    print("[A4.1] Gemini info builder started", flush=True)
    pending_info_sources: list[str] = []
    for src in selected_pending:
        if src in reused_ready_sources:
            continue
        ws = workspaces[src]
        if options.resume and Path(ws["site_info_path"]).exists() and Path(ws["legacy_info_path"]).exists():
            _write_status(ws, "site_info_done", "resume_reuse")
            continue
        pending_info_sources.append(src)

    info_input_root = run_root / "gemini_info_stage" / "stories"
    info_mapping: list[dict[str, str]] = []
    info_msg = "site info reused from existing artifacts"
    if pending_info_sources:
        cleaned_paths = [Path(cleaned_by_source[src]) for src in pending_info_sources if src in cleaned_by_source]
        info_root = run_root / "gemini_info_stage"
        info_input_root, info_mapping = _build_gemini_input(cleaned_paths, cleaned_out_dir, info_root)
        ok_info, info_msg = _run_legacy_gemini_gate(
            config,
            info_input_root,
            options.gemini_registry_path,
            options.gemini_info_stage_key,
            options.gemini_workers,
            runs_logs_root,
            events_file=config.events_file,
            run_id=options.story_id,
            target_active_workers=max(1, min(5, int(options.gemini_target_active_workers))),
            pending_count=len(info_mapping),
            profiles_total=max(1, min(5, int(options.gemini_profiles_total))),
            max_restarts_per_profile=max(1, int(options.gemini_max_restarts_per_profile)),
            profile_cooldown_seconds=float(options.gemini_profile_cooldown_seconds),
            supervised_workers=bool(options.gemini_supervised_workers),
        )
        if not ok_info:
            status.append(
                story_id=options.story_id,
                pipeline=pipeline,
                stage=stage,
                state="failed",
                message=info_msg,
            )
            _write_json(
                run_root / "site_info_manifest.json",
                {"stage": "site_info_builder", "ok": False, "message": info_msg},
            )
            _write_stage_stop_report(
                runs_root / "REPORT.md",
                options.story_id,
                branch,
                "site_info_builder_failed",
                info_msg,
                runs_root,
                output_dir,
            )
            return {"ok": False, "message": info_msg}

    info_written = len(reused_ready_sources)
    for item in info_mapping:
        rel = Path(item["relative_path"])
        src_info = Path(item["gemini_story_dir"]) / "info.txt"
        if not src_info.exists():
            continue
        src_story = str((cleaned_out_dir / rel).resolve())
        legacy_story_src = ""
        for k, v in cleaned_by_source.items():
            if v == src_story:
                legacy_story_src = k
                break
        if not legacy_story_src:
            continue
        ws = workspaces[legacy_story_src]
        raw_text = src_info.read_text(encoding="utf-8", errors="ignore")
        Path(ws["site_info_raw_path"]).write_text(raw_text, encoding="utf-8")
        site_info = _parse_site_info_result(str(ws["story_id"]), str(ws["canonical_basename"]), raw_text)
        _write_json(Path(ws["site_info_path"]), site_info)
        legacy_info_path = Path(ws["legacy_info_path"])
        if legacy_info_path.exists():
            existing = legacy_info_path.read_text(encoding="utf-8", errors="ignore")
            if _looks_like_selection_output(existing):
                msg = (
                    f"info.txt contains selection output for story_id={ws['story_id']}. "
                    "Нужно пересобрать артефакты."
                )
                status.append(
                    story_id=options.story_id,
                    pipeline=pipeline,
                    stage=stage,
                    state="failed",
                    message=msg,
                )
                return {"ok": False, "message": msg}
        legacy_info_path.write_text(_render_legacy_info(site_info), encoding="utf-8")
        _write_status(ws, "site_info_done", "site_info_builder")
        out_story_dir = ws.get("output_story_dir")
        if isinstance(out_story_dir, Path):
            (out_story_dir / "info.txt").write_text(_render_legacy_info(site_info), encoding="utf-8")
        dst_info = (cleaned_out_dir / rel).with_name("info.txt")
        dst_info.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_info, dst_info)
        info_written += 1
    _write_json(
        run_root / "site_info_manifest.json",
        {
            "stage": "site_info_builder",
            "ok": True,
            "message": info_msg,
            "info_written": info_written,
            "info_input_root": str(info_input_root),
        },
    )
    _append_run_log(run_log, f"site_info done info_written={info_written}")
    print(f"[A4.1] Gemini info builder done: info_written={info_written}", flush=True)

    visual_export_dir = runs_root / "visual"
    from orchestrator.site_visual_validate import (
        run_retry_invalid_site_info_visuals,
        run_validate_site_info_visuals,
    )

    validate_visual = run_validate_site_info_visuals(
        runs_stories_dir=runs_root / "stories",
        output_site_dir=output_dir,
        export_dir=visual_export_dir,
        runs_root=runs_root,
        summary_extra={"stage": "validate_after_site_info"},
    )
    _write_json(
        run_root / "validate_site_info_visuals.json",
        {
            "stage": "validate_site_info_visuals",
            "ok": validate_visual.ok,
            "message": validate_visual.message,
            "report": validate_visual.report,
            "human_dir": str(validate_visual.human_dir) if validate_visual.human_dir else "",
            "human_xlsx_path": str(validate_visual.human_xlsx_path) if validate_visual.human_xlsx_path else "",
            "human_sync_error": validate_visual.human_sync_error,
        },
    )
    if not validate_visual.ok:
        status.append(
            story_id=options.story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=validate_visual.message,
        )
        return {"ok": False, "message": validate_visual.message}

    # A4.1.6 Auto-retry invalid через site_info_builder.
    # Управляется env-флагом CF_AUTO_RETRY_VISUALS (по умолчанию ON для полного site-цикла).
    auto_retry_env = (os.getenv("CF_AUTO_RETRY_VISUALS") or "").strip()
    do_auto_retry = auto_retry_env not in {"0", "false", "no", "off"}
    invalid_before = int(validate_visual.report.get("invalid_prompts", 0) or 0)
    retryable_reasons = {
        "no_visual_prompt_found",
        "gemini_refusal_or_policy_response",
        "prompt_too_short",
        "truncated_prompt_blocked",
        "title_fallback_blocked",
        "missing_raw",
    }
    has_retryable = any(
        int(validate_visual.report.get(k, 0) or 0) > 0 for k in retryable_reasons
    )
    if do_auto_retry and invalid_before > 0 and has_retryable:
        print(
            f"[A4.1.6] auto-retry invalid visuals: invalid_before={invalid_before} "
            f"(disable via CF_AUTO_RETRY_VISUALS=0)",
            flush=True,
        )
        retry_result = run_retry_invalid_site_info_visuals(
            config=config,
            runs_root=runs_root,
            output_site_dir=output_dir,
            export_dir=visual_export_dir,
            gemini_registry_path=options.gemini_registry_path,
            gemini_info_stage_key=options.gemini_info_stage_key,
            gemini_workers=1,
            max_retry_attempts=max(1, int(os.getenv("CF_AUTO_RETRY_VISUALS_MAX_ATTEMPTS", "2") or "2")),
            execute=True,
            gemini_target_active_workers=max(1, min(5, int(options.gemini_target_active_workers))),
            gemini_profiles_total=max(1, min(5, int(options.gemini_profiles_total))),
            gemini_max_restarts_per_profile=max(1, int(options.gemini_max_restarts_per_profile)),
            gemini_profile_cooldown_seconds=float(options.gemini_profile_cooldown_seconds),
            gemini_supervised_workers=False,
            profile_index=None,
            auto_profile=True,
        )
        _write_json(
            run_root / "auto_retry_site_info_visuals.json",
            {
                "stage": "auto_retry_site_info_visuals",
                "ok": retry_result.ok,
                "status": retry_result.status,
                "exit_reason": retry_result.exit_reason,
                "retried": retry_result.retried,
                "retry_candidates": retry_result.retry_candidates,
                "retry_succeeded": retry_result.retry_succeeded,
                "retry_failed": retry_result.retry_failed,
                "retry_skipped": retry_result.retry_skipped,
                "selected_gemini_profile": retry_result.selected_gemini_profile,
                "preflight_status": retry_result.preflight_status,
                "browser_launch_error": retry_result.browser_launch_error[:1000] if retry_result.browser_launch_error else "",
                "final_valid_count": retry_result.final_valid_count,
                "final_invalid_count": retry_result.final_invalid_count,
                "message": retry_result.message,
            },
        )
        if retry_result.validate_after is not None:
            validate_visual = retry_result.validate_after
        print(
            f"[A4.1.6] auto-retry done: status={retry_result.status} "
            f"valid={retry_result.final_valid_count} invalid={retry_result.final_invalid_count}",
            flush=True,
        )

    for src in selected_pending:
        ws = workspaces[src]
        out_story_dir = ws.get("output_story_dir")
        if not isinstance(out_story_dir, Path):
            continue
        can = str(ws["canonical_basename"])
        sid = str(ws["story_id"])
        wdir = Path(ws["story_dir"])
        info_p = out_story_dir / "info.txt"
        if not info_p.is_file():
            info_p = Path(ws["legacy_info_path"])
        _finalize_cleaned_txt_voice_rename(
            story_id=sid,
            canonical=can,
            info_path=info_p,
            out_story_dir=out_story_dir,
            workspace_story_dir=wdir,
            run_log=run_log,
        )

    # A4.2 Visual stage (manual export or auto image generation)
    visual_mode = str(options.visual_mode).strip().lower() or "manual"
    if visual_mode not in {"manual", "auto"}:
        visual_mode = "manual"
    director_module_rel = config.legacy_modules.get("director_2_0", "legacy/director_2_0")
    workflow_path = (config.root_dir / director_module_rel / "FLUX 2 — Simple Text-To-Image.json").resolve()
    print(f"[A4.2] visual stage started: mode={visual_mode}", flush=True)
    visual_result = run_visual_stage(
        output_dir=output_dir,
        export_dir=visual_export_dir,
        mode=visual_mode,
        pod_url=options.visual_pod_url,
        workflow_path=workflow_path,
        runs_site_stories_dir=runs_root / "stories",
    )
    _write_json(
        run_root / "visual_manifest.json",
        {
            "stage": "visual",
            "mode": visual_mode,
            "ok": visual_result.ok,
            "generated_count": visual_result.generated_count,
            "failed_count": visual_result.failed_count,
            "csv_path": str(visual_result.csv_path),
            "xlsx_path": str(visual_result.xlsx_path) if visual_result.xlsx_path else None,
            "invalid_csv_path": str(visual_result.invalid_csv_path) if visual_result.invalid_csv_path else None,
            "build_report_path": str(visual_result.build_report_path) if visual_result.build_report_path else None,
            "valid_row_count": visual_result.valid_row_count,
            "invalid_row_count": visual_result.invalid_row_count,
            "errors": visual_result.errors,
        },
    )
    if not visual_result.ok:
        msg = (
            "Visual stage failed: "
            + ("; ".join(visual_result.errors) if visual_result.errors else "unknown visual error")
        )
        status.append(
            story_id=options.story_id,
            pipeline=pipeline,
            stage=stage,
            state="failed",
            message=msg,
        )
        _write_stage_stop_report(
            runs_root / "REPORT.md",
            options.story_id,
            branch,
            "visual_failed",
            msg,
            runs_root,
            output_dir,
        )
        return {"ok": False, "message": msg}
    print(
        f"[A4.2] visual stage done: mode={visual_mode} generated={visual_result.generated_count}",
        flush=True,
    )

    # A5 BranchSplit (real boundary before next connected runtime stage)
    print("[A5] branch split started", flush=True)
    site_queue: list[str] = []
    youtube_queue: list[str] = []
    deferred: list[dict[str, str]] = []
    for src in selected_pending:
        st = state_map.get(src, {}).get("state")
        if st == "cleaned":
            deferred.append(
                {
                    "source_path": src,
                    "cleaned_path": cleaned_by_source.get(src, ""),
                    "run_story_dir": str(workspaces[src]["story_dir"]),
                    "canonical_basename": str(workspaces[src]["canonical_basename"]),
                }
            )
            state_map[src] = {"state": "routed_deferred", "reason": "waiting_next_real_stage"}
            _write_status(workspaces[src], "waiting_next_real_stage", "branch_split")
    split_manifest = {
        "stage": "branch_split",
        "mode": "waiting_next_real_stage",
        "site_queue_count": len(site_queue),
        "youtube_queue_count": len(youtube_queue),
        "deferred_count": len(deferred),
    }
    _write_json(run_root / "branch_split_manifest.json", split_manifest)
    print(
        f"[A5] branch split done: site_queue={len(site_queue)} youtube_queue={len(youtube_queue)} deferred={len(deferred)}",
        flush=True,
    )

    # A6 ReadyQueues artifacts
    print("[A6] ready queues write started", flush=True)
    queues_dir = run_root / "ready_queues"
    _write_json(queues_dir / "site_queue.json", {"queue": "site_queue", "items": site_queue})
    _write_json(queues_dir / "youtube_queue.json", {"queue": "youtube_queue", "items": youtube_queue})
    _write_json(queues_dir / "deferred.json", {"queue": "deferred", "items": deferred})
    print("[A6] ready queues write done", flush=True)

    state_transition = []
    for src, meta in sorted(state_map.items()):
        state_transition.append(
            {
                "source_path": src,
                "story_id": workspaces[src]["story_id"] if src in workspaces else "",
                "state": meta["state"],
                "reason": meta["reason"],
            }
        )
    _write_json(run_root / "story_state_manifest.json", {"stories": state_transition})
    _append_run_log(run_log, f"routing done deferred={len(deferred)}")
    manifest_payload = {
        "run_id": options.story_id,
        "pipeline": branch,
        "input_dir": str(stories_dir),
        "run_dir": str(runs_root.resolve()),
        "reports_dir": str(runs_root.resolve()),
        "legacy_reports_dir": None,
        "scaffold_used": False,
        "output_dir": str(output_dir),
        "accepted_in_output": len(output_story_dirs),
        "output_story_folders_created": output_story_dirs,
        "stage_stop": "waiting_next_real_stage",
        "total_input": len(intake_files),
        "length_passed": len([1 for _, s in state_map.items() if s["state"] != "short_rejected"]),
        "selected": len(selected_pending),
        "rejected": len(rejected_gemini),
        "policy_refusal": len(policy_refusal_gemini),
        "manual_review": len(review_gemini),
        "site_info_done": info_written,
        "visual_done": visual_result.generated_count,
        "tts_done": 0,
        "published": 0,
        "errors": 0,
        "path_to_stories": str(runs_stories_root),
        "path_to_rejected": str(runs_rejected_root),
        "path_to_rejected_by_length": str(runs_rejected_by_length_root),
        "path_to_rejected_by_selection": str(runs_rejected_by_selection_root),
        "path_to_policy_refusal": str(runs_policy_refusal_root),
        "path_to_manual_review": str(runs_manual_root),
        "path_to_logs": str(runs_logs_root),
        "path_to_visual_csv": str(visual_result.csv_path),
        "path_to_visual_xlsx": str(visual_result.xlsx_path) if visual_result.xlsx_path else "",
        "visual_mode": visual_mode,
        "deletable": True,
    }
    _write_json(runs_root / "site_pipeline_manifest.json", manifest_payload)
    _write_json(runs_root / "manifest.json", manifest_payload)
    _write_json(
        runs_root / "selection_index.json",
        {
            "selected_story_ids": [str(workspaces[p]["story_id"]) for p in selected_pending if p in workspaces],
            "rejected_story_ids": [str(workspaces[p]["story_id"]) for p in rejected_gemini if p in workspaces],
            "policy_refusal_story_ids": [str(workspaces[p]["story_id"]) for p in policy_refusal_gemini if p in workspaces],
            "manual_review_story_ids": [str(workspaces[p]["story_id"]) for p in review_gemini if p in workspaces],
        },
    )
    selected_story_ids = [str(workspaces[p]["story_id"]) for p in selected_pending if p in workspaces]
    report_md = runs_root / "REPORT.md"
    report_md.write_text(
        "\n".join(
            [
                f"# {'YouTube' if branch == 'youtube' else 'Site'} Pipeline Report: {options.story_id}",
                "",
                "## Pipeline metadata",
                "- scaffold_used: false",
                "- production_ready: false",
                "",
                f"- Входных рассказов: {len(intake_files)}",
                f"- Отсеяно по длине: {len([1 for _, s in state_map.items() if s['state']=='short_rejected'])}",
                f"- selected: {len(selected_pending)}",
                f"- rejected: {len(rejected_gemini)}",
                f"- policy_refusal: {len(policy_refusal_gemini)}",
                f"- manual_review: {len(review_gemini)}",
                f"- site_info_done: {info_written}",
                f"- accepted_in_output: {len(output_story_dirs)}",
                f"- visual_mode: {visual_mode}",
                f"- visual_done: {visual_result.generated_count}",
                "",
                "## Что пошло дальше",
                f"- deferred: {len(deferred)}",
                "",
                "## Рабочий output",
                f"- {output_dir}",
                f"- created_story_folders: {len(output_story_dirs)}",
                *(f"- {Path(x).name}" for x in output_story_dirs),
                "",
                "## Финальные папки рассказов",
                f"- {runs_stories_root}",
                f"- rejected/by_length: {runs_rejected_by_length_root}",
                f"- rejected/by_selection: {runs_rejected_by_selection_root}",
                f"- policy_refusal: {runs_policy_refusal_root}",
                f"- manual_review: {runs_manual_root}",
                "",
                "## На каком этапе остановились",
                "- waiting_next_real_stage (site_preparation_done)",
                "",
                "## Визуал",
                f"- mode: {visual_mode}",
                f"- csv: {visual_result.csv_path}",
                f"- xlsx: {visual_result.xlsx_path if visual_result.xlsx_path else 'not_generated'}",
                f"- generated_images: {visual_result.generated_count}",
                "",
                "## Что сделать вручную",
                "- Если visual_mode=manual: заполнить/проверить visual_prompts и добавить .jpg в output/site/<story>/.",
                "",
                "## Selected stories",
                *(f"- {sid}" for sid in selected_story_ids),
            ]
        ),
        encoding="utf-8",
    )

    summary = (
        f"phase A done; intake={len(intake_files)}; selected_pending={len(selected_pending)}; "
        f"cleaned={len(cleaned_files)}; site_queue={len(site_queue)}; "
        f"youtube_queue={len(youtube_queue)}; deferred={len(deferred)}; "
        f"skipped_test_limit={len(skipped_by_limit)}"
    )
    status.append(
        story_id=options.story_id,
        pipeline=pipeline,
        stage=stage,
        state="done",
        message=summary,
    )
    _write_json(
        run_root / "phase_a_summary.json",
        {
            "ok": True,
            "summary": summary,
            "stats": {
                "intake_total": len(intake_files),
                "selected_pending_gemini": len(selected_pending),
                "rejected_gemini": len(rejected_gemini),
                "policy_refusal_gemini": len(policy_refusal_gemini),
                "manual_review_gemini": len(review_gemini),
                "short_rejected_total": len([1 for _, s in state_map.items() if s["state"] == "short_rejected"]),
                "cleaned_total": len(cleaned_files),
                "deferred_total": len(deferred),
                "skipped_test_limit": len(skipped_by_limit),
                "accepted_in_output": len(output_story_dirs),
            },
            "run_root": str(run_root),
            "runs_root": str(runs_root),
            "report_md": str(report_md),
            "stories_root": str(runs_stories_root),
            "phase_order": [
                "intake",
                "length_filter",
                "selection_gate_gemini",
                "clean_passed_only",
                "site_info_builder",
                "branch_split",
                "ready_queues",
            ],
        },
    )
    print(f"[PHASE A] finished: {summary}", flush=True)
    _append_run_log(run_log, f"phase_a finished summary={summary}")
    return {
        "ok": True,
        "summary": summary,
        "run_root": str(run_root),
        "runs_root": str(runs_root),
        "report_md": str(report_md),
        "stories_root": str(runs_stories_root),
    }
