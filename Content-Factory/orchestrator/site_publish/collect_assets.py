from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.site_publish.paths import (
    describe_layout,
    is_run_scoped,
    resolve_launch_dir,
    resolve_site_publish_root,
    site_publish_manifest_path,
)
from orchestrator.site_publish.prepare import SUPPORTED_IMAGE_EXTS, _sanitize_folder_name
from orchestrator.site_tts.colab_batch import (
    _drive_dir_from,
    _load_expected_files,
    _read_manual_skipped,
    _split_story_voice,
)
from orchestrator.site_tts.config import load_site_tts_settings


SUPPORTED_TEXT_EXTS = {".txt"}
SUPPORTED_AUDIO_EXTS = {".mp3"}
COPY_RETRIES = 2
COPY_RETRY_SLEEP_SECONDS = 2.0
COPY_TIMEOUT_SECONDS = 90
COPY_ABORT_STREAK = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _norm_key(value: str) -> str:
    cleaned = _sanitize_folder_name(value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().casefold()
    return cleaned


def _strip_voice_suffix(stem: str) -> str:
    story, _voice = _split_story_voice(stem)
    return story


def _first_existing(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.is_file():
            return path
    return None


def _copy2_with_retries(src: Path, dst: Path) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, COPY_RETRIES + 1):
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{time.time_ns()}.tmpcopy")
        try:
            if src.suffix.lower() == ".mp3":
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import shutil, sys; shutil.copy2(sys.argv[1], sys.argv[2])",
                        str(src),
                        str(tmp),
                    ],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=COPY_TIMEOUT_SECONDS,
                )
                if proc.returncode != 0:
                    err = ((proc.stderr or "") + (proc.stdout or "")).strip()[:500]
                    raise RuntimeError(err or f"copy subprocess failed exit={proc.returncode}")
            else:
                shutil.copy2(src, tmp)
            tmp.replace(dst)
            return
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            last_exc = exc
            if attempt >= COPY_RETRIES:
                break
            time.sleep(COPY_RETRY_SLEEP_SECONDS)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    if last_exc is not None:
        raise last_exc


def _needs_copy(src: Path, dst: Path, *, force: bool) -> bool:
    if force or not dst.exists():
        return True
    try:
        return src.stat().st_size != dst.stat().st_size
    except OSError:
        return True


def _ascii_safe(value: str) -> str:
    return value.encode("ascii", "backslashreplace").decode("ascii")


def _collect_by_story_key(root: Path, pattern: str, *, strip_voice_suffix: bool) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob(pattern), key=lambda p: str(p).lower()):
        if not path.is_file():
            continue
        stem = _strip_voice_suffix(path.stem) if strip_voice_suffix else path.stem
        out.setdefault(_norm_key(stem), path)
    return out


def _candidate_launches(root: Path, explicit_launch: Path | None) -> list[Path]:
    if explicit_launch is not None:
        launch = explicit_launch if explicit_launch.is_absolute() else root / explicit_launch
        return [launch.resolve()] if launch.is_dir() else []
    launches_root = root / "Запуски"
    if not launches_root.is_dir():
        return []
    candidates = [p for p in launches_root.iterdir() if p.is_dir()]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _pick_launch(
    root: Path,
    expected_stories: set[str],
    explicit_launch: Path | None,
    *,
    require_explicit: bool = False,
) -> tuple[Path | None, dict[str, Any]]:
    """Best-effort launch picker.

    Если ``require_explicit`` — берём только explicit_launch (без авто-подбора по manifest),
    чтобы run-scoped режим не молча подменял launch на чужой.
    """
    if require_explicit:
        if explicit_launch is None:
            return None, {"reason": "explicit_launch_required_but_missing"}
        cand = explicit_launch if explicit_launch.is_absolute() else root / explicit_launch
        cand = cand.resolve()
        if cand.is_dir():
            return cand, {"reason": "explicit_launch_dir_used", "manifest_path": str(cand / "manifest.json")}
        return None, {"reason": "explicit_launch_dir_missing", "expected_path": str(cand)}

    best: tuple[int, Path | None, dict[str, Any]] = (-1, None, {})
    expected_keys = {_norm_key(name) for name in expected_stories}
    for launch in _candidate_launches(root, explicit_launch):
        manifest = _read_json(launch / "manifest.json")
        manifest_stories = manifest.get("story_ids") if isinstance(manifest.get("story_ids"), list) else []
        manifest_keys = {_norm_key(str(item)) for item in manifest_stories}
        score = len(expected_keys & manifest_keys)
        if score > best[0]:
            best = (score, launch, {"manifest_story_matches": score, "manifest_path": str(launch / "manifest.json")})
    return best[1], best[2]


