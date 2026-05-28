# Artifact Structure Target

Связан с `docs/AUTOMATION_READINESS_AUDIT.md`.

Главный принцип:

> Одна папка запуска = один полный мир запуска.
> Если удалить `Запуски/<LAUNCH_NAME>/`, все локальные артефакты этого запуска исчезают.
> Внешние артефакты (Google Drive, Supabase, R2, YouTube) — отдельная зона, должны быть очищаемы отдельной командой с подтверждением.

---

## 1. Целевая структура `Запуски/<LAUNCH_NAME>/`

```
Запуски/<LAUNCH_NAME>/
  manifest.json                 # текущая launch-метадата (recovery_execute, site_flow_bat, etc.)
  status.json                   # aggregate launch status (refresh_launch_status_file)
  README_СТРУКТУРА_ЗАПУСКА.md

  00_Манифесты/                 # новое: snapshot всех конфигов на момент запуска
    orchestrator.yaml.snapshot
    runtime_modes.yaml.snapshot
    site_tts.yaml.snapshot
    youtube_video_render.yaml.snapshot
    gemini_bots_registry.yaml.snapshot
    paths.yaml.snapshot
    launch_environment.json    # py версии, env vars (без секретов), platform

  01_Общее/                    # уже есть
    01_Исходные_рассказы/      # копии .txt входа (опционально через --input-snapshot)
    02_Фильтр_по_длине/
    03_Первичный_отбор_Gemini/
    input_snapshot/            # уже есть; если --input-snapshot, исходники сюда
  02_Сайт/
    01_Очистка_текста/<story>/cleaned_story.txt
    02_Информация_для_сайта_Gemini/<story>/{site_info.json, info.en.txt, raw_response.txt, validation.json}
    03_Визуал_для_сайта/
      Обложки_ЗАГРУЗИТЕ_СЮДА/   # сюда пользователь руками кладёт обложки
      <story>/<story>.jpg       # после import_site_visuals
    04_Озвучка_для_сайта/<story>/{<story>.mp3, txt_chunks/...}
    05_Публикация_на_сайт/      # P0-4 DONE: site story package (бывший output/site/<story>/)
      site_publish_manifest.json # P0-4 DONE: launch_name + collected_count + missing_assets_count + source_paths
      <story>/{info.txt, <story>.mp3, <story>.jpg, <story>__M.txt, site_info.json, story_manifest.json, published.json (после publish)}
      _to_publish/<story>/      # P0-4 DONE: staging для legacy autopublisher (бывший legacy/autopublisher/To_Publish/<story>/)
  03_YouTube/                  # НОВОЕ: сейчас пустой scaffold; будет жить YouTube story tree
    <story>/
      00_source/
      01_selection/
      02_safe_story/safe_story.txt
      03_promo/
      04_audio/narration.mp3
      05_characters/characters.txt
      06_prompts/prompts_list.txt   # 06_director или 06_prompts в зависимости от Gemini source
      07_frames/<frame>.png
      08_video/
        segments/<segment_id>.mp4    # imported back from Drive
        final_video.mp4              # final assembled
      _drive_binding.json            # путь к Drive video_jobs folder для этого рассказа
      youtube_story_manifest.json
    _selection/                  # бывший runs/youtube/<id>/_selection
    _gemini_selection/
    _gemini_safe/
  04_Telegram/                 # НОВОЕ: создаётся, добавить D04_TELEGRAM в top_level_dirs
    _channel_binding.json        # bot_token alias + channel_id (НЕ сам токен — он в .env.telegram)
    <story>/
      post.txt
      image.jpg
      raw_text.txt               # neочищенный текст (source.txt или story__[MFU].txt — спека ниже)
      site_url.txt
      sent.json                  # idempotency marker: {message_id, sent_at, channel_id}
  05_Рассказы/                 # уже есть; per-story human-readable mirror
    <story_id>/
      01_Общее/source.txt
      02_Отбор/{result.json, raw_response.txt, validation.json}
      03_Сайт/
        01_Очищенный_текст/cleaned_story.txt
        02_Информация_для_сайта/{site_info.json, info.en.txt, validation.json}
        03_Визуал/<story>.jpg
        04_Озвучка/audio.mp3
        05_Публикация/{result.json, .published_ok, published.json}
      04_YouTube/
        08_Telegram/             # пока pending; после Telegram stage будет sent.json
      status.json
      tmp/
  06_Отчёты/                   # уже есть
    ФИНАЛЬНЫЙ_ОТЧЁТ.json
    ФИНАЛЬНЫЙ_ОТЧЁТ.csv
    cleanup_manifest.json
    site_flow_bat.summary.json
    youtube_flow.summary.json
    telegram_flow.summary.json    # НОВОЕ
    incremental_progress_sync.json
  07_Логи/                     # уже есть, но улучшить mirror
    events.jsonl                 # mirror of .orchestrator/events.jsonl (filtered by launch)
    status.jsonl                 # mirror of .orchestrator/status.jsonl
    reports/                     # mirror of .orchestrator/reports/ (этот launch)
    site_flow_bat.jsonl
    phase_a_subprocess.log
    phase_b_subprocess.log
    site_run.log
  08_Карантин/                 # уже есть
    _visuals_clean_quarantine_<ts>/
    _frames_reset_<ts>/
    _stale_locks_<ts>/
  09_Архив/                    # уже есть; пусто пока launch жив, заполняется после archive --execute
  10_Временные_файлы/          # уже есть
    legacy/                      # технический mirror
      runs/site/<run>-a/_phase_a/...
      runs/site/<run>-b/_phase_b/...
      runs/youtube/<run>/_phase_a/...
      output/site/<story>/...      # backward compat (если нужно отдать legacy publisher), но source of truth — `02_Сайт/05_Публикация_на_сайт/`
      output/youtube/<story>/...
    site_flow_bat_state.json
    recovery_queue_map.json
    last_sync_report.json
    last_progress_sync_report.json
    orchestrator_launch_trace.json
    test_input/                   # smoke staging
    test_input_recovery/          # recovery staging
  11_Отчёты_внешние/           # опционально: ссылки на Supabase / R2 / YouTube / Telegram
    supabase_publish_log.jsonl    # копия из autopublisher
    r2_object_keys.json
    youtube_uploads.json
    telegram_messages.json
  12_Логи_воркеров/            # опционально: dump-ы Colab worker status JSON
    site_tts/<worker_email>_status.json
    youtube_video/<worker_email>_status.json
```

