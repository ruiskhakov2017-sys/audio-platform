"""
GUI-автопаблишер: сканирует To_Publish, загружает медиа в R2, вставляет в Supabase,
пишет отчёт в publish_log.txt. Запуск публикации в отдельном потоке.
"""
import os
import re
import time
import threading
import queue
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client
from botocore.config import Config
import boto3
from tinytag import TinyTag

load_dotenv()

# --- Конфиг из env ---
SCRIPT_DIR = Path(__file__).resolve().parent
TO_PUBLISH = SCRIPT_DIR / "To_Publish"
PUBLISHED_ARCHIVE = SCRIPT_DIR / "Published_Archive"
LOG_FILE = SCRIPT_DIR / "publish_log.txt"

SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET_NAME") or os.environ.get("R2_BUCKET") or "stories"
R2_PUBLIC_URL = (os.environ.get("R2_PUBLIC_URL") or "").rstrip("/")

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".m4b", ".ogg"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

INFO_KEYS = {
    "название": "title", "title": "title",
    "описание": "description", "description": "description",
    "жанры": "genres", "genres": "genres",
    "теги": "tags", "tags": "tags",
    "автор": "author", "author": "author",
    "премиум": "is_premium", "premium": "is_premium", "is_premium": "is_premium",
}


def _log_line(prefix: str, message: str) -> None:
    """Дописывает одну строку в publish_log.txt с датой/временем."""
    try:
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{prefix} - {ts} - {message}\n")
            f.flush()
    except Exception:
        pass


def parse_info_txt(path: Path) -> dict:
    result = {
        "title": "", "description": "", "genres": [], "tags": [], "author": "", "is_premium": False,
    }
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            idx = line.find(": ")
            if idx == -1:
                idx = line.find(":")
            if idx == -1:
                continue
            key_raw = line[:idx].strip().lower()
            value = line[idx + 1:].strip()
            key = INFO_KEYS.get(key_raw)
            if not key:
                continue
            if key == "title":
                result["title"] = value
            elif key == "description":
                result["description"] = value
            elif key == "genres":
                result["genres"] = [s.strip() for s in value.split(",") if s.strip()]
            elif key == "tags":
                result["tags"] = [s.strip() for s in value.split(",") if s.strip()]
            elif key == "author":
                result["author"] = value
            elif key == "is_premium":
                result["is_premium"] = value.lower() in ("1", "true", "да", "yes", "premium")
    return result


def find_story_text(folder: Path) -> str:
    parts = []
    for p in sorted(folder.glob("*.txt")):
        if p.name.lower() == "info.txt":
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            parts.append(f.read())
    return "\n\n".join(parts).strip() if parts else ""


def find_audio(folder: Path) -> Path | None:
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in AUDIO_EXT:
            return f
    return None


def find_image(folder: Path) -> Path | None:
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in IMAGE_EXT:
            return f
    return None


def get_audio_duration_seconds(path: Path) -> int:
    try:
        tag = TinyTag.get(str(path))
        if tag and tag.duration is not None:
            return int(round(tag.duration))
    except Exception:
        pass
    return 0


def get_r2_client():
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY]):
        return None
    return boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


def content_type_for(path: Path) -> str:
    mime = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".ogg": "audio/ogg",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    }
    return mime.get(path.suffix.lower(), "application/octet-stream")


def upload_file_to_r2(client, local_path: Path, content_type: str, key: str) -> str | None:
    try:
        with open(local_path, "rb") as f:
            client.put_object(Bucket=R2_BUCKET, Key=key, Body=f, ContentType=content_type)
        if R2_PUBLIC_URL:
            return f"{R2_PUBLIC_URL}/{key}"
        return None
    except Exception:
        return None


def ensure_dirs():
    TO_PUBLISH.mkdir(parents=True, exist_ok=True)
    PUBLISHED_ARCHIVE.mkdir(parents=True, exist_ok=True)


