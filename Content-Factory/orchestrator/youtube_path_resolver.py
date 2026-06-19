"""Central YouTube path resolution for legacy vs isolated launch layouts."""

from __future__ import annotations

from pathlib import Path

from orchestrator.config import OrchestratorConfig
from orchestrator.isolated_launch_guard import (
    WriteGuardContext,
    guarded_mkdir,
    guarded_write_json,
)
from orchestrator.isolated_launch_context import get_batch_launch_id
from orchestrator.isolated_launch_mode import (
    is_isolated_launch,
    launch_root_for_id,
    normalize_batch_launch_id,
    resolver_if_isolated,
)
from orchestrator.isolated_launch_paths import LaunchPathResolver
from orchestrator.launch_contract import path_is_inside, story_slug

WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT = "WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT"


class WriteOutsideYoutubeLaunchRootError(RuntimeError):
    """Raised when a YouTube production write targets output/youtube or outside launch root."""


def legacy_global_youtube_story_root(config: OrchestratorConfig) -> Path:
    """Read-only legacy recovery root (``output/youtube``). Not for production writes."""
    return _global_youtube_story_root(config)


def _global_youtube_story_root(config: OrchestratorConfig) -> Path:
    return (config.root_dir / "output" / "youtube").resolve()


def resolve_youtube_launch_root(config: OrchestratorConfig, youtube_run_id: str) -> Path:
    """``Запуски/<youtube_run_id>/`` for batch-scoped production."""
    batch_id = normalize_batch_launch_id(youtube_run_id)
    return launch_root_for_id(config, batch_id)


def assert_youtube_production_write_allowed(
    config: OrchestratorConfig,
    path: Path | str,
    *,
    youtube_run_id: str,
    module: str = "",
    function: str = "",
) -> Path:
    """
    Hard guard: with ``youtube_run_id``, production writes must stay inside
    ``Запуски/<id>/`` (story artifacts under ``03_youtube/<story>/``).
    Writes to global ``output/youtube`` are always blocked.
    """
    batch_id = normalize_batch_launch_id(youtube_run_id)
    if not batch_id:
        raise WriteOutsideYoutubeLaunchRootError(f"{WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT}: youtube_run_id is required")
    launch_root = launch_root_for_id(config, batch_id).resolve()
    resolved = Path(path).expanduser().resolve()
    legacy_root = _global_youtube_story_root(config)
    if path_is_inside(resolved, legacy_root) and not path_is_inside(resolved, launch_root):
        ctx = f" module={module}" if module else ""
        ctx += f" function={function}" if function else ""
        raise WriteOutsideYoutubeLaunchRootError(
            f"{WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT}: attempted_path={resolved} launch_root={launch_root}"
            f" legacy_root={legacy_root}{ctx}"
        )
    if not path_is_inside(resolved, launch_root):
        ctx = f" module={module}" if module else ""
        ctx += f" function={function}" if function else ""
        raise WriteOutsideYoutubeLaunchRootError(
            f"{WRITE_OUTSIDE_YOUTUBE_LAUNCH_ROOT}: attempted_path={resolved} launch_root={launch_root}{ctx}"
        )
    return resolved


def resolve_legacy_youtube_recovery_story_dir(
    config: OrchestratorConfig,
    story_id: str,
) -> Path | None:
    """Best-effort legacy read-only story dir under ``output/youtube`` (recovery source only)."""
    root = _global_youtube_story_root(config)
    if not root.is_dir():
        return None
    key = story_id.strip()
    direct = root / key
    if direct.is_dir() and (direct / "youtube_story_manifest.json").is_file():
        return direct.resolve()
    matches: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("_"):
            continue
        manifest = child / "youtube_story_manifest.json"
        if not manifest.is_file():
            if child.name.casefold() == story_slug(key).casefold():
                matches.append(child)
            continue
        try:
            import json

            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        sid = str(data.get("story_id", "")).strip()
        canonical = str(data.get("canonical_basename", "")).strip()
        if key in {sid, canonical, child.name} or canonical.casefold() == key.casefold() or sid.casefold() == key.casefold():
            matches.append(child)
    if len(matches) == 1:
        return matches[0].resolve()
    if direct.is_dir():
        return direct.resolve()
    return None


def _effective_launch_id(launch_id: str | None) -> str | None:
    raw = (launch_id or get_batch_launch_id() or "").strip()
    return normalize_batch_launch_id(raw) if raw else None


