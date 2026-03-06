#!/usr/bin/env python3
"""
Скрипт докачивает недостающие первые части текста с защищённого сайта
и вклеивает их в начало существующих .txt файлов.
"""

import re
import sys
import time
from collections import Counter
from pathlib import Path
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup

# ——— Настройки, синхронизированные с основным парсером (main_new.py) ———
BASE_URL = "https://bestweapon.vip"
LOGIN_ENDPOINT = f"{BASE_URL}/logination.php"

# Логин/пароль как в основном парсере (можешь заменить или вынести в env)
USERNAME = "ruslanv6"
PASSWORD = "UZbP9MBJ"

# Селектор основного текста рассказа (см. main_new._parse_story_page)
TEXT_CONTAINER_SELECTOR = "span#postmaintext"

# Где искать файлы: 1) аргумент командной строки, 2) папка all_stories рядом со скриптом
_DEFAULT_ROOT = Path(__file__).resolve().parent / "all_stories"

# Паттерн строки с URL первой части
URL_LINE_PATTERN = re.compile(
    r"URL первой части:\s*(https?://[^\s]+)",
    re.IGNORECASE,
)
# Строка "Все части: url1, url2, ..." — берём первый URL
ALL_PARTS_LINE_PATTERN = re.compile(
    r"Все части:\s*(https?://[^\s,]+)",
    re.IGNORECASE,
)

SKIP_FILENAMES = {"clean_text.txt"}

# Сообщения об ошибках по-русски (для вывода и отчёта)
ERROR_MSG_RU = {
    "err:no_url": "не найден URL первой части в файле",
    "err:no_header": "не найдена шапка (строка с = после «Все части»/«URL первой части»)",
    "err:fetch": "не удалось скачать страницу",
    "err:no_text": "на странице не найден текст рассказа",
    "err:header_wrong": "шапка определена неверно (текст начинается с СТРАНИЦА 3+)",
    "err:read": "не удалось прочитать файл",
    "err:unknown": "неизвестная ошибка",
}

# Длина фрагмента для сравнения «первая часть уже есть»
SAMPLE_LEN = 80
# Порог похожести (0..1): выше — считаем, что первая часть уже в файле
SIMILARITY_THRESHOLD = 0.75
# Сравниваем только начало текста с началом первой страницы с сайта (единственный надёжный критерий).
FIRST_LINE_COMPARE_CHARS = 220
FIRST_LINE_MATCH_THRESHOLD = 0.88

# Паттерны одной части (проверка до скачивания)
SINGLE_PART_PATTERNS = [
    re.compile(r"в рассказе:\s*1\s*част", re.IGNORECASE),
    re.compile(r"1\s+из\s+1", re.IGNORECASE),
    re.compile(r"страница\s+1\s+из\s+1", re.IGNORECASE),
]

# Блок разделителя страницы: ─── ... СТРАНИЦА ... https ... ───
PAGE_BLOCK_PATTERN = re.compile(
    r"─{3,}\s*\n.*?СТРАНИЦА\s+.+?https\S*.*?\n.*?─{3,}\s*",
    re.DOTALL | re.IGNORECASE,
)

