import os
import boto3
from dotenv import load_dotenv

# Загружаем настройки из .env
load_dotenv()

endpoint = os.getenv('AWS_S3_ENDPOINT_URL')
key = os.getenv('AWS_ACCESS_KEY_ID')
secret = os.getenv('AWS_SECRET_ACCESS_KEY')
bucket_name = os.getenv('AWS_STORAGE_BUCKET_NAME')

print(f"--- ПРОВЕРКА S3 ---")
print(f"1. Endpoint: {endpoint}")
print(f"2. Bucket:   {bucket_name}")
print(f"3. Key ID:   {key} (Length: {len(str(key)) if key else 0})")

if not endpoint or not key or not secret:
    print("❌ ОШИБКА: Какие-то данные не загрузились из .env!")
    exit()

try:
    print("\nПопытка подключения...")
    s3 = boto3.client('s3',
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name='auto'
    )
    
    # Пробуем получить список бакетов
    response = s3.list_buckets()
    print("✅ УСПЕХ! Соединение установлено.")
    print("Ваши бакеты:")
    for bucket in response['Buckets']:
        print(f" - {bucket['Name']}")

except Exception as e:
    print(f"\n❌ ОШИБКА ПОДКЛЮЧЕНИЯ:\n{e}")