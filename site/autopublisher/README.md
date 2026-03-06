# Автопаблишер рассказов

Скрипт сканирует папку `To_Publish`, загружает медиа в R2 и добавляет записи в Supabase, затем перемещает обработанные папки в `Published_Archive`.

## Установка

```bash
cd site/autopublisher
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Скопируй `.env.example` в `.env` и заполни переменные.

## Структура папки рассказа (в `To_Publish/Имя_папки/`)

- **info.txt** — метаданные в формате `Ключ: Значение`:
  - Название / Title
  - Описание / Description
  - Жанры / Genres (через запятую)
  - Теги / Tags (через запятую)
  - Автор / Author (опционально)
  - Премиум / is_premium (опционально: 1, true, да, yes)
- **\*.txt** (кроме info.txt) — текст рассказа (если в info нет Описания, склеивается сюда).
- Один аудиофайл: `.mp3`, `.wav`, `.m4a`, `.m4b`, `.ogg`
- Одно изображение: `.jpg`, `.png`, `.webp`, `.gif`

## Запуск

```bash
python publish_stories.py
```

Между загрузками папок — пауза 2 секунды.
