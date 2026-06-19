from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing
import os
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
from orchestrator.youtube_selection_bridge import (
    YoutubeRunSelectionBridgeOptions,
    run_youtube_run_selection_bridge,
)
from orchestrator.youtube_selection_batch import (
    YoutubeSelectionBatchFromSiteOptions,
    run_youtube_selection_batch_from_site,
)
from orchestrator.youtube_promo_bridge import (
    YoutubePromoRunOptions,
    YoutubePromoStatusOptions,
    run_youtube_promo_run,
    run_youtube_promo_status,
)
from orchestrator.youtube_language import (
    YoutubeSafeRegenerateOptions,
    YoutubeSafeStatusOptions,
    run_youtube_safe_regenerate,
    run_youtube_safe_status,
)
from orchestrator.youtube_safe_english_bridge import (
    YoutubeSafeEnglishRunOptions,
    run_youtube_safe_english_run,
)
from orchestrator.youtube_tts_kokoro_bridge import (
    DEFAULT_YOUTUBE_DRIVE_ROOT,
    YoutubeTtsKokoroColabExportOptions,
    YoutubeTtsKokoroColabImportOptions,
    YoutubeTtsKokoroColabVerifyOptions,
    run_youtube_tts_kokoro_colab_import,
    run_youtube_tts_kokoro_colab_export,
    run_youtube_tts_kokoro_colab_verify,
)
from orchestrator.youtube_tts_launch_jobs import (
    PrepareLaunchJobsOptions,
    TtsLaunchOptions,
    preflight_launch_jobs,
    prepare_launch_jobs,
    status_launch_jobs,
)
from orchestrator.youtube_tts_readiness_repair import RepairReadinessOptions, repair_tts_readiness
from orchestrator.youtube_tts_identity_audit import IdentityAuditOptions, run_identity_audit
from orchestrator.youtube_tts_voice_plan import VoicePlanOptions, print_voice_plan_terminal, run_voice_plan
from orchestrator.youtube_tts_promo_forensic_audit import PromoForensicAuditOptions, run_youtube_tts_promo_forensic_audit
from orchestrator.youtube_tts_launch_wait_import import (
    ImportFromDriveOptions,
    LaunchWaitImportOptions,
    print_final_summary,
    print_import_summary,
    run_import_from_drive,
    run_launch_wait_import,
)
from orchestrator.youtube_video_segments import (
    YoutubeVideoPrepareSegmentsOptions,
    YoutubeVideoRenderSegmentOptions,
    YoutubeVideoSegmentStatusOptions,
    run_youtube_video_prepare_segments,
    run_youtube_video_render_segment,
    run_youtube_video_segment_status,
)
from orchestrator.youtube_colab_supervisor import (
    YoutubeVideoColabSupervisorOptions,
    run_youtube_video_colab_supervisor,
)
from orchestrator.youtube_video_drive import (
    YoutubeVideoAssembleFinalOptions,
    YoutubeVideoDispatchSegmentsOptions,
    YoutubeVideoDriveStatusOptions,
    YoutubeVideoExportJobOptions,
    YoutubeVideoFullDriveFlowOptions,
    YoutubeVideoInspectSegmentOptions,
    YoutubeVideoImportResultsOptions,
    YoutubeVideoQueueStatusOptions,
    YoutubeVideoCleanupPartialOptions,
    YoutubeVideoColabBrowserProfilesOptions,
    YoutubeVideoReclaimStaleSegmentsOptions,
    YoutubeVideoSetupColabWorkersOptions,
    YoutubeVideoValidateJobAssetsOptions,
    YoutubeVideoWatchQueueOptions,
    YoutubeVideoWorkersAuditOptions,
    run_youtube_video_assemble_final,
    run_youtube_video_cleanup_partial_checkpoints,
    run_youtube_video_colab_browser_profiles,
    run_youtube_video_dispatch_segments,
    run_youtube_video_drive_status,
    run_youtube_video_export_job,
    run_youtube_video_full_drive_flow,
    run_youtube_video_inspect_segment,
    run_youtube_video_import_results,
    run_youtube_video_queue_status,
    run_youtube_video_reclaim_stale_segments,
    run_youtube_video_setup_colab_workers,
    run_youtube_video_validate_job_assets,
    run_youtube_video_watch_queue,
    run_youtube_video_workers_audit,
)
from orchestrator.youtube_visuals_bridge import (
    YoutubeCharactersBridgeOptions,
    YoutubeCharactersExportOptions,
    YoutubeCharactersImportOptions,
    YoutubeDirectorPromptsBridgeOptions,
    YoutubeDirectorPromptsExportOptions,
    YoutubeDirectorPromptsImportOptions,
    YoutubeFramesRunpodBridgeOptions,
    run_youtube_characters_bridge,
    run_youtube_characters_export,
    run_youtube_characters_import,
    run_youtube_director_prompts_bridge,
    run_youtube_director_prompts_export,
    run_youtube_director_prompts_import,
    run_youtube_frames_runpod_bridge,
)
from orchestrator.youtube_visuals_runner import (
    YoutubeGeminiPreflightAccountsOptions,
    YoutubeGeminiWorkersOptions,
    YoutubePromptsProgressStatusOptions,
    YoutubePromptsResumeAuditOptions,
    YoutubeStageSetOptions,
    YoutubeVisualsRunAllOptions,
    YoutubeVisualsRunOptions,
    YoutubeVisualsStatusOptions,
    mark_story_excluded_from_video,
    run_youtube_gemini_workers_setup,
    run_youtube_gemini_preflight_accounts,
    run_youtube_gemini_workers_status,
    run_youtube_prompts_progress_status,
    run_youtube_prompts_resume_audit,
    run_youtube_visuals_launch_status,
    run_youtube_visuals_run,
    run_youtube_visuals_run_all,
    run_youtube_visuals_status,
    set_launch_stage,
)
from orchestrator.youtube_prompts_temp_import_repair import (
    YoutubePromptsTempImportRepairOptions,
    run_youtube_prompts_temp_import_repair,
)
from orchestrator.youtube_prompts_targeted_repair import (
    YoutubePromptsTargetedRepairOptions,
    run_youtube_prompts_targeted_repair,
)
from orchestrator.youtube_visual_prompts_audit import (
    YoutubeVisualPromptsAuditOptions,
    run_youtube_visual_prompts_audit,
)
from orchestrator.youtube_frames_reset import (
    YoutubeFramesResetOptions,
    run_youtube_frames_reset,
)
from orchestrator.youtube_visuals_clean import (
    YoutubeVisualsCleanOptions,
    run_youtube_visuals_clean,
)
from orchestrator.youtube_characters_anchor_audit import (
    YoutubeCharactersAnchorAuditOptions,
    run_youtube_characters_anchor_audit,
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

    site = sub.add_parser("site", help="Site production diagnostics and safe orchestration helpers")
    site_sub = site.add_subparsers(dest="site_cmd", required=True)
    site_intake = site_sub.add_parser(
        "intake",
        help="Create a new SITE_FULL launch with sampled input stories only.",
    )
    site_intake.add_argument("--source-dir", type=Path, required=True, help="Library root with top-level folders")
    site_intake.add_argument("--per-folder", type=int, required=True, help="How many .txt stories to copy from each top-level folder")
    site_intake.add_argument("--seed", default="", help="Optional deterministic sampler seed")
    site_intake.add_argument("--execute", action="store_true", help="Create launch folder and copy sampled .txt files")
    site_process = site_sub.add_parser(
        "process-launch",
        help="Run existing launch run-site-flow for an intake launch, then post diagnostics.",
    )
    site_process.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_process.add_argument("--execute", action="store_true", help="Запустить run-site-flow и post-diagnostics")
    site_gemini_preflight = site_sub.add_parser(
        "gemini-preflight",
        help="Safe Gemini/phase_a preflight for a SITE_FULL launch.",
    )
    site_gemini_preflight.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_gemini_preflight.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
        help="YAML registry with Gemini accounts/stage URLs",
    )
    site_gemini_preflight.add_argument("--stage-key", default="general_selection")
    site_gemini_preflight.add_argument("--info-stage-key", default="site_info_builder")
    site_gemini_preflight.add_argument("--profiles-total", type=int, default=5)
    site_gemini_preflight.add_argument("--target-active-workers", type=int, default=3)
    site_ready = site_sub.add_parser(
        "readiness",
        help="Read-only readiness summary for Запуски/<launch>; with --execute writes readiness reports.",
    )
    site_ready.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_ready.add_argument("--execute", action="store_true", help="Записать reports в 10_Отчёты")
    site_sync = site_sub.add_parser(
        "sync-artifacts",
        help="Dry-run artifact index; with --execute safely mirrors missing site artifacts into launch folder.",
    )
    site_sync.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_sync.add_argument("--execute", action="store_true", help="Копировать только недостающие artifacts и записать reports")
    site_pub_state = site_sub.add_parser(
        "sync-published-state",
        help="Dry-run/execute local published marker sync without publishing.",
    )
    site_pub_state.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_pub_state.add_argument("--execute", action="store_true", help="Создать published_marker.json для inferred published stories")
    site_pending = site_sub.add_parser(
        "pending-report",
        help="Dry-run pending selected stories report; with --execute writes resume instructions.",
    )
    site_pending.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_pending.add_argument("--execute", action="store_true", help="Записать pending reports в 10_Отчёты")
    site_publish_ready = site_sub.add_parser(
        "publish-ready",
        help="Dry-run/execute publishing only ready_unpublished site stories.",
    )
    site_publish_ready.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_publish_ready.add_argument("--execute", action="store_true", help="Опубликовать только ready_unpublished stories")
    site_watch = site_sub.add_parser(
        "readiness-watch",
        help="Watch launch readiness and ask before publish-ready.",
    )
    site_watch.add_argument("--launch-name", required=True, help="Имя папки Запуски/<launch>")
    site_watch.add_argument("--threshold-percent", type=float, default=90.0)
    site_watch.add_argument("--check-interval-minutes", type=int, default=180)
    site_watch.add_argument("--max-wait-hours", type=float, default=0.0)
    site_watch.add_argument("--execute", action="store_true", help="Включить execute-подкоманды и интерактивные действия")

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
    yt_pref.add_argument("--min-minutes", type=int, default=None)
    yt_pref.add_argument("--max-minutes", type=int, default=None)
    yt_pref.add_argument("--words-per-minute", type=int, default=None)
    yt_pref.add_argument("--min-words", type=int, default=None, help="Override derived min_words.")
    yt_pref.add_argument("--max-words", type=int, default=None, help="Override derived max_words.")
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
    yt_auto_prepare.add_argument("--min-minutes", type=int, default=None)
    yt_auto_prepare.add_argument("--max-minutes", type=int, default=None)
    yt_auto_prepare.add_argument("--words-per-minute", type=int, default=None)
    yt_auto_prepare.add_argument("--min-words", type=int, default=None, help="Override derived min_words.")
    yt_auto_prepare.add_argument("--max-words", type=int, default=None, help="Override derived max_words.")
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

    yt_safe_status = yt_sub.add_parser(
        "safe-status",
        help="Diagnose English language contract for YouTube safe/promo/TTS artifacts.",
    )
    yt_safe_status.add_argument("--story-id", required=True, help="story_id или canonical_basename/output folder.")

    yt_safe_regen = yt_sub.add_parser(
        "safe-regenerate",
        help="Regenerate English safe story when an English-safe legacy adapter exists (dry-run by default).",
    )
    yt_safe_regen.add_argument("--story-id", required=True, help="story_id или canonical_basename/output folder.")
    yt_safe_regen.add_argument("--execute", action="store_true", help="Launch English-safe Gemini adapter and overwrite safe_story only after validation.")

    yt_safe_en = yt_sub.add_parser(
        "safe-english-run",
        help="Run isolated English YouTube-safe rewrite adapter (dry-run by default).",
    )
    yt_safe_en.add_argument("--story-id", required=True, help="story_id или canonical_basename/output folder.")
    yt_safe_en.add_argument("--execute", action="store_true", help="Launch Gemini and write English 02_safe_story/safe_story.txt.")
    yt_safe_en.add_argument("--force", action="store_true", help="Regenerate even if English safe_story already exists.")
    yt_safe_en.add_argument("--account-index", type=int, default=0, help="Индекс аккаунта из gemini registry для youtube_safe_text (0-based).")
    yt_safe_en.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
        help="YAML registry с email и youtube_safe_text URL.",
    )
    yt_safe_en.add_argument(
        "--reuse-legacy-user-data",
        action="store_true",
        help="Use legacy/youtube_tts/user_data instead of isolated 02_safe_story/_english_adapter/user_data.",
    )

    yt_rsel = yt_sub.add_parser(
        "run-selection-bridge",
        help=(
            "Single-input: тонкий facade вокруг legacy/youtube_tts/gemini_auto.py для бота youtube_selection. "
            "Default — preflight; реальный subprocess только с --execute."
        ),
    )
    yt_rsel.add_argument("--youtube-run-id", required=True)
    yt_rsel_target = yt_rsel.add_mutually_exclusive_group(required=True)
    yt_rsel_target.add_argument(
        "--input-id",
        default="",
        help="item_id из _gemini_selection/input/gemini_selection_input_manifest.json (например yt_00001).",
    )
    yt_rsel_target.add_argument(
        "--story-id",
        default="",
        help="canonical_basename или stem source_path (например 'Holiday Dream').",
    )
    yt_rsel.add_argument(
        "--execute",
        action="store_true",
        help="Запустить legacy gemini_auto.py (Playwright/Chrome). Без флага — preflight + manual_cmd.",
    )
    yt_rsel.add_argument(
        "--force",
        action="store_true",
        help="Перезаписать staging input txt если уже есть.",
    )
    yt_rsel.add_argument(
        "--reuse-legacy-user-data",
        action="store_true",
        help="GEMINI_USER_DATA_DIR=legacy/youtube_tts/user_data (уже залогиненный профиль).",
    )
    yt_rsel.add_argument(
        "--account-index",
        type=int,
        default=0,
        help="Индекс валидного аккаунта в registry с ключом youtube_selection (0-based).",
    )
    yt_rsel.add_argument(
        "--user-data-dir",
        default="",
        help=(
            "Явный путь к Chrome user_data. Без него: авто-подбор legacy/youtube_selection/user_data_N по email "
            "(account_info[*].email) — если нет совпадения, изолированный профиль bridge."
        ),
    )

    yt_batch = yt_sub.add_parser(
        "selection-batch-from-site",
        help="Managed batch queue for youtube_selection: dry-run by default, --execute runs single-story attempts.",
    )
    yt_batch.add_argument("--site-run-id", required=True)
    yt_batch.add_argument("--youtube-run-id", required=True)
    yt_batch.add_argument("--min-minutes", type=int, default=None)
    yt_batch.add_argument("--max-minutes", type=int, default=None)
    yt_batch.add_argument("--words-per-minute", type=int, default=None)
    yt_batch.add_argument("--min-words", type=int, default=None)
    yt_batch.add_argument("--max-words", type=int, default=None)
    yt_batch.add_argument("--max-attempts", type=int, required=True)
    yt_batch.add_argument("--target-yes", type=int, default=1)
    yt_batch.add_argument("--workers", type=int, default=1)
    yt_batch.add_argument("--account-start-index", type=int, default=0)
    yt_batch.add_argument("--retry-failed", action="store_true")
    yt_batch.add_argument("--seed", type=int, default=None)
    yt_batch.add_argument("--execute", action="store_true")

    yt_promo_run = yt_sub.add_parser(
        "promo-run",
        help="Run legacy YouTube promo insertion for one story (dry-run by default).",
    )
    yt_promo_run.add_argument("--story-id", required=True, help="story_id или canonical_basename/output folder.")
    yt_promo_run.add_argument("--execute", action="store_true", help="Launch legacy Gemini promo inserter and write 03_promo outputs.")
    yt_promo_run.add_argument("--force", action="store_true", help="Re-run even if text_ready_for_audio already has promo inserts.")
    yt_promo_run.add_argument("--account-index", type=int, default=0, help="Индекс аккаунта из gemini registry для youtube_ad_point (0-based).")
    yt_promo_run.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.example.yaml"),
        help="YAML registry с email и youtube_ad_point URL.",
    )
    yt_promo_run.add_argument(
        "--reuse-legacy-user-data",
        action="store_true",
        help="Use legacy/youtube_tts/user_data instead of isolated 03_promo/_legacy_staging/user_data_fresh.",
    )

    yt_promo_status = yt_sub.add_parser(
        "promo-status",
        help="Diagnose YouTube promo insertion and audio staleness for one story.",
    )
    yt_promo_status.add_argument("--story-id", required=True, help="story_id или canonical_basename/output folder.")

    yt_tts = yt_sub.add_parser(
        "tts-kokoro-colab",
        help="YouTube Kokoro Colab Drive bridge (isolated ContentFactory_YouTube; dry-run by default).",
    )
    yt_tts_sub = yt_tts.add_subparsers(dest="youtube_tts_kokoro_cmd", required=True)
    yt_tts_exp = yt_tts_sub.add_parser(
        "export",
        help="Single-story export-only Drive job for YouTube Kokoro Colab (does not run Colab/TTS).",
    )
    yt_tts_exp.add_argument("--youtube-run-id", required=True)
    yt_tts_exp.add_argument("--story-id", required=True, help="story_id или canonical_basename из youtube_story_manifest.")
    yt_tts_exp.add_argument(
        "--drive-root",
        type=Path,
        default=DEFAULT_YOUTUBE_DRIVE_ROOT,
        help="Isolated YouTube Drive root. Default: G:\\Мой диск\\ContentFactory_YouTube",
    )
    yt_tts_exp.add_argument("--execute", action="store_true", help="Создать Drive folders/job files и обновить manifest.")

    yt_tts_verify = yt_tts_sub.add_parser(
        "verify",
        help="Проверить expected Drive mp3 для YouTube Kokoro Colab без копирования.",
    )
    yt_tts_verify.add_argument("--youtube-run-id", required=True)
    yt_tts_verify.add_argument("--story-id", required=True, help="story_id или canonical_basename из youtube_story_manifest.")
    yt_tts_verify.add_argument(
        "--drive-root",
        type=Path,
        default=DEFAULT_YOUTUBE_DRIVE_ROOT,
        help="Isolated YouTube Drive root. Default: G:\\Мой диск\\ContentFactory_YouTube",
    )

    yt_tts_import = yt_tts_sub.add_parser(
        "import",
        help="Импортировать expected Drive mp3 в output/youtube/<story>/04_audio/narration.mp3.",
    )
    yt_tts_import.add_argument("--youtube-run-id", required=True)
    yt_tts_import.add_argument("--story-id", required=True, help="story_id или canonical_basename из youtube_story_manifest.")
    yt_tts_import.add_argument(
        "--drive-root",
        type=Path,
        default=DEFAULT_YOUTUBE_DRIVE_ROOT,
        help="Isolated YouTube Drive root. Default: G:\\Мой диск\\ContentFactory_YouTube",
    )
    yt_tts_import.add_argument("--force", action="store_true", help="Перезаписать existing narration.mp3.")

    yt_tts_launch = yt_sub.add_parser("tts", help="YouTube TTS production launch jobs for multi-worker Colab.")
    yt_tts_launch_sub = yt_tts_launch.add_subparsers(dest="youtube_tts_cmd", required=True)
    yt_tts_prepare = yt_tts_launch_sub.add_parser("prepare-launch-jobs", help="Create launch-scoped TTS job and worker partitions.")
    yt_tts_prepare.add_argument("--youtube-run-id", required=True)
    yt_tts_prepare.add_argument("--workers", type=int, default=5)
    yt_tts_prepare.add_argument("--retry-failed", action="store_true")
    yt_tts_prepare.add_argument("--force", action="store_true")
    yt_tts_prepare.add_argument("--dry-run", action="store_true")
    yt_tts_prepare.add_argument("--execute", action="store_true")
    yt_tts_preflight = yt_tts_launch_sub.add_parser("preflight", help="Fail hard if launch TTS job contract is incomplete.")
    yt_tts_preflight.add_argument("--youtube-run-id", required=True)
    yt_tts_preflight.add_argument("--workers", type=int, default=5)
    yt_tts_status = yt_tts_launch_sub.add_parser("status", help="Aggregate launch TTS job/worker status.")
    yt_tts_status.add_argument("--youtube-run-id", required=True)
    yt_tts_status.add_argument("--workers", type=int, default=5)
    yt_tts_repair = yt_tts_launch_sub.add_parser(
        "repair-readiness",
        help="Audit, repair TTS readiness, rebuild launch job/partitions, and run preflight.",
    )
    yt_tts_repair.add_argument("--youtube-run-id", required=True)
    yt_tts_repair.add_argument("--workers", type=int, default=5)
    yt_tts_repair.add_argument("--execute", action="store_true")
    yt_tts_identity = yt_tts_launch_sub.add_parser(
        "identity-audit",
        help="Audit already_done TTS identity/voice; optionally quarantine bad audio and rebuild job.",
    )
    yt_tts_identity.add_argument("--youtube-run-id", required=True)
    yt_tts_identity.add_argument("--workers", type=int, default=5)
    yt_tts_identity.add_argument("--execute", action="store_true")
    yt_tts_voice_plan = yt_tts_launch_sub.add_parser(
        "voice-plan",
        help="Build human-readable Colab TTS voice plan report (audit-only, no repair).",
    )
    yt_tts_voice_plan.add_argument("--youtube-run-id", required=True)
    yt_tts_voice_plan.add_argument("--workers", type=int, default=5)
    yt_tts_promo_forensic = yt_tts_launch_sub.add_parser(
        "promo-forensic-audit",
        help="Forensic audit of promo/TTS text inputs for a launch (read-only; writes reports).",
    )
    yt_tts_promo_forensic.add_argument("--youtube-run-id", required=True)
    yt_tts_promo_forensic.add_argument("--drive-root", type=Path, default=None)
    yt_tts_import_drive = yt_tts_launch_sub.add_parser(
        "import-from-drive",
        help="Import launch-scoped YouTube TTS mp3 files from Google Drive and optionally cleanup Drive temp files.",
    )
    yt_tts_import_drive.add_argument("--youtube-run-id", required=True)
    yt_tts_import_drive.add_argument("--drive-root", type=Path, default=DEFAULT_YOUTUBE_DRIVE_ROOT)
    yt_tts_import_drive.add_argument("--cleanup-drive-after-import", action="store_true")
    yt_tts_import_drive.add_argument("--execute", action="store_true")
    yt_tts_launch_wait_import = yt_tts_launch_sub.add_parser(
        "launch-wait-import",
        help="Run readiness, start Colab browser workers, wait for terminal TTS states, then import audio.",
    )
    yt_tts_launch_wait_import.add_argument("--youtube-run-id", required=True)
    yt_tts_launch_wait_import.add_argument("--workers", type=int, default=5)
    yt_tts_launch_wait_import.add_argument("--poll-minutes", type=float, default=30.0)
    yt_tts_launch_wait_import.add_argument("--max-hours", type=float, default=1000.0)
    yt_tts_launch_wait_import.add_argument("--execute", action="store_true")
    yt_tts_launch_wait_import.add_argument("--start-browser", dest="start_browser", action="store_true", default=True)
    yt_tts_launch_wait_import.add_argument("--no-start-browser", dest="start_browser", action="store_false")
    yt_tts_launch_wait_import.add_argument("--start-cmd", default=".\\START_YOUTUBE_TTS_YANDEX_5TABS_PROFILE_PROXY.bat")
    yt_tts_launch_wait_import.add_argument("--continue-next-stage", action="store_true")
    yt_tts_launch_wait_import.add_argument("--drive-root", type=Path, default=DEFAULT_YOUTUBE_DRIVE_ROOT)
    yt_tts_launch_wait_import.add_argument("--cleanup-drive-after-import", action="store_true")

    yt_chars = yt_sub.add_parser(
        "characters",
        help="YouTube visuals bridge: preflight/stage characters input for one story; does not launch Gemini.",
    )
    yt_chars.add_argument("--story-id", required=True)
    yt_chars.add_argument(
        "--execute",
        action="store_true",
        help="Create local legacy staging/report only. Gemini is not launched.",
    )

    yt_chars_export = yt_sub.add_parser(
        "characters-export",
        help="Export safe story text into 05_characters/_staging for manual/Gemini characters extraction.",
    )
    yt_chars_export.add_argument("--story-id", required=True)
    yt_chars_export.add_argument("--execute", action="store_true")

    yt_chars_import = yt_sub.add_parser(
        "characters-import",
        help="Import ready characters.txt into 05_characters and update story manifest.",
    )
    yt_chars_import.add_argument("--story-id", required=True)
    yt_chars_import.add_argument("--source", type=Path, required=True)
    yt_chars_import.add_argument("--execute", action="store_true")

    yt_dir_prompts = yt_sub.add_parser(
        "director-prompts",
        help="YouTube visuals bridge: preflight/stage director prompt input for one story; does not launch Gemini.",
    )
    yt_dir_prompts.add_argument("--story-id", required=True)
    yt_dir_prompts.add_argument(
        "--execute",
        action="store_true",
        help="Create local legacy staging/report only. Gemini is not launched.",
    )

    yt_dir_prompts_export = yt_sub.add_parser(
        "director-prompts-export",
        help="Export story/audio/characters into 06_prompts/_staging for manual/Gemini prompt creation.",
    )
    yt_dir_prompts_export.add_argument("--story-id", required=True)
    yt_dir_prompts_export.add_argument("--execute", action="store_true")

    yt_dir_prompts_import = yt_sub.add_parser(
        "director-prompts-import",
        help="Import ready prompts_list.txt into 06_prompts and update story manifest.",
    )
    yt_dir_prompts_import.add_argument("--story-id", required=True)
    yt_dir_prompts_import.add_argument("--source", type=Path, required=True)
    yt_dir_prompts_import.add_argument("--execute", action="store_true")

    yt_frames_runpod = yt_sub.add_parser(
        "frames-runpod",
        help="YouTube visuals bridge: frame generation preflight/job manifest; calls RunPod only with --execute.",
    )
    yt_frames_runpod.add_argument("--story-id", required=True)
    yt_frames_runpod.add_argument("--runpod-url", default="", help="RunPod/ComfyUI API URL. Required only with --execute.")
    yt_frames_runpod.add_argument("--workflow", default="", help="ComfyUI workflow preset file/name from legacy/director_2_0/workflows.")
    yt_frames_runpod.add_argument(
        "--prepare-only",
        action="store_true",
        help="Write 07_frames/frame_jobs.json and report without calling RunPod.",
    )
    yt_frames_runpod.add_argument(
        "--execute",
        action="store_true",
        help="Call RunPod/ComfyUI and generate missing frames.",
    )

    yt_visuals_run = yt_sub.add_parser(
        "visuals-run",
        help="Run single-story YouTube visuals state machine through characters/prompts/frames/segment prep.",
    )
    yt_visuals_run.add_argument("--story-id", required=True)
    yt_visuals_run.add_argument("--youtube-run-id", default="")
    yt_visuals_run.add_argument("--runpod-url", default="", help="Optional. If omitted, visuals-run asks for it at READY_FOR_RUNPOD after Gemini prompts are ready.")
    yt_visuals_run.add_argument("--workflow", default="", help="ComfyUI workflow preset file/name used only at the frames stage.")
    yt_visuals_run.add_argument("--segment-sec", type=float, default=180.0)
    yt_visuals_run.add_argument("--execute", action="store_true")
    yt_visuals_run.add_argument("--watch", action="store_true")
    yt_visuals_run.add_argument("--auto-gemini", action="store_true", help="Launch legacy director_2_0 Gemini automation for characters/prompts.")
    yt_visuals_run.add_argument("--allow-gemini", action="store_true", help="Alias for --auto-gemini.")
    yt_visuals_run.add_argument("--fresh-visuals", action="store_true", help="Quarantine existing visual artifacts before regenerating characters/prompts.")
    yt_visuals_run.add_argument("--no-runpod-prompt", action="store_true", help="Do not ask for RunPod URL at READY_FOR_RUNPOD; stop after preparing frame jobs.")
    yt_visuals_run.add_argument("--accept-known-promo-issues", action="store_true")
    yt_visuals_run.add_argument("--watch-interval-sec", type=int, default=5)
    yt_visuals_run.add_argument("--watch-timeout-sec", type=int, default=0)

    yt_visuals_run_all = yt_sub.add_parser(
        "visuals-run-all",
        help="Run launch-scoped visuals state machine for all audio-ready stories.",
    )
    yt_visuals_run_all.add_argument("--youtube-run-id", required=True)
    yt_visuals_run_all.add_argument("--story-id", default="")
    yt_visuals_run_all.add_argument("--runpod-url", default="")
    yt_visuals_run_all.add_argument("--workflow", default="")
    yt_visuals_run_all.add_argument("--workers", type=int, default=3)
    yt_visuals_run_all.add_argument("--limit", type=int, default=0)
    yt_visuals_run_all.add_argument("--execute", action="store_true")
    yt_visuals_run_all.add_argument("--dry-run", action="store_true")
    yt_visuals_run_all.add_argument("--auto-gemini", action="store_true")
    yt_visuals_run_all.add_argument("--allow-gemini", action="store_true")
    yt_visuals_run_all.add_argument("--accept-known-promo-issues", action="store_true")
    yt_visuals_run_all.add_argument("--segment-sec", type=float, default=180.0)
    yt_visuals_run_all.add_argument("--no-runpod-prompt", action="store_true")
    yt_visuals_run_all.add_argument("--prompts-only", action="store_true", help="Stop after prompts/director readiness; do not prepare frames or RunPod jobs.")

    yt_gemini_workers_status = yt_sub.add_parser(
        "gemini-workers-status",
        help="Show Gemini prompt worker profile/account/bot readiness.",
    )
    yt_gemini_workers_status.add_argument("--workers", type=int, default=3)

    yt_gemini_workers_setup = yt_sub.add_parser(
        "gemini-workers-setup",
        help="Create Gemini prompt worker profile dirs and mapping files from registry.",
    )
    yt_gemini_workers_setup.add_argument("--workers", type=int, default=3)
    yt_gemini_workers_setup.add_argument("--execute", action="store_true")

    gem = sub.add_parser("gemini", help="Gemini runtime utilities.")
    gem_sub = gem.add_subparsers(dest="gemini_cmd", required=True)
    gem_preflight = gem_sub.add_parser("preflight-accounts", help="Controlled Gemini browser preflight without generation.")
    gem_preflight.add_argument("--stage", default="visuals")
    gem_preflight.add_argument("--youtube-run-id", default="")
    gem_preflight.add_argument("--accounts", default="0,1,2")
    gem_preflight.add_argument("--execute", action="store_true")

    yt_visuals_status = yt_sub.add_parser(
        "visuals-status",
        help="Show single-story YouTube visuals state and current blocker.",
    )
    yt_visuals_status.add_argument("--story-id", default="")
    yt_visuals_status.add_argument("--youtube-run-id", default="")
    yt_visuals_status.add_argument("--accept-known-promo-issues", action="store_true")

    yt_prompts_resume_audit = yt_sub.add_parser(
        "prompts-resume-audit",
        help="Read-only audit for YouTube visual prompts resume/checkpoint and RunPod readiness.",
    )
    yt_prompts_resume_audit.add_argument("--youtube-run-id", required=True)
    yt_prompts_resume_audit.add_argument("--accept-known-promo-issues", action="store_true")

    yt_prompts_temp_repair = yt_sub.add_parser(
        "prompts-temp-import-repair",
        help="Validate temp prompts session outputs and import valid files into canonical launch story folders.",
    )
    yt_prompts_temp_repair.add_argument("--youtube-run-id", required=True)
    yt_prompts_temp_repair.add_argument("--run-session-id", default="")
    yt_prompts_temp_repair.add_argument("--execute", action="store_true")

    yt_prompts_targeted_repair = yt_sub.add_parser(
        "prompts-targeted-repair",
        help="Forensic and targeted Gemini prompts rerun/repair for specific launch stories.",
    )
    yt_prompts_targeted_repair.add_argument("--youtube-run-id", required=True)
    yt_prompts_targeted_repair.add_argument("--story-id", action="append", required=True)
    yt_prompts_targeted_repair.add_argument("--workers", type=int, default=1)
    yt_prompts_targeted_repair.add_argument("--preferred-session-id", default="20260618_082047")
    yt_prompts_targeted_repair.add_argument("--accept-known-promo-issues", action="store_true")
    yt_prompts_targeted_repair.add_argument("--execute", action="store_true")

    yt_path_repair = yt_sub.add_parser(
        "production-path-repair",
        help="Forensic path leak audit, import valid legacy output/youtube artifacts into launch, recalc launch-only readiness.",
    )
    yt_path_repair.add_argument("--youtube-run-id", required=True)
    yt_path_repair.add_argument("--execute", action="store_true", help="Actually import legacy artifacts (default dry-run recovery).")

    yt_prompts_progress_status = yt_sub.add_parser(
        "prompts-progress-status",
        help="Show current launch-level Gemini prompts progress ledger reconciled with filesystem.",
    )
    yt_prompts_progress_status.add_argument("--youtube-run-id", required=True)
    yt_prompts_progress_status.add_argument("--run-session-id", default="")
    yt_prompts_progress_status.add_argument("--accept-known-promo-issues", action="store_true")

    yt_stage = yt_sub.add_parser("stage", help="Launch stage utilities.")
    yt_stage_sub = yt_stage.add_subparsers(dest="youtube_stage_cmd", required=True)
    yt_stage_set = yt_stage_sub.add_parser("set", help="Set launch current_stage in queue/stage_status.json.")
    yt_stage_set.add_argument("--youtube-run-id", required=True)
    yt_stage_set.add_argument("--stage", required=True)
    yt_stage_set.add_argument("--execute", action="store_true")

    yt_exclude_video = yt_sub.add_parser("exclude-from-video", help="Exclude one launch story from visuals/video/publish queues.")
    yt_exclude_video.add_argument("--youtube-run-id", required=True)
    yt_exclude_video.add_argument("--story-id", required=True)
    yt_exclude_video.add_argument("--reason", default="too_short_story_user_rejected")
    yt_exclude_video.add_argument("--execute", action="store_true")

    yt_visuals_clean = yt_sub.add_parser(
        "visuals-clean",
        help="Quarantine per-story YouTube visual artifacts before regenerating characters/prompts.",
    )
    yt_visuals_clean.add_argument("--story-id", required=True)
    yt_visuals_clean.add_argument("--execute", action="store_true")

    yt_visual_prompts_audit = yt_sub.add_parser(
        "visual-prompts-audit",
        help="Read-only diagnostics for YouTube visual prompt length/continuity risks.",
    )
    yt_visual_prompts_audit.add_argument("--story-id", required=True)

    yt_characters_anchor_audit = yt_sub.add_parser(
        "characters-anchor-audit",
        help="Read-only diagnostics for forbidden terms in YouTube character anchors.",
    )
    yt_characters_anchor_audit.add_argument("--story-id", required=True)

    yt_frames_reset = yt_sub.add_parser(
        "frames-reset",
        help="Archive current YouTube frames and mark them stale without permanent deletion.",
    )
    yt_frames_reset.add_argument("--story-id", required=True)
    yt_frames_reset.add_argument("--reason", required=True)
    yt_frames_reset.add_argument("--execute", action="store_true")

    yt_video = yt_sub.add_parser(
        "video",
        help="YouTube video segment MVP (local dry-run/execute only; no Colab dispatcher).",
    )
    yt_video_sub = yt_video.add_subparsers(dest="youtube_video_cmd", required=True)
    yt_video_prepare = yt_video_sub.add_parser(
        "prepare-segments",
        help="Create local video_timeline.json and segment_jobs.json for one story.",
    )
    yt_video_prepare.add_argument("--story-id", required=True)
    yt_video_prepare.add_argument("--segment-sec", type=float, default=180.0)
    yt_video_prepare.add_argument("--execute", action="store_true")
    yt_video_prepare.add_argument("--force", action="store_true")

    yt_video_render = yt_video_sub.add_parser(
        "render-segment",
        help="Render one prepared segment locally into 08_video/segments.",
    )
    yt_video_render.add_argument("--story-id", required=True)
    yt_video_render.add_argument("--segment-id", required=True)
    yt_video_render.add_argument("--execute", action="store_true")

    yt_video_status = yt_video_sub.add_parser(
        "segment-status",
        help="Show prepared segment validation status for one story.",
    )
    yt_video_status.add_argument("--story-id", required=True)

    yt_video_export = yt_video_sub.add_parser(
        "export-job",
        help="Export a YouTube video segment job to Google Drive.",
    )
    yt_video_export.add_argument("--story-id", required=True)
    yt_video_export.add_argument("--execute", action="store_true")
    yt_video_export.add_argument("--force", action="store_true")

    yt_video_drive_status = yt_video_sub.add_parser(
        "drive-status",
        help="Show Google Drive video job status for one story.",
    )
    yt_video_drive_status.add_argument("--story-id", required=True)

    yt_video_setup_colab = yt_video_sub.add_parser(
        "setup-colab-workers",
        help="Create root Colab compatibility paths and assigned queue folders.",
    )
    yt_video_setup_colab.add_argument("--story-id", required=True)
    yt_video_setup_colab.add_argument("--youtube-folder-id", default="", help="Google Drive folder id for ContentFactory_YouTube, used in generated Colab bootstrap cell")
    yt_video_setup_colab.add_argument("--execute", action="store_true")

    yt_video_browser_profiles = yt_video_sub.add_parser(
        "colab-browser-profiles",
        help="Read-only diagnostic: find Chrome/Yandex executables and profile directories for Colab workers.",
    )
    yt_video_browser_profiles.add_argument("--config-path", type=Path, default=Path("configs/youtube_video_colab_workers.yaml"))

    yt_video_workers_audit = yt_video_sub.add_parser(
        "workers-audit",
        help="Read-only diagnostic: compare Colab launcher workers with render queue workers and Drive assigned/status folders.",
    )
    yt_video_workers_audit.add_argument("--story-id", required=True)
    yt_video_workers_audit.add_argument("--config-path", type=Path, default=Path("configs/youtube_video_colab_workers.yaml"))

    yt_video_dispatch = yt_video_sub.add_parser(
        "dispatch-segments",
        help="Assign global pending video segments to worker-specific queues.",
    )
    yt_video_dispatch.add_argument("--story-id", required=True)
    yt_video_dispatch.add_argument("--workers", default="")
    yt_video_dispatch.add_argument("--target-per-worker", type=int, default=1)
    yt_video_dispatch.add_argument("--max-total-assigned", type=int, default=5)
    yt_video_dispatch.add_argument("--execute", action="store_true")

    yt_video_reclaim = yt_video_sub.add_parser(
        "reclaim-stale-segments",
        help="Return stale processing video segments to global_pending or failed.",
    )
    yt_video_reclaim.add_argument("--story-id", required=True)
    yt_video_reclaim.add_argument(
        "--stale-minutes",
        type=int,
        default=10,
        help="Heartbeat age in minutes after which a processing segment is considered stale (default: 10).",
    )
    yt_video_reclaim.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="After how many reclaim attempts the segment is moved to failed instead of pending (default: 3).",
    )
    yt_video_reclaim.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be reclaimed (no file moves). This is the default unless --execute is passed.",
    )
    yt_video_reclaim.add_argument("--execute", action="store_true")

    yt_video_queue_status = yt_video_sub.add_parser(
        "queue-status",
        help="Show assigned video queue status for one story.",
    )
    yt_video_queue_status.add_argument("--story-id", required=True)
    yt_video_queue_status.add_argument(
        "--stale-minutes",
        type=int,
        default=10,
        help="Heartbeat age in minutes used to flag stale processing segments in the status report (default: 10).",
    )
    yt_video_queue_status.add_argument(
        "--quick",
        action="store_true",
        help="Skip heavier asset preflight and show queue/worker heartbeat status only.",
    )

    yt_video_validate_assets = yt_video_sub.add_parser(
        "validate-job-assets",
        help="Validate that every required input frame exists on Drive for all segments.",
    )
    yt_video_validate_assets.add_argument("--story-id", required=True)
    yt_video_validate_assets.add_argument("--dry-run", action="store_true", help="Informational flag; command is always read-only.")

    yt_video_cleanup = yt_video_sub.add_parser(
        "cleanup-partial-checkpoints",
        help="Delete *.partial.mp4 and orphan checkpoint mp4 without .done.json in work_segments and segments.",
    )
    yt_video_cleanup.add_argument("--story-id", required=True)
    yt_video_cleanup.add_argument("--dry-run", action="store_true")
    yt_video_cleanup.add_argument("--execute", action="store_true")

    yt_video_watch = yt_video_sub.add_parser(
        "watch-queue",
        help="Production watcher loop: reclaim stale -> dispatch -> status until video job is done.",
    )
    yt_video_watch.add_argument("--story-id", required=True)
    yt_video_watch.add_argument("--poll-seconds", type=int, default=60)
    yt_video_watch.add_argument("--stale-minutes", type=int, default=10)
    yt_video_watch.add_argument("--max-attempts", type=int, default=3)
    yt_video_watch.add_argument("--pending-per-worker", type=int, default=1)
    yt_video_watch.add_argument("--max-total-assigned", type=int, default=0, help="0 means pending-per-worker * workers_count.")
    yt_video_watch.add_argument("--workers", default="", help="Comma-separated worker emails; empty = config defaults.")
    yt_video_watch.add_argument("--max-runtime-minutes", type=float, default=0.0, help="0 means no limit.")
    yt_video_watch.add_argument("--once", action="store_true", help="Run exactly one watcher tick and exit.")
    yt_video_watch.add_argument("--dry-run", action="store_true", help="Force dry-run (no file moves and no copies). Default when --execute is not provided.")
    yt_video_watch.add_argument("--execute", action="store_true")
    yt_video_watch.add_argument(
        "--no-auto-import",
        action="store_true",
        help="Do not run import-results when all segments are done. By default the watcher imports final segments locally on completion.",
    )
    yt_video_watch.add_argument(
        "--skip-asset-preflight",
        action="store_true",
        help="Skip validate-job-assets gate before dispatch. Use only if you know assets are fine.",
    )

    yt_video_supervisor = yt_video_sub.add_parser(
        "colab-supervisor",
        help="Monitor Colab worker heartbeats and auto-relaunch stale/offline prepared notebooks.",
    )
    yt_video_supervisor.add_argument("--story-id", required=True)
    yt_video_supervisor.add_argument("--workers", default="", help="Comma-separated worker emails; empty = config defaults.")
    yt_video_supervisor.add_argument("--poll-seconds", type=int, default=60)
    yt_video_supervisor.add_argument("--stale-minutes", type=int, default=10)
    yt_video_supervisor.add_argument("--cooldown-minutes", type=int, default=7, help="Min minutes between relaunch attempts per worker (5-10).")
    yt_video_supervisor.add_argument("--wait-after-open-seconds", type=int, default=45)
    yt_video_supervisor.add_argument("--wait-for-run-start-seconds", type=int, default=120)
    yt_video_supervisor.add_argument("--heartbeat-wait-seconds", type=int, default=180)
    yt_video_supervisor.add_argument("--config-path", type=Path, default=Path("configs/youtube_video_colab_workers.yaml"))
    yt_video_supervisor.add_argument("--once", action="store_true", help="Run exactly one supervisor tick and exit.")
    yt_video_supervisor.add_argument("--max-runtime-minutes", type=float, default=0.0, help="0 means no limit.")
    yt_video_supervisor.add_argument("--no-auto-run", action="store_true", help="Open notebook only; operator must Run all manually.")
    yt_video_supervisor.add_argument(
        "--autorun-mode",
        choices=["browser-tab", "legacy", "manual"],
        default="browser-tab",
        help="browser-tab: safe Playwright/CDP tab autorun (default); legacy: CDP+pyautogui; manual: open only.",
    )
    yt_video_supervisor.add_argument("--new-window", action="store_true", help="Force --new-window on relaunch (may duplicate tabs).")
    yt_video_supervisor.add_argument("--dry-run", action="store_true", help="Force dry-run (no browser launch). Default when --execute is not provided.")
    yt_video_supervisor.add_argument("--execute", action="store_true")

    yt_video_inspect = yt_video_sub.add_parser(
        "inspect-segment",
        help="Inspect one video segment across global/assigned queues and outputs.",
    )
    yt_video_inspect.add_argument("--story-id", required=True)
    yt_video_inspect.add_argument("--segment-id", required=True)

    yt_video_import = yt_video_sub.add_parser(
        "import-results",
        help="Import rendered Drive video segments back to output/youtube.",
    )
    yt_video_import.add_argument("--story-id", required=True)
    yt_video_import.add_argument("--execute", action="store_true")

    yt_video_assemble = yt_video_sub.add_parser(
        "assemble-final",
        help="Concat imported video segments and mux narration into final_video.mp4.",
    )
    yt_video_assemble.add_argument("--story-id", required=True)
    yt_video_assemble.add_argument("--execute", action="store_true")

    yt_video_full = yt_video_sub.add_parser(
        "full-drive-flow",
        help="Prepare missing manifests, export Drive job, and print Colab worker commands.",
    )
    yt_video_full.add_argument("--story-id", required=True)
    yt_video_full.add_argument("--execute", action="store_true")
    yt_video_full.add_argument("--force", action="store_true")

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

    siv = sub.add_parser(
        "site-info-visual",
        help="Validate/rebuild visual_prompts tables and retry invalid site_info (Gemini site_info_builder only)",
    )
    siv_sub = siv.add_subparsers(dest="site_info_visual_cmd", required=True)
    siv_common = argparse.ArgumentParser(add_help=False)
    siv_common.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="runs/site/<story-id>-a (default: latest *-a under runs/site or launch legacy)",
    )
    siv_common.add_argument(
        "--output-site-dir",
        type=Path,
        default=None,
        help="legacy/output/site (default: sibling of runs-root parent)",
    )
    siv_common.add_argument(
        "--export-dir",
        type=Path,
        default=None,
        help="Куда писать visual_prompts.* (default: <runs-root>/visual)",
    )
    siv_val = siv_sub.add_parser(
        "validate",
        parents=[siv_common],
        help="validate_site_info_visuals → CSV/XLSX/invalid/report",
    )
    siv_reb = siv_sub.add_parser(
        "rebuild",
        parents=[siv_common],
        help="Синоним validate (пересобрать таблицы без Gemini)",
    )
    siv_retry = siv_sub.add_parser(
        "retry",
        parents=[siv_common],
        help="retry_invalid_site_info_visuals через site_info_builder",
    )
    siv_retry.add_argument("--execute", action="store_true", help="Запустить Gemini (иначе dry-run)")
    siv_retry.add_argument("--max-retry-attempts", type=int, default=2)
    siv_retry.add_argument("--gemini-workers", type=int, default=1)
    siv_retry.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.yaml"),
    )
    siv_retry.add_argument(
        "--profile",
        default="",
        help="Имя или индекс Chrome user_data_*: 'user_data_1' или '1'. Пусто = user_data_0 (или --auto-profile).",
    )
    siv_retry.add_argument(
        "--auto-profile",
        action="store_true",
        help="Перебрать user_data_* и выбрать первый свободный (фолбэк, если запрошенный занят).",
    )
    siv_retry.add_argument("--gemini-profiles-total", type=int, default=5)
    siv_full = siv_sub.add_parser(
        "full",
        parents=[siv_common],
        help="validate → retry invalid (--execute) → validate",
    )
    siv_full.add_argument("--execute", action="store_true")
    siv_full.add_argument("--max-retry-attempts", type=int, default=2)
    siv_full.add_argument("--gemini-workers", type=int, default=1)
    siv_full.add_argument(
        "--gemini-registry",
        type=Path,
        default=Path("configs/gemini_bots_registry.yaml"),
    )
    siv_full.add_argument(
        "--profile",
        default="",
        help="Имя или индекс Chrome user_data_*: 'user_data_1' или '1'.",
    )
    siv_full.add_argument(
        "--auto-profile",
        action="store_true",
        help="Перебрать user_data_* и выбрать первый свободный.",
    )
    siv_full.add_argument("--gemini-profiles-total", type=int, default=5)

    sp = sub.add_parser("site-publish", help="Prepare output/site stories for legacy autopublisher (To_Publish)")
    sp_sub = sp.add_subparsers(dest="site_publish_cmd", required=True)
    sp_prep = sp_sub.add_parser("prepare", help="Bridge ready output/site stories into legacy/autopublisher/To_Publish")
    sp_prep.add_argument("--dry-run", action="store_true", help="Показать план без копирования")
    sp_prep.add_argument("--execute", action="store_true", help="Реально копировать файлы в To_Publish")
    sp_prep.add_argument("--force", action="store_true", help="Разрешить перезапись папки To_Publish/<story>")
    sp_prep.add_argument("--story", default="", help="Точечно подготовить одну story (имя папки в output/site/)")
    sp_prep.add_argument("--allow-partial-tts", action="store_true", help="Готовить только stories с mp3; terminal skipped/missing не блокируют batch")
    sp_prep.add_argument("--launch-name", default="", help="Run-scoped: имя Запуски/<name>; пакеты лежат в 02_Сайт/05_Публикация_на_сайт")
    sp_prep.add_argument("--launch-dir", type=Path, default=None, help="Run-scoped: явный путь к launch-папке (перекрывает --launch-name)")
    sp_collect = sp_sub.add_parser("collect-assets", help="Collect text/info/mp3/images into output/site before prepare")
    sp_collect.add_argument("--execute", action="store_true", help="Реально копировать story packages в output/site")
    sp_collect.add_argument("--force", action="store_true", help="Перезаписать уже собранные файлы story package")
    sp_collect.add_argument("--allow-partial-tts", action="store_true", help="Не блокировать batch из-за manual skipped / missing mp3")
    sp_collect.add_argument("--launch-name", default="", help="Имя папки Запуски/<name>; пусто = автоопределение по TTS job")
    sp_collect.add_argument("--launch-dir", type=Path, default=None, help="Явный путь к launch-папке")
    sp_collect.add_argument("--images-dir", type=Path, default=None, help="Папка с готовыми картинками; default input/site_visual_import")
    sp_doc = sp_sub.add_parser("env-doctor", help="Audit and sync site publish env from Dirtysecrets")
    sp_doc.add_argument("--dirtysecrets-path", type=Path, default=Path(r"D:\Cursor AI\Dirtysecrets"))
    sp_doc.add_argument("--no-write-env", action="store_true", help="Только аудит, без обновления .env.site_publish")
    sp_pub = sp_sub.add_parser("publish", help="Headless legacy publish with required env precheck")
    sp_pub.add_argument("--story", default="", help="Точечная публикация одной story")
    sp_pub.add_argument("--dry-run", action="store_true", help="Проверка без upload/insert")
    sp_pub.add_argument("--execute", action="store_true", help="Реальная публикация (запрещена при blockers)")
    sp_pub.add_argument("--allow-partial-tts", action="store_true", help="Публиковать только stories с готовым mp3; TTS skips отражать в отчёте")
    sp_pub.add_argument("--dirtysecrets-path", type=Path, default=Path(r"D:\Cursor AI\Dirtysecrets"))
    sp_pub.add_argument("--launch-name", default="", help="Run-scoped: имя Запуски/<name>; legacy publisher получит bridge на launch")
    sp_pub.add_argument("--launch-dir", type=Path, default=None, help="Run-scoped: явный путь к launch-папке (перекрывает --launch-name)")

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
    st_cc_exp_drive.add_argument(
        "--job-only",
        action="store_true",
        help="Только пересобрать job/kokoro_voices_job.json по TXT на Drive (не копировать texts)",
    )
    st_cc_exp_drive.add_argument(
        "--force-job",
        action="store_true",
        help="С --job-only: то же; при полном export — не пропускать export из-за pending_job_txts_still_on_drive",
    )
    st_cc_exp_drive.add_argument(
        "--execute",
        action="store_true",
        help="Для --job-only: записать job-файлы (без флага — dry-run). Полный export-drive по-прежнему всегда пишет texts.",
    )
    st_cc_rebuild_job = st_cc_sub.add_parser(
        "rebuild-voice-job",
        help="Пересобрать kokoro_voices_job.json по TXT на Drive без перезаписи texts/mp3",
    )
    st_cc_rebuild_job.add_argument(
        "--drive-texts",
        action="store_true",
        help="Читать TXT из google_drive texts_dir (по умолчанию включено)",
    )
    st_cc_rebuild_job.add_argument("--texts-dir", type=Path, default=None, help="Путь к Google Drive texts dir")
    st_cc_rebuild_job.add_argument(
        "--execute",
        action="store_true",
        help="Записать job/kokoro_voices_job.json и EXPECTED_* (без флага — dry-run)",
    )
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
    st_cc_reconcile_queue = st_cc_sub.add_parser(
        "reconcile-drive-queue",
        help="Dynamic Drive queue: adopt existing mp3 into done markers without deleting/moving mp3.",
    )
    st_cc_reconcile_queue.add_argument("--execute", action="store_true", help="Создать missing done markers для valid Drive mp3")
    st_cc_export_queue = st_cc_sub.add_parser(
        "export-drive-queue",
        help="Dynamic Drive queue: copy missing txt and create pending site_tts jobs.",
    )
    st_cc_export_queue.add_argument("--limit", type=int, default=0, help="Сколько реально missing jobs добавить в pending (0 = все)")
    st_cc_export_queue.add_argument("--execute", action="store_true", help="Копировать txt и писать pending job json")
    st_cc_migrate_assigned = st_cc_sub.add_parser("migrate-to-assigned-queue", help="Dynamic Drive queue: copy legacy pending into global_pending")
    st_cc_migrate_assigned.add_argument("--execute", action="store_true", help="Создать global_pending из legacy pending")
    st_cc_dispatch_queue = st_cc_sub.add_parser("dispatch-drive-queue", help="Dynamic Drive queue: assign global pending jobs to per-worker queues")
    st_cc_dispatch_queue.add_argument("--workers", default=",".join([
        "ru.iskhakov2017@gmail.com",
        "isi.cordeiro@gmail.com",
        "iheuko119@gmail.com",
        "goegoeseijin@gmail.com",
        "suteadodesun6@gmail.com",
    ]))
    st_cc_dispatch_queue.add_argument("--target-per-worker", type=int, default=2)
    st_cc_dispatch_queue.add_argument("--max-total-assigned", type=int, default=10)
    st_cc_dispatch_queue.add_argument("--execute", action="store_true", help="Записать assigned/<worker>/pending jobs")
    st_cc_reclaim_assigned = st_cc_sub.add_parser("reclaim-stale-assigned", help="Dynamic Drive queue: reclaim stale assigned processing jobs")
    st_cc_reclaim_assigned.add_argument("--stale-minutes", type=int, default=120)
    st_cc_reclaim_assigned.add_argument("--execute", action="store_true", help="Вернуть stale processing assignment в global_pending")
    st_cc_status_queue = st_cc_sub.add_parser("queue-status", help="Dynamic Drive queue status for site_tts")
    st_cc_status_queue.add_argument("--stale-lease-minutes", type=int, default=60)
    st_cc_inspect_job = st_cc_sub.add_parser("inspect-job", help="Dynamic Drive queue: inspect one pending job claimability")
    st_cc_inspect_job.add_argument("--job-id", required=True)
    st_cc_inspect_job.add_argument("--stale-lease-minutes", type=int, default=60)
    st_cc_quarantine_job = st_cc_sub.add_parser("quarantine-job", help="Dynamic Drive queue: disable one bad pending job without deleting text/mp3/done")
    st_cc_quarantine_job.add_argument("--job-id", required=True)
    st_cc_quarantine_job.add_argument("--reason", required=True)
    st_cc_quarantine_job.add_argument("--execute", action="store_true", help="Записать invalid marker")
    st_cc_requeue_stale = st_cc_sub.add_parser(
        "requeue-stale",
        help="Dynamic Drive queue: safely release stale site_tts leases without deleting jobs/text/mp3/done.",
    )
    st_cc_requeue_stale.add_argument("--stale-lease-minutes", type=int, default=60)
    st_cc_requeue_stale.add_argument("--execute", action="store_true", help="Пометить stale leases released и освободить processing markers")
    st_cc_verify_queue = st_cc_sub.add_parser("verify-drive-queue", help="Dynamic Drive queue: verify mp3/done/pending/stale")
    st_cc_verify_queue.add_argument("--stale-lease-minutes", type=int, default=60)
    st_cc_import_queue = st_cc_sub.add_parser("import-drive-queue", help="Dynamic Drive queue: import ready mp3 to local site layout")
    st_cc_import_queue.add_argument("--execute", action="store_true", help="Копировать valid Drive mp3 локально")
    st_cc_import_queue.add_argument("--force", action="store_true", help="Перезаписать локальный mp3")
    st_cc_mark_skipped = st_cc_sub.add_parser(
        "mark-skipped",
        help="Mark selected expected Drive mp3 names as manual skipped terminal items.",
    )
    st_cc_mark_skipped.add_argument("--names", required=True, help="Expected mp3 names separated by | or comma")
    st_cc_mark_skipped.add_argument("--reason", default="manual skip missing expected mp3")
    st_cc_mark_skipped.add_argument("--execute", action="store_true", help="Write manual skipped marker on Drive")
    st_cc_mark_missing_skipped = st_cc_sub.add_parser(
        "mark-missing-skipped",
        help="Mark all currently missing expected Drive mp3 files as manual skipped.",
    )
    st_cc_mark_missing_skipped.add_argument("--reason", default="manual skip missing expected mp3")
    st_cc_mark_missing_skipped.add_argument("--execute", action="store_true", help="Write manual skipped marker on Drive")
    st_cc_wait_drive = st_cc_sub.add_parser("wait-drive", help="Ждать mp3 в Drive и авто-импортировать в output/site")
    st_cc_wait_drive.add_argument("--mp3-dir", type=Path, default=None, help="Путь к Google Drive mp3 dir")
    st_cc_wait_drive.add_argument("--wait-interval-minutes", type=int, default=0, help="Интервал проверки mp3 (0 = из конфига)")
    st_cc_wait_drive.add_argument("--max-wait-hours", type=int, default=0, help="Максимум ожидания в часах (0 = из конфига)")
    st_cc_wait_drive.add_argument("--force", action="store_true", help="Разрешить перезапись существующих mp3")
    st_cc_resume_wait = st_cc_sub.add_parser(
        "resume-drive-wait",
        help="Resume: txt/job уже на Drive — ждать mp3 и импортировать (без повторного export texts)",
    )
    st_cc_resume_wait.add_argument("--mp3-dir", type=Path, default=None, help="Путь к Google Drive mp3 dir")
    st_cc_resume_wait.add_argument("--wait-interval-minutes", type=int, default=0, help="Интервал проверки mp3 (0 = из конфига)")
    st_cc_resume_wait.add_argument("--max-wait-hours", type=int, default=0, help="Максимум ожидания в часах (0 = из конфига)")
    st_cc_resume_wait.add_argument("--force", action="store_true", help="Разрешить перезапись существующих mp3")
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


