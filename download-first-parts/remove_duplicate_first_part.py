#!/usr/bin/env python3
"""
Удаляет дубликат первой части в начале файла: когда после шапки идут два раза
один и тот же текст первой главы, оставляет один раз.

Запуск: python remove_duplicate_first_part.py <папка с .txt>
"""

import re
import sys
import time
from pathlib import Path
from difflib import SequenceMatcher

from download_first_parts import (
    HEADER_END_PATTERN,
    HEADER_END_FALLBACK,
    login,
    fetch_text,
    extract_first_part_url,
    find_txt_files,
    is_single_part_story,
    DELAY_SECONDS,
)
import requests

PAGE_BLOCK_PATTERN = re.compile(
    r"─{3,}\s*\n.*?СТРАНИЦА\s+\d+\s+ИЗ\s+\d+.*?https\S*.*?\n.*?─{3,}\s*",
    re.DOTALL | re.IGNORECASE,
)

WINDOW = 400
SIMILARITY = 0.75


def _norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip()).lower()
    return re.sub(r"[^\w\s]", "", s, flags=re.IGNORECASE)


def similar(a: str, b: str, length: int = WINDOW) -> bool:
    x, y = _norm(a)[:length], _norm(b)[:length]
    if not x or not y:
        return False
    if x.startswith(y[:80]) or y.startswith(x[:80]):
        return True
    return SequenceMatcher(None, x, y).ratio() >= SIMILARITY


def find_header_end(content: str) -> int | None:
    m = HEADER_END_PATTERN.search(content)
    if m:
        return m.end()
    m = HEADER_END_FALLBACK.search(content)
    return m.end() if m else None


def find_second_first_part_start(body: str, first_part: str) -> int:
    """Позиция начала второго вхождения первой части в body. Иначе -1."""
    fp = first_part.strip()[:WINDOW]
    # Ищем не с самого начала (первые 200 символов — первая копия), а с 200-го
    for i in range(200, len(body) - 100):
        chunk = body[i : i + WINDOW]
        if similar(chunk, first_part, length=min(WINDOW, len(chunk), len(fp))):
            return i
    return -1


def process_file(session: requests.Session, file_path: Path) -> str:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"error: {e}"

    url = extract_first_part_url(content)
    if not url:
        return "no_url"
    if is_single_part_story(content):
        return "skip"

    header_end = find_header_end(content)
    if header_end is None:
        return "skip"

    body = content[header_end:].lstrip()
    first_part = fetch_text(session, url)
    if not first_part:
        return "error: не удалось скачать первую часть"

    first_part = first_part.strip()
    if not similar(body[:WINDOW], first_part[:WINDOW]):
        return "skip"

    second_start = find_second_first_part_start(body, first_part)
    if second_start < 0:
        return "skip"

    # Конец второй копии: следующий блок СТРАНИЦА или конец первой части по длине
    rest = body[second_start:]
    m = PAGE_BLOCK_PATTERN.search(rest)
    if m:
        second_end = second_start + m.start()
    else:
        second_end = second_start + len(first_part)

    new_body = body[:second_start].rstrip() + "\n\n" + body[second_end:].lstrip()
    new_content = content[:header_end].rstrip() + "\n\n" + new_body + "\n"
    file_path.write_text(new_content, encoding="utf-8")
    return "ok"


def main():
    if len(sys.argv) < 2:
        print("Использование: python remove_duplicate_first_part.py <папка с .txt>")
        return
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print("Укажите папку.")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })

    print("Авторизация...")
    if not login(session):
        print("Логин не удался.")
        return
    print("Логин успешен.\n")

    files = [f for f in find_txt_files(root) if extract_first_part_url(f.read_text(encoding="utf-8", errors="replace"))]
    print(f"Найдено файлов с URL: {len(files)}\n")

    ok = skip = err = 0
    for i, f in enumerate(files, 1):
        rel = f.relative_to(root)
        print(f"[{i}/{len(files)}] {rel} ... ", end="", flush=True)
        r = process_file(session, f)
        if r == "ok":
            print("OK (дубликат убран)")
            ok += 1
        elif r == "skip":
            print("пропуск")
            skip += 1
        else:
            print(r)
            err += 1
        if i < len(files):
            time.sleep(DELAY_SECONDS)

    print(f"\nГотово: исправлено {ok}, пропущено {skip}, ошибок {err}.")


if __name__ == "__main__":
    main()
