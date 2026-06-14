"""Mode 1 — Characters: sends story to a Gemini Gem and extracts style + character anchors.

Flow:
  1. Open the Characters Gem (characters_gemini_url from config.json).
  2. Attach full .txt story file.
  3. Send extraction prompt → JSON (в т.ч. global_consistency_suffix для хвоста каждого кадра).
  4. Save normalized JSON (or plain text fallback) to <story>/characters.txt.

Reuses browser automation helpers from gemini_director.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

_LEGACY_ROOT = Path(__file__).resolve().parents[1]
if str(_LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEGACY_ROOT))
from gemini_browser_proxy import append_chrome_proxy_args

from analyzer import list_story_leaf_folders_with_text
from config import (
    CHARACTERS_BASENAME,
    CHARACTERS_GEMINI_URL,
    STORIES_DIR,
    USER_DATA_DIR,
)
from characters_payload import normalize_characters_response_for_disk
from gemini_director import (
    GEMINI_URL_PATTERN,
    ensure_logged_in,
    ensure_thinking_mode,
    resilient_gemini_call,
    recover_after_error,
    send_file_and_read,
)
from log import get_logger

logger = get_logger(__name__)
_AUTHUSER_RE = re.compile(r"gemini\.google\.com/u/(\d+)/", re.IGNORECASE)

CHARACTERS_PROMPT = (
    "I am sending you the full story text as a file.\n\n"
    "Analyze the story using your configured Gem instructions.\n\n"
    "Return only the final JSON object required by this Gem.\n\n"
    "Do not add Markdown, explanations, comments, or any text before or after the JSON.\n\n"
    "All style selection rules, character anchor rules, schema rules, and formatting rules must come from the configured Gem instructions, not from this message."
)


def resolve_characters_url() -> str:
    # Characters mode is pinned to config URL for stable account/bot routing.
    url = (CHARACTERS_GEMINI_URL or "").strip()
    if GEMINI_URL_PATTERN.fullmatch(url):
        logger.info("Characters Gem URL (pinned from config): %s", url)
        return url
    raise RuntimeError(f"Invalid characters_gemini_url in config: {url}")


def _auth_hub_from_gem_url(gem_url: str) -> str | None:
    """Extract /u/N/app hub from gem URL for reliable account switch."""
    m = _AUTHUSER_RE.search((gem_url or "").strip())
    if not m:
        return None
    return f"https://gemini.google.com/u/{m.group(1)}/app"


def _authuser_from_url(url: str) -> int | None:
    m = _AUTHUSER_RE.search((url or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _open_characters_gem_with_account_switch(page, gem_url: str) -> None:
    """
    Open account hub first, then gem URL.
    This stabilizes account selection when many Google accounts are logged in.
    """
    hub = _auth_hub_from_gem_url(gem_url)
    target_u = _authuser_from_url(gem_url)
    if target_u is None:
        page.goto(gem_url, wait_until="domcontentloaded")
        return

    last_url = ""
    for attempt in range(1, 6):
        if hub:
            try:
                page.goto(hub, wait_until="domcontentloaded")
                time.sleep(1.2)
            except Exception as e:
                logger.warning("Account hub open failed (%s): %s", hub, e)
        page.goto(gem_url, wait_until="domcontentloaded")
        time.sleep(0.9)
        last_url = page.url or ""
        current_u = _authuser_from_url(last_url)
        if current_u == target_u:
            return
        logger.warning(
            "Wrong account slot after switch attempt %d/5: need /u/%d/, got /u/%s/ (%s)",
            attempt,
            target_u,
            "?" if current_u is None else str(current_u),
            last_url,
        )
    raise RuntimeError(
        f"Could not switch to target account slot /u/{target_u}/ for Characters Gem. Last URL: {last_url}"
    )


def list_stories_missing_characters(root: Path = STORIES_DIR) -> list[Path]:
    return [f for f in list_story_leaf_folders_with_text(root) if not (f / CHARACTERS_BASENAME).exists()]


def _extract_characters_one_story(
    ctx,
    page_holder: list,
    gemini_url: str,
    story_folder: Path,
) -> Path:
    from analyzer import find_story_files

    txt_path, _ = find_story_files(story_folder)
    if not txt_path:
        raise FileNotFoundError(f"No .txt found in {story_folder}")

    story_dir = txt_path.parent
    out_path = story_dir / CHARACTERS_BASENAME

    logger.info("Attaching %s + characters prompt...", txt_path.name)

    response = resilient_gemini_call(
        ctx,
        page_holder,
        [gemini_url],
        "Characters extraction",
        lambda p: send_file_and_read(p, txt_path, CHARACTERS_PROMPT),
    )

    out_path.write_text(normalize_characters_response_for_disk(response), encoding="utf-8")
    logger.info("Saved characters → %s", out_path)
    from gemini_persistent_runtime import write_orchestrator_processed_marker

    write_orchestrator_processed_marker(
        story_dir,
        clean_file=out_path,
        stage="youtube_characters",
    )
    return out_path


def _run_characters_batch(ctx, page_holder, gemini_url: str, pending: list[Path]) -> Path | None:
    last_out: Path | None = None
    requests_in_dialog = 0
    done = 0
    failed = 0
    for si, story_folder in enumerate(pending):
        print(
            f"[characters-batch] START {si + 1}/{len(pending)} story={story_folder.name} "
            f"dialog_request={requests_in_dialog + 1 if requests_in_dialog < 5 else 1}/5",
            flush=True,
        )
        logger.info(
            "========== Story %d / %d: %s ==========",
            si + 1,
            len(pending),
            story_folder,
        )
        if requests_in_dialog == 0:
            logger.info("Characters: opening fresh Gemini dialog for next batch of up to 5 stories.")
            _open_characters_gem_with_account_switch(page_holder[0], gemini_url)
        elif requests_in_dialog >= 5:
            logger.info("Characters: 5 requests reached; reloading Gemini and starting a fresh dialog.")
            try:
                page_holder[0].reload(wait_until="domcontentloaded")
                time.sleep(1.5)
            except Exception as exc:
                logger.warning("Characters reload before fresh dialog failed: %s", exc)
            _open_characters_gem_with_account_switch(page_holder[0], gemini_url)
            requests_in_dialog = 0
        while not ensure_logged_in(page_holder[0]):
            logger.warning("Gemini UI not ready, retrying in 60s...")
            time.sleep(60)
            try:
                _open_characters_gem_with_account_switch(page_holder[0], gemini_url)
            except Exception:
                pass
        ensure_thinking_mode(page_holder[0])
        try:
            last_out = _extract_characters_one_story(ctx, page_holder, gemini_url, story_folder)
            done += 1
            requests_in_dialog += 1
            print(
                f"[characters-batch] DONE {si + 1}/{len(pending)} story={story_folder.name} "
                f"done={done} failed={failed} remaining={len(pending) - si - 1}",
                flush=True,
            )
        except Exception as exc:
            failed += 1
            requests_in_dialog += 1
            print(
                f"[characters-batch] FAILED {si + 1}/{len(pending)} story={story_folder.name} "
                f"reason={type(exc).__name__}: {exc} done={done} failed={failed} remaining={len(pending) - si - 1}",
                flush=True,
            )
            logger.exception("Characters story failed (%s): %s", story_folder.name, exc)
    return last_out


def run_characters(folder: Path | None = None) -> Path | None:
    """
    Mode 1: извлечение стиля + якорей персонажей (JSON) → characters.txt.

    Пропускает папки, где characters.txt уже есть.
    При GEMINI_PERSISTENT_INBOX=1 и folder=None — persistent inbox (browser reuse).
    """
    from gemini_persistent_runtime import PERSISTENT_INBOX, run_persistent_inbox_loop

    explicit_folder = folder is not None
    if explicit_folder:
        story_folder = Path(folder).resolve()
        existing = story_folder / CHARACTERS_BASENAME
        if existing.exists():
            logger.info("Skip %s: %s already exists", story_folder.name, CHARACTERS_BASENAME)
            return existing
        pending: list[Path] = [story_folder]
    elif PERSISTENT_INBOX:
        from gemini_persistent_runtime import list_stories_needing_orchestrator_output

        pending = list_stories_needing_orchestrator_output(STORIES_DIR, CHARACTERS_BASENAME)
    else:
        pending = list_stories_missing_characters(STORIES_DIR)
        all_stories = list_story_leaf_folders_with_text(STORIES_DIR)
        skipped = len(all_stories) - len(pending)
        if skipped > 0:
            logger.info("Skipped %d story folder(s) that already have %s", skipped, CHARACTERS_BASENAME)

    if not pending and not PERSISTENT_INBOX:
        logger.info("Nothing to do: all stories already have %s.", CHARACTERS_BASENAME)
        return None

    if pending and not PERSISTENT_INBOX:
        logger.info(
            "Characters queue: %d story/stories: %s",
            len(pending),
            ", ".join(p.name for p in pending),
        )

    gemini_url = resolve_characters_url()
    logger.info("Characters Gem URL: %s", gemini_url)

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    last_out: Path | None = None

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            channel="chrome",
            headless=False,
            viewport=None,
            args=append_chrome_proxy_args(["--disable-blink-features=AutomationControlled"]),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page_holder = [page]
        ctx.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin="https://gemini.google.com",
        )

        if PERSISTENT_INBOX and not explicit_folder:
            def _process_one(story_folder: Path) -> bool:
                nonlocal last_out
                try:
                    _open_characters_gem_with_account_switch(page_holder[0], gemini_url)
                    while not ensure_logged_in(page_holder[0]):
                        logger.warning("Gemini UI not ready, retrying in 60s...")
                        time.sleep(60)
                        try:
                            _open_characters_gem_with_account_switch(page_holder[0], gemini_url)
                        except Exception:
                            pass
                    ensure_thinking_mode(page_holder[0])
                    last_out = _extract_characters_one_story(ctx, page_holder, gemini_url, story_folder)
                    return last_out is not None and last_out.is_file()
                except Exception as exc:
                    logger.exception("Characters story failed (%s): %s", story_folder.name, exc)
                    return False

            run_persistent_inbox_loop(
                refresh_pending=lambda: list_stories_needing_orchestrator_output(STORIES_DIR, CHARACTERS_BASENAME),
                process_folder=_process_one,
                stage_label="youtube_characters",
            )
        else:
            last_out = _run_characters_batch(ctx, page_holder, gemini_url, pending)

        try:
            ctx.close()
        except Exception:
            pass

    logger.info("Characters finished. Last output: %s", last_out)
    return last_out


if __name__ == "__main__":
    run_characters(None)
