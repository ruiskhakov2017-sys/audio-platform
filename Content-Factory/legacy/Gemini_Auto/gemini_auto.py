import base64
import mimetypes
import msvcrt
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError, sync_playwright

_LEGACY_ROOT = Path(__file__).resolve().parent.parent
if str(_LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEGACY_ROOT))
from gemini_browser_proxy import append_chrome_proxy_args


GEMINI_URL_PATTERN = re.compile(
    r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$",
    re.IGNORECASE,
)
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

# Очередь: из GEMINI_STORIES_DIR в main(); до запуска main — заглушка для относительных путей в хелперах.
STORIES_DIR = (PROJECT_DIR / "stories").resolve()
USER_DATA_DIR = Path(os.getenv("GEMINI_USER_DATA_DIR") or str(PROJECT_DIR / "user_data")).resolve()
LOG_FILE_PATH = Path(os.getenv("GEMINI_LOG_FILE") or str(PROJECT_DIR / "run.log")).resolve()
INFO_FILE_NAME = "info.txt"
REPORT_FILE_NAME = "result_report.txt"
GENRE_REPORT_FILE_NAME = "genre_report.txt"
WAIT_TIMEOUT_MS = 180_000
SLOW_MO_MS = 0
PAUSE_MIN_SECONDS = 0
PAUSE_MAX_SECONDS = 0
REPORT_FILE_PATTERN = re.compile(r"^result_report(-\d+)?\.txt$", re.IGNORECASE)
YOUTUBE_STATUS_LINE_PATTERN = re.compile(
    r"^\s*подходит\s+для\s+youtube\s*:\s*(да|нет)\s*$",
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
# Сообщения UI/страницы про исчерпание thinking (не считаем фатальной остановкой воркера).
THINKING_LIMIT_HINTS = [
    "thinking model is",
    "thinking isn't available",
    "thinking is not available",
    "thinking temporarily",
    "thinking quota",
    "thinking limit",
    "лимит thinking",
    "лимит думающ",
    "думающая модель недоступна",
    "thinking model unavailable",
    "upgrade to continue using thinking",
]
EXIT_CODE_OK = 0
EXIT_CODE_FILE_LIMIT = 42
EXIT_CODE_ERROR = 1
# Согласован с orchestrator/phase_a_gemini_supervisor.py (ротация профиля при «мёртвой» отправке).
EXIT_CODE_SESSION_SEND_EXHAUSTED = 44
COPY_RETRIES = 3
IDLE_STABLE_ROUNDS = 4
HEARTBEAT_SECONDS = 30
# Keep retries responsive: avoid multi-minute idle hangs in worker loops.
TRANSIENT_RETRY_BACKOFF_SECONDS = [15, 30, 45, 60, 90]
LONG_PAUSE_SECONDS = int((os.getenv("GEMINI_LONG_PAUSE_SECONDS") or "180").strip() or "180")
LIMIT_PAUSE_SECONDS = int((os.getenv("GEMINI_LIMIT_PAUSE_SECONDS") or "120").strip() or "120")
PARALLEL_WORKERS = max(1, min(5, int((os.getenv("PARALLEL_WORKERS") or "1").strip() or "1")))
WORKER_INDEX = max(0, int((os.getenv("WORKER_INDEX") or "0").strip() or "0"))
GEMINI_STAGE_KEY = (os.getenv("GEMINI_STAGE_KEY") or "").strip().lower()
DYNAMIC_QUEUE_ENABLED = (os.getenv("GEMINI_DYNAMIC_QUEUE") or "1").strip() == "1"
WORKER_LOCK_FILE = ".cf_worker.lock"
LOCK_STALE_SECONDS = int((os.getenv("GEMINI_LOCK_STALE_SECONDS") or "21600").strip() or "21600")
WAIT_OVERRIDE_KEY = "w"
RESPONSE_MARKER_PREFIX = "REQUEST_MARKER"
DIALOG_RESET_EVERY_FILES = 10
# Сколько раз жмём «Отправить» / ждём старт генерации на один запрос (до RuntimeError в process_story_folder).
# Согласовано с GEMINI_MAX_SEND_START_FAILURES_PER_SESSION: сессия выходит с кодом 44 после N подряд
# ошибок «отправка не стартует» — супервизор phase_a переключает другой Chrome-профиль (другой аккаунт).
SEND_RETRIES = max(1, int((os.getenv("GEMINI_SEND_RETRIES") or "7").strip() or "7"))
REQUEST_START_TIMEOUT_MS = int((os.getenv("GEMINI_REQUEST_START_TIMEOUT_MS") or "15000").strip() or "15000")
BROWSER_RECOVERY_PAUSE_SECONDS = 20
STARTUP_RETRIES = int((os.getenv("GEMINI_STARTUP_RETRIES") or "5").strip() or "5")
# Динамическая очередь: не выходить, если все папки временно залочены другими воркерами.
QUEUE_WAIT_EMPTY_SECONDS = float((os.getenv("GEMINI_QUEUE_WAIT_EMPTY_SECONDS") or "2.0").strip() or "2.0")
QUEUE_EMPTY_SPIN_MAX = int((os.getenv("GEMINI_QUEUE_EMPTY_SPIN_MAX") or "3600").strip() or "3600")
# Подряд таймауты «ответ не завершился…» на одной папке → stub info.txt и переход к следующей
# (иначе воркер вечно крутит один рассказ).
GEMINI_MAX_RESPONSE_UI_TIMEOUTS_PER_STORY = max(
    1,
    int((os.getenv("GEMINI_MAX_RESPONSE_UI_TIMEOUTS_PER_STORY") or "8").strip() or "8"),
)
# Подряд «отправка не стартует» по сессии (типичный софт-лимит аккаунта) → exit 44, супервизор уводит профиль в cooldown.
GEMINI_MAX_SEND_START_FAILURES_PER_SESSION = max(
    1,
    int((os.getenv("GEMINI_MAX_SEND_START_FAILURES_PER_SESSION") or "7").strip() or "7"),
)

# После успешного fallback на fast не дергать thinking на каждом рассказе (анти-стопор).
_SESSION_USE_FAST_ONLY = False

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
]
MODE_MENU_TRIGGER_SELECTORS = [
    'button[aria-label*="Открыть меню выбора режима"]',
    'button[aria-label*="Open mode selection menu"]',
    'button[aria-label*="Open mode menu"]',
    'button[aria-label*="Open model selector"]',
    'button[aria-label*="Select model"]',
    'button[aria-label*="Выбрать модель"]',
    'button[aria-label*="Model selector"]',
    'button[aria-label*="Выбор режима"]',
    'button[aria-label*="Select mode"]',
    'button[aria-label*="Model mode"]',
    'button[aria-label*="режим модели"]',
    'button:has-text("Thinking")',
    'button:has-text("Думающая")',
    'button:has-text("Flash")',
    'button:has-text("Быстрая")',
]
RESPONSE_BLOCK_SELECTORS = [
    '[data-test-id="message-content"]',
    '[data-test-id*="response"]',
    "message-content",
    "model-response",
    "div.markdown",
    "article",
]