def _resolve_site_info_visual_paths(
    cfg: OrchestratorConfig,
    args: argparse.Namespace,
) -> tuple[Path, Path, Path] | None:
    """runs_root, output_site_dir, export_dir."""
    runs_root = getattr(args, "runs_root", None)
    if runs_root is not None and str(runs_root).strip():
        runs_root = Path(runs_root).resolve()
    else:
        candidates = sorted(
            (cfg.root_dir / "runs" / "site").glob("*-a"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        launch_legacy = sorted(
            (cfg.root_dir / "Запуски").glob("*/10_Временные_файлы/legacy/runs/site/*-a"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        pick = launch_legacy[0] if launch_legacy else (candidates[0] if candidates else None)
        if pick is None:
            print("site-info-visual: не найден runs/site/*-a — укажите --runs-root")
            return None
        runs_root = pick.resolve()

    output_site = getattr(args, "output_site_dir", None)
    if output_site is not None and str(output_site).strip():
        output_site = Path(output_site).resolve()
    else:
        legacy = runs_root.parent.parent
        output_site = (legacy / "output" / "site").resolve()
        if not output_site.is_dir():
            output_site = (cfg.root_dir / "output" / "site").resolve()

    export_dir = getattr(args, "export_dir", None)
    if export_dir is not None and str(export_dir).strip():
        export_dir = Path(export_dir).resolve()
    else:
        export_dir = (runs_root / "visual").resolve()

    return runs_root, output_site, export_dir


def _site_info_visual_cli(args: argparse.Namespace, cfg: OrchestratorConfig) -> int:
    paths = _resolve_site_info_visual_paths(cfg, args)
    if paths is None:
        return 2
    runs_root, output_site, export_dir = paths
    sub_cmd = str(getattr(args, "site_info_visual_cmd", "") or "").strip().lower()

    from orchestrator.site_visual_validate import (
        run_retry_invalid_site_info_visuals,
        run_site_info_visual_full_cycle,
        run_validate_site_info_visuals,
    )

    registry = getattr(args, "gemini_registry", Path("configs/gemini_bots_registry.yaml"))
    reg_path = registry if registry.is_absolute() else (cfg.root_dir / registry).resolve()

    if sub_cmd in {"validate", "rebuild"}:
        require_human = (os.getenv("CF_REQUIRE_HUMAN_VISUAL_DIR") or "").strip() in {"1", "true", "yes", "on"}
        res = run_validate_site_info_visuals(
            runs_stories_dir=runs_root / "stories",
            output_site_dir=output_site,
            export_dir=export_dir,
            runs_root=runs_root,
            require_human_dir=require_human,
        )
        r = res.report
        print(f"ok={res.ok}")
        print(f"valid={r.get('valid_prompts')} invalid={r.get('invalid_prompts')}")
        print("--- technical artifacts ---")
        print(f"valid_csv={res.valid_csv_path}")
        print(f"invalid_csv={res.invalid_csv_path}")
        print(f"xlsx={res.xlsx_path}")
        print(f"report={res.report_path}")
        print("--- human artifacts ---")
        if res.human_xlsx_path:
            print(f"human_xlsx={res.human_xlsx_path}")
        elif res.human_dir:
            print(f"human_dir={res.human_dir} (xlsx not present)")
        if res.human_sync_error:
            print(f"human_sync_error={res.human_sync_error}")
        comfy_ready = int(r.get("valid_prompts") or 0) > 0
        print(f"comfyui_ready={comfy_ready}")
        return 0 if res.ok else 2

    profile_index = _parse_profile_index(getattr(args, "profile", ""))
    auto_profile = bool(getattr(args, "auto_profile", False))
    profiles_total = int(getattr(args, "gemini_profiles_total", 5) or 5)

    if sub_cmd == "retry":
        res = run_retry_invalid_site_info_visuals(
            config=cfg,
            runs_root=runs_root,
            output_site_dir=output_site,
            export_dir=export_dir,
            gemini_registry_path=reg_path,
            gemini_workers=int(getattr(args, "gemini_workers", 1)),
            max_retry_attempts=int(getattr(args, "max_retry_attempts", 2)),
            execute=bool(getattr(args, "execute", False)),
            profile_index=profile_index,
            auto_profile=auto_profile,
            gemini_profiles_total=profiles_total,
        )
        print(
            f"ok={res.ok} status={res.status} retried={res.retried} "
            f"profile=user_data_{res.selected_gemini_profile if res.selected_gemini_profile is not None else 'n/a'} "
            f"preflight={res.preflight_status}"
        )
        if res.exit_reason:
            print(f"exit_reason={res.exit_reason}")
        if res.browser_launch_error:
            print(f"browser_launch_error={res.browser_launch_error[:300]}")
        if res.report_path:
            print(f"retry_report={res.report_path}")
        if res.validate_after:
            r = res.validate_after.report
            print(f"after: valid={r.get('valid_prompts')} invalid={r.get('invalid_prompts')}")
            print("--- human artifacts ---")
            if res.validate_after.human_xlsx_path:
                print(f"human_xlsx={res.validate_after.human_xlsx_path}")
            elif res.validate_after.human_dir:
                print(f"human_dir={res.validate_after.human_dir} (xlsx not present)")
            if res.validate_after.human_sync_error:
                print(f"human_sync_error={res.validate_after.human_sync_error}")
            comfy_ready = int(r.get("valid_prompts") or 0) > 0
            print(f"comfyui_ready={comfy_ready}")
        return 0 if res.ok else 2

    if sub_cmd == "full":
        payload = run_site_info_visual_full_cycle(
            config=cfg,
            runs_root=runs_root,
            output_site_dir=output_site,
            export_dir=export_dir,
            gemini_registry_path=reg_path,
            execute=bool(getattr(args, "execute", False)),
            max_retry_attempts=int(getattr(args, "max_retry_attempts", 2)),
            gemini_workers=int(getattr(args, "gemini_workers", 1)),
            profile_index=profile_index,
            auto_profile=auto_profile,
            gemini_profiles_total=profiles_total,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        before = payload.get("validate_before") or {}
        retry_block = payload.get("retry") or {}
        after = payload.get("validate_after") or {}
        print("--- site-info-visual full summary ---")
        print(
            "before: valid={} invalid={}".format(
                before.get("valid_prompts"), before.get("invalid_prompts")
            )
        )
        print(
            "retry: status={} profile={} retried={} succeeded={} failed={} candidates={} "
            "skipped_max_retries={} exit_reason={}".format(
                retry_block.get("status"),
                retry_block.get("selected_gemini_profile"),
                retry_block.get("retried"),
                retry_block.get("retry_succeeded"),
                retry_block.get("retry_failed"),
                retry_block.get("retry_candidates"),
                retry_block.get("skipped_max_retries"),
                retry_block.get("exit_reason"),
            )
        )
        if retry_block.get("browser_launch_error"):
            print(f"retry.browser_launch_error={str(retry_block.get('browser_launch_error'))[:300]}")
        print(
            "after: valid={} invalid={}".format(
                (after or {}).get("valid_prompts"), (after or {}).get("invalid_prompts")
            )
        )
        # Расширенный output contract.
        export_xlsx = export_dir / "visual_prompts.xlsx"
        print(f"technical_xlsx={export_xlsx}")
        # human dir мы определяем тем же путём, что и validate.
        from orchestrator.site_visual_validate import (
            HUMAN_VISUAL_XLSX_NAME,
            resolve_human_visual_dir,
        )

        human_dir = resolve_human_visual_dir(runs_root)
        if human_dir:
            human_xlsx = human_dir / HUMAN_VISUAL_XLSX_NAME
            print(f"human_xlsx={human_xlsx} (exists={human_xlsx.is_file()})")
        else:
            print("human_xlsx=human_visual_prompts_xlsx_path_not_found")
        comfy_ok = int((after or {}).get("valid_prompts") or 0) > 0
        print(f"comfyui_ready={comfy_ok}")
        return 0 if payload.get("ok") else 2

    print(f"Неизвестная подкоманда site-info-visual: {sub_cmd}")
    return 2


def _parse_profile_index(value: Any) -> int | None:
    """'user_data_2' -> 2, '2' -> 2, '' -> None."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    if s.startswith("user_data_"):
        s = s[len("user_data_"):]
    try:
        idx = int(s)
    except ValueError:
        return None
    return idx if idx >= 0 else None


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
        and st_cc
        in {
            "export",
            "import",
            "verify",
            "export-drive",
            "import-drive",
            "rebuild-voice-job",
            "wait-drive",
            "resume-drive-wait",
            "reconcile-drive-queue",
            "export-drive-queue",
            "migrate-to-assigned-queue",
            "dispatch-drive-queue",
            "reclaim-stale-assigned",
            "verify-drive-queue",
            "import-drive-queue",
            "mark-skipped",
            "mark-missing-skipped",
            "queue-status",
            "inspect-job",
            "quarantine-job",
            "requeue-stale",
        }
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

    def _queue_human_launch_fallback() -> Path | None:
        if human_launch is not None:
            return human_launch
        if site_root.is_dir():
            return None
        candidates = sorted(
            [p.parent for p in (cfg.root_dir / "Запуски").glob("*/05_Рассказы") if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0].resolve() if candidates else None

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
            mark_drive_expected_skipped,
            mark_missing_drive_expected_skipped,
            rebuild_drive_voice_job,
            setup_drive_workspace,
            verify_mp3_coverage,
            verify_drive_status,
            wait_drive_mp3_and_import,
            _print_rebuild_voice_job_summary,
        )
        from orchestrator.site_tts.drive_queue import (
            dispatch_drive_queue,
            export_drive_queue,
            inspect_queue_job,
            import_drive_queue,
            migrate_to_assigned_queue,
            quarantine_queue_job,
            queue_status,
            reconcile_drive_queue,
            reclaim_stale_assigned,
            requeue_stale_leases,
            verify_drive_queue,
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
                print(
                    "wait_drive_cmd=python -m orchestrator site-tts kokoro-colab wait-drive"
                )
                print(
                    "[NOTE] kokoro-colab export (human) только выгружает txt/job. "
                    "Ожидание mp3+import: wait-drive, resume-drive-wait или launch run-site-flow --execute."
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

        if sub == "rebuild-voice-job":
            tdir = getattr(args, "texts_dir", None)
            try:
                res = rebuild_drive_voice_job(
                    cfg.root_dir,
                    texts_dir=tdir,
                    site_root=site_root,
                    human_launch=human_launch,
                    execute=execute,
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            _print_rebuild_voice_job_summary(res)
            return 0 if res.get("ok", False) else 1

        if sub == "export-drive":
            lim = int(getattr(args, "limit", 0) or 0)
            tdir = getattr(args, "texts_dir", None)
            sfilter = getattr(args, "stories_filter_dir", None)
            job_only = bool(getattr(args, "job_only", False))
            force_job = bool(getattr(args, "force_job", False))
            if job_only:
                try:
                    res = export_drive_texts(
                        cfg.root_dir,
                        texts_dir=tdir,
                        site_root=site_root,
                        human_launch=human_launch,
                        job_only=True,
                        execute=execute,
                    )
                except ValueError as exc:
                    print(str(exc))
                    return 2
                _print_rebuild_voice_job_summary(res)
                return 0 if res.get("ok", False) else 1
            if force_job:
                import os

                os.environ["CONTENT_FACTORY_FORCE_DRIVE_REEXPORT"] = "1"
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
                print("next_cmd=python -m orchestrator site-tts kokoro-colab wait-drive")
            vj = str(res.get("voices_job_json") or "").strip()
            if vj:
                print(f"voices_job_json={vj}")
            if int(res.get("exported", 0) or 0) > 0:
                print("next_cmd=python -m orchestrator site-tts kokoro-colab wait-drive")
                print(
                    "[NOTE] export-drive только выгружает txt. Для ожидания mp3: wait-drive, "
                    "resume-drive-wait, full-cycle-drive или launch run-site-flow --execute."
                )
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

        if sub == "reconcile-drive-queue":
            try:
                q_human_launch = _queue_human_launch_fallback()
                if q_human_launch is not None and human_launch is None:
                    print(f"[site-tts queue] auto_human_launch={q_human_launch}")
                res = reconcile_drive_queue(
                    cfg.root_dir,
                    site_root=site_root,
                    human_launch=q_human_launch,
                    execute=bool(getattr(args, "execute", False)),
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            for k in (
                "execute",
                "drive_root",
                "queue_root",
                "total_expected",
                "drive_mp3_total",
                "drive_valid_mp3_total",
                "existing_drive_mp3",
                "existing_local_mp3",
                "adopted_done_markers",
                "done_markers_existing",
                "pending_needed",
                "partial_or_invalid",
                "extra_drive_mp3_without_expected_story",
            ):
                print(f"{k}={res.get(k)}")
            print(f"duplicates={json.dumps(res.get('duplicates') or {}, ensure_ascii=True)}")
            print(f"sample_adopted={json.dumps(res.get('sample_adopted') or [], ensure_ascii=True)}")
            print(f"sample_pending_needed={json.dumps(res.get('sample_pending_needed') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "export-drive-queue":
            lim = int(getattr(args, "limit", 0) or 0)
            try:
                q_human_launch = _queue_human_launch_fallback()
                if q_human_launch is not None and human_launch is None:
                    print(f"[site-tts queue] auto_human_launch={q_human_launch}")
                res = export_drive_queue(
                    cfg.root_dir,
                    site_root=site_root,
                    human_launch=q_human_launch,
                    limit=(lim if lim > 0 else None),
                    execute=bool(getattr(args, "execute", False)),
                )
            except ValueError as exc:
                print(str(exc))
                return 2
            for k in (
                "execute",
                "drive_root",
                "texts_dir",
                "mp3_dir",
                "queue_root",
                "total_expected",
                "limit",
                "planned_pending_jobs",
                "created_pending_jobs",
                "copied_texts",
                "text_existing",
                "skipped_already_done",
                "skipped_drive_done",
                "skipped_local_done",
                "skipped_pending_existing",
                "skipped_failed_existing",
                "invalid_or_partial",
            ):
                print(f"{k}={res.get(k)}")
            print(f"first_job_ids={json.dumps(res.get('first_job_ids') or [], ensure_ascii=True)}")
            if res.get("errors"):
                print(f"errors={json.dumps(res.get('errors'), ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "migrate-to-assigned-queue":
            res = migrate_to_assigned_queue(
                cfg.root_dir,
                execute=bool(getattr(args, "execute", False)),
            )
            for k in (
                "execute",
                "drive_root",
                "queue_root",
                "source_pending_count",
                "copied_to_global_pending",
                "skipped_done_or_mp3",
                "skipped_invalid",
                "skipped_existing_global",
            ):
                print(f"{k}={res.get(k)}")
            if res.get("errors"):
                print(f"errors={json.dumps(res.get('errors'), ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "dispatch-drive-queue":
            workers_arg = str(getattr(args, "workers", "") or "")
            workers = [w.strip() for w in workers_arg.split(",") if w.strip()]
            res = dispatch_drive_queue(
                cfg.root_dir,
                workers=workers,
                target_per_worker=int(getattr(args, "target_per_worker", 2) or 2),
                max_total_assigned=int(getattr(args, "max_total_assigned", 10) or 10),
                execute=bool(getattr(args, "execute", False)),
            )
            for k in (
                "execute",
                "drive_root",
                "queue_root",
                "target_per_worker",
                "max_total_assigned",
                "source_pending_count",
                "assigned",
            ):
                print(f"{k}={res.get(k)}")
            print(f"assigned_count_by_worker={json.dumps(res.get('assigned_count_by_worker') or {}, ensure_ascii=True)}")
            print(f"skipped_by_reason={json.dumps(res.get('skipped_by_reason') or {}, ensure_ascii=True)}")
            print(f"assignments={json.dumps(res.get('assignments') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "reclaim-stale-assigned":
            res = reclaim_stale_assigned(
                cfg.root_dir,
                stale_minutes=int(getattr(args, "stale_minutes", 120) or 120),
                execute=bool(getattr(args, "execute", False)),
            )
            for k in (
                "execute",
                "drive_root",
                "queue_root",
                "stale_minutes",
                "reclaimed",
                "skipped_done_or_mp3",
                "skipped_fresh",
            ):
                print(f"{k}={res.get(k)}")
            if res.get("errors"):
                print(f"errors={json.dumps(res.get('errors'), ensure_ascii=True)}")
            print(f"rows={json.dumps(res.get('rows') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "queue-status":
            res = queue_status(
                cfg.root_dir,
                stale_minutes=int(getattr(args, "stale_lease_minutes", 60) or 60),
            )
            for k in (
                "drive_root",
                "queue_root",
                "pending_count",
                "global_pending_count",
                "processing_count",
                "done_count",
                "failed_count",
                "invalid_count",
                "active_leases_count",
                "stale_leases_count",
                "active_locks_count",
                "stale_locks_count",
                "pending_claimable",
                "pending_already_done",
                "pending_invalid",
                "pending_blocked_by_active_lock",
                "mp3_done_count",
                "existing_mp3_without_done_marker",
                "partial_or_invalid_mp3",
            ):
                print(f"{k}={res.get(k)}")
            print(f"pending_reasons={json.dumps(res.get('pending_reasons') or {}, ensure_ascii=True)}")
            print(f"assigned_pending_by_worker={json.dumps(res.get('assigned_pending_by_worker') or {}, ensure_ascii=True)}")
            print(f"assigned_processing_by_worker={json.dumps(res.get('assigned_processing_by_worker') or {}, ensure_ascii=True)}")
            print(f"assigned_done_by_worker={json.dumps(res.get('assigned_done_by_worker') or {}, ensure_ascii=True)}")
            print(f"workers_current_job={json.dumps(res.get('workers_current_job') or {}, ensure_ascii=True)}")
            print(f"duplicate_assigned_jobs={json.dumps(res.get('duplicate_assigned_jobs') or [], ensure_ascii=True)}")
            print(f"duplicate_processing_jobs={json.dumps(res.get('duplicate_processing_jobs') or [], ensure_ascii=True)}")
            workers = res.get("workers") if isinstance(res.get("workers"), list) else []
            print("workers:")
            for w in workers:
                print(
                    f"  {w.get('worker_email')}: state={w.get('state')} current_job={w.get('current_job')} "
                    f"heartbeat={w.get('heartbeat_at')} completed={w.get('completed')} failed={w.get('failed')}"
                )
            return 0

        if sub == "inspect-job":
            res = inspect_queue_job(
                cfg.root_dir,
                job_id=str(getattr(args, "job_id", "") or ""),
                stale_minutes=int(getattr(args, "stale_lease_minutes", 60) or 60),
            )
            def _ascii_value(value: object) -> str:
                return json.dumps(str(value), ensure_ascii=True)[1:-1]

            for k in (
                "pending_json_path",
                "pending_json_readable",
                "job_id",
                "text_name",
                "drive_text_path",
                "text_exists",
                "text_size",
                "expected_mp3_path",
                "final_mp3_exists",
                "final_mp3_valid",
                "done_marker_exists",
                "processing_marker_exists",
                "lock_path",
                "lock_state",
                "can_claim",
                "reject_reason",
            ):
                print(f"{k}={_ascii_value(res.get(k))}")
            print(f"active_leases={json.dumps(res.get('active_leases') or [], ensure_ascii=True)}")
            print(f"released_leases={json.dumps(res.get('released_leases') or [], ensure_ascii=True)}")
            print(f"stale_leases={json.dumps(res.get('stale_leases') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "quarantine-job":
            res = quarantine_queue_job(
                cfg.root_dir,
                job_id=str(getattr(args, "job_id", "") or ""),
                reason=str(getattr(args, "reason", "") or ""),
                execute=bool(getattr(args, "execute", False)),
            )
            def _ascii_value(value: object) -> str:
                return json.dumps(str(value), ensure_ascii=True)[1:-1]

            for k in (
                "execute",
                "job_id",
                "reason",
                "pending_json_path",
                "pending_json_exists",
                "invalid_marker_path",
                "invalid_marker_written",
                "action",
            ):
                print(f"{k}={_ascii_value(res.get(k))}")
            return 0 if res.get("ok", False) else 2

        if sub == "requeue-stale":
            res = requeue_stale_leases(
                cfg.root_dir,
                stale_minutes=int(getattr(args, "stale_lease_minutes", 60) or 60),
                execute=bool(getattr(args, "execute", False)),
            )
            for k in (
                "execute",
                "drive_root",
                "queue_root",
                "stale_lease_minutes",
                "leases_count",
                "planned_touch_count",
                "skipped_active",
                "skipped_released",
                "stale_released",
                "stale_locks_released",
                "skipped_active_locks",
                "adopted_done_markers",
                "processing_markers_removed",
            ):
                print(f"{k}={res.get(k)}")
            print("leases:")
            rows = res.get("rows") if isinstance(res.get("rows"), list) else []
            def _ascii_value(value: object) -> str:
                return json.dumps(str(value), ensure_ascii=True)[1:-1]

            for row in rows:
                print(
                    "  "
                    f"job_id={_ascii_value(row.get('job_id'))} "
                    f"worker={_ascii_value(row.get('worker_email'))} "
                    f"state={_ascii_value(row.get('state'))} "
                    f"claimed_at={_ascii_value(row.get('claimed_at'))} "
                    f"heartbeat_at={_ascii_value(row.get('heartbeat_at'))} "
                    f"age_minutes={row.get('age_minutes')} "
                    f"active={row.get('active')} "
                    f"stale={row.get('stale')} "
                    f"final_mp3={row.get('final_mp3_valid')} "
                    f"done_marker={row.get('done_marker_exists')} "
                    f"processing_marker={row.get('processing_marker_exists')} "
                    f"will={_ascii_value(row.get('planned_action'))}"
                )
            if res.get("errors"):
                print(f"errors={json.dumps(res.get('errors'), ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "verify-drive-queue":
            res = verify_drive_queue(
                cfg.root_dir,
                stale_minutes=int(getattr(args, "stale_lease_minutes", 60) or 60),
            )
            for k in (
                "drive_root",
                "queue_root",
                "pending",
                "done",
                "failed",
                "done_ready",
                "done_missing_output",
                "pending_ready_without_done",
                "pending_missing_output",
                "active_leases",
                "stale_leases",
                "mp3_done_count",
                "partial_or_invalid_mp3",
            ):
                print(f"{k}={res.get(k)}")
            return 0

        if sub == "import-drive-queue":
            res = import_drive_queue(
                cfg.root_dir,
                execute=bool(getattr(args, "execute", False)),
                force=bool(getattr(args, "force", False)),
            )
            for k in (
                "execute",
                "drive_root",
                "mp3_dir",
                "queue_root",
                "job_files",
                "planned_import",
                "imported",
                "skipped_existing",
                "missing_mp3",
                "invalid_mp3",
                "missing_target",
                "report_path",
            ):
                print(f"{k}={res.get(k)}")
            if res.get("errors"):
                print(f"errors={json.dumps(res.get('errors'), ensure_ascii=True)}")
            print(f"first_targets={json.dumps(res.get('first_targets') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "mark-skipped":
            raw_names = str(getattr(args, "names", "") or "")
            names = [item.strip() for item in raw_names.replace("|", ",").split(",") if item.strip()]
            res = mark_drive_expected_skipped(
                cfg.root_dir,
                names=names,
                reason=str(getattr(args, "reason", "") or "manual skip missing expected mp3"),
                execute=bool(getattr(args, "execute", False)),
            )
            for k in (
                "execute",
                "job_dir",
                "expected_count",
                "requested_count",
                "marked_count",
                "already_or_total_manual_skipped",
                "manual_skipped_json",
                "manual_skipped_txt",
                "report_path",
            ):
                print(f"{k}={res.get(k)}")
            print(f"marked={json.dumps(res.get('marked') or [], ensure_ascii=True)}")
            print(f"skipped_not_expected={json.dumps(res.get('skipped_not_expected') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub == "mark-missing-skipped":
            res = mark_missing_drive_expected_skipped(
                cfg.root_dir,
                reason=str(getattr(args, "reason", "") or "manual skip missing expected mp3"),
                execute=bool(getattr(args, "execute", False)),
            )
            for k in (
                "execute",
                "job_dir",
                "expected_count",
                "missing_before_mark_count",
                "marked_count",
                "already_or_total_manual_skipped",
                "manual_skipped_json",
                "manual_skipped_txt",
                "report_path",
            ):
                print(f"{k}={res.get(k)}")
            print(f"missing_before_mark={json.dumps(res.get('missing_before_mark') or [], ensure_ascii=True)}")
            print(f"marked={json.dumps(res.get('marked') or [], ensure_ascii=True)}")
            return 0 if res.get("ok", False) else 2

        if sub in {"wait-drive", "resume-drive-wait"}:
            mdir = getattr(args, "mp3_dir", None)
            force = bool(getattr(args, "force", False))
            wait_interval = int(getattr(args, "wait_interval_minutes", 0) or 0)
            max_wait = int(getattr(args, "max_wait_hours", 0) or 0)
            if sub == "resume-drive-wait":
                print("[resume-drive-wait] TXT/job на Drive не перезаписываются — только ожидание mp3 и import.", flush=True)
            try:
                res = wait_drive_mp3_and_import(
                    cfg.root_dir,
                    mp3_dir=mdir,
                    wait_interval_minutes=(wait_interval if wait_interval > 0 else None),
                    max_wait_hours=(max_wait if max_wait > 0 else None),
                    force=force,
                    human_launch=human_launch,
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
                from orchestrator.site_tts.colab_batch import drive_kokoro_job_pending_on_drive

                pending_fc, pinfo_fc = drive_kokoro_job_pending_on_drive(cfg.root_dir)
                if not pending_fc:
                    print("No prepared output/site stories found.")
                    print("This is a TTS-only command.")
                    print("For raw input stories, run:")
                    print("[S] Full Site pipeline with Kokoro Google Drive TTS")
                    return 2
                print(
                    f"[full-cycle-drive] pending Drive job detected ({pinfo_fc.get('reason', '')}) — "
                    "пропускаем export, переходим к wait/import.",
                    flush=True,
                )
            else:
                print("[full-cycle-drive] export done — entering wait for Drive mp3…", flush=True)
            try:
                res = wait_drive_mp3_and_import(
                    cfg.root_dir,
                    mp3_dir=mdir,
                    wait_interval_minutes=(wait_interval if wait_interval > 0 else None),
                    max_wait_hours=(max_wait if max_wait > 0 else None),
                    force=force,
                    human_launch=human_launch,
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

    if args.command == "gemini":
        sub_cmd = str(getattr(args, "gemini_cmd", "") or "").strip().lower()
        if sub_cmd == "preflight-accounts":
            result = run_youtube_gemini_preflight_accounts(
                config=cfg,
                options=YoutubeGeminiPreflightAccountsOptions(
                    stage=str(getattr(args, "stage", "visuals") or "visuals").strip(),
                    youtube_run_id=str(getattr(args, "youtube_run_id", "") or "").strip(),
                    accounts=str(getattr(args, "accounts", "0,1,2") or "0,1,2").strip(),
                    execute=bool(getattr(args, "execute", False)),
                ),
            )
            print("GEMINI_PREFLIGHT_ACCOUNTS", flush=True)
            print(f"stage: {result.get('stage')}", flush=True)
            print(f"execute: {str(bool(result.get('execute'))).lower()}", flush=True)
            print(f"source_of_truth: {result.get('source_of_truth')}", flush=True)
            print(f"proxy_required: {str(bool(result.get('proxy_required'))).lower()}", flush=True)
            print(f"proxy_server: {result.get('proxy_server')}", flush=True)
            print(f"proxy_source: {result.get('proxy_source')}", flush=True)
            print(f"bridge_started: {str(bool(result.get('bridge_started'))).lower()}", flush=True)
            print(f"proxy_error: {result.get('proxy_error')}", flush=True)
            print("worker | profile_dir | actual_email | resolved_registry_email | proxy_server | proxy_source | actual_url | bot_ok | internet_ok | gemini_ok | screenshot | result", flush=True)
            for row in result.get("rows") or []:
                print(
                    f"{row.get('worker_id')} | {row.get('profile_dir')} | {row.get('actual_email')} | "
                    f"{row.get('resolved_registry_email')} | {row.get('proxy_server')} | {row.get('proxy_source')} | "
                    f"{row.get('actual_url')} | {row.get('bot_ok')} | "
                    f"{row.get('internet_ok')} | {row.get('gemini_ok')} | {row.get('screenshot')} | {row.get('result')}",
                    flush=True,
                )
                if row.get("browser_error"):
                    print(f"browser_error[{row.get('worker_id')}]: {row.get('browser_error')}", flush=True)
                if row.get("html"):
                    print(f"html[{row.get('worker_id')}]: {row.get('html')}", flush=True)
                if row.get("screenshot"):
                    print(f"screenshot[{row.get('worker_id')}]: {row.get('screenshot')}", flush=True)
            if result.get("blockers"):
                print("BLOCKERS:", flush=True)
                for blocker in result.get("blockers") or []:
                    print(f"- {blocker}", flush=True)
            print(f"report_json: {result.get('report_json')}", flush=True)
            print(f"report_md: {result.get('report_md')}", flush=True)
            return 0 if result.get("ok") else 2
        print("Неизвестная подкоманда gemini")
        return 2

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

    if args.command == "site":
        from orchestrator.site_readiness import print_site_readiness_summary, run_site_readiness

        sub_cmd = str(getattr(args, "site_cmd", "") or "").strip().lower()
        if sub_cmd == "intake":
            from orchestrator.site_intake import print_site_intake_summary, run_site_intake

            report = run_site_intake(
                config=cfg,
                source_dir=getattr(args, "source_dir"),
                per_folder=int(getattr(args, "per_folder", 0) or 0),
                execute=bool(getattr(args, "execute", False)),
                seed=str(getattr(args, "seed", "") or ""),
            )
            print_site_intake_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "process-launch":
            from orchestrator.site_process_launch import print_site_process_launch_summary, run_site_process_launch

            report = run_site_process_launch(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_process_launch_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "gemini-preflight":
            from orchestrator.site_gemini_preflight import print_site_gemini_preflight_summary, run_site_gemini_preflight

            report = run_site_gemini_preflight(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                gemini_registry_path=getattr(args, "gemini_registry", Path("configs/gemini_bots_registry.example.yaml")),
                stage_key=str(getattr(args, "stage_key", "general_selection") or "general_selection").strip(),
                info_stage_key=str(getattr(args, "info_stage_key", "site_info_builder") or "site_info_builder").strip(),
                profiles_total=int(getattr(args, "profiles_total", 5) or 5),
                target_active_workers=int(getattr(args, "target_active_workers", 3) or 3),
            )
            print_site_gemini_preflight_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "readiness":
            report = run_site_readiness(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_readiness_summary(report)
            if report.get("ok") and bool(getattr(args, "execute", False)):
                reports = report.get("reports") if isinstance(report.get("reports"), dict) else {}
                print(f"readiness_report={reports.get('readiness_report_json', '')}")
                print(f"missing_assets_report={reports.get('missing_assets_report_csv', '')}")
            return 0 if report.get("ok") else 2
        if sub_cmd == "sync-artifacts":
            from orchestrator.site_artifact_sync import print_site_artifact_sync_summary, run_site_artifact_sync

            report = run_site_artifact_sync(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_artifact_sync_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "sync-published-state":
            from orchestrator.site_published_state import print_site_published_state_summary, run_site_published_state_sync

            report = run_site_published_state_sync(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_published_state_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "pending-report":
            from orchestrator.site_pending_report import print_site_pending_report_summary, run_site_pending_report

            report = run_site_pending_report(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_pending_report_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "publish-ready":
            from orchestrator.site_publish_ready import print_site_publish_ready_summary, run_site_publish_ready

            report = run_site_publish_ready(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_publish_ready_summary(report)
            return 0 if report.get("ok") else 2
        if sub_cmd == "readiness-watch":
            from orchestrator.site_readiness_watch import print_site_readiness_watch_summary, run_site_readiness_watch

            report = run_site_readiness_watch(
                config=cfg,
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                threshold_percent=float(getattr(args, "threshold_percent", 90.0) or 90.0),
                check_interval_minutes=int(getattr(args, "check_interval_minutes", 180) or 180),
                max_wait_hours=float(getattr(args, "max_wait_hours", 0.0) or 0.0),
                execute=bool(getattr(args, "execute", False)),
            )
            print_site_readiness_watch_summary(report)
            return 0 if report.get("ok") else 2
        print("Неизвестная подкоманда site")
        return 2

    if args.command == "youtube":
        sub_cmd = str(getattr(args, "youtube_cmd", "") or "").strip().lower()
        if sub_cmd == "prefilter-from-site":
            result = run_youtube_prefilter_from_site(
                config=cfg,
                options=YoutubePrefilterFromSiteOptions(
                    site_run_id=str(args.site_run_id).strip(),
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    min_words=args.min_words,
                    max_words=args.max_words,
                    min_minutes=args.min_minutes,
                    max_minutes=args.max_minutes,
                    words_per_minute=args.words_per_minute,
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
                f" empty_text={result.get('empty_text', 0)}"
                f" missing_cleaned_path={result.get('missing_cleaned_path', 0)}"
            )
            print(
                "duration_contract="
                f"{result.get('min_minutes')}-{result.get('max_minutes')}min "
                f"@{result.get('words_per_minute')}wpm "
                f"words={result.get('min_words')}-{result.get('max_words')}"
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
                    min_words=args.min_words,
                    max_words=args.max_words,
                    min_minutes=args.min_minutes,
                    max_minutes=args.max_minutes,
                    words_per_minute=args.words_per_minute,
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
            print(
                "duration_contract="
                f"{result.get('min_minutes')}-{result.get('max_minutes')}min "
                f"@{result.get('words_per_minute')}wpm "
                f"words={result.get('min_words')}-{result.get('max_words')}"
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

        if sub_cmd == "safe-status":
            result = run_youtube_safe_status(
                config=cfg,
                options=YoutubeSafeStatusOptions(story_id=str(args.story_id).strip()),
            )
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"expected_language={result.get('expected_language')}")
            print(f"source_path={result.get('source_path')}")
            print(f"source_language={result.get('source_language')}")
            print(f"safe_story_path={result.get('safe_story_path')}")
            print(f"safe_story_language={result.get('safe_story_language')}")
            print(f"safe_story_status={result.get('safe_story_status')}")
            print(f"promo_path={result.get('promo_path')}")
            print(f"promo_language={result.get('promo_language')}")
            print(f"promo_status={result.get('promo_status')}")
            print(f"text_ready_for_audio_language={result.get('text_ready_for_audio_language')}")
            print(f"audio_path={result.get('audio_path')}")
            print(f"audio_exists={result.get('audio_exists')}")
            print(f"tts_status={result.get('tts_status')}")
            print(f"current_blocker={result.get('current_blocker')}")
            print(f"next_action={result.get('next_action')}")
            print(f"story_manifest={result.get('story_manifest')}")
            return 0

        if sub_cmd == "safe-regenerate":
            result = run_youtube_safe_regenerate(
                config=cfg,
                options=YoutubeSafeRegenerateOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                ),
            )
            print(f"ok={result.get('ok')}")
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"source_path={result.get('source_path')}")
            print(f"source_language={result.get('source_language')}")
            print(f"safe_story_path={result.get('safe_story_path')}")
            print(f"safe_story_language={result.get('safe_story_language')}")
            print(f"safe_story_status={result.get('safe_story_status')}")
            print(f"backup_path={result.get('backup_path')}")
            print(f"current_blocker={result.get('current_blocker')}")
            print(f"next_action={result.get('next_action')}")
            if result.get("message"):
                print(f"message={result.get('message')}")
            for path in result.get("changed_files") or []:
                print(f"changed_file={path}")
            return 0 if result.get("ok", False) else 2

        if sub_cmd == "safe-english-run":
            result = run_youtube_safe_english_run(
                config=cfg,
                options=YoutubeSafeEnglishRunOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                    force=bool(getattr(args, "force", False)),
                    account_index=int(getattr(args, "account_index", 0) or 0),
                    gemini_registry_path=getattr(args, "gemini_registry", Path("configs/gemini_bots_registry.example.yaml")),
                    reuse_legacy_user_data=bool(getattr(args, "reuse_legacy_user_data", False)),
                ),
            )
            print(f"ok={result.get('ok')}")
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"source_path={result.get('source_path')}")
            print(f"source_language={result.get('source_language')}")
            print(f"safe_story_path={result.get('safe_story_path')}")
            print(f"safe_story_language={result.get('safe_story_language')}")
            print(f"expected_language={result.get('expected_language')}")
            print(f"prompt_path={result.get('prompt_path')}")
            print(f"chunks_total={result.get('chunks_total')}")
            print(f"chunks_done={result.get('chunks_done')}")
            print(f"raw_outputs_dir={result.get('raw_outputs_dir')}")
            print(f"candidate_output_path={result.get('candidate_output_path')}")
            print(f"final_output_path={result.get('final_output_path')}")
            print(f"detected_language={result.get('detected_language')}")
            print(f"validation_status={result.get('validation_status')}")
            print(f"reason={result.get('reason')}")
            print(f"backup_path={result.get('backup_path')}")
            print(f"gemini_account_email={result.get('gemini_account_email')}")
            print(f"gemini_account_index={result.get('gemini_account_index')}")
            print(f"gemini_bot_key={result.get('gemini_bot_key')}")
            print(f"gemini_url={result.get('gemini_url')}")
            print(f"user_data_dir={result.get('user_data_dir')}")
            print(f"log_path={result.get('log_path')}")
            if result.get("message"):
                print(f"message={result.get('message')}")
            for path in result.get("changed_files") or []:
                print(f"changed_file={path}")
            return 0 if result.get("ok", False) else 2

        if sub_cmd == "run-selection-bridge":
            result = run_youtube_run_selection_bridge(
                config=cfg,
                options=YoutubeRunSelectionBridgeOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    input_id=str(getattr(args, "input_id", "") or "").strip(),
                    story_id=str(getattr(args, "story_id", "") or "").strip(),
                    execute=bool(args.execute),
                    force=bool(getattr(args, "force", False)),
                    reuse_legacy_user_data=bool(getattr(args, "reuse_legacy_user_data", False)),
                    account_index=int(getattr(args, "account_index", 0) or 0),
                    user_data_dir=str(getattr(args, "user_data_dir", "") or "").strip(),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube run-selection-bridge failed"))
                if result.get("selection_bridge_status"):
                    print(f"selection_bridge_status={result.get('selection_bridge_status')}")
                return 2
            print(f"skipped_subprocess={result.get('skipped_subprocess', False)}")
            print(f"item_id={result.get('item_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"registry_path={result.get('registry_path')}")
            print(f"bot_key={result.get('bot_key')}")
            print(f"bot_account_email={result.get('bot_account_email')}")
            print(f"bot_url={result.get('bot_url')}")
            print(f"staging_input_txt={result.get('staging_input_txt')}")
            print(f"gemini_stories_dir={result.get('gemini_stories_dir')}")
            print(f"gemini_user_data_dir={result.get('gemini_user_data_dir')}")
            print(f"user_data_source={result.get('user_data_source')}")
            print(f"gemini_bots_config={result.get('gemini_bots_config')}")
            print(f"expected_gemini_output_text={result.get('expected_gemini_output_text')}")
            print(f"word_count={result.get('word_count')}")
            print(f"estimated_tts_minutes={result.get('estimated_tts_minutes')}")
            print(f"duration_gate={result.get('duration_gate')}")
            print(f"duration_contract={result.get('duration_contract')}")
            print(f"metadata_header_present={result.get('metadata_header_present')}")
            print(f"manual_cmd_windows={result.get('manual_cmd_windows')}")
            print(f"selection_bridge_status={result.get('selection_bridge_status')}")
            if result.get("gemini_auto_exit_code") is not None:
                print(f"gemini_auto_exit_code={result.get('gemini_auto_exit_code')}")
            if result.get("imported_to"):
                print(f"imported_to={result.get('imported_to')}")
            if result.get("import_message"):
                print(f"import_message={result.get('import_message')}")
            if result.get("verdict"):
                print(f"verdict={result.get('verdict')}")
            if result.get("message"):
                print(f"message={result.get('message')}")
            return 0

        if sub_cmd == "selection-batch-from-site":
            result = run_youtube_selection_batch_from_site(
                config=cfg,
                options=YoutubeSelectionBatchFromSiteOptions(
                    site_run_id=str(args.site_run_id).strip(),
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    min_words=args.min_words,
                    max_words=args.max_words,
                    min_minutes=args.min_minutes,
                    max_minutes=args.max_minutes,
                    words_per_minute=args.words_per_minute,
                    max_attempts=int(args.max_attempts),
                    target_yes=int(args.target_yes),
                    workers=int(args.workers),
                    account_start_index=int(args.account_start_index),
                    execute=bool(args.execute),
                    retry_failed=bool(args.retry_failed),
                    seed=args.seed,
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "youtube selection-batch-from-site failed"))
                return 2
            print(
                "summary:"
                f" status={result.get('status', '')}"
                f" candidates={result.get('candidate_count', '')}"
                f" planned={result.get('planned_count', '')}"
                f" attempts={result.get('attempts', 0)}"
                f" yes={result.get('yes_count', 0)}"
                f" no={result.get('no_count', 0)}"
                f" failed={result.get('failed_count', 0)}"
            )
            if result.get("excluded") is not None:
                print(f"excluded={json.dumps(result.get('excluded'), ensure_ascii=False)}")
            if result.get("already_checked_clean_input_count") is not None:
                print(f"already_checked_clean_input_count={result.get('already_checked_clean_input_count')}")
            if result.get("input_format"):
                print(f"input_format={result.get('input_format')}")
            if result.get("metadata_header_enabled") is not None:
                print(f"metadata_header_enabled={result.get('metadata_header_enabled')}")
            if result.get("plan_path"):
                print(f"plan_path={result.get('plan_path')}")
            if result.get("summary_path"):
                print(f"summary_path={result.get('summary_path')}")
            if result.get("results_path"):
                print(f"results_path={result.get('results_path')}")
            if result.get("yes_path"):
                print(f"yes_path={result.get('yes_path')}")
            if result.get("no_path"):
                print(f"no_path={result.get('no_path')}")
            if result.get("failed_path"):
                print(f"failed_path={result.get('failed_path')}")
            for row in result.get("first_20", [])[:20] if isinstance(result.get("first_20", []), list) else []:
                print(
                    "candidate:"
                    f" rank={row.get('rank')}"
                    f" title={row.get('canonical_basename')}"
                    f" words={row.get('word_count')}"
                    f" minutes={row.get('estimated_minutes')}"
                )
            return 0

        if sub_cmd == "promo-run":
            result = run_youtube_promo_run(
                config=cfg,
                options=YoutubePromoRunOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                    force=bool(getattr(args, "force", False)),
                    fresh_gemini_session=not bool(getattr(args, "reuse_legacy_user_data", False)),
                    account_index=int(getattr(args, "account_index", 0) or 0),
                    gemini_registry_path=getattr(args, "gemini_registry", None),
                ),
            )
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"source_path={result.get('source_path')}")
            print(f"source_language={result.get('source_language')}")
            print(f"expected_language={result.get('expected_language')}")
            print(f"output_path={result.get('output_path')}")
            print(f"output_language={result.get('output_language')}")
            print(f"climax_snippet_path={result.get('climax_snippet_path')}")
            print(f"intro_inserted={result.get('intro_inserted')}")
            print(f"mid_inserted={result.get('mid_inserted')}")
            print(f"outro_inserted={result.get('outro_inserted')}")
            print(f"climax_method={result.get('climax_method')}")
            print(f"placeholder_ads_used={result.get('placeholder_ads_used')}")
            print(f"fresh_gemini_session={result.get('fresh_gemini_session')}")
            print(f"user_data_dir={result.get('user_data_dir')}")
            print(f"gemini_account_email={result.get('gemini_account_email')}")
            print(f"gemini_account_index={result.get('gemini_account_index')}")
            print(f"gemini_bot_key={result.get('gemini_bot_key')}")
            print(f"gemini_url={result.get('gemini_url')}")
            print(f"gemini_registry_path={result.get('gemini_registry_path')}")
            audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
            print(f"audio_status={audio.get('status')}")
            print(f"audio_stale={audio.get('stale')}")
            print(f"current_blocker={result.get('current_blocker')}")
            print(f"next_action={result.get('next_action')}")
            if result.get("legacy_log_path"):
                print(f"legacy_log_path={result.get('legacy_log_path')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            if result.get("manifest_path"):
                print(f"manifest_path={result.get('manifest_path')}")
            print(f"changed_files={json.dumps(result.get('changed_files') or [], ensure_ascii=True)}")
            return 0 if result.get("ok") or result.get("status") == "would_run" else 2

        if sub_cmd == "promo-status":
            result = run_youtube_promo_status(
                config=cfg,
                options=YoutubePromoStatusOptions(story_id=str(args.story_id).strip()),
            )
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"safe_story_exists={result.get('safe_story_exists')}")
            print(f"safe_story_path={result.get('safe_story_path')}")
            print(f"source_path={result.get('source_path')}")
            print(f"source_language={result.get('source_language')}")
            print(f"expected_language={result.get('expected_language')}")
            print(f"source_exists={result.get('source_exists')}")
            print(f"output_exists={result.get('output_exists')}")
            print(f"output_path={result.get('output_path')}")
            print(f"output_language={result.get('output_language')}")
            print(f"has_promo_markers={result.get('has_promo_markers')}")
            print(f"has_legacy_promo_text={result.get('has_legacy_promo_text')}")
            print(f"output_equals_safe_story={result.get('output_equals_safe_story')}")
            print(f"intro_inserted={result.get('intro_inserted')}")
            print(f"mid_inserted={result.get('mid_inserted')}")
            print(f"outro_inserted={result.get('outro_inserted')}")
            print(f"climax_snippet_exists={result.get('climax_snippet_exists')}")
            print(f"climax_snippet_path={result.get('climax_snippet_path')}")
            print(f"climax_method={result.get('climax_method')}")
            print(f"placeholder_ads_used={result.get('placeholder_ads_used')}")
            audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
            print(f"audio_exists={audio.get('exists')}")
            print(f"audio_status={audio.get('status')}")
            print(f"audio_stale={audio.get('stale')}")
            print(f"text_ready_for_audio_hash={audio.get('text_ready_for_audio_hash')}")
            print(f"current_text_ready_for_audio_hash={audio.get('current_text_ready_for_audio_hash')}")
            print(f"current_blocker={result.get('current_blocker')}")
            print(f"next_action={result.get('next_action')}")
            print(f"report_path={result.get('report_path')}")
            return 0


        if sub_cmd == "tts":
            tts_cmd = str(getattr(args, "youtube_tts_cmd", "") or "").strip()
            if tts_cmd == "prepare-launch-jobs":
                result = prepare_launch_jobs(
                    cfg,
                    PrepareLaunchJobsOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                        retry_failed=bool(getattr(args, "retry_failed", False)),
                        force=bool(getattr(args, "force", False)),
                        dry_run=bool(getattr(args, "dry_run", False)),
                        execute=bool(getattr(args, "execute", False)),
                    ),
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 2
            if tts_cmd == "preflight":
                result = preflight_launch_jobs(
                    cfg,
                    TtsLaunchOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                    ),
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 2
            if tts_cmd == "status":
                result = status_launch_jobs(
                    cfg,
                    TtsLaunchOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                    ),
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 2
            if tts_cmd == "repair-readiness":
                result = repair_tts_readiness(
                    cfg,
                    RepairReadinessOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                        execute=bool(getattr(args, "execute", False)),
                    ),
                )
                summary = result.get("summary") or {}
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0 if summary.get("ok") else 2
            if tts_cmd == "identity-audit":
                result = run_identity_audit(
                    cfg,
                    IdentityAuditOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                        execute=bool(getattr(args, "execute", False)),
                    ),
                )
                print(json.dumps(result.get("summary") or result, ensure_ascii=False, indent=2))
                return 0 if (result.get("summary") or {}).get("identity_mismatch", 1) == 0 else 2
            if tts_cmd == "voice-plan":
                result = run_voice_plan(
                    cfg,
                    VoicePlanOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                    ),
                )
                print_voice_plan_terminal(result)
                return 0 if result.get("voice_plan_ready") else 2
            if tts_cmd == "promo-forensic-audit":
                drive_root = getattr(args, "drive_root", None)
                result = run_youtube_tts_promo_forensic_audit(
                    config=cfg,
                    options=PromoForensicAuditOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        drive_root=drive_root,
                    ),
                )
                print(json.dumps({k: result[k] for k in result if k != "stories"}, ensure_ascii=False, indent=2))
                return 0
            if tts_cmd == "import-from-drive":
                result = run_import_from_drive(
                    cfg,
                    ImportFromDriveOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                        cleanup_drive_after_import=bool(getattr(args, "cleanup_drive_after_import", False)),
                        drive_root=getattr(args, "drive_root", None),
                    ),
                )
                print_import_summary(result)
                return 0 if result.get("import_complete") else 2
            if tts_cmd == "launch-wait-import":
                result = run_launch_wait_import(
                    cfg,
                    LaunchWaitImportOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        workers=int(getattr(args, "workers", 5) or 5),
                        poll_minutes=float(getattr(args, "poll_minutes", 30.0) or 30.0),
                        max_hours=float(getattr(args, "max_hours", 1000.0) or 1000.0),
                        execute=bool(getattr(args, "execute", False)),
                        start_browser=bool(getattr(args, "start_browser", True)),
                        start_cmd=str(getattr(args, "start_cmd", "") or ".\\START_YOUTUBE_TTS_YANDEX_5TABS_PROFILE_PROXY.bat"),
                        continue_next_stage=bool(getattr(args, "continue_next_stage", False)),
                        drive_root=getattr(args, "drive_root", None),
                        cleanup_drive_after_import=bool(getattr(args, "cleanup_drive_after_import", False)),
                    ),
                )
                print_final_summary(result)
                return 0 if (result.get("summary") or {}).get("TTS_STAGE_COMPLETE") else 2
            print("unknown youtube tts subcommand")
            return 2

        if sub_cmd == "tts-kokoro-colab":
            tts_sub = str(getattr(args, "youtube_tts_kokoro_cmd", "") or "").strip()
            if tts_sub == "export":
                result = run_youtube_tts_kokoro_colab_export(
                    config=cfg,
                    options=YoutubeTtsKokoroColabExportOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        story_id=str(args.story_id).strip(),
                        execute=bool(args.execute),
                        drive_root=args.drive_root,
                    ),
                )
                if not result.get("ok", False):
                    print(result.get("message", "youtube tts-kokoro-colab export failed"))
                    for p in result.get("missing", []) or []:
                        print(f"missing={p}")
                    return 2
                print(f"status={result.get('status')}")
                print(f"execute={result.get('execute')}")
                print(f"youtube_run_id={result.get('youtube_run_id')}")
                print(f"story_id={result.get('story_id')}")
                print(f"canonical_basename={result.get('canonical_basename')}")
                print(f"source_text_path={result.get('source_text_path')}")
                print(f"drive_root={result.get('drive_root')}")
                print(f"drive_text_path={result.get('drive_text_path')}")
                print(f"expected_drive_audio_path={result.get('expected_drive_audio_path')}")
                print(f"expected_local_audio_path={result.get('expected_local_audio_path')}")
                print(f"voice_label={result.get('voice_label')}")
                print(f"kokoro_voice={result.get('kokoro_voice')}")
                print(f"speed={result.get('speed')}")
                print(f"sample_rate={result.get('sample_rate')}")
                print(f"expected_files_path={result.get('expected_files_path')}")
                print(f"expected_count_path={result.get('expected_count_path')}")
                print(f"youtube_tts_job_path={result.get('youtube_tts_job_path')}")
                print(f"youtube_tts_job_manifest_path={result.get('youtube_tts_job_manifest_path')}")
                if result.get("local_export_report"):
                    print(f"local_export_report={result.get('local_export_report')}")
                return 0
            if tts_sub == "verify":
                result = run_youtube_tts_kokoro_colab_verify(
                    config=cfg,
                    options=YoutubeTtsKokoroColabVerifyOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        story_id=str(args.story_id).strip(),
                        drive_root=args.drive_root,
                    ),
                )
                if not result.get("ok", False):
                    print(result.get("message", "youtube tts-kokoro-colab verify failed"))
                    return 2
                print(f"status={result.get('status')}")
                print(f"youtube_run_id={result.get('youtube_run_id')}")
                print(f"story_id={result.get('story_id')}")
                print(f"canonical_basename={result.get('canonical_basename')}")
                print(f"expected_drive_audio_path={result.get('expected_drive_audio_path')}")
                print(f"exists={result.get('exists')}")
                print(f"size={result.get('size')}")
                print(f"modified_time={result.get('modified_time')}")
                print(f"expected_local_audio_path={result.get('expected_local_audio_path')}")
                return 0
            if tts_sub == "import":
                result = run_youtube_tts_kokoro_colab_import(
                    config=cfg,
                    options=YoutubeTtsKokoroColabImportOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        story_id=str(args.story_id).strip(),
                        drive_root=args.drive_root,
                        force=bool(args.force),
                    ),
                )
                if not result.get("ok", False):
                    print(result.get("message", "youtube tts-kokoro-colab import failed"))
                    if result.get("expected_drive_audio_path"):
                        print(f"expected_drive_audio_path={result.get('expected_drive_audio_path')}")
                    if result.get("expected_local_audio_path"):
                        print(f"expected_local_audio_path={result.get('expected_local_audio_path')}")
                    return 2
                print(f"status={result.get('status')}")
                print(f"youtube_run_id={result.get('youtube_run_id')}")
                print(f"story_id={result.get('story_id')}")
                print(f"canonical_basename={result.get('canonical_basename')}")
                print(f"expected_drive_audio_path={result.get('expected_drive_audio_path')}")
                print(f"expected_local_audio_path={result.get('expected_local_audio_path')}")
                print(f"source_size={result.get('source_size')}")
                print(f"target_size={result.get('target_size')}")
                print(f"audio_manifest_path={result.get('audio_manifest_path')}")
                return 0
            print("Неизвестная подкоманда youtube tts-kokoro-colab")
            return 2

        if sub_cmd == "characters":
            result = run_youtube_characters_bridge(
                config=cfg,
                options=YoutubeCharactersBridgeOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                print(result.get("message", "youtube characters bridge preflight failed"))
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"story_dir={result.get('story_dir')}")
            print(f"source_text_path={result.get('source_text_path')}")
            print(f"source_text_words={result.get('source_text_words')}")
            print(f"characters_path={result.get('characters_path')}")
            print(f"characters_exists={result.get('characters_exists')}")
            print(f"legacy_stage_dir={result.get('legacy_stage_dir')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            print(f"manual_cmd_windows={result.get('manual_cmd_windows')}")
            print(f"note={result.get('note')}")
            return 0

        if sub_cmd == "characters-export":
            result = run_youtube_characters_export(
                config=cfg,
                options=YoutubeCharactersExportOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"source_text_path={result.get('source_text_path')}")
            print(f"source_text_words={result.get('source_text_words')}")
            print(f"staging_dir={result.get('staging_dir')}")
            print(f"staging_story_txt={result.get('staging_story_txt')}")
            print(f"staging_readme={result.get('staging_readme')}")
            print(f"target_characters_path={result.get('target_characters_path')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            print(f"note={result.get('note')}")
            return 0

        if sub_cmd == "characters-import":
            result = run_youtube_characters_import(
                config=cfg,
                options=YoutubeCharactersImportOptions(
                    story_id=str(args.story_id).strip(),
                    source=args.source,
                    execute=bool(args.execute),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"source={result.get('source')}")
            print(f"source_size={result.get('source_size')}")
            print(f"target_characters_path={result.get('target_characters_path')}")
            if result.get("target_size") is not None:
                print(f"target_size={result.get('target_size')}")
            if result.get("manifest_path"):
                print(f"manifest_path={result.get('manifest_path')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            return 0

        if sub_cmd == "director-prompts":
            result = run_youtube_director_prompts_bridge(
                config=cfg,
                options=YoutubeDirectorPromptsBridgeOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                print(result.get("message", "youtube director-prompts bridge preflight failed"))
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"story_dir={result.get('story_dir')}")
            print(f"source_text_path={result.get('source_text_path')}")
            print(f"source_text_words={result.get('source_text_words')}")
            print(f"audio_path={result.get('audio_path')}")
            print(f"audio_duration_sec={result.get('audio_duration_sec')}")
            print(f"frame_duration_sec={result.get('frame_duration_sec')}")
            print(f"estimated_prompts={result.get('estimated_prompts')}")
            print(f"characters_path={result.get('characters_path')}")
            print(f"characters_exists={result.get('characters_exists')}")
            print(f"prompts_path={result.get('prompts_path')}")
            print(f"prompts_count={result.get('prompts_count')}")
            print(f"legacy_stage_dir={result.get('legacy_stage_dir')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            print(f"manual_cmd_windows={result.get('manual_cmd_windows')}")
            print(f"note={result.get('note')}")
            return 0

        if sub_cmd == "director-prompts-export":
            result = run_youtube_director_prompts_export(
                config=cfg,
                options=YoutubeDirectorPromptsExportOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(args.execute),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"source_text_path={result.get('source_text_path')}")
            print(f"source_text_words={result.get('source_text_words')}")
            print(f"audio_path={result.get('audio_path')}")
            print(f"audio_duration_sec={result.get('audio_duration_sec')}")
            print(f"frame_duration_sec={result.get('frame_duration_sec')}")
            print(f"estimated_prompts={result.get('estimated_prompts')}")
            print(f"characters_path={result.get('characters_path')}")
            print(f"staging_dir={result.get('staging_dir')}")
            print(f"staging_story_txt={result.get('staging_story_txt')}")
            print(f"staging_characters_txt={result.get('staging_characters_txt')}")
            print(f"staging_narration_path_txt={result.get('staging_narration_path_txt')}")
            print(f"staging_readme={result.get('staging_readme')}")
            print(f"target_prompts_path={result.get('target_prompts_path')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            print(f"note={result.get('note')}")
            return 0

        if sub_cmd == "director-prompts-import":
            result = run_youtube_director_prompts_import(
                config=cfg,
                options=YoutubeDirectorPromptsImportOptions(
                    story_id=str(args.story_id).strip(),
                    source=args.source,
                    execute=bool(args.execute),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"source={result.get('source')}")
            print(f"source_size={result.get('source_size')}")
            print(f"prompts_count={result.get('prompts_count')}")
            print(f"target_prompts_path={result.get('target_prompts_path')}")
            if result.get("target_size") is not None:
                print(f"target_size={result.get('target_size')}")
            if result.get("manifest_path"):
                print(f"manifest_path={result.get('manifest_path')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            return 0

        if sub_cmd == "frames-runpod":
            result = run_youtube_frames_runpod_bridge(
                config=cfg,
                options=YoutubeFramesRunpodBridgeOptions(
                    story_id=str(args.story_id).strip(),
                    runpod_url=str(args.runpod_url).strip(),
                    execute=bool(args.execute),
                    prepare_only=bool(getattr(args, "prepare_only", False)),
                    workflow=str(getattr(args, "workflow", "") or "").strip(),
                ),
            )
            if not result.get("ok", False):
                print(f"status={result.get('status')}")
                print(result.get("message", "youtube frames-runpod bridge preflight failed"))
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                for p in result.get("missing_prerequisites", []) or []:
                    print(f"missing_prerequisite={p}")
                if result.get("report_path"):
                    print(f"report_path={result.get('report_path')}")
                if result.get("frame_jobs_path"):
                    print(f"frame_jobs_path={result.get('frame_jobs_path')}")
                validation = result.get("workflow_validation")
                if validation:
                    print(f"workflow_validation={json.dumps(validation, ensure_ascii=True)}")
                return 2
            print(f"status={result.get('status')}")
            print(f"execute={result.get('execute')}")
            print(f"prepare_only={result.get('prepare_only')}")
            print(f"prompt_mode={result.get('prompt_mode')}")
            print(f"story_id={result.get('story_id')}")
            print(f"canonical_basename={result.get('canonical_basename')}")
            print(f"story_dir={result.get('story_dir')}")
            print(f"characters_path={result.get('characters_path')}")
            print(f"characters_exists={result.get('characters_exists')}")
            print(f"missing_prerequisites={json.dumps(result.get('missing_prerequisites') or [], ensure_ascii=True)}")
            print(f"prompts_path={result.get('prompts_path')}")
            print(f"prompts_count={result.get('prompts_count')}")
            if result.get("payload_debug_path"):
                print(f"payload_debug_path={result.get('payload_debug_path')}")
            print(f"frames_dir={result.get('frames_dir')}")
            print(f"workflow_path={result.get('workflow_path')}")
            print(f"workflow={json.dumps(result.get('workflow') or {}, ensure_ascii=True)}")
            print(f"workflow_validation={json.dumps(result.get('workflow_validation') or {}, ensure_ascii=True)}")
            print(f"runpod_url_preview={result.get('runpod_url_preview')}")
            print(f"expected_frames={result.get('expected_frames')}")
            print(f"generated_frames={result.get('generated_frames')}")
            print(f"pending_frames={result.get('pending_frames')}")
            print(f"failed_frames={result.get('failed_frames')}")
            print(f"existing_frames_total={result.get('existing_frames_total')}")
            print(f"legacy_named_existing={json.dumps(result.get('legacy_named_existing') or [], ensure_ascii=True)}")
            print(f"first_10_pending={json.dumps(result.get('first_10_pending') or [], ensure_ascii=True)}")
            print(f"first_10_failed={json.dumps(result.get('first_10_failed') or [], ensure_ascii=True)}")
            if result.get("frame_jobs_path"):
                print(f"frame_jobs_path={result.get('frame_jobs_path')}")
            if result.get("failed_frames_path"):
                print(f"failed_frames_path={result.get('failed_frames_path')}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            if result.get("duration_sec") is not None:
                print(f"duration_sec={result.get('duration_sec')}")
            print(f"note={result.get('note')}")
            return 0

        if sub_cmd == "visuals-run":
            result = run_youtube_visuals_run(
                config=cfg,
                options=YoutubeVisualsRunOptions(
                    story_id=str(args.story_id).strip(),
                    youtube_run_id=str(getattr(args, "youtube_run_id", "") or "").strip(),
                    runpod_url=str(getattr(args, "runpod_url", "") or "").strip(),
                    workflow=str(getattr(args, "workflow", "") or "").strip(),
                    execute=bool(args.execute),
                    watch=bool(getattr(args, "watch", False)),
                    allow_gemini=bool(getattr(args, "allow_gemini", False)),
                    auto_gemini=bool(getattr(args, "auto_gemini", False)),
                    fresh_visuals=bool(getattr(args, "fresh_visuals", False)),
                    prompt_runpod_url=not bool(getattr(args, "no_runpod_prompt", False)),
                    segment_sec=float(getattr(args, "segment_sec", 180.0) or 180.0),
                    watch_interval_sec=int(getattr(args, "watch_interval_sec", 5) or 5),
                    watch_timeout_sec=int(getattr(args, "watch_timeout_sec", 0) or 0),
                    accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                ),
            )
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"mode={result.get('mode')}")
            print(f"story_id={result.get('story_id')}")
            print(f"story_dir={result.get('story_dir')}")
            print(f"next_action={result.get('next_action')}")
            print(f"blockers={json.dumps(result.get('blockers') or [], ensure_ascii=True)}")
            print(f"changed_files={json.dumps(result.get('changed_files') or [], ensure_ascii=True)}")
            for row in result.get("stages", []) if isinstance(result.get("stages"), list) else []:
                print(
                    "stage:"
                    f" name={row.get('stage')}"
                    f" status={row.get('status')}"
                    f" message={row.get('message')}"
                )
            reports = ((result.get("status_report") or {}).get("reports") or {}) if isinstance(result.get("status_report"), dict) else {}
            if reports.get("run_report"):
                print(f"run_report={reports.get('run_report')}")
            if reports.get("status_report"):
                print(f"status_report={reports.get('status_report')}")
            return 0 if result.get("ok") else 2

        if sub_cmd == "visuals-run-all":
            result = run_youtube_visuals_run_all(
                config=cfg,
                options=YoutubeVisualsRunAllOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    story_id=str(getattr(args, "story_id", "") or "").strip(),
                    runpod_url=str(getattr(args, "runpod_url", "") or "").strip(),
                    workflow=str(getattr(args, "workflow", "") or "").strip(),
                    workers=int(getattr(args, "workers", 3) or 3),
                    limit=int(getattr(args, "limit", 0) or 0),
                    execute=bool(getattr(args, "execute", False)) and not bool(getattr(args, "dry_run", False)),
                    auto_gemini=bool(getattr(args, "auto_gemini", False)),
                    allow_gemini=bool(getattr(args, "allow_gemini", False)),
                    accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                    segment_sec=float(getattr(args, "segment_sec", 180.0) or 180.0),
                    prompt_runpod_url=not bool(getattr(args, "no_runpod_prompt", False)),
                    prompts_only=bool(getattr(args, "prompts_only", False)),
                ),
            )
            print("YOUTUBE_VISUALS_RUN_ALL", flush=True)
            print(f"launch_id: {result.get('launch_id')}", flush=True)
            print(f"execute: {result.get('execute')}", flush=True)
            print(f"selected: {result.get('selected_count')}", flush=True)
            print(f"processed: {result.get('processed_count')}", flush=True)
            print(f"skipped: {result.get('skipped_count')}", flush=True)
            print(f"promo_issues_accepted: {str(bool(result.get('promo_issues_accepted'))).lower()}", flush=True)
            print(f"stage_status_path: {result.get('stage_status_path')}", flush=True)
            for row in result.get("skipped", []) or []:
                print(f"skipped: {row.get('story_id')} -> {row.get('reason')} {row.get('exclude_reason') or ''}".rstrip(), flush=True)
            for row in result.get("stories", []) or []:
                print(
                    f"story: {row.get('story_id')} stage={row.get('stage')} "
                    f"status={row.get('status')} ok={row.get('ok')} next={row.get('next_action')}",
                    flush=True,
                )
            print(f"report_path: {result.get('report_path')}", flush=True)
            return 0 if result.get("ok") else 2

        if sub_cmd in {"gemini-workers-status", "gemini-workers-setup"}:
            options = YoutubeGeminiWorkersOptions(
                workers=int(getattr(args, "workers", 3) or 3),
                execute=bool(getattr(args, "execute", False)),
            )
            result = (
                run_youtube_gemini_workers_setup(config=cfg, options=options)
                if sub_cmd == "gemini-workers-setup"
                else run_youtube_gemini_workers_status(config=cfg, options=options)
            )
            print("FOUND_WORKER_CONFIGS:", flush=True)
            for row in result.get("found_worker_configs") or []:
                print(f"- path: {row.get('path')}", flush=True)
                print(f"  contains_emails: {str(bool(row.get('contains_emails'))).lower()}", flush=True)
                print(f"  contains_bots: {str(bool(row.get('contains_bots'))).lower()}", flush=True)
                print(f"  contains_worker_mapping: {str(bool(row.get('contains_worker_mapping'))).lower()}", flush=True)
                print(f"  selected_as_source_of_truth: {str(bool(row.get('selected_as_source_of_truth'))).lower()}", flush=True)
            print("GEMINI_WORKERS_STATUS", flush=True)
            print(f"source_of_truth: {result.get('source_of_truth')}", flush=True)
            print(f"execute: {str(bool(result.get('execute'))).lower()}", flush=True)
            print("worker | profile_dir | actual_email_marker | resolved_registry_email | bot_url | gem_id | mapping_ok", flush=True)
            for row in result.get("rows") or []:
                print(f"worker_{row.get('worker_id')}:", flush=True)
                print(f"  profile_dir: {row.get('profile_dir')}", flush=True)
                print(f"  actual_email_marker: {row.get('actual_email_marker')}", flush=True)
                print(f"  resolved_registry_email: {row.get('resolved_registry_email')}", flush=True)
                print(f"  bot_url: {row.get('bot_url')}", flush=True)
                print(f"  gem_id: {row.get('bot_url').split('/gem/', 1)[-1] if row.get('bot_url') else ''}", flush=True)
                print(f"  mapping_ok: {str(bool(row.get('mapping_ok'))).lower()}", flush=True)
                print(f"  cloned_profile: {str(bool(row.get('cloned_profile'))).lower()}", flush=True)
                print(f"  ready: {str(bool(row.get('ready'))).lower()}", flush=True)
                print(f"  blocker: {row.get('blocker')}", flush=True)
                print(
                    f"{row.get('worker_id')} | {row.get('profile_dir')} | {row.get('actual_email_marker')} | "
                    f"{row.get('resolved_registry_email')} | {row.get('bot_url')} | "
                    f"{row.get('bot_url').split('/gem/', 1)[-1] if row.get('bot_url') else ''} | "
                    f"{str(bool(row.get('mapping_ok'))).lower()}",
                    flush=True,
                )
            print(f"GEMINI_WORKERS_READY = {str(bool(result.get('ready'))).lower()}", flush=True)
            blockers = result.get("blockers") or result.get("setup_blockers") or []
            if blockers:
                print("BLOCKERS:", flush=True)
                for blocker in blockers:
                    print(f"- {blocker}", flush=True)
            changed = result.get("changed_files") or []
            if changed:
                print(f"changed_files={json.dumps(changed, ensure_ascii=True)}", flush=True)
            return 0 if result.get("ok") else 2

        if sub_cmd == "visual-prompts-audit":
            result = run_youtube_visual_prompts_audit(
                config=cfg,
                options=YoutubeVisualPromptsAuditOptions(story_id=str(args.story_id).strip()),
            )
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            diagnosis = result.get("diagnosis") if isinstance(result.get("diagnosis"), dict) else {}
            files = result.get("files") if isinstance(result.get("files"), dict) else {}
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"mode={result.get('mode')}")
            print(f"story_id={result.get('story_id')}")
            print(f"total_prompts={summary.get('total_prompts')}")
            print(f"avg_chars={summary.get('avg_chars')}")
            print(f"max_chars={summary.get('max_chars')}")
            print(f"avg_words={summary.get('avg_words')}")
            print(f"max_words={summary.get('max_words')}")
            print(f"prompts_over_1000_chars={summary.get('prompts_over_1000_chars')}")
            print(f"prompts_over_1500_chars={summary.get('prompts_over_1500_chars')}")
            print(f"prompts_over_2000_chars={summary.get('prompts_over_2000_chars')}")
            print(f"prompts_with_face_conflict={summary.get('prompts_with_face_conflict')}")
            print(f"prompts_with_beauty_bias={summary.get('prompts_with_beauty_bias')}")
            print(f"prompts_with_car_but_no_same_car={summary.get('prompts_with_car_but_no_same_car')}")
            print(f"prompts_with_character_but_no_same_character={summary.get('prompts_with_character_but_no_same_character')}")
            print(f"prompt_overload_risk={diagnosis.get('prompt_overload_risk')}")
            print(f"report_json={files.get('report_json_path')}")
            print(f"report_txt={files.get('report_txt_path')}")
            for p in result.get("missing", []) or []:
                print(f"missing={p}")
            return 0 if result.get("ok") else 2

        if sub_cmd == "characters-anchor-audit":
            result = run_youtube_characters_anchor_audit(
                config=cfg,
                options=YoutubeCharactersAnchorAuditOptions(story_id=str(args.story_id).strip()),
            )
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"story_id={result.get('story_id')}")
            print(f"characters_path={result.get('characters_path')}")
            print(f"style_name={result.get('style_name')}")
            print(f"characters_count={result.get('characters_count')}")
            print(f"invalid_anchors_count={result.get('invalid_anchors_count')}")
            print(f"forbidden_terms_total={result.get('forbidden_terms_total')}")
            for item in result.get("findings", []) or []:
                print(
                    "finding:"
                    f" id={item.get('id')}"
                    f" role={item.get('role')}"
                    f" forbidden_terms={json.dumps(item.get('forbidden_terms') or [], ensure_ascii=True)}"
                )
            for p in result.get("missing", []) or []:
                print(f"missing={p}")
            return 0 if result.get("status") != "missing_characters" else 2

        if sub_cmd == "frames-reset":
            result = run_youtube_frames_reset(
                config=cfg,
                options=YoutubeFramesResetOptions(
                    story_id=str(args.story_id).strip(),
                    reason=str(args.reason).strip(),
                    execute=bool(getattr(args, "execute", False)),
                ),
            )
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"reason={result.get('reason')}")
            print(f"archive_dir={result.get('archive_dir')}")
            print(f"expected_frames={result.get('expected_frames')}")
            print(f"archive_count={result.get('archive_count')}")
            for p in result.get("archive_candidates", []) or []:
                print(f"would_archive={p}")
            for p in result.get("archived_files", []) or []:
                print(f"archived={p}")
            if result.get("manifest_path"):
                print(f"manifest_path={result.get('manifest_path')}")
            if result.get("reset_report_path"):
                print(f"reset_report_path={result.get('reset_report_path')}")
            for p in result.get("missing", []) or []:
                print(f"missing={p}")
            return 0 if result.get("ok") else 2

        if sub_cmd == "visuals-clean":
            result = run_youtube_visuals_clean(
                config=cfg,
                options=YoutubeVisualsCleanOptions(
                    story_id=str(args.story_id).strip(),
                    execute=bool(getattr(args, "execute", False)),
                ),
            )
            print(f"status={result.get('status')}")
            print(f"ok={result.get('ok')}")
            print(f"mode={result.get('mode')}")
            print(f"story_id={result.get('story_id')}")
            print(f"quarantine_dir={result.get('quarantine_dir')}")
            print(f"removed_files_count={result.get('removed_files_count', 0)}")
            for item in result.get("cleanup_candidates", []) or []:
                if isinstance(item, dict):
                    print(f"would_clean={item.get('path')} reason={item.get('reason')}")
            for item in result.get("moved_files", []) or []:
                if isinstance(item, dict):
                    print(f"moved={item.get('source')} -> {item.get('target')}")
            for p in result.get("protected_paths", []) or []:
                print(f"protected={p}")
            verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
            for p in verification.get("still_exists", []) or []:
                print(f"verification_still_exists={p}")
            scan = result.get("stale_token_scan") if isinstance(result.get("stale_token_scan"), dict) else {}
            print(f"stale_token_findings_count={scan.get('findings_count', 0)}")
            for item in scan.get("findings", []) or []:
                if isinstance(item, dict):
                    print(
                        "stale_token:"
                        f" path={item.get('path')}"
                        f" line={item.get('line')}"
                        f" terms={json.dumps(item.get('terms') or [], ensure_ascii=True)}"
                    )
            for blocker in result.get("blockers", []) or []:
                print(f"blocker={blocker}")
            if result.get("report_path"):
                print(f"report_path={result.get('report_path')}")
            print(f"next_action={result.get('next_action')}")
            return 0 if result.get("ok") else 2

        if sub_cmd == "visuals-status":
            if str(getattr(args, "youtube_run_id", "") or "").strip() and not str(getattr(args, "story_id", "") or "").strip():
                result = run_youtube_visuals_launch_status(
                    config=cfg,
                    options=YoutubeVisualsStatusOptions(
                        youtube_run_id=str(args.youtube_run_id).strip(),
                        accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                    ),
                )
                summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
                print("YOUTUBE_VISUALS_STATUS")
                print(f"launch_id: {result.get('launch_id')}")
                print(f"total stories: {summary.get('total_stories', 0)}")
                print(f"total_tts_imported: {summary.get('total_tts_imported', 0)}")
                print(f"excluded_from_video: {summary.get('excluded_from_video', 0)}")
                print(f"ready_for_video: {summary.get('ready_for_video', 0)}")
                print(f"audio ready: {summary.get('audio_ready', 0)}")
                print(f"visual prompts ready: {summary.get('visual_prompts_ready', 0)}")
                prompts_summary = summary.get("prompts") if isinstance(summary.get("prompts"), dict) else {}
                if prompts_summary:
                    print("prompts:")
                    for key in ("done", "partial", "failed", "pending", "in_progress", "ready_for_runpod"):
                        print(f"  {key}: {prompts_summary.get(key, 0)}")
                print(f"images ready: {summary.get('images_ready', 0)}")
                print(f"blocked: {summary.get('blocked', 0)}")
                print(f"pending: {summary.get('pending', 0)}")
                print(f"ready_for_frames: {summary.get('ready_for_frames', 0)}")
                print(f"known promo issues accepted: {str(bool(summary.get('known_promo_issues_accepted'))).lower()}")
                print("stories:")
                print("story_id | title | characters | prompts_status | expected | actual | validation | blocker | next_action")
                for row in result.get("stories", []) or []:
                    print(
                        f"{row.get('story_id')} | {row.get('title')} | {row.get('characters_status', '')} | "
                        f"{row.get('prompts_status', '')} | {row.get('expected_prompts', '')} | "
                        f"{row.get('actual_prompts', '')} | {row.get('prompts_validation', '')} | "
                        f"{row.get('blocker', '')} | {row.get('next_action', '')}"
                    )
                print(f"report_path: {result.get('report_path')}")
                return 0 if result.get("ok") else 2

            result = run_youtube_visuals_status(
                config=cfg,
                options=YoutubeVisualsStatusOptions(
                    story_id=str(args.story_id).strip(),
                    youtube_run_id=str(getattr(args, "youtube_run_id", "") or "").strip(),
                    accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                ),
            )
            print(f"story_id={result.get('story_id')}")
            print(f"safe_story={result.get('safe_story')}")
            lang = result.get("language") if isinstance(result.get("language"), dict) else {}
            print(f"expected_language={lang.get('expected_language')}")
            print(f"source_language={lang.get('source_language')}")
            print(f"safe_story_language={lang.get('safe_story_language')}")
            print(f"safe_story_status={lang.get('safe_story_status')}")
            print(f"text_ready_for_audio_language={lang.get('text_ready_for_audio_language')}")
            promo = result.get("promo") if isinstance(result.get("promo"), dict) else {}
            print(f"promo_status={promo.get('status')}")
            print(f"promo_output_path={promo.get('output_path')}")
            print(f"promo_intro_inserted={promo.get('intro_inserted')}")
            print(f"promo_mid_inserted={promo.get('mid_inserted')}")
            print(f"promo_outro_inserted={promo.get('outro_inserted')}")
            narration = result.get("narration") if isinstance(result.get("narration"), dict) else {}
            print(f"narration_status={narration.get('status')}")
            print(f"narration_stale={narration.get('stale')}")
            print(f"narration_duration_sec={narration.get('duration_sec')}")
            chars = result.get("characters") if isinstance(result.get("characters"), dict) else {}
            prompts = result.get("prompts") if isinstance(result.get("prompts"), dict) else {}
            frames = result.get("frames") if isinstance(result.get("frames"), dict) else {}
            video_segments = result.get("video_segments") if isinstance(result.get("video_segments"), dict) else {}
            print(f"characters_status={chars.get('status')}")
            print(f"characters_path={chars.get('path')}")
            anchor_audit = chars.get("anchor_audit") if isinstance(chars.get("anchor_audit"), dict) else {}
            print(f"characters_anchor_status={anchor_audit.get('status')}")
            print(f"characters_anchor_invalid_count={anchor_audit.get('invalid_anchors_count')}")
            print(f"characters_anchor_forbidden_terms_total={anchor_audit.get('forbidden_terms_total')}")
            print(f"prompts_status={prompts.get('status')}")
            print(f"prompts_count={prompts.get('prompts_count')}")
            print(f"estimated_prompts={prompts.get('estimated_prompts')}")
            print(f"prompt_mode_available={json.dumps(prompts.get('prompt_mode_available') or {}, ensure_ascii=True)}")
            print(f"available_prompt_modes={json.dumps(prompts.get('available_prompt_modes') or [], ensure_ascii=True)}")
            print(f"recommended_prompt_mode={prompts.get('recommended_prompt_mode')}")
            print(f"frames_expected={frames.get('expected')}")
            print(f"frames_status={frames.get('status')}")
            print(f"frames_reason={frames.get('reason')}")
            print(f"frames_archived_to={frames.get('archived_to')}")
            print(f"frames_existing={frames.get('existing')}")
            print(f"frames_valid={frames.get('valid')}")
            print(f"frames_missing={frames.get('missing')}")
            print(f"frames_failed={frames.get('failed')}")
            print(f"video_timeline_exists={video_segments.get('timeline_exists')}")
            print(f"segment_jobs_exists={video_segments.get('segment_jobs_exists')}")
            print(f"total_segments={video_segments.get('total_segments')}")
            print(f"current_blocker={result.get('current_blocker')}")
            print(f"next_action={result.get('next_action')}")
            reports = result.get("reports") if isinstance(result.get("reports"), dict) else {}
            print(f"status_report={reports.get('status_report')}")
            print(f"run_report={reports.get('run_report')}")
            print(f"frames_report={reports.get('frames_report')}")
            return 0

        if sub_cmd == "production-path-repair":
            from orchestrator.youtube_launch_path_ops import run_youtube_production_path_repair

            launch_id = str(args.youtube_run_id).strip()
            result = run_youtube_production_path_repair(
                config=cfg,
                youtube_run_id=launch_id,
                execute_recovery=bool(args.execute),
            )
            if not result.get("ok", False):
                print(result.get("message", "production path repair failed"))
                return 2
            print("YOUTUBE_PRODUCTION_PATH_REPAIR")
            print(f"forensic_json={result.get('forensic_reports', {}).get('json')}")
            print(f"recovery_json={result.get('recovery_reports', {}).get('json')}")
            print(f"readiness_json={result.get('readiness_reports', {}).get('json')}")
            recovery = result.get("recovery") if isinstance(result.get("recovery"), dict) else {}
            readiness = result.get("readiness") if isinstance(result.get("readiness"), dict) else {}
            print(f"imported={recovery.get('imported_count', 0)} rejected={recovery.get('rejected_count', 0)}")
            summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
            print(
                f"launch_only ready={summary.get('ready', 0)} pending={summary.get('pending', 0)} "
                f"partial={summary.get('partial', 0)} failed={summary.get('failed', 0)} "
                f"legacy_only_ignored={summary.get('legacy_only_ignored', 0)} "
                f"ready_for_runpod={summary.get('ready_for_runpod', 0)}"
            )
            return 0

        if sub_cmd == "prompts-temp-import-repair":
            result = run_youtube_prompts_temp_import_repair(
                config=cfg,
                options=YoutubePromptsTempImportRepairOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    run_session_id=str(getattr(args, "run_session_id", "") or "").strip(),
                    execute=bool(getattr(args, "execute", False)),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "prompts temp import repair failed"))
                return 2
            print("PROMPTS_TEMP_IMPORT_REPAIR")
            print(f"json_report={result.get('reports', {}).get('json')}")
            print(f"md_report={result.get('reports', {}).get('md')}")
            print(f"temp_sessions={len(result.get('temp_sessions_found') or [])}")
            print(f"imported={result.get('imported_count', 0)}")
            print(f"rejected={result.get('rejected_count', 0)}")
            final_readiness = result.get("final_readiness") if isinstance(result.get("final_readiness"), dict) else {}
            print(
                f"ready_for_runpod={final_readiness.get('ready_for_runpod', 0)} "
                f"blocked={final_readiness.get('blocked', 0)} "
                f"pending={final_readiness.get('pending', 0)} "
                f"failed={final_readiness.get('failed', 0)} "
                f"next_stage_allowed={str(bool(final_readiness.get('next_stage_allowed'))).lower()}"
            )
            return 0

        if sub_cmd == "prompts-targeted-repair":
            result = run_youtube_prompts_targeted_repair(
                config=cfg,
                options=YoutubePromptsTargetedRepairOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    story_ids=list(getattr(args, "story_id", None) or []),
                    workers=max(1, int(getattr(args, "workers", 1) or 1)),
                    preferred_session_id=str(getattr(args, "preferred_session_id", "") or "").strip(),
                    accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                    execute=bool(getattr(args, "execute", False)),
                ),
            )
            if not result.get("ok", False) and result.get("message"):
                print(result.get("message", "prompts targeted repair failed"))
                return 2
            print("PROMPTS_TARGETED_REPAIR")
            print(f"json_report={result.get('reports', {}).get('json')}")
            print(f"md_report={result.get('reports', {}).get('md')}")
            for row in result.get("stories", []):
                forensic = row.get("forensic") if isinstance(row.get("forensic"), dict) else {}
                print(
                    f"- {row.get('story_id')}: worker={row.get('assigned_worker')} "
                    f"sessions={','.join(forensic.get('sessions_with_stage_dir') or [])} "
                    f"temp_final={forensic.get('temp_prompts_list_found')} "
                    f"temp_partial={forensic.get('temp_partial_found')} "
                    f"raw_gemini={forensic.get('raw_gemini_response_found')} "
                    f"reason={row.get('why_no_canonical_prompts_list')}"
                )
            final_readiness = result.get("final_readiness") if isinstance(result.get("final_readiness"), dict) else {}
            print(
                f"selected_not_ready={final_readiness.get('selected_not_ready', 0)} "
                f"ready_for_runpod={final_readiness.get('ready_for_runpod', 0)} "
                f"next_stage_allowed={str(bool(final_readiness.get('next_stage_allowed'))).lower()}"
            )
            return 0 if result.get("ok", False) else 1

        if sub_cmd == "prompts-resume-audit":
            result = run_youtube_prompts_resume_audit(
                config=cfg,
                options=YoutubePromptsResumeAuditOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "prompts resume audit failed"))
                return 2
            print("PROMPTS_RESUME_AUDIT")
            print(f"total active stories: {result.get('total_active_stories')}")
            print(f"prompts done valid: {result.get('prompts_done_valid')}")
            print(f"prompts partial: {result.get('prompts_partial')}")
            print(f"prompts missing: {result.get('prompts_missing')}")
            print(f"prompts invalid: {result.get('prompts_invalid')}")
            print(f"ready_for_runpod: {result.get('ready_for_runpod')}")
            print(f"resume_safe: {str(bool(result.get('resume_safe'))).lower()}")
            print(f"runpod_safe: {str(bool(result.get('runpod_safe'))).lower()}")
            reports = result.get("reports") if isinstance(result.get("reports"), dict) else {}
            print(f"json_report={reports.get('json')}")
            print(f"md_report={reports.get('md')}")
            return 0

        if sub_cmd == "prompts-progress-status":
            result = run_youtube_prompts_progress_status(
                config=cfg,
                options=YoutubePromptsProgressStatusOptions(
                    youtube_run_id=str(args.youtube_run_id).strip(),
                    run_session_id=str(getattr(args, "run_session_id", "") or "").strip(),
                    accept_known_promo_issues=bool(getattr(args, "accept_known_promo_issues", False)),
                ),
            )
            if not result.get("ok", False):
                print(result.get("message", "prompts progress status failed"))
                return 2
            print("PROMPTS_PROGRESS_STATUS")
            print(f"launch_id: {result.get('launch_id')}")
            print(f"session: {result.get('run_session_id')}")
            print(f"active_queue: {result.get('active_queue', 0)}")
            print(f"done_valid: {result.get('done', 0)}")
            print(f"partial: {result.get('partial', 0)}")
            print(f"in_progress_fresh: {result.get('in_progress', 0)}")
            print(f"stale_in_progress: 0")
            print(f"failed: {result.get('failed', 0)}")
            print(f"pending: {result.get('pending', 0)}")
            print(f"remaining: {result.get('remaining', 0)}")
            print(f"ready_for_runpod: {result.get('ready_for_runpod', 0)}")
            print(f"not_ready_for_runpod: {result.get('not_ready_for_runpod', 0)}")
            print("story_id | title | worker | status | chunk | prompts | validation | blocker | next_action")
            for row in result.get("stories_list", []) or []:
                print(
                    f"{row.get('story_id')} | {row.get('title')} | {row.get('assigned_worker', '')} | "
                    f"{row.get('status')} | {row.get('current_chunk', 0)}/{row.get('total_chunks', 0)} | "
                    f"{row.get('actual_prompts', 0)}/{row.get('expected_prompts', 0)} | "
                    f"{row.get('validation', '')} | {row.get('blocker', '')} | {row.get('next_action', '')}"
                )
            return 0

        if sub_cmd == "stage":
            stage_cmd = str(getattr(args, "youtube_stage_cmd", "") or "").strip()
            if stage_cmd == "set":
                result = set_launch_stage(
                    cfg,
                    launch_id=str(args.youtube_run_id).strip(),
                    stage=str(args.stage).strip(),
                    execute=bool(getattr(args, "execute", False)),
                    reason="manual stage set via CLI",
                )
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"youtube_run_id={result.get('youtube_run_id')}")
                print(f"stage={result.get('stage')}")
                print(f"stage_status_path={result.get('stage_status_path')}")
                return 0 if result.get("ok") else 2

        if sub_cmd == "exclude-from-video":
            result = mark_story_excluded_from_video(
                cfg,
                launch_id=str(args.youtube_run_id).strip(),
                story_id=str(args.story_id).strip(),
                reason=str(getattr(args, "reason", "") or "too_short_story_user_rejected").strip(),
                execute=bool(getattr(args, "execute", False)),
            )
            print(f"ok={result.get('ok')}")
            print(f"execute={result.get('execute')}")
            print(f"story_id={result.get('story_id')}")
            print(f"excluded_from_video={result.get('excluded_from_video')}")
            print(f"exclude_reason={result.get('exclude_reason')}")
            print(f"manifest_path={result.get('manifest_path')}")
            return 0 if result.get("ok") else 2

        if sub_cmd == "video":
            video_sub = str(getattr(args, "youtube_video_cmd", "") or "").strip()
            if video_sub == "prepare-segments":
                result = run_youtube_video_prepare_segments(
                    config=cfg,
                    options=YoutubeVideoPrepareSegmentsOptions(
                        story_id=str(args.story_id).strip(),
                        segment_sec=float(args.segment_sec or 180.0),
                        execute=bool(args.execute),
                        force=bool(getattr(args, "force", False)),
                    ),
                )
                if not result.get("ok", False):
                    print(result.get("message", "youtube video prepare-segments failed"))
                    return 2
                print(f"status={result.get('status')}")
                print(f"execute={result.get('execute')}")
                print(f"story_id={result.get('story_id')}")
                print(f"story_dir={result.get('story_dir')}")
                print(f"audio_path={result.get('audio_path')}")
                print(f"audio_duration_sec={result.get('audio_duration_sec')}")
                print(f"frames_dir={result.get('frames_dir')}")
                print(f"total_frames={result.get('total_frames')}")
                print(f"segment_sec={result.get('segment_sec')}")
                print(f"segment_target_sec={result.get('segment_target_sec')}")
                print(f"segment_boundary_policy={result.get('segment_boundary_policy')}")
                print(f"render_mode={result.get('render_mode')}")
                print(f"total_segments={result.get('total_segments')}")
                print(f"total_segment_duration_sec={result.get('total_segment_duration_sec')}")
                print(f"timeline_path={result.get('timeline_path')}")
                print(f"segment_jobs_path={result.get('segment_jobs_path')}")
                print(f"segments_dir={result.get('segments_dir')}")
                for row in result.get("first_5_segments", [])[:5]:
                    print(
                        "segment:"
                        f" id={row.get('segment_id')}"
                        f" start={row.get('start_sec')}"
                        f" end={row.get('end_sec')}"
                        f" duration={row.get('duration_sec')}"
                        f" render_mode={row.get('render_mode')}"
                        f" frame_range={row.get('frame_start_index')}-{row.get('frame_end_index')}"
                        f" frames={len(row.get('frames') or [])}"
                    )
                return 0
            if video_sub == "render-segment":
                result = run_youtube_video_render_segment(
                    config=cfg,
                    options=YoutubeVideoRenderSegmentOptions(
                        story_id=str(args.story_id).strip(),
                        segment_id=str(args.segment_id).strip(),
                        execute=bool(args.execute),
                    ),
                )
                if not result.get("ok", False):
                    print(result.get("message", "youtube video render-segment failed"))
                    if result.get("render_log_path"):
                        print(f"render_log_path={result.get('render_log_path')}")
                    if result.get("report_path"):
                        print(f"report_path={result.get('report_path')}")
                    if result.get("partial_output_path"):
                        print(f"partial_output_path={result.get('partial_output_path')}")
                    return 2
                print(f"status={result.get('status')}")
                print(f"execute={result.get('execute')}")
                print(f"story_id={result.get('story_id')}")
                print(f"segment_id={result.get('segment_id')}")
                print(f"start_sec={result.get('start_sec')}")
                print(f"end_sec={result.get('end_sec')}")
                print(f"duration_sec={result.get('duration_sec')}")
                print(f"render_mode={result.get('render_mode')}")
                print(f"audio_required={result.get('audio_required')}")
                print(f"frames_count={result.get('frames_count')}")
                print(f"output_segment_path={result.get('output_segment_path')}")
                print(f"partial_output_path={result.get('partial_output_path')}")
                if result.get("render_log_path"):
                    print(f"render_log_path={result.get('render_log_path')}")
                if result.get("report_path"):
                    print(f"report_path={result.get('report_path')}")
                validation = result.get("validation")
                if validation:
                    print(f"validation={json.dumps(validation, ensure_ascii=True)}")
                return 0
            if video_sub == "segment-status":
                result = run_youtube_video_segment_status(
                    config=cfg,
                    options=YoutubeVideoSegmentStatusOptions(story_id=str(args.story_id).strip()),
                )
                if not result.get("ok", False):
                    print(result.get("message", "youtube video segment-status failed"))
                    if result.get("segment_jobs_path"):
                        print(f"segment_jobs_path={result.get('segment_jobs_path')}")
                    return 2
                print(f"story_id={result.get('story_id')}")
                print(f"audio_duration_sec={result.get('audio_duration_sec')}")
                print(f"total_segments={result.get('total_segments')}")
                print(f"done_segments={result.get('done_segments')}")
                print(f"pending_segments={result.get('pending_segments')}")
                print(f"failed_segments={result.get('failed_segments')}")
                print(f"missing_segments={result.get('missing_segments')}")
                print(f"invalid_segments={result.get('invalid_segments')}")
                print(f"total_segment_duration_sec={result.get('total_segment_duration_sec')}")
                print(f"first_10_pending={json.dumps(result.get('first_10_pending') or [], ensure_ascii=True)}")
                print(f"first_10_invalid={json.dumps(result.get('first_10_invalid') or [], ensure_ascii=True)}")
                print(f"segment_jobs_path={result.get('segment_jobs_path')}")
                return 0
            if video_sub == "export-job":
                result = run_youtube_video_export_job(
                    config=cfg,
                    options=YoutubeVideoExportJobOptions(
                        story_id=str(args.story_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                        force=bool(getattr(args, "force", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"story_id={result.get('story_id')}")
                print(f"story_slug={result.get('story_slug')}")
                print(f"drive_job_root={result.get('drive_job_root')}")
                print(f"expected_frames={result.get('expected_frames')}")
                print(f"frames_count={result.get('frames_count')}")
                print(f"total_segments={result.get('total_segments')}")
                print(f"effects_found={result.get('effects_found')}")
                print(f"missing_effects={json.dumps(result.get('missing_effects') or [], ensure_ascii=True)}")
                print(f"ready_marker={result.get('ready_marker')}")
                print(f"report_path={result.get('report_path')}")
                for p in result.get("missing", []) or []:
                    print(f"missing={p}")
                for cmd in result.get("worker_commands", []) or []:
                    print(f"worker_command={cmd}")
                return 0 if result.get("ok") else 2
            if video_sub == "drive-status":
                result = run_youtube_video_drive_status(
                    config=cfg,
                    options=YoutubeVideoDriveStatusOptions(story_id=str(args.story_id).strip()),
                )
                print(f"status={result.get('status')}")
                print(f"job_ready={result.get('job_ready')}")
                print(f"drive_job_root={result.get('drive_job_root')}")
                print(f"expected_segments={result.get('expected_segments')}")
                print(f"global_pending={result.get('global_pending')}")
                print(f"assigned_pending_by_worker={json.dumps(result.get('assigned_pending_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_processing_by_worker={json.dumps(result.get('assigned_processing_by_worker') or {}, ensure_ascii=True)}")
                print(f"segments_done_count={result.get('segments_done_count')}")
                print(f"duplicate_assigned_segments={json.dumps(result.get('duplicate_assigned_segments') or [], ensure_ascii=True)}")
                print(f"duplicate_processing_segments={json.dumps(result.get('duplicate_processing_segments') or [], ensure_ascii=True)}")
                print(f"workers={json.dumps(result.get('workers') or [], ensure_ascii=True)}")
                print(f"worker_details={json.dumps(result.get('worker_details') or {}, ensure_ascii=True)}")
                print(f"can_import={result.get('can_import')}")
                print(f"can_assemble={result.get('can_assemble')}")
                print(f"report_path={result.get('report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "setup-colab-workers":
                result = run_youtube_video_setup_colab_workers(
                    config=cfg,
                    options=YoutubeVideoSetupColabWorkersOptions(
                        story_id=str(args.story_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                        youtube_folder_id=str(getattr(args, "youtube_folder_id", "") or "").strip(),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"root_worker_script_exists={result.get('root_worker_script_exists')}")
                print(f"root_worker_script={result.get('root_worker_script')}")
                print(f"root_bootstrap_script_exists={result.get('root_bootstrap_script_exists')}")
                print(f"root_bootstrap_script={result.get('root_bootstrap_script')}")
                print(f"colab_bootstrap_cell_path={result.get('colab_bootstrap_cell_path')}")
                print(f"youtube_folder_id_set={result.get('youtube_folder_id_set')}")
                print(f"root_compat_queue_exists={result.get('root_compat_queue_exists')}")
                print(f"root_compat_queue={result.get('root_compat_queue')}")
                migration = result.get("migration") if isinstance(result.get("migration"), dict) else {}
                print(f"migrated_legacy_pending={migration.get('migrated_legacy_pending')}")
                print(f"legacy_pending_left={json.dumps(migration.get('legacy_pending_left') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "colab-browser-profiles":
                result = run_youtube_video_colab_browser_profiles(
                    config=cfg,
                    options=YoutubeVideoColabBrowserProfilesOptions(
                        config_path=Path(str(getattr(args, "config_path", "configs/youtube_video_colab_workers.yaml"))),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"config_path={result.get('config_path')}")
                print(f"chrome={json.dumps(result.get('chrome') or {}, ensure_ascii=True)}")
                print(f"yandex={json.dumps(result.get('yandex') or {}, ensure_ascii=True)}")
                print(f"manual_action_required={json.dumps(result.get('manual_action_required') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "workers-audit":
                result = run_youtube_video_workers_audit(
                    config=cfg,
                    options=YoutubeVideoWorkersAuditOptions(
                        story_id=str(args.story_id).strip(),
                        config_path=Path(str(getattr(args, "config_path", "configs/youtube_video_colab_workers.yaml"))),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"story_id={result.get('story_id')}")
                print(f"drive_job_root={result.get('drive_job_root')}")
                print(f"job_ready={result.get('job_ready')}")
                print(f"workers_in_launcher_count={result.get('workers_in_launcher_count')}")
                print(f"workers_in_render_queue_config_count={result.get('workers_in_render_queue_config_count')}")
                print(f"missing_in_render_config={json.dumps(result.get('missing_in_render_config') or [], ensure_ascii=True)}")
                print(f"extra_in_render_config={json.dumps(result.get('extra_in_render_config') or [], ensure_ascii=True)}")
                print(f"mismatch_count={result.get('mismatch_count')}")
                print(f"assigned_dirs_complete={result.get('assigned_dirs_complete')}")
                print(f"missing_assigned_dirs={json.dumps(result.get('missing_assigned_dirs') or [], ensure_ascii=True)}")
                print(f"missing_heartbeat={json.dumps(result.get('missing_heartbeat') or [], ensure_ascii=True)}")
                print(f"workers={json.dumps(result.get('workers') or [], ensure_ascii=True)}")
                print(f"single_worker_command={result.get('single_worker_command')}")
                print(f"five_yandex_workers_command={result.get('five_yandex_workers_command')}")
                print(f"ten_workers_command={result.get('ten_workers_command')}")
                print(f"queue_status_command={result.get('queue_status_command')}")
                print(f"report_path={result.get('report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "dispatch-segments":
                result = run_youtube_video_dispatch_segments(
                    config=cfg,
                    options=YoutubeVideoDispatchSegmentsOptions(
                        story_id=str(args.story_id).strip(),
                        workers=str(getattr(args, "workers", "") or ""),
                        target_per_worker=int(getattr(args, "target_per_worker", 1)),
                        max_total_assigned=int(getattr(args, "max_total_assigned", 5)),
                        execute=bool(getattr(args, "execute", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"assigned_count={result.get('assigned_count')}")
                print(f"assignments={json.dumps(result.get('assignments') or [], ensure_ascii=True)}")
                queue_status = result.get("queue_status") if isinstance(result.get("queue_status"), dict) else {}
                print(f"global_pending={queue_status.get('global_pending')}")
                print(f"assigned_pending_by_worker={json.dumps(queue_status.get('assigned_pending_by_worker') or {}, ensure_ascii=True)}")
                print(f"duplicate_assigned_segments={json.dumps(queue_status.get('duplicate_assigned_segments') or [], ensure_ascii=True)}")
                print(f"duplicate_processing_segments={json.dumps(queue_status.get('duplicate_processing_segments') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "reclaim-stale-segments":
                result = run_youtube_video_reclaim_stale_segments(
                    config=cfg,
                    options=YoutubeVideoReclaimStaleSegmentsOptions(
                        story_id=str(args.story_id).strip(),
                        stale_minutes=int(getattr(args, "stale_minutes", 10)),
                        max_attempts=int(getattr(args, "max_attempts", 3)),
                        execute=bool(getattr(args, "execute", False)),
                        dry_run=bool(getattr(args, "dry_run", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"dry_run={result.get('dry_run')}")
                print(f"stale_minutes={result.get('stale_minutes')}")
                print(f"max_attempts={result.get('max_attempts')}")
                print(f"scanned_workers_count={result.get('scanned_workers_count')}")
                print(f"scanned_workers={json.dumps(result.get('scanned_workers') or [], ensure_ascii=True)}")
                print(f"scanned_processing_segments={result.get('scanned_processing_segments')}")
                print(f"reclaimed_count={result.get('reclaimed_count')}")
                print(f"moved_to_failed_count={result.get('moved_to_failed_count')}")
                print(f"marked_done_count={result.get('marked_done_count')}")
                print(f"skipped_count={result.get('skipped_count')}")
                print(f"reclaimed={json.dumps(result.get('reclaimed') or [], ensure_ascii=True)}")
                print(f"moved_to_failed={json.dumps(result.get('moved_to_failed') or [], ensure_ascii=True)}")
                print(f"marked_done={json.dumps(result.get('marked_done') or [], ensure_ascii=True)}")
                print(f"fresh_processing={json.dumps(result.get('fresh_processing') or [], ensure_ascii=True)}")
                print(f"details={json.dumps(result.get('details') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "queue-status":
                result = run_youtube_video_queue_status(
                    config=cfg,
                    options=YoutubeVideoQueueStatusOptions(
                        story_id=str(args.story_id).strip(),
                        stale_minutes=int(getattr(args, "stale_minutes", 10)),
                        quick=bool(getattr(args, "quick", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"job_ready={result.get('job_ready')}")
                print(f"drive_job_root={result.get('drive_job_root')}")
                print(f"stale_minutes_threshold={result.get('stale_minutes_threshold')}")
                print(f"worker_count={result.get('worker_count')}")
                print(f"active_worker_count={result.get('active_worker_count')}")
                print(f"active_workers={json.dumps(result.get('active_workers') or [], ensure_ascii=True)}")
                print(f"offline_workers={json.dumps(result.get('offline_workers') or [], ensure_ascii=True)}")
                print(f"idle_worker_count={result.get('idle_worker_count')}")
                print(f"idle_workers={json.dumps(result.get('idle_workers') or [], ensure_ascii=True)}")
                print(f"workers_with_assigned_pending_count={result.get('workers_with_assigned_pending_count')}")
                print(f"workers_with_assigned_pending={json.dumps(result.get('workers_with_assigned_pending') or [], ensure_ascii=True)}")
                print(f"workers_exited_by_idle_timeout_count={result.get('workers_exited_by_idle_timeout_count')}")
                print(f"workers_exited_by_idle_timeout={json.dumps(result.get('workers_exited_by_idle_timeout') or [], ensure_ascii=True)}")
                print(f"global_pending={result.get('global_pending')}")
                print(f"assigned_pending_by_worker={json.dumps(result.get('assigned_pending_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_processing_by_worker={json.dumps(result.get('assigned_processing_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_done_by_worker={json.dumps(result.get('assigned_done_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_failed_by_worker={json.dumps(result.get('assigned_failed_by_worker') or {}, ensure_ascii=True)}")
                print(f"stale_processing_by_worker={json.dumps(result.get('stale_processing_by_worker') or {}, ensure_ascii=True)}")
                print(f"stale_processing_count={result.get('stale_processing_count')}")
                print(f"stale_processing_segments={json.dumps(result.get('stale_processing_segments') or [], ensure_ascii=True)}")
                print(f"total_segments={result.get('total_segments')}")
                print(f"segments_done_count={result.get('segments_done_count')}")
                print(f"final_marker_count={result.get('final_marker_count')}")
                print(f"checkpointed_segments_count={result.get('checkpointed_segments_count')}")
                print(f"partial_segments_count={result.get('partial_segments_count')}")
                print(f"asset_preflight_ok={result.get('asset_preflight_ok')}")
                print(f"asset_preflight_included={result.get('asset_preflight_included')}")
                print(f"missing_asset_segments_count={result.get('missing_asset_segments_count')}")
                print(f"missing_assets_count={result.get('missing_assets_count')}")
                print(f"missing_asset_segments={json.dumps(result.get('missing_asset_segments') or [], ensure_ascii=True)}")
                print(f"permanent_failed_count={result.get('permanent_failed_count')}")
                print(f"assigned_pending_total={result.get('assigned_pending_total')}")
                print(f"assigned_processing_total={result.get('assigned_processing_total')}")
                print(f"assigned_failed_total={result.get('assigned_failed_total')}")
                print(f"checkpoints_per_segment={json.dumps(result.get('checkpoints_per_segment') or [], ensure_ascii=True)}")
                print(f"duplicate_assigned_segments={json.dumps(result.get('duplicate_assigned_segments') or [], ensure_ascii=True)}")
                print(f"duplicate_processing_segments={json.dumps(result.get('duplicate_processing_segments') or [], ensure_ascii=True)}")
                print(f"warnings={json.dumps(result.get('warnings') or [], ensure_ascii=True)}")
                print(f"workers={json.dumps(result.get('workers') or [], ensure_ascii=True)}")
                print(f"worker_details={json.dumps(result.get('worker_details') or {}, ensure_ascii=True)}")
                print(f"can_import={result.get('can_import')}")
                print(f"can_assemble={result.get('can_assemble')}")
                print(f"report_path={result.get('report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "validate-job-assets":
                result = run_youtube_video_validate_job_assets(
                    config=cfg,
                    options=YoutubeVideoValidateJobAssetsOptions(
                        story_id=str(args.story_id).strip(),
                        dry_run=bool(getattr(args, "dry_run", False)),
                    ),
                )
                print(f"ok={result.get('ok')}")
                print(f"status={result.get('status')}")
                print(f"total_segments={result.get('total_segments')}")
                print(f"total_required_frames={result.get('total_required_frames')}")
                print(f"unique_required_frames={result.get('unique_required_frames')}")
                print(f"existing_frames_count={result.get('existing_frames_count')}")
                print(f"missing_frames_count={result.get('missing_frames_count')}")
                print(f"missing_asset_segments_count={result.get('missing_asset_segments_count')}")
                print(f"missing_frames={json.dumps(result.get('missing_frames') or [], ensure_ascii=True)}")
                print(f"missing_by_segment={json.dumps(result.get('missing_by_segment') or {}, ensure_ascii=True)}")
                print(f"assets_frames_dir={result.get('assets_frames_dir')}")
                print(f"input_frames_dir={result.get('input_frames_dir')}")
                print(f"report_path={result.get('report_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "cleanup-partial-checkpoints":
                result = run_youtube_video_cleanup_partial_checkpoints(
                    config=cfg,
                    options=YoutubeVideoCleanupPartialOptions(
                        story_id=str(args.story_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                        dry_run=bool(getattr(args, "dry_run", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"execute={result.get('execute')}")
                print(f"dry_run={result.get('dry_run')}")
                print(f"scanned_segments={result.get('scanned_segments')}")
                print(f"actions_total={result.get('actions_total')}")
                print(f"deleted_total={result.get('deleted_total')}")
                print(f"per_segment={json.dumps(result.get('per_segment') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "watch-queue":
                result = run_youtube_video_watch_queue(
                    config=cfg,
                    options=YoutubeVideoWatchQueueOptions(
                        story_id=str(args.story_id).strip(),
                        poll_seconds=int(getattr(args, "poll_seconds", 60)),
                        stale_minutes=int(getattr(args, "stale_minutes", 10)),
                        max_attempts=int(getattr(args, "max_attempts", 3)),
                        pending_per_worker=int(getattr(args, "pending_per_worker", 1)),
                        max_total_assigned=int(getattr(args, "max_total_assigned", 0)),
                        workers=str(getattr(args, "workers", "") or ""),
                        execute=bool(getattr(args, "execute", False)),
                        dry_run=bool(getattr(args, "dry_run", False)),
                        once=bool(getattr(args, "once", False)),
                        max_runtime_minutes=float(getattr(args, "max_runtime_minutes", 0.0) or 0.0),
                        auto_import_on_complete=not bool(getattr(args, "no_auto_import", False)),
                        skip_asset_preflight=bool(getattr(args, "skip_asset_preflight", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"stop_reason={result.get('stop_reason')}")
                print(f"execute={result.get('execute')}")
                print(f"dry_run={result.get('dry_run')}")
                print(f"once={result.get('once')}")
                print(f"interrupted={result.get('interrupted')}")
                print(f"ticks={result.get('ticks')}")
                print(f"runtime_seconds={result.get('runtime_seconds')}")
                print(f"totals={json.dumps(result.get('totals') or {}, ensure_ascii=True)}")
                last_status = result.get("last_status") if isinstance(result.get("last_status"), dict) else {}
                print(f"global_pending={last_status.get('global_pending')}")
                print(f"assigned_pending_by_worker={json.dumps(last_status.get('assigned_pending_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_processing_by_worker={json.dumps(last_status.get('assigned_processing_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_done_by_worker={json.dumps(last_status.get('assigned_done_by_worker') or {}, ensure_ascii=True)}")
                print(f"assigned_failed_by_worker={json.dumps(last_status.get('assigned_failed_by_worker') or {}, ensure_ascii=True)}")
                print(f"stale_processing_by_worker={json.dumps(last_status.get('stale_processing_by_worker') or {}, ensure_ascii=True)}")
                print(f"stale_processing_count={last_status.get('stale_processing_count')}")
                print(f"total_segments={last_status.get('total_segments')}")
                print(f"segments_done_count={last_status.get('segments_done_count')}")
                print(f"expected_segments={last_status.get('expected_segments')}")
                print(f"checkpointed_segments_count={last_status.get('checkpointed_segments_count')}")
                print(f"partial_segments_count={last_status.get('partial_segments_count')}")
                print(f"final_marker_count={last_status.get('final_marker_count')}")
                print(f"asset_preflight_ok={last_status.get('asset_preflight_ok')}")
                print(f"missing_asset_segments_count={last_status.get('missing_asset_segments_count')}")
                print(f"missing_assets_count={last_status.get('missing_assets_count')}")
                print(f"permanent_failed_count={last_status.get('permanent_failed_count')}")
                print(f"can_import={last_status.get('can_import')}")
                print(f"can_assemble={last_status.get('can_assemble')}")
                print(f"warnings={json.dumps(last_status.get('warnings') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                print(f"local_events_path={result.get('local_events_path')}")
                print(f"drive_events_path={result.get('drive_events_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "colab-supervisor":
                result = run_youtube_video_colab_supervisor(
                    config=cfg,
                    options=YoutubeVideoColabSupervisorOptions(
                        story_id=str(args.story_id).strip(),
                        workers=str(getattr(args, "workers", "") or ""),
                        poll_seconds=int(getattr(args, "poll_seconds", 60)),
                        stale_minutes=int(getattr(args, "stale_minutes", 10)),
                        cooldown_minutes=int(getattr(args, "cooldown_minutes", 7)),
                        wait_after_open_seconds=int(getattr(args, "wait_after_open_seconds", 45)),
                        wait_for_run_start_seconds=int(getattr(args, "wait_for_run_start_seconds", 120)),
                        heartbeat_wait_seconds=int(getattr(args, "heartbeat_wait_seconds", 180)),
                        colab_config_path=Path(getattr(args, "config_path", Path("configs/youtube_video_colab_workers.yaml"))),
                        execute=bool(getattr(args, "execute", False)),
                        dry_run=bool(getattr(args, "dry_run", False)),
                        once=bool(getattr(args, "once", False)),
                        max_runtime_minutes=float(getattr(args, "max_runtime_minutes", 0.0) or 0.0),
                        auto_run=not bool(getattr(args, "no_auto_run", False)),
                        autorun_mode=str(getattr(args, "autorun_mode", "browser-tab") or "browser-tab"),
                        reuse_profile_window=not bool(getattr(args, "new_window", False)),
                    ),
                )
                launch_reports = result.get("worker_launch_reports") if isinstance(result.get("worker_launch_reports"), dict) else {}
                for worker_email, launch_report in launch_reports.items():
                    if not isinstance(launch_report, dict):
                        continue
                    print(f"launch_report.{worker_email}={json.dumps(launch_report, ensure_ascii=True)}")
                if not launch_reports:
                    print("launch_report=none (no relaunch this tick)")
                print(f"status={result.get('status')}")
                print(f"stop_reason={result.get('stop_reason')}")
                print(f"execute={result.get('execute')}")
                print(f"dry_run={result.get('dry_run')}")
                print(f"once={result.get('once')}")
                print(f"interrupted={result.get('interrupted')}")
                print(f"ticks={result.get('ticks')}")
                print(f"runtime_seconds={result.get('runtime_seconds')}")
                print(f"totals={json.dumps(result.get('totals') or {}, ensure_ascii=True)}")
                last_status = result.get("last_status") if isinstance(result.get("last_status"), dict) else {}
                print(f"global_pending={last_status.get('global_pending')}")
                print(f"active_workers={json.dumps(last_status.get('active_workers') or [], ensure_ascii=True)}")
                print(f"offline_workers={json.dumps(last_status.get('offline_workers') or [], ensure_ascii=True)}")
                print(f"workers_exited_by_idle_timeout={json.dumps(last_status.get('workers_exited_by_idle_timeout') or [], ensure_ascii=True)}")
                print(f"stale_processing_count={last_status.get('stale_processing_count')}")
                print(f"autorun_mode={result.get('autorun_mode')}")
                print(f"autorun_audit={json.dumps(result.get('autorun_audit') or {}, ensure_ascii=True)}")
                print(f"supervisor_state_path={result.get('supervisor_state_path')}")
                print(f"report_path={result.get('report_path')}")
                print(f"local_events_path={result.get('local_events_path')}")
                print(f"drive_report_path={result.get('drive_report_path')}")
                print(f"drive_events_path={result.get('drive_events_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "inspect-segment":
                result = run_youtube_video_inspect_segment(
                    config=cfg,
                    options=YoutubeVideoInspectSegmentOptions(
                        story_id=str(args.story_id).strip(),
                        segment_id=str(args.segment_id).strip(),
                    ),
                )
                print(f"segment_id={result.get('segment_id')}")
                print(f"locations={json.dumps(result.get('locations') or [], ensure_ascii=True)}")
                print(f"output_exists={result.get('output_exists')}")
                print(f"output_valid={result.get('output_valid')}")
                print(f"output_segment={result.get('output_segment')}")
                print(f"report_path={result.get('report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "import-results":
                result = run_youtube_video_import_results(
                    config=cfg,
                    options=YoutubeVideoImportResultsOptions(
                        story_id=str(args.story_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"drive_done_segments={result.get('drive_done_segments')}")
                print(f"drive_failed_segments={result.get('drive_failed_segments')}")
                print(f"expected_segments={result.get('expected_segments')}")
                print(f"missing_segments={json.dumps(result.get('missing_segments') or [], ensure_ascii=True)}")
                print(f"report_path={result.get('report_path')}")
                return 0 if result.get("ok") else 2
            if video_sub == "assemble-final":
                result = run_youtube_video_assemble_final(
                    config=cfg,
                    options=YoutubeVideoAssembleFinalOptions(
                        story_id=str(args.story_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                print(f"expected_segments={result.get('expected_segments')}")
                print(f"missing_segments={json.dumps(result.get('missing_segments') or [], ensure_ascii=True)}")
                print(f"final_video_path={result.get('final_video_path')}")
                print(f"report_path={result.get('report_path')}")
                if result.get("validation"):
                    print(f"validation={json.dumps(result.get('validation'), ensure_ascii=True)}")
                return 0 if result.get("ok") else 2
            if video_sub == "full-drive-flow":
                result = run_youtube_video_full_drive_flow(
                    config=cfg,
                    options=YoutubeVideoFullDriveFlowOptions(
                        story_id=str(args.story_id).strip(),
                        execute=bool(getattr(args, "execute", False)),
                        force=bool(getattr(args, "force", False)),
                    ),
                )
                print(f"status={result.get('status')}")
                print(f"ok={result.get('ok')}")
                print(f"execute={result.get('execute')}")
                export = result.get("export") if isinstance(result.get("export"), dict) else {}
                drive_status = result.get("drive_status") if isinstance(result.get("drive_status"), dict) else {}
                print(f"drive_job_root={export.get('drive_job_root')}")
                print(f"expected_segments={drive_status.get('expected_segments')}")
                print(f"global_pending={drive_status.get('global_pending')}")
                print(f"segments_done_count={drive_status.get('segments_done_count')}")
                for cmd in result.get("worker_commands", []) or []:
                    print(f"worker_command={cmd}")
                print(result.get("note", ""))
                return 0 if result.get("ok") else 2
            print("Неизвестная подкоманда youtube video")
            return 2

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

    if args.command == "site-info-visual":
        return _site_info_visual_cli(args, cfg)

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
        from orchestrator.site_publish.collect_assets import run_site_publish_collect_assets
        from orchestrator.site_publish.env_doctor import run_site_publish_env_doctor
        from orchestrator.site_publish.publish import run_site_publish
        from orchestrator.site_publish.prepare import prepare_site_publish

        sub_cmd = str(getattr(args, "site_publish_cmd", "") or "").strip().lower()
        if sub_cmd == "collect-assets":
            execute = bool(args.execute)
            if not execute:
                print("site-publish collect-assets: dry-run (default). Use --execute for real copy.")
            res = run_site_publish_collect_assets(
                cfg.root_dir,
                execute=execute,
                force=bool(args.force),
                allow_partial_tts=bool(getattr(args, "allow_partial_tts", False)),
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                launch_dir=getattr(args, "launch_dir", None),
                images_dir=getattr(args, "images_dir", None),
            )
            print(
                "expected="
                + str(res.get("expected_total"))
                + " mp3_found="
                + str(res.get("mp3_found"))
                + " images_found="
                + str(res.get("images_found"))
                + " texts_found="
                + str(res.get("text_found"))
                + " info_found="
                + str(res.get("info_found"))
            )
            print(
                "packages_ready="
                + str(res.get("packages_ready"))
                + " skipped_tts="
                + str(res.get("skipped_tts"))
                + " real_missing_audio="
                + str(res.get("missing_audio"))
            )
            print(
                "missing_image="
                + str(res.get("missing_image"))
                + " missing_text="
                + str(res.get("missing_text"))
                + " missing_info="
                + str(res.get("missing_info"))
            )
            print(f"launch_dir={res.get('launch_dir')}")
            layout = res.get("layout") or {}
            if layout:
                print(f"layout_mode={layout.get('mode')}")
                print(f"site_publish_root={layout.get('site_publish_root')}")
                print(f"to_publish_root={layout.get('to_publish_root')}")
                if layout.get("legacy_output_site_no_longer_source_of_truth"):
                    print(f"legacy_output_site_no_longer_source_of_truth={layout.get('legacy_output_site_no_longer_source_of_truth')}")
            print(f"manifest_path={res.get('manifest_path')}")
            print(f"output_dir_scanned_by_prepare={res.get('output_dir_scanned_by_prepare')}")
            print(f"can_run_prepare={res.get('can_run_prepare')}")
            print(f"report_path={res.get('report_path')}")
            return 0 if res.get("ok") else 2

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
                allow_partial_tts=bool(getattr(args, "allow_partial_tts", False)),
                launch_name=str(getattr(args, "launch_name", "") or "").strip(),
                launch_dir=getattr(args, "launch_dir", None),
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
                layout = res.get("layout") or {}
                if layout:
                    print(f"layout_mode={layout.get('mode')}")
                    print(f"launch_dir={res.get('launch_dir')}")
                    print(f"site_publish_root={res.get('site_publish_root')}")
                    print(f"to_publish_root={res.get('to_publish_root')}")
                    print(f"manifest_path={res.get('manifest_path')}")
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
            layout = res.get("layout") or {}
            if layout:
                print(f"layout_mode={layout.get('mode')}")
                print(f"launch_dir={res.get('launch_dir')}")
                print(f"site_publish_root={res.get('site_publish_root')}")
                print(f"to_publish_root={res.get('to_publish_root')}")
                print(f"manifest_path={res.get('manifest_path')}")
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
            allow_partial_tts=bool(getattr(args, "allow_partial_tts", False)),
            launch_name=str(getattr(args, "launch_name", "") or "").strip(),
            launch_dir=getattr(args, "launch_dir", None),
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
        layout = res.get("layout") or {}
        if layout:
            print(f"layout_mode={layout.get('mode')}")
            print(f"launch_dir={res.get('launch_dir')}")
            print(f"site_root={res.get('site_root')}")
            print(f"to_publish_root={res.get('to_publish_root')}")
            print(f"manifest_path={res.get('manifest_path')}")
            if layout.get("legacy_output_site_no_longer_source_of_truth"):
                print(f"legacy_output_site_no_longer_source_of_truth={layout.get('legacy_output_site_no_longer_source_of_truth')}")
            if layout.get("legacy_to_publish_no_longer_source_of_truth"):
                print(f"legacy_to_publish_no_longer_source_of_truth={layout.get('legacy_to_publish_no_longer_source_of_truth')}")
        print(f"report_path={res.get('report_path')}")
        print(f"allow_partial_tts={res.get('allow_partial_tts')}")
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
