"""
Prepare a single manual RunPod TTS job pack for Fish Audio S2 Pro (no pipeline, no local inference).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from orchestrator.site_tts.info_parser import resolve_cleaned_story_txt_path


def _site_output_root(config_root: Path, data_dirs: dict[str, str]) -> Path:
    out_rel = str(data_dirs.get("output", "output") or "output").strip().rstrip("/\\")
    return (config_root / out_rel / "site").resolve()


def list_eligible_site_stories(site_root: Path) -> list[str]:
    if not site_root.is_dir():
        return []
    names: list[str] = []
    for d in sorted(site_root.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        if resolve_cleaned_story_txt_path(d, d.name).is_file() and (d / "info.txt").is_file():
            names.append(d.name)
    return names


def prepare_fish_s2_pro_runpod_job_pack(
    config_root: Path,
    *,
    job_id: str = "fish_s2_pro_test_001",
    story_name: str | None = None,
    force: bool = False,
    data_dirs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Creates ``runs/site_tts_test/<job_id>/`` with input/output/logs, job.json, README.md.
    Does not run TTS, ElevenLabs, or modify production pipelines.
    """
    data_dirs = data_dirs or {}
    site_root = _site_output_root(config_root, data_dirs)
    eligible = list_eligible_site_stories(site_root)

    if not eligible:
        return {
            "ok": False,
            "message": f"Нет рассказов с очищенным .txt (cleaned_story или *__[MFU].txt) и info.txt в {site_root}",
        }

    chosen: str
    if story_name:
        if story_name not in eligible:
            return {
                "ok": False,
                "message": (
                    f"story_name={story_name!r} не найден среди подходящих. "
                    f"Допустимые: {', '.join(eligible)}"
                ),
            }
        chosen = story_name
    else:
        if len(eligible) > 1:
            print(
                "Подходящие рассказы (очищенный текст + info.txt):\n"
                + "\n".join(f"  - {n}" for n in eligible)
                + f"\n\nБез --story-name выбран первый по сортировке: {eligible[0]}\n",
                flush=True,
            )
        chosen = eligible[0]

    story_dir = site_root / chosen
    cleaned_src = resolve_cleaned_story_txt_path(story_dir, chosen)

    job_root = (config_root / "runs" / "site_tts_test" / job_id).resolve()
    if job_root.exists():
        if any(job_root.iterdir()) and not force:
            return {
                "ok": False,
                "message": f"Папка уже существует и не пуста: {job_root}. Удалите или запустите с --force.",
            }
        if force:
            shutil.rmtree(job_root, ignore_errors=True)

    job_root.mkdir(parents=True, exist_ok=True)

    input_dir = job_root / "input"
    output_dir = job_root / "output"
    logs_dir = job_root / "logs"
    for p in (input_dir, output_dir, logs_dir):
        p.mkdir(parents=True, exist_ok=True)

    dst_txt = input_dir / f"{chosen}.txt"
    shutil.copy2(cleaned_src, dst_txt)

    # Paths in job.json: repo-relative, forward slashes (as in spec)
    def rel_posix(p: Path) -> str:
        return p.relative_to(config_root).as_posix()

    input_rel = rel_posix(dst_txt)
    expected_mp3_rel = rel_posix(output_dir / f"{chosen}.mp3")
    final_mp3_rel = rel_posix(site_root / chosen / f"{chosen}.mp3")

    job_payload = {
        "job_id": job_id,
        "story_name": chosen,
        "runtime": "runpod",
        "tts_engine": "fish_audio_s2_pro",
        "input_text_path": input_rel,
        "expected_output_mp3": expected_mp3_rel,
        "final_target_mp3": final_mp3_rel,
        "status": "prepared_for_manual_runpod_test",
    }
    (job_root / "job.json").write_text(
        json.dumps(job_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    readme = f"""# Ручной TTS-тест Fish Audio S2 Pro (RunPod)

**Job ID:** `{job_id}`  
**Рассказ:** `{chosen}`

## Что отправить на RunPod

Текст для озвучки лежит в:

- `{input_rel}`

(копия очищенного файла из `output/site/{chosen}/`, см. `*__[MFU].txt` или `cleaned_story.txt`)

## Куда положить результат с RunPod

После генерации на RunPod сохраните MP3 в каталог job-пакета:

- `{expected_mp3_rel}`

(имя файла: `{chosen}.mp3`)

Папка `output/` внутри job уже создана; при необходимости скопируйте файл туда вручную.

## Итоговый путь на сайте (цель)

После проверки качества вручную можно перенести файл в финальную площадку сайта:

- `{final_mp3_rel}`

То есть: `output/site/<story_name>/<story_name>.mp3` для этого рассказа — `{chosen}.mp3`.

## Ограничения

- Не запускать Fish Audio локально.
- Не использовать ElevenLabs в рамках этого тест-пакета.
- Production site pipeline этим job не трогается.

Логи ручного прогона (если ведёте сами) можно класть в: `{rel_posix(logs_dir)}/`
"""
    (job_root / "README.md").write_text(readme, encoding="utf-8")

    return {
        "ok": True,
        "message": "job pack prepared",
        "story_name": chosen,
        "job_root": str(job_root),
        "eligible_count": len(eligible),
        "job_json": str(job_root / "job.json"),
    }
