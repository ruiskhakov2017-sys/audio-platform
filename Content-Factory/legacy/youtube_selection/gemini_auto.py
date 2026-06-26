# -*- coding: utf-8 -*-
import base64
import json
import mimetypes
import msvcrt
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Locator, Page, TimeoutError, sync_playwright

_LEGACY_ROOT = Path(__file__).resolve().parents[1]
if str(_LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEGACY_ROOT))
from gemini_browser_proxy import append_chrome_proxy_args


DEFAULT_GEMINI_URL = "https://gemini.google.com/u/2/gem/8afc56845f94"
GEMINI_URL_PATTERN = re.compile(
    r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$"
)
PROJECT_DIR = Path(__file__).resolve().parent
CONTENT_FACTORY_ROOT = PROJECT_DIR.parents[1]
if str(CONTENT_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTENT_FACTORY_ROOT))

from orchestrator.youtube_full_auto.gemini_attach_state import (  # noqa: E402
    dismiss_overlays,
    recover_ui_for_attach,
)
from orchestrator.gemini_model_resolver import (  # noqa: E402
    ModelChoice,
    expected_model_labels,
    is_mode_selector_placeholder_label,
    model_fallback_order_for_stage,
    normalize_model_tier,
    parse_model_fallback_order_env,
    resolve_gemini_model_alias,
    selection_accepts_model_label,
    ui_label_matches_gemini_choice,
)
from orchestrator.youtube_full_auto.gemini_limit_policy import (  # noqa: E402
    LIMIT_ACCOUNT_ALL_MODELS,
    LIMIT_MODEL_CURRENT,
    LIMIT_PREFIX,
    classify_gemini_limit,
    limit_error_message,
)


def _gemini_env_path(env_name: str, default: Path) -> Path:
    """Optional path override via env (для orchestrator bridge). Без env → прежний дефолт рядом со скриптом."""
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return default.resolve()
    return Path(raw).expanduser().resolve()


