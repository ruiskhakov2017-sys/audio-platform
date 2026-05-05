# Итог текущей работы и состояние проекта (Content-Factory)

Дата фиксации: 2026-05-01.

## 1. Структура проекта (целевой ориентир)

### Корень репозитория

Рабочий код и данные разведены: **исходники и конфиги** не смешиваются с **артефактами прогонов**.

- **`orchestrator/`** — Python-пакет оркестратора (CLI, `phase_a` / `phase_b`, preflight, cleanup-скан и т.д.). Располагается **в корне репозитория** (не в `app/orchestrator/`; в `docs/root/ROOT_STRUCTURE.md` в дереве иногда фигурирует исторический вариант с `app/` — фактическая раскладка на диске — корневой `orchestrator/`).
- **`legacy/`** — все внешние/наследуемые модули (Gemini_Auto, bulk-text-cleaner, ElevenLabs, autopublisher, AutoVideo, content_combiner, youtube_* и др.). Оркестратор вызывает их через subprocess и пути из реестра.
- **`configs/`** — конфигурация, в т.ч. **`configs/paths.yaml`** (path registry: куда смотреть legacy-модулям и базовым data-папкам).
- **`docs/`** — документация (в т.ч. `docs/root/ROOT_STRUCTURE.md`, аудиты, карты пайплайна).
- **`tools/`** — вспомогательные утилиты (если используются в репозитории).
- **`Content-Factory-Запуск.bat`** — лаунчер для Windows.
- **`.env`** — секреты/локальные настройки (в репозиторий не коммитить по правилам безопасности).

### Данные и артефакты

Зафиксировано в `configs/paths.yaml` (`data_dirs`):

| Назначение | Путь |
|------------|------|
| Входные рассказы | `stories/input/` |
| Прогоны (run-scoped) | `runs/` → `runs/site/<run_id>/`, `runs/youtube/<run_id>/` |
| Финальный output | `output/site/`, `output/youtube/` |
| Общие логи | `logs/` |
| Архив / карантин | `archive/` → в т.ч. `archive/quarantine_old_runs/`, `archive/temp/` |

### Тяжёлые модели (веса)

| Назначение | Путь |
|------------|------|
| Локальные веса (не коммитить, не cleanup) | `models/` — например **`models/fish_audio/fish-s2-pro/`** для Fish Audio S2 Pro |

Реестр относительных путей: **`configs/paths.yaml`** → секция `models` (ключ `fish_audio_s2_pro`).  
Эти каталоги **не** являются intake/output/run-артефактами; в `.gitignore` игнорируется вся `models/` и типичные расширения весов.

Дополнительно (legacy/оркестратор до полной консолидации):

- **`.orchestrator/`** — служебные отчёты и логи состояния (`reports/`, `status.jsonl`, `events.jsonl`). Это **generated**; для чистого теста переносится в `archive/quarantine_old_runs/<timestamp>/`, а не правится руками в коде.

---

## 2. Пользовательская логика (site)

1. Положить новые рассказы (`.txt`) в **`stories/input/`**.
2. Запустить из меню лаунчера пункт **«Наполнить сайт аудиорассказами»** (site pipeline).
3. Оркестратор создаёт прогон в **`runs/site/<run_id>/`** (манифесты, `REPORT.md`, per-story папки, логи прогона и т.д. — по текущей реализации `phase_a` / последующих стадий).
4. Итоговые артефакты сайта — в **`output/site/`** (после стадий, которые реально пишут в output; пустая папка до первого успешного прогона — норма).

YouTube-ветка симметрично: **`runs/youtube/<run_id>/`**, **`output/youtube/`**.

---

## 3. Что уже сделано (кратко)

- **Меню лаунчера**: упрощение ввода (`set /p`), режим лимита историй без «круга по меню», пути к комбинеру и отчётам привязаны к актуальной структуре.
- **Два главных процесса**: сайт и YouTube разведены по веткам `runs/site` и `runs/youtube` и соответствующему output.
- **Cleanup**: dry-run / scan сгруппированных артефактов, перенос в quarantine (`orchestrator/cleanup.py` + CLI).
- **Структура `runs` / `output` / `logs` / `archive`**: договорённость и path registry в `configs/paths.yaml`; прогоны — run-scoped под `runs/<branch>/<run_id>/`.
- **Run-scoped отчётность**: в прогоне ожидаются `REPORT.md`, индексы/манифесты (например `selection_index.json`, отчёты в `.orchestrator/reports/phase_a_*`) — конкретный набор зависит от версии запуска.
- **Разделение selection vs site info**: артефакты в `_pipeline/` (например `selection_result.json`, `site_info.json`), `info.txt` как legacy-экспорт из site info для выбранных историй — по задумке оркестратора.
- **Legacy в `legacy/`** — модули не переписывались «с нуля»; оркестратор — адаптер поверх них.

