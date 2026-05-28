"""
Browser/profile preflight for visual prompts retry.

Проверяет user_data_<N> профили Gemini перед запуском legacy gemini_auto:
- наличие папки профиля;
- залогинен ли (email в Preferences);
- есть ли URL для нужного stage_key в registry;
- свободен ли профиль (нет SingletonLock / LOCK / запущенного chrome.exe с этим user-data-dir).

Возвращает структурированный отчёт; не запускает Playwright (только файлы + PowerShell-список процессов).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig

# Имена lock-файлов, которые Chrome создаёт в каталоге профиля при запуске.
# Достаточно одного из них, чтобы считать профиль занятым.
PROFILE_LOCK_FILES: tuple[str, ...] = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
)

# Дополнительные lock-файлы в Default/ (например, LevelDB), которые сами по себе
# не блокируют запуск, но полезны для диагностики.
PROFILE_DEFAULT_LOCK_FILES: tuple[str, ...] = ("LOCK",)


@dataclass
class ProfileStatus:
    profile_index: int
    user_data_dir: str
    exists: bool
    email: str
    registry_url_for_stage: bool
    lock_files_present: list[str]
    chrome_pids: list[int]
    is_locked: bool
    is_ready: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class ProfilePreflightResult:
    ok: bool
    selected_profile_index: int | None
    preflight_status: str  # "ok" | "fallback" | "no_free_profile" | "registry_missing" | ...
    reason: str
    profiles: list[ProfileStatus]
    gemini_module_dir: str
    registry_path: str
    registry_bots: int
    requested_profile_index: int | None
    auto_profile: bool


def _gemini_module_dir(config: OrchestratorConfig) -> Path:
    rel = str(config.legacy_entrypoints.get("gemini_auto", "legacy/Gemini_Auto/gemini_auto.py")).strip()
    return (config.root_dir / rel).resolve().parent


def _read_email_from_preferences(user_data_dir: Path) -> str:
    prefs = user_data_dir / "Default" / "Preferences"
    if not prefs.is_file():
        return ""
    try:
        payload = json.loads(prefs.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return ""
    info = payload.get("account_info") if isinstance(payload, dict) else None
    if not isinstance(info, list):
        return ""
    for item in info:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip().lower()
        if email:
            return email
    return ""


def _detect_lock_files(user_data_dir: Path) -> list[str]:
    found: list[str] = []
    if not user_data_dir.is_dir():
        return found
    for name in PROFILE_LOCK_FILES:
        p = user_data_dir / name
        if p.exists():
            found.append(name)
    default = user_data_dir / "Default"
    if default.is_dir():
        for name in PROFILE_DEFAULT_LOCK_FILES:
            p = default / name
            if p.exists():
                # Только пометим как «вспомогательный» признак, не блокирующий.
                found.append(f"Default/{name}")
    return found


def _list_chrome_pids_for_profile(user_data_dir: Path, *, timeout_sec: float = 25.0) -> list[int]:
    """
    Список pid'ов chrome.exe, чей CommandLine содержит данный user-data-dir.
    Windows only; на других ОС возвращает [].
    """
    if sys.platform != "win32":
        return []
    target = str(user_data_dir.resolve()).lower()
    target_esc = target.replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" "
        "| Where-Object { $_.CommandLine -and ($_.CommandLine.ToLower().Contains('"
        + target_esc
        + "')) } "
        "| Select-Object ProcessId "
        "| ConvertTo-Json -Depth 1 -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except Exception:
        return []
    raw = (r.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = [x for x in data if isinstance(x, dict)]
    else:
        return []
    out: list[int] = []
    for row in rows:
        try:
            out.append(int(row.get("ProcessId", 0)))
        except (TypeError, ValueError):
            continue
    return [pid for pid in out if pid > 0]


def _load_registry(registry_path: Path) -> tuple[list[dict[str, str]], str]:
    """Возвращает (bots, error_message)."""
    if not registry_path.is_file():
        return [], f"gemini registry not found: {registry_path}"
    # Используем тот же loader, что в orchestrator.phase_a, чтобы не дублировать парсер.
    from orchestrator.phase_a import _load_gemini_registry

    try:
        bots = _load_gemini_registry(registry_path)
    except Exception as exc:
        return [], f"gemini registry load failed: {exc}"
    if not bots:
        return [], f"gemini registry empty or unreadable: {registry_path}"
    return bots, ""


def _registry_url_for(bots: list[dict[str, str]], email: str, stage_key: str) -> bool:
    if not email:
        return False
    target = email.strip().lower()
    for bot in bots:
        if str(bot.get("email") or "").strip().lower() == target:
            return bool(str(bot.get(stage_key) or "").strip())
    return False


def inspect_profiles(
    *,
    config: OrchestratorConfig,
    registry_path: Path,
    stage_key: str,
    profiles_total: int = 5,
) -> tuple[list[ProfileStatus], list[dict[str, str]], str]:
    """Возвращает (profiles, bots, registry_error)."""
    bots, reg_err = _load_registry(registry_path)
    gem_dir = _gemini_module_dir(config)
    profiles_total = max(1, min(10, int(profiles_total or 5)))
    profiles: list[ProfileStatus] = []
    for idx in range(profiles_total):
        user_data = gem_dir / f"user_data_{idx}"
        exists = user_data.is_dir()
        email = _read_email_from_preferences(user_data) if exists else ""
        url_ok = _registry_url_for(bots, email, stage_key) if bots else False
        locks = _detect_lock_files(user_data) if exists else []
        pids = _list_chrome_pids_for_profile(user_data) if exists else []
        blocking_locks = [n for n in locks if not n.startswith("Default/")]
        is_locked = bool(blocking_locks) or bool(pids)
        reasons: list[str] = []
        if not exists:
            reasons.append("profile_dir_missing")
        if exists and not email:
            reasons.append("not_logged_in")
        if email and not url_ok:
            reasons.append(f"registry_url_missing_for_stage:{stage_key}")
        if blocking_locks:
            reasons.append(f"lock_files:{','.join(blocking_locks)}")
        if pids:
            reasons.append(f"chrome_running_pids:{','.join(str(p) for p in pids)}")
        ready = exists and bool(email) and url_ok and not is_locked
        profiles.append(
            ProfileStatus(
                profile_index=idx,
                user_data_dir=str(user_data),
                exists=exists,
                email=email,
                registry_url_for_stage=url_ok,
                lock_files_present=locks,
                chrome_pids=pids,
                is_locked=is_locked,
                is_ready=ready,
                reasons=reasons,
            )
        )
    return profiles, bots, reg_err


def pick_profile(
    profiles: list[ProfileStatus],
    *,
    requested_profile_index: int | None = None,
    auto_profile: bool = False,
) -> tuple[int | None, str, str]:
    """
    Возвращает (selected_index, preflight_status, reason).
    preflight_status: "ok" | "fallback" | "no_free_profile" | "requested_not_ready" | "requested_invalid".
    """
    if not profiles:
        return None, "no_free_profile", "no profiles inspected"

    if requested_profile_index is not None:
        idx = int(requested_profile_index)
        target = next((p for p in profiles if p.profile_index == idx), None)
        if target is None:
            return None, "requested_invalid", f"requested profile_index={idx} not in pool"
        if target.is_ready:
            return idx, "ok", f"requested profile {idx} ready"
        if not auto_profile:
            return None, "requested_not_ready", (
                f"requested profile {idx} not ready: " + "; ".join(target.reasons or ["unknown"])
            )
        ready = next((p for p in profiles if p.is_ready), None)
        if ready is None:
            return None, "no_free_profile", "requested profile not ready and no free fallback"
        return ready.profile_index, "fallback", (
            f"requested profile {idx} not ready -> fallback to {ready.profile_index}"
        )

    if auto_profile:
        ready = next((p for p in profiles if p.is_ready), None)
        if ready is None:
            return None, "no_free_profile", "no ready profile (locked / not logged in / no registry url)"
        return ready.profile_index, "ok", f"auto-picked profile {ready.profile_index}"

    target = next((p for p in profiles if p.profile_index == 0), profiles[0])
    if target.is_ready:
        return target.profile_index, "ok", f"default profile {target.profile_index} ready"
    return None, "requested_not_ready", (
        f"default profile {target.profile_index} not ready: " + "; ".join(target.reasons or ["unknown"])
    )


def run_profile_preflight(
    *,
    config: OrchestratorConfig,
    registry_path: Path,
    stage_key: str = "site_info_builder",
    profiles_total: int = 5,
    requested_profile_index: int | None = None,
    auto_profile: bool = False,
) -> ProfilePreflightResult:
    profiles, bots, reg_err = inspect_profiles(
        config=config,
        registry_path=registry_path,
        stage_key=stage_key,
        profiles_total=profiles_total,
    )

    if reg_err:
        return ProfilePreflightResult(
            ok=False,
            selected_profile_index=None,
            preflight_status="registry_missing",
            reason=reg_err,
            profiles=profiles,
            gemini_module_dir=str(_gemini_module_dir(config)),
            registry_path=str(registry_path),
            registry_bots=0,
            requested_profile_index=requested_profile_index,
            auto_profile=auto_profile,
        )

    selected, status, reason = pick_profile(
        profiles,
        requested_profile_index=requested_profile_index,
        auto_profile=auto_profile,
    )
    return ProfilePreflightResult(
        ok=selected is not None,
        selected_profile_index=selected,
        preflight_status=status,
        reason=reason,
        profiles=profiles,
        gemini_module_dir=str(_gemini_module_dir(config)),
        registry_path=str(registry_path),
        registry_bots=len(bots),
        requested_profile_index=requested_profile_index,
        auto_profile=auto_profile,
    )


def preflight_to_dict(result: ProfilePreflightResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "selected_profile_index": result.selected_profile_index,
        "preflight_status": result.preflight_status,
        "reason": result.reason,
        "requested_profile_index": result.requested_profile_index,
        "auto_profile": result.auto_profile,
        "gemini_module_dir": result.gemini_module_dir,
        "registry_path": result.registry_path,
        "registry_bots": result.registry_bots,
        "profiles": [asdict(p) for p in result.profiles],
    }
