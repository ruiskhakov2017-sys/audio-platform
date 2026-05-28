# Content-Factory — Automation Readiness Audit

Дата: 2026-05-27. Режим: read-only аудит (никакой production-логики не трогаем).

Цель — оценить, насколько проект готов к режиму «одна кнопка для Site, одна кнопка для YouTube», и зафиксировать gaps.

---

## 0. TL;DR

- Site pipeline частично one-button готов: `Content-Factory-Запуск.bat → [2] → [A]` запускает `launch run-site-flow`, который сам гоняет phase-a, phase-b, `run --pipeline site` и инкрементально синкает legacy → `Запуски/<имя>/`. Но **есть три критических разрыва**:
  - публикация в Telegram отсутствует как stage (есть только пустые папки-заглушки).
  - `site-publish prepare/collect-assets` живут отдельным шагом и не вшиты в `run-site-flow`.
  - финальные артефакты сайта пишутся не в `Запуски/<name>/...`, а в `output/site/<story>/` и `legacy/autopublisher/To_Publish/<story>/` в корне репо.
- YouTube pipeline сейчас **per-story state machine**, batch-режима «одна кнопка для всех» нет. Меню `[Y] YouTube Visuals` дёргает отдельные команды по `--story-id`. `[6] YouTube pipeline` в bat — это старый `phase-a → phase-b → run --pipeline youtube` через global `runs/youtube`, он рассыпан и не использует assigned-queue worker model.
- Telegram stage отсутствует полностью. Только scaffold `05_Рассказы/<id>/04_YouTube/08_Telegram/` и счётчик `published_telegram=0`.
- Artifact structure не «одна папка запуска = один мир»: даже при kokoro-drive профиле сайт пишет в `Запуски/<name>/10_Временные_файлы/legacy/output/site/...`, но `site-publish prepare` читает `output/site/...` в корне репо. YouTube вообще ничего не пишет в `Запуски/<name>/`.

---

## 1. Site pipeline automation audit

### 1.1 Главный entry point

| Уровень | Команда |
|---|---|
| Bat | `[2] Site full → [A]` |
| Python | `python -m orchestrator launch run-site-flow --name <LAUNCH> --stories-dir stories/input --bat-profile kokoro-drive --limit 0 --execute` |
| Реализация | `orchestrator/human_launch_site_flow_bat.py::run_site_flow_bat_execute` |

`run-site-flow` делает:

1. `set-mode site_tts_engine kokoro_colab_drive` (если профиль `kokoro-drive`).
2. `phase-a` через `orchestrator/human_launch_phase_a_subprocess.py` с live polling каждые ~120 сек: copy legacy artifacts to `Запуски/<name>/...` and refresh status.
3. `phase-b --branch site --allow-scaffold` (для kokoro-drive профиля).
4. `orchestrator run --pipeline site --launch-dir <launch>` — Runner вызывает wrappers: `bulk_text_cleaner → gemini_auto → site_tts → content_combiner → autopublisher`.
5. После каждого шага — `mirror_phase_a_progress_to_human` и `refresh_launch_status_file`.
6. State каждого шага хранится в `Запуски/<name>/10_Временные_файлы/site_flow_bat_state.json` → resume-safe: если `returncode==0` и нужный артефакт есть, шаг скипается.

### 1.2 Что реально автоматизировано

