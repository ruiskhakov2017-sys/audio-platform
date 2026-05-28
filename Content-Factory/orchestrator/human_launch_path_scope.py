"""
Жёсткий scope для isolated site launch: все записи под Запуски/<name>/10_.../legacy/.
"""
from __future__ import annotations

from pathlib import Path

from orchestrator.human_launch_layout import D10_LEGACY, D10_TEMP


def launch_legacy_anchor(launch: Path) -> Path:
    return (launch.resolve() / D10_TEMP / D10_LEGACY).resolve()


def global_site_write_roots(content_factory_root: Path) -> list[Path]:
    root = content_factory_root.resolve()
    return [
        (root / "runs" / "site").resolve(),
        (root / "output" / "site").resolve(),
        (root / "legacy" / "content_combiner").resolve(),
    ]


def path_is_descendant(path: Path, ancestor: Path) -> bool:
    try:
        path.resolve().relative_to(ancestor.resolve())
        return True
    except ValueError:
        return False


def path_uses_forbidden_global_write(
    candidate: Path,
    *,
    content_factory_root: Path,
    launch_legacy: Path,
) -> bool:
    """
    True если путь указывает на запись в глобальные site/combiner корни CF,
    но не внутри данного launch legacy anchor.
    """
    c = candidate.resolve()
    leg = launch_legacy.resolve()
    if path_is_descendant(c, leg):
        return False
    for g in global_site_write_roots(content_factory_root):
        if path_is_descendant(c, g):
            return True
    return False


def validate_isolated_artifact_root(
    *,
    launch_dir: Path,
    content_factory_root: Path,
    artifact_root: Path | None,
) -> str | None:
    """
    Для run --pipeline site с --launch-dir: artifact_root должен совпадать с launch legacy anchor.
    Возвращает текст ошибки или None.
    """
    if artifact_root is None:
        return None
    expected = launch_legacy_anchor(launch_dir)
    ar = artifact_root.resolve()
    if ar != expected:
        return (
            "GLOBAL_PATH_WRITE_BLOCKED: artifact_root "
            f"{ar} != expected launch legacy anchor {expected} "
            f"(content_factory_root={content_factory_root.resolve()})"
        )
    return None
