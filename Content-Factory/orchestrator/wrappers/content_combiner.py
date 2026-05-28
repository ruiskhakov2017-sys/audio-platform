from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from orchestrator.contracts import StageContract
from orchestrator.human_launch_layout import (
    human_launch_dir_from_legacy_anchor,
    launch_human_combiner_export_dir,
    launch_human_combiner_images_in_dir,
)
from orchestrator.wrappers.base import BaseWrapper, WrapperResult


def artifact_root_for_combiner_env(site_stories_dir: Path, project_root: Path) -> Path:
    """Если site = …/output/site → корень артефактов = родитель output; иначе project_root."""
    site = site_stories_dir.resolve()
    root = project_root.resolve()
    if site.name == "site" and site.parent.name == "output":
        return site.parent.parent
    return root


def build_content_combiner_env(
    root_dir: Path,
    artifact_root: Path,
    entrypoint: Path,
    *,
    site_stories_dir: Path | None = None,
) -> dict[str, str]:
    root = root_dir.resolve()
    artifact = artifact_root.resolve()
    combiner_root = entrypoint.resolve().parent
    site_stories = (site_stories_dir or (artifact / "output" / "site")).resolve()
    env = os.environ.copy()
    env["CF_STORIES_DIR"] = str(site_stories)
    if artifact.resolve() != root.resolve():
        work = (artifact / "_content_combiner_runtime").resolve()
        work.mkdir(parents=True, exist_ok=True)
        for sub in ("AUDIO_IN", "TEXTS_OUT_DIR"):
            (work / sub).mkdir(parents=True, exist_ok=True)
        launch = human_launch_dir_from_legacy_anchor(artifact)
        if launch is not None:
            export_dir = launch_human_combiner_export_dir(launch)
            images_dir = launch_human_combiner_images_in_dir(launch)
            export_dir.mkdir(parents=True, exist_ok=True)
            images_dir.mkdir(parents=True, exist_ok=True)
            env["CF_EXPORT_DIR"] = str(export_dir)
            env["CF_IMAGES_IN"] = str(images_dir)
        else:
            (work / "IMAGES_IN").mkdir(parents=True, exist_ok=True)
            env["CF_EXPORT_DIR"] = str(work)
            env["CF_IMAGES_IN"] = str(work / "IMAGES_IN")
        env["CF_AUDIO_IN"] = str(work / "AUDIO_IN")
        env["CF_TEXTS_OUT_DIR"] = str(work / "TEXTS_OUT_DIR")
    else:
        env["CF_IMAGES_IN"] = str(combiner_root / "IMAGES_IN")
        env["CF_AUDIO_IN"] = str(combiner_root / "AUDIO_IN")
        env["CF_TEXTS_OUT_DIR"] = str(combiner_root / "TEXTS_OUT_DIR")
        env["CF_EXPORT_DIR"] = str(combiner_root)

    exp_p = Path(env["CF_EXPORT_DIR"])
    img_p = Path(env["CF_IMAGES_IN"])
    exp_p.mkdir(parents=True, exist_ok=True)
    img_p.mkdir(parents=True, exist_ok=True)
    _write_russian_combiner_hints(exp_p, img_p)
    return env


def run_content_combiner_modes(
    *,
    root_dir: Path,
    modes: list[str],
    site_stories_dir: Path | None = None,
    artifact_root: Path | None = None,
    capture_output: bool = True,
) -> tuple[bool, str]:
    """
    Запуск legacy content_combiner.py с заданными --mode.
    artifact_root=None → выводится из site_stories_dir (…/output/site → родитель output).
    """
    root = root_dir.resolve()
    site = (site_stories_dir or (root / "output" / "site")).resolve()
    artifact = (artifact_root.resolve() if artifact_root is not None else artifact_root_for_combiner_env(site, root)).resolve()
    entrypoint = (root / "legacy" / "content_combiner" / "content_combiner.py").resolve()
    if not entrypoint.is_file():
        return False, f"combiner missing: {entrypoint}"
    env = build_content_combiner_env(root, artifact, entrypoint, site_stories_dir=site)
    py = sys.executable
    for mode in modes:
        proc = subprocess.run(
            [py, str(entrypoint), "--mode", mode],
            env=env,
            cwd=str(root),
            capture_output=capture_output,
            text=True,
        )
        if proc.returncode != 0:
            err = ((proc.stderr or "") + (proc.stdout or "")).strip()[:800] or f"exit {proc.returncode}"
            return False, f"content_combiner --mode {mode}: {err}"
    return True, ""


