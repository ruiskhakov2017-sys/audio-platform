# -*- coding: utf-8 -*-
"""Автоматизация Gem для озвучки: длинный текст режется на чанки по абзацам, ответы пишутся в *_clean.txt."""
import base64
import json
import unicodedata
import mimetypes
import msvcrt
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

from playwright.sync_api import Locator, Page, TimeoutError, sync_playwright

_LEGACY_ROOT = Path(__file__).resolve().parents[1]
if str(_LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEGACY_ROOT))
from gemini_browser_proxy import append_chrome_proxy_args


DEFAULT_GEMINI_URL = "https://gemini.google.com/u/0/gem/e33cef38a3ba"
GEMINI_URL_PATTERN = re.compile(
    r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$"
)
# Hub для смены аккаунта: …/u/N/app или …/u/N/chat (если в конфиге app отличается от индекса в url гема)
GEMINI_APP_HUB_PATTERN = re.compile(
    r"^https://gemini\.google\.com/u/\d+/(app|chat)/?$",
    re.IGNORECASE,
)
GEMINI_AUTHUSER_IN_URL = re.compile(r"gemini\.google\.com/u/(\d+)/", re.IGNORECASE)
PROJECT_DIR = Path(__file__).resolve().parent
CONTENT_FACTORY_ROOT = PROJECT_DIR.parents[1]
if str(CONTENT_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTENT_FACTORY_ROOT))

from orchestrator.gemini_model_resolver import (  # noqa: E402
    ModelChoice,
    expected_model_labels,
    resolve_gemini_model_alias,
    ui_label_matches_gemini_choice,
)


def _gemini_env_path(env_name: str, default: Path) -> Path:
    """Переопределение путей для orchestrator bridge (env), иначе прежние дефолты рядом со скриптом."""
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return default.resolve()
    return Path(raw).expanduser().resolve()


def _gemini_noninteractive() -> bool:
    """GEMINI_NON_INTERACTIVE=1 — без консольного выбора бота/URL; вход в Google/Gemini по-прежнему через UI."""
    return (os.getenv("GEMINI_NON_INTERACTIVE") or "").strip().lower() in ("1", "true", "yes", "on")


# GEMINI_STORIES_DIR — корень дерева «жанров» как legacy/youtube_tts/stories (листья — папки с исходным .txt).
# GEMINI_USER_DATA_DIR — профиль Chrome для Playwright.
STORIES_DIR = _gemini_env_path("GEMINI_STORIES_DIR", PROJECT_DIR / "stories")
USER_DATA_DIR = _gemini_env_path("GEMINI_USER_DATA_DIR", PROJECT_DIR / "user_data")
LOG_FILE_PATH = _gemini_env_path("GEMINI_LOG_FILE", PROJECT_DIR / "run.log")
INFO_FILE_NAME = "info.txt"
REPORT_FILE_NAME = "result_report.txt"
GENRE_REPORT_FILE_NAME = "genre_report.txt"
STAGED_MARKER_NAME = "ORCHESTRATOR_STAGED.json"
PROCESSED_MARKER_NAME = "ORCHESTRATOR_PROCESSED.json"
POLICY_REFUSAL_MARKER_NAME = "ORCHESTRATOR_POLICY_REFUSAL.json"
PERSISTENT_INBOX = os.getenv("GEMINI_PERSISTENT_INBOX", "0").strip() == "1"
PERSISTENT_IDLE_SEC = float((os.getenv("GEMINI_PERSISTENT_IDLE_SEC", "30") or "30").strip() or "30")
PERSISTENT_NO_IDLE_EXIT = (os.getenv("GEMINI_PERSISTENT_NO_IDLE_EXIT", "0").strip() == "1") or (
    float(os.getenv("GEMINI_PERSISTENT_IDLE_SEC", "30") or "30") <= 0
)
PERSISTENT_MAX_STORIES = int((os.getenv("GEMINI_PERSISTENT_MAX_STORIES", "0") or "0").strip() or "0")
PERSISTENT_MAX_LIFETIME_MIN = float((os.getenv("GEMINI_PERSISTENT_MAX_LIFETIME_MIN", "60") or "60").strip() or "60")
PERSISTENT_STOP_FILE = (os.getenv("GEMINI_PERSISTENT_STOP_FILE") or "").strip()
BROWSER_SESSION_ID = (os.getenv("GEMINI_BROWSER_SESSION_ID") or "").strip()
WORKER_ID = (os.getenv("GEMINI_WORKER_ID") or os.getenv("WORKER_ID") or "").strip()
GEM_SYSTEM_PROMPT_FILE = PROJECT_DIR / "gem_system_prompt.txt"
GEM_BOT_NAME = "YouTube-Safe Editor"
def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.getenv(name, str(default)) or str(default)).strip()
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.getenv(name, str(default)) or str(default)).strip()
        return float(raw)
    except ValueError:
        return default


# Ожидание ответа/генерации (думающая модель + длинные чанки): по умолчанию 10 мин; было 3 мин — частые таймауты.
WAIT_TIMEOUT_MS = _env_int("GEMINI_WAIT_TIMEOUT_MS", 600_000)
POST_MODEL_STABILIZE_SECONDS = float(
    (os.getenv("GEMINI_POST_MODEL_STABILIZE_SEC", "4") or "4").strip() or "4"
)
SAFE_MAX_MODEL_SELECTION_ATTEMPTS = max(
    1, _env_int("GEMINI_SAFE_MAX_MODEL_SELECT_ATTEMPTS", 3)
)
SLOW_MO_MS = 0
PAUSE_MIN_SECONDS = 0
PAUSE_MAX_SECONDS = 0
REPORT_FILE_PATTERN = re.compile(r"^result_report(-\d+)?\.txt$", re.IGNORECASE)
YOUTUBE_STATUS_LINE_PATTERN = re.compile(
    r"^\s*подходит\s+для\s+youtube\s*:\s*(да|нет)\s*$",
    re.IGNORECASE,
)
# Фразы из UI Gemini про лимит. Не использовать короткие подстроки («квот», «try again later») —
# в тексте чата/вставленном рассказе они дают ложные срабатывания и ротацию аккаунта.
LIMIT_HINTS = [
    "превышен лимит",
    "превышен лимит запросов",
    "достигнут лимит",
    "исчерпан лимит",
    "лимит исчерпан",
    "quota exceeded",
    "rate limit exceeded",
    "слишком много запросов",
    "temporarily unavailable",
    "service unavailable",
    "не удаётся обработать запрос",
    "daily limit",
    "usage limit",
    "запросы временно огранич",
    "временно недоступ",
    # целиком типичные формулировки Google (не короткие куски вроде «can't generate»)
    "unable to generate a response",
    "can't generate a response",
    "cannot generate a response",
]
# Короткий ответ модели (ошибка вместо текста) — доп. проверка без всего body.
LIMIT_HINTS_SHORT_RESPONSE = [
    "quota exceeded",
    "rate limit",
    "unable to generate",
    "can't generate",
    "cannot generate",
    "превышен лимит",
    "достигнут лимит",
    "лимит исчерпан",
    "исчерпан лимит",
]
EXIT_CODE_OK = 0
EXIT_CODE_FILE_LIMIT = 42
EXIT_CODE_ERROR = 1
COPY_RETRIES = 3
IDLE_STABLE_ROUNDS = 4
HEARTBEAT_SECONDS = 30
# Подряд N статусов «нет кнопки Остановить» без генерации → Лимит Gem-бота (0 = выключить)
STUCK_HEARTBEATS_BEFORE_LIMIT = _env_int("GEMINI_STUCK_HEARTBEATS_BEFORE_LIMIT", 10)
# Подряд N статусов «генерация или обновление блока» / ожидание копирования без завершения → лимит (0 = выключить)
STUCK_GENERATION_HEARTBEATS_BEFORE_LIMIT = _env_int("GEMINI_STUCK_GENERATION_HEARTBEATS_BEFORE_LIMIT", 15)
# Пауза после перехода на /u/N/ (смена аккаунта в одном Chrome-профиле), сек.
try:
    GEMINI_AUTHUSER_HUB_PAUSE_SEC = float(
        (os.getenv("GEMINI_AUTHUSER_HUB_PAUSE_SEC", "1.2") or "1.2").strip() or "1.2"
    )
except ValueError:
    GEMINI_AUTHUSER_HUB_PAUSE_SEC = 1.2
