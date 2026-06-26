from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import orchestrator.launch_contract as launch_contract
from orchestrator.youtube_tts_kokoro_bridge import _merge_job_items, _write_expected_audio_files
from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_tts_launch_jobs import (
    PrepareLaunchJobsOptions,
    TtsLaunchOptions,
    preflight_launch_jobs,
    prepare_launch_jobs,
    status_launch_jobs,
)


def _load_colab_worker():
    path = Path(__file__).resolve().parents[1] / "colab" / "kokoro_google_drive_youtube_colab.py"
    spec = importlib.util.spec_from_file_location("kokoro_google_drive_youtube_colab_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _write_story(root: Path, launch_id: str, name: str, *, audio_done: bool = False) -> None:
    story_dir = root / "Запуски" / launch_id / "03_youtube" / name.replace(" ", "_")
    promo = story_dir / "03_promo"
    audio = story_dir / "04_audio"
    promo.mkdir(parents=True)
    audio.mkdir(parents=True)
    text = promo / "text_ready_for_audio.txt"
    text.write_text(f"This is the production TTS text for {name}.", encoding="utf-8")
    if audio_done:
        (audio / "narration.mp3").write_bytes(b"x" * 40_000)
    manifest = {
        "story_id": name,
        "canonical_basename": name,
        "launch_id": launch_id,
        "text_ready_for_audio": {"status": "done", "path": str(text)},
        "tts_kokoro_colab": {"status": "pending", "audio_path": str(audio / "narration.mp3")},
        "voice_contract": {"voice_label": "U", "kokoro_voice": "af_bella", "expected_gender": "U"},
    }
    (story_dir / "youtube_story_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _patch_drive_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    drive = root / "drive" / "ContentFactory_YouTube"
    monkeypatch.setattr(launch_contract, "LEGACY_YOUTUBE_GLOBAL_DRIVE", root / "drive" / "ContentFactory_YouTube")
    return drive


def test_youtube_tts_export_job_accumulates_items_and_expected_files(tmp_path: Path) -> None:
    job_path = tmp_path / "jobs" / "youtube_tts_job.json"
    first = {
        "story_id": "story-a",
        "canonical_basename": "Story A",
        "drive_text_path": str(tmp_path / "texts" / "Story_A.txt"),
        "expected_drive_audio_path": str(tmp_path / "audio" / "Story_A.mp3"),
    }
    second = {
        "story_id": "story-b",
        "canonical_basename": "Story B",
        "drive_text_path": str(tmp_path / "texts" / "Story_B.txt"),
        "expected_drive_audio_path": str(tmp_path / "audio" / "Story_B.mp3"),
    }

    items = _merge_job_items(job_path, first)
    job_path.parent.mkdir(parents=True)
    job_path.write_text(json.dumps({"items": items}), encoding="utf-8")
    items = _merge_job_items(job_path, second)
    expected = _write_expected_audio_files(tmp_path / "jobs" / "EXPECTED_FILES.txt", items)

    assert [item["story_id"] for item in items] == ["story-a", "story-b"]
    assert expected == ["Story_A.mp3", "Story_B.mp3"]
    assert (tmp_path / "jobs" / "EXPECTED_FILES.txt").read_text(encoding="utf-8") == "Story_A.mp3\nStory_B.mp3\n"


def test_colab_launch_full_reads_batch_youtube_tts_job(tmp_path: Path) -> None:
    worker = _load_colab_worker()
    launch_id = "YT_TEST"
    drive_root = tmp_path / "ContentFactory_YouTube"
    launch_root = drive_root / "launches" / launch_id
    texts = launch_root / "texts"
    jobs = launch_root / "jobs"
    texts.mkdir(parents=True)
    jobs.mkdir(parents=True)
    (launch_root / "audio").mkdir(parents=True)

    story_a = texts / "Story_A.txt"
    story_b = texts / "Story_B.txt"
    story_a.write_text("This is an English story with enough text for a batch job.", encoding="utf-8")
    story_b.write_text("This is another English story with enough text for a batch job.", encoding="utf-8")
    (jobs / "youtube_tts_job.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "story_id": "story-a",
                        "canonical_basename": "Story A",
                        "drive_text_path": str(story_a),
                        "expected_drive_audio_path": str(launch_root / "audio" / "Story_A.mp3"),
                        "source_text_hash": _sha256_bytes(story_a),
                        "voice_label": "U",
                        "kokoro_voice": "af_bella",
                    },
                    {
                        "story_id": "story-b",
                        "canonical_basename": "Story B",
                        "drive_text_path": str(story_b),
                        "expected_drive_audio_path": str(launch_root / "audio" / "Story_B.mp3"),
                        "source_text_hash": _sha256_bytes(story_b),
                        "voice_label": "F",
                        "kokoro_voice": "af_heart",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    worker.GLOBAL_YOUTUBE_ROOT = drive_root
    worker.WORKER_MODE = "launch"
    worker.LAUNCH_ID = launch_id
    worker.JOB_TYPE = "full"
    worker.STORY_SLUG = ""
    worker._apply_launch_paths(launch_id, "full")

    all_items = worker.load_launch_job_items(launch_id, "full", "")
    filtered = worker.load_launch_job_items(launch_id, "full", "Story_B")

    assert [item["audio_name"] for item in all_items] == ["Story_A.mp3", "Story_B.mp3"]
    assert len(filtered) == 1
    assert filtered[0]["canonical_basename"] == "Story B"


def test_colab_legacy_tts_batch_partition_for_five_workers() -> None:
    worker = _load_colab_worker()
    items = [{"canonical_basename": f"Story {index}"} for index in range(1, 12)]

    assigned = [
        worker.partition_items_for_worker(items, worker_index=index, worker_count=5)
        for index in range(1, 6)
    ]

    assert [[item["canonical_basename"] for item in slot] for slot in assigned] == [
        ["Story 1", "Story 6", "Story 11"],
        ["Story 2", "Story 7"],
        ["Story 3", "Story 8"],
        ["Story 4", "Story 9"],
        ["Story 5", "Story 10"],
    ]
    flattened = [item["canonical_basename"] for slot in assigned for item in slot]
    assert sorted(flattened, key=lambda value: int(value.rsplit(" ", 1)[1])) == [
        item["canonical_basename"] for item in items
    ]


def test_colab_launch_mode_does_not_fallback_to_global_job(tmp_path: Path) -> None:
    worker = _load_colab_worker()
    launch_id = "YT_TEST_NO_FALLBACK"
    drive_root = tmp_path / "ContentFactory_YouTube"
    launch_root = drive_root / "launches" / launch_id
    launch_root.mkdir(parents=True)
    global_jobs = drive_root / "jobs"
    global_jobs.mkdir(parents=True)
    text_path = launch_root / "texts" / "Story_A.txt"
    text_path.parent.mkdir(parents=True)
    text_path.write_text("This is an English story for fallback batch job.", encoding="utf-8")
    audio_path = launch_root / "audio" / "Story_A.mp3"
    audio_path.parent.mkdir(parents=True)
    (global_jobs / "youtube_tts_job.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "story_id": "story-a",
                        "canonical_basename": "Story A",
                        "drive_text_path": str(text_path),
                        "expected_drive_audio_path": str(audio_path),
                        "source_text_hash": _sha256_bytes(text_path),
                        "voice_label": "U",
                        "kokoro_voice": "af_bella",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    worker.GLOBAL_YOUTUBE_ROOT = drive_root
    worker.WORKER_MODE = "launch"
    worker.LAUNCH_ID = launch_id
    worker.JOB_TYPE = "full"
    worker.STORY_SLUG = ""
    worker._apply_launch_paths(launch_id, "full")

    try:
        worker.load_launch_job_items(launch_id, "full", "")
    except FileNotFoundError as exc:
        assert "launch job missing" in str(exc)
    else:
        raise AssertionError("launch mode must not fallback to global jobs/youtube_tts_job.json")


def test_prepare_launch_jobs_creates_job_and_five_partitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_PREPARE"
    for index in range(36):
        _write_story(tmp_path, launch_id, f"Story {index:02d}")

    result = prepare_launch_jobs(cfg, PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True))
    job_path = Path(str(result["job_path"]))

    assert result["eligible_count"] == 36
    assert job_path.is_file()
    partition_counts = [part["count"] for part in result["partitions"]]
    assert partition_counts == [8, 7, 7, 7, 7]
    for idx in range(5):
        assert (job_path.parent / "partitions" / f"worker_{idx}.json").is_file()


def test_preflight_fails_missing_launch_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_PREFLIGHT"
    (tmp_path / "Запуски" / launch_id / "03_youtube").mkdir(parents=True)

    result = preflight_launch_jobs(cfg, TtsLaunchOptions(youtube_run_id=launch_id, workers=5))

    assert not result["ok"]
    assert any("missing_job" in err for err in result["errors"])


def test_preflight_fails_missing_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_MISSING_PART"
    for index in range(3):
        _write_story(tmp_path, launch_id, f"Story {index:02d}")
    prepare_launch_jobs(cfg, PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True))
    job_path = Path(str(preflight_launch_jobs(cfg, TtsLaunchOptions(youtube_run_id=launch_id, workers=5))["job_path"]))
    (job_path.parent / "partitions" / "worker_3.json").unlink()

    result = preflight_launch_jobs(cfg, TtsLaunchOptions(youtube_run_id=launch_id, workers=5))

    assert not result["ok"]
    assert any("missing_partition" in err for err in result["errors"])


def test_preflight_rejects_stub_job_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_STUB"
    (tmp_path / "Запуски" / launch_id / "03_youtube").mkdir(parents=True)
    result = prepare_launch_jobs(cfg, PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True))
    job_path = Path(str(result["job_path"]))
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    payload["kind"] = "dummy_stub"
    job_path.write_text(json.dumps(payload), encoding="utf-8")

    result = preflight_launch_jobs(cfg, TtsLaunchOptions(youtube_run_id=launch_id, workers=5))

    assert not result["ok"]
    assert any("invalid_job_kind" in err for err in result["errors"])


def test_done_audio_not_included_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_DONE"
    _write_story(tmp_path, launch_id, "Done Story", audio_done=True)
    _write_story(tmp_path, launch_id, "Pending Story")

    result = prepare_launch_jobs(cfg, PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True))

    assert result["eligible_count"] == 1
    job = json.loads(Path(str(result["job_path"])).read_text(encoding="utf-8"))
    assert [item["canonical_basename"] for item in job["items"]] == ["Pending Story"]


