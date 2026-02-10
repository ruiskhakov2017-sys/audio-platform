# Telegram Listener

Минимальный слушатель для мониторинга Telegram каналов и отправки сообщений в webhook.

## Быстрый старт

1. Установите зависимости: `pip install -r requirements.txt`
2. Скопируйте `example.env` в `.env` и заполните все поля (API_ID, API_HASH, WEBHOOK_URL, SOURCE_USERNAMES)
3. Запустите: `python main.py`
4. При первом запуске введите номер телефона и код подтверждения из Telegram
5. Слушатель работает бесконечно, для остановки нажмите Ctrl+C

## Примечания

- Обработанные message_id хранятся в `processed_ids.json`
- Логи записываются в `listener.log` и выводятся в консоль
- Сессия Telegram сохраняется в файле `{SESSION_NAME}.session`
