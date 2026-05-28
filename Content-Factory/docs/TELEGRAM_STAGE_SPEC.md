# Telegram Stage — Specification

Связан с `docs/AUTOMATION_READINESS_AUDIT.md` и `docs/AUTOMATION_READINESS_BACKLOG.md`.

Статус: **stage не реализован**. Это спека, не отчёт о том что есть.

---

## 1. Цель

После публикации рассказа на сайт автоматически отправлять его в Telegram-канал. Не блокировать сайт-публикацию при ошибке Telegram. Не отправлять повторно при resume.

---

## 2. Что отправляется

| Поле | Источник | Обязательно |
|---|---|---|
| Текст рассказа (raw / неочищенный) | `Запуски/<launch>/05_Рассказы/<id>/01_Общее/source.txt` ИЛИ `Запуски/<launch>/02_Сайт/05_Публикация_на_сайт/<story>/<story>__[MFU].txt` (cleaned, но НЕ safe) | да; точный выбор задаётся config `telegram.text_source` |
| Текст поста / описание | `Запуски/<launch>/04_Telegram/<story>/post.txt` (рендерится из `site_info.json + render_info_en_txt`) или отдельный Gemini bot `telegram_post_builder` (будущее) | да |
| Картинка | `Запуски/<launch>/02_Сайт/05_Публикация_на_сайт/<story>/<story>.jpg` | да |
| Ссылка на сайт | `Запуски/<launch>/02_Сайт/05_Публикация_на_сайт/<story>/published.json::site_url` | да (если published_ok); без неё — отправка задерживается |
| Title | `site_info.json::title` или `alternative_title` | да |
| Genres | `site_info.json::genres` | опционально |
| Tags | `site_info.json::tags` | опционально |
| Voice type | `site_info.json::voice_type` (M/F/U) | опционально |

YouTube версия (safe rewrite) **не** идёт в Telegram. Telegram = raw / cleaned-but-not-safe.

---

## 3. Шаблон поста

Конфиг `configs/telegram.yaml` (новый):
```yaml
telegram:
  bot_token_env: TELEGRAM_BOT_TOKEN          # из .env.telegram
  channel_id_env: TELEGRAM_CHANNEL_ID        # из .env.telegram (e.g. -1001234567890)
  text_source: source                         # source | cleaned_voice_tagged
  post_template: |
    *{{ title }}*
    _{{ alternative_title }}_

    {{ description }}

    Жанры: {{ genres | join(", ") }}
    Теги: {{ tags | join(", ") }}

    Читать полностью: {{ site_url }}

  send_strategy: image_with_caption           # image_with_caption | text_only | text_plus_image
  caption_max_chars: 1024                     # Telegram limit для image caption
  text_chunk_chars: 4000                       # если text_only, разбить на части
  send_attachment: site_url                   # attach link as link-preview
  retries: 3
  retry_backoff_seconds: [5, 15, 60]
  dry_run_default: true
```

Если description + url > 1024 — fallback на text_plus_image (картинка отдельно, текст отдельно).

---

## 4. CLI команды

```
python -m orchestrator telegram prepare --launch-name <X>           # dry-run, проверки
python -m orchestrator telegram send --launch-name <X> --execute    # отправка
python -m orchestrator telegram send --launch-name <X> --story-id <Y> --execute
python -m orchestrator telegram resend --launch-name <X> --story-id <Y> --execute  # принудительный re-send
python -m orchestrator telegram report --launch-name <X>            # status report
```

---

## 5. Drive / Path bindings

```
Запуски/<launch>/
  04_Telegram/                              # НОВОЕ; добавить D04_TELEGRAM в top_level_dirs
    _channel_binding.json                   # { "channel_alias": "default", "channel_id": "-100..." } (БЕЗ токена)
    <story>/
      raw_text.txt                          # копия source/cleaned, использованная для отправки
      post.txt                              # рендеренный пост (без секретов)
      image.jpg                             # копия cover
      site_url.txt                          # site URL на момент отправки
      sent.json                             # idempotency marker {message_id, sent_at, channel_id, file_id_image, retries_used}
      attempt_<n>.log                       # лог попыток
    summary.json                            # batch summary
```

`sent.json` — единственный source of truth: «отправлено / не отправлено». При `telegram send` — если `sent.json.message_id` есть и `sent_at` валидно — skip без re-send.

---

## 6. Pipeline

### `telegram prepare`
1. Прочитать `Запуски/<launch>/manifest.json` → список stories.
2. Для каждого story:
   - Проверить наличие raw_text source (по config.text_source).
   - Проверить наличие image (`02_Сайт/05_Публикация_на_сайт/<story>/<story>.jpg`).
   - Проверить наличие `published.json` с `site_url` (если published_ok отсутствует — `status=waiting_for_publish`).
   - Проверить `sent.json` (если есть — `status=already_sent`).
   - Сформировать post через `post_template`.
3. Записать `04_Telegram/<story>/raw_text.txt + post.txt + image.jpg`.
4. Записать `04_Telegram/summary.json` со списком `to_send / already_sent / waiting_for_publish / missing_assets`.
5. Никакого Telegram API не вызывается.

