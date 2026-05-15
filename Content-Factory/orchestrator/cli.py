from __future__ import annotations

import argparse
import contextlib
import multiprocessing
import sys
from pathlib import Path

from orchestrator.cleanup import move_items_to_quarantine, move_run_to_quarantine, print_scan, scan_generated_artifacts
from orchestrator.config import DEFAULT_CONFIG_PATH, OrchestratorConfig, load_config
from orchestrator.archive_input import ArchiveInputOptions, run_archive_input
from orchestrator.library_sampler import LibrarySamplerOptions, run_library_sampler
from orchestrator.length_filter import LengthFilterOptions, run_length_filter
from orchestrator.phase_a import PhaseAOptions, run_phase_a
from orchestrator.phase_b import PhaseBOptions, run_phase_b
from orchestrator.preflight import run_preflight
from orchestrator.series_extractor import SeriesExtractorOptions, run_series_extraction
from orchestrator.audit_series_titles import run_audit_series_titles
from orchestrator.series_title_audit_all import run_series_title_audit_all_sources
from orchestrator.stories_input_series_return import StoriesInputSeriesReturnOptions, run_stories_input_series_return
from orchestrator.clean_library_series import CleanLibrarySeriesOptions, run_clean_library_series
from orchestrator.fish_runpod_test_pack import prepare_fish_s2_pro_runpod_job_pack
from orchestrator.runtime_modes import (
    ALLOWED_VALUES,
    DEFAULT_MODES,
    load_runtime_modes,
    save_runtime_modes,
    set_runtime_mode,
)
from orchestrator.runner import RunOptions, Runner
from orchestrator.status import StatusStore
from orchestrator.youtube_bridge_manifest import (
    YoutubeBuildBridgeManifestOptions,
    YoutubeInitBridgeFixtureOptions,
    run_youtube_build_bridge_manifest,
    run_youtube_init_bridge_fixture,
)
from orchestrator.youtube_safe_bridge import (
    YoutubeImportSafeResultOptions,
    YoutubePrepareSafeBridgeOptions,
    YoutubeRunSafeBridgeOptions,
    run_youtube_import_safe_result,
    run_youtube_prepare_safe_bridge,
    run_youtube_run_safe_bridge,
)
from orchestrator.youtube_from_site import (
    YoutubeContinueAfterSelectionOptions,
    run_youtube_diagnose_cleaned_paths,
    YoutubeParseGeminiSelectionOptions,
    YoutubePrepareGeminiSelectionInputOptions,
    YoutubePrepareSafeInputOptions,
    YoutubePrefilterFromSiteOptions,
    YoutubeSelectionFromSiteOptions,
    run_youtube_continue_after_selection,
    run_youtube_prepare_gemini_selection_input,
    run_youtube_parse_gemini_selection,
    run_youtube_prepare_safe_input,
    run_youtube_prefilter_from_site,
    run_youtube_selection_from_site,
)

