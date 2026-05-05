# EXECUTION PLAN

Практический трекер работ по `Content-Factory` (операционный план, не фазная стратегия).

Статусы:
- `done`
- `in_progress`
- `next`
- `blocked`
- `backlog`

---

## 1. Уже сделано

| ID | Блок / направление | Конкретная задача | Зачем она нужна | Статус | Зависит от | Результат выполнения | Критерий завершения |
|---|---|---|---|---|---|---|---|
| EP-001 | Аудит реальности | Сформировать карту фактической системы | Зафиксировать реальный прод-процесс до изменений | done | — | `docs/project/PROJECT_REALITY_MAP.md` | Документ покрывает вход/выход/границы и интеграционный принцип |
| EP-002 | Инвентарь модулей | Зафиксировать роли, входы/выходы, риски legacy-модулей | Понять что можно/нельзя подключать в оркестратор | done | EP-001 | `docs/project/MODULE_INVENTORY.md` | Для каждого ключевого модуля описаны контрактные поля |
| EP-003 | Pipeline mapping | Описать end-to-end потоки site + YouTube + потенциальный Telegram | Убрать разрыв между кодом и фактическим производством | done | EP-001 | `docs/project/FULL_PIPELINE_MAP.md` | Карта покрывает путь от raw текста до публикации |
| EP-004 | Gap analysis | Разложить что автоматизировано, ручное, полуавто, отсутствует | Определить безопасный порядок внедрения | done | EP-002, EP-003 | `docs/project/AUTOMATION_GAP_MAP.md` | Явно выделены automation gaps |
| EP-005 | Risk control | Формализовать риски и ограничения | Не сломать рабочие ветки | done | EP-002, EP-004 | `docs/project/RISKS_AND_CONSTRAINTS.md` | Есть ограничения на destructive этапы и legacy-touch |
| EP-006 | Стратегия | Сформировать фазный roadmap | Дать макро-порядок работ | done | EP-001..EP-005 | `docs/project/ROADMAP.md` | Определены фазы 0..7 с deliverables |
| EP-007 | MVP design | Описать безопасный первый слой оркестрации | Подготовить реализацию без переписывания legacy | done | EP-006 | `docs/project/MVP_ORCHESTRATOR_PLAN.md` | Определен scope MVP и границы изменений |
| EP-008 | Project rules | Создать постоянные правила в `.cursor/rules` | Зафиксировать safety policy в проекте | done | EP-007 | `.cursor/rules/*.mdc` | Правила покрывают legacy-first + dry-run-by-default |
| EP-009 | Orchestrator skeleton | Создать каркас `orchestrator/` + CLI | Получить единую точку управления | done | EP-007, EP-008 | `python -m orchestrator` с командами | Команды `preflight/plan/status/run` работают |
| EP-010 | Dry-run wrappers | Подключить wrappers как фасады dry-run-only | Интегрировать модули без side effects | done | EP-009 | wrappers для `bulk_text_cleaner/gemini_auto/elevenlabs/autovideo/autopublisher` | Все wrappers выполняют contract-check, execute не запускает destructive логику |
| EP-011 | Service telemetry | Вынести статусы/события в отдельный сервисный слой | Не засорять legacy runtime | done | EP-009 | `.orchestrator/status.jsonl`, `.orchestrator/events.jsonl` | Run фиксируется независимо от legacy |

---

## 2. Сейчас в работе

| ID | Блок / направление | Конкретная задача | Зачем она нужна | Статус | Зависит от | Результат выполнения | Критерий завершения |
|---|---|---|---|---|---|---|---|
| EP-012 | Контракты модулей | Уточнение контрактов wrappers до уровня pre/post checks на артефакты | Перейти от “пустого dry-run” к проверяемому dry-run | in_progress | EP-010 | Явные `preconditions/postconditions/failure modes` по каждому wrapper | Для каждого wrapper есть проверяемые contract checks, без реального выполнения destructive этапов |

---

## 3. Следующие шаги

| ID | Блок / направление | Конкретная задача | Зачем она нужна | Статус | Зависит от | Результат выполнения | Критерий завершения |
|---|---|---|---|---|---|---|---|
| EP-013 | Safe execution | Подключить 1 реально исполняемый безопасный wrapper (read-mostly) через subprocess | Проверить “живой” запуск без риска | next | EP-012 | Один wrapper умеет реальный safe-run + dry-run | Успешный запуск на тестовом story без delete/move/upload |
| EP-014 | Post-check | Добавить post-check контракт для EP-013 (ожидаемые артефакты/логи) | Подтвердить корректный результат шага | next | EP-013 | Контракт post-check в wrapper | Wrapper возвращает pass/fail по факту артефактов |
| EP-015 | Execution record | Ввести явную запись pipeline execution (run manifest) | Нужен аудит шагов и воспроизводимость | next | EP-011 | `run_manifest` в `.orchestrator/` | Каждый run имеет список шагов, режим, итоги |
| EP-016 | Stage selection | Добавить контролируемый запуск subset/stage (`--stages`) | Точечная проверка модулей без full-run | next | EP-015 | CLI-параметр выбора этапов | Можно запустить только один/несколько этапов |
| EP-017 | Preflight hardening | Расширить preflight: unsafe-gates, конфиг-валидация, обязательные пути | Снизить риск ошибочного запуска | next | EP-012 | Усиленные preflight checks | Preflight блокирует run при нарушениях |
| EP-018 | Site pilot slice | Выбрать первый безопасный E2E фрагмент site-ветки (без publish) | Начать сборку полезного контура | next | EP-013, EP-017 | Описанный и запускаемый “site mini-flow” | Flow стабильно проходит dry-run + safe-step run |

