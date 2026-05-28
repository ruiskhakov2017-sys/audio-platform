#!/usr/bin/env python3
"""
Скрипт докачивает недостающие первые части текста с защищённого сайта
и вклеивает их в начало существующих .txt файлов.
"""

import re
import sys
import time
import json
import csv
from collections import Counter
from pathlib import Path
from difflib import SequenceMatcher
import concurrent.futures as cf

import requests
from bs4 import BeautifulSoup

# ——— Настройки, синхронизированные с основным парсером (main_new.py) ———
BASE_URL = "https://bestweapon.vip"
ALT_BASE_URLS = ["https://bestweapon.in"]
ALL_BASE_URLS = [BASE_URL] + ALT_BASE_URLS

def _login_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/logination.php"

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
    "err:unresolved": "не удалось сопоставить пару часть2->часть1 (см. unresolved.csv)",
    "err:unknown": "неизвестная ошибка",
}

# Длина фрагмента для сравнения «первая часть уже есть»
SAMPLE_LEN = 80
# Порог похожести (0..1): выше — считаем, что первая часть уже в файле
SIMILARITY_THRESHOLD = 0.75
# Сравниваем только начало текста с началом первой страницы с сайта (единственный надёжный критерий).
FIRST_LINE_COMPARE_CHARS = 220
FIRST_LINE_MATCH_THRESHOLD = 0.88
MAX_SECONDS_PER_FILE = 45

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
HAS_PAGE1_BLOCK_PATTERN = re.compile(r"СТРАНИЦА\s+1\s+ИЗ\s+\d+", re.IGNORECASE)
PAGE_HEADER_WITH_URL_PATTERN = re.compile(
    r"СТРАНИЦА\s+(?P<page>\d+)\s+ИЗ\s+(?P<total>\d+)\s*\|\s*(?P<url>https?://\S+)",
    re.IGNORECASE,
)
PREV_STORY_URL_IN_TEXT_PATTERN = re.compile(
    r"(?:до|что\s+случилось\s+до)\s*:\s*(https?://[^\s]*post_\d+)",
    re.IGNORECASE,
)
POST_ID_FROM_URL_PATTERN = re.compile(r"post_(\d+)", re.IGNORECASE)
PART2_IN_TITLE_PATTERN = re.compile(r"(?:част[ьи]\s*2|глава\s*2|\bч\.\s*2\b)", re.IGNORECASE)
PART1_IN_TITLE_PATTERN = re.compile(r"(?:част[ьи]\s*1|глава\s*1|\bч\.\s*1\b)", re.IGNORECASE)
PART_NUM_PATTERN = re.compile(r"(?:част[ьи]|глава|ч\.)\s*(\d+)", re.IGNORECASE)
PART_SUFFIX_CLEAN_PATTERN = re.compile(r"(?:[-–—:]?\s*(?:част[ьи]|глава)\s*\d+.*)$", re.IGNORECASE)
TITLE_CLEAN_CHARS_PATTERN = re.compile(r"[^\w\s]+", re.IGNORECASE)
SITE_SUFFIX_PATTERN = re.compile(
    r"(?:[-–—]\s*(?:порно|эротический)\s+рассказ.*)$",
    re.IGNORECASE,
)
BACKWARD_SEARCH_LIMIT = 500
INDEX_LOOKUP_MAX_BACK_IDS = 20000
INDEX_LOOKUP_MAX_FETCH_IDS = 300
INDEX_FETCH_CHUNK = 100
INDEX_FETCH_WORKERS = 32
TITLE_CACHE_FILE = Path(__file__).resolve().parent / ".post_title_cache.json"
POST_IDS_CACHE_FILE = Path(__file__).resolve().parent / ".post_ids_cache.json"
POST_INDEX_FILE = Path(__file__).resolve().parent / "post_index.json"
UNRESOLVED_FILE = Path(__file__).resolve().parent / "unresolved.csv"
ENABLE_INDEX_SEARCH = True
MAX_INDEX_SECONDS_PER_FILE = 90
KNOWN_PREV_PART_BY_ID: dict[int, int] = {
    # Явно проблемные рассказы "часть 2", где первая часть отдельным постом без нормальной навигации.
    40912: 40888,  # Alexisverse
    21310: 21307,  # (You drive me) crazy
    94357: 92575,  # Автостопщица 2
    64603: 64227,  # Адвокат для секс-шопа. Глава 2
    64953: 64886,  # А наши лучше - часть 2
}

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
        login_data = {"login": USERNAME, "password": PASSWORD, "submit": "Вход"}
        ok_any = False

        for base in ALL_BASE_URLS:
            # 1) age-gate
            resp = session.get(base, headers=HEADERS, timeout=LOGIN_TIMEOUT)
            resp.raise_for_status()
            if "age-gate" in resp.text or "age_confirm" in resp.text:
                resp = session.post(
                    base,
                    data={"age_confirm": "1"},
                    headers=HEADERS,
                    timeout=LOGIN_TIMEOUT,
                )
                resp.raise_for_status()

            # 2) login
            resp = session.post(
                _login_endpoint(base),
                data=login_data,
                headers=HEADERS,
                timeout=LOGIN_TIMEOUT,
            )
            resp.raise_for_status()

            # 3) check
            check = session.get(base, headers=HEADERS, timeout=LOGIN_TIMEOUT)
            check.raise_for_status()
            html = check.text.lower()
            if "logout" in html or "profile" in html or "выход" in html or "профиль" in html:
                ok_any = True

        if ok_any:
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


