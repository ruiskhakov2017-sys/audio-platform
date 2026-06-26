# Automation Readiness Audit

Дата аудита: 2026-05-28. Режим: анализ + документация, production code не изменялся.

Цель проекта: две production-кнопки, `SITE FULL AUTO` и `YOUTUBE FULL AUTO`, которые берут рассказы из источников, проводят весь pipeline, публикуют результат и пишут понятный финальный отчёт.

## Executive Summary

Site готов примерно на 68%. В проекте уже есть рабочий isolated launch flow: `Content-Factory-Запуск.bat` -> `[2] Site full run to Zapuski folder` -> `run-site-flow`. Он создаёт `Запуски/<launch>`, запускает `phase-a`, `phase-b`, `orchestrator run --pipeline site`, синхронизирует legacy-артефакты в human launch folder и поддерживает resume через `site_flow_bat_state.json`. Главные разрывы: Telegram stage отсутствует, финальный report не вызывается автоматически, visual covers и Colab TTS всё ещё требуют человека, а site publish/asset bridge частично живёт отдельными CLI-командами.

YouTube готов примерно на 42%. Есть сильный per-story контур для safe text, promo, visuals, segment manifests, Drive video jobs, assigned queues, watcher, import и `assemble-final`. Но это не один production flow: нет batch-команды от site-approved до `final_video.mp4`, нет YouTube upload stage, нет run-scoped YouTube launch folder, Colab/RunPod остаются ручными, а top-level BAT содержит hardcoded story в video production helpers.

Telegram готов на 5%. Есть только scaffold/metadata placeholders в launch layout и `published_telegram=0` в status aggregation. Кода отправки, env-doctor, CLI и idempotency marker нет.

## 1. BAT Menu / Top-Level Commands

### `Content-Factory-Запуск.bat`

| Menu item | Реальная команда | Статус | Комментарий |
|---|---|---|---|
| `[1] [LEGACY / DANGEROUS] Site partial run` | `phase-a -> phase-b -> orchestrator run --pipeline site` без isolated launch | Не production | Пишет в global `runs/site` и `output/site`. Помечено dangerous, но всё ещё в top-level меню. |
| `[2] Site full run to Zapuski folder` | `python -m orchestrator launch run-site-flow --name SITE_FULL_<ts> --stories-dir stories/input --bat-profile kokoro-drive --limit 0 --execute` | Основной Site entrypoint | Реально isolated, resume-aware, но не закрывает Telegram/final-report и зависит от ручных visual/Colab действий. |
| `[3] Site: continue isolated launch (resume)` | pick launch -> `launch run-site-flow --execute` | Работает | Resume выбирается вручную, не latest-by-mtime. |
| `[4] Site: technical stages` | `filter-length`, `phase-b`, `site-info-visual validate/retry/full` | Dev/repair | Это отдельные repair tools, не production full auto. |
| `[5] Site: TTS -> MP3` | `site-tts kokoro-colab export/import/verify/full-cycle-drive/setup-drive` | Работает частично | TTS-only menu для prepared `output/site`; Colab execution ручной. |
| `[6] YouTube: full pipeline` | legacy `phase-a -> phase-b -> orchestrator run --pipeline youtube` | Не production | Не использует новый `output/youtube/<story>` state machine, Drive assigned queues и video watcher. |
| `[7] YouTube: stages` | stub message `not wired` | Не реализовано | Пункт зарезервирован. |
| `[8] Checks, reports, logs` | preflight/open reports/open logs/show modes | Работает | Service tools. |
| `[9] Service / runtime / cleanup` | set modes, cleanup, scaffold, redirect to Site full | Работает как maintenance | Не production flow. |
| `[Y] YouTube Visuals` | per-story commands: `visuals-run`, `frames-runpod`, `video prepare-segments`, `export-job`, `import-results`, `assemble-final`, `full-drive-flow` | Работает per-story | Требует `story_id` руками. |
| `[V] YouTube video production / 10 Colab` | watcher/launchers/status/validate/cleanup | Частично production | `queue-status`, `validate-job-assets`, `cleanup-partial-checkpoints` hardcoded на `Becoming A Slut Wife Alma`. |
| `[Q] Sample library -> stories/input` | `sample-library --per-folder N` | Работает | Может взять N файлов из каждого genre folder, но MOVE по умолчанию. |

