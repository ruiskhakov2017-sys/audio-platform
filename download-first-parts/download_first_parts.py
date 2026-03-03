#!/usr/bin/env python3
"""
Скрипт докачивает недостающие первые части текста с защищённого сайта
и вклеивает их в начало существующих .txt файлов.
"""

import re
import sys
import time
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

SKIP_FILENAMES = {"clean_text.txt"}

# Длина фрагмента для сравнения «первая часть уже есть»
SAMPLE_LEN = 80
# Порог похожести (0..1): выше — считаем, что первая часть уже в файле
SIMILARITY_THRESHOLD = 0.75

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

DELAY_SECONDS = 2


def login(session: requests.Session) -> bool:
    """Авторизация, максимально близкая к main_new.BestWeaponScraper.login."""
    try:
        # 1. Проходим age-gate
        resp = session.get(BASE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        if "age-gate" in resp.text or "age_confirm" in resp.text:
            resp = session.post(
                BASE_URL,
                data={"age_confirm": "1"},
                headers=HEADERS,
                timeout=30,
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
            timeout=30,
        )
        resp.raise_for_status()

        # 3. Проверяем по главной, как в основном парсере
        check = session.get(BASE_URL, headers=HEADERS, timeout=30)
        check.raise_for_status()
        html = check.text.lower()
        if "logout" in html or "profile" in html or "выход" in html or "профиль" in html:
            return True
        print("⚠️ Авторизация не удалась (logout/profile не найдены).")
        return False
    except Exception as e:
        print(f"Ошибка логина: {e}")
        return False


def find_txt_files(root: Path):
    """Найти все .txt кроме clean_text.txt и логов."""
    for f in root.rglob("*.txt"):
        if f.name in SKIP_FILENAMES or "log" in f.name.lower():
            continue
        yield f


def extract_first_part_url(content: str) -> str | None:
    """Извлечь URL из строки вида 'URL первой части: https://...'"""
    m = URL_LINE_PATTERN.search(content)
    return m.group(1).strip() if m else None


def is_single_part_story(content: str) -> bool:
    """Проверка: рассказ из одной части (В рассказе: 1 частей / СТРАНИЦА 1 ИЗ 1)."""
    return any(p.search(content) for p in SINGLE_PART_PATTERNS)


def _normalize_snippet(s: str) -> str:
    """Очистка фрагмента для сравнения: без пунктуации, лишних пробелов, нижний регистр."""
    s = re.sub(r"\s+", " ", s.strip()).lower()
    return re.sub(r"[^\w\s]", "", s, flags=re.IGNORECASE)


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


def _normalize_url(u: str) -> str:
    """Один URL для сравнения: без якоря и query."""
    return (u or "").split("#")[0].split("?")[0].strip().rstrip("/")


def _get_first_part_url_from_storylist(soup: BeautifulSoup) -> str | None:
    """
    Из ol#storylist взять URL первой части (первая ссылка на post_XXX).
    Как в main_new._get_first_part_url_from_storylist — чтобы всегда брать текст именно с первой страницы.
    """
    storylist = soup.find("ol", id="storylist")
    if not storylist:
        return None
    for li in storylist.find_all("li"):
        a = li.find("a", href=re.compile(r"post_\d+"))
        if not a:
            continue
        href = (a.get("href") or "").split("#")[0].strip()
        if not href or not re.search(r"post_\d+", href):
            continue
        if not href.startswith("http"):
            href = f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"
        return href
    return None


def _extract_text_from_soup(soup: BeautifulSoup) -> str | None:
    """Из уже собранного soup извлечь текст из span#postmaintext (как в main_new)."""
    for comment in soup.find_all("div", class_="textcomm"):
        comment.decompose()
    main_span = soup.find("span", id="postmaintext")
    if not main_span:
        return None
    text_parts: list[str] = []
    for p in main_span.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) < 20:
            continue
        lower = t.lower()
        if any(m in lower for m in ["категории", "добавить", "форум", "рейтинг"]):
            continue
        text_parts.append(t)
    full_text = "\n\n".join(text_parts) if text_parts else main_span.get_text(
        separator="\n", strip=True
    )
    return full_text.strip() or None