def _extract_post_id(url: str) -> int | None:
    m = POST_ID_FROM_URL_PATTERN.search(url or "")
    return int(m.group(1)) if m else None


def _url_for_post_id(base_url: str, post_id: int) -> str:
    return f"{base_url.rstrip('/')}/post_{post_id}"


def _load_json_cache(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_json_cache(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _try_fetch_post_text_any_domain(session: requests.Session, url: str) -> tuple[str, str | None]:
    """
    Иногда на .vip часть постов отдаёт только сайдбар, а текст есть на .in.
    Если URL содержит post_ID — пробуем взять тот же post_ID на всех доменах.
    Возвращает (url_который_сработал, text|None).
    """
    post_id = _extract_post_id(url)
    if not post_id:
        soup, text = fetch_page(session, url)
        return url, text

    for base in ALL_BASE_URLS:
        cand_url = _url_for_post_id(base, post_id)
        _, text = fetch_page(session, cand_url)
        if not text:
            _, text = _fetch_page_with_loose_text(session, cand_url)
        if text and _looks_like_sidebar(text):
            text = None
        if text:
            return cand_url, text
    return url, None


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
SIDEBAR_LINE_PATTERN = re.compile(
    r"^(?:Новые рассказы|А в попку лучше|В первый раз|Ваши рассказы|Восемнадцать лет|Гетеросексуалы|"
    r"Зрелый возраст|По принуждению|Рассказы с фото|Сексwife|Служебный роман|Эротическая сказка|"
    r"Логин\s*\(|Пароль\s*\(|Email\s*\()",
    re.IGNORECASE,
)


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
    # 6) эвристика: берём самый длинный блок <div>/<article>/<section> с большим объёмом кириллицы.
    best_len = 0
    best_text = None
    for node in soup.find_all(["div", "article", "section"]):
        out = node.get_text(separator="\n", strip=True)
        if not out or len(out) < 500:
            continue
        if _cyrillic_ratio(out) < 0.2:
            continue
        if _looks_like_sidebar(out):
            continue
        if len(out) > best_len:
            best_len = len(out)
            best_text = out
    if best_text:
        return best_text
    # 7) все <p> из body (если контейнер без опознаваемого id/class)
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


def fetch_page(session: requests.Session, url: str, quiet: bool = False) -> tuple[BeautifulSoup | None, str | None]:
    """
    Скачать страницу, вернуть (soup, текст из postmaintext).
    Нужно для разбора storylist и извлечения текста.
    """
    try:
        r = session.get(url, headers=HEADERS, timeout=(4, 8))
        r.raise_for_status()
        # На сайте встречаются страницы в cp1251 и utf-8, поэтому пробуем декодировать «мягко».
        enc = (r.encoding or "").lower()
        if not enc or enc == "iso-8859-1":
            enc = (r.apparent_encoding or "").lower()
        raw = r.content
        if enc:
            try:
                html = raw.decode(enc, errors="replace")
            except Exception:
                try:
                    html = raw.decode("windows-1251", errors="replace")
                except Exception:
                    html = raw.decode("utf-8", errors="replace")
        else:
            try:
                html = raw.decode("windows-1251", errors="replace")
            except Exception:
                html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        text = _extract_text_from_soup(soup)
        return soup, text
    except Exception as e:
        if not quiet:
            print(f"  Ошибка загрузки: {e}")
        return None, None


def _extract_page_title(soup: BeautifulSoup | None) -> str:
    if not soup or not soup.title:
        return ""
    return soup.title.get_text(" ", strip=True).strip()


def _normalize_title_for_match(title: str) -> str:
    t = (title or "").lower()
    t = SITE_SUFFIX_PATTERN.sub("", t)
    t = PART_SUFFIX_CLEAN_PATTERN.sub("", t)
    t = TITLE_CLEAN_CHARS_PATTERN.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _same_story_title(base_title: str, candidate_title: str) -> bool:
    """
    Защита от подмены: принимаем только тот же рассказ.
    После нормализации требуем почти точное совпадение.
    """
    a = _normalize_title_for_match(base_title)
    b = _normalize_title_for_match(candidate_title)
    if not a or not b:
        return False
    return a == b


def _extract_part_num(title: str) -> int | None:
    m = PART_NUM_PATTERN.search(title or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _extract_story_title_from_file(content: str) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    start = 0
    for i, ln in enumerate(lines[:20]):
        s = (ln or "").strip()
        if s and set(s) == {"="}:
            start = i + 1
            break
    for ln in lines[start:start + 20]:
        s = (ln or "").strip()
        if not s:
            continue
        if set(s) == {"="}:
            continue
        return s
    return ""


def _is_second_story_part(file_path: Path, content: str) -> bool:
    title = _extract_story_title_from_file(content)
    name = file_path.name.lower()
    combined = (title + " " + name).lower()
    if PART2_IN_TITLE_PATTERN.search(combined):
        return True
    # Имя вида "..._2_..." тоже трактуем как не-первую часть.
    if re.search(r"_2_", name):
        return True
    # "главы N-M" / "глава N" / "часть N" / "ч.N" / "чN" где N > 1
    m = re.search(r"(?:глав[аы]\s*|главы\s*|част[ьи]\s*|ч\.?\s*)(\d+)", combined)
    if m:
        try:
            n = int(m.group(1))
            if n > 1:
                return True
        except Exception:
            pass
    # Явная подсказка "что случилось до: ...post_xxx" — это продолжение.
    if PREV_STORY_URL_IN_TEXT_PATTERN.search(content):
        return True
    return False


def _get_first_storylist_url(session: requests.Session, post_id: int) -> str | None:
    """
    Зайти на страницу поста, найти ol#storylist, вернуть URL первой ссылки
    (= реальная первая глава). Пробуем все домены (.vip, .in).
    """
    for base in ALL_BASE_URLS:
        url = _url_for_post_id(base, post_id)
        try:
            r = session.get(url, headers=HEADERS, timeout=(4, 8))
            if r.status_code != 200:
                continue
            enc = (r.encoding or "").lower()
            if not enc or enc == "iso-8859-1":
                enc = (r.apparent_encoding or "").lower()
            raw = r.content
            try:
                html = raw.decode(enc or "windows-1251", errors="replace")
            except Exception:
                html = raw.decode("windows-1251", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            sl = soup.find("ol", id="storylist")
            if not sl:
                continue
            for li in sl.find_all("li"):
                a = li.find("a", href=re.compile(r"post_(\d+)"))
                if a:
                    href = (a.get("href") or "").split("#")[0].strip()
                    if not href.startswith("http"):
                        href = f"{base.rstrip('/')}/{href.lstrip('/')}"
                    return href
        except Exception:
            continue
    return None


def _find_part1_for_second_story(
    session: requests.Session, start_url: str, file_path: Path, content: str
) -> tuple[str | None, str | None, bool]:
    """
    Для файлов "Часть 2/Глава 2" ищем реальную первую главу.
    Основной метод: ol#storylist на странице поста — первая ссылка = первая глава.
    Возвращает (url, text, from_storylist).
    from_storylist=True означает, что источник гарантированно из того же рассказа.
    """
    pid = _extract_post_id(start_url)

    # 1) Основной путь: ol#storylist на странице текущего поста.
    if pid:
        first_url = _get_first_storylist_url(session, pid)
        if first_url:
            real_url, text = _try_fetch_post_text_any_domain(session, first_url)
            if text:
                return real_url, text, True

    # 2) Быстрый known-map (для постов, где storylist отсутствует).
    if pid and pid in KNOWN_PREV_PART_BY_ID:
        mapped = _url_for_post_id(BASE_URL, KNOWN_PREV_PART_BY_ID[pid])
        url, text = _try_fetch_post_text_any_domain(session, mapped)
        return url, text, True

    # 3) Прямой хинт из текста файла: "что случилось до: ...post_xxx"
    hint_match = PREV_STORY_URL_IN_TEXT_PATTERN.search(content or "")
    if hint_match:
        prev_url = hint_match.group(1).strip()
        pm = POST_ID_FROM_URL_PATTERN.search(prev_url)
        if pm:
            prev_url = _url_for_post_id(BASE_URL, int(pm.group(1)))
        real_url, text = _try_fetch_post_text_any_domain(session, prev_url)
        if text:
            return real_url, text, False

    # 4) Последний fallback: навигация "prev" + защита от подмены.
    story_title_raw = _extract_story_title_from_file(content)
    safe_url, safe_text = resolve_first_part_url(session, start_url, content)
    if safe_text:
        safe_title = _fetch_title_only(session, safe_url or "")
        if _same_story_title(story_title_raw, safe_title):
            return safe_url, safe_text, False
    return None, None, False


def _fetch_title_and_text(session: requests.Session, url: str) -> tuple[str, str | None]:
    soup, text = fetch_page(session, url, quiet=True)
    return _extract_page_title(soup), text


def _fetch_title_only(session: requests.Session, url: str) -> str:
    try:
        r = session.get(url, headers=HEADERS, timeout=(3, 5))
        r.raise_for_status()
        enc = (r.encoding or "").lower()
        if not enc or enc == "iso-8859-1":
            enc = (r.apparent_encoding or "").lower()
        raw = r.content
        if enc:
            try:
                html = raw.decode(enc, errors="replace")
            except Exception:
                html = raw.decode("windows-1251", errors="replace")
        else:
            html = raw.decode("windows-1251", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        return _extract_page_title(soup)
    except Exception:
        return ""


def _fetch_title_for_post_id(post_id: int) -> str:
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept-Language": HEADERS["Accept-Language"],
    }
    for base in ALL_BASE_URLS:
        url = _url_for_post_id(base, post_id)
        try:
            r = requests.get(url, headers=headers, timeout=(0.6, 1.0))
            if r.status_code != 200:
                continue
            enc = (r.encoding or "").lower()
            if not enc or enc == "iso-8859-1":
                enc = (r.apparent_encoding or "").lower()
            raw = r.content
            if enc:
                try:
                    html = raw.decode(enc, errors="replace")
                except Exception:
                    html = raw.decode("windows-1251", errors="replace")
            else:
                html = raw.decode("windows-1251", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            title = _extract_page_title(soup)
            if title and "404" not in title.lower():
                return title
        except Exception:
            continue
    return ""


def _get_post_ids_from_sitemap() -> list[int]:
    cache = _load_json_cache(POST_IDS_CACHE_FILE)
    cached_ids = cache.get("ids")
    if isinstance(cached_ids, list) and cached_ids:
        return [int(x) for x in cached_ids if str(x).isdigit()]

    ids: set[int] = set()
    try:
        xml = requests.get(f"{BASE_URL}/sitemap.xml", timeout=90).content.decode(
            "windows-1251", errors="replace"
        )
        for m in re.finditer(r"post_(\d+)", xml, flags=re.IGNORECASE):
            ids.add(int(m.group(1)))
    except Exception:
        return []
    out = sorted(ids)
    _save_json_cache(POST_IDS_CACHE_FILE, {"ids": out})
    return out


def _find_part1_via_cached_index(current_post_id: int, story_title: str) -> int | None:
    if not current_post_id:
        return None
    base_title = _normalize_title_for_match(story_title)
    if not base_title:
        return None
    current_part = _extract_part_num(story_title)

    all_ids = _get_post_ids_from_sitemap()
    if not all_ids:
        return None

    lower_bound = max(1, current_post_id - INDEX_LOOKUP_MAX_BACK_IDS)
    candidates = [i for i in all_ids if lower_bound <= i < current_post_id]
    candidates.sort(reverse=True)
    if not candidates:
        return None

    raw_cache = _load_json_cache(TITLE_CACHE_FILE)
    title_cache: dict[int, str] = {}
    for k, v in raw_cache.items():
        if str(k).isdigit() and isinstance(v, str):
            title_cache[int(k)] = v

    def is_match(title: str) -> bool:
        if not title:
            return False
        cand_base = _normalize_title_for_match(title)
        if not cand_base:
            return False
        if cand_base != base_title:
            return False
        cand_part = _extract_part_num(title)
        if current_part is not None and cand_part is not None:
            return cand_part < current_part
        return bool(PART1_IN_TITLE_PATTERN.search(title.lower()))

    started = time.monotonic()
    for pid in candidates:
        if time.monotonic() - started > MAX_INDEX_SECONDS_PER_FILE:
            return None
        if is_match(title_cache.get(pid, "")):
            return pid

    to_fetch = [pid for pid in candidates if pid not in title_cache][:INDEX_LOOKUP_MAX_FETCH_IDS]
    dirty = False
    for off in range(0, len(to_fetch), INDEX_FETCH_CHUNK):
        if time.monotonic() - started > MAX_INDEX_SECONDS_PER_FILE:
            break
        chunk = to_fetch[off : off + INDEX_FETCH_CHUNK]
        with cf.ThreadPoolExecutor(max_workers=INDEX_FETCH_WORKERS) as ex:
            for pid, title in zip(chunk, ex.map(_fetch_title_for_post_id, chunk)):
                if title:
                    title_cache[pid] = title
                    dirty = True
        if dirty:
            _save_json_cache(TITLE_CACHE_FILE, {str(k): v for k, v in title_cache.items()})
        for pid in chunk:
            if is_match(title_cache.get(pid, "")):
                if dirty:
                    _save_json_cache(TITLE_CACHE_FILE, {str(k): v for k, v in title_cache.items()})
                return pid

    if dirty:
        _save_json_cache(TITLE_CACHE_FILE, {str(k): v for k, v in title_cache.items()})
    return None


def _load_post_index() -> dict[str, dict]:
    data = _load_json_cache(POST_INDEX_FILE)
    rows = data.get("rows")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("post_id")
        if isinstance(pid, int):
            out[str(pid)] = row
        elif isinstance(pid, str) and pid.isdigit():
            out[pid] = row
    return out


def _save_post_index(index_map: dict[str, dict]) -> None:
    rows = list(index_map.values())
    rows.sort(key=lambda x: int(x.get("post_id", 0)))
    payload = {"version": 1, "rows": rows, "updated_at": int(time.time())}
    _save_json_cache(POST_INDEX_FILE, payload)


def _build_index_from_sitemap() -> None:
    post_ids = _get_post_ids_from_sitemap()
    if not post_ids:
        print("Index build: sitemap пустой/недоступен.", flush=True)
        return
    index_map = _load_post_index()
    done = len(index_map)
    total = len(post_ids)
    print(f"Index build: {done}/{total} уже в кэше.", flush=True)

    pending = [pid for pid in post_ids if str(pid) not in index_map]
    if not pending:
        print("Index build: обновление не требуется.", flush=True)
        return

    for off in range(0, len(pending), INDEX_FETCH_CHUNK):
        chunk = pending[off : off + INDEX_FETCH_CHUNK]
        with cf.ThreadPoolExecutor(max_workers=INDEX_FETCH_WORKERS) as ex:
            for pid, title in zip(chunk, ex.map(_fetch_title_for_post_id, chunk)):
                if not title:
                    continue
                norm = _normalize_title_for_match(title)
                index_map[str(pid)] = {
                    "post_id": pid,
                    "url": _url_for_post_id(BASE_URL, pid),
                    "title": title,
                    "base_title": norm,
                    "part_num": _extract_part_num(title),
                    "thread_key": norm,
                }
        _save_post_index(index_map)
        print(f"Index build: {min(off + len(chunk), len(pending))}/{len(pending)} новых", flush=True)

    print(f"Index build done: {len(index_map)} постов.", flush=True)


def _find_part1_via_post_index(current_post_id: int, story_title: str) -> int | None:
    if not current_post_id:
        return None
    index_map = _load_post_index()
    if not index_map:
        return None

    cur = index_map.get(str(current_post_id))
    current_part = _extract_part_num(story_title)
    thread_key = _normalize_title_for_match(story_title)
    if cur:
        current_part = cur.get("part_num", current_part)
        thread_key = cur.get("thread_key") or thread_key
    if not thread_key:
        return None

    best_pid = None
    best_part = 10**9
    fallback_pid = None
    for row in index_map.values():
        if row.get("thread_key") != thread_key:
            continue
        pid = int(row.get("post_id", 0) or 0)
        if pid <= 0 or pid >= current_post_id:
            continue
        part_num = row.get("part_num")
        if isinstance(part_num, int):
            if current_part is not None and part_num >= current_part:
                continue
            if part_num < best_part:
                best_part = part_num
                best_pid = pid
            continue
        # Если у кандидата нет номера части в title (частый кейс "без Часть 1"),
        # запоминаем ближайший предыдущий post_id как резерв.
        if fallback_pid is None or pid > fallback_pid:
            fallback_pid = pid
    return best_pid or fallback_pid


def _append_unresolved(file_path: Path, reason: str, source_url: str | None) -> None:
    header = ["file", "reason", "source_url"]
    exists = UNRESOLVED_FILE.exists()
    with UNRESOLVED_FILE.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow([str(file_path), reason, source_url or ""])


def _fetch_page_with_loose_text(session: requests.Session, url: str) -> tuple[BeautifulSoup | None, str | None]:
    """
    Мягкий fallback для страниц, где основной селектор текста не сработал.
    Берём текст из <p> по body почти без фильтров.
    """
    try:
        r = session.get(url, headers=HEADERS, timeout=(4, 8))
        r.raise_for_status()
        enc = (r.encoding or "").lower()
        if not enc or enc == "iso-8859-1":
            enc = (r.apparent_encoding or "").lower()
        raw = r.content
        if enc:
            try:
                html = raw.decode(enc, errors="replace")
            except Exception:
                html = raw.decode("windows-1251", errors="replace")
        else:
            html = raw.decode("windows-1251", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        body = soup.find("body") or soup
        parts: list[str] = []
        for p in body.find_all("p"):
            t = p.get_text(" ", strip=True)
            if len(t) < 20:
                continue
            parts.append(t)
        text = "\n\n".join(parts).strip()
        return soup, (text if len(text) >= 300 else None)
    except Exception:
        return None, None


def _clean_fetched_text(text: str) -> str:
    """Убрать навигационный мусор, если loose-fallback захватил сайдбар."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    # Если встретили явный блок "СТРАНИЦА ...", начинаем с него.
    for i, ln in enumerate(lines):
        if "СТРАНИЦА" in ln.upper() and "http" in ln.lower():
            cleaned = "\n".join(lines[i:]).strip()
            return cleaned
    # Иначе убираем типовые строки сайдбара в начале.
    while lines and (not lines[0] or SIDEBAR_LINE_PATTERN.search(lines[0])):
        lines.pop(0)
    return "\n".join(lines).strip()


def _find_part1_by_backward_scan(
    session: requests.Session,
    current_url: str,
    current_title: str,
) -> tuple[str | None, str | None]:
    """
    Если текущая страница похожа на "часть 2/глава 2", ищем назад по post_ID страницу
    с близким названием и признаком "часть 1/глава 1".
    """
    m = POST_ID_FROM_URL_PATTERN.search(current_url or "")
    if not m:
        return None, None
    current_id = int(m.group(1))
    base_title = _normalize_title_for_match(current_title)
    if not base_title:
        return None, None

    start_id = max(1, current_id - BACKWARD_SEARCH_LIMIT)
    for pid in range(current_id - 1, start_id - 1, -1):
        cand_url = f"{BASE_URL}/post_{pid}"
        title = _fetch_title_only(session, cand_url)
        if not title:
            continue
        low = title.lower()
        if not PART1_IN_TITLE_PATTERN.search(low):
            continue
        cand_base = _normalize_title_for_match(title)
        if not cand_base:
            continue
        # Достаточно, чтобы одна сторона включала другую (названия часто различаются хвостами).
        if cand_base in base_title or base_title in cand_base:
            _, text = _fetch_title_and_text(session, cand_url)
            if text:
                return cand_url, text
    return None, None


def fetch_text(session: requests.Session, url: str) -> str | None:
    """Скачать страницу и вернуть только текст (для обратной совместимости вызовов)."""
    _, text = fetch_page(session, url)
    return text


def resolve_first_part_url(session: requests.Session, url_from_file: str, content_hint: str = "") -> tuple[str, str | None]:
    """
    Перейти на настоящую первую часть.

    Ключевой кейс: исходный парсер мог записать URL «первой части», который на самом деле ведёт на 2-ю главу.
    Поэтому основной способ — подниматься по навигации «предыдущая» (rel=prev / "предыдущая"/"назад")
    пока ссылка не пропадёт. Это и есть первая часть.
    Возвращает (url_первой_части, текст_с_этой_страницы).
    """
    def _abs(href: str) -> str:
        if href.startswith("http"):
            return href
        return f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"

    def _get_prev_post_url(soup: BeautifulSoup) -> str | None:
        # 1) rel="prev"
        link = soup.find("link", rel=re.compile(r"\bprev\b", re.IGNORECASE))
        if link and link.get("href"):
            href = str(link.get("href")).strip()
            if re.search(r"post_\d+", href):
                return _abs(href.split("#")[0].strip())
        # 2) <a rel="prev">
        a = soup.find("a", rel=re.compile(r"\bprev\b", re.IGNORECASE), href=re.compile(r"post_\d+"))
        if a and a.get("href"):
            return _abs(str(a.get("href")).split("#")[0].strip())
        # 3) текстовые маркеры
        for a in soup.find_all("a", href=re.compile(r"post_\d+")):
            txt = (a.get_text(" ", strip=True) or "").lower()
            if any(k in txt for k in ("предыдущ", "назад", "prev", "←", "<<")):
                return _abs(str(a.get("href")).split("#")[0].strip())
        return None

    current = url_from_file
    start_soup, _ = fetch_page(session, current)
    start_title = _extract_page_title(start_soup)
    current_id_match = POST_ID_FROM_URL_PATTERN.search(current or "")
    current_id = int(current_id_match.group(1)) if current_id_match else None
    # Ограничитель на случай циклических ссылок.
    for _ in range(30):
        soup, _ = fetch_page(session, current)
        if not soup:
            break
        prev_url = _get_prev_post_url(soup)
        if not prev_url or _normalize_url(prev_url) == _normalize_url(current):
            break
        current = prev_url

    current, text = _try_fetch_post_text_any_domain(session, current)
    if text:
        return current, text

    # Резерв A: явная ссылка "что случилось до: https://...post_xxx" в тексте файла.
    hint_match = PREV_STORY_URL_IN_TEXT_PATTERN.search(content_hint or "")
    if hint_match:
        prev_url = hint_match.group(1).strip()
        # Нормализуем домен на BASE_URL
        pm = POST_ID_FROM_URL_PATTERN.search(prev_url)
        if pm:
            prev_url = _url_for_post_id(BASE_URL, int(pm.group(1)))
        prev_url, prev_text = _try_fetch_post_text_any_domain(session, prev_url)
        if prev_text:
            return prev_url, prev_text

    # Резерв B: быстрые явные маппинги для известных "часть 2" без навигации.
    if current_id in KNOWN_PREV_PART_BY_ID:
        prev_url = _url_for_post_id(BASE_URL, KNOWN_PREV_PART_BY_ID[current_id])
        prev_url, prev_text = _try_fetch_post_text_any_domain(session, prev_url)
        if prev_text:
            return prev_url, prev_text
        # Для известных кейсов не уходим в долгий перебор, чтобы не зависать.
        return current, None

    # Резерв C: "часть 2/глава 2" без навигации — ищем "часть 1" назад по ID.
    if PART2_IN_TITLE_PATTERN.search((start_title or "").lower()):
        found_url, found_text = _find_part1_by_backward_scan(session, url_from_file, start_title)
        if found_url and found_text:
            return found_url, found_text

    # Резерв D: если навигация не помогла/текст не извлёкся, пробуем storylist/min post id.
    if soup:
        first_url = _get_first_part_url_from_storylist(soup) or _get_first_post_url_from_page(soup)
        if first_url and _normalize_url(first_url) != _normalize_url(current):
            _, text2 = fetch_page(session, first_url)
            if text2:
                return first_url, text2
    return current, None


def _get_existing_page_info(content_after_header: str) -> tuple[int | None, str | None, bool]:
    """
    По локальному файлу понять, с какой страницы он реально начинается.
    Возвращает:
      (first_page_num, first_page_url, has_any_page1)
    где first_page_num/first_page_url — первая встреченная шапка "СТРАНИЦА N ИЗ M | URL".
    """
    first_page = None
    first_url = None
    has_any_page1 = False
    for m in PAGE_HEADER_WITH_URL_PATTERN.finditer(content_after_header):
        try:
            page = int(m.group("page"))
        except Exception:
            continue
        url = (m.group("url") or "").strip()
        if not url:
            continue
        if page == 1:
            has_any_page1 = True
        if first_page is None:
            first_page = page
            first_url = url
    return first_page, first_url, has_any_page1


def process_file(session: requests.Session, file_path: Path, content: str) -> str | bool:
    """
    Обработать один файл (content уже прочитан; проверка на одну часть делается снаружи).
    Возвращает:
      True — вклеено,
      "already_has[:reason]" — первая часть уже в файле,
      "err:*" — ошибка.
    """
    header_end = find_header_end(content)
    if header_end is None:
        return "err:no_header"

    content_after_header = content[header_end:]
    started_at = time.monotonic()

    url = extract_first_part_url(content)
    if not url:
        m_first = PAGE_HEADER_WITH_URL_PATTERN.search(content_after_header)
        url = (m_first.group("url").strip() if m_first else None)
    if not url:
        # Резерв: берём post_ID из имени файла "..._12345.txt"
        m_id = re.search(r"_(\d+)$", file_path.stem)
        if m_id:
            url = _url_for_post_id(BASE_URL, int(m_id.group(1)))
    if not url:
        return "err:no_url"
    second_story_mode = _is_second_story_part(file_path, content)
    first_page_in_file, url_first_page_in_file, has_any_page1 = _get_existing_page_info(content_after_header)
    # Авточистка: если перед первым блоком "СТРАНИЦА ..." уже есть большой текст,
    # считаем это дублем/мусором и отрезаем префикс.
    first_page_header_match = PAGE_HEADER_WITH_URL_PATTERN.search(content_after_header)
    if first_page_header_match and first_page_header_match.start() > 200:
        cleaned_body = content_after_header[first_page_header_match.start() :].lstrip()
        content = content[:header_end].rstrip() + "\n\n" + cleaned_body
        file_path.write_text(content, encoding="utf-8")
        content_after_header = content[header_end:]
        first_page_in_file, url_first_page_in_file, has_any_page1 = _get_existing_page_info(content_after_header)
    # Если файл начинается со 2+ страницы — первая часть в начале файла отсутствует.
    has_prev_hint = bool(PREV_STORY_URL_IN_TEXT_PATTERN.search(content))
    filename_part2_hint = bool(re.search(r"(?:часть[_\s-]*2|глава[_\s-]*2|\bч2\b)", file_path.name, re.IGNORECASE))
    missing_part1 = bool(first_page_in_file and first_page_in_file > 1)
    if first_page_in_file is None and (has_prev_hint or filename_part2_hint):
        missing_part1 = True

    # Всегда пытаемся получить реальную первую часть через навигацию «предыдущая».
    # Стартовая точка = post_id из имени файла (не доверяем локальной шапке —
    # исходный парсер мог записать URL второй главы как «первую часть»).
    print(" fetch...", end="", flush=True)
    try:
        m_id = re.search(r"_(\d+)$", file_path.stem)
        start_url = _url_for_post_id(BASE_URL, int(m_id.group(1))) if m_id else url
        from_storylist = False
        if second_story_mode:
            real_first_url, text, from_storylist = _find_part1_for_second_story(session, start_url, file_path, content)
            if not text:
                _append_unresolved(file_path, "part1_not_found_for_part2", start_url)
                return "err:unresolved"
        else:
            real_first_url, text = resolve_first_part_url(session, start_url, content)
    except Exception as e:
        print(f" {e}", flush=True)
        return "err:fetch"
    if time.monotonic() - started_at > MAX_SECONDS_PER_FILE:
        return "err:fetch"

    if not text:
        return "err:no_text"
    text = _clean_fetched_text(text)
    if not text:
        return "err:no_text"

    # Стоп от подмены чужим рассказом: проверяем title источника.
    # Если найдено через storylist — проверка не нужна (storylist гарантирует тот же рассказ).
    if second_story_mode and not from_storylist:
        source_title = _fetch_title_only(session, real_first_url or "")
        story_title = _extract_story_title_from_file(content)
        if not _same_story_title(story_title, source_title):
            return "err:no_text"

    # Стоп от дублирования той же главы в начало.
    if second_story_mode:
        body_after_header = re.sub(r"\s+", " ", content_after_header.lstrip())[:FIRST_LINE_COMPARE_CHARS]
        fetched_start = re.sub(r"\s+", " ", text.strip())[:FIRST_LINE_COMPARE_CHARS]
        if len(body_after_header) >= 80 and len(fetched_start) >= 80:
            a = _normalize_snippet(body_after_header)
            b = _normalize_snippet(fetched_start)
            dup_ratio = SequenceMatcher(None, a[:FIRST_LINE_COMPARE_CHARS], b[:FIRST_LINE_COMPARE_CHARS]).ratio()
            if a[:80] == b[:80] or dup_ratio >= 0.97:
                return f"already_has:starts_match_real_part1_ratio={dup_ratio:.3f}"

    # Если мы точно знаем, что файл начинается со 2+ страницы — нельзя делать "already_has" по совпадению,
    # потому что URL мог указывать на 2-ю главу. В этом кейсе вставляем первую часть принудительно.
    if not missing_part1 and not second_story_mode:
        # Критерий «уже есть первая часть»: текст СРАЗУ после шапки совпадает с началом скачанной главы.
        body_after_header = re.sub(r"\s+", " ", content_after_header.lstrip())[:FIRST_LINE_COMPARE_CHARS]
        fetched_start = re.sub(r"\s+", " ", text.strip())[:FIRST_LINE_COMPARE_CHARS]
        if len(body_after_header) >= 80 and len(fetched_start) >= 80:
            a, b = _normalize_snippet(body_after_header), _normalize_snippet(fetched_start)
            ratio = SequenceMatcher(None, a[:FIRST_LINE_COMPARE_CHARS], b[:FIRST_LINE_COMPARE_CHARS]).ratio()
            if a[:80] == b[:80] or ratio >= FIRST_LINE_MATCH_THRESHOLD:
                return f"already_has:starts_match_ratio={ratio:.3f}"

    new_content = content[:header_end].rstrip() + "\n\n" + text.strip() + "\n\n" + content[header_end:].lstrip()
    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    if not backup_path.exists():
        try:
            backup_path.write_text(content, encoding="utf-8")
        except Exception:
            pass
    file_path.write_text(new_content, encoding="utf-8")
    return True


def main():
    if "--build-index" in sys.argv[1:]:
        print("Режим: build-index", flush=True)
        _build_index_from_sitemap()
        return

    # Safe mode: работаем только с явно переданной папкой.
    if len(sys.argv) <= 1:
        print("Отменено: папка не передана аргументом.", flush=True)
        print('Пример: python download_first_parts.py "D:\\path\\to\\stories"', flush=True)
        return
    root_dir = Path(str(sys.argv[1]).strip().strip('"')).resolve()
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
    to_process = files[:]

    print(f"Found files: {len(to_process)}. Processing...", flush=True)

    report_inserted: list[Path] = []
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
        before_content = content
        result = process_file(session, f, content)
        if result is True:
            try:
                after_content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                after_content = ""
            if after_content != before_content:
                print("OK")
                report_inserted.append(f)
            else:
                print("SKIP (no actual file change)")
                report_skipped_already.append(f)
        elif isinstance(result, str) and result.startswith("already_has"):
            reason = result.split(":", 1)[1] if ":" in result else "unknown"
            print(f"SKIP (already has part 1; {reason})")
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
        f"Пропущено (уже есть первая часть): {len(report_skipped_already)}. "
        f"Ошибки: {len(report_errors)}."
    )
    print(summary, flush=True)
    if report_errors:
        print(f"  Причины ошибок: {err_details}", flush=True)
        print("  Файлы с ошибками:", flush=True)
        for p, code in report_errors:
            reason = ERROR_MSG_RU.get(code, code)
            print(f"    {rel_path(p)}  —  {reason}", flush=True)
    print("=" * 60, flush=True)
    sys.stdout.flush()

    if report_errors:
        report_path = root_dir / "отчёт_ошибок.txt"
        with report_path.open("w", encoding="utf-8") as rf:
            rf.write(f"Ошибки при докачке первых глав ({len(report_errors)} файлов)\n")
            rf.write("=" * 60 + "\n\n")
            for p, code in report_errors:
                reason = ERROR_MSG_RU.get(code, code)
                rf.write(f"{rel_path(p)}\n  Причина: {reason}\n\n")
        print(f"\nОтчёт об ошибках: {report_path}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
