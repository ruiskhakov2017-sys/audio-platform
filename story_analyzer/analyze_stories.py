#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт глубокого анализа библиотеки эротических рассказов (txt).
Оценка через локальную LLM (Ollama, модель dolphin-mistral) для отбора лучших для аудио-озвучки.
"""

import csv
import json
import re
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
INPUT_DIR = Path(__file__).resolve().parent / "input_stories"
CSV_FILENAME = "analysis_results.csv"  # в каждой папке с рассказами свой CSV
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "dolphin-mistral"
TEMPERATURE = 0.3
MAX_TEXT_LENGTH = 12000  # ограничение длины текста для запроса (токены ограничены)

SYSTEM_PROMPT = """ТЫ — ГЛАВНЫЙ РЕДАКТОР ЭРОТИЧЕСКОГО ИЗДАТЕЛЬСТВА С 25-ЛЕТНИМ СТАЖЕМ.
У тебя 15 лет личного опыта написания бестселлеров в жанре эротики. Ты ненавидишь примитивные сюжеты, скучный слог и "картонных" персонажей. Твоя задача — найти бриллианты для аудио-адаптации.

КРИТЕРИИ ОЦЕНКИ (Все шкалы строго от 0 до 100):

1. EROTIC_SCORE (0-100): Насколько рассказ возбуждает?
   - 0-40: Скучно, анатомично, без эмоций или вульгарно-глупо.
   - 41-70: Стандартная эротика, "на один раз".
   - 71-100: Шедевр. Высокое напряжение, психология, химия между персонажами, оригинальные фетиши.

2. AUDIO_SCORE (0-100): Насколько это подходит для озвучки?
   - Оценивай ритм текста, наличие живых диалогов.
   - Текст не должен быть перегружен сложными деепричастными оборотами. Он должен "литься" как песня.

3. GRAMMAR_SCORE (0-100): Литературное качество.
   - Богатый ли язык? Нет ли тавтологий?

ТВОЯ ЗАДАЧА:
Верни ответ ТОЛЬКО в формате JSON:
{
  "summary": "Краткое описание сюжета на 5-7 предложений для описания аудиорассказа: завязка, развитие, кульминация.",
  "erotic_score": int (0-100),
  "audio_score": int (0-100),
  "grammar_score": int (0-100),
  "tags": ["список", "тегов", "жанр", "фетиши"],
  "verdict": "Краткий комментарий редактора (почему это стоит или не стоит озвучивать)"
}"""

USER_PROMPT_TEMPLATE = """Оцени этот рассказ по критериям выше. Верни ТОЛЬКО валидный JSON без markdown и пояснений.

ТЕКСТ РАССКАЗА:
---
{text}
---"""


def get_txt_files(folder: Path) -> list[Path]:
    """Возвращает список .txt файлов только в этой папке (без подпапок)."""
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.txt"))


def get_category_folders(root: Path) -> list[Path]:
    """
    Возвращает папки с рассказами: подпапки root с .txt и сам root, если в нём есть .txt.
    В каждой такой папке будет свой analysis_results.csv.
    """
    folders = []
    if get_txt_files(root):
        folders.append(root)
    for item in sorted(root.iterdir()):
        if item.is_dir() and get_txt_files(item):
            folders.append(item)
    return folders


def load_text(path: Path) -> str:
    """Загружает текст из файла с поддержкой кодировок."""
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Не удалось декодировать файл: {path}")


def truncate_for_prompt(text: str, max_len: int = MAX_TEXT_LENGTH) -> str:
    """Обрезает текст до допустимой длины (по символам, грубая оценка токенов)."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n[... текст обрезан ...]"


def extract_json_from_response(raw: str) -> str:
    """Пытается извлечь JSON из ответа (убирает markdown, лишний текст)."""
    raw = raw.strip()
    # Убрать обёртку ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        raw = match.group(1).strip()
    # Найти первый { и последний }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return raw


def fix_common_json_issues(s: str) -> str:
    """Простые правки типичных ошибок в JSON от LLM."""
    # Убрать trailing comma перед ] или }
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # Заменить одинарные кавычки на двойные (осторожно: только в ключах/значениях)
    # Не делаем глобально, чтобы не сломать текст с апострофами. Оставим как есть.
    return s


