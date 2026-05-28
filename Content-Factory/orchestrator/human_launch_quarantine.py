"""
Карантин старых smoke/test артефактов: dry-run или перенос в Запуски/_Карантин_старых_запусков/<ts>/.
Ничего не удаляет.
"""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import human_zapuski_root

QUARANTINE_DIR = "_Карантин_старых_запусков"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_smoke_test_name(name: str) -> bool:
    u = name.upper()
    return u.startswith("SMOKE") or u.startswith("TEST") or u.startswith("CLEAN_E2E_TEST")


def _collect_quarantine_candidates(
    config: OrchestratorConfig,
    *,
    exclude_launch_names: frozenset[str],
) -> list[dict[str, Any]]:
    cf = config.root_dir.resolve()
    out: list[dict[str, Any]] = []
    zap = human_zapuski_root(cf)
    if zap.is_dir():
        for child in sorted(zap.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if child.name == QUARANTINE_DIR or child.name.startswith("_"):
                continue
            if child.name in exclude_launch_names:
                continue
            if _is_smoke_test_name(child.name):
                out.append(
                    {
                        "kind": "launch_folder",
                        "path": str(child.resolve()),
                        "reason": "SMOKE_or_TEST_prefix",
                    }
                )
    rs = cf / "runs" / "site"
    if rs.is_dir():
        for child in rs.iterdir():
            if not child.is_dir():
                continue
            if _is_smoke_test_name(child.name):
                out.append({"kind": "runs_site", "path": str(child.resolve()), "reason": "SMOKE_or_TEST"})
    osite = cf / "output" / "site"
    if osite.is_dir():
        for child in osite.iterdir():
            if not child.is_dir():
                continue
            n = child.name.upper()
            if "__SMOKE" in n or "__TEST" in n or n.endswith("_SMOKE") or n.endswith("_TEST"):
                out.append({"kind": "output_site", "path": str(child.resolve()), "reason": "SMOKE_or_TEST_marker"})
    comb = cf / "legacy" / "content_combiner"
    if comb.is_dir():
        for child in comb.iterdir():
            if not child.is_dir():
                continue
            if _is_smoke_test_name(child.name):
                out.append({"kind": "content_combiner", "path": str(child.resolve()), "reason": "SMOKE_or_TEST"})
    tp = cf / "legacy" / "autopublisher" / "To_Publish"
    if tp.is_dir():
        for child in tp.iterdir():
            if not child.is_dir():
                continue
            if _is_smoke_test_name(child.name):
                out.append({"kind": "to_publish", "path": str(child.resolve()), "reason": "SMOKE_or_TEST"})
    return out


def quarantine_old_artifacts(
    config: OrchestratorConfig,
    *,
    execute: bool,
    exclude_launch_names: frozenset[str] | None = None,
) -> dict[str, Any]:
    ex = exclude_launch_names or frozenset({"RECOVERY_site-drive-run-a"})
    rows = _collect_quarantine_candidates(config, exclude_launch_names=ex)
    zap = human_zapuski_root(config.root_dir)
    stamp = _now_stamp()
    dest_root = (zap / QUARANTINE_DIR / stamp).resolve()
    moves: list[dict[str, str]] = []
    if execute and rows:
        dest_root.mkdir(parents=True, exist_ok=True)
    for r in rows:
        src = Path(str(r["path"]))
        if not src.exists():
            continue
        rel = f"{r['kind']}_{src.name}"
        dst = dest_root / rel
        if execute:
            dst.parent.mkdir(parents=True, exist_ok=True)
            idx = 1
            final_dst = dst
            while final_dst.exists():
                idx += 1
                final_dst = dest_root / f"{rel}_v{idx}"
            shutil.move(str(src), str(final_dst))
            moves.append({"from": str(src), "to": str(final_dst), "kind": str(r["kind"])})
    rep_dir = (config.root_dir / ".orchestrator" / "reports").resolve()
    rep_dir.mkdir(parents=True, exist_ok=True)
    jpath = rep_dir / f"quarantine_manifest_{stamp}.json"
    cpath = rep_dir / f"quarantine_manifest_{stamp}.csv"
    payload = {
        "ok": True,
        "execute": execute,
        "candidates_count": len(rows),
        "dest_root": str(dest_root) if execute else str(dest_root),
        "moves": moves,
        "candidates": rows,
    }
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with cpath.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "path", "reason"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    payload["json_path"] = str(jpath)
    payload["csv_path"] = str(cpath)
    return payload
