from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.text_chunking import pack_paragraph_chunks


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
                "1. Zip this batch folder and upload to Colab.",
                "2. Check GPU: `!nvidia-smi`",
                "3. Check torch CUDA in Colab:",
                "   - `import torch`",
                "   - `print(torch.cuda.is_available())`",
                "4. Install Kokoro dependencies in Colab (`kokoro`, `soundfile`, ffmpeg).",
                "5. Unpack batch and open `manifest.json`.",
                "6. For each item:",
                "   - Read `chunks/<item_id>/chunk_*.txt` in order.",
                "   - Synthesize with Kokoro on GPU.",
                "   - Merge chunks and save `results/<item_id>.mp3`.",
                "7. Zip `results/` (and optionally updated manifest) and download.",
                "8. Put results back into this batch folder locally.",
                "9. Run local import:",
                "   - `python -m orchestrator site-tts kokoro-colab import --batch-id <batch_id>`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "batch_id": batch,
        "batch_dir": str(batch_root),
        "exported": len(items),
        "skipped": len(skipped),
        "manifest_path": str(batch_root / "manifest.json"),
    }


def _resolve_batch_dir(root: Path, batch_id: str | None, batch_dir: Path | None) -> Path:
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
    force: bool = False,
) -> dict[str, Any]:
    root = root_dir.resolve()
    bdir = _resolve_batch_dir(root, batch_id, batch_dir)
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "message": f"manifest not found: {manifest_path}"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest.get("items", []))

    imported = 0
    skipped_existing = 0
    missing_result = 0
    errors = 0
    details: list[dict[str, str]] = []

    for item in items:
        item_id = str(item.get("item_id", "")).strip()
        result_rel = str(item.get("expected_result_mp3", "")).strip() or f"results/{item_id}.mp3"
        result_mp3 = (bdir / result_rel).resolve()
        out_rel = str(item.get("expected_output_mp3", "")).strip()
        out_mp3 = (root / out_rel).resolve() if out_rel else None
        if out_mp3 is None:
            errors += 1
            item["status"] = "error"
            details.append({"item_id": item_id, "status": "error", "reason": "missing_expected_output_mp3"})
            continue
        if not result_mp3.is_file():
            missing_result += 1
            item["status"] = "missing_result"
            details.append({"item_id": item_id, "status": "missing_result", "path": str(result_mp3)})
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
    return {"ok": True, "batch_dir": str(bdir), **report}


def verify_mp3_coverage(
    root_dir: Path,
    *,
    batch_id: str | None = None,
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

    if (batch_id or "").strip():
        bdir = (root / "runs" / "tts_colab_batches" / str(batch_id).strip()).resolve()
        manifest_path = bdir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            items = list(manifest.get("items", []))
            results_found = 0
            already_imported = 0
            for it in items:
                rid = str(it.get("expected_result_mp3", "")).strip()
                out_rel = str(it.get("expected_output_mp3", "")).strip()
                if rid and (bdir / rid).is_file():
                    results_found += 1
                if out_rel and (root / out_rel).is_file():
                    already_imported += 1
            out["batch"] = {
                "batch_id": manifest.get("batch_id", bdir.name),
                "batch_dir": str(bdir),
                "exported_items": len(items),
                "results_found": results_found,
                "already_imported": already_imported,
                "missing_results": max(0, len(items) - results_found),
            }
        else:
            out["batch"] = {"batch_id": str(batch_id), "error": f"manifest not found: {manifest_path}"}
    return out
