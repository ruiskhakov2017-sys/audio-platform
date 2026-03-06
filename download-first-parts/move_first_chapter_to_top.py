#!/usr/bin/env python3
"""
Ставит первую главу на место: вырезает блок СТРАНИЦА 1 ИЗ M + текст до следующего блока
и вставляет сразу после шапки. Остальное не трогает.
Только stdlib. Папка в argv[1].
"""
import re
import sys
from pathlib import Path

SKIP = {"clean_text.txt"}
URL_PATTERN = re.compile(r"URL первой части:|Все части:", re.IGNORECASE)


def has_url(content: str) -> bool:
    return bool(URL_PATTERN.search(content))


def header_end(content: str) -> int:
    """Конец шапки: после строки с URL и следующей строки из = или ─."""
    m = re.search(r"(?:Все части:|URL первой части:)[^\n]*\n(?:[^\n]*\n){0,4}[─=\s]{3,}\s*\n", content, re.IGNORECASE)
    if m:
        return m.end()
    idx = content.find("СТРАНИЦА ")
    if idx != -1:
        return idx  # шапка — всё до первого СТРАНИЦА
    return 0


def find_first_block(content: str) -> tuple[int, int, int] | None:
    """Найти блок СТРАНИЦА 1 ИЗ + текст до следующего блока. (start, end_block, end_text)."""
    m = re.search(r"СТРАНИЦА\s+1\s+ИЗ\s+\d+", content, re.IGNORECASE)
    if not m:
        return None
    idx = m.start()
    # Начало блока — предыдущая строка из ─ или =
    line_start = content.rfind("\n", 0, idx)
    if line_start >= 0:
        line_start += 1
        prev_nl = content.rfind("\n", 0, max(0, line_start - 1))
        dash_start = prev_nl + 1 if prev_nl >= 0 else 0
        dash_line = content[dash_start:line_start].strip()
        if len(dash_line) >= 2 and any(c in "─=" for c in dash_line):
            line_start = dash_start
    else:
        line_start = 0
    # Конец блока — следующая строка из ─ или = после СТРАНИЦА 1
    after = content[idx:]
    end_of_block = idx
    for line in after.split("\n")[1:]:
        end_of_block += len(line) + 1
        s = line.strip()
        if len(s) >= 2 and any(c in "─=" for c in s) and all(c in "─= \t" for c in s):
            break
    else:
        end_of_block = len(content)
    # Текст первой главы — до следующего блока (СТРАНИЦА 2/3/4)
    rest = content[end_of_block:].lstrip()
    next_m = re.search(r"[─=]{2,}\s*\n.*?СТРАНИЦА\s+[2-9]\s+ИЗ|[─=]{2,}\s*\n.*?СТРАНИЦА\s+\d{2,}\s+ИЗ", rest, re.IGNORECASE | re.DOTALL)
    if next_m:
        text_end = end_of_block + next_m.start()
    else:
        text_end = len(content)
    return (line_start, end_of_block, text_end)


def fix_one(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"error: {e}"
    if not has_url(content):
        return "no_url"
    he = header_end(content)
    head = content[:he].rstrip()
    body = content[he:].lstrip()
    if not body.strip():
        return "skip"
    first = find_first_block(content)
    if not first:
        return "skip"
    line_start, end_of_block, text_end = first
    first_chunk = content[line_start:text_end].rstrip()
    if not first_chunk.strip():
        return "skip"
    # Убрать первую главу из текущего места
    before = content[:line_start].rstrip()
    after = content[text_end:].lstrip()
    without_first = (before + "\n\n" + after).strip()
    # Найти конец шапки в уже обрезанном тексте (тот же he, т.к. мы резали ниже)
    he2 = header_end(without_first)
    head2 = without_first[:he2].rstrip()
    body2 = without_first[he2:].lstrip()
    new_content = head2 + "\n\n" + first_chunk + "\n\n" + body2 + "\n"
    path.write_text(new_content, encoding="utf-8")
    return "ok"


def main():
    if len(sys.argv) < 2:
        print("Usage: python move_first_chapter_to_top.py <folder>")
        return
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print("Not a folder.")
        return
    files = []
    for f in root.rglob("*.txt"):
        if f.name in SKIP or "log" in f.name.lower():
            continue
        try:
            if has_url(f.read_text(encoding="utf-8", errors="replace")):
                files.append(f)
        except Exception:
            pass
    print(f"Files: {len(files)}")
    ok = skip = err = 0
    for i, f in enumerate(files, 1):
        rel = f.relative_to(root)
        print(f"[{i}/{len(files)}] {rel} ... ", end="", flush=True)
        r = fix_one(f)
        if r == "ok":
            print("OK")
            ok += 1
        elif r in ("skip", "no_url"):
            print("skip")
            skip += 1
        else:
            print(r)
            err += 1
    print(f"\nDone: ok={ok}, skip={skip}, err={err}")


if __name__ == "__main__":
    main()
