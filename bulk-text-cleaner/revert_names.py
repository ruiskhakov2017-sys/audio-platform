#!/usr/bin/env python3
"""
Откат имён файлов после очистки.
В каждой папке рассказа: clean_text.txt (исходный текст) → переименовать в имя рассказа,
файл с именем рассказа (обрезанный) → переименовать в clean_text.txt.
Структура папок: корень → категории → папки рассказов.
"""

import sys
from pathlib import Path

BACKUP_FILENAME = "clean_text.txt"


def get_subdirs(root: Path):
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir()]


def _collect_story_dirs(root_dir: Path) -> list[tuple[Path, Path]]:
    result = []
    for category in get_subdirs(root_dir):
        for story_dir in get_subdirs(category):
            result.append((category, story_dir))
    return result


def run_revert(root_dir: Path):
    """
    В каждой папке рассказа: если есть clean_text.txt и ровно один другой .txt —
    поменять имена местами (исходник в clean_text.txt станет с именем рассказа).
    """
    if not root_dir.exists():
        return {"error": f"Папка не найдена: {root_dir}", "swapped": 0, "skipped": 0, "errors": 0, "total": 0}

    story_dirs = _collect_story_dirs(root_dir)
    total = len(story_dirs)
    if total == 0:
        return {"error": "Нет папок рассказов", "swapped": 0, "skipped": 0, "errors": 0, "total": 0}

    swapped = 0
    skipped = 0
    errors = 0

    for category, story_dir in story_dirs:
        backup_file = story_dir / BACKUP_FILENAME
        txt_files = [f for f in story_dir.iterdir() if f.is_file() and f.suffix.lower() == ".txt"]
        other_txt = [f for f in txt_files if f.name != BACKUP_FILENAME]

        if not backup_file.exists() or len(other_txt) != 1:
            skipped += 1
            continue

        story_file = other_txt[0]
        temp_file = story_dir / "_revert_tmp.txt"

        try:
            story_file.rename(temp_file)
            backup_file.rename(story_file)
            temp_file.rename(backup_file)
            swapped += 1
        except Exception:
            errors += 1
            if temp_file.exists() and not story_file.exists():
                temp_file.rename(story_file)

    return {"swapped": swapped, "skipped": skipped, "errors": errors, "total": total}


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "all_stories"
    result = run_revert(root)
    if result.get("error"):
        print(result["error"])
        return
    print(f"Готово. Поменяно имён: {result['swapped']}, пропущено: {result['skipped']}, ошибок: {result['errors']}.")


if __name__ == "__main__":
    main()
