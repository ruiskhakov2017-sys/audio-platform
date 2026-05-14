"""
Удаляет из R2 файлы-сироты — те, что не привязаны ни к одному рассказу в Supabase.

Запуск:
  python cleanup_r2_orphans.py           # dry-run: только показывает что будет удалено
  python cleanup_r2_orphans.py --delete  # реально удаляет файлы
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


def _url_to_r2_key(url: str) -> str | None:
    """Извлекает R2-ключ из полного URL через парсинг пути (не зависит от домена)."""
    if not url:
        return None
    try:
        path = urllib.parse.urlparse(url).path.lstrip("/")
        return path if path else None
    except Exception:
        return None


def fetch_known_r2_keys() -> set[str]:
    """Запрашивает Supabase и возвращает set R2-ключей, привязанных к рассказам."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("ОШИБКА: не заданы SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY", flush=True)
        sys.exit(1)

    known: set[str] = set()
    offset = 0
    limit = 1000

    print("Загружаем известные ключи из Supabase...", flush=True)
    # Пробуем с text_url, если колонки нет — fallback без неё
    select_fields_options = [
        "audio_url,image_url,text_url",
        "audio_url,image_url",
    ]
    select_fields = select_fields_options[0]

    while True:
        url = (
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/stories"
            f"?select={select_fields}&limit={limit}&offset={offset}"
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
                rows = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if e.code == 400 and "text_url" in body and select_fields == select_fields_options[0]:
                print(
                    "  Колонка text_url ещё не добавлена в Supabase — запрашиваем без неё.",
                    flush=True,
                )
                select_fields = select_fields_options[1]
                offset = 0
                known.clear()
                continue
            print(f"ОШИБКА Supabase {e.code}: {body or e.reason}", flush=True)
            sys.exit(1)

        for row in rows:
            for field in ("audio_url", "image_url", "text_url"):
                key = _url_to_r2_key(row.get(field) or "")
                if key:
                    known.add(key)

        if len(rows) < limit:
            break
        offset += limit

    print(f"  Известных R2-ключей в Supabase: {len(known)}", flush=True)
    if known:
        sample = sorted(known)[:3]
        print(f"  Примеры ключей из Supabase: {sample}", flush=True)
    else:
        print("  ВНИМАНИЕ: не удалось извлечь ни одного ключа из Supabase!", flush=True)
        print("  Проверьте что audio_url/image_url заполнены в таблице stories.", flush=True)
    return known


def get_r2_client():
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY]):
        print("ОШИБКА: не заданы R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY", flush=True)
        sys.exit(1)
    return boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


def list_all_r2_objects(client) -> list[tuple[str, int]]:
    """Возвращает список (key, size_bytes) всех объектов в бакете."""
    print(f"Листаем содержимое R2 бакета '{R2_BUCKET}'...", flush=True)
    objects: list[tuple[str, int]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET):
        for obj in page.get("Contents", []):
            objects.append((obj["Key"], obj["Size"]))
    print(f"  Всего объектов в R2: {len(objects)}", flush=True)
    return objects


def delete_keys_from_r2(client, keys: list[str]) -> None:
    """Удаляет объекты из R2 пачками по 1000 (лимит S3 API)."""
    total = len(keys)
    deleted = 0
    for i in range(0, total, 1000):
        batch = keys[i : i + 1000]
        response = client.delete_objects(
            Bucket=R2_BUCKET,
            Delete={"Objects": [{"Key": k} for k in batch]},
        )
        errors = response.get("Errors", [])
        if errors:
            for err in errors:
                print(f"  [!] Не удалось удалить {err['Key']}: {err['Message']}", flush=True)
        deleted += len(batch) - len(errors)
        print(f"  Удалено {deleted}/{total}...", flush=True)


def main() -> None:
    dry_run = "--delete" not in sys.argv

    if dry_run:
        print("=" * 60, flush=True)
        print("  РЕЖИМ DRY-RUN (без удаления)", flush=True)
        print("  Запусти с --delete для реального удаления.", flush=True)
        print("=" * 60, flush=True)
    else:
        print("=" * 60, flush=True)
        print("  РЕЖИМ УДАЛЕНИЯ — файлы будут УДАЛЕНЫ из R2!", flush=True)
        print("=" * 60, flush=True)

    known_keys = fetch_known_r2_keys()
    r2_client = get_r2_client()
    all_objects = list_all_r2_objects(r2_client)

    orphans = [(key, size) for key, size in all_objects if key not in known_keys]
    total_orphan_size = sum(size for _, size in orphans)

    print(f"\nФайлов-сирот (не привязаны к рассказам): {len(orphans)}", flush=True)
    print(f"Суммарный объём: {_fmt_size(total_orphan_size)}", flush=True)

    if not orphans:
        print("\nМусора нет, R2 чистый!", flush=True)
        return

    print("\nСписок сирот:", flush=True)
    for key, size in orphans:
        print(f"  {_fmt_size(size):>10}  {key}", flush=True)

    if dry_run:
        print(
            f"\nДля удаления {len(orphans)} файлов ({_fmt_size(total_orphan_size)}) "
            "запусти:\n  python cleanup_r2_orphans.py --delete",
            flush=True,
        )
        return

    print(f"\nУдаляем {len(orphans)} файлов ({_fmt_size(total_orphan_size)})...", flush=True)
    orphan_keys = [key for key, _ in orphans]
    delete_keys_from_r2(r2_client, orphan_keys)
    print(f"\nГотово. Освобождено ~{_fmt_size(total_orphan_size)} в R2.", flush=True)


if __name__ == "__main__":
    main()
