#!/usr/bin/env python3
"""
Fix chapter order: header + part 1 (once) + part 2 + part 3 + part 4.
No network, no deps — only stdlib.
Usage: python fix_first_chapter_order.py <folder>
"""

import re
import sys
from pathlib import Path
from difflib import SequenceMatcher

# Локальные копии — без импорта download_first_parts (там requests/bs4, без них скрипт не стартует).
SKIP_FILENAMES = {"clean_text.txt"}
URL_LINE_PATTERN = re.compile(r"URL первой части:\s*(https?://[^\s]+)", re.IGNORECASE)
ALL_PARTS_LINE_PATTERN = re.compile(r"Все части:\s*(https?://[^\s,]+)", re.IGNORECASE)
SINGLE_PART_PATTERNS = [
    re.compile(r"в рассказе:\s*1\s*част", re.IGNORECASE),
    re.compile(r"1\s+из\s+1", re.IGNORECASE),
    re.compile(r"страница\s+1\s+из\s+1", re.IGNORECASE),
]


def find_txt_files(root: Path):
    for f in root.rglob("*.txt"):
        if f.name in SKIP_FILENAMES or "log" in f.name.lower():
            continue
        yield f


def extract_first_part_url(content: str) -> str | None:
    m = URL_LINE_PATTERN.search(content)
    if m:
        return m.group(1).strip()
    m = ALL_PARTS_LINE_PATTERN.search(content)
    return m.group(1).strip() if m else None


def is_single_part_story(content: str) -> bool:
    return any(p.search(content) for p in SINGLE_PART_PATTERNS)


HEADER_END_PATTERN = re.compile(
    r"(?:Все части:|URL первой части:)[^\n]*\n(?:\s*\n){0,2}\s*=+\s*\n\s*",
    re.IGNORECASE,
)
HEADER_END_FALLBACK = re.compile(
    r"(?:Все части:|URL первой части:)[^\n]*\n(?:[^\n]*\n){0,3}=+\s*\n\s*",
    re.IGNORECASE,
)

# Used only for single chunk (small string). Do NOT use .search() on whole body - backtracking.
PAGE_BLOCK_WITH_NUM = re.compile(
    r"([─=]{3,}\s*\n.*?СТРАНИЦА\s+(\d+)\s+ИЗ\s+\d+.*?https\S*.*?\n.*?[─=]{3,}\s*)",
    re.DOTALL | re.IGNORECASE,
)

PART_LINE = re.compile(r"СТРАНИЦА\s+(\d+)\s+ИЗ\s+\d+", re.IGNORECASE)


def _find_block_at(body: str, start_from: int) -> tuple[int | None, int | None, int | None]:
    """Find one PAGE block without scanning whole body. Returns (block_start, part_num, block_end) or (None, None, None)."""
    idx = body.find("СТРАНИЦА ", start_from)
    if idx < 0:
        return (None, None, None)
    m = PART_LINE.search(body, idx, idx + 80)
    if not m:
        return (None, None, None)
    part_num = int(m.group(1))
    line_start = body.rfind("\n", start_from, idx)
    line_start = line_start + 1 if line_start >= 0 else start_from
    prev_nl = body.rfind("\n", start_from, max(start_from, line_start - 1))
    dash_start = prev_nl + 1 if prev_nl >= 0 else start_from
    dash_line_end = body.find("\n", dash_start)
    dash_line = body[dash_start:dash_line_end] if dash_line_end >= 0 else body[dash_start:]
    dash_stripped = dash_line.strip()
    if len(dash_stripped) < 3 or not all(c in "─= \t" for c in dash_stripped) or not any(c in "─=" for c in dash_stripped):
        return (None, None, None)
    block_start = dash_start
    search_after = body.find("\n", idx) + 1
    if search_after <= 0:
        search_after = len(body)
    if "https" not in body[idx : idx + 600]:
        return (None, None, None)
    pos = search_after
    block_end = None
    while pos < len(body):
        next_nl = body.find("\n", pos)
        if next_nl < 0:
            line = body[pos:]
            pos = len(body)
        else:
            line = body[pos:next_nl]
            pos = next_nl + 1
        line = line.strip()
        if len(line) >= 3 and all(c in "─= \t" for c in line) and any(c in "─=" for c in line):
            block_end = next_nl if next_nl >= 0 else len(body)
            break
    if block_end is None:
        return (None, None, None)
    return (block_start, part_num, block_end)

MATCH_LEN = 400
WINDOW_LEN = 350
SIMILARITY_THRESHOLD = 0.72


def _normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip()).lower()
    return re.sub(r"[^\w\s]", "", s, flags=re.IGNORECASE)


