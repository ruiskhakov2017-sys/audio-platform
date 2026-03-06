#!/usr/bin/env python3
"""
Убирает дубликаты блоков СТРАНИЦА N ИЗ M в файле: оставляет по одному блоку на каждую часть,
собирает файл в порядке частей 1, 2, 3, ... Запуск: python fix_duplicate_parts.py <путь к папке или к .txt>.
"""

import re
import sys
from pathlib import Path

# Такой же паттерн блока, как в download_first_parts.py
PAGE_BLOCK_PATTERN = re.compile(
    r"─{3,}\s*\n.*?СТРАНИЦА\s+(\d+)\s+ИЗ\s+\d+.*?https\S*.*?\n.*?─{3,}\s*",
    re.DOTALL | re.IGNORECASE,
)

HEADER_END_PATTERN = re.compile(
    r"(?:Все части:|URL первой части:)[^\n]*\n\s*=+\s*\n\s*",
    re.IGNORECASE,
)


def find_header_end(content: str) -> int | None:
    m = HEADER_END_PATTERN.search(content)
    return m.end() if m else None


def fix_file(file_path: Path) -> bool:
    content = file_path.read_text(encoding="utf-8", errors="replace")
    header_end = find_header_end(content)
    if header_end is None:
        return False

    body = content[header_end:]
    header = content[:header_end].rstrip()

    # Найти все блоки СТРАНИЦА N ИЗ M и номера частей
    seen_parts: dict[int, tuple[int, int, int]] = {}  # part_num -> (start, block_end, next_start)
    for m in PAGE_BLOCK_PATTERN.finditer(body):
        part_num = int(m.group(1))
        if part_num not in seen_parts:
            start = m.start()
            block_end = m.end()
            seen_parts[part_num] = (start, block_end, len(body))

    if not seen_parts:
        return False

    # Для каждой части: конец текста = начало следующего блока или конец
    part_orders = sorted(seen_parts.keys())
    for i, part_num in enumerate(part_orders):
        start, block_end, _ = seen_parts[part_num]
        next_start = len(body)
        if i + 1 < len(part_orders):
            next_part = part_orders[i + 1]
            next_start = seen_parts[next_part][0]
        seen_parts[part_num] = (start, block_end, next_start)

    # Собрать: заголовок + по порядку часть 1, 2, 3, ...
    parts = []
    for part_num in part_orders:
        start, block_end, next_start = seen_parts[part_num]
        chunk = body[start:next_start].rstrip()
        parts.append(chunk)

    new_content = header + "\n\n" + "\n\n".join(parts) + "\n"
    file_path.write_text(new_content, encoding="utf-8")
    return True


def main():
    if len(sys.argv) < 2:
        print("Использование: python fix_duplicate_parts.py <папка или .txt файл>")
        return
    path = Path(sys.argv[1]).resolve()
    if path.is_file() and path.suffix.lower() == ".txt":
        files = [path]
    elif path.is_dir():
        files = list(path.rglob("*.txt"))
        files = [f for f in files if "clean_text" not in f.name and "log" not in f.name.lower()]
    else:
        print("Укажите папку или .txt файл")
        return

    for f in files:
        print(f"Обработка: {f}")
        if fix_file(f):
            print("  OK")
        else:
            print("  Пропуск (нет шапки или блоков)")


if __name__ == "__main__":
    main()