> Папки `11_Отчёты_внешние/` и `12_Логи_воркеров/` опциональны (см. P1-7, P1-9 в backlog). Если они не нужны — оставить только 00..10.

---

## 2. Текущие пути → целевые

| Current path | Producer | Consumer | Artifact type | Source of truth? | Target launch path | Action |
|---|---|---|---|---|---|---|
| `stories/input/*.txt` | пользователь / sample-library | phase_a intake | inbox | да (inbox) | остаётся как inbox; копия в `<launch>/01_Общее/01_Исходные_рассказы/` при `--input-snapshot` | оставить |
| `runs/site/<id>-a/_phase_a/...` | phase_a (без launch-dir) | phase-b | legacy | нет | `<launch>/10_Временные_файлы/legacy/runs/site/<id>-a/_phase_a/...` | перенести (`--launch-dir` уже есть, deprecate global) |
| `runs/site/<id>-a/_phase_b/...` | phase-b | run --pipeline site | legacy | нет | `<launch>/10_Временные_файлы/legacy/runs/site/<id>-a/_phase_b/...` | перенести |
| `runs/youtube/<id>/_selection, _gemini_*` | youtube_from_site | youtube continue | YouTube run state | нет (legacy) | `<launch>/03_YouTube/_selection, _gemini_selection, _gemini_safe` | перенести |
| `output/site/<story>/{info.txt, *.mp3, *.jpg, *__[MFU].txt}` | site_tts + collect-assets + import_site_visuals | autopublisher | site package | **P0-4 DONE**: при `--launch-name` пишется в launch; без флага — legacy fallback | `<launch>/02_Сайт/05_Публикация_на_сайт/<story>/` | **реализовано** (см. `orchestrator/site_publish/paths.py::resolve_site_publish_output_dir`) |
| `output/site/<story>/site_info.json, story_manifest.json` | collect-assets | publish / debug | metadata | в launch (P0-4) | `<launch>/02_Сайт/05_Публикация_на_сайт/<story>/` | реализовано |
| `output/youtube/<story>/00..09_*` | youtube_visuals_run + video_drive + tts | assemble-final / upload | YouTube package | **проблема**: должен быть в launch | `<launch>/03_YouTube/<story>/00..08/` | **перенести** (P0-4) |
| `output/youtube/<story>/youtube_story_manifest.json` | youtube_visuals_run | visuals-status | metadata | в launch | `<launch>/03_YouTube/<story>/youtube_story_manifest.json` | перенести |
| `output/youtube/<story>/_visuals_clean_quarantine_*` | visuals-clean | hist | quarantine | временное | `<launch>/08_Карантин/_visuals_clean_quarantine_<ts>/` | перенести |
| `legacy/autopublisher/To_Publish/<story>/` | site_publish/prepare | publish_stories.py | staging | временное (P0-4) | `<launch>/02_Сайт/05_Публикация_на_сайт/_to_publish/<story>/` | **P0-4 DONE**: при `--launch-name` resolver кладёт в launch; без флага — legacy fallback |
| `.orchestrator/events.jsonl` | EventLogger | reports | global service log | global, но mirror | дублировать в `<launch>/07_Логи/events.jsonl` (filtered by launch) | mirror (P1-1) |
| `.orchestrator/status.jsonl` | StatusStore | status command | global | дублировать в `<launch>/07_Логи/status.jsonl` | mirror |
| `.orchestrator/reports/run_manifest_*.json` | Runner | reports | global | копировать в `<launch>/07_Логи/reports/` | mirror |
| `.orchestrator/site_publish_*.json` | site_publish | reports | global | копировать в `<launch>/06_Отчёты/` | mirror |
| `reports/site_publish_*.json` | site_publish | external | global | копировать в `<launch>/06_Отчёты/` | mirror |
| `Запуски/<name>/01..10/...` | mirror_*, human_launch_* | пользователь / status | human-readable | да | оставить как есть | оставить |
| `Запуски/<name>/10_Временные_файлы/legacy/runs/site/...` | phase-a/b с `--launch-dir` | resume | isolated legacy | да | как есть | оставить |
| `Запуски/<name>/10_Временные_файлы/legacy/output/site/<story>/` | site Runner с `--launch-dir` (kokoro-drive) | autopublisher | isolated site outputs | да | **переименовать**: source of truth — `<launch>/02_Сайт/05_Публикация_на_сайт/<story>/`, legacy mirror — `<launch>/10_Временные_файлы/legacy/output/site/<story>/` | оставить как mirror, но source поменять |
| `G:\Мой диск\ContentFactory_TTS\{texts, mp3, job, scripts, cache, logs}` | site_tts kokoro-drive | Colab worker | external handoff | external | нельзя положить в launch; ссылку записать в `<launch>/10_Временные_файлы/site_tts_drive_binding.json` | external + binding |
| `G:\Мой диск\ContentFactory_YouTube\video_jobs\<story>\queue\...` | youtube_video_drive | Colab worker | external handoff | external | external + `<launch>/03_YouTube/<story>/_drive_binding.json` | external + binding |
| `G:\Мой диск\ContentFactory_YouTube\<run>\{texts, mp3}` | youtube_tts_kokoro_bridge | Colab worker | external handoff | external | external + binding | external + binding |
| `models/...` | one-time setup | local TTS | ML weights | global | не трогать | оставить |
| `legacy/...` | Git | runtime call | code | global | не трогать | оставить |
| `archive/stories_input/<ts>` | archive-input | бэкап | архив | временное | `<launch>/09_Архив/stories_input/<ts>` или global archive | оставить |
| `Запуски/<name>/05_Рассказы/<id>/03_Сайт/...` | mirror_site_story_outputs_from_legacy_site | per-story status | mirror | mirror | как есть | оставить |
| `Запуски/<name>/05_Рассказы/<id>/04_YouTube/08_Telegram/` | ensure_telegram_story_scaffold | telegram stage (future) | scaffold | mirror | заполнить через telegram stage | дополнить (P0-2) |