| Stage | Status | Где живёт |
|---|---|---|
| intake / length filter | automated | `runs/site/<id>-a/_phase_a/...` mirrored в `Запуски/<name>/01_Общее/02_Фильтр_по_длине/` |
| Gemini #1 selection | semi_automated | внутри `phase_a`. Реально жмёт кнопки в `legacy/Gemini_Auto/gemini_auto.py` через Playwright + Chrome user_data_0..4. Может упасть если ботам не хватает аккаунтов, не запущен Chrome или Gemini вернёт ошибку — нужна visual проверка. |
| text cleaning | automated | `bulk_text_cleaner` wrapper |
| Gemini site info | semi_automated | `gemini_auto` stage, тот же риск что и selection. Возвращает `site_info.json` + рендерит `info.en.txt`. |
| Site visual (covers) | manual | пользователь руками кладёт обложки в `Запуски/<name>/02_Сайт/03_Визуал_для_сайта/Обложки_ЗАГРУЗИТЕ_СЮДА/`. `site-info-visual` команды только валидируют CSV и могут передёргивать Gemini за info. |
| Site TTS (kokoro_colab_drive) | semi_automated | Wrapper `site_tts_stage` экспортирует txt в `G:\Мой диск\ContentFactory_TTS\texts\`, ждёт mp3 в `mp3/`, импортирует обратно. Реальный Colab запускается **руками** (открыть notebook, нажать Run). Manual skip / partial — через `mark-skipped` / `mark-missing-skipped`. |
| site-publish collect-assets | manual | не вызывается `run-site-flow`. Чтобы собрать `output/site/<story>/{text,info,image,mp3}` пакеты, пользователь руками выполняет `site-publish collect-assets --execute`. |
| site-publish prepare → To_Publish | semi_automated | Внутри Runner: `autopublisher` wrapper по умолчанию вызовет `legacy/autopublisher/publish_stories.py --headless --bridge-output-site output/site --to-publish-dir legacy/autopublisher/To_Publish/`. Но если пакеты в `output/site` не собраны (collect-assets не запускали), prepare даст `scanned=0`. |
| site publish (Supabase + R2) | automated | через `autopublisher` wrapper, если в `output/site/<story>/` есть info+mp3+jpg+text и env-doctor пройден (.env.site_publish, SUPABASE_*, R2 keys). |
| **Telegram send** | **missing** | Нет ни stage, ни кода, ни конфига, ни env. |
| sync to launch folder | automated | После каждого шага копируется `cleaned_story/info.en.txt/audio.mp3/.published_ok` в `Запуски/<name>/05_Рассказы/<id>/03_Сайт/...`. |
| final-report | semi_automated | `launch final-report --execute` пишет `06_Отчёты/ФИНАЛЬНЫЙ_ОТЧЁТ.json + cleanup_manifest.json`. В `run-site-flow` НЕ вызывается. |

### 1.3 Ответы на конкретные вопросы

- **«Есть ли один bat-пункт, который проводит полный site flow?»** — Почти. `[2] → [A]` = `run-site-flow` доводит до `autopublisher` включительно, но НЕ:
  - не вызывает `site-publish collect-assets` (значит при kokoro-drive Drive mp3 могут не доехать в `output/site/<story>/`).
  - не отправляет Telegram.
  - не вызывает `final-report`.

- **Какие этапы ручные:**
  - запуск Colab TTS notebook (одна ссылка, но руками).
  - подкладывание обложек в `Обложки_ЗАГРУЗИТЕ_СЮДА`.
  - `site-publish collect-assets --execute` (если использовать Drive mp3 как источник).
  - mark-skipped для рассказов где TTS зашёл в тупик.
  - Telegram (целиком).

- **Где Gemini / браузерная автоматизация:** `legacy/Gemini_Auto`, `legacy/youtube_selection`, `legacy/youtube_tts/gemini_auto.py`, `legacy/director_2_0/main.py`, `legacy/youtube_safe_text`. Все через Playwright + Chrome `user_data_0..4`.

- **Resume:**
  - `run-site-flow` resume-safe для phase-a/phase-b/site_run через `site_flow_bat_state.json` + проверка `_phase_a/phase_a_summary.json + deferred.json + .published_ok`.
  - Per-story resume через `build_story_status_payload` (по файлам в `Запуски/<name>/05_Рассказы/<id>/03_Сайт/...`).
  - **Опасные сценарии:**
    - `phase-a` может дублировать рассказ при resume, если staging recovery_queue_map отсутствует и юзер передёргивает `stories-dir` (см. `_materialize_recovery_selection_staging`).
    - `site-publish prepare` без force перетирает старую папку `To_Publish/<story>/` при `dst.exists()` → `shutil.rmtree` (`prepare.py:262`). Это **destructive**, но dst — это staging, не финал.
    - `collect-assets` retries copy через subprocess, имеет `COPY_ABORT_STREAK=10` (после 10 подряд copy_failed — abort). Это защита от Drive throttling.

- **Dry-run / execute:**
  - Все шаги поддерживают dry-run через отсутствие `--execute`. Это базовая safety rule из `.cursor/rules/content-factory-safety.mdc`.
  - `run-site-flow` без `--execute` печатает `phase_a_cmd / phase_b_cmd / site_run_cmd` и не запускает.

- **Финальные vs временные артефакты:**
  - Финальные сейчас:
    - `output/site/<story>/{<story>.mp3, info.txt, *.jpg, *_M/F/U.txt}` (для legacy autopublisher).
    - `legacy/autopublisher/To_Publish/<story>/...` (staging для R2/Supabase).
    - На сайте: запись в Supabase + аудио в R2 (вне репозитория).
  - Полу-финальные в `Запуски/<name>/05_Рассказы/<id>/03_Сайт/...` — зеркала.
  - Всё остальное (logs, raw responses, validation, staging txt) — временное в `10_Временные_файлы/legacy/...`.

- **Что нужно для публикации:** `info.txt + <story>.mp3 + <story>.jpg + <story>__[MFU].txt` в `output/site/<story>/`.
  - `prepare.py:_check_story` проверяет наличие.
  - `publish.py + env_doctor` проверяет `.env.site_publish` (SUPABASE_URL, SUPABASE_SECRET_KEY/SERVICE_ROLE_KEY, R2_*).
  - `autopublisher` wrapper дополнительно проверяет, что mp3 не равен stub `orchestrator/site_tts/_silent_stub.mp3`.

- **Что будет если часть MP3 отсутствует:**
  - Если рассказ есть в `output/site/<story>/` без mp3 — `prepare` ставит `skipped_missing_audio`. Если есть terminal маркер (`manual_skipped.json` или `COLAB_DONE.txt+file_status=failed`) — `skipped_tts_<status>`. Публикация продолжается для остальных.
  - С `--allow-partial-tts` это считается ожидаемым.

- **Что будет если часть картинок:** `skipped_missing_image`. Без них рассказ не публикуется.

- **Что будет если часть metadata/info отсутствует:** `skipped_missing_info`. Не публикуется.

### 1.4 Site pipeline — таблица stages

См. `docs/PIPELINE_AUTOMATION_MATRIX.csv` секция `site`.

---

## 2. YouTube pipeline automation audit

### 2.1 Главный entry point

**Нет одной кнопки.** В bat есть два пути:

| Bat пункт | Что делает | Состояние |
|---|---|---|
| `[6] YouTube full pipeline` | Запускает `phase-a → phase-b → orchestrator run --pipeline youtube` через **global** `runs/youtube/`. Wrappers: `bulk_text_cleaner, gemini_auto, youtube_selection, youtube_safe_text, director20, youtube_tts, autovideo`. | **legacy / depricated**: рассыпан, не использует assigned queues, не использует `output/youtube/<story>/00_source...09_publish`, по факту с момента visuals-run перестал быть рабочим путём. |
| `[7] YouTube stages` | Стаб с надписью «not wired». | reserved / not implemented |
| `[Y] YouTube Visuals` | Per-story state machine для одного рассказа. Кнопки `[1..9]` — отдельные команды (visuals-run, frames-runpod, prepare-segments, export-job, drive-status, import-results, assemble-final, full-drive-flow, visuals-clean) под `--story-id`. | **рабочее**, но: только один рассказ за раз, нет batch-режима, нет общего «открой и нажми». |

### 2.2 Источник рассказов

Текущая реализация YouTube:

- `youtube prefilter-from-site --site-run-id <id>` читает `runs/site/<id>-a/_phase_a/ready_queues/deferred.json` и `output/site/<canonical>/cleaned_story.txt` → строит `runs/youtube/<youtube-run-id>/_selection/...`.
- `youtube selection-from-site` / `youtube selection-batch-from-site` запускают Gemini #1 для отбора подходящих рассказов.
- `youtube prepare-safe-input` / `youtube continue-after-selection` готовят safe-версии (Gemini #2).

Источник = **site-approved/cleaned**: YouTube _не_ запускает свой intake. Берёт уже отобранные и очищенные сайтом рассказы. Это правильно архитектурно.

### 2.3 Что автоматизировано

| Stage | Status |
|---|---|
| YouTube prefilter (длина) | automated |
| Gemini #1 selection (YES/NO) | semi_automated (через batch-runner; запускает Playwright Gemini, multi-account). |
| Gemini #2 safe rewrite | semi_automated (`safe-english-run` запускает Playwright Gemini). |
| Promo intro/mid/outro insertion | semi_automated (`promo-run` Gemini). |
| Gemini characters / director prompts | semi_automated (`visuals-run` запускает legacy director_2_0). |
| Frames RunPod | semi_automated (нужна RunPod URL; запускается через `frames-runpod --execute`). |
| YouTube TTS Kokoro Colab | semi_automated (`tts-kokoro-colab export/verify/import`; реально запускает Colab notebook — руки). |
| Video prepare-segments | automated |
| Video export-job to Drive | automated |
| **Colab worker render** | manual (5 Colab notebooks нужно открыть и нажать Run; bootstrap script делает root resolver и assigned queues) |
| dispatch-segments / reclaim-stale-segments / queue-status / inspect-segment | automated (по команде) |
| Video import-results | automated |
| Video assemble-final | automated |
| **YouTube upload to YouTube** | **missing** (нет команды; AutoVideo legacy stub, youtube_publish mode в runtime_modes стоит manual). |
| Telegram | missing |

### 2.4 Где живут артефакты

- `runs/youtube/<youtube-run-id>/_selection/...` — selection
- `runs/youtube/<youtube-run-id>/_gemini_selection/...` — Gemini #1 in/out
- `runs/youtube/<youtube-run-id>/_gemini_safe/...` — Gemini #2 in/out
- `output/youtube/<story>/00_source/01_selection/02_safe_story/03_promo/04_audio/05_characters/06_director/06_prompts/07_frames/08_video/logs/` — финальные артефакты одного рассказа
- `output/youtube/<story>/youtube_story_manifest.json` — story manifest
- `G:\Мой диск\ContentFactory_YouTube\video_jobs\<story_slug>\queue\{global_pending,assigned/<worker>/{pending,processing,done,failed}}` — Drive video render queue
- `G:\Мой диск\ContentFactory_YouTube\<youtube_run_id>/texts/, mp3/` — Kokoro Colab TTS Drive
- Финальное видео: `output/youtube/<story>/08_video/final_video.mp4`

**Ничего из YouTube не попадает в `Запуски/<name>/03_YouTube/...` (там пустые папки-заглушки).**

### 2.5 Что объективно останется ручным

- старт Colab Kokoro TTS workers (нужен ручной запуск notebooks с аккаунтов, тк нужны разные Google-account для разных Colab лимитов GPU).
- старт 5 Colab video workers (то же самое).
- ввод RunPod URL для frames-runpod (потому что RunPod pod иногда выключается, URL меняется).
- подкладывание обложек / финальное превью YouTube видео (если нужно).
- Подача `--story-id` сейчас руками — может быть закрыта batch-командой.

### 2.6 Что можно закрыть bat-меню / orchestrator-ом

- batch wrapper `youtube run-flow --youtube-run-id ... --execute`: prefilter → selection-batch → wait Gemini → continue → safe-english-run → promo-run → visuals-run → tts-export → wait → tts-import → video prepare-segments → export-job → wait workers → import-results → assemble-final.
- watcher loop, который раз в N минут пуллит `queue-status` и `reclaim-stale-segments`.
- статус-команда `youtube launch-status --launch-name <X>`, которая зеркалит результаты в `Запуски/<name>/03_YouTube/...`.

### 2.7 YouTube pipeline — таблица stages

См. `docs/PIPELINE_AUTOMATION_MATRIX.csv` секция `youtube`.

---

## 3. Telegram publishing gap

### 3.1 Что есть сейчас

- `orchestrator/human_launch_layout.py`:
  - `D04_TELEGRAM = "04_Telegram"` — константа, **не используется** в `top_level_dirs()` → папка `Запуски/<name>/04_Telegram/` НЕ создаётся.
  - `S04_08_TELEGRAM = "08_Telegram"` под `05_Рассказы/<id>/04_YouTube/08_Telegram/` — это per-story scaffold.
- `orchestrator/human_launch_legacy_sync.py::ensure_telegram_story_scaffold` создаёт пустые подпапки `01_Текст / 02_Информация / 03_Озвучка / 04_Визуал / 05_Пост / 06_Публикация`.
- `write_telegram_snapshot_metadata` пишет `metadata.json` с относительными путями к cleaned story и audio dir.
- `aggregate_launch_status` имеет поля `published_telegram=0`, `telegram = {snapshot:pending, post:pending, publish:pending}` — всё hardcoded `pending`.
- НЕТ:
  - bot token / channel id в `.env*` или `configs/`.
  - Telegram CLI команды.
  - Telegram wrapper в `orchestrator/wrappers/`.
  - Telegram pipeline stage в `Runner`.
  - идемпотентного маркера отправки.

### 3.2 Нужная логика (по запросу пользователя)

- После `autopublisher` (или параллельно ему) — отправлять каждый опубликованный рассказ в Telegram.
- В Telegram уходит:
  - **сырой / неочищенный** текст рассказа: брать `Запуски/<name>/05_Рассказы/<id>/01_Общее/source.txt` (это та самая исходная .txt) ИЛИ `output/site/<story>/<story>__[MFU].txt` (post-cleaner) — нужно решить, что считать «raw».
  - **текст поста / описание**: либо отрендерить шаблон из `info.en.txt`, либо отдельное поле `telegram_post.txt` от Gemini.
  - **картинка**: `output/site/<story>/<story>.jpg`.
  - **ссылка на сайт**: после publish autopublisher знает Supabase slug; нужно прокинуть URL в результат (сейчас в `site_publish_results.jsonl` хранится stdout, надо парсить).
  - title / genre / tags из `site_info.json`.
- Для YouTube используется safe-версия (она уже в `output/youtube/<story>/02_safe_story/safe_story.txt`). **Не путать с raw для Telegram.**

### 3.3 Спека Telegram stage

См. `docs/TELEGRAM_STAGE_SPEC.md`.

Кратко:

- `telegram_prepare` — single-story dry-run: проверить наличие raw text + post + image + published URL, ничего не отправлять.
- `telegram_send` — отправить (по `bot_token` + `channel_id`), записать idempotency marker.
- `telegram_report` — агрегировать `Запуски/<name>/04_Telegram/` (включить эту папку в `top_level_dirs()`).
- `telegram_sent.json` — per-story marker `{message_id, sent_at, channel_id, published_url}`; resume не отправляет повторно.

### 3.4 Где должен стоять stage

После `autopublisher` finalize. Если site publish не дошёл — можно отправлять Telegram отдельно (decoupled), но тогда без url. Рекомендую: **post-publish hook внутри `autopublisher` wrapper или отдельным wrapper-этапом в `SITE_STAGES`**.

### 3.5 Готовность

**0%.** Только пустой scaffold.

---

## 4. Artifact / output structure audit

### 4.1 Что куда пишется сейчас (фактическое состояние)

| Путь | Источник | Назначение | Должен быть source of truth? |
|---|---|---|---|
| `stories/input/*.txt` | пользователь / `sample-library` | raw intake | да, как inbox; но конкретный запуск должен иметь свой snapshot |
| `runs/site/<id>-a/_phase_a/...` | legacy `phase_a` (без launch-dir) | legacy artifacts | нет; должны быть только в `Запуски/<name>/10_Временные_файлы/legacy/runs/site/...` |
| `runs/site/<id>-a/_phase_b/...` | `phase_b` | legacy artifacts | то же |
| `runs/youtube/<id>/...` | youtube prefilter/selection/safe | YouTube run state | то же, должен переехать в `Запуски/<name>/10_Временные_файлы/legacy/runs/youtube/...` или в `Запуски/<name>/03_YouTube/_pipeline_state/` |
| `output/site/<story>/{info.txt, *.mp3, *.jpg, *__[MFU].txt}` | `site-publish collect-assets` + `site_tts` import | site story package для autopublisher | **проблема**: лежит вне launch folder. Сейчас 836 рассказов из разных запусков смешаны. |
| `output/youtube/<story>/00..09_*` | youtube_visuals_run / video_drive | YouTube story tree | **проблема**: вне launch folder, не привязан к запуску |
| `output/youtube/<story>/_visuals_clean_quarantine_*` | visuals-clean | quarantine | временное |
| `legacy/autopublisher/To_Publish/<story>/` | `site-publish prepare` | staging для R2/Supabase | временное; должен быть в `Запуски/<name>/10_Временные_файлы/legacy/autopublisher_To_Publish/` или `Запуски/<name>/02_Сайт/05_Публикация_на_сайт/_To_Publish/<story>/` |
| `.orchestrator/events.jsonl, status.jsonl, reports/, logs/` | EventLogger / Runner / wrappers | global service log | global, но содержит данные всех запусков — должно зеркалироваться в `Запуски/<name>/07_Логи/` |
| `.orchestrator/site_publish_*.json` | `site_publish/prepare.py + collect_assets.py + publish.py` | reports | то же |
| `reports/site_publish_*.json` | `site_publish/prepare.py` | TTS availability + skip report | то же |
| `Запуски/<name>/01..10/...` | `human_launch_*` + `mirror_*` | human-readable mirror | да, должен стать source of truth |
| `Запуски/<name>/10_Временные_файлы/legacy/runs/site/...` | phase-a/b с `--launch-dir` | isolated legacy | да, технический legacy |
| `Запуски/<name>/10_Временные_файлы/legacy/output/site/<story>/` | site Runner с `--launch-dir` | isolated site outputs | да; **но** `site-publish prepare` сейчас читает `output/site/` в корне, не из launch |
| `G:\Мой диск\ContentFactory_TTS\{texts, mp3, job, scripts, cache, logs}` | site_tts kokoro-drive | Drive TTS handoff | external; нельзя положить в launch, но launch должен ссылаться |
| `G:\Мой диск\ContentFactory_YouTube\video_jobs\<story>\queue\assigned\<worker>\` | youtube_video_drive | Drive video render handoff | external |
| `G:\Мой диск\ContentFactory_YouTube\<run>/texts, mp3` | youtube_tts_kokoro_bridge | Drive YT TTS handoff | external |
| `Запуски/<name>/08_Карантин/_visuals_clean_quarantine_*` | visuals-clean | quarantine | временное |
| `archive/stories_input/<timestamp>` | `archive-input` | архив исходников | временное |
| `models/fish_audio/...` | ML weights | NOT artifact | global, не трогать |
| `legacy/...` | весь legacy code | code, не artifact | global, не трогать |

### 4.2 Главные проблемы

1. **`output/site/` и `output/youtube/` в корне репо** — не привязаны к launch, нельзя «удалил папку запуска — всё локально пропало».
2. **`runs/site/` и `runs/youtube/` в корне репо** — то же самое; есть параллельный `Запуски/<name>/10_Временные_файлы/legacy/runs/...`, но не все этапы пишут именно туда.
3. **`legacy/autopublisher/To_Publish/`** — общая папка staging, не isolated.
4. **`.orchestrator/`** — global service dir со status / events / reports / logs от всех запусков. Не зеркалируется в `Запуски/<name>/`.
5. **`Запуски/<name>/03_YouTube/` пустой scaffold** — никаких YouTube артефактов не сохраняется в launch folder.
6. **`Запуски/<name>/04_Telegram/`** — даже не создаётся.
7. **836 stories в `output/site/`** vs `Запуски/<name>/05_Рассказы/` ~ показывает, что куча данных из старых запусков всё ещё в корне.

### 4.3 Целевая структура

См. `docs/ARTIFACT_STRUCTURE_TARGET.md`.

### 4.4 Готовность

**~45%.** Каркас launch folder есть, mirror работает, но финальные артефакты (site/youtube) живут вне launch.

---

## 5. Bat menu audit

См. `docs/BAT_MENU_TARGET.md`.

### 5.1 Что есть сейчас

`Content-Factory-Запуск.bat` — ~1400 строк, главное меню:

| Item | Назначение | Статус |
|---|---|---|
| [1] Site partial run (LEGACY) | `RUN_SITE_PIPELINE` → global `runs/site` + `output/site` | dangerous; помечен как legacy |
| [2] Site full to Zapuski | `run-site-flow` (recommended path) | **production-ready** |
| [3] Site resume | `run-site-flow --execute` для существующего launch | production-ready |
| [4] Site technical stages | filter-length, phase-b, site-info-visual | dev tools |
| [5] Site TTS Kokoro Colab/Drive | export/import/verify/wait/setup-drive | production-ready, но требует ручной старт Colab |
| [6] YouTube full pipeline | `RUN_YOUTUBE_PIPELINE` → global `runs/youtube` | **deprecated**, не использует visuals-run / assigned queues |
| [7] YouTube stages | stub «not wired» | dead |
| [8] Checks/reports/logs | preflight, open `.orchestrator/reports`, show-modes | dev tools |
| [9] Service/runtime/cleanup | set-mode, cleanup-scan, cleanup-move, cleanup-run, phase-b scaffold | dev tools |
| [Y] YouTube Visuals | per-story: visuals-run, frames-runpod, prepare-segments, export-job, drive-status, import-results, assemble-final, full-drive-flow, visuals-clean | **production per-story**; batch-режим отсутствует |
| [Q] Sample library | sample-library MOVE из библиотеки в stories/input | production-ready |
| [0] Exit | | |

### 5.2 Проблемы

- Опасные пункты ([1], [6]) и production ([2]) лежат рядом → пользователь может нажать не туда.
- [6] и [7] — оба «YouTube», один deprecated, второй stub.
- Нет одной кнопки для **полного** site flow (включая Telegram + collect-assets + final-report).
- Нет одной кнопки для **полного** youtube flow (нет batch-режима).
- Smoke / test команды (smoke-site-cycle, init-bridge-fixture, phase-b --allow-scaffold) перемешаны с production.
- Нет явного разделения Production / Smoke / Maintenance.

### 5.3 Готовность production-меню

**~50%.** Site one-button есть (но без collect-assets/Telegram/final-report); YouTube one-button нет.

---

## 6. Full automation readiness score

| Pipeline | Score | Почему не 100% |
|---|---|---|
| Site pipeline | **65%** | run-site-flow доводит до publish, но: (1) collect-assets не вшит, (2) Telegram отсутствует, (3) final-report не запускается автоматом, (4) Gemini может упасть и нужен ручной retry через site-info-visual retry. |
| YouTube pipeline | **35%** | Per-story работает, но: (1) нет batch-команды над всеми selected, (2) 5 Colab workers стартуют руками (объективно), (3) YouTube upload отсутствует, (4) нет live статуса в launch folder, (5) `[6]` deprecated. |
| Telegram stage | **0%** | Stage не существует. |
| Artifact structure | **45%** | Launch folder есть, mirror работает, но финальные артефакты сайта/YouTube не isolated в launch. |
| Bat menu production readiness | **50%** | Нет разделения Production/Smoke. Опасный legacy рядом с production. Нет one-button YouTube. |

---

## 7. Failure points / risk register

См. также `docs/AUTOMATION_READINESS_BACKLOG.md` приоритеты.

| Risk | Pipeline | Severity | Где в коде | Симптом | Текущая защита | Что нужно |
|---|---|---|---|---|---|---|
| Gemini Playwright crash (Chrome user_data) | site / youtube | high | `legacy/Gemini_Auto/gemini_auto.py`, `phase_a_gemini_supervisor.py` | phase-a останавливается, остаются stale `.cf_worker.lock` | `--repair-stale-locks --repair-locks-execute --older-than-minutes 60`; `gemini-progress`; supervised pool с cooldown | автоматический retry на «зависших» рассказах |
| Duplicate stories on resume | site | medium | `_materialize_recovery_selection_staging` | при отсутствии `recovery_queue_map.json` повторно отправляется весь stories/input | `recovery_queue_resume_counters` + gate | гарантия `recovery_queue_map.json` для каждого launch |
| Missing TTS files | site | medium | `site_tts/colab_batch.py` + `site_publish/prepare.py` | часть mp3 не приехала с Colab | `mark-skipped`, `mark-missing-skipped`, `--allow-partial-tts`, terminal status `manual_skipped/terminal_failed` | автоматическая mark-missing-skipped после max_wait_hours |
| Missing covers | site | medium | `prepare.py:_find_image` | story `skipped_missing_image` | bat сообщает где класть | автоматическое уведомление пользователю + параллельный stage cover-prepare-Gemini |
| Missing info.txt | site | medium | `prepare.py:_find_info` | story `skipped_missing_info` | Gemini site_info_builder + render_info_en_txt | автоматический retry через site-info-visual retry |
| Partial publish | site | low | `prepare.py` | другие рассказы продолжают публиковаться | implemented in prepare/publish | OK |
| Telegram resend | telegram | high (future) | n/a | повторная отправка при resume | n/a | `telegram_sent.json` marker (см. spec) |
| Colab worker no access to Drive | youtube video | high | `colab/youtube_video_bootstrap_colab.py` | `ROOT exists: False` | `CONTENT_FACTORY_YOUTUBE_FOLDER_ID` + shortcut create | OK, но требует ручного ввода folder_id |
| Stale processing video segments | youtube video | medium | `youtube_video_drive.run_youtube_video_reclaim_stale_segments` | сегмент висит processing >180min | `reclaim-stale-segments --stale-minutes 180 --execute` | watcher loop, который запускает reclaim каждые 30 мин |
| Failed video segments | youtube video | medium | `assigned/<worker>/failed/` | стопит assemble-final | bat показывает в `queue-status` | автоматический requeue в global_pending |
| Scattered outputs (output/site, output/youtube вне launch) | site / youtube | high | `output/site`, `output/youtube` | удаление launch не чистит финальные артефакты | none | переезд `output/...` под `Запуски/<name>/05_Рассказы/<id>/...` или `Запуски/<name>/02_Сайт/05_Публикация_на_сайт/<story>/` |
| Bat ambiguity (legacy vs production) | bat | high | `[1] vs [2] vs [6]` | пользователь нажмёт wrong button | warnings в bat | разделить меню Production / Smoke / Maintenance, скрыть legacy за дополнительной кнопкой |
| Old worker scripts on Drive | youtube video | medium | `setup-colab-workers` | старая версия script вызывает старый формат queue | hash compare в setup-colab-workers report | OK, но каждый раз перед run надо setup |
| Drive cache delay | site / youtube | medium | Google Drive sync | mp3 не появляется хотя Colab завершил | wait loop `--max-wait-hours 1000` | OK, нужна доп. диагностика «Drive не виден» |
| Manual movement of failed/pending json | youtube video | medium | пользователь раньше двигал руками | duplicate processing / lost segments | inspect-segment + reclaim | OK |
| Output folder ↔ launch folder divergence | both | high | разные источники истины | `prepare scanned=0` несмотря на готовый launch | сейчас лечится через `site-publish collect-assets` | вшить collect-assets в run-site-flow |
| Gemini bot URLs / accounts drift | both | medium | `configs/gemini_bots_registry.yaml` | wrong URL → отбор работает не туда | resolver + preflight syncs | держать registry единственным source of truth (уже сделано) |
| Telegram bot token leak в логе | telegram | high (future) | n/a | secrets в `.orchestrator/events.jsonl` | safety rule «не логировать секреты» | при подключении Telegram — env-doctor + redact |
| RunPod URL expired | youtube | medium | `frames-runpod` | RunPod pod выключен | bat спрашивает URL руками | при batch — попросить URL один раз и переиспользовать |

См. полный risk register в `docs/AUTOMATION_READINESS_BACKLOG.md`.

---

## 8. Required implementation backlog

См. `docs/AUTOMATION_READINESS_BACKLOG.md`.

---

## 9. Reports

| Файл | Содержит |
|---|---|
| `docs/AUTOMATION_READINESS_AUDIT.md` | этот документ |
| `docs/AUTOMATION_READINESS_BACKLOG.md` | P0/P1/P2 задачи |
| `docs/ARTIFACT_STRUCTURE_TARGET.md` | текущие vs целевые пути, новая структура launch |
| `docs/BAT_MENU_TARGET.md` | предложение нового меню |
| `docs/TELEGRAM_STAGE_SPEC.md` | спека Telegram stage |
| `docs/PIPELINE_AUTOMATION_MATRIX.csv` | CSV таблица всех stages |

---

## 10. Не сделано

- Не запускались `queue-status` / `verify-runtime` / `resume-plan` команды (даже read-only) — нет блокирующих вопросов, всё видно из кода и filesystem.
- Не открывались `manifest.json` отдельного запуска `SITE_FULL_20260513_1309` (read-only можно при необходимости в follow-up задаче).
