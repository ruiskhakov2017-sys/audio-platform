from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.human_launch_layout import D10_LEGACY, D10_TEMP
from orchestrator.events import EventLogger
from orchestrator.length_filter import LengthFilterOptions, run_length_filter
from orchestrator.runtime_modes import DEFAULT_MODES, load_runtime_modes
from orchestrator.status import StatusStore
from orchestrator.wrappers import build_wrappers_for_pipeline


@dataclass
class RunOptions:
    pipeline: str
    story_id: str
    execute: bool
    run_profile: str
    stories_dir: Path | None = None
    allow_real_stages: list[str] | None = None
    # Корень Запуски/<name>/: output/site через 10_Временные_файлы/legacy/...
    launch_dir: Path | None = None


class Runner:
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self.logger = EventLogger(config.events_file)
        self.status = StatusStore(config.status_file)

    def plan(self, pipeline: str, story_id: str) -> list[dict[str, str]]:
        wrappers = build_wrappers_for_pipeline(pipeline, self.config.root_dir, self.config.legacy_entrypoints)
        return [w.plan(story_id=story_id, pipeline=pipeline) for w in wrappers]

    def _is_stage_real_allowed(self, stage: str, opts: RunOptions) -> bool:
        if not opts.execute:
            return False
        profile = opts.run_profile
        allowed = set(self.config.real_stage_whitelist)
        if opts.allow_real_stages:
            allowed.update(opts.allow_real_stages)
        if profile == "dry-run-all":
            return False
        if profile == "site-real" and stage.startswith("youtube_"):
            return False
        if profile == "youtube-real" and stage.startswith("site_"):
            return False
        return stage in allowed

    def _check_tts_engine_adapter(self, stage: str) -> str | None:
        """
        Return a user-friendly message when a non-connected TTS engine is selected.
        """
        key_by_stage = {
            "site_tts": "site_tts_engine",
            "youtube_tts": "youtube_tts_engine",
        }
        mode_key = key_by_stage.get(stage)
        if not mode_key:
            return None
        modes = load_runtime_modes(self.config.root_dir / "configs" / "runtime_modes.yaml")
        engine = modes.get(mode_key, DEFAULT_MODES[mode_key])
        if engine == "elevenlabs":
            return None
        if stage == "site_tts":
            if engine == "vibevoice":
                return (
                    "VibeVoice: адаптер experimental/disabled; production batch site TTS на нём не строится. "
                    "Используйте site_tts_engine: kokoro (локально) или elevenlabs (legacy)."
                )
            if engine == "kokoro":
                rt = str(modes.get("site_tts_runtime", "")).strip().lower()
                if rt and rt != "local":
                    return (
                        f"TTS Kokoro: ожидается site_tts_runtime=local для локального inference (сейчас {rt})."
                    )
                return None
            if engine == "kokoro_colab_drive":
                return None
            if engine in {"edge_tts", "fish_audio"}:
                return (
                    f"TTS engine {engine} для site: слот в modular registry зарезервирован, адаптер пока не подключён."
                )
        if engine == "fish_audio_s2_pro":
            return (
                "TTS: fish_audio_s2_pro — только RunPod/remote GPU; локальный inference не используется "
                "(адаптер orchestrator/adapters/fish_audio_runpod.py — заготовка)."
            )
        return f"TTS engine {engine} выбран, но адаптер пока не подключён"

    def run(self, opts: RunOptions) -> tuple[str, bool]:
        """
        Возвращает (run_id, pipeline_ok).
        pipeline_ok=False если length_filter или любой wrapper-этап завершился с failed.
        """
        run_id = uuid.uuid4().hex
        print(
            f"[RUN] started: pipeline={opts.pipeline} story_id={opts.story_id} run_id={run_id}",
            flush=True,
        )
        artifact_root: Path | None = None
        if opts.launch_dir is not None:
            artifact_root = (opts.launch_dir.resolve() / D10_TEMP / D10_LEGACY).resolve()
            from orchestrator.human_launch_path_scope import validate_isolated_artifact_root

            err = validate_isolated_artifact_root(
                launch_dir=opts.launch_dir,
                content_factory_root=self.config.root_dir,
                artifact_root=artifact_root,
            )
            if err:
                print(err, flush=True)
                return run_id, False
        wrappers = build_wrappers_for_pipeline(
            opts.pipeline,
            self.config.root_dir,
            self.config.legacy_entrypoints,
            artifact_root=artifact_root,
        )
        manifest: dict[str, Any] = {
            "run_id": run_id,
            "pipeline": opts.pipeline,
            "story_id": opts.story_id,
            "execute_requested": opts.execute,
            "run_profile": opts.run_profile,
            "allowed_real_stages": sorted(
                set(self.config.real_stage_whitelist + (opts.allow_real_stages or []))
            ),
            "stages": [],
        }

        resolver = None
        if opts.launch_dir is not None:
            from orchestrator.isolated_launch_mode import is_isolated_launch, resolver_if_isolated

            launch_path = opts.launch_dir.resolve()
            if is_isolated_launch(self.config, launch_root=launch_path):
                resolver = resolver_if_isolated(self.config, launch_root=launch_path)

        from orchestrator.isolated_launch_context import (
            isolated_session,
            resolve_events_file,
            resolve_reports_dir,
            resolve_status_file,
        )

        with isolated_session(resolver, batch_launch_id=resolver.launch_id if resolver else None):
            logger = EventLogger(resolve_events_file(self.config))
            status = StatusStore(resolve_status_file(self.config))
            reports_dir = resolve_reports_dir(self.config)

            if opts.stories_dir:
                print("[RUN] stage length_filter: started", flush=True)
                lf = run_length_filter(
                    config=self.config,
                    options=LengthFilterOptions(
                        stories_dir=opts.stories_dir,
                        short_dir=None,
                        execute=opts.execute and self._is_stage_real_allowed("length_filter", opts),
                        words_per_minute=self.config.pre_filter_words_per_minute,
                        min_minutes=self.config.pre_filter_min_minutes,
                        min_words=self.config.pre_filter_min_words,
                        extensions=self.config.pre_filter_extensions,
                        artifacts_dir=reports_dir if resolver is not None else None,
                    ),
                    pipeline=opts.pipeline,
                    story_id=opts.story_id,
                )
                manifest["stages"].append(
                    {
                        "stage": "length_filter",
                        "branch": "common",
                        "unsafe": True,
                        "execute_allowed": opts.execute and self._is_stage_real_allowed("length_filter", opts),
                        "state": "done" if lf.get("ok", False) else "failed",
                        "message": lf.get("summary", lf.get("message", "")),
                    }
                )
                print(
                    f"[RUN] stage length_filter: {'done' if lf.get('ok', False) else 'failed'}",
                    flush=True,
                )
                if not lf.get("ok", False):
                    reports_dir.mkdir(parents=True, exist_ok=True)
                    manifest_path = reports_dir / f"run_manifest_{run_id}.json"
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print("[RUN] pipeline failed at length_filter", flush=True)
                    return run_id, False
            for w in wrappers:
                stage = w.contract.stage
                allow_real = self._is_stage_real_allowed(stage, opts)
                tts_adapter_message = self._check_tts_engine_adapter(stage)
                if tts_adapter_message:
                    print(f"[RUN] stage {stage}: partial_connected ({tts_adapter_message})", flush=True)
                    status.append(
                        story_id=opts.story_id,
                        pipeline=opts.pipeline,
                        stage=stage,
                        state="partial_connected",
                        message=tts_adapter_message,
                    )
                    logger.emit(
                        run_id=run_id,
                        story_id=opts.story_id,
                        pipeline=opts.pipeline,
                        stage=stage,
                        action="finish",
                        result="partial_connected",
                        message=tts_adapter_message,
                    )
                    manifest["stages"].append(
                        {
                            "stage": stage,
                            "branch": w.contract.branch,
                            "unsafe": w.contract.unsafe,
                            "execute_allowed": allow_real,
                            "state": "partial_connected",
                            "message": tts_adapter_message,
                        }
                    )
                    continue
                print(f"[RUN] stage {stage}: started", flush=True)
                status.append(
                    story_id=opts.story_id,
                    pipeline=opts.pipeline,
                    stage=stage,
                    state="running",
                    message="stage started",
                )
                logger.emit(
                    run_id=run_id,
                    story_id=opts.story_id,
                    pipeline=opts.pipeline,
                    stage=stage,
                    action="start",
                    result="ok",
                    message="stage start",
                )
                result = w.run(
                    story_id=opts.story_id,
                    pipeline=opts.pipeline,
                    execute=opts.execute,
                    allow_real=allow_real,
                    stories_dir=opts.stories_dir,
                )
                state = result.state
                status.append(
                    story_id=opts.story_id,
                    pipeline=opts.pipeline,
                    stage=stage,
                    state=state,
                    message=result.message,
                )
                logger.emit(
                    run_id=run_id,
                    story_id=opts.story_id,
                    pipeline=opts.pipeline,
                    stage=stage,
                    action="finish",
                    result=state,
                    message=result.message,
                )
                manifest["stages"].append(
                    {
                        "stage": stage,
                        "branch": w.contract.branch,
                        "unsafe": w.contract.unsafe,
                        "execute_allowed": allow_real,
                        "state": state,
                        "message": result.message,
                    }
                )
                print(f"[RUN] stage {stage}: {state}", flush=True)
                if not result.ok:
                    print(f"[RUN] stopped after stage failure: {stage}", flush=True)
                    break
            pipeline_ok = not any(str(s.get("state")) == "failed" for s in manifest["stages"])
            reports_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = reports_dir / f"run_manifest_{run_id}.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            stage_counts: dict[str, int] = {}
            for s in manifest["stages"]:
                key = s["state"]
                stage_counts[key] = stage_counts.get(key, 0) + 1
            v1_report = {
                "run_id": run_id,
                "pipeline": opts.pipeline,
                "profile": opts.run_profile,
                "counts_by_state": stage_counts,
                "real_working_stages": [s["stage"] for s in manifest["stages"] if s["state"] == "done"],
                "partial_connected_stages": [
                    s["stage"] for s in manifest["stages"] if s["state"] == "partial_connected"
                ],
                "blocked_stages": [s["stage"] for s in manifest["stages"] if s["state"] == "blocked_external"],
                "failed_stages": [s["stage"] for s in manifest["stages"] if s["state"] == "failed"],
            }
            (reports_dir / f"v1_report_{run_id}.json").write_text(
                json.dumps(v1_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if pipeline_ok:
                print(f"[RUN] finished: run_id={run_id}", flush=True)
            else:
                print(f"[RUN] finished with failures: run_id={run_id}", flush=True)
            return run_id, pipeline_ok