---

## 3. Финальные vs временные

### Финальные (живут после `cleanup --execute`, но до `delete launch --execute`)

- `<launch>/manifest.json + status.json`
- `<launch>/00_Манифесты/*.snapshot`
- `<launch>/02_Сайт/05_Публикация_на_сайт/<story>/{info.txt, *.mp3, *.jpg, *__[MFU].txt, site_info.json, story_manifest.json, published.json, .published_ok}`
- `<launch>/03_YouTube/<story>/{02_safe_story.txt, 04_audio/narration.mp3, 08_video/final_video.mp4, youtube_story_manifest.json}`
- `<launch>/04_Telegram/<story>/{post.txt, sent.json}`
- `<launch>/05_Рассказы/<story>/status.json + 03_Сайт/02_Информация_для_сайта/info.en.txt + 03_Сайт/05_Публикация/.published_ok`
- `<launch>/06_Отчёты/{ФИНАЛЬНЫЙ_ОТЧЁТ.*, cleanup_manifest.json}`
- `<launch>/11_Отчёты_внешние/*` (если решим внедрить)

### Временные (могут удаляться через cleanup-plan)

- `<launch>/10_Временные_файлы/legacy/...` (включая legacy phase-a/b artifacts, output mirrors, To_Publish staging)
- `<launch>/10_Временные_файлы/site_flow_bat_state.json` (после успешного завершения)
- `<launch>/10_Временные_файлы/{last_sync_report, recovery_queue_map}`.json (опционально оставить для аудита)
- `<launch>/07_Логи/*` (опционально, можно архивировать)
- `<launch>/08_Карантин/*` (после ручной проверки)
- `<launch>/12_Логи_воркеров/*`