def _text_candidates(launch: Path | None, story: str, txt_name: str) -> list[Path]:
    if launch is None:
        return []
    return [
        launch / "02_Сайт" / "01_Очистка_текста" / story / txt_name,
        launch / "05_Рассказы" / story / "03_Сайт" / "01_Очистка_текста" / txt_name,
        launch / "05_Рассказы" / story / "01_Очистка_текста" / txt_name,
    ]


def _info_candidates(launch: Path | None, story: str) -> list[Path]:
    if launch is None:
        return []
    return [
        launch / "05_Рассказы" / story / "03_Сайт" / "02_Информация_для_сайта" / "info.txt",
        launch / "02_Сайт" / "02_Информация_для_сайта_Gemini" / story / "info.txt",
        launch / "02_Сайт" / "02_Информация_для_сайта" / story / "info.txt",
    ]


def _site_info_candidates(launch: Path | None, story: str) -> list[Path]:
    if launch is None:
        return []
    return [
        launch / "05_Рассказы" / story / "03_Сайт" / "02_Информация_для_сайта" / "site_info.json",
        launch / "02_Сайт" / "02_Информация_для_сайта_Gemini" / story / "site_info.json",
        launch / "02_Сайт" / "02_Информация_для_сайта" / story / "site_info.json",
    ]


def _image_roots(root: Path, launch: Path | None, images_dir: Path | None) -> list[Path]:
    roots: list[Path] = []
    if images_dir is not None:
        roots.append((images_dir if images_dir.is_absolute() else root / images_dir).resolve())
    roots.append((root / "input" / "site_visual_import").resolve())
    if launch is not None:
        roots.extend(
            [
                launch / "02_Сайт" / "03_Визуал_для_сайта" / "Обложки_ЗАГРУЗИТЕ_СЮДА",
                launch / "02_Сайт",
                launch / "05_Рассказы",
                launch / "06_Отчёты",
            ]
        )
    seen: set[str] = set()
    unique: list[Path] = []
    for item in roots:
        key = str(item.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item.resolve())
    return unique


