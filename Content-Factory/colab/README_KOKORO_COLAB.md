# Kokoro Colab Runner

Простой Colab flow для Site TTS handoff.

## Что загрузить в Colab

- Локально после export возьми файл:
  - `_COLAB_EXPORTS/<batch_folder>/02_UPLOAD_THIS_TO_COLAB.zip`

## Как запустить в Colab

1. Открой новый Google Colab notebook.
2. Установи зависимости:
   - `!pip install kokoro soundfile numpy`
   - Убедись, что `ffmpeg` доступен (`!ffmpeg -version`).
3. Загрузи в Colab файл `02_UPLOAD_THIS_TO_COLAB.zip`.
4. Запусти runner:
   - `!python kokoro_colab_runner.py --input-zip "/content/02_UPLOAD_THIS_TO_COLAB.zip" --output-dir "/content/kokoro_output"`

> Файл runner: `Content-Factory/colab/kokoro_colab_runner.py` (загрузи его в Colab или скопируй код в ячейку).

## Что получишь после выполнения

- `/content/kokoro_output/results/` — готовые mp3
- `/content/kokoro_output/results_report.csv` — статус по каждому item
- `/content/kokoro_output/kokoro_results.zip` — архив для скачивания

## Что делать локально после Colab

1. Скачай `kokoro_results.zip` (или mp3 из `results/`).
2. Положи mp3 в:
   - `_COLAB_EXPORTS/<batch_folder>/results_drop_here/`
3. Импортируй:
   - `python -m orchestrator site-tts kokoro-colab import --latest`
4. Проверь:
   - `python -m orchestrator site-tts kokoro-colab verify --latest`

## Важные заметки

- Локальный `sync --execute` для этого flow **не нужен**.
- Runner читает `manifest.json` и использует ожидаемые имена файлов (`item_XXXXXX.mp3`).
- Если часть item упала, batch не останавливается: ошибки пишутся в `results_report.csv`.
