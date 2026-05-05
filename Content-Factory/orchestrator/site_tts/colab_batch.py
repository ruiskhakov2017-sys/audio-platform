from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.text_chunking import pack_paragraph_chunks

_HANDOFF_ROOT = "_COLAB_EXPORTS"
_HANDOFF_RESULTS_DIR = "results_drop_here"
_CURRENT_ROOT = "COLAB_TTS_CURRENT"
_CURRENT_TEXTS = "TEXTS_TO_COLAB"
_CURRENT_MP3 = "MP3_FROM_COLAB"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel_posix(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _pick_voice(settings: Any, voice_type: str) -> str:
    vt = (voice_type or "U").upper()[:1]
    if vt == "M":
        return settings.kokoro_voice_male
    if vt == "F":
        return settings.kokoro_voice_female
    return settings.kokoro_voice_neutral


def _lang_code(settings: Any, voice: str) -> str:
    if settings.kokoro_lang_code:
        return settings.kokoro_lang_code.strip().lower()[:1]
    if voice:
        c = voice.strip().lower()[:1]
        if c in "abefhijpz":
            return c
    return "a"


@dataclass(frozen=True)
class StoryTtsSource:
    story_id: str
    story_folder: Path
    tts_text_path: Path
    voice_type: str
    has_mp3: bool
    expected_output_mp3: Path


def _resolve_story_tts_source(story_folder: Path) -> tuple[StoryTtsSource | None, str | None]:
    story_id = story_folder.name
    expected_mp3 = story_folder / f"{story_id}.mp3"
    candidates = []
    for vt in ("M", "F", "U"):
        p = story_folder / f"{story_id}__{vt}.txt"
        if p.is_file():
            candidates.append((vt, p))
    if not candidates:
        return None, "missing_tts_text_file"
    if len(candidates) > 1:
        names = ",".join(p.name for _, p in candidates)
        return None, f"multiple_tts_text_files:{names}"
    vt, path = candidates[0]
    return (
        StoryTtsSource(
            story_id=story_id,
            story_folder=story_folder,
            tts_text_path=path,
            voice_type=vt,
            has_mp3=expected_mp3.is_file(),
            expected_output_mp3=expected_mp3,
        ),
        None,
    )


def _iter_story_dirs(site_root: Path) -> list[Path]:
    if not site_root.is_dir():
        return []
    return sorted([p for p in site_root.iterdir() if p.is_dir()], key=lambda x: x.name.lower())


def _safe_name(name: str) -> str:
    out = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    out = out.replace("\n", "_").replace("\r", "_")
    return out or "story"


def _split_story_voice(stem: str) -> tuple[str, str]:
    s = (stem or "").strip()
    if "__" in s:
        story, voice = s.rsplit("__", 1)
        v = voice.strip().upper()[:1]
        if v in {"M", "F", "U"}:
            return story, v
    return s, "U"


def _resolve_drive_dir(root: Path, cli_dir: Path | None, cfg_dir: str) -> Path:
    if cli_dir is not None:
        return (cli_dir if cli_dir.is_absolute() else (root / cli_dir)).resolve()
    raw = (cfg_dir or "").strip()
    if not raw:
        raise ValueError("drive directory is not configured; pass via CLI or configs/site_tts.yaml")
    p = Path(raw)
    return (p if p.is_absolute() else (root / p)).resolve()


def _drive_root(root: Path, settings: Any) -> Path | None:
    raw = str(getattr(settings, "google_drive_root_dir", "") or "").strip()
    if not raw:
        return None
    p = Path(raw)
    return (p if p.is_absolute() else (root / p)).resolve()


def _drive_dir_from(root: Path, settings: Any, key: str, default_sub: str) -> Path:
    cli = ""
    if key == "texts":
        cli = str(getattr(settings, "google_drive_texts_dir", "") or "").strip()
    elif key == "mp3":
        cli = str(getattr(settings, "google_drive_mp3_dir", "") or "").strip()
    elif key == "scripts":
        cli = str(getattr(settings, "google_drive_scripts_dir", "") or "").strip()
    elif key == "cache":
        cli = str(getattr(settings, "google_drive_cache_dir", "") or "").strip()
    elif key == "logs":
        cli = str(getattr(settings, "google_drive_logs_dir", "") or "").strip()
    elif key == "job":
        cli = str(getattr(settings, "google_drive_job_dir", "") or "").strip()
    if cli:
        p = Path(cli)
        return (p if p.is_absolute() else (root / p)).resolve()
    dr = _drive_root(root, settings)
    if dr is not None:
        return (dr / default_sub).resolve()
    raise ValueError(
        f"google drive path for {key} is not configured; set google_drive_tts.root_dir or explicit {key}_dir"
    )


def _load_expected_files(job_dir: Path) -> list[str]:
    p = job_dir / "EXPECTED_FILES.txt"
    if not p.is_file():
        return []
    names: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        t = raw.strip()
        if t and t.lower().endswith(".mp3"):
            names.append(Path(t).name)
    return names


def export_drive_texts(
    root_dir: Path,
    *,
    texts_dir: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    target = (
        (texts_dir if texts_dir.is_absolute() else (root / texts_dir)).resolve()
        if texts_dir is not None
        else _drive_dir_from(root, settings, "texts", "texts")
    )
    target.mkdir(parents=True, exist_ok=True)
    index_csv = target.parent / "STORIES_INDEX.csv"
    drive_root = target.parent
    scripts_dir = _drive_dir_from(root, settings, "scripts", "scripts")
    cache_dir = _drive_dir_from(root, settings, "cache", "cache")
    logs_dir = _drive_dir_from(root, settings, "logs", "logs")
    job_dir = _drive_dir_from(root, settings, "job", "job")
    mp3_dir = _drive_dir_from(root, settings, "mp3", "mp3")
    scripts_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    mp3_dir.mkdir(parents=True, exist_ok=True)

    site_root = (root / "output" / "site").resolve()
    exported_rows: list[list[str]] = []
    skipped_rows: list[list[str]] = []
    lim = None if limit is None or int(limit) <= 0 else int(limit)
    exported = 0
    for i, story_folder in enumerate(_iter_story_dirs(site_root), start=1):
        src, err = _resolve_story_tts_source(story_folder)
        if src is None:
            skipped_rows.append([f"{i:03d}", story_folder.name, "", "", "", f"skip:{err or 'unknown'}"])
            continue
        if src.has_mp3:
            skipped_rows.append([f"{i:03d}", src.story_id, src.tts_text_path.name, f"{src.story_id}__{src.voice_type}.mp3", str(src.expected_output_mp3), "skip:has_mp3"])
            continue
        txt_name = f"{_safe_name(src.story_id)}__{src.voice_type}.txt"
        mp3_name = f"{_safe_name(src.story_id)}__{src.voice_type}.mp3"
        dst = target / txt_name
        dst.write_text(src.tts_text_path.read_text(encoding="utf-8"), encoding="utf-8")
        exported += 1
        exported_rows.append([f"{exported:03d}", src.story_id, txt_name, mp3_name, str(src.expected_output_mp3), src.voice_type])
        if lim is not None and exported >= lim:
            break

    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["number", "story_folder", "source_txt", "expected_mp3", "final_output_path", "voice"])
        w.writerows(exported_rows)
        if skipped_rows:
            w.writerow([])
            w.writerow(["number", "story_folder", "source_txt", "expected_mp3", "final_output_path", "note"])
            w.writerows(skipped_rows[:200])

    expected_files = [r[3] for r in exported_rows]
    job = {
        "job_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "created_at": _utc_now_iso(),
        "expected_count": len(expected_files),
        "expected_files": expected_files,
        "texts_dir": str(target),
        "mp3_dir": str(mp3_dir),
        "state": "exported_waiting_mp3",
    }
    (job_dir / "current_job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "EXPECTED_COUNT.txt").write_text(str(len(expected_files)) + "\n", encoding="utf-8")
    (job_dir / "EXPECTED_FILES.txt").write_text("\n".join(expected_files) + ("\n" if expected_files else ""), encoding="utf-8")
    (job_dir / "LOCAL_STATUS.json").write_text(
        json.dumps(
            {
                "state": "exported_waiting_mp3",
                "expected_count": len(expected_files),
                "found_mp3": 0,
                "updated_at": _utc_now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "texts_dir": str(target),
        "mp3_dir": str(mp3_dir),
        "drive_root": str(drive_root),
        "job_dir": str(job_dir),
        "index_csv": str(index_csv),
        "exported": exported,
        "skipped": len(skipped_rows),
        "message": "TXT copied to Google Drive texts folder",
    }


def import_drive_mp3(
    root_dir: Path,
    *,
    mp3_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    source = (
        (mp3_dir if mp3_dir.is_absolute() else (root / mp3_dir)).resolve()
        if mp3_dir is not None
        else _drive_dir_from(root, settings, "mp3", "mp3")
    )
    source.mkdir(parents=True, exist_ok=True)
    job_dir = _drive_dir_from(root, settings, "job", "job")
    expected = set(_load_expected_files(job_dir))
    site_root = (root / "output" / "site").resolve()

    imported = 0
    skipped_existing = 0
    missing_story = 0
    invalid_mp3 = 0
    errors = 0
    details: list[dict[str, str]] = []
    for mp3 in sorted(source.glob("*.mp3")):
        if expected and mp3.name not in expected:
            missing_story += 1
            details.append({"status": "extra_mp3", "file": mp3.name, "reason": "not_in_expected_files"})
            continue
        try:
            size = mp3.stat().st_size
        except OSError as exc:
            errors += 1
            details.append({"status": "error", "file": mp3.name, "reason": str(exc)})
            continue
        if size <= 0:
            invalid_mp3 += 1
            details.append({"status": "invalid_mp3", "file": mp3.name, "reason": "empty_file"})
            continue
        story, _voice = _split_story_voice(mp3.stem)
        story = _safe_name(story)
        folder = site_root / story
        if not folder.is_dir():
            missing_story += 1
            details.append({"status": "extra_mp3", "file": mp3.name, "reason": "story_folder_not_found"})
            continue
        dst = folder / f"{story}.mp3"
        if dst.is_file() and not force:
            skipped_existing += 1
            details.append({"status": "skipped_existing", "file": mp3.name, "path": str(dst)})
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(mp3.read_bytes())
            imported += 1
            details.append({"status": "imported", "file": mp3.name, "path": str(dst)})
        except OSError as exc:
            errors += 1
            details.append({"status": "error", "file": mp3.name, "reason": str(exc)})
    return {
        "ok": True,
        "mp3_dir": str(source),
        "job_dir": str(job_dir),
        "imported": imported,
        "skipped_existing": skipped_existing,
        "missing_story": missing_story,
        "invalid_mp3": invalid_mp3,
        "errors": errors,
        "details": details,
    }


def verify_drive_status(
    root_dir: Path,
    *,
    texts_dir: Path | None = None,
    mp3_dir: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    texts = (
        (texts_dir if texts_dir.is_absolute() else (root / texts_dir)).resolve()
        if texts_dir is not None
        else _drive_dir_from(root, settings, "texts", "texts")
    )
    mp3 = (
        (mp3_dir if mp3_dir.is_absolute() else (root / mp3_dir)).resolve()
        if mp3_dir is not None
        else _drive_dir_from(root, settings, "mp3", "mp3")
    )
    job_dir = _drive_dir_from(root, settings, "job", "job")
    texts.mkdir(parents=True, exist_ok=True)
    mp3.mkdir(parents=True, exist_ok=True)

    txt_files = sorted([p for p in texts.glob("*.txt") if p.is_file()])
    mp3_files = sorted([p for p in mp3.glob("*.mp3") if p.is_file()])
    valid_mp3_stems: set[str] = set()
    invalid_mp3_names: list[str] = []
    for p in mp3_files:
        try:
            if p.stat().st_size > 0:
                valid_mp3_stems.add(p.stem)
            else:
                invalid_mp3_names.append(p.name)
        except OSError:
            invalid_mp3_names.append(p.name)
    txt_set = {p.stem for p in txt_files}
    expected_mp3 = set(_load_expected_files(job_dir))
    if expected_mp3:
        expected_stems = {Path(x).stem for x in expected_mp3}
    else:
        expected_stems = {p.stem for p in txt_files}
    missing = sorted(expected_stems - valid_mp3_stems)
    extra = sorted(valid_mp3_stems - expected_stems)
    can_import = sorted(expected_stems & valid_mp3_stems)
    return {
        "ok": True,
        "texts_dir": str(texts),
        "mp3_dir": str(mp3),
        "job_dir": str(job_dir),
        "texts_count": len(txt_set),
        "mp3_count": len(mp3_files),
        "valid_mp3_count": len(valid_mp3_stems),
        "invalid_mp3_count": len(invalid_mp3_names),
        "can_import": len(can_import),
        "missing_mp3": len(missing),
        "extra_mp3": len(extra),
        "first_missing": missing[:20],
        "first_extra": extra[:20],
        "first_invalid": invalid_mp3_names[:20],
    }


def setup_drive_workspace(root_dir: Path) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    drive_root = _drive_root(root, settings)
    if drive_root is None:
        raise ValueError("google_drive_tts.root_dir is not configured in configs/site_tts.yaml")
    texts = _drive_dir_from(root, settings, "texts", "texts")
    mp3 = _drive_dir_from(root, settings, "mp3", "mp3")
    scripts = _drive_dir_from(root, settings, "scripts", "scripts")
    cache = _drive_dir_from(root, settings, "cache", "cache")
    logs = _drive_dir_from(root, settings, "logs", "logs")
    job = _drive_dir_from(root, settings, "job", "job")
    for d in (texts, mp3, scripts, cache, logs, job):
        d.mkdir(parents=True, exist_ok=True)
    src_runner = (root / "colab" / "kokoro_google_drive_colab.py").resolve()
    copied = False
    if src_runner.is_file():
        (scripts / "kokoro_google_drive_colab.py").write_text(src_runner.read_text(encoding="utf-8"), encoding="utf-8")
        copied = True
    return {
        "ok": True,
        "drive_root": str(drive_root),
        "texts_dir": str(texts),
        "mp3_dir": str(mp3),
        "scripts_dir": str(scripts),
        "cache_dir": str(cache),
        "logs_dir": str(logs),
        "job_dir": str(job),
        "runner_copied": copied,
        "colab_cmd": "python /content/drive/MyDrive/ContentFactory_TTS/scripts/kokoro_google_drive_colab.py",
    }


def wait_drive_mp3_and_import(
    root_dir: Path,
    *,
    mp3_dir: Path | None = None,
    wait_interval_minutes: int | None = None,
    max_wait_hours: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    source = (
        (mp3_dir if mp3_dir.is_absolute() else (root / mp3_dir)).resolve()
        if mp3_dir is not None
        else _drive_dir_from(root, settings, "mp3", "mp3")
    )
    job_dir = _drive_dir_from(root, settings, "job", "job")
    source.mkdir(parents=True, exist_ok=True)
    expected = _load_expected_files(job_dir)
    if not expected:
        return {"ok": False, "message": f"EXPECTED_FILES.txt is empty or missing in {job_dir}"}
    interval = max(1, int(wait_interval_minutes or settings.google_drive_wait_interval_minutes))
    max_hours = max(1, int(max_wait_hours or settings.google_drive_max_wait_hours))
    deadline = time.time() + max_hours * 3600
    expected_set = set(expected)

    last_status: dict[str, Any] = {}
    while True:
        found_files = [p for p in source.glob("*.mp3") if p.is_file()]
        valid_set = {p.name for p in found_files if p.stat().st_size > 0}
        zero_size = [p.name for p in found_files if p.stat().st_size <= 0]
        missing = sorted(expected_set - valid_set)
        extra = sorted(valid_set - expected_set)
        last_status = {
            "expected": len(expected_set),
            "found": len(valid_set & expected_set),
            "missing": len(missing),
            "zero_size": len(zero_size),
            "extra": len(extra),
            "next_check_in_minutes": interval,
        }
        print(
            f"expected={last_status['expected']} found={last_status['found']} missing={last_status['missing']} "
            f"zero_size={last_status['zero_size']} extra={last_status['extra']} next_check_in={interval}_minutes",
            flush=True,
        )
        if not missing and not zero_size:
            break
        if time.time() >= deadline:
            return {"ok": False, "message": "max wait time exceeded", "status": last_status, "missing_files": missing[:50], "zero_size_files": zero_size[:50]}
        time.sleep(interval * 60)

    imp = import_drive_mp3(root, mp3_dir=source, force=force)
    if not imp.get("ok", False) or int(imp.get("errors", 0) or 0) > 0:
        return {"ok": False, "message": "import-drive failed", "import": imp, "status": last_status}

    # post-check: expected output files exist and non-zero
    site_root = (root / "output" / "site").resolve()
    failed_local: list[str] = []
    for name in expected:
        story, _v = _split_story_voice(Path(name).stem)
        story = _safe_name(story)
        out_mp3 = site_root / story / f"{story}.mp3"
        if not out_mp3.is_file() or out_mp3.stat().st_size <= 0:
            failed_local.append(story)
    if failed_local:
        return {"ok": False, "message": "local post-check failed", "failed_stories": failed_local[:50], "import": imp}

    cleaned = {"texts_deleted": 0, "mp3_deleted": 0}
    if settings.google_drive_cleanup_after_success:
        texts = _drive_dir_from(root, settings, "texts", "texts")
        for name in expected:
            txt_name = Path(name).with_suffix(".txt").name
            p = texts / txt_name
            if p.is_file():
                try:
                    p.unlink()
                    cleaned["texts_deleted"] += 1
                except OSError:
                    pass
            p2 = source / name
            if p2.is_file():
                try:
                    p2.unlink()
                    cleaned["mp3_deleted"] += 1
                except OSError:
                    pass

    (job_dir / "LOCAL_STATUS.json").write_text(
        json.dumps(
            {
                "state": "imported_success",
                "expected_count": len(expected),
                "imported": int(imp.get("imported", 0) or 0),
                "cleanup": cleaned,
                "updated_at": _utc_now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"ok": True, "status": last_status, "import": imp, "cleanup": cleaned}


def _current_paths(root: Path) -> tuple[Path, Path, Path]:
    current = (root / _CURRENT_ROOT).resolve()
    texts = (current / _CURRENT_TEXTS).resolve()
    mp3 = (current / _CURRENT_MP3).resolve()
    return current, texts, mp3


def _clear_dir(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _latest_handoff_dir(root: Path) -> Path | None:
    base = (root / _HANDOFF_ROOT).resolve()
    if not base.is_dir():
        return None
    candidates = [p for p in base.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _resolve_handoff_dir(root: Path, handoff_dir: Path | None, latest: bool) -> Path | None:
    if handoff_dir is not None:
        return (handoff_dir if handoff_dir.is_absolute() else (root / handoff_dir)).resolve()
    if latest:
        return _latest_handoff_dir(root)
    return None


def _read_handoff_manifest(handoff: Path) -> dict[str, Any]:
    path = handoff / "internal_manifest.json"
    if not path.is_file():
        raise ValueError(f"internal_manifest.json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read handoff manifest: {path} ({exc})") from exc


def _create_handoff_package(root: Path, batch_root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    batch_id = str(manifest.get("batch_id", batch_root.name))
    total_items = int(manifest.get("total_items", 0) or 0)
    handoff_name = f"{batch_id}__site_tts__{total_items}_stories"
    handoff_dir = (root / _HANDOFF_ROOT / handoff_name).resolve()
    handoff_dir.mkdir(parents=True, exist_ok=False)

    index_csv = handoff_dir / "01_STORIES_INDEX.csv"
    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "human_number",
                "story_id",
                "story_title_or_folder",
                "voice",
                "chunks_count",
                "expected_result_filename",
                "expected_output_mp3",
                "status",
            ]
        )
        for i, item in enumerate(list(manifest.get("items", [])), start=1):
            exp = str(item.get("expected_result_mp3", "")).strip()
            w.writerow(
                [
                    f"{i:03d}",
                    item.get("story_id", ""),
                    item.get("story_folder", ""),
                    item.get("voice_type", ""),
                    item.get("chunks_count", ""),
                    Path(exp).name if exp else "",
                    item.get("expected_output_mp3", ""),
                    item.get("status", ""),
                ]
            )

    internal_manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "batch_dir": str(batch_root),
        "created_at": manifest.get("created_at", _utc_now_iso()),
        "items_count": total_items,
    }
    (handoff_dir / "internal_manifest.json").write_text(
        json.dumps(internal_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (handoff_dir / "00_README_START_HERE.txt").write_text(
        "\n".join(
            [
                "Kokoro Colab Site TTS handoff (START HERE)",
                "",
                "1) Upload 02_UPLOAD_THIS_TO_COLAB.zip to Colab and run generation there.",
                "2) Download produced MP3 files from Colab.",
                "3) Put resulting MP3 files into results_drop_here/ in this folder.",
                f"4) Run import:",
                f"   python -m orchestrator site-tts kokoro-colab import --handoff-dir \"{handoff_dir}\"",
                "5) Verify status:",
                f"   python -m orchestrator site-tts kokoro-colab verify --handoff-dir \"{handoff_dir}\"",
                "",
                "Colab runner in repository:",
                "- Content-Factory/colab/kokoro_colab_runner.py",
                "- Content-Factory/colab/README_KOKORO_COLAB.md",
                "",
                "Notes:",
                "- Keep MP3 names matching item_id (example: item_000001.mp3).",
                "- No local sync --execute is required for this flow.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (handoff_dir / _HANDOFF_RESULTS_DIR).mkdir(parents=True, exist_ok=True)

    upload_zip = handoff_dir / "02_UPLOAD_THIS_TO_COLAB.zip"
    with zipfile.ZipFile(upload_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in ("manifest.json", "README_COLAB.md"):
            p = batch_root / rel
            if p.is_file():
                zf.write(p, arcname=rel)
        for sub in ("stories", "chunks"):
            base = batch_root / sub
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(batch_root)).replace("\\", "/"))

    return {
        "handoff_dir": str(handoff_dir),
        "handoff_index_csv": str(index_csv),
        "handoff_upload_zip": str(upload_zip),
        "handoff_results_drop": str(handoff_dir / _HANDOFF_RESULTS_DIR),
        "handoff_internal_manifest": str(handoff_dir / "internal_manifest.json"),
    }


def _build_current_folder(root: Path, batch_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    current_dir, texts_dir, mp3_dir = _current_paths(root)
    _clear_dir(texts_dir)
    mp3_dir.mkdir(parents=True, exist_ok=True)

    items = list(manifest.get("items", []))
    index_csv = current_dir / "STORIES_INDEX.csv"
    mapping: list[dict[str, str]] = []
    collisions: set[str] = set()
    used_names: set[str] = set()
    rows: list[list[str]] = []

    for i, item in enumerate(items, start=1):
        story_id = str(item.get("story_id", "")).strip()
        voice = str(item.get("voice_type", "U")).strip().upper()[:1] or "U"
        source_rel = str(item.get("source_text_path", "")).strip()
        source_path = (root / source_rel).resolve() if source_rel else None
        base = _safe_name(story_id)
        txt_name = f"{base}__{voice}.txt"
        if txt_name in used_names:
            txt_name = f"{base}__{voice}__{i:03d}.txt"
            collisions.add(story_id)
        used_names.add(txt_name)
        mp3_name = Path(txt_name).with_suffix(".mp3").name
        out_rel = str(item.get("expected_output_mp3", "")).strip()

        if source_path is not None and source_path.is_file():
            (texts_dir / txt_name).write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            (texts_dir / txt_name).write_text("", encoding="utf-8")

        rows.append(
            [
                f"{i:03d}",
                story_id,
                txt_name,
                mp3_name,
                out_rel,
                voice,
                str(item.get("status", "pending")),
            ]
        )
        mapping.append(
            {
                "story_folder": story_id,
                "source_txt": txt_name,
                "expected_mp3": mp3_name,
                "expected_output_mp3": out_rel,
                "voice": voice,
            }
        )

    with index_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["number", "story_folder", "source_txt", "expected_mp3", "final_output_path", "voice", "status"])
        w.writerows(rows)

    readme = current_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "COLAB_TTS_CURRENT quick flow",
                "",
                "1) Upload all TXT files from TEXTS_TO_COLAB/ into Colab.",
                "2) Generate MP3 with the same basenames.",
                "3) Put MP3 files into MP3_FROM_COLAB/.",
                "4) Run import:",
                "   python -m orchestrator site-tts kokoro-colab import --current",
                "5) Run verify:",
                "   python -m orchestrator site-tts kokoro-colab verify --current",
                "",
                "Important:",
                "- Keep filenames unchanged between TXT and MP3.",
                "- Local sync --execute is NOT required for this flow.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    py_cmd = "py -3"
    import_bat = current_dir / "IMPORT_MP3.bat"
    import_bat.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal EnableExtensions DisableDelayedExpansion",
                "cd /d \"%~dp0\\..\"",
                f"set \"PY_CMD={py_cmd}\"",
                "where py >nul 2>nul || set \"PY_CMD=python\"",
                "%PY_CMD% -m orchestrator site-tts kokoro-colab import --current",
                "pause",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    verify_bat = current_dir / "VERIFY_MP3.bat"
    verify_bat.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal EnableExtensions DisableDelayedExpansion",
                "cd /d \"%~dp0\\..\"",
                f"set \"PY_CMD={py_cmd}\"",
                "where py >nul 2>nul || set \"PY_CMD=python\"",
                "%PY_CMD% -m orchestrator site-tts kokoro-colab verify --current",
                "pause",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    internal = {
        "schema_version": 1,
        "batch_id": str(manifest.get("batch_id", batch_root.name)),
        "batch_dir": str(batch_root),
        "created_at": manifest.get("created_at", _utc_now_iso()),
        "items_count": len(mapping),
        "mapping": mapping,
    }
    (current_dir / "internal_manifest.json").write_text(json.dumps(internal, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "current_dir": str(current_dir),
        "texts_dir": str(texts_dir),
        "mp3_dir": str(mp3_dir),
        "index_csv": str(index_csv),
        "collisions": sorted(collisions),
        "items_count": len(mapping),
    }


def export_kokoro_colab_batch(
    root_dir: Path,
    *,
    limit: int | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    site_root = (root / "output" / "site").resolve()
    if not site_root.is_dir():
        return {"ok": False, "message": f"site output not found: {site_root}"}

    settings = load_site_tts_settings(root)
    chunk_max = int(settings.kokoro_chunk_max_chars)
    speed = float(settings.kokoro_speed)
    batch = (batch_id or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_root = (root / "runs" / "tts_colab_batches" / batch).resolve()
    if batch_root.exists():
        return {"ok": False, "message": f"batch already exists: {batch_root}"}

    stories_dir = batch_root / "stories"
    chunks_root = batch_root / "chunks"
    results_dir = batch_root / "results"
    stories_dir.mkdir(parents=True, exist_ok=True)
    chunks_root.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    skipped: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    exported = 0
    lim = None if limit is None or int(limit) <= 0 else int(limit)

    for story_folder in _iter_story_dirs(site_root):
        src, err = _resolve_story_tts_source(story_folder)
        if src is None:
            skipped.append({"story_id": story_folder.name, "reason": err or "unknown"})
            continue
        if src.has_mp3:
            skipped.append({"story_id": src.story_id, "reason": "has_mp3"})
            continue
        text = src.tts_text_path.read_text(encoding="utf-8")
        chunks = pack_paragraph_chunks(text, chunk_max)
        if not chunks:
            skipped.append({"story_id": src.story_id, "reason": "empty_tts_text"})
            continue

        item_id = f"item_{exported + 1:06d}"
        voice = _pick_voice(settings, src.voice_type)
        lang = _lang_code(settings, voice)
        item_story_rel = Path("stories") / f"{item_id}.txt"
        item_chunks_rel = Path("chunks") / item_id
        item_story_path = batch_root / item_story_rel
        item_chunks_path = batch_root / item_chunks_rel
        item_story_path.parent.mkdir(parents=True, exist_ok=True)
        item_chunks_path.mkdir(parents=True, exist_ok=True)
        item_story_path.write_text(text, encoding="utf-8")
        for idx, ch in enumerate(chunks):
            (item_chunks_path / f"chunk_{idx:04d}.txt").write_text(ch, encoding="utf-8")

        items.append(
            {
                "item_id": item_id,
                "story_id": src.story_id,
                "story_folder": _rel_posix(src.story_folder, root),
                "source_text_path": _rel_posix(src.tts_text_path, root),
                "original_tts_filename": src.tts_text_path.name,
                "batch_text_path": str(item_story_rel).replace("\\", "/"),
                "chunks_dir": str(item_chunks_rel).replace("\\", "/"),
                "expected_result_mp3": f"results/{item_id}.mp3",
                "expected_output_mp3": _rel_posix(src.expected_output_mp3, root),
                "voice_type": src.voice_type,
                "kokoro_voice": voice,
                "kokoro_lang_code": lang,
                "speed": speed,
                "text_chars": len(text),
                "chunks_count": len(chunks),
                "hash_text_sha256": _sha256_text(text),
                "status": "pending",
            }
        )
        exported += 1
        if lim is not None and exported >= lim:
            break

    manifest = {
        "schema_version": 1,
        "batch_id": batch,
        "created_at": _utc_now_iso(),
        "source_root": "output/site",
        "total_items": len(items),
        "settings": {
            "kokoro_lang_code": (settings.kokoro_lang_code.strip().lower()[:1] if settings.kokoro_lang_code else ""),
            "speed": speed,
            "chunk_max_chars": chunk_max,
        },
        "items": items,
    }
    (batch_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    export_report = {
        "batch_id": batch,
        "created_at": manifest["created_at"],
        "site_root": _rel_posix(site_root, root),
        "exported": len(items),
        "skipped": skipped,
    }
    (batch_root / "export_report.json").write_text(
        json.dumps(export_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (batch_root / "README_COLAB.md").write_text(
        "\n".join(
            [
                "# Kokoro Colab Batch",
                "",
                "Эта папка создана командой:",
                "- `python -m orchestrator site-tts kokoro-colab export --limit <N>`",
                "",
                "Цель: сгенерировать MP3 в Colab (GPU) и импортировать их обратно в `output/site` без локального TTS.",
                "",
                "## Что внутри",
                "- `manifest.json` — список item-ов и куда вернуть итоговые mp3.",
                "- `stories/` — полный текст item-а (удобно для отладки).",
                "- `chunks/<item_id>/chunk_*.txt` — чанки в правильном порядке.",
                "- `results/` — сюда Colab должен сохранить `item_XXXXXX.mp3`.",
                "",
                "## Шаги в Colab",
                "1. Zip batch-папку и загрузите в Colab.",
                "2. Проверьте GPU: `!nvidia-smi`",
                "3. Проверьте torch CUDA:",
                "   - `import torch`",
                "   - `print(torch.cuda.is_available())`",
                "4. Установите зависимости Kokoro в Colab (`kokoro`, `soundfile`, ffmpeg).",
                "5. Распакуйте batch и откройте `manifest.json`.",
                "6. Для каждого item:",
                "   - прочитайте `chunks/<item_id>/chunk_*.txt` по порядку;",
                "   - синтезируйте аудио Kokoro на GPU;",
                "   - сохраните итог в `results/<item_id>.mp3` (имя строго как в manifest).",
                "7. Скачайте обратно папку `results/` (можно вместе с manifest).",
                "",
                "## Локальный импорт и проверка",
                "1. Положите `results/*.mp3` в этот batch-каталог локально.",
                "2. Импортируйте:",
                "   - `python -m orchestrator site-tts kokoro-colab import --batch-id <batch_id>`",
                "3. Проверьте покрытие:",
                "   - `python -m orchestrator site-tts kokoro-colab verify --batch-id <batch_id>`",
                "",
                "Важно: этот flow НЕ запускает локальный `sync --execute`.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    handoff = _create_handoff_package(root, batch_root, manifest)
    current = _build_current_folder(root, batch_root, manifest)
    return {
        "ok": True,
        "batch_id": batch,
        "batch_dir": str(batch_root),
        "exported": len(items),
        "skipped": len(skipped),
        "manifest_path": str(batch_root / "manifest.json"),
        **handoff,
        **current,
    }


def _resolve_batch_dir(root: Path, batch_id: str | None, batch_dir: Path | None, handoff: Path | None = None) -> Path:
    if handoff is not None:
        meta = _read_handoff_manifest(handoff)
        bid = str(meta.get("batch_id", "")).strip()
        if bid:
            return (root / "runs" / "tts_colab_batches" / bid).resolve()
        bdir = str(meta.get("batch_dir", "")).strip()
        if bdir:
            return Path(bdir).resolve()
        raise ValueError("handoff manifest does not contain batch_id/batch_dir")
    if batch_dir is not None:
        return (batch_dir if batch_dir.is_absolute() else (root / batch_dir)).resolve()
    bid = (batch_id or "").strip()
    if not bid:
        raise ValueError("either --batch-id or --batch-dir is required")
    return (root / "runs" / "tts_colab_batches" / bid).resolve()


def import_kokoro_colab_results(
    root_dir: Path,
    *,
    batch_id: str | None = None,
    batch_dir: Path | None = None,
    handoff_dir: Path | None = None,
    latest: bool = False,
    current: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    if current:
        current_dir, _texts_dir, mp3_dir = _current_paths(root)
        internal_path = current_dir / "internal_manifest.json"
        if not internal_path.is_file():
            return {"ok": False, "message": f"current manifest not found: {internal_path}"}
        try:
            meta = json.loads(internal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "message": f"failed to read {internal_path}: {exc}"}
        mapping = list(meta.get("mapping", []))
        imported = 0
        skipped_existing = 0
        missing_result = 0
        errors = 0
        details: list[dict[str, str]] = []
        for m in mapping:
            expected_mp3 = str(m.get("expected_mp3", "")).strip()
            out_rel = str(m.get("expected_output_mp3", "")).strip()
            if not expected_mp3 or not out_rel:
                errors += 1
                details.append({"status": "error", "reason": "mapping missing expected_mp3/output", "story": str(m.get("story_folder", ""))})
                continue
            src = (mp3_dir / expected_mp3).resolve()
            dst = (root / out_rel).resolve()
            if not src.is_file():
                missing_result += 1
                details.append({"status": "missing_result", "path": str(src), "story": str(m.get("story_folder", ""))})
                continue
            if dst.is_file() and not force:
                skipped_existing += 1
                details.append({"status": "skipped_existing", "path": str(dst), "story": str(m.get("story_folder", ""))})
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                imported += 1
                details.append({"status": "imported", "path": str(dst), "story": str(m.get("story_folder", ""))})
            except OSError as exc:
                errors += 1
                details.append({"status": "error", "reason": str(exc), "story": str(m.get("story_folder", ""))})
        return {
            "ok": True,
            "mode": "current",
            "current_dir": str(current_dir),
            "results_drop_dir": str(mp3_dir),
            "batch_dir": str(meta.get("batch_dir", "")),
            "imported": imported,
            "skipped_existing": skipped_existing,
            "missing_result": missing_result,
            "errors": errors,
            "force": bool(force),
            "details": details,
        }

    handoff = _resolve_handoff_dir(root, handoff_dir, latest)
    bdir = _resolve_batch_dir(root, batch_id, batch_dir, handoff=handoff)
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "message": f"manifest not found: {manifest_path}"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest.get("items", []))
    handoff_results = (handoff / _HANDOFF_RESULTS_DIR).resolve() if handoff is not None else None

    imported = 0
    skipped_existing = 0
    missing_result = 0
    errors = 0
    details: list[dict[str, str]] = []

    for item in items:
        item_id = str(item.get("item_id", "")).strip()
        result_rel = str(item.get("expected_result_mp3", "")).strip() or f"results/{item_id}.mp3"
        result_candidates = [(bdir / result_rel).resolve()]
        if handoff_results is not None:
            result_candidates.append((handoff_results / Path(result_rel).name).resolve())
            result_candidates.append((handoff_results / f"{item_id}.mp3").resolve())
        result_mp3 = next((p for p in result_candidates if p.is_file()), None)
        out_rel = str(item.get("expected_output_mp3", "")).strip()
        out_mp3 = (root / out_rel).resolve() if out_rel else None
        if out_mp3 is None:
            errors += 1
            item["status"] = "error"
            details.append({"item_id": item_id, "status": "error", "reason": "missing_expected_output_mp3"})
            continue
        if result_mp3 is None:
            missing_result += 1
            item["status"] = "missing_result"
            details.append(
                {
                    "item_id": item_id,
                    "status": "missing_result",
                    "searched": [str(p) for p in result_candidates],
                }
            )
            continue
        if out_mp3.is_file() and not force:
            skipped_existing += 1
            item["status"] = "skipped_existing"
            details.append({"item_id": item_id, "status": "skipped_existing", "path": str(out_mp3)})
            continue
        try:
            out_mp3.parent.mkdir(parents=True, exist_ok=True)
            out_mp3.write_bytes(result_mp3.read_bytes())
            imported += 1
            item["status"] = "imported"
            details.append({"item_id": item_id, "status": "imported", "path": str(out_mp3)})
        except OSError as exc:
            errors += 1
            item["status"] = "error"
            details.append({"item_id": item_id, "status": "error", "reason": str(exc)})

    manifest["items"] = items
    manifest["updated_at"] = _utc_now_iso()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "batch_id": manifest.get("batch_id", bdir.name),
        "updated_at": manifest["updated_at"],
        "imported": imported,
        "skipped_existing": skipped_existing,
        "missing_result": missing_result,
        "errors": errors,
        "force": bool(force),
        "details": details,
    }
    (bdir / "import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out: dict[str, Any] = {"ok": True, "batch_dir": str(bdir), **report}
    if handoff is not None:
        out["handoff_dir"] = str(handoff)
        out["results_drop_dir"] = str(handoff_results) if handoff_results is not None else ""
    return out


def verify_mp3_coverage(
    root_dir: Path,
    *,
    batch_id: str | None = None,
    handoff_dir: Path | None = None,
    latest: bool = False,
    current: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    site_root = (root / "output" / "site").resolve()
    total_story_dirs = 0
    with_tts_text = 0
    with_mp3 = 0
    missing_mp3 = 0
    skipped_no_tts = 0
    ambiguous_tts = 0

    for folder in _iter_story_dirs(site_root):
        total_story_dirs += 1
        src, err = _resolve_story_tts_source(folder)
        if src is None:
            if err and err.startswith("multiple_tts_text_files"):
                ambiguous_tts += 1
            else:
                skipped_no_tts += 1
            if (folder / f"{folder.name}.mp3").is_file():
                with_mp3 += 1
            continue
        with_tts_text += 1
        if src.expected_output_mp3.is_file():
            with_mp3 += 1
        else:
            missing_mp3 += 1

    out: dict[str, Any] = {
        "ok": True,
        "source_root": str(site_root),
        "total_story_dirs": total_story_dirs,
        "with_tts_text_file": with_tts_text,
        "with_mp3": with_mp3,
        "missing_mp3": missing_mp3,
        "skipped_no_tts_file": skipped_no_tts,
        "ambiguous_tts_files": ambiguous_tts,
    }

    if current:
        current_dir, texts_dir, mp3_dir = _current_paths(root)
        internal_path = current_dir / "internal_manifest.json"
        if not internal_path.is_file():
            out["current"] = {"error": f"current manifest not found: {internal_path}"}
            return out
        try:
            meta = json.loads(internal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            out["current"] = {"error": f"failed to read {internal_path}: {exc}"}
            return out
        mapping = list(meta.get("mapping", []))
        txt_count = len([p for p in texts_dir.glob("*.txt") if p.is_file()])
        expected_mp3 = {str(m.get("expected_mp3", "")).strip() for m in mapping if str(m.get("expected_mp3", "")).strip()}
        found_mp3 = {p.name for p in mp3_dir.glob("*.mp3") if p.is_file()}
        can_import = 0
        missing: list[str] = []
        for m in mapping:
            name = str(m.get("expected_mp3", "")).strip()
            out_rel = str(m.get("expected_output_mp3", "")).strip()
            if not name:
                continue
            if name in found_mp3:
                can_import += 1
            else:
                missing.append(name)
        extra = sorted(found_mp3 - expected_mp3)
        out["current"] = {
            "current_dir": str(current_dir),
            "texts_exported": txt_count,
            "mapping_items": len(mapping),
            "mp3_found": len(found_mp3),
            "can_import": can_import,
            "missing_mp3": len(missing),
            "extra_mp3": len(extra),
            "first_missing": missing[:10],
            "first_extra": extra[:10],
            "batch_id": str(meta.get("batch_id", "")),
        }
        return out

    handoff = _resolve_handoff_dir(root, handoff_dir, latest)
    if (batch_id or "").strip() or handoff is not None:
        bdir = _resolve_batch_dir(root, batch_id, None, handoff=handoff)
        manifest_path = bdir / "manifest.json"
        handoff_results = (handoff / _HANDOFF_RESULTS_DIR).resolve() if handoff is not None else None
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            items = list(manifest.get("items", []))
            results_found_in_batch = 0
            results_found_in_handoff = 0
            already_imported = 0
            missing_item_ids: list[str] = []
            for it in items:
                item_id = str(it.get("item_id", "")).strip()
                rid = str(it.get("expected_result_mp3", "")).strip() or f"results/{item_id}.mp3"
                out_rel = str(it.get("expected_output_mp3", "")).strip()
                in_batch = bool(rid and (bdir / rid).is_file())
                in_handoff = bool(
                    handoff_results is not None
                    and (
                        (handoff_results / Path(rid).name).is_file()
                        or (handoff_results / f"{item_id}.mp3").is_file()
                    )
                )
                if in_batch:
                    results_found_in_batch += 1
                if in_handoff:
                    results_found_in_handoff += 1
                if not in_batch and not in_handoff:
                    missing_item_ids.append(item_id)
                if out_rel and (root / out_rel).is_file():
                    already_imported += 1
            results_found = max(results_found_in_batch, len(items) - len(missing_item_ids))
            waiting_mp3 = max(0, len(items) - already_imported)
            out["batch"] = {
                "batch_id": manifest.get("batch_id", bdir.name),
                "batch_dir": str(bdir),
                "handoff_dir": str(handoff) if handoff is not None else "",
                "exported_items": len(items),
                "results_found": results_found,
                "results_found_in_batch_results": results_found_in_batch,
                "results_found_in_handoff_drop": results_found_in_handoff,
                "already_imported": already_imported,
                "missing_results": max(0, len(items) - results_found),
                "waiting_mp3": waiting_mp3,
                "first_problems": missing_item_ids[:10],
            }
        else:
            out["batch"] = {"batch_id": str(batch_id), "error": f"manifest not found: {manifest_path}"}
    return out
