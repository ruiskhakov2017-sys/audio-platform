from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import load_config
from orchestrator.isolated_launch_context import isolated_session
from orchestrator.launch_contract import LAUNCHES_DIR_NAME, youtube_story_dir
from orchestrator.youtube_launch_path_ops import (
    YoutubeLegacyOutputRecoveryOptions,
    compute_launch_only_readiness,
    recover_legacy_youtube_outputs,
)
from orchestrator.youtube_path_resolver import (
    WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT,
    WriteOutsideYoutubeLaunchRootError,
    assert_youtube_production_write_allowed,
    resolve_bridge_legacy_write_path,
    resolve_youtube_story_write_path,
    resolve_youtube_technical_story_dir,
)
from orchestrator.youtube_visuals_bridge import _story_basics
from orchestrator.youtube_visuals_clean import validate_visual_prompts_file


LAUNCH_ID = "YT_PATH_GUARD_TEST"
STORY_ID = "yt_test_story"
CANONICAL = "Test Story Alpha"


def _cfg(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg, "root_dir", tmp_path)
    return cfg


def _launch_root(tmp_path: Path) -> Path:
    return (tmp_path / LAUNCHES_DIR_NAME / LAUNCH_ID).resolve()


def _scaffold_story(tmp_path: Path, *, launch: bool, legacy: bool) -> tuple[Path, Path]:
    launch_story = youtube_story_dir(_launch_root(tmp_path), CANONICAL)
    legacy_story = (tmp_path / "output" / "youtube" / CANONICAL).resolve()
    manifest = {
        "story_id": STORY_ID,
        "canonical_basename": CANONICAL,
        "youtube_run_id": LAUNCH_ID,
    }
    if launch:
        launch_story.mkdir(parents=True, exist_ok=True)
        (launch_story / "youtube_story_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if legacy:
        legacy_story.mkdir(parents=True, exist_ok=True)
        (legacy_story / "youtube_story_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return launch_story, legacy_story


def _valid_prompts(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("A cinematic wide shot of a quiet town at dusk.\n\nA close-up of an old wooden door.\n", encoding="utf-8")


def test_production_safe_output_path_inside_launch_root(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _scaffold_story(tmp_path, launch=True, legacy=False)
    target = resolve_youtube_story_write_path(cfg, CANONICAL, "02_safe_story/safe_story.txt", launch_id=LAUNCH_ID)
    assert LAUNCHES_DIR_NAME in str(target)
    assert "03_youtube" in str(target).replace("\\", "/")


def test_production_prompts_output_path_inside_launch_root(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _scaffold_story(tmp_path, launch=True, legacy=False)
    target = resolve_bridge_legacy_write_path(
        cfg,
        CANONICAL,
        "06_prompts/prompts_list.txt",
        tmp_path / "output" / "youtube" / CANONICAL / "06_prompts" / "prompts_list.txt",
        launch_id=LAUNCH_ID,
    )
    assert "03_youtube" in str(target).replace("\\", "/")


def test_production_visuals_output_path_inside_launch_root(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _scaffold_story(tmp_path, launch=True, legacy=False)
    target = resolve_youtube_technical_story_dir(cfg, CANONICAL, launch_id=LAUNCH_ID)
    visuals = target / "05_characters" / "characters.txt"
    assert "03_youtube" in str(visuals).replace("\\", "/")


def test_write_to_output_youtube_with_youtube_run_id_fails(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    leak = tmp_path / "output" / "youtube" / CANONICAL / "06_prompts" / "prompts_list.txt"
    with pytest.raises(WriteOutsideYoutubeLaunchRootError) as exc:
        assert_youtube_production_write_allowed(cfg, leak, youtube_run_id=LAUNCH_ID)
    assert WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT in str(exc.value)


def test_prompts_status_ignores_legacy_only_outputs(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    launch_story, legacy_story = _scaffold_story(tmp_path, launch=True, legacy=True)
    legacy_prompts = legacy_story / "06_prompts" / "prompts_list.txt"
    _valid_prompts(legacy_prompts)
    assert validate_visual_prompts_file(legacy_prompts)["ok"] is True
    readiness = compute_launch_only_readiness(cfg, LAUNCH_ID)
    row = next(r for r in readiness["stories"] if r.get("story_id") == STORY_ID)
    assert row.get("legacy_prompts_only") is True
    assert row.get("prompts_status_launch_only") != "done"


def test_valid_legacy_output_can_be_imported(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    launch_story, legacy_story = _scaffold_story(tmp_path, launch=True, legacy=True)
    _valid_prompts(legacy_story / "06_prompts" / "prompts_list.txt")
    result = recover_legacy_youtube_outputs(
        config=cfg,
        options=YoutubeLegacyOutputRecoveryOptions(youtube_run_id=LAUNCH_ID, execute=True),
    )
    launch_prompts = launch_story / "06_prompts" / "prompts_list.txt"
    assert launch_prompts.is_file()
    assert any(row.get("artifact") == "prompts_primary" for row in result.get("imported", []))


def test_empty_legacy_output_rejected(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    launch_story, legacy_story = _scaffold_story(tmp_path, launch=True, legacy=True)
    empty = legacy_story / "06_prompts" / "prompts_list.txt"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")
    result = recover_legacy_youtube_outputs(
        config=cfg,
        options=YoutubeLegacyOutputRecoveryOptions(youtube_run_id=LAUNCH_ID, execute=True),
    )
    assert not (launch_story / "06_prompts" / "prompts_list.txt").is_file()
    assert any(row.get("artifact") == "prompts_primary" for row in result.get("rejected", []))


def test_runpod_preflight_reads_only_launch_folder(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    launch_story, legacy_story = _scaffold_story(tmp_path, launch=True, legacy=True)
    _valid_prompts(launch_story / "06_prompts" / "prompts_list.txt")
    with isolated_session(None, batch_launch_id=LAUNCH_ID, config=cfg):
        basics = _story_basics(cfg, STORY_ID)
    story_dir = Path(basics["story_dir"])
    assert launch_story.resolve() == story_dir.resolve()
    assert legacy_story.resolve() not in story_dir.parents
    assert "03_youtube" in str(story_dir).replace("\\", "/")