if sys.platform == "win32":
    try:
        multiprocessing.set_executable(sys.executable)
    except (ValueError, OSError, AttributeError):
        pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orchestrator")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p.add_argument("--modes-config", type=Path, default=Path("configs/runtime_modes.yaml"))
    sub = p.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--pipeline", default="full")
    pre.add_argument("--run-profile", default="")
    pre.add_argument("--allow-real-stages", default="")
    pre.add_argument("--stories-dir", type=Path, default=None, help="Input dir for intake stats (default: paths.yaml input_stories)")
    pre.add_argument("--execute", action="store_true")

    plan = sub.add_parser("plan")
    plan.add_argument("--pipeline", default="site")
    plan.add_argument("--story-id", default="demo-story")

    status = sub.add_parser("status")
    status.add_argument("--limit", type=int, default=20)

    run = sub.add_parser("run")
    run.add_argument("--pipeline", default="full")
    run.add_argument("--run-profile", default="")
    run.add_argument("--allow-real-stages", default="")
    run.add_argument("--story-id", default="demo-story")
    run.add_argument("--stories-dir", type=Path)
    run.add_argument(
        "--launch-dir",
        type=Path,
        default=None,
        help="Корень Запуски/<name>/: site pipeline пишет в 10_Временные_файлы/legacy/runs и legacy/output.",
    )
    run.add_argument("--execute", action="store_true")

    flt = sub.add_parser("filter-length")
    flt.add_argument("--stories-dir", type=Path, required=True)
    flt.add_argument("--short-dir", type=Path)
    flt.add_argument("--words-per-minute", type=int)
    flt.add_argument("--min-minutes", type=float)
    flt.add_argument("--min-words", type=int)
    flt.add_argument(
        "--extensions",
        default="",
        help="Comma-separated text file extensions, e.g. .txt,.text",
    )
    flt.add_argument("--execute", action="store_true")

    pha = sub.add_parser("phase-a")
    pha.add_argument(
        "--stories-dir",
        type=Path,
        default=None,
        help="Каталог с .txt для intake (обязателен, кроме режимов --inspect-human-structure / --gemini-progress / repair).",
    )
    pha.add_argument("--short-dir", type=Path)
    pha.add_argument("--story-id", default="phase-a-run")
    pha.add_argument("--words-per-minute", type=int)
    pha.add_argument("--min-minutes", type=float)
    pha.add_argument("--min-words", type=int)
    pha.add_argument("--extensions", default="")
    pha.add_argument("--gemini-workers", type=int, default=5)
    pha.add_argument(
        "--gemini-target-active-workers",
        type=int,
        default=3,
        help="Одновременных процессов gemini_auto (остальные профили в пуле ожидают).",
    )
    pha.add_argument(
        "--gemini-profiles-total",
        type=int,
        default=5,
        help="Размер пула Chrome user_data_0..N-1 для Gemini (макс. 5).",
    )
    pha.add_argument("--gemini-max-restarts-per-profile", type=int, default=3)
    pha.add_argument("--gemini-profile-cooldown-seconds", type=float, default=900.0)
    pha.add_argument(
        "--gemini-legacy-parallel-all",
        action="store_true",
        help="Старый режим: сразу поднять все воркеры (как раньше). Иначе — supervised pool.",
    )
    pha.add_argument(
        "--gemini-progress",
        action="store_true",
        help="Показать прогресс Gemini selection / supervisor для --story-id/--run-id и выйти.",
    )
    pha.add_argument(
        "--repair-stale-locks",
        action="store_true",
        help="Сухой прогон или удаление устаревших .cf_worker.lock под gemini_input (см. --repair-locks-execute).",
    )
    pha.add_argument("--repair-locks-execute", action="store_true", help="Выполнить удаление stale lock-файлов")
    pha.add_argument("--older-than-minutes", type=int, default=60)
    pha.add_argument("--run-id", default="", help="Алиас к --story-id (идентификатор run в runs/<branch>/)")
    pha.add_argument("--max-stories", type=int, default=0)
    pha.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
    )
    pha.add_argument("--gemini-stage-key", default="general_selection")
    pha.add_argument("--gemini-info-stage-key", default="site_info_builder")
    pha.add_argument("--run-branch", default="site")
    pha.add_argument("--resume", action="store_true")
    pha.add_argument(
        "--launch-dir",
        type=Path,
        default=None,
        help="Корень Запуски/<name>/: phase-a пишет runs и output в 10_Временные_файлы/legacy/...",
    )
    pha.add_argument("--visual-mode", default="", help="manual|auto (default from runtime_modes for site branch)")
    pha.add_argument("--visual-pod-url", default="", help="ComfyUI/RunPod URL for visual auto mode")
    pha.add_argument("--execute", action="store_true")
    pha.add_argument(
        "--inspect-human-structure",
        action="store_true",
        help="Сводка legacy run ↔ папка Запуски/ (только чтение; нужен --run-id или --story-id).",
    )

    launch = sub.add_parser(
        "launch",
        help="Человекочитаемая структура Запуски/ (inspect, migrate, resume-plan, cleanup, archive, delete).",
    )
    launch_sub = launch.add_subparsers(dest="launch_cmd", required=True)

    l_insp = launch_sub.add_parser("inspect", help="Показать legacy-пути и статистику по артефактам.")
    l_insp.add_argument("--name", default="", help="Имя папки в Запуски/<name>/ (если уже есть manifest.json).")
    l_insp.add_argument("--from-run-id", default="", help="Идентификатор run в runs/<branch>/ (если нет папки Запуски).")
    l_insp.add_argument("--run-branch", default="site")

    l_mig = launch_sub.add_parser(
        "migrate-to-human-structure",
        help="Dry-run или копирование в Запуски/<имя>/ без удаления legacy.",
    )
    l_mig.add_argument("--from-run-id", required=True)
    l_mig.add_argument("--name", default="", help="Имя папки; по умолчанию из length_filter_manifest")
    l_mig.add_argument("--run-branch", default="site")
    l_mig.add_argument("--verbose", action="store_true", help="Подробный dry-run: копии, проблемные story, причины.")
    l_mig.add_argument("--execute", action="store_true")

    l_cl = launch_sub.add_parser("cleanup-plan", help="Что финальное / legacy / временное (без удаления).")
    l_cl.add_argument("--name", required=True)
    l_cl.add_argument("--run-branch", default="site")

    l_rp = launch_sub.add_parser("resume-plan", help="План продолжения по status.json (ничего не запускает).")
    l_rp.add_argument("--name", required=True)

    l_pick = launch_sub.add_parser(
        "pick-site-launch",
        help="Список Site launch в Запуски/ и интерактивный выбор (без silent latest-by-mtime). Для BAT: --out файл.",
    )
    l_pick.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Путь к .cmd/.bat: будет записан setter `set \"LAUNCH_NAME=...\"` (ASCII) для `call` из Content-Factory-Запуск.bat.",
    )

    l_rs = launch_sub.add_parser(
        "resume",
        help="Без --execute: контракт. С --execute: синхронизация legacy -> Запуски (без Gemini/phase_a).",
    )
    l_rs.add_argument("--name", required=True)
    l_rs.add_argument(
        "--execute",
        action="store_true",
        help="Скопировать новые файлы из legacy runs/.../stories/<id>/_pipeline в дерево запуска и обновить status.json.",
    )
    l_rs.add_argument(
        "--output-conflict-policy",
        choices=["fail", "skip-existing", "test-suffix", "archive-existing"],
        default="fail",
        help="Политика конфликтов output (зарезервировано для resume runtime).",
    )

    l_arc = launch_sub.add_parser("archive", help="Перенос запуска в Запуски/_Архив/ (dry-run без --execute).")
    l_arc.add_argument("--name", required=True)
    l_arc.add_argument("--execute", action="store_true")

    l_del = launch_sub.add_parser(
        "delete",
        help="Dry-run удаления папки запуска; с --execute — отчёт в История_запусков/ и удаление.",
    )
    l_del.add_argument("--name", required=True)
    l_del.add_argument("--execute", action="store_true")

    l_ss = launch_sub.add_parser(
        "start-site",
        help="Новый site-запуск в Запуски/<имя>/: каркас + source.txt из каталога (без phase_a/Gemini по умолчанию).",
    )
    l_ss.add_argument("--name", required=True, help="Имя папки запуска под Запуски/")
    l_ss.add_argument("--stories-dir", type=Path, required=True, help="Каталог с исходными .txt")
    l_ss.add_argument("--limit", type=int, default=0, help="Макс. число файлов (0 = все)")
    l_ss.add_argument("--execute", action="store_true", help="Создать папки и скопировать тексты")
    l_ss.add_argument(
        "--input-snapshot",
        action="store_true",
        help="Скопировать выбранные .txt в 01_Общее/input_snapshot и записать пути в manifest (изолированный intake).",
    )
    l_ss.add_argument(
        "--output-conflict-policy",
        choices=["fail", "skip-existing", "test-suffix", "archive-existing"],
        default="fail",
        help="Политика конфликтов output/site (start-site только фиксирует в manifest).",
    )

    l_fc = launch_sub.add_parser(
        "full-site-cycle",
        help="План полного site cycle; phase_a только с --execute --invoke-legacy-phase-a (осторожно: Gemini).",
    )
    l_fc.add_argument("--name", required=True)
    l_fc.add_argument("--stories-dir", type=Path, required=True)
    l_fc.add_argument("--limit", type=int, default=0, help="Лимит историй для start-site и потолок phase-a (0 -> 50 для phase-a)")
    l_fc.add_argument("--execute", action="store_true")
    l_fc.add_argument(
        "--invoke-legacy-phase-a",
        action="store_true",
        help="После start-site вызвать legacy `orchestrator phase-a` (может запустить воркеры Gemini).",
    )
    l_fc.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=0,
        help="Жёсткий таймаут phase-a в минутах (0 = без лимита; Windows: taskkill /T).",
    )
    l_fc.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
        help="Путь к YAML registry ботов Gemini для phase-a/preflight.",
    )
    l_fc.add_argument(
        "--output-conflict-policy",
        choices=["fail", "skip-existing", "test-suffix", "archive-existing"],
        default="fail",
        help="Политика конфликтов output/site для full-site-cycle (default: fail).",
    )

    l_sm = launch_sub.add_parser(
        "smoke-site-cycle",
        help=(
            "Частичный smoke: staging test_input, preflight, только phase-a, таймаут, sync, verify-runtime. "
            "НЕ полный цикл (нет phase-b и orchestrator run --pipeline site). Полный site — команда run-site-flow."
        ),
    )
    l_sm.add_argument("--name", required=True)
    l_sm.add_argument("--stories-dir", type=Path, required=True)
    l_sm.add_argument("--limit", type=int, default=2, help="Сколько .txt в staging и start-site (по умолчанию 2)")
    l_sm.add_argument("--execute", action="store_true", help="Выполнить (без флага — только план).")
    l_sm.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=15.0,
        help="Таймаут subprocess phase-a (минуты).",
    )
    l_sm.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
    )
    l_sm.add_argument(
        "--output-conflict-policy",
        choices=["fail", "skip-existing", "test-suffix", "archive-existing"],
        default="test-suffix",
        help="Политика конфликтов output/site для smoke (default: test-suffix).",
    )

    l_rsf = launch_sub.add_parser(
        "run-site-flow",
        help=(
            "Полный site-цикл: phase-a → phase-b → `orchestrator run --pipeline site` + sync в Запуски/<имя> "
            "(см. Content-Factory-Запуск.bat). Не путать со smoke-site-cycle (там только phase-a)."
        ),
    )
    l_rsf.add_argument("--name", required=True, help="Имя папки под Запуски/")
    l_rsf.add_argument("--stories-dir", type=Path, required=True)
    l_rsf.add_argument("--limit", type=int, default=1, help="Сколько .txt в start-site и --max-stories для phase-a (0 = без лимита в phase-a)")
    l_rsf.add_argument("--execute", action="store_true", help="Создать Запуски и запустить subprocess (без флага — только план команд).")
    l_rsf.add_argument(
        "--bat-profile",
        choices=["classic", "kokoro-drive"],
        default="kokoro-drive",
        help="classic = меню [1] BAT; kokoro-drive = меню [2] (set-mode site_tts_engine + phase-b --branch site --allow-scaffold).",
    )
    l_rsf.add_argument(
        "--site-run-id",
        default="",
        help="RUN_ID без суффиксов -a/-b/-site (по умолчанию — sanitize имени папки запуска).",
    )
    l_rsf.add_argument("--gemini-workers", type=int, default=5)
    l_rsf.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
    )
    l_rsf.add_argument(
        "--output-conflict-policy",
        choices=["fail", "skip-existing", "test-suffix", "archive-existing"],
        default="skip-existing",
        help="Политика для start-site (по умолчанию skip-existing как в требовании production).",
    )
    l_rsf.add_argument(
        "--phase-b-allow-scaffold",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Переопределить allow-scaffold для phase-b (по умолчанию: True для kokoro-drive, False для classic).",
    )
    l_rsf.add_argument(
        "--phase-b-branch",
        choices=["all", "site"],
        default=None,
        help="Переопределить --branch phase-b (по умолчанию site при kokoro-drive, all при classic).",
    )
    l_rsf.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=0.0,
        help="Жёсткий таймаут только для subprocess phase-a (0 = без лимита; Windows: taskkill /T).",
    )
    l_rsf.add_argument(
        "--input-snapshot",
        action="store_true",
        help="При создании нового запуска: snapshot .txt в 01_Общее/input_snapshot (как start-site --input-snapshot).",
    )

    l_gpf = launch_sub.add_parser(
        "gemini-preflight",
        help="Проверка перед phase-a/Gemini: профили, registry, intake, конфликт процессов.",
    )
    l_gpf.add_argument("--name", required=True, help="Имя папки под Запуски/")
    l_gpf.add_argument(
        "--stories-dir",
        type=Path,
        default=None,
        help="Если нет staging test_input — каталог исходных .txt для подсчёта intake.",
    )
    l_gpf.add_argument("--limit", type=int, default=2, help="Подсказка размера очереди при --stories-dir")
    l_gpf.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
    )

    l_fr = launch_sub.add_parser(
        "final-report",
        help="Сводка 06_Отчёты/ФИНАЛЬНЫЙ_ОТЧЁТ + cleanup_manifest (с --execute — запись файлов).",
    )
    l_fr.add_argument("--name", required=True)
    l_fr.add_argument("--execute", action="store_true")

    l_vr = launch_sub.add_parser(
        "verify-runtime",
        help="Проверка runtime-наполнения папки Запуски/<имя> и resume support/unsupported.",
    )
    l_vr.add_argument("--name", required=True)

    l_sync = launch_sub.add_parser(
        "sync-legacy",
        help="Синхронизировать legacy результаты (_pipeline/output/site) в существующий Запуски/<имя>.",
    )
    l_sync.add_argument("--name", required=True)
    l_sync.add_argument("--execute", action="store_true", help="Без флага только план (ничего не копирует).")

    l_sync_progress = launch_sub.add_parser(
        "sync-progress",
        help="Инкрементальный safe-sync прогресса из legacy в human-папки (можно во время running phase-a).",
    )
    l_sync_progress.add_argument("--name", required=True)

    l_pathaudit = launch_sub.add_parser(
        "path-audit",
        help="Read-only: куда смотрят phase-a/b и site pipeline для Запуски/<name> (JSON+CSV в .orchestrator/reports/).",
    )
    l_pathaudit.add_argument("--name", required=True, help="Имя папки под Запуски/")

    l_quar = launch_sub.add_parser(
        "quarantine-old-artifacts",
        help="Найти или перенести SMOKE/TEST артефакты в Запуски/_Карантин_старых_запусков/<timestamp>/ (без удаления).",
    )
    l_quar.add_argument("--execute", action="store_true", help="Перенести; без флага — только dry-run + manifest.")
    l_quar.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Имя запуска в Запуски, не трогать (можно указать несколько раз).",
    )

    phb = sub.add_parser("phase-b")
    phb.add_argument("--story-id", default="phase-b-run")
    phb.add_argument("--deferred-manifest", type=Path, required=True)
    phb.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
    )
    phb.add_argument("--reports-subdir", default="")
    phb.add_argument("--promo-intro-en", default="promo_intro_en")
    phb.add_argument("--promo-mid-en", default="promo_mid_en")
    phb.add_argument("--promo-outro-en", default="promo_outro_en")
    phb.add_argument("--branch", choices=["all", "site"], default="all", help="phase-b route: all|site")
    phb.add_argument("--allow-scaffold", action="store_true")
    phb.add_argument(
        "--launch-dir",
        type=Path,
        default=None,
        help="Запуски/<name>/: deferred.json должен быть под .../10_Временные_файлы/legacy/.",
    )

    yt = sub.add_parser("youtube", help="YouTube tools using site-selected deferred input")
    yt_sub = yt.add_subparsers(dest="youtube_cmd", required=True)

    yt_pref = yt_sub.add_parser(
        "prefilter-from-site",
        help="Build YouTube size filter from runs/site/<site-run-id>/_phase_a/ready_queues/deferred.json",
    )
    yt_pref.add_argument("--site-run-id", required=True)
    yt_pref.add_argument("--youtube-run-id", required=True)
    yt_pref.add_argument("--min-words", type=int, default=4500)
    yt_pref.add_argument("--max-words", type=int, default=9000)
    yt_pref.add_argument("--force", action="store_true")

    yt_diag = yt_sub.add_parser(
        "diagnose-cleaned-paths",
        help="Diagnose cleaned_path resolution for site deferred items",
    )
    yt_diag.add_argument("--site-run-id", required=True)
    yt_diag.add_argument("--youtube-run-id", required=True)

    yt_parse = yt_sub.add_parser(
        "parse-gemini-selection",
        help="Parse binary YES/NO Gemini #1 outputs and produce youtube_selected_yes/no",
    )
    yt_parse.add_argument("--youtube-run-id", required=True)
    yt_parse.add_argument("--force", action="store_true")

    yt_prep_sel = yt_sub.add_parser(
        "prepare-gemini-selection-input",
        help="Prepare Gemini #1 selection input from youtube_size_yes.json",
    )
    yt_prep_sel.add_argument("--youtube-run-id", required=True)
    yt_prep_sel.add_argument("--force", action="store_true")

    yt_safe = yt_sub.add_parser(
        "prepare-safe-input",
        help="Prepare Gemini #2 safe input from youtube_selected_yes and scaffold output/youtube story folders",
    )
    yt_safe.add_argument("--youtube-run-id", required=True)
    yt_safe.add_argument("--force", action="store_true")

    yt_auto_prepare = yt_sub.add_parser(
        "selection-from-site",
        help="Run YouTube selection preparation from site deferred and stop at Gemini #1 handoff",
    )
    yt_auto_prepare.add_argument("--site-run-id", required=True)
    yt_auto_prepare.add_argument("--youtube-run-id", required=True)
    yt_auto_prepare.add_argument("--min-words", type=int, default=4500)
    yt_auto_prepare.add_argument("--max-words", type=int, default=9000)
    yt_auto_prepare.add_argument("--force", action="store_true")

    yt_auto_continue = yt_sub.add_parser(
        "continue-after-selection",
        help="Continue YouTube flow after real Gemini #1 outputs and prepare safe input",
    )
    yt_auto_continue.add_argument("--youtube-run-id", required=True)
    yt_auto_continue.add_argument("--force", action="store_true")

    yt_bridge = yt_sub.add_parser(
        "build-bridge-manifest",
        help="Dry-run: youtube_bridge_manifest.json + story manifests + validation (no Gemini/TTS/RunPod/AutoVideo)",
    )
    yt_bridge.add_argument("--youtube-run-id", required=True)
    yt_bridge.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Default True: не создавать scaffold-папки под output/youtube (только JSON-манифесты).",
    )
    yt_bridge.add_argument(
        "--fixture-layout",
        action="store_true",
        help="Писать story manifest в output/youtube/_smoke/<youtube-run-id>/… и создавать папки (smoke/fixture).",
    )

    yt_fix = yt_sub.add_parser(
        "init-bridge-fixture",
        help="Создать минимальный youtube run с одним synthetic YES (fixture, без Gemini)",
    )
    yt_fix.add_argument("--youtube-run-id", default="yt-bridge-fixture-a")
    yt_fix.add_argument("--force", action="store_true")

    yt_psb = yt_sub.add_parser(
        "prepare-safe-bridge",
        help="Single-story: положить вход safe в legacy/.../stories_from_orchestrator/... (без запуска gemini_auto)",
    )
    yt_psb.add_argument("--youtube-run-id", required=True)
    yt_psb.add_argument("--story-id", required=True, help="story_id или canonical_basename из youtube_bridge_manifest.json")
    yt_psb.add_argument("--force", action="store_true")

    yt_isb = yt_sub.add_parser(
        "import-safe-result",
        help="Single-story: импорт *_clean.txt из staging в 02_safe_story/safe_story.txt (без TTS/promo/director)",
    )
    yt_isb.add_argument("--youtube-run-id", required=True)
    yt_isb.add_argument("--story-id", required=True)
    yt_isb.add_argument("--force", action="store_true")

    yt_rsb = yt_sub.add_parser(
        "run-safe-bridge",
        help="Single-story: env GEMINI_* + запуск gemini_auto на staging (только с --execute), затем import-safe-result",
    )
    yt_rsb.add_argument("--youtube-run-id", required=True)
    yt_rsb.add_argument("--story-id", required=True)
    yt_rsb.add_argument(
        "--execute",
        action="store_true",
        help="Реально запустить legacy gemini_auto.py (Playwright/Chrome). Без флага — только проверки и manual_cmd.",
    )
    yt_rsb.add_argument("--force", action="store_true", help="Передать в import-safe-result")
    yt_rsb.add_argument(
        "--reuse-legacy-user-data",
        action="store_true",
        help="GEMINI_USER_DATA_DIR=legacy/youtube_tts/user_data (уже залогиненный профиль); STORIES_DIR остаётся изолированным.",
    )

    show_modes = sub.add_parser("show-modes")

    set_mode = sub.add_parser("set-mode")
    set_mode.add_argument("--key", required=True)
    set_mode.add_argument("--value", required=True)

    reset_modes = sub.add_parser("reset-modes")

    cleanup_scan = sub.add_parser("cleanup-scan")
    cleanup_scan.add_argument("--root", type=Path, default=Path("."))

    cleanup_move = sub.add_parser("cleanup-move")
    cleanup_move.add_argument("--root", type=Path, default=Path("."))
    cleanup_move.add_argument(
        "--paths",
        required=True,
        help="Comma-separated relative paths to move into _quarantine_old_runs/<timestamp>",
    )
    cleanup_move.add_argument("--timestamp", default="")

    cleanup_run = sub.add_parser("cleanup-run")
    cleanup_run.add_argument("--root", type=Path, default=Path("."))
    cleanup_run.add_argument("--run-id", required=True)
    cleanup_run.add_argument("--timestamp", default="")

    sx = sub.add_parser("extract-series", help="Extract confirmed story series into <top>/_series/<series_title>")
    sx.add_argument("--source-dir", type=Path, required=True)
    sx.add_argument("--dry-run", action="store_true", help="Показать план (режим по умолчанию)")
    sx.add_argument("--execute", action="store_true", help="Реально перенести confirmed series")
    sx.add_argument("--progress-every", type=int, default=5000, help="Печатать прогресс каждые N txt-файлов")
    sx.add_argument("--top-folders-limit", type=int, default=0, help="Ограничить количество верхних папок (0 = без лимита)")
    sx.add_argument("--only-folder", action="append", default=[], help="Обработать только указанную верхнюю папку (можно повторять)")
    sx.add_argument("--max-files-per-folder", type=int, default=0, help="Максимум txt на верхнюю папку (0 = без лимита)")
    sx.add_argument("--stop-after-files", type=int, default=0, help="Остановиться после N txt-файлов (0 = без лимита)")

    ast = sub.add_parser(
        "audit-series-titles",
        help="Аудит серийности имён: stories/input + gemini_input uncategorized (read-only, отчёт в .orchestrator/reports/).",
    )
    ast.add_argument(
        "--stories-input-dir",
        type=Path,
        default=Path("stories/input"),
        help="Папка с .txt очереди (по умолчанию stories/input от cwd)",
    )
    ast.add_argument(
        "--gemini-queue-dir",
        type=Path,
        default=Path("runs/site/site-drive-run-a/_phase_a/gemini_input/stories/uncategorized"),
        help="Папка uncategorized с подпапками очереди phase_a (текущий site run)",
    )
    ast.add_argument(
        "--batch-manifest",
        type=Path,
        default=Path("stories/input/_batch_manifest.json"),
        help="Манифест для manifest_hit / return_safe (ключи по basename .txt)",
    )
    ast.add_argument("--max-examples", type=int, default=8, help="Сколько примеров на bucket в консоль")

    asall = sub.add_parser(
        "audit-series-all-sources",
        help="Аудит серийников: все источники из манифеста/sampler + stories/* + archive + dry-run план (series_title_audit_all_sources.*).",
    )
    asall.add_argument(
        "--stories-input-dir",
        type=Path,
        default=Path("stories/input"),
        help="Очередь input (manifest + .txt)",
    )
    asall.add_argument(
        "--gemini-queue-dir",
        type=Path,
        default=Path("runs/site/site-drive-run-a/_phase_a/gemini_input/stories/uncategorized"),
        help="Очередь gemini uncategorized (если папки нет — пропуск)",
    )
    asall.add_argument("--no-gemini-queue", action="store_true", help="Не включать gemini uncategorized в отчёт")
    asall.add_argument(
        "--max-txt-files",
        type=int,
        default=120000,
        help="Максимум уникальных .txt при сканировании корней (защита от бесконечного rglob)",
    )

    gau = sub.add_parser(
        "gemini-audit",
        help="Аудит resume/дублей Gemini phase_a (read-only отчёты в .orchestrator/logs/).",
    )
    gau_sub = gau.add_subparsers(dest="gemini_audit_cmd", required=True)
    gau_resume = gau_sub.add_parser(
        "resume",
        help="Полный отчёт A–D: gemini_resume_audit.json/.md",
    )
    gau_resume.add_argument(
        "--run-path",
        type=Path,
        required=True,
        help="Корень _phase_a или run; будет найден gemini_input/stories",
    )
    gau_resume.add_argument(
        "--stories-dir",
        type=Path,
        default=None,
        help="Intake (корень .txt); иначе stories_dir из intake_manifest.json в phase_a",
    )
    gau_resume.add_argument(
        "--extensions",
        default=".txt",
        help="Список расширений intake через запятую (как в phase-a)",
    )
    gau_resume.add_argument(
        "--dry-run",
        action="store_true",
        help="Режим только чтения (по смыслу команды всегда так; флаг для единообразия CLI)",
    )
    gau_dedupe = gau_sub.add_parser(
        "dedupe-plan",
        help="План canonical/extra по дублям -> gemini_dedupe_plan.json (только с --dry-run)",
    )
    gau_dedupe.add_argument("--run-path", type=Path, required=True)
    gau_dedupe.add_argument("--stories-dir", type=Path, default=None)
    gau_dedupe.add_argument("--extensions", default=".txt")
    gau_dedupe.add_argument(
        "--dry-run",
        action="store_true",
        help="Обязательно укажите: план без изменений на диске (кроме JSON-отчёта).",
    )

    rs = sub.add_parser(
        "return-series-from-input",
        help="Очередь stories/input: серийники по normalized_base_title → исходник из манифеста или stories/_series_return_unknown/",
    )
    rs.add_argument(
        "--input-dir",
        type=Path,
        default=Path("stories/input"),
        help="Папка с .txt (по умолчанию stories/input от cwd)",
    )
    rs.add_argument("--execute", action="store_true", help="Реально MOVE (без удаления и без перезаписи существующих целей)")

    cls = sub.add_parser(
        "clean-library-series",
        help="Библиотека: только <library-root>/<genre>/*.txt → serial в <genre>/_series/ (dry-run; --execute — MOVE).",
    )
    cls.add_argument(
        "--library-root",
        type=Path,
        required=True,
        help="Корень библиотеки (например D:\\Проекты сохр\\AudioProject\\output)",
    )
    cls.add_argument("--execute", action="store_true", help="Реально переместить serial-файлы в _series")

    sl = sub.add_parser(
        "sample-library",
        help="Пополнить очередь: до N новых .txt из каждой верхней папки source-dir в target-dir (MOVE по умолчанию; basename уже в очереди — пропуск)",
    )
    sl.add_argument("--source-dir", type=Path, required=True)
    sl.add_argument("--target-dir", type=Path, required=True)
    sl.add_argument("--per-folder", type=int, required=True)
    sl.add_argument("--seed", default="", help="Deterministic sampling seed")
    sl.add_argument("--allow-nonempty-target", action="store_true", help="Разрешить execute при существующих .txt в target-dir (нужно для очереди)")
    sl.add_argument(
        "--copy",
        action="store_true",
        help="Копировать вместо MOVE (исходники остаются; для очереди stories/input не рекомендуется)",
    )
    sl.add_argument("--confirm-move", action="store_true", help="Подтверждение физического MOVE из source library (--execute без --copy)")
    sl.add_argument(
        "--confirm-add",
        action="store_true",
        help="Подтверждение реальной записи в target-dir при --execute --copy",
    )
    sl.add_argument("--dry-run", action="store_true", help="Показать план (режим по умолчанию)")
    sl.add_argument("--execute", action="store_true", help="Реально перенести (MOVE) или скопировать (--copy) выбранные файлы")

    ai = sub.add_parser("archive-input", help="Archive stories/input .txt files into archive/stories_input/<timestamp>")
    ai.add_argument("--input-dir", type=Path, required=True)
    ai.add_argument("--dry-run", action="store_true", help="Показать план архивации (режим по умолчанию)")
    ai.add_argument("--execute", action="store_true", help="Реально MOVE .txt и _batch_manifest.json в архив")

    fish_pack = sub.add_parser(
        "prepare-fish-tts-runpod-pack",
        help="Собрать ручной job-пакет для Fish S2 Pro на RunPod (без TTS и без production pipeline).",
    )
    fish_pack.add_argument("--job-id", default="fish_s2_pro_test_001")
    fish_pack.add_argument(
        "--story-name",
        default="",
        help="Имя папки в output/site/ (если пусто — первый подходящий из списка)",
    )
    fish_pack.add_argument(
        "--force",
        action="store_true",
        help="Пересоздать runs/site_tts_test/<job_id>/ если папка уже есть",
    )

    sv = sub.add_parser("site-visual", help="Site visual tools (manual image import)")
    sv_sub = sv.add_subparsers(dest="site_visual_cmd", required=True)
    sv_imp = sv_sub.add_parser("import", help="Import prepared images into output/site/<story>/<story>.jpg")
    sv_imp.add_argument("--dry-run", action="store_true", help="Показать план без копирования")
    sv_imp.add_argument("--execute", action="store_true", help="Реально копировать картинки")
    sv_imp.add_argument("--force", action="store_true", help="Перезаписать существующие <story>.jpg")
    sv_imp.add_argument("--import-dir", type=Path, default=Path("input/site_visual_import"), help="Папка с готовыми картинками")
    sv_imp.add_argument(
        "--report-path",
        type=Path,
        default=Path(".orchestrator/site_visual_import_report.json"),
        help="Куда писать JSON-отчёт",
    )

    sp = sub.add_parser("site-publish", help="Prepare output/site stories for legacy autopublisher (To_Publish)")
    sp_sub = sp.add_subparsers(dest="site_publish_cmd", required=True)
    sp_prep = sp_sub.add_parser("prepare", help="Bridge ready output/site stories into legacy/autopublisher/To_Publish")
    sp_prep.add_argument("--dry-run", action="store_true", help="Показать план без копирования")
    sp_prep.add_argument("--execute", action="store_true", help="Реально копировать файлы в To_Publish")
    sp_prep.add_argument("--force", action="store_true", help="Разрешить перезапись папки To_Publish/<story>")
    sp_prep.add_argument("--story", default="", help="Точечно подготовить одну story (имя папки в output/site/)")
    sp_doc = sp_sub.add_parser("env-doctor", help="Audit and sync site publish env from Dirtysecrets")
    sp_doc.add_argument("--dirtysecrets-path", type=Path, default=Path(r"D:\Cursor AI\Dirtysecrets"))
    sp_doc.add_argument("--no-write-env", action="store_true", help="Только аудит, без обновления .env.site_publish")
    sp_pub = sp_sub.add_parser("publish", help="Headless legacy publish with required env precheck")
    sp_pub.add_argument("--story", default="", help="Точечная публикация одной story")
    sp_pub.add_argument("--dry-run", action="store_true", help="Проверка без upload/insert")
    sp_pub.add_argument("--execute", action="store_true", help="Реальная публикация (запрещена при blockers)")
    sp_pub.add_argument("--dirtysecrets-path", type=Path, default=Path(r"D:\Cursor AI\Dirtysecrets"))

    st = sub.add_parser("site-tts", help="Модульный site TTS (output/site -> mp3, см. configs/site_tts.yaml)")
    st.add_argument("--modes-config", type=Path, default=Path("configs/runtime_modes.yaml"))
    st.add_argument(
        "--site-output-root",
        type=Path,
        default=None,
        help="Корень site output (папка site): по умолчанию <project>/output/site; для запуска — …/Запуски/<n>/10_…/legacy/output/site",
    )
    st.add_argument(
        "--launch-name",
        default="",
        help="Human-launch: имя в Запуски/<name> — scan/one/sync по 05_Рассказы без legacy output/site.",
    )
    st.add_argument(
        "--launch-dir",
        type=Path,
        default=None,
        help="Human-launch: явный путь к корню запуска (перекрывает --launch-name).",
    )
    st_sub = st.add_subparsers(dest="site_tts_cmd", required=True)
    st_one = st_sub.add_parser("one", help="Озвучить один рассказ (имя папки в output/site/)")
    st_one.add_argument("--story-name", required=True)
    st_one.add_argument("--execute", action="store_true", help="Реальная генерация (без флага — dry-run)")
    st_one.add_argument("--force", action="store_true", help="Перезаписать существующий mp3")
    st_miss = st_sub.add_parser(
        "missing-mp3",
        help="(как sync) Все папки output/site без mp3, с очищенным текстом (cleaned_story или *__[MFU].txt)",
    )
    st_miss.add_argument("--limit", type=int, default=0, help="Макс. количество (0 = без лимита, для первых 100: --limit 100)")
    st_miss.add_argument("--execute", action="store_true")
    st_miss.add_argument("--force", action="store_true")
    st_miss.add_argument("--voice", default="", help="Фильтр Тип голоса из info.txt: M,F,U через запятую; пусто = все")
    st_miss.add_argument("--folder-suffix", default="", help="Только папки, имя оканчивается на _M / _F / _U (одна буква)")
    st_scan = st_sub.add_parser("scan", help="Показать все папки output/site: очередь, mp3, голос из info.txt")
    st_scan.add_argument("--limit", type=int, default=0, help="Показать только первые N строк (0 = все)")
    st_scan.add_argument("--voice", default="", help="Подсветка фильтра M,F,U (для колонки in_queue); пусто = без фильтра по голосу")
    st_scan.add_argument("--folder-suffix", default="", help="Учитывать суффикс _M/_F/_U в имени папки при in_queue")
    st_sync = st_sub.add_parser("sync", help="Поток: очередь без mp3; существующий mp3 пропускается (без --force)")
    st_sync.add_argument("--limit", type=int, default=0, help="Макс. количество (0 = без лимита)")
    st_sync.add_argument("--execute", action="store_true")
    st_sync.add_argument("--force", action="store_true")
    st_sync.add_argument("--voice", default="", help="Только Тип голоса из info.txt: M,F,U через запятую")
    st_sync.add_argument("--folder-suffix", default="", help="Только папки ..._M / ..._F / ..._U в имени")
    st_fn = st_sub.add_parser("first-n", help="Первые N рассказов без mp3 (порядок по имени папки)")
    st_fn.add_argument("--n", type=int, required=True)
    st_fn.add_argument("--execute", action="store_true")
    st_fn.add_argument("--force", action="store_true")
    st_fn.add_argument("--voice", default="", help="Фильтр M,F,U из info.txt; пусто = все")
    st_fn.add_argument("--folder-suffix", default="", help="Суффикс _M/_F/_U в имени папки")
    st_cc = st_sub.add_parser("kokoro-colab", help="Batch bridge: export/import/verify для Kokoro через Colab GPU")
    st_cc_sub = st_cc.add_subparsers(dest="site_tts_colab_cmd", required=True)
    st_cc_exp = st_cc_sub.add_parser("export", help="Собрать batch для Colab без локального TTS")
    st_cc_exp.add_argument("--limit", type=int, default=0, help="Макс. количество историй (0 = без лимита)")
    st_cc_exp.add_argument("--batch-id", default="", help="Идентификатор batch (по умолчанию timestamp)")
    st_cc_exp_drive = st_cc_sub.add_parser("export-drive", help="Google Drive flow: копировать txt в одну папку texts/")
    st_cc_exp_drive.add_argument("--limit", type=int, default=0, help="Макс. количество историй (0 = без лимита)")
    st_cc_exp_drive.add_argument(
        "--stories-filter-dir",
        type=Path,
        default=None,
        help="Только папки output/site, совпадающие по stem с .txt из этого каталога (как при run-site-flow *-site)",
    )
    st_cc_exp_drive.add_argument("--texts-dir", type=Path, default=None, help="Путь к Google Drive texts dir")
    st_cc_setup_drive = st_cc_sub.add_parser("setup-drive", help="Создать структуру Google Drive и скопировать Colab runner")
    st_cc_imp = st_cc_sub.add_parser("import", help="Импортировать mp3-результаты Colab в output/site")
    st_cc_imp.add_argument("--batch-id", default="", help="ID batch в runs/tts_colab_batches/")
    st_cc_imp.add_argument("--batch-dir", type=Path, default=None, help="Явный путь к batch-папке")
    st_cc_imp.add_argument("--handoff-dir", type=Path, default=None, help="Путь к _COLAB_EXPORTS/<handoff-folder>")
    st_cc_imp.add_argument("--latest", action="store_true", help="Взять последний handoff из _COLAB_EXPORTS/")
    st_cc_imp.add_argument("--current", action="store_true", help="Импортировать mp3 из COLAB_TTS_CURRENT/MP3_FROM_COLAB")
    st_cc_imp.add_argument("--force", action="store_true", help="Разрешить перезапись существующих mp3")
    st_cc_imp_drive = st_cc_sub.add_parser("import-drive", help="Google Drive flow: импорт mp3 из одной папки mp3/")
    st_cc_imp_drive.add_argument("--mp3-dir", type=Path, default=None, help="Путь к Google Drive mp3 dir")
    st_cc_imp_drive.add_argument("--force", action="store_true", help="Разрешить перезапись существующих mp3")
    st_cc_ver = st_cc_sub.add_parser("verify", help="Проверить покрытие mp3 и (опционально) статус batch")
    st_cc_ver.add_argument("--batch-id", default="", help="ID batch для проверки статуса результатов")
    st_cc_ver.add_argument("--handoff-dir", type=Path, default=None, help="Путь к _COLAB_EXPORTS/<handoff-folder>")
    st_cc_ver.add_argument("--latest", action="store_true", help="Взять последний handoff из _COLAB_EXPORTS/")
    st_cc_ver.add_argument("--current", action="store_true", help="Проверить COLAB_TTS_CURRENT (TXT/MP3/mapping)")
    st_cc_ver_drive = st_cc_sub.add_parser("verify-drive", help="Google Drive flow: сравнить texts/ и mp3/")
    st_cc_ver_drive.add_argument("--texts-dir", type=Path, default=None, help="Путь к Google Drive texts dir")
    st_cc_ver_drive.add_argument("--mp3-dir", type=Path, default=None, help="Путь к Google Drive mp3 dir")
    st_cc_wait_drive = st_cc_sub.add_parser("wait-drive", help="Ждать mp3 в Drive и авто-импортировать в output/site")
    st_cc_wait_drive.add_argument("--mp3-dir", type=Path, default=None, help="Путь к Google Drive mp3 dir")
    st_cc_wait_drive.add_argument("--wait-interval-minutes", type=int, default=0, help="Интервал проверки mp3 (0 = из конфига)")
    st_cc_wait_drive.add_argument("--max-wait-hours", type=int, default=0, help="Максимум ожидания в часах (0 = из конфига)")
    st_cc_wait_drive.add_argument("--force", action="store_true", help="Разрешить перезапись существующих mp3")
    st_cc_full_drive = st_cc_sub.add_parser("full-cycle-drive", help="Export txt -> wait mp3 -> import -> cleanup")
    st_cc_full_drive.add_argument("--limit", type=int, default=0, help="Макс. количество историй (0 = без лимита)")
    st_cc_full_drive.add_argument(
        "--stories-filter-dir",
        type=Path,
        default=None,
        help="Как export-drive: ограничить набор историй по .txt в каталоге",
    )
    st_cc_full_drive.add_argument("--texts-dir", type=Path, default=None, help="Путь к Google Drive texts dir")
    st_cc_full_drive.add_argument("--mp3-dir", type=Path, default=None, help="Путь к Google Drive mp3 dir")
    st_cc_full_drive.add_argument("--wait-interval-minutes", type=int, default=0, help="Интервал проверки mp3 (0 = из конфига)")
    st_cc_full_drive.add_argument("--max-wait-hours", type=int, default=0, help="Максимум ожидания в часах (0 = из конфига)")
    st_cc_full_drive.add_argument("--force", action="store_true", help="Разрешить перезапись существующих mp3")

    return p


