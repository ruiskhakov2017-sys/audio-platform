"""
Read-only аудит путей site launch: phase-a / phase-b / pipeline vs глобальные каталоги CF.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import (
    D10_LEGACY,
    D10_TEMP,
    F_MANIFEST,
    human_zapuski_root,
    launch_legacy_output_root,
    launch_legacy_runs_root,
    read_json,
)
from orchestrator.human_launch_lifecycle import launch_story_scope_bundle
from orchestrator.human_launch_path_scope import global_site_write_roots, launch_legacy_anchor, path_is_descendant
from orchestrator.human_launch_site_flow_bat import default_site_run_base_from_launch_name

_SITE_FLOW_STATE_NAME = "site_flow_bat_state.json"


def _site_flow_state_path(launch: Path) -> Path:
    return launch / D10_TEMP / _SITE_FLOW_STATE_NAME


def _parse_cmd_args(cmd: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(cmd, list):
        return out
    for i, tok in enumerate(cmd):
        if not isinstance(tok, str):
            continue
        if tok.startswith("--") and i + 1 < len(cmd):
            nxt = cmd[i + 1]
            if isinstance(nxt, str) and not nxt.startswith("--"):
                out[tok] = nxt
    return out


def _row(
    *,
    module: str,
    expected_path: str,
    actual_path: str,
    launch: Path,
    cf_root: Path,
) -> dict[str, Any]:
    exp_p = Path(expected_path) if expected_path else Path()
    act_p = Path(actual_path) if actual_path else Path()
    leg = launch_legacy_anchor(launch)
    inside = bool(actual_path) and path_is_descendant(act_p, launch.resolve())
    uses_global = False
    if actual_path:
        ap = act_p.resolve()
        if not path_is_descendant(ap, leg.resolve()):
            for g in global_site_write_roots(cf_root):
                if path_is_descendant(ap, g):
                    uses_global = True
                    break
    status = "ok"
    if not actual_path:
        status = "unknown"
    elif uses_global and not inside:
        status = "wrong_global_path"
    elif inside and uses_global:
        status = "mixed"
    rec = ""
    if status == "wrong_global_path":
        rec = "Перенести артефакты под Запуски/<name>/10_Временные_файлы/legacy/ и передавать --launch-dir во все subprocess."
    elif status == "unknown":
        rec = "Запустить run-site-flow --execute или проверить site_flow_bat_state.json / deferred.json."
    elif status == "mixed":
        rec = "Путь внутри launch, но также под глобальным корнем — проверить symlink/дубликат."
    return {
        "module": module,
        "expected_path": expected_path,
        "actual_path": actual_path,
        "is_inside_launch": inside,
        "uses_global_path": uses_global,
        "status": status,
        "recommendation": rec,
    }


def run_launch_path_audit(config: OrchestratorConfig, *, launch_name: str) -> dict[str, Any]:
    cf = config.root_dir.resolve()
    launch = (human_zapuski_root(cf) / launch_name.strip()).resolve()
    manifest = read_json(launch / F_MANIFEST) or {}
    _sids, _scope, recovery_items = launch_story_scope_bundle(launch, manifest)
    site_base = default_site_run_base_from_launch_name(launch.name)
    sb = str(manifest.get("site_flow_bat", {}).get("site_run_base") or "").strip()
    if sb:
        site_base = sb
    phase_a_id = f"{site_base}-a"
    phase_b_id = f"{site_base}-b"
    site_run_id = f"{site_base}-site"

    exp_phase_a_root = str((launch_legacy_runs_root(launch, "site", phase_a_id) / "_phase_a").resolve())
    exp_runs_parent = str(launch_legacy_runs_root(launch, "site", phase_a_id).resolve())
    exp_output_site = str(launch_legacy_output_root(launch, branch="site").resolve())
    exp_combiner = str((launch_legacy_anchor(launch) / "_content_combiner_runtime").resolve())
    exp_autopublish = str((launch_legacy_anchor(launch) / "_autopublisher_To_Publish").resolve())

    state = read_json(_site_flow_state_path(launch)) or {}
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    phase_a_step = steps.get("phase_a") if isinstance(steps.get("phase_a"), dict) else {}
    phase_b_step = steps.get("phase_b") if isinstance(steps.get("phase_b"), dict) else {}
    site_step = steps.get("site_run") if isinstance(steps.get("site_run"), dict) else {}

    pa_args = _parse_cmd_args(phase_a_step.get("cmd"))
    pb_args = _parse_cmd_args(phase_b_step.get("cmd"))
    sr_args = _parse_cmd_args(site_step.get("cmd"))

    deferred_manifest = pa_args.get("--deferred-manifest", "") or pb_args.get("--deferred-manifest", "")
    if not deferred_manifest:
        deferred_manifest = str(
            (launch_legacy_runs_root(launch, "site", phase_a_id) / "_phase_a" / "ready_queues" / "deferred.json").resolve()
        )

    actual_phase_a_dir = ""
    if Path(deferred_manifest).is_file():
        p = Path(deferred_manifest).resolve().parent.parent
        if p.name == "_phase_a":
            actual_phase_a_dir = str(p.resolve())

    actual_output_site = ""
    ld = sr_args.get("--launch-dir", "")
    if ld:
        actual_output_site = str((Path(ld) / D10_TEMP / D10_LEGACY / "output" / "site").resolve())

    rows: list[dict[str, Any]] = []

    rows.append(
        _row(
            module="phase_a_root",
            expected_path=exp_phase_a_root,
            actual_path=actual_phase_a_dir or exp_phase_a_root if Path(exp_phase_a_root).is_dir() else "",
            launch=launch,
            cf_root=cf,
        )
    )
    rows.append(
        _row(
            module="phase_a_gemini_input",
            expected_path=str((Path(exp_runs_parent) / "_phase_a" / "gemini_input").resolve())
            if Path(exp_runs_parent).exists()
            else str((Path(exp_phase_a_root) / "gemini_input").resolve()),
            actual_path=str((Path(actual_phase_a_dir or exp_phase_a_root) / "gemini_input").resolve())
            if (actual_phase_a_dir or (Path(exp_phase_a_root).is_dir()))
            else "",
            launch=launch,
            cf_root=cf,
        )
    )
    rows.append(
        _row(
            module="phase_a_logs",
            expected_path=str((Path(exp_runs_parent) / "logs").resolve()),
            actual_path=str((Path(exp_runs_parent) / "logs").resolve()) if Path(exp_runs_parent, "logs").is_dir() else "",
            launch=launch,
            cf_root=cf,
        )
    )

    phase_b_root = ""
    if Path(deferred_manifest).is_file():
        dr = Path(deferred_manifest).resolve()
        if dr.name == "deferred.json" and dr.parent.name == "ready_queues" and dr.parent.parent.name == "_phase_a":
            run_root = dr.parent.parent.parent
            phase_b_root = str((run_root / "_phase_b").resolve())

    rows.append(
        _row(
            module="phase_b_root",
            expected_path=str((Path(exp_runs_parent) / "_phase_b").resolve()),
            actual_path=phase_b_root if Path(phase_b_root).exists() else "",
            launch=launch,
            cf_root=cf,
        )
    )
    rows.append(
        _row(
            module="phase_b_deferred_input",
            expected_path=str(Path(deferred_manifest).resolve()) if deferred_manifest else "",
            actual_path=str(Path(deferred_manifest).resolve()) if deferred_manifest and Path(deferred_manifest).is_file() else "",
            launch=launch,
            cf_root=cf,
        )
    )

    rows.append(
        _row(
            module="pipeline_output_site",
            expected_path=exp_output_site,
            actual_path=actual_output_site or (exp_output_site if Path(exp_output_site).exists() else ""),
            launch=launch,
            cf_root=cf,
        )
    )
    rows.append(
        _row(
            module="pipeline_site_tts_io",
            expected_path=exp_output_site,
            actual_path=actual_output_site or exp_output_site,
            launch=launch,
            cf_root=cf,
        )
    )
    rows.append(
        _row(
            module="pipeline_content_combiner_runtime",
            expected_path=exp_combiner,
            actual_path=exp_combiner if Path(exp_combiner).exists() else "",
            launch=launch,
            cf_root=cf,
        )
    )
    rows.append(
        _row(
            module="pipeline_autopublisher_to_publish",
            expected_path=exp_autopublish,
            actual_path=exp_autopublish if Path(exp_autopublish).exists() else "",
            launch=launch,
            cf_root=cf,
        )
    )

    global_hits: list[str] = []
    for label, pth in (
        ("global_runs_site", cf / "runs" / "site"),
        ("global_output_site", cf / "output" / "site"),
        ("global_legacy_content_combiner", cf / "legacy" / "content_combiner"),
    ):
        if pth.is_dir() and any(pth.iterdir()):
            global_hits.append(f"{label}={pth} (non-empty)")

    payload: dict[str, Any] = {
        "ok": True,
        "launch": str(launch),
        "launch_name": launch.name,
        "content_factory_root": str(cf),
        "site_run_base": site_base,
        "phase_a_id": phase_a_id,
        "recovery_queue_map": recovery_items is not None,
        "rows": rows,
        "global_non_empty_hints": global_hits,
        "notes": [
            "Аудит read-only: actual для phase_a берётся из deferred.json если есть.",
            "Если site_run не выполнялся, pipeline пути могут быть unknown.",
        ],
    }
    return payload


def write_launch_path_audit_reports(config: OrchestratorConfig, *, launch_name: str) -> dict[str, Any]:
    data = run_launch_path_audit(config, launch_name=launch_name)
    rep_dir = (config.root_dir / ".orchestrator" / "reports").resolve()
    rep_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[<>:"/\\|?*]+', "_", launch_name.strip()) or "launch"
    jpath = rep_dir / f"launch_path_audit_{safe}.json"
    cpath = rep_dir / f"launch_path_audit_{safe}.csv"
    jpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with cpath.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "module",
                "expected_path",
                "actual_path",
                "is_inside_launch",
                "uses_global_path",
                "status",
                "recommendation",
            ],
        )
        w.writeheader()
        for r in data.get("rows", []):
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    data["json_path"] = str(jpath)
    data["csv_path"] = str(cpath)
    return data
