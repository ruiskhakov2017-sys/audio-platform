from __future__ import annotations

import re

_VOICE_FILE_RE = re.compile(r"^.+__[MFUmfu]\.txt$", re.IGNORECASE)


def _letter_from_ozvuchka_rest(rest: str) -> str:
    """По тексту после «Озвучка:» — M/F/U (Мужчина / Женщина / иначе)."""
    low = rest.lower()
    if "мужчина" in low:
        return "M"
    if "женщина" in low:
        return "F"
    return "U"


def parse_voice_type_mfu(info_txt: str) -> str:
    """
    Читает «Тип голоса: M|F|U» из legacy info.txt (структуру не меняем).
    При нескольких строках берётся последняя распознанная.
    """
    last = "U"
    prefix = "тип голоса:"
    for line in info_txt.splitlines():
        s = line.strip()
        low = s.lower()
        if not low.startswith(prefix):
            continue
        _, _, rest = s.partition(":")
        token = rest.strip().upper()[:1]
        if token in {"M", "F", "U"}:
            last = token
    return last


def resolve_voice_letter_from_info_content(info_txt: str) -> tuple[str, str | None, str]:
    """
    Источник правды для озвучки: строка «Озвучка:» (как в ответе Gemini), иначе «Тип голоса:».

    Возвращает (M|F|U, полная найденная строка для лога или None, предупреждение или пустая строка).
    """
    last_oz_line: str | None = None
    last_oz_letter = "U"
    for line in info_txt.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("озвучка:"):
            _, _, rest = s.partition(":")
            last_oz_line = s
            last_oz_letter = _letter_from_ozvuchka_rest(rest)
    if last_oz_line is not None:
        return last_oz_letter, last_oz_line, ""

    has_voice_type = any(l.strip().lower().startswith("тип голоса:") for l in info_txt.splitlines())
    if has_voice_type:
        letter = parse_voice_type_mfu(info_txt)
        matched_line: str | None = None
        for line in info_txt.splitlines():
            if line.strip().lower().startswith("тип голоса:"):
                matched_line = line.strip()
        return letter, matched_line, ""

    return "U", None, "WARN: в info.txt нет строк «Озвучка:» и «Тип голоса:»; используется U"


def resolve_cleaned_story_txt_path(folder: Path, story_folder_name: str) -> Path:
    """
    Файл очищенного текста для TTS: {story}__[MFU].txt если есть; иначе legacy cleaned_story.txt;
    иначе любой *__[MFU].txt в папке; иначе путь cleaned_story.txt (для сообщения «нет файла»).
    """
    voice_files = [
        folder / f"{story_folder_name}__{c}.txt"
        for c in ("M", "F", "U")
        if (folder / f"{story_folder_name}__{c}.txt").is_file()
    ]
    if len(voice_files) == 1:
        return voice_files[0]
    if len(voice_files) > 1:
        return sorted(voice_files, key=lambda p: p.name.lower())[0]
    legacy = folder / "cleaned_story.txt"
    if legacy.is_file():
        return legacy
    extra = sorted(
        [p for p in folder.glob("*__[mfuMFU].txt") if _VOICE_FILE_RE.match(p.name)],
        key=lambda p: p.name.lower(),
    )
    if extra:
        return extra[0]
    return legacy