---

## 4. Что ещё требует проверки / доводки

Ниже — зона риска; перед продом имеет смысл прогнать контрольный run и сверить артефакты.

- **Строгий отбор** `selected` / `rejected` / `manual_review`: парсинг ответа Gemini и границы «явный вердикт vs ручной разбор».
- **Production vs scaffold**: флаги вроде `--allow-scaffold` для `phase-b` — убедиться, что в «боевом» сценарии scaffold не маскирует отсутствие runtime.
- **Контракт `selection_result` vs `site_info`**: нет ли утечки selection-текста в `info.txt`; строгое чтение gate только из `selection_result.json`.
- **Не тащить `rejected` / `manual_review` дальше** cleaner/site_info: проверить по логам и по папкам после run.
- **Мусор вне `runs/site/<run_id>/`**: старые `.orchestrator/reports`, `tmp*`, `_results` — периодически выносить в `archive/quarantine_old_runs/` (как в этой чистке).

---

## 5. После очистки рабочих данных — что сделать пользователю

1. Скопировать новые `.txt` в **`stories/input/`**.
2. Запустить **preflight** (как у вас принято в проекте — через CLI оркестратора или пункт меню, если добавлен).
3. Запустить **site pipeline** из `Content-Factory-Запуск.bat`.
4. Открыть **`runs/site/<run_id>/REPORT.md`** и папку **`runs/site/<run_id>/stories/`** (или разнесённые по вердикту каталоги — по версии оркестратора на момент запуска).
5. Проверить **`output/site/`** после стадий, которые туда пишут.

---

## 6. Примечание про `docs/root/ROOT_STRUCTURE.md`

В дереве файлов иногда встречаются имена вроде `site_stories/`; в актуальном договоре путей для оркестратора используются **`output/site/`** и **`output/youtube/`** (см. `configs/paths.yaml` и раздел «Данные» выше). При расхождении между документами ориентир — **реестр путей и фактическая структура на диске после прогона**.

---

## 7. Подготовительная очистка перед новым тестовым запуском (2026-05-01)

Выполнена **безопасная** уборка только данных: код, `legacy/`, `orchestrator/`, `configs/`, `docs/` (кроме этого файла), лаунчер, `.env`, `.cursor/` не изменялись.

### Dry-run (критерии)

Учитывались только разрешённые категории: входные `.txt` в корне `stories/input/`, прогоны в `runs/site/`, `runs/youtube/`, содержимое `output/site/`, `output/youtube/`, `logs/`, `archive/temp/`, а также перенос в карантин: `.orchestrator/reports`, `status.jsonl`, `events.jsonl`, `stories/input/_results`, старые `tmp*` в корне (если бы были).

### Факт переноса в карантин

Корневая папка карантина одного сеанса:

`archive/quarantine_old_runs/20260501_091919_prep_test_clean/`

Внутри сохранена относительная структура, например:

- `.orchestrator/reports`, `status.jsonl`, `events.jsonl`
- `.orchestrator/phasea_test`, `.orchestrator/phaseb_test` (дополнительно вынесены как явно generated тестовые деревья)
- `runs/site/site-run-a/`
- `archive/temp/20260429_135639/`
- `stories/input/_results/`

### Удалено безвозвратно

Только **файлы `*.txt` в корне** `stories/input/` (исходные рассказы для повторного теста — пользователь кладёт новые копии).

### Что осталось на месте

- Пустые служебные подпапки в `stories/input/` (`_audio_inbox`, `_tts_texts`, `_visual_inbox`) — **пустые**, на новый прогон не влияют.
- Файл `.orchestrator/phase_a_limit_mode.txt` (режим лимита из лаунчера) **не трогался**.
- Пустые `logs/`, `output/site/`, `output/youtube/`, пустой `runs/youtube/` — как контейнеры для следующего run.
