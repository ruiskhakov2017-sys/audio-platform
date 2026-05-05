# Orchestrator Runbook

## Purpose

This is a safe orchestration shell for Content-Factory legacy modules.
It does not rewrite legacy logic and defaults to dry-run behavior.

## Commands

- `python -m orchestrator preflight`
- `python -m orchestrator preflight --pipeline full --run-profile dry-run-all`
- `python -m orchestrator plan --pipeline site --story-id demo-story`
- `python -m orchestrator status`
- `python -m orchestrator run --pipeline full --story-id demo-story --stories-dir "<stories_dir>"`
- `python -m orchestrator run --pipeline full --story-id demo-story --stories-dir "<stories_dir>" --execute --run-profile full-real --allow-real-stages length_filter`
- `python -m orchestrator filter-length --stories-dir "<path_to_stories>"`
- `python -m orchestrator filter-length --stories-dir "<path_to_stories>" --execute`
- `python -m orchestrator phase-a --stories-dir "<path_to_stories>" --story-id phasea-001`
- `python -m orchestrator phase-a --stories-dir "<path_to_stories>" --story-id phasea-001 --execute`
- `python -m orchestrator phase-b --story-id phaseb-001 --deferred-manifest ".orchestrator/reports/phase_a_phasea-001/ready_queues/deferred.json" --gemini-registry "configs/gemini_bots_registry.example.yaml"`
- `python -m orchestrator show-modes`
- `python -m orchestrator set-mode --key site_tts_runtime --value colab`
- `python -m orchestrator reset-modes`
- `python -m orchestrator site-tts scan --limit 1`
- `python -m orchestrator site-tts missing-mp3 --limit 1`
- `python -m orchestrator site-tts kokoro-colab export --limit 1`
- `python -m orchestrator site-tts kokoro-colab verify`

## Safety model

- Default: dry-run.
- Real execution requires explicit `--execute`.
- Real execution additionally requires whitelist (`--allow-real-stages` or config whitelist).
- At current V1 stage many wrappers are intentionally dry-run-only and return `partial_connected`.
- Unsafe stages are marked in contracts and surfaced in preflight/plan output.
- `filter-length` in dry-run only builds report and movement plan.
- `filter-length --execute` moves only short stories to `short_under_15m` (no delete).

## V1 unified flow

- Common: `length_filter -> bulk_text_cleaner -> gemini_auto`
- Site branch: `site_tts -> content_combiner -> autopublisher`
- YouTube branch: `youtube_selection -> youtube_safe_text -> director20 -> youtube_tts -> autovideo`
- Pipeline options:
  - `--pipeline site`
  - `--pipeline youtube`
  - `--pipeline full`

## Phase A flow (no Gemini block yet)

- `intake -> length_filter -> selection_gate_placeholder -> clean_passed_only -> branch_split -> ready_queues`
- Selection gate is intentionally placeholder:
  - no semantic quality judgment;
  - stories are marked `selected_pending_gemini`.
- Branch split before Gemini block:
  - `site_queue`: empty placeholder queue;
  - `youtube_queue`: empty placeholder queue;
  - `deferred`: cleaned stories waiting for real Gemini block connection.

## Phase B flow (Gemini block scaffold)

- `general_selection`
- `site_info_builder`
- `youtube_top_tier_selection`
- `youtube_safe_text`
- `youtube_ad_point`
- `promo_insertion` (script-like stage using promo assets)
- `youtube_characters`
- `youtube_scene_prompts`

Notes:
- Site and YouTube selections are not mixed.
- `youtube_top_tier_selection` runs on already selected pool.
- `youtube_ad_point` runs after `youtube_safe_text`.
- Promo insertion applies `promo_intro_en`, `promo_mid_en`, `promo_outro_en`.
- Runtime modes are loaded from `configs/runtime_modes.yaml`.
- Mode snapshot is saved in each Phase B run as `runtime_modes_snapshot.json`.

## Length filter behavior

- Processes only text files by extension (default: `.txt`).
- Explicitly excludes `short_under_15m` from scanning.
- Formula: `estimated_minutes = word_count / 150`.
- Threshold: story is short when `estimated_minutes < 15`.
- Report fields:
  - `source_path`
  - `file_name`
  - `char_count`
  - `word_count`
  - `estimated_minutes`
  - `result`

## Service files