def segments_similar(a: str, b: str, length: int = MATCH_LEN) -> bool:
    x = _normalize(a)[:length]
    y = _normalize(b)[:length]
    if not x or not y:
        return False
    if x.startswith(y[:80]) or y.startswith(x[:80]):
        return True
    return SequenceMatcher(None, x, y).ratio() >= SIMILARITY_THRESHOLD


def find_header_end(content: str) -> int | None:
    m = HEADER_END_PATTERN.search(content)
    if m:
        return m.end()
    m = HEADER_END_FALLBACK.search(content)
    return m.end() if m else None


def find_first_part_start_in_text(segment_text: str, first_part: str) -> int:
    fp_start = first_part.strip()[:WINDOW_LEN]
    if not fp_start:
        return -1
    for i in range(0, len(segment_text) - 80):
        chunk = segment_text[i : i + WINDOW_LEN]
        if segments_similar(chunk, first_part, length=min(WINDOW_LEN, len(chunk), len(fp_start))):
            return i
    return -1


def find_first_part_end_in_text(segment_text: str, first_part: str) -> int:
    fp = first_part.strip()
    if len(fp) < 300 or len(segment_text) < 400:
        return -1
    for length in (250, 200, 180, 150):
        tail = fp[-length:].strip()
        if len(tail) < 60:
            continue
        pos = segment_text.find(tail)
        if pos >= 0:
            return pos + len(tail)
    return -1


def parse_segments_from_body(body: str):
    segments = []
    pos = 0
    while pos < len(body):
        block_start, part_num, block_end = _find_block_at(body, pos)
        if block_start is None:
            break
        next_start, _, _ = _find_block_at(body, block_end)
        text_end = next_start if next_start is not None else len(body)
        text = body[block_end:text_end].strip()
        full_chunk = body[block_start:text_end].rstrip()
        segments.append((part_num, full_chunk, text))
        pos = block_end
    return segments


def fix_file(file_path: Path) -> str:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"error: {e}"

    if not extract_first_part_url(content):
        return "no_url"
    if is_single_part_story(content):
        return "skip"

    header_end = find_header_end(content)
    if header_end is None:
        return "skip"

    body = content[header_end:].lstrip()
    block_start, _, _ = _find_block_at(body, 0)
    if block_start is None:
        return "skip"

    body_clean = body[block_start:]
    segments = parse_segments_from_body(body_clean)
    if not segments:
        return "skip"

    first_part_norm = None
    for pn, _, text in segments:
        if pn == 1 and text.strip():
            first_part_norm = text.strip()
            break
    if not first_part_norm:
        return "error: no part 1 in file"

    header = content[:header_end].rstrip()

    processed = []
    for part_num, full_chunk, text in segments:
        if part_num == 1:
            processed.append((part_num, None, None))
            continue
        m_block = PAGE_BLOCK_WITH_NUM.match(full_chunk)
        block = full_chunk[: m_block.end()] if m_block else ""
        if segments_similar(text, first_part_norm):
            processed.append((part_num, None, None))
            continue
        cut_at = find_first_part_start_in_text(text, first_part_norm)
        if cut_at == 0:
            processed.append((part_num, None, None))
            continue
        if cut_at > 0:
            text = text[:cut_at].rstrip()
            full_chunk = block + "\n\n" + text if block else text
        if part_num >= 2 and segments_similar(text[:WINDOW_LEN], first_part_norm[:WINDOW_LEN]):
            end_pos = find_first_part_end_in_text(text, first_part_norm)
            if end_pos > 0:
                text = text[end_pos:].lstrip()
                full_chunk = block + "\n\n" + text if block else text
        processed.append((part_num, full_chunk, text))

    seen_part_num = set()
    out_parts = [header, "", first_part_norm]
    for part_num, full_chunk, text in processed:
        if full_chunk is None:
            continue
        if part_num in seen_part_num:
            continue
        seen_part_num.add(part_num)
        out_parts.append("")
        out_parts.append(full_chunk)

    new_content = "\n\n".join(out_parts) + "\n"
    file_path.resolve().write_text(new_content, encoding="utf-8")
    return "ok"


def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_first_chapter_order.py <folder>")
        return
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print("Not a folder.")
        return

    files = []
    for f in find_txt_files(root):
        try:
            if extract_first_part_url(f.read_text(encoding="utf-8", errors="replace")):
                files.append(f)
        except Exception:
            pass
    print(f"Files with URL: {len(files)}")

    ok = err = skip = 0
    for i, f in enumerate(files, 1):
        rel = f.relative_to(root)
        print(f"[{i}/{len(files)}] {rel} ... ", end="", flush=True)
        result = fix_file(f)
        if result == "ok":
            print("OK")
            ok += 1
        elif result == "skip" or result == "no_url":
            print("skip")
            skip += 1
        else:
            print(result)
            err += 1
    print(f"\nDone: ok={ok}, skip={skip}, err={err}.")


if __name__ == "__main__":
    main()
