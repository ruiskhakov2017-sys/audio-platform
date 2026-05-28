"""
Человекочитаемая структура запусков: пути и шаблоны (без изменения legacy-воркеров).
Корень: <root_dir>/Запуски/<имя_запуска>/
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DIR_ZAPUSKI = "Запуски"

# Корень запуска
F_MANIFEST = "manifest.json"
F_STATUS = "status.json"

# Верхний уровень (01–10)
D01_OBSHCHEE = "01_Общее"
D02_SITE = "02_Сайт"
D03_YOUTUBE = "03_YouTube"
D04_TELEGRAM = "04_Telegram"
D05_RASSKAZY = "05_Рассказы"
D06_OTCHETY = "06_Отчёты"
D07_LOGI = "07_Логи"
D08_KARANTIN = "08_Карантин"
D09_ARHIV = "09_Архив"
D10_TEMP = "10_Временные_файлы"

# 01_Общее
D01_01_ISHODNYE = "01_Исходные_рассказы"
D01_02_DLINA = "02_Фильтр_по_длине"
D01_03_OTBOR_GEMINI = "03_Первичный_отбор_Gemini"

# 02_Сайт
D02_01_CLEAN = "01_Очистка_текста"
D02_02_SITE_INFO_GEMINI = "02_Информация_для_сайта_Gemini"
D02_03_VISUAL = "03_Визуал_для_сайта"
# Внутри 03_Визуал_для_сайта: сюда кладут готовые обложки (stem = имя папки рассказа в output/site)
D02_03_COVERS_IN = "Обложки_ЗАГРУЗИТЕ_СЮДА"
D02_04_TTS = "04_Озвучка_для_сайта"
D02_05_PUBLISH = "05_Публикация_на_сайт"

STAGE_IO = ("Вход", "Сырые_ответы", "Результат", "Ошибки", "Логи")
F_OTCHET_ETAPA = "Отчёт_этапа.json"

# Per-story
S01_OBSHCHEE = "01_Общее"
S02_OTBOR = "02_Отбор"
S03_SITE = "03_Сайт"
S03_01_CLEANED = "01_Очищенный_текст"
S03_02_INFO = "02_Информация_для_сайта"
S03_03_VISUAL = "03_Визуал"
S03_04_TTS = "04_Озвучка"
S03_05_PUBLISH = "05_Публикация"

S04_YOUTUBE = "04_YouTube"
S04_08_TELEGRAM = "08_Telegram"

F_SOURCE_TXT = "source.txt"
F_RAW_RESPONSE = "raw_response.txt"
F_RESULT_JSON = "result.json"
F_VALIDATION_JSON = "validation.json"
F_SITE_INFO_JSON = "site_info.json"
F_INFO_EN = "info.en.txt"
F_STORY_STATUS = "status.json"
STORY_TMP = "tmp"

F_MIGRATION_MANIFEST_CSV = "migration_manifest.csv"
F_LEGACY_PATHS_JSON = "legacy_technical_paths.json"
F_ORCHESTRATOR_TRACE = "orchestrator_launch_trace.json"
D10_STAGING_TEST_INPUT = "test_input"


def human_zapuski_root(root_dir: Path) -> Path:
    return (root_dir / DIR_ZAPUSKI).resolve()


def latest_launch_name_by_manifest_mtime(root_dir: Path) -> str | None:
    """Имя подпапки в Запуски с manifest.json и самым свежим mtime манифеста (для bat resume без ввода)."""
    root_dir = Path(root_dir).expanduser().resolve()
    base = human_zapuski_root(root_dir)
    if not base.is_dir():
        return None
    best_name: str | None = None
    best_t = -1.0
    for child in base.iterdir():
        if not child.is_dir():
            continue
        mf = child / F_MANIFEST
        if not mf.is_file():
            continue
        t = mf.stat().st_mtime
        if t > best_t:
            best_t = t
            best_name = child.name
    return best_name


def sanitize_launch_folder_name(name: str) -> str:
    base = (name or "").strip()
    base = re.sub(r'[<>:"/\\|?*]+', "_", base)
    return base.rstrip(" .")


def generated_launch_name(*, smoke: bool) -> str:
    prefix = "SITE_SMOKE" if smoke else "SITE_FULL"
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def unique_launch_path(root_dir: Path, desired_name: str) -> Path:
    base = sanitize_launch_folder_name(desired_name)
    p = human_zapuski_root(root_dir) / base
    if not p.exists():
        return p
    for n in range(2, 1000):
        cand = human_zapuski_root(root_dir) / f"{base}_v{n}"
        if not cand.exists():
            return cand
    raise RuntimeError("cannot allocate unique launch folder")


def mkdirs_plan(base: Path, rels: Iterable[str]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for rel in rels:
        path = base / rel
        out.append((rel, path))
    return out


def top_level_dirs() -> list[str]:
    return [
        D01_OBSHCHEE,
        D02_SITE,
        D03_YOUTUBE,
        D05_RASSKAZY,
        D06_OTCHETY,
        D07_LOGI,
        D08_KARANTIN,
        D09_ARHIV,
        D10_TEMP,
    ]


def obshchee_subdirs() -> list[str]:
    return [
        f"{D01_OBSHCHEE}/{D01_01_ISHODNYE}",
        f"{D01_OBSHCHEE}/{D01_02_DLINA}",
        f"{D01_OBSHCHEE}/{D01_03_OTBOR_GEMINI}",
        f"{D01_OBSHCHEE}/input_snapshot",
    ]


def site_subdirs() -> list[str]:
    return [
        f"{D02_SITE}/{D02_01_CLEAN}",
        f"{D02_SITE}/{D02_02_SITE_INFO_GEMINI}",
        f"{D02_SITE}/{D02_03_VISUAL}",
        f"{D02_SITE}/{D02_04_TTS}",
        f"{D02_SITE}/{D02_05_PUBLISH}",
    ]


def stage_with_io_under(prefix: str) -> list[str]:
    rows: list[str] = []
    for part in STAGE_IO:
        rows.append(f"{prefix}/{part}")
    rows.append(f"{prefix}/{F_OTCHET_ETAPA}")
    return rows


def all_skeleton_relative_paths() -> list[str]:
    rels: list[str] = []
    rels.extend(top_level_dirs())
    rels.extend(obshchee_subdirs())
    rels.extend(site_subdirs())
    rels.extend(stage_with_io_under(f"{D01_OBSHCHEE}/{D01_01_ISHODNYE}"))
    rels.extend(stage_with_io_under(f"{D01_OBSHCHEE}/{D01_02_DLINA}"))
    # IO для ключевых Gemini-этапов в 01 и 02
    rels.extend(stage_with_io_under(f"{D01_OBSHCHEE}/{D01_03_OTBOR_GEMINI}"))
    rels.extend(stage_with_io_under(f"{D02_SITE}/{D02_01_CLEAN}"))
    rels.extend(stage_with_io_under(f"{D02_SITE}/{D02_02_SITE_INFO_GEMINI}"))
    # YouTube — только каркас папок этапов (без внедрения логики)
    for _, name in enumerate(
        [
            "01_Отбор_лучших_историй_Gemini",
            "02_Безопасная_версия_Gemini",
            "03_Точка_рекламной_вставки",
            "04_Персонажи",
            "05_Сцены_и_промпты",
            "06_Кадры",
            "07_Озвучка_для_видео",
            "08_Сборка_видео",
            "09_Публикация_на_YouTube",
        ],
        start=1,
    ):
        rels.append(f"{D03_YOUTUBE}/{name}")
    return rels


def story_youtube_root(launch: Path, story_id: str) -> Path:
    return (launch / D05_RASSKAZY / story_id / S04_YOUTUBE).resolve()


def story_telegram_root(launch: Path, story_id: str) -> Path:
    return (story_youtube_root(launch, story_id) / S04_08_TELEGRAM).resolve()


def story_base_paths(launch: Path, story_id: str) -> dict[str, Path]:
    root = launch / D05_RASSKAZY / story_id
    return {
        "root": root,
        "obshchee": root / S01_OBSHCHEE,
        "otbor": root / S02_OTBOR,
        "site": root / S03_SITE,
        "site_cleaned": root / S03_SITE / S03_01_CLEANED,
        "site_info": root / S03_SITE / S03_02_INFO,
        "site_visual": root / S03_SITE / S03_03_VISUAL,
        "site_tts": root / S03_SITE / S03_04_TTS,
        "site_publish": root / S03_SITE / S03_05_PUBLISH,
        "youtube": root / S04_YOUTUBE,
        "youtube_telegram": root / S04_YOUTUBE / S04_08_TELEGRAM,
        "story_status": root / F_STORY_STATUS,
        "tmp": root / STORY_TMP,
    }


# Технический legacy layout внутри уже существующей папки 10_Временные_файлы (не новый верхний уровень Запуски).
D10_LEGACY = "legacy"


def launch_legacy_runs_root(launch: Path, branch: str, run_id: str) -> Path:
    """Запуски/<name>/10_Временные_файлы/legacy/runs/<branch>/<run_id>/ — аналог runs/<branch>/<run_id>."""
    b = (branch or "site").strip().lower() or "site"
    rid = (run_id or "").strip()
    return (launch / D10_TEMP / D10_LEGACY / "runs" / b / rid).resolve()


def launch_legacy_output_root(launch: Path, *, branch: str = "site") -> Path:
    """Запуски/<name>/10_Временные_файлы/legacy/output/site|youtube/ — аналог output/site."""
    b = (branch or "site").strip().lower() or "site"
    sub = "youtube" if b == "youtube" else "site"
    return (launch / D10_TEMP / D10_LEGACY / "output" / sub).resolve()


def human_launch_dir_from_legacy_anchor(artifact_legacy: Path) -> Path | None:
    """
    Запуски/<name>/ из пути …/<name>/10_Временные_файлы/legacy (artifact_root комбайна/site pipeline).
    """
    p = artifact_legacy.resolve()
    if p.name != D10_LEGACY or p.parent.name != D10_TEMP:
        return None
    return p.parent.parent.resolve()


def launch_site_visual_root(launch: Path) -> Path:
    """Запуски/<имя>/02_Сайт/03_Визуал_для_сайта — визуал и таблица промптов этого прогона."""
    p = (launch.resolve() / D02_SITE / D02_03_VISUAL).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def launch_human_combiner_export_dir(launch: Path) -> Path:
    """Сюда stories_export.csv / .xlsx (формат комбайна): 02_Сайт/03_Визуал_для_сайта."""
    return launch_site_visual_root(launch)


def launch_human_combiner_images_in_dir(launch: Path) -> Path:
    """Сюда пользователь кладёт обложки до distribute-images: …/03_Визуал_для_сайта/Обложки_ЗАГРУЗИТЕ_СЮДА."""
    p = (launch_site_visual_root(launch) / D02_03_COVERS_IN).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def launch_dir_from_site_output_root(site_root: Path) -> Path | None:
    """
    Если site_root = Запуски/<name>/10_Временные_файлы/legacy/output/site — вернуть Запуски/<name>/.
    Иначе None (например только …/Content-Factory/output/site без изолированного запуска).
    """
    p = site_root.resolve()
    try:
        if (
            p.name == "site"
            and p.parent.name == "output"
            and p.parent.parent.name == D10_LEGACY
            and p.parent.parent.parent.name == D10_TEMP
        ):
            return p.parent.parent.parent.parent.resolve()
    except (IndexError, OSError, ValueError):
        return None
    return None


def mirror_exported_drive_txt_to_human_clean(
    launch: Path,
    *,
    drive_texts_dir: Path,
    story_folder_name: str,
    txt_name: str,
) -> None:
    """Копия txt, отправленного на Drive, в 02_Сайт/01_Очистка_текста/<история>/ (человекочитаемое зеркало)."""
    launch = launch.resolve()
    sid = (story_folder_name or "").strip()
    if not sid:
        return
    dst_dir = (launch / D02_SITE / D02_01_CLEAN / sid).resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)
    src = drive_texts_dir / txt_name
    if src.is_file():
        shutil.copy2(src, dst_dir / Path(txt_name).name)


def mirror_site_story_outputs_from_legacy_site(launch: Path, site_root: Path, story_folder_names: list[str]) -> None:
    """
    После импорта mp3: зеркало в 02_Сайт/01_Очистка_текста, 02_Информация_для_сайта_Gemini, 04_Озвучка_для_сайта.
    Источник — канонический …/legacy/output/site/<story>/ (контракт пайплайна).
    """
    from orchestrator.site_tts.contract import SiteTtsPaths

    launch = launch.resolve()
    site_root = site_root.resolve()
    for story in story_folder_names:
        story = (story or "").strip()
        if not story:
            continue
        paths = SiteTtsPaths.from_site_root(site_root, story)
        tts_dir = (launch / D02_SITE / D02_04_TTS / story).resolve()
        tts_dir.mkdir(parents=True, exist_ok=True)
        if paths.output_mp3.is_file():
            shutil.copy2(paths.output_mp3, tts_dir / paths.output_mp3.name)
        info_dir = (launch / D02_SITE / D02_02_SITE_INFO_GEMINI / story).resolve()
        info_dir.mkdir(parents=True, exist_ok=True)
        if paths.info_txt.is_file():
            shutil.copy2(paths.info_txt, info_dir / "info.txt")
        cl_dir = (launch / D02_SITE / D02_01_CLEAN / story).resolve()
        cl_dir.mkdir(parents=True, exist_ok=True)
        if paths.cleaned_story_txt.is_file():
            shutil.copy2(paths.cleaned_story_txt, cl_dir / paths.cleaned_story_txt.name)


def render_info_en_txt(site_info: dict[str, Any]) -> str:
    genres = site_info.get("genres") or []
    tags = site_info.get("tags") or []
    if not isinstance(genres, list):
        genres = [genres]
    if not isinstance(tags, list):
        tags = [tags]
    return (
        f"Title: {site_info.get('title', '')}\n"
        f"Alternative Title: {site_info.get('alternative_title', '')}\n"
        f"Description: {site_info.get('description', '')}\n"
        f"Genres: {', '.join(str(x) for x in genres)}\n"
        f"Tags: {', '.join(str(x) for x in tags)}\n"
        f"Voice Type: {site_info.get('voice_type', 'U')}\n"
        f"Main Character: {site_info.get('main_character', '')}\n"
        f"Visual Prompt: {site_info.get('visual_prompt', '')}\n"
    )


def _write_text_atomic(path: Path, text: str) -> None:
    """Запись через .tmp + replace: меньше шансов на битый JSON при обрыве и короче lock на целевой файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_json(path: Path, payload: Any, *, retries: int = 6) -> None:
    """
    JSON в human-дерево запуска. На Windows файл может быть read-only или кратко занят Explorer/IDE —
    снимаем readonly, пишем атомарно, несколько повторов с паузой.
    """
    path = path.resolve()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    last_err: BaseException | None = None
    for attempt in range(max(1, int(retries))):
        try:
            _write_text_atomic(path, text)
            return
        except PermissionError as ex:
            last_err = ex
            if path.is_file():
                try:
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
            time.sleep(0.2 * (attempt + 1))
        except OSError as ex:
            if getattr(ex, "winerror", None) == 5 or isinstance(ex, PermissionError):
                last_err = ex
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
    if last_err is not None:
        raise PermissionError(
            f"не удалось записать {path} после {retries} попыток "
            f"(закройте файл в редакторе/Explorer): {last_err}"
        ) from last_err


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def append_migration_csv_row(csv_path: Path, row: dict[str, str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "story_id",
        "action",
        "from_path",
        "to_path",
        "reason",
        "selection_status",
        "site_info_status",
    ]
    new_file = not csv_path.is_file()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


