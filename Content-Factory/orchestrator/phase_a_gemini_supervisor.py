from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from orchestrator.events import EventLogger
from orchestrator.gemini_diagnostics import extract_model_switch_failure, has_model_switch_failure, read_text_tail, write_json

# Должен совпадать с legacy/Gemini_Auto/gemini_auto.py EXIT_CODE_SESSION_SEND_EXHAUSTED
GEMINI_EXIT_SESSION_SEND_EXHAUSTED = 44


def gemini_worker_subprocess_creationflags() -> int:
    """
    Отдельное консольное окно на каждый gemini_auto только при GEMINI_SPAWN_NEW_CONSOLE=1.
    По умолчанию 0: лог идёт в текущий терминал оркестратора — иначе на Windows всплывают
    пустые окна cmd, которые сразу закрываются при выходе воркера.
    """
    if (os.getenv("GEMINI_SPAWN_NEW_CONSOLE") or "").strip() == "1":
        return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return 0


def _is_nonempty_info_txt(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def count_pending_gemini_folders(gemini_stories_root: Path) -> int:
    """Папки с исходным .txt, где нет непустого info.txt (очередь Gemini)."""
    root = gemini_stories_root.resolve()
    if not root.is_dir():
        return 0
    pending_parents: set[Path] = set()
    story_dir_rx = re.compile(r".+_\d{6}$")
    for txt in root.rglob("*.txt"):
        if txt.name.lower() == "info.txt":
            continue
        # Жанровые агрегаты не являются story-элементами очереди.
        if txt.name.lower() in {"result_report.txt"}:
            continue
        parent = txt.parent
        # В очередь считаем только canonical story dirs (foo_000001), а не category dirs.
        if not story_dir_rx.match(parent.name):
            continue
        if _is_nonempty_info_txt(parent / "info.txt"):
            continue
        pending_parents.add(parent)
    return len(pending_parents)


def maybe_print_gemini_queue_progress(
    *,
    stage_key: str,
    gemini_stories_root: Path,
    pending: int | None,
    expected_total: int | None,
    progress_state: dict[str, Any],
) -> None:
    """
    Печать в терминал при изменении числа папок-историй без непустого info.txt
    (та же метрика, что у супервизора). Один вывод на каждое уменьшение remaining.
    """
    p = int(count_pending_gemini_folders(gemini_stories_root)) if pending is None else int(pending)
    if progress_state.get("initial") is None:
        if expected_total is not None and int(expected_total) > 0:
            progress_state["initial"] = max(int(expected_total), p)
        else:
            progress_state["initial"] = p
    total = int(progress_state["initial"])
    last = progress_state.get("last_pending")
    if last is not None and p == last:
        return
    done = max(0, total - p)
    print(
        f"[GEMINI][{stage_key}] remaining={p} total={total} done={done}",
        flush=True,
    )
    progress_state["last_pending"] = p


@dataclass
class GeminiSupervisorOptions:
    """Пул профилей Chrome (user_data_*) и ограничение одновременных процессов gemini_auto."""

    target_active_workers: int = 3
    profiles_total: int = 5
    max_restarts_per_profile: int = 3
    profile_cooldown_seconds: float = 900.0
    poll_interval_seconds: float = 0.75
    max_supervisor_seconds: float = 0.0
    run_id: str = ""
    pipeline: str = "phase-a"
    gemini_start_mode: str = "staggered-first-result"
    ramp_up_stop_on_system_fail: bool = True


@dataclass
class _ProfileRuntime:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    session_restarts: int = 0
    last_error: str = ""
    on_fast_due_to_thinking_limit: bool = False


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _emit(
    events_file: Path | None,
    *,
    run_id: str,
    action: str,
    result: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if events_file is None:
        return
    try:
        EventLogger(events_file).emit(
            run_id=run_id or "-",
            story_id="-",
            pipeline="phase-a",
            stage="gemini_supervisor",
            action=action,
            result=result,
            message=message,
            payload=payload or {},
        )
    except OSError:
        pass


def run_supervised_gemini_workers(
    *,
    gemini_script: Path,
    gemini_stories_root: Path,
    logs_dir: Path,
    stage_key: str,
    options: GeminiSupervisorOptions,
    build_proc_env: Callable[[int, int], dict[str, str] | None],
    events_file: Path | None = None,
    expected_gemini_pending_total: int | None = None,
) -> tuple[bool, str]:
    """
    Держит одновременно не более ``target_active_workers`` процессов ``gemini_auto.py``,
    индексы профилей 0..profiles_total-1. При падении процесса сначала тот же профиль,
    после max_restarts — cooldown и другой профиль из пула.
    """
    profiles_total = max(1, min(5, int(options.profiles_total)))
    target = max(1, min(int(options.target_active_workers), profiles_total))
    max_restarts = max(1, int(options.max_restarts_per_profile))
    cooldown = float(options.profile_cooldown_seconds)
    poll_iv = float(options.poll_interval_seconds)
    run_id = str(options.run_id or "").strip() or "-"
    started_at = time.time()
    max_supervisor_seconds = float(options.max_supervisor_seconds or 0.0)

    state_path = (logs_dir / "gemini_supervisor_state.json").resolve()
    profiles: dict[int, _ProfileRuntime] = {i: _ProfileRuntime() for i in range(profiles_total)}
    active: dict[int, subprocess.Popen[bytes]] = {}
    from orchestrator.gemini_worker_scheduler import normalize_gemini_start_mode
    from orchestrator.youtube_full_auto.bridge_errors import classify_from_text

    start_mode = normalize_gemini_start_mode(str(getattr(options, "gemini_start_mode", "") or ""))
    max_spawn_profile = profiles_total - 1 if start_mode == "immediate" else 0
    profile_first_outcome: set[int] = set()
    # Chrome-профиль (idx) ≠ слот для assign_worker_slice в gemini_auto: при пропуске профиля 1
    # и воркерах 0+2 оба имели бы WORKER_INDEX%PARALLEL_WORKERS==0 без отдельного slice.
    profile_parallel_slot: dict[int, int] = {}
    profile_meta: dict[int, dict[str, str]] = {}

    def parallel_slice_index_for_profile(profile_idx: int) -> int:
        if profile_idx in profile_parallel_slot:
            return profile_parallel_slot[profile_idx]
        used = {profile_parallel_slot[p] for p in active if p in profile_parallel_slot}
        for s in range(target):
            if s not in used:
                profile_parallel_slot[profile_idx] = s
                return s
        profile_parallel_slot[profile_idx] = profile_idx % max(1, target)
        return profile_parallel_slot[profile_idx]

    def snapshot() -> dict[str, Any]:
        now = time.time()
        alive = [i for i, p in active.items() if p.poll() is None]
        temporarily_unavailable = [i for i, pr in profiles.items() if pr.cooldown_until > now]
        return {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": run_id,
            "stage_key": stage_key,
            "target_active_workers": target,
            "profiles_total": profiles_total,
            "current_active_workers": len(alive),
            "active_profile_indices": alive,
            "temporarily_unavailable_profiles": temporarily_unavailable,
            "profiles": {
                str(i): {
                    "consecutive_failures": pr.consecutive_failures,
                    "cooldown_until": pr.cooldown_until,
                    "session_restarts": pr.session_restarts,
                    "last_error": pr.last_error,
                    "cooldown_remaining_s": max(0.0, pr.cooldown_until - now),
                    "on_fast_due_to_thinking_limit": pr.on_fast_due_to_thinking_limit,
                }
                for i, pr in profiles.items()
            },
        }

    def spawn(profile_idx: int) -> bool:
        slice_idx = parallel_slice_index_for_profile(profile_idx)
        env = build_proc_env(profile_idx, slice_idx)
        if env is None:
            return False
        profile_meta[profile_idx] = {
            "user_data_dir": env.get("GEMINI_USER_DATA_DIR", ""),
            "account_email": env.get("GEMINI_ACCOUNT_EMAIL", ""),
            "gemini_url": env.get("GEMINI_URL", ""),
        }
        cmd = [sys.executable, str(gemini_script)]
        creationflags = gemini_worker_subprocess_creationflags()
        _emit(
            events_file,
            run_id=run_id,
            action="worker_started",
            result="ok",
            message=f"profile={profile_idx}",
            payload={
                "profile_idx": profile_idx,
                "parallel_slice_index": slice_idx,
                "stage_key": stage_key,
            },
        )
        proc = subprocess.Popen(cmd, env=env, creationflags=creationflags)
        active[profile_idx] = proc
        profiles[profile_idx].session_restarts += 1
        _write_state(state_path, snapshot())
        print(
            f"[A3/supervisor] started profile={profile_idx + 1}/{profiles_total} "
            f"active={len(active)}/{target} parallel_slice={slice_idx} cmd={' '.join(cmd)}",
            flush=True,
        )
        return True

    def terminate_active_workers() -> None:
        for _idx, proc in list(active.items()):
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass
        for _idx, proc in list(active.items()):
            try:
                if proc.poll() is None:
                    proc.kill()
            except OSError:
                pass

    def fail_fast_model_switch_report(profile_idx: int, code: int) -> dict[str, Any] | None:
        log_path = (logs_dir / f"{stage_key}_worker_{profile_idx + 1}.log").resolve()
        tail = read_text_tail(log_path)
        if not has_model_switch_failure(tail):
            return None
        meta = profile_meta.get(profile_idx, {})
        report_path = (logs_dir / "phase_a_gemini_error_report.json").resolve()
        report = extract_model_switch_failure(
            tail,
            stage_key=stage_key,
            worker_id=profile_idx + 1,
            profile_index=profile_idx,
            account_email=meta.get("account_email", ""),
            user_data_dir=meta.get("user_data_dir", ""),
            gemini_url=meta.get("gemini_url", ""),
            log_path=str(log_path),
            report_path=str(report_path),
            next_action="Запустите python -m orchestrator site gemini-preflight --launch-name <launch> и проверьте указанный user_data профиль.",
        )
        report["exit_code"] = int(code)
        write_json(report_path, report)
        return report

    def try_fill_slots() -> None:
        if len(active) >= target:
            return
        now = time.time()
        for profile_idx in range(profiles_total):
            if len(active) >= target:
                return
            if start_mode != "immediate" and profile_idx > max_spawn_profile:
                continue
            if profile_idx in active and active[profile_idx].poll() is None:
                continue
            pr = profiles[profile_idx]
            if pr.cooldown_until > now:
                continue
            spawn(profile_idx)

    def maybe_ramp_profile(profile_idx: int, code: int) -> None:
        nonlocal max_spawn_profile
        if start_mode != "staggered-first-result":
            return
        if profile_idx in profile_first_outcome:
            return
        profile_first_outcome.add(profile_idx)
        if code == 0:
            max_spawn_profile = max(max_spawn_profile, profile_idx + 1)
            print(
                f"[A3/supervisor] ramp-up: profile {profile_idx} first outcome ok → "
                f"max_spawn_profile={max_spawn_profile}",
                flush=True,
            )
            return
        if not bool(getattr(options, "ramp_up_stop_on_system_fail", True)):
            max_spawn_profile = max(max_spawn_profile, profile_idx + 1)
            return
        log_path = (logs_dir / f"{stage_key}_worker_{profile_idx + 1}.log").resolve()
        reason = classify_from_text(text=read_text_tail(log_path), exit_code=code)
        if reason and reason not in {"unknown_bridge_failure", "bridge_subprocess_failed"}:
            print(
                f"[A3/supervisor] ramp-up paused after profile {profile_idx} system fail: {reason}",
                flush=True,
            )
            return
        max_spawn_profile = max(max_spawn_profile, profile_idx + 1)

    degraded_logged = False
    progress_state: dict[str, Any] = {}

    while True:
        now = time.time()
        if max_supervisor_seconds > 0 and (now - started_at) > max_supervisor_seconds:
            for _idx, proc in list(active.items()):
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except OSError:
                    pass
            for _idx, proc in list(active.items()):
                try:
                    if proc.poll() is None:
                        proc.kill()
                except OSError:
                    pass
            _write_state(state_path, snapshot())
            msg = (
                "supervisor: timeout reached while pending stories remain "
                f"(max_supervisor_seconds={int(max_supervisor_seconds)})"
            )
            _emit(events_file, run_id=run_id, action="supervisor_timeout", result="error", message=msg, payload=snapshot())
            return False, msg
        for idx, pr in profiles.items():
            if pr.cooldown_until > 0 and pr.cooldown_until <= now:
                _emit(
                    events_file,
                    run_id=run_id,
                    action="profile_returned_to_pool",
                    result="ok",
                    message=f"profile={idx} cooldown_elapsed",
                    payload={"profile_idx": idx},
                )
                pr.consecutive_failures = 0
                pr.cooldown_until = 0.0

        finished_indices: list[int] = []
        for idx, proc in list(active.items()):
            code = proc.poll()
            if code is None:
                continue
            finished_indices.append(idx)
            del active[idx]
            pr = profiles[idx]
            print(f"[A3/supervisor] profile={idx + 1} exited code={code}", flush=True)
            maybe_ramp_profile(idx, int(code))
            if code == 0:
                pr.consecutive_failures = 0
                pr.last_error = ""
                _emit(
                    events_file,
                    run_id=run_id,
                    action="worker_exit_ok",
                    result="ok",
                    message=f"profile={idx} clean_exit",
                    payload={"profile_idx": idx, "exit_code": code},
                )
            elif code == GEMINI_EXIT_SESSION_SEND_EXHAUSTED:
                # Один «мёртвый» аккаунт: сразу cooldown, слоты занимают другие профили (другие почты).
                pr.last_error = "session_send_exhausted_rotate_profile"
                pr.consecutive_failures = 0
                pr.cooldown_until = now + cooldown
                _emit(
                    events_file,
                    run_id=run_id,
                    action="worker_exit_session_send_exhausted",
                    result="warn",
                    message=f"profile={idx} code={code} cooldown_s={cooldown}",
                    payload={"profile_idx": idx, "exit_code": code, "cooldown_until": pr.cooldown_until},
                )
                _emit(
                    events_file,
                    run_id=run_id,
                    action="profile_marked_unhealthy",
                    result="warn",
                    message=f"profile={idx} cooldown_s={cooldown} reason=session_send_exhausted",
                    payload={"profile_idx": idx, "cooldown_until": pr.cooldown_until},
                )
            else:
                model_report = fail_fast_model_switch_report(idx, int(code or 0))
                if model_report is not None:
                    terminate_active_workers()
                    _write_state(state_path, snapshot())
                    msg = (
                        f"supervisor: fail-fast {model_report.get('failure_kind', 'gemini_model_failure')} "
                        f"profile={idx + 1} story_id={model_report.get('story_id') or 'unknown'} "
                        f"report={model_report.get('report_path')}"
                    )
                    _emit(
                        events_file,
                        run_id=run_id,
                        action="worker_model_switch_failed",
                        result="error",
                        message=msg,
                        payload=model_report,
                    )
                    return False, msg
                pr.consecutive_failures += 1
                pr.last_error = f"exit_code={code}"
                _emit(
                    events_file,
                    run_id=run_id,
                    action="worker_crashed",
                    result="error",
                    message=f"profile={idx} code={code}",
                    payload={"profile_idx": idx, "exit_code": code},
                )
                _emit(
                    events_file,
                    run_id=run_id,
                    action="worker_restart_attempt",
                    result="pending",
                    message=f"profile={idx} failures={pr.consecutive_failures}",
                    payload={"profile_idx": idx, "consecutive_failures": pr.consecutive_failures},
                )
                if pr.consecutive_failures >= max_restarts:
                    pr.cooldown_until = now + cooldown
                    _emit(
                        events_file,
                        run_id=run_id,
                        action="profile_marked_unhealthy",
                        result="warn",
                        message=f"profile={idx} cooldown_s={cooldown}",
                        payload={"profile_idx": idx, "cooldown_until": pr.cooldown_until},
                    )
                    _emit(
                        events_file,
                        run_id=run_id,
                        action="profile_temporarily_unavailable",
                        result="warn",
                        message=f"profile={idx} temporarily_unavailable cooldown_s={cooldown}",
                        payload={"profile_idx": idx, "cooldown_until": pr.cooldown_until},
                    )
                    _emit(
                        events_file,
                        run_id=run_id,
                        action="worker_restart_failed",
                        result="error",
                        message=f"profile={idx} max_restarts",
                        payload={"profile_idx": idx},
                    )

        _write_state(state_path, snapshot())

        avail = [
            i
            for i in range(profiles_total)
            if profiles[i].cooldown_until <= now and build_proc_env(i, 0) is not None
        ]
        pending = count_pending_gemini_folders(gemini_stories_root)
        maybe_print_gemini_queue_progress(
            stage_key=stage_key,
            gemini_stories_root=gemini_stories_root,
            pending=pending,
            expected_total=expected_gemini_pending_total,
            progress_state=progress_state,
        )

        if not active and pending == 0:
            break

        if not active and not avail:
            msg = "supervisor: no spawnable profiles (registry/login/cooldown) while work remains"
            _emit(events_file, run_id=run_id, action="active_workers_degraded", result="error", message=msg, payload={})
            return False, msg

        if len(active) < target and pending > 0:
            try_fill_slots()
            if len(active) < target and len(avail) < target and not degraded_logged:
                degraded_logged = True
                _emit(
                    events_file,
                    run_id=run_id,
                    action="active_workers_degraded",
                    result="warn",
                    message=f"active={len(active)} target={target} avail_profiles={len(avail)} pending={pending}",
                    payload=snapshot(),
                )

        time.sleep(poll_iv)

    merge_worker_telemetry_from_logs(logs_dir, profiles_total, stage_key)
    snap = snapshot()
    snap["pending_gemini_folders_est"] = count_pending_gemini_folders(gemini_stories_root)
    _write_state(state_path, snap)
    return True, f"legacy Gemini gate completed (supervised profiles={profiles_total} target_active={target})"


def merge_worker_telemetry_from_logs(logs_dir: Path, profiles_total: int, stage_key: str) -> None:
    """Лёгкая эвристика: grep fast/thinking из worker-логов в state json (best-effort)."""
    state_path = logs_dir / "gemini_supervisor_state.json"
    if not state_path.exists():
        return
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    prof = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(prof, dict):
        return
    for i in range(profiles_total):
        logf = logs_dir / f"{stage_key}_worker_{i + 1}.log"
        if not logf.is_file():
            continue
        try:
            tail = logf.read_text(encoding="utf-8", errors="ignore")[-8000:]
        except OSError:
            continue
        low = tail.lower()
        entry = prof.get(str(i))
        if not isinstance(entry, dict):
            entry = {}
        if "переключено на быструю" in tail or "switched_to_fast" in low or "fast model" in low:
            entry["on_fast_due_to_thinking_limit"] = True
        prof[str(i)] = entry
    data["profiles"] = prof
    _write_state(state_path, data)