TRANSIENT_RETRY_BACKOFF_SECONDS = [60, 60, 180, 300, 1200, 1800]
LIMIT_RETRY_BACKOFF_SECONDS = [60, 180, 300, 300, 300, 300, 300, 300, 300, 300]
# После серии временных сбоев: по умолчанию 10 мин вместо 3 ч (переопределение: GEMINI_LONG_PAUSE_ON_FAIL_SEC)
LONG_PAUSE_SECONDS = _env_int("GEMINI_LONG_PAUSE_ON_FAIL_SEC", 600)
BOT_OPEN_RETRY_CYCLES = _env_int("GEMINI_BOT_OPEN_RETRY_CYCLES", 2)
SEND_BUTTON_TIMEOUT_MS = _env_int("GEMINI_SEND_BUTTON_TIMEOUT_MS", 45_000)
# После insert_text длинного чанка кнопка «Отправить» может долго оставаться disabled
SEND_READY_TIMEOUT_MS = _env_int("GEMINI_SEND_READY_TIMEOUT_MS", 180_000)
# Playwright по умолчанию 30s мало для тяжёлого Gemini UI / сети
GEMINI_GOTO_TIMEOUT_MS = _env_int("GEMINI_GOTO_TIMEOUT_MS", 120_000)
INSERT_SETTLE_MS = _env_int("GEMINI_INSERT_SETTLE_MS", 400)
# Если за это время нет ни Stop, ни нового блока — одна повторная попытка отправки
RESPONSE_ACTIVITY_SEC = _env_int("GEMINI_RESEND_IF_NO_ACTIVITY_SEC", 45)
# Несколько Gem-ботов: см. gemini_bots.json (email → url) или GEMINI_URLS=url1,url2
GEMINI_BOTS_CONFIG = (os.getenv("GEMINI_BOTS_CONFIG") or str(PROJECT_DIR / "gemini_bots.json")).strip()
ROTATE_ON_LIMIT = (os.getenv("GEMINI_ROTATE_ON_LIMIT", "1") or "1").strip().lower() not in ("0", "false", "no")
# Во время ожидания кнопки «Отправить» не вызывать проверку лимита по тексту страницы (в main попадает текст рассказа).
GEMINI_LIMIT_CHECK_WHILE_SEND_WAIT = (os.getenv("GEMINI_LIMIT_CHECK_WHILE_SEND_WAIT", "0") or "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
ROTATE_AFTER_TRANSIENT_EXHAUSTED = (os.getenv("GEMINI_ROTATE_AFTER_TRANSIENT", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
PROGRESS_VERSION = 1
WAIT_OVERRIDE_KEY = "w"
# Латиница: в фокусе должна быть консоль, не окно Chrome. Переопределение: GEMINI_FORCE_ROTATE_KEY= x
FORCE_ROTATE_KEY = ((os.getenv("GEMINI_FORCE_ROTATE_KEY", "r") or "r").strip().lower()[:1] or "r")
RESPONSE_MARKER_PREFIX = "REQUEST_MARKER"


class ForceRotateBot(Exception):
    """Принудительная смена Gem-бота по горячей клавише в консоли (см. drain_console_hotkeys)."""


def drain_console_hotkeys() -> tuple[bool, bool]:
    """
    Считывает буфер консоли Windows: (нажата W — пропуск длинного ожидания, нажата R — следующий бот).
    """
    skip_wait = False
    force_rotate = False
    while msvcrt.kbhit():
        try:
            key = msvcrt.getwch()
        except Exception:
            break
        if not key:
            continue
        k = key.lower()
        if k == WAIT_OVERRIDE_KEY:
            skip_wait = True
        if k == FORCE_ROTATE_KEY:
            force_rotate = True
    return skip_wait, force_rotate


def sleep_interruptible(
    seconds: float,
    *,
    allow_skip_w: bool = False,
    step_sec: float = 0.35,
) -> None:
    """Сон короткими кусками: R — ForceRotateBot; W — прервать сон, если allow_skip_w."""
    if seconds <= 0:
        return
    deadline = time.time() + seconds
    while time.time() < deadline:
        skip, rotate = drain_console_hotkeys()
        if rotate:
            raise ForceRotateBot(
                "Принудительная смена Gem-бота (клавиша в консоли). Следующий аккаунт из gemini_bots.json."
            )
        if allow_skip_w and skip:
            return
        time.sleep(min(step_sec, max(0.0, deadline - time.time())))
# Размер чанков (символы) и пауза между запросами (анти rate limit). Задаются через окружение.
CHUNK_MIN_CHARS = int((os.getenv("CHUNK_MIN_CHARS", "3000") or "3000").strip() or "3000")
CHUNK_MAX_CHARS = int((os.getenv("CHUNK_MAX_CHARS", "4000") or "4000").strip() or "4000")
CHUNK_PAUSE_MIN_SEC = int((os.getenv("CHUNK_PAUSE_MIN_SEC", "5") or "5").strip() or "5")
CHUNK_PAUSE_MAX_SEC = int((os.getenv("CHUNK_PAUSE_MAX_SEC", "15") or "15").strip() or "15")
# Итоговый файл: <имя_исходника>_clean.txt (рядом с исходным .txt)
CLEAN_FILE_SUFFIX = "_clean.txt"
CLEAN_FILE_TMP_SUFFIX = "_clean.tmp"
# Чекпоинт для возобновления с середины рассказа: <stem>_clean.progress.json
PROGRESS_FILE_SUFFIX = "_clean.progress.json"

PROMPT_INPUT_SELECTORS = [
    'rich-textarea div[contenteditable="true"]',
    'div[contenteditable="true"][aria-label]',
    'div[contenteditable="true"][role="textbox"]',
    "textarea",
]

SEND_BUTTON_SELECTORS = [
    'button[aria-label*="Отправить сообщение"]',
    'button[aria-label*="Отправ"]',
    'button[aria-label*="Send"]',
    'button:has-text("Отправить")',
    'button:has-text("Send")',
]

COPY_BUTTON_SELECTORS = [
    'button[aria-label*="Копировать"]',
    'button[aria-label*="Copy"]',
    'button:has-text("Копировать")',
    'button:has-text("Copy")',
]
STOP_BUTTON_SELECTORS = [
    'button[aria-label*="Останов"]',
    'button[aria-label*="Stop"]',
]

BOT_DELETED_HINTS = [
    "gem-бот, который был удалён",
    "gem-бот, который был удален",
    "gem-бот удалён",
    "gem-бот удален",
    "бот был удалён",
    "бот был удален",
    "который был удалён",
    "который был удален",
    "в этом чате участвовал gem-бот",
    "gem was deleted",
    "gem has been deleted",
    "bot was deleted",
    "создайте другого gem-бота",
    "create a new gem",
]

ATTACHED_FILE_SELECTORS = [
    'button[aria-label*=".txt"]',
    '[data-testid*="attachment"]',
    '[data-test-id*="attachment"]',
]
MODE_MENU_TRIGGER_SELECTORS = [
    'button[aria-label*="Открыть меню выбора режима"]',
    'button[aria-label*="Open mode selection menu"]',
    'button[aria-label*="Open mode menu"]',
    'button[aria-label*="Выбор режима"]',
    'button[aria-label*="Select mode"]',
    'button[aria-label*="Model mode"]',
    'button[aria-label*="режим модели"]',
]
RESPONSE_BLOCK_SELECTORS = [
    '[data-test-id="message-content"]',
    '[data-test-id*="response"]',
    "message-content",
    "model-response",
    "div.markdown",
    "article",
]


class TeeStream:
    def __init__(self, console_stream, log_stream):
        self.console_stream = console_stream
        self.log_stream = log_stream

    def write(self, data: str) -> int:
        try:
            written = self.console_stream.write(data)
        except UnicodeEncodeError:
            enc = getattr(self.console_stream, "encoding", None) or "utf-8"
            safe = data.encode(enc, errors="replace").decode(enc, errors="replace")
            written = self.console_stream.write(safe)
        self.log_stream.write(data)
        return written

    def flush(self) -> None:
        self.console_stream.flush()
        self.log_stream.flush()

    def isatty(self) -> bool:
        try:
            return self.console_stream.isatty()
        except Exception:
            return False


def setup_dual_logging() -> None:
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PERSISTENT_INBOX:
        # PersistentSafeSession already captures stdout/stderr and writes the bridge log.
        # Avoid writing the same line twice to persistent_session.log from child and parent.
        print("")
        print("=" * 80)
        print(f"[SESSION] Старт: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"[SESSION] Лог-файл: {LOG_FILE_PATH}")
        return
    log_stream = open(LOG_FILE_PATH, mode="a", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = TeeStream(sys.__stdout__, log_stream)
    sys.stderr = TeeStream(sys.__stderr__, log_stream)
    print("")
    print("=" * 80)
    print(f"[SESSION] Старт: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[SESSION] Лог-файл: {LOG_FILE_PATH}")


def human_pause(step_name: str) -> None:
    seconds = random.randint(PAUSE_MIN_SECONDS, PAUSE_MAX_SECONDS)
    print(f"[PAUSE] {step_name}: жду {seconds} сек.")
    sleep_interruptible(
        float(seconds),
        allow_skip_w=(PAUSE_MIN_SECONDS > 0 or PAUSE_MAX_SECONDS > 0),
    )


def format_duration(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}ч {minutes}м {sec}с"
    if minutes:
        return f"{minutes}м {sec}с"
    return f"{sec}с"


def wait_with_status(total_seconds: int, reason: str) -> None:
    if total_seconds <= 0:
        return
    print(f"[WAIT] {reason}. Пауза: {format_duration(total_seconds)}")
    start_ts = time.time()
    remaining = total_seconds
    while remaining > 0:
        if consume_wait_override_key():
            print(f"[WAIT] Принудительный пропуск ожидания клавишей '{WAIT_OVERRIDE_KEY}'.")
            break
        chunk = min(HEARTBEAT_SECONDS, remaining)
        time.sleep(chunk)
        elapsed = int(time.time() - start_ts)
        remaining = max(0, total_seconds - elapsed)
        print(f"[WAIT] {reason}. Осталось: {format_duration(remaining)}")
    print(f"[WAIT] {reason}. Продолжаю работу.")


def wait_for_prompt_input(page: Page, timeout_ms: int = 30_000) -> Locator:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        skip, rotate = drain_console_hotkeys()
        if rotate:
            raise ForceRotateBot(
                "Принудительная смена Gem-бота (клавиша в консоли). Следующий аккаунт из gemini_bots.json."
            )
        if skip:
            pass
        for selector in PROMPT_INPUT_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=800)
                return locator
            except TimeoutError:
                continue
        time.sleep(0.2)
    raise TimeoutError("Не найдено поле ввода Gemini.")


def consume_wait_override_key() -> bool:
    """W — пропуск ожидания; R — бросает ForceRotateBot (принудительная ротация бота)."""
    skip, rotate = drain_console_hotkeys()
    if rotate:
        raise ForceRotateBot(
            "Принудительная смена Gem-бота (клавиша в консоли). Следующий аккаунт из gemini_bots.json."
        )
    return skip


def is_login_screen_visible(page: Page) -> bool:
    current_url = (page.url or "").lower()
    if "accounts.google.com" not in current_url:
        return False
    login_url_hints = [
        "/signin",
        "/identifier",
        "/challenge",
        "/accountchooser",
        "/v3/signin",
        "service=gemini",
    ]
    if any(hint in current_url for hint in login_url_hints):
        return True
    login_form_selectors = [
        'input[type="email"]',
        'input[type="password"]',
        'input[name="identifier"]',
        "#identifierId",
    ]
    for selector in login_form_selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0 and locator.is_visible():
                return True
        except Exception:
            continue
    return False


def _normalize_text_for_match(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("ё", "е").replace("—", "-").replace("–", "-")
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_bot_deleted_visible(page: Page) -> bool:
    """Проверяет, показывает ли страница сообщение об удалённом/недоступном Gem-боте."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    cur_url = (page.url or "").lower()
    if "/gem/" not in cur_url:
        return False
    try:
        body_text = (page.locator("body").inner_text(timeout=5000) or "").lower()
    except Exception:
        return False
    if len(body_text.strip()) < 10:
        return False
    normalized_body = _normalize_text_for_match(body_text)
    normalized_hints = [_normalize_text_for_match(hint) for hint in BOT_DELETED_HINTS]
    generic_deleted_chat = (
        "в этом чате участвовал" in normalized_body
        and "удален" in normalized_body
        and "gem" in normalized_body
    )
    hit = generic_deleted_chat or any(hint in normalized_body for hint in normalized_hints)
    if hit:
        print(f"[DEBUG] is_bot_deleted_visible=True; url={page.url}; body_snippet={normalized_body[:200]}")
        sys.stdout.flush()
    return hit


def _load_gem_system_prompt() -> str:
    if GEM_SYSTEM_PROMPT_FILE.is_file():
        return GEM_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    return ""


def _dump_page_debug(page: Page, label: str) -> None:
    """Скриншот + первые 500 символов body для диагностики UI."""
    try:
        dbg_dir = PROJECT_DIR / "debug_screenshots"
        dbg_dir.mkdir(parents=True, exist_ok=True)
        ss_path = dbg_dir / f"debug_{label}_{int(time.time())}.png"
        page.screenshot(path=str(ss_path), full_page=False)
        print(f"[GEM-DEBUG] Скриншот: {ss_path}")
    except Exception as exc:
        print(f"[GEM-DEBUG] Не удалось сделать скриншот: {exc}")
    try:
        snippet = (page.locator("body").inner_text(timeout=5000) or "")[:500]
        print(f"[GEM-DEBUG] body text ({label}): {snippet}")
    except Exception:
        pass
    try:
        editables = page.locator('[contenteditable="true"]').count()
        textareas = page.locator('textarea').count()
        inputs = page.locator('input').count()
        print(f"[GEM-DEBUG] Элементы: contenteditable={editables}, textarea={textareas}, input={inputs}")
    except Exception:
        pass
    sys.stdout.flush()


def _extract_create_gem_href(page: Page) -> str | None:
    """Пробует достать URL создания Gem из текущей страницы удаления."""
    script = """
() => {
  const norm = (s) => (s || '').toLowerCase().replace(/ё/g, 'е');
  const needles = [
    'создайте другого gem-бота',
    'создайте другого gem бота',
    'create another gem',
    'new gem'
  ];
  const isMatch = (txt) => {
    const t = norm(txt);
    return needles.some(n => t.includes(n));
  };

  const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],[href]'));
  const isAllowedHref = (href) => {
    const h = (href || '').trim();
    if (!h) return false;
    if (/signoutoptions|accounts\\.google\\.com/i.test(h)) return false;
    if (/^https?:\\/\\//i.test(h) && !/gemini\\.google\\.com/i.test(h)) return false;
    return /\\/gems\\b|\\/gem\\//i.test(h);
  };

  for (const n of nodes) {
    const txt = norm((n.innerText || n.textContent || '').trim());
    if (!txt) continue;
    if (!isMatch(txt)) continue;
    const href = n.getAttribute('href') || n.href || '';
    if (isAllowedHref(href)) return href;
  }

  for (const a of Array.from(document.querySelectorAll('a[href]'))) {
    const href = (a.getAttribute('href') || '').trim();
    if (!href) continue;
    const txt = norm((a.innerText || a.textContent || '').trim());
    if ((isMatch(txt) || /\\/gems\\b/i.test(href) || /\\/gem\\//i.test(href)) && isAllowedHref(href)) return href;
  }
  return null;
}
"""
    try:
        href = page.evaluate(script)
    except Exception:
        return None
    if not href:
        return None
    href = str(href).strip()
    if href.startswith("/"):
        return f"https://gemini.google.com{href}"
    return href


def _extract_gem_editor_href(page: Page) -> str | None:
    """Ищет на витрине gems/view ссылку, ведущую в редактор создания Gem."""
    script = """
() => {
  const norm = (s) => (s || '').toLowerCase().replace(/ё/g, 'е');
  const allowedHost = (href) => !href || /gemini\\.google\\.com/i.test(href) || href.startsWith('/');
  const isGoodPath = (href) => /\\/gems\\/(create|new|editor|edit|builder)|gem\\//i.test(href || '');

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  for (const a of anchors) {
    const href = (a.getAttribute('href') || '').trim();
    if (!href || !allowedHost(href)) continue;
    const txt = norm((a.innerText || a.textContent || '').trim());
    if ((txt.includes('создать') || txt.includes('new gem') || txt.includes('create')) && isGoodPath(href)) return href;
  }
  for (const a of anchors) {
    const href = (a.getAttribute('href') || '').trim();
    if (!href || !allowedHost(href)) continue;
    if (isGoodPath(href)) return href;
  }
  return null;
}
"""
    try:
        href = page.evaluate(script)
    except Exception:
        return None
    if not href:
        return None
    href = str(href).strip()
    if href.startswith("/"):
        return f"https://gemini.google.com{href}"
    return href


def _extract_chat_gem_href(page: Page) -> str | None:
    """Ищет на текущей странице ссылку вида /gem/<id> для перехода в чат."""
    script = """
() => {
  const nodes = Array.from(document.querySelectorAll('a[href],button,[role="button"]'));
  const txtOk = (t) => /начать\\s*чат|start\\s*chat|open\\s*chat|перейти\\s*в\\s*чат/i.test(t || '');
  for (const n of nodes) {
    const href = (n.getAttribute && n.getAttribute('href')) || n.href || '';
    const txt = (n.innerText || n.textContent || '').trim();
    if (href && /\\/gem\\/[A-Za-z0-9-]+/i.test(href)) return href;
    if (txtOk(txt) && href) return href;
  }
  return null;
}
"""
    try:
        href = page.evaluate(script)
    except Exception:
        return None
    if not href:
        return None
    href = str(href).strip()
    if href.startswith("/"):
        return f"https://gemini.google.com{href}"
    return href


def _derive_chat_url_from_edit_url(url: str) -> str | None:
    """
    /u/N/gems/edit/<id> -> /u/N/gem/<id>
    Используем как fallback, если UI-кнопка "Начать чат" не нажимается.
    """
    m = re.search(r"^(https://gemini\.google\.com(?:/u/\d+)?)/gems/edit/([A-Za-z0-9-]+)", (url or "").strip(), re.IGNORECASE)
    if not m:
        return None
    return f"{m.group(1)}/gem/{m.group(2)}"


def _click_first_visible(page: Page, selectors: list[str], *, timeout_ms: int = 2500) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout_ms):
                loc.click()
                print(f"[GEM-CREATE] Клик: {sel}")
                return True
        except Exception:
            continue
    return False


def _fill_first_visible(page: Page, selectors: list[str], value: str, label: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2500):
                loc.click()
                time.sleep(0.2)
                tag = (loc.evaluate("el => el.tagName.toLowerCase()") or "").lower()
                if tag == "textarea" or tag == "input":
                    loc.fill(value)
                else:
                    loc.evaluate(
                        "(el, text) => { el.innerText = text; el.dispatchEvent(new Event('input', {bubbles:true})); }",
                        value,
                    )
                print(f"[GEM-CREATE] Заполнил {label} через {sel}")
                return True
        except Exception:
            continue
    return False


def _fill_gem_editor_fields(page: Page, name_text: str, description_text: str, instructions_text: str) -> dict[str, bool]:
    """Пытается заполнить Название/Описание/Инструкции по семантическим меткам полей."""
    script = r"""
(payload) => {
  const norm = (s) => (s || '').toLowerCase().replace(/ё/g, 'е');
  const getCtx = (el) => {
    const attrs = [
      el.getAttribute('aria-label') || '',
      el.getAttribute('placeholder') || '',
      el.getAttribute('name') || '',
      el.getAttribute('id') || '',
      el.getAttribute('data-testid') || '',
    ].join(' ');
    const parent = el.closest('label,[role="group"],section,article,div') || el.parentElement;
    const ptxt = parent ? (parent.innerText || '').slice(0, 300) : '';
    return norm(attrs + ' ' + ptxt);
  };
  const setVal = (el, text) => {
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') {
      el.focus();
      el.value = text;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    if (el.isContentEditable) {
      el.focus();
      el.innerText = text;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
  };
  const fields = Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]'))
    .filter(el => {
      const st = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return st.display !== 'none' && st.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    });

  const res = { name: false, description: false, instructions: false };
  for (const el of fields) {
    const ctx = getCtx(el);
    if (!res.name && /(назв|name|title)/.test(ctx) && !/(опис|description|инструк|instruction)/.test(ctx)) {
      setVal(el, payload.name);
      res.name = true;
      continue;
    }
    if (!res.description && /(опис|description|about)/.test(ctx) && !/(инструк|instruction)/.test(ctx)) {
      setVal(el, payload.description);
      res.description = true;
      continue;
    }
    if (!res.instructions && /(инструк|instruction|custom instructions|guide)/.test(ctx)) {
      setVal(el, payload.instructions);
      res.instructions = true;
      continue;
    }
  }
  return res;
}
"""
    try:
        return page.evaluate(
            script,
            {"name": name_text, "description": description_text, "instructions": instructions_text},
        ) or {"name": False, "description": False, "instructions": False}
    except Exception:
        return {"name": False, "description": False, "instructions": False}


def _fill_largest_instructions_field(page: Page, instructions_text: str) -> bool:
    """Fallback: пишет инструкцию в самое большое текстовое поле редактора (обычно это Instructions)."""
    script = r"""
(text) => {
  const norm = (s) => (s || '').toLowerCase().replace(/ё/g, 'е');
  const fields = Array.from(document.querySelectorAll('textarea,[contenteditable="true"]'))
    .filter(el => {
      const st = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      if (st.display === 'none' || st.visibility === 'hidden' || rect.width < 40 || rect.height < 20) return false;
      const ctx = norm([
        el.getAttribute('aria-label') || '',
        el.getAttribute('placeholder') || '',
        el.getAttribute('name') || '',
        (el.closest('label,[role="group"],section,article,div')?.innerText || '').slice(0, 200),
      ].join(' '));
      // Отсекаем явное поле названия
      if (/(назв|name|title)/.test(ctx) && !/(инструк|instruction)/.test(ctx)) return false;
      return true;
    });
  if (!fields.length) return false;
  fields.sort((a, b) => {
    const ra = a.getBoundingClientRect();
    const rb = b.getBoundingClientRect();
    return (rb.width * rb.height) - (ra.width * ra.height);
  });
  const el = fields[0];
  if (el.tagName.toLowerCase() === 'textarea') {
    el.focus();
    el.value = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  if (el.isContentEditable) {
    el.focus();
    el.innerText = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }
  return false;
}
"""
    try:
        return bool(page.evaluate(script, instructions_text))
    except Exception:
        return False


def _fill_gem_editor_by_geometry(page: Page, name_text: str, description_text: str, instructions_text: str) -> dict[str, bool]:
    """
    Fallback для /gems/edit/*: заполняет поля по размеру/типу.
    - короткое поле (input или маленький textarea/contenteditable) -> Название
    - самое большое поле -> Инструкции
    - второе по размеру -> Описание
    """
    script = r"""
(payload) => {
  const vis = (el) => {
    const st = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 40 && r.height > 16;
  };
  const all = Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]')).filter(vis);
  const toEntry = (el) => ({ el, rect: el.getBoundingClientRect(), tag: (el.tagName || '').toLowerCase(), ce: !!el.isContentEditable });
  const entries = all.map(toEntry);
  if (!entries.length) return { name: false, description: false, instructions: false };

  const setVal = (e, text) => {
    const el = e.el;
    if (e.tag === 'input' || e.tag === 'textarea') {
      el.focus();
      el.value = text;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    if (e.ce) {
      el.focus();
      el.innerText = text;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }
    return false;
  };

  const out = { name: false, description: false, instructions: false };

  const nameCand = entries
    .filter(e => e.tag === 'input' || e.rect.height < 80)
    .sort((a,b) => (a.rect.height - b.rect.height) || (a.rect.top - b.rect.top))[0];
  if (nameCand) out.name = setVal(nameCand, payload.name);

  const areas = entries
    .filter(e => e.tag !== 'input' && e.rect.height >= 40)
    .sort((a,b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));

  if (areas[0]) out.instructions = setVal(areas[0], payload.instructions);
  if (areas[1]) out.description = setVal(areas[1], payload.description);

  return out;
}
"""
    try:
        return page.evaluate(
            script,
            {"name": name_text, "description": description_text, "instructions": instructions_text},
        ) or {"name": False, "description": False, "instructions": False}
    except Exception:
        return {"name": False, "description": False, "instructions": False}


def create_gem_bot(page: Page, authuser_idx: int) -> str | None:
    """
    Создаёт нового Gem-бота через UI Gemini для аккаунта /u/{authuser_idx}/.
    Возвращает новый URL gem-бота или None при неудаче.
    """
    prompt_text = _load_gem_system_prompt()
    if not prompt_text:
        print("[ERROR] Файл gem_system_prompt.txt пуст или не найден — не могу создать Gem-бота.")
        return None

    print(f"[GEM-CREATE] Создаю нового Gem-бота на /u/{authuser_idx}/ по фиксированному сценарию...")
    sys.stdout.flush()

    # Шаг 1: строго открыть витрину Gems нужного аккаунта
    view_url = f"https://gemini.google.com/u/{authuser_idx}/gems/view?hl=ru"
    try:
        page.goto(view_url, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(3)
    except Exception as exc:
        print(f"[GEM-CREATE] Не удалось открыть {view_url}: {exc}")
        return None
    _dump_page_debug(page, "gems_view_loaded")

    # Шаг 2: нажать "Создать Gem-бота"
    create_selectors = [
        'button:has-text("Создать Gem-бота")',
        'a:has-text("Создать Gem-бота")',
        'button:has-text("Create Gem")',
        'a:has-text("Create Gem")',
        'button:has-text("Create a Gem")',
        'a:has-text("Create a Gem")',
        'button:has-text("Новый Gem")',
        'a:has-text("Новый Gem")',
        'button:has-text("Создать")',
        'a:has-text("Создать")',
    ]
    clicked_create = _click_first_visible(page, create_selectors, timeout_ms=3500)
    if not clicked_create:
        href = _extract_gem_editor_href(page) or _extract_create_gem_href(page)
        if href:
            try:
                print(f"[GEM-CREATE] Перехожу в редактор по href: {href}")
                page.goto(href, wait_until="domcontentloaded", timeout=90_000)
                clicked_create = True
            except Exception as exc:
                print(f"[GEM-CREATE] Не удалось перейти по href редактора: {exc}")
    if not clicked_create:
        _dump_page_debug(page, "no_create_button")
        print("[GEM-CREATE] Не найдена кнопка/ссылка 'Создать Gem-бота'.")
        return None

    time.sleep(3)
    _dump_page_debug(page, "gem_editor_opened")

    # Сначала критично заполняем Название + Инструкцию; Описание вторично.
    sem = _fill_gem_editor_fields(page, GEM_BOT_NAME, "", prompt_text)
    print(f"[GEM-CREATE] Семантическое заполнение: {sem}")

    # --- Имя бота ---
    name_selectors = [
        'div[contenteditable="true"][aria-label*="Название" i]',
        'div[contenteditable="true"][aria-label*="Name" i]',
        'input[name*="name" i]',
        'input[aria-label*="Name" i]',
        'input[aria-label*="Название" i]',
        'input[placeholder*="Name" i]',
        'input[placeholder*="Название" i]',
        'input[placeholder*="Имя" i]',
    ]
    name_filled = bool(sem.get("name")) or _fill_first_visible(page, name_selectors, GEM_BOT_NAME, "Название")
    if not name_filled:
        print("[GEM-CREATE] Поле Название не найдено.")

    time.sleep(1)

    # --- Описание (любое) ---
    description_selectors = [
        'textarea[aria-label*="Описание" i]',
        'textarea[aria-label*="Description" i]',
        'textarea[placeholder*="Описание" i]',
        'textarea[placeholder*="Description" i]',
        'div[contenteditable="true"][aria-label*="Описание" i]',
        'div[contenteditable="true"][aria-label*="Description" i]',
    ]
    description_text = "Автосозданный Gem-бот для очистки текста под YouTube."
    _fill_first_visible(page, description_selectors, description_text, "Описание")

    # --- Инструкция ---
    instructions_selectors = [
        'textarea[aria-label*="Инструк" i]',
        'textarea[aria-label*="Instruction" i]',
        'textarea[aria-label*="nstruct" i]',
        'textarea[placeholder*="nstruct" i]',
        'textarea[placeholder*="Инструк" i]',
        'div[contenteditable="true"][aria-label*="Инструк" i]',
        'div[contenteditable="true"][aria-label*="nstruct" i]',
    ]
    instructions_filled = bool(sem.get("instructions")) or _fill_first_visible(
        page, instructions_selectors, prompt_text, "Инструкция"
    )
    if not instructions_filled:
        instructions_filled = _fill_largest_instructions_field(page, prompt_text)
        if instructions_filled:
            print("[GEM-CREATE] Инструкции вставлены fallback-методом (крупнейшее текстовое поле).")
    if not instructions_filled or not name_filled:
        geo = _fill_gem_editor_by_geometry(
            page,
            GEM_BOT_NAME,
            "Автосозданный Gem-бот для очистки текста под YouTube.",
            prompt_text,
        )
        print(f"[GEM-CREATE] Геометрический fallback: {geo}")
        name_filled = name_filled or bool(geo.get("name"))
        instructions_filled = instructions_filled or bool(geo.get("instructions"))
    if not instructions_filled:
        _dump_page_debug(page, "no_instructions_field")
        print("[GEM-CREATE] Не удалось вставить инструкции — поле не найдено.")
        return None

    time.sleep(1)
    _dump_page_debug(page, "before_save")

    # --- Сохранить ---
    save_selectors = [
        'button:has-text("Сохранить")',
        'button:has-text("Save")',
        'button[aria-label*="Save" i]',
        'button[aria-label*="Сохранить" i]',
        'button:has-text("Создать")',
        'button:has-text("Create")',
    ]
    saved = _click_first_visible(page, save_selectors, timeout_ms=4000)
    if not saved:
        print("[GEM-CREATE] Не удалось нажать кнопку сохранения.")
        _dump_page_debug(page, "no_save_button")
        return None

    # --- Начать чат (обязательно) ---
    time.sleep(2)
    start_chat_selectors = [
        '[role="dialog"] button:has-text("Начать чат")',
        '[role="dialog"] a:has-text("Начать чат")',
        '[role="dialog"] button:has-text("Start chat")',
        '[role="dialog"] a:has-text("Start chat")',
        'button:has-text("Начать чат")',
        'a:has-text("Начать чат")',
        'button:has-text("Start chat")',
        'a:has-text("Start chat")',
        'button:has-text("Перейти в чат")',
        'a:has-text("Перейти в чат")',
        'button:has-text("Open chat")',
        'a:has-text("Open chat")',
    ]

    for wait_round in range(20):
        # Часто мешает модалка "Gem-бот создан": жмем Esc и пробуем force-click
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        clicked = False
        for sel in start_chat_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=1200):
                    loc.click(force=True)
                    print(f"[GEM-CREATE] Клик Начать чат: {sel}")
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            _click_first_visible(page, start_chat_selectors, timeout_ms=1200)
        direct_chat_href = _extract_chat_gem_href(page)
        if direct_chat_href:
            try:
                page.goto(direct_chat_href, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass
        else:
            derived = _derive_chat_url_from_edit_url(page.url or "")
            if derived:
                try:
                    print(f"[GEM-CREATE] Fallback переход в чат по edit->gem: {derived}")
                    page.goto(derived, wait_until="domcontentloaded", timeout=60_000)
                except Exception:
                    pass
        time.sleep(2)
        new_url = page.url or ""
        if "/gem/" in new_url and "/gems/" not in new_url and "gemini.google.com" in new_url:
            clean_url = new_url.split("?")[0].rstrip("/")
            print(f"[GEM-CREATE] Новый Gem-бот создан: {clean_url}")
            sys.stdout.flush()
            return clean_url

    final_url = page.url or ""
    print(f"[GEM-CREATE] URL после сохранения не содержит /gem/: {final_url}")
    _dump_page_debug(page, "after_save_no_gem_url")
    return None


def update_bots_json_url(email: str, new_url: str) -> bool:
    """Обновляет url для указанного email в gemini_bots.json."""
    config_path = Path(GEMINI_BOTS_CONFIG)
    if not config_path.is_absolute():
        config_path = PROJECT_DIR / config_path
    if not config_path.is_file():
        return False
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, list):
        return False
    updated = False
    for item in raw:
        if isinstance(item, dict) and (item.get("email") or "").strip().lower() == email.strip().lower():
            item["url"] = new_url
            updated = True
    if updated:
        config_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[GEM-CREATE] gemini_bots.json обновлён: {email} → {new_url}")
    return updated


def try_recreate_deleted_gem(page: Page, entry: "GemBotEntry") -> str | None:
    """Пытается пересоздать удалённый Gem-бот и обновить конфиг."""
    authuser = gemini_authuser_index(entry.url)
    if authuser is None:
        authuser = gemini_authuser_index(entry.hub_url) if entry.hub_url else None
    if authuser is None:
        print("[GEM-CREATE] Не удалось определить /u/N/ для пересоздания Gem.")
        return None
    new_url = create_gem_bot(page, authuser)
    if new_url and entry.email:
        update_bots_json_url(entry.email, new_url)
    return new_url


INITIAL_GEMINI_LOAD_TIMEOUT_SEC = _env_int("GEMINI_INITIAL_LOAD_TIMEOUT_SEC", 180)
MAX_GEMINI_PAGE_RELOADS = _env_int("GEMINI_MAX_PAGE_RELOADS", 2)
GEMINI_RELOAD_PAUSE_SEC = float(_env_int("GEMINI_RELOAD_PAUSE_SEC", 3))


def wait_for_prompt_input_soft(page: Page, *, timeout_ms: int = 5000) -> bool:
    try:
        wait_for_prompt_input(page, timeout_ms=timeout_ms)
        return True
    except TimeoutError:
        return False


def _page_has_server_error(page: Page) -> bool:
    try:
        body = (page.locator("body").inner_text(timeout=2_000) or "").lower()
    except Exception:
        return False
    return any(
        hint in body
        for hint in (
            "500",
            "502",
            "503",
            "that's an error",
            "something went wrong",
            "что-то пошло не так",
            "ошибка сервера",
        )
    )


def wait_for_initial_gemini_load(page: Page, *, reason: str = "initial_gemini_load") -> bool:
    started = time.time()
    reloads = 0
    deadline = started + INITIAL_GEMINI_LOAD_TIMEOUT_SEC
    while time.time() < deadline:
        elapsed = int(time.time() - started)
        if wait_for_prompt_input_soft(page, timeout_ms=5000):
            print(
                f"[WAIT_LOAD] elapsed={elapsed}/{INITIAL_GEMINI_LOAD_TIMEOUT_SEC} "
                f"reason={reason} status=input_ready"
            )
            sys.stdout.flush()
            return True
        if is_login_screen_visible(page):
            print(f"[WAIT_LOAD] elapsed={elapsed}/{INITIAL_GEMINI_LOAD_TIMEOUT_SEC} reason=login_screen_visible")
            sys.stdout.flush()
            return False
        if _page_has_server_error(page):
            if reloads < MAX_GEMINI_PAGE_RELOADS:
                reloads += 1
                print(
                    f"[RELOAD] reason=server_error_page attempt={reloads}/{MAX_GEMINI_PAGE_RELOADS} "
                    f"pause={GEMINI_RELOAD_PAUSE_SEC}s"
                )
                if not _safe_log_page_reload("server_error_page", caller="wait_for_initial_gemini_load", attempt=reloads):
                    continue
                sys.stdout.flush()
                time.sleep(GEMINI_RELOAD_PAUSE_SEC)
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
                continue
            return False
        print(
            f"[NO_RELOAD] waiting_for_initial_load elapsed={elapsed}/{INITIAL_GEMINI_LOAD_TIMEOUT_SEC} "
            f"reason={reason} url={((page.url or '')[:80])}"
        )
        sys.stdout.flush()
        time.sleep(5 if "gemini.google.com" in (page.url or "").lower() else 3)
    if reloads < MAX_GEMINI_PAGE_RELOADS:
        reloads += 1
        print(
            f"[RELOAD] reason=hard_timeout_after_{INITIAL_GEMINI_LOAD_TIMEOUT_SEC}s "
            f"attempt={reloads}/{MAX_GEMINI_PAGE_RELOADS} pause={GEMINI_RELOAD_PAUSE_SEC}s"
        )
        if not _safe_log_page_reload(
            f"hard_timeout_after_{INITIAL_GEMINI_LOAD_TIMEOUT_SEC}s",
            caller="wait_for_initial_gemini_load",
            attempt=reloads,
        ):
            if _safe_persistent_nav_recover(
                page,
                page.url or "",
                reason=f"hard_timeout_after_{INITIAL_GEMINI_LOAD_TIMEOUT_SEC}s",
                caller="wait_for_initial_gemini_load",
            ):
                return True
            return False
        sys.stdout.flush()
        time.sleep(GEMINI_RELOAD_PAUSE_SEC)
        try:
            page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        return wait_for_prompt_input_soft(page, timeout_ms=30_000)
    print(f"[WARN] hard timeout initial load after {INITIAL_GEMINI_LOAD_TIMEOUT_SEC}s")
    sys.stdout.flush()
    return False


def ensure_logged_in(page: Page, *, bot_entry: "GemBotEntry | None" = None) -> bool:
    if wait_for_initial_gemini_load(page, reason="ensure_logged_in"):
        return True
    print("[AUTH] Жду поле ввода чата Gemini (до ~45 с на попытку; при зависании закрой другой Chrome с этим же user_data).")
    sys.stdout.flush()
    for attempt in range(1, 4):
        try:
            wait_for_prompt_input(page, timeout_ms=45_000)
            return True
        except TimeoutError:
            if is_login_screen_visible(page):
                print("[AUTH] Вижу реальный экран входа Google/Gemini.")
                if _gemini_noninteractive():
                    print(
                        "[AUTH] GEMINI_NON_INTERACTIVE=1 — не жду Enter после входа; "
                        "залогинься вручную в окне Chrome, скрипт продолжит ожидание поля чата."
                    )
                    sys.stdout.flush()
                else:
                    input("[AUTH] Войди в аккаунт и нажми Enter...")
                try:
                    wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
                    return True
                except TimeoutError:
                    print("[WARN] После ручного входа поле ввода Gemini всё ещё не появилось.")
            if is_bot_deleted_visible(page):
                print(f"[WARN] Gem-бот удалён (URL: {page.url}). Пересоздаю автоматически...")
                if bot_entry:
                    new_url = try_recreate_deleted_gem(page, bot_entry)
                    if new_url:
                        page.goto(new_url, wait_until="domcontentloaded", timeout=60_000)
                        time.sleep(3)
                        try:
                            wait_for_prompt_input(page, timeout_ms=30_000)
                            return True
                        except TimeoutError:
                            pass
                raise RuntimeError(
                    f"Gem-бот удалён и пересоздать не удалось. URL: {page.url}"
                )
            print(
                f"[RELOAD] reason=ui_not_ready_after_wait attempt={attempt}/3 "
                f"pause={GEMINI_RELOAD_PAUSE_SEC}s"
            )
            if not _safe_log_page_reload("ui_not_ready_after_wait", caller="ensure_logged_in", attempt=attempt):
                if _safe_persistent_nav_recover(
                    page,
                    page.url or "",
                    reason="ui_not_ready_after_wait",
                    caller="ensure_logged_in",
                ):
                    continue
                continue
            sys.stdout.flush()
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:
                pass
            sleep_interruptible(float(GEMINI_RELOAD_PAUSE_SEC))
    print("[WARN] Не удалось подтвердить готовность Gemini UI. Попробую восстановиться позже.")
    return False


def _split_gemini_urls(raw: str) -> list[str]:
    parts = re.split(r"[\s,;|]+", raw.strip())
    return [p for p in parts if p]


class GemBotEntry(NamedTuple):
    """Один Gem-бот: аккаунт Google (подсказка для логов), ссылка /u/N/gem/..., опционально hub /u/M/app."""

    email: str | None
    url: str
    hub_url: str | None = None  # если слот аккаунта в профиле ≠ индексу в url гема


def load_gem_bot_chain() -> list[GemBotEntry]:
    """
    Приоритет:
    1) JSON-файл GEMINI_BOTS_CONFIG (по умолчанию gemini_bots.json рядом со скриптом) —
       список объектов { "email": "...", "url": "https://.../gem/...", "app": "https://.../u/N/app" }.
       app опционален: если в профиле Chrome слот аккаунта ≠ индексу в url гема, укажи hub …/u/M/app.
    2) Иначе — GEMINI_URLS / GEMINI_URL / ввод (без привязки к почте).
    """
    config_path = Path(GEMINI_BOTS_CONFIG)
    if not config_path.is_absolute():
        config_path = PROJECT_DIR / config_path
    if config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Не удалось разобрать {config_path}: {exc}")
            raw = None
        if isinstance(raw, list) and raw:
            out: list[GemBotEntry] = []
            for i, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                email_raw = (item.get("email") or item.get("account") or item.get("google") or "").strip()
                email = email_raw or None
                url = (
                    (item.get("url") or item.get("gem_url") or item.get("bot_url") or item.get("gemini_url") or "")
                    .strip()
                )
                if not url:
                    print(f"[WARN] {config_path.name}: запись {i} без поля url — пропуск.")
                    continue
                if not GEMINI_URL_PATTERN.fullmatch(url):
                    print(f"[WARN] {config_path.name}: некорректный url в записи {i}: {url}")
                    continue
                hub_raw = (
                    (item.get("app") or item.get("hub_url") or item.get("hub") or "").strip()
                )
                hub_url: str | None = hub_raw or None
                if hub_url and not GEMINI_APP_HUB_PATTERN.fullmatch(hub_url):
                    print(f"[WARN] {config_path.name}: некорректный app/hub в записи {i}: {hub_url} — игнорирую.")
                    hub_url = None
                out.append(GemBotEntry(email=email, url=url, hub_url=hub_url))
            if out:
                print(f"[INFO] Цепочка ботов загружена из {config_path} ({len(out)} шт.)")
                return out
            print(f"[WARN] {config_path.name} пустой или без валидных записей — fallback на GEMINI_URLS.")
    urls = resolve_all_gemini_urls()
    return [GemBotEntry(email=None, url=u, hub_url=None) for u in urls]


def resolve_all_gemini_urls() -> list[str]:
    """
    Список URL Gem-ботов: GEMINI_URLS (через запятую/перенос/|), иначе одна GEMINI_URL, иначе ввод / DEFAULT.
    Один профиль Chrome — залогинь все нужные Google-аккаунты; ссылки вида /u/0/ /u/1/ ведут на разные аккаунты.
    """
    raw = (os.getenv("GEMINI_URLS") or "").strip()
    urls: list[str] = []
    if raw:
        for p in _split_gemini_urls(raw):
            if GEMINI_URL_PATTERN.fullmatch(p):
                urls.append(p)
            else:
                print(f"[WARN] Пропускаю некорректный URL в GEMINI_URLS: {p}")
    if not urls:
        single = (os.getenv("GEMINI_URL") or "").strip()
        if single:
            if GEMINI_URL_PATTERN.fullmatch(single):
                urls = [single]
            else:
                print(f"[WARN] Некорректный GEMINI_URL, будет запрошен ввод / дефолт.")
    if not urls:
        print("[INFO] Можно задать несколько ботов: GEMINI_URLS=url1,url2,url3 (ротация при лимите).")
        print(f"[INFO] Дефолтный бот: {DEFAULT_GEMINI_URL}")
        if _gemini_noninteractive():
            print("[INFO] GEMINI_NON_INTERACTIVE=1 — URL бота без консольного ввода, использую DEFAULT_GEMINI_URL.")
            urls = [DEFAULT_GEMINI_URL]
        else:
            user_input_url = input("[INPUT] Вставь ссылку первого Gem-бота или Enter для дефолта: ").strip()
            if user_input_url:
                if GEMINI_URL_PATTERN.fullmatch(user_input_url):
                    urls = [user_input_url]
                else:
                    print("[WARN] Неверный формат — использую дефолт.")
                    urls = [DEFAULT_GEMINI_URL]
            else:
                urls = [DEFAULT_GEMINI_URL]
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def resolve_session_gemini_url() -> str:
    """Первая ссылка из цепочки (совместимость со старым вызовом)."""
    return load_gem_bot_chain()[0].url


def gemini_authuser_index(url: str) -> int | None:
    """Индекс аккаунта Google в URL: .../u/2/gem/... → 2."""
    m = GEMINI_AUTHUSER_IN_URL.search(url or "")
    return int(m.group(1)) if m else None


def page_goto_gemini(page: Page, url: str, *, context: str = "", force: bool = False) -> bool:
    """Переход на URL Gemini с увеличенным таймаутом; без необработанного TimeoutError до [FATAL]."""
    if not force and _safe_skip_redundant_goto(page, url, context=context):
        return True
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=GEMINI_GOTO_TIMEOUT_MS)
        return True
    except Exception as exc:
        suffix = f" ({context})" if context else ""
        print(f"[WARN] page.goto не удался{suffix}: {exc}")
        sys.stdout.flush()
        return False


def find_bot_index_for_authuser(bot_chain: list[GemBotEntry], authuser: int) -> int | None:
    """Индекс бота: совпадение /u/N/ в ссылке гема или в app/hub."""
    for i, entry in enumerate(bot_chain):
        if gemini_authuser_index(entry.url) == authuser:
            return i
        if entry.hub_url and gemini_authuser_index(entry.hub_url) == authuser:
            return i
    return None


def sync_bot_idx_from_browser_session(page: Page, bot_chain: list[GemBotEntry]) -> int:
    """
    После запуска Chrome с user_data Gemini редиректит на последний активный Google-аккаунт (/u/N/).
    Выбираем запись из gemini_bots.json с тем же N, а не «первую в файле».
    """
    if not bot_chain:
        return 0
    print("[INFO] Смотрю, на каком /u/N/ открылась Gemini (последняя активная почта в профиле)…")
    sys.stdout.flush()
    try:
        page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=90_000)
        time.sleep(0.6)
    except Exception as exc:
        print(f"[WARN] Переход на gemini.google.com/: {exc}")
    cur = page.url or ""
    u = gemini_authuser_index(cur)
    if u is None:
        for suffix in ("/app", "/chat"):
            try:
                page.goto(f"https://gemini.google.com{suffix}", wait_until="domcontentloaded", timeout=60_000)
                time.sleep(0.45)
            except Exception:
                continue
            cur = page.url or ""
            u = gemini_authuser_index(cur)
            if u is not None:
                break
    if u is None:
        print(
            "[WARN] В URL не видно /u/N/ — стартую с первого бота в списке. "
            "Открой Gemini вручную на нужной почте или проверь вход в Google."
        )
        sys.stdout.flush()
        return 0
    idx = find_bot_index_for_authuser(bot_chain, u)
    if idx is None:
        print(
            f"[WARN] В gemini_bots.json нет бота с /u/{u}/ (gem или app) — стартую с первого. "
            f"Добавь в конфиг url с …/u/{u}/gem/… или app …/u/{u}/app для этой почты."
        )
        sys.stdout.flush()
        return 0
    who = bot_chain[idx].email or bot_chain[idx].url
    print(f"[INFO] Активная сессия: /u/{u}/ → бот {idx + 1}/{len(bot_chain)} ({who}).")
    sys.stdout.flush()
    return idx


def _url_path_norm(url: str) -> str:
    return (url or "").split("?")[0].rstrip("/").lower()


def ensure_gemini_authuser_context(page: Page, gemini_url: str, hub_url: str | None = None) -> None:
    """
    В одном Chrome-профиле разные почты — это /u/0/, /u/1/, ...
    Сначала заходим на hub нужного индекса (из app или из url гема), иначе goto на /u/N/gem/... может не сменить аккаунт.
    """
    target = gemini_authuser_index(hub_url) if hub_url else gemini_authuser_index(gemini_url)
    if target is None:
        return
    cur = page.url or ""
    cur_idx = gemini_authuser_index(cur)
    if cur_idx == target:
        return
    hub = (hub_url.strip() if hub_url else f"https://gemini.google.com/u/{target}/")
    print(
        f"[AUTH] Смена аккаунта Google: перехожу на {hub} (нужен слот /u/{target}/; "
        f"текущий URL с /u/{cur_idx if cur_idx is not None else '?'}). "
        f"Дальше откроется Gem."
    )
    sys.stdout.flush()
    if page_goto_gemini(page, hub, context="hub /u/N"):
        time.sleep(max(0.0, GEMINI_AUTHUSER_HUB_PAUSE_SEC))


def ensure_on_bot_page(page: Page, gemini_url: str, hub_url: str | None = None) -> bool:
    """Открывает нужный Gem; при смене аккаунта сначала hub (app или /u/N/), см. ensure_gemini_authuser_context."""
    ensure_gemini_authuser_context(page, gemini_url, hub_url=hub_url)
    if _url_path_norm(page.url) == _url_path_norm(gemini_url):
        return True
    print(f"[INFO] Переход по URL Gem: {gemini_url}")
    sys.stdout.flush()
    return page_goto_gemini(page, gemini_url, context="URL Gem")


def recover_session_state(
    page: Page,
    gemini_url: str,
    *,
    hub_url: str | None = None,
    resume_same_chat: bool = False,
    bot_entry: "GemBotEntry | None" = None,
    force_goto: bool = False,
) -> bool:
    """
    resume_same_chat=True: не делаем goto, если уже на gemini.google.com — сохраняем контекст чата
    для продолжения с того же диалога после сбоя.
    """
    if not resume_same_chat:
        ensure_gemini_authuser_context(page, gemini_url, hub_url=hub_url)
        if force_goto or not _safe_skip_redundant_goto(page, gemini_url, context="recover_session"):
            page_goto_gemini(page, gemini_url, context="recover_session", force=force_goto)
    else:
        cur = (page.url or "").lower()
        if "gemini.google.com" not in cur:
            ensure_gemini_authuser_context(page, gemini_url, hub_url=hub_url)
            page_goto_gemini(page, gemini_url, context="recover_session (не было Gemini)")
    if not ensure_logged_in(page, bot_entry=bot_entry):
        return False
    if not ensure_on_bot_page(page, gemini_url, hub_url=hub_url):
        return False
    prepare_clean_prompt(page)
    return True


def open_bot_with_retries(
    page: Page,
    gemini_url: str,
    hub_url: str | None,
    *,
    reason: str,
    bot_entry: "GemBotEntry | None" = None,
) -> bool:
    """
    Ограниченное восстановление сессии для конкретного бота.
    Важно: без бесконечных циклов ожидания на "UI Gemini ещё не готов".
    """
    cycles = max(1, BOT_OPEN_RETRY_CYCLES)
    for attempt in range(1, cycles + 1):
        try:
            if recover_session_state(page, gemini_url, hub_url=hub_url, resume_same_chat=False, bot_entry=bot_entry):
                return True
        except RuntimeError:
            return False
        if attempt < cycles:
            wait_with_status(180, f"{reason}: повтор {attempt}/{cycles}")
    return False


def bot_label(entry: GemBotEntry) -> str:
    return entry.email or entry.url


def print_bot_health_summary(working: set[str], broken: set[str]) -> None:
    w = sorted([x for x in working if x and x not in broken], key=str.lower)
    b = sorted([x for x in broken if x], key=str.lower)
    print(f"[BOT-HEALTH] Рабочие почты/боты: {', '.join(w) if w else 'нет подтверждённых'}")
    print(f"[BOT-HEALTH] Нерабочие почты/боты: {', '.join(b) if b else 'нет'}")
    sys.stdout.flush()


def choose_start_bot_idx(bot_chain: list[GemBotEntry]) -> int | None:
    """
    Возвращает индекс стартового аккаунта/бота по выбору пользователя.
    Enter/auto — автосинхронизация по текущему /u/N/ в браузере.
    """
    if len(bot_chain) <= 1:
        return 0
    if _gemini_noninteractive():
        raw_idx = (os.getenv("GEMINI_START_BOT_INDEX") or "").strip()
        if raw_idx.isdigit():
            k = int(raw_idx)
            if 0 <= k < len(bot_chain):
                print(f"[START] GEMINI_NON_INTERACTIVE=1 — стартовый бот GEMINI_START_BOT_INDEX={k}: {bot_label(bot_chain[k])}")
                sys.stdout.flush()
                return k
        print("[START] GEMINI_NON_INTERACTIVE=1 — стартовый бот: первый в цепочке (индекс 0).")
        sys.stdout.flush()
        return 0
    print("[START] Выбери стартовый аккаунт/бот:")
    for i, entry in enumerate(bot_chain, start=1):
        print(f"  {i}) {bot_label(entry)}")
    print("  Enter) авто (по текущему /u/N/ в открытой сессии Chrome)")
    while True:
        raw = input(f"[INPUT] Стартовый аккаунт [1-{len(bot_chain)}] или Enter: ").strip().lower()
        if raw in ("", "auto", "a"):
            return None
        if raw.isdigit():
            num = int(raw)
            if 1 <= num <= len(bot_chain):
                return num - 1
        print(f"[WARN] Некорректный ввод: '{raw}'. Введи число 1..{len(bot_chain)} или Enter.")


def pick_story_source_file(folder: Path) -> Path | None:
    candidates = list_story_source_files(folder)
    return candidates[0] if candidates else None


def _is_safe_chunk_artifact_txt(path: Path) -> bool:
    """Chunk staging files must never be treated as standalone story sources."""
    name = path.name.lower()
    if name.startswith("chunk_"):
        return True
    if ".raw_response." in name or ".validation." in name:
        return True
    if name.endswith(".input.txt"):
        return True
    return False


def _is_safe_chunks_dir(folder: Path) -> bool:
    """Persistent safe writes chunk artifacts under story/chunks — not a story root."""
    return folder.name.lower() == "chunks"


def list_story_source_files(folder: Path) -> list[Path]:
    if _is_safe_chunks_dir(folder):
        return []

    def is_generated_txt(path: Path) -> bool:
        name = path.name.lower()
        if _is_safe_chunk_artifact_txt(path):
            return True
        if name == INFO_FILE_NAME.lower():
            return True
        if name == GENRE_REPORT_FILE_NAME.lower():
            return True
        if name.endswith("_clean.txt"):
            return True
        if name.endswith("_clean.tmp"):
            return True
        return REPORT_FILE_PATTERN.fullmatch(name) is not None

    return sorted([path for path in folder.glob("*.txt") if not is_generated_txt(path)])


def collect_story_folders(stories_dir: Path) -> list[Path]:
    print("[STATUS] Сканирую папки рассказов...")
    all_dirs = [
        path
        for path in stories_dir.rglob("*")
        if path.is_dir() and not _is_safe_chunks_dir(path)
    ]
    with_source = [folder for folder in all_dirs if pick_story_source_file(folder) is not None]
    with_source_set = set(with_source)
    leaf_story_folders = [folder for folder in with_source if folder not in {child.parent for child in with_source_set}]
    result = sorted(leaf_story_folders, key=lambda path: str(path.relative_to(stories_dir)).lower())
    print(f"[STATUS] Сканирование завершено. Найдено story-папок: {len(result)}")
    return result


def attachment_visible(page: Page, source_file: Path, timeout_ms: int = 8_000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    file_name = source_file.name
    stem = source_file.stem
    while time.time() < deadline:
        for selector in ATTACHED_FILE_SELECTORS:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                aria_label = (locator.first.get_attribute("aria-label") or "").lower()
                text_value = (locator.first.inner_text() or "").lower()
                if file_name.lower() in aria_label or file_name.lower() in text_value:
                    return True
                if stem.lower() in aria_label or stem.lower() in text_value:
                    return True
        if page.locator(f'text="{file_name}"').count() > 0:
            return True
        time.sleep(0.2)
    return False


def paste_file_into_prompt(page: Page, source_file: Path) -> bool:
    prompt_input = wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
    prompt_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.press("Escape")
    raw_bytes = source_file.read_bytes()
    mime_type = mimetypes.guess_type(str(source_file))[0] or "text/plain"
    payload = {
        "name": source_file.name,
        "mimeType": mime_type,
        "contentBase64": base64.b64encode(raw_bytes).decode("ascii"),
    }
    try:
        prompt_input.evaluate(
            """(el, payload) => {
                const binary = atob(payload.contentBase64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }
                const file = new File([bytes], payload.name, { type: payload.mimeType });
                const dt = new DataTransfer();
                dt.items.add(file);
                const pasteEvent = new ClipboardEvent('paste', {
                    bubbles: true,
                    cancelable: true,
                    clipboardData: dt
                });
                Object.defineProperty(pasteEvent, 'clipboardData', { value: dt });
                el.dispatchEvent(pasteEvent);
            }""",
            payload,
        )
    except Exception:
        return False
    return attachment_visible(page, source_file, timeout_ms=8_000)


def prepare_clean_prompt(page: Page) -> None:
    prompt_input = wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
    prompt_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.press("Escape")


def _page_limit_text_sample(page: Page) -> str:
    """Текст только из области чата/основного контента — меньше ложных «лимитов» из страницы целиком."""
    for sel in (
        "main",
        '[role="main"]',
        '[data-test-id="chat-container"]',
        '[class*="conversation"]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            t = (loc.inner_text(timeout=2000) or "").lower()
            if len(t) > 80:
                return t
        except Exception:
            continue
    try:
        return (page.locator("body").inner_text(timeout=1500) or "").lower()
    except Exception:
        return ""


def has_limit_message(page: Page) -> bool:
    body_text = _page_limit_text_sample(page)
    if not body_text:
        return False
    return any(hint in body_text for hint in LIMIT_HINTS)


def raise_if_gemini_limit(page: Page, where: str = "") -> None:
    """Если на странице виден лимит/недоступный Gem — бросаем исключение для ротации ботов."""
    if is_bot_deleted_visible(page):
        suffix = f" ({where})" if where else ""
        raise RuntimeError(
            "Лимит Gem-бота: Gem-бот по ссылке удалён или не принадлежит текущему аккаунту Google"
            f"{suffix}. Проверь /u/N/ и связку url/app в gemini_bots.json."
        )
    if not has_limit_message(page):
        return
    suffix = f" ({where})" if where else ""
    raise RuntimeError(f"Лимит Gem-бота: сервис сообщает о лимите{suffix}.")


def is_generation_in_progress(page: Page) -> bool:
    for selector in STOP_BUTTON_SELECTORS:
        button = page.locator(selector).first
        if button.count() == 0:
            continue
        try:
            if button.is_visible():
                return True
        except Exception:
            continue
    return False


def wait_for_generation_idle(page: Page, timeout_ms: int = WAIT_TIMEOUT_MS) -> None:
    deadline = time.time() + timeout_ms / 1000
    stable_rounds = 0
    last_status_print = 0.0
    last_limit_check = 0.0
    while time.time() < deadline:
        if consume_wait_override_key():
            print(f"[STATUS] Ожидание idle принудительно пропущено клавишей '{WAIT_OVERRIDE_KEY}'.")
            return
        now = time.time()
        if now - last_limit_check >= 3.0:
            last_limit_check = now
            raise_if_gemini_limit(page, "ожидание idle перед чанком")
        if is_generation_in_progress(page):
            now = time.time()
            if now - last_status_print >= HEARTBEAT_SECONDS:
                print("[STATUS] Жду завершения генерации текущего ответа...")
                last_status_print = now
            stable_rounds = 0
            time.sleep(0.5)
            continue
        stable_rounds += 1
        if stable_rounds >= IDLE_STABLE_ROUNDS:
            return
        time.sleep(0.4)
    raise TimeoutError("Gemini слишком долго генерирует ответ, idle-состояние не наступило.")


def wait_until_send_clickable(page: Page, timeout_ms: int | None = None) -> None:
    """Ждём, пока Gemini примет вставленный текст и включит отправку (иначе клик бесполезен)."""
    if timeout_ms is None:
        timeout_ms = SEND_READY_TIMEOUT_MS
    deadline = time.time() + timeout_ms / 1000
    last_print = 0.0
    last_limit_check = 0.0
    stuck_send_disabled_hb = 0
    while time.time() < deadline:
        skip, rotate = drain_console_hotkeys()
        if rotate:
            raise ForceRotateBot(
                "Принудительная смена Gem-бота (клавиша в консоли). Следующий аккаунт из gemini_bots.json."
            )
        if skip:
            pass
        now = time.time()
        if GEMINI_LIMIT_CHECK_WHILE_SEND_WAIT and now - last_limit_check >= 3.0:
            last_limit_check = now
            raise_if_gemini_limit(page, "ожидание кнопки «Отправить»")
        if is_send_button_enabled(page):
            return
        if now - last_print >= HEARTBEAT_SECONDS:
            print(
                "[STATUS] Жду, пока станет доступна кнопка «Отправить» "
                "(длинный текст / лимит — кнопка может долго быть неактивна)…"
            )
            last_print = now
            if STUCK_HEARTBEATS_BEFORE_LIMIT > 0:
                stuck_send_disabled_hb += 1
                if stuck_send_disabled_hb >= STUCK_HEARTBEATS_BEFORE_LIMIT:
                    raise RuntimeError(
                        "Лимит Gem-бота: кнопка «Отправить» не активна слишком долго "
                        f"({stuck_send_disabled_hb}×~{HEARTBEAT_SECONDS} с) — похоже на лимит, аккаунт не принимает запросы."
                    )
        time.sleep(0.25)
    if GEMINI_LIMIT_CHECK_WHILE_SEND_WAIT and has_limit_message(page):
        raise RuntimeError(
            "Лимит Gem-бота: кнопка «Отправить» не активировалась, на странице признаки лимита."
        )
    raise TimeoutError(
        "Кнопка «Отправить» не стала активной: текст не принят, перегруз страницы или слишком длинный ввод."
    )


def try_send_message(page: Page) -> bool:
    """Несколько способов отправить: клик, Enter, Ctrl+Enter, принудительный клик."""
    if click_send_button(page):
        return True
    time.sleep(0.15)
    page.keyboard.press("Enter")
    time.sleep(0.2)
    if click_send_button(page, timeout_ms=15_000):
        return True
    try:
        page.keyboard.press("Control+Enter")
    except Exception:
        pass
    time.sleep(0.2)
    if click_send_button(page, timeout_ms=12_000):
        return True
    for selector in SEND_BUTTON_SELECTORS:
        button = page.locator(selector).first
        if button.count() == 0 or not button.is_visible():
            continue
        try:
            button.scroll_into_view_if_needed(timeout=2000)
            button.click(timeout=4000, force=True)
            return True
        except Exception:
            continue
    return False


def click_send_button(page: Page, timeout_ms: int | None = None) -> bool:
    if timeout_ms is None:
        timeout_ms = SEND_BUTTON_TIMEOUT_MS
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        skip, rotate = drain_console_hotkeys()
        if rotate:
            raise ForceRotateBot(
                "Принудительная смена Gem-бота (клавиша в консоли). Следующий аккаунт из gemini_bots.json."
            )
        if skip:
            pass
        for selector in SEND_BUTTON_SELECTORS:
            button = page.locator(selector).first
            if button.count() == 0 or not button.is_visible():
                continue
            try:
                if button.is_enabled():
                    try:
                        button.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    button.click(timeout=3000)
                    return True
            except Exception:
                continue
        time.sleep(0.25)
    return False


def is_send_button_enabled(page: Page) -> bool:
    for selector in SEND_BUTTON_SELECTORS:
        button = page.locator(selector).first
        if button.count() == 0 or not button.is_visible():
            continue
        try:
            return button.is_enabled()
        except Exception:
            continue
    return False


def get_copy_buttons_count(page: Page) -> int:
    max_count = 0
    for selector in COPY_BUTTON_SELECTORS:
        count = page.locator(selector).count()
        if count > max_count:
            max_count = count
    return max_count


def wait_for_new_copy_button(page: Page, previous_count: int) -> Locator:
    deadline = time.time() + WAIT_TIMEOUT_MS / 1000
    while time.time() < deadline:
        skip, rotate = drain_console_hotkeys()
        if rotate:
            raise ForceRotateBot(
                "Принудительная смена Gem-бота (клавиша в консоли). Следующий аккаунт из gemini_bots.json."
            )
        if skip:
            pass
        for selector in COPY_BUTTON_SELECTORS:
            buttons = page.locator(selector)
            count = buttons.count()
            if count <= previous_count:
                continue
            for index in range(count - 1, -1, -1):
                candidate = buttons.nth(index)
                if not candidate.is_visible():
                    continue
                aria_label = (candidate.get_attribute("aria-label") or "").lower()
                text_value = (candidate.inner_text() or "").lower()
                combined = f"{aria_label} {text_value}"
                if "скопировать запрос" in combined or "copy prompt" in combined:
                    continue
                return candidate
        time.sleep(0.5)
    raise TimeoutError("Ответ не завершился: новая кнопка копирования не появилась.")


def find_new_copy_button(page: Page, previous_count: int) -> Locator | None:
    for selector in COPY_BUTTON_SELECTORS:
        buttons = page.locator(selector)
        count = buttons.count()
        if count <= previous_count:
            continue
        for index in range(count - 1, -1, -1):
            candidate = buttons.nth(index)
            if not candidate.is_visible():
                continue
            aria_label = (candidate.get_attribute("aria-label") or "").lower()
            text_value = (candidate.inner_text() or "").lower()
            combined = f"{aria_label} {text_value}"
            if "скопировать запрос" in combined or "copy prompt" in combined:
                continue
            return candidate
    return None


def wait_response_ready(page: Page, previous_count: int, timeout_ms: int = WAIT_TIMEOUT_MS) -> Locator:
    deadline = time.time() + timeout_ms / 1000
    stable_ready_rounds = 0
    last_status_print = 0.0
    last_limit_check = 0.0
    stuck_copy_heartbeats = 0
    while time.time() < deadline:
        if consume_wait_override_key():
            print(f"[STATUS] Ожидание готовности ответа принудительно пропущено клавишей '{WAIT_OVERRIDE_KEY}'.")
            candidate = find_new_copy_button(page, previous_count)
            if candidate is not None:
                return candidate
            raise TimeoutError("Ожидание готовности ответа пропущено вручную, но кнопка копирования не найдена.")
        now = time.time()
        if now - last_limit_check >= 3.0:
            last_limit_check = now
            raise_if_gemini_limit(page, "ожидание кнопки копирования")
        candidate = find_new_copy_button(page, previous_count)
        if candidate is not None and not is_generation_in_progress(page):
            stuck_copy_heartbeats = 0
            stable_ready_rounds += 1
            if stable_ready_rounds >= IDLE_STABLE_ROUNDS:
                return candidate
        else:
            if now - last_status_print >= HEARTBEAT_SECONDS:
                print("[STATUS] Ожидаю, пока Gemini закончит ответ и кнопка копирования стабилизируется...")
                last_status_print = now
                if STUCK_GENERATION_HEARTBEATS_BEFORE_LIMIT > 0:
                    stuck_copy_heartbeats += 1
                    if stuck_copy_heartbeats >= STUCK_GENERATION_HEARTBEATS_BEFORE_LIMIT:
                        raise RuntimeError(
                            "Лимит Gem-бота: кнопка копирования не стабилизируется слишком долго "
                            f"({stuck_copy_heartbeats}×~{HEARTBEAT_SECONDS} с) — похоже на лимит или зависание UI."
                        )
            stable_ready_rounds = 0
        sleep_interruptible(0.35)
    raise TimeoutError("Ответ не завершился вовремя (кнопка копирования не стала стабильной).")


def _toolbar_label_suggests_thinking(label: str) -> bool:
    t = label.lower()
    if "думающ" in t or "thinking" in t:
        return not ("быстр" in t or "fast" in t or "quick" in t or "flash" in t or "мгновен" in t)
    return False


def _toolbar_label_suggests_fast(label: str) -> bool:
    t = label.lower()
    return (
        "быстр" in t
        or "fast" in t
        or "quick" in t
        or "flash" in t
        or "мгновен" in t
        or "базов" in t
    )


def thinking_mode_selected_from_toolbar(page: Page) -> bool | None:
    """True — на триггере видно думающий режим; False — явно быстрый; None — не определили."""
    toggles = page.locator(
        'button[aria-haspopup="listbox"], button[aria-haspopup="menu"], button[aria-haspopup="true"]'
    )
    n = toggles.count()
    for i in range(min(n, 48)):
        btn = toggles.nth(i)
        try:
            if not btn.is_visible():
                continue
            label = (btn.get_attribute("aria-label") or "") + " " + (btn.inner_text() or "")
        except Exception:
            continue
        if _toolbar_label_suggests_thinking(label):
            return True
        if _toolbar_label_suggests_fast(label):
            return False
    return None


def open_model_mode_menu(page: Page) -> bool:
    for sel in MODE_MENU_TRIGGER_SELECTORS:
        loc = page.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            if not loc.is_visible():
                continue
            loc.click(timeout=5_000)
            return True
        except Exception:
            continue
    toggles = page.locator('button[aria-haspopup="listbox"], button[aria-haspopup="menu"]')
    for i in range(min(toggles.count(), 48)):
        btn = toggles.nth(i)
        try:
            if not btn.is_visible():
                continue
            label = (btn.get_attribute("aria-label") or "") + " " + (btn.inner_text() or "")
            low = label.lower()
            if any(
                x in low
                for x in [
                    "режим",
                    "mode",
                    "думающ",
                    "thinking",
                    "быстр",
                    "fast",
                    "flash",
                    "мгновен",
                ]
            ):
                btn.click(timeout=5_000)
                return True
        except Exception:
            continue
    return False


def click_thinking_model_option(page: Page) -> bool:
    name_rx = re.compile(r"Думающая|Thinking", re.IGNORECASE)
    factories = [
        lambda: page.get_by_role("menuitem", name=name_rx),
        lambda: page.get_by_role("option", name=name_rx),
        lambda: page.get_by_role("menuitemradio", name=name_rx),
    ]
    for factory in factories:
        try:
            loc = factory()
            if loc.count() == 0:
                continue
            target = loc.first
            if not target.is_visible():
                continue
            aria_disabled = (target.get_attribute("aria-disabled") or "").lower()
            if aria_disabled == "true":
                print("[INFO] Думающая модель найдена в меню, но отключена (disabled).")
                return False
            target.click(timeout=2_500)
            return True
        except Exception:
            continue
    for role in ("menuitem", "option", "menuitemradio"):
        try:
            loc = page.locator(f'[role="{role}"]').filter(has_text=name_rx).first
            if loc.count() > 0 and loc.is_visible():
                aria_disabled = (loc.get_attribute("aria-disabled") or "").lower()
                if aria_disabled == "true":
                    print("[INFO] Думающая модель найдена в меню, но отключена (disabled).")
                    return False
                loc.click(timeout=2_500)
                return True
        except Exception:
            continue
    return False


_FLASH35_RX = re.compile(r"\b3\.5\s*Flash\b", re.IGNORECASE)


def _visible_text(locator: Locator) -> str:
    try:
        return " ".join(((locator.get_attribute("aria-label") or "") + " " + (locator.inner_text() or "")).split())
    except Exception:
        return ""


def current_model_label_from_toolbar(page: Page) -> str:
    toggles = page.locator(
        'button[aria-haspopup="listbox"], button[aria-haspopup="menu"], button[aria-haspopup="true"]'
    )
    try:
        count = toggles.count()
    except Exception:
        return ""
    for i in range(min(count, 64)):
        btn = toggles.nth(i)
        try:
            if not btn.is_visible():
                continue
        except Exception:
            continue
        label = _visible_text(btn)
        low = label.lower()
        if "flash" in low or "gemini" in low or "thinking" in low or "думающ" in low:
            return label
    return ""


def _click_model_label_candidate(page: Page, name_rx: re.Pattern[str], choice: ModelChoice) -> str:
    factories = [
        lambda: page.get_by_role("menuitem", name=name_rx),
        lambda: page.get_by_role("option", name=name_rx),
        lambda: page.get_by_role("menuitemradio", name=name_rx),
    ]
    for factory in factories:
        try:
            loc = factory()
            for i in range(min(loc.count(), 12)):
                target = loc.nth(i)
                if not target.is_visible():
                    continue
                label = _visible_text(target)
                if not ui_label_matches_gemini_choice(label, choice):
                    continue
                if (target.get_attribute("aria-disabled") or "").lower() == "true":
                    print(f"[MODEL] candidate disabled: {label}")
                    continue
                target.click(timeout=5_000)
                return label or choice.preferred_ui_label
        except Exception:
            continue
    return ""


def click_resolved_model_option(page: Page, choice: ModelChoice) -> str:
    exact_rx = re.compile(r"^\s*(?:Gemini\s+)?3\.5\s+Flash\s*$", re.IGNORECASE)
    clicked = _click_model_label_candidate(page, exact_rx, choice)
    if clicked:
        return clicked

    for role in ("menuitem", "option", "menuitemradio"):
        loc = page.locator(f'[role="{role}"]')
        try:
            count = loc.count()
        except Exception:
            continue
        for i in range(min(count, 80)):
            target = loc.nth(i)
            try:
                if not target.is_visible():
                    continue
                label = _visible_text(target)
                if not ui_label_matches_gemini_choice(label, choice):
                    continue
                if (target.get_attribute("aria-disabled") or "").lower() == "true":
                    print(f"[MODEL] candidate disabled: {label}")
                    continue
                target.click(timeout=5_000)
                return label or choice.preferred_ui_label
            except Exception:
                continue
    return ""


def flash35_selected_from_toolbar(page: Page) -> bool:
    return ui_label_matches_gemini_choice(current_model_label_from_toolbar(page), resolve_gemini_model_alias("thinking"))


def legacy_flash35_selected_from_toolbar(page: Page) -> bool:
    toggles = page.locator(
        'button[aria-haspopup="listbox"], button[aria-haspopup="menu"], button[aria-haspopup="true"]'
    )
    n = toggles.count()
    for i in range(min(n, 48)):
        btn = toggles.nth(i)
        try:
            if not btn.is_visible():
                continue
            label = (btn.get_attribute("aria-label") or "") + " " + (btn.inner_text() or "")
        except Exception:
            continue
        if _FLASH35_RX.search(label):
            return True
    return False


def click_flash35_model_option(page: Page) -> bool:
    factories = [
        lambda: page.get_by_role("menuitem", name=_FLASH35_RX),
        lambda: page.get_by_role("option", name=_FLASH35_RX),
        lambda: page.get_by_role("menuitemradio", name=_FLASH35_RX),
    ]
    for factory in factories:
        try:
            loc = factory()
            if loc.count() == 0:
                continue
            target = loc.first
            if not target.is_visible():
                continue
            aria_disabled = (target.get_attribute("aria-disabled") or "").lower()
            if aria_disabled == "true":
                return False
            target.click(timeout=2_500)
            return True
        except Exception:
            continue
    for role in ("menuitem", "option", "menuitemradio"):
        try:
            loc = page.locator(f'[role="{role}"]').filter(has_text=_FLASH35_RX).first
            if loc.count() > 0 and loc.is_visible():
                aria_disabled = (loc.get_attribute("aria-disabled") or "").lower()
                if aria_disabled == "true":
                    return False
                loc.click(timeout=2_500)
                return True
        except Exception:
            continue
    return False


def _dismiss_all_overlays(page: Page) -> None:
    """Несколько раз жмёт Escape и кликает в пустоту, чтобы закрыть любые меню/диалоги."""
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.15)
    try:
        page.mouse.click(1, 1)
    except Exception:
        pass
    time.sleep(0.2)


def _safe_accept_model_label(alias: str, label: str) -> bool:
    """Selection-style acceptance: toolbar/click label good enough for safe to continue."""
    from orchestrator.gemini_model_resolver import (
        is_mode_selector_placeholder_label,
        selection_accepts_model_label,
    )

    clean = " ".join(str(label or "").split())
    if not clean or is_mode_selector_placeholder_label(clean):
        return False
    if selection_accepts_model_label(clean):
        return True
    low = clean.lower()
    tier = str(alias or "").strip().lower()
    if tier in {"default", "fast", "balanced"} and "flash" in low:
        return True
    if tier == "thinking" and ("thinking" in low or "думающ" in low or " pro" in f" {low}"):
        return True
    return False


def ensure_thinking_mode(
    page: Page,
    *,
    alias: str = "thinking",
    safe_caller: str = "",
    safe_reason: str = "",
) -> bool:
    """Select the centralized resolved Gemini model for this automation layer."""
    choice = resolve_gemini_model_alias(alias)
    deadline = time.time() + 30
    try:
        wait_for_prompt_input(page, timeout_ms=15_000)
    except TimeoutError:
        print("[WARN] Поле ввода Gemini не найдено, пробую переключить режим модели.")
    stabilize_sec = POST_MODEL_STABILIZE_SECONDS if safe_caller else 0.6
    time.sleep(stabilize_sec)
    current_label = current_model_label_from_toolbar(page)
    if ui_label_matches_gemini_choice(current_label, choice):
        print(f"[MODEL] selected={choice.preferred_ui_label} reason=already_selected label={current_label!r}")
        return True
    if safe_caller and _safe_accept_model_label(alias, current_label):
        print(
            f"[MODEL] selected={choice.preferred_ui_label} reason=safe_acceptable_toolbar "
            f"label={current_label!r}"
        )
        return True
    max_attempts = SAFE_MAX_MODEL_SELECTION_ATTEMPTS if safe_caller else 4
    last_clicked_label = ""
    for attempt in range(1, max_attempts + 1):
        if time.time() > deadline:
            break
        _dismiss_all_overlays(page)
        if not open_model_mode_menu(page):
            print(f"[WARN] Меню режима модели не открылось (resolved model, попытка {attempt}/{max_attempts}).")
            time.sleep(0.5)
            continue
        time.sleep(0.45)
        clicked_label = click_resolved_model_option(page, choice)
        if clicked_label:
            last_clicked_label = str(clicked_label)
            time.sleep(POST_MODEL_STABILIZE_SECONDS if safe_caller else 0.55)
            selected_label = current_model_label_from_toolbar(page)
            verified = ui_label_matches_gemini_choice(selected_label, choice)
            if not verified and safe_caller:
                verified = _safe_accept_model_label(alias, selected_label) or _safe_accept_model_label(
                    alias, clicked_label
                )
                if verified:
                    print(
                        f"[SAFE_MODEL_ACCEPT_UNVERIFIED] alias={alias} "
                        f"clicked={clicked_label!r} toolbar={selected_label!r}"
                    )
            allow_unverified = bool(safe_caller and clicked_label and not verified)
            if allow_unverified and _safe_accept_model_label(alias, clicked_label):
                verified = True
                print(
                    f"[SAFE_MODEL_ACCEPT_UNVERIFIED] alias={alias} "
                    f"clicked={clicked_label!r} reason=selection_style_continue"
                )
            print(
                f"[MODEL] selected={choice.preferred_ui_label} reason=resolved_model_selected "
                f"clicked={clicked_label!r} verified={verified}"
            )
            _dismiss_all_overlays(page)
            if verified or (safe_caller and clicked_label):
                return True
            return verified
        _dismiss_all_overlays(page)
        time.sleep(0.3)
    if safe_caller:
        final_label = current_model_label_from_toolbar(page) or last_clicked_label
        if _safe_accept_model_label(alias, final_label):
            print(
                f"[SAFE_MODEL_ACCEPT_UNVERIFIED] alias={alias} "
                f"toolbar={final_label!r} reason=final_toolbar_fallback"
            )
            return True
    print(
        f"[WARN] Не удалось выбрать resolved Gemini model alias={choice.requested_alias} "
        f"expected={expected_model_labels(choice)}."
    )
    return False


def read_clipboard_text(page: Page) -> str:
    for _ in range(8):
        try:
            text = page.evaluate("() => navigator.clipboard.readText()")
            if text and text.strip():
                return text.strip()
        except Exception:
            pass
        time.sleep(0.4)
    return ""


def read_latest_response_from_dom(page: Page) -> str:
    """
    Последний блок ответа модели. Важно: у последнего узла в списке не отбрасывать короткий текст
    (ошибка лимита в 1–2 строки) — иначе вернётся предыдущий длинный ответ и чанк будет «пропущен».
    """
    for selector in RESPONSE_BLOCK_SELECTORS:
        try:
            nodes = page.locator(selector)
            count = nodes.count()
        except Exception:
            continue
        if count == 0:
            continue
        for index in range(count - 1, -1, -1):
            try:
                node = nodes.nth(index)
                if not node.is_visible():
                    continue
                text = (node.inner_text() or "").strip()
                if not text:
                    continue
                # Самый новый блок — любая непустая строка; старые — только если достаточно длины (шум)
                if index == count - 1 or len(text) >= 30:
                    return text
            except Exception:
                continue
    return ""


def _response_fingerprint(text: str) -> str:
    return " ".join((text or "").split())


def get_response_blocks_count(page: Page) -> int:
    max_count = 0
    for selector in RESPONSE_BLOCK_SELECTORS:
        try:
            count = page.locator(selector).count()
        except Exception:
            continue
        if count > max_count:
            max_count = count
    return max_count


def wait_for_new_response_block(
    page: Page,
    previous_count: int,
    previous_copy_count: int,
    timeout_ms: int = WAIT_TIMEOUT_MS,
) -> None:
    """
    Ждём завершения ответа. Раньше требовали только рост числа DOM-блоков — при стриминге в один узел
    счётчик не меняется и скрипт «висел». Учитываем: конец генерации (Stop), новую кнопку «Копировать».
    """
    deadline = time.time() + timeout_ms / 1000
    stable_rounds = 0
    last_status_print = 0.0
    saw_generation = False
    t_enter = time.time()
    resent_done = False
    last_limit_check = 0.0
    stuck_no_generation_heartbeats = 0
    stuck_generation_heartbeats = 0
    while time.time() < deadline:
        if consume_wait_override_key():
            print(f"[STATUS] Ожидание нового блока ответа принудительно пропущено клавишей '{WAIT_OVERRIDE_KEY}'.")
            return
        now = time.time()
        if now - last_limit_check >= 3.0:
            last_limit_check = now
            raise_if_gemini_limit(page, "ожидание ответа")
        gen = is_generation_in_progress(page)
        if gen:
            saw_generation = True
            stuck_no_generation_heartbeats = 0
        current_count = get_response_blocks_count(page)
        copy_count = get_copy_buttons_count(page)

        if current_count > previous_count and not gen:
            stuck_generation_heartbeats = 0
            stable_rounds += 1
            if stable_rounds >= IDLE_STABLE_ROUNDS:
                return
        elif copy_count > previous_copy_count and not gen:
            stuck_generation_heartbeats = 0
            stable_rounds += 1
            if stable_rounds >= IDLE_STABLE_ROUNDS:
                return
        elif saw_generation and not gen:
            stuck_generation_heartbeats = 0
            stable_rounds += 1
            if stable_rounds >= IDLE_STABLE_ROUNDS:
                return
        else:
            stable_rounds = 0
            now = time.time()
            if (
                not resent_done
                and RESPONSE_ACTIVITY_SEC > 0
                and now - t_enter >= RESPONSE_ACTIVITY_SEC
                and not saw_generation
                and current_count <= previous_count
                and copy_count <= previous_copy_count
            ):
                print(
                    "[WARN] Нет признаков генерации — повторяю отправку один раз "
                    f"(прошло ≥{RESPONSE_ACTIVITY_SEC} с)."
                )
                try_send_message(page)
                resent_done = True
                t_enter = time.time()
            if now - last_status_print >= HEARTBEAT_SECONDS:
                if not saw_generation and not gen:
                    print(
                        "[STATUS] Ожидаю ответ Gemini (нет кнопки «Остановить» — запрос мог не уйти; "
                        "см. повтор отправки выше)…"
                    )
                    if STUCK_HEARTBEATS_BEFORE_LIMIT > 0:
                        stuck_no_generation_heartbeats += 1
                        if stuck_no_generation_heartbeats >= STUCK_HEARTBEATS_BEFORE_LIMIT:
                            raise RuntimeError(
                                "Лимит Gem-бота: нет реакции на запрос после "
                                f"{stuck_no_generation_heartbeats} интервалов ожидания (~{HEARTBEAT_SECONDS} с каждый) "
                                "без признаков генерации — похоже на лимит или блокировку."
                            )
                else:
                    stuck_no_generation_heartbeats = 0
                    print("[STATUS] Ожидаю ответ Gemini (генерация или обновление блока)…")
                    if STUCK_GENERATION_HEARTBEATS_BEFORE_LIMIT > 0:
                        stuck_generation_heartbeats += 1
                        if stuck_generation_heartbeats >= STUCK_GENERATION_HEARTBEATS_BEFORE_LIMIT:
                            raise RuntimeError(
                                "Лимит Gem-бота: ответ «висит» в генерации/обновлении (или UI не снимает режим генерации) "
                                f"уже {stuck_generation_heartbeats}×~{HEARTBEAT_SECONDS} с — похоже на лимит или зависание."
                            )
                last_status_print = now
        time.sleep(0.35)
    if has_limit_message(page):
        raise RuntimeError("Лимит Gem-бота: сервис сообщает о лимите (таймаут ожидания ответа).")
    raise TimeoutError("Ответ не завершился вовремя (новый блок ответа не появился).")


def click_copy_button_resilient(page: Page, copy_button: Locator) -> bool:
    for attempt in range(1, COPY_RETRIES + 1):
        try:
            copy_button.click(timeout=4_000)
            return True
        except Exception:
            pass
        try:
            fresh_button = find_new_copy_button(page, 0)
            if fresh_button is not None:
                fresh_button.click(timeout=4_000, force=True)
                return True
        except Exception:
            pass
        try:
            copy_button.evaluate("(el) => el.click()")
            return True
        except Exception:
            pass
        if attempt < COPY_RETRIES:
            time.sleep(0.8)
    return False


def build_info_with_genre(folder: Path, response_text: str) -> str:
    cleaned_response = response_text.strip()
    try:
        relative_folder = folder.relative_to(STORIES_DIR)
    except Exception:
        return cleaned_response + "\n"
    if len(relative_folder.parts) < 2:
        return cleaned_response + "\n"
    genre_name = relative_folder.parts[0]
    lines = cleaned_response.splitlines()
    updated = False
    for index, line in enumerate(lines):
        if not re.match(r"^\s*жанры\s*:", line, flags=re.IGNORECASE):
            continue
        prefix, _, value = line.partition(":")
        current_value = value.strip()
        if genre_name.lower() in current_value.lower():
            updated = True
            break
        if current_value:
            lines[index] = f"{prefix}: {current_value}, {genre_name}"
        else:
            lines[index] = f"{prefix}: {genre_name}"
        updated = True
        break
    if updated:
        return "\n".join(lines).strip() + "\n"
    return cleaned_response + "\n"


def clean_output_path(source_file: Path) -> Path:
    """Путь к итоговому файлу: рядом с исходником, имя `<stem>_clean.txt`."""
    return source_file.parent / f"{source_file.stem}{CLEAN_FILE_SUFFIX}"


def clean_tmp_path(source_file: Path) -> Path:
    """Путь к временному файлу записи: `<stem>_clean.tmp`."""
    return source_file.parent / f"{source_file.stem}{CLEAN_FILE_TMP_SUFFIX}"


def progress_file_path(source_file: Path) -> Path:
    return source_file.parent / f"{source_file.stem}{PROGRESS_FILE_SUFFIX}"


def _write_progress_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_progress_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _infer_next_chunk_from_tmp(tmp_path: Path, total_chunks: int) -> int | None:
    """Грубая оценка без JSON-чекпоинта: число блоков, разделённых \\n\\n. Может ошибаться, если в тексте есть \\n\\n."""
    try:
        raw = tmp_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return None
    if not raw:
        return 0
    parts = [p for p in raw.split("\n\n") if p.strip()]
    n = len(parts)
    if n > total_chunks:
        return None
    return n


def story_folder_processing_done(folder: Path) -> bool:
    """Папка считается обработанной, если рядом с исходным .txt уже есть *_clean.txt."""
    src = pick_story_source_file(folder)
    if src is None:
        return True
    return clean_output_path(src).exists()


def _utc_marker_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def persistent_log_account_index(runtime_idx: int) -> int:
    raw = (os.getenv("GEMINI_LOG_ACCOUNT_INDEX") or os.getenv("START_ACCOUNT_INDEX") or "").strip()
    if raw.isdigit():
        return int(raw)
    return int(runtime_idx)


def _read_orchestrator_staged_marker(folder: Path) -> dict | None:
    path = folder / STAGED_MARKER_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _find_orchestrator_staged_marker(folder: Path) -> dict | None:
    current = folder.resolve()
    for _ in range(5):
        staged = _read_orchestrator_staged_marker(current)
        if staged:
            return staged
        if current.parent == current:
            break
        current = current.parent
    return None


def _write_orchestrator_processed_marker(folder: Path, *, clean_file: Path) -> None:
    staged = _find_orchestrator_staged_marker(folder)
    if not staged:
        print(f"[WARN] {folder.name}: {STAGED_MARKER_NAME} not found — skip {PROCESSED_MARKER_NAME}")
        return
    processed_at = _utc_marker_now()
    payload = {
        "schema_version": 1,
        "marker_format": "orchestrator_processed_v1",
        "run_id": str(staged.get("run_id") or ""),
        "story_id": str(staged.get("story_id") or ""),
        "story_slug": str(staged.get("story_slug") or ""),
        "title": str(staged.get("title") or staged.get("story_slug") or folder.name),
        "text_hash_sha256": str(staged.get("text_hash_sha256") or ""),
        "stage": "youtube_safe_text",
        "browser_session_id": str(staged.get("browser_session_id") or BROWSER_SESSION_ID),
        "account": int(staged.get("account", staged.get("account_index", persistent_log_account_index(0)))),
        "account_index": int(staged.get("account_index", staged.get("account", persistent_log_account_index(0)))),
        "worker": str(staged.get("worker") or staged.get("worker_id") or WORKER_ID or "w1"),
        "worker_id": str(staged.get("worker_id") or staged.get("worker") or WORKER_ID or "w1"),
        "staged_at": str(staged.get("staged_at") or ""),
        "processed_at": processed_at,
        "legacy_done": True,
        "decision": "DONE",
        "info_path": clean_file.name,
        "info_absolute_path": str(clean_file.resolve()),
        "result_report_path": "",
    }
    out_path = folder / PROCESSED_MARKER_NAME
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PROCESSED] {folder.name}: wrote {PROCESSED_MARKER_NAME} decision=DONE")


def _write_orchestrator_policy_refusal_marker(
    folder: Path,
    *,
    chunk_index: int,
    chunks_total: int,
    response_excerpt: str,
    response_text: str,
) -> None:
    staged = _find_orchestrator_staged_marker(folder) or {}
    payload = {
        "schema_version": 1,
        "marker_format": "orchestrator_policy_refusal_v1",
        "run_id": str(staged.get("run_id") or ""),
        "story_id": str(staged.get("story_id") or ""),
        "story_slug": str(staged.get("story_slug") or folder.name),
        "title": str(staged.get("title") or staged.get("story_slug") or folder.name),
        "text_hash_sha256": str(staged.get("text_hash_sha256") or ""),
        "stage": "youtube_safe_text",
        "decision": "POLICY_REFUSAL",
        "reason_code": "GEMINI_POLICY_REFUSAL",
        "policy_refusal": True,
        "chunk_index": int(chunk_index),
        "chunks_total": int(chunks_total),
        "response_excerpt": str(response_excerpt or "")[:1000],
        "response_chars": len(response_text or ""),
        "account": int(staged.get("account", staged.get("account_index", persistent_log_account_index(0)))),
        "account_index": int(staged.get("account_index", staged.get("account", persistent_log_account_index(0)))),
        "worker": str(staged.get("worker") or staged.get("worker_id") or WORKER_ID or "w1"),
        "worker_id": str(staged.get("worker_id") or staged.get("worker") or WORKER_ID or "w1"),
        "browser_session_id": str(staged.get("browser_session_id") or BROWSER_SESSION_ID),
        "processed_at": _utc_marker_now(),
    }
    out_path = folder / POLICY_REFUSAL_MARKER_NAME
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[SAFE_POLICY_REFUSAL] story={folder.name} chunk={int(chunk_index)}/{int(chunks_total)} "
        f"reason=GEMINI_POLICY_REFUSAL marker={POLICY_REFUSAL_MARKER_NAME} "
        f"excerpt={str(response_excerpt or '')[:180]!r}"
    )


def story_has_partial_checkpoint(folder: Path) -> bool:
    """Есть незавершённая обработка — не делаем полный goto, чтобы не сбросить диалог Gemini."""
    src = pick_story_source_file(folder)
    if src is None:
        return False
    return progress_file_path(src).exists() or clean_tmp_path(src).exists()


def mark_folder_need_new_bot_rules(folder: Path) -> None:
    """После смены Gem-бота: снова отправить правила режима; чанки и *.tmp не удаляем."""
    src = pick_story_source_file(folder)
    if src is None:
        return
    prog = progress_file_path(src)
    if not prog.exists():
        return
    data = _read_progress_json(prog)
    if not isinstance(data, dict):
        return
    nx = int(data.get("next_chunk_index", 0))
    data["rules_confirmed"] = False
    data["gemini_url"] = ""
    _write_progress_json(prog, data)
    print(
        f"[ROTATE] Сохранён прогресс (следующий фрагмент {nx + 1}). "
        f"На новом боте снова отправлю инструкции режима; готовый текст в tmp не трогаю."
    )


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _collect_boundary_units(text: str) -> list[str]:
    """
    Делит текст на цельные блоки без разрыва середины абзаца:
    при наличии пустых строк — только по границам абзацев (\\n\\n),
    иначе — по одиночным переводам строк (\\n).
    """
    t = _normalize_newlines(text).strip()
    if not t:
        return []
    if re.search(r"\n\s*\n", t):
        parts = re.split(r"\n\s*\n+", t)
        return [p.strip() for p in parts if p.strip()]
    return [ln.strip() for ln in t.split("\n") if ln.strip()]


def _split_long_line_no_mid_word(line: str, max_chars: int) -> list[str]:
    """
    Если одна строка длиннее max_chars (нет переносов внутри), дробит по пробелам
    так, чтобы куски не превышали max_chars (последний запасной вариант).
    """
    line = line.rstrip()
    if not line:
        return []
    if len(line) <= max_chars:
        return [line]
    words = line.split()
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur_len + add <= max_chars:
            cur.append(w)
            cur_len += add
        else:
            if cur:
                out.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
    if cur:
        out.append(" ".join(cur))
    return out


def _split_oversized_unit(unit: str, max_chars: int) -> list[str]:
    """Абзац длиннее max_chars: сначала по внутренним \\n, затем по пробелам."""
    if len(unit) <= max_chars:
        return [unit]
    if "\n" in unit:
        acc: list[str] = []
        for ln in unit.split("\n"):
            s = ln.strip()
            if not s:
                continue
            acc.extend(_split_long_line_no_mid_word(s, max_chars))
        return acc
    return _split_long_line_no_mid_word(unit, max_chars)


def split_text_into_boundary_chunks(
    text: str,
    min_chars: int,
    max_chars: int,
) -> list[str]:
    """
    Разбивает текст на фрагменты длиной примерно в диапазоне [min_chars, max_chars] символов.

    Порядок границ (строго не режем «на глаз» посередине абзаца):
    1. Сначала текст делится на блоки: абзацы (пустая строка между блоками = \\n\\n).
    2. Если в тексте нет абзацев — блоком считается каждая непустая строка (\\n).
    3. Блоки последовательно упаковываются в чанки: суммарная длина чанка не больше max_chars.
    4. Если один блок всё ещё длиннее max_chars, он дополнительно делится по строкам и пробелам.

    Последний чанк может быть короче min_chars; при необходимости он склеивается с предыдущим,
    если получился слишком короткий «хвост».

    Возвращает список строк-чанков; для пустого входа — [].
    """
    if min_chars > max_chars:
        min_chars, max_chars = max_chars, min_chars
    units = _collect_boundary_units(text)
    expanded: list[str] = []
    for u in units:
        if len(u) <= max_chars:
            expanded.append(u)
        else:
            expanded.extend(_split_oversized_unit(u, max_chars))
    if not expanded:
        return []
    sep = "\n\n"
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for u in expanded:
        add = len(u) + (len(sep) if buf else 0)
        if buf_len + add <= max_chars:
            buf.append(u)
            buf_len += add
            continue
        if buf:
            chunks.append(sep.join(buf))
        buf = [u]
        buf_len = len(u)
    if buf:
        chunks.append(sep.join(buf))
    if len(chunks) >= 2 and len(chunks[-1]) < min(min_chars // 2, 800):
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()
    return [c.strip() for c in chunks if c.strip()]


def strip_marker_lines_from_response(response_text: str) -> str:
    """
    Убирает из ответа модели строки с REQUEST_MARKER и любые «служебные» хвосты.
    В итоговый *_clean.txt попадает только чистый текст без маркеров.
    """
    out_lines: list[str] = []
    prefix_lower = RESPONSE_MARKER_PREFIX.lower()
    for line in response_text.replace("\r\n", "\n").split("\n"):
        if prefix_lower in line.lower():
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def _safe_response_stable_seconds() -> float:
    try:
        return max(3.0, float((os.getenv("GEMINI_SAFE_RESPONSE_STABLE_SEC") or "4").strip() or "4"))
    except ValueError:
        return 4.0


def _wait_response_text_stable(page: Page, *, previous_text: str = "") -> str:
    """Wait for streaming to finish and response text to stay unchanged."""
    stable_sec = _safe_response_stable_seconds()
    deadline = time.time() + WAIT_TIMEOUT_MS / 1000
    last_text = str(previous_text or "")
    stable_since = 0.0
    while time.time() < deadline:
        if is_generation_in_progress(page):
            stable_since = 0.0
            time.sleep(0.4)
            continue
        current = read_latest_response_from_dom(page)
        if current and current != last_text:
            last_text = current
            stable_since = time.time()
        elif current:
            if stable_since <= 0:
                stable_since = time.time()
            if time.time() - stable_since >= stable_sec:
                return current
        time.sleep(0.4)
    return last_text


def _exchange_message_and_read(
    page: Page,
    folder: Path,
    message_body: str,
    *,
    min_response_chars: int | None = None,
) -> str:
    """
    Вставляет текст в поле Gem, отправляет, ждёт ответ и возвращает только текст ответа
    (без строк REQUEST_MARKER — они вырезаются).
    """
    wait_for_generation_idle(page, timeout_ms=WAIT_TIMEOUT_MS)
    prepare_clean_prompt(page)
    snapshot_before_response = read_latest_response_from_dom(page)
    previous_response_blocks = get_response_blocks_count(page)
    previous_copy_count = get_copy_buttons_count(page)
    if has_limit_message(page):
        raise RuntimeError("Лимит Gem-бота: сервис временно не принимает запросы.")
    prompt_input = wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
    prompt_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.press("Escape")
    time.sleep(0.15)
    prompt_input.click()
    page.keyboard.insert_text(message_body)
    if INSERT_SETTLE_MS > 0:
        time.sleep(INSERT_SETTLE_MS / 1000.0)
    try:
        wait_until_send_clickable(page)
    except TimeoutError as send_ready_err:
        if has_limit_message(page):
            raise RuntimeError("Лимит Gem-бота: сервис временно не отправляет запросы.") from send_ready_err
        raise RuntimeError(
            f"{folder.name}: поле ввода не дало отправить сообщение (кнопка не активна). "
            f"Попробуй уменьшить CHUNK_MAX_CHARS или увеличить GEMINI_SEND_READY_TIMEOUT_MS."
        ) from send_ready_err
    if not try_send_message(page):
        if has_limit_message(page):
            raise RuntimeError("Лимит Gem-бота: сервис временно не отправляет запросы.")
        raise RuntimeError(f"{folder.name}: не удалось отправить запрос")
    try:
        wait_for_new_response_block(
            page,
            previous_response_blocks,
            previous_copy_count,
            timeout_ms=WAIT_TIMEOUT_MS,
        )
    except TimeoutError as timeout_error:
        if has_limit_message(page):
            raise RuntimeError("Лимит Gem-бота: генерация заблокирована лимитами.") from timeout_error
        try:
            copy_button = wait_response_ready(page, previous_copy_count, timeout_ms=WAIT_TIMEOUT_MS)
            if not click_copy_button_resilient(page, copy_button):
                raise timeout_error
        except Exception:
            if has_limit_message(page):
                raise RuntimeError("Лимит Gem-бота: генерация заблокирована лимитами.") from timeout_error
            raise
    human_pause("после ответа модели")
    response_text = _wait_response_text_stable(page, previous_text=snapshot_before_response)
    if not response_text:
        try:
            copy_button = wait_response_ready(page, previous_copy_count, timeout_ms=WAIT_TIMEOUT_MS)
            if click_copy_button_resilient(page, copy_button):
                response_text = read_clipboard_text(page)
        except Exception:
            response_text = ""
    if not response_text:
        raise RuntimeError(f"{folder.name}: пустой ответ модели (response_incomplete)")
    if min_response_chars is None:
        min_stable_chars = max(80, _env_int("GEMINI_SAFE_MIN_RESPONSE_CHARS", 120))
    else:
        min_stable_chars = max(0, int(min_response_chars))
    if min_stable_chars > 0 and len(response_text.strip()) < min_stable_chars:
        raise RuntimeError(
            f"{folder.name}: response_incomplete — ответ слишком короткий ({len(response_text.strip())} симв.)"
        )
    if len(response_text) < 1200:
        low = response_text.lower()
        if any(h in low for h in LIMIT_HINTS_SHORT_RESPONSE):
            raise RuntimeError("Лимит Gem-бота: в ответе модели сообщение о лимите/квоте.")
    skip_fp = (os.getenv("GEMINI_SKIP_RESPONSE_FINGERPRINT_CHECK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not skip_fp:
        fp_before = _response_fingerprint(snapshot_before_response)
        fp_after = _response_fingerprint(response_text)
        if fp_before and fp_before == fp_after:
            raise RuntimeError(
                f"{folder.name}: ответ в чате не изменился после запроса — нового ответа нет "
                f"(лимит/сбой; раньше из-за короткого сообщения об ошибке подставлялся предыдущий длинный ответ)."
            )
    return strip_marker_lines_from_response(response_text)


def _safe_max_chunks_per_chat() -> int:
    raw = _env_int("GEMINI_SAFE_MAX_CHUNKS_PER_CHAT", 3)
    return max(1, min(10, raw))


_safe_model_session: dict[str, dict] = {}


def _safe_model_session_state() -> dict:
    key = _safe_story_account_label()
    state = _safe_model_session.setdefault(
        key,
        {
            "model_selected": False,
            "model_selected_model": "",
            "model_selected_at": "",
            "model_select_attempts": 0,
            "last_model_name": "",
            "page_reload_count": 0,
            "reload_after_model_select": False,
            "last_reload_reason": "",
            "last_action": "",
            "last_recycle_reason": "",
        },
    )
    return state


def _safe_persistent_session_active() -> bool:
    return (os.getenv("GEMINI_PERSISTENT_INBOX") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _safe_log_page_reload(reason: str, *, caller: str, attempt: int = 0) -> bool:
    if not _safe_persistent_session_active():
        return True
    state = _safe_model_session_state()
    after_model_select = state.get("last_action") == "model_selected"
    print(
        f"[SAFE_PAGE_RELOAD_CALL] caller={caller} account=acc{_safe_story_account_label()} "
        f"reason={reason} after_model_select={str(bool(after_model_select)).lower()}"
    )
    state["reload_after_model_select"] = bool(after_model_select)
    print("[SAFE_PAGE_RELOAD_DISABLED] reason=persistent_safe_no_reload")
    return False


def _safe_persistent_nav_recover(page: Page, url: str, *, reason: str, caller: str) -> bool:
    """Persistent safe: re-open Gem bot URL instead of page.reload (keeps profile, fresh UI)."""
    if not _safe_persistent_session_active():
        return False
    target = str(url or page.url or "").strip()
    if not target.startswith("https://gemini.google.com/"):
        return False
    print(
        f"[SAFE_NAV_RECOVER] account=acc{_safe_story_account_label()} caller={caller} "
        f"reason={reason} action=goto url={target[:96]}"
    )
    sys.stdout.flush()
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=60_000)
    except Exception as exc:
        print(f"[SAFE_NAV_RECOVER_FAIL] {exc}")
        sys.stdout.flush()
        return False
    sleep_interruptible(2.0)
    return wait_for_prompt_input_soft(page, timeout_ms=20_000)


def _safe_skip_redundant_goto(page: Page, url: str, *, context: str = "") -> bool:
    """В persistent safe не делаем page.goto на тот же URL — это выглядит как перезагрузка."""
    if not _safe_persistent_session_active():
        return False
    if _url_path_norm(page.url) != _url_path_norm(url):
        return False
    ctx = f" context={context}" if context else ""
    print(f"[SAFE_NAV_SKIP] already_on_target url={url}{ctx}")
    sys.stdout.flush()
    return True


def _safe_model_select_aliases() -> list[str]:
    from orchestrator.gemini_model_resolver import model_fallback_order_for_stage

    aliases: list[str] = []
    for tier in model_fallback_order_for_stage("safe"):
        alias = "default" if tier == "balanced" else tier
        if alias not in aliases:
            aliases.append(alias)
    return aliases or ["thinking", "default"]


def ensure_safe_thinking_mode_once(
    page: Page,
    *,
    force: bool = False,
    reason: str = "",
    caller: str = "ensure_safe_thinking_mode_once",
) -> None:
    """Select Gemini model at most once per safe browser/account session unless forced."""
    from orchestrator.gemini_model_resolver import (
        resolve_gemini_model_alias,
        ui_label_matches_gemini_choice,
    )

    state = _safe_model_session_state()
    attempts_before = int(state.get("model_select_attempts") or 0)
    already_selected = bool(state.get("model_selected"))
    print(
        f"[SAFE_MODEL_SELECT_CALL] caller={caller} account=acc{_safe_story_account_label()} "
        f"attempt={attempts_before + 1} reason={reason or 'select_model'} "
        f"already_selected={str(already_selected).lower()}"
    )
    if already_selected and not force:
        print(f"[SAFE_MODEL_SELECT_SKIP] account=acc{_safe_story_account_label()} reason=already_selected")
        print(
            f"[SAFE_MODEL_SELECTED] account=acc{_safe_story_account_label()} "
            f"model={state.get('model_selected_model') or state.get('last_model_name') or 'Thinking'} "
            f"attempts={attempts_before or 1} reason=already_selected"
        )
        return

    current_label = current_model_label_from_toolbar(page)
    if not force and attempts_before >= 1:
        stored_alias = str(state.get("model_selected_alias") or "")
        if stored_alias:
            stored_choice = resolve_gemini_model_alias(stored_alias)
            if ui_label_matches_gemini_choice(current_label, stored_choice):
                state["model_selected"] = True
                state["model_selected_model"] = str(stored_choice.preferred_ui_label)
                state["last_model_name"] = str(stored_choice.preferred_ui_label)
                state["last_action"] = "model_selected"
                print(
                    f"[SAFE_MODEL_SELECTED] account=acc{_safe_story_account_label()} "
                    f"model={stored_choice.preferred_ui_label} attempts={attempts_before} "
                    f"reason=verified_without_reselect"
                )
                return

    if force or attempts_before >= 1:
        print(
            f"[SAFE_MODEL_RESELECT] account=acc{_safe_story_account_label()} "
            f"reason={reason or 'verification_failed'} attempts_before={attempts_before}"
        )

    aliases = _safe_model_select_aliases()
    verified = False
    selected_choice = resolve_gemini_model_alias(aliases[0])
    selected_alias = aliases[0]
    for alias in aliases:
        choice = resolve_gemini_model_alias(alias)
        current_label = current_model_label_from_toolbar(page)
        if ui_label_matches_gemini_choice(current_label, choice) or _safe_accept_model_label(alias, current_label):
            verified = True
            selected_choice = choice
            selected_alias = alias
            print(
                f"[SAFE_MODEL_FALLBACK_OK] account=acc{_safe_story_account_label()} "
                f"alias={alias} model={choice.preferred_ui_label} reason=already_on_toolbar"
            )
            break
        print(
            f"[SAFE_MODEL_FALLBACK_TRY] account=acc{_safe_story_account_label()} "
            f"alias={alias} model={choice.preferred_ui_label}"
        )
        if ensure_thinking_mode(page, alias=alias, safe_caller=caller, safe_reason=reason):
            verified = True
            selected_choice = choice
            selected_alias = alias
            selected_label = current_model_label_from_toolbar(page)
            print(
                f"[SAFE_MODEL_FALLBACK_OK] account=acc{_safe_story_account_label()} "
                f"alias={alias} model={choice.preferred_ui_label} "
                f"toolbar={selected_label!r} reason=ui_selected"
            )
            break

    state["model_select_attempts"] = attempts_before + 1
    state["last_model_name"] = str(selected_choice.preferred_ui_label)
    if not verified:
        final_label = current_model_label_from_toolbar(page)
        for alias in aliases:
            if _safe_accept_model_label(alias, final_label):
                choice = resolve_gemini_model_alias(alias)
                verified = True
                selected_choice = choice
                selected_alias = alias
                print(
                    f"[SAFE_MODEL_FALLBACK_OK] account=acc{_safe_story_account_label()} "
                    f"alias={alias} model={choice.preferred_ui_label} "
                    f"toolbar={final_label!r} reason=selection_style_final_toolbar"
                )
                break
    if not verified:
        state["model_selected"] = False
        state["model_selected_model"] = ""
        state["last_action"] = "model_select_failed"
        print(
            f"[SAFE_MODEL_SELECT_FAILED] account=acc{_safe_story_account_label()} "
            f"aliases={','.join(aliases)} attempts={state['model_select_attempts']} "
            f"caller={caller} reason=no_acceptable_model continue=true"
        )
        print(
            "[WARN] SAFE_MODEL_SELECT_NOT_VERIFIED — продолжаю safe-сессию "
            "(selection-style, без FATAL exit)."
        )
        return
    state["model_selected"] = True
    state["model_selected_model"] = str(selected_choice.preferred_ui_label)
    state["model_selected_alias"] = str(selected_alias)
    state["last_action"] = "model_selected"
    from datetime import datetime, timezone

    state["model_selected_at"] = datetime.now(timezone.utc).isoformat()
    print(
        f"[SAFE_MODEL_SELECTED] account=acc{_safe_story_account_label()} "
        f"model={selected_choice.preferred_ui_label} alias={selected_alias} "
        f"attempts={state['model_select_attempts']}"
    )


def ensure_runtime_thinking_mode(page: Page, *, caller: str, reason: str = "") -> None:
    if _safe_persistent_session_active():
        ensure_safe_thinking_mode_once(page, caller=caller, reason=reason)
        return
    ensure_thinking_mode(page)


def _safe_strict_validation_enabled() -> bool:
    return (os.getenv("GEMINI_SAFE_STRICT_VALIDATION") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _safe_max_chunk_retries() -> int:
    return max(0, _env_int("GEMINI_SAFE_MAX_CHUNK_RETRIES", 2))


def _safe_story_account_label() -> str:
    return (os.getenv("GEMINI_LOG_ACCOUNT_INDEX") or os.getenv("START_ACCOUNT_INDEX") or "0").strip()


def _safe_chunks_dir(folder: Path) -> Path:
    raw = (os.getenv("GEMINI_SAFE_CHUNK_ARTIFACTS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (folder / "chunks").resolve()


def _validate_safe_chunk_output(input_text: str, output_text: str, **kwargs) -> dict:
    if not _safe_strict_validation_enabled():
        return {"passed": True, "failure_reason": "", "warnings": []}
    try:
        from orchestrator.youtube_full_auto.safe_chunk_quality import (
            default_safe_quality_config,
            validate_chunk_output,
        )

        return validate_chunk_output(input_text, output_text, config=default_safe_quality_config(), **kwargs)
    except Exception:
        out_words = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", output_text or ""))
        in_words = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", input_text or ""))
        ratio_ok = out_words >= max(1, int(in_words * 0.70))
        return {
            "passed": ratio_ok and bool((output_text or "").strip()),
            "failure_reason": "" if ratio_ok else "SAFE_CHUNK_TOO_SHORT",
            "warnings": [],
        }


def _save_safe_chunk_artifacts(
    folder: Path,
    *,
    chunk_index: int,
    input_text: str,
    raw_response: str,
    clean_text: str,
    validation: dict,
) -> None:
    try:
        from orchestrator.youtube_full_auto.safe_chunk_artifacts import save_chunk_artifacts

        save_chunk_artifacts(
            _safe_chunks_dir(folder),
            chunk_index=chunk_index,
            input_text=input_text,
            raw_response=raw_response,
            clean_text=clean_text,
            validation=validation,
        )
    except Exception as exc:
        print(f"[WARN] {folder.name}: не удалось сохранить chunk artifacts: {exc}")


def _start_new_safe_gemini_chat(
    page: Page,
    folder: Path,
    gemini_url: str,
    hub_url: str | None,
    *,
    rules_body: str,
    chat_index: int,
    after_chunk: int,
    old_chat_index: int = 0,
    reason: str = "max_chunks_per_chat",
    forced: bool = False,
) -> None:
    story_slug = folder.name
    account = _safe_story_account_label()
    next_chunk = int(after_chunk) + 1
    tag = "SAFE_CHAT_FORCED_RECYCLE" if forced else "SAFE_CHAT_RECYCLE"
    _safe_model_session_state()["last_recycle_reason"] = str(reason)
    print(
        f"[{tag}] story={story_slug} account=acc{account} "
        f"after_chunk={after_chunk} next_chunk={next_chunk} "
        f"old_chat={old_chat_index or max(1, chat_index - 1)} new_chat={chat_index} reason={reason}"
    )
    opened = False
    for _try in range(1, 4):
        if recover_session_state(
            page,
            gemini_url,
            hub_url=hub_url,
            resume_same_chat=False,
            bot_entry=None,
            force_goto=True,
        ):
            opened = True
            break
        wait_with_status(45, f"Не удалось открыть новый Gemini chat ({_try}/3)")
    if not opened:
        raise RuntimeError(f"{folder.name}: не удалось открыть новый Gemini chat для safe recycle")
    ensure_safe_thinking_mode_once(page, force=forced, reason=reason, caller="_start_new_safe_gemini_chat")
    human_pause("перед повторной отправкой safe-инструкции")
    rules_sent = False
    last_rules_err = ""
    for rules_try in range(1, 4):
        try:
            _exchange_message_and_read(page, folder, rules_body, min_response_chars=0)
            rules_sent = True
            break
        except RuntimeError as exc:
            last_rules_err = str(exc)
            print(
                f"[SAFE_CHAT_RULES_RETRY] story={story_slug} chat={chat_index} "
                f"attempt={rules_try}/3 error={last_rules_err[:160]}"
            )
            human_pause("перед повторной отправкой safe-инструкции после recycle")
    if not rules_sent:
        raise RuntimeError(
            f"{folder.name}: не удалось подтвердить safe-инструкцию после recycle: {last_rules_err}"
        )
    model_selected = bool(_safe_model_session_state().get("model_selected"))
    print(
        f"[SAFE_NEW_CHAT] story={story_slug} chat={chat_index} "
        f"model_selected={str(model_selected).lower()} reload=false"
    )
    print(f"[SAFE_CHAT_NEW] story={story_slug} chat={chat_index} instruction_sent=true")


def process_story_folder(page: Page, folder: Path, gemini_url: str, hub_url: str | None = None) -> bool:
    """
    Читает исходный .txt, режет на чанки по границам абзацев, отправляет в Gem по очереди:
    сначала сообщение с «правилами», затем каждый фрагмент. Ответы пишутся во временный
    *_clean.tmp, и только после завершения ВСЕХ чанков файл переименовывается в *_clean.txt.

    Прогресс хранится в *_clean.progress.json; при сбое можно продолжить с того же чанка без
    полного перезапуска. Сброс: удалить tmp и progress или GEMINI_RESET_PARTIAL=1.

    Новый рассказ по умолчанию открывается через goto; при возобновлении (resume) страница
    не перезагружается, чтобы не потерять контекст диалога в Gemini.
    В файл попадает только текст ответа, без REQUEST_MARKER и прочих служебных вставок.
    """
    source_file = pick_story_source_file(folder)
    if source_file is None:
        print(f"[SKIP] {folder.name}: нет исходного .txt файла")
        return False

    clean_path = clean_output_path(source_file)
    if clean_path.exists():
        print(f"[SKIP] {folder.name}: уже есть готовый файл {clean_path.name}")
        return False

    tmp_path = clean_tmp_path(source_file)
    prog_path = progress_file_path(source_file)
    reset_partial = (os.getenv("GEMINI_RESET_PARTIAL") or "").strip().lower() in ("1", "true", "yes", "on")

    raw_text = source_file.read_text(encoding="utf-8", errors="replace")
    chunks = split_text_into_boundary_chunks(raw_text, CHUNK_MIN_CHARS, CHUNK_MAX_CHARS)
    if not chunks:
        raise RuntimeError(f"{folder.name}: после разбивки не осталось ни одного чанка (пустой файл?)")

    total_chunks = len(chunks)
    try:
        source_mtime_ns = source_file.stat().st_mtime_ns
    except Exception:
        source_mtime_ns = 0

    if reset_partial:
        if tmp_path.exists():
            tmp_path.unlink()
        if prog_path.exists():
            prog_path.unlink()
        print(f"[INFO] {folder.name}: GEMINI_RESET_PARTIAL — сброшены tmp и progress.")

    rules_confirmed = False
    start_i = 0
    resume_same_chat = False
    loaded_chunk_index_from_json = False

    pr = _read_progress_json(prog_path) if prog_path.exists() else None
    if pr and pr.get("version") == PROGRESS_VERSION and pr.get("total_chunks") == total_chunks:
        if pr.get("source_mtime_ns") != source_mtime_ns:
            print(f"[WARN] {folder.name}: исходник изменился — начинаю заново (прогресс сброшен).")
            pr = None
        else:
            rules_confirmed = bool(pr.get("rules_confirmed", False))
            start_i = int(pr.get("next_chunk_index", 0))
            saved_bot = (pr.get("gemini_url") or "").strip()
            same_bot = not saved_bot or saved_bot == gemini_url
            if rules_confirmed and not same_bot:
                print(f"[INFO] {folder.name}: в прогрессе другой URL бота — повторю правила для текущего бота.")
                rules_confirmed = False
            resume_same_chat = bool(rules_confirmed and same_bot)
            loaded_chunk_index_from_json = True
            print(f"[RESUME] {folder.name}: чекпоинт — следующий чанк {start_i + 1}/{total_chunks}.")

    # Оценка по tmp — только если нет валидного progress.json (иначе грубый подсчёт \\n\\n даёт ложный индекс).
    if not resume_same_chat and tmp_path.exists() and not loaded_chunk_index_from_json:
        inferred = _infer_next_chunk_from_tmp(tmp_path, total_chunks)
        if inferred is None:
            print(
                f"[WARN] {folder.name}: не удалось восстановить прогресс по {tmp_path.name}. "
                f"Удали файл или задай GEMINI_RESET_PARTIAL=1."
            )
            return False
        if inferred == 0:
            tmp_path.unlink()
            print(f"[INFO] {folder.name}: пустой tmp удалён — полный прогон с начала.")
        else:
            rules_confirmed = True
            start_i = inferred
            resume_same_chat = True
            _write_progress_json(
                prog_path,
                {
                    "version": PROGRESS_VERSION,
                    "total_chunks": total_chunks,
                    "source_mtime_ns": source_mtime_ns,
                    "rules_confirmed": True,
                    "next_chunk_index": start_i,
                    "gemini_url": gemini_url,
                },
            )
            print(f"[RESUME] {folder.name}: по размеру tmp — следующий чанк {start_i + 1}/{total_chunks}.")

    if not resume_same_chat and tmp_path.exists() and loaded_chunk_index_from_json:
        inferred_dbg = _infer_next_chunk_from_tmp(tmp_path, total_chunks)
        if inferred_dbg is not None and inferred_dbg != start_i:
            print(
                f"[INFO] {folder.name}: по числу \\n\\n-блоков в {tmp_path.name} вышло бы ~{inferred_dbg + 1}-й чанк, "
                f"в {prog_path.name} — следующий {start_i + 1}/{total_chunks}; использую JSON (точный чекпоинт)."
            )

    if start_i > 0 and not tmp_path.exists():
        print(f"[WARN] {folder.name}: в прогрессе чанк {start_i + 1}, но нет {tmp_path.name}. Сбрось или восстанови tmp.")
        return False

    if resume_same_chat:
        print("[SESSION] Продолжение рассказа — без перезагрузки страницы (тот же диалог Gemini).")
    elif _safe_persistent_session_active():
        print("[SESSION] Новый рассказ — продолжаю в том же окне Gemini (без перезагрузки).")
    else:
        print("[SESSION] Новый рассказ — открываю бота по ссылке (новый диалог).")
    while not recover_session_state(page, gemini_url, hub_url=hub_url, resume_same_chat=resume_same_chat, bot_entry=None):
        wait_with_status(180, "Не удалось открыть Gem для нового рассказа, повтор")
    ensure_safe_thinking_mode_once(page, reason="story_session_start", caller="process_story_folder")

    handshake_lang = (os.getenv("GEMINI_SAFE_HANDSHAKE_LANG") or "").strip().lower()
    if handshake_lang in {"en", "english"}:
        rules_body = (
            "Chunked YouTube voiceover mode. I will send English story fragments.\n"
            "Process each part fully: no shortening, no summary — full narration-ready text.\n"
            "Reply only with processed English text or a brief English confirmation, no markers.\n"
            "Always output in English to match the source story language."
        )
    else:
        rules_body = (
            "Режим работы по частям (озвучка для YouTube). Дальше я буду присылать текст фрагментами.\n"
            "Каждую часть нужно обработать полностью: без сокращений, без саммари — "
            "только полный текст, пригодный для озвучки, без потери смысла и сюжета.\n"
            "Отвечай только обработанным текстом или коротким подтверждением, без служебных строк и маркеров.\n"
            "Подтверди одним-двумя предложениями, что готов к такому режиму."
        )

    print(
        f"[RUN] {folder.name}: исходник {source_file.name} → {clean_path.name} "
        f"(чанков: {total_chunks}, целевой размер {CHUNK_MIN_CHARS}–{CHUNK_MAX_CHARS} симв.)"
    )

    if not rules_confirmed:
        human_pause("перед отправкой правил режима")
        _exchange_message_and_read(page, folder, rules_body, min_response_chars=0)
        rules_confirmed = True
        _write_progress_json(
            prog_path,
            {
                "version": PROGRESS_VERSION,
                "total_chunks": total_chunks,
                "source_mtime_ns": source_mtime_ns,
                "rules_confirmed": True,
                "next_chunk_index": 0,
                "gemini_url": gemini_url,
            },
        )

    recycle_after = _safe_max_chunks_per_chat()
    configured_max_chunk_retries = max(2, _safe_max_chunk_retries())
    account_label = _safe_story_account_label()
    chunk_validations: list[dict] = []
    from orchestrator.youtube_full_auto.safe_chunk_quality import (
        accept_repeated_policy_trim,
        accept_stable_policy_trim,
        chunks_in_current_chat,
        format_safe_chat_plan,
        safe_chat_index_for_chunk,
        should_recycle_chat_before_chunk,
    )
    from orchestrator.youtube_full_auto.gemini_policy_refusal import detect_gemini_policy_refusal

    plan = format_safe_chat_plan(total_chunks, recycle_after=recycle_after)
    print(
        f"[SAFE_CONFIG] story={folder.name} recycle_after_chunks={recycle_after} "
        f"configured_max_chunk_retries={configured_max_chunk_retries} "
        f"retry_flow=in_chat_once_then_recycle_with_full_prompt"
    )
    print(
        f"[SAFE_CHAT_PLAN] story={folder.name} total_chunks={total_chunks} "
        f"max_per_chat={recycle_after} plan={plan}"
    )

    for i in range(start_i, total_chunks):
        chunk = chunks[i]
        chunk_1based = i + 1
        chat_index = safe_chat_index_for_chunk(chunk_1based, recycle_after=recycle_after)
        if should_recycle_chat_before_chunk(i, recycle_after=recycle_after):
            _start_new_safe_gemini_chat(
                page,
                folder,
                gemini_url,
                hub_url,
                rules_body=rules_body,
                chat_index=chat_index,
                after_chunk=i,
                old_chat_index=max(1, chat_index - 1),
                reason="max_chunks_per_chat",
                forced=False,
            )
        in_chat = chunks_in_current_chat(chunk_1based, recycle_after=recycle_after)
        print(
            f"[SAFE_CHAT_CONTINUE] story={folder.name} chat={chat_index} "
            f"chunk={chunk_1based}/{total_chunks} chunks_in_chat={in_chat}/{recycle_after}"
        )
        user_msg = (
            f"Фрагмент {i + 1} из {total_chunks}. Обработай ТОЛЬКО этот фрагмент полностью (без сокращения).\n\n"
            f"{chunk}"
        )
        print(f"[CHUNK] {folder.name}: часть {i + 1}/{total_chunks} (~{len(chunk)} символов)")

        max_story_quality_chat_retries = max(
            1, int(os.getenv("GEMINI_SAFE_STORY_QUALITY_RETRIES", "2") or "2")
        )
        chunk_ok = False
        validated = ""
        validation: dict = {"passed": True, "failure_reason": ""}
        last_failure = ""
        short_retry_outputs: list[str] = []
        for story_q_attempt in range(1, max_story_quality_chat_retries + 1):
            validated = ""
            validation = {"passed": True, "failure_reason": ""}
            last_failure = ""
            retry_phases: list[tuple[str, bool]] = [
                ("initial", False),
                ("in_chat_retry", False),
                ("recycle_retry", True),
            ]
            for phase_name, force_recycle in retry_phases:
                if force_recycle:
                    _start_new_safe_gemini_chat(
                        page,
                        folder,
                        gemini_url,
                        hub_url,
                        rules_body=rules_body,
                        chat_index=safe_chat_index_for_chunk(chunk_1based, recycle_after=recycle_after),
                        after_chunk=max(0, i - 1),
                        old_chat_index=chat_index,
                        reason="quality_retry_recycle",
                        forced=True,
                    )
                    chat_index = safe_chat_index_for_chunk(chunk_1based, recycle_after=recycle_after)
                try:
                    validated = _exchange_message_and_read(page, folder, user_msg, min_response_chars=0)
                except RuntimeError as exc:
                    err_text = str(exc)
                    last_failure = "response_incomplete" if "response_incomplete" in err_text.lower() else err_text
                    print(
                        f"[SAFE_CHUNK_RETRY] story={folder.name} chunk={chunk_1based}/{total_chunks} "
                        f"phase={phase_name} reason={last_failure}"
                    )
                    continue
                refused, refusal_excerpt = detect_gemini_policy_refusal(validated)
                if refused:
                    validation = {
                        "passed": False,
                        "failure_reason": "GEMINI_POLICY_REFUSAL",
                        "policy_refusal": True,
                        "response_excerpt": refusal_excerpt,
                        "chunk_index": chunk_1based,
                        "chunks_total": total_chunks,
                        "phase": phase_name,
                        "chat_index": chat_index,
                    }
                    _save_safe_chunk_artifacts(
                        folder,
                        chunk_index=chunk_1based,
                        input_text=chunk,
                        raw_response=validated,
                        clean_text="",
                        validation=validation,
                    )
                    _write_orchestrator_policy_refusal_marker(
                        folder,
                        chunk_index=chunk_1based,
                        chunks_total=total_chunks,
                        response_excerpt=refusal_excerpt,
                        response_text=validated,
                    )
                    raise RuntimeError(
                        f"policy_refusal_safe: GEMINI_POLICY_REFUSAL chunk {chunk_1based}/{total_chunks}"
                    )
                validation = _validate_safe_chunk_output(
                    chunk,
                    validated,
                    chunk_index=i + 1,
                    chunks_total=total_chunks,
                    retries=retry_phases.index((phase_name, force_recycle)),
                    chat_index=chat_index,
                    account=f"acc{account_label}",
                )
                if validation.get("passed"):
                    if phase_name != "initial":
                        print(
                            f"[SAFE_CHUNK_RETRY_OK] story={folder.name} chunk={chunk_1based}/{total_chunks} "
                            f"phase={phase_name} chat={chat_index}"
                        )
                    chunk_ok = True
                    break
                last_failure = str(validation.get("failure_reason") or "SAFE_CHUNK_TOO_SHORT")
                if (os.getenv("GEMINI_SAFE_ACCEPT_STABLE_POLICY_TRIM") or "1").strip().lower() not in (
                    "0",
                    "false",
                    "no",
                    "off",
                ):
                    accepted = accept_stable_policy_trim(
                        validation,
                        input_text=chunk,
                        output_text=validated,
                        previous_outputs=short_retry_outputs,
                        min_similarity=_env_float("GEMINI_SAFE_POLICY_TRIM_MIN_SIMILARITY", 0.92),
                        min_ratio=_env_float("GEMINI_SAFE_POLICY_TRIM_MIN_RATIO", 0.10),
                        min_output_chars=_env_int("GEMINI_SAFE_POLICY_TRIM_MIN_CHARS", 40),
                        min_output_words=_env_int("GEMINI_SAFE_POLICY_TRIM_MIN_WORDS", 8),
                    )
                    if accepted is not None:
                        validation = accepted
                        last_failure = str(accepted.get("accept_reason") or "SAFE_CHUNK_POLICY_TRIM_ACCEPTED")
                        print(
                            f"[SAFE_CHUNK_POLICY_TRIM_ACCEPTED] story={folder.name} "
                            f"chunk={chunk_1based}/{total_chunks} phase={phase_name} "
                            f"reason={accepted.get('original_failure_reason', '')} "
                            f"similarity={accepted.get('stable_policy_trim_similarity', 0)} "
                            f"ratio={accepted.get('char_ratio', 0)} chat={chat_index}"
                        )
                        chunk_ok = True
                        break
                    repeated = accept_repeated_policy_trim(
                        validation,
                        input_text=chunk,
                        output_text=validated,
                        previous_outputs=short_retry_outputs,
                        min_outputs=_env_int("GEMINI_SAFE_POLICY_TRIM_REPEAT_OUTPUTS", 2),
                        min_ratio=_env_float("GEMINI_SAFE_POLICY_TRIM_REPEAT_MIN_RATIO", 0.12),
                        min_output_chars=_env_int("GEMINI_SAFE_POLICY_TRIM_REPEAT_MIN_CHARS", 120),
                        min_output_words=_env_int("GEMINI_SAFE_POLICY_TRIM_REPEAT_MIN_WORDS", 20),
                    )
                    if (
                        repeated is not None
                        and (os.getenv("GEMINI_SAFE_ACCEPT_REPEATED_POLICY_TRIM") or "1").strip().lower()
                        not in ("0", "false", "no", "off")
                    ):
                        validation = repeated
                        last_failure = str(repeated.get("accept_reason") or "SAFE_CHUNK_REPEATED_POLICY_TRIM_ACCEPTED")
                        print(
                            f"[SAFE_CHUNK_REPEATED_POLICY_TRIM_ACCEPTED] story={folder.name} "
                            f"chunk={chunk_1based}/{total_chunks} phase={phase_name} "
                            f"reason={repeated.get('original_failure_reason', '')} "
                            f"similarity={repeated.get('stable_policy_trim_similarity', 0)} "
                            f"ratio={repeated.get('char_ratio', 0)} chat={chat_index}"
                        )
                        chunk_ok = True
                        break
                    if validated.strip():
                        short_retry_outputs.append(validated)
                print(
                    f"[SAFE_CHUNK_DEBUG] story={folder.name} chunk={chunk_1based}/{total_chunks} "
                    f"phase={phase_name} reason={last_failure} response_chars={len(validated or '')}"
                )
                print(
                    f"[SAFE_CHUNK_RETRY] story={folder.name} chunk={chunk_1based}/{total_chunks} "
                    f"phase={phase_name} reason={last_failure} chat={chat_index}"
                )
            if chunk_ok:
                break
            if story_q_attempt < max_story_quality_chat_retries:
                print(
                    f"[SAFE_STORY_QUALITY_RETRY] story={folder.name} chunk={chunk_1based}/{total_chunks} "
                    f"attempt={story_q_attempt + 1}/{max_story_quality_chat_retries} "
                    f"reason={last_failure} action=new_chat_same_browser=true"
                )
                chat_index = safe_chat_index_for_chunk(chunk_1based, recycle_after=recycle_after)
                _start_new_safe_gemini_chat(
                    page,
                    folder,
                    gemini_url,
                    hub_url,
                    rules_body=rules_body,
                    chat_index=chat_index,
                    after_chunk=max(0, i - 1),
                    old_chat_index=chat_index,
                    reason="quality_story_retry",
                    forced=True,
                )

        _save_safe_chunk_artifacts(
            folder,
            chunk_index=i + 1,
            input_text=chunk,
            raw_response=validated,
            clean_text=validated,
            validation=validation,
        )
        chunk_validations.append(validation)

        if not validation.get("passed"):
            print(
                f"[SAFE_CHUNK_FAIL] story={folder.name} chunk={i + 1}/{total_chunks} "
                f"reason={last_failure} quality_retry_needed=true"
            )
            raise RuntimeError(
                f"{folder.name}: quality_retry_needed safe chunk {i + 1}/{total_chunks} "
                f"failed quality validation: {last_failure}"
            )

        sep_needed = tmp_path.exists() and tmp_path.stat().st_size > 0
        with open(tmp_path, "a", encoding="utf-8") as out:
            if sep_needed:
                out.write("\n\n")
            out.write(validated.strip())
            out.write("\n")
        print(
            f"[SAFE_CHUNK_SAVED] story={folder.name} chunk={chunk_1based}/{total_chunks} "
            f"chars={len(validated.strip())} chat={chat_index}"
        )
        _write_progress_json(
            prog_path,
            {
                "version": PROGRESS_VERSION,
                "total_chunks": total_chunks,
                "source_mtime_ns": source_mtime_ns,
                "rules_confirmed": True,
                "next_chunk_index": i + 1,
                "gemini_url": gemini_url,
                "safe_chat_index": chat_index,
                "safe_recycle_after_chunks": recycle_after,
            },
        )
        if i < total_chunks - 1:
            pause_sec = random.randint(CHUNK_PAUSE_MIN_SEC, CHUNK_PAUSE_MAX_SEC)
            print(f"[PAUSE] {pause_sec} с до следующего чанка (анти rate limit)")
            sleep_interruptible(float(pause_sec))

    assembled_text = tmp_path.read_text(encoding="utf-8", errors="replace") if tmp_path.is_file() else ""
    try:
        from orchestrator.youtube_full_auto.safe_chunk_artifacts import assemble_clean_story_from_chunks

        chunks_dir = folder / "chunks"
        chunk_clean_files = sorted(chunks_dir.glob("chunk_*.clean.txt")) if chunks_dir.is_dir() else []
        assembled_from_chunks = assemble_clean_story_from_chunks(chunks_dir) if chunk_clean_files else ""
        if len(chunk_clean_files) >= total_chunks and len(assembled_from_chunks.strip()) > len(assembled_text.strip()):
            print(
                f"[SAFE_ASSEMBLE_FROM_CHUNKS] story={folder.name} chunks={len(chunk_clean_files)}/{total_chunks} "
                f"tmp_chars={len(assembled_text.strip())} assembled_chars={len(assembled_from_chunks.strip())}"
            )
            assembled_text = assembled_from_chunks
            tmp_path.write_text(assembled_text, encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] {folder.name}: chunk assembly fallback skipped: {exc}")
    if _safe_strict_validation_enabled():
        try:
            from orchestrator.youtube_full_auto.safe_chunk_quality import (
                default_safe_quality_config,
                validate_story_output,
            )

            story_validation = validate_story_output(
                raw_text,
                assembled_text,
                config=default_safe_quality_config(),
                chunk_validations=chunk_validations,
            )
            if story_validation.get("verdict") == "FAIL":
                reason = str(story_validation.get("failure_reason") or "SAFE_STORY_RATIO_LOW")
                print(f"[SAFE_STORY_FAIL] {folder.name}: {reason} validation={story_validation}")
                raise RuntimeError(f"{folder.name}: safe story failed final validation: {reason}")
            if story_validation.get("verdict") == "WARN":
                print(f"[SAFE_STORY_WARN] {folder.name}: ratio below target — {story_validation}")
        except RuntimeError:
            raise
        except Exception as exc:
            print(f"[WARN] {folder.name}: story-level validation skipped: {exc}")

    try:
        prog_path.unlink()
    except Exception:
        pass
    tmp_path.rename(clean_path)
    print(f"[DONE] {folder.name}: сохранён {clean_path.name}")
    _write_orchestrator_processed_marker(folder, clean_file=clean_path)
    human_pause("между подпапками")
    return True


def generate_report(stories_dir: Path, processed_count: int, story_folders: list[Path]) -> None:
    build_genre_reports(stories_dir, story_folders)
    print(f"[INFO] Отчёты по жанрам созданы. Обработано в запуске: {processed_count}")


def consolidate_report_files(stories_dir: Path) -> None:
    report_files = sorted(
        [
            path
            for path in stories_dir.glob("result_report*.txt")
            if path.is_file()
        ],
        key=lambda path: path.stat().st_mtime,
    )
    if not report_files:
        return

    consolidated_file = stories_dir / REPORT_FILE_NAME
    combined_sections: list[str] = []
    for report_path in report_files:
        try:
            report_text = report_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            report_text = ""
        section = f"===== {report_path.name} =====\n{report_text}" if report_text else f"===== {report_path.name} ====="
        combined_sections.append(section)

    consolidated_text = "\n\n".join(combined_sections).strip() + "\n"
    consolidated_file.write_text(consolidated_text, encoding="utf-8")

    for report_path in report_files:
        if report_path == consolidated_file:
            continue
        try:
            report_path.unlink()
        except Exception:
            pass
    print(f"[INFO] Сводный отчёт: {consolidated_file}. Объединено файлов: {len(report_files)}")


def parse_youtube_suitable_status(info_text: str) -> str | None:
    """Ищет в тексте info.txt строку «подходит для YouTube: да|нет». Возвращает «да»/«нет» или None."""
    for line in info_text.replace("\r\n", "\n").splitlines():
        m = YOUTUBE_STATUS_LINE_PATTERN.match(line.strip())
        if m:
            return m.group(1).lower()
    return None


def build_single_genre_report(genre_dir: Path) -> None:
    info_files = sorted([path for path in genre_dir.rglob(INFO_FILE_NAME) if path.is_file()])
    youtube_yes: list[str] = []
    for info_path in info_files:
        folder = info_path.parent
        try:
            response = info_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            response = ""
        if parse_youtube_suitable_status(response) != "да":
            continue
        story_rel = folder.relative_to(genre_dir).as_posix()
        youtube_yes.append(story_rel)
    youtube_yes.sort(key=str.lower)
    genre_report_path = genre_dir / REPORT_FILE_NAME
    genre_report_path.write_text("\n".join(youtube_yes) + ("\n" if youtube_yes else ""), encoding="utf-8")
    print(f"[INFO] Отчёт жанра создан: {genre_report_path}")


def build_genre_reports(stories_dir: Path, story_folders: list[Path]) -> None:
    top_level_dirs = sorted([path for path in stories_dir.iterdir() if path.is_dir()], key=lambda path: path.name.lower())
    for genre_dir in top_level_dirs:
        build_single_genre_report(genre_dir)


def run_safe_preflight_only() -> int:
    """Open Gemini once and verify safe-stage UI readiness for preflight."""
    import traceback as _traceback

    email = (os.getenv("GEMINI_ACCOUNT_EMAIL") or "").strip()
    gem_url = (os.getenv("GEMINI_URL") or "https://gemini.google.com/").strip()
    profile_email = email
    context = None
    try:
        with sync_playwright() as playwright:
            print("[SAFE_PREFLIGHT] browser_started=true")
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(USER_DATA_DIR),
                channel="chrome",
                headless=False,
                slow_mo=SLOW_MO_MS,
                viewport=None,
                chromium_sandbox=True,
                args=append_chrome_proxy_args(["--disable-blink-features=AutomationControlled"]),
            )
            page = context.pages[0] if context.pages else context.new_page()
            print("[SAFE_PREFLIGHT] page_created=true")
            page.goto(gem_url, wait_until="domcontentloaded", timeout=90_000)
            current_url = str(page.url or "")
            page_title = ""
            try:
                page_title = str(page.title() or "")
            except Exception:
                page_title = ""
            login_required = any(
                token in current_url.lower()
                for token in ("accounts.google.com", "/signin", "/challenge/", "service/login")
            )
            input_ready = wait_for_prompt_input_soft(page, timeout_ms=60_000)
            ensure_safe_thinking_mode_once(page, reason="safe_preflight", caller="run_safe_preflight_only")
            model_state = _safe_model_session_state()
            selected_model = current_model_label_from_toolbar(page)
            if not str(selected_model or "").strip():
                selected_model = str(
                    model_state.get("model_selected_model") or model_state.get("last_model_name") or ""
                ).strip()
            model_menu_ready = bool(str(selected_model or "").strip()) or bool(model_state.get("model_selected"))
            gemini_page_ok = "gemini.google.com" in current_url.lower() and not login_required
            print(
                f"[SAFE_PREFLIGHT] current_url={current_url} page_title={page_title} "
                f"profile_email={profile_email} expected_email={email} "
                f"email_match={str(profile_email.lower() == email.lower() if email else False).lower()} "
                f"input_ready={str(bool(input_ready)).lower()} "
                f"model_menu_ready={str(model_menu_ready).lower()} "
                f"selected_model={selected_model} gemini_page_ok={str(gemini_page_ok).lower()} "
                f"login_required={str(login_required).lower()}"
            )
            ok = gemini_page_ok and input_ready and model_menu_ready and not login_required
            return EXIT_CODE_OK if ok else EXIT_CODE_ERROR
    except Exception as exc:
        tail = _traceback.format_exc()[-2000:]
        print(f"[SAFE_PREFLIGHT] preflight_failed error={exc}")
        print(f"[SAFE_PREFLIGHT] traceback_tail={tail}")
        return EXIT_CODE_ERROR
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def main() -> int:
    if (os.getenv("GEMINI_SAFE_PREFLIGHT_ONLY") or "").strip() == "1":
        STORIES_DIR.mkdir(parents=True, exist_ok=True)
        if not USER_DATA_DIR.is_dir():
            print(f"[SAFE_PREFLIGHT] profile_dir_missing path={USER_DATA_DIR}")
            return EXIT_CODE_ERROR
        return run_safe_preflight_only()

    if not STORIES_DIR.exists():
        raise FileNotFoundError(f"Папка stories не найдена: {STORIES_DIR}")

    if not (os.getenv("GEMINI_PERSISTENT_INBOX") or "").strip() in {"1", "true", "yes", "on"}:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_story_folders = collect_story_folders(STORIES_DIR)
    pending = [folder for folder in all_story_folders if not story_folder_processing_done(folder)]
    top_level_dirs = sorted([item for item in STORIES_DIR.iterdir() if item.is_dir()])

    print(f"[INFO] user_data_dir: {USER_DATA_DIR}")
    print(f"[INFO] stories: {STORIES_DIR}")
    print(f"[INFO] Найдено верхнеуровневых папок: {len(top_level_dirs)}")
    print(f"[INFO] Найдено папок с историями: {len(all_story_folders)}")
    print(f"[INFO] Требуют обработки: {len(pending)}")

    bot_chain = load_gem_bot_chain()
    bot_idx = 0
    working_bots: set[str] = set()
    broken_bots: set[str] = set()
    print("[AUTH] Один профиль Chrome (user_data): залогинь все нужные Google-аккаунты.")
    for entry in bot_chain:
        print(f"[INFO] Аккаунт/бот: {bot_label(entry)}")
        print(f"       URL: {entry.url}")
        if entry.hub_url:
            print(f"       app: {entry.hub_url}")
    print(
        "[INFO] Дальше: Playwright → Chrome (persistent user_data). Первый запуск или занятый профиль — "
        "пауза до 1–2 мин без новых строк — это норма; если >5 мин — закрой все Chrome и повтори."
    )
    print(
        f"[HOTKEY] Консоль (фокус на окне терминала, не на Chrome): "
        f"'{WAIT_OVERRIDE_KEY.upper()}' — пропуск длинного ожидания; "
        f"'{FORCE_ROTATE_KEY.upper()}' — принудительно следующий бот из gemini_bots.json. "
        f"Другая клавиша: GEMINI_FORCE_ROTATE_KEY в окружении."
    )
    selected_start_idx = choose_start_bot_idx(bot_chain)
    if selected_start_idx is None:
        print("[START] Режим старта: авто (по текущему /u/N/ в браузере).")
    else:
        print(f"[START] Режим старта: вручную — {bot_label(bot_chain[selected_start_idx])}.")
    sys.stdout.flush()

    processed_count = 0
    stop_reason = ""
    stop_exit_code = EXIT_CODE_OK
    print("[INFO] Инициализация Playwright…")
    sys.stdout.flush()
    with sync_playwright() as playwright:
        print("[INFO] Запуск Chrome с профилем user_data (может занять время)…")
        sys.stdout.flush()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            channel="chrome",
            headless=False,
            slow_mo=SLOW_MO_MS,
            viewport=None,
            chromium_sandbox=True,
            args=append_chrome_proxy_args(["--disable-blink-features=AutomationControlled"]),
        )
        print("[INFO] Chrome поднят, открываю страницу Gem…")
        sys.stdout.flush()
        page = context.pages[0] if context.pages else context.new_page()
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://gemini.google.com")
        skip_sync = (os.getenv("GEMINI_SKIP_SESSION_BOT_SYNC") or "").strip().lower() in ("1", "true", "yes", "on")
        if selected_start_idx is not None:
            bot_idx = selected_start_idx
            skip_sync = True
        if not skip_sync and len(bot_chain) > 1:
            bot_idx = sync_bot_idx_from_browser_session(page, bot_chain)
        elif skip_sync and len(bot_chain) > 1 and selected_start_idx is None:
            print("[INFO] GEMINI_SKIP_SESSION_BOT_SYNC — стартую с первого бота в списке.")
            sys.stdout.flush()
        gemini_url = bot_chain[bot_idx].url
        hub_url = bot_chain[bot_idx].hub_url
        _first_l = bot_label(bot_chain[bot_idx])
        print(f"[INFO] Стартовый аккаунт/бот: {_first_l}")
        sys.stdout.flush()
        for _nav_try in range(1, 4):
            if ensure_on_bot_page(page, gemini_url, hub_url):
                break
            wait_with_status(45, f"Повтор открытия Gem ({_nav_try}/3), был таймаут навигации")
        startup_auth_attempt = 0
        startup_ok = False
        while not startup_ok:
            try:
                if ensure_logged_in(page, bot_entry=bot_chain[bot_idx]):
                    new_cur_url = page.url or ""
                    if "/gem/" in new_cur_url:
                        clean = new_cur_url.split("?")[0].rstrip("/")
                        if clean != bot_chain[bot_idx].url:
                            gemini_url = clean
                            bot_chain[bot_idx] = bot_chain[bot_idx]._replace(url=clean)
                    working_bots.add(bot_label(bot_chain[bot_idx]))
                    startup_ok = True
                    break
                startup_auth_attempt += 1
                startup_wait_sec = 45 if PERSISTENT_INBOX else 180
                wait_with_status(
                    startup_wait_sec,
                    f"UI Gemini не готов на старте (попытка {startup_auth_attempt}), жду и пробую снова",
                )
                if PERSISTENT_INBOX:
                    _safe_persistent_nav_recover(
                        page,
                        gemini_url,
                        reason="startup_ui_not_ready",
                        caller="main_startup_auth",
                    )
                else:
                    page_goto_gemini(page, gemini_url, context="старт после ожидания UI")
            except ForceRotateBot as fr:
                print(f"[ROTATE] {fr}")
                if bot_idx + 1 < len(bot_chain):
                    bot_idx += 1
                    gemini_url = bot_chain[bot_idx].url
                    hub_url = bot_chain[bot_idx].hub_url
                    _lbl = bot_label(bot_chain[bot_idx])
                    print(f"[ROTATE] Переключаюсь на аккаунт/бот: {_lbl}")
                    for _nav_try in range(1, 4):
                        if ensure_on_bot_page(page, gemini_url, hub_url):
                            break
                        wait_with_status(45, f"Повтор открытия Gem после R ({_nav_try}/3)")
                else:
                    print("[WARN] Это уже последний бот в цепочке — переключаться некуда. Продолжаю с текущим.")
            except RuntimeError as startup_err:
                print(f"[ERROR] Стартовый бот: {startup_err}")
                err_text = str(startup_err)
                if "Gem-бот удалён и пересоздать не удалось" in err_text:
                    print(
                        f"[BOT-HEALTH] Почта {bot_label(bot_chain[bot_idx])} доступна, "
                        "но автосоздание Gem не завершилось — не помечаю как нерабочую."
                    )
                    wait_with_status(10, "Повтор автосоздания Gem на том же аккаунте")
                    continue
                else:
                    broken_bots.add(bot_label(bot_chain[bot_idx]))
                print_bot_health_summary(working_bots, broken_bots)
                if bot_idx + 1 < len(bot_chain):
                    bot_idx += 1
                    gemini_url = bot_chain[bot_idx].url
                    hub_url = bot_chain[bot_idx].hub_url
                    _lbl = bot_label(bot_chain[bot_idx])
                    print(f"[ROTATE] Пробую следующий аккаунт/бот на старте: {_lbl}")
                    for _nav_try in range(1, 4):
                        if ensure_on_bot_page(page, gemini_url, hub_url):
                            break
                        wait_with_status(45, f"Повтор открытия Gem ({_nav_try}/3)")
                else:
                    print("[FATAL] Все боты в цепочке недоступны (удалены или /u/N/ не совпадает). Проверь gemini_bots.json.")
                    raise
        for _nav_try in range(1, 4):
            if ensure_on_bot_page(page, gemini_url, hub_url):
                break
            wait_with_status(45, f"Повтор открытия Gem после входа ({_nav_try}/3)")
        ensure_runtime_thinking_mode(page, caller="main_startup_after_login", reason="startup_after_login")

        should_stop = False
        persistent_stories_done = 0
        session_started_mono = time.monotonic()
        serial_idx = 0
        inbox_stop_reason = ""

        def _refresh_pending_folders() -> list[Path]:
            all_folders = collect_story_folders(STORIES_DIR)
            return [
                folder
                for folder in all_folders
                if pick_story_source_file(folder) is not None and not story_folder_processing_done(folder)
            ]

        if PERSISTENT_INBOX and BROWSER_SESSION_ID:
            print(
                f"[BROWSER] event=opened account={persistent_log_account_index(0)} "
                f"browser_session_id={BROWSER_SESSION_ID}"
            )

        while True:
            folder = None
            if PERSISTENT_INBOX:
                if PERSISTENT_STOP_FILE and Path(PERSISTENT_STOP_FILE).is_file():
                    inbox_stop_reason = "orchestrator_stop"
                    break
                pending = _refresh_pending_folders()
                if not pending:
                    if (
                        persistent_stories_done > 0
                        and PERSISTENT_MAX_STORIES > 0
                        and persistent_stories_done >= PERSISTENT_MAX_STORIES
                    ):
                        inbox_stop_reason = "max_stories"
                        break
                    if (
                        persistent_stories_done > 0
                        and (time.monotonic() - session_started_mono) > PERSISTENT_MAX_LIFETIME_MIN * 60
                    ):
                        inbox_stop_reason = "max_lifetime"
                        break
                    if PERSISTENT_NO_IDLE_EXIT:
                        last_wait_log = 0.0
                        got_new = False
                        while True:
                            if PERSISTENT_STOP_FILE and Path(PERSISTENT_STOP_FILE).is_file():
                                inbox_stop_reason = "orchestrator_stop"
                                should_stop = True
                                break
                            pending = _refresh_pending_folders()
                            if pending:
                                got_new = True
                                break
                            now = time.monotonic()
                            if now - last_wait_log >= 30.0:
                                print("[PERSISTENT] inbox_empty_waiting")
                                last_wait_log = now
                            time.sleep(0.5)
                        if should_stop:
                            break
                        if not got_new:
                            continue
                    else:
                        inbox_stop_reason = "idle_timeout"
                        break
                if pending:
                    folder = pending[0]
            else:
                if serial_idx >= len(pending):
                    break
                folder = pending[serial_idx]
                serial_idx += 1

            if folder is None:
                continue

            if PERSISTENT_INBOX and persistent_stories_done > 0 and BROWSER_SESSION_ID:
                print(
                    f"[BROWSER] event=reused account={persistent_log_account_index(0)} "
                    f"browser_session_id={BROWSER_SESSION_ID} story={folder.name}"
                )

            transient_attempt = 0
            limit_attempt = 0
            while True:
                try:
                    gemini_url = bot_chain[bot_idx].url
                    hub_url = bot_chain[bot_idx].hub_url
                    _bl = bot_chain[bot_idx].email or gemini_url
                    working_bots.add(bot_label(bot_chain[bot_idx]))
                    relative_folder = str(folder.relative_to(STORIES_DIR))
                    print(
                        f"[STATUS] Обрабатываю: {relative_folder} "
                        f"[аккаунт/бот: {_bl}]"
                    )
                    wait_for_generation_idle(page, timeout_ms=WAIT_TIMEOUT_MS)
                    if process_story_folder(page, folder, gemini_url, hub_url):
                        processed_count += 1
                        persistent_stories_done += 1
                        try:
                            relative_folder = folder.relative_to(STORIES_DIR)
                            if len(relative_folder.parts) >= 2:
                                genre_dir = STORIES_DIR / relative_folder.parts[0]
                                build_single_genre_report(genre_dir)
                        except Exception:
                            pass
                    limit_attempt = 0
                    break
                except Exception as error:
                    error_text = str(error)
                    error_text_lower = error_text.lower()
                    print(f"[ERROR] {folder.name}: {error_text}")
                    force_rotate = isinstance(error, ForceRotateBot)
                    limit_msg = "Лимит Gem-бота" in error_text
                    limit_like = limit_msg and ROTATE_ON_LIMIT

                    if force_rotate and bot_idx + 1 >= len(bot_chain):
                        print(
                            "[WARN] Принудительная смена (клавиша в консоли): это последний бот в "
                            "gemini_bots.json — некуда переключаться. Повторяю на текущем аккаунте."
                        )
                        continue

                    if (force_rotate or limit_like) and bot_idx + 1 < len(bot_chain):
                        if force_rotate:
                            _cur = bot_chain[bot_idx].email or bot_chain[bot_idx].url
                            _nxt = bot_chain[bot_idx + 1].email or bot_chain[bot_idx + 1].url
                            print(
                                f"[ROTATE] Принудительно (клавиша {FORCE_ROTATE_KEY.upper()} в консоли): "
                                f"{bot_idx + 1}/{len(bot_chain)} ({_cur}) → следующий: {_nxt}"
                            )
                        else:
                            _cur = bot_label(bot_chain[bot_idx])
                            _nxt = bot_label(bot_chain[bot_idx + 1])
                            print(
                                f"[ROTATE] Лимит на аккаунте/боте {_cur} — следующий аккаунт/бот: {_nxt}"
                            )
                        _reason = "hotkey" if force_rotate else "limit"
                        print(
                            f"[ROTATE-TRACE] reason={_reason}; from_idx={bot_idx + 1}; "
                            f"to_idx={bot_idx + 2}; page_url={page.url}"
                        )
                        mark_folder_need_new_bot_rules(folder)
                        bot_idx += 1
                        limit_attempt = 0
                        transient_attempt = 0
                        wait_with_status(3, "Смена Gem-бота")
                        _nu = bot_chain[bot_idx].url
                        _hu = bot_chain[bot_idx].hub_url
                        _ne = bot_label(bot_chain[bot_idx])
                        print(f"[ROTATE] Открываю следующий аккаунт/бот ({_ne}): {_nu}")
                        sys.stdout.flush()
                        opened = open_bot_with_retries(
                            page,
                            _nu,
                            _hu,
                            reason="Не удалось открыть следующий аккаунт/бот после лимита",
                            bot_entry=bot_chain[bot_idx],
                        )
                        if not opened:
                            broken_bots.add(bot_label(bot_chain[bot_idx]))
                            print_bot_health_summary(working_bots, broken_bots)
                            if bot_idx + 1 < len(bot_chain):
                                _bad = bot_label(bot_chain[bot_idx])
                                bot_idx += 1
                                _next = bot_label(bot_chain[bot_idx])
                                print(f"[ROTATE] Бот недоступен ({_bad}) — пробую следующий: {_next}")
                                continue
                            print("[FATAL] После лимита не удалось открыть ни одного следующего аккаунта/бота.")
                            should_stop = True
                            stop_reason = "Не удалось открыть следующий аккаунт/бот после лимита"
                            stop_exit_code = EXIT_CODE_ERROR
                            break
                        ensure_runtime_thinking_mode(page, caller="main_after_limit_rotation", reason="limit_rotation")
                        continue

                    if limit_msg:
                        if limit_attempt < len(LIMIT_RETRY_BACKOFF_SECONDS):
                            wait_seconds = LIMIT_RETRY_BACKOFF_SECONDS[limit_attempt]
                            limit_attempt += 1
                            wait_with_status(
                                wait_seconds,
                                f"Возможный лимит Gem-бота (может быть ложный). "
                                f"Повторная попытка {limit_attempt}/{len(LIMIT_RETRY_BACKOFF_SECONDS)}"
                            )
                        else:
                            print(
                                f"[LIMIT] Подтверждённый лимит: {limit_attempt} попыток не удались. "
                                f"Ухожу на длинную паузу {format_duration(LONG_PAUSE_SECONDS)}."
                            )
                            wait_with_status(LONG_PAUSE_SECONDS, "Подтверждённый лимит Gem-бота после многократных попыток")
                            limit_attempt = 0
                        try:
                            if not recover_session_state(
                                page,
                                bot_chain[bot_idx].url,
                                hub_url=bot_chain[bot_idx].hub_url,
                                resume_same_chat=story_has_partial_checkpoint(folder),
                                bot_entry=bot_chain[bot_idx],
                            ):
                                wait_with_status(180, "UI Gemini не восстановился после лимита, жду и пробую снова")
                        except RuntimeError:
                            pass
                        transient_attempt = 0
                        continue
                    target_closed = (
                        "target page, context or browser has been closed" in error_text_lower
                        or "browser has been closed" in error_text_lower
                        or "targetclosederror" in error_text_lower
                    )
                    if PERSISTENT_INBOX and target_closed:
                        print(
                            "[BROWSER_CONTEXT_CLOSED] persistent_safe=true "
                            "action=restart_browser reason=target_closed"
                        )
                        stop_reason = (
                            "browser_context_closed: Target page, context or browser has been closed"
                        )
                        stop_exit_code = EXIT_CODE_ERROR
                        should_stop = True
                        break

                    is_transient = (
                        "не удалось вставить файл" in error_text_lower
                        or "не удалось отправить запрос" in error_text_lower
                        or "ответ не завершился" in error_text_lower
                        or "временный сбой копирования ответа" in error_text_lower
                        or "временный сбой чтения ответа" in error_text_lower
                        or "locator.click: timeout" in error_text_lower
                        or target_closed
                        # Лимит/задержка: DOM не обновился — не фатальный стоп, повтор и при необходимости ротация
                        or "ответ в чате не изменился" in error_text_lower
                        or "пустой ответ модели" in error_text_lower
                        or "не дало отправить сообщение" in error_text_lower
                        or "кнопка не активна" in error_text_lower
                    )
                    if is_transient:
                        if transient_attempt < len(TRANSIENT_RETRY_BACKOFF_SECONDS):
                            wait_seconds = TRANSIENT_RETRY_BACKOFF_SECONDS[transient_attempt]
                            transient_attempt += 1
                            wait_with_status(
                                wait_seconds,
                                f"Временная ошибка. Попытка {transient_attempt}/{len(TRANSIENT_RETRY_BACKOFF_SECONDS)}"
                            )
                        else:
                            if ROTATE_AFTER_TRANSIENT_EXHAUSTED and bot_idx + 1 < len(bot_chain):
                                _cur = bot_label(bot_chain[bot_idx])
                                _nxt = bot_label(bot_chain[bot_idx + 1])
                                print(
                                    f"[ROTATE] Серия сбоев на аккаунте/боте {_cur} — следующий: {_nxt}"
                                )
                                print(
                                    f"[ROTATE-TRACE] reason=transient_exhausted; from_idx={bot_idx + 1}; "
                                    f"to_idx={bot_idx + 2}; page_url={page.url}"
                                )
                                mark_folder_need_new_bot_rules(folder)
                                bot_idx += 1
                                transient_attempt = 0
                                limit_attempt = 0
                                wait_with_status(3, "Смена Gem-бота")
                                _nu = bot_chain[bot_idx].url
                                _hu = bot_chain[bot_idx].hub_url
                                _ne = bot_label(bot_chain[bot_idx])
                                print(f"[ROTATE] Открываю следующий аккаунт/бот ({_ne}): {_nu}")
                                sys.stdout.flush()
                                opened = open_bot_with_retries(
                                    page,
                                    _nu,
                                    _hu,
                                    reason="Не удалось открыть следующий аккаунт/бот после серии сбоев",
                                    bot_entry=bot_chain[bot_idx],
                                )
                                if not opened:
                                    broken_bots.add(bot_label(bot_chain[bot_idx]))
                                    print_bot_health_summary(working_bots, broken_bots)
                                    if bot_idx + 1 < len(bot_chain):
                                        _bad = bot_label(bot_chain[bot_idx])
                                        bot_idx += 1
                                        _next = bot_label(bot_chain[bot_idx])
                                        print(f"[ROTATE] Бот недоступен ({_bad}) — пробую следующий: {_next}")
                                        continue
                                    print("[FATAL] После серии сбоев не удалось открыть ни одного следующего аккаунта/бота.")
                                    should_stop = True
                                    stop_reason = "Не удалось открыть следующий аккаунт/бот после серии сбоев"
                                    stop_exit_code = EXIT_CODE_ERROR
                                    break
                                ensure_runtime_thinking_mode(
                                    page,
                                    caller="main_after_transient_rotation",
                                    reason="transient_rotation",
                                )
                                continue
                            wait_with_status(
                                LONG_PAUSE_SECONDS,
                                "Слишком много временных сбоев подряд, беру длинную паузу"
                            )
                            transient_attempt = 0
                        try:
                            if not recover_session_state(
                                page,
                                bot_chain[bot_idx].url,
                                hub_url=bot_chain[bot_idx].hub_url,
                                resume_same_chat=story_has_partial_checkpoint(folder),
                                bot_entry=bot_chain[bot_idx],
                            ):
                                wait_with_status(LONG_PAUSE_SECONDS, "UI Gemini не восстановился после временной ошибки")
                        except RuntimeError:
                            pass
                        continue
                    quality_story_retry = (
                        "quality_retry_needed" in error_text_lower
                        or "failed quality validation" in error_text_lower
                    )
                    if quality_story_retry:
                        print(
                            "[SAFE_QUALITY_STORY_RETRY] keeping_browser_open=true "
                            "action=new_gemini_chat_in_same_bot"
                        )
                        try:
                            recover_session_state(
                                page,
                                bot_chain[bot_idx].url,
                                hub_url=bot_chain[bot_idx].hub_url,
                                resume_same_chat=False,
                                bot_entry=bot_chain[bot_idx],
                                force_goto=True,
                            )
                            ensure_runtime_thinking_mode(
                                page,
                                caller="main_quality_story_retry",
                                reason="quality_story_retry",
                            )
                        except Exception as recover_err:
                            print(f"[SAFE_QUALITY_STORY_RETRY] recover_failed: {recover_err}")
                        transient_attempt = 0
                        limit_attempt = 0
                        continue
                    print(
                        "[ERROR] Останавливаю обработку: пока текущий файл не завершён и не сохранён, следующий не запускается."
                    )
                    stop_reason = error_text
                    stop_exit_code = EXIT_CODE_ERROR
                    should_stop = True
                    break
            if should_stop:
                break

        if inbox_stop_reason:
            stop_reason = inbox_stop_reason
        if PERSISTENT_INBOX and BROWSER_SESSION_ID:
            close_reason = stop_reason or inbox_stop_reason or "done"
            if PERSISTENT_INBOX and (
                "quality_retry_needed" in str(close_reason).lower()
                or "failed quality validation" in str(close_reason).lower()
            ):
                close_reason = "quality_story_retry"
            print(
                f"[BROWSER] event=closed account={persistent_log_account_index(0)} "
                f"browser_session_id={BROWSER_SESSION_ID} reason={close_reason}"
            )

        generate_report(STORIES_DIR, processed_count, all_story_folders)
        skip_close = (
            "Target page, context or browser has been closed" in stop_reason
            or "browser_context_closed" in str(stop_reason).lower()
            or (
                PERSISTENT_INBOX
                and (
                    "quality_retry_needed" in str(stop_reason).lower()
                    or "failed quality validation" in str(stop_reason).lower()
                )
            )
        )
        if not skip_close:
            try:
                context.close()
            except Exception:
                pass

    if os.getenv("HOLD_OPEN", "0") == "1" and not _gemini_noninteractive():
        input("Нажми Enter, чтобы закрыть программу...")
    return stop_exit_code


if __name__ == "__main__":
    try:
        setup_dual_logging()
        sys.exit(main())
    except Exception as unhandled_error:
        print(f"[FATAL] {unhandled_error}")
        sys.exit(EXIT_CODE_ERROR)
