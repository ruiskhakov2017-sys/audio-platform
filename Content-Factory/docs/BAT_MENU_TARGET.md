# Content-Factory-Запуск.bat — Target Menu

Связан с `docs/AUTOMATION_READINESS_AUDIT.md` и `docs/AUTOMATION_READINESS_BACKLOG.md`.

Главные изменения:
- Разделение **Production**, **Smoke / Diagnostics**, **Maintenance**.
- Production содержит one-button команды для Site и YouTube.
- Опасный legacy ([1] partial run, [6] global youtube pipeline) — только в Maintenance с двойным подтверждением.

---

## 1. Главное меню

```
============================================================
  Content-Factory — Main menu
============================================================

  [1] Site pipeline
  [2] YouTube pipeline
  [3] TTS / Colab tools
  [4] YouTube video / Colab tools
  [5] Reports / status
  [6] Maintenance / cleanup
  [7] Smoke / Diagnostics / Dry-run

  [Q] Sample library → stories/input
  [0] Exit
```

---

## 2. [1] Site pipeline

```
============================================================
  Site pipeline (Production)
============================================================
  Current launch: <auto-selected RECOVERY/normal launch or NEW>

  [1] Full Site Run  (source → selected → cleaned → site_info → cover → TTS → publish → Telegram)
  [2] Resume Site Run (existing launch)
  [3] Site status (read-only)
  [4] Site publish only (collect-assets → prepare → publish, без Gemini/TTS)
  [5] Site Telegram only (post-publish, без re-send)
  [6] Site final report + cleanup_manifest
  [7] Open launch folder
  [8] Open launch logs
  [9] Monitor sync-progress (loop)

  [0] Back
```

### [1] Full Site Run

Команда (target):
```
python -m orchestrator launch run-site-flow ^
  --name SITE_FULL_<auto_ts> ^
  --stories-dir stories\input ^
  --bat-profile kokoro-drive ^
  --limit 0 ^
  --include-collect-assets ^
  --include-final-report ^
  --include-telegram ^
  --execute
```

Под капотом:
1. preflight (env-doctor, Chrome user_data, registry sync)
2. run-site-flow (phase-a → phase-b → run --pipeline site)
3. site-publish collect-assets (NEW)
4. site-publish prepare + publish (NEW — вместо wrapper)
5. telegram send (NEW)
6. launch final-report --execute (NEW)
7. вывести summary: total / published_site / published_telegram / failed / skipped

### [2] Resume Site Run

```
python -m orchestrator launch pick-site-launch --out tmp.cmd
call tmp.cmd
python -m orchestrator launch run-site-flow --name %LAUNCH_NAME% --execute (тот же набор флагов)
```

### [3] Site status

```
python -m orchestrator launch verify-runtime --name %LAUNCH_NAME%
python -m orchestrator launch resume-plan --name %LAUNCH_NAME%
```

### [4] Site publish only

```
python -m orchestrator site-publish collect-assets --launch-name %LAUNCH_NAME% --allow-partial-tts --execute
python -m orchestrator site-publish prepare --launch-name %LAUNCH_NAME% --allow-partial-tts --execute
python -m orchestrator site-publish publish --launch-name %LAUNCH_NAME% --execute
```

### [5] Site Telegram only

```
python -m orchestrator telegram prepare --launch-name %LAUNCH_NAME%
python -m orchestrator telegram send --launch-name %LAUNCH_NAME% --execute
```

---

## 3. [2] YouTube pipeline

```
============================================================
  YouTube pipeline (Production)
============================================================
  Current launch: <auto-selected>

  [1] Build YouTube candidates from Site-approved launch
  [2] Full YouTube Run (selection → safe → promo → visuals → frames → tts → video → assemble)
  [3] Resume YouTube Run
  [4] YouTube status (read-only)
  [5] Queue status (Drive)
  [6] Reclaim stale video segments
  [7] Setup Colab workers (workers + bootstrap script to Drive)
  [8] Watcher loop (queue-status + reclaim)
  [9] Open launch YouTube folder
  [B] Per-story tools (legacy submenu — для отдельных рассказов)

  [0] Back
```

### [1] Build YouTube candidates

```
python -m orchestrator youtube selection-from-site ^
  --site-run-id <derived from launch> ^
  --youtube-run-id YT_<launch_ts> ^
  --execute
```

### [2] Full YouTube Run

```
python -m orchestrator youtube run-batch ^
  --launch-name %LAUNCH_NAME% ^
  --youtube-run-id YT_<launch_ts> ^
  --site-run-id <derived> ^
  --execute
```

Под капотом (NEW; P0-6):
1. Если selection не сделан — selection-batch-from-site --execute (ждёт Gemini).
2. Для каждого YES-story:
   - safe-english-run --execute
   - promo-run --execute
   - visuals-run --auto-gemini --execute (характеры, директор, кадры)
   - frames-runpod --execute (один раз спросить RunPod URL)
   - tts-kokoro-colab export --execute → ждать → import --execute (нужен ручной старт Colab)
   - video prepare-segments --execute
   - video export-job --execute
3. После того как все рассказы экспортированы → setup-colab-workers --execute (раздать новый worker script на Drive).
4. Запросить: «нажмите Run на 5 Colab notebooks». Запустить watcher loop.
5. Когда все сегменты в done → для каждого video import-results + assemble-final.

### [7] Setup Colab workers

```
python -m orchestrator youtube video setup-colab-workers --story-id <X> --youtube-folder-id <ID> --execute
```

### [8] Watcher loop

```
python -m orchestrator youtube video watcher --launch-name %LAUNCH_NAME% --interval-min 5 --reclaim-stale-min 30
```