# Ищем конец шапки только в самом начале (шапка ~400–700 символов; в тексте главы бывает «Все части»/«=====»).
HEADER_MAX_SEARCH = 800
# Конец шапки: после "Все части:" / "URL первой части:" сразу идёт строка из = (макс. 2 пустые строки между).
HEADER_END_PATTERN = re.compile(
    r"(?:Все части:|URL первой части:)[^\n]*\n(?:\s*\n){0,2}\s*=+\s*\n\s*",
    re.IGNORECASE,
)
HEADER_END_FALLBACK = re.compile(
    r"(?:Все части:|URL первой части:)[^\n]*\n(?:[^\n]*\n){0,3}=+\s*\n\s*",
    re.IGNORECASE,
)
# В первом блоке СТРАНИЦА N ИЗ M — номер части
FIRST_PAGE_NUM = re.compile(
    r"СТРАНИЦА\s+(\d+)\s+ИЗ\s+\d+",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

DELAY_SECONDS = 2
# Таймаут для логина (сайт может отвечать медленно)
LOGIN_TIMEOUT = 30


def login(session: requests.Session) -> bool:
    """Авторизация, максимально близкая к main_new.BestWeaponScraper.login."""
    try:
        # 1. Проходим age-gate
        resp = session.get(BASE_URL, headers=HEADERS, timeout=LOGIN_TIMEOUT)
        resp.raise_for_status()
        if "age-gate" in resp.text or "age_confirm" in resp.text:
            resp = session.post(
                BASE_URL,
                data={"age_confirm": "1"},
                headers=HEADERS,
                timeout=LOGIN_TIMEOUT,
            )
            resp.raise_for_status()

        # 2. Логин на logination.php
        login_data = {
            "login": USERNAME,
            "password": PASSWORD,
            "submit": "Вход",
        }
        resp = session.post(
            LOGIN_ENDPOINT,
            data=login_data,
            headers=HEADERS,
            timeout=LOGIN_TIMEOUT,
        )
        resp.raise_for_status()

        # 3. Проверяем по главной, как в основном парсере
        check = session.get(BASE_URL, headers=HEADERS, timeout=LOGIN_TIMEOUT)
        check.raise_for_status()
        html = check.text.lower()
        if "logout" in html or "profile" in html or "выход" in html or "профиль" in html:
            return True
        print("Login failed (logout/profile not found).")
        return False
    except Exception as e:
        print(f"Login error: {e}")
        return False


def find_txt_files(root: Path):
    """Найти все .txt кроме clean_text.txt и логов."""
    for f in root.rglob("*.txt"):
        if f.name in SKIP_FILENAMES or "log" in f.name.lower():
            continue
        yield f


def extract_first_part_url(content: str) -> str | None:
    """Извлечь URL первой части: 'URL первой части: https://...' или первый URL из 'Все части: url1, url2, ...'."""
    m = URL_LINE_PATTERN.search(content)
    if m:
        return m.group(1).strip()
    m = ALL_PARTS_LINE_PATTERN.search(content)
    return m.group(1).strip() if m else None


def is_single_part_story(content: str) -> bool:
    """Проверка: рассказ из одной части (В рассказе: 1 частей / СТРАНИЦА 1 ИЗ 1)."""
    return any(p.search(content) for p in SINGLE_PART_PATTERNS)


# Файл явно «только первая часть»: имя/шапка содержат часть 1 / ч1, при этом в рассказе 2+ части — не вставлять.
FILENAME_PART1_PATTERN = re.compile(
    r"часть[_\s]*1|ч1\b|_1_?\d*\.txt$",
    re.IGNORECASE,
)
HEADER_PART1_PATTERN = re.compile(
    r"в рассказе:\s*2\s*част",
    re.IGNORECASE,
)


def file_is_likely_part1_only(content: str, file_path: Path) -> bool:
    """
    True, если по имени файла или шапке видно, что это файл именно первой части (часть 1 / ч1),
    а в рассказе при этом 2+ части. Такие файлы не трогаем — не вставляем в начало ничего.
    """
    if not HEADER_PART1_PATTERN.search(content[:1500]):
        return False
    name = file_path.name.lower()
    return bool(FILENAME_PART1_PATTERN.search(name))


def _normalize_snippet(s: str) -> str:
    """Очистка фрагмента для сравнения: без пунктуации, лишних пробелов, нижний регистр."""
    s = re.sub(r"\s+", " ", s.strip()).lower()
    return re.sub(r"[^\w\s]", "", s, flags=re.IGNORECASE)


def find_header_end(content: str) -> int | None:
    """Позиция сразу после шапки (после строки с = и пустых строк). Ищем только в начале файла."""
    head = content[:HEADER_MAX_SEARCH]
    m = HEADER_END_PATTERN.search(head)
    if m:
        return m.end()
    m = HEADER_END_FALLBACK.search(head)
    return m.end() if m else None


def find_story_start_and_snippet(content: str) -> tuple[str | None, str | None]:
    """
    Найти начало текста рассказа после блока СТРАНИЦА ... https ... и взять первый SAMPLE_LEN символов.
    Возвращает (snippet или None, конец позиции блока для вставки или None).
    """
    m = PAGE_BLOCK_PATTERN.search(content)
    if not m:
        return None, None
    after_block = content[m.end() :].strip()
    after_block = re.sub(r"\s+", " ", after_block)
    snippet = after_block[:SAMPLE_LEN].strip() if after_block else None
    return snippet, m.start()


def snippets_similar(local: str, remote: str) -> bool:
    """Проверка: фрагменты совпадают или очень похожи (difflib + startswith)."""
    a = _normalize_snippet(local)[:SAMPLE_LEN]
    b = _normalize_snippet(remote)[:SAMPLE_LEN]
    if not a or not b:
        return False
    if a.startswith(b[:50]) or b.startswith(a[:50]):
        return True
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD


def snippets_similar_strict(local: str, remote: str, min_len: int, threshold: float) -> bool:
    """Строгое сравнение на длинном куске: для решения «уже есть первая часть»."""
    a = _normalize_snippet(local)[:min_len]
    b = _normalize_snippet(remote)[:min_len]
    if len(a) < min_len * 3 // 4 or len(b) < min_len * 3 // 4:
        return False
    if a.startswith(b[:80]) or b.startswith(a[:80]):
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _normalize_url(u: str) -> str:
    """Один URL для сравнения: без якоря и query."""
    return (u or "").split("#")[0].split("?")[0].strip().rstrip("/")


def _get_first_part_url_from_storylist(soup: BeautifulSoup) -> str | None:
    """
    Из ol#storylist взять URL именно первой части (по порядку чтения).
    На сайте части часто идут в списке как [2-я, 1-я] — первая ссылка в DOM = вторая глава.
    Берём ссылку с минимальным номером post_XXX (часть 1 = меньший id).
    """
    storylist = soup.find("ol", id="storylist")
    if not storylist:
        return None
    candidates: list[tuple[int, str]] = []
    for li in storylist.find_all("li"):
        a = li.find("a", href=re.compile(r"post_(\d+)"))
        if not a:
            continue
        href = (a.get("href") or "").split("#")[0].strip()
        mo = re.search(r"post_(\d+)", href)
        if not href or not mo:
            continue
        if not href.startswith("http"):
            href = f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"
        candidates.append((int(mo.group(1)), href))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _get_first_post_url_from_page(soup: BeautifulSoup) -> str | None:
    """Резерв: первая ссылка на post_XXX на странице (если нет ol#storylist)."""
    for a in soup.find_all("a", href=re.compile(r"post_\d+")):
        href = (a.get("href") or "").split("#")[0].strip()
        if not href:
            continue
        if not href.startswith("http"):
            href = f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"
        return href
    return None


def _extract_text_from_node(node) -> str | None:
    """Собрать текст из контейнера (span/div) как из postmaintext."""
    for comment in node.find_all("div", class_="textcomm"):
        comment.decompose()
    text_parts: list[str] = []
    for p in node.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) < 20:
            continue
        lower = t.lower()
        if any(m in lower for m in ["категории", "добавить", "форум", "рейтинг"]):
            continue
        text_parts.append(t)
    full_text = "\n\n".join(text_parts) if text_parts else node.get_text(
        separator="\n", strip=True
    )
    return full_text.strip() or None


