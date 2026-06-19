"""YouTube production path leak forensic, legacy recovery, and launch-only readiness."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.isolated_launch_context import isolated_session
from orchestrator.launch_contract import build_launch_context
from orchestrator.youtube_path_resolver import (
    assert_youtube_production_write_allowed,
    legacy_global_youtube_story_root,
    resolve_legacy_youtube_recovery_story_dir,
)
from orchestrator.youtube_visuals_clean import validate_visual_characters_file, validate_visual_prompts_file
from orchestrator.youtube_visuals_runner import (
    YoutubePromptsResumeAuditOptions,
    _iter_launch_story_dirs,
    _load_manifest,
    _story_identity,
    run_youtube_prompts_resume_audit,
)

_FORENSIC_LEAK_SITES: tuple[tuple[str, str, str], ...] = (
    ("orchestrator.youtube_from_site", "run_youtube_prepare_safe_input", "story scaffold (was output/youtube)"),
    ("orchestrator.youtube_bridge_manifest", "_output_story_root", "story manifest dir (was output/youtube)"),
    ("orchestrator.youtube_visuals_clean", "_story_dir", "visuals clean (was output/youtube)"),
    ("orchestrator.youtube_visuals_bridge", "_story_dir", "bridge story dir via resolve_bridge_story_dir"),
    ("orchestrator.youtube_path_resolver", "resolve_youtube_technical_story_dir", "central story resolver"),
    ("orchestrator.youtube_full_auto.stage_runners", "_story_output_dir", "full-auto stage output"),
    ("orchestrator.launch_contract", "sync_youtube_story_from_legacy", "legacy → launch import bridge"),
)

_RECOVERABLE_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("safe_story", "02_safe_story/safe_story.txt", "text"),
    ("promo_audio_text", "03_promo/text_ready_for_audio.txt", "text"),
    ("narration_mp3", "04_audio/narration.mp3", "binary"),
    ("characters", "05_characters/characters.txt", "characters"),
    ("prompts_primary", "06_prompts/prompts_list.txt", "prompts"),
    ("prompts_legacy", "06_director/prompts_list.txt", "prompts"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_nonempty_text(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _validate_artifact(path: Path, kind: str) -> dict[str, Any]:
    if kind == "binary":
        ok = path.is_file() and path.stat().st_size > 1024
        return {"ok": ok, "status": "ok" if ok else ("empty" if path.is_file() else "missing"), "path": str(path)}
    if kind == "text":
        ok = _is_nonempty_text(path)
        return {"ok": ok, "status": "ok" if ok else ("empty" if path.is_file() else "missing"), "path": str(path)}
    if kind == "characters":
        return validate_visual_characters_file(path)
    if kind == "prompts":
        return validate_visual_prompts_file(path)
    return {"ok": False, "status": "unknown_kind", "path": str(path)}


def _legacy_story_keys(story_dir: Path, manifest: dict[str, Any]) -> list[str]:
    story_id, title = _story_identity(story_dir, manifest)
    keys = [story_id, title, story_dir.name]
    canonical = str(manifest.get("canonical_basename", "")).strip()
    if canonical:
        keys.append(canonical)
    return list(dict.fromkeys(k for k in keys if k))


def _find_legacy_dir(config: OrchestratorConfig, keys: list[str]) -> Path | None:
    for key in keys:
        found = resolve_legacy_youtube_recovery_story_dir(config, key)
        if found is not None:
            return found
    return None


def audit_production_path_leak(config: OrchestratorConfig, youtube_run_id: str) -> dict[str, Any]:
    launch_id = youtube_run_id.strip()
    ctx = build_launch_context(config, launch_id=launch_id)
    legacy_root = legacy_global_youtube_story_root(config)
    launch_yt = ctx.youtube_root
    story_rows: list[dict[str, Any]] = []

    for story_dir in _iter_launch_story_dirs(config, launch_id):
        manifest = _load_manifest(story_dir)
        story_id, title = _story_identity(story_dir, manifest)
        keys = _legacy_story_keys(story_dir, manifest)
        legacy_dir = _find_legacy_dir(config, keys)
        launch_only: list[str] = []
        legacy_only: list[str] = []
        both: list[str] = []
        for _name, rel, kind in _RECOVERABLE_ARTIFACTS:
            launch_path = story_dir / rel.replace("/", "\\").replace("\\", "/")
            launch_path = story_dir / Path(rel)
            legacy_path = (legacy_dir / rel) if legacy_dir else None
            launch_ok = _validate_artifact(launch_path, kind).get("ok", False)
            legacy_ok = (
                _validate_artifact(legacy_path, kind).get("ok", False) if legacy_path and legacy_path.is_file() else False
            )
            if launch_ok and legacy_ok:
                both.append(rel)
            elif launch_ok:
                launch_only.append(rel)
            elif legacy_ok:
                legacy_only.append(rel)
        story_rows.append(
            {
                "story_id": story_id,
                "title": title,
                "launch_story_dir": str(story_dir),
                "legacy_story_dir": str(legacy_dir) if legacy_dir else None,
                "artifacts_launch_only": launch_only,
                "artifacts_legacy_only": legacy_only,
                "artifacts_both": both,
                "path_leak_risk": bool(legacy_only),
            }
        )

    leaked_legacy_only = sum(1 for row in story_rows if row["path_leak_risk"])
    return {
        "ok": True,
        "youtube_run_id": launch_id,
        "generated_at": _now_iso(),
        "launch_root": str(ctx.launch_root),
        "launch_youtube_root": str(launch_yt),
        "legacy_global_root": str(legacy_root),
        "known_leak_sites_fixed": [
            {"module": m, "function": f, "description": d} for m, f, d in _FORENSIC_LEAK_SITES
        ],
        "launch_story_count": len(story_rows),
        "stories_with_legacy_only_artifacts": leaked_legacy_only,
        "stories": story_rows,
        "guard_token": "WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT",
        "production_output_rule": "Запуски/<youtube_run_id>/03_youtube/<story>/ only",
        "legacy_rule": "output/youtube read-only recovery source",
    }


@dataclass
class YoutubeLegacyOutputRecoveryOptions:
    youtube_run_id: str
    execute: bool = False


def recover_legacy_youtube_outputs(
    *,
    config: OrchestratorConfig,
    options: YoutubeLegacyOutputRecoveryOptions,
) -> dict[str, Any]:
    launch_id = str(options.youtube_run_id).strip()
    if not launch_id:
        return {"ok": False, "message": "youtube_run_id is required"}

    imported: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for story_dir in _iter_launch_story_dirs(config, launch_id):
        manifest = _load_manifest(story_dir)
        story_id, title = _story_identity(story_dir, manifest)
        keys = _legacy_story_keys(story_dir, manifest)
        legacy_dir = _find_legacy_dir(config, keys)
        if legacy_dir is None:
            skipped.append({"story_id": story_id, "reason": "no_legacy_dir"})
            continue

        for artifact_name, rel, kind in _RECOVERABLE_ARTIFACTS:
            launch_path = story_dir / Path(rel)
            if launch_path.is_file() and _validate_artifact(launch_path, kind).get("ok", False):
                skipped.append({"story_id": story_id, "artifact": artifact_name, "reason": "launch_already_valid"})
                continue
            legacy_path = legacy_dir / Path(rel)
            validation = _validate_artifact(legacy_path, kind)
            if not validation.get("ok", False):
                rejected.append(
                    {
                        "story_id": story_id,
                        "artifact": artifact_name,
                        "legacy_path": str(legacy_path),
                        "reason": validation.get("status", "invalid"),
                        "validation": validation,
                    }
                )
                continue
            dest = launch_path
            row = {
                "story_id": story_id,
                "artifact": artifact_name,
                "legacy_path": str(legacy_path),
                "launch_path": str(dest),
                "sha256": _sha256_file(legacy_path),
            }
            if options.execute:
                assert_youtube_production_write_allowed(
                    config,
                    dest,
                    youtube_run_id=launch_id,
                    module="orchestrator.youtube_launch_path_ops",
                    function="recover_legacy_youtube_outputs",
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_path, dest)
                row["imported"] = True
            else:
                row["imported"] = False
                row["dry_run"] = True
            imported.append(row)

    return {
        "ok": True,
        "youtube_run_id": launch_id,
        "generated_at": _now_iso(),
        "execute": bool(options.execute),
        "imported_count": len(imported),
        "rejected_count": len(rejected),
        "skipped_count": len(skipped),
        "imported": imported,
        "rejected": rejected,
        "skipped": skipped,
    }


def compute_launch_only_readiness(config: OrchestratorConfig, youtube_run_id: str) -> dict[str, Any]:
    launch_id = youtube_run_id.strip()
    with isolated_session(None, batch_launch_id=launch_id, config=config):
        audit = run_youtube_prompts_resume_audit(
            config=config,
            options=YoutubePromptsResumeAuditOptions(
                youtube_run_id=launch_id,
                accept_known_promo_issues=False,
            ),
        )
    stories = audit.get("stories", []) if isinstance(audit.get("stories"), list) else []
    legacy_root = legacy_global_youtube_story_root(config)
    adjusted: list[dict[str, Any]] = []
    for row in stories:
        if not isinstance(row, dict):
            continue
        story_id = str(row.get("story_id", ""))
        story_dir = Path(str(row.get("story_dir", "")))
        manifest = _load_manifest(story_dir) if story_dir.is_dir() else {}
        keys = _legacy_story_keys(story_dir, manifest) if story_dir.is_dir() else [story_id]
        legacy_dir = _find_legacy_dir(config, keys)
        launch_prompts = story_dir / "06_prompts" / "prompts_list.txt"
        legacy_prompts = (legacy_dir / "06_prompts" / "prompts_list.txt") if legacy_dir else None
        legacy_prompts_alt = (legacy_dir / "06_director" / "prompts_list.txt") if legacy_dir else None
        launch_ok = validate_visual_prompts_file(launch_prompts).get("ok", False) if launch_prompts.is_file() else False
        legacy_ok = False
        if legacy_prompts and legacy_prompts.is_file():
            legacy_ok = validate_visual_prompts_file(legacy_prompts).get("ok", False)
        elif legacy_prompts_alt and legacy_prompts_alt.is_file():
            legacy_ok = validate_visual_prompts_file(legacy_prompts_alt).get("ok", False)
        status = str(row.get("prompts_status", ""))
        if not launch_ok and legacy_ok:
            status = "pending"
        adjusted.append(
            {
                **row,
                "prompts_status_launch_only": status,
                "prompts_ready_launch_only": status == "done" and str(row.get("validation", "")) == "ok" and launch_ok,
                "legacy_prompts_only": bool(not launch_ok and legacy_ok),
                "launch_prompts_path": str(launch_prompts),
                "legacy_prompts_path": str(legacy_prompts) if legacy_prompts and legacy_prompts.is_file() else (
                    str(legacy_prompts_alt) if legacy_prompts_alt and legacy_prompts_alt.is_file() else None
                ),
            }
        )

    active = [r for r in adjusted if not r.get("excluded_from_video")]
    summary = {
        "ready": sum(1 for r in active if r.get("prompts_status_launch_only") == "done" and r.get("validation") == "ok"),
        "pending": sum(1 for r in active if r.get("prompts_status_launch_only") == "pending"),
        "partial": sum(1 for r in active if r.get("prompts_status_launch_only") == "partial"),
        "failed": sum(1 for r in active if r.get("prompts_status_launch_only") in {"failed", "blocked"}),
        "legacy_only_ignored": sum(1 for r in active if r.get("legacy_prompts_only")),
        "ready_for_runpod": sum(1 for r in active if r.get("ready_for_runpod") and r.get("prompts_ready_launch_only")),
    }
    return {
        "ok": True,
        "youtube_run_id": launch_id,
        "generated_at": _now_iso(),
        "legacy_global_root": str(legacy_root),
        "readiness_source": "launch_folder_only",
        "summary": summary,
        "stories": adjusted,
        "prompts_audit": {
            "prompts_done_valid": audit.get("prompts_done_valid"),
            "prompts_partial": audit.get("prompts_partial"),
            "prompts_missing": audit.get("prompts_missing"),
            "prompts_invalid": audit.get("prompts_invalid"),
            "ready_for_runpod": audit.get("ready_for_runpod"),
        },
    }


def _reports_dir(config: OrchestratorConfig) -> Path:
    return (config.root_dir / "reports" / "gemini_execution").resolve()


def write_path_leak_reports(config: OrchestratorConfig, youtube_run_id: str) -> dict[str, str]:
    payload = audit_production_path_leak(config, youtube_run_id)
    out = _reports_dir(config)
    json_path = out / "YOUTUBE_PRODUCTION_PATH_LEAK_FORENSIC.json"
    md_path = out / "YOUTUBE_PRODUCTION_PATH_LEAK_FORENSIC.md"
    _write_json(json_path, payload)
    lines = [
        "# YouTube production path leak forensic",
        "",
        f"- launch: `{payload['launch_root']}`",
        f"- legacy (read-only): `{payload['legacy_global_root']}`",
        f"- stories with legacy-only artifacts: **{payload['stories_with_legacy_only_artifacts']}**",
        f"- guard: `{payload['guard_token']}`",
        "",
        "## Fixed leak sites",
        "",
    ]
    for site in payload.get("known_leak_sites_fixed", []):
        lines.append(f"- `{site['module']}.{site['function']}` — {site['description']}")
    lines.extend(["", "## Per-story legacy-only artifacts", ""])
    for row in payload.get("stories", []):
        if row.get("artifacts_legacy_only"):
            lines.append(
                f"- **{row['story_id']}**: legacy-only {row['artifacts_legacy_only']} "
                f"(legacy_dir={row.get('legacy_story_dir')})"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def write_legacy_recovery_reports(config: OrchestratorConfig, youtube_run_id: str, *, execute: bool = True) -> dict[str, Any]:
    result = recover_legacy_youtube_outputs(
        config=config,
        options=YoutubeLegacyOutputRecoveryOptions(youtube_run_id=youtube_run_id, execute=execute),
    )
    out = _reports_dir(config)
    json_path = out / "YOUTUBE_LEGACY_OUTPUT_RECOVERY.json"
    md_path = out / "YOUTUBE_LEGACY_OUTPUT_RECOVERY.md"
    _write_json(json_path, result)
    lines = [
        "# YouTube legacy output recovery",
        "",
        f"- imported: **{result['imported_count']}**",
        f"- rejected: **{result['rejected_count']}**",
        f"- skipped: **{result['skipped_count']}**",
        f"- execute: `{result['execute']}`",
        "",
        "## Imported",
        "",
    ]
    for row in result.get("imported", []):
        lines.append(f"- {row['story_id']} / {row['artifact']}: `{row['legacy_path']}` → `{row['launch_path']}`")
    lines.extend(["", "## Rejected", ""])
    for row in result.get("rejected", []):
        lines.append(f"- {row['story_id']} / {row['artifact']}: {row['reason']} (`{row.get('legacy_path', '')}`)")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {**result, "reports": {"json": str(json_path), "md": str(md_path)}}


def write_launch_only_readiness_reports(config: OrchestratorConfig, youtube_run_id: str) -> dict[str, Any]:
    payload = compute_launch_only_readiness(config, youtube_run_id)
    out = _reports_dir(config)
    json_path = out / "YOUTUBE_LAUNCH_ONLY_READINESS.json"
    md_path = out / "YOUTUBE_LAUNCH_ONLY_READINESS.md"
    _write_json(json_path, payload)
    summary = payload.get("summary", {})
    lines = [
        "# YouTube launch-only readiness",
        "",
        f"- source: `{payload['readiness_source']}`",
        f"- ready: **{summary.get('ready', 0)}**",
        f"- pending: **{summary.get('pending', 0)}**",
        f"- partial: **{summary.get('partial', 0)}**",
        f"- failed/blocked: **{summary.get('failed', 0)}**",
        f"- legacy-only ignored: **{summary.get('legacy_only_ignored', 0)}**",
        f"- ready_for_runpod (launch-only): **{summary.get('ready_for_runpod', 0)}**",
        "",
        "| story_id | prompts | validation | legacy_only | ready_runpod |",
        "|---|---|---|---|---|",
    ]
    for row in payload.get("stories", []):
        if row.get("excluded_from_video"):
            continue
        lines.append(
            f"| {row.get('story_id')} | {row.get('prompts_status_launch_only')} | "
            f"{row.get('validation')} | {row.get('legacy_prompts_only')} | {row.get('ready_for_runpod')} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {**payload, "reports": {"json": str(json_path), "md": str(md_path)}}


def run_youtube_production_path_repair(
    *,
    config: OrchestratorConfig,
    youtube_run_id: str,
    execute_recovery: bool = True,
) -> dict[str, Any]:
    """Forensic → legacy import → launch-only readiness (no production execute)."""
    forensic = write_path_leak_reports(config, youtube_run_id)
    recovery = write_legacy_recovery_reports(config, youtube_run_id, execute=execute_recovery)
    readiness = write_launch_only_readiness_reports(config, youtube_run_id)
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "forensic_reports": forensic,
        "recovery_reports": recovery.get("reports"),
        "recovery": recovery,
        "readiness_reports": readiness.get("reports"),
        "readiness": readiness,
    }