### Не должны быть в launch вообще

- `models/`, `legacy/`, `tools/`, `tests/`, `docs/`, `configs/`
- `.venv-tts/`, `.pytest_cache/`, `__pycache__/`
- `G:\Мой диск\...` (Drive — external)

---

## 4. Минимальные изменения кода, чтобы это работало

1. Все производители `output/site/` принимают `--bridge-output-site <dir>` или читают `--launch-name`/`--launch-dir`:
   - `legacy/autopublisher/publish_stories.py` уже принимает `--bridge-output-site`.
   - `orchestrator/site_publish/{prepare.py, collect_assets.py, publish.py}` — **P0-4 DONE**: `--launch-name`/`--launch-dir` через единый resolver `orchestrator/site_publish/paths.py::resolve_site_publish_output_dir`. При флаге пишется в `<launch>/02_Сайт/05_Публикация_на_сайт/`, без флага — legacy `output/site/`.
   - `orchestrator/wrappers/autopublisher.py` — **TODO (P0-1)**: при run-site-flow передавать launch-dir в wrapper, чтобы Runner-режим тоже писал в launch (сейчас resolver есть, но wrapper всё ещё указывает на `<artifact_root>/output/site`).
2. YouTube:
   - `orchestrator/youtube_from_site.py` — `_youtube_run_root` принимает `--launch-dir`.
   - `orchestrator/youtube_visuals_runner.py` — `output/youtube/<story>` → `<launch>/03_YouTube/<story>`.
   - `orchestrator/youtube_video_drive.py` — `_output_youtube_story_root` (используется в import-results) учитывает `--launch-dir`.
3. `human_launch_layout.py::top_level_dirs()` добавить `D04_TELEGRAM` и `_All_artifacts_marker_for_delete.json`.
4. `human_launch_lifecycle.py::delete_launch` — добавить опциональный Drive cleanup.

---

## 5. Совместимость

- Legacy путь `output/site/<story>/` оставить как **mirror** через `10_Временные_файлы/legacy/output/site/`. Если кому-то нужно отдать legacy publisher, можно симлинком (на Windows — junction) или копией.
- `runs/site/<run>` и `runs/youtube/<run>` оставить только для smoke / dev. Production-меню никогда туда не пишет.
- `.orchestrator/` остаётся как global service dir (events/status), но reports должны зеркалироваться.

---

## 6. Финальный чек

`delete launch <X> --execute --drive-cleanup` должен:
1. Записать summary в `История_запусков/<X>_summary.{json,csv}`.
2. Удалить `Запуски/<X>/`.
3. Удалить external Drive артефакты (если `--drive-cleanup`):
   - `G:\Мой диск\ContentFactory_TTS\<run_id>\`
   - `G:\Мой диск\ContentFactory_YouTube\video_jobs\<story_slug>\`
4. На сайте/Supabase/R2/YouTube/Telegram — НЕ трогать (это уже опубликованные внешние артефакты; для отзыва — отдельный workflow).

Если шаг 2 убрал все локальные данные — критерий «удалил папку = всё локально пропало» выполнен.
