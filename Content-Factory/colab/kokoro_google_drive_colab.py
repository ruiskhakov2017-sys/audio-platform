"""
Google Colab one-code runner for Content-Factory Kokoro workflow.

Usage in Colab:
1) Upload this script into /content (or clone repo and run from there).
2) Run:
   !python kokoro_google_drive_colab.py
"""

from __future__ import annotations

import csv
from pathlib import Path


# Set True if you want to regenerate existing mp3 files.
FORCE = False

# Google Drive folders (after drive mount).
TEXTS_DIR = "/content/drive/MyDrive/ContentFactory_TTS/texts"
MP3_DIR = "/content/drive/MyDrive/ContentFactory_TTS/mp3"
REPORT_CSV = "/content/drive/MyDrive/ContentFactory_TTS/results_report.csv"

# Kokoro voices for M/F/U suffix
VOICE_M = "am_adam"
VOICE_F = "af_heart"
VOICE_U = "af_heart"
SPEED = 1.0


def _pick_voice_from_stem(stem: str) -> str:
    if "__" in stem:
        suffix = stem.rsplit("__", 1)[-1].strip().upper()[:1]
        if suffix == "M":
            return VOICE_M
        if suffix == "F":
            return VOICE_F
        if suffix == "U":
            return VOICE_U
    return VOICE_U


def main() -> int:
    from google.colab import drive  # type: ignore

    print("Mounting Google Drive...")
    drive.mount("/content/drive")

    texts_dir = Path(TEXTS_DIR)
    mp3_dir = Path(MP3_DIR)
    report_csv = Path(REPORT_CSV)
    texts_dir.mkdir(parents=True, exist_ok=True)
    mp3_dir.mkdir(parents=True, exist_ok=True)
    report_csv.parent.mkdir(parents=True, exist_ok=True)

    txt_files = sorted([p for p in texts_dir.glob("*.txt") if p.is_file()])
    if not txt_files:
        print(f"[WARN] No txt files found in {texts_dir}")
        with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "status", "message"])
        return 0

    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
        from kokoro import KPipeline  # type: ignore
    except Exception:
        raise RuntimeError(
            "Install dependencies in Colab first: `pip install kokoro soundfile numpy` "
            "and ensure ffmpeg is available."
        )

    rows: list[list[str]] = []
    ok = 0
    err = 0
    for idx, txt in enumerate(txt_files, start=1):
        mp3_out = mp3_dir / f"{txt.stem}.mp3"
        if mp3_out.is_file() and not FORCE:
            rows.append([txt.name, "skipped_existing", "mp3 already exists"])
            print(f"[{idx}/{len(txt_files)}] {txt.name}: skipped_existing")
            continue
        try:
            text = txt.read_text(encoding="utf-8").strip()
            if not text:
                rows.append([txt.name, "error", "empty txt"])
                err += 1
                continue
            voice = _pick_voice_from_stem(txt.stem)
            lang = voice.strip().lower()[:1] if voice and voice.strip().lower()[:1] in "abefhijpz" else "a"
            pipe = KPipeline(lang_code=lang)
            parts = []
            for _gs, _ps, audio in pipe(text, voice=voice, speed=float(SPEED), split_pattern=r"\n+"):
                arr = np.asarray(audio, dtype=np.float32).reshape(-1)
                if arr.size:
                    parts.append(arr)
            if not parts:
                rows.append([txt.name, "error", "kokoro returned empty audio"])
                err += 1
                continue
            merged = np.concatenate(parts) if len(parts) > 1 else parts[0]
            wav_tmp = mp3_dir / f"{txt.stem}.wav"
            sf.write(str(wav_tmp), merged, 24000)

            import subprocess

            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_tmp), "-codec:a", "libmp3lame", "-qscale:a", "2", str(mp3_out)],
                capture_output=True,
                text=True,
            )
            try:
                wav_tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass
            if proc.returncode != 0:
                rows.append([txt.name, "error", f"ffmpeg failed: {(proc.stderr or proc.stdout or '')[:300]}"])
                err += 1
                continue
            rows.append([txt.name, "ok", f"written {mp3_out.name}"])
            ok += 1
            print(f"[{idx}/{len(txt_files)}] {txt.name}: ok")
        except Exception as exc:  # pragma: no cover
            rows.append([txt.name, "error", str(exc)])
            err += 1
            print(f"[{idx}/{len(txt_files)}] {txt.name}: error {exc}")

    with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "message"])
        w.writerows(rows)

    print(f"[DONE] ok={ok} error={err}")
    print(f"[DONE] mp3_dir={mp3_dir}")
    print(f"[DONE] report_csv={report_csv}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
