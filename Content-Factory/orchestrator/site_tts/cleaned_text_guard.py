"""
Защита входа TTS: не пропускать явно «мусорный» текст в синтез до Kokoro.

Не заменяет cleaner: только финальная проверка содержимого cleaned_story перед озвучкой.

Одиночные слова «глава» / «рассказ» не блокируются — в художественном тексте это норма;
блокируются URL-подобные фрагменты, фраза «глава рассказа» и отдельные строки-заглушки.
"""
from __future__ import annotations

# Подстроки, характерные для сырого веб/мусора (нижний регистр для поиска).
_URL_LIKE_MARKERS: tuple[str, ...] = (
    "http://",
    "https://",
    "www.",
    ".com/",
    ".net/",
    ".org/",
)

# Целые строки (после strip), которые считаем навигационным мусором.
_JUNK_LINES: frozenset[str] = frozenset({"форум", "ссылка", "форум:", "ссылка:"})


def validate_cleaned_text_for_tts(text: str) -> tuple[bool, str]:
    """
    Возвращает (ok, reason). reason пустой при ok.
    """
    raw = text or ""
    if not raw.strip():
        return False, "empty_cleaned_story_text"
    low = raw.lower()
    for m in _URL_LIKE_MARKERS:
        if m in low:
            return False, f"blocked_substring:{m}"
    if "глава рассказа" in low:
        return False, "blocked_phrase:глава рассказа"
    for line in raw.splitlines():
        s = line.strip().lower()
        if s in _JUNK_LINES:
            return False, f"blocked_line:{s!r}"
    return True, ""
