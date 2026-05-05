from __future__ import annotations

import argparse
import contextlib
import multiprocessing
import sys
from pathlib import Path

from orchestrator.cleanup import move_items_to_quarantine, move_run_to_quarantine, print_scan, scan_generated_artifacts
from orchestrator.config import DEFAULT_CONFIG_PATH, OrchestratorConfig, load_config
from orchestrator.length_filter import LengthFilterOptions, run_length_filter
from orchestrator.phase_a import PhaseAOptions, run_phase_a
from orchestrator.phase_b import PhaseBOptions, run_phase_b
from orchestrator.preflight import run_preflight
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
    run.add_argument("--execute", action="store_true")

    flt = sub.add_parser("filter-length")
    flt.add_argument("--stories-dir", type=Path, required=True)
    flt.add_argument("--short-dir", type=Path)
    flt.add_argument("--words-per-minute", type=int)
    flt.add_argument("--min-minutes", type=float)
    flt.add_argument(
        "--extensions",
        default="",
        help="Comma-separated text file extensions, e.g. .txt,.text",
    )
    flt.add_argument("--execute", action="store_true")

    pha = sub.add_parser("phase-a")
    pha.add_argument("--stories-dir", type=Path, required=True)
    pha.add_argument("--short-dir", type=Path)
    pha.add_argument("--story-id", default="phase-a-run")
    pha.add_argument("--words-per-minute", type=int)
    pha.add_argument("--min-minutes", type=float)
    pha.add_argument("--extensions", default="")
    pha.add_argument("--gemini-workers", type=int, default=5)
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
    pha.add_argument("--visual-mode", default="", help="manual|auto (default from runtime_modes for site branch)")
    pha.add_argument("--visual-pod-url", default="", help="ComfyUI/RunPod URL for visual auto mode")
    pha.add_argument("--execute", action="store_true")

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
    phb.add_argument("--allow-scaffold", action="store_true")

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

    st = sub.add_parser("site-tts", help="Модульный site TTS (output/site → mp3, см. configs/site_tts.yaml)")
    st.add_argument("--modes-config", type=Path, default=Path("configs/runtime_modes.yaml"))
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
        parse_voice_filter_arg,
        run_site_tts_for_story,
        scan_site_tts_queue,
    )

    modes_path = (
        args.modes_config.resolve()
        if args.modes_config.is_absolute()
        else (cfg.root_dir / args.modes_config).resolve()
    )
    execute = bool(getattr(args, "execute", False))
    force = bool(getattr(args, "force", False))
    site_root = (cfg.root_dir / "output" / "site").resolve()

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
        )
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
        rows = scan_site_tts_queue(site_root, voice_types=vf, folder_suffix=fs)
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
        items = collect_batch_items(site_root, limit=lim, voice_types=vf, folder_suffix=fs)
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
            res = export_kokoro_colab_batch(cfg.root_dir, limit=(lim if lim > 0 else None), batch_id=bid)
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
            try:
                res = export_drive_texts(cfg.root_dir, texts_dir=tdir, limit=(lim if lim > 0 else None))
            except ValueError as exc:
                print(str(exc))
                return 2
            print(res.get("message", "ok"))
            print(f"texts_dir={res.get('texts_dir')}")
            print(f"stories_index={res.get('index_csv')}")
            print(f"exported={res.get('exported')}")
            print(f"skipped={res.get('skipped')}")
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
            print(f"batch_dir={res.get('batch_dir')}")
            if res.get("handoff_dir"):
                print(f"handoff_dir={res.get('handoff_dir')}")
                print(f"results_drop_dir={res.get('results_drop_dir')}")
            print(f"imported={res.get('imported')}")
            print(f"skipped_existing={res.get('skipped_existing')}")
            print(f"missing_result={res.get('missing_result')}")
            print(f"errors={res.get('errors')}")
            if int(res.get("missing_result", 0) or 0) > 0:
                print("hint=Проверьте results_drop_here или runs/tts_colab_batches/<batch_id>/results")
            return 0 if int(res.get("errors", 0) or 0) == 0 else 2

        if sub == "import-drive":
            mdir = getattr(args, "mp3_dir", None)
            force = bool(getattr(args, "force", False))
            try:
                res = import_drive_mp3(cfg.root_dir, mp3_dir=mdir, force=force)
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
            res = verify_mp3_coverage(cfg.root_dir, batch_id=bid, handoff_dir=hdir, latest=latest, current=current)
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
            try:
                exp = export_drive_texts(cfg.root_dir, texts_dir=tdir, limit=(lim if lim > 0 else None))
            except ValueError as exc:
                print(str(exc))
                return 2
            print(exp.get("message", "export ok"))
            print(f"exported={exp.get('exported')} skipped={exp.get('skipped')}")
            print(f"waiting_for_mp3={exp.get('exported')}")
            if int(exp.get("exported", 0) or 0) <= 0:
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
        run_id = runner.run(
            RunOptions(
                pipeline=args.pipeline,
                story_id=args.story_id,
                execute=args.execute,
                run_profile=run_profile,
                stories_dir=args.stories_dir,
                allow_real_stages=allow_real_stages,
            )
        )
        print(f"run_id={run_id}")
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
                extensions=extensions,
                gemini_workers=max(1, min(5, int(args.gemini_workers))),
                max_stories=max(0, int(args.max_stories)),
                gemini_registry_path=args.gemini_registry,
                gemini_stage_key=str(args.gemini_stage_key).strip() or "general_selection",
                gemini_info_stage_key=str(args.gemini_info_stage_key).strip() or "site_info_builder",
                run_branch=str(args.run_branch).strip().lower() or "site",
                resume=bool(args.resume),
            ),
        )
        print(result.get("summary", result.get("message", "done")))
        if not result.get("ok", False):
            return 2
        return 0

    if args.command == "phase-a":
        if not args.execute:
            print("Running PHASE A in DRY-RUN for length filter move step.")
        print("Текущие режимы:")
        for k in DEFAULT_MODES:
            print(f"- {k}: {modes.get(k, DEFAULT_MODES[k])}")
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
                allow_scaffold=bool(args.allow_scaffold),
            ),
        )
        print(result.get("summary", result.get("message", "phase B completed")))
        if not result.get("ok", False):
            return 2
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