def _cyrillic_ratio(s: str) -> float:
    """Доля кириллицы в строке (для проверки «похоже на текст рассказа»)."""
    if not s:
        return 0.0
    letters = sum(c.isalpha() for c in s)
    cyrillic = sum(1 for c in s if "\u0400" <= c <= "\u04FF")
    return cyrillic / letters if letters else 0.0


# Признаки сайдбара/категорий (не текст рассказа): "Ваши рассказы", "А в попку лучше", счётчики 1234+9
SIDEBAR_MARKERS = (
    "ваши рассказы",
    "а в попку лучше",
    "восемнадцать лет",
    "гетеросексуалы",
    "зрелый возраст",
    "по принуждению",
    "рассказы с фото",
    "сексwife",
    "служебный роман",
    "эротическая сказка",
    "юмористические",
)
# Строка похожа на счётчик категории: много цифр и +
SIDEBAR_COUNTER_PATTERN = re.compile(r"\d{3,}\+\d+")


def _looks_like_sidebar(text: str) -> bool:
    """True, если текст похож на блок категорий/навигации, а не на рассказ."""
    if not text or len(text) < 50:
        return False
    head = text.lower()[:700]
    if any(m in head for m in SIDEBAR_MARKERS):
        return True
    # Несколько вхождений "число+число" (13630+9) — типичный сайдбар
    if len(SIDEBAR_COUNTER_PATTERN.findall(text)) >= 2:
        return True
    # Первые строки выглядят как "Название1234+5"
    lines = text.split("\n")[:15]
    bad = sum(1 for line in lines if SIDEBAR_COUNTER_PATTERN.search(line.strip()))
    if bad >= 2:
        return True
    return False


