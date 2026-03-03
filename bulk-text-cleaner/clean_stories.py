#!/usr/bin/env python3
"""
Массовая очистка текстов рассказов из папки all_stories.
Результат сохраняется в каждой подпапке как clean_text.txt.
"""

import re
import sys
from pathlib import Path

from tqdm import tqdm

# Путь к главной папке: 1) аргумент командной строки, 2) папка all_stories рядом со скриптом
ROOT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "all_stories"
OUTPUT_FILENAME = "clean_text.txt"


def _is_equals_line(line: str) -> bool:
    """Строка состоит только из = и пробелов."""
    s = line.strip()
    return len(s) >= 3 and all(c in "=\s" for c in s) and "=" in s


def _is_header_meta_line(line: str) -> bool:
    """Строка типа «Теги: ...» или «В рассказе: ...» или «Название. Глава N»."""
    s = line.strip().replace("\r", "")
    if not s:
        return False
    low = s.lower()
    if low.startswith("теги:") or low.startswith("в рассказе"):
        return True
    if re.search(r"Глава\s*\d+", s, re.IGNORECASE):
        return True
    return False


def _next_nonempty_is_meta(lines: list, i: int, max_ahead: int = 10) -> bool:
    """Ближайшая следующая непустая строка — Теги: или В рассказе: (текущая может быть названием)."""
    for j in range(i + 1, min(i + 1 + max_ahead, len(lines))):
        n = lines[j].strip().replace("\r", "")
        if not n:
            continue
        low = n.lower()
        if low.startswith("теги:") or low.startswith("в рассказе"):
            return True
        if "теги:" in low or "в рассказе" in low:
            return True
    return False


def remove_technical_header(text: str) -> str:
    """Удалить технический заголовок в начале: название, Теги:, В рассказе:, строки из ===."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip().replace("\r", "")
        if not stripped:
            start = i + 1
            continue
        if _is_equals_line(line) or _is_header_meta_line(line):
            start = i + 1
            continue
        if _next_nonempty_is_meta(lines, i):
            start = i + 1
            continue
        break
    result_lines = lines[start:]
    result_lines = [ln for ln in result_lines if not _is_equals_line(ln)]
    return "\n".join(result_lines)


def remove_page_separators(text: str) -> str:
    """Удалить разделители страниц (─── СТРАНИЦА X ИЗ Y | https://... ───)."""
    pattern = r"─{3,}\s*\n.*?СТРАНИЦА\s+\d+\s+ИЗ\s+\d+.*?\n.*?─{3,}\s*"
    return re.sub(pattern, "\n", text, flags=re.DOTALL | re.IGNORECASE)


# Окно слов: при повторном появлении считаем начало глобального дубликата и отрезаем хвост
TAIL_DEDUP_WINDOW = 40

# Только буквы и цифры для сравнения (без пунктуации и регистра)
_WORD_NORM = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")
_SENTENCE_END = re.compile(r".*[.!?]$")


def _normalize_word_for_cmp(word: str) -> str:
    """Токен для сравнения: извлечь буквы/цифры, в нижний регистр."""
    m = _WORD_NORM.search(word)
    return (m.group(0).lower() if m else "")


def cut_duplicate_tail_by_ngrams(text: str) -> str:
    """
    Универсальная дедупликация по скользящему окну слов (n-grams).
    Текст → сплошной список слов (пунктуация/переносы не влияют на сравнение).
    Окно 40 слов: при повторном вхождении — начало дубликата, отрезаем хвост до конца файла.
    По возможности обрезаем по последней точке перед дублем.
    """
    words = text.split()
    if len(words) < TAIL_DEDUP_WINDOW:
        return text

    norms = [_normalize_word_for_cmp(w) for w in words]
    n = TAIL_DEDUP_WINDOW
    seen: dict[tuple[str, ...], int] = {}

    for i in range(len(norms) - n + 1):
        ngram = tuple(norms[i : i + n])
        if not any(ngram):
            continue
        if ngram in seen:
            j = seen[ngram]
            if j + n <= i:
                cut_index = i
                for pos in range(i - 1, max(0, i - 300), -1):
                    if _SENTENCE_END.match(words[pos].strip()):
                        cut_index = pos + 1
                        break
                out = " ".join(words[:cut_index])
                out = re.sub(r"([.!?])\s+([А-ЯA-Z])", r"\1\n\n\2", out)
                return out
        else:
            seen[ngram] = i

    return text


def _normalize_block(s: str) -> str:
    """Нормализация для сравнения: без лишних пробелов и переносов."""
    return re.sub(r"\s+", " ", s.strip())


def remove_duplicate_paragraphs(text: str) -> str:
    """Удаление подряд идущих одинаковых абзацев (доп. к n-gram дедупу)."""
    paragraphs = text.split("\n\n")
    result = []
    prev = None
    for p in paragraphs:
        stripped = _normalize_block(p)
        if stripped and stripped == prev:
            continue
        if stripped:
            prev = stripped
        else:
            prev = None
        result.append(p)
    return "\n\n".join(result)


def collapse_blank_lines(text: str) -> str:
    """Больше двух подряд пустых строк заменить на одну."""
    return re.sub(r"\n{3,}", "\n\n", text)


# Номер главы в начале строки (Глава 1, Глава 2, ...)
CHAPTER_HEADER = re.compile(r"^\s*Глава\s*(\d+)\s*[.:]?\s*", re.IGNORECASE)


def remove_duplicate_chapters(text: str) -> str:
    """Удалить повторяющиеся блоки глав: если Глава N уже была, второй такой же блок выкинуть."""
    lines = text.split("\n")
    blocks = []  # (chapter_num or None для вступления, список строк)
    current_num = None
    current_lines = []

    for line in lines:
        m = CHAPTER_HEADER.match(line)
        if m:
            if current_num is not None or current_lines:
                blocks.append((current_num, current_lines))
            current_num = int(m.group(1))
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_num is not None or current_lines:
        blocks.append((current_num, current_lines))

    seen = set()
    result = []
    for num, block_lines in blocks:
        if num is None:
            result.extend(block_lines)
            continue
        if num in seen:
            continue
        seen.add(num)
        result.extend(block_lines)

    return "\n".join(result)


# Ссылки и строки с ними удалять
BESTWEAPON_URL_PATTERN = re.compile(r"https://bestweapon\.vip/post\S*", re.IGNORECASE)
ANY_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def remove_bestweapon_links(text: str) -> str:
    """Удалить ссылки на bestweapon.vip/post и строки, в которых они есть."""
    lines = text.split("\n")
    kept = []
    for line in lines:
        if BESTWEAPON_URL_PATTERN.search(line):
            continue
        line = BESTWEAPON_URL_PATTERN.sub("", line).strip()
        kept.append(line)
    return "\n".join(kept)


def remove_sentences_with_urls(text: str) -> str:
    """Удалить предложения, в которых есть любая http(s) ссылка, и сами ссылки."""
    parts = re.split(r"(\s*[.!?]\s*)", text)
    result = []
    i = 0
    while i < len(parts):
        if i % 2 == 0:
            phrase = parts[i]
            if ANY_URL_PATTERN.search(phrase):
                i += 2 if i + 1 < len(parts) else 1
                continue
            cleaned = ANY_URL_PATTERN.sub("", phrase).strip()
            if cleaned:
                result.append(cleaned)
                if i + 1 < len(parts):
                    result.append(parts[i + 1])
            i += 2 if i + 1 < len(parts) else 1
            continue
        i += 1
    out = "".join(result)
    out = ANY_URL_PATTERN.sub("", out)
    return out


def clean_text(raw: str) -> str:
    """Применить все правила очистки."""
    text = remove_technical_header(raw)
    text = remove_page_separators(text)
    text = remove_bestweapon_links(text)
    text = remove_sentences_with_urls(text)
    text = remove_duplicate_chapters(text)
    text = cut_duplicate_tail_by_ngrams(text)
    text = remove_duplicate_paragraphs(text)
    text = collapse_blank_lines(text)
    return text.strip()


def get_subdirs(root: Path):
    """Подпапки первого уровня."""
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir()]