### `START_*.bat`

| File | Что делает | Статус |
|---|---|---|
| `START_YOUTUBE_VIDEO_PRODUCTION_10COLAB.bat` | Запускает watcher в PowerShell и открывает Yandex 5 + Chrome 5 prepared Colab notebooks | Частично production | Hardcoded story id. Открывает/auto-run tabs, но не гарантирует Colab auth/T4/run success. |
| `START_YOUTUBE_VIDEO_WATCHER.bat` | `youtube video watch-queue --story-id "Becoming A Slut Wife Alma" --execute ...` | Работает для одного hardcoded story | Нужно параметризовать. |
| `START_COLAB_ALL.bat` | Открывает Yandex group, ждёт 300 sec, открывает Chrome group | Работает как launcher | Не валидирует, что worker реально выполняется. |
| `START_COLAB_YANDEX.bat`, `START_COLAB_CHROME.bat` | Открывают prepared notebook tabs по группе | Работает как launcher | Зависит от logged-in profiles. |

### CLI readiness

| Production need | Current CLI | Status |
|---|---|---|
| Site full auto | `orchestrator launch run-site-flow` | Есть, но не полный business flow |
| Site resume | `orchestrator launch resume-plan`, `launch resume`, `launch run-site-flow` on existing launch | Есть |
| Site publish | `orchestrator site-publish collect-assets/prepare/publish` + `autopublisher` wrapper | Есть, но bridge не полностью встроен в one-button |
| YouTube full auto | Нет единой команды | Missing |
| YouTube resume | Per-story commands/status exist; no launch-level resume | Partial |
| YouTube video render | `youtube video export-job/watch-queue/import-results/assemble-final` | Есть per-story |
| Queue status | `youtube video queue-status`, `site-tts kokoro-colab queue-status` | Есть |
| Cleanup/retry | `cleanup-scan/move/run`, `youtube video reclaim-stale-segments`, `cleanup-partial-checkpoints`, `visuals-clean` | Есть кусками |

Финальные production-команды должны стать:

- `python -m orchestrator site full-auto --launch-name <name> --source library --per-genre 5 --execute`
- `python -m orchestrator site resume --launch-name <name> --execute`
- `python -m orchestrator site publish --launch-name <name> --execute`
- `python -m orchestrator youtube full-auto --launch-name <name> --from-site-launch <site_launch> --workers 10 --execute`
- `python -m orchestrator youtube resume --launch-name <name> --execute`
- `python -m orchestrator youtube video render --story-id <id> --workers 10 --execute`
- `python -m orchestrator queue status --launch-name <name>`
- `python -m orchestrator launch cleanup --name <name> --execute`

## 2. Site Pipeline Audit

