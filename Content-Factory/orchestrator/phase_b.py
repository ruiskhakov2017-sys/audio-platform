from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.status import StatusStore


WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)


@dataclass
class PhaseBOptions:
    story_id: str
    deferred_manifest: Path
    gemini_registry_path: Path
    reports_subdir: str = ""
    runtime_modes: dict[str, str] | None = None
    promo_intro_en: str = "promo_intro_en"
    promo_mid_en: str = "promo_mid_en"
    promo_outro_en: str = "promo_outro_en"
    allow_scaffold: bool = False


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Invalid registry structure")
        return data
    except Exception:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        bots: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("- email:"):
                if current:
                    bots.append(current)
                current = {"email": line.split(":", 1)[1].strip()}
                continue
            if current is None:
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "gemini_bots":
                continue
            current[key] = value
        if current:
            bots.append(current)
        return {"gemini_bots": bots}


def _phase_a_runs_root_from_deferred(manifest: Path) -> Path | None:
    """If deferred.json lives at runs/<branch>/<id>/_phase_a/ready_queues/deferred.json, return that run root."""
    p = manifest.resolve()
    if p.name != "deferred.json" or p.parent.name != "ready_queues":
        return None
    phase_a_staging = p.parent.parent
    if phase_a_staging.name != "_phase_a":
        return None
    return phase_a_staging.parent


def _resolve_phase_b_run_root(
    config: OrchestratorConfig,
    deferred_manifest: Path,
    story_id: str,
    reports_subdir: str,
) -> Path:
    base = _phase_a_runs_root_from_deferred(deferred_manifest)
    if base is not None:
        return base / "_phase_b"
    reports_dir = config.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    folder_name = reports_subdir.strip() or f"phase_b_{story_id}"
    return reports_dir / folder_name


def _update_run_report_scaffold_flags(phase_a_runs_root: Path, scaffold_used: bool, production_ready: bool) -> None:
    report = phase_a_runs_root / "REPORT.md"
    if not report.exists():
        return
    text = report.read_text(encoding="utf-8")
    text = re.sub(
        r"scaffold_used:\s*\S+",
        f"scaffold_used: {'true' if scaffold_used else 'false'}",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"production_ready:\s*\S+",
        f"production_ready: {'true' if production_ready else 'false'}",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    report.write_text(text, encoding="utf-8")


def _normalize_deferred_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_items = payload.get("items", [])
    out: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, str):
            out.append({"source_path": item, "cleaned_path": item})
        elif isinstance(item, dict) and "source_path" in item:
            out.append(
                {
                    "source_path": str(item["source_path"]),
                    "cleaned_path": str(item.get("cleaned_path", item["source_path"])),
                }
            )
    return out