def extract_authuser_from_url(url: str) -> str:
    match = re.search(r"/u/(\d+)/", url)
    return match.group(1) if match else ""


def extract_authuser_from_current_url(url: str) -> str:
    match = re.search(r"/u/(\d+)/", url or "")
    return match.group(1) if match else ""


def has_deleted_bot_message(page: Page) -> bool:
    try:
        body_text = (page.locator("body").inner_text(timeout=2000) or "").lower()
    except Exception:
        return False
    ru_hit = "в этом чате участвовал gem-бот" in body_text and "удален" in body_text
    en_hit = "this chat used a gem that was deleted" in body_text
    return ru_hit or en_hit


class TeeStream:
    def __init__(self, console_stream, log_stream):
        self.console_stream = console_stream
        self.log_stream = log_stream

    def write(self, data: str) -> int:
        n = len(data)
        try:
            self.console_stream.write(data)
        except UnicodeEncodeError:
            enc = getattr(self.console_stream, "encoding", None) or "cp1252"
            self.console_stream.write(data.encode(enc, errors="replace").decode(enc))
        self.log_stream.write(data)
        return n

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
    time.sleep(seconds)


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


def ensure_logged_in(page: Page) -> bool:
    for attempt in range(1, 4):
        try:
            wait_for_prompt_input(page, timeout_ms=45_000)
            return True
        except TimeoutError:
            if is_login_screen_visible(page):
                print("[AUTH] Вижу реальный экран входа Google/Gemini.")
                input("[AUTH] Войди в аккаунт и нажми Enter...")
                try:
                    wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
                    return True
                except TimeoutError:
                    print("[WARN] После ручного входа поле ввода Gemini всё ещё не появилось.")
            print(f"[WARN] UI Gemini ещё не готов (попытка {attempt}/3). Обновляю страницу и жду...")
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:
                pass
            time.sleep(2)
    print("[WARN] Не удалось подтвердить готовность Gemini UI. Попробую восстановиться позже.")
    return False


def _content_factory_repo_root() -> Path | None:
    """Корень репозитория (рядом с каталогом configs/), если скрипт лежит в legacy/Gemini_Auto/."""
    candidate = PROJECT_DIR.parent.parent
    if (candidate / "configs").is_dir():
        return candidate
    return None


def _gem_url_from_configs_registry() -> str | None:
    """
    Если GEMINI_URL не задан: первый URL из configs/gemini_bots_registry.yaml
    (или .example.yaml) для ключа GEMINI_STAGE_KEY (по умолчанию general_selection).
    Так ручной запуск не спрашивает бота; оркестратор всё равно задаёт GEMINI_URL сам.
    """
    root = _content_factory_repo_root()
    if root is None:
        return None
    stage = (os.getenv("GEMINI_STAGE_KEY") or "general_selection").strip() or "general_selection"
    for fname in ("gemini_bots_registry.yaml", "gemini_bots_registry.example.yaml"):
        path = root / "configs" / fname
        if not path.is_file():
            continue
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        bots = payload.get("gemini_bots") if isinstance(payload, dict) else None
        if not isinstance(bots, list):
            continue
        for bot in bots:
            if not isinstance(bot, dict):
                continue
            url = str(bot.get(stage, "")).strip()
            if url and GEMINI_URL_PATTERN.fullmatch(url):
                print(f"[INFO] GEMINI_URL из registry: {path.name} stage={stage}", flush=True)
                return url
    return None


def resolve_session_gemini_url() -> str:
    env_url = (os.getenv("GEMINI_URL") or "").strip()
    if env_url and GEMINI_URL_PATTERN.fullmatch(env_url):
        print(f"[INFO] Использую GEMINI_URL из окружения: {env_url}")
        return env_url
    if env_url:
        raise RuntimeError(
            "GEMINI_URL задан, но не подходит под формат (один id после /gem/). "
            f"Получено: {env_url!r}"
        )
    reg_url = _gem_url_from_configs_registry()
    if reg_url:
        print(f"[INFO] Использую GEMINI_URL из configs/registry: {reg_url}")
        return reg_url
    raise RuntimeError(
        "Нет GEMINI_URL и не удалось взять ссылку из configs/gemini_bots_registry.yaml "
        "или gemini_bots_registry.example.yaml для текущего GEMINI_STAGE_KEY."
    )


def ensure_on_bot_page(page: Page, gemini_url: str) -> None:
    expected_authuser = extract_authuser_from_url(gemini_url)
    for attempt in range(1, 5):
        current_url = page.url or ""
        if not current_url.startswith(gemini_url):
            try:
                page.goto(gemini_url, wait_until="domcontentloaded")
            except Exception as error:
                print(f"[WARN] Не удалось открыть URL бота (попытка {attempt}/4): {error}")
                time.sleep(1.5)
                continue
        current_authuser = extract_authuser_from_current_url(page.url or "")
        if expected_authuser and current_authuser and current_authuser != expected_authuser:
            print(
                f"[WARN] Открылся другой слот аккаунта: ожидался /u/{expected_authuser}/, получен /u/{current_authuser}/ "
                f"(попытка {attempt}/4). Повторяю переход.",
            )
            time.sleep(1.0)
            continue
        if has_deleted_bot_message(page):
            raise RuntimeError(
                f"Gem-бот недоступен для текущей сессии ({gemini_url}): в чате указан удаленный бот."
            )
        return
    raise RuntimeError(f"Не удалось закрепиться на нужном Gem-боте: {gemini_url}")


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


