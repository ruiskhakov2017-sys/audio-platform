"""
Интерактивный выбор папки запуска из Запуски/ (без silent latest-by-mtime).

Используется Content-Factory-Запуск.bat для Continue / Status / Open folder.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import F_MANIFEST, human_zapuski_root, read_json
from orchestrator.human_launch_lifecycle import (
    _recovery_queue_map_path,
    recovery_queue_resume_counters,
    refresh_launch_status_file,
)


def is_smoke_or_test_launch_name(name: str) -> bool:
    """SMOKE / TEST в имени папки — не считать production resume по умолчанию."""
    u = (name or "").upper()
    if "SMOKE" in u or "__SMOKE__" in u:
        return True
    # не ловим подстроку TEST внутри CONTEST и т.п.
    return bool(re.search(r"(^|[^A-Z0-9])TEST([^A-Z0-9]|$)", u))


def _sort_tier(is_recovery: bool, is_smoke: bool) -> int:
    if is_recovery and not is_smoke:
        return 0
    if not is_recovery and not is_smoke:
        return 1
    if is_recovery and is_smoke:
        return 2
    return 3


def build_site_launch_pick_rows(config: OrchestratorConfig) -> list[dict[str, Any]]:
    root = human_zapuski_root(config.root_dir)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        mf = child / F_MANIFEST
        if not mf.is_file():
            continue
        manifest = read_json(mf) or {}
        name = child.name
        sm = is_smoke_or_test_launch_name(name)
        qm_path = _recovery_queue_map_path(child, manifest)
        is_recovery = qm_path is not None
        ls = refresh_launch_status_file(child)
        can_resume = bool(ls.get("can_resume"))
        status = str(ls.get("status", ""))
        summary_bits: list[str] = []
        if is_recovery and qm_path is not None:
            data = read_json(qm_path) or {}
            raw_items = data.get("items") if isinstance(data.get("items"), list) else []
            items = [x for x in raw_items if isinstance(x, dict)]
            rc = recovery_queue_resume_counters(items)
            summary_bits.append(f"pending_to_process={rc['pending_to_process']}")
            summary_bits.append(f"already_done={rc['already_done']}")
            if int(rc.get("invalid_or_stub", 0) or 0) > 0:
                summary_bits.append(f"invalid_or_stub={rc['invalid_or_stub']}")
            kind = "recovery"
        else:
            kind = "smoke/test" if sm else "site"
            total = int(ls.get("total_stories", 0) or 0)
            failed = int(ls.get("failed", 0) or 0)
            completed = int(ls.get("completed_stories", 0) or 0)
            summary_bits.append(f"stories_total={total}")
            summary_bits.append(f"completed={completed}")
            summary_bits.append(f"failed={failed}")
        row = {
            "name": name,
            "path": child,
            "kind": kind,
            "is_smoke": sm,
            "is_recovery": is_recovery,
            "can_resume": can_resume,
            "launch_status": status,
            "summary": " | ".join(summary_bits),
            "sort_tier": _sort_tier(is_recovery, sm),
        }
        rows.append(row)
    rows.sort(key=lambda r: (int(r["sort_tier"]), str(r["name"]).lower()))
    return rows


def print_site_launch_pick_menu(rows: list[dict[str, Any]]) -> None:
    print("=== Доступные Site launch (Запуски/ с manifest.json) ===", flush=True)
    print("Выбор номера: production resume — не smoke/test по умолчанию.", flush=True)
    smoke_names = [str(r["name"]) for r in rows if r["is_smoke"]]
    if smoke_names:
        print(
            "[WARN] В списке есть тестовые папки (SMOKE/TEST в имени). "
            "Не использовать как production resume: "
            + ", ".join(smoke_names),
            flush=True,
        )
    print("", flush=True)
    for i, r in enumerate(rows, start=1):
        smoke_flag = "smoke/test" if r["is_smoke"] else r["kind"]
        cr = "True" if r["can_resume"] else "False"
        line = (
            f"[{i}] {r['name']} | {smoke_flag} | can_resume={cr} "
            f"| status={r['launch_status']} | {r['summary']}"
        )
        print(line, flush=True)
    print("", flush=True)


def pick_site_launch_interactive(config: OrchestratorConfig, *, out_file: Path | None) -> tuple[int, str | None]:
    """
    Печатает меню; при out_file — пишет мини-.cmd с одной строкой set "LAUNCH_NAME=..." (ASCII) для call из BAT.

    Returns:
        (exit_code, chosen_name or None) — 0 успех, 2 отмена/ошибка.
    """
    rows = build_site_launch_pick_rows(config)
    if not rows:
        print("[ERROR] Нет папок Запуски/*/manifest.json.", flush=True)
        return 2, None
    print_site_launch_pick_menu(rows)
    while True:
        try:
            raw = input(f"Enter launch number 1-{len(rows)} (empty=cancel): ").strip()
        except EOFError:
            print("[ERROR] EOF — отмена.", flush=True)
            return 2, None
        if not raw:
            print("[INFO] Отмена.", flush=True)
            return 2, None
        try:
            n = int(raw)
        except ValueError:
            print("[WARN] Введите целое число.", flush=True)
            continue
        if n < 1 or n > len(rows):
            print(f"[WARN] Допустимо 1..{len(rows)}.", flush=True)
            continue
        chosen = rows[n - 1]
        if chosen["is_smoke"]:
            print(
                "[WARN] Это тестовый запуск (SMOKE/TEST в имени). Не использовать как production resume.",
                flush=True,
            )
        name = str(chosen["name"]).strip()
        if out_file is not None:
            out_path = Path(out_file).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # BAT: call этого файла задаёт LAUNCH_NAME без set/p и без for/f (eol «;», UTF-8, chcp).
            safe = re.sub(r'[\r\n\x00"^&<>|\t]', "_", name).replace("%", "_")
            if not safe:
                print("[ERROR] Пустое имя после санитизации для .cmd.", flush=True)
                return 2, None
            out_path.write_text(f'@set "LAUNCH_NAME={safe}"\r\n', encoding="ascii", errors="replace")
        print(f"[OK] Выбран launch: {name}", flush=True)
        return 0, name


def run_pick_site_launch_cli(config: OrchestratorConfig, *, out_file: Path | None) -> int:
    code, _name = pick_site_launch_interactive(config, out_file=out_file)
    return int(code)