def _extract_ad_point(text: str) -> dict[str, Any]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        return {"anchor_type": "empty", "anchor_text": "", "paragraph_index": -1}
    mid_idx = max(0, len(paras) // 2 - 1)
    anchor = paras[mid_idx][:260]
    return {"anchor_type": "paragraph_mid", "anchor_text": anchor, "paragraph_index": mid_idx}


def _apply_promo(text: str, ad_point: dict[str, Any], intro: str, mid: str, outro: str) -> str:
    paras = [p for p in text.split("\n\n") if p.strip()]
    if not paras:
        return f"{intro}\n\n{mid}\n\n{outro}".strip()
    idx = ad_point.get("paragraph_index", 0)
    if not isinstance(idx, int):
        idx = 0
    idx = max(0, min(idx, len(paras) - 1))
    out = []
    out.append(intro)
    out.extend(paras[: idx + 1])
    out.append(mid)
    out.extend(paras[idx + 1 :])
    out.append(outro)
    return "\n\n".join(x for x in out if x.strip())


def run_phase_b(config: OrchestratorConfig, options: PhaseBOptions) -> dict[str, Any]:
    pipeline = "phase-b"
    stage = "phase_b"
    status = StatusStore(config.status_file)
    status.append(
        story_id=options.story_id,
        pipeline=pipeline,
        stage=stage,
        state="running",
        message="phase B started",
    )
    if not options.allow_scaffold:
        msg = "Реальный Gemini runtime не подключён. Нельзя продолжать production pipeline."
        status.append(story_id=options.story_id, pipeline=pipeline, stage=stage, state="failed", message=msg)
        return {"ok": False, "message": msg}

    if not options.deferred_manifest.exists():
        msg = f"deferred manifest not found: {options.deferred_manifest}"
        status.append(story_id=options.story_id, pipeline=pipeline, stage=stage, state="failed", message=msg)
        return {"ok": False, "message": msg}
    if not options.gemini_registry_path.exists():
        msg = f"gemini registry not found: {options.gemini_registry_path}"
        status.append(story_id=options.story_id, pipeline=pipeline, stage=stage, state="failed", message=msg)
        return {"ok": False, "message": msg}

    run_root = _resolve_phase_b_run_root(
        config,
        options.deferred_manifest,
        options.story_id,
        options.reports_subdir,
    )
    run_root.mkdir(parents=True, exist_ok=True)
    print(f"[PHASE B] started: story_id={options.story_id}", flush=True)
    print(f"[PHASE B] reports: {run_root}", flush=True)

    deferred_payload = json.loads(options.deferred_manifest.read_text(encoding="utf-8"))
    deferred_items = _normalize_deferred_items(deferred_payload)
    registry_payload = _load_yaml(options.gemini_registry_path)
    gemini_bots = registry_payload.get("gemini_bots", [])
    modes = options.runtime_modes or {}
    print(
        f"[PHASE B] input: deferred={len(deferred_items)} gemini_accounts={len(gemini_bots)}",
        flush=True,
    )

    _write_json(
        run_root / "phase_b_input_manifest.json",
        {
            "deferred_manifest": str(options.deferred_manifest.resolve()),
            "deferred_items_count": len(deferred_items),
            "gemini_accounts_count": len(gemini_bots),
            "gemini_registry_path": str(options.gemini_registry_path.resolve()),
        },
    )
    _write_json(run_root / "runtime_modes_snapshot.json", {"modes": modes})

    story_states: dict[str, dict[str, str]] = {}
    general_rows: list[dict[str, Any]] = []
    info_rows: list[dict[str, Any]] = []
    youtube_rows: list[dict[str, Any]] = []
    safe_rows: list[dict[str, Any]] = []
    ad_rows: list[dict[str, Any]] = []
    promo_rows: list[dict[str, Any]] = []
    characters_rows: list[dict[str, Any]] = []
    scene_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    info_dir = run_root / "info_outputs"
    safe_dir = run_root / "safe_text"
    ad_dir = run_root / "ad_points"
    promo_dir = run_root / "promo_applied_text"
    characters_dir = run_root / "characters"
    scene_dir = run_root / "scene_prompts"

    site_ready: list[dict[str, Any]] = []
    youtube_ready: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []

    total_items = len(deferred_items)
    for i, row in enumerate(deferred_items):
        src_str = row["source_path"]
        cleaned_str = row.get("cleaned_path", src_str)
        src = Path(src_str)
        cleaned_src = Path(cleaned_str)
        story_key = f"story_{i+1:06d}"
        print(f"[B][{i+1}/{total_items}] processing: {src.name}", flush=True)
        if not cleaned_src.exists():
            manual_review.append({"source_path": src_str, "reason": "source_missing"})
            story_states[src_str] = {"state": "manual_review", "reason": "source_missing"}
            errors.append({"source_path": src_str, "error": "source_missing"})
            continue

        text = _read_text(cleaned_src)
        word_count = len(WORD_RE.findall(text))

        # 1) general_selection
        if word_count < 50:
            g_decision = "rejected"
            g_reason = "too_short_after_phase_a"
        else:
            g_decision = "selected"
            g_reason = "placeholder_selected_for_phase_b"
        general_rows.append(
            {
                "source_path": src_str,
                "decision": g_decision,
                "reason": g_reason,
                "word_count": word_count,
            }
        )
        if g_decision == "rejected":
            print(f"[B][{i+1}/{total_items}] rejected: {g_reason}", flush=True)
            rejected.append({"source_path": src_str, "reason": g_reason})
            story_states[src_str] = {"state": "rejected", "reason": g_reason}
            continue

        # 2) site_info_builder
        info_obj = {
            "title": src.stem.replace("_", " ").strip() or "Untitled Story",
            "description": "placeholder description (Phase B, Gemini block not connected yet)",
            "genres": ["unknown"],
            "tags": ["phase-b", "placeholder"],
            "voice": "U",
            "visual": "neutral",
        }
        info_path = info_dir / f"{story_key}.info.json"
        _write_json(info_path, info_obj)
        info_rows.append({"source_path": src_str, "info_output": str(info_path)})

        # 3) youtube_top_tier_selection
        y_decision = "youtube_selected" if word_count >= 700 else "site_only"
        y_reason = "placeholder_top_tier_rule_word_count"
        youtube_rows.append(
            {
                "source_path": src_str,
                "decision": y_decision,
                "reason": y_reason,
                "word_count": word_count,
            }
        )

        # Site is ready whenever general selected
        site_ready_item = {
            "source_path": src_str,
            "info_output": str(info_path),
            "tts_runtime": modes.get("site_tts_runtime", "local"),
            "tts_engine": modes.get("site_tts_engine", "elevenlabs"),
            "tts_executor": (
                "site_tts_colab_runner"
                if modes.get("site_tts_runtime") == "colab"
                else "site_tts_local_runner"
            ),
        }
        if site_ready_item["tts_engine"] == "elevenlabs":
            site_ready_item["elevenlabs_mode"] = modes.get("elevenlabs_mode", "normal")
        site_ready.append(site_ready_item)

        if y_decision != "youtube_selected":
            print(f"[B][{i+1}/{total_items}] site_only", flush=True)
            story_states[src_str] = {"state": "site_ready", "reason": "site_only_after_youtube_selection"}
            continue

        # 4) youtube_safe_text
        safe_path = safe_dir / f"{story_key}.safe.txt"
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(text, encoding="utf-8")
        safe_rows.append({"source_path": src_str, "safe_text": str(safe_path)})

        # 5) youtube_ad_point
        ad_point = _extract_ad_point(text)
        ad_payload = {"source_path": src_str, **ad_point}
        ad_path = ad_dir / f"{story_key}.ad_point.json"
        _write_json(ad_path, ad_payload)
        ad_rows.append({"source_path": src_str, "ad_point_output": str(ad_path)})

        # 6) promo insertion (script-like stage in orchestrator contour)
        promo_text = _apply_promo(text, ad_point, options.promo_intro_en, options.promo_mid_en, options.promo_outro_en)
        promo_path = promo_dir / f"{story_key}.promo.txt"
        promo_path.parent.mkdir(parents=True, exist_ok=True)
        promo_path.write_text(promo_text, encoding="utf-8")
        promo_rows.append({"source_path": src_str, "promo_applied_text": str(promo_path)})

        # 7) youtube_characters
        chars_obj = {
            "style": "placeholder_style",
            "characters": [
                {"id": "CHAR_1", "role": "protagonist", "appearance": "placeholder", "consistency_tags": ["anchor1"]}
            ],
        }
        chars_path = characters_dir / f"{story_key}.characters.json"
        _write_json(chars_path, chars_obj)
        characters_rows.append({"source_path": src_str, "characters_output": str(chars_path)})

        # 8) youtube_scene_prompts
        scene_obj = {
            "source_path": src_str,
            "scenes": [
                {
                    "scene_id": "S1",
                    "prompt": "placeholder prompt based on characters and story text",
                }
            ],
        }
        scene_path = scene_dir / f"{story_key}.scene_prompts.json"
        _write_json(scene_path, scene_obj)
        scene_rows.append({"source_path": src_str, "scene_prompts_output": str(scene_path)})

        yt_item = {
            "source_path": src_str,
            "safe_text": str(safe_path),
            "ad_point_output": str(ad_path),
            "promo_applied_text": str(promo_path),
            "characters_output": str(chars_path),
            "scene_prompts_output": str(scene_path),
            "tts_runtime": modes.get("youtube_tts_runtime", "local"),
            "tts_engine": modes.get("youtube_tts_engine", "elevenlabs"),
            "tts_executor": (
                "youtube_tts_colab_runner"
                if modes.get("youtube_tts_runtime") == "colab"
                else "youtube_tts_local_runner"
            ),
            "video_build": modes.get("video_build", "local"),
            "video_builder": {
                "local": "video_local_builder",
                "colab": "video_colab_builder",
                "runpod": "video_runpod_builder",
            }.get(modes.get("video_build", "local"), "video_local_builder"),
            "youtube_publish_mode": modes.get("youtube_publish", "api"),
        }
        if yt_item["tts_engine"] == "elevenlabs":
            yt_item["elevenlabs_mode"] = modes.get("elevenlabs_mode", "normal")
        youtube_ready.append(yt_item)
        story_states[src_str] = {"state": "youtube_ready", "reason": "full_youtube_phase_b_path_done"}
        print(f"[B][{i+1}/{total_items}] youtube_ready", flush=True)

    # artifacts
    _write_jsonl(run_root / "general_selection_results.jsonl", general_rows)
    _write_jsonl(run_root / "info_outputs_results.jsonl", info_rows)
    _write_jsonl(run_root / "youtube_selection_results.jsonl", youtube_rows)
    _write_jsonl(run_root / "safe_text_results.jsonl", safe_rows)
    _write_jsonl(run_root / "youtube_ad_point_results.jsonl", ad_rows)
    _write_jsonl(run_root / "promo_applied_results.jsonl", promo_rows)
    _write_jsonl(run_root / "characters_results.jsonl", characters_rows)
    _write_jsonl(run_root / "scene_prompts_results.jsonl", scene_rows)

    _write_json(run_root / "routing_rejected.json", {"items": rejected})
    _write_json(run_root / "routing_manual_review.json", {"items": manual_review})
    _write_json(run_root / "routing_site_ready.json", {"items": site_ready})
    _write_json(run_root / "routing_youtube_ready.json", {"items": youtube_ready})
    _write_json(
        run_root / "site_visual_plan.json",
        {
            "mode": modes.get("site_visual", "auto"),
            "action": (
                "generate_site_visual_prompts_for_manual"
                if modes.get("site_visual") == "manual"
                else "run_site_visual_auto_pipeline"
            ),
        },
    )
    _write_json(
        run_root / "youtube_publish_plan.json",
        {
            "mode": modes.get("youtube_publish", "api"),
            "action": (
                "prepare_manual_publish_package"
                if modes.get("youtube_publish") == "manual"
                else "publish_via_api"
            ),
        },
    )
    _write_json(
        run_root / "story_state_manifest.json",
        {
            "stories": [{"source_path": k, "state": v["state"], "reason": v["reason"]} for k, v in sorted(story_states.items())]
        },
    )
    _write_json(
        run_root / "phase_b_summary.json",
        {
            "deferred_in": len(deferred_items),
            "rejected": len(rejected),
            "manual_review": len(manual_review),
            "site_ready": len(site_ready),
            "youtube_ready": len(youtube_ready),
            "errors": len(errors),
            "run_root": str(run_root),
            "phase_b_order": [
                "general_selection",
                "site_info_builder",
                "youtube_top_tier_selection",
                "youtube_safe_text",
                "youtube_ad_point",
                "promo_insertion",
                "youtube_characters",
                "youtube_scene_prompts",
            ],
            "placeholder_note": "Gemini bots runtime is not connected yet; results are scaffolded for integration tests.",
        },
    )

    summary = (
        f"phase B done; deferred={len(deferred_items)} rejected={len(rejected)} "
        f"review={len(manual_review)} site_ready={len(site_ready)} youtube_ready={len(youtube_ready)}"
    )
    status.append(
        story_id=options.story_id,
        pipeline=pipeline,
        stage=stage,
        state="done",
        message=summary,
    )
    print(f"[PHASE B] finished: {summary}", flush=True)
    phase_a_runs = _phase_a_runs_root_from_deferred(options.deferred_manifest)
    if phase_a_runs is not None and options.allow_scaffold:
        _update_run_report_scaffold_flags(phase_a_runs, scaffold_used=True, production_ready=False)
    return {"ok": True, "summary": summary, "run_root": str(run_root)}