def parse_llm_response(raw: str) -> dict | None:
    """
    Парсит ответ LLM в словарь. При битом JSON пробует восстановить или вернуть None.
    """
    raw = extract_json_from_response(raw)
    raw = fix_common_json_issues(raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Попытка вытащить числа по регуляркам и собрать минимальный объект
    try:
        er = re.search(r'"erotic_score"\s*:\s*(\d+)', raw)
        au = re.search(r'"audio_score"\s*:\s*(\d+)', raw)
        gr = re.search(r'"grammar_score"\s*:\s*(\d+)', raw)
        sm = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        vd = re.search(r'"verdict"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        tags_match = re.search(r'"tags"\s*:\s*\[(.*?)\]', raw, re.DOTALL)
        tags = []
        if tags_match:
            tag_str = tags_match.group(1)
            for m in re.finditer(r'"([^"]*)"', tag_str):
                tags.append(m.group(1))

        return {
            "summary": sm.group(1) if sm else "",
            "erotic_score": int(er.group(1)) if er else 0,
            "audio_score": int(au.group(1)) if au else 0,
            "grammar_score": int(gr.group(1)) if gr else 0,
            "tags": tags,
            "verdict": vd.group(1) if vd else "",
        }
    except Exception:
        return None


def call_ollama(text: str) -> str | None:
    """Отправляет запрос в Ollama, возвращает содержимое ответа или None при ошибке."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
        ],
        "stream": False,
        "options": {"temperature": TEMPERATURE},
        "format": "json",
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        return (data.get("message") or {}).get("content")
    except requests.RequestException as e:
        print(f"  [Ошибка запроса] {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [Ошибка] {e}", file=sys.stderr)
        return None


def analyze_file(path: Path) -> dict | None:
    """
    Анализирует один файл: загружает текст, вызывает Ollama, парсит JSON.
    Возвращает словарь для строки CSV или None при неудаче.
    """
    try:
        text = load_text(path)
    except Exception as e:
        print(f"  [Ошибка чтения] {e}", file=sys.stderr)
        return None

    text = truncate_for_prompt(text)
    raw = call_ollama(text)
    if not raw:
        return None

    parsed = parse_llm_response(raw)
    if not parsed:
        print(f"  [Ошибка] Не удалось разобрать JSON, пропуск.", file=sys.stderr)
        return None

    return {
        "Filename": path.name,
        "Erotic_Score": parsed.get("erotic_score", 0),
        "Audio_Score": parsed.get("audio_score", 0),
        "Grammar_Score": parsed.get("grammar_score", 0),
        "Summary": parsed.get("summary", ""),  # описание для аудио (5–7 предложений)
        "Tags": " | ".join(parsed.get("tags") or []),
        "Verdict": parsed.get("verdict", ""),
    }


def ensure_csv_header(csv_path: Path):
    """Создаёт файл с заголовками, если его ещё нет."""
    if not csv_path.exists():
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "Filename",
                    "Erotic_Score",
                    "Audio_Score",
                    "Grammar_Score",
                    "Summary",
                    "Tags",
                    "Verdict",
                ],
            )
            w.writeheader()


def append_result(csv_path: Path, row: dict):
    """Добавляет одну строку в CSV."""
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "Filename",
                "Erotic_Score",
                "Audio_Score",
                "Grammar_Score",
                "Summary",
                "Tags",
                "Verdict",
            ],
        )
        w.writerow(row)


def main():
    script_dir = Path(__file__).resolve().parent
    input_dir = script_dir / "input_stories"

    if not input_dir.is_dir():
        print(f"Папка не найдена: {input_dir}", file=sys.stderr)
        sys.exit(1)

    category_folders = get_category_folders(input_dir)
    if not category_folders:
        print("В input_stories нет папок с .txt файлами (ни в корне, ни в подпапках).")
        return

    total_done = 0
    total_skipped = 0
    for cat_dir in category_folders:
        files = get_txt_files(cat_dir)
        output_csv = cat_dir / CSV_FILENAME
        print(f"\n--- Категория: {cat_dir.name} ({len(files)} файлов) → {output_csv}")
        ensure_csv_header(output_csv)
        done = 0
        skipped = 0
        for i, path in enumerate(files, 1):
            print(f"  [{i}/{len(files)}] {path.name} ... ", end="", flush=True)
            row = analyze_file(path)
            if row:
                append_result(output_csv, row)
                summary_preview = (row["Summary"] or "").replace("\n", " ").strip()[:120]
                if len((row.get("Summary") or "").replace("\n", " ")) > 120:
                    summary_preview += "..."
                print(
                    f"E:{row['Erotic_Score']} A:{row['Audio_Score']} G:{row['Grammar_Score']} | Описание: {summary_preview}"
                )
                done += 1
            else:
                print("пропущен")
                skipped += 1
        total_done += done
        total_skipped += skipped
        print(f"  → В папку записано: {output_csv} ({done} строк)")

    print(f"\nГотово. Всего обработано: {total_done}, пропущено: {total_skipped}.")


if __name__ == "__main__":
    main()