STORIES_DIR = _gemini_env_path("GEMINI_STORIES_DIR", PROJECT_DIR / "stories")
TRASH_DIR = _gemini_env_path("GEMINI_TRASH_DIR", PROJECT_DIR / "trash")
USER_DATA_DIR = _gemini_env_path("GEMINI_USER_DATA_DIR", PROJECT_DIR / "user_data")
LOG_FILE_PATH = _gemini_env_path("GEMINI_LOG_FILE", PROJECT_DIR / "run.log")
PARALLEL_STATE_DIR = _gemini_env_path("GEMINI_PARALLEL_STATE_DIR", PROJECT_DIR / "parallel_state")
INFO_FILE_NAME = "info.txt"
REPORT_FILE_NAME = "result_report.txt"
GENRE_REPORT_FILE_NAME = "genre_report.txt"
OUTPUT_FILE_NAME = "вывод.txt"
WAIT_TIMEOUT_MS = 180_000
SLOW_MO_MS = 0
PAUSE_MIN_SECONDS = 0
PAUSE_MAX_SECONDS = 0
REPORT_FILE_PATTERN = re.compile(r"^result_report(-\d+)?\.txt$", re.IGNORECASE)
YOUTUBE_STATUS_LINE_PATTERN = re.compile(
    r"^\s*\**\s*подходит\s+для\s+youtube[\s:]+\**\s*(да|нет)\b",
    re.IGNORECASE,
)
LIMIT_HINTS = [
    "лимит",
    "превыш",
    "квот",
    "quota",
    "rate limit",
    "try again later",
    "слишком много запросов",
    "temporarily unavailable",
]
EXIT_CODE_OK = 0
EXIT_CODE_FILE_LIMIT = 42
EXIT_CODE_ERROR = 1
COPY_RETRIES = 3
IDLE_STABLE_ROUNDS = 4
HEARTBEAT_SECONDS = 30
TRANSIENT_RETRY_BACKOFF_SECONDS = [60, 60, 180, 300, 1200, 1800]
LONG_PAUSE_SECONDS = 10_800
GEMINI_STAGE_KEY = (os.getenv("GEMINI_STAGE_KEY") or "selection").strip().lower()
MODEL_FALLBACK_ON_LIMIT = (os.getenv("GEMINI_MODEL_FALLBACK_ON_LIMIT", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
LIMIT_RETRY_PAUSE_SECONDS = int(
    (os.getenv("GEMINI_LIMIT_RETRY_PAUSE_SECONDS") or ("60" if MODEL_FALLBACK_ON_LIMIT else "300")).strip() or "60"
)
LIMIT_MAX_CONSECUTIVE_FAILURES = 5
GEMINI_MODEL_FALLBACK_ORDER = parse_model_fallback_order_env(
    os.getenv("GEMINI_MODEL_FALLBACK_ORDER"),
    stage=GEMINI_STAGE_KEY,
)
_SESSION_CURRENT_MODEL_TIER = ""
_SESSION_ACTUAL_MODEL_LABEL = ""
_SESSION_MODEL_VERIFIED = False
_SESSION_MODEL_FALLBACK_USED = False
_SESSION_PREVIOUS_MODEL_LABEL = ""
_SESSION_MODEL_LIMIT_COUNT = 0
_SESSION_MODEL_FALLBACK_ATTEMPTS = 0
_SESSION_MODEL_SELECTION_ATTEMPTS = 0
_SESSION_STORY_MODEL_LOCKED = False
_SESSION_LIMIT_REASON = ""
MAX_MODEL_SELECTION_ATTEMPTS = int(os.getenv("GEMINI_MAX_MODEL_SELECTION_ATTEMPTS", "1").strip() or "1")
GEMINI_INITIAL_MODEL_TIER = normalize_model_tier(
    os.getenv("GEMINI_INITIAL_MODEL_TIER") or (GEMINI_MODEL_FALLBACK_ORDER[0] if GEMINI_MODEL_FALLBACK_ORDER else "fast")
)
WAIT_OVERRIDE_KEY = "w"
RESPONSE_MARKER_PREFIX = "REQUEST_MARKER"
PARALLEL_WORKERS = int(os.getenv("PARALLEL_WORKERS", "1").strip() or "1")
_default_fpa = "0" if PARALLEL_WORKERS > 1 else "100"
_fpa = (os.getenv("FILES_PER_ACCOUNT", _default_fpa) or _default_fpa).strip()
_default_fpd = "1" if PARALLEL_WORKERS <= 1 else "10"
FILES_PER_DIALOG = int((os.getenv("FILES_PER_DIALOG", _default_fpd) or _default_fpd).strip() or "10")
FILES_PER_ACCOUNT = int(_fpa) if _fpa else int(_default_fpa)
ACCOUNTS_FILE = _gemini_env_path("GEMINI_ACCOUNTS_FILE", PROJECT_DIR / "accounts.txt")
SEPARATE_PROFILES = os.getenv("SEPARATE_PROFILES", "0").strip() == "1"
WORKER_ID = os.getenv("WORKER_ID", "").strip()
WORKER_INDEX = int(os.getenv("WORKER_INDEX", "0").strip() or "0")
CLAIM_FILE_NAME = ".claim"
CLAIM_TTL_SECONDS = int(os.getenv("CLAIM_TTL_SECONDS", str(6 * 60 * 60)).strip() or str(6 * 60 * 60))
CLEAN_CLAIMS = os.getenv("CLEAN_CLAIMS", "0").strip() == "1"
PERSISTENT_INBOX = os.getenv("GEMINI_PERSISTENT_INBOX", "0").strip() == "1"
PERSISTENT_IDLE_SEC = float((os.getenv("GEMINI_PERSISTENT_IDLE_SEC", "30") or "30").strip() or "30")
PERSISTENT_NO_IDLE_EXIT = (os.getenv("GEMINI_PERSISTENT_NO_IDLE_EXIT", "0").strip() == "1") or (
    float(os.getenv("GEMINI_PERSISTENT_IDLE_SEC", "30") or "30") <= 0
)
PERSISTENT_MAX_STORIES = int((os.getenv("GEMINI_PERSISTENT_MAX_STORIES", "0") or "0").strip() or "0")
PERSISTENT_MAX_LIFETIME_MIN = float((os.getenv("GEMINI_PERSISTENT_MAX_LIFETIME_MIN", "60") or "60").strip() or "60")
PERSISTENT_STOP_FILE = (os.getenv("GEMINI_PERSISTENT_STOP_FILE") or "").strip()
BROWSER_SESSION_ID = (os.getenv("GEMINI_BROWSER_SESSION_ID") or "").strip()
STAGED_MARKER_NAME = "ORCHESTRATOR_STAGED.json"
PROCESSED_MARKER_NAME = "ORCHESTRATOR_PROCESSED.json"
GEMINI_LOG_ACCOUNT_INDEX_RAW = (os.getenv("GEMINI_LOG_ACCOUNT_INDEX") or "").strip()


def persistent_log_account_index(runtime_idx: int) -> int:
    if GEMINI_LOG_ACCOUNT_INDEX_RAW.lstrip("-").isdigit():
        return int(GEMINI_LOG_ACCOUNT_INDEX_RAW)
    return runtime_idx


def _utc_marker_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _write_orchestrator_processed_marker(folder: Path, *, info_file: Path, body: str) -> None:
    staged = _find_orchestrator_staged_marker(folder)
    if not staged:
        print(f"[WARN] {folder.name}: {STAGED_MARKER_NAME} not found — skip {PROCESSED_MARKER_NAME}")
        return
    from orchestrator.youtube_full_auto.gemini_policy_refusal import detect_gemini_policy_refusal

    refused, refusal_excerpt = detect_gemini_policy_refusal(body)
    if refused:
        verdict = "policy_refusal"
        decision = "POLICY_REFUSAL"
    else:
        verdict = parse_youtube_suitable_status(body) or "unknown"
        decision = "YES" if verdict == "да" else ("NO" if verdict == "нет" else "UNKNOWN")
    result_report = folder / REPORT_FILE_NAME
    if not result_report.is_file():
        reports = sorted(folder.glob("result_report*.txt"), key=lambda p: p.name.lower())
        result_report = reports[0] if reports else None
    processed_at = _utc_marker_now()
    payload = {
        "schema_version": 1,
        "marker_format": "orchestrator_processed_v1",
        "run_id": str(staged.get("run_id") or ""),
        "story_id": str(staged.get("story_id") or ""),
        "story_slug": str(staged.get("story_slug") or ""),
        "title": str(staged.get("title") or staged.get("story_slug") or folder.name),
        "text_hash_sha256": str(staged.get("text_hash_sha256") or ""),
        "stage": "youtube_selection",
        "browser_session_id": str(staged.get("browser_session_id") or BROWSER_SESSION_ID),
        "account": int(staged.get("account", staged.get("account_index", persistent_log_account_index(0)))),
        "account_index": int(staged.get("account_index", staged.get("account", persistent_log_account_index(0)))),
        "worker": str(staged.get("worker") or staged.get("worker_id") or WORKER_ID or "w1"),
        "worker_id": str(staged.get("worker_id") or staged.get("worker") or WORKER_ID or "w1"),
        "mini_youtube_run_id": str(staged.get("mini_youtube_run_id") or ""),
        "staged_at": str(staged.get("staged_at") or ""),
        "processed_at": processed_at,
        "legacy_done": True,
        "decision": decision,
        "info_path": info_file.name,
        "info_absolute_path": str(info_file.resolve()),
        "result_report_path": str(result_report.resolve()) if result_report is not None else "",
        "verdict": verdict if verdict else "unknown",
        "policy_refusal": bool(refused),
        "response_excerpt": refusal_excerpt if refused else "",
        "expected_model": str(staged.get("expected_model") or _SESSION_CURRENT_MODEL_TIER or ""),
        "actual_model": str(_SESSION_ACTUAL_MODEL_LABEL or staged.get("actual_model") or ""),
        "model_verified": bool(_SESSION_MODEL_VERIFIED),
        "model_fallback_used": bool(_SESSION_MODEL_FALLBACK_USED),
        "previous_model": str(_SESSION_PREVIOUS_MODEL_LABEL or ""),
        "limit_reason": str(staged.get("limit_reason") or _SESSION_LIMIT_REASON or ""),
    }
    out_path = folder / PROCESSED_MARKER_NAME
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PROCESSED] {folder.name}: wrote {PROCESSED_MARKER_NAME} decision={decision}")
INITIAL_GEMINI_LOAD_TIMEOUT_SEC = int(os.getenv("GEMINI_INITIAL_LOAD_TIMEOUT_SEC", "180").strip() or "180")
MAX_GEMINI_PAGE_RELOADS = int(os.getenv("GEMINI_MAX_PAGE_RELOADS", "2").strip() or "2")
GEMINI_RELOAD_PAUSE_SEC = int(os.getenv("GEMINI_RELOAD_PAUSE_SEC", "45").strip() or "45")
POST_MODEL_STABILIZE_SECONDS = float(os.getenv("GEMINI_POST_MODEL_STABILIZE_SEC", "4").strip() or "4")
MAX_ATTACH_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTACH_ATTEMPTS", "3").strip() or "3")
ATTACH_RETRY_PAUSE_SEC = float(os.getenv("GEMINI_ATTACH_RETRY_PAUSE_SEC", "3").strip() or "3")

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

ATTACHED_FILE_SELECTORS = [
    'button[aria-label*=".txt"]',
    '[data-testid*="attachment"]',
    '[data-test-id*="attachment"]',
    '[data-test-id*="file-chip"]',
    '[data-testid*="file-chip"]',
    '[class*="attachment"]',
    '[class*="file-chip"]',
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

MODEL_MENU_SIDEBAR_FORBIDDEN_TOKENS = (
    "новый чат",
    "new chat",
    "библиотека",
    "library",
    "gem-бот",
    "gem bot",
    "блокнот",
    "notebook",
    "поиск по чатам",
    "search chats",
    "создать",
    "create",
)
RESPONSE_BLOCK_SELECTORS = [
    '[data-test-id="message-content"]',
    '[data-test-id*="response"]',
    "message-content",
    "model-response",
    "div.markdown",
    "article",
]


@dataclass
class AccountConfig:
    """Данные одного Google-аккаунта для работы с Gemini."""

    email: str
    password: str
    gem_url: str
    user_data_dir: Path


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
    log_path = LOG_FILE_PATH
    if PARALLEL_WORKERS > 1 or WORKER_ID:
        PARALLEL_STATE_DIR.mkdir(parents=True, exist_ok=True)
        suffix = WORKER_ID or f"w{WORKER_INDEX + 1}"
        log_path = PARALLEL_STATE_DIR / f"run-{suffix}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_stream = open(log_path, mode="a", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = TeeStream(sys.__stdout__, log_stream)
    sys.stderr = TeeStream(sys.__stderr__, log_stream)
    print("")
    print("=" * 80)
    print(f"[SESSION] Старт: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[SESSION] Лог-файл: {log_path}")


def human_pause(step_name: str) -> None:
    seconds = random.randint(PAUSE_MIN_SECONDS, PAUSE_MAX_SECONDS)
    print(f"[PAUSE] {step_name}: жду {seconds} сек.")
    time.sleep(seconds)


def prompt_user(message: str, default: str = "") -> str:
    """Безопасный ввод: в неинтерактивном режиме не роняет процесс на EOF."""
    try:
        return input(message)
    except EOFError:
        print("[WARN] stdin недоступен (EOF). Продолжаю без ручного ввода.")
        return default


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
        if page.is_closed():
            raise RuntimeError("Страница браузера закрыта (Target closed).")
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
    pressed = False
    while msvcrt.kbhit():
        try:
            key = msvcrt.getwch()
        except Exception:
            return False
        if key and key.lower() == WAIT_OVERRIDE_KEY:
            pressed = True
    return pressed


def is_login_screen_visible(page: Page) -> bool:
    current_url = (page.url or "").lower()
    if "accounts.google.com" not in current_url:
        return False
    if "accountchooser" in current_url:
        return False
    login_url_hints = [
        "/signin",
        "/identifier",
        "/challenge",
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


def _debug_dir() -> Path | None:
    raw = (os.getenv("GEMINI_DEBUG_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw)


def save_debug_artifacts(page: Page, tag: str) -> None:
    root = _debug_dir()
    if root is None:
        return
    root.mkdir(parents=True, exist_ok=True)
    safe_tag = re.sub(r"[^A-Za-z0-9_-]+", "_", tag).strip("_") or "debug"
    try:
        page.screenshot(path=str(root / f"debug_{safe_tag}.png"), full_page=True)
    except Exception as exc:
        print(f"[DEBUG] screenshot failed: {exc}")
    try:
        (root / f"debug_{safe_tag}.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        print(f"[DEBUG] html dump failed: {exc}")


def wait_for_prompt_input_soft(page: Page, timeout_ms: int) -> bool:
    try:
        wait_for_prompt_input(page, timeout_ms=timeout_ms)
        return True
    except TimeoutError:
        return False


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
            return True
        if is_login_screen_visible(page):
            print(f"[WAIT_LOAD] elapsed={elapsed}/{INITIAL_GEMINI_LOAD_TIMEOUT_SEC} reason=login_screen_visible")
            return False
        if _page_has_server_error(page):
            if reloads < MAX_GEMINI_PAGE_RELOADS:
                reloads += 1
                print(
                    f"[RELOAD] reason=server_error_page attempt={reloads}/{MAX_GEMINI_PAGE_RELOADS} "
                    f"pause={GEMINI_RELOAD_PAUSE_SEC}s"
                )
                time.sleep(GEMINI_RELOAD_PAUSE_SEC)
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
                continue
            return False
        print(
            f"[NO_RELOAD] page still loading, waiting elapsed={elapsed}/{INITIAL_GEMINI_LOAD_TIMEOUT_SEC} "
            f"reason={reason} url={((page.url or '')[:80])}"
        )
        settle = 5 if "gemini.google.com" in (page.url or "").lower() else 3
        time.sleep(settle)
    if reloads < MAX_GEMINI_PAGE_RELOADS:
        reloads += 1
        print(
            f"[RELOAD] reason=hard_timeout_after_{INITIAL_GEMINI_LOAD_TIMEOUT_SEC}s "
            f"attempt={reloads}/{MAX_GEMINI_PAGE_RELOADS} pause={GEMINI_RELOAD_PAUSE_SEC}s"
        )
        time.sleep(GEMINI_RELOAD_PAUSE_SEC)
        try:
            page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        return wait_for_prompt_input_soft(page, timeout_ms=30_000)
    print(f"[WARN] hard timeout initial load after {INITIAL_GEMINI_LOAD_TIMEOUT_SEC}s")
    return False


def ensure_logged_in(page: Page) -> bool:
    if wait_for_initial_gemini_load(page, reason="ensure_logged_in"):
        return True
    if is_login_screen_visible(page):
        prompt_user("[AUTH] Войди в аккаунт и нажми Enter...")
        return wait_for_initial_gemini_load(page, reason="post_manual_login")
    print("[WARN] Не удалось подтвердить готовность Gemini UI после initial load wait.")
    return False


def resolve_session_gemini_url() -> str:
    env_url = (os.getenv("GEMINI_URL") or "").strip()
    if env_url:
        if GEMINI_URL_PATTERN.fullmatch(env_url):
            print(f"[INFO] Использую GEMINI_URL из окружения: {env_url}")
            return env_url
        print(f"[WARN] Некорректный GEMINI_URL в окружении, использую значение по умолчанию: {env_url}")
        return DEFAULT_GEMINI_URL
    print(f"[INFO] Текущий бот по умолчанию: {DEFAULT_GEMINI_URL}")
    user_input_url = prompt_user(
        "[INPUT] Вставь ссылку Gem-бота или нажми Enter для значения по умолчанию: "
    ).strip()
    if user_input_url:
        if GEMINI_URL_PATTERN.fullmatch(user_input_url):
            return user_input_url
        print(f"[WARN] Некорректный формат ссылки, использую значение по умолчанию: {user_input_url}")
    return DEFAULT_GEMINI_URL


def _gem_id_from_url(url: str) -> str:
    from orchestrator.youtube_full_auto.gem_bot_registry import gem_id_from_url

    return gem_id_from_url(url)


def _gem_session_key(url: str) -> str | None:
    gem_id = _gem_id_from_url(url)
    if not gem_id:
        return None
    return f"gemini.google.com/gem/{gem_id.lower()}"


def _is_conversation_url(url: str) -> bool:
    from orchestrator.youtube_full_auto.gem_bot_registry import is_conversation_url

    return is_conversation_url(url)


def has_deleted_gem_bot_screen(page: Page) -> bool:
    from orchestrator.youtube_full_auto.gem_bot_identity import body_has_deleted_gem_screen

    try:
        body = page.locator("body").inner_text(timeout=3_000) or ""
    except Exception:
        return False
    return body_has_deleted_gem_screen(body)


def _log_gem_bot_identity(**fields: object) -> None:
    parts = ["[GEM_BOT]"]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        if " " in text:
            parts.append(f"{key}='{text.replace(chr(39), chr(34))}'")
        else:
            parts.append(f"{key}={text}")
    print(" ".join(parts))


def verify_expected_gem_bot(page: Page, expected_url: str) -> dict[str, object]:
    from orchestrator.youtube_full_auto.gem_bot_identity import verify_urls_from_strings

    try:
        body = page.locator("body").inner_text(timeout=4_000) or ""
    except Exception:
        body = ""
    opened = str(getattr(page, "url", "") or "")
    result = verify_urls_from_strings(
        expected_url=expected_url,
        opened_url=opened,
        final_url=opened,
        body_text=body,
    )
    _log_gem_bot_identity(
        expected_gem_bot_url=expected_url,
        opened_url=opened,
        final_url_after_redirect=opened,
        expected_gem_id=result.get("expected_gem_id"),
        actual_gem_id_from_url=result.get("actual_gem_id_from_url"),
        url_match=result.get("url_match"),
        bot_identity_verified=result.get("bot_identity_verified"),
        deleted_gem_screen_detected=result.get("deleted_gem_screen_detected"),
        conversation_url_used=result.get("conversation_url_used"),
        reason=result.get("reason") or "",
    )
    return result


def ensure_canonical_gem_bot_page(page: Page, expected_url: str, *, max_attempts: int = 2) -> dict[str, object]:
    """Navigate away from restored chats; fail-fast on deleted Gem bot screen."""
    last: dict[str, object] = {"ok": False, "reason": "gem_url_mismatch"}
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        cur = page.url or ""
        if _is_conversation_url(cur) or (_gem_id_from_url(cur) and not _bot_url_matches(page, expected_url)):
            print(f"[GEM_BOT] stale_or_conversation_url attempt={attempt} cur={cur[:120]}")
            try:
                page.goto("about:blank", wait_until="domcontentloaded", timeout=15_000)
            except Exception:
                pass
            time.sleep(0.5)
        try:
            page.goto(expected_url, wait_until="domcontentloaded", timeout=90_000)
        except Exception:
            pass
        time.sleep(max(2.0, POST_MODEL_STABILIZE_SECONDS))
        ensure_on_bot_page(page, expected_url, max_goto=1)
        last = verify_expected_gem_bot(page, expected_url)
        if last.get("deleted_gem_screen_detected"):
            raise RuntimeError("GEM_BOT_DELETED: deleted Gem bot screen detected")
        if last.get("conversation_url_used"):
            continue
        if last.get("ok"):
            return last
    reason = str(last.get("reason") or "gem_url_mismatch")
    raise RuntimeError(f"GEM_URL_MISMATCH: {reason}")


def _body_indicates_server_error(body: str) -> bool:
    text = (body or "").lower()
    if not text.strip():
        return False
    if "that's an error" in text:
        return True
    if "server error" in text and ("google" in text or "gemini" in text or "error" in text):
        return True
    if re.search(r"\b502\b.{0,40}\berror\b", text) or re.search(r"\b503\b.{0,40}\b(unavailable|error)\b", text):
        return True
    if "temporary error" in text and ("try again" in text or "повтор" in text):
        return True
    return False


def _page_has_server_error(page: Page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=3000) or ""
    except Exception:
        return False
    return _body_indicates_server_error(body)


def _bot_url_matches(page: Page, gemini_url: str) -> bool:
    want_id = _gem_id_from_url(gemini_url)
    cur_id = _gem_id_from_url(page.url or "")
    if want_id and cur_id:
        return want_id.lower() == cur_id.lower()
    want = _gem_session_key(gemini_url)
    if not want:
        return "gemini.google.com" in (page.url or "").lower()
    return _gem_session_key(page.url or "") == want


def _wait_for_bot_url_settle(page: Page, gemini_url: str, *, timeout_sec: int = 45) -> bool:
    """Wait for Gemini SPA routing to reach the target gem URL without navigating."""
    deadline = time.time() + max(5, int(timeout_sec))
    last_url = ""
    stable_rounds = 0
    while time.time() < deadline:
        if _bot_url_matches(page, gemini_url) and not _page_has_server_error(page):
            if wait_for_prompt_input_soft(page, timeout_ms=2000):
                return True
            stable_rounds += 1
            if stable_rounds >= 2:
                return True
        else:
            stable_rounds = 0
        cur = page.url or ""
        if cur == last_url:
            time.sleep(1.5)
        else:
            last_url = cur
            time.sleep(0.8)
    return _bot_url_matches(page, gemini_url)


def ensure_on_bot_page(page: Page, gemini_url: str, *, max_goto: int | None = None) -> None:
    want = _gem_session_key(gemini_url)
    nav_budget = max(1, int(max_goto if max_goto is not None else MAX_GEMINI_PAGE_RELOADS + 1))
    if _wait_for_bot_url_settle(page, gemini_url, timeout_sec=45):
        print(f"[NO_RELOAD] bot page settled url={((page.url or '')[:80])}")
        return
    for attempt in range(1, nav_budget + 1):
        cur = page.url or ""
        if want and _gem_session_key(cur) == want and not _page_has_server_error(page):
            if wait_for_prompt_input_soft(page, timeout_ms=5000):
                return
        print(
            f"[NAV] ensure_on_bot_page goto attempt={attempt}/{nav_budget} "
            f"want={want or gemini_url[:60]} cur={cur[:80]}"
        )
        try:
            page.goto(gemini_url, wait_until="domcontentloaded", timeout=90_000)
        except Exception:
            pass
        pause = max(8, min(GEMINI_RELOAD_PAUSE_SEC, 30))
        print(f"[WAIT_LOAD] post_navigation_settle pause={pause}s attempt={attempt}/{nav_budget}")
        time.sleep(pause)
        if _wait_for_bot_url_settle(page, gemini_url, timeout_sec=30):
            return
    cur = page.url or ""
    if want and _gem_session_key(cur) != want:
        print("[MANUAL] Не удалось открыть нужный Gem в нужном аккаунте.")
        print(f"[MANUAL] Нужно: {gemini_url}")
        print(f"[MANUAL] Сейчас: {cur}")
        prompt_user("[MANUAL] Открой правильную ссылку в браузере и нажми Enter...")


def recover_session_state(page: Page, gemini_url: str) -> bool:
    try:
        page.goto(gemini_url, wait_until="domcontentloaded")
    except Exception:
        pass
    if not ensure_logged_in(page):
        return False
    ensure_on_bot_page(page, gemini_url)
    prepare_clean_prompt(page)
    return True


def soft_reset_persistent_browser(page: Page, gem_url: str, *, reason: str, account_idx: int) -> bool:
    """New Gem dialog in the same Chrome session (no context.close)."""
    print(
        f"[BROWSER] event=soft_reset account={persistent_log_account_index(account_idx)} "
        f"browser_session_id={BROWSER_SESSION_ID} reason={reason}"
    )
    ok = recover_session_state(page, gem_url)
    if ok:
        reset_story_model_lock()
        ensure_stage_default_model(page)
    return ok


def pick_story_source_file(folder: Path) -> Path | None:
    candidates = list_story_source_files(folder)
    return candidates[0] if candidates else None


def list_story_source_files(folder: Path) -> list[Path]:
    def is_generated_txt(path: Path) -> bool:
        name = path.name.lower()
        if name == INFO_FILE_NAME.lower():
            return True
        if name == GENRE_REPORT_FILE_NAME.lower():
            return True
        if name == OUTPUT_FILE_NAME.lower():
            return True
        return REPORT_FILE_PATTERN.fullmatch(name) is not None

    return sorted([path for path in folder.glob("*.txt") if not is_generated_txt(path)])


def collect_story_folders(stories_dir: Path, *, quiet: bool = False) -> list[Path]:
    if not quiet:
        print("[STATUS] Сканирую папки рассказов...")
    all_dirs = [path for path in stories_dir.rglob("*") if path.is_dir()]
    with_source = [folder for folder in all_dirs if pick_story_source_file(folder) is not None]
    with_source_set = set(with_source)
    leaf_story_folders = [folder for folder in with_source if folder not in {child.parent for child in with_source_set}]
    result = sorted(leaf_story_folders, key=lambda path: str(path.relative_to(stories_dir)).lower())
    if not quiet:
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
    if not wait_for_prompt_input_soft(page, timeout_ms=WAIT_TIMEOUT_MS):
        return False
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


def _page_health_snapshot(page: Page) -> dict[str, object]:
    from orchestrator.youtube_full_auto.gemini_page_health import detect_gemini_page_health

    return detect_gemini_page_health(
        page,
        input_ready_fn=wait_for_prompt_input_soft,
        server_error_fn=_page_has_server_error,
    )


def _recover_page_if_broken(page: Page, gem_url: str) -> bool:
    health = _page_health_snapshot(page)
    if not health.get("broken"):
        return True
    reason = str(health.get("reason") or "page_broken")
    print(f"[PAGE_HEALTH] broken reason={reason} attempting_one_soft_reload")
    from orchestrator.youtube_full_auto.gemini_page_health import recover_gemini_page_once

    ok = recover_gemini_page_once(
        page,
        gem_url,
        settle_fn=lambda pg, url: _wait_for_bot_url_settle(pg, url, timeout_sec=30),
        pause_sec=min(15, GEMINI_RELOAD_PAUSE_SEC),
    )
    after = _page_health_snapshot(page)
    print(
        f"[PAGE_HEALTH] recover_ok={str(ok).lower()} after_reason={after.get('reason') or 'ok'} "
        f"body_chars={after.get('body_chars')}"
    )
    return bool(ok and not after.get("broken"))


def paste_file_into_prompt_with_retries(page: Page, source_file: Path, *, story_name: str = "") -> bool:
    from orchestrator.youtube_full_auto.gemini_attach_state import run_attach_state_machine

    label = story_name or source_file.stem
    result = run_attach_state_machine(
        page,
        source_file,
        story_name=label,
        max_attempts=MAX_ATTACH_ATTEMPTS,
        retry_pause_sec=ATTACH_RETRY_PAUSE_SEC,
        attachment_visible_fn=attachment_visible,
        paste_fn=paste_file_into_prompt,
        prepare_clean_fn=prepare_clean_prompt,
        page_health_fn=_page_health_snapshot,
        save_debug_fn=save_debug_artifacts,
        send_enabled_fn=is_send_button_enabled,
        recover_fn=_recover_page_for_attach,
    )
    state = str(result.get("state") or "")
    reason = str(result.get("reason") or "")
    attempts = int(result.get("attempts") or 0)
    ui_text = str(result.get("ui_text") or "")[:200]
    print(
        f"[ATTACH] story={label} state={state} ok={str(bool(result.get('ok'))).lower()} "
        f"attempts={attempts} reason={reason} expected={result.get('expected_filename')} "
        f"actual={result.get('actual_filename') or 'n/a'} ui={ui_text!r}"
    )
    if result.get("ok"):
        print(f"[ATTACH] story={label} status=ok attempt={attempts}")
        return True
    print(f"[ATTACH] story={label} status=failed attempt={attempts}/{MAX_ATTACH_ATTEMPTS}")
    return False


def prepare_clean_prompt(page: Page) -> None:
    if not wait_for_prompt_input_soft(page, timeout_ms=15_000):
        return
    try:
        prompt_input = wait_for_prompt_input(page, timeout_ms=8_000)
    except TimeoutError:
        return
    prompt_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.press("Escape")


def _recover_page_for_attach(page: Page) -> bool:
    gem_url = (os.getenv("GEMINI_EXPECTED_GEM_BOT_URL") or os.getenv("GEMINI_URL") or "").strip()
    if not gem_url:
        return recover_ui_for_attach(page)
    return _recover_page_if_broken(page, gem_url)


def has_limit_message(page: Page) -> bool:
    try:
        body_text = (page.locator("body").inner_text(timeout=3000) or "").lower()
    except Exception:
        return False
    if len(body_text.strip()) < 30:
        return False
    server_error_markers = ["that's an error", "server error", "err_connection", "dns_probe"]
    if any(marker in body_text for marker in server_error_markers):
        return False
    if _page_has_server_error(page):
        return False
    return any(hint in body_text for hint in LIMIT_HINTS)


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
    while time.time() < deadline:
        if consume_wait_override_key():
            print(f"[STATUS] Ожидание idle принудительно пропущено клавишей '{WAIT_OVERRIDE_KEY}'.")
            return
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


def click_send_button(page: Page, timeout_ms: int = 12_000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in SEND_BUTTON_SELECTORS:
            button = page.locator(selector).first
            if button.count() == 0 or not button.is_visible():
                continue
            try:
                if button.is_enabled():
                    button.click(timeout=1500)
                    return True
            except Exception:
                continue
        time.sleep(0.2)
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
    while time.time() < deadline:
        if consume_wait_override_key():
            print(f"[STATUS] Ожидание готовности ответа принудительно пропущено клавишей '{WAIT_OVERRIDE_KEY}'.")
            candidate = find_new_copy_button(page, previous_count)
            if candidate is not None:
                return candidate
            raise TimeoutError("Ожидание готовности ответа пропущено вручную, но кнопка копирования не найдена.")
        candidate = find_new_copy_button(page, previous_count)
        if candidate is not None and not is_generation_in_progress(page):
            stable_ready_rounds += 1
            if stable_ready_rounds >= IDLE_STABLE_ROUNDS:
                return candidate
        else:
            now = time.time()
            if now - last_status_print >= HEARTBEAT_SECONDS:
                print("[STATUS] Ожидаю, пока Gemini закончит ответ и кнопка копирования стабилизируется...")
                last_status_print = now
            stable_ready_rounds = 0
        time.sleep(0.35)
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


def _model_menu_button_is_sidebar(label: str) -> bool:
    low = str(label or "").lower()
    return any(token in low for token in MODEL_MENU_SIDEBAR_FORBIDDEN_TOKENS)


def _model_menu_search_root(page: Page) -> Locator:
    for sel in ('main', '[role="main"]', '[data-test-id="conversation"]', "body"):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return page.locator("body").first


def open_model_mode_menu(page: Page) -> bool:
    root = _model_menu_search_root(page)
    for sel in MODE_MENU_TRIGGER_SELECTORS:
        loc = root.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            if not loc.is_visible():
                continue
            label = (loc.get_attribute("aria-label") or "") + " " + (loc.inner_text() or "")
            if _model_menu_button_is_sidebar(label):
                print(f"[MODEL] rejected_sidebar_menu_trigger selector={sel} label={label[:80]!r}")
                continue
            loc.click(timeout=5_000)
            print(f"[MODEL] opened_mode_menu selector={sel} label={label[:80]!r}")
            return True
        except Exception:
            continue
    toggles = root.locator('button[aria-haspopup="listbox"], button[aria-haspopup="menu"]')
    for i in range(min(toggles.count(), 48)):
        btn = toggles.nth(i)
        try:
            if not btn.is_visible():
                continue
            label = (btn.get_attribute("aria-label") or "") + " " + (btn.inner_text() or "")
            if _model_menu_button_is_sidebar(label):
                continue
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
                print(f"[MODEL] opened_mode_menu toggle_label={label[:80]!r}")
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
            if target.is_visible():
                target.click(timeout=5_000)
                return True
        except Exception:
            continue
    for role in ("menuitem", "option", "menuitemradio"):
        try:
            loc = page.locator(f'[role="{role}"]').filter(has_text=name_rx).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=5_000)
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
            if target.is_visible():
                target.click(timeout=5_000)
                return True
        except Exception:
            continue
    for role in ("menuitem", "option", "menuitemradio"):
        try:
            loc = page.locator(f'[role="{role}"]').filter(has_text=_FLASH35_RX).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=5_000)
                return True
        except Exception:
            continue
    return False


def click_model_option_for_tier(page: Page, tier: str) -> str:
    normalized = normalize_model_tier(tier)
    choice = resolve_gemini_model_alias(normalized)
    if normalized == "thinking":
        if click_thinking_model_option(page):
            return "Thinking"
    clicked = click_resolved_model_option(page, choice)
    if clicked:
        return clicked
    return ""


def has_thinking_limit_message(page: Page) -> bool:
    try:
        body_text = (page.locator("body").inner_text(timeout=2_000) or "").lower()
    except Exception:
        return False
    from orchestrator.youtube_full_auto.gemini_limit_policy import THINKING_LIMIT_HINTS

    return any(h.lower() in body_text for h in THINKING_LIMIT_HINTS)


def reset_story_model_lock() -> None:
    global _SESSION_STORY_MODEL_LOCKED
    persistent_cap = int(os.getenv("GEMINI_PERSISTENT_MAX_STORIES", "0") or "0")
    if persistent_cap > 1:
        return
    _SESSION_STORY_MODEL_LOCKED = False


def ensure_model_tier(page: Page, tier: str, *, required_verify: bool = True) -> dict[str, object]:
    global _SESSION_CURRENT_MODEL_TIER, _SESSION_ACTUAL_MODEL_LABEL, _SESSION_MODEL_VERIFIED
    global _SESSION_PREVIOUS_MODEL_LABEL, _SESSION_MODEL_SELECTION_ATTEMPTS, _SESSION_STORY_MODEL_LOCKED

    normalized = normalize_model_tier(tier)
    choice = resolve_gemini_model_alias(normalized)
    selection_stage = GEMINI_STAGE_KEY == "selection"
    allow_unverified_continue = selection_stage
    result: dict[str, object] = {
        "ok": False,
        "tier": normalized,
        "expected_model": choice.preferred_ui_label,
        "actual_model": "",
        "model_verified": False,
        "model_fallback_used": bool(_SESSION_MODEL_FALLBACK_USED),
        "model_selection_attempts": int(_SESSION_MODEL_SELECTION_ATTEMPTS),
        "reason": "ui_not_ready",
    }
    if _SESSION_MODEL_VERIFIED and _SESSION_CURRENT_MODEL_TIER == normalized:
        current_label = current_model_label_from_toolbar(page) or _SESSION_ACTUAL_MODEL_LABEL
        result.update(
            {
                "ok": True,
                "actual_model": current_label,
                "model_verified": True,
                "reason": "session_already_verified",
            }
        )
        print(
            f"[MODEL] expected={choice.preferred_ui_label} actual={current_label!r} "
            f"model_verified=true skipped_reason=already_verified "
            f"selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS} elapsed_seconds=0"
        )
        return result
    if _SESSION_STORY_MODEL_LOCKED and selection_stage:
        current_label = current_model_label_from_toolbar(page) or _SESSION_ACTUAL_MODEL_LABEL
        label_ok = bool(
            current_label
            and not is_mode_selector_placeholder_label(current_label)
            and (
                selection_accepts_model_label(current_label)
                or ui_label_matches_gemini_choice(current_label, choice)
            )
        )
        if label_ok:
            result.update(
                {
                    "ok": True,
                    "actual_model": current_label,
                    "model_verified": bool(_SESSION_MODEL_VERIFIED),
                    "reason": "story_model_locked",
                }
            )
            print(
                f"[MODEL] story_model_locked actual={current_label!r} "
                f"model_verified={bool(_SESSION_MODEL_VERIFIED)} "
                f"model_selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
            )
            return result
        print(
            f"[MODEL] story_model_lock_cleared actual={current_label!r} "
            f"reason=placeholder_or_unacceptable_label "
            f"model_selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
        )
        _SESSION_STORY_MODEL_LOCKED = False
        _SESSION_MODEL_VERIFIED = False
    if not wait_for_prompt_input_soft(page, timeout_ms=60_000):
        print("[WARN] Поле ввода Gemini не найдено — пропускаю выбор модели (state=waiting_input_editor)")
        if selection_stage and current_model_label_from_toolbar(page):
            label = current_model_label_from_toolbar(page)
            result.update({"ok": True, "actual_model": label, "reason": "use_current_model_input_missing"})
            return result
        return result
    time.sleep(POST_MODEL_STABILIZE_SECONDS)
    current_label = current_model_label_from_toolbar(page)
    toolbar_acceptable = bool(
        current_label
        and (
            ui_label_matches_gemini_choice(current_label, choice)
            or (selection_stage and selection_accepts_model_label(current_label))
        )
    )
    if toolbar_acceptable:
        verified = True
        skipped = "acceptable_current_model" if selection_stage else "already_selected"
        result.update(
            {
                "ok": True,
                "actual_model": current_label,
                "model_verified": verified,
                "reason": skipped,
                "skipped_reason": skipped,
            }
        )
        _SESSION_PREVIOUS_MODEL_LABEL = _SESSION_ACTUAL_MODEL_LABEL
        _SESSION_CURRENT_MODEL_TIER = normalized
        _SESSION_ACTUAL_MODEL_LABEL = current_label
        _SESSION_MODEL_VERIFIED = verified
        _SESSION_STORY_MODEL_LOCKED = True
        print(
            f"[MODEL] expected={choice.preferred_ui_label} actual={current_label!r} "
            f"model_verified={verified} skipped_reason={skipped} "
            f"selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS} elapsed_seconds=0"
        )
        dismiss_overlays(page)
        recover_ui_for_attach(page)
        return result

    if selection_stage and not current_label:
        print(
            f"[MODEL] skipped_reason=menu_not_opened_no_toolbar_label "
            f"selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
        )

    if selection_stage and is_mode_selector_placeholder_label(current_label or ""):
        dismiss_overlays(page)
        recover_ui_for_attach(page)
        for _ in range(3):
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(0.15)

    menu_budget = max(1, int(MAX_MODEL_SELECTION_ATTEMPTS))
    for attempt in range(1, menu_budget + 1):
        _SESSION_MODEL_SELECTION_ATTEMPTS += 1
        result["model_selection_attempts"] = int(_SESSION_MODEL_SELECTION_ATTEMPTS)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.2)
        if not open_model_mode_menu(page):
            print(
                f"[WARN] Меню режима модели не открылось (tier={normalized}, "
                f"попытка {attempt}/{menu_budget})."
            )
            time.sleep(0.8)
            continue
        time.sleep(0.45)
        clicked_label = click_model_option_for_tier(page, normalized)
        if clicked_label:
            time.sleep(POST_MODEL_STABILIZE_SECONDS)
            selected_label = current_model_label_from_toolbar(page)
            verified = ui_label_matches_gemini_choice(selected_label, choice)
            accept_unverified = allow_unverified_continue and bool(selected_label or clicked_label)
            result.update(
                {
                    "ok": verified or (not required_verify) or accept_unverified,
                    "actual_model": selected_label or clicked_label,
                    "model_verified": verified,
                    "reason": "resolved_model_selected" if verified else "MODEL_SELECTION_NOT_VERIFIED",
                    "clicked_label": clicked_label,
                }
            )
            _SESSION_PREVIOUS_MODEL_LABEL = _SESSION_ACTUAL_MODEL_LABEL
            _SESSION_CURRENT_MODEL_TIER = normalized
            _SESSION_ACTUAL_MODEL_LABEL = str(result.get("actual_model") or "")
            _SESSION_MODEL_VERIFIED = verified
            _SESSION_STORY_MODEL_LOCKED = True
            print(
                f"[MODEL] expected={choice.preferred_ui_label} actual={result.get('actual_model')!r} "
                f"model_verified={verified} clicked={clicked_label!r} "
                f"model_selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
            )
            if required_verify and not verified and not accept_unverified:
                print("[MODEL] MODEL_SELECTION_NOT_VERIFIED — selection not confirmed in UI")
                return result
            if not verified and accept_unverified:
                print("[MODEL] MODEL_SELECTION_NOT_VERIFIED — continuing with current UI model (selection stage)")
                actual = str(result.get("actual_model") or current_label or "")
                acceptable = ui_label_matches_gemini_choice(actual, choice) or selection_accepts_model_label(actual)
                if acceptable:
                    _SESSION_MODEL_VERIFIED = True
                    result["model_verified"] = True
                    result["skipped_reason"] = "acceptable_current_model"
                    print(
                        f"[MODEL] skipped_reason=acceptable_current_model elapsed_seconds=0 "
                        f"selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
                    )
            dismiss_overlays(page)
            return result
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.45)
    current_label = current_model_label_from_toolbar(page)
    if selection_stage and current_label:
        acceptable = selection_accepts_model_label(current_label) or ui_label_matches_gemini_choice(
            current_label, choice
        )
        result.update(
            {
                "ok": True,
                "actual_model": current_label,
                "model_verified": acceptable,
                "reason": "acceptable_current_model" if acceptable else "MODEL_SELECTION_NOT_VERIFIED",
            }
        )
        _SESSION_ACTUAL_MODEL_LABEL = current_label
        _SESSION_CURRENT_MODEL_TIER = normalized
        _SESSION_STORY_MODEL_LOCKED = True
        if acceptable:
            _SESSION_MODEL_VERIFIED = True
            print(
                f"[MODEL] skipped_reason=acceptable_current_model actual={current_label!r} "
                f"selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS} elapsed_seconds=0"
            )
        else:
            print(
                f"[MODEL] model_selection_failed tier={normalized} expected={expected_model_labels(choice)} "
                f"actual={current_label!r} reason=MODEL_SELECTION_NOT_VERIFIED continue=false "
                f"selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
            )
            result.update({"ok": False, "reason": "model_selection_failed"})
        dismiss_overlays(page)
        return result
    print(
        f"[MODEL] model_selection_failed tier={normalized} expected={expected_model_labels(choice)} "
        f"model_selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}"
    )
    result["reason"] = "model_selection_failed"
    return result


def try_model_fallback_on_limit(page: Page) -> dict[str, object]:
    global _SESSION_MODEL_FALLBACK_USED, _SESSION_MODEL_FALLBACK_ATTEMPTS, _SESSION_MODEL_LIMIT_COUNT
    global _SESSION_LIMIT_REASON

    _SESSION_MODEL_LIMIT_COUNT += 1
    current = normalize_model_tier(_SESSION_CURRENT_MODEL_TIER or "thinking")
    for tier in GEMINI_MODEL_FALLBACK_ORDER:
        if tier == current:
            continue
        _SESSION_MODEL_FALLBACK_ATTEMPTS += 1
        print(
            f"[LIMIT] model_fallback_try tier={tier} previous_tier={current} "
            f"attempt={_SESSION_MODEL_FALLBACK_ATTEMPTS}"
        )
        res = ensure_model_tier(page, tier, required_verify=True)
        if res.get("ok") and res.get("model_verified"):
            _SESSION_MODEL_FALLBACK_USED = True
            _SESSION_LIMIT_REASON = LIMIT_MODEL_CURRENT
            if not wait_for_prompt_input_soft(page, timeout_ms=30_000):
                print("[WARN] input editor not ready after model fallback")
                continue
            print(
                f"[LIMIT] model_fallback_success tier={tier} actual={res.get('actual_model')!r} "
                f"limit_reason={LIMIT_MODEL_CURRENT}"
            )
            return {
                "ok": True,
                "tier": tier,
                "actual_model": res.get("actual_model"),
                "limit_reason": LIMIT_MODEL_CURRENT,
            }
    print(f"[LIMIT] model_fallback_exhausted tiers={GEMINI_MODEL_FALLBACK_ORDER}")
    return {"ok": False, "limit_reason": LIMIT_ACCOUNT_ALL_MODELS}


def handle_gem_limit_error(page: Page, error_text: str, *, gem_url: str) -> str:
    """Return recovery action: retry_story | account_limit."""
    limit_kind = classify_gemini_limit(error_text)
    if has_thinking_limit_message(page):
        limit_kind = LIMIT_MODEL_CURRENT
    if MODEL_FALLBACK_ON_LIMIT:
        fb = try_model_fallback_on_limit(page)
        if fb.get("ok"):
            return "retry_story"
        raise RuntimeError(
            limit_error_message(
                kind=LIMIT_ACCOUNT_ALL_MODELS,
                detail="all model fallback tiers exhausted",
            )
        )
    wait_with_status(
        LIMIT_RETRY_PAUSE_SECONDS,
        f"Лимит Gem-бота, короткая пауза {LIMIT_RETRY_PAUSE_SECONDS} сек",
    )
    if not recover_session_state(page, gem_url):
        wait_with_status(LIMIT_RETRY_PAUSE_SECONDS, "UI не восстановился после лимита")
    return "retry_story"


def ensure_stage_default_model(page: Page) -> bool:
    tier = GEMINI_INITIAL_MODEL_TIER or (GEMINI_MODEL_FALLBACK_ORDER[0] if GEMINI_MODEL_FALLBACK_ORDER else "fast")
    required = GEMINI_STAGE_KEY != "selection"
    started = time.monotonic()
    res = ensure_model_tier(page, tier, required_verify=required)
    elapsed = time.monotonic() - started
    skipped = str(res.get("skipped_reason") or res.get("reason") or "")
    if skipped in {"session_already_verified", "acceptable_current_model", "already_selected", "story_model_locked"}:
        print(
            f"[MODEL] skipped_reason={skipped} selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS} "
            f"elapsed_seconds={elapsed:.1f}"
        )
    else:
        print(f"[MODEL] selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS} elapsed_seconds={elapsed:.1f}")
    return bool(res.get("ok"))


def ensure_thinking_mode(page: Page) -> bool:
    if GEMINI_STAGE_KEY == "selection":
        return ensure_stage_default_model(page)
    res = ensure_model_tier(page, "thinking", required_verify=True)
    return bool(res.get("ok") and res.get("model_verified"))


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
                if len(text) >= 30:
                    return text
            except Exception:
                continue
    return ""


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


def wait_for_new_response_block(page: Page, previous_count: int, timeout_ms: int = WAIT_TIMEOUT_MS) -> None:
    deadline = time.time() + timeout_ms / 1000
    stable_rounds = 0
    last_status_print = 0.0
    while time.time() < deadline:
        if consume_wait_override_key():
            print(f"[STATUS] Ожидание нового блока ответа принудительно пропущено клавишей '{WAIT_OVERRIDE_KEY}'.")
            return
        current_count = get_response_blocks_count(page)
        if current_count > previous_count and not is_generation_in_progress(page):
            stable_rounds += 1
            if stable_rounds >= IDLE_STABLE_ROUNDS:
                return
        else:
            stable_rounds = 0
            now = time.time()
            if now - last_status_print >= HEARTBEAT_SECONDS:
                print("[STATUS] Ожидаю появление нового блока ответа Gemini...")
                last_status_print = now
        time.sleep(0.35)
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


def build_response_marker(folder: Path, source_file: Path) -> str:
    base = f"{folder.name}_{source_file.stem}_{int(time.time())}"
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", base)
    return normalized[:120]


def extract_validated_response(response_text: str, marker: str) -> str:
    normalized_text = response_text.replace("\r\n", "\n")
    escaped_prefix = re.escape(RESPONSE_MARKER_PREFIX)
    escaped_marker = re.escape(marker)
    marker_regex = re.compile(
        rf"^\s*[*`_#>-]*\s*{escaped_prefix}\s*[:\-]\s*[*`_#>-]*\s*{escaped_marker}\s*[*`_#<-]*\s*$",
        re.IGNORECASE
    )
    lines = normalized_text.split("\n")
    filtered_lines = [line for line in lines if not marker_regex.match(line)]
    marker_found = len(filtered_lines) != len(lines)
    cleaned = "\n".join(filtered_lines).strip()
    if marker_found and cleaned:
        return cleaned
    if marker_found and not cleaned:
        raise RuntimeError("Маркер ответа получен, но полезный текст отсутствует.")
    print("[WARN] Маркер ответа не найден. Принимаю ответ по DOM-привязке текущего сообщения.")
    return normalized_text.strip()


def process_story_folder(page: Page, folder: Path) -> bool:
    info_file = folder / INFO_FILE_NAME
    if info_file.exists():
        print(f"[SKIP] {folder.name}: найден {INFO_FILE_NAME}")
        return False

    source_file = pick_story_source_file(folder)
    if source_file is None:
        print(f"[SKIP] {folder.name}: нет исходного .txt файла")
        return False

    story_started = time.monotonic()
    model_started = time.monotonic()
    response_marker = build_response_marker(folder, source_file)
    print(f"[RUN] {folder.name}: отправляю {source_file.name}")
    reset_story_model_lock()
    human_pause("перед отправкой запроса")
    wait_for_generation_idle(page, timeout_ms=WAIT_TIMEOUT_MS)
    if GEMINI_STAGE_KEY == "selection" and not _SESSION_MODEL_VERIFIED:
        ensure_stage_default_model(page)
    elif GEMINI_STAGE_KEY == "selection" and _SESSION_MODEL_VERIFIED:
        print(
            f"[MODEL] skipped_reason=already_verified selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS} "
            f"elapsed_seconds=0"
        )
    model_elapsed = time.monotonic() - model_started
    print(f"[MODEL] elapsed_seconds={model_elapsed:.1f} selection_attempts={_SESSION_MODEL_SELECTION_ATTEMPTS}")
    dismiss_overlays(page)
    recover_ui_for_attach(page)
    previous_response_blocks = get_response_blocks_count(page)
    previous_copy_count = get_copy_buttons_count(page)
    if has_limit_message(page):
        raise RuntimeError("Лимит Gem-бота: сервис временно не принимает запросы.")
    if has_deleted_gem_bot_screen(page):
        raise RuntimeError("GEM_BOT_DELETED: deleted Gem bot screen before attach")
    gem_url = (os.getenv("GEMINI_EXPECTED_GEM_BOT_URL") or os.getenv("GEMINI_URL") or "").strip()
    health = _page_health_snapshot(page)
    if health.get("broken"):
        reason = str(health.get("reason") or "GEMINI_PAGE_BLANK")
        if gem_url and _recover_page_if_broken(page, gem_url):
            health = _page_health_snapshot(page)
        if health.get("broken"):
            raise RuntimeError(f"{reason}: Gemini page broken before attach")
    attach_started = time.monotonic()
    if not paste_file_into_prompt_with_retries(page, source_file, story_name=folder.name):
        if has_limit_message(page):
            raise RuntimeError("Лимит Gem-бота: сервис временно не принимает файлы.")
        raise RuntimeError(f"{folder.name}: не удалось вставить файл {source_file.name} в чат")
    attach_elapsed = time.monotonic() - attach_started
    print(f"[TIMING] attach_seconds={attach_elapsed:.1f}")

    pre_submit_elapsed = time.monotonic() - story_started
    if pre_submit_elapsed > 90.0:
        print(f"[TIMING] PRE_SUBMIT_TOO_SLOW pre_submit_seconds={pre_submit_elapsed:.1f}")
        save_debug_artifacts(page, f"pre_submit_too_slow_{folder.name}")

    submit_started = time.monotonic()
    prompt_input = wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
    prompt_input.click()
    page.keyboard.insert_text(
        f"Обработай содержимое файла {source_file.name}.\n"
        f"Добавь отдельной строкой маркер: {RESPONSE_MARKER_PREFIX}: {response_marker}"
    )
    if not click_send_button(page):
        page.keyboard.press("Enter")
        if not click_send_button(page, timeout_ms=5_000):
            if has_limit_message(page):
                raise RuntimeError("Лимит Gem-бота: сервис временно не отправляет запросы.")
            raise RuntimeError(f"{folder.name}: не удалось отправить запрос")
    submit_elapsed = time.monotonic() - submit_started
    print(f"[TIMING] submit_seconds={submit_elapsed:.1f}")

    response_started = time.monotonic()
    try:
        wait_for_new_response_block(page, previous_response_blocks, timeout_ms=WAIT_TIMEOUT_MS)
    except TimeoutError as timeout_error:
        try:
            copy_button = wait_response_ready(page, previous_copy_count, timeout_ms=12_000)
            if not click_copy_button_resilient(page, copy_button):
                raise timeout_error
        except Exception:
            if has_limit_message(page):
                raise RuntimeError("Лимит Gem-бота: генерация заблокирована лимитами.") from timeout_error
            raise
    human_pause("после генерации перед копированием")
    response_text = read_latest_response_from_dom(page)
    if not response_text:
        try:
            copy_button = wait_response_ready(page, previous_copy_count, timeout_ms=10_000)
            if click_copy_button_resilient(page, copy_button):
                response_text = read_clipboard_text(page)
        except Exception:
            response_text = ""
    if not response_text:
        raise RuntimeError(f"{folder.name}: временный сбой чтения ответа")

    parse_started = time.monotonic()
    validated_text = extract_validated_response(response_text, response_marker)
    info_output = build_info_with_genre(folder, validated_text)
    parse_seconds = time.monotonic() - parse_started
    info_file.write_text(info_output, encoding="utf-8")
    _write_orchestrator_processed_marker(folder, info_file=info_file, body=info_output)
    print(f"[DONE] {folder.name}: сохранён {INFO_FILE_NAME}")
    try:
        body = info_file.read_text(encoding="utf-8", errors="ignore")
        status = parse_youtube_suitable_status(body)
        if status == "нет":
            dest_root = TRASH_DIR / folder.relative_to(STORIES_DIR)
            dest_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(dest_root))
            print(f"[TRASH] {folder.name}: youtube=no -> {dest_root}")
    except Exception as exc:
        print(f"[WARN] Не удалось переместить в trash: {exc}")
    human_pause("между подпапками")
    response_elapsed = time.monotonic() - response_started
    story_total = time.monotonic() - story_started
    print(
        f"[TIMING] bot_open_seconds=n/a identity_verify_seconds=n/a model_verify_seconds={model_elapsed:.1f} "
        f"attach_seconds={attach_elapsed:.1f} submit_seconds={submit_elapsed:.1f} "
        f"response_wait_seconds={response_elapsed:.1f} parse_seconds={parse_seconds:.1f} "
        f"story_total_seconds={story_total:.1f} pre_submit_seconds={pre_submit_elapsed:.1f}"
    )
    return True


def generate_report(stories_dir: Path, processed_count: int, story_folders: list[Path]) -> None:
    build_genre_reports(stories_dir, story_folders)
    print(f"[INFO] Отчёты по жанрам созданы. Обработано в запуске: {processed_count}")




def normalize_top_level_genre_txt_files(stories_dir: Path) -> int:
    """Переносит stories/<жанр>/*.txt в stories/<жанр>/<имя>/<имя>.txt (одна папка на рассказ)."""
    moved = 0
    for genre_dir in sorted([p for p in stories_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        if genre_dir.name.lower() == "trash":
            continue
        for txt in list(genre_dir.glob("*.txt")):
            n = txt.name.lower()
            if n == INFO_FILE_NAME.lower() or n == GENRE_REPORT_FILE_NAME.lower():
                continue
            if REPORT_FILE_PATTERN.match(txt.name):
                continue
            stem = txt.stem
            dest_dir = genre_dir / stem
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / txt.name
            if dest_file.exists():
                continue
            shutil.move(str(txt), str(dest_file))
            moved += 1
    return moved


def recover_trash_to_stories(trash_dir: Path, stories_dir: Path) -> int:
    """RECOVER_TRASH=1: возвращает верхний уровень папок из trash обратно в stories."""
    if not trash_dir.exists():
        return 0
    n = 0
    for child in list(trash_dir.iterdir()):
        if not child.is_dir():
            continue
        target = stories_dir / child.name
        if target.exists():
            continue
        shutil.move(str(child), str(target))
        n += 1
    return n


def sweep_stories_not_youtube_yes_to_trash(stories_dir: Path) -> int:
    """На старте: если в папке уже есть info.txt и статус не «да» — в trash."""
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0
    for info_path in stories_dir.rglob(INFO_FILE_NAME):
        folder = info_path.parent
        if folder == stories_dir:
            continue
        try:
            text = info_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        st = parse_youtube_suitable_status(text)
        if st != "нет":
            continue
        try:
            rel = folder.relative_to(stories_dir)
        except ValueError:
            continue
        dest = TRASH_DIR / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(dest))
        moved += 1
    return moved


def _is_claim_stale(claim_path: Path) -> bool:
    try:
        age = time.time() - claim_path.stat().st_mtime
        return age > CLAIM_TTL_SECONDS
    except Exception:
        return True


def try_claim_story_folder(folder: Path, worker_id: str) -> bool:
    claim = folder / CLAIM_FILE_NAME
    try:
        with open(claim, "x", encoding="utf-8") as f:
            f.write(worker_id + "\n")
        return True
    except FileExistsError:
        if _is_claim_stale(claim):
            try:
                claim.unlink()
            except Exception:
                return False
            return try_claim_story_folder(folder, worker_id)
        return False
    except OSError:
        # Folder was moved to trash by another worker — silently skip it.
        return False


def release_story_claim(folder: Path) -> None:
    claim = folder / CLAIM_FILE_NAME
    try:
        claim.unlink()
    except Exception:
        pass


def cleanup_stale_claims(stories_dir: Path) -> int:
    removed = 0
    for claim in stories_dir.rglob(CLAIM_FILE_NAME):
        if _is_claim_stale(claim):
            try:
                claim.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def pick_next_pending_folder_with_claim(
    pending: list[Path], worker_id: str
) -> Path | None:
    for folder in pending:
        if try_claim_story_folder(folder, worker_id):
            return folder
    return None


def _auto_fill_google_email(page: Page, email: str) -> None:
    try:
        el = page.locator('input[type="email"], input[name="identifier"], #identifierId').first
        if el.count() and el.is_visible(timeout=3_000):
            el.fill(email, timeout=5_000)
            page.keyboard.press("Enter")
    except Exception:
        pass


def _auto_fill_google_password(page: Page, password: str) -> None:
    if not password:
        return
    try:
        el = page.locator('input[type="password"], input[name="password"]').first
        if el.count() and el.is_visible(timeout=3_000):
            el.fill(password, timeout=5_000)
            page.keyboard.press("Enter")
    except Exception:
        pass


def _auto_fill_2fa_code(page: Page, code: str) -> None:
    try:
        el = page.locator(
            'input[type="tel"], input[name="totpPin"], input[name="code"], input[autocomplete="one-time-code"]'
        ).first
        if el.count() and el.is_visible(timeout=3_000):
            el.fill(code, timeout=5_000)
            page.keyboard.press("Enter")
    except Exception:
        pass


def _accountchooser_click_email_row(page: Page, email: str) -> bool:
    for attr in ("data-identifier", "data-email", "data-profileidentifier"):
        try:
            loc = page.locator(f'[{attr}="{email}"]')
            if loc.count() > 0 and loc.first.is_visible(timeout=3_000):
                loc.first.scroll_into_view_if_needed(timeout=3_000)
                loc.first.click(timeout=5_000)
                return True
        except Exception:
            continue
    try:
        loc = page.locator(f'li:has([data-identifier="{email}"])')
        if loc.count() > 0:
            loc.first.click(timeout=5_000)
            return True
    except Exception:
        pass
    try:
        exact = page.get_by_text(email, exact=True)
        for i in range(min(exact.count(), 5)):
            el = exact.nth(i)
            if el.is_visible():
                el.scroll_into_view_if_needed(timeout=2_000)
                el.click(timeout=5_000)
                return True
    except Exception:
        pass
    return False


def _try_switch_account_via_chooser(page: Page, email: str, gem_url: str) -> bool:
    if not email:
        return False
    want_key = _gem_session_key(gem_url)
    em = quote(email, safe="")
    gm = quote(gem_url, safe="")
    chooser_url = f"https://accounts.google.com/AccountChooser?Email={em}&continue={gm}"
    for attempt in range(1, 4):
        try:
            page.goto(chooser_url, wait_until="domcontentloaded", timeout=90_000)
            time.sleep(2.5)
        except Exception:
            continue
        cur = page.url or ""
        if "accounts.google.com" not in cur.lower():
            if want_key and _gem_session_key(cur) == want_key:
                print(f"[ACCOUNT] AccountChooser → авто-редирект на {email} ✓")
                return True
            print(f"[WARN] Редирект, но URL не тот (попытка {attempt}/3): {cur[:80]}")
            continue
        print(f"[ACCOUNT] Список аккаунтов. Ищу строку для {email}...")
        if _accountchooser_click_email_row(page, email):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=45_000)
                time.sleep(2.0)
            except Exception:
                pass
            cur = page.url or ""
            if want_key and _gem_session_key(cur) == want_key:
                print(f"[ACCOUNT] Клик по строке → успешно для {email} ✓")
                return True
            print(f"[WARN] Кликнул, но URL не тот (попытка {attempt}/3): {cur[:80]}")
        else:
            print(f"[WARN] Строка {email} не найдена в AccountChooser (попытка {attempt}/3).")
    return False


def initialize_account_browser(page: Page, account: AccountConfig) -> None:
    want_key = _gem_session_key(account.gem_url)
    bot_open_started = time.monotonic()
    print(f"[ACCOUNT] Открываю: {account.gem_url}")
    identity = ensure_canonical_gem_bot_page(page, account.gem_url, max_attempts=2)
    if not identity.get("ok"):
        raise RuntimeError(f"GEM_URL_MISMATCH: {identity.get('reason') or 'bot_identity_not_verified'}")
    time.sleep(1)
    if (
        want_key
        and _gem_session_key(page.url or "") != want_key
        and account.email
        and PARALLEL_WORKERS <= 1
        and not SEPARATE_PROFILES
    ):
        print("[ACCOUNT] Прямой goto → не тот аккаунт. Переключаю через AccountChooser...")
        if not _try_switch_account_via_chooser(page, account.email, account.gem_url):
            print("[WARN] Автоматически не вышло.")
            print(f"[MANUAL] Нужно: {account.gem_url}")
            print(f"[MANUAL] Сейчас: {page.url}")
            prompt_user("[MANUAL] Открой URL вручную в браузере и нажми Enter...")
    if is_login_screen_visible(page):
        if account.email:
            print(f"[AUTH] Требуется вход. Автовход: {account.email} ...")
            _auto_fill_google_email(page, account.email)
            time.sleep(1.5)
            _auto_fill_google_password(page, account.password)
            time.sleep(3)
            if "accounts.google.com" in (page.url or "").lower():
                code = prompt_user(
                    f"[2FA] Введи код 2FA для {account.email} (Enter — вручную): "
                ).strip()
                if code:
                    _auto_fill_2fa_code(page, code)
                    time.sleep(3)
                else:
                    prompt_user("[AUTH] Войди вручную в браузере и нажми Enter здесь...")
        else:
            prompt_user("[AUTH] Войди в аккаунт Google вручную в браузере и нажми Enter здесь...")
    auth_round = 0
    while not wait_for_initial_gemini_load(page, reason="initial_account_setup"):
        auth_round += 1
        if auth_round > 3:
            print(f"[WARN] UI Gemini [{account.gem_url}] не готов после {auth_round} раундов ожидания.")
            break
        if is_login_screen_visible(page):
            print("[AUTH] Экран входа Google — требуется авторизация.")
            break
        if _page_has_server_error(page):
            wait_with_status(30, f"Ошибка сервера Google (раунд {auth_round})")
        else:
            wait_with_status(20, f"UI Gemini загружается (раунд {auth_round}), без reload")
    ensure_on_bot_page(page, account.gem_url, max_goto=1)
    if has_deleted_gem_bot_screen(page):
        raise RuntimeError("GEM_BOT_DELETED: deleted Gem bot screen after navigation")
    if not wait_for_prompt_input_soft(page, timeout_ms=30_000):
        wait_for_initial_gemini_load(page, reason="post_bot_navigation")
    identity_verify_started = time.monotonic()
    identity = verify_expected_gem_bot(page, account.gem_url)
    identity_verify_seconds = time.monotonic() - identity_verify_started
    if not identity.get("ok"):
        raise RuntimeError(f"GEM_BOT_IDENTITY_FAILED: {identity.get('reason') or 'not_verified'}")
    model_verify_started = time.monotonic()
    ensure_stage_default_model(page)
    model_verify_seconds = time.monotonic() - model_verify_started
    bot_open_seconds = time.monotonic() - bot_open_started
    print(
        f"[TIMING] bot_open_seconds={bot_open_seconds:.1f} "
        f"identity_verify_seconds={identity_verify_seconds:.1f} "
        f"model_verify_seconds={model_verify_seconds:.1f}"
    )


def open_account_context(playwright, account: AccountConfig):
    account.user_data_dir.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(account.user_data_dir),
        channel="chrome",
        headless=False,
        slow_mo=SLOW_MO_MS,
        viewport=None,
        chromium_sandbox=True,
        args=append_chrome_proxy_args(["--disable-blink-features=AutomationControlled"]),
    )
    for extra_page in list(context.pages)[1:]:
        try:
            extra_page.close()
        except Exception:
            pass
    page = context.pages[0] if context.pages else context.new_page()
    try:
        if page.url and page.url not in ("about:blank", ""):
            page.goto("about:blank", wait_until="domcontentloaded", timeout=15_000)
    except Exception:
        pass
    context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin="https://gemini.google.com",
    )
    return context, page


