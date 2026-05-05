# MVP ORCHESTRATOR PLAN

## Цель MVP

Сделать минимальный, безопасный управляющий слой над legacy-модулями, который:
- ничего не переписывает внутри рабочих веток;
- дает единую точку запуска;
- фиксирует статусы и логи;
- поддерживает dry-run;
- позволяет контролируемо вызывать отдельные этапы.

## Принципы MVP

1. Legacy-модули считаются black-box.
2. Оркестратор только валидирует входы, вызывает шаг, фиксирует результат.
3. Любой destructive шаг требует preflight и явного режима `real-run`.
4. Каждая операция маркируется `run_id` и `story_id`.

## Минимальная функциональность

## 1) Central Entry Point
- CLI-команда уровня `python -m orchestrator ...`
- Команды:
  - `plan` (показать, какие шаги будут вызваны),
  - `run` (выполнить),
  - `status` (показать состояния по run/story),
  - `resume` (возобновить прерванный запуск).

## 2) Unified Config
- Один конфиг (yaml/json) с:
  - путями к legacy-модулям,
  - корневыми data-папками,
  - feature flags,
  - окружениями (`dev/prod`),
  - режимом dry-run по умолчанию.

## 3) Status Model
- Состояния story-item:
  - `discovered`, `prechecked`, `running`, `succeeded`, `failed`, `skipped`.
- Состояния этапа:
  - `pending`, `in_progress`, `done`, `error`, `blocked`.

## 4) Logging/Event Journal
- JSONL event-log на каждый run.
- Поля: timestamp, run_id, story_id, stage, action, result, message.
- Секреты маскируются.

## 5) Safe Wrappers (Facade)
- Для каждого legacy-модуля отдельный wrapper:
  - проверка preconditions;
  - сбор аргументов;
  - запуск subprocess;
  - нормализация stdout/stderr в общий event-format;
  - post-check ожидаемых артефактов.

## 6) Dry-Run Mode
- Никаких destructive external writes.
- Выполняются только валидации, проверка доступности и построение execution-plan.

## 7) Controlled Step Execution
- Возможность запускать:
  - полный pipeline,
  - subset этапов,
  - одиночный этап для конкретного story-id.

## Что не входит в MVP

- Замена внутренней логики legacy-скриптов.
- Полный distributed scheduler.
- Глубокая UI-панель и продвинутый observability stack.
- Масштабирование на весь корпус (700k) на первом запуске.

## Минимальный безопасный первый шаг реализации

1. Создать каркас оркестратора (entry/config/status/logging) без подключения destructive шагов.  
2. Подключить 1-2 read-mostly шага в режиме dry-run.  
3. Проверить целостность run/status модели на малом батче.  
4. Только после этого подключать ветку site publish и YouTube video generation.

Почему это безопасно:
- не меняется legacy-код;
- не меняются промпты;
- нет необратимых операций на первом прогоне;
- можно быстро откатить orchestration-слой, не затронув прод-пайплайны.

## Предлагаемые новые файлы для первого безопасного этапа

- `orchestrator/__init__.py`
- `orchestrator/__main__.py`
- `orchestrator/cli.py`
- `orchestrator/config.py`
- `orchestrator/models.py`
- `orchestrator/status_store.py`
- `orchestrator/logger.py`
- `orchestrator/runner.py`
- `orchestrator/preflight.py`
- `orchestrator/contracts.py`
- `orchestrator/wrappers/base.py`
- `orchestrator/wrappers/bulk_text_cleaner.py`
- `orchestrator/wrappers/gemini_auto.py`
- `orchestrator/wrappers/elevenlabs.py`
- `orchestrator/wrappers/director20.py`
- `orchestrator/wrappers/autovideo.py`
- `orchestrator/wrappers/autopublisher.py`
- `configs/orchestrator.example.yaml`
- `docs/orchestrator-runbook.md`

Эти файлы безопасны, потому что они создают только управляющий слой и не модифицируют код существующих модулей.