def _extract_text_from_soup(soup: BeautifulSoup) -> str | None:
    """Из soup извлечь текст рассказа: span#postmaintext или резервные варианты."""
    # 1) основной контейнер
    main_span = soup.find("span", id="postmaintext")
    if main_span:
        out = _extract_text_from_node(main_span)
        if out and len(out) > 50 and not _looks_like_sidebar(out):
            return out
    # 2) id без учёта регистра (на случай postMainText и т.п.)
    main_span = soup.find(id=re.compile(r"postmaintext", re.IGNORECASE))
    if main_span:
        out = _extract_text_from_node(main_span)
        if out and len(out) > 50 and not _looks_like_sidebar(out):
            return out
    # 3) контейнер с большим количеством параграфов (типичное тело поста)
    candidates = soup.find_all(["div", "span"], class_=re.compile(r"post|main|text|content|story", re.IGNORECASE))
    for node in candidates:
        paras = node.find_all("p")
        if len(paras) < 3:
            continue
        out = _extract_text_from_node(node)
        if out and len(out) > 100 and not _looks_like_sidebar(out):
            return out
    # 4) любой элемент с id, содержащим post/text (часто контейнер поста)
    for tag in soup.find_all(id=True):
        tid = (tag.get("id") or "").lower()
        if "post" in tid or "maintext" in tid or "story" in tid:
            out = _extract_text_from_node(tag)
            if out and len(out) > 200 and _cyrillic_ratio(out) > 0.3 and not _looks_like_sidebar(out):
                return out
    # 5) последний резерв: блок с максимум параграфов и кириллицей (типичное тело страницы)
    best: tuple[int, str] = (0, "")
    for div in soup.find_all("div"):
        paras = div.find_all("p")
        if len(paras) < 4:
            continue
        out = _extract_text_from_node(div)
        if not out or len(out) < 200:
            continue
        if _cyrillic_ratio(out) < 0.25:
            continue
        if any(m in out.lower()[:500] for m in ["категории", "добавить в избранное", "рейтинг", "комментари"]):
            continue
        if _looks_like_sidebar(out):
            continue
        if len(out) > best[0]:
            best = (len(out), out)
    if best[1]:
        return best[1]
    # 6) все <p> из body (если контейнер без опознаваемого id/class)
    body = soup.find("body") or soup
    paras = body.find_all("p")
    if len(paras) >= 5:
        parts = []
        for p in paras:
            t = p.get_text(strip=True)
            if len(t) < 15:
                continue
            if any(m in t.lower() for m in ["категории", "добавить", "форум", "рейтинг", "комментари"]):
                continue
            parts.append(t)
        if parts:
            out = "\n\n".join(parts)
            if len(out) > 300 and _cyrillic_ratio(out) > 0.25 and not _looks_like_sidebar(out):
                return out
    return None


def fetch_page(session: requests.Session, url: str) -> tuple[BeautifulSoup | None, str | None]:
    """
    Скачать страницу, вернуть (soup, текст из postmaintext).
    Нужно для разбора storylist и извлечения текста.
    """
    try:
        r = session.get(url, headers=HEADERS, timeout=(4, 8))
        r.raise_for_status()
        raw = r.content
        try:
            html = raw.decode("windows-1251")
        except Exception:
            html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        text = _extract_text_from_soup(soup)
        return soup, text
    except Exception as e:
        print(f"  Ошибка загрузки: {e}")
        return None, None


def fetch_text(session: requests.Session, url: str) -> str | None:
    """Скачать страницу и вернуть только текст (для обратной совместимости вызовов)."""
    _, text = fetch_page(session, url)
    return text


def resolve_first_part_url(session: requests.Session, url_from_file: str) -> tuple[str, str | None]:
    """
    Перейти на настоящую первую часть: по storylist на странице взять первую ссылку.
    Возвращает (url_первой_части, текст_с_этой_страницы или None если пришлось перейти на другой URL).
    """
    soup, text = fetch_page(session, url_from_file)
    if not soup:
        return url_from_file, None
    first_url = _get_first_part_url_from_storylist(soup)
    if not first_url:
        return url_from_file, text
    if _normalize_url(first_url) != _normalize_url(url_from_file):
        return first_url, None
    return url_from_file, text