Orchestrator writes service data outside legacy runtime paths:
- `.orchestrator/events.jsonl`
- `.orchestrator/status.jsonl`
- `.orchestrator/logs/` (reserved)
- `.orchestrator/reports/filter_report.csv`
- `.orchestrator/reports/filter_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/intake_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/length_filter_report.csv`
- `.orchestrator/reports/phase_a_<story_id>/length_filter_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/selection_gate_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/clean_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/branch_split_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/ready_queues/site_queue.json`
- `.orchestrator/reports/phase_a_<story_id>/ready_queues/youtube_queue.json`
- `.orchestrator/reports/phase_a_<story_id>/ready_queues/deferred.json`
- `.orchestrator/reports/phase_a_<story_id>/story_state_manifest.json`
- `.orchestrator/reports/phase_a_<story_id>/phase_a_summary.json`
- `.orchestrator/reports/phase_b_<story_id>/phase_b_input_manifest.json`
- `.orchestrator/reports/phase_b_<story_id>/general_selection_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/info_outputs_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/youtube_selection_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/safe_text_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/youtube_ad_point_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/promo_applied_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/characters_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/scene_prompts_results.jsonl`
- `.orchestrator/reports/phase_b_<story_id>/routing_rejected.json`
- `.orchestrator/reports/phase_b_<story_id>/routing_manual_review.json`
- `.orchestrator/reports/phase_b_<story_id>/routing_site_ready.json`
- `.orchestrator/reports/phase_b_<story_id>/routing_youtube_ready.json`
- `.orchestrator/reports/phase_b_<story_id>/story_state_manifest.json`
- `.orchestrator/reports/phase_b_<story_id>/phase_b_summary.json`
- `.orchestrator/reports/phase_b_<story_id>/runtime_modes_snapshot.json`

## Site TTS Kokoro Colab flow (safe, main: Google Drive folders)

- Queue view (safe, no write): `python -m orchestrator site-tts scan --limit 20`
- Missing queue dry-run (safe, no mp3 write): `python -m orchestrator site-tts missing-mp3 --limit 20`
- Export for Colab (safe, no local TTS): `python -m orchestrator site-tts kokoro-colab export --limit 20`

Main user workflow (Google Drive folders):
- Export to Google Drive texts:
  - `python -m orchestrator site-tts kokoro-colab export-drive --limit 100 --texts-dir "<MyDrive>/ContentFactory_TTS/texts"`
- Run Colab code:
  - `Content-Factory/colab/kokoro_google_drive_colab.py`
- Colab writes mp3 into:
  - `<MyDrive>/ContentFactory_TTS/mp3`
- Import locally:
  - `python -m orchestrator site-tts kokoro-colab import-drive --mp3-dir "<MyDrive>/ContentFactory_TTS/mp3"`
- Verify:
  - `python -m orchestrator site-tts kokoro-colab verify-drive --texts-dir "<MyDrive>/ContentFactory_TTS/texts" --mp3-dir "<MyDrive>/ContentFactory_TTS/mp3"`

Local simple fallback (still available):
- `COLAB_TTS_CURRENT/` with `--current` commands.

Legacy/internal workflow (optional):
- `--batch-id`, `--handoff-dir`, `--latest`
- useful for troubleshooting and low-level batch inspection.

Batch directory:
- `runs/tts_colab_batches/<batch_id>/`
- Expected structure: `manifest.json`, `README_COLAB.md`, `stories/`, `chunks/`, `results/`
- Colab must write files as `results/item_XXXXXX.mp3` (exact `item_id` from `manifest.json`)

Human-friendly handoff directory (recommended):
- `_COLAB_EXPORTS/<batch_id>__site_tts__<N>_stories/`
- Contains:
  - `00_README_START_HERE.txt`
  - `01_STORIES_INDEX.csv`
  - `02_UPLOAD_THIS_TO_COLAB.zip`
  - `results_drop_here/` (put downloaded mp3 files here)
  - `internal_manifest.json`

Colab runner:
- `Content-Factory/colab/kokoro_colab_runner.py`
- `Content-Factory/colab/README_KOKORO_COLAB.md`
- Colab output contract:
  - `results/`
  - `results_report.csv`
  - `kokoro_results.zip`

Safety:
- Allowed by default: `scan`, `missing-mp3` (without `--execute`), `kokoro-colab export|verify`
- Forbidden without explicit approval: `site-tts sync --execute`, `site-tts missing-mp3 --execute`