---

## 4. После этого

| ID | Блок / направление | Конкретная задача | Зачем она нужна | Статус | Зависит от | Результат выполнения | Критерий завершения |
|---|---|---|---|---|---|---|---|
| EP-019 | Site assembly | Собрать полный site pipeline в оркестраторе (до publish-gates) | Перейти к управляемому прод-потоку для сайта | backlog | EP-018 | Связанный flow `clean -> metadata -> audio -> package` | Flow проходит на пилотной партии без ручной склейки |
| EP-020 | Publish gating | Внедрить verify-gates перед `autopublisher` | Защитить от ошибочных публикаций | backlog | EP-019 | Правила допуска к publish | Без прохождения gates publish не стартует |
| EP-021 | Controlled publish | Разрешить execute для `autopublisher` только после verify | Аккуратно включить destructive этап | backlog | EP-020 | Частично реальный publish-run | Пилот публикуется корректно, без orphan/дублей |
| EP-022 | YouTube assembly | Подключить YouTube-ветку в оркестратор по шагам | Управляемый выпуск роликов | backlog | EP-018, EP-017 | Оркестрируемый `selection -> safe -> director -> audio -> video` | Минимум 1 стабильный E2E YouTube pilot |
| EP-023 | Resume/retry | Добавить межмодульные resume/retry политики | Устойчивость long-running пайплайнов | backlog | EP-019, EP-022 | Глобальные retry/resume правила | Сбои на шагах восстанавливаются без ручного re-run всей цепочки |

---

## 5. Позже / backlog

| ID | Блок / направление | Конкретная задача | Зачем она нужна | Статус | Зависит от | Результат выполнения | Критерий завершения |
|---|---|---|---|---|---|---|---|
| EP-024 | Monitoring | Добавить агрегированные отчеты/дашборд по run и ошибкам | Операционный контроль масштаба | backlog | EP-023 | Сводный мониторинг по пайплайнам | Видны latency/fail-rate/узкие места |
| EP-025 | Throughput scaling | Стандартизировать batch-политику и конкурентность | Масштаб к большим объемам | backlog | EP-024 | Регламент батчей и лимитов API | Достигнут стабильный throughput на пилотах |
| EP-026 | Manual flow codification | Формализовать ручные/полуавто шаги в SOP | Убрать “знание в голове оператора” | backlog | EP-019, EP-022 | Операционные инструкции + чек-листы | Критические ручные шаги документированы |
| EP-027 | Telegram branch | Проектирование и подключение Telegram-ветки как policy-target | Multi-channel публикация | backlog | EP-019, EP-022 | Отдельный маршрут доставки для Telegram | Есть безопасный pilot Telegram path |

---

## 6. Блокеры и риски

| ID | Блок / направление | Конкретная задача | Зачем она нужна | Статус | Зависит от | Результат выполнения | Критерий завершения |
|---|---|---|---|---|---|---|---|
| EP-B01 | Доступы | Проверить рабочие креды/квоты для Gemini/ElevenLabs/Cloud/DB | Без валидных доступов нельзя проверять real-run | blocked | Внешние условия | Подтвержденный доступ по каждому сервису | Все нужные внешние сервисы проходят auth-check |
| EP-B02 | Runtime hygiene | Разграничить сервисные артефакты оркестратора и legacy runtime | Снизить риск случайной порчи состояния | next | EP-011 | Зафиксирован policy хранения state/log | Нет пересечений `.orchestrator` с legacy runtime |
| EP-B03 | Destructive safety | Формализовать список unsafe шагов и разрешений execute | Не допустить непреднамеренных delete/move/upload | in_progress | EP-012 | Матрица unsafe-by-stage + gate policy | Каждый unsafe шаг требует явного допуска и preflight pass |
| EP-B04 | Hidden manual steps | Выявить неявные ручные переходы между модулями | Иначе E2E будет нестабилен | next | EP-026 | Карта ручных операций и владельцев | Для каждого ручного шага есть owner и checklist |

---

## Что пока нельзя трогать

- Нельзя переписывать legacy-модули.
- Нельзя менять Gemini-промпты.
- Нельзя менять старую файловую структуру runtime-папок.
- Нельзя включать destructive publish/cleanup этапы в real-run без verify-gates.
- Нельзя объединять дублирующиеся legacy-ветки до отдельного решения после стабилизации оркестратора.

---

## Рекомендуемый порядок работ на ближайшие 10 шагов

1. Завершить `EP-012`: довести контракты wrappers до проверяемых pre/post checks.
2. Реализовать `EP-017`: усилить preflight и блокировки unsafe запусков.
3. Выполнить `EP-013`: подключить 1 safe wrapper с реальным subprocess-run.
4. Выполнить `EP-014`: добавить post-check артефактов для этого wrapper.
5. Выполнить `EP-015`: добавить `run_manifest` и единый execution record.
6. Выполнить `EP-016`: добавить выбор запускаемых этапов (`--stages`).
7. Выполнить `EP-018`: собрать первый безопасный site mini-flow (без publish).
8. Выполнить `EP-020`: внедрить publish verify-gates.
9. Выполнить `EP-021`: разрешить controlled execute для `autopublisher` только после gate-pass.
10. Перейти к `EP-022`: пошаговая сборка YouTube pipeline в оркестраторе.
