from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _print(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class RunnerConfig:
    input_zip: Path
    output_dir: Path
    dry_run: bool
    default_voice: str
    voice_m: str
    voice_f: str
    voice_u: str
    speed: float
    force_cpu: bool


def _safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pick_voice(item: dict[str, Any], cfg: RunnerConfig) -> str:
    vt = str(item.get("voice_type", "U") or "U").upper()[:1]
    if vt == "M":
        return cfg.voice_m
    if vt == "F":
        return cfg.voice_f
    if vt == "U":
        return cfg.voice_u
    return cfg.default_voice


def _lang_code_from_voice(voice: str, fallback: str = "a") -> str:
    if not voice:
        return fallback
    c = voice.strip().lower()[:1]
    return c if c in "abefhijpz" else fallback


def _expected_result_basename(item: dict[str, Any]) -> str:
    rel = str(item.get("expected_result_mp3", "") or "").strip()
    if rel:
        return Path(rel).name
    item_id = str(item.get("item_id", "") or "").strip() or "item_unknown"
    return f"{item_id}.mp3"


def _load_chunks(batch_root: Path, item: dict[str, Any]) -> list[str]:
    chunks_dir = str(item.get("chunks_dir", "") or "").strip()
    if not chunks_dir:
        return []
    full = (batch_root / chunks_dir).resolve()
    if not full.is_dir():
        return []
    out: list[str] = []
    for p in sorted(full.glob("chunk_*.txt")):
        txt = _safe_read_text(p).strip()
        if txt:
            out.append(txt)
    return out


def _zip_results(results_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(results_dir.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(results_dir)).replace("\\", "/"))


def _ensure_kokoro(force_cpu: bool) -> tuple[Any, Any]:
    try:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from kokoro import KPipeline  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Kokoro dependencies are missing. Install in Colab: "
            "`pip install kokoro soundfile numpy torch` and ensure ffmpeg is available."
        ) from exc

    if force_cpu:
        try:
            torch.set_default_device("cpu")
        except Exception:
            pass
    return np, KPipeline