| Stage | Command | Inputs | Outputs | Writes where | Resume support | Skip support | Manual actions | Status |
|---|---|---|---|---|---|---|---|---|
| Library sampler | `orchestrator sample-library --source-dir <library> --target-dir stories/input --per-folder N --execute` | Top-level genre folders with `.txt` | Selected `.txt`, `_batch_manifest.json`, `.orchestrator/manifests/library_sample_*.json` | `stories/input`, `.orchestrator/manifests`, `.orchestrator/reports` | No launch resume, but collision-safe | Skips basename collisions/reserved `_series` | Choose N and source | Automated command, not wired into site full auto |
| Launch init | `launch run-site-flow --name <name> --stories-dir stories/input` | `stories/input/*.txt` | `manifest.json`, `status.json`, launch tree | `Запуски/<name>` | Yes | Existing launch reused | Pick or create launch | Automated |
| Phase A intake/selection/clean/site info/visual prompts | `phase-a --run-branch site --resume --execute --launch-dir <launch>` | launch input snapshot or `stories/input` | `phase_a_summary.json`, `ready_queues/deferred.json`, selection/site info artifacts | `Запуски/<name>/10_Временные_файлы/legacy/runs/site/<id>-a/_phase_a` mirrored to `05_Рассказы` | Yes via phase-a resume + `site_flow_bat_state.json` | Failed/deferred rows tracked | Gemini browser/accounts can need attention | Mostly automated |
| Phase B | `phase-b --deferred-manifest ... --branch site --allow-scaffold --launch-dir <launch>` | Phase A `deferred.json` | Phase B artifacts | launch legacy `runs/site/<id>-a/_phase_b` | Yes if returncode 0 and deferred exists | No business skip, only stage skip | None normally | Automated |
| Runtime site run | `orchestrator run --pipeline site --story-id <id>-site --launch-dir <launch> --execute` | Phase artifacts + `stories_dir` | Runner manifest/reports and wrapper outputs | `.orchestrator/reports`, launch legacy `output/site` | Partial via wrapper state and file markers | Stage-level | External services | Automated shell, mixed internals |
| Text cleaning | Runner `bulk_text_cleaner` wrapper | raw/phase data | cleaned text | launch legacy `output/site`, mirrored to `05_Рассказы/*/03_Сайт/01_Очистка_текста` | File-based | No | None | Automated |
| Site metadata/info | Runner `gemini_auto` + site info rendering | cleaned text | `site_info.json`, `info.en.txt`/`info.txt` | launch folders + legacy output | File-based | Failed validation can be retried by `site-info-visual retry` | Gemini accounts | Semi-automated |
| Visual prompts/images | `content_combiner` export/process/distribute-images; `site-info-visual validate/retry/full` | info visual fields and uploaded covers | `stories_export.csv/xlsx`, copied image into story package | launch visual upload folder, output site package | No true image resume, file overwrite checks | Missing image skips publish | User places images in `Обложки_ЗАГРУЗИТЕ_СЮДА` | Manual/semi |
| TTS export to Drive | `site_tts_stage` -> `export_drive_texts` or `site-tts kokoro-colab export` | prepared site text + voice label | Drive job/texts/expected files | `G:\Мой диск\ContentFactory_TTS\texts`, `job` | Detects pending Drive job | Existing mp3 skipped | Start Colab separately | Semi |
| TTS wait/import | `wait_drive_mp3_and_import` / `site-tts kokoro-colab import/verify` | Drive mp3 | `audio.mp3`/story mp3 | launch story TTS + legacy output + `output/site` depending mode | Yes, pending job detection | `manual_skipped`, `failed_terminal`, `mark-missing-skipped` | Colab run/OAuth | Semi |
| Collect assets | `site-publish collect-assets --launch-name <name> --execute` | text/info/mp3/images + TTS expected files | publish-ready story package + `story_manifest.json` | run-scoped publish root if `--launch-name`; legacy `output/site` fallback | Re-run safe with force/size checks | `skipped_tts_manual`, incomplete packages | Usually should not be manual | Exists, not reliably in one-button |
| Prepare package | `site-publish prepare --launch-name <name> --allow-partial-tts --execute` | complete story packages | `_to_publish/<story>` | run-scoped To_Publish or legacy autopublisher To_Publish | Re-run guarded by existing dest/force | Missing audio/image/info/text skipped | None after assets ready | Automated command |
| Publish to site | `site-publish publish --launch-name <name> --execute` or `autopublisher` wrapper | To_Publish/output site packages + env | Supabase/R2 result JSONL/report | external site + `.orchestrator/logs/site_publish_results.jsonl` + run manifest | Partial; idempotency not fully proven | Publishes ready only, skips incomplete earlier | Site env/secrets | Semi-production |
| Reports | `launch verify-runtime`, `resume-plan`, `final-report --execute`, status sync | launch files | final report, cleanup manifest, status | `Запуски/<name>/06_Отчёты`, `status.json` | Yes | N/A | final-report command currently separate | Partial |

Specific answers:

- 5/10 stories per genre: yes via `sample-library --per-folder 5` or `--per-folder 10`, where "genre" currently means top-level library folder under `source-dir`, not metadata genre from `info.txt`. BAT `[Q]` has dry-run, move 50, custom N.
- Configuration: BAT `PER_N` or CLI `--per-folder`; source defaults from `LIBRARY_SOURCE_DIR`.
- Selected stories: `stories/input/*.txt`; manifest: `.orchestrator/manifests/library_sample_<batch>.json` and `stories/input/_batch_manifest.json` on execute.
- Run manifest: `Запуски/<launch>/manifest.json`; phase manifest: `_phase_a/phase_a_summary.json`, `_phase_a/ready_queues/deferred.json`; site flow state: `10_Временные_файлы/site_flow_bat_state.json`.
- Publishing only ready packages: yes at prepare level; missing `audio/image/info/text` are skipped and reported.
- Continue after crash: yes for site launch through `run-site-flow` state + file markers; not perfect for external Colab/visual/manual steps.

