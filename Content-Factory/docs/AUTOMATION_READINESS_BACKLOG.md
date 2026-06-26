# Automation Readiness Backlog

Связано с `docs/AUTOMATION_READINESS_AUDIT.md`.

## Phase 1 — Site Full Auto Stabilization

Цель: сделать `SITE FULL AUTO` реальной production-командой от source/library до site publish report.

Файлы:

- `Content-Factory-Запуск.bat`
- `orchestrator/cli.py`
- `orchestrator/human_launch_site_flow_bat.py`
- `orchestrator/human_launch_site_bootstrap.py`
- `orchestrator/site_publish/collect_assets.py`
- `orchestrator/site_publish/prepare.py`
- `orchestrator/site_publish/publish.py`
- `orchestrator/wrappers/autopublisher.py`
- `orchestrator/wrappers/site_tts_stage.py`

Что менять:

- Встроить `site-publish collect-assets --launch-dir <launch> --allow-partial-tts --execute` после TTS import и перед publish.
- Встроить `site-publish prepare --launch-dir <launch> --allow-partial-tts --execute` перед autopublisher.
- Передавать launch-scoped publish root в autopublisher wrapper, чтобы не полагаться на global `output/site`.
- Автоматически запускать `launch final-report --execute` в конце успешного/partial site flow.
- Сделать `sample-library --per-folder N` optional pre-stage в Site full auto. Важно: текущий `per-folder` означает top-level library folder, не metadata genre.
- Параметризовать per-genre count: `--source-dir`, `--per-genre`, `--sample-mode copy|move`.
- Добавить preflight блокеры для site env, Drive root, required Python deps, ffmpeg only if needed.

Команды после phase:

- `python -m orchestrator site full-auto --launch-name <name> --source-dir <library> --per-genre 5 --execute`
- `python -m orchestrator site resume --launch-name <name> --execute`
- `python -m orchestrator site publish --launch-name <name> --execute`

Как тестировать:

- Smoke: 2 stories, one with missing cover, one complete.
- Controlled: 5 stories per genre from library with `--sample-mode copy`.
- Crash test: stop during phase-a, rerun same launch.
- TTS partial: mark one expected mp3 skipped, verify publish continues for ready packages.

Готовность:

- No manual shell command between launch start and final report.
- `status.json` shows completed or partially_completed with exact reasons.
- `06_Отчёты/ФИНАЛЬНЫЙ_ОТЧЁТ.json` and `cleanup_manifest.json` are written.
- `site_publish_manifest.json` exists and points to run-scoped package root.

## Phase 2 — Artifact Structure / Launch Folder Cleanup

Цель: один launch folder должен содержать все локальные артефакты запуска или ссылки на explicit external bindings.

Файлы:

- `orchestrator/human_launch_layout.py`
- `orchestrator/human_launch_lifecycle.py`
- `orchestrator/human_launch_legacy_sync.py`
- `orchestrator/site_publish/paths.py`
- `orchestrator/youtube_from_site.py`
- `orchestrator/youtube_visuals_runner.py`
- `orchestrator/youtube_video_drive.py`
- `orchestrator/youtube_video_segments.py`
- `orchestrator/runner.py`

Что менять:

- Сделать run-scoped site publish root default для `run-site-flow`.
- Ввести `Запуски/<launch>/03_YouTube/<story>` как primary output для YouTube.
- Зеркалировать `.orchestrator/events/status/reports` в `Запуски/<launch>/07_Логи`.
- Записывать Drive bindings:
  - `10_Временные_файлы/drive_bindings/site_tts.json`
  - `03_YouTube/<story>/_drive_video_job.json`
- Обновить `delete launch --execute` так, чтобы он проверял external bindings и писал, что не удалено без `--drive-cleanup`.

Команды после phase:

- `python -m orchestrator launch cleanup-plan --name <launch>`
- `python -m orchestrator launch delete --name <launch> --execute`
- `python -m orchestrator launch delete --name <launch> --execute --drive-cleanup`

Как тестировать:

- Создать launch, прогнать 2 stories, удалить dry-run.
- Проверить, что root `output/site` и `output/youtube` не обновились в run-scoped mode.
- Проверить, что cleanup-plan честно перечисляет Drive external leftovers.