def _synthesize_item_mp3(
    batch_root: Path,
    results_dir: Path,
    item: dict[str, Any],
    cfg: RunnerConfig,
) -> tuple[str, str]:
    item_id = str(item.get("item_id", "") or "").strip() or "item_unknown"
    out_name = _expected_result_basename(item)
    out_mp3 = results_dir / out_name
    chunks = _load_chunks(batch_root, item)
    if not chunks:
        return "error", "chunks are empty or missing"

    voice = _pick_voice(item, cfg)
    lang = str(item.get("kokoro_lang_code", "") or "").strip().lower()[:1] or _lang_code_from_voice(voice, "a")

    np, KPipeline = _ensure_kokoro(cfg.force_cpu)
    import soundfile as sf  # type: ignore

    pipe = KPipeline(lang_code=lang)
    merged_parts: list[Any] = []
    for ch in chunks:
        local_parts: list[Any] = []
        for _gs, _ps, audio in pipe(ch, voice=voice, speed=float(cfg.speed), split_pattern=r"\n+"):
            arr = np.asarray(audio, dtype=np.float32).reshape(-1)
            if arr.size:
                local_parts.append(arr)
        if not local_parts:
            return "error", f"empty audio for {item_id}"
        chunk_audio = np.concatenate(local_parts) if len(local_parts) > 1 else local_parts[0]
        merged_parts.append(chunk_audio)
    if not merged_parts:
        return "error", "no audio generated"

    merged = np.concatenate(merged_parts) if len(merged_parts) > 1 else merged_parts[0]
    wav_tmp = results_dir / f"{item_id}.wav"
    sf.write(str(wav_tmp), merged, 24000)

    cmd = ["ffmpeg", "-y", "-i", str(wav_tmp), "-codec:a", "libmp3lame", "-qscale:a", "2", str(out_mp3)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        wav_tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
    except OSError:
        pass
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return "error", f"ffmpeg failed: {err[:500]}"
    return "ok", f"written {out_mp3.name}"


def run(cfg: RunnerConfig) -> int:
    if not cfg.input_zip.is_file():
        _print(f"[ERROR] input zip not found: {cfg.input_zip}")
        return 2

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    results_dir = cfg.output_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_csv = cfg.output_dir / "results_report.csv"
    results_zip = cfg.output_dir / "kokoro_results.zip"

    with tempfile.TemporaryDirectory(prefix="kokoro_colab_") as td:
        temp_dir = Path(td).resolve()
        try:
            with zipfile.ZipFile(cfg.input_zip, "r") as zf:
                zf.extractall(temp_dir)
        except Exception as exc:
            _print(f"[ERROR] cannot unzip input: {exc}")
            return 2

        manifest_path = temp_dir / "manifest.json"
        if not manifest_path.is_file():
            _print(f"[ERROR] manifest.json not found in zip: {cfg.input_zip}")
            return 2

        try:
            manifest = json.loads(_safe_read_text(manifest_path))
        except Exception as exc:
            _print(f"[ERROR] cannot parse manifest.json: {exc}")
            return 2

        items = list(manifest.get("items", []))
        if not items:
            _print("[WARN] manifest has 0 items. Nothing to process.")
            with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["item_id", "story_id", "expected_result_filename", "status", "message"])
            _zip_results(results_dir, results_zip)
            _print(f"[OK] empty run: {results_zip}")
            return 0

        rows: list[list[str]] = []
        for idx, item in enumerate(items, start=1):
            item_id = str(item.get("item_id", "") or "").strip() or f"item_{idx:06d}"
            story_id = str(item.get("story_id", "") or "").strip()
            out_name = _expected_result_basename(item)
            try:
                if cfg.dry_run:
                    chunks = _load_chunks(temp_dir, item)
                    if not chunks:
                        status, message = "error", "chunks are empty or missing"
                    else:
                        status, message = "dry-run", f"validated ({len(chunks)} chunks)"
                else:
                    status, message = _synthesize_item_mp3(temp_dir, results_dir, item, cfg)
            except Exception as exc:  # pragma: no cover
                status, message = "error", f"exception: {exc}"
            rows.append([item_id, story_id, out_name, status, message])
            _print(f"[{idx}/{len(items)}] {item_id}: {status} | {message}")

        with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["item_id", "story_id", "expected_result_filename", "status", "message"])
            w.writerows(rows)

        _zip_results(results_dir, results_zip)

        ok = sum(1 for r in rows if r[3] in {"ok", "dry-run"})
        err = sum(1 for r in rows if r[3] == "error")
        _print(f"[DONE] total={len(rows)} ok={ok} error={err}")
        _print(f"[DONE] results_dir={results_dir}")
        _print(f"[DONE] report_csv={report_csv}")
        _print(f"[DONE] results_zip={results_zip}")
        return 0 if err == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Kokoro Colab runner for Content-Factory handoff zip.")
    p.add_argument("--input-zip", type=Path, required=True, help="Path to 02_UPLOAD_THIS_TO_COLAB.zip")
    p.add_argument("--output-dir", type=Path, default=Path("./kokoro_output"), help="Output folder for results/report/zip")
    p.add_argument("--dry-run", action="store_true", help="Validate manifest/chunks without synthesis")
    p.add_argument("--default-voice", default="af_heart")
    p.add_argument("--voice-m", default="am_adam")
    p.add_argument("--voice-f", default="af_heart")
    p.add_argument("--voice-u", default="af_heart")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--force-cpu", action="store_true")
    args = p.parse_args()

    cfg = RunnerConfig(
        input_zip=args.input_zip.resolve(),
        output_dir=args.output_dir.resolve(),
        dry_run=bool(args.dry_run),
        default_voice=str(args.default_voice),
        voice_m=str(args.voice_m),
        voice_f=str(args.voice_f),
        voice_u=str(args.voice_u),
        speed=float(args.speed),
        force_cpu=bool(args.force_cpu),
    )
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