def is_browser_closed_error(error_text: str) -> bool:
    """
    Обрыв CDP/WebSocket/Chrome: не считать фатальным для всего воркера — main перезапускает сессию.
    Playwright часто даёт «Connection closed while reading from the driver» на wait_for/click.
    """
    text = (error_text or "").lower()
    return (
        "target page, context or browser has been closed" in text
        or "browser has been closed" in text
        or "locator.wait_for: target closed" in text
        or "connection closed while reading from the driver" in text
        or "connection closed while writing to the driver" in text
        or "econnreset" in text
        or "websocket error" in text
        or "socket hang up" in text
        or "net::err_connection" in text
        or "protocol error" in text
        or "page crashed" in text
        or "crash" in text and "browser" in text
    )


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
        return REPORT_FILE_PATTERN.fullmatch(name) is not None

    return sorted([path for path in folder.glob("*.txt") if not is_generated_txt(path)])


def collect_story_folders(stories_dir: Path) -> list[Path]:
    print("[STATUS] Сканирую папки рассказов...")
    all_dirs = [path for path in stories_dir.rglob("*") if path.is_dir()]
    with_source = [folder for folder in all_dirs if pick_story_source_file(folder) is not None]
    with_source_set = set(with_source)
    leaf_story_folders = [folder for folder in with_source if folder not in {child.parent for child in with_source_set}]
    result = sorted(leaf_story_folders, key=lambda path: str(path.relative_to(stories_dir)).lower())
    print(f"[STATUS] Сканирование завершено. Найдено story-папок: {len(result)}")
    return result


def assign_worker_slice(story_folders: list[Path]) -> list[Path]:
    workers = max(1, PARALLEL_WORKERS)
    if workers <= 1:
        return story_folders
    idx = WORKER_INDEX % workers
    return [folder for pos, folder in enumerate(story_folders) if pos % workers == idx]


def dedupe_pending_folders_by_source_txt_name(pending: list[Path], *, stories_root: Path) -> list[Path]:
    """
    Несколько leaf-папок с одним и тем же именем исходного *.txt (копии после разных прогонов phase_a:
    …_000001, …_000002). Статический срез иначе отдаёт их разным воркерам → два аккаунта, один текст.
    Оставляем одну папку на ключ — ту же детерминированную «canonical», что и orchestrator phase_a для
    незавершённых: лексикографически минимальное имя папки (а не mtime исходника, чтобы не расходиться
    с _build_gemini_input после resume). Остальные в этом запуске не трогаем.
    """
    if len(pending) <= 1:
        return pending
    root = stories_root.resolve()
    by_key: dict[str, list[Path]] = {}
    for folder in pending:
        src = pick_story_source_file(folder)
        key = src.name.lower() if src is not None else folder.name.lower()
        by_key.setdefault(key, []).append(folder)
    out: list[Path] = []
    for key, group in sorted(by_key.items(), key=lambda kv: kv[0]):
        if len(group) == 1:
            out.extend(group)
            continue

        best = sorted(group, key=lambda p: p.name.lower())[0]
        out.append(best)
        losers = [p for p in group if p.resolve() != best.resolve()]
        try:
            rel_best = str(best.resolve().relative_to(root))
        except ValueError:
            rel_best = best.name
        print(
            f"[WARN] Несколько gemini-папок с одним исходником {key!r} — в работу только «{rel_best}», "
            f"остальные пропускаем в этом процессе: {[p.name for p in losers]}",
            flush=True,
        )
    return sorted(out, key=lambda p: str(p.relative_to(root)).lower())


def _lock_is_stale(lock_file: Path) -> bool:
    try:
        age = time.time() - lock_file.stat().st_mtime
    except OSError:
        return False
    return age > LOCK_STALE_SECONDS


