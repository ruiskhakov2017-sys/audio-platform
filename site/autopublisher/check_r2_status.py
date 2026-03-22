"""
Только проверяет состояние R2 vs Supabase. Ничего не удаляет.
Показывает сколько файлов в R2 и сколько из них привязаны к рассказам.
"""
import os
import sys
import json
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(path=None):
        env_file = path or Path(__file__).resolve().parent / ".env"
        if not Path(env_file).exists():
            return
        with open(env_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                idx = line.find("=")
                if idx <= 0:
                    continue
                key = line[:idx].strip()
                value = line[idx + 1:].strip().strip("'\"")
                os.environ.setdefault(key, value)

from botocore.config import Config
import boto3

_load_dir = Path(__file__).resolve().parent
load_dotenv(_load_dir / ".env")
load_dotenv(_load_dir.parent / ".env.local")
load_dotenv(_load_dir.parent / ".env")
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET_NAME") or os.environ.get("R2_BUCKET") or "stories"
R2_PUBLIC_URL = (os.environ.get("R2_PUBLIC_URL") or "").rstrip("/")


def _fmt_size(size_bytes: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} ТБ"


def fetch_all_stories() -> list[dict]:
    """Загружает все рассказы из Supabase постранично (по 1000)."""
    page_size = 1000
    offset = 0
    all_rows: list[dict] = []
    while True:
        url = (
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/stories"
            f"?select=id,title,audio_url,image_url&limit={page_size}&offset={offset}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                batch = json.loads(resp.read())
        except Exception as e:
            print(f"ОШИБКА Supabase: {e}", flush=True)
            sys.exit(1)
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_rows


def main():
    print("--- Шаг 1: Считаем рассказы в Supabase ---", flush=True)
    rows = fetch_all_stories()

    print(f"  Рассказов в Supabase: {len(rows)}", flush=True)
    if rows:
        print(f"  Пример записи: {rows[0].get('title')} | audio: {rows[0].get('audio_url', '')[:60]}...", flush=True)

    known_keys: set[str] = set()
    for row in rows:
        for field in ("audio_url", "image_url"):
            val = row.get(field) or ""
            if val:
                key = urllib.parse.urlparse(val).path.lstrip("/")
                if key:
                    known_keys.add(key)

    print(f"  Известных R2-ключей (audio+image): {len(known_keys)}", flush=True)

    print("", flush=True)
    print("--- Шаг 2: Считаем файлы в R2 ---", flush=True)
    r2 = boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )

    all_keys: list[tuple[str, int]] = []
    paginator = r2.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET):
        for obj in page.get("Contents", []):
            all_keys.append((obj["Key"], obj["Size"]))

    total_size = sum(s for _, s in all_keys)
    print(f"  Файлов в R2: {len(all_keys)}", flush=True)
    print(f"  Суммарный объём: {_fmt_size(total_size)}", flush=True)

    matched = [(k, s) for k, s in all_keys if k in known_keys]
    orphans = [(k, s) for k, s in all_keys if k not in known_keys]
    orphan_size = sum(s for _, s in orphans)

    print("", flush=True)
    print("--- Итог ---", flush=True)
    print(f"  Файлов принадлежат рассказам (аудио/картинки): {len(matched)}", flush=True)
    print(f"  Файлов-сирот (мусор): {len(orphans)} ({_fmt_size(orphan_size)})", flush=True)

    if len(matched) == len(known_keys):
        print("", flush=True)
        print("  ВСЕ ФАЙЛЫ РАССКАЗОВ НА МЕСТЕ. R2 целый.", flush=True)
    else:
        missing = len(known_keys) - len(matched)
        print("", flush=True)
        print(f"  ВНИМАНИЕ: {missing} файлов из Supabase НЕ НАЙДЕНЫ в R2!", flush=True)
        missing_keys = known_keys - {k for k, _ in matched}
        for k in sorted(missing_keys)[:10]:
            print(f"    пропал: {k}", flush=True)
        if len(missing_keys) > 10:
            print(f"    ... и ещё {len(missing_keys) - 10}", flush=True)

    # Показываем примеры файлов для сравнения
    print("", flush=True)
    print("--- Примеры файлов рассказов (эти точно нужны, их НЕ тронем) ---", flush=True)
    for k, s in sorted(matched)[:5]:
        print(f"  {_fmt_size(s):>10}  {k}", flush=True)

    print("", flush=True)
    print("--- Примеры сирот (эти будут удалены) ---", flush=True)
    for k, s in sorted(orphans)[:15]:
        print(f"  {_fmt_size(s):>10}  {k}", flush=True)
    if len(orphans) > 15:
        print(f"  ... и ещё {len(orphans) - 15} файлов", flush=True)

    print("", flush=True)
    print("Сравни: если сироты выглядят похоже на файлы рассказов (те же имена,", flush=True)
    print("только дублированные с другим timestamp) — значит это мусор от упавших запусков.", flush=True)


if __name__ == "__main__":
    main()
