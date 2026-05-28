# Content-Factory — Implementation Backlog

Связан с `docs/AUTOMATION_READINESS_AUDIT.md`.

Приоритеты:

- **P0** — обязательно до чистового запуска 400–500 рассказов.
- **P1** — важно, но допустимо после первого controlled production run.
- **P2** — улучшения / удобство.

Acceptance criteria — проверяемые. Нет «вообще лучше».

---

## P0 — обязательно до чистового запуска

### P0-1. Вшить `site-publish collect-assets + prepare` в `run-site-flow`

| Поле | Значение |
|---|---|
| Why needed | Сейчас после `run-site-flow` пакеты `output/site/<story>/{text,info,mp3,image}` собираются руками. Это причина `prepare scanned=0`. Без этого one-button не работает. |
| Files likely affected | `orchestrator/human_launch_site_flow_bat.py`, `orchestrator/site_publish/collect_assets.py`, `orchestrator/wrappers/autopublisher.py` |
| Acceptance criteria | После `run-site-flow --bat-profile kokoro-drive --execute` без ручных команд: `output/site/<story>/<story>.mp3 + info.txt + *.jpg + *__M/F/U.txt` собраны для каждого ready рассказа. `site_publish_collect_assets_report.json.packages_ready` равен числу ready stories. `autopublisher` wrapper не возвращает `scanned=0`. |

### P0-2. Telegram stage (raw text + post + image + URL)

| Поле | Значение |
|---|---|
| Why needed | Пользователь хочет, чтобы рассказ ушёл в Telegram одновременно с публикацией на сайт. Сейчас отсутствует. |
| Files likely affected | `orchestrator/wrappers/telegram_publish.py` (new), `orchestrator/site_publish/...`, `orchestrator/human_launch_layout.py` (добавить `D04_TELEGRAM` в `top_level_dirs`), `orchestrator/cli.py` (add `telegram` subcommand), `configs/orchestrator.example.yaml` (telegram block), `.env.telegram` |
| Acceptance criteria | (1) `python -m orchestrator telegram prepare --launch-name X` показывает план без отправки. (2) `telegram send --launch-name X --execute` отправляет N рассказов в `TELEGRAM_CHANNEL_ID`. (3) повторный `telegram send` skips уже отправленные (`telegram_sent.json` per story). (4) `Запуски/<name>/04_Telegram/<story>/{post.txt, image.jpg, sent.json}` создаётся. (5) `published_telegram` counter в `launch status.json` увеличивается. См. `docs/TELEGRAM_STAGE_SPEC.md`. |

### P0-3. Перенести `output/site/<story>/` под `Запуски/<name>/...` (partially implemented)

| Поле | Значение |
|---|---|
| Why needed | Сейчас 836 рассказов в `output/site/` — пересечение всех запусков. Удалить папку запуска не получается «чисто». |
| Files likely affected | `orchestrator/site_publish/paths.py` (NEW), `orchestrator/site_publish/{prepare,collect_assets,publish}.py`, `orchestrator/cli.py`. ВНЕ ЗАДАЧИ (для следующего шага): `wrappers/autopublisher.py` через Runner и `youtube_from_site.py`. |
| Acceptance criteria | После `run-site-flow --bat-profile kokoro-drive --execute` все site story packages лежат в `Запуски/<LAUNCH>/02_Сайт/05_Публикация_на_сайт/<story>/`. `output/site/` в корне репо НЕ обновляется. `delete launch --execute` удаляет все site артефакты этого запуска. |
| Status (2026-05-27) | **partially implemented** (запрошено как P0-4: run-scoped paths для site-publish output). Реализовано: единый resolver `orchestrator/site_publish/paths.py::resolve_site_publish_output_dir(launch_name, story_id, launch_dir)`. `site-publish collect-assets / prepare / publish` приняли `--launch-name`/`--launch-dir`. При флаге пишут в `Запуски/<LAUNCH>/02_Сайт/05_Публикация_на_сайт/<story>/`, без флага — legacy fallback `output/site/<story>/`. To_Publish bridge в run-scoped режиме: `…/05_Публикация_на_сайт/_to_publish/<story>/`. Манифест `site_publish_manifest.json` пишется на каждом stage. Осталось: интеграция в `run-site-flow` (P0-1) и `autopublisher` wrapper (передача `--launch-dir` через Runner). |

### P0-4. Перенести `output/youtube/<story>/` под `Запуски/<name>/...`

