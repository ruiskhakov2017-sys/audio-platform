#!/usr/bin/env python3
"""
Удаление технической шапки Literotica из уже выгруженных TXT на Google Drive (локальная папка sync).

Примеры:
  python tools/clean_drive_txt_headers.py --root "G:\\My Drive\\ContentFactory_TTS\\texts" --dry-run
  python tools/clean_drive_txt_headers.py --root "G:\\My Drive\\ContentFactory_TTS\\texts" --apply --backup
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orchestrator.text_cleaning.literotica_header import (  # noqa: E402
    literotica_header_remnant_warning,
    strip_literotica_source_header,
)


def _iter_txt_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.txt") if p.is_file())


def _process_file(path: Path, *, apply: bool, backup: bool) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned, diag = strip_literotica_source_header(raw)
    changed = cleaned != raw
    remnant = literotica_header_remnant_warning(cleaned) if changed else literotica_header_remnant_warning(raw)
    if apply and changed:
        if backup:
            bak = path.with_suffix(path.suffix + ".bak")
            if not bak.is_file():
                shutil.copy2(path, bak)
        path.write_text(cleaned, encoding="utf-8")
    return {
        "path": str(path),
        "changed": changed,
        "removed_lines": diag.get("removed_literotica_header_lines_count", 0),
        "samples": diag.get("removed_literotica_header_lines_sample", []),
        "warning": remnant,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Strip Literotica header lines from Drive TXT files.")
    ap.add_argument("--root", type=Path, required=True, help="Корень texts/ на Drive (локальный sync)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Только отчёт, без записи")
    mode.add_argument("--apply", action="store_true", help="Записать очищенные файлы")
    ap.add_argument(
        "--backup",
        action="store_true",
        help="При --apply: копия .txt.bak перед перезаписью (только если .bak ещё нет)",
    )
    args = ap.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2
    if args.apply and not args.backup:
        print("NOTE: --apply без --backup: исходники перезаписываются без копии.", flush=True)

    files = _iter_txt_files(root)
    print(f"root={root} txt_files={len(files)} mode={'apply' if args.apply else 'dry-run'}", flush=True)
    changed_n = 0
    warn_n = 0
    for p in files:
        row = _process_file(p, apply=bool(args.apply), backup=bool(args.backup))
        if row["changed"]:
            changed_n += 1
            print(
                f"{'WRITE' if args.apply else 'WOULD'} {row['path']} "
                f"removed_lines={row['removed_lines']} samples={row['samples'][:2]}",
                flush=True,
            )
        if row["warning"]:
            warn_n += 1
            print(f"WARN {row['path']}: {row['warning']}", flush=True)

    print(
        f"done at {datetime.now(tz=timezone.utc).isoformat()} "
        f"scanned={len(files)} changed={changed_n} warnings={warn_n}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