def test_worker_reads_only_own_launch_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    drive_root = _patch_drive_root(monkeypatch, tmp_path)
    worker = _load_colab_worker()
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_WORKER_PART"
    for index in range(6):
        _write_story(tmp_path, launch_id, f"Story {index:02d}")
    prepare_launch_jobs(cfg, PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True))

    worker.GLOBAL_YOUTUBE_ROOT = drive_root
    worker.WORKER_MODE = "launch"
    worker.LAUNCH_ID = launch_id
    worker.JOB_TYPE = "full"
    worker._apply_launch_paths(launch_id, "full")

    items = worker.load_launch_partition_items(launch_id, 0, 5)
    assert [item["canonical_basename"] for item in items] == ["Story 00", "Story 05"]


def test_status_command_aggregates_five_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_drive_root(monkeypatch, tmp_path)
    cfg = _test_config(tmp_path)
    launch_id = "YT_TEST_STATUS"
    for index in range(6):
        _write_story(tmp_path, launch_id, f"Story {index:02d}")
    result = prepare_launch_jobs(cfg, PrepareLaunchJobsOptions(youtube_run_id=launch_id, workers=5, execute=True))
    job = json.loads(Path(str(result["job_path"])).read_text(encoding="utf-8"))
    Path(job["items"][0]["expected_drive_audio_path"]).write_bytes(b"x" * 300)

    status = status_launch_jobs(cfg, TtsLaunchOptions(youtube_run_id=launch_id, workers=5))

    assert status["total"] == 6
    assert status["done"] == 1
    assert len(status["worker_progress"]) == 5