def get_dirty_txt(subdir: Path):
    """Найти .txt в папке, не clean_text.txt."""
    for f in subdir.iterdir():
        if f.is_file() and f.suffix.lower() == ".txt" and f.name != OUTPUT_FILENAME:
            return f
    return None


def run_clean(root_dir: Path, progress_callback=None):
    """
    Обработать все подпапки в root_dir.
    progress_callback(current_index, total, current_folder_name) вызывается при каждой папке.
    Возвращает dict: processed, skipped, errors, total.
    """
    if not root_dir.exists():
        return {"error": f"Папка не найдена: {root_dir}", "processed": 0, "skipped": 0, "errors": 0, "total": 0}

    subdirs = get_subdirs(root_dir)
    total = len(subdirs)
    if total == 0:
        return {"error": "Нет подпапок", "processed": 0, "skipped": 0, "errors": 0, "total": 0}

    processed = 0
    skipped = 0
    errors = 0

    for i, subdir in enumerate(subdirs):
        if progress_callback:
            progress_callback(i + 1, total, subdir.name)

        out_file = subdir / OUTPUT_FILENAME
        src = get_dirty_txt(subdir)
        if not src and out_file.exists():
            src = out_file
        if not src:
            skipped += 1
            continue

        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
            cleaned = clean_text(raw)
            out_file.write_text(cleaned, encoding="utf-8")
            processed += 1
        except Exception as e:
            errors += 1
            if progress_callback:
                progress_callback(i + 1, total, f"Ошибка: {subdir.name} — {e}")

    return {"processed": processed, "skipped": skipped, "errors": errors, "total": total}


def main():
    root = ROOT_DIR
    subdirs = get_subdirs(root)
    if not root.exists():
        print(f"Папка не найдена: {root}")
        return
    if not subdirs:
        print("Нет подпапок.")
        return

    with tqdm(total=len(subdirs), desc="Обработка папок", unit="папка") as pbar:
        def on_progress(current, total, name):
            pbar.n = current
            pbar.set_postfix_str(name[:30])
            pbar.refresh()

        result = run_clean(root, progress_callback=on_progress)

    if result.get("error"):
        print(result["error"])
        return
    print(f"\nГотово. Обработано: {result['processed']}, пропущено: {result['skipped']}, ошибок: {result['errors']}.")


if __name__ == "__main__":
    main()
