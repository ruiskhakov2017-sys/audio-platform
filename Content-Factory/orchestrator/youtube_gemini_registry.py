"""Resolve and sync YouTube visuals Gemini bot URLs from the central registry."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


GEMINI_URL_RE = re.compile(r"^https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*$", re.IGNORECASE)
GEMINI_URL_FIND_RE = re.compile(r"https://gemini\.google\.com(?:/u/\d+)?/gem/[A-Za-z0-9][A-Za-z0-9-]*", re.IGNORECASE)
AUTHUSER_RE = re.compile(r"gemini\.google\.com/u/(\d+)/", re.IGNORECASE)
CHARACTER_STAGE_KEY = "youtube_characters"
DIRECTOR_STAGE_KEYS = ("youtube_scene_prompts", "youtube_director")


@dataclass(frozen=True)
class GeminiRegistryEntry:
    email: str
    account_index: int
    profile_path: Path
    character_url: str
    director_url: str
    app_url: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _director_module_dir(config: OrchestratorConfig) -> Path:
    rel = config.legacy_modules.get("director_2_0", "legacy/director_2_0")
    return (config.root_dir / rel).resolve()


def _registry_path(config: OrchestratorConfig) -> Path:
    return (config.root_dir / "configs" / "gemini_bots_registry.yaml").resolve()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        data: dict[str, Any] = {"gemini_bots": []}
        current: dict[str, str] | None = None
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- "):
                if current:
                    data["gemini_bots"].append(current)
                current = {}
                stripped = stripped[2:].strip()
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            if current is not None:
                current[key.strip()] = value.strip().strip("\"'")
        if current:
            data["gemini_bots"].append(current)
        return data


def _gem_id(url: str) -> str:
    text = str(url or "").strip().rstrip("/")
    marker = "/gem/"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].strip()


def _gem_url(gem_id_or_url: str, account_index: int | None = None) -> str:
    del account_index
    gem_id = _gem_id(gem_id_or_url) or str(gem_id_or_url or "").strip()
    if not gem_id:
        return ""
    # Visuals workers run in separate Chrome profiles. In that model the logged-in
    # account is the profile's /u/0, so forcing /u/N redirects to the wrong slot.
    return f"https://gemini.google.com/gem/{gem_id}"


def _authuser_from_url(url: str) -> int | None:
    match = AUTHUSER_RE.search(str(url or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _load_legacy_bot_chain(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        data = _read_json(path)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        app = str(item.get("app", "") or "").strip()
        if email:
            out.append({"email": email, "url": url, "app": app})
    return out


def _legacy_account_indexes(chain: list[dict[str, str]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for fallback_idx, item in enumerate(chain):
        email = item.get("email", "")
        idx = _authuser_from_url(item.get("app", "")) or _authuser_from_url(item.get("url", ""))
        mapping[email] = fallback_idx if idx is None else idx
    return mapping


def _load_legacy_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = _read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _registry_entries(config: OrchestratorConfig) -> tuple[Path, list[dict[str, Any]], list[str]]:
    path = _registry_path(config)
    warnings: list[str] = []
    if not path.is_file():
        return path, [], [f"missing registry: {path}"]
    data = _read_yaml(path)
    raw_bots = data.get("gemini_bots")
    if not isinstance(raw_bots, list):
        return path, [], [f"registry has no gemini_bots list: {path}"]
    bots = [item for item in raw_bots if isinstance(item, dict)]
    if not bots:
        warnings.append(f"registry gemini_bots list is empty: {path}")
    return path, bots, warnings


def _select_primary_email(
    *,
    registry_bots: list[dict[str, Any]],
    legacy_chain: list[dict[str, str]],
    legacy_config: dict[str, Any],
) -> str:
    emails = {str(item.get("email", "") or "").strip() for item in registry_bots}
    explicit = (os.getenv("YOUTUBE_VISUALS_GEMINI_EMAIL") or os.getenv("YOUTUBE_GEMINI_ACCOUNT_EMAIL") or "").strip()
    if explicit and explicit in emails:
        return explicit
    for item in legacy_chain:
        email = str(item.get("email", "") or "").strip()
        if email in emails:
            return email
    legacy_character_id = _gem_id(str(legacy_config.get("characters_gemini_url", "") or ""))
    if legacy_character_id:
        for item in registry_bots:
            if _gem_id(str(item.get(CHARACTER_STAGE_KEY, "") or "")) == legacy_character_id:
                return str(item.get("email", "") or "").strip()
    for item in registry_bots:
        email = str(item.get("email", "") or "").strip()
        if email:
            return email
    return ""


def resolve_youtube_gemini_bots(config: OrchestratorConfig) -> dict[str, Any]:
    director_dir = _director_module_dir(config)
    legacy_config_path = director_dir / "config.json"
    legacy_bots_path = director_dir / "gemini_bots.json"
    profile_path = director_dir / "user_data"
    registry_path, registry_bots, warnings = _registry_entries(config)
    legacy_config = _load_legacy_config(legacy_config_path)
    legacy_chain = _load_legacy_bot_chain(legacy_bots_path)
    account_indexes = _legacy_account_indexes(legacy_chain)
    primary_email = _select_primary_email(registry_bots=registry_bots, legacy_chain=legacy_chain, legacy_config=legacy_config)

    entries: list[GeminiRegistryEntry] = []
    for registry_idx, item in enumerate(registry_bots):
        email = str(item.get("email", "") or "").strip()
        character_raw = str(item.get(CHARACTER_STAGE_KEY, "") or "").strip()
        director_raw = ""
        for key in DIRECTOR_STAGE_KEYS:
            director_raw = str(item.get(key, "") or "").strip()
            if director_raw:
                break
        if not email:
            warnings.append("registry row without email skipped")
            continue
        if not character_raw:
            warnings.append(f"registry row {email} missing {CHARACTER_STAGE_KEY}")
            continue
        if not director_raw:
            warnings.append(f"registry row {email} missing one of {', '.join(DIRECTOR_STAGE_KEYS)}")
            continue
        account_index = account_indexes.get(email, registry_idx)
        character_url = _gem_url(character_raw, account_index)
        director_url = _gem_url(director_raw, account_index)
        if not GEMINI_URL_RE.fullmatch(character_url):
            warnings.append(f"invalid character Gem URL for {email}: {character_url}")
            continue
        if not GEMINI_URL_RE.fullmatch(director_url):
            warnings.append(f"invalid director Gem URL for {email}: {director_url}")
            continue
        entries.append(
            GeminiRegistryEntry(
                email=email,
                account_index=account_index,
                profile_path=profile_path,
                character_url=character_url,
                director_url=director_url,
                app_url="https://gemini.google.com/app",
            )
        )

    selected = next((entry for entry in entries if entry.email == primary_email), entries[0] if entries else None)
    if selected is None:
        warnings.append("no usable YouTube visuals Gemini bot entries found")
    ordered_chain = ([selected] if selected else []) + [entry for entry in entries if selected is None or entry.email != selected.email]
    allowed_ids = {_gem_id(entry.character_url) for entry in entries} | {_gem_id(entry.director_url) for entry in entries}
    legacy_urls = _legacy_urls(legacy_config_path) + _legacy_urls(legacy_bots_path)
    unknown_legacy_urls = [url for url in legacy_urls if _gem_id(url) and _gem_id(url) not in allowed_ids]

    return {
        "registry_path": str(registry_path),
        "registry_loaded": bool(registry_bots),
        "legacy_config_json_path": str(legacy_config_path),
        "legacy_gemini_bots_json_path": str(legacy_bots_path),
        "selected_character": _entry_payload(selected, "youtube_characters") if selected else {},
        "selected_director_chain": [_entry_payload(entry, "youtube_scene_prompts") for entry in ordered_chain],
        "all_registry_gem_ids": sorted(allowed_ids),
        "old_urls_found_in_active_legacy": bool(unknown_legacy_urls),
        "unknown_legacy_urls": unknown_legacy_urls,
        "warnings": warnings,
    }


def _entry_payload(entry: GeminiRegistryEntry | None, stage_key: str) -> dict[str, Any]:
    if entry is None:
        return {}
    url = entry.character_url if stage_key == "youtube_characters" else entry.director_url
    return {
        "email": entry.email,
        "account_index": entry.account_index,
        "profile_path": str(entry.profile_path),
        "gem_url": url,
        "app_url": entry.app_url,
        "source": "configs/gemini_bots_registry.yaml",
    }


def _legacy_urls(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return GEMINI_URL_FIND_RE.findall(text)


def sync_youtube_gemini_legacy_files(config: OrchestratorConfig, *, story_dir: Path | None = None, execute: bool = False) -> dict[str, Any]:
    resolved = resolve_youtube_gemini_bots(config)
    warnings = list(resolved.get("warnings") or [])
    selected_character = resolved.get("selected_character") if isinstance(resolved.get("selected_character"), dict) else {}
    director_chain = resolved.get("selected_director_chain") if isinstance(resolved.get("selected_director_chain"), list) else []
    ok = bool(resolved.get("registry_loaded")) and bool(selected_character.get("gem_url")) and bool(director_chain)
    if resolved.get("old_urls_found_in_active_legacy") and not execute:
        warnings.append("active legacy files contain Gem URLs not present in registry; execute mode will overwrite them from registry")

    changed_files: list[str] = []
    if execute and ok:
        config_path = Path(str(resolved["legacy_config_json_path"]))
        bots_path = Path(str(resolved["legacy_gemini_bots_json_path"]))
        legacy_config = _load_legacy_config(config_path)
        legacy_config["characters_gemini_url"] = str(selected_character["gem_url"])
        legacy_config["gemini_url"] = str(director_chain[0]["gem_url"])
        _write_json(config_path, legacy_config)
        changed_files.append(str(config_path))
        bot_chain_payload = [
            {
                "email": str(item.get("email", "")),
                "url": str(item.get("gem_url", "")),
                "app": str(item.get("app_url") or "https://gemini.google.com/app"),
            }
            for idx, item in enumerate(director_chain)
            if isinstance(item, dict)
        ]
        _write_json(bots_path, bot_chain_payload)
        changed_files.append(str(bots_path))
        resolved = resolve_youtube_gemini_bots(config)
        warnings = list(resolved.get("warnings") or [])

    result = {
        **resolved,
        "ok": ok and not bool(resolved.get("old_urls_found_in_active_legacy")),
        "will_sync_legacy_files": bool(execute and ok),
        "changed_files": changed_files,
        "warnings": warnings,
        "written_at": _now_iso(),
    }
    if story_dir is not None:
        logs_dir = story_dir / "logs"
        report_path = logs_dir / "youtube_gemini_bots_preflight.json"
        text_report_path = logs_dir / "youtube_gemini_bots_preflight.txt"
        _write_json(report_path, result)
        text_report_path.parent.mkdir(parents=True, exist_ok=True)
        text_report_path.write_text(_preflight_text(result), encoding="utf-8")
        result["report_path"] = str(report_path)
        result["text_report_path"] = str(text_report_path)
    return result


def _preflight_text(result: dict[str, Any]) -> str:
    selected = result.get("selected_character") if isinstance(result.get("selected_character"), dict) else {}
    chain = result.get("selected_director_chain") if isinstance(result.get("selected_director_chain"), list) else []
    lines = [
        "# YouTube Gemini bots preflight",
        "",
        "Source of truth:",
        str(result.get("registry_path", "")),
        "",
        "Character Gem:",
        f"email: {selected.get('email', '')}",
        f"profile: {selected.get('profile_path', '')}",
        f"url: {selected.get('gem_url', '')}",
        "",
        "Director chain:",
    ]
    for idx, item in enumerate(chain, start=1):
        if not isinstance(item, dict):
            continue
        lines.append(f"{idx}. {item.get('email', '')} / {item.get('profile_path', '')} / {item.get('gem_url', '')}")
    lines.extend(
        [
            "",
            f"legacy_config_json_path: {result.get('legacy_config_json_path', '')}",
            f"legacy_gemini_bots_json_path: {result.get('legacy_gemini_bots_json_path', '')}",
            f"will_sync_legacy_files: {result.get('will_sync_legacy_files', False)}",
            f"old_urls_found_in_active_legacy: {result.get('old_urls_found_in_active_legacy', False)}",
        ]
    )
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines).rstrip() + "\n"