def _site_tts_cli(args: argparse.Namespace, cfg: OrchestratorConfig) -> int:
    import multiprocessing

    if multiprocessing.current_process().name != "MainProcess":
        print("site-tts: пропуск (не MainProcess — дочерний процесс multiprocessing).", flush=True)
        return 0

    from orchestrator.site_tts.bootstrap_diag import log_site_tts_bootstrap

    log_site_tts_bootstrap(cfg, execute=bool(getattr(args, "execute", False)))

    from orchestrator.site_tts.batch import (
        collect_batch_items,
        collect_human_launch_tts_items,
        parse_voice_filter_arg,
        resolve_site_tts_human_launch_root,
        run_site_tts_for_story,
        scan_human_launch_tts_queue,
        scan_site_tts_queue,
    )

    modes_path = (
        args.modes_config.resolve()
        if args.modes_config.is_absolute()
        else (cfg.root_dir / args.modes_config).resolve()
    )
    execute = bool(getattr(args, "execute", False))
    force = bool(getattr(args, "force", False))
    launch_name = str(getattr(args, "launch_name", "") or "").strip()
    launch_dir_arg = getattr(args, "launch_dir", None)
    launch_requested = bool(launch_name) or (
        launch_dir_arg is not None and str(launch_dir_arg).strip()
    )
    st_cmd = str(getattr(args, "site_tts_cmd", "") or "")
    st_cc = str(getattr(args, "site_tts_colab_cmd", "") or "").strip().lower()
    uses_human_launch_flags = st_cmd in {
        "one",
        "scan",
        "sync",
        "missing-mp3",
        "first-n",
    } or (
        st_cmd == "kokoro-colab"
        and st_cc in {"export", "import", "verify", "export-drive", "import-drive"}
    )
    human_launch = resolve_site_tts_human_launch_root(
        cfg.root_dir,
        launch_name=launch_name,
        launch_dir=launch_dir_arg,
    )
    if launch_requested and uses_human_launch_flags and human_launch is None:
        print(
            "site-tts: не найден human-launch (--launch-name / --launch-dir): "
            "нужна существующая папка запуска с 05_Рассказы внутри.",
            flush=True,
        )
        return 2

    site_root = (cfg.root_dir / "output" / "site").resolve()
    sor = getattr(args, "site_output_root", None)
    if human_launch is not None:
        if sor:
            print(
                "[site-tts] human-launch режим: --site-output-root игнорируется.",
                flush=True,
            )
    elif sor:
        p = Path(sor)
        site_root = (p if p.is_absolute() else (cfg.root_dir / p)).resolve()

    def _modes_path() -> Path:
        return modes_path

    def _voice_arg() -> frozenset[str] | None:
        return parse_voice_filter_arg(str(getattr(args, "voice", "") or ""))

    def _suffix_arg() -> str | None:
        s = str(getattr(args, "folder_suffix", "") or "").strip()
        return s[:1] if s else None

    def _one(name: str) -> int:
        res = run_site_tts_for_story(
            cfg.root_dir,
            story_name=name,
            modes_config=_modes_path(),
            execute=execute,
            force=force,
            site_output_root=site_root,
            human_launch=human_launch,
        )
        if isinstance(res.details, dict) and res.details.get("hint_engine_colab_drive"):
            print(res.message)
            return 0
        print(f"{name}: {res.status} | {res.message}")
        if res.output_path:
            print(f"  mp3: {res.output_path}")
        if res.logs_path:
            print(f"  log: {res.logs_path}")
        if res.duration_sec is not None:
            print(f"  duration_sec: {res.duration_sec}")
        return 0 if res.status == "success" else 2

    if args.site_tts_cmd == "one":
        if not execute:
            print("site-tts one: dry-run (без --execute mp3 не пишется).")
        return _one(str(args.story_name).strip())

    if args.site_tts_cmd == "scan":
        vf = _voice_arg()
        fs = _suffix_arg()
        if human_launch is not None:
            rows = scan_human_launch_tts_queue(
                human_launch, project_root=cfg.root_dir, voice_types=vf, folder_suffix=fs
            )
        else:
            rows = scan_site_tts_queue(site_root, project_root=cfg.root_dir, voice_types=vf, folder_suffix=fs)
        lim = int(getattr(args, "limit", 0) or 0)
        shown = rows[:lim] if lim > 0 else rows
        print("story\tvoice\thas_mp3\thas_cleaned\tin_queue\tskip")
        for r in shown:
            print(
                f"{r['story']}\t{r['voice']}\t{r['has_mp3']}\t{r['has_cleaned']}\t{r['need_tts']}\t{r['skip']}"
            )
        nq = sum(1 for r in rows if r["need_tts"])
        print(
            f"# shown={len(shown)}/{len(rows)} in_queue={nq} "
            f"(фильтры: voice={vf or 'все'}, folder_suffix={fs or 'нет'})"
        )
        return 0

    def _run_queue(cmd_label: str, *, lim: int | None) -> int:
        if not execute:
            print(f"site-tts {cmd_label}: dry-run (без --execute mp3 не пишется).")
        vf = _voice_arg()
        fs = _suffix_arg()
        if human_launch is not None:
            items = collect_human_launch_tts_items(
                human_launch, project_root=cfg.root_dir, limit=lim, voice_types=vf, folder_suffix=fs
            )
        else:
            items = collect_batch_items(
                site_root, project_root=cfg.root_dir, limit=lim, voice_types=vf, folder_suffix=fs
            )
        if not items:
            print("Очередь пуста: все mp3 уже есть, нет очищенного .txt или не прошли фильтры.")
            return 0
        print(f"Очередь: {len(items)} папок")
        rc = 0
        for it in items:
            r = run_site_tts_for_story(
                cfg.root_dir,
                story_name=it.story_name,
                modes_config=_modes_path(),
                execute=execute,
                force=force,
                site_output_root=site_root,
                human_launch=human_launch,
            )
            print(f"{it.story_name}: {r.status} | {r.message}")
            if r.logs_path:
                print(f"  log: {r.logs_path}")
            if r.status != "success":
                rc = 2
        return rc

    if args.site_tts_cmd in {"sync", "missing-mp3"}:
        lim = int(getattr(args, "limit", 0) or 0)
        return _run_queue("sync", lim=lim if lim > 0 else None)

    if args.site_tts_cmd == "first-n":
        n = max(1, int(args.n))
        return _run_queue("first-n", lim=n)

    if args.site_tts_cmd == "kokoro-colab":
        from orchestrator.site_tts.colab_batch import (
            export_kokoro_colab_batch,
            export_drive_texts,
            import_kokoro_colab_results,
            import_drive_mp3,
            setup_drive_workspace,
            verify_mp3_coverage,
            verify_drive_status,
            wait_drive_mp3_and_import,
        )

        sub = str(getattr(args, "site_tts_colab_cmd", "") or "").strip().lower()
        if sub == "export":
            lim = int(getattr(args, "limit", 0) or 0)
            bid = str(getattr(args, "batch_id", "") or "").strip() or None
            if human_launch is not None:
                try:
                    res_d = export_drive_texts(
                        cfg.root_dir,
                        limit=(lim if lim > 0 else None),
                        human_launch=human_launch,
                    )
                except ValueError as exc:
                    print(str(exc))
                    return 2
                if not res_d.get("ok", False):
                    print(res_d.get("message", "export-drive (human) failed"))
                    return 2
                print("[human-launch] Kokoro: txt/job → Google Drive (см. configs/site_tts.yaml → google_drive_tts).")
                print(res_d.get("message", "ok"))
                print(f"texts_dir={res_d.get('texts_dir')}")
                print(f"job_dir={res_d.get('job_dir')}")
                print(f"mp3_dir={res_d.get('mp3_dir')}")
                print(f"index_csv={res_d.get('index_csv')}")
                print(f"exported={res_d.get('exported')}")
                print(f"skipped={res_d.get('skipped')}")
                vj = str(res_d.get("voices_job_json") or "").strip()
                if vj:
                    print(f"voices_job_json={vj}")
                if res_d.get("colab_current_dir"):
                    print(f"colab_current_dir={res_d.get('colab_current_dir')}")
                    print(f"colab_current_texts={res_d.get('colab_current_texts')}")
                    print("import_current_cmd=python -m orchestrator site-tts kokoro-colab import --current")
                    print("verify_current_cmd=python -m orchestrator site-tts kokoro-colab verify --current")
                print(
                    "import_after_colab_cmd="
                    "python -m orchestrator site-tts --launch-name <LAUNCH> kokoro-colab import"
                )
                print(
                    "verify_drive_cmd=python -m orchestrator site-tts kokoro-colab verify-drive"
                )
                return 0
            res = export_kokoro_colab_batch(
                cfg.root_dir,
                limit=(lim if lim > 0 else None),
                batch_id=bid,
            )
            if not res.get("ok", False):
                print(res.get("message", "export failed"))
                return 2
            print(f"batch_id={res.get('batch_id')}")
            print(f"batch_dir={res.get('batch_dir')}")
            print(f"exported={res.get('exported')}")
            print(f"skipped={res.get('skipped')}")
            print(f"manifest={res.get('manifest_path')}")
            if res.get("current_dir"):
                print(f"current_dir={res.get('current_dir')}")
                print(f"texts_to_colab={res.get('texts_dir')}")
                print(f"mp3_from_colab={res.get('mp3_dir')}")
                print(f"stories_index={res.get('index_csv')}")
                print("import_current_cmd=python -m orchestrator site-tts kokoro-colab import --current")
                print("verify_current_cmd=python -m orchestrator site-tts kokoro-colab verify --current")
            if res.get("handoff_dir"):
                print("legacy_handoff=available (_COLAB_EXPORTS; optional/internal)")
            return 0

        if sub == "export-drive":
            lim = int(getattr(args, "limit", 0) or 0)
            tdir = getattr(args, "texts_dir", None)
            sfilter = getattr(args, "stories_filter_dir", None)
            try:
                res = export_drive_texts(
                    cfg.root_dir,
                    texts_dir=tdir,
                    limit=(lim if lim > 0 else None),
                    stories_filter_dir=sfilter,
                    human_launch=human_launch,
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            print(res.get("message", "ok"))
            print(f"texts_dir={res.get('texts_dir')}")
            print(f"job_dir={res.get('job_dir')}")
            print(f"mp3_dir={res.get('mp3_dir')}")
            print(f"stories_index={res.get('index_csv')}")
            print(f"exported={res.get('exported')}")
            print(f"skipped={res.get('skipped')}")
            print(f"stories_filter_applied={res.get('stories_filter_applied', False)}")
            if res.get("colab_current_dir"):
                print(f"colab_current_dir={res.get('colab_current_dir')}")
            if res.get("resume_wait_for_pending_job"):
                print("resume_wait_for_pending_job=1 (export body skipped; kokoro_voices_job.json not rewritten)")
            vj = str(res.get("voices_job_json") or "").strip()
            if vj:
                print(f"voices_job_json={vj}")
            return 0

        if sub == "setup-drive":
            try:
                res = setup_drive_workspace(cfg.root_dir)
            except ValueError as exc:
                print(str(exc))
                return 2
            for k in ("drive_root", "texts_dir", "mp3_dir", "scripts_dir", "cache_dir", "logs_dir", "job_dir", "runner_copied"):
                print(f"{k}={res.get(k)}")
            print(f"colab_command={res.get('colab_cmd')}")
            return 0

        if sub == "import":
            bid = str(getattr(args, "batch_id", "") or "").strip() or None
            bdir = getattr(args, "batch_dir", None)
            hdir = getattr(args, "handoff_dir", None)
            latest = bool(getattr(args, "latest", False))
            current = bool(getattr(args, "current", False))
            force = bool(getattr(args, "force", False))
            if current:
                res = import_kokoro_colab_results(
                    cfg.root_dir,
                    batch_id=bid,
                    batch_dir=bdir,
                    handoff_dir=hdir,
                    latest=latest,
                    current=current,
                    force=force,
                )
            elif human_launch is not None:
                try:
                    res = import_drive_mp3(cfg.root_dir, force=force, human_launch=human_launch)
                except ValueError as exc:
                    print(str(exc))
                    return 2
            else:
                res = import_kokoro_colab_results(
                    cfg.root_dir,
                    batch_id=bid,
                    batch_dir=bdir,
                    handoff_dir=hdir,
                    latest=latest,
                    current=current,
                    force=force,
                )
            if not res.get("ok", False):
                print(res.get("message", "import failed"))
                return 2
            if res.get("mode") == "current":
                print(f"current_dir={res.get('current_dir')}")
                print(f"results_drop_dir={res.get('results_drop_dir')}")
            elif res.get("missing_after_import") is not None:
                print(f"mp3_dir={res.get('mp3_dir')}")
                print(f"job_dir={res.get('job_dir')}")
                ma = res.get("missing_after_import")
                if isinstance(ma, list) and ma:
                    print(f"missing_after_import_count={len(ma)}")
            elif res.get("batch_dir"):
                print(f"batch_dir={res.get('batch_dir')}")
            if res.get("handoff_dir"):
                print(f"handoff_dir={res.get('handoff_dir')}")
                print(f"results_drop_dir={res.get('results_drop_dir')}")
            print(f"imported={res.get('imported')}")
            print(f"skipped_existing={res.get('skipped_existing')}")
            if "missing_result" in res:
                print(f"missing_result={res.get('missing_result')}")
            if "missing_story" in res:
                print(f"missing_story={res.get('missing_story')}")
            print(f"errors={res.get('errors')}")
            if int(res.get("missing_result", 0) or 0) > 0:
                print("hint=Проверьте results_drop_here или runs/tts_colab_batches/<batch_id>/results")
            return 0 if int(res.get("errors", 0) or 0) == 0 else 2

        if sub == "import-drive":
            mdir = getattr(args, "mp3_dir", None)
            force = bool(getattr(args, "force", False))
            try:
                res = import_drive_mp3(cfg.root_dir, mp3_dir=mdir, force=force, human_launch=human_launch)
            except ValueError as exc:
                print(str(exc))
                return 2
            print(f"mp3_dir={res.get('mp3_dir')}")
            print(f"imported={res.get('imported')}")
            print(f"skipped_existing={res.get('skipped_existing')}")
            print(f"missing_story={res.get('missing_story')}")
            print(f"invalid_mp3={res.get('invalid_mp3')}")
            print(f"errors={res.get('errors')}")
            return 0 if int(res.get("errors", 0) or 0) == 0 else 2

        if sub == "verify":
            bid = str(getattr(args, "batch_id", "") or "").strip() or None
            hdir = getattr(args, "handoff_dir", None)
            latest = bool(getattr(args, "latest", False))
            current = bool(getattr(args, "current", False))
            res = verify_mp3_coverage(
                cfg.root_dir,
                batch_id=bid,
                handoff_dir=hdir,
                latest=latest,
                current=current,
                human_launch=human_launch,
            )
            print(f"source_root={res.get('source_root')}")
            print(f"total_story_dirs={res.get('total_story_dirs')}")
            print(f"with_tts_text_file={res.get('with_tts_text_file')}")
            print(f"with_mp3={res.get('with_mp3')}")
            print(f"missing_mp3={res.get('missing_mp3')}")
            print(f"skipped_no_tts_file={res.get('skipped_no_tts_file')}")
            print(f"ambiguous_tts_files={res.get('ambiguous_tts_files')}")
            current_info = res.get("current")
            if isinstance(current_info, dict):
                print("-- current --")
                for k in ("current_dir", "batch_id", "texts_exported", "mapping_items", "mp3_found", "can_import", "missing_mp3", "extra_mp3", "first_missing", "first_extra", "error"):
                    if k in current_info:
                        print(f"{k}={current_info[k]}")
            batch = res.get("batch")
            if isinstance(batch, dict):
                print("-- batch --")
                for k in (
                    "batch_id",
                    "batch_dir",
                    "handoff_dir",
                    "exported_items",
                    "waiting_mp3",
                    "results_found",
                    "results_found_in_batch_results",
                    "results_found_in_handoff_drop",
                    "already_imported",
                    "missing_results",
                    "first_problems",
                    "error",
                ):
                    if k in batch:
                        print(f"{k}={batch[k]}")
            return 0

        if sub == "verify-drive":
            tdir = getattr(args, "texts_dir", None)
            mdir = getattr(args, "mp3_dir", None)
            try:
                res = verify_drive_status(cfg.root_dir, texts_dir=tdir, mp3_dir=mdir)
            except ValueError as exc:
                print(str(exc))
                return 2
            for k in (
                "texts_dir",
                "mp3_dir",
                "texts_count",
                "mp3_count",
                "valid_mp3_count",
                "invalid_mp3_count",
                "can_import",
                "missing_mp3",
                "extra_mp3",
                "first_missing",
                "first_extra",
                "first_invalid",
            ):
                print(f"{k}={res.get(k)}")
            return 0

        if sub == "wait-drive":
            mdir = getattr(args, "mp3_dir", None)
            force = bool(getattr(args, "force", False))
            wait_interval = int(getattr(args, "wait_interval_minutes", 0) or 0)
            max_wait = int(getattr(args, "max_wait_hours", 0) or 0)
            try:
                res = wait_drive_mp3_and_import(
                    cfg.root_dir,
                    mp3_dir=mdir,
                    wait_interval_minutes=(wait_interval if wait_interval > 0 else None),
                    max_wait_hours=(max_wait if max_wait > 0 else None),
                    force=force,
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            if not res.get("ok", False):
                print(res.get("message", "wait-drive failed"))
                print(f"status={res.get('status')}")
                return 2
            print("wait_drive=ok")
            print(f"status={res.get('status')}")
            print(f"cleanup={res.get('cleanup')}")
            return 0

        if sub == "full-cycle-drive":
            lim = int(getattr(args, "limit", 0) or 0)
            tdir = getattr(args, "texts_dir", None)
            mdir = getattr(args, "mp3_dir", None)
            force = bool(getattr(args, "force", False))
            wait_interval = int(getattr(args, "wait_interval_minutes", 0) or 0)
            max_wait = int(getattr(args, "max_wait_hours", 0) or 0)
            sfilter_fc = getattr(args, "stories_filter_dir", None)
            try:
                exp = export_drive_texts(
                    cfg.root_dir,
                    texts_dir=tdir,
                    limit=(lim if lim > 0 else None),
                    stories_filter_dir=sfilter_fc,
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            print(exp.get("message", "export ok"))
            print(f"exported={exp.get('exported')} skipped={exp.get('skipped')}")
            print(f"stories_filter_applied={exp.get('stories_filter_applied', False)}")
            resume_fc = bool(exp.get("resume_wait_for_pending_job"))
            print(f"resume_wait_for_pending_job={resume_fc}")
            if int(exp.get("exported", 0) or 0) <= 0 and not resume_fc:
                print("No prepared output/site stories found.")
                print("This is a TTS-only command.")
                print("For raw input stories, run:")
                print("[S] Full Site pipeline with Kokoro Google Drive TTS")
                return 2
            try:
                res = wait_drive_mp3_and_import(
                    cfg.root_dir,
                    mp3_dir=mdir,
                    wait_interval_minutes=(wait_interval if wait_interval > 0 else None),
                    max_wait_hours=(max_wait if max_wait > 0 else None),
                    force=force,
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            if not res.get("ok", False):
                print(res.get("message", "full-cycle-drive failed"))
                print(f"status={res.get('status')}")
                return 2
            print("full_cycle_drive=ok")
            print(f"status={res.get('status')}")
            print(f"cleanup={res.get('cleanup')}")
            return 0

    print("Неизвестная подкоманда site-tts")
    return 2


def main() -> int:
    args = _parser().parse_args()
    cfg = load_config(args.config)
    modes_cfg = args.modes_config
    modes = load_runtime_modes(modes_cfg)
    runner = Runner(cfg)

    if args.command == "show-modes":
        print("Текущие режимы:")
        for k in DEFAULT_MODES:
            print(f"- {k}: {modes.get(k, DEFAULT_MODES[k])}")
        return 0

    if args.command == "set-mode":
        try:
            updated = set_runtime_mode(modes_cfg, args.key, args.value)
        except Exception as exc:
            print(f"Ошибка: {exc}")
            print("Допустимые ключи и значения:")
            for k in DEFAULT_MODES:
                print(f"- {k}: {sorted(ALLOWED_VALUES[k])}")
            return 2
        print("Режим обновлён:")
        for k in DEFAULT_MODES:
            print(f"- {k}: {updated.get(k, DEFAULT_MODES[k])}")
        return 0

    if args.command == "reset-modes":
        save_runtime_modes(modes_cfg, dict(DEFAULT_MODES))
        print("Режимы сброшены к значениям по умолчанию.")
        return 0

    if args.command == "cleanup-scan":
        root = args.root.resolve()
        print_scan(scan_generated_artifacts(root))
        return 0

    if args.command == "cleanup-move":
        root = args.root.resolve()
        rel_paths = [x.strip().replace("\\", "/") for x in str(args.paths).split(",") if x.strip()]
        result = move_items_to_quarantine(root, rel_paths, timestamp=(args.timestamp or None))
        print_scan(result)
        return 0

    if args.command == "cleanup-run":
        root = args.root.resolve()
        result = move_run_to_quarantine(root, args.run_id, timestamp=(args.timestamp or None))
        print_scan(result)
        return 0

    if args.command == "youtube":
        sub_cmd = str(getattr(args, "youtube_cmd", "") or "").strip().lower()
        if sub_cmd == "prefilter-from-site":
            result = run_youtube_prefilter_from_site(
                config=cfg,
                options=YoutubePrefilterFromSiteOptions(
                    site_run_id=str(args.site_run_id).strip(),
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    min_words=int(args.min_words),
                    max_words=int(args.max_words),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube prefilter-from-site failed"))
                return 2
            print(
                "summary:"
                f" total={result.get('total', 0)}"
                f" size_yes={result.get('size_yes', 0)}"
                f" size_no={result.get('size_no', 0)}"
                f" too_short={result.get('too_short', 0)}"
                f" too_long={result.get('too_long', 0)}"
                f" missing_cleaned_path={result.get('missing_cleaned_path', 0)}"
            )
            print(f"deferred_manifest={result.get('deferred_manifest')}")
            print(f"selection_dir={result.get('selection_dir')}")
            print(f"gemini_selection_input_dir={result.get('gemini_selection_input_dir')}")
            print(f"gemini_selection_output_dir={result.get('gemini_selection_output_dir')}")
            print(f"gemini_selection_parsed_dir={result.get('gemini_selection_parsed_dir')}")
            print(f"gemini_safe_input_dir={result.get('gemini_safe_input_dir')}")
            print(f"gemini_safe_output_dir={result.get('gemini_safe_output_dir')}")
            print(f"gemini_safe_parsed_dir={result.get('gemini_safe_parsed_dir')}")
            print(f"status_jsonl={result.get('status_jsonl')}")
            return 0

        if sub_cmd == "selection-from-site":
            result = run_youtube_selection_from_site(
                config=cfg,
                options=YoutubeSelectionFromSiteOptions(
                    site_run_id=str(args.site_run_id).strip(),
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    min_words=int(args.min_words),
                    max_words=int(args.max_words),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube selection-from-site failed"))
                return 2
            print(
                "summary:"
                f" status={result.get('status', '')}"
                f" size_yes={result.get('size_yes', 0)}"
                f" size_no={result.get('size_no', 0)}"
                f" prepared={result.get('prepared', 0)}"
            )
            if result.get("message"):
                print(f"message={result.get('message')}")
            print(f"report_path={result.get('report_path')}")
            if result.get("manifest_path"):
                print(f"manifest_path={result.get('manifest_path')}")
            if result.get("input_dir"):
                print(f"input_dir={result.get('input_dir')}")
            if result.get("output_dir"):
                print(f"output_dir={result.get('output_dir')}")
            if result.get("raw_dir"):
                print(f"raw_dir={result.get('raw_dir')}")
            return 0

        if sub_cmd == "diagnose-cleaned-paths":
            result = run_youtube_diagnose_cleaned_paths(
                config=cfg,
                site_run_id=str(args.site_run_id).strip(),
                youtube_run_id=str(args.youtube_run_id).strip(),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube diagnose-cleaned-paths failed"))
                return 2
            print(
                "summary:"
                f" items_total={result.get('items_total', 0)}"
                f" resolved_deferred={result.get('resolved_sources', {}).get('deferred.cleaned_path', 0)}"
                f" resolved_run_story={result.get('resolved_sources', {}).get('run_story_dir.cleaned_story', 0)}"
                f" resolved_output_site={result.get('resolved_sources', {}).get('output_site.cleaned_story', 0)}"
                f" resolved_voice_variant={result.get('resolved_sources', {}).get('run_story_dir.voice_variant', 0)}"
                f" missing={result.get('resolved_sources', {}).get('missing', 0)}"
            )
            print(f"diagnostics_txt={result.get('diagnostics_txt')}")
            print(f"diagnostics_json={result.get('diagnostics_json')}")
            return 0

        if sub_cmd == "parse-gemini-selection":
            result = run_youtube_parse_gemini_selection(
                config=cfg,
                options=YoutubeParseGeminiSelectionOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube parse-gemini-selection failed"))
                return 2
            print(
                "summary:"
                f" total_inputs={result.get('total_inputs', 0)}"
                f" selection_yes={result.get('selection_yes', 0)}"
                f" selection_no={result.get('selection_no', 0)}"
                f" missing_output={result.get('missing_gemini_output', 0)}"
            )
            print(f"selection_yes_json={result.get('selection_yes_json')}")
            print(f"selection_no_json={result.get('selection_no_json')}")
            print(f"report_txt={result.get('report_txt')}")
            return 0

        if sub_cmd == "prepare-gemini-selection-input":
            result = run_youtube_prepare_gemini_selection_input(
                config=cfg,
                options=YoutubePrepareGeminiSelectionInputOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube prepare-gemini-selection-input failed"))
                return 2
            print(
                "summary:"
                f" prepared={result.get('prepared', 0)}"
                f" created_input_files={result.get('created_input_files', 0)}"
                f" skipped_input_files={result.get('skipped_input_files', 0)}"
            )
            print(f"manifest_path={result.get('manifest_path')}")
            print(f"input_dir={result.get('input_dir')}")
            print(f"output_dir={result.get('output_dir')}")
            print(f"parsed_dir={result.get('parsed_dir')}")
            return 0

        if sub_cmd == "prepare-safe-input":
            result = run_youtube_prepare_safe_input(
                config=cfg,
                options=YoutubePrepareSafeInputOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube prepare-safe-input failed"))
                return 2
            print(
                "summary:"
                f" prepared={result.get('prepared', 0)}"
                f" created_input_files={result.get('created_input_files', 0)}"
                f" skipped_input_files={result.get('skipped_input_files', 0)}"
            )
            print(f"safe_input_manifest={result.get('safe_input_manifest')}")
            print(f"safe_input_dir={result.get('safe_input_dir')}")
            print(f"safe_output_dir={result.get('safe_output_dir')}")
            print(f"safe_parsed_dir={result.get('safe_parsed_dir')}")
            print(f"output_youtube_root={result.get('output_youtube_root')}")
            print(f"status_jsonl={result.get('status_jsonl')}")
            return 0

        if sub_cmd == "continue-after-selection":
            result = run_youtube_continue_after_selection(
                config=cfg,
                options=YoutubeContinueAfterSelectionOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube continue-after-selection failed"))
                return 2
            print(
                "summary:"
                f" status={result.get('status', '')}"
                f" selection_yes={result.get('selection_yes', 0)}"
                f" selection_no={result.get('selection_no', 0)}"
                f" safe_prepared={result.get('prepared_safe_items', 0)}"
            )
            if result.get("message"):
                print(f"message={result.get('message')}")
            print(f"report_path={result.get('report_path')}")
            if result.get("safe_input_manifest"):
                print(f"safe_input_manifest={result.get('safe_input_manifest')}")
            if result.get("safe_input_dir"):
                print(f"safe_input_dir={result.get('safe_input_dir')}")
            if result.get("safe_output_dir"):
                print(f"safe_output_dir={result.get('safe_output_dir')}")
            return 0

        if sub_cmd == "init-bridge-fixture":
            result = run_youtube_init_bridge_fixture(
                config=cfg,
                options=YoutubeInitBridgeFixtureOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube init-bridge-fixture failed"))
                return 2
            print(f"ok youtube_run_id={result.get('youtube_run_id')}")
            if result.get("message"):
                print(f"message={result.get('message')}")
            print(f"youtube_selected_yes={result.get('youtube_selected_yes')}")
            return 0

        if sub_cmd == "build-bridge-manifest":
            modes_path = (
                modes_cfg.resolve()
                if modes_cfg.is_absolute()
                else (cfg.root_dir / modes_cfg).resolve()
            )
            result = run_youtube_build_bridge_manifest(
                config=cfg,
                options=YoutubeBuildBridgeManifestOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    dry_run=bool(args.dry_run),
                    fixture_layout=bool(args.fixture_layout),
                    modes_config=modes_path,
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube build-bridge-manifest failed"))
                return 2
            print(
                "summary:"
                f" selected_yes_count={result.get('selected_yes_count', 0)}"
                f" story_manifests={len(result.get('story_manifests') or [])}"
            )
            print(f"run_manifest={result.get('run_manifest')}")
            print(f"validation_report={result.get('validation_report')}")
            for p in result.get("story_manifests") or []:
                print(f"story_manifest={p}")
            return 0

        if sub_cmd == "prepare-safe-bridge":
            result = run_youtube_prepare_safe_bridge(
                config=cfg,
                options=YoutubePrepareSafeBridgeOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    story_id=str(args.story_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube prepare-safe-bridge failed"))
                return 2
            print(f"ok staging_dir={result.get('staging_dir')}")
            print(f"staging_input_txt={result.get('staging_input_txt')}")
            print(f"input_resolved_from={result.get('input_resolved_from')}")
            print(f"safe_bridge_status={result.get('safe_bridge_status')}")
            print(f"story_manifest={result.get('story_manifest')}")
            return 0

        if sub_cmd == "import-safe-result":
            result = run_youtube_import_safe_result(
                config=cfg,
                options=YoutubeImportSafeResultOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    story_id=str(args.story_id).strip(),
                    force=bool(args.force),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube import-safe-result failed"))
                return 2
            print(f"import_status={result.get('import_status')}")
            if result.get("message"):
                print(f"message={result.get('message')}")
            if result.get("safe_story_path"):
                print(f"safe_story_path={result.get('safe_story_path')}")
            if result.get("imported_from"):
                print(f"imported_from={result.get('imported_from')}")
            print(f"safe_bridge_status={result.get('safe_bridge_status')}")
            return 0

        if sub_cmd == "run-safe-bridge":
            result = run_youtube_run_safe_bridge(
                config=cfg,
                options=YoutubeRunSafeBridgeOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                    force=bool(args.force),
                    reuse_legacy_user_data=bool(getattr(args, "reuse_legacy_user_data", False)),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube run-safe-bridge failed"))
                return 2
            print(f"skipped_subprocess={result.get('skipped_subprocess', False)}")
            print(f"reuse_legacy_user_data={result.get('reuse_legacy_user_data', False)}")
            print(f"gemini_stories_dir={result.get('gemini_stories_dir', '')}")
            print(f"gemini_user_data_dir={result.get('gemini_user_data_dir', '')}")
            if result.get("story_folders_preview_count") is not None:
                print(f"story_folders_preview_count={result.get('story_folders_preview_count')}")
            if result.get("start_bot_index_note"):
                print(f"start_bot_index={result.get('start_bot_index')} ({result.get('start_bot_index_note')})")
            if result.get("manual_cmd_windows"):
                print(f"manual_cmd_windows={result.get('manual_cmd_windows')}")
            if result.get("message"):
                print(f"message={result.get('message')}")
            if result.get("gemini_auto_exit_code") is not None:
                print(f"gemini_auto_exit_code={result.get('gemini_auto_exit_code')}")
            if result.get("safe_bot_log"):
                print(f"safe_bot_log={result.get('safe_bot_log')}")
            imp = result.get("import") or {}
            if imp:
                print(f"import_status={imp.get('import_status')}")
            return 0

        print("Неизвестная подкоманда youtube")
        return 2

    if args.command == "extract-series":
        if bool(args.execute) and bool(args.dry_run):
            print("Нельзя одновременно использовать --execute и --dry-run.")
            return 2
        execute = bool(args.execute)
        if not execute:
            print("extract-series: dry-run mode (default). Use --execute for real file moves.")
        result = run_series_extraction(
            config=cfg,
            options=SeriesExtractorOptions(
                source_dir=args.source_dir,
                execute=execute,
                progress_every=max(1, int(args.progress_every or 5000)),
                top_folders_limit=(int(args.top_folders_limit) if int(args.top_folders_limit or 0) > 0 else None),
                only_folders=[str(x) for x in (args.only_folder or [])],
                max_files_per_folder=(int(args.max_files_per_folder) if int(args.max_files_per_folder or 0) > 0 else None),
                stop_after_files=(int(args.stop_after_files) if int(args.stop_after_files or 0) > 0 else None),
            ),
        )
        if not result.get("ok", False):
            print(result.get("message", "extract-series failed"))
            return 2
        summary = result.get("summary", {})
        print(
            "summary:"
            f" top_folders={summary.get('top_folders_scanned', 0)}"
            f" confirmed_series={summary.get('confirmed_series_count', 0)}"
            f" confirmed_members={summary.get('confirmed_series_member_rows', 0)}"
            f" series_files={summary.get('series_files_found', 0)}"
            f" single_candidates={summary.get('single_part_candidates', 0)}"
            f" single_candidates_moved={summary.get('single_part_candidates_moved', 0)}"
            f" normal_stories={summary.get('normal_stories', 0)}"
            f" moved_series={summary.get('moved_count', 0)}"
            f" moved_total={summary.get('moved_total', summary.get('moved_count', 0))}"
            f" elapsed_sec={summary.get('elapsed_sec', 0)}"
            f" files_per_sec={summary.get('files_per_sec', 0)}"
        )
        print(
            "limits:"
            f" partial_run={bool(result.get('partial_run', False))}"
            f" interrupted_or_limited={bool(result.get('interrupted_or_limited', False))}"
            f" processed_top_folders={result.get('processed_top_folders', 0)}"
            f" skipped_due_to_limit={result.get('skipped_due_to_limit', 0)}"
        )
        print(f"report_json={result.get('report_json')}")
        print(f"report_csv={result.get('report_csv')}")
        return 0

    if args.command == "audit-series-titles":
        run_audit_series_titles(
            config=cfg,
            stories_input_dir=args.stories_input_dir,
            gemini_queue_dir=args.gemini_queue_dir,
            batch_manifest=args.batch_manifest,
            max_examples=max(1, int(args.max_examples or 8)),
        )
        return 0

    if args.command == "audit-series-all-sources":
        gqp = getattr(args, "gemini_queue_dir", None)
        if bool(getattr(args, "no_gemini_queue", False)):
            gqp = None
        run_series_title_audit_all_sources(
            config=cfg,
            stories_input_dir=args.stories_input_dir,
            gemini_queue_dir=gqp,
            max_txt_files=max(1000, int(getattr(args, "max_txt_files", 250000) or 250000)),
        )
        return 0

    if args.command == "gemini-audit":
        from orchestrator.gemini_resume_audit import run_gemini_audit_cli

        subc = str(getattr(args, "gemini_audit_cmd", "") or "").strip()
        ext_raw = str(getattr(args, "extensions", ".txt") or ".txt")
        ext_list = [x.strip() for x in ext_raw.split(",") if x.strip()]
        if not ext_list:
            ext_list = [".txt"]
        run_path = Path(getattr(args, "run_path", "."))
        stories_dir_arg = getattr(args, "stories_dir", None)
        if subc == "dedupe-plan" and not bool(getattr(args, "dry_run", False)):
            print("dedupe-plan: укажите --dry-run (план без перемещения/удаления папок).")
            return 2
        dry = bool(getattr(args, "dry_run", False))
        out = run_gemini_audit_cli(
            mode=subc,
            run_path=run_path,
            stories_dir=Path(stories_dir_arg).resolve() if stories_dir_arg is not None else None,
            extensions=ext_list,
            logs_dir=cfg.logs_dir,
            dry_run=dry,
        )
        print(f"audit_json={out.get('audit_json')}")
        print(f"audit_md={out.get('audit_md')}")
        if out.get("dedupe_plan_json"):
            print(f"dedupe_plan_json={out.get('dedupe_plan_json')}")
        return 0

    if args.command == "return-series-from-input":
        execute = bool(args.execute)
        if not execute:
            print("return-series-from-input: dry-run (по умолчанию). Для переноса укажите --execute.")
        result = run_stories_input_series_return(
            config=cfg,
            options=StoriesInputSeriesReturnOptions(input_dir=args.input_dir, execute=execute),
        )
        if not result.get("ok", False):
            print(result.get("message", "return-series-from-input failed"))
            return 2
        summary = result.get("summary", {})
        print(
            "summary:"
            f" txt_total={summary.get('txt_total', 0)}"
            f" serial_planned={summary.get('serial_planned', 0)}"
            f" keep_non_serial={summary.get('keep_non_serial', 0)}"
            f" moved_count={summary.get('moved_count', 0)}"
            f" errors_count={summary.get('errors_count', 0)}"
        )
        print(f"report_csv={result.get('report_csv')}")
        print(f"report_json={result.get('report_json')}")
        errs = result.get("errors") or []
        if errs:
            for e in errs[:20]:
                print(f"error: {e}")
            if len(errs) > 20:
                print(f"... and {len(errs) - 20} more errors")
        return 0

    if args.command == "clean-library-series":
        execute_cls = bool(args.execute)
        if not execute_cls:
            print("clean-library-series: dry-run (по умолчанию). Для переноса укажите --execute.")
        result_cls = run_clean_library_series(
            config=cfg,
            options=CleanLibrarySeriesOptions(library_root=args.library_root, execute=execute_cls),
        )
        if not result_cls.get("ok", False):
            print("clean-library-series: завершилось с ошибками (см. report_json и stderr).")
            errs_cls = result_cls.get("errors") or []
            for e in errs_cls[:30]:
                print(f"error: {e}")
            return 2
        return 0

    if args.command == "sample-library":
        if bool(args.execute) and bool(args.dry_run):
            print("Нельзя одновременно использовать --execute и --dry-run.")
            return 2
        execute = bool(args.execute)
        copy_mode = bool(getattr(args, "copy", False))
        if not execute:
            op = "COPY (preview)" if copy_mode else "MOVE (preview)"
            print(f"sample-library: dry-run mode (default). Use --execute for real file operation ({op}).")
        else:
            if copy_mode:
                if not bool(getattr(args, "confirm_add", False)):
                    print("sample-library: --execute --copy требует явного подтверждения --confirm-add.")
                    print("Команда копирует выбранные .txt в target-dir; исходники в source-dir не удаляются.")
                    print(
                        "Пример: python -m orchestrator sample-library "
                        "--source-dir \"D:\\lib\\output\" "
                        "--target-dir \"D:\\Cursor AI\\Content-Factory\\stories\\input\" "
                        "--per-folder 50 --copy --allow-nonempty-target --execute --confirm-add"
                    )
                    return 2
                print("This will COPY selected files into target (sources remain).")
            else:
                if not bool(args.confirm_move):
                    print("sample-library: --execute без --copy требует явного подтверждения --confirm-move.")
                    print("Команда физически перемещает файлы из source library в target-dir.")
                    print(
                        "Пример: python -m orchestrator sample-library "
                        "--source-dir \"D:\\Проекты сохр\\AudioProject\\output\" "
                        "--target-dir \"D:\\Cursor AI\\Content-Factory\\stories\\input\" "
                        "--per-folder 50 --execute --confirm-move"
                    )
                    return 2
                print("This will MOVE selected files from source library to target.")
        result = run_library_sampler(
            config=cfg,
            options=LibrarySamplerOptions(
                source_dir=args.source_dir,
                target_dir=args.target_dir,
                per_folder=int(args.per_folder),
                seed=(str(args.seed).strip() or None),
                execute=execute,
                allow_nonempty_target=bool(args.allow_nonempty_target),
                copy_mode=copy_mode,
            ),
        )
        if not result.get("ok", False):
            print(result.get("message", "sample-library failed"))
            if "existing_txt_count" in result:
                print(f"existing_txt_count={result.get('existing_txt_count')}")
                print(f"existing_txt_examples={result.get('existing_txt_examples')}")
                print(f"target_dir={result.get('target_dir')}")
            return 2
        print(
            "summary:"
            f" top_folders_found={result.get('top_folders_found', 0)}"
            f" would_move={result.get('would_move_total', 0)}"
            f" moved={result.get('moved_total', 0)}"
            f" skipped_queue_basename={result.get('skipped_queue_basename_total', 0)}"
            f" collision_renames={result.get('renamed_due_to_collision_total', result.get('skipped_existing_target', 0))}"
            f" errors={result.get('errors_count', 0)}"
        )
        counts = result.get("selected_by_source_folder") or {}
        if counts:
            print(f"per_folder_planned={counts}")
        skip_f = result.get("skipped_queue_by_folder") or {}
        if skip_f:
            print(f"skipped_queue_by_folder={skip_f}")
        print(f"seed={result.get('seed')}")
        print(f"series_scan_excluded={bool(result.get('series_scan_excluded', False))}")
        print(f"manifest_json={result.get('manifest_path')}")
        print(f"report_csv={result.get('report_csv_path')}")
        if execute:
            print(f"target_manifest={result.get('target_manifest_path')}")
        return 0

    if args.command == "archive-input":
        if bool(args.execute) and bool(args.dry_run):
            print("Нельзя одновременно использовать --execute и --dry-run.")
            return 2
        execute = bool(args.execute)
        if not execute:
            print("archive-input: dry-run mode (default). Use --execute for real file MOVE.")
        result = run_archive_input(
            config=cfg,
            options=ArchiveInputOptions(
                input_dir=args.input_dir,
                execute=execute,
            ),
        )
        if not result.get("ok", False):
            print(result.get("message", "archive-input failed"))
            return 2
        print(
            "summary:"
            f" txt_count={result.get('txt_count', 0)}"
            f" batch_manifest_exists={bool(result.get('batch_manifest_exists', False))}"
            f" planned_total={result.get('planned_total', 0)}"
            f" moved_total={result.get('moved_total', 0)}"
            f" errors={result.get('errors_count', 0)}"
        )
        print(f"archive_dir={result.get('archive_dir')}")
        print(f"archived_manifest={result.get('archived_manifest_path')}")
        return 0

    if args.command == "site-tts":
        from orchestrator.site_tts.process_lock import site_tts_execute_lock

        execute_flag = bool(getattr(args, "execute", False))
        lock_cm = site_tts_execute_lock(cfg.service_dir) if execute_flag else contextlib.nullcontext()
        try:
            with lock_cm:
                return _site_tts_cli(args, cfg)
        except RuntimeError as exc:
            print(str(exc))
            return 2

    if args.command == "site-visual":
        from orchestrator.site_visual.importer import import_site_visuals

        sub_cmd = str(getattr(args, "site_visual_cmd", "") or "").strip().lower()
        if sub_cmd != "import":
            print("Неизвестная подкоманда site-visual")
            return 2
        if bool(args.execute) and bool(args.dry_run):
            print("Нельзя одновременно использовать --execute и --dry-run.")
            return 2
        execute = bool(args.execute)
        res = import_site_visuals(
            cfg.root_dir,
            execute=execute,
            force=bool(args.force),
            import_dir=args.import_dir,
            report_path=args.report_path,
        )
        print(f"mode={res.get('mode')}")
        print(f"input_dir={res.get('input_dir')}")
        print(f"site_root={res.get('site_root')}")
        print(f"imported_count={res.get('imported_count')}")
        print(f"already_exists_count={res.get('already_exists_count')}")
        print(f"missing_count={res.get('missing_count')}")
        print(f"unmatched_images_count={res.get('unmatched_images_count')}")
        print(f"duplicate_images_count={res.get('duplicate_images_count')}")
        print(f"report_path={res.get('report_path')}")
        items = list(res.get("items", []))
        if items:
            print("-- image_items --")
            for row in items[:200]:
                print(
                    f"{row.get('status')}\t{row.get('source_image_path')}\t{row.get('matched_story_dir')}\t{row.get('reason')}"
                )
            if len(items) > 200:
                print(f"...truncated {len(items) - 200} rows")
        return 0

    if args.command == "site-publish":
        from orchestrator.site_publish.env_doctor import run_site_publish_env_doctor
        from orchestrator.site_publish.publish import run_site_publish
        from orchestrator.site_publish.prepare import prepare_site_publish

        sub_cmd = str(getattr(args, "site_publish_cmd", "") or "").strip().lower()
        if sub_cmd == "env-doctor":
            res = run_site_publish_env_doctor(
                content_factory_root=cfg.root_dir,
                dirtysecrets_root=args.dirtysecrets_path,
                write_env_file=not bool(args.no_write_env),
            )
            mv = dict(res.get("masked_active_values", {}))
            print(f"source_env_files_dirtysecrets={len(res.get('env_files_checked', {}).get('dirtysecrets', []))}")
            print(f"source_env_files_content_factory={len(res.get('env_files_checked', {}).get('content_factory', []))}")
            print(f"target_env_file={res.get('target_env_file')}")
            print(f"supabase_url={mv.get('SUPABASE_URL', '<missing>')}")
            print(f"supabase_project_ref={mv.get('SUPABASE_PROJECT_REF', '<missing>')}")
            print(f"server_side_key_present={'yes' if bool(res.get('server_side_key_present', False)) else 'no'}")
            print(f"server_side_key_source={res.get('server_side_key_source', '<missing>')}")
            print(f"service_role_key_present={'yes' if mv.get('SUPABASE_SERVICE_ROLE_KEY') not in {'<missing>'} else 'no'}")
            print(f"secret_key_present={'yes' if mv.get('SUPABASE_SECRET_KEY') not in {'<missing>'} else 'no'}")
            print(f"anon_key_present={'yes' if mv.get('SUPABASE_ANON_KEY') not in {'<missing>'} else 'no'}")
            print(f"r2_account_present={'yes' if mv.get('R2_ACCOUNT_ID') not in {'<missing>'} else 'no'}")
            print(f"r2_bucket={mv.get('R2_BUCKET_NAME', '<missing>')}")
            print(f"r2_public_url={mv.get('R2_PUBLIC_URL', '<missing>')}")
            print(f"site_url={mv.get('NEXT_PUBLIC_SITE_URL', '<missing>')}")
            print(f"backend_api_url={mv.get('NEXT_PUBLIC_API_URL', mv.get('API_URL', '<missing>'))}")
            print(f"warnings_count={len(res.get('warnings', []))}")
            print(f"blockers_count={len(res.get('blockers', []))}")
            for w in list(res.get("warnings", []))[:50]:
                print(f"warning={w}")
            for b in list(res.get("blockers", []))[:50]:
                print(f"blocker={b}")
            print(f"report_path={res.get('report_path')}")
            return 0 if not res.get("blockers") else 2

        if sub_cmd == "publish":
            if bool(args.execute) and bool(args.dry_run):
                print("Нельзя одновременно использовать --execute и --dry-run.")
                return 2
            execute = bool(args.execute)
            dry_run = bool(args.dry_run) or (not execute)
            if not execute:
                print("site-publish publish: dry-run (default). Use --execute for real publish.")
            res = run_site_publish(
                content_factory_root=cfg.root_dir,
                story=str(getattr(args, "story", "") or "").strip(),
                dry_run=dry_run,
                execute=execute,
                dirtysecrets_root=args.dirtysecrets_path,
            )
            if res.get("status") == "blocked":
                print(f"status=blocked reason={res.get('reason')}")
                cmd = res.get("command", [])
                if cmd:
                    print(f"command={' '.join([str(x) for x in cmd])}")
                print(f"cwd={res.get('cwd')}")
                out = str(res.get("stdout", "") or "")
                err = str(res.get("stderr", "") or "")
                if out:
                    print(f"stdout_tail={out}")
                if err:
                    print(f"stderr_tail={err}")
                print(f"env_report_path={res.get('env_report_path')}")
                print(f"report_path={res.get('report_path')}")
                print(f"result_jsonl={res.get('result_jsonl')}")
                return 2
            print(f"status={res.get('status')} returncode={res.get('returncode')}")
            if res.get("status") != "done":
                print(f"reason={res.get('reason')}")
                cmd = res.get("command", [])
                if cmd:
                    print(f"command={' '.join([str(x) for x in cmd])}")
                print(f"cwd={res.get('cwd')}")
                out = str(res.get("stdout", "") or "")
                err = str(res.get("stderr", "") or "")
                if out:
                    print(f"stdout_tail={out}")
                if err:
                    print(f"stderr_tail={err}")
            print(f"env_report_path={res.get('env_report_path')}")
            print(f"report_path={res.get('report_path')}")
            print(f"result_jsonl={res.get('result_jsonl')}")
            return 0 if res.get("ok", False) else 2

        if sub_cmd != "prepare":
            print("Неизвестная подкоманда site-publish")
            return 2
        if bool(args.execute) and bool(args.dry_run):
            print("Нельзя одновременно использовать --execute и --dry-run.")
            return 2
        execute = bool(args.execute)
        if not execute and not bool(args.dry_run):
            print("site-publish prepare: dry-run (default). Use --execute for real copy.")
        res = prepare_site_publish(
            cfg.root_dir,
            execute=execute,
            force=bool(args.force),
            story=str(getattr(args, "story", "") or "").strip(),
        )
        print(
            "scanned="
            + str(res.get("total_stories"))
            + " ready="
            + str(res.get("ready_count"))
            + " prepared="
            + str(res.get("prepared_count"))
            + " skipped="
            + str(res.get("skipped_count"))
        )
        print(
            "missing_audio="
            + str(res.get("missing_audio_count"))
            + " missing_image="
            + str(res.get("missing_image_count"))
            + " missing_info="
            + str(res.get("missing_info_count"))
            + " missing_text="
            + str(res.get("missing_text_count"))
        )
        print(f"report_path={res.get('report_path')}")
        return 0

    if args.command == "prepare-fish-tts-runpod-pack":
        story = str(args.story_name).strip() or None
        result = prepare_fish_s2_pro_runpod_job_pack(
            cfg.root_dir,
            job_id=str(args.job_id).strip() or "fish_s2_pro_test_001",
            story_name=story,
            force=bool(args.force),
            data_dirs=cfg.data_dirs,
        )
        if not result.get("ok", False):
            print(result.get("message", "failed"))
            return 2
        print(result.get("message", "ok"))
        for k in ("story_name", "job_root", "job_json", "eligible_count"):
            if k in result:
                print(f"{k}={result[k]}")
        return 0

    if args.command == "preflight":
        run_profile = args.run_profile or cfg.default_run_profile
        allow_real_stages = [x.strip() for x in args.allow_real_stages.split(",") if x.strip()]
        checks = run_preflight(
            cfg,
            pipeline=args.pipeline,
            execute=args.execute,
            run_profile=run_profile,
            allow_real_stages=allow_real_stages,
            stories_dir=args.stories_dir,
            story_extensions=cfg.pre_filter_extensions,
        )
        ok = True
        for c in checks:
            marker = "OK" if c.ok else "FAIL"
            print(f"[{marker}] {c.message}")
            ok = ok and c.ok
        return 0 if ok else 2

    if args.command == "plan":
        for step in runner.plan(args.pipeline, args.story_id):
            print(
                f"{step['stage']}: unsafe={step['unsafe']} dry_run_only={step['dry_run_only']} "
                f"entrypoint={step['entrypoint']}"
            )
        return 0

    if args.command == "status":
        store = StatusStore(cfg.status_file)
        for rec in store.latest(limit=args.limit):
            print(
                f"{rec.timestamp} | {rec.story_id} | {rec.pipeline} | "
                f"{rec.stage} | {rec.state} | {rec.message}"
            )
        return 0

    if args.command == "run":
        if not args.execute:
            print("Running in DRY-RUN mode (default). Use --execute for explicit real mode.")
        print("Текущие режимы:")
        for k in DEFAULT_MODES:
            print(f"- {k}: {modes.get(k, DEFAULT_MODES[k])}")
        run_profile = args.run_profile or cfg.default_run_profile
        allow_real_stages = [x.strip() for x in args.allow_real_stages.split(",") if x.strip()]
        run_id, pipeline_ok = runner.run(
            RunOptions(
                pipeline=args.pipeline,
                story_id=args.story_id,
                execute=args.execute,
                run_profile=run_profile,
                stories_dir=args.stories_dir,
                allow_real_stages=allow_real_stages,
                launch_dir=getattr(args, "launch_dir", None),
            )
        )
        print(f"run_id={run_id}")
        if not pipeline_ok:
            print("[FAILED] orchestrator run: pipeline завершился с ошибкой этапа", flush=True)
            return 2
        return 0

    if args.command == "filter-length":
        if not args.execute:
            print("Running in DRY-RUN mode (default). Use --execute for file moves.")
        extensions = (
            [x.strip() for x in args.extensions.split(",") if x.strip()]
            if args.extensions
            else cfg.pre_filter_extensions
        )
        result = run_length_filter(
            config=cfg,
            options=LengthFilterOptions(
                stories_dir=args.stories_dir,
                short_dir=args.short_dir,
                execute=args.execute,
                words_per_minute=args.words_per_minute or cfg.pre_filter_words_per_minute,
                min_minutes=args.min_minutes or cfg.pre_filter_min_minutes,
                min_words=args.min_words or cfg.pre_filter_min_words,
                extensions=extensions,
            ),
        )
        print(result.get("summary", result.get("message", "done")))
        if not result.get("ok", False):
            return 2
        return 0

    if args.command == "launch":
        from orchestrator.human_launch_layout import generated_launch_name, sanitize_launch_folder_name
        from orchestrator.human_launch_lifecycle import (
            archive_launch,
            delete_launch,
            generate_final_report_launch,
            print_resume_contract,
            resume_launch_execute,
            resume_plan,
            verify_runtime_launch,
        )
        from orchestrator.human_launch_legacy_sync import (
            mirror_legacy_pipeline_to_human,
            mirror_phase_a_progress_to_human,
            plan_mirror_legacy_pipeline_to_human,
        )
        from orchestrator.human_launch_migrate import (
            cleanup_plan,
            inspect_human_structure,
            migrate_to_human_structure,
            print_inspect_report,
            print_migrate_dry_run_report,
        )
        from orchestrator.human_launch_gemini_preflight import run_gemini_preflight_for_human_launch
        from orchestrator.human_launch_site_bootstrap import (
            full_site_cycle_execute,
            full_site_cycle_plan,
            run_site_flow_execute,
            smoke_site_cycle_execute,
            smoke_site_cycle_plan,
            start_site_launch,
        )

        def _effective_launch_name(raw: str, *, smoke: bool = False) -> str:
            cleaned = sanitize_launch_folder_name(raw)
            return cleaned if cleaned else generated_launch_name(smoke=smoke)

        if args.launch_cmd == "inspect":
            name = str(getattr(args, "name", "") or "").strip()
            fr = str(getattr(args, "from_run_id", "") or "").strip()
            if not name and not fr:
                print("Укажите --name и/или --from-run-id.")
                return 2
            rep = inspect_human_structure(
                cfg,
                human_name=name or None,
                from_run_id=fr or None,
                branch=str(getattr(args, "run_branch", "site") or "site").strip().lower() or "site",
            )
            print_inspect_report(rep, title="=== launch inspect ===")
            if rep.problems and "Не удалось определить" in rep.problems[0]:
                return 2
            return 2 if not rep.legacy["runs_root"].is_dir() else 0

        if args.launch_cmd == "migrate-to-human-structure":
            if args.execute:
                print("migrate-to-human-structure: EXECUTE (копирование, без удаления legacy).")
            else:
                print("migrate-to-human-structure: DRY-RUN")
            out = migrate_to_human_structure(
                cfg,
                from_run_id=str(args.from_run_id).strip(),
                launch_name=str(getattr(args, "name", "") or "").strip() or None,
                branch=str(getattr(args, "run_branch", "site") or "site").strip().lower() or "site",
                execute=bool(args.execute),
                verbose=bool(getattr(args, "verbose", False)),
                log=print,
            )
            diag = out.get("dry_run_diagnostics")
            legacy = out.get("legacy")
            planned = out.get("planned_launch_path")
            if legacy and planned and diag is not None:
                print_migrate_dry_run_report(
                    legacy=legacy,
                    launch=Path(planned),
                    desired_clean=str(out.get("desired_clean", "")),
                    mkdir_total=int(out.get("mkdir_actions_total") or 0),
                    diag=diag,
                    verbose=bool(getattr(args, "verbose", False)),
                )
            if not out.get("ok"):
                print(out.get("message", "failed"))
                return 2
            if out.get("dry_run"):
                pass
            else:
                print(f"launch_path: {out.get('launch_path')}")
                print(f"migration_csv: {out.get('migration_csv')}")
            return 0

        if args.launch_cmd == "cleanup-plan":
            r = cleanup_plan(cfg, human_name=str(args.name).strip())
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "resume-plan":
            r = resume_plan(cfg, human_name=str(args.name).strip())
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "pick-site-launch":
            from orchestrator.human_launch_pick import run_pick_site_launch_cli

            return run_pick_site_launch_cli(cfg, out_file=getattr(args, "out", None))

        if args.launch_cmd == "resume":
            if bool(getattr(args, "execute", False)):
                r = resume_launch_execute(cfg, human_name=str(args.name).strip())
                return 0 if r.get("ok") else 2
            print_resume_contract()
            return 0

        if args.launch_cmd == "start-site":
            launch_name = _effective_launch_name(str(args.name).strip(), smoke=False)
            print(f"Имя запуска: {launch_name}")
            r = start_site_launch(
                cfg,
                name=launch_name,
                stories_dir=args.stories_dir,
                limit=int(getattr(args, "limit", 0) or 0),
                execute=bool(args.execute),
                output_conflict_policy=str(getattr(args, "output_conflict_policy", "fail") or "fail"),
                use_input_snapshot=bool(getattr(args, "input_snapshot", False)),
            )
            if r.get("dry_run"):
                print(f"start-site DRY-RUN -> {r.get('launch_path')} stories={r.get('story_count')}")
                for line in (r.get("plan_actions_sample") or [])[:25]:
                    print(f"  {line}")
                if int(r.get("plan_actions_total") or 0) > 25:
                    print(f"  ... total actions: {r.get('plan_actions_total')}")
            else:
                print(f"start-site OK -> {r.get('launch_path')} stories={r.get('story_count')}")
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "full-site-cycle":
            launch_name = _effective_launch_name(str(args.name).strip(), smoke=False)
            print(f"Имя запуска: {launch_name}")
            if not bool(getattr(args, "execute", False)):
                full_site_cycle_plan(
                    cfg,
                    name=launch_name,
                    stories_dir=args.stories_dir,
                    limit=int(getattr(args, "limit", 0) or 0),
                    invoke_legacy_phase_a=bool(getattr(args, "invoke_legacy_phase_a", False)),
                    execute=False,
                )
                return 0
            if bool(getattr(args, "invoke_legacy_phase_a", False)):
                r = full_site_cycle_execute(
                    cfg,
                    name=launch_name,
                    stories_dir=args.stories_dir,
                    limit=int(getattr(args, "limit", 0) or 0),
                    invoke_legacy_phase_a=True,
                    max_runtime_minutes=float(getattr(args, "max_runtime_minutes", 0) or 0),
                    gemini_registry_path=getattr(args, "gemini_registry", None),
                    output_conflict_policy=str(getattr(args, "output_conflict_policy", "fail") or "fail"),
                )
                return 0 if r.get("ok") and int(r.get("phase_a_exit", 0) or 0) == 0 else 2
            r = start_site_launch(
                cfg,
                name=launch_name,
                stories_dir=args.stories_dir,
                limit=int(getattr(args, "limit", 0) or 0),
                execute=True,
                output_conflict_policy=str(getattr(args, "output_conflict_policy", "fail") or "fail"),
            )
            print("full-site-cycle: только start-site (--invoke-legacy-phase-a не задан).")
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "smoke-site-cycle":
            launch_name = _effective_launch_name(str(args.name).strip(), smoke=True)
            print(f"Имя запуска: {launch_name}")
            lim = int(getattr(args, "limit", 2) or 2)
            mrm = float(getattr(args, "max_runtime_minutes", 15) or 15)
            if not bool(getattr(args, "execute", False)):
                smoke_site_cycle_plan(
                    cfg,
                    name=launch_name,
                    stories_dir=args.stories_dir,
                    limit=lim,
                    max_runtime_minutes=mrm,
                )
                return 0
            r = smoke_site_cycle_execute(
                cfg,
                name=launch_name,
                stories_dir=args.stories_dir,
                limit=lim,
                max_runtime_minutes=mrm,
                gemini_registry_path=getattr(args, "gemini_registry", None),
                output_conflict_policy=str(getattr(args, "output_conflict_policy", "test-suffix") or "test-suffix"),
            )
            return 0 if r.get("ok") and int(r.get("phase_a_exit", 0) or 0) == 0 else 2

        if args.launch_cmd == "run-site-flow":
            launch_name = _effective_launch_name(str(args.name).strip(), smoke=False)
            print(f"Имя запуска: {launch_name}")
            r = run_site_flow_execute(
                cfg,
                name=launch_name,
                stories_dir=args.stories_dir,
                limit=int(getattr(args, "limit", 1) or 0),
                execute=bool(getattr(args, "execute", False)),
                site_run_id=str(getattr(args, "site_run_id", "") or "").strip() or None,
                bat_profile=str(getattr(args, "bat_profile", "kokoro-drive") or "kokoro-drive"),
                gemini_workers=int(getattr(args, "gemini_workers", 5) or 5),
                gemini_registry_path=getattr(args, "gemini_registry", Path("configs/gemini_bots_registry.example.yaml")),
                phase_b_allow_scaffold=getattr(args, "phase_b_allow_scaffold", None),
                phase_b_branch=getattr(args, "phase_b_branch", None),
                output_conflict_policy=str(getattr(args, "output_conflict_policy", "skip-existing") or "skip-existing"),
                max_runtime_minutes=float(getattr(args, "max_runtime_minutes", 0.0) or 0.0),
                use_input_snapshot=bool(getattr(args, "input_snapshot", False)),
            )
            if r.get("dry_run"):
                return 0 if r.get("ok") else 2
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "gemini-preflight":
            out = run_gemini_preflight_for_human_launch(
                cfg,
                human_name=str(args.name).strip(),
                stories_dir=getattr(args, "stories_dir", None),
                limit=int(getattr(args, "limit", 2) or 0),
                gemini_registry_path=getattr(args, "gemini_registry", Path("configs/gemini_bots_registry.example.yaml")),
            )
            if not out.get("ok") and out.get("message"):
                print(out.get("message"))
                return 2
            print("=== gemini-preflight ===")
            for k in (
                "ok",
                "reasons",
                "notes",
                "intake_txt_count",
                "profiles_ready",
                "profiles_checked",
                "target_active_workers",
                "registry_bots",
                "registry_path",
            ):
                if k in out:
                    print(f"{k}: {out.get(k)}")
            return 0 if out.get("ok") else 2

        if args.launch_cmd == "final-report":
            r = generate_final_report_launch(
                cfg,
                human_name=str(args.name).strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "verify-runtime":
            r = verify_runtime_launch(cfg, human_name=str(args.name).strip())
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "sync-legacy":
            from orchestrator.human_launch_layout import human_zapuski_root

            launch = (human_zapuski_root(cfg.root_dir) / str(args.name).strip()).resolve()
            if bool(getattr(args, "execute", False)):
                r = mirror_legacy_pipeline_to_human(cfg, launch, execute=True)
                print(f"sync-legacy execute: copied={r.get('copied', 0)} tts_synced={r.get('tts_synced', 0)} publish_synced={r.get('publish_synced', 0)}")
            else:
                r = plan_mirror_legacy_pipeline_to_human(cfg, launch)
                print(f"sync-legacy dry-run: actions={len(r.get('actions', []))}")
                for row in (r.get("actions") or [])[:20]:
                    print(f"  {row.get('story_id')}: {row.get('from')} -> {row.get('to')}")
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "sync-progress":
            from orchestrator.human_launch_layout import human_zapuski_root

            launch = (human_zapuski_root(cfg.root_dir) / str(args.name).strip()).resolve()
            r = mirror_phase_a_progress_to_human(cfg, launch, execute=True)
            print(
                "sync-progress: "
                f"copied={r.get('copied', 0)} copied_logs={r.get('copied_logs', 0)} "
                f"errors={len(r.get('sync_errors') or [])}"
            )
            print(f"report={launch / '06_Отчёты' / 'incremental_progress_sync.json'}")
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "path-audit":
            from orchestrator.human_launch_path_audit import write_launch_path_audit_reports

            r = write_launch_path_audit_reports(cfg, launch_name=str(args.name).strip())
            print(f"path-audit json={r.get('json_path')}")
            print(f"path-audit csv={r.get('csv_path')}")
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "quarantine-old-artifacts":
            from orchestrator.human_launch_quarantine import quarantine_old_artifacts

            raw_ex = getattr(args, "exclude", None) or []
            ex: set[str] = {str(x).strip() for x in raw_ex if str(x).strip()}
            ex.add("RECOVERY_site-drive-run-a")
            r = quarantine_old_artifacts(cfg, execute=bool(args.execute), exclude_launch_names=frozenset(ex))
            print(f"quarantine candidates={r.get('candidates_count')} execute={r.get('execute')}")
            print(f"quarantine manifest_json={r.get('json_path')}")
            return 0

        if args.launch_cmd == "archive":
            r = archive_launch(cfg, human_name=str(args.name).strip(), execute=bool(args.execute))
            return 0 if r.get("ok") else 2

        if args.launch_cmd == "delete":
            if bool(args.execute):
                print("delete: EXECUTE — будет запись в История_запусков/ и удаление папки запуска.")
            else:
                print("delete: DRY-RUN (ничего не удаляется).")
            r = delete_launch(cfg, human_name=str(args.name).strip(), execute=bool(args.execute))
            return 0 if r.get("ok") else 2

        return 1

    if args.command == "phase-a":
        rid = str(getattr(args, "run_id", "") or "").strip()
        if rid:
            args.story_id = rid
        if bool(getattr(args, "inspect_human_structure", False)):
            from orchestrator.human_launch_migrate import inspect_human_structure, print_inspect_report

            run_key = rid or str(args.story_id).strip()
            if not run_key:
                print("Нужен --run-id или --story-id для inspect-human-structure.")
                return 2
            rep = inspect_human_structure(
                cfg,
                human_name=None,
                from_run_id=run_key,
                branch=str(args.run_branch).strip().lower() or "site",
            )
            print_inspect_report(rep, title="=== phase-a inspect-human-structure ===")
            return 2 if not rep.legacy["runs_root"].is_dir() else 0
        if bool(getattr(args, "gemini_progress", False)):
            from orchestrator.phase_a_ops import print_phase_a_gemini_progress

            print_phase_a_gemini_progress(
                cfg,
                run_id=str(args.story_id).strip(),
                branch=str(args.run_branch).strip().lower() or "site",
            )
            return 0
        if bool(getattr(args, "repair_stale_locks", False)):
            from orchestrator.phase_a_ops import repair_gemini_stale_locks

            n, lines = repair_gemini_stale_locks(
                cfg,
                run_id=str(args.story_id).strip(),
                branch=str(args.run_branch).strip().lower() or "site",
                older_than_minutes=int(getattr(args, "older_than_minutes", 60) or 60),
                execute=bool(getattr(args, "repair_locks_execute", False)),
            )
            for ln in lines:
                print(ln)
            print(f"repair_stale_locks touched={n} execute={bool(getattr(args, 'repair_locks_execute', False))}")
            return 0
        if not args.execute:
            print("Running PHASE A in DRY-RUN for length filter move step.")
        print("Текущие режимы:")
        for k in DEFAULT_MODES:
            print(f"- {k}: {modes.get(k, DEFAULT_MODES[k])}")
        if args.stories_dir is None:
            print("Для запуска phase A укажите --stories-dir (каталог с .txt).")
            return 2
        extensions = (
            [x.strip() for x in args.extensions.split(",") if x.strip()]
            if args.extensions
            else cfg.pre_filter_extensions
        )
        result = run_phase_a(
            config=cfg,
            options=PhaseAOptions(
                stories_dir=args.stories_dir,
                short_dir=args.short_dir,
                execute=args.execute,
                story_id=args.story_id,
                words_per_minute=args.words_per_minute or cfg.pre_filter_words_per_minute,
                min_minutes=args.min_minutes or cfg.pre_filter_min_minutes,
                min_words=args.min_words or cfg.pre_filter_min_words,
                extensions=extensions,
                gemini_workers=max(1, min(5, int(args.gemini_workers))),
                max_stories=max(0, int(args.max_stories)),
                gemini_registry_path=args.gemini_registry,
                gemini_stage_key=str(args.gemini_stage_key).strip() or "general_selection",
                gemini_info_stage_key=str(args.gemini_info_stage_key).strip() or "site_info_builder",
                run_branch=str(args.run_branch).strip().lower() or "site",
                resume=bool(args.resume),
                visual_mode=(
                    (str(args.visual_mode).strip().lower())
                    or (
                        modes.get("site_visual", DEFAULT_MODES["site_visual"])
                        if (str(args.run_branch).strip().lower() or "site") == "site"
                        else "manual"
                    )
                ),
                visual_pod_url=str(args.visual_pod_url).strip(),
                gemini_target_active_workers=max(1, min(5, int(args.gemini_target_active_workers))),
                gemini_profiles_total=max(1, min(5, int(args.gemini_profiles_total))),
                gemini_max_restarts_per_profile=max(1, int(args.gemini_max_restarts_per_profile)),
                gemini_profile_cooldown_seconds=float(args.gemini_profile_cooldown_seconds),
                gemini_supervised_workers=not bool(getattr(args, "gemini_legacy_parallel_all", False)),
                launch_dir=getattr(args, "launch_dir", None),
            ),
        )
        print(result.get("summary", result.get("message", "phase A completed")))
        if not result.get("ok", False):
            return 2
        return 0

    if args.command == "phase-b":
        print("Текущие режимы:")
        for k in DEFAULT_MODES:
            print(f"- {k}: {modes.get(k, DEFAULT_MODES[k])}")
        result = run_phase_b(
            config=cfg,
            options=PhaseBOptions(
                story_id=args.story_id,
                deferred_manifest=args.deferred_manifest,
                gemini_registry_path=args.gemini_registry,
                reports_subdir=args.reports_subdir,
                runtime_modes=modes,
                promo_intro_en=args.promo_intro_en,
                promo_mid_en=args.promo_mid_en,
                promo_outro_en=args.promo_outro_en,
                branch=str(args.branch).strip().lower() or "all",
                allow_scaffold=bool(args.allow_scaffold),
                launch_dir=getattr(args, "launch_dir", None),
            ),
        )
        print(result.get("summary", result.get("message", "phase B completed")))
        if not result.get("ok", False):
            return 2
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
