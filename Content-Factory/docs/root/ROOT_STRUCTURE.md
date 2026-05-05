# ROOT_STRUCTURE

## Целевая структура корня

```text
Content-Factory/
  app/
    orchestrator/
  legacy/
    autopublisher/
    AutoVideo/
    bulk-text-cleaner/
    ElevenLabs/
    Gemini_Auto/
    content_combiner/
    youtube_selection/
    youtube_tts/
    director_2_0/
  configs/
  docs/
  stories/
    input/
  models/
    fish_audio/
      fish-s2-pro/
  runs/
    site/
    youtube/
  output/
    site_stories/
    youtube_videos/
  logs/
  archive/
    quarantine_old_runs/
    old_reports/
    temp/
    debug_fixes/
  tools/
  .env
  Content-Factory-Запуск.bat
  docs/
    project/
      PROJECT_REALITY_MAP.md
      MODULE_INVENTORY.md
      FULL_PIPELINE_MAP.md
      AUTOMATION_GAP_MAP.md
      RISKS_AND_CONSTRAINTS.md
      ROADMAP.md
      MVP_ORCHESTRATOR_PLAN.md
      EXECUTION_PLAN.md
    audits/
      STRUCTURE_AUDIT.md
    root/
      ROOT_STRUCTURE.md
```

## Правила размещения

- Входные рассказы: только `stories/input/`.
- Тяжёлые веса (Fish Audio S2 Pro и др.): только `models/...` (см. `configs/paths.yaml` → `models`). Не класть в `stories/input/`, `output/`, `runs/`.
- Legacy-модули: только `legacy/...`.
- Прогоны оркестратора: только `runs/site/<run_id>` или `runs/youtube/<run_id>`.
- Финальный output: только `output/site/` и `output/youtube/`.
- Логи: только `logs/` и run-local лог внутри `runs/<...>/run.log`.
- Архив/карантин старых артефактов: только `archive/...`.

## Что можно удалять

- Любой завершенный run в `runs/site/<run_id>` или `runs/youtube/<run_id>`.
- Архивные временные артефакты в `archive/temp`.
- Старые отчеты в `archive/old_reports`.
- Старые карантинные пакеты в `archive/quarantine_old_runs` (после ручной проверки).

## Что нельзя удалять

- `stories/input/`, `models/`, `output/`, `configs/`, `orchestrator/` (до полной миграции в `app/orchestrator`), `legacy/`.
- `.env`, `.git`, project source files.
- Launcher и управляющие скрипты.

## Совместимость

- Path resolution выполняется через `configs/paths.yaml`.
- Любые legacy пути должны браться из registry, а не быть захардкожены в коде.
- Для временной совместимости допустимы alias/переадресации на уровне launcher/preflight.
