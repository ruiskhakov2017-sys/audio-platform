from __future__ import annotations

import json
from pathlib import Path

from orchestrator.config import load_config
from orchestrator.launch_contract import LAUNCHES_DIR_NAME
from orchestrator.youtube_prompts_temp_import_repair import (
    TEMP_PROMPTS_COUNT_MISMATCH,
    TEMP_PROMPTS_EMPTY,
    TEMP_PROMPTS_FORBIDDEN_TERMS,
    TEMP_PROMPTS_MISSING,
    YoutubePromptsTempImportRepairOptions,
    run_youtube_prompts_temp_import_repair,
)
from orchestrator.youtube_visuals_runner import YoutubePromptsResumeAuditOptions, run_youtube_prompts_resume_audit

LAUNCH_ID = "YT_TEMP_IMPORT_TEST"
SESSION_ID = "20260618_082047"


def _cfg(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg, "root_dir", tmp_path)
    return cfg


def _launch_root(tmp_path: Path) -> Path:
    return tmp_path / LAUNCHES_DIR_NAME / LAUNCH_ID


def _story_slug(title: str) -> str:
    return title.replace(" ", "_")


def _prompt_text(count: int, *, forbidden: bool = False) -> str:
    parts = []
    for idx in range(1, count + 1):
        token = " teenage " if forbidden and idx == 1 else " "
        parts.append(f"{idx}. A cinematic{token}scene in a lived-in house with realistic lighting and adult subjects.")
    return "\n\n".join(parts) + "\n"


def _write_story(tmp_path: Path, *, story_id: str, expected: int) -> Path:
    story_dir = _launch_root(tmp_path) / "03_youtube" / _story_slug(story_id)
    for rel in ("04_audio", "05_characters", "06_prompts", "06_director", "logs"):
        (story_dir / rel).mkdir(parents=True, exist_ok=True)
    (story_dir / "04_audio" / "narration.mp3").write_bytes(b"fake-mp3")
    (story_dir / "05_characters" / "characters.txt").write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "id": "CHAR_1",
                        "name": "Anna",
                        "role": "lead",
                        "anchor": "42-year-old woman, brown hair, calm posture, practical clothes, adult",
                        "description": "42-year-old woman, brown hair, calm posture, practical clothes, adult",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {
        "story_id": story_id,
        "canonical_basename": story_id,
        "audio": {"valid_for_video": True},
        "visual_prompts": {"expected_prompts": expected, "status": "failed", "actual_prompts": 0, "validation": "failed"},
    }
    (story_dir / "youtube_story_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return story_dir


def _write_temp_prompts(tmp_path: Path, *, worker: str, story_id: str, text: str | None) -> Path:
    stage_dir = _launch_root(tmp_path) / "10_Временные_файлы" / "visuals_gemini_batch" / "prompts" / SESSION_ID / worker / story_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    path = stage_dir / "prompts_list.txt"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


def test_valid_temp_prompts_import_into_canonical_launch_folder(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    story_dir = _write_story(tmp_path, story_id="Home by the Sea", expected=3)
    _write_temp_prompts(tmp_path, worker="worker_2", story_id="Home by the Sea", text=_prompt_text(3))

    result = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )

    assert result["imported_count"] == 1
    assert (story_dir / "06_prompts" / "prompts_list.txt").is_file()
    assert (story_dir / "06_director" / "prompts_list.txt").is_file()


def test_canonical_readiness_sees_imported_prompts(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _write_story(tmp_path, story_id="Naga Massage Review", expected=2)
    _write_temp_prompts(tmp_path, worker="worker_3", story_id="Naga Massage Review", text=_prompt_text(2))

    before = run_youtube_prompts_resume_audit(
        config=cfg,
        options=YoutubePromptsResumeAuditOptions(youtube_run_id=LAUNCH_ID),
    )
    row_before = next(row for row in before["stories"] if row["story_id"] == "Naga Massage Review")
    assert row_before["prompts_status"] != "done"

    run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )
    after = run_youtube_prompts_resume_audit(
        config=cfg,
        options=YoutubePromptsResumeAuditOptions(youtube_run_id=LAUNCH_ID),
    )
    row_after = next(row for row in after["stories"] if row["story_id"] == "Naga Massage Review")
    assert row_after["prompts_status"] == "done"
    assert row_after["validation"] == "ok"


def test_missing_canonical_plus_valid_temp_not_ready_until_import(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _write_story(tmp_path, story_id="Who You Love", expected=2)
    _write_temp_prompts(tmp_path, worker="worker_1", story_id="Who You Love", text=_prompt_text(2))

    dry = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=False),
    )
    row = next(item for item in dry["stories"] if item["story"] == "Who You Love")
    assert row["action"] == "would_import"
    assert row["final_status"] == "done"
    assert not (_launch_root(tmp_path) / "03_youtube" / "Who_You_Love" / "06_prompts" / "prompts_list.txt").exists()


