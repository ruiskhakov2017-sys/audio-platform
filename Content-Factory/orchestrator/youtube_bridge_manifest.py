"""YouTube bridge manifest: links runs/youtube + output/youtube to legacy staging paths (dry-run, no heavy stages)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.runtime_modes import load_runtime_modes
from orchestrator.youtube_from_site import (
    _append_status,
    _now_iso,
    _read_json,
    _resolve_cleaned_path,
    _safe_name,
    _selection_dir,
    _write_json,
    _youtube_run_root,
)


# Canonical orchestrator layout (matches factual pipeline order).
YOUTUBE_STORY_SUBDIRS: tuple[str, ...] = (
    "00_source",
    "01_selection",
    "02_safe_story",
    "03_promo",
    "04_audio",
    "05_characters",
    "06_director",
    "07_frames",
    "08_video",
    "logs",
)

LEGACY_SCRIPT_DEFAULT_ROOTS: dict[str, str] = {
    "youtube_tts_stories": "legacy/youtube_tts/stories",
    "youtube_tts_promo": "legacy/youtube_tts/promo_stories",
    "director_stories": "legacy/director_2_0/stories",
    "autovideo": "legacy/AutoVideo",
}

# Planned staging roots (legacy scripts do not read these paths today).
STAGING_SUFFIX = "from_orchestrator"


def _deferred_path(root: Path, site_run_id: str) -> Path:
    return (root / "runs" / "site" / site_run_id / "_phase_a" / "ready_queues" / "deferred.json").resolve()


def _infer_site_run_id(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        sid = str(row.get("site_run_id", "")).strip()
        if sid:
            return sid
    return ""


def _story_id_from_row(row: dict[str, Any]) -> str:
    return str(row.get("item_id", "")).strip() or _safe_name(str(row.get("canonical_basename", "story")))


def _resolved_cleaned_for_row(root_dir: Path, row: dict[str, Any]) -> tuple[Path | None, str]:
    canonical = str(row.get("canonical_basename", "")).strip() or "story"
    cleaned_str = str(row.get("cleaned_path", "")).strip()
    run_story = str(row.get("site_run_story_dir", "")).strip()
    p, src = _resolve_cleaned_path(root_dir, cleaned_str, run_story, canonical)
    if p and p.exists():
        return p, src
    alt = str(row.get("resolved_cleaned_path", "")).strip()
    if alt:
        ap = Path(alt)
        if ap.exists():
            return ap, "row.resolved_cleaned_path"
    return None, "missing"


def _output_story_root(
    *,
    root_dir: Path,
    youtube_run_id: str,
    canonical: str,
    layout_mode: str,
) -> Path:
    if layout_mode == "fixture":
        return (root_dir / "output" / "youtube" / "_smoke" / youtube_run_id / canonical).resolve()
    return (root_dir / "output" / "youtube" / canonical).resolve()


def _legacy_bridge_paths(root_dir: Path, youtube_run_id: str, story_id: str) -> dict[str, str]:
    """Планируемые изолированные staging-папки (без копирования файлов в этой команде)."""
    base = root_dir.resolve()
    return {
        "youtube_safe_story_dir": str(base / "legacy" / "youtube_tts" / f"stories_{STAGING_SUFFIX}" / youtube_run_id / story_id),
        "promo_story_dir": str(base / "legacy" / "youtube_tts" / f"promo_stories_{STAGING_SUFFIX}" / youtube_run_id / story_id),
        "director_story_dir": str(base / "legacy" / "director_2_0" / f"stories_{STAGING_SUFFIX}" / youtube_run_id / story_id),
        "autovideo_base_dir": str(base / "legacy" / "AutoVideo" / f"{STAGING_SUFFIX}" / youtube_run_id / story_id),
    }


def _legacy_bridge_gaps(root_dir: Path) -> dict[str, Any]:
    base = root_dir.resolve()
    return {
        "scripts_support_staging_paths": False,
        "missing_bridge": True,
        "legacy_scripts_read_default": {k: str(base / Path(v)) for k, v in LEGACY_SCRIPT_DEFAULT_ROOTS.items()},
        "note": "Текущие legacy/youtube_tts и director_2_0 обычно читают фиксированные корни (stories/, promo_stories/, ...). "
        "Каталоги *_from_orchestrator зарезервированы; до адаптеров/wrapper-ов копирование туда вручную.",
    }


def _expected_artifacts(story_dir: Path) -> dict[str, str]:
    sd = story_dir.resolve()
    return {
        "safe_story": str(sd / "02_safe_story" / "safe_story.txt"),
        "promo_text_ready_for_audio": str(sd / "03_promo" / "text_ready_for_audio.txt"),
        "final_narration_text": str(sd / "03_promo" / "text_ready_for_audio.txt"),
        "audio_mp3": str(sd / "04_audio" / "narration.mp3"),
        "characters_txt": str(sd / "05_characters" / "characters.txt"),
        "prompts_list_txt": str(sd / "06_director" / "prompts_list.txt"),
        "frames_dir": str(sd / "07_frames"),
        "final_video_mp4": str(sd / "08_video" / "final_video.mp4"),
    }


def _youtube_outputs_block(story_dir: Path) -> dict[str, str]:
    sd = story_dir.resolve()
    return {
        "story_dir": str(sd),
        "source_dir": "00_source",
        "selection_dir": "01_selection",
        "safe_story_dir": "02_safe_story",
        "promo_dir": "03_promo",
        "audio_dir": "04_audio",
        "characters_dir": "05_characters",
        "director_dir": "06_director",
        "frames_dir": "07_frames",
        "video_dir": "08_video",
        "logs_dir": "logs",
    }


def _build_story_manifest_payload(
    *,
    config: OrchestratorConfig,
    youtube_run_id: str,
    row: dict[str, Any],
    layout_mode: str,
    run_mode_label: str,
    modes: dict[str, str],
) -> dict[str, Any]:
    root_dir = config.root_dir
    canonical = str(row.get("canonical_basename", "")).strip() or "story"
    story_id = _story_id_from_row(row)
    site_run_id = str(row.get("site_run_id", "")).strip() or _infer_site_run_id([row])
    story_dir = _output_story_root(
        root_dir=root_dir,
        youtube_run_id=youtube_run_id,
        canonical=canonical,
        layout_mode=layout_mode,
    )
    cleaned_file, cleaned_src = _resolved_cleaned_for_row(root_dir, row)
    deferred_path = _deferred_path(root_dir, site_run_id) if site_run_id else Path()

    raw_sel = str(row.get("raw_result_path", "")).strip()
    norm_sel = str(row.get("normalized_result_path", row.get("gemini_output_path", ""))).strip()

    return {
        "youtube_run_id": youtube_run_id,
        "site_run_id": site_run_id,
        "story_id": story_id,
        "canonical_basename": canonical,
        "mode": run_mode_label,
        "source": {
            "site_deferred_path": str(deferred_path) if site_run_id and deferred_path.exists() else "",
            "source_path": str(row.get("source_path", "")).strip(),
            "cleaned_path": str(row.get("cleaned_path", "")).strip(),
            "resolved_cleaned_path": str(cleaned_file) if cleaned_file else "",
            "resolved_cleaned_source": cleaned_src,
            "site_run_story_dir": str(row.get("site_run_story_dir", "")).strip(),
        },
        "selection": {
            "size_status": str(row.get("youtube_size_status", "")).strip(),
            "word_count": int(row.get("word_count", 0) or 0),
            "estimated_minutes": float(row.get("estimated_minutes", 0) or 0),
            "gemini_selection_status": str(row.get("youtube_selection_status", "")).strip(),
            "raw_selection_path": raw_sel,
            "normalized_selection_path": norm_sel,
        },
        "youtube_outputs": _youtube_outputs_block(story_dir),
        "expected_artifacts": _expected_artifacts(story_dir),
        "tts": {
            "mode": "site_style_tts",
            "engine": "from_runtime_modes",
            "site_tts_engine": modes.get("site_tts_engine", ""),
            "site_tts_runtime": modes.get("site_tts_runtime", ""),
            "input_text_priority": [
                "03_promo/text_ready_for_audio.txt",
                "02_safe_story/safe_story.txt",
            ],
            "expected_audio_path": "04_audio/narration.mp3",
            "status": "pending",
            "pipeline_note": "Озвучка YouTube через тот же контур, что site (site_tts_engine / site_tts_runtime), не отдельный legacy ElevenLabs-процесс как основной путь.",
        },
        "legacy_bridge": _legacy_bridge_paths(root_dir, youtube_run_id, story_id),
        "legacy_bridge_gaps": _legacy_bridge_gaps(root_dir),
        "director_dependency": {
            "requires_text_and_mp3": True,
            "audio_before_director": True,
            "reason": "director_2_0 analyzer вычисляет WPS и prompts_per_chunk по длительности MP3; без narration.mp3 режиссёрский этап неполный.",
        },
        "guards": {
            "single_story_only": True,
            "forbidden_root_scan": True,
            "expected_story_count": 1,
        },
        "status": {
            "safe_staged": False,
            "safe_done": False,
            "promo_done": False,
            "audio_done": False,
            "characters_done": False,
            "director_done": False,
            "frames_done": False,
            "video_done": False,
        },
    }


def _mkdir_story_layout(story_dir: Path) -> None:
    for name in YOUTUBE_STORY_SUBDIRS:
        (story_dir / name).mkdir(parents=True, exist_ok=True)


@dataclass
class YoutubeInitBridgeFixtureOptions:
    youtube_run_id: str = "yt-bridge-fixture-a"
    force: bool = False


def run_youtube_init_bridge_fixture(*, config: OrchestratorConfig, options: YoutubeInitBridgeFixtureOptions) -> dict[str, Any]:
    """Create a minimal fixture run with one synthetic selected YES (no Gemini/TTS)."""
    youtube_run_id = str(options.youtube_run_id).strip() or "yt-bridge-fixture-a"
    run_root = _youtube_run_root(config.root_dir, youtube_run_id)
    fixture_dir = run_root / "_fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    cleaned = fixture_dir / "cleaned.txt"
    if not cleaned.exists() or options.force:
        cleaned.write_text(
            "Fixture cleaned story for YouTube bridge manifest smoke test.\n\n"
            "Second paragraph so word-splitting heuristics have a boundary.\n",
            encoding="utf-8",
        )

    site_run_id = "fixture-site"
    canonical = "YouTube_Bridge_Fixture_Story"
    item_id = "yt_fixture_00001"
    selection_dir = _selection_dir(config.root_dir, youtube_run_id)
    selection_dir.mkdir(parents=True, exist_ok=True)
    yes_path = selection_dir / "youtube_selected_yes.json"
    payload = {
        "items": [
            {
                "item_id": item_id,
                "canonical_basename": canonical,
                "source_path": str((fixture_dir / "source_stub.txt").resolve()),
                "cleaned_path": str(cleaned.resolve()),
                "resolved_cleaned_path": str(cleaned.resolve()),
                "cleaned_path_source": "fixture",
                "site_run_story_dir": "",
                "site_run_id": site_run_id,
                "youtube_run_id": youtube_run_id,
                "word_count": 24,
                "estimated_minutes": 0.16,
                "youtube_size_status": "yes",
                "youtube_selection_status": "yes",
                "reject_reason": "",
                "selection_reject_reason": "",
                "raw_result_path": "",
                "normalized_result_path": "",
                "gemini_output_path": "",
            }
        ],
    }
    if yes_path.exists() and not options.force:
        return {"ok": True, "message": "fixture already exists (use --force)", "youtube_run_id": youtube_run_id, "path": str(yes_path)}
    _write_json(yes_path, payload)
    (fixture_dir / "source_stub.txt").write_text("stub source path for fixture\n", encoding="utf-8")
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "site_run_id": site_run_id,
        "youtube_selected_yes": str(yes_path),
        "fixture_cleaned": str(cleaned.resolve()),
    }


@dataclass
class YoutubeBuildBridgeManifestOptions:
    youtube_run_id: str
    dry_run: bool = True
    fixture_layout: bool = False
    modes_config: Path | None = None


def run_youtube_build_bridge_manifest(
    *, config: OrchestratorConfig, options: YoutubeBuildBridgeManifestOptions
) -> dict[str, Any]:
    youtube_run_id = str(options.youtube_run_id).strip()
    if not youtube_run_id:
        return {"ok": False, "message": "youtube_run_id is required"}

    root_dir = config.root_dir.resolve()
    run_root = _youtube_run_root(root_dir, youtube_run_id)
    selection_dir = _selection_dir(root_dir, youtube_run_id)
    yes_path = selection_dir / "youtube_selected_yes.json"
    status_jsonl = run_root / "youtube_status.jsonl"

    if not yes_path.exists():
        return {"ok": False, "message": f"missing file: {yes_path}"}

    payload = _read_json(yes_path)
    rows = [x for x in payload.get("items", []) if isinstance(x, dict)]
    selected_yes = [r for r in rows if str(r.get("youtube_selection_status", "")).strip().lower() == "yes"]
    count_yes = len(selected_yes)

    modes_path = (options.modes_config or (root_dir / "configs" / "runtime_modes.yaml")).resolve()
    modes = load_runtime_modes(modes_path)

    layout_mode = "fixture" if options.fixture_layout else "production"
    if options.fixture_layout:
        run_mode_label = "fixture"
    elif options.dry_run:
        run_mode_label = "dry-run"
    else:
        run_mode_label = "production-ready"

    site_run_id = _infer_site_run_id(selected_yes if count_yes else rows)
    if not site_run_id:
        parsed_sel = run_root / "_gemini_selection" / "parsed" / "selection_results.json"
        if parsed_sel.exists():
            try:
                pr = _read_json(parsed_sel)
                pr_items = [x for x in pr.get("items", []) if isinstance(x, dict)]
                site_run_id = _infer_site_run_id(pr_items)
            except Exception:
                pass
    deferred_p = _deferred_path(root_dir, site_run_id) if site_run_id else Path()

    story_paths: list[str] = []
    missing_notes: list[str] = []
    risk_notes: list[str] = [
        "Legacy gemini_auto / director обходят целые деревья stories/ при отсутствии изоляции — используйте только story_dir из manifest.",
        "Перезапись: --force у prepare-safe-input и повторный build могут обновить JSON/manifest.",
    ]

    if count_yes == 0:
        run_manifest = {
            "youtube_run_id": youtube_run_id,
            "site_run_id": site_run_id,
            "created_at": _now_iso(),
            "mode": run_mode_label,
            "dry_run": bool(options.dry_run),
            "fixture_layout": bool(options.fixture_layout),
            "mkdir_output_layout": False,
            "stories_count": 0,
            "selected_yes_count": 0,
            "stories": [],
            "summary": {
                "message": "Нет selected YES, downstream bridge для production не строится.",
                "site_deferred": str(deferred_p) if site_run_id else "",
            },
            "validation_report": str(run_root / "bridge_manifest_validation_report.txt"),
        }
        _write_json(run_root / "youtube_bridge_manifest.json", run_manifest)
        report_lines = [
            f"youtube_run_id={youtube_run_id}",
            f"site_run_id={site_run_id or '(unknown)'}",
            f"selected_yes_count=0",
            "story_manifests_created=0",
            "",
            "Нет selected YES, downstream bridge не строится.",
            "Production story manifests в output/youtube не создаются.",
            "",
            f"youtube_selected_yes.json: {yes_path}",
            "",
            "Downstream: Gemini / safe / TTS / RunPod / AutoVideo не запускались.",
            "stories/input не использовался этой командой.",
        ]
        rep = run_root / "bridge_manifest_validation_report.txt"
        rep.write_text("\n".join(report_lines), encoding="utf-8")
        _append_status(
            status_jsonl,
            {
                "timestamp": _now_iso(),
                "youtube_run_id": youtube_run_id,
                "stage": "youtube_build_bridge_manifest",
                "state": "stopped_no_selected_yes",
                "selected_yes_count": 0,
            },
        )
        return {
            "ok": True,
            "youtube_run_id": youtube_run_id,
            "selected_yes_count": 0,
            "run_manifest": str(run_root / "youtube_bridge_manifest.json"),
            "validation_report": str(rep),
            "story_manifests": [],
        }

    story_payloads: list[dict[str, Any]] = []
    mkdir_layout = bool(options.fixture_layout) or (not options.dry_run)
    per_story_report: list[str] = []

    for row in selected_yes:
        canonical = str(row.get("canonical_basename", "")).strip() or "story"
        story_id = _story_id_from_row(row)
        story_dir = _output_story_root(
            root_dir=root_dir,
            youtube_run_id=youtube_run_id,
            canonical=canonical,
            layout_mode=layout_mode,
        )
        cleaned_file, cleaned_src = _resolved_cleaned_for_row(root_dir, row)
        if cleaned_file is None:
            missing_notes.append(f"{canonical}: resolved cleaned text missing ({cleaned_src})")

        if mkdir_layout:
            _mkdir_story_layout(story_dir)

        sm = _build_story_manifest_payload(
            config=config,
            youtube_run_id=youtube_run_id,
            row=row,
            layout_mode=layout_mode,
            run_mode_label=run_mode_label,
            modes=modes,
        )
        sm_path = story_dir / "youtube_story_manifest.json"
        _write_json(sm_path, sm)
        story_paths.append(str(sm_path))
        story_payloads.append(
            {
                "story_id": story_id,
                "canonical_basename": canonical,
                "story_manifest": str(sm_path),
                "story_dir": str(story_dir),
            }
        )

        lb = _legacy_bridge_paths(root_dir, youtube_run_id, story_id)
        per_story_report.append(f"--- {canonical} ({story_id}) ---")
        per_story_report.append(f"story_dir={story_dir}")
        per_story_report.append(f"cleaned_resolved={cleaned_file or '(missing)'} ({cleaned_src})")
        for k, v in lb.items():
            per_story_report.append(f"{k}={v}")
        exp = _expected_artifacts(story_dir)
        per_story_report.append("expected_artifacts (existence not checked here):")
        for k, v in exp.items():
            exists = Path(v).exists()
            per_story_report.append(f"  {k}: {v}  exists={exists}")
        if story_dir.exists():
            old_markers = ["03_chunks", "04_frame_prompts", "05_frames", "06_audio", "07_video"]
            hits = [m for m in old_markers if (story_dir / m).exists()]
            if hits:
                risk_notes.append(f"{canonical}: обнаружена старая scaffold-папка {hits} — не трогаем; bridge использует 03_promo…08_video.")
        per_story_report.append("")

    run_manifest = {
        "youtube_run_id": youtube_run_id,
        "site_run_id": site_run_id,
        "created_at": _now_iso(),
        "mode": run_mode_label,
        "dry_run": bool(options.dry_run),
        "fixture_layout": bool(options.fixture_layout),
        "mkdir_output_layout": mkdir_layout,
        "stories_count": len(selected_yes),
        "selected_yes_count": count_yes,
        "stories": story_payloads,
        "runtime_modes_file": str(modes_path),
        "site_tts_engine": modes.get("site_tts_engine", ""),
        "site_tts_runtime": modes.get("site_tts_runtime", ""),
        "summary": {
            "status_breakdown": {
                "selected_yes": count_yes,
                "story_manifests_written": len(story_paths),
            },
            "expected_pipeline_order": [
                "1 youtube_selection_from_site_deferred",
                "2 safe_rewrite",
                "3 promo",
                "4 final_narration_text",
                "5 site_style_tts_audio",
                "6 characters",
                "7 director_technical_prep",
                "8 director_scenes_prompts",
                "9 runpod_comfy_flux_frames",
                "10 video_assembly",
            ],
            "audio_before_director": True,
            "tts_contract": "YouTube narration uses site_tts_engine/site_tts_runtime (same family as site), not legacy youtube_tts_engine as primary path.",
        },
        "validation_report": str(run_root / "bridge_manifest_validation_report.txt"),
    }
    _write_json(run_root / "youtube_bridge_manifest.json", run_manifest)

    report_lines = [
        f"youtube_run_id={youtube_run_id}",
        f"site_run_id={site_run_id}",
        f"selected_yes_count={count_yes}",
        f"story_manifests_created={len(story_paths)}",
        f"layout_mode={layout_mode}",
        f"dry_run={options.dry_run}",
        f"mkdir_output_layout={mkdir_layout}",
        "",
        "=== TTS / audio ===",
        f"site_tts_engine={modes.get('site_tts_engine', '')}",
        f"site_tts_runtime={modes.get('site_tts_runtime', '')}",
        "Приоритет текста для озвучки: 03_promo/text_ready_for_audio.txt затем 02_safe_story/safe_story.txt",
        "Ожидаемый MP3: 04_audio/narration.mp3",
        "Director требует .txt + .mp3 до режиссёрского этапа (см. legacy/director_2_0/analyzer.py).",
        "",
        "=== Story manifests ===",
        *[f"- {p}" for p in story_paths],
        "",
        "=== Missing ===",
        *(missing_notes if missing_notes else ["(none)"]),
        "",
        "=== Risks ===",
        *risk_notes,
        "",
        "=== Legacy staging (не копировалось в этой команде) ===",
        "Зарезервированы каталоги *_from_orchestrator/<youtube_run_id>/<story_id>/ — см. legacy_bridge в youtube_story_manifest.json; legacy_bridge_gaps.missing_bridge=true.",
        "",
        "=== Per-story ===",
        *per_story_report,
        "Downstream: Gemini / safe / TTS / RunPod / Colab / AutoVideo не запускались.",
        "stories/input не изменялся.",
        "",
        "=== End-to-end gaps (ожидаемо до wiring legacy) ===",
        "- safe_rewrite / promo / characters / director / RunPod / AutoVideo: нет автозапуска из orchestrator в этой задаче.",
        "- legacy_bridge_gaps.missing_bridge=true: скрипты не читают *_from_orchestrator без адаптера.",
    ]
    rep = run_root / "bridge_manifest_validation_report.txt"
    rep.write_text("\n".join(report_lines), encoding="utf-8")

    _append_status(
        status_jsonl,
        {
            "timestamp": _now_iso(),
            "youtube_run_id": youtube_run_id,
            "stage": "youtube_build_bridge_manifest",
            "state": "done",
            "selected_yes_count": count_yes,
            "story_manifests": len(story_paths),
        },
    )
    return {
        "ok": True,
        "youtube_run_id": youtube_run_id,
        "selected_yes_count": count_yes,
        "run_manifest": str(run_root / "youtube_bridge_manifest.json"),
        "validation_report": str(rep),
        "story_manifests": story_paths,
    }
