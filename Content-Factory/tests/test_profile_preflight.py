from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import site_visual_profile_preflight as pp
from orchestrator.site_visual_profile_preflight import (
    ProfileStatus,
    pick_profile,
    run_profile_preflight,
)


def _make_profile(
    *,
    idx: int = 0,
    exists: bool = True,
    email: str = "bot@example.com",
    url_ok: bool = True,
    locks: list[str] | None = None,
    pids: list[int] | None = None,
) -> ProfileStatus:
    locks = locks or []
    pids = pids or []
    blocking = [n for n in locks if not n.startswith("Default/")]
    is_locked = bool(blocking) or bool(pids)
    ready = exists and bool(email) and url_ok and not is_locked
    return ProfileStatus(
        profile_index=idx,
        user_data_dir=f"/fake/user_data_{idx}",
        exists=exists,
        email=email,
        registry_url_for_stage=url_ok,
        lock_files_present=locks,
        chrome_pids=pids,
        is_locked=is_locked,
        is_ready=ready,
        reasons=[],
    )


def test_pick_profile_default_picks_zero_when_ready() -> None:
    profiles = [_make_profile(idx=0), _make_profile(idx=1)]
    idx, status, _ = pick_profile(profiles)
    assert idx == 0
    assert status == "ok"


def test_pick_profile_auto_skips_locked_zero() -> None:
    profiles = [
        _make_profile(idx=0, locks=["SingletonLock"]),
        _make_profile(idx=1),
    ]
    idx, status, _ = pick_profile(profiles, auto_profile=True)
    assert idx == 1
    assert status == "ok"


def test_pick_profile_requested_locked_falls_back_with_auto() -> None:
    profiles = [
        _make_profile(idx=0, locks=["SingletonLock"]),
        _make_profile(idx=2),
    ]
    idx, status, _ = pick_profile(profiles, requested_profile_index=0, auto_profile=True)
    assert idx == 2
    assert status == "fallback"


def test_pick_profile_no_ready_returns_none() -> None:
    profiles = [
        _make_profile(idx=0, locks=["SingletonLock"]),
        _make_profile(idx=1, email=""),
        _make_profile(idx=2, url_ok=False),
    ]
    idx, status, _ = pick_profile(profiles, auto_profile=True)
    assert idx is None
    assert status == "no_free_profile"


def test_pick_profile_requested_not_ready_without_auto_fails() -> None:
    profiles = [_make_profile(idx=0, locks=["SingletonLock"]), _make_profile(idx=1)]
    idx, status, _ = pick_profile(profiles, requested_profile_index=0, auto_profile=False)
    assert idx is None
    assert status == "requested_not_ready"


def test_pick_profile_requested_invalid_index() -> None:
    profiles = [_make_profile(idx=0)]
    idx, status, _ = pick_profile(profiles, requested_profile_index=42)
    assert idx is None
    assert status == "requested_invalid"


def test_detect_lock_files_picks_singleton_only(tmp_path: Path) -> None:
    profile = tmp_path / "user_data_0"
    (profile / "Default").mkdir(parents=True)
    (profile / "SingletonLock").write_text("x", encoding="utf-8")
    (profile / "Default" / "LOCK").write_text("y", encoding="utf-8")
    locks = pp._detect_lock_files(profile)
    assert "SingletonLock" in locks
    assert "Default/LOCK" in locks


def test_run_profile_preflight_reports_registry_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "missing_registry.yaml"
    config = SimpleNamespace(
        root_dir=tmp_path,
        legacy_entrypoints={"gemini_auto": "legacy/Gemini_Auto/gemini_auto.py"},
    )
    monkeypatch.setattr(pp, "_list_chrome_pids_for_profile", lambda *_a, **_k: [])
    result = run_profile_preflight(
        config=config,
        registry_path=registry,
        stage_key="site_info_builder",
        profiles_total=2,
    )
    assert not result.ok
    assert result.preflight_status == "registry_missing"


def test_run_profile_preflight_picks_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gem_dir = tmp_path / "legacy" / "Gemini_Auto"
    gem_dir.mkdir(parents=True)
    (gem_dir / "gemini_auto.py").write_text("# stub", encoding="utf-8")
    for idx in (0, 1, 2):
        p = gem_dir / f"user_data_{idx}"
        (p / "Default").mkdir(parents=True)
        (p / "Default" / "Preferences").write_text(
            json.dumps({"account_info": [{"email": f"bot{idx}@example.com"}]}),
            encoding="utf-8",
        )
    (gem_dir / "user_data_0" / "SingletonLock").write_text("locked", encoding="utf-8")

    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "gemini_bots:\n"
        "  - email: bot0@example.com\n"
        "    site_info_builder: https://example.com/0\n"
        "  - email: bot1@example.com\n"
        "    site_info_builder: https://example.com/1\n"
        "  - email: bot2@example.com\n"
        "    site_info_builder: https://example.com/2\n",
        encoding="utf-8",
    )

    config = SimpleNamespace(
        root_dir=tmp_path,
        legacy_entrypoints={"gemini_auto": "legacy/Gemini_Auto/gemini_auto.py"},
    )

    monkeypatch.setattr(pp, "_list_chrome_pids_for_profile", lambda *_a, **_k: [])

    result = run_profile_preflight(
        config=config,
        registry_path=registry,
        stage_key="site_info_builder",
        profiles_total=3,
        auto_profile=True,
    )
    assert result.ok
    assert result.selected_profile_index == 1
    assert result.preflight_status == "ok"
    assert result.profiles[0].is_locked is True
    assert result.profiles[1].is_ready is True