### `telegram send --execute`
1. То же что prepare.
2. Если `sent.json` отсутствует и все ассеты есть — вызвать Telegram Bot API:
   - `https://api.telegram.org/bot<TOKEN>/sendPhoto` с `chat_id, photo (file), caption (markdown)` если caption <= 1024.
   - Иначе `sendPhoto` без caption + `sendMessage` с post + url.
3. Записать `sent.json` с `message_id` из ответа.
4. retries при HTTP ошибках; backoff из config.
5. Append в `04_Telegram/<story>/attempt_<n>.log`.
6. Не падать на первой ошибке — собрать errors в `summary.json` и продолжить остальные.

### `telegram resend --story-id Y --execute`
- Удалить `sent.json`, вызвать `telegram send --story-id Y --execute`.
- Записать `resent_at` в новый `sent.json`.

### `telegram report --launch-name X`
- Read-only.
- Печатает: total / sent / waiting_for_publish / missing_assets / failed.

---

## 7. Идемпотентность

- Маркер: `04_Telegram/<story>/sent.json` (см. секция 5).
- При resume:
  - `telegram send` без `--force` skips story, если `sent.json.message_id` валиден.
  - При `--force` — обновляет post (предупреждение пользователю в Telegram о возможном дубле).
- Глобальный counter `published_telegram` в `launch status.json` инкрементится из количества `sent.json` файлов.

---

## 8. Env / config

`.env.telegram` (new):
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL_ID=-100xxxxxxxxxxx
TELEGRAM_BOT_USERNAME=...   # опционально для логирования
```

Safety:
- Не логировать TELEGRAM_BOT_TOKEN ни в `events.jsonl`, ни в `04_Telegram/<story>/attempt_*.log`.
- Маска `TELEGRAM_BOT_TOKEN=<redacted>` в `_channel_binding.json`.

---

## 9. Где встроить в run-site-flow

В `orchestrator/human_launch_site_flow_bat.py::run_site_flow_bat_execute`:

```
after site_run (autopublisher) succeeded:
  cmd_collect = _build_collect_assets_cmd(...)
  subprocess.run(cmd_collect, ...)

  cmd_prepare = _build_site_publish_prepare_cmd(...)
  subprocess.run(cmd_prepare, ...)

  cmd_publish = _build_site_publish_publish_cmd(...)
  subprocess.run(cmd_publish, ...)

  if config.telegram.enabled:
    cmd_telegram = _build_telegram_send_cmd(launch_name, execute=True)
    subprocess.run(cmd_telegram, ...)

  cmd_final = _build_final_report_cmd(launch_name, execute=True)
  subprocess.run(cmd_final, ...)
```

Каждая команда — отдельный subprocess для изоляции и логов. Ошибка Telegram **не отменяет** site publish, но фиксируется в `terminal_detail`.

---

## 10. Wrapper-вариант (альтернатива)

Можно сделать Telegram отдельным stage в `Runner` (как `autopublisher`):

```python
# orchestrator/wrappers/telegram_publish.py
class TelegramPublishWrapper(BaseWrapper):
    contract = StageContract(
        stage="telegram_publish",
        branch="site",
        unsafe=True,
        destructive_ops=["external_message_send"],
        entrypoint="orchestrator/telegram/main.py",
    )
```

В `orchestrator/wrappers/__init__.py::SITE_STAGES` добавить `"telegram_publish"` после `autopublisher`.

Плюс: единый event flow, status.jsonl, real_stage_whitelist.
Минус: каждый рассказ отдельным wrapper-execute, медленнее.

Рекомендация: **сначала subprocess-вариант (CLI команда)**, потом если нужна гранулярность — wrapper.

---

## 11. Acceptance criteria (P0-2)

1. `python -m orchestrator telegram prepare --launch-name X` показывает план без отправки и пишет `Запуски/X/04_Telegram/summary.json`.
2. `python -m orchestrator telegram send --launch-name X --execute` отправляет всех `to_send` рассказов; для каждого создаётся `sent.json`.
3. Повторный запуск с теми же параметрами не отправляет повторно (skips через `sent.json`).
4. При неуспехе одного рассказа продолжает остальных и возвращает exit code 0 если хотя бы один отправлен; 1 если все failed.
5. `Запуски/X/04_Telegram/` создаётся, что добавляет D04_TELEGRAM в `top_level_dirs()`.
6. `aggregate_launch_status` увеличивает `published_telegram` корректно (счётчик == количество `sent.json` файлов).
7. После `delete launch --execute` папка `04_Telegram/` удаляется вместе с маркерами.
8. Безопасность: `TELEGRAM_BOT_TOKEN` не попадает в `.orchestrator/events.jsonl`, `04_Telegram/*.log`, `_channel_binding.json`.
9. Dry-run по умолчанию: без `--execute` ничего не отправляется (соответствует `.cursor/rules/content-factory-safety.mdc`).
10. `Content-Factory-Запуск.bat` имеет `[1] Site → [5] Site Telegram only` отдельным пунктом.

---

## 12. Будущие расширения

- `telegram_post_builder` — отдельный Gemini bot, который пишет красивый пост (а не render_info_en_txt из shaблонa).
- Многоканальная рассылка (см. P2-4).
- Подписи по жанрам (#тэги).
- Кнопки inline reply markup со ссылкой на сайт / next story.
- Опционально: posting в Telegram channel + commenting bot в Telegram group.