def _is_nonempty_info_txt(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def basenames_with_nonempty_global_done(all_story_folders: list[Path]) -> set[str]:
    """Имена исходных *.txt (lower), для которых уже есть непустой info.txt хотя бы в одной leaf-папке."""
    done: set[str] = set()
    for folder in all_story_folders:
        if not _is_nonempty_info_txt(folder / INFO_FILE_NAME):
            continue
        src = pick_story_source_file(folder)
        if src is not None:
            done.add(src.name.lower())
    return done


def drop_pending_when_done_elsewhere(
    pending: list[Path],
    *,
    done_basenames: set[str],
    stories_root: Path,
) -> list[Path]:
    """
    Не ставить в очередь папку без info, если для того же basename исходника другая папка уже имеет
    непустой info.txt (типичный duplicate extra после resume до фикса orchestrator).
    """
    try:
        root = stories_root.resolve()
    except Exception:
        root = stories_root
    out: list[Path] = []
    for folder in pending:
        src = pick_story_source_file(folder)
        if src is None:
            out.append(folder)
            continue
        key = src.name.lower()
        if key in done_basenames:
            try:
                rel = str(folder.resolve().relative_to(root))
            except ValueError:
                rel = folder.name
            print(
                f"[WARN] Пропуск duplicate/orphan папки: для исходника {key!r} уже есть непустой "
                f"{INFO_FILE_NAME} в другой gemini-папке; эта не в очереди: {rel}",
                flush=True,
            )
            continue
        out.append(folder)
    return out


def effective_gemini_pending_folders_from_list(
    all_story_folders: list[Path],
    stories_dir: Path,
) -> list[Path]:
    """Фактическая очередь gemini_auto: без orphan-дублей и не более одной папки на basename."""
    done_bn = basenames_with_nonempty_global_done(all_story_folders)
    pending_raw = [f for f in all_story_folders if not _is_nonempty_info_txt(f / INFO_FILE_NAME)]
    pending = drop_pending_when_done_elsewhere(
        pending_raw, done_basenames=done_bn, stories_root=stories_dir
    )
    return dedupe_pending_folders_by_source_txt_name(pending, stories_root=stories_dir)


def effective_gemini_pending_folders(stories_dir: Path) -> list[Path]:
    return effective_gemini_pending_folders_from_list(collect_story_folders(stories_dir), stories_dir)


def _try_acquire_folder_lock(folder: Path) -> Path | None:
    lock_file = folder / WORKER_LOCK_FILE
    if _is_nonempty_info_txt(folder / INFO_FILE_NAME):
        return None
    for _ in range(2):
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                payload = f"pid={os.getpid()} worker={WORKER_INDEX} ts={int(time.time())}\n"
                os.write(fd, payload.encode("utf-8", errors="ignore"))
            finally:
                os.close(fd)
            return lock_file
        except FileExistsError:
            if _lock_is_stale(lock_file):
                try:
                    lock_file.unlink()
                    continue
                except Exception:
                    return None
            return None
        except Exception:
            return None
    return None


def _release_folder_lock(lock_file: Path | None) -> None:
    if lock_file is None:
        return
    try:
        lock_file.unlink()
    except Exception:
        pass


def _find_chrome_pids_by_user_data_dir(user_data_dir: Path) -> list[int]:
    """
    Find chrome.exe PIDs whose command line contains this worker profile dir.
    Works on Windows via PowerShell/CIM.
    """
    if os.name != "nt":
        return []
    marker = str(user_data_dir.resolve()).replace("\\", "\\\\")
    ps_script = (
        "$p = Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\"; "
        "$p | Where-Object { $_.CommandLine -and ($_.CommandLine -like \"*"
        + marker
        + "*\") } | ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        )
    except Exception:
        return []
    pids: list[int] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def _force_kill_leftover_chrome_for_worker(user_data_dir: Path) -> None:
    if os.name != "nt":
        return
    pids = _find_chrome_pids_by_user_data_dir(user_data_dir)
    if not pids:
        return
    print(f"[WARN] Найдены зависшие chrome-процессы воркера: {pids}. Завершаю принудительно.")
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=6,
                check=False,
            )
        except Exception:
            pass


def pick_next_folder_dynamic(stories_dir: Path) -> tuple[Path | None, Path | None]:
    pending = effective_gemini_pending_folders(stories_dir)
    for folder in pending:
        lock_file = _try_acquire_folder_lock(folder)
        if lock_file is not None:
            return folder, lock_file
    return None, None


def any_pending_story_work(stories_dir: Path) -> bool:
    """Есть ли ещё папки с исходным .txt без готового info.txt (динамическая очередь)."""
    return len(effective_gemini_pending_folders(stories_dir)) > 0


def attachment_visible(page: Page, source_file: Path, timeout_ms: int = 8_000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    file_name = source_file.name
    stem = source_file.stem
    while time.time() < deadline:
        for selector in ATTACHED_FILE_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    aria_label = (locator.first.get_attribute("aria-label") or "").lower()
                    text_value = (locator.first.inner_text() or "").lower()
                    if file_name.lower() in aria_label or file_name.lower() in text_value:
                        return True
                    if stem.lower() in aria_label or stem.lower() in text_value:
                        return True
            except Exception:
                return False
        try:
            if page.locator(f'text="{file_name}"').count() > 0:
                return True
        except Exception:
            return False
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


def has_limit_message(page: Page) -> bool:
    try:
        body_text = (page.locator("body").inner_text(timeout=1500) or "").lower()
    except Exception:
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


def wait_for_request_started(
    page: Page,
    previous_response_blocks: int,
    previous_copy_count: int,
    timeout_ms: int = REQUEST_START_TIMEOUT_MS,
) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if is_generation_in_progress(page):
            return True
        if get_response_blocks_count(page) > previous_response_blocks:
            return True
        if get_copy_buttons_count(page) > previous_copy_count:
            return True
        time.sleep(0.25)
    return False


def send_request_with_retries(
    page: Page,
    folder_name: str,
    previous_response_blocks: int,
    previous_copy_count: int,
) -> None:
    for attempt in range(1, SEND_RETRIES + 1):
        sent = click_send_button(page)
        if not sent:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
            sent = click_send_button(page, timeout_ms=5_000)
        if not sent:
            print(f"[WARN] {folder_name}: не удалось нажать отправку (попытка {attempt}/{SEND_RETRIES}).")
            continue
        if wait_for_request_started(
            page,
            previous_response_blocks=previous_response_blocks,
            previous_copy_count=previous_copy_count,
            timeout_ms=REQUEST_START_TIMEOUT_MS,
        ):
            return
        print(
            f"[WARN] {folder_name}: запрос не стартовал после отправки (попытка {attempt}/{SEND_RETRIES}), повторяю."
        )
        try:
            wait_for_prompt_input(page, timeout_ms=5_000).click()
        except Exception:
            pass
    raise RuntimeError(f"{folder_name}: не удалось отправить запрос (Gemini не стартует генерацию)")


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
    """Клик по пункту Thinking; если опция disabled — не кликаем (как в legacy/director_2_0/gemini_director.py)."""
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
                print("[INFO] Thinking в меню найден, но отключён (лимит/disabled).")
                return False
            target.click(timeout=5_000)
            return True
        except Exception:
            continue
    for role in ("menuitem", "option", "menuitemradio"):
        try:
            loc = page.locator(f'[role="{role}"]').filter(has_text=name_rx).first
            if loc.count() > 0 and loc.is_visible():
                aria_disabled = (loc.get_attribute("aria-disabled") or "").lower()
                if aria_disabled == "true":
                    print("[INFO] Thinking в меню найден, но отключён (лимит/disabled).")
                    return False
                loc.click(timeout=5_000)
                return True
        except Exception:
            continue
    return False


def _dismiss_all_overlays(page: Page) -> None:
    """Закрыть всплывающие слои перед работой с меню модели (порт с legacy/director_2_0/gemini_director.py)."""
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


def click_fast_model_option(page: Page) -> bool:
    """Выбрать быструю/flash-модель в открытом меню."""
    name_rx = re.compile(r"Быстрая|Быстрый|Fast|Flash|Quick|Мгновен", re.IGNORECASE)
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
        if "flash" in label.lower() or "gemini" in label.lower() or "thinking" in label.lower() or "думающ" in label.lower():
            return label
    return ""


def collect_visible_model_labels(page: Page) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for role in ("menuitem", "option", "menuitemradio"):
        loc = page.locator(f'[role="{role}"]')
        try:
            count = loc.count()
        except Exception:
            continue
        for i in range(min(count, 80)):
            item = loc.nth(i)
            try:
                if not item.is_visible():
                    continue
            except Exception:
                continue
            label = _visible_text(item)
            if not label:
                continue
            low = label.lower()
            if not any(token in low for token in ("flash", "gemini", "thinking", "pro", "lite", "думающ", "быстр")):
                continue
            if low in seen:
                continue
            seen.add(low)
            out.append(label)
    return out


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


def has_thinking_limit_message(page: Page) -> bool:
    try:
        body_text = (page.locator("body").inner_text(timeout=2_000) or "").lower()
    except Exception:
        return False
    return any(h.lower() in body_text for h in THINKING_LIMIT_HINTS)


def ensure_fast_mode_via_menu(page: Page) -> bool:
    """Явно переключиться на fast через меню (после лимита thinking)."""
    choice = resolve_gemini_model_alias("fast")
    for attempt in range(1, 5):
        _dismiss_all_overlays(page)
        if not open_model_mode_menu(page):
            time.sleep(0.4)
            continue
        time.sleep(0.35)
        if click_resolved_model_option(page, choice):
            time.sleep(0.45)
            if ui_label_matches_gemini_choice(current_model_label_from_toolbar(page), choice):
                return True
            return True
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.3)
    return ui_label_matches_gemini_choice(current_model_label_from_toolbar(page), choice)


def ensure_usable_gemini_model(
    page: Page,
    *,
    preferred: str = "thinking",
    fallback: str = "fast",
) -> dict[str, object]:
    """
    Central model alias resolver for all stage bots using this automation layer.
    Current Gemini UI: legacy aliases thinking/fast/default/pro resolve to 3.5 Flash.
    """
    choice = resolve_gemini_model_alias(preferred or fallback)
    result: dict[str, object] = {
        "ok": False,
        "selected_model": "unknown",
        "fallback_used": False,
        "reason": "ui_not_ready",
        "model_preference": str(preferred or "").lower(),
        "requested_alias": choice.requested_alias,
        "resolved_ui_label": choice.preferred_ui_label,
        "fallback_ui_labels": list(choice.fallback_ui_labels),
        "forbidden_labels": list(choice.forbidden_labels),
        "expected_labels": expected_model_labels(choice),
        "available_labels": [],
        "model_verified": False,
    }
    print(f"[MODEL] alias={choice.requested_alias} resolved={choice.preferred_ui_label}")
    try:
        wait_for_prompt_input(page, timeout_ms=60_000)
    except TimeoutError:
        result["reason"] = "ui_not_ready"
        print("[WARN] Поле ввода Gemini не найдено, пробую переключить режим модели.")
    time.sleep(0.45)

    current_label = current_model_label_from_toolbar(page)
    if ui_label_matches_gemini_choice(current_label, choice):
        result["ok"] = True
        result["selected_model"] = choice.preferred_ui_label
        result["selected_ui_label"] = current_label
        result["reason"] = "resolved_model_already_selected"
        result["model_verified"] = True
        print(f"[MODEL] selected={choice.preferred_ui_label} reason=already_selected label={current_label!r}")
        return result

    available_labels: list[str] = []
    for attempt in range(1, 6):
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.2)
        _dismiss_all_overlays(page)
        if not open_model_mode_menu(page):
            print(f"[WARN] Меню режима модели не открылось (попытка {attempt}/5).")
            if attempt == 3:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30_000)
                    wait_for_prompt_input(page, timeout_ms=45_000)
                except Exception:
                    pass
            time.sleep(0.6)
            continue
        time.sleep(0.45)
        available_labels = collect_visible_model_labels(page)
        result["available_labels"] = available_labels
        clicked_label = click_resolved_model_option(page, choice)
        if clicked_label:
            time.sleep(0.55)
            selected_label = current_model_label_from_toolbar(page)
            result["ok"] = True
            result["selected_model"] = choice.preferred_ui_label
            result["selected_ui_label"] = selected_label or clicked_label
            result["clicked_ui_label"] = clicked_label
            result["reason"] = "resolved_model_selected"
            result["model_verified"] = ui_label_matches_gemini_choice(selected_label, choice)
            print(
                f"[MODEL] selected={choice.preferred_ui_label} reason=resolved_model_selected "
                f"clicked={clicked_label!r} verified={result['model_verified']}"
            )
            _dismiss_all_overlays(page)
            return result
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.4)

    _dismiss_all_overlays(page)
    result["reason"] = "model_alias_not_found"
    result["available_labels"] = available_labels
    print(
        f"[MODEL] selected=unknown reason=model_alias_not_found alias={choice.requested_alias} "
        f"expected={expected_model_labels(choice)} available={available_labels}"
    )
    return result