def resolve_bridge_story_dir(
    config: OrchestratorConfig,
    story_id: str,
    *,
    launch_id: str | None = None,
) -> Path:
    """Story contract root for YouTube bridge modules."""
    lid = _effective_launch_id(launch_id)
    return resolve_youtube_technical_story_dir(config, story_id, launch_id=lid)


def resolve_youtube_run_root(config: OrchestratorConfig, youtube_run_id: str) -> Path:
    """``runs/youtube/<id>`` globally, or ``…/04_Технические_файлы/runs/youtube/<id>`` when isolated."""
    batch_id = _effective_launch_id(youtube_run_id) or normalize_batch_launch_id(youtube_run_id)
    resolver = resolver_if_isolated(config, launch_id=batch_id)
    if resolver is not None:
        return (resolver.technical_runs_youtube_dir() / youtube_run_id).resolve()
    return (config.root_dir / "runs" / "youtube" / youtube_run_id).resolve()


def legacy_run_root_for_batch(config: OrchestratorConfig, youtube_run_id: str) -> Path:
    """Alias used by ``youtube_full_auto.layout.legacy_run_root``."""
    return resolve_youtube_run_root(config, youtube_run_id)


def resolve_youtube_technical_story_dir(
    config: OrchestratorConfig,
    story_id: str,
    *,
    launch_id: str | None = None,
) -> Path:
    """
    Legacy contract root (``04_audio/``, ``02_safe_story/``, …) for bridges/subprocess staging.

    Isolated → ``04_Технические_файлы/output/youtube/<story>/``.
    Launch-scoped full-auto → ``Запуски/<launch>/03_youtube/<story_slug>/``.
    Legacy → ``output/youtube/<story>/``.
    """
    batch_id = _effective_launch_id(launch_id)
    resolver = resolver_if_isolated(config, launch_id=batch_id) if batch_id else None
    if resolver is not None:
        return (resolver.technical_output_youtube_dir() / story_id).resolve()
    if batch_id:
        launch_root = launch_root_for_id(config, batch_id)
        yt_root = launch_root / "03_youtube"
        direct = yt_root / story_slug(story_id)
        key = story_id.strip()
        matches: list[Path] = []
        if yt_root.is_dir():
            for child in yt_root.iterdir():
                if not child.is_dir():
                    continue
                if child.name.casefold() == story_slug(key).casefold():
                    matches.append(child)
                    continue
                manifest = child / "youtube_story_manifest.json"
                if not manifest.is_file():
                    continue
                try:
                    import json

                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                sid = str(data.get("story_id", "")).strip()
                canonical = str(data.get("canonical_basename", "")).strip()
                if key in {sid, canonical} or canonical.casefold() == key.casefold() or sid.casefold() == key.casefold():
                    matches.append(child)
        if len(matches) == 1:
            return matches[0].resolve()
        return direct.resolve()

    root = _global_youtube_story_root(config)
    direct = root / story_id
    if direct.is_dir():
        return direct.resolve()

    key = story_id.strip()
    matches: list[Path] = []
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            manifest = child / "youtube_story_manifest.json"
            if not manifest.is_file():
                continue
            try:
                import json

                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            sid = str(data.get("story_id", "")).strip()
            canonical = str(data.get("canonical_basename", "")).strip()
            if key in {sid, canonical} or canonical.casefold() == key.casefold() or sid.casefold() == key.casefold():
                matches.append(child)
    if len(matches) == 1:
        return matches[0].resolve()
    return direct.resolve()


def resolve_youtube_human_story_path(
    config: OrchestratorConfig,
    story_id: str,
    *relative_parts: str,
    launch_id: str | None = None,
) -> Path:
    """Human-facing path under ``02_YouTube/Рассказы/<story>/`` when isolated."""
    batch_id = _effective_launch_id(launch_id)
    resolver = resolver_if_isolated(config, launch_id=batch_id) if batch_id else None
    if resolver is None:
        base = resolve_youtube_technical_story_dir(config, story_id, launch_id=launch_id)
        return base.joinpath(*relative_parts).resolve() if relative_parts else base
    rel = "/".join(relative_parts).replace("\\", "/") if relative_parts else ""
    if rel:
        mapped = resolver.youtube_human_legacy_relative(story_id, rel)
        return mapped.resolve()
    return resolver.youtube_human_story_dir(story_id)