def normalize_gem_url_for_single_profile(gem_url: str) -> str:
    return re.sub(r"/u/\d+/", "/u/0/", gem_url, count=1)


def load_accounts_from_file(path: Path) -> list[AccountConfig]:
    accounts: list[AccountConfig] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        email = ""
        password = ""
        gem_url = ""
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                email, password, gem_url = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                email, gem_url = parts[0], parts[1]
        else:
            gem_url = line
        if not gem_url.startswith("http"):
            continue
        if SEPARATE_PROFILES:
            gem_url = normalize_gem_url_for_single_profile(gem_url)
        user_data_dir = (PROJECT_DIR / f"user_data_{len(accounts)}") if SEPARATE_PROFILES else USER_DATA_DIR
        accounts.append(AccountConfig(email=email, password=password, gem_url=gem_url, user_data_dir=user_data_dir))
    return accounts


def prompt_accounts_interactive() -> list[AccountConfig]:
    print("\n[INPUT] Введи ссылки Gem-ботов для каждого аккаунта.")
    accounts: list[AccountConfig] = []
    while True:
        idx = len(accounts)
        gem_url_input = prompt_user(
            f"[INPUT] Ссылка Gem-бота для аккаунта {idx + 1} (Enter — закончить): "
        ).strip()
        if not gem_url_input:
            break
        if not gem_url_input.startswith("http"):
            gem_url_input = DEFAULT_GEMINI_URL
        user_data_dir = (PROJECT_DIR / f"user_data_{idx}") if SEPARATE_PROFILES else USER_DATA_DIR
        if SEPARATE_PROFILES:
            gem_url_input = normalize_gem_url_for_single_profile(gem_url_input)
        accounts.append(AccountConfig(email="", password="", gem_url=gem_url_input, user_data_dir=user_data_dir))
    return accounts