def ensure_thinking_mode(page: Page) -> None:
    """Обёртка для совместимости: делегирует ensure_usable_gemini_model."""
    res = ensure_usable_gemini_model(page, preferred="thinking", fallback="fast")
    if res.get("ok"):
        return
    print(f"[WARN] ensure_usable_gemini_model: ok=false reason={res.get('reason')}")


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


def build_stage_output_contract(stage_key: str) -> str:
    if stage_key == "general_selection":
        return (
            "В САМОМ КОНЦЕ ответа добавь отдельной строкой строго в формате:\n"
            "Итог: ACCEPT\n"
            "или\n"
            "Итог: REJECT\n"
            "Никаких других вариантов в этой строке."
        )
    return ""


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


def _is_response_ui_timeout_message(error_text_lower: str) -> bool:
    """Таймауты ожидания готовности ответа / кнопки копирования в UI Gemini."""
    return (
        "ответ не завершился" in error_text_lower
        or "новая кнопка копирования не появилась" in error_text_lower
    )


def _is_send_never_started_transient(error_text_lower: str) -> bool:
    """UI «живой», но генерация не стартует после отправки (часто квота/лимит аккаунта)."""
    return (
        "не удалось отправить запрос" in error_text_lower
        or "gemini не стартует генерацию" in error_text_lower
        or "запрос не стартовал после отправки" in error_text_lower
    )


def write_stub_info_after_response_ui_timeouts(folder: Path, error_summary: str) -> None:
    """
    Непустой info.txt, чтобы папка не зацикливалась в очереди; для general_selection —
    явный REJECT + «подходит для youtube: нет», чтобы phase_a не ушёл в ambiguous.
    """
    info_file = folder / INFO_FILE_NAME
    detail = (error_summary or "").strip().replace("\r\n", "\n")
    if len(detail) > 800:
        detail = detail[:800].rstrip() + "…"
    body = (
        "[CF_SKIP] gemini_auto: превышен лимит подряд таймаутов UI (ответ / кнопка копирования).\n"
        f"Последняя ошибка:\n{detail}\n\n"
        "подходит для youtube: нет\n\n"
        "Итог: REJECT\n"
    )
    info_file.write_text(body, encoding="utf-8")