def publish_one(
    folder: Path,
    supabase: Client,
    r2_client,
    report,
    log_success,
    log_error,
) -> tuple[bool, str | None]:
    """
    Обрабатывает одну папку. Возвращает (True, None) при успехе, (False, причина) при ошибке.
    report(msg) — вывод в консоль GUI; log_success/log_error — запись в publish_log.txt.
    """
    folder_name = folder.name
    info_path = folder / "info.txt"
    meta = parse_info_txt(info_path)

    title = (meta["title"] or folder_name).strip()
    if not title:
        reason = "Нет названия в info.txt и имя папки пустое."
        log_error(f'Папка "{folder_name}". Причина: {reason}')
        return False, reason

    report("  Чтение описания и текста...")
    description = meta["description"] or find_story_text(folder) or ""

    audio_path = find_audio(folder)
    image_path = find_image(folder)
    if not audio_path:
        reason = "Не найден аудиофайл (.mp3, .wav, .m4a и т.д.)."
        log_error(f'Папка "{folder_name}". Причина: {reason}')
        return False, reason
    if not image_path:
        reason = "Не найдено изображение (.jpg, .png и т.д.)."
        log_error(f'Папка "{folder_name}". Причина: {reason}')
        return False, reason
    report(f"  Аудио: {audio_path.name}, обложка: {image_path.name}")

    report("  Загрузка аудио в R2...")
    prefix = f"{int(time.time() * 1000)}"
    safe = re.sub(r"[^\w\-.]", "-", title)[:80]
    audio_key = f"{prefix}-{safe}{audio_path.suffix}"
    image_key = f"{prefix}-{safe}{image_path.suffix}"

    audio_url = upload_file_to_r2(r2_client, audio_path, content_type_for(audio_path), audio_key)
    report("  Аудио в R2: " + (audio_url or "ОШИБКА"))
    report("  Загрузка обложки в R2...")
    image_url = upload_file_to_r2(r2_client, image_path, content_type_for(image_path), image_key)
    report("  Обложка в R2: " + (image_url or "ОШИБКА"))

    if not audio_url or not image_url:
        reason = "Ошибка загрузки медиа в R2."
        log_error(f'Папка "{folder_name}". Причина: {reason}')
        return False, reason

    duration = get_audio_duration_seconds(audio_path)
    genres = meta["genres"] or ["Без жанра"]
    tags = meta["tags"] or []
    report(f"  Длительность: {duration} сек, жанры: {genres}")

    row = {
        "title": title,
        "description": description or None,
        "author": meta["author"] or "",
        "genre": genres[0] if genres else None,
        "genres": genres,
        "tags": tags if tags else None,
        "image_url": image_url,
        "audio_url": audio_url,
        "duration": duration,
        "is_premium": meta["is_premium"],
    }

    report("  Сохранение в Supabase...")
    try:
        supabase.table("stories").insert(row).execute()
        report("  Supabase: запись создана.")
    except Exception as e:
        reason = str(e)
        log_error(f'Папка "{folder_name}". Причина: {reason}')
        return False, reason

    log_success(f'Рассказ "{title}" опубликован.')
    report("  Перенос папки в Published_Archive...")
    dest = PUBLISHED_ARCHIVE / folder.name
    if dest.exists():
        for i in range(1, 1000):
            dest = PUBLISHED_ARCHIVE / f"{folder_name}_{i}"
            if not dest.exists():
                break
    folder.rename(dest)
    return True, None


# --- GUI ---
STATUS_QUEUE: queue.Queue[str | None] = queue.Queue()
SENTINEL = None  # конец работы воркера


def _console(msg: str) -> None:
    """Печать в консоль (и в лог при запуске через start.bat)."""
    print(msg, flush=True)