def _write_russian_combiner_hints(export_dir: Path, images_dir: Path) -> None:
    tab = export_dir / "ЧИТАЙ_МЕНЯ_таблица_Excel.txt"
    img = images_dir / "ЧИТАЙ_МЕНЯ_куда_класть_картинки.txt"
    exp_abs = str(export_dir.resolve())
    img_abs = str(images_dir.resolve())
    tab_txt = (
        "Таблица промптов для обложек (формат legacy content_combiner).\n"
        "При запуске из «Запуски/<имя>/…» файлы здесь: 02_Сайт/03_Визуал_для_сайта/ (этот каталог).\n"
        "При запуске только из корня репозитория без изолированного запуска — legacy/content_combiner/.\n"
        "Появляются после export-prompts / process:\n"
        "  • stories_export.csv\n"
        "  • stories_export.xlsx  (нужен пакет openpyxl)\n"
        "Формат таблицы:\n"
        "  Название папки ; Технический промпт ; Визуал ; Итоговый промпт\n"
        "(разделитель в CSV — точка с запятой, UTF-8.)\n"
        "Данные из info.txt в папках рассказов в output/site (Озвучка: / Визуал:).\n"
        f"\nСейчас таблица пишется сюда:\n  {exp_abs}\n"
    )
    img_txt = (
        "СЮДА загружайте готовые файлы ОБЛОЖЕК (stem = имя папки рассказа в output/site).\n"
        "При изолированном запуске обычно это подпапка «Обложки_ЗАГРУЗИТЕ_СЮДА» внутри 02_Сайт/03_Визуал_для_сайта/.\n"
        "distribute-images перенесёт файлы в папки рассказов после импорта mp3 (или на этапе content_combiner).\n"
        f"\nСейчас кладите картинки СЮДА:\n  {img_abs}\n"
    )
    try:
        tab.write_text(tab_txt, encoding="utf-8")
        img.write_text(img_txt, encoding="utf-8")
    except OSError:
        pass


class ContentCombinerWrapper(BaseWrapper):
    contract = StageContract(
        stage="content_combiner",
        description="Legacy content packaging for site route",
        branch="site",
        unsafe=True,
        destructive_ops=["move", "rename", "csv_write"],
        dry_run_only=False,
        entrypoint="content_combiner/content_combiner.py",
    )

    def __init__(
        self,
        entrypoint: Path | None = None,
        *,
        root_dir: Path | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        super().__init__(entrypoint)
        if root_dir is not None:
            self._root = root_dir.resolve()
        elif entrypoint is not None:
            self._root = entrypoint.resolve().parents[2]
        else:
            self._root = Path(".").resolve()
        self._artifact_root = (artifact_root if artifact_root is not None else self._root).resolve()

    def run(
        self,
        *,
        story_id: str,
        pipeline: str,
        execute: bool,
        allow_real: bool,
        stories_dir: Path | None = None,
    ) -> WrapperResult:
        if execute and not allow_real:
            return WrapperResult(
                ok=True,
                state="blocked_external",
                message="content_combiner: execute requested but stage is not whitelisted",
            )
        if not execute:
            return WrapperResult(
                ok=True,
                state="dry-run",
                message="content_combiner: dry-run (legacy script not started)",
            )
        if self.entrypoint is None or not self.entrypoint.is_file():
            return WrapperResult(
                ok=False,
                state="failed",
                message=f"content_combiner: entrypoint missing: {self.entrypoint}",
            )

        env = build_content_combiner_env(self._root, self._artifact_root, self.entrypoint)

        py = sys.executable
        ep = str(self.entrypoint)

        for mode in ("process", "distribute-images", "distribute-audio"):
            proc = subprocess.run(
                [py, ep, "--mode", mode],
                env=env,
                cwd=str(self._root),
                capture_output=False,
                text=True,
            )
            if proc.returncode != 0:
                return WrapperResult(
                    ok=False,
                    state="failed",
                    message=f"content_combiner --mode {mode} failed exit={proc.returncode}",
                )
        return WrapperResult(
            ok=True,
            state="done",
            message="content_combiner: process + distribute-images + distribute-audio finished",
        )
