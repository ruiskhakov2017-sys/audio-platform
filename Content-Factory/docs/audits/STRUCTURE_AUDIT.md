# STRUCTURE_AUDIT

Дата аудита: 2026-04-29

## Классификация корня проекта

| Path | Category | Notes |
|---|---|---|
| `orchestrator` | core app | Текущий управляющий слой (до переноса в `app/orchestrator`). |
| `configs` | config | Конфиги оркестратора и runtime режимов. |
| `docs` | docs | Документация проекта. |
| `stories` | input data / mixed | Смешанная папка: и входные рассказы, и legacy-артефакты (`_results`). |
| `short_under_15m` | generated output | Результат length-filter (legacy). |
| `runs` | generated output | Новые run-scoped прогоны. |
| `.orchestrator` | generated output | Legacy service/reports/status/events. |
| `_quarantine_old_runs` | temporary/debug | Исторический карантин артефактов. |
| `zz_phase_fix` | temporary/debug | Техническая debug-папка. |
| `autopublisher` | legacy module | Модуль публикации. |
| `AutoVideo` | legacy module | Модуль сборки видео. |
| `bulk-text-cleaner` | legacy module | Модуль очистки текста. |
| `ElevenLabs` | legacy module | Модуль TTS. |
| `Gemini_Auto` | legacy module | Модуль Gemini runtime. |
| `content_combiner` | legacy module | Модуль сборки промежуточных артефактов. |
| `Отбор для YouTube` | legacy module | Будет перемещен в `legacy/youtube_selection`. |
| `Озвучка для YouTube` | legacy module | Будет перемещен в `legacy/youtube_tts`. |
| `Режиссер 2.0` | legacy module | Будет перемещен в `legacy/director_2_0`. |
| `Фаза` | unknown | Требует ручной проверки содержимого и назначения. |
| `.cursor` | config | Локальные IDE/rules настройки. |
| `.env` | config | Локальные секреты и переменные окружения. |
| `Content-Factory-Запуск.bat` | core app | Основной launcher. |
| `filter_length.bat` | tools | Вспомогательный bat-инструмент. |
| `*.md` (ROADMAP, MAP, RISKS, etc.) | docs | Проектная документация и карты текущего состояния. |

## Риски текущей структуры

- Смешение runtime-артефактов и исходников в корне.
- Несколько конкурирующих “рабочих” зон (`stories`, `.orchestrator`, `runs`).
- Отсутствие единого path-registry для legacy модулей.
- Высокий риск роста “мусора” в корне на массовом прогоне.