def fetch_page(session: requests.Session, url: str) -> tuple[BeautifulSoup | None, str | None]:
    """
    Скачать страницу, вернуть (soup, текст из postmaintext).
    Нужно для разбора storylist и извлечения текста.
    """
    try:
        r = session.get(url, headers=HEADERS, timeout=30)
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
        return False

    local_snippet, block_start = find_story_start_and_snippet(content)
    if block_start is None:
        return False

    # Переход на настоящую первую страницу по storylist (первая ссылка в списке частей)
    real_first_url, text_from_resolve = resolve_first_part_url(session, url)
    if text_from_resolve is not None:
        text = text_from_resolve
    else:
        text = fetch_text(session, real_first_url)
    if not text:
        return False

    remote_snippet = re.sub(r"\s+", " ", text.strip())[:SAMPLE_LEN].strip()
    if local_snippet and remote_snippet and snippets_similar(local_snippet, remote_snippet):
        return "already_has"

    lines_before = content[:block_start].split("\n")
    new_before_lines = []
    url_removed = False
    for line in lines_before:
        if URL_LINE_PATTERN.search(line) and not url_removed:
            url_removed = True
            continue
        new_before_lines.append(line)
    before = "\n".join(new_before_lines).rstrip()
    rest = content[block_start:]
    new_content = before + "\n\n" + text.strip() + "\n\n" + rest
    file_path.write_text(new_content, encoding="utf-8")
    return True


def main():
    root_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _DEFAULT_ROOT
    if not root_dir.exists():
        print(f"Папка не найдена: {root_dir}")
        return
    if not root_dir.is_dir():
        print(f"Указан не каталог: {root_dir}")
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    print("Авторизация...")
    if not login(session):
        print("Логин не удался. Проверьте LOGIN_URL, USERNAME, PASSWORD и LOGIN_FORM_FIELDS.")
        return

    print("Логин успешен.\n")
    print(f"Обрабатываю папку: {root_dir}\n")

    files = list(find_txt_files(root_dir))
    to_process = []

    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            if extract_first_part_url(content):
                to_process.append(f)
        except Exception:
            pass

    print(f"Найдено файлов с URL первой части: {len(to_process)}\n")

    report_inserted: list[Path] = []
    report_skipped_single: list[Path] = []
    report_skipped_already: list[Path] = []
    report_errors: list[Path] = []

    for i, f in enumerate(to_process, 1):
        rel = f.relative_to(root_dir)
        print(f"[{i}/{len(to_process)}] {rel} ... ", end="", flush=True)
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            print("ОШИБКА (чтение)")
            report_errors.append(f)
            continue
        if is_single_part_story(content):
            print("[ПРОПУСК] Рассказ состоит из 1 части")
            report_skipped_single.append(f)
            continue
        result = process_file(session, f, content)
        if result is True:
            print("OK")
            report_inserted.append(f)
        elif result == "already_has":
            print("[ПРОПУСК] Первая часть уже есть в файле")
            report_skipped_already.append(f)
        else:
            print("ОШИБКА")
            report_errors.append(f)
        if i < len(to_process):
            time.sleep(DELAY_SECONDS)

    def rel_path(p: Path) -> str:
        return str(p.relative_to(root_dir))

    lines = [
        "",
        "=" * 60,
        "ОТЧЁТ ПО РАБОТЕ",
        "=" * 60,
        f"\nВставлена первая часть ({len(report_inserted)}):",
    ]
    for p in report_inserted:
        lines.append(f"  + {rel_path(p)}")
    lines.append(f"\nПропущено — рассказ из 1 части ({len(report_skipped_single)}):")
    for p in report_skipped_single:
        lines.append(f"  - {rel_path(p)}")
    lines.append(f"\nПропущено — первая часть уже в файле ({len(report_skipped_already)}):")
    for p in report_skipped_already:
        lines.append(f"  = {rel_path(p)}")
    lines.append(f"\nОшибки ({len(report_errors)}):")
    for p in report_errors:
        lines.append(f"  ! {rel_path(p)}")
    lines.append("\n" + "=" * 60)
    lines.append("Готово.")

    report_text = "\n".join(lines)
    print(report_text)
    sys.stdout.flush()

    report_file = Path(__file__).resolve().parent / "report_download_first_parts.txt"
    try:
        report_file.write_text(report_text, encoding="utf-8")
        print(f"\nОтчёт сохранён: {report_file}")
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":
    main()