def resolve_youtube_story_write_path(
    config: OrchestratorConfig,
    story_id: str,
    legacy_relative: str,
    *,
    launch_id: str | None = None,
) -> Path:
    """
    Redirect writer target: human tree when isolated, else global/technical legacy layout.

    ``legacy_relative`` example: ``02_safe_story/safe_story.txt``, ``04_audio/narration/narration.mp3``.
    """
    batch_id = _effective_launch_id(launch_id)
    if batch_id and is_isolated_launch(config, launch_id=batch_id):
        return resolve_youtube_human_story_path(
            config,
            story_id,
            legacy_relative,
            launch_id=batch_id,
        )
    base = resolve_youtube_technical_story_dir(config, story_id, launch_id=launch_id)
    target = (base / legacy_relative.replace("\\", "/")).resolve()
    if batch_id:
        assert_youtube_production_write_allowed(
            config,
            target,
            youtube_run_id=batch_id,
            module="orchestrator.youtube_path_resolver",
            function="resolve_youtube_story_write_path",
        )
    return target


def write_mini_deferred(
    config: OrchestratorConfig,
    site_run_id: str,
    item: dict,
    *,
    batch_launch_id: str | None = None,
) -> Path:
    """Write mini deferred.json for YouTube full-auto selection (isolated-aware)."""
    batch_id = _effective_launch_id(batch_launch_id)
    resolver = resolver_if_isolated(config, launch_id=batch_id) if batch_id else None
    payload = {"queue": "deferred", "items": [item]}

    if resolver is not None:
        out = (
            resolver.technical_runs_site_dir()
            / site_run_id
            / "_phase_a"
            / "ready_queues"
            / "deferred.json"
        ).resolve()
        guarded_write_json(
            out,
            payload,
            resolver.launch_root(),
            isolated=True,
            context=WriteGuardContext(
                module="orchestrator.youtube_selection_batch",
                function="write_mini_deferred",
                operation="write_json",
            ),
            resolver=resolver,
            project_root=config.root_dir,
        )
        return out

    out = (
        config.root_dir
        / "runs"
        / "site"
        / site_run_id
        / "_phase_a"
        / "ready_queues"
        / "deferred.json"
    ).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def resolve_bridge_legacy_write_path(
    config: OrchestratorConfig,
    story_id: str,
    legacy_relative: str,
    legacy_fallback: Path,
    *,
    launch_id: str | None = None,
) -> Path:
    """Human tree when isolated; else legacy contract path under story_dir."""
    batch_id = _effective_launch_id(launch_id)
    if batch_id and is_isolated_launch(config, launch_id=batch_id):
        return resolve_youtube_story_write_path(
            config,
            story_id,
            legacy_relative,
            launch_id=batch_id,
        )
    if batch_id:
        target = (
            resolve_youtube_technical_story_dir(config, story_id, launch_id=batch_id)
            / legacy_relative.replace("\\", "/")
        ).resolve()
        assert_youtube_production_write_allowed(
            config,
            target,
            youtube_run_id=batch_id,
            module="orchestrator.youtube_path_resolver",
            function="resolve_bridge_legacy_write_path",
        )
        return target
    return legacy_fallback.resolve()


def ensure_isolated_run_dir(
    config: OrchestratorConfig,
    path: Path,
    *,
    launch_id: str,
) -> Path:
    """mkdir only when isolated and path is inside launch (for runtime dirs that must exist before write)."""
    resolver = resolver_if_isolated(config, launch_id=launch_id)
    if resolver is None:
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()
    guarded_mkdir(
        path,
        resolver.launch_root(),
        isolated=True,
        context=WriteGuardContext(module="orchestrator.youtube_path_resolver", function="ensure_isolated_run_dir"),
        resolver=resolver,
        project_root=config.root_dir,
    )
    return path.resolve()


def batch_queue_reports_root(config: OrchestratorConfig, youtube_run_id: str) -> Path:
    """Batch queue/reports root: ``03_Отчеты/queue`` when isolated else ``Запуски/<id>/reports``."""
    batch_id = normalize_batch_launch_id(youtube_run_id)
    resolver = resolver_if_isolated(config, launch_id=batch_id)
    if resolver is not None:
        return (resolver.reports_dir() / "queue").resolve()
    return launch_root_for_id(config, batch_id) / "reports"


def batch_logs_root(config: OrchestratorConfig, youtube_run_id: str) -> Path:
    resolver = resolver_if_isolated(config, launch_id=normalize_batch_launch_id(youtube_run_id))
    if resolver is not None:
        return (resolver.reports_dir() / "logs").resolve()
    return launch_root_for_id(config, normalize_batch_launch_id(youtube_run_id)) / "logs"