def _collect_images(root: Path, launch: Path | None, images_dir: Path | None) -> tuple[dict[str, Path], list[str]]:
    images: dict[str, Path] = {}
    scanned_roots: list[str] = []
    for image_root in _image_roots(root, launch, images_dir):
        if not image_root.is_dir():
            continue
        scanned_roots.append(str(image_root))
        for path in sorted(image_root.rglob("*"), key=lambda p: str(p).lower()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                continue
            story_stem = _strip_voice_suffix(path.stem)
            images.setdefault(_norm_key(story_stem), path)
    return images, scanned_roots


def run_site_publish_collect_assets(
    root_dir: Path,
    *,
    execute: bool = False,
    force: bool = False,
    allow_partial_tts: bool = False,
    launch_name: str = "",
    launch_dir: Path | None = None,
    images_dir: Path | None = None,
) -> dict[str, Any]:
    root = root_dir.resolve()
    settings = load_site_tts_settings(root)
    job_dir = _drive_dir_from(root, settings, "job", "job")
    mp3_dir = _drive_dir_from(root, settings, "mp3", "mp3")
    texts_dir = _drive_dir_from(root, settings, "texts", "texts")
    report_path = (root / ".orchestrator" / "site_publish_collect_assets_report.json").resolve()

    expected = _load_expected_files(job_dir)
    expected_set = set(expected)
    manual_skipped = _read_manual_skipped(job_dir)
    job_payload = _read_json(job_dir / "kokoro_voices_job.json")
    job_items = job_payload.get("items") if isinstance(job_payload.get("items"), list) else []
    expected_stories = {_split_story_voice(Path(name).stem)[0] for name in expected}

    run_scoped_requested = is_run_scoped(launch_name=launch_name, launch_dir=launch_dir)
    explicit_launch: Path | None
    if launch_dir is not None:
        explicit_launch = launch_dir
    elif launch_name.strip():
        explicit_launch = resolve_launch_dir(root, launch_name=launch_name) or (root / "Запуски" / launch_name.strip())
    else:
        explicit_launch = None
    launch, launch_diag = _pick_launch(
        root,
        expected_stories,
        explicit_launch,
        require_explicit=run_scoped_requested,
    )
    output_site = resolve_site_publish_root(root, launch)
    layout_info = describe_layout(root, launch_name=launch_name, launch_dir=launch_dir)
    manifest_path = site_publish_manifest_path(root, launch)

    if run_scoped_requested and launch is None:
        payload_err: dict[str, Any] = {
            "ok": False,
            "mode": "dry-run" if not execute else "execute",
            "execute": bool(execute),
            "force": bool(force),
            "allow_partial_tts": bool(allow_partial_tts),
            "written_at": _now_iso(),
            "reason": "launch_not_found_for_run_scoped_request",
            "launch_name": (launch_name or "").strip(),
            "launch_diag": launch_diag,
            "layout": layout_info,
            "expected_total": len(expected_set),
            "report_path": str(report_path),
        }
        _write_json(report_path, payload_err)
        return payload_err

    item_by_mp3: dict[str, dict[str, Any]] = {}
    for item in job_items:
        if not isinstance(item, dict):
            continue
        mp3_name = Path(str(item.get("mp3_name") or "")).name
        if mp3_name:
            item_by_mp3[mp3_name] = item

    image_index, image_roots = _collect_images(root, launch, images_dir)
    drive_text_index = _collect_by_story_key(texts_dir, "*.txt", strip_voice_suffix=True)

    items: list[dict[str, Any]] = []
    mp3_found = 0
    text_found = 0
    info_found = 0
    images_found = 0
    packages_ready = 0
    packages_created = 0
    skipped_tts = 0
    missing_audio = 0
    missing_image = 0
    missing_text = 0
    missing_info = 0
    copy_failed = 0
    consecutive_copy_failed = 0
    aborted = False
    abort_reason = ""
    copy_failed_items: list[dict[str, str]] = []
    real_missing_audio: list[str] = []

    for index, mp3_name in enumerate(expected, start=1):
        job_item = item_by_mp3.get(mp3_name, {})
        story_raw = str(job_item.get("story_folder") or _split_story_voice(Path(mp3_name).stem)[0]).strip()
        story_slug = _sanitize_folder_name(story_raw)
        txt_name = Path(str(job_item.get("txt_name") or Path(mp3_name).with_suffix(".txt").name)).name
        voice_label = str(job_item.get("voice_label") or _split_story_voice(Path(mp3_name).stem)[1] or "U").strip()[:1] or "U"
        story_key = _norm_key(story_raw)
        mp3_path = mp3_dir / mp3_name
        text_path = _first_existing(_text_candidates(launch, story_raw, txt_name))
        if text_path is None:
            text_path = drive_text_index.get(story_key) or (texts_dir / txt_name if (texts_dir / txt_name).is_file() else None)
        info_path = _first_existing(_info_candidates(launch, story_raw))
        site_info_path = _first_existing(_site_info_candidates(launch, story_raw))
        image_path = image_index.get(story_key)

        has_mp3 = mp3_path.is_file() and mp3_path.stat().st_size > 0
        has_text = text_path is not None and text_path.is_file()
        has_info = info_path is not None and info_path.is_file()
        has_image = image_path is not None and image_path.is_file()
        is_skipped_tts = mp3_name in manual_skipped

        if is_skipped_tts and not has_mp3:
            skipped_tts += 1
            status = "skipped_tts_manual"
        else:
            if has_mp3:
                mp3_found += 1
            else:
                missing_audio += 1
                real_missing_audio.append(mp3_name)
            if has_text:
                text_found += 1
            else:
                missing_text += 1
            if has_info:
                info_found += 1
            else:
                missing_info += 1
            if has_image:
                images_found += 1
            else:
                missing_image += 1
            status = "ready" if has_mp3 and has_text and has_info and has_image else "incomplete"

        package_dir = output_site / story_slug
        copy_error = ""
        if status != "skipped_tts_manual" and has_mp3 and has_text and has_info and has_image:
            packages_created += 1
            if not execute:
                packages_ready += 1
            else:
                try:
                    package_dir.mkdir(parents=True, exist_ok=True)
                    text_dst = package_dir / f"{story_slug}__{voice_label}.txt"
                    info_dst = package_dir / "info.txt"
                    audio_dst = package_dir / f"{story_slug}.mp3"
                    if _needs_copy(text_path, text_dst, force=force):
                        _copy2_with_retries(text_path, text_dst)
                    if _needs_copy(info_path, info_dst, force=force):
                        _copy2_with_retries(info_path, info_dst)
                    if _needs_copy(mp3_path, audio_dst, force=force):
                        _copy2_with_retries(mp3_path, audio_dst)
                    image_dst = ""
                    if has_image and image_path is not None:
                        final_image = package_dir / f"{story_slug}{image_path.suffix.lower()}"
                        if _needs_copy(image_path, final_image, force=force):
                            _copy2_with_retries(image_path, final_image)
                        image_dst = str(final_image)
                    manifest = {
                        "story_id": story_raw,
                        "story_slug": story_slug,
                        "expected_mp3_name": mp3_name,
                        "source_text": str(text_path),
                        "source_info": str(info_path),
                        "source_audio": str(mp3_path),
                        "source_image": str(image_path) if image_path else "",
                        "text": str(text_dst),
                        "info": str(info_dst),
                        "audio": str(audio_dst),
                        "image": image_dst,
                        "site_info_json": str(site_info_path) if site_info_path else "",
                        "collected_at": _now_iso(),
                    }
                    _write_json(package_dir / "story_manifest.json", manifest)
                    if site_info_path is not None and site_info_path.is_file():
                        site_info_dst = package_dir / "site_info.json"
                        if _needs_copy(site_info_path, site_info_dst, force=force):
                            _copy2_with_retries(site_info_path, site_info_dst)
                    packages_ready += 1
                    consecutive_copy_failed = 0
                except Exception as exc:
                    copy_failed += 1
                    consecutive_copy_failed += 1
                    copy_error = f"{type(exc).__name__}: {exc}"
                    status = "copy_failed"
                    copy_failed_items.append(
                        {
                            "story": story_raw,
                            "expected_mp3_name": mp3_name,
                            "copy_error": copy_error[:1000],
                        }
                    )
                    if consecutive_copy_failed >= COPY_ABORT_STREAK:
                        aborted = True
                        abort_reason = (
                            f"aborted_after_{consecutive_copy_failed}_consecutive_copy_failed; "
                            "Google Drive source appears unavailable or throttled"
                        )
        if execute and (index == 1 or index % 25 == 0 or status in {"copy_failed", "skipped_tts_manual"}):
            suffix = f" error={_ascii_safe(copy_error[:180])}" if copy_error else ""
            print(f"[collect-assets] {index}/{len(expected)} {_ascii_safe(story_slug)}: {status}{suffix}", flush=True)
        if aborted:
            break

        missing = []
        if not has_mp3:
            missing.append("audio")
        if not has_text:
            missing.append("text")
        if not has_info:
            missing.append("info")
        if not has_image:
            missing.append("image")
        items.append(
            {
                "story": story_raw,
                "story_slug": story_slug,
                "expected_mp3_name": mp3_name,
                "status": status,
                "missing": missing,
                "copy_error": copy_error,
                "manual_skipped": is_skipped_tts,
                "package_dir": str(package_dir),
                "text_path": str(text_path) if text_path else "",
                "info_path": str(info_path) if info_path else "",
                "site_info_path": str(site_info_path) if site_info_path else "",
                "mp3_path": str(mp3_path) if has_mp3 else "",
                "image_path": str(image_path) if image_path else "",
            }
        )

    real_missing_audio = sorted([name for name in real_missing_audio if name not in manual_skipped], key=str.lower)
    payload: dict[str, Any] = {
        "ok": True,
        "mode": "execute" if execute else "dry-run",
        "execute": bool(execute),
        "force": bool(force),
        "allow_partial_tts": bool(allow_partial_tts),
        "written_at": _now_iso(),
        "job_dir": str(job_dir),
        "texts_dir": str(texts_dir),
        "mp3_dir": str(mp3_dir),
        "launch_dir": str(launch) if launch else "",
        "launch_diag": launch_diag,
        "layout": layout_info,
        "manifest_path": str(manifest_path),
        "image_roots_scanned": image_roots,
        "output_dir_scanned_by_prepare": str(output_site),
        "expected_total": len(expected_set),
        "mp3_found": mp3_found,
        "images_found": images_found,
        "text_found": text_found,
        "info_found": info_found,
        "packages_created": packages_created,
        "packages_ready": packages_ready,
        "skipped_tts": skipped_tts,
        "missing_audio": len(real_missing_audio),
        "missing_image": missing_image,
        "missing_text": missing_text,
        "missing_info": missing_info,
        "copy_failed": copy_failed,
        "aborted": aborted,
        "abort_reason": abort_reason,
        "copy_failed_sample": copy_failed_items[:25],
        "real_missing_audio_files": real_missing_audio[:50],
        "manual_skipped_files": sorted(manual_skipped.keys(), key=str.lower),
        "can_run_prepare": packages_ready > 0,
        "items": items,
        "report_path": str(report_path),
    }
    _write_json(report_path, payload)
    if launch is not None:
        _write_json(
            manifest_path,
            {
                "stage": "collect_assets",
                "launch_name": (launch_name or "").strip() or launch.name,
                "launch_dir": str(launch),
                "site_publish_root": str(output_site),
                "stories_count": len(expected_set),
                "collected_count": packages_ready,
                "missing_assets_count": int(missing_image + missing_text + missing_info + len(real_missing_audio)),
                "skipped_tts_count": skipped_tts,
                "manual_skipped_files": sorted(manual_skipped.keys(), key=str.lower),
                "mode": "execute" if execute else "dry-run",
                "generated_at": _now_iso(),
                "source_paths": {
                    "job_dir": str(job_dir),
                    "texts_dir": str(texts_dir),
                    "mp3_dir": str(mp3_dir),
                    "image_roots_scanned": image_roots,
                },
                "report_path": str(report_path),
            },
        )
    return payload