def load_or_prompt_accounts() -> list[AccountConfig]:
    pref_account = _account_from_preflight_env()
    if pref_account is not None:
        print(f"[INFO] Preflight account from env: {pref_account.email or '(no email)'} -> {pref_account.gem_url}")
        return [pref_account]
    env_url = (os.getenv("GEMINI_URL") or "").strip()
    if env_url and GEMINI_URL_PATTERN.fullmatch(env_url):
        return [AccountConfig(email="", password="", gem_url=env_url, user_data_dir=USER_DATA_DIR)]
    if ACCOUNTS_FILE.exists():
        accounts = load_accounts_from_file(ACCOUNTS_FILE)
        if accounts:
            print(f"[INFO] Загружено аккаунтов из {ACCOUNTS_FILE.name}: {len(accounts)}")
            for i, acc in enumerate(accounts):
                print(f"  {i + 1}. {acc.email or '(no email)'} -> {acc.gem_url}")
            return accounts
        print(f"[WARN] {ACCOUNTS_FILE.name} найден, но аккаунты не распознаны. Ввод вручную.")
    accounts = prompt_accounts_interactive()
    if not accounts:
        env_url = (os.getenv("GEMINI_URL") or "").strip()
        gem_url = env_url if env_url and GEMINI_URL_PATTERN.fullmatch(env_url) else DEFAULT_GEMINI_URL
        print(f"[INFO] Одиночный режим. Gem-бот: {gem_url}")
        return [AccountConfig(email="", password="", gem_url=gem_url, user_data_dir=USER_DATA_DIR)]
    save = prompt_user(f"\n[INPUT] Сохранить список ссылок в {ACCOUNTS_FILE.name}? (y/n): ").strip().lower()
    if save == "y":
        lines = [
            "# Формат: email|password|url или одна ссылка на строку",
        ]
        for acc in accounts:
            lines.append(acc.gem_url)
        ACCOUNTS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[INFO] Ссылки сохранены в {ACCOUNTS_FILE}")
    return accounts



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


