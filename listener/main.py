"""
Telegram слушатель для мониторинга каналов и отправки сообщений в webhook
"""

import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, Any
import asyncio

import requests
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv


# Загрузка конфигурации из .env
load_dotenv()

API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
SOURCE_USERNAMES = os.getenv('SOURCE_USERNAMES', '').split(',')
SESSION_NAME = os.getenv('SESSION_NAME', 'arb_session')
POST_PAUSE_SECONDS = float(os.getenv('POST_PAUSE_SECONDS', '1.5'))

# Максимальная длина текста сообщения
MAX_TEXT_LENGTH = 10000

# Максимальное количество ID в памяти дублей
MAX_PROCESSED_IDS = 10000

# Файлы для хранения данных
PROCESSED_IDS_FILE = Path('processed_ids.json')
LOG_FILE = Path('listener.log')


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class MessageProcessor:
    """Обработчик сообщений из Telegram каналов"""
    
    def __init__(self):
        self.processed_ids: Set[int] = self.load_processed_ids()
        self.source_usernames = [u.strip().lower().lstrip('@') for u in SOURCE_USERNAMES if u.strip()]
        logger.info(f"Отслеживаемые каналы: {', '.join(self.source_usernames)}")
    
    def load_processed_ids(self) -> Set[int]:
        """Загрузка списка обработанных message_id из файла"""
        if PROCESSED_IDS_FILE.exists():
            try:
                with open(PROCESSED_IDS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    ids = set(data.get('ids', []))
                    logger.info(f"Загружено {len(ids)} обработанных ID")
                    return ids
            except Exception as e:
                logger.error(f"Ошибка загрузки processed_ids: {e}")
        return set()
    
    def save_processed_ids(self):
        """Сохранение списка обработанных ID в файл"""
        try:
            # Оставляем только последние MAX_PROCESSED_IDS записей
            ids_to_save = list(self.processed_ids)[-MAX_PROCESSED_IDS:]
            self.processed_ids = set(ids_to_save)
            
            with open(PROCESSED_IDS_FILE, 'w', encoding='utf-8') as f:
                json.dump({'ids': ids_to_save}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения processed_ids: {e}")
    
    def is_processed(self, message_id: int) -> bool:
        """Проверка, было ли сообщение уже обработано"""
        return message_id in self.processed_ids
    
    def mark_as_processed(self, message_id: int):
        """Отметить сообщение как обработанное"""
        self.processed_ids.add(message_id)
        self.save_processed_ids()
    
    def send_to_webhook(self, payload: Dict[str, Any], max_retries: int = 5) -> bool:
        """
        Отправка данных в webhook с повторными попытками и экспоненциальной задержкой
        """
        delay = 1
        
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    WEBHOOK_URL,
                    json=payload,
                    headers={'Content-Type': 'application/json'},
                    timeout=30
                )
                
                if response.status_code in (200, 201, 202):
                    logger.info(f"✓ Отправлено в webhook: {payload['source_username']}/{payload['message_id']}")
                    return True
                else:
                    logger.warning(f"Webhook ответил {response.status_code}: {response.text[:200]}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка отправки в webhook (попытка {attempt + 1}/{max_retries}): {e}")
            
            # Экспоненциальная задержка перед следующей попыткой
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
        
        logger.error(f"✗ Не удалось отправить в webhook после {max_retries} попыток")
        return False
    
    async def process_message(self, event):
        """Обработка нового сообщения"""
        message = event.message
        
        # Пропускаем сообщения без текста
        if not message.text:
            return
        
        # Получаем username канала
        try:
            chat = await event.get_chat()
            source_username = getattr(chat, 'username', None)
            
            if not source_username:
                return
            
            source_username = source_username.lower()
            
            # Проверяем, что канал в списке отслеживаемых
            if source_username not in self.source_usernames:
                return
            
        except Exception as e:
            logger.error(f"Ошибка получения информации о чате: {e}")
            return
        
        message_id = message.id
        
        # Проверяем дубликаты
        if self.is_processed(message_id):
            return
        
        # Обрезаем текст до MAX_TEXT_LENGTH символов
        text = message.text[:MAX_TEXT_LENGTH]
        
        # Формируем payload
        payload = {
            'source_username': source_username,
            'message_id': message_id,
            'text': text,
            'timestamp': message.date.isoformat()
        }
        
        logger.info(f"→ Новое сообщение: @{source_username} #{message_id}")
        
        # Отправляем в webhook
        success = self.send_to_webhook(payload)
        
        if success:
            # Отмечаем как обработанное
            self.mark_as_processed(message_id)
            
            # Пауза между запросами
            await asyncio.sleep(POST_PAUSE_SECONDS)


async def main():
    """Основная функция запуска слушателя"""
    
    # Проверка обязательных переменных окружения
    if not API_ID or not API_HASH:
        logger.error("❌ API_ID и API_HASH должны быть указаны в .env файле")
        return
    
    if not WEBHOOK_URL:
        logger.error("❌ WEBHOOK_URL должен быть указан в .env файле")
        return
    
    if not SOURCE_USERNAMES or not any(SOURCE_USERNAMES):
        logger.error("❌ SOURCE_USERNAMES должен быть указан в .env файле")
        return
    
    logger.info("=" * 60)
    logger.info("Запуск Telegram слушателя")
    logger.info(f"Webhook: {WEBHOOK_URL}")
    logger.info(f"Пауза между запросами: {POST_PAUSE_SECONDS} сек")
    logger.info("=" * 60)
    
    # Инициализация процессора сообщений
    processor = MessageProcessor()
    
    # Создание клиента Telethon
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    
    # Обработчик новых сообщений
    @client.on(events.NewMessage())
    async def handler(event):
        try:
            await processor.process_message(event)
        except FloodWaitError as e:
            logger.warning(f"⏳ FloodWait: ожидание {e.seconds} секунд")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)
    
    # Запуск клиента
    await client.start()
    logger.info("✓ Клиент запущен и слушает сообщения...")
    logger.info("Для остановки нажмите Ctrl+C")
    
    # Бесконечная работа
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⏹ Остановка слушателя...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