Готовность:

- Удаление `Запуски/<launch>` удаляет все локальные временные файлы запуска.
- External Drive leftovers известны и управляются отдельным explicit флагом.

## Phase 3 — Telegram Stage

Цель: site publish автоматически отправляет Telegram announcement и пишет report.

Файлы:

- new `orchestrator/telegram_publish.py`
- new `orchestrator/wrappers/telegram_publish.py`
- `orchestrator/cli.py`
- `orchestrator/human_launch_layout.py`
- `orchestrator/human_launch_lifecycle.py`
- `orchestrator/site_publish/publish.py`
- `orchestrator/wrappers/__init__.py`
- new `.env.telegram.example`

Что менять:

- Добавить `04_Telegram` в top-level launch dirs.
- Добавить `telegram prepare/status/send`.
- Взять из story package: title, cover, description/post text, site URL, optional audio preview.
- Ввести marker `telegram_sent.json` per story.
- Редактировать status aggregation: `published_telegram`.
- Добавить env-doctor с redaction для токена.

Команды после phase:

- `python -m orchestrator telegram prepare --launch-name <launch>`
- `python -m orchestrator telegram send --launch-name <launch> --execute`
- `python -m orchestrator telegram status --launch-name <launch>`

Как тестировать:

- Dry-run without token -> blocked with clear env report.
- Execute with test channel -> sends one story.
- Re-run -> skips sent story.
- Simulated Telegram API failure -> site publish remains successful, Telegram report says failed/retryable.

Готовность:

- Telegram stage does not duplicate messages.
- Site final report includes Telegram sent/skipped/failed counts.

## Phase 4 — YouTube Full Auto Stabilization

Цель: одна команда доводит хотя бы один site-approved story до `final_video.mp4`.

Файлы:

- `orchestrator/cli.py`
- new `orchestrator/youtube_full_flow.py`
- `orchestrator/youtube_from_site.py`
- `orchestrator/youtube_safe_english_bridge.py`
- `orchestrator/youtube_promo_bridge.py`
- `orchestrator/youtube_visuals_runner.py`
- `orchestrator/youtube_tts_kokoro_bridge.py`
- `orchestrator/youtube_video_segments.py`
- `orchestrator/youtube_video_drive.py`

Что менять:

- Добавить `youtube full-auto --from-site-launch <launch> --story-limit N`.
- Добавить batch loop по selected stories.
- Убрать ручной `story_id` из production flow, оставить per-story commands как debug.
- Ввести YouTube launch manifest/status per story.
- После watcher completion automatically call `import-results` and `assemble-final`.
- Параметризовать RunPod URL once per batch.
- Не использовать legacy `[6] YouTube full pipeline` как основу production flow: текущий production-grade video route живёт в `output/youtube/<story>` + `youtube_video_drive.py`.

Команды после phase:

- `python -m orchestrator youtube full-auto --from-site-launch <launch> --story-limit 1 --execute`
- `python -m orchestrator youtube resume --launch-name <launch> --execute`
- `python -m orchestrator youtube status --launch-name <launch>`

Как тестировать:

- One story with existing safe text/audio/frames -> prepare/export/import/assemble.
- One story from site selection -> full route to `final_video.mp4`.
- Missing frames -> blocked with next action.

Готовность:

- One command can produce `final_video.mp4` for at least one story when external Colab/RunPod workers complete.
- Resume does not repeat completed safe/promo/audio/video stages.

## Phase 5 — Colab / RunPod Render Production Hardening

Цель: сделать Drive render queue устойчивой для 10 workers.

Файлы:

- `START_YOUTUBE_VIDEO_PRODUCTION_10COLAB.bat`
- `START_YOUTUBE_VIDEO_WATCHER.bat`
- `START_COLAB_ALL.bat`
- `tools/colab_launcher/launch_colab_group.py`
- `configs/youtube_video_colab_workers.yaml`
- `orchestrator/youtube_video_drive.py`
- `colab_workers/youtube_video/*.ipynb`

Что менять:

- Убрать hardcoded story id из BAT, принимать `%1` или prompt.
- Перед запуском workers делать `validate-job-assets`.
- Watcher должен по completion вызывать import + assemble.
- `queue-status` должен ясно показывать permanent_failed и next retry command.
- Добавить worker health preflight по prepared notebook URL/profile.
- Добавить max-attempt policy для failed segments: `requeue-failed --segment-id` или `requeue-failed --all`.
- Синхронизировать смысл workers: `configs/youtube_video_render.yaml` сейчас содержит 5 render workers, а `configs/youtube_video_colab_workers.yaml` описывает 10 prepared browser workers.

Команды после phase:

- `python -m orchestrator youtube video production --story-id <id> --workers 10 --execute`
- `python -m orchestrator youtube video requeue-failed --story-id <id> --all --execute`

Как тестировать:

- 3 segments, 2 workers, kill one worker mid-processing.
- Verify stale reclaim returns segment to pending.
- Verify failed after max attempts appears in report.

Готовность:

- 10 worker launcher is parameterized.
- No hardcoded production story.
- Queue can recover stale and report failed deterministically.

## Phase 6 — Final BAT Menu And Docs

Цель: top-level menu должен быть понятным и безопасным.

Файлы:

- `Content-Factory-Запуск.bat`
- `START_*.bat`
- `docs/*.md`

Что менять:

- Разделить меню на Production / Smoke / Maintenance.
- Скрыть legacy dangerous `[1]` и deprecated YouTube `[6]` за Maintenance confirmation.
- Добавить:
  - `SITE FULL AUTO`
  - `SITE RESUME`
  - `SITE STATUS`
  - `YOUTUBE FULL AUTO`
  - `YOUTUBE RESUME`
  - `YOUTUBE VIDEO RENDER`
  - `QUEUE STATUS`
  - `CLEANUP / RETRY`
- В меню показывать exact command before execution.
- Явно показать, что complete `YOUTUBE RESUME` и named `CLEANUP / RETRY` пока должны появиться как production wrappers; сейчас есть только частичные команды (`phase-a --resume`, `watch-queue`, `reclaim-stale-segments`, `cleanup-partial-checkpoints`).

Команды после phase:

- BAT production menu mirrors CLI production commands.

Как тестировать:

- Dry-run click-through for every menu item.
- Confirm dangerous legacy path requires extra confirmation.

Готовность:

- Expert user can run production flow without remembering manual CLI commands.

## Phase 7 — End-To-End Smoke Tests

Цель: доказать, что проект завершён не на словах.

Файлы:

- new `tests/smoke/test_site_full_auto.py` or script under `tools/smoke/`
- new `tests/smoke/test_youtube_one_story.py`
- `docs/FINAL_PRODUCTION_CRITERIA.md`

Что менять:

- Добавить fixture stories.
- Добавить dry-run test and controlled execute test with mocked external calls where possible.
- Добавить real smoke checklist for Colab/RunPod/manual external boundary.

Команды после phase:

- `python -m orchestrator smoke site --stories 2 --execute`
- `python -m orchestrator smoke youtube --story-id <id> --execute`

Как тестировать:

- Run before release.
- Verify reports exist and no unexpected root `output/*` changes in run-scoped mode.

Готовность:

- Smoke passes from clean launch folder.
- Docs reflect actual commands.

## Top 10 Blockers

1. Telegram stage отсутствует.
2. YouTube full-auto batch command отсутствует.
3. YouTube upload/publish отсутствует.
4. YouTube artifacts are not run-scoped under `Запуски/<launch>`.
5. Site visual covers still require manual upload.
6. Site/YouTube Colab execution still requires manual OAuth/run.
7. `START_YOUTUBE_VIDEO_*` scripts hardcode one story id.
8. `.orchestrator` status/reports are global, not launch-scoped.
9. `output/site` / `output/youtube` can mix multiple launches.
10. `final-report` / cleanup preconditions are not automatically completed by full flows.

## Current Observed Failure To Use As Regression Fixture

- `Запуски/SITE_FULL_20260513_1309/status.json`: `status=failed`, `current_stage=02_Сайт/03_Визуал_для_сайта`, `can_resume=true`.
- Trace for the same launch reports `site_flow_site_run_failed` and site run exit `2`.
- Add this launch shape as a regression fixture for Phase 1/2: resume must not replay already completed selection/TTS/publish markers, and status must explain whether the next action is visual cover import, site publish asset collection, or repair.