def _account_from_preflight_env() -> AccountConfig | None:
    open_url = (os.getenv("GEMINI_EXPECTED_GEM_BOT_URL") or os.getenv("GEMINI_URL") or "").strip()
    if not open_url:
        return None
    if not GEMINI_URL_PATTERN.fullmatch(open_url) and _gem_id_from_url(open_url):
        open_url = f"https://gemini.google.com/gem/{_gem_id_from_url(open_url)}"
    email = (os.getenv("GEMINI_ACCOUNT_EMAIL") or "").strip()
    return AccountConfig(email=email, password="", gem_url=open_url, user_data_dir=USER_DATA_DIR)


def run_gem_bot_preflight_only() -> int:
    """Open expected Gem bot URL once and log GEM_BOT identity fields."""
    import traceback as _traceback

    current_account = _account_from_preflight_env()
    if current_account is None:
        accounts = load_or_prompt_accounts()
        if not accounts:
            print("[GEM_BOT] preflight_failed reason=no_accounts")
            _log_gem_bot_identity(bot_identity_verified=False, reason="no_accounts")
            return EXIT_CODE_ERROR
        account_idx = int(os.getenv("START_ACCOUNT_INDEX", "0").strip() or "0") % max(1, len(accounts))
        current_account = accounts[account_idx]
    open_url = current_account.gem_url
    if _gem_id_from_url(open_url):
        open_url = f"https://gemini.google.com/gem/{_gem_id_from_url(open_url)}"
        current_account = AccountConfig(
            email=current_account.email,
            password=current_account.password,
            gem_url=open_url,
            user_data_dir=current_account.user_data_dir,
        )
    print(
        f"[GEM_BOT] preflight expected_account_email={current_account.email or 'n/a'} "
        f"navigate_url={open_url}"
    )
    context = None
    try:
        with sync_playwright() as playwright:
            _log_gem_bot_identity(browser_started=True, navigate_url=open_url)
            context, page = open_account_context(playwright, current_account)
            _log_gem_bot_identity(page_created=True, navigate_attempted=False)
            _log_gem_bot_identity(navigate_attempted=True, navigate_url=open_url)
            identity = ensure_canonical_gem_bot_page(page, current_account.gem_url, max_attempts=2)
            if not wait_for_prompt_input_soft(page, timeout_ms=45_000):
                identity = verify_expected_gem_bot(page, current_account.gem_url)
                identity["ok"] = False
                identity["reason"] = "input_editor_not_ready"
            verified = bool(identity.get("ok"))
            print(f"[GEM_BOT] preflight_done bot_identity_verified={str(verified).lower()}")
            return EXIT_CODE_OK if verified else EXIT_CODE_ERROR
    except Exception as exc:
        tail = _traceback.format_exc()[-2000:]
        print(f"[GEM_BOT] preflight_failed error={exc}")
        print(f"[GEM_BOT] traceback_tail={tail}")
        _log_gem_bot_identity(
            bot_identity_verified=False,
            reason=str(exc),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            traceback_tail=tail,
        )
        return EXIT_CODE_ERROR
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def main() -> int:
    if os.getenv("GEMINI_PREFLIGHT_ONLY", "0").strip() == "1":
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        STORIES_DIR.mkdir(parents=True, exist_ok=True)
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        return run_gem_bot_preflight_only()

    if not STORIES_DIR.exists():
        raise FileNotFoundError(f"Папка stories не найдена: {STORIES_DIR}")

    if CLEAN_CLAIMS:
        removed = cleanup_stale_claims(STORIES_DIR)
        print(f"[CLEAN] Удалено протухших {CLAIM_FILE_NAME}: {removed} (TTL={CLAIM_TTL_SECONDS} сек)")
        return EXIT_CODE_OK

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)

    recover = os.getenv("RECOVER_TRASH", "0").strip() == "1"
    if recover:
        restored = recover_trash_to_stories(TRASH_DIR, STORIES_DIR)
        if restored:
            print(f"[INFO] Восстановлено из trash обратно в stories (верхний уровень): {restored}")

    moved = 0
    if not PERSISTENT_INBOX:
        moved = normalize_top_level_genre_txt_files(STORIES_DIR)
    if moved:
        print(f"[INFO] Нормализация структуры: перемещено исходных .txt -> подпапки: {moved}")

    swept = sweep_stories_not_youtube_yes_to_trash(STORIES_DIR)
    if swept:
        print(f"[INFO] При старте в {TRASH_DIR.name} перенесено папок с уже готовым info.txt (не «да»): {swept}")

    all_story_folders = collect_story_folders(STORIES_DIR)
    pending = [folder for folder in all_story_folders if not (folder / INFO_FILE_NAME).exists()]
    top_level_dirs = sorted(
        [item for item in STORIES_DIR.iterdir() if item.is_dir() and item.name.lower() != "trash"]
    )

    print(f"[INFO] stories: {STORIES_DIR}")
    print(f"[INFO] корзина (не подходит для YouTube): {TRASH_DIR}")
    print(f"[INFO] Найдено верхнеуровневых папок: {len(top_level_dirs)}")
    print(f"[INFO] Найдено папок с историями: {len(all_story_folders)}")
    print(f"[INFO] Требуют обработки: {len(pending)}")

    accounts = load_or_prompt_accounts()
    print(f"[INFO] Аккаунтов для работы: {len(accounts)}")
    print(f"[INFO] Рассказов на диалог: {FILES_PER_DIALOG}")
    if PARALLEL_WORKERS <= 1:
        if FILES_PER_ACCOUNT > 0:
            print(f"[INFO] Рассказов на аккаунт (ротация): {FILES_PER_ACCOUNT}")
        else:
            print("[INFO] Лимит на аккаунт: нет (FILES_PER_ACCOUNT=0)")
    else:
        print("[INFO] Параллельный режим: каждый воркер на своём аккаунте")

    processed_count = 0
    stop_reason = ""
    stop_exit_code = EXIT_CODE_OK
    with sync_playwright() as playwright:
        if PARALLEL_WORKERS > 1:
            account_idx = WORKER_INDEX % max(1, len(accounts))
        else:
            wi = (os.getenv("WORKER_INDEX") or "").strip()
            if wi and wi != "0":
                print(
                    "[WARN] WORKER_INDEX задан, но в одиночном режиме игнорируется "
                    f"(было {wi!r}). Старт с START_ACCOUNT_INDEX / первого аккаунта."
                )
            account_idx = int(os.getenv("START_ACCOUNT_INDEX", "0").strip() or "0") % max(1, len(accounts))

        current_account = accounts[account_idx]
        print(f"[ACCOUNT] Старт с аккаунта {account_idx + 1}/{len(accounts)}: {current_account.email or '(default)'}")
        for _init_attempt in range(1, 4):
            try:
                context, page = open_account_context(playwright, current_account)
                initialize_account_browser(page, current_account)
                break
            except Exception as init_err:
                print(f"[ERROR] Ошибка инициализации браузера (попытка {_init_attempt}/3): {init_err}")
                try:
                    context.close()
                except Exception:
                    pass
                if _init_attempt == 3:
                    raise
                wait_with_status(30, f"Жду перед повторной инициализацией (попытка {_init_attempt})")

        should_stop = False
        limit_consecutive = 0
        processed_since_dialog_reset = 0
        processed_this_account = 0
        persistent_stories_done = 0
        session_started_mono = time.monotonic()
        serial_idx = 0

        def _refresh_pending_folders() -> list[Path]:
            all_folders = collect_story_folders(STORIES_DIR, quiet=PERSISTENT_NO_IDLE_EXIT)
            return [
                folder
                for folder in all_folders
                if not (folder / INFO_FILE_NAME).exists() and pick_story_source_file(folder) is not None
            ]

        if PERSISTENT_INBOX and BROWSER_SESSION_ID:
            print(
                f"[BROWSER] event=opened account={persistent_log_account_index(account_idx)} "
                f"browser_session_id={BROWSER_SESSION_ID}"
            )
        if PARALLEL_WORKERS > 1 and not WORKER_ID:
            print("[WARN] PARALLEL_WORKERS>1, но WORKER_ID не задан. Ставлю WORKER_ID='w1'.")
            worker_id = "w1"
        else:
            worker_id = WORKER_ID or "w1"

        while True:
            folder = None
            if PARALLEL_WORKERS > 1:
                folder = pick_next_pending_folder_with_claim(pending, worker_id)
                if folder is None:
                    print(f"[WORKER] {worker_id}: задач больше нет, выхожу.")
                    break
            elif PERSISTENT_INBOX:
                if PERSISTENT_STOP_FILE and Path(PERSISTENT_STOP_FILE).is_file():
                    stop_reason = "orchestrator_stop"
                    break
                pending = _refresh_pending_folders()
                if not pending:
                    if (
                        persistent_stories_done > 0
                        and PERSISTENT_MAX_STORIES > 0
                        and persistent_stories_done >= PERSISTENT_MAX_STORIES
                    ):
                        while not soft_reset_persistent_browser(
                            page, current_account.gem_url, reason="max_stories", account_idx=account_idx
                        ):
                            wait_with_status(30, "soft reset после max_stories")
                        persistent_stories_done = 0
                        processed_since_dialog_reset = 0
                        session_started_mono = time.monotonic()
                        continue
                    if (
                        persistent_stories_done > 0
                        and PERSISTENT_MAX_LIFETIME_MIN > 0
                        and (time.monotonic() - session_started_mono) > PERSISTENT_MAX_LIFETIME_MIN * 60
                    ):
                        while not soft_reset_persistent_browser(
                            page, current_account.gem_url, reason="max_lifetime", account_idx=account_idx
                        ):
                            wait_with_status(30, "soft reset после max_lifetime")
                        persistent_stories_done = 0
                        processed_since_dialog_reset = 0
                        session_started_mono = time.monotonic()
                        continue
                    if PERSISTENT_NO_IDLE_EXIT:
                        last_wait_log = 0.0
                        got_new = False
                        while True:
                            if PERSISTENT_STOP_FILE and Path(PERSISTENT_STOP_FILE).is_file():
                                stop_reason = "orchestrator_stop"
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
                        idle_deadline = time.monotonic() + PERSISTENT_IDLE_SEC
                        got_new = False
                        while time.monotonic() < idle_deadline:
                            if PERSISTENT_STOP_FILE and Path(PERSISTENT_STOP_FILE).is_file():
                                stop_reason = "orchestrator_stop"
                                should_stop = True
                                break
                            pending = _refresh_pending_folders()
                            if pending:
                                got_new = True
                                break
                            time.sleep(0.5)
                        if should_stop:
                            break
                        if not got_new:
                            stop_reason = "idle_timeout"
                            break
                if pending:
                    folder = pending[0]
            else:
                if serial_idx >= len(pending):
                    break
                folder = pending[serial_idx]

            if folder is None:
                continue

            if PERSISTENT_INBOX and persistent_stories_done > 0 and BROWSER_SESSION_ID:
                print(
                    f"[BROWSER] event=reused account={persistent_log_account_index(account_idx)} "
                    f"browser_session_id={BROWSER_SESSION_ID} "
                    f"story={folder.name}"
                )

            transient_attempt = 0
            while True:
                try:
                    relative_folder = str(folder.relative_to(STORIES_DIR))
                    print(f"[STATUS] Обрабатываю: {relative_folder}")
                    if has_deleted_gem_bot_screen(page):
                        raise RuntimeError("GEM_BOT_DELETED: deleted Gem bot screen before story")
                    story_identity = verify_expected_gem_bot(page, current_account.gem_url)
                    if not story_identity.get("ok"):
                        raise RuntimeError(
                            f"GEM_URL_MISMATCH: {story_identity.get('reason') or 'bot_identity_not_verified'}"
                        )
                    wait_for_generation_idle(page, timeout_ms=WAIT_TIMEOUT_MS)
                    prepare_clean_prompt(page)
                    story_ok = process_story_folder(page, folder)
                    if story_ok:
                        processed_count += 1
                        if PERSISTENT_INBOX and PARALLEL_WORKERS <= 1:
                            persistent_stories_done += 1
                        limit_consecutive = 0
                        try:
                            relative_folder = folder.relative_to(STORIES_DIR)
                            if len(relative_folder.parts) >= 2:
                                genre_dir = STORIES_DIR / relative_folder.parts[0]
                                build_single_genre_report(genre_dir)
                        except Exception:
                            pass
                        processed_since_dialog_reset += 1
                        if PARALLEL_WORKERS <= 1 and FILES_PER_ACCOUNT > 0:
                            processed_this_account += 1
                        if (
                            PARALLEL_WORKERS <= 1
                            and FILES_PER_ACCOUNT > 0
                            and processed_this_account >= FILES_PER_ACCOUNT
                        ):
                            account_idx = (account_idx + 1) % len(accounts)
                            current_account = accounts[account_idx]
                            print(
                                f"[ACCOUNT] Лимит {FILES_PER_ACCOUNT} рассказов. "
                                f"Смена аккаунта → {current_account.email or account_idx + 1}"
                            )
                            if SEPARATE_PROFILES:
                                try:
                                    context.close()
                                except Exception:
                                    pass
                                context, page = open_account_context(playwright, current_account)
                            initialize_account_browser(page, current_account)
                            processed_this_account = 0
                            processed_since_dialog_reset = 0
                        elif processed_since_dialog_reset >= FILES_PER_DIALOG:
                            print(
                                f"[DIALOG] Новый диалог после {FILES_PER_DIALOG} рассказ(ов). "
                                f"Аккаунт: {current_account.email or account_idx + 1}."
                            )
                            while not recover_session_state(page, current_account.gem_url):
                                wait_with_status(
                                    180,
                                    "Не удалось открыть новый диалог, жду и пробую восстановить сессию",
                                )
                            ensure_stage_default_model(page)
                            processed_since_dialog_reset = 0
                    else:
                        print(f"[SKIP] Папка без обработки (SKIP): {folder.name}")
                    if PARALLEL_WORKERS > 1:
                        # В параллельном режиме обязательно убираем завершённые/неактуальные папки
                        # из локальной очереди воркера, иначе возможен бесконечный цикл по SKIP.
                        folder_done = story_ok or (folder / INFO_FILE_NAME).exists() or (pick_story_source_file(folder) is None)
                        if folder_done:
                            try:
                                pending.remove(folder)
                            except ValueError:
                                pass
                    if PARALLEL_WORKERS <= 1:
                        serial_idx += 1
                    if PARALLEL_WORKERS > 1:
                        release_story_claim(folder)
                    break
                except Exception as error:
                    error_text = str(error)
                    error_text_lower = error_text.lower()
                    print(f"[ERROR] {folder.name}: {error_text}")
                    if "target page, context or browser has been closed" in error_text_lower:
                        print("[WARN] Браузер закрыт. Переоткрываю контекст.")
                        try:
                            try:
                                context.close()
                            except Exception:
                                pass
                            context, page = open_account_context(playwright, current_account)
                            initialize_account_browser(page, current_account)
                        except Exception as reopen_error:
                            stop_reason = str(reopen_error)
                            stop_exit_code = EXIT_CODE_ERROR
                            should_stop = True
                            break
                        transient_attempt = 0
                        continue
                    if "Лимит Gem-бота" in error_text or error_text.startswith(LIMIT_PREFIX):
                        try:
                            handle_gem_limit_error(page, error_text, gem_url=current_account.gem_url)
                        except RuntimeError as limit_exc:
                            stop_reason = str(limit_exc)
                            stop_exit_code = EXIT_CODE_ERROR
                            should_stop = True
                            break
                        limit_consecutive = 0
                        transient_attempt = 0
                        continue
                    is_transient = (
                        "не удалось вставить файл" in error_text_lower
                        or "не удалось отправить запрос" in error_text_lower
                        or "ответ не завершился" in error_text_lower
                        or "временный сбой копирования ответа" in error_text_lower
                        or "временный сбой чтения ответа" in error_text_lower
                        or "маркер ответа не совпал" in error_text_lower
                        or "маркер ответа получен, но полезный текст отсутствует" in error_text_lower
                        or "locator.click: timeout" in error_text_lower
                    )
                    if is_transient:
                        if transient_attempt < len(TRANSIENT_RETRY_BACKOFF_SECONDS):
                            wait_seconds = TRANSIENT_RETRY_BACKOFF_SECONDS[transient_attempt]
                            transient_attempt += 1
                            wait_with_status(
                                wait_seconds,
                                f"Временная ошибка. Попытка {transient_attempt}/{len(TRANSIENT_RETRY_BACKOFF_SECONDS)}",
                            )
                        else:
                            wait_with_status(
                                LONG_PAUSE_SECONDS,
                                "Слишком много временных сбоев подряд",
                            )
                            transient_attempt = 0
                        if not recover_session_state(page, current_account.gem_url):
                            wait_with_status(LONG_PAUSE_SECONDS, "UI не восстановился после временной ошибки")
                        continue
                    print(
                        "[ERROR] Останавливаю обработку: пока текущий файл не завершён, следующий не запускается."
                    )
                    if PARALLEL_WORKERS > 1:
                        release_story_claim(folder)
                    stop_reason = error_text
                    stop_exit_code = EXIT_CODE_ERROR
                    should_stop = True
                    break
            if should_stop:
                break

        generate_report(STORIES_DIR, processed_count, all_story_folders)
        if PERSISTENT_INBOX and BROWSER_SESSION_ID:
            close_reason = stop_reason or "normal_exit"
            print(
                f"[BROWSER] event=closed account={persistent_log_account_index(account_idx)} browser_session_id={BROWSER_SESSION_ID} "
                f"reason={close_reason}"
            )
        if "Target page, context or browser has been closed" not in stop_reason:
            try:
                context.close()
            except Exception:
                pass

    if os.getenv("HOLD_OPEN", "0") == "1":
        prompt_user("Нажми Enter, чтобы закрыть программу...")
    return stop_exit_code


if __name__ == "__main__":
    try:
        setup_dual_logging()
        sys.exit(main())
    except Exception as unhandled_error:
        print(f"[FATAL] {unhandled_error}")
        sys.exit(EXIT_CODE_ERROR)