def process_file(session: requests.Session, file_path: Path, content: str) -> str | bool:
    """
    Обработать один файл (content уже прочитан; проверка на одну часть делается снаружи).
    Возвращает: True — вклеено, False — ошибка, "already_has" — первая часть уже в файле.
    """
    url = extract_first_part_url(content)
    if not url:
        return "err:no_url"

    # Всегда определяем реальную первую часть по storylist на сайте: в файле может быть указан URL "второй" страницы как первой.
    print(" fetch...", end="", flush=True)
    try:
        soup, text = fetch_page(session, url)
        real_first_url = _get_first_part_url_from_storylist(soup) if soup else None
        if real_first_url and _normalize_url(real_first_url) != _normalize_url(url):
            # В файле указан не первый пост — качаем настоящую первую часть
            _, text = fetch_page(session, real_first_url)
        elif not text and soup:
            first_url = real_first_url or _get_first_post_url_from_page(soup)
            if first_url and _normalize_url(first_url) != _normalize_url(url):
                _, text = fetch_page(session, first_url)
        if not text and not ALL_PARTS_LINE_PATTERN.search(content):
            real_first_url, text_from_resolve = resolve_first_part_url(session, url)
            if text_from_resolve is not None:
                text = text_from_resolve
            elif real_first_url != url:
                _, text = fetch_page(session, real_first_url)
        if not soup:
            soup = None
    except Exception as e:
        print(f" {e}", flush=True)
        return "err:fetch"
    if not text:
        return "err:no_text"

    header_end = find_header_end(content)
    if header_end is None:
        return "err:no_header"

    # Шапка не может заходить за первый блок СТРАНИЦА N ИЗ M — иначе в шапку попадёт глава и порядок сломается.
    first_block = PAGE_BLOCK_PATTERN.search(content)
    if first_block and first_block.start() < header_end:
        header_end = first_block.start()

    # Не вставлять второй раз только если после шапки УЖЕ идёт первая глава (а не вторая).
    body_after_header = content[header_end:].lstrip()
    first_block_in_body = PAGE_BLOCK_PATTERN.search(content[header_end:])
    first_part_num = None
    if first_block_in_body:
        nm = FIRST_PAGE_NUM.search(first_block_in_body.group(0))
        if nm:
            first_part_num = int(nm.group(1))
    # Единственный критерий «уже есть первая часть»: начало текста в файле совпадает с началом первой страницы на сайте.
    # Блок «СТРАНИЦА 1 ИЗ» в файле не учитываем — под ним может быть ошибочно вставлена вторая часть.
    body_after_block_raw = content[header_end:][first_block_in_body.end():].strip() if first_block_in_body else content[header_end:].lstrip()
    body_start = re.sub(r"\s+", " ", body_after_block_raw)[:FIRST_LINE_COMPARE_CHARS]
    fetched_start = re.sub(r"\s+", " ", text.strip())[:FIRST_LINE_COMPARE_CHARS]
    if len(body_start) >= 80 and len(fetched_start) >= 80:
        a, b = _normalize_snippet(body_start), _normalize_snippet(fetched_start)
        if a[:80] == b[:80] or SequenceMatcher(None, a[:FIRST_LINE_COMPARE_CHARS], b[:FIRST_LINE_COMPARE_CHARS]).ratio() >= FIRST_LINE_MATCH_THRESHOLD:
            return "already_has"

    # Не вставлять первую главу перед 3-й/4-й: если тело после шапки начинается с СТРАНИЦА 3+, шапка посчитана неверно.
    body_start_line = body_after_header[:80].strip()
    if re.match(r"СТРАНИЦА\s+[3-9]\s+ИЗ|СТРАНИЦА\s+\d{2,}\s+ИЗ", body_start_line, re.IGNORECASE):
        return "err:header_wrong"

    new_content = content[:header_end].rstrip() + "\n\n" + text.strip() + "\n\n" + content[header_end:].lstrip()
    file_path.write_text(new_content, encoding="utf-8")
    return True


