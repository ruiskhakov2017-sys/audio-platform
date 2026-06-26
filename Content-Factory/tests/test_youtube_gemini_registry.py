from __future__ import annotations

import json
from pathlib import Path

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_gemini_registry import sync_youtube_gemini_legacy_files


def _config(root: Path) -> OrchestratorConfig:
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
        default_run_profile="full-real",
        real_stage_whitelist=[],
        legacy_entrypoints={},
        legacy_modules={"director_2_0": "legacy/director_2_0"},
        data_dirs={},
        models_paths={},
        paths_registry_file=root / "configs" / "paths.yaml",
    )


def test_syncs_legacy_visuals_gemini_files_from_registry(tmp_path: Path) -> None:
    root = tmp_path
    registry = root / "configs" / "gemini_bots_registry.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "\n".join(
            [
                "gemini_bots:",
                "  - email: first@example.com",
                "    youtube_characters: https://gemini.google.com/gem/char1",
                "    youtube_scene_prompts: https://gemini.google.com/gem/dir1",
                "  - email: second@example.com",
                "    youtube_characters: https://gemini.google.com/gem/char2",
                "    youtube_scene_prompts: https://gemini.google.com/gem/dir2",
            ]
        ),
        encoding="utf-8",
    )
    director = root / "legacy" / "director_2_0"
    director.mkdir(parents=True)
    (director / "config.json").write_text(
        json.dumps(
            {
                "characters_gemini_url": "https://gemini.google.com/u/0/gem/oldchar",
                "gemini_url": "https://gemini.google.com/u/0/gem/olddir",
            }
        ),
        encoding="utf-8",
    )
    (director / "gemini_bots.json").write_text(
        json.dumps(
            [
                {"email": "second@example.com", "url": "https://gemini.google.com/u/0/gem/olddir", "app": "https://gemini.google.com/u/0/app"},
                {"email": "first@example.com", "url": "https://gemini.google.com/u/1/gem/olddir2", "app": "https://gemini.google.com/u/1/app"},
            ]
        ),
        encoding="utf-8",
    )
    story_dir = root / "output" / "youtube" / "Story"

    result = sync_youtube_gemini_legacy_files(_config(root), story_dir=story_dir, execute=True)

    assert result["ok"]
    assert not result["old_urls_found_in_active_legacy"]
    synced_config = json.loads((director / "config.json").read_text(encoding="utf-8"))
    assert synced_config["characters_gemini_url"] == "https://gemini.google.com/gem/char2"
    assert synced_config["gemini_url"] == "https://gemini.google.com/gem/dir2"
    synced_chain = json.loads((director / "gemini_bots.json").read_text(encoding="utf-8"))
    assert synced_chain[0]["email"] == "second@example.com"
    assert synced_chain[0]["url"] == "https://gemini.google.com/gem/dir2"
    assert synced_chain[0]["app"] == "https://gemini.google.com/app"
    assert all("/u/" not in row["url"] for row in synced_chain)
    assert (story_dir / "logs" / "youtube_gemini_bots_preflight.json").is_file()