def worker_run(status_queue: queue.Queue) -> None:
    """Работает в отдельном потоке: публикация папок, сообщения кладёт в status_queue и в консоль."""
    def report(msg: str) -> None:
        status_queue.put(msg)
        _console(msg)

    def log_success(msg: str) -> None:
        _log_line("[УСПЕХ]", msg)
        status_queue.put(f"[Лог] {msg}")
        _console(f"[УСПЕХ] {msg}")

    def log_error(msg: str) -> None:
        _log_line("[ОШИБКА]", msg)
        status_queue.put(f"[Ошибка в лог] {msg}")
        _console(f"[ОШИБКА] {msg}")

    _console("--- Проверка окружения ---")
    ensure_dirs()

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        report("Ошибка: задайте SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY в .env")
        status_queue.put(SENTINEL)
        return

    _console("Подключение к Supabase...")
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        _console("Supabase: OK")
    except Exception as e:
        report(f"Ошибка подключения к Supabase: {e}")
        status_queue.put(SENTINEL)
        return

    _console("Подключение к R2...")
    r2_client = get_r2_client()
    if not r2_client:
        report("Ошибка: задайте R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY в .env")
        status_queue.put(SENTINEL)
        return
    _console("R2: OK")

    folders = [p for p in TO_PUBLISH.iterdir() if p.is_dir()]
    _console(f"Найдено папок в To_Publish: {len(folders)}")
    if not folders:
        report("Нет папок в To_Publish.")
        status_queue.put(SENTINEL)
        return

    ok = 0
    for i, folder in enumerate(folders, 1):
        report(f"[{i}/{len(folders)}] Обработка папки: {folder.name}")
        try:
            success, err = publish_one(folder, supabase, r2_client, report, log_success, log_error)
            if success:
                ok += 1
                report("  -> Успешно опубликовано.")
            else:
                report(f"  -> Ошибка: {err}")
        except Exception as e:
            log_error(f'Папка "{folder.name}". Причина: {e}')
            report(f"  -> Исключение: {e}")
        time.sleep(2)

    report(f"Готово. Опубликовано: {ok} из {len(folders)}.")
    status_queue.put(SENTINEL)


def poll_queue(root, text_widget, btn_start) -> None:
    """Раз в 100 мс забирает сообщения из очереди и выводит в текст."""
    try:
        while True:
            msg = STATUS_QUEUE.get_nowait()
            if msg is SENTINEL:
                text_widget.insert("end", "\n--- Завершено ---\n")
                text_widget.see("end")
                btn_start.config(state="normal")
                return
            text_widget.insert("end", msg + "\n")
            text_widget.see("end")
    except queue.Empty:
        pass
    root.after(100, lambda: poll_queue(root, text_widget, btn_start))


def start_worker(root, text_widget, btn_start) -> None:
    text_widget.delete("1.0", "end")
    btn_start.config(state="disabled")
    thread = threading.Thread(target=worker_run, args=(STATUS_QUEUE,), daemon=True)
    thread.start()
    poll_queue(root, text_widget, btn_start)


def main_gui() -> None:
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title("Автопаблишер рассказов")
    root.minsize(500, 400)
    root.geometry("600x500")

    main = tk.Frame(root, padx=12, pady=12)
    main.pack(fill="both", expand=True)

    btn_start = tk.Button(
        main,
        text="ПУСК",
        command=lambda: start_worker(root, text_console, btn_start),
        font=tkfont.Font(size=18, weight="bold"),
        cursor="hand2",
        height=2,
    )
    btn_start.pack(fill="x", pady=(0, 10))

    text_console = tk.Text(main, wrap="word", state="normal", height=20, font=("Consolas", 10))
    scroll = tk.Scrollbar(main, command=text_console.yview)
    text_console.configure(yscrollcommand=scroll.set)
    text_console.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    root.mainloop()


if __name__ == "__main__":
    import sys
    import traceback

    def _wait():
        try:
            input("\nНажмите Enter, чтобы закрыть окно...")
        except Exception:
            pass

    print("Скрипт автопаблишера запущен.", flush=True)
    print(f"Папка скрипта: {SCRIPT_DIR}", flush=True)
    print(f"To_Publish: {TO_PUBLISH} (существует: {TO_PUBLISH.exists()})", flush=True)
    try:
        print("Запуск окна программы...", flush=True)
        main_gui()
        print("Окно программы закрыто.", flush=True)
    except Exception:
        traceback.print_exc()
        crash_log = SCRIPT_DIR / "autopublisher_crash.txt"
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        print(f"\nОшибка записана в: {crash_log}", flush=True)
        _wait()
        sys.exit(1)
    _wait()
