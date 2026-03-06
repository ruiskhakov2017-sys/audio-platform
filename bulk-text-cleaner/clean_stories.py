#!/usr/bin/env python3
"""
Массовая очистка текстов рассказов.
Структура: корень → папки категорий → папки рассказов (в каждой .txt).
Очищенный текст сохраняется под исходным именем файла, исходник переименовывается в clean_text.txt.
"""

import re
import sys
from pathlib import Path

from tqdm import tqdm

# Путь к главной папке: 1) аргумент командной строки, 2) папка all_stories рядом со скриптом
ROOT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "all_stories"
# Имя файла, в который переименовывается исходный текст (очищенный получает имя исходного)
BACKUP_FILENAME = "clean_text.txt"


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


# Строка целиком — только «СТРАНИЦА N ИЗ M» с опциональным | и URL
PAGE_HEADER_LINE = re.compile(
    r"^\s*СТРАНИЦА\s+\d+\s+ИЗ\s+\d+\s*(\|\s*.*?)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# В начале строки: «СТРАНИЦА N ИЗ M | » (если склеено с текстом)
PAGE_HEADER_PREFIX = re.compile(
    r"^\s*СТРАНИЦА\s+\d+\s+ИЗ\s+\d+\s*\|?\s*",
    re.IGNORECASE | re.MULTILINE,
)


def remove_page_separators(text: str) -> str:
    """Удалить разделители страниц: блоки с ───, отдельные строки «СТРАНИЦА N ИЗ M», префикс в начале строки."""
    pattern = r"─{3,}\s*\n.*?СТРАНИЦА\s+\d+\s+ИЗ\s+\d+.*?\n.*?─{3,}\s*"
    text = re.sub(pattern, "\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = PAGE_HEADER_LINE.sub("", text)
    text = PAGE_HEADER_PREFIX.sub("", text)
    return text


# Окно слов для поиска дубликатов: режем только если подряд идёт несколько совпадений (реальный дубль)
TAIL_DEDUP_WINDOW = 40
# Сколько подряд идущих n-грамм должны совпасть, чтобы считать это дубликатом (не случайной фразой)
TAIL_DEDUP_MIN_RUN = 2

# Только буквы и цифры для сравнения (без пунктуации и регистра)
_WORD_NORM = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")
_SENTENCE_END = re.compile(r".*[.!?]$")


def _normalize_word_for_cmp(word: str) -> str:
    """Токен для сравнения: извлечь буквы/цифры, в нижний регистр."""
    m = _WORD_NORM.search(word)
    return (m.group(0).lower() if m else "")


def cut_duplicate_tail_by_ngrams(text: str) -> str:
    """
    Удаляем только повторяющийся блок: когда подряд несколько окон по 40 слов
    совпадают с более ранним куском, вырезаем именно этот дубль (второе вхождение),
    а не всё до конца рассказа. Остальной текст сохраняем.
    """
    words = text.split()
    n = TAIL_DEDUP_WINDOW
    if len(words) < n + TAIL_DEDUP_MIN_RUN - 1:
        return text

    norms = [_normalize_word_for_cmp(w) for w in words]
    seen: dict[tuple[str, ...], int] = {}

    for i in range(len(norms) - n + 1):
        ngram = tuple(norms[i : i + n])
        if not any(ngram):
            continue
        if ngram not in seen:
            seen[ngram] = i
            continue
        j = seen[ngram]
        if j + n > i:
            continue
        run = 1
        while run < TAIL_DEDUP_MIN_RUN and i + run + n <= len(norms) and j + run + n <= len(norms):
            ngram_i = tuple(norms[i + run : i + run + n])
            ngram_j = tuple(norms[j + run : j + run + n])
            if ngram_i != ngram_j or not any(ngram_i):
                break
            run += 1
        if run < TAIL_DEDUP_MIN_RUN:
            continue
        # Длина дубликата в словах: n + (run - 1)
        block_len = n + (run - 1)
        # Начало блока — по границе предложения
        block_start = i
        for pos in range(i - 1, max(0, i - 300), -1):
            if _SENTENCE_END.match(words[pos].strip()):
                block_start = pos + 1
                break
        # Конец блока — по границе предложения (не режем предложение)
        block_end = min(block_start + block_len, len(words))
        for pos in range(block_end, min(block_end + 100, len(words))):
            if _SENTENCE_END.match(words[pos].strip()):
                block_end = pos + 1
                break
        # Вырезаем только дубль, остальное склеиваем
        out_words = words[:block_start] + words[block_end:]
        out = " ".join(out_words)
        out = re.sub(r"([.!?])\s+([А-ЯA-Z])", r"\1\n\n\2", out)
        return out

    return text


def _normalize_block(s: str) -> str:
    """Нормализация для сравнения: без лишних пробелов и переносов."""
    return re.sub(r"\s+", " ", s.strip())


DUPLICATE_BLOCK_MIN_PARAS = 3


def remove_duplicate_paragraph_sequences(text: str) -> str:
    """Удалить блоки из 3+ параграфов, которые повторяются в тексте (второй раз вырезаем)."""
    paras = [p for p in text.split("\n\n") if p.strip()]
    if len(paras) < DUPLICATE_BLOCK_MIN_PARAS * 2:
        return text
    norms = [_normalize_block(p) for p in paras]
    for run in range(min(20, len(paras) - DUPLICATE_BLOCK_MIN_PARAS), DUPLICATE_BLOCK_MIN_PARAS - 1, -1):
        if run > len(paras) // 2:
            continue
        tail_start = len(paras) - run
        chunk = tuple(norms[tail_start : tail_start + run])
        for i in range(len(norms) - run):
            if i + run > tail_start:
                break
            if tuple(norms[i : i + run]) == chunk:
                new_paras = paras[:tail_start] + paras[tail_start + run :]
                return "\n\n".join(new_paras)
    return text


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
    """
    Если одна и та же глава (номер N) встречается несколько раз — оставляем один блок с этим номером:
    тот, где больше текста (реальная глава). Дубликаты и короткие повторы выкидываем. Порядок глав сохраняем.
    """
    lines = text.split("\n")
    blocks = []
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

    # По каждому номеру главы выбираем один блок — самый длинный (реальная глава, не дубль)
    best_by_num: dict[int, list[str]] = {}
    for num, block_lines in blocks:
        if num is None:
            continue
        length = sum(len(l) for l in block_lines)
        if num not in best_by_num or length > sum(len(l) for l in best_by_num[num]):
            best_by_num[num] = block_lines

    # Собираем результат в исходном порядке: вступление, потом первое появление каждой главы (берём выбранный блок)
    result = []
    seen_nums = set()
    for num, block_lines in blocks:
        if num is None:
            # Вступление — может быть несколько кусков подряд
            result.extend(block_lines)
            continue
        if num in seen_nums:
            continue
        seen_nums.add(num)
        result.extend(best_by_num[num])

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
    # Удаляет только найденный дубль (блок слов), не режет до конца рассказа
    text = cut_duplicate_tail_by_ngrams(text)
    text = remove_duplicate_paragraphs(text)
    text = remove_duplicate_paragraph_sequences(text)
    text = collapse_blank_lines(text)
    return text.strip()


def get_subdirs(root: Path):
    """Подпапки первого уровня."""
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir()]


def get_dirty_txt(subdir: Path):
    """Найти .txt в папке (исходник рассказа), не clean_text.txt."""
    for f in subdir.iterdir():
        if f.is_file() and f.suffix.lower() == ".txt" and f.name != BACKUP_FILENAME:
            return f
    return None


def _collect_story_dirs(root_dir: Path) -> list[tuple[Path, Path]]:
    """Собрать пары (категория, папка рассказа): корень → категории → папки рассказов."""
    result = []
    for category in get_subdirs(root_dir):
        for story_dir in get_subdirs(category):
            result.append((category, story_dir))
    return result


def run_clean(root_dir: Path, progress_callback=None):
    """
    Обработать структуру: root_dir → категории → папки рассказов.
    В каждой папке рассказа: исходный .txt переименовывается в clean_text.txt,
    очищенный текст записывается под исходным именем файла.
    progress_callback(current_index, total, current_folder_name).
    Возвращает dict: processed, skipped, errors, total.
    """
    if not root_dir.exists():
        return {"error": f"Папка не найдена: {root_dir}", "processed": 0, "skipped": 0, "errors": 0, "total": 0}

    story_dirs = _collect_story_dirs(root_dir)
    total = len(story_dirs)
    if total == 0:
        return {"error": "Нет папок рассказов (ожидается: корень → категории → папки с .txt)", "processed": 0, "skipped": 0, "errors": 0, "total": 0}

    processed = 0
    skipped = 0
    errors = 0

    for i, (category, story_dir) in enumerate(story_dirs):
        display_name = f"{category.name} / {story_dir.name}"
        if progress_callback:
            progress_callback(i + 1, total, display_name)

        src = get_dirty_txt(story_dir)
        if not src:
            skipped += 1
            continue

        backup_file = story_dir / BACKUP_FILENAME
        original_name = src.name

        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
            cleaned = clean_text(raw)
            # Исходник → clean_text.txt, очищенный → под именем исходного файла
            if backup_file.exists():
                backup_file.unlink()
            src.rename(backup_file)
            (story_dir / original_name).write_text(cleaned, encoding="utf-8")
            processed += 1
        except Exception as e:
            errors += 1
            if progress_callback:
                progress_callback(i + 1, total, f"Ошибка: {display_name} — {e}")

    return {"processed": processed, "skipped": skipped, "errors": errors, "total": total}


def main():
    root = ROOT_DIR
    if not root.exists():
        print(f"Папка не найдена: {root}")
        return
    story_dirs = _collect_story_dirs(root)
    if not story_dirs:
        print("Нет папок рассказов. Ожидается: корень → категории → папки с .txt")
        return

    with tqdm(total=len(story_dirs), desc="Обработка папок", unit="папка") as pbar:
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