def process_story_folder(page: Page, folder: Path) -> bool:
    info_file = folder / INFO_FILE_NAME
    if _is_nonempty_info_txt(info_file):
        print(f"[SKIP] {folder.name}: найден непустой {INFO_FILE_NAME} — считается done")
        return False

    source_file = pick_story_source_file(folder)
    if source_file is None:
        print(f"[SKIP] {folder.name}: нет исходного .txt файла")
        return False

    # Режим модели: thinking по возможности, иначе fast без стопора (ensure_usable_gemini_model).
    model_res = ensure_usable_gemini_model(page, preferred="thinking", fallback="fast")
    if not model_res.get("ok"):
        raise RuntimeError(
            f"{folder.name}: модель Gemini недоступна ({model_res.get('reason', 'unknown')})"
        )
    response_marker = build_response_marker(folder, source_file)
    print(f"[RUN] {folder.name}: отправляю {source_file.name}")
    human_pause("перед отправкой запроса")
    wait_for_generation_idle(page, timeout_ms=WAIT_TIMEOUT_MS)
    previous_response_blocks = get_response_blocks_count(page)
    previous_copy_count = get_copy_buttons_count(page)
    if not paste_file_into_prompt(page, source_file):
        raise RuntimeError(f"{folder.name}: не удалось вставить файл {source_file.name} в чат")

    prompt_input = wait_for_prompt_input(page, timeout_ms=WAIT_TIMEOUT_MS)
    prompt_input.click()
    stage_contract = build_stage_output_contract(GEMINI_STAGE_KEY)
    user_prompt = (
        f"Обработай содержимое файла {source_file.name}.\n"
        f"Добавь отдельной строкой маркер: {RESPONSE_MARKER_PREFIX}: {response_marker}"
    )
    if stage_contract:
        user_prompt += f"\n\n{stage_contract}"
    page.keyboard.insert_text(
        user_prompt
    )
    send_request_with_retries(
        page,
        folder_name=folder.name,
        previous_response_blocks=previous_response_blocks,
        previous_copy_count=previous_copy_count,
    )

    try:
        wait_for_new_response_block(page, previous_response_blocks, timeout_ms=WAIT_TIMEOUT_MS)
    except TimeoutError as timeout_error:
        try:
            copy_button = wait_response_ready(page, previous_copy_count, timeout_ms=12_000)
            if not click_copy_button_resilient(page, copy_button):
                raise timeout_error
        except Exception:
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

    validated_text = extract_validated_response(response_text, response_marker)
    info_output = build_info_with_genre(folder, validated_text)
    info_file.write_text(info_output, encoding="utf-8")
    print(f"[DONE] {folder.name}: сохранён {INFO_FILE_NAME}")
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


def print_gemini_worker_session_stats(
    *,
    processed_count: int,
    stop_exit_code: int,
    stop_reason: str,
) -> None:
    """Сводка одного процесса gemini_auto (один подъём браузера / один «слот» супервизора)."""
    slot = WORKER_INDEX % max(1, PARALLEL_WORKERS)
    log_hint = (os.getenv("GEMINI_LOG_FILE") or "").strip()
    print(
        "[STATS] gemini_worker_session "
        f"WORKER_INDEX={WORKER_INDEX} parallel_slot={slot}/{PARALLEL_WORKERS} "
        f"user_data_dir={USER_DATA_DIR.name} "
        f"stories_saved_this_run={processed_count} "
        f"exit_code={stop_exit_code}"
        + (f" note={stop_reason[:280]!r}" if (stop_reason or "").strip() else ""),
        flush=True,
    )
    if log_hint:
        print(f"[STATS] log_file={log_hint}", flush=True)
    print(
        "[STATS] resume_hint: очередь = папки с исходным .txt без непустого info.txt в GEMINI_STORIES_DIR; "
        "уже сохранённые не трогаются — просто снова запусти phase-a / site-flow с тем же run и --resume.",
        flush=True,
    )


