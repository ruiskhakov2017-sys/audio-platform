# Докачка первых частей рассказов

Скрипт ищет в .txt файлах строку `URL первой части: https://...`, скачивает текст по ссылке (с авторизацией) и вклеивает его в начало файла.

## Установка

```
pip install -r requirements.txt
```

## Настройка

В начале `download_first_parts.py`:
- `LOGIN_URL`, `USERNAME`, `PASSWORD`
- `LOGIN_FORM_FIELDS` — имена полей формы (name="..." в HTML)
- `TEXT_CONTAINER_SELECTOR` — CSS-селектор контейнера с текстом (например `div.post-content`)
- `ROOT_DIR` — папка с подпапками (по умолчанию `all_stories` рядом со скриптом)

## Запуск

```
cd download-first-parts
python download_first_parts.py
```