| Поле | Значение |
|---|---|
| Why needed | YouTube story tree (00..09) сейчас в `output/youtube/<story>/`, не isolated. |
| Files likely affected | `orchestrator/youtube_visuals_runner.py`, `youtube_video_drive.py`, `youtube_video_segments.py`, `youtube_from_site.py`, `youtube_tts_kokoro_bridge.py`, `wrappers/autovideo.py`, `wrappers/director20.py` |
| Acceptance criteria | YouTube артефакты пишутся в `Запуски/<LAUNCH>/03_YouTube/<story>/00_source ... 08_video/`. `delete launch --execute` чистит и YouTube артефакты. Drive артефакты (`G:\Мой диск\ContentFactory_YouTube\video_jobs\<story>\`) остаются external, но их путь записан в `<launch>/03_YouTube/<story>/_drive_binding.json`. |

### P0-5. Production меню в bat (separate Production / Smoke / Maintenance)

| Поле | Значение |
|---|---|
| Why needed | Сейчас [1] legacy danger и [2] production рядом. Можно нажать не туда и записать в global runs/output. |
| Files likely affected | `Content-Factory-Запуск.bat` |
| Acceptance criteria | Новое главное меню: Production (Site / YouTube / TTS / Status / Reports), Smoke / Diagnostics, Maintenance. Legacy [1] и [6] недоступны без переключения в Maintenance. См. `docs/BAT_MENU_TARGET.md`. |

### P0-6. Idempotent batch YouTube flow

| Поле | Значение |
|---|---|
| Why needed | YouTube сейчас per-story. Чтобы публиковать 50 видео в неделю одной кнопкой — нужен batch. |
| Files likely affected | `orchestrator/cli.py`, `orchestrator/youtube_from_site.py`, новый `orchestrator/youtube_batch_flow.py` |
| Acceptance criteria | `python -m orchestrator youtube run-batch --youtube-run-id <id> --site-run-id <id> --execute` идёт по списку YES-stories из `_gemini_selection/output` и для каждого: safe-english-run → promo-run → visuals-run --auto-gemini → tts-export → wait → tts-import → video prepare-segments → export-job → wait workers → import-results → assemble-final. Re-run скипает завершённые. Все промежуточные команды — те же что и сейчас. |

### P0-7. `final-report` + `published_telegram` в `run-site-flow`

| Поле | Значение |
|---|---|
| Why needed | Сейчас `final-report` руками. Без него `cleanup_manifest.json` не пишется и `delete launch --execute` блокируется. |
| Files likely affected | `orchestrator/human_launch_site_flow_bat.py`, `orchestrator/human_launch_lifecycle.py::generate_final_report_launch` |
| Acceptance criteria | После успешного `run-site-flow`: `Запуски/<name>/06_Отчёты/{ФИНАЛЬНЫЙ_ОТЧЁТ.json, cleanup_manifest.json}` существуют. `published_site + published_telegram` правильно посчитаны. |

### P0-8. `delete launch` чистит Drive (опционально, с подтверждением)

| Поле | Значение |
|---|---|
| Why needed | Сейчас `delete launch --execute` чистит только локальную папку. Drive copies остаются. |
| Files likely affected | `orchestrator/human_launch_lifecycle.py::delete_launch`, новый helper в `site_tts/colab_batch.py` и `youtube_video_drive.py` для drive cleanup |
| Acceptance criteria | `delete launch --execute --drive-cleanup` дополнительно удаляет `G:\Мой диск\ContentFactory_TTS\<run_id>\` (если cleanup_after_success=true в config) и `G:\Мой диск\ContentFactory_YouTube\video_jobs\<story>\` для всех stories запуска. Без `--drive-cleanup` — только local. |

### P0-9. Watcher loop для youtube_video assigned queues

| Поле | Значение |
|---|---|
| Why needed | Сейчас reclaim-stale запускается руками. При 5 Colab workers и десятках сегментов — будут зависания. |
| Files likely affected | `orchestrator/youtube_video_drive.py`, `Content-Factory-Запуск.bat` |
| Acceptance criteria | `python -m orchestrator youtube video watcher --story-id X --interval-min 5 --reclaim-stale-min 30` запускает бесконечный loop: каждые 5 мин `queue-status`, каждые 30 мин `reclaim-stale-segments --execute`. Корректно ловит Ctrl+C. |

### P0-10. Stable resume гарантия для phase-a (recovery_queue_map.json всегда)

| Поле | Значение |
|---|---|
| Why needed | При отсутствии `recovery_queue_map.json` `run-site-flow` может проиграть весь `stories/input` повторно. |
| Files likely affected | `orchestrator/human_launch_site_bootstrap.py`, `orchestrator/human_launch_legacy_sync.py`, `orchestrator/launch start-site`. |
| Acceptance criteria | После первого успешного `phase-a` запуска `Запуски/<name>/10_Временные_файлы/recovery_queue_map.json` создаётся. При resume `run-site-flow` уважает его строки и не повторно отправляет уже `selection_done=true` рассказы. |

---

## P1 — важно, но можно после первого controlled run

### P1-1. Mirror `.orchestrator/{events,status,reports,logs}` → `Запуски/<name>/07_Логи/`

| Why needed | Логи `.orchestrator/` сейчас в корне репо. Хочется иметь все логи запуска в его папке. |
| Files | `orchestrator/events.py`, `orchestrator/status.py`, `orchestrator/runner.py` |
| Acceptance | Запуск с `--launch-dir <X>` дублирует append в `<X>/07_Логи/events.jsonl` и `status.jsonl`. Главные reports тоже копируются в `<X>/07_Логи/reports/`. |

### P1-2. Automatic mark-missing-skipped после `max_wait_hours`

| Why needed | Сейчас при зависании TTS пользователь ждёт сутки или жмёт `mark-missing-skipped` руками. |
| Files | `orchestrator/site_tts/colab_batch.py` |
| Acceptance | `site-tts kokoro-colab wait-drive --auto-mark-missing-skipped-after-hours 12` после 12 часов без прогресса помечает оставшиеся как `manual_skipped` с reason `auto_timeout_12h`. Site publish продолжает с partial. |

### P1-3. Smoke-режим в новом меню

| Why needed | `smoke-site-cycle`, `init-bridge-fixture`, `phase-b --allow-scaffold` нужны для тестов, но не должны быть рядом с production. |
| Files | `Content-Factory-Запуск.bat` |
| Acceptance | Smoke / Diagnostics секция в меню содержит smoke-site-cycle, init-bridge-fixture, scaffold phase-b, preflight, dry-run audits. Production меню их не показывает. |

### P1-4. Status / progress dashboard

| Why needed | Сейчас прогресс — это `sync-progress` или `queue-status`. Нет единой команды «как идёт запуск». |
| Files | новый `orchestrator/launch dashboard` |
| Acceptance | `python -m orchestrator launch dashboard --name X` показывает: stories total, ready_to_publish, published, telegram_sent, youtube_video_assembled, errors. Read-only. |

### P1-5. Cover auto-prepare через Gemini

| Why needed | Сейчас пользователь руками кладёт обложки. Можно через site_info.visual_prompt + flux/SDXL generate. |
| Files | новый `orchestrator/site_visual/auto_generate.py`, `wrappers/...` |
| Acceptance | Опциональная команда `site-visual auto-generate --launch X --execute` для рассказов без image. Создаёт `<story>.jpg`. Не блокирует pipeline, но если включена в run-site-flow — пропуск рассказов с missing image снижается. |

### P1-6. YouTube upload stage

| Why needed | После `assemble-final` видео руками заливается на YouTube. Можно автоматизировать. |
| Files | новый `orchestrator/youtube_upload/...`, `runtime_modes.youtube_publish=api` |
| Acceptance | `youtube upload --story-id X --execute` через YouTube Data API заливает `final_video.mp4`, ставит title/description/tags из safe story + site_info. Idempotent (per-story marker). |

### P1-7. Live YouTube launch status в `Запуски/<name>/03_YouTube/`

| Why needed | YouTube артефакты не зеркалируются в launch. |
| Files | `orchestrator/youtube_visuals_runner.py`, новый `youtube_launch_sync.py` |
| Acceptance | После `visuals-run` / `video assemble-final` финальные артефакты копируются в `Запуски/<name>/03_YouTube/<story>/{02_safe_story.txt, final_video.mp4, characters.txt, prompts_list.txt}`. |

### P1-8. Drive disk space pre-check

| Why needed | Site TTS / YouTube video Drive могут переполнить квоту 15GB. |
| Files | `site_tts/colab_batch.py + youtube_video_drive.py` (preflight) |
| Acceptance | Перед export job — оценка размера; warning если total > 80% бесплатной квоты Drive.  |

### P1-9. Атомарная отметка `published_site_url` в результат

| Why needed | Чтобы Telegram мог послать URL, нужно сохранить site URL после publish. |
| Files | `legacy/autopublisher/publish_stories.py` (если можно патчить) или `wrappers/autopublisher.py` парсит stdout и пишет `output/site/<story>/published.json` |
| Acceptance | После publish для каждого `<story>` создаётся `published.json` с `{site_url, supabase_record_id, r2_audio_key, published_at}`. Telegram читает оттуда. |

### P1-10. CI / pre-flight static checks в bat

| Why needed | Сейчас preflight — отдельный пункт. Можно автоматом перед run-site-flow. |
| Files | `Content-Factory-Запуск.bat`, `orchestrator/preflight.py` |
| Acceptance | `[Site Production] → Full Run` сначала вызывает `preflight --pipeline site --run-profile dry-run-all` и если есть blockers — спрашивает «продолжить?». |

---

## P2 — удобства

### P2-1. Web UI вместо bat

| Why needed | bat-меню не масштабируется. |
| Files | новый `app/` (FastAPI или streamlit) |
| Acceptance | Запуск через одну кнопку в браузере; статус на той же странице. Опционально. |

### P2-2. Параллельные launch (Site + YouTube одновременно)

| Why needed | Сейчас один launch блокирует второй (Chrome user_data конфликты). |
| Files | `orchestrator/phase_a_gemini_supervisor.py` |
| Acceptance | Можно одновременно держать Site phase-a и YouTube safe-english-run, если использовать разные account profiles. |

### P2-3. Анти-throttling для Google Drive

| Why needed | На больших batch Drive копирует мееедленно или 0-byte файлы. |
| Files | `site_publish/collect_assets.py`, `youtube_video_drive.py` |
| Acceptance | Retry/backoff с экспоненциальной задержкой; predict Drive cache delay. |

### P2-4. Поддержка нескольких Telegram каналов / групп

| Why needed | Можно публиковать разные жанры в разные каналы. |
| Files | `configs/telegram_channels.yaml` |
| Acceptance | `telegram send --channel <slug>` берёт `bot_token / channel_id` из registry. |

### P2-5. Лог-метрики через JSON Schema

| Why needed | `events.jsonl` сейчас free-form. Хочется чёткой схемы. |
| Files | `orchestrator/events.py` |
| Acceptance | каждый event валидируется против `events.schema.json`; pre-commit hook. |

### P2-6. История запусков с поиском

| Why needed | `История_запусков/` сейчас плоский список csv. |
| Files | `orchestrator/human_launch_lifecycle.py::delete_launch` + новая команда `history list/search` |
| Acceptance | `launch history list --status completed --published-gt 100` — отчёт по архивным запускам. |

### P2-7. Cleanup quarantine auto-old (>30 days)

| Why needed | `Запуски/_Карантин_старых_запусков/` копится. |
| Files | `orchestrator/cleanup.py` |
| Acceptance | `quarantine purge --older-than-days 30 --execute` удаляет старый карантин. |

---

## Risk register (extended)

См. секцию 7 в `docs/AUTOMATION_READINESS_AUDIT.md` + дополнительно:

| Risk | Pipeline | Severity | Symptom | Recommended fix priority |
|---|---|---|---|---|
| Pip env drift (boto3 / tinytag / kokoro missing) | site / publish | high | publish `blocked: missing dependency` | P0 (env-doctor уже есть; добавить в preflight Production) |
| .env.site_publish missing or wrong | site publish | high | env-doctor returns blockers | P0 (env-doctor вызывается; UI улучшить) |
| Chrome user_data corrupted | site / youtube | high | Gemini login loop | P1 (auto-reset user_data per profile) |
| RunPod URL expired mid-run | youtube | medium | frames-runpod fails | P1 (один раз спросить и reuse в batch) |
| 5 Colab workers одновременно стартуют разные jobs одной story | youtube | medium | duplicate processing | P1 (assigned queues решают, но dispatch должен учитывать concurrency) |
| ffmpeg crash / GPU OOM на Colab | youtube | medium | segment failed | P1 (auto-retry в reclaim-stale, если worker logger показывает OOM — переставить) |
| Tokens leak в `events.jsonl` | telegram (future) | high | secrets in repo logs | P0 при подключении telegram (redact + safety rule) |
