from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.launch_contract as launch_contract
from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_tts_launch_jobs import PrepareLaunchJobsOptions, prepare_launch_jobs
from orchestrator.youtube_tts_readiness_repair import RepairReadinessOptions, repair_tts_readiness


def _test_config(root: Path) -> OrchestratorConfig:
    return OrchestratorConfig(
        root_dir=root,
        service_dir=root / ".orchestrator",
        logs_dir=root / ".orchestrator" / "logs",
        status_file=root / ".orchestrator" / "status.jsonl",
        events_file=root / ".orchestrator" / "events.jsonl",
        reports_dir=root / ".orchestrator" / "reports",
        pre_filter_min_minutes=15,
        pre_filter_words_per_minute=150,
        pre_filter_min_words=750,
        pre_filter_extensions=[".txt"],
        youtube_min_minutes=30,
        youtube_max_minutes=80,
        youtube_words_per_minute=150,
        youtube_min_words=4000,
        youtube_max_words=12000,
        youtube_split_long_stories=False,
        default_run_profile="dry-run-all",
        real_stage_whitelist=[],
        legacy_entrypoints={},
        legacy_modules={},
        data_dirs={},
        models_paths={},
        paths_registry_file=root / "configs" / "paths.yaml",
    )


def _patch_drive_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    drive = root / "drive" / "ContentFactory_YouTube"
    monkeypatch.setattr(launch_contract, "LEGACY_YOUTUBE_GLOBAL_DRIVE", drive)
    return drive


def _write_story(
    root: Path,
    launch_id: str,
    name: str,
    *,
    with_text: bool = True,
    with_voice: bool = True,
    audio_done: bool = False,
) -> None:
    story_dir = root / "Запуски" / launch_id / "03_youtube" / name.replace(" ", "_")
    promo = story_dir / "03_promo"
    audio = story_dir / "04_audio"
    safe = story_dir / "02_safe_story"
    promo.mkdir(parents=True)
    audio.mkdir(parents=True)
    safe.mkdir(parents=True)
    text = promo / "text_ready_for_audio.txt"
    safe_story = safe / "safe_story.txt"
    safe_story.write_text(f"Safe fallback text for {name}. " * 80, encoding="utf-8")
    if with_text:
        text.write_text(f"This is the production TTS text for {name}. " * 80, encoding="utf-8")
    if audio_done:
        (audio / "narration.mp3").write_bytes(b"x" * 300)
    manifest: dict = {
        "story_id": name,
        "canonical_basename": name,
        "launch_id": launch_id,
        "text_ready_for_audio": {"status": "done", "path": str(text)},
        "tts_kokoro_colab": {"status": "pending", "audio_path": str(audio / "narration.mp3")},
    }
    if with_voice:
        manifest["voice_contract"] = {"voice_label": "U", "kokoro_voice": "af_bella", "expected_gender": "U"}
    (story_dir / "youtube_story_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_repair_readiness_restores_missing_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_REPAIR"
    _write_story(tmp_path, launch_id, "Needs Repair", with_text=False, with_voice=True)
    _write_story(tmp_path, launch_id, "Done Story", audio_done=True)

    result = repair_tts_readiness(
        cfg,
        RepairReadinessOptions(youtube_run_id=launch_id, workers=5, execute=True),
    )
    summary = result["summary"]

    assert summary["ok"] is True
    assert summary["total_launch_stories"] == 2
    assert summary["accounted_for_tts"] == 2
    assert summary["already_done"] == 1
    assert summary["pending_for_tts"] == 1
    assert summary["skipped_invalid"] == 0
    assert (tmp_path / "reports" / "gemini_execution" / "YOUTUBE_TTS_READINESS_AUDIT.json").is_file()
    assert (tmp_path / "reports" / "gemini_execution" / "YOUTUBE_TTS_JOB_FINAL.json").is_file()


def test_prepare_account_all_stories_keeps_done_in_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_ACCOUNT_ALL"
    _write_story(tmp_path, launch_id, "Done Story", audio_done=True)
    _write_story(tmp_path, launch_id, "Pending Story")

    result = prepare_launch_jobs(
        cfg,
        PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True, account_all_stories=True),
    )
    job = json.loads(Path(str(result["job_path"])).read_text(encoding="utf-8"))

    assert result["accounted_count"] == 2
    assert result["already_done_count"] == 1
    assert result["eligible_count"] == 1
    assert len(job["items"]) == 2
    statuses = {item["canonical_basename"]: item["job_status"] for item in job["items"]}
    assert statuses["Done Story"] == "already_done"
    assert statuses["Pending Story"] == "pending"
