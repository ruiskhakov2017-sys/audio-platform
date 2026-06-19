from __future__ import annotations

import json
from pathlib import Path

from orchestrator.config import load_config
from orchestrator.launch_contract import LAUNCHES_DIR_NAME
from orchestrator.youtube_prompts_failure_reasons import (
    GEMINI_NO_RESPONSE,
    NO_TEMP_OUTPUT,
    PROMPTS_GENERATION_INCOMPLETE,
    TEMP_IMPORT_FAILED,
    classify_stage_prompts_failure,
    normalize_failure_reason,
)
from orchestrator.youtube_prompts_targeted_repair import (
    YoutubePromptsTargetedRepairOptions,
    run_youtube_prompts_targeted_repair,
)
from orchestrator.youtube_visuals_runner import YoutubeVisualsStatusOptions, run_youtube_visuals_launch_status

LAUNCH_ID = "YT_TARGETED_REPAIR_TEST"
SESSION_ID = "20260618_082047"


def _cfg(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg, "root_dir", tmp_path)
    return cfg


def _launch_root(tmp_path: Path) -> Path:
    return tmp_path / LAUNCHES_DIR_NAME / LAUNCH_ID


def _story_slug(title: str) -> str:
    return title.replace(" ", "_")


def _write_story(tmp_path: Path, *, story_id: str, expected: int) -> Path:
    story_dir = _launch_root(tmp_path) / "03_youtube" / _story_slug(story_id)
    for rel in ("04_audio", "05_characters", "06_prompts", "06_director", "logs"):
        (story_dir / rel).mkdir(parents=True, exist_ok=True)
    (story_dir / "04_audio" / "narration.mp3").write_bytes(b"fake-mp3")
    (story_dir / "05_characters" / "characters.txt").write_text("Anna | adult woman", encoding="utf-8")
    manifest = {
        "story_id": story_id,
        "canonical_basename": story_id,
        "audio": {"valid_for_video": True},
        "visual_prompts": {"expected_prompts": expected, "status": "failed", "actual_prompts": 0, "validation": "failed", "error": "failed"},
    }
    (story_dir / "youtube_story_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return story_dir


def test_normalize_failure_reason_rejects_vague_failed():
    assert normalize_failure_reason("failed") == "UNKNOWN_PROMPTS_FAILURE"
    assert normalize_failure_reason("NO_TEMP_OUTPUT") == "NO_TEMP_OUTPUT"


def test_classify_stage_prompts_failure_cases(tmp_path):
    stage = tmp_path / "worker_1" / "Story A"
    stage.mkdir(parents=True)
    assert classify_stage_prompts_failure(stage_dir=stage) == NO_TEMP_OUTPUT

    (stage / "ORCHESTRATOR_STAGED.json").write_text("{}", encoding="utf-8")
    assert classify_stage_prompts_failure(stage_dir=stage) == GEMINI_NO_RESPONSE

    (stage / "prompts_list.partial.txt").write_text("1. scene\n", encoding="utf-8")
    assert classify_stage_prompts_failure(stage_dir=stage) == PROMPTS_GENERATION_INCOMPLETE

    (stage / "prompts_list.txt").write_text("1. scene\n", encoding="utf-8")
    assert classify_stage_prompts_failure(stage_dir=stage) == TEMP_IMPORT_FAILED


def test_targeted_repair_forensic_reports_partial_and_staged(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    story_id = "Who You Love"
    _write_story(tmp_path, story_id=story_id, expected=3)
    stage = _launch_root(tmp_path) / "10_Временные_файлы" / "visuals_gemini_batch" / "prompts" / SESSION_ID / "worker_2" / story_id
    stage.mkdir(parents=True)
    (stage / "prompts_list.partial.txt").write_text("1. scene\n", encoding="utf-8")
    (stage / "director_checkpoint.json").write_text('{"total_chunks": 3, "next_chunk_index": 1}', encoding="utf-8")

    result = run_youtube_prompts_targeted_repair(
        config=cfg,
        options=YoutubePromptsTargetedRepairOptions(
            youtube_run_id=LAUNCH_ID,
            story_ids=[story_id],
            workers=1,
            preferred_session_id=SESSION_ID,
            execute=False,
        ),
    )
    assert result["ok"] is False
    row = result["stories"][0]
    assert row["why_no_canonical_prompts_list"] == PROMPTS_GENERATION_INCOMPLETE
    assert row["forensic"]["temp_partial_found"] is True
    assert SESSION_ID in row["forensic"]["sessions_with_stage_dir"]


def test_launch_status_next_stage_allowed_false_when_prompts_missing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ready_id = "Ready Story"
    missing_id = "Missing Story"
    _write_story(tmp_path, story_id=ready_id, expected=1)
    missing_dir = _write_story(tmp_path, story_id=missing_id, expected=2)
    prompts_path = missing_dir / "06_prompts" / "prompts_list.txt"
    ready_path = _launch_root(tmp_path) / "03_youtube" / _story_slug(ready_id) / "06_prompts" / "prompts_list.txt"
    ready_path.write_text("1. ready scene with enough detail for validation.\n", encoding="utf-8")
    ready_manifest_path = _launch_root(tmp_path) / "03_youtube" / _story_slug(ready_id) / "youtube_story_manifest.json"
    ready_manifest = json.loads(ready_manifest_path.read_text(encoding="utf-8"))
    ready_manifest["visual_prompts"] = {
        "expected_prompts": 1,
        "actual_prompts": 1,
        "status": "done",
        "validation": "ok",
    }
    ready_manifest_path.write_text(json.dumps(ready_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not prompts_path.is_file()

    status = run_youtube_visuals_launch_status(
        config=cfg,
        options=YoutubeVisualsStatusOptions(youtube_run_id=LAUNCH_ID),
    )
    summary = status["summary"]
    assert summary["not_ready_for_runpod"] >= 1
    assert summary["next_stage_allowed"] is False