def test_missing_or_empty_temp_rejected(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _write_story(tmp_path, story_id="Within", expected=2)
    _write_story(tmp_path, story_id="Would It Be Awkward", expected=2)
    _write_temp_prompts(tmp_path, worker="worker_1", story_id="Would It Be Awkward", text="")

    result = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )
    reasons = {row["story"]: row["reason"] for row in result["stories"]}
    assert reasons["Within"] == TEMP_PROMPTS_MISSING
    assert reasons["Would It Be Awkward"] == TEMP_PROMPTS_EMPTY


def test_count_mismatch_temp_rejected(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _write_story(tmp_path, story_id="Search and Replace", expected=3)
    _write_temp_prompts(tmp_path, worker="worker_2", story_id="Search and Replace", text=_prompt_text(2))

    result = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )
    row = next(item for item in result["stories"] if item["story"] == "Search and Replace")
    assert row["reason"] == TEMP_PROMPTS_COUNT_MISMATCH
    assert row["action"] == "rejected"


def test_stale_forbidden_temp_rejected(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _write_story(tmp_path, story_id="Do You Want To Know A Secret", expected=2)
    _write_temp_prompts(tmp_path, worker="worker_1", story_id="Do You Want To Know A Secret", text=_prompt_text(2, forbidden=True))

    result = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )
    row = next(item for item in result["stories"] if item["story"] == "Do You Want To Know A Secret")
    assert row["reason"] == TEMP_PROMPTS_FORBIDDEN_TERMS
    assert row["action"] == "rejected"


def test_next_stage_allowed_false_when_blocked_and_no_failed_ok_true(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    _write_story(tmp_path, story_id="Home by the Sea", expected=2)
    _write_story(tmp_path, story_id="Within", expected=2)
    _write_temp_prompts(tmp_path, worker="worker_2", story_id="Home by the Sea", text=_prompt_text(2))

    result = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )
    assert result["final_readiness"]["next_stage_allowed"] is False
    for row in result["stories"]:
        if row["final_status"] == "failed":
            assert row["ok"] is False


def test_missing_staged_marker_does_not_skip_valid_temp_commit(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, monkeypatch)
    story_dir = _write_story(tmp_path, story_id="Home by the Sea", expected=2)
    stage_dir = _write_temp_prompts(tmp_path, worker="worker_2", story_id="Home by the Sea", text=_prompt_text(2)).parent
    staged_marker = stage_dir / "ORCHESTRATOR_STAGED.json"
    assert not staged_marker.exists()

    result = run_youtube_prompts_temp_import_repair(
        config=cfg,
        options=YoutubePromptsTempImportRepairOptions(youtube_run_id=LAUNCH_ID, run_session_id=SESSION_ID, execute=True),
    )
    row = next(item for item in result["stories"] if item["story"] == "Home by the Sea")
    assert row["action"] == "imported"
    assert (story_dir / "06_prompts" / "prompts_list.txt").is_file()
