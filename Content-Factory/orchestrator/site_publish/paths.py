"""Site-publish path resolver.

Единая точка истины для путей site-publish.

Режимы:
- legacy (launch_name пуст и launch_dir не задан): пакеты пишутся в
  ``<root>/output/site/<story>/``, bridge для autopublisher идёт в
  ``<root>/legacy/autopublisher/To_Publish/``.
- run-scoped (передан launch_name или launch_dir):
  ``<root>/Запуски/<launch>/02_Сайт/05_Публикация_на_сайт/<story>/``,
  bridge — ``<root>/Запуски/<launch>/02_Сайт/05_Публикация_на_сайт/_to_publish/<story>/``.

Удаление папки Запуски/<launch>/ полностью убирает локальные артефакты публикации
этого запуска и манифест ``site_publish_manifest.json``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from orchestrator.human_launch_layout import (
    D02_05_PUBLISH,
    D02_SITE,
    DIR_ZAPUSKI,
    sanitize_launch_folder_name,
)


SITE_PUBLISH_MANIFEST_NAME = "site_publish_manifest.json"
RUN_SCOPED_TO_PUBLISH_DIRNAME = "_to_publish"


def _sanitize_folder_name(name: str) -> str:
    out = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    out = out.replace("\n", "_").replace("\r", "_")
    return out or "story"


def resolve_launch_dir(
    root: Path,
    *,
    launch_name: str = "",
    launch_dir: Path | None = None,
) -> Path | None:
    """Resolve human launch folder by explicit dir or name.

    Returns None если ни launch_name, ни launch_dir не заданы (legacy mode).
    Возвращает None если папка не существует — вызывающий код решает, что делать
    (например, упасть с понятной ошибкой в run-scoped режиме).
    """
    if launch_dir is not None:
        p = launch_dir if launch_dir.is_absolute() else root / launch_dir
        p = p.resolve()
        return p if p.is_dir() else None
    name = (launch_name or "").strip()
    if not name:
        return None
    p = (root / DIR_ZAPUSKI / sanitize_launch_folder_name(name)).resolve()
    return p if p.is_dir() else None


def is_run_scoped(launch_name: str = "", launch_dir: Path | None = None) -> bool:
    """True если хотя бы один из launch_name/launch_dir непустой."""
    return bool((launch_name or "").strip()) or launch_dir is not None


def resolve_site_publish_root(root: Path, launch: Path | None) -> Path:
    """Корень папки с per-story пакетами публикации сайта.

    - launch is None → ``<root>/output/site``
    - launch given  → ``<launch>/02_Сайт/05_Публикация_на_сайт``
    """
    if launch is None:
        return (root / "output" / "site").resolve()
    return (launch / D02_SITE / D02_05_PUBLISH).resolve()


def resolve_site_publish_output_dir(
    root: Path,
    *,
    launch_name: str | None = None,
    story_id: str | None = None,
    launch_dir: Path | None = None,
) -> Path:
    """Run-scoped per-story package dir с fallback на legacy ``output/site``.

    Если story_id пуст — возвращает корень (без `<story>/`).
    """
    launch = resolve_launch_dir(root, launch_name=launch_name or "", launch_dir=launch_dir)
    base = resolve_site_publish_root(root, launch)
    if story_id is None or not str(story_id).strip():
        return base
    return (base / _sanitize_folder_name(str(story_id))).resolve()


def resolve_to_publish_root(root: Path, launch: Path | None) -> Path:
    """Корень bridge-папки для legacy autopublisher (входит в его --to-publish-dir).

    - launch is None → ``<root>/legacy/autopublisher/To_Publish``
    - launch given  → ``<launch>/02_Сайт/05_Публикация_на_сайт/_to_publish``
    """
    if launch is None:
        return (root / "legacy" / "autopublisher" / "To_Publish").resolve()
    return (resolve_site_publish_root(root, launch) / RUN_SCOPED_TO_PUBLISH_DIRNAME).resolve()


def site_publish_manifest_path(root: Path, launch: Path | None) -> Path:
    """Путь к манифесту site-publish.

    Для run-scoped — внутри launch-папки. Для legacy — единый файл в .orchestrator/.
    """
    if launch is None:
        return (root / ".orchestrator" / SITE_PUBLISH_MANIFEST_NAME).resolve()
    return (resolve_site_publish_root(root, launch) / SITE_PUBLISH_MANIFEST_NAME).resolve()


def describe_layout(
    root: Path,
    *,
    launch_name: str = "",
    launch_dir: Path | None = None,
) -> dict[str, Any]:
    """Diag-структура для dry-run output: куда будем писать и какие global paths уйдут из source-of-truth."""
    launch = resolve_launch_dir(root, launch_name=launch_name, launch_dir=launch_dir)
    run_scoped = is_run_scoped(launch_name=launch_name, launch_dir=launch_dir)
    site_publish_root = resolve_site_publish_root(root, launch)
    to_publish_root = resolve_to_publish_root(root, launch)
    manifest_path = site_publish_manifest_path(root, launch)
    legacy_output_site = (root / "output" / "site").resolve()
    legacy_to_publish = (root / "legacy" / "autopublisher" / "To_Publish").resolve()
    return {
        "mode": "run_scoped" if launch is not None else ("requested_run_scoped_but_launch_missing" if run_scoped else "legacy"),
        "run_scoped_requested": run_scoped,
        "launch_name": (launch_name or "").strip(),
        "launch_dir": str(launch) if launch is not None else "",
        "site_publish_root": str(site_publish_root),
        "to_publish_root": str(to_publish_root),
        "manifest_path": str(manifest_path),
        "legacy_output_site_no_longer_source_of_truth": str(legacy_output_site) if launch is not None else "",
        "legacy_to_publish_no_longer_source_of_truth": str(legacy_to_publish) if launch is not None else "",
    }