def main():
    root_dir = Path(str(sys.argv[1]).strip().strip('"')).resolve() if len(sys.argv) > 1 else _DEFAULT_ROOT
    print(f"Folder: {root_dir}", flush=True)
    if not root_dir.exists():
        print(f"ERROR: Folder not found: {root_dir}", flush=True)
        return
    if not root_dir.is_dir():
        print(f"ERROR: Not a folder: {root_dir}", flush=True)
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Login... (timeout {LOGIN_TIMEOUT}s)", flush=True)
    if not login(session):
        print("Login failed. Check USERNAME, PASSWORD.", flush=True)
        return
    print("Login OK.", flush=True)
    print(f"Folder: {root_dir}\n", flush=True)

    files = list(find_txt_files(root_dir))
    to_process = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            if extract_first_part_url(content):
                to_process.append(f)
        except Exception:
            pass

    print(f"Found files: {len(to_process)}. Processing...", flush=True)

    report_inserted: list[Path] = []
    report_skipped_single: list[Path] = []
    report_skipped_already: list[Path] = []
    report_errors: list[tuple[Path, str]] = []

    for i, f in enumerate(to_process, 1):
        rel = f.relative_to(root_dir)
        print(f"[{i}/{len(to_process)}] {rel} ...", end="", flush=True)
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            print("ERROR (read)")
            report_errors.append((f, "err:read"))
            continue
        if is_single_part_story(content):
            print("SKIP (1 part only)")
            report_skipped_single.append(f)
            continue
        if file_is_likely_part1_only(content, f):
            print("SKIP (file is part 1)")
            report_skipped_already.append(f)
            continue
        result = process_file(session, f, content)
        if result is True:
            print("OK")
            report_inserted.append(f)
        elif result == "already_has":
            print("SKIP (already has part 1)")
            report_skipped_already.append(f)
        elif isinstance(result, str) and result.startswith("err:"):
            print(result)
            report_errors.append((f, result))
        else:
            print("ERROR")
            report_errors.append((f, "err:unknown"))
        if i < len(to_process):
            time.sleep(DELAY_SECONDS)

    def rel_path(p: Path) -> str:
        return str(p.relative_to(root_dir))

    # Сводка по ошибкам для итога
    err_counts = Counter(code for _, code in report_errors)
    err_details = "; ".join(
        f"{ERROR_MSG_RU.get(c, c)} — {n}" for c, n in err_counts.most_common()
    ) if report_errors else "—"

    # Итог: в логах — только изменённые файлы (главное для пользователя).
    print("")
    print("=" * 60, flush=True)
    print("ИЗМЕНЕНО (добавлена первая глава):", flush=True)
    print("=" * 60, flush=True)
    if report_inserted:
        for p in report_inserted:
            print(rel_path(p), flush=True)
        print(f"\nВсего: {len(report_inserted)} файл(ов).", flush=True)
    else:
        print("(нет)", flush=True)
    print("=" * 60, flush=True)
    summary = (
        f"Пропущено (одна часть): {len(report_skipped_single)}. "
        f"Пропущено (уже есть первая часть): {len(report_skipped_already)}. "
        f"Ошибки: {len(report_errors)}."
    )
    print(summary, flush=True)
    if report_errors:
        print(f"  Причины ошибок: {err_details}", flush=True)
    print("=" * 60, flush=True)
    sys.stdout.flush()

    # В файл отчёта пишем то же: сначала список изменённых, потом краткая сводка.
    lines = [
        "",
        "=" * 60,
        "ИЗМЕНЕНО (добавлена первая глава)",
        "=" * 60,
    ]
    if report_inserted:
        for p in report_inserted:
            lines.append(rel_path(p))
        lines.append(f"\nВсего: {len(report_inserted)} файл(ов).")
    else:
        lines.append("(нет)")
    lines.append("")
    lines.append(summary)
    if report_errors:
        lines.append(f"Причины ошибок: {err_details}")
        # Разбивка по коду ошибки и файлам
        by_code: dict[str, list[Path]] = {}
        for p, code in report_errors:
            by_code.setdefault(code, []).append(p)
        for code in err_counts:
            msg = ERROR_MSG_RU.get(code, code)
            lines.append(f"  {msg}:")
            for p in by_code[code]:
                lines.append(f"    {rel_path(p)}")
    lines.append("=" * 60)
    report_text = "\n".join(lines)
    report_file = Path(__file__).resolve().parent / "report_download_first_parts.txt"
    try:
        report_file.write_text(report_text, encoding="utf-8")
        print(f"\nReport: {report_file}", flush=True)
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