---

## 4. [3] TTS / Colab tools

```
============================================================
  TTS / Colab tools
============================================================

  [1] Setup Google Drive workspace (one-time per launch)
  [2] Export queue → Drive
  [3] Wait for mp3 + auto-import
  [4] Resume wait (drive-only)
  [5] Mark missing as skipped (manual)
  [6] Verify Drive (texts vs mp3 coverage)
  [7] Queue status (Drive)
  [8] Open Drive folder

  [0] Back
```

Команды как сейчас (`site-tts kokoro-colab setup-drive / export / wait-drive / mark-missing-skipped / verify-drive / queue-status`).

---

## 5. [4] YouTube video / Colab tools

```
============================================================
  YouTube video / Colab tools
============================================================

  [1] Prepare segments (one story)
  [2] Export video job to Drive
  [3] Setup Colab workers (workers + bootstrap)
  [4] Dispatch segments (per worker)
  [5] Reclaim stale segments
  [6] Queue status
  [7] Inspect segment
  [8] Import rendered segments
  [9] Assemble final video
  [F] Full Drive flow (one story)

  [0] Back
```

Команды как сейчас (`youtube video prepare-segments / export-job / setup-colab-workers / dispatch-segments / reclaim-stale-segments / queue-status / inspect-segment / import-results / assemble-final / full-drive-flow`).

---

## 6. [5] Reports / status

```
============================================================
  Reports / status
============================================================

  [1] Preflight (non-destructive)
  [2] Open .orchestrator/reports
  [3] Open .orchestrator/logs (status.jsonl + events.jsonl)
  [4] Show runtime modes
  [5] Launch verify-runtime
  [6] Launch resume-plan
  [7] Launch final-report (dry-run, без записи файлов)

  [0] Back
```

---

## 7. [6] Maintenance / cleanup

```
============================================================
  Maintenance / cleanup
============================================================
   WARNING: Эта секция может удалять / переносить артефакты.

  [1] Cleanup scan (dry-run)
  [2] Cleanup move paths
  [3] Cleanup run by run_id
  [4] Quarantine old SMOKE/TEST артефакты
  [5] Archive launch (move to Запуски/_Архив/)
  [6] Delete launch (с подтверждением)
  [7] Visuals clean for story
  [8] Frames reset for story
  [9] Toggle Phase A limit (TEST/FULL)
  [L] LEGACY: site partial run (global runs/output) — DANGEROUS
  [M] LEGACY: youtube full pipeline (global runs/output) — DEPRECATED

  [0] Back
```

`[L]` и `[M]` требуют двойного подтверждения и большую warning.

---

## 8. [7] Smoke / Diagnostics / Dry-run

```
============================================================
  Smoke / Diagnostics / Dry-run
============================================================

  [1] Smoke site cycle (staging + phase-a only)
  [2] Plan only (run-site-flow без --execute)
  [3] Init bridge fixture (youtube smoke)
  [4] Phase-b scaffold
  [5] Gemini preflight
  [6] Path audit
  [7] Visual prompts audit
  [8] Characters anchor audit
  [9] Visuals status (story)

  [0] Back
```

---

## 9. Q. Sample library

```
============================================================
  Sample library → stories\input
============================================================
  Tool: orchestrator sample-library (MOVE; collision-safe by basename).

  [1] Dry-run sampling (50 per folder)
  [2] Move 50 per folder
  [3] Custom N per folder
  [4] Open stories\input

  [0] Back
```

---

## 10. Production-safe правила в новом меню

- Production команды:
  - всегда печатают `[DIAG] launch_name=... stories_dir=... mode=execute`.
  - перед `--execute` показывают, что именно будет запущено, и спрашивают `[Y/N]`.
  - после завершения печатают `[SUMMARY] published_site=N published_telegram=N failed=N`.
- Maintenance / Legacy команды дополнительно требуют ввести `YES` (не Y/N), чтобы случайно не нажать.
- Smoke / Diagnostics команды никогда не пишут в `Запуски/_Архив` или в production launch без `--name` явно.
- `[6] → [L] / [M]` показывают warning с указанием `runs/site` и `output/site` (global paths) — пользователь видит, что попадает в legacy путь.

---

## 11. Что убрать из текущего меню

- `[1] Site partial run (LEGACY)` → переехало в Maintenance [L].
- `[6] YouTube full pipeline` → переехало в Maintenance [M] (deprecated path через global runs/youtube).
- `[7] YouTube stages (not wired)` → удалить, заменить на новый `[2] YouTube pipeline` подменю.
- Service / runtime / cleanup → разделено: Runtime → теперь в `[5] Reports`, cleanup → в `[6] Maintenance`.

---

## 12. Acceptance criteria для нового меню

- Production menu имеет ровно одну кнопку «Full Site Run» и одну «Full YouTube Run», каждая запускает соответствующий полный цикл.
- Legacy / deprecated пути недоступны из главного меню напрямую (нужно зайти в Maintenance).
- Smoke commands отделены от production.
- Каждый production пункт работает в **resume** режиме без специального флага: если артефакты уже есть — шаги скипаются.
- Тестовый сценарий:
  1. Запустить `[1] → [1] Full Site Run` с 2 рассказами.
  2. Прервать в середине phase-a (Ctrl+C).
  3. Запустить `[1] → [2] Resume Site Run`. Должно завершиться без потери прогресса.
  4. Открыть `[1] → [3] Site status` — увидеть пройденные этапы.
  5. Удалить launch через `[6] → [6] Delete launch --execute` (без `--drive-cleanup`). Все локальные артефакты этого запуска удалены, кроме Drive.