## 3. YouTube Pipeline Audit

| Stage | Command | Inputs | Outputs | Writes where | Resume support | Skip support | Manual actions | Status |
|---|---|---|---|---|---|---|---|---|
| Selection source | `youtube prefilter-from-site` / `selection-from-site` | site `deferred.json`, cleaned story paths | size filter yes/no, Gemini selection input | `runs/youtube/<id>/_selection`, `_gemini_selection` | Re-run with force control | Too short/long/missing cleaned -> no | Need site run id | Automated prefilter, Gemini handoff manual/semi |
| Gemini selection parse | `youtube continue-after-selection` | Gemini raw/output files | `youtube_selected_yes/no.json` | `runs/youtube/<id>/_selection` | Yes by files | Missing output -> no | Gemini output generation | Semi |
| Safe text | `youtube prepare-safe-input`, `safe-english-run`, `youtube_safe_bridge` | selected yes cleaned text | `02_safe_story/safe_story.txt` | `output/youtube/<story>` | Per-story manifest/status | Missing output tracked | Gemini browser | Semi |
| Promo insertion | `youtube promo-run --story-id <id> --execute` | safe story | `03_promo/text_ready_for_audio.txt`, report | `output/youtube/<story>/03_promo` | Status command exists | No | Gemini/browser if legacy anchor used | Semi |
| YouTube audio/TTS | `youtube tts-kokoro-colab export/import/verify` | promo text | `04_audio/narration.mp3` | `output/youtube/<story>/04_audio`, Drive | Partial | Existing/missing reports | Colab run/OAuth | Semi |
| Characters/director prompts | `youtube visuals-run --auto-gemini --story-id <id>` | safe/promo/audio | characters, prompts | `output/youtube/<story>/05_characters`, `06_prompts` | Per-story file status | Validation/audit tools | Gemini/browser | Semi |
| Visual frames | `youtube frames-runpod --story-id <id> --runpod-url <url> --execute` | prompts | frames | `output/youtube/<story>/07_frames` | File/status based | Failed/pending frames status | RunPod URL/pod | Semi |
| Segment manifests | `youtube video prepare-segments --story-id <id> --execute` | narration + frames | `video_timeline.json`, `segment_jobs.json` | `output/youtube/<story>/08_video/manifests` | Re-run with force | Blocked if missing assets | None | Automated |
| Drive video job | `youtube video export-job --story-id <id> --execute` | audio, frames, segment jobs | Drive job with `global_pending` queue | `G:\Мой диск\ContentFactory_YouTube\video_jobs\<story>` | Replaces previous job with backup | Missing assets blocks export | Drive availability | Automated export |
| Workers setup/launch | `setup-colab-workers`; `START_COLAB_ALL.bat` | worker config | notebooks/scripts/status | Drive + browser profiles | Not launch-level | No | Colab login/OAuth/run | Launcher only |
| Watcher/queue | `youtube video watch-queue --execute` | Drive assigned queue | dispatch/reclaim/status/import reports | local `08_video/reports` + Drive reports | Loop can resume | stale reclaim, max attempts, failed bucket | Start watcher command | Automated per-story |
| Import rendered segments | `youtube video import-results --execute` | Drive `segments/segment_*.mp4` | local segment mp4 files | `output/youtube/<story>/08_video/segments` | Re-run copies | partial import status | None | Automated |
| Assemble final video | `youtube video assemble-final --execute` | all local segments + narration | `final_video.mp4`, report | `output/youtube/<story>/08_video/final_video.mp4` | Re-run if inputs exist | Blocks on missing segment | ffmpeg installed | Automated |
| YouTube upload/publish | none found | final mp4 | YouTube video | N/A | N/A | N/A | Manual | Missing |

Honest answers:

- What works: per-story YouTube artifact tree, safe/promo/visuals commands, segment preparation, Drive job export, assigned queue dispatch/reclaim/status/watch, import, final assembly.
- What is only prepared: top-level `[6] YouTube full pipeline` is a legacy wrapper chain, not the new YouTube production pipeline. `[7]` is not wired. Launch-level YouTube folder is scaffold-only.
- Missing production bridge: no `youtube full-auto`, no batch over selected stories, no launch-level resume/status, no YouTube upload, no Telegram.
- One command to final mp4: no. `youtube video full-drive-flow` exports Drive job and explicitly says workers must be started manually; watcher auto-imports but does not assemble final.
- One command to start 10 workers: `START_COLAB_ALL.bat` / `START_YOUTUBE_VIDEO_PRODUCTION_10COLAB.bat` open 10 prepared notebook tabs, but cannot guarantee Colab auth, runtime allocation, Drive mount, or actual run completion. `configs/youtube_video_render.yaml` still lists 5 default render workers; 10 browser workers are described in `configs/youtube_video_colab_workers.yaml`.
- Failed/stale segments: `reclaim-stale-segments` returns stale processing to `global_pending` until `max_attempts`, then moves to failed. `cleanup-partial-checkpoints` removes partial files. `queue-status` reports failed/stale/partial/checkpoint counts.
- Final video path: `output/youtube/<story>/08_video/final_video.mp4`.
- Upload/publish to YouTube: no production CLI/module was found. Current backlog target is a future `youtube upload`/`youtube_upload` stage.

## 4. Telegram Stage Audit

Current state:

- `human_launch_layout.py` defines Telegram-related constants and story paths.
- `human_launch_legacy_sync.py` has `ensure_telegram_story_scaffold` and `write_telegram_snapshot_metadata`.
- `human_launch_lifecycle.py` includes `published_telegram=0` and pending telegram substatuses.
- No Telegram sender, no CLI, no wrapper, no env/config, no report, no idempotency marker.

Required addition:

- New CLI: `orchestrator telegram prepare/send/status --launch-name <name>`.
- Env/config: `.env.telegram` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` or `TELEGRAM_CHANNEL_ID`, optional `TELEGRAM_PARSE_MODE`, `TELEGRAM_DISABLE_NOTIFICATION`.
- Stage position: after successful site publish, preferably after published URL is written per story.
- Inputs: title/site url/description from publish result and site info, cover image from story package, optional audio preview.
- Outputs: `Запуски/<launch>/04_Telegram/<story>/post.txt`, `telegram_sent.json`, `telegram_publish_report.json`.
- Error handling: never fail the already completed site publish because Telegram failed; mark story as `telegram_failed`, retry on resume, never resend if `telegram_sent.json` exists unless `--force`.

## 5. Artifact Structure Audit

Full artifact map is in `docs/ARTIFACT_STRUCTURE_AUDIT.md`.

Main answer: no, after a run finishes you cannot currently delete only `Запуски/<launch>` and be certain that all temporary files for that run are gone. Blockers:

- root `output/site` and `output/youtube` can contain run artifacts;
- root `runs/site` and `runs/youtube` still exist for legacy/manual paths;
- `.orchestrator` logs/reports/status are global;
- Drive folders under `G:\Мой диск\ContentFactory_TTS` and `G:\Мой диск\ContentFactory_YouTube` are external and not tied to launch cleanup;
- `legacy/autopublisher/To_Publish` or run-scoped `_to_publish` may contain staging data;
- browser profiles and Colab notebooks live outside launch;
- YouTube artifacts are not mirrored to `Запуски/<launch>/03_YouTube`.

## 6. Resume / Status / Reports Audit

Source of truth today is split:

- Site launch: `Запуски/<launch>/manifest.json`, `status.json`, per-story `status.json`, `10_Временные_файлы/site_flow_bat_state.json`.
- Recovery launch: `10_Временные_файлы/recovery_queue_map.json` or `manifest.recovery_execute.queue_map` is stronger source of truth than filesystem scan.
- Site phase data: launch legacy `_phase_a/phase_a_summary.json`, `_phase_a/ready_queues/deferred.json`, `_phase_b`.
- Runner: `.orchestrator/status.jsonl`, `.orchestrator/events.jsonl`, `.orchestrator/reports/run_manifest_*.json`.
- TTS: Drive `job` status, `manual_skipped.json`, local reports.
- Publish: `.orchestrator/site_publish_*.json`, `.orchestrator/logs/site_publish_results.jsonl`, run-scoped `site_publish_manifest.json`.
- YouTube: `runs/youtube/<id>/youtube_status.jsonl`, `output/youtube/<story>/youtube_story_manifest.json`, `08_video/reports/*.json`, Drive queue reports.

How to tell:

- Launch completed: `Запуски/<launch>/status.json.status == completed` and no pending/failed stories by `verify-runtime`.
- Can publish site: `site-publish collect-assets` reports `packages_ready > 0`; `prepare` reports ready/prepared and missing assets; env-doctor has no blockers.
- Partial success: `status.json.status == partially_completed` or publish/TTS reports contain skipped/manual_failed but some ready/published stories.
- Continue after crash: use BAT `[3]` or `launch run-site-flow` on same launch; for YouTube use per-story status/queue commands.
- Desync risk: `output/site`, `output/youtube`, Drive and `.orchestrator` can disagree with `Запуски/<launch>`.
- Observed launch example from readonly audit: `Запуски/SITE_FULL_20260513_1309/status.json` reports `failed`, `current_stage=02_Сайт/03_Визуал_для_сайта`, `can_resume=true`; trace reports `site_flow_site_run_failed` with exit `2`.

## 7. Manual Actions List

| Manual action | Can automate | Should automate | Safe fallback |
|---|---:|---:|---|
| Choose library N/per genre | Yes | Yes | Keep `[Q]` custom N dry-run |
| Start Google Drive sync / shortcut | Partial | Yes preflight | Print exact Drive root and folder id |
| Colab Drive mount/OAuth | No reliable full automation | No | Prepared notebooks + profile diagnostics |
| Colab Run for Site TTS | Partial launcher | Keep semi-manual | `mark-missing-skipped` and partial publish |
| Colab Run for YouTube video workers | Partial launcher | Keep semi-manual | watcher + reclaim + queue status |
| RunPod URL/pod for frames | Partial | Ask once per batch | Stop at `READY_FOR_RUNPOD` |
| Upload/provide site covers | Yes with image generator | Yes later | Missing image skips story |
| Telegram token/channel config | Yes env-doctor | Yes | Telegram stage skipped, site publish still success |
| Site publish env/secrets | Partial env-doctor | Yes | Block publish with clear report |
| GitHub push for Colab notebooks | No in local pipeline | Maybe docs-only | Prepared notebook URLs must be checked |
| Manual skipped TTS files | Yes timeout policy | Yes | Manual `mark-skipped` command |
| Failed/stale video segments | Yes watcher | Yes | `reclaim-stale-segments`, inspect, cleanup partial |
| YouTube upload | Yes via API | Yes Phase 4/5 | Manual upload with generated metadata |

## 8. Final Completion Criteria

See `docs/FINAL_PRODUCTION_CRITERIA.md`.

Minimum completion means:

- Site full auto completes N stories without manual shell commands.
- Resume is idempotent after crash.
- Skipped TTS/image/info stories do not block publishing ready stories.
- Published site report shows success/partial with exact skipped reasons.
- Final artifacts and reports are under the launch folder or explicitly external-bound.
- Telegram stage is integrated and idempotent.
- YouTube full auto can take at least one site-approved story to `final_video.mp4`.
- Colab/RunPod render has retry/resume/status.
- BAT menu exposes only clear production paths by default.

## 9. Backlog

See `docs/AUTOMATION_READINESS_BACKLOG.md`.

## 10. Reconciliation Notes From Subagents

- BAT/CLI: `site full`, `site resume`, `site-publish`, YouTube video queue/render/status commands exist; complete `youtube resume` and named `cleanup-retry` commands do not exist. Closest commands are `phase-a --resume`, `youtube video reclaim-stale-segments`, `cleanup-partial-checkpoints`, and `watch-queue`.
- Site: sampler is per top-level source folder, not metadata genre. Full Site launch is `launch run-site-flow`; `launch smoke-site-cycle` is only a smoke/diagnostic route and is not a substitute.
- Site publish readiness: complete package means `info.txt + audio + image + text`; missing packages are skipped and reported. Legacy direct publish bridge may have weaker text assumptions, so production should use `site-publish collect-assets/prepare`.
- YouTube: actual new production path is per-story under `output/youtube/<story>` plus Drive queue. Legacy `[6] YouTube full pipeline` is not the same path.
- Artifacts: source of truth is split-brain. Site is partially run-scoped; YouTube and Telegram are not. `status.json` is a snapshot inferred from files, not an append-only truth.
- Drive cleanup: Site TTS may clean Drive `texts/mp3` after successful import when configured, but Drive `job/cache/logs` and YouTube `video_jobs/<story>` remain external leftovers unless a future explicit cleanup handles them.