def main() -> int:
    global STORIES_DIR, PARALLEL_WORKERS, WORKER_INDEX, DYNAMIC_QUEUE_ENABLED
    raw_sd = (os.getenv("GEMINI_STORIES_DIR") or "").strip()
    if raw_sd:
        STORIES_DIR = Path(raw_sd).resolve()
    else:
        STORIES_DIR = (PROJECT_DIR / "stories").resolve()
        print(f"[INFO] GEMINI_STORIES_DIR не задан — очередь по умолчанию: {STORIES_DIR}", flush=True)
    STORIES_DIR.mkdir(parents=True, exist_ok=True)

    # Не полагаться только на значения, вычисленные при import: дочерний процесс phase-a
    # всегда задаёт env, но порядок загрузки/обвязки на Windows давал рассинхрон и двойной dynamic.
    PARALLEL_WORKERS = max(1, min(5, int((os.getenv("PARALLEL_WORKERS") or "1").strip() or "1")))
    WORKER_INDEX = max(0, int((os.getenv("WORKER_INDEX") or "0").strip() or "0"))
    DYNAMIC_QUEUE_ENABLED = (os.getenv("GEMINI_DYNAMIC_QUEUE") or "1").strip() == "1"

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_story_folders = collect_story_folders(STORIES_DIR)
    pending_all = effective_gemini_pending_folders_from_list(all_story_folders, STORIES_DIR)
    pending = assign_worker_slice(pending_all)
    # При PARALLEL_WORKERS>1 dynamic-queue даёт двум Chrome один и тот же pending; статический срез — раздельные папки.
    # Явно: GEMINI_FORCE_DYNAMIC_QUEUE=1 чтобы снова включить dynamic при нескольких воркерах (экспертный режим).
    force_dynamic_multi = (os.getenv("GEMINI_FORCE_DYNAMIC_QUEUE") or "").strip() == "1"
    dynamic_mode = bool(force_dynamic_multi and DYNAMIC_QUEUE_ENABLED and PARALLEL_WORKERS > 1)
    top_level_dirs = sorted([item for item in STORIES_DIR.iterdir() if item.is_dir()])

    print(f"[INFO] user_data_dir: {USER_DATA_DIR}")
    print(f"[INFO] stories: {STORIES_DIR}")
    print(f"[INFO] parallel workers: {PARALLEL_WORKERS} | worker index: {WORKER_INDEX % max(1, PARALLEL_WORKERS)}")
    print(f"[INFO] worker distribution mode: {'dynamic_queue' if dynamic_mode else 'static_slice'}")
    print(f"[INFO] Найдено верхнеуровневых папок: {len(top_level_dirs)}")
    print(f"[INFO] Найдено папок с историями: {len(all_story_folders)}")
    print(f"[INFO] Требуют обработки (все): {len(pending_all)}")
    if dynamic_mode:
        print("[INFO] Требуют обработки (этот воркер): dynamic pull from shared queue")
    else:
        print(f"[INFO] Требуют обработки (этот воркер): {len(pending)}")

    gemini_url = resolve_session_gemini_url()
    print(f"[INFO] Gem-бот сессии: {gemini_url}")

    processed_count = 0
    stop_reason = ""
    stop_exit_code = EXIT_CODE_OK

    if not pending_all:
        print(
            "[INFO] Очередь gemini_auto пуста: у всех папок с исходником уже есть непустой info.txt; браузер не запускаю.",
            flush=True,
        )
        print_gemini_worker_session_stats(processed_count=0, stop_exit_code=EXIT_CODE_OK, stop_reason="queue_empty")
        return EXIT_CODE_OK

    def launch_browser_session(playwright_obj) -> tuple[object, Page]:
        context_obj = playwright_obj.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            channel="chrome",
            headless=False,
            slow_mo=SLOW_MO_MS,
            viewport=None,
            args=append_chrome_proxy_args(["--disable-blink-features=AutomationControlled"]),
        )
        page_obj = context_obj.pages[0] if context_obj.pages else context_obj.new_page()
        context_obj.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://gemini.google.com")
        ensure_on_bot_page(page_obj, gemini_url)
        startup_auth_attempt = 0
        while not ensure_logged_in(page_obj):
            startup_auth_attempt += 1
            wait_with_status(180, f"UI Gemini не готов на старте (попытка {startup_auth_attempt}), жду и пробую снова")
            try:
                page_obj.goto(gemini_url, wait_until="domcontentloaded")
            except Exception:
                pass
        ensure_on_bot_page(page_obj, gemini_url)
        ensure_thinking_mode(page_obj)
        return context_obj, page_obj

    _playwright_cm = sync_playwright()
    playwright = _playwright_cm.__enter__()
    try:
        context = None
        page = None
        startup_attempt = 0
        while True:
            try:
                context, page = launch_browser_session(playwright)
                break
            except Exception as startup_error:
                startup_attempt += 1
                print(f"[WARN] Не удалось поднять сессию Gemini (попытка {startup_attempt}/{STARTUP_RETRIES}): {startup_error}")
                try:
                    if context is not None:
                        context.close()
                except Exception:
                    pass
                context = None
                page = None
                if startup_attempt >= STARTUP_RETRIES:
                    stop_reason = f"startup_failed_after_retries: {startup_error}"
                    stop_exit_code = EXIT_CODE_ERROR
                    print(f"[FATAL] {stop_reason}")
                    _force_kill_leftover_chrome_for_worker(USER_DATA_DIR)
                    print_gemini_worker_session_stats(
                        processed_count=processed_count,
                        stop_exit_code=stop_exit_code,
                        stop_reason=stop_reason,
                    )
                    return stop_exit_code
                wait_with_status(
                    BROWSER_RECOVERY_PAUSE_SECONDS,
                    "Стартовая сессия не поднялась, жду перед повторным запуском браузера",
                )

        should_stop = False
        send_start_failures_session = 0
        processed_since_dialog_reset = 0
        static_iter = iter(pending)
        queue_idle_spins = 0
        while True:
            lock_file: Path | None = None
            if dynamic_mode:
                folder, lock_file = pick_next_folder_dynamic(STORIES_DIR)
                if folder is None:
                    if not any_pending_story_work(STORIES_DIR):
                        break
                    queue_idle_spins += 1
                    if queue_idle_spins > QUEUE_EMPTY_SPIN_MAX:
                        print(
                            f"[WARN] Динамическая очередь: слишком долго нет свободных папок "
                            f"(spins>{QUEUE_EMPTY_SPIN_MAX}), выхожу."
                        )
                        break
                    wait_with_status(
                        max(1, int(QUEUE_WAIT_EMPTY_SECONDS)),
                        "Все папки заняты другими воркерами, жду освобождения лока…",
                    )
                    continue
                queue_idle_spins = 0
            else:
                folder = next(static_iter, None)
                if folder is None:
                    break
            transient_attempt = 0
            response_ui_timeouts_on_folder = 0
            while True:
                try:
                    relative_folder = str(folder.relative_to(STORIES_DIR))
                    print(f"[STATUS] Обрабатываю: {relative_folder}")
                    wait_for_generation_idle(page, timeout_ms=WAIT_TIMEOUT_MS)
                    prepare_clean_prompt(page)
                    if process_story_folder(page, folder):
                        processed_count += 1
                        send_start_failures_session = 0
                        try:
                            relative_folder = folder.relative_to(STORIES_DIR)
                            if len(relative_folder.parts) >= 2:
                                genre_dir = STORIES_DIR / relative_folder.parts[0]
                                build_single_genre_report(genre_dir)
                        except Exception:
                            pass
                        processed_since_dialog_reset += 1
                        if processed_since_dialog_reset >= DIALOG_RESET_EVERY_FILES:
                            print(
                                f"[STATUS] Достигнут лимит {DIALOG_RESET_EVERY_FILES} файлов в диалоге. Перехожу по ссылке бота и начинаю новый диалог."
                            )
                            while not recover_session_state(page, gemini_url):
                                wait_with_status(
                                    180,
                                    "Не удалось сразу открыть новый диалог, жду и пробую восстановить сессию"
                                )
                            ensure_thinking_mode(page)
                            processed_since_dialog_reset = 0
                    break
                except Exception as error:
                    error_text = str(error)
                    error_text_lower = error_text.lower()
                    print(f"[ERROR] {folder.name}: {error_text}")
                    if is_browser_closed_error(error_text):
                        print("[WARN] Обнаружено закрытие browser/context/page. Перезапускаю сессию воркера.")
                        try:
                            context.close()
                        except Exception:
                            pass
                        wait_with_status(
                            BROWSER_RECOVERY_PAUSE_SECONDS,
                            "Краткая пауза перед перезапуском браузерной сессии",
                        )
                        try:
                            context, page = launch_browser_session(playwright)
                            transient_attempt = 0
                            send_start_failures_session = 0
                            continue
                        except Exception as relaunch_error:
                            print(f"[WARN] Перезапуск сессии не удался: {relaunch_error}")
                            stop_reason = str(relaunch_error)
                            stop_exit_code = EXIT_CODE_ERROR
                            should_stop = True
                            break
                    if "Лимит Gem-бота" in error_text:
                        wait_with_status(
                            LIMIT_PAUSE_SECONDS,
                            f"Обнаружен лимит Gem-бота, жду {LIMIT_PAUSE_SECONDS} сек перед повтором",
                        )
                        if not recover_session_state(page, gemini_url):
                            wait_with_status(
                                LIMIT_PAUSE_SECONDS,
                                f"UI Gemini не восстановился после лимита, жду {LIMIT_PAUSE_SECONDS} сек и пробую снова",
                            )
                        transient_attempt = 0
                        send_start_failures_session = 0
                        continue
                    is_transient = (
                        "не удалось вставить файл" in error_text_lower
                        or "не удалось отправить файл" in error_text_lower
                        or "не удалось отправить запрос" in error_text_lower
                        or "ответ не завершился" in error_text_lower
                        or "временный сбой копирования ответа" in error_text_lower
                        or "временный сбой чтения ответа" in error_text_lower
                        or "маркер ответа не совпал" in error_text_lower
                        or "маркер ответа получен, но полезный текст отсутствует" in error_text_lower
                        or "locator.click: timeout" in error_text_lower
                    )
                    if is_transient:
                        if _is_response_ui_timeout_message(error_text_lower):
                            response_ui_timeouts_on_folder += 1
                            if response_ui_timeouts_on_folder >= GEMINI_MAX_RESPONSE_UI_TIMEOUTS_PER_STORY:
                                print(
                                    f"[SKIP] {folder.name}: {response_ui_timeouts_on_folder} подряд таймаутов UI ответа "
                                    f"(лимит {GEMINI_MAX_RESPONSE_UI_TIMEOUTS_PER_STORY}) — записываю stub {INFO_FILE_NAME} и перехожу к следующей истории."
                                )
                                write_stub_info_after_response_ui_timeouts(folder, error_text)
                                transient_attempt = 0
                                response_ui_timeouts_on_folder = 0
                                break
                        else:
                            response_ui_timeouts_on_folder = 0
                        if _is_send_never_started_transient(error_text_lower):
                            send_start_failures_session += 1
                            print(
                                f"[WARN] streak_send_not_started={send_start_failures_session}/"
                                f"{GEMINI_MAX_SEND_START_FAILURES_PER_SESSION} story={folder.name}",
                                flush=True,
                            )
                            if send_start_failures_session >= GEMINI_MAX_SEND_START_FAILURES_PER_SESSION:
                                print(
                                    f"[FATAL] Подряд {send_start_failures_session} сбоев «отправка не стартует» "
                                    f"(лимит {GEMINI_MAX_SEND_START_FAILURES_PER_SESSION}) — похоже на исчерпание/лимит аккаунта. "
                                    f"Завершаю процесс (код {EXIT_CODE_SESSION_SEND_EXHAUSTED}), супервизор переключит другой профиль."
                                )
                                should_stop = True
                                stop_exit_code = EXIT_CODE_SESSION_SEND_EXHAUSTED
                                break
                        else:
                            # Только для transient БЕЗ «отправка не стартует»: сбрасываем streak квоты отправки.
                            # Не цеплять к «if send_start…>=»: иначе при send-fail + streak<7 ветка elif сбрасывала бы счётчик.
                            if not _is_response_ui_timeout_message(error_text_lower):
                                send_start_failures_session = 0
                        if transient_attempt < len(TRANSIENT_RETRY_BACKOFF_SECONDS):
                            wait_seconds = TRANSIENT_RETRY_BACKOFF_SECONDS[transient_attempt]
                            transient_attempt += 1
                            wait_with_status(
                                wait_seconds,
                                f"Временная ошибка. Попытка {transient_attempt}/{len(TRANSIENT_RETRY_BACKOFF_SECONDS)}"
                            )
                        else:
                            wait_with_status(LONG_PAUSE_SECONDS, "Слишком много временных сбоев подряд, короткая восстановительная пауза")
                            transient_attempt = 0
                        if not recover_session_state(page, gemini_url):
                            wait_with_status(LONG_PAUSE_SECONDS, "UI Gemini не восстановился после временной ошибки")
                        continue
                    print(
                        "[ERROR] Останавливаю обработку: пока текущий файл не завершён и не сохранён, следующий не запускается."
                    )
                    stop_reason = error_text
                    stop_exit_code = EXIT_CODE_ERROR
                    should_stop = True
                    break
            if should_stop:
                _release_folder_lock(lock_file)
                break
            _release_folder_lock(lock_file)

        generate_report(STORIES_DIR, processed_count, all_story_folders)
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        _force_kill_leftover_chrome_for_worker(USER_DATA_DIR)
    finally:
        try:
            _playwright_cm.__exit__(*sys.exc_info())
        except Exception as _pw_close_err:
            if is_browser_closed_error(str(_pw_close_err)):
                print(
                    f"[WARN] Ошибка при закрытии Playwright (игнорирую): {_pw_close_err}",
                    flush=True,
                )
            else:
                raise

    if os.getenv("HOLD_OPEN", "0") == "1":
        input("Нажми Enter, чтобы закрыть программу...")
    print_gemini_worker_session_stats(
        processed_count=processed_count,
        stop_exit_code=stop_exit_code,
        stop_reason=stop_reason,
    )
    return stop_exit_code


if __name__ == "__main__":
    try:
        setup_dual_logging()
        sys.exit(main())
    except Exception as unhandled_error:
        print(f"[FATAL] {unhandled_error}")
        sys.exit(EXIT_CODE_ERROR)
