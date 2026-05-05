"""
Google Colab one-file workflow for ContentFactory_TTS Google Drive folders.

Usage in Colab:
1) Upload this file to /content
2) Run:
   !python /content/kokoro_google_drive_colab.py
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from google.colab import drive  # type: ignore


FORCE = False
SPEED = 0.92
VOICE_M = "am_michael"
VOICE_F = "af_bella"
VOICE_U = "af_heart"


def pick_voice_from_name(stem: str) -> str:
    up = stem.upper()
    if up.endswith("__M"):
        return VOICE_M
    if up.endswith("__F"):
        return VOICE_F
    if up.endswith("__U"):
        return VOICE_U
    return VOICE_U


def ensure_deps() -> None:
    subprocess.run(["pip", "install", "-q", "kokoro", "soundfile", "numpy"], check=False)


def main() -> None:
    drive.mount("/content/drive")
    ensure_deps()

    from kokoro import KPipeline  # type: ignore
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore

    root = Path("/content/drive/MyDrive/ContentFactory_TTS")
    texts_dir = root / "texts"
    mp3_dir = root / "mp3"
    mp3_dir.mkdir(parents=True, exist_ok=True)

    txt_files = sorted(texts_dir.glob("*.txt"))
    print(f"texts_dir={texts_dir}")
    print(f"mp3_dir={mp3_dir}")
    print(f"txt_count={len(txt_files)}")

    report = []
    for i, txt in enumerate(txt_files, start=1):
        stem = txt.stem
        out_mp3 = mp3_dir / f"{stem}.mp3"
        if out_mp3.exists() and not FORCE:
            msg = "skip existing"
            print(f"[{i}/{len(txt_files)}] {stem}: {msg}")
            report.append((stem, "skipped", msg))
            continue
        text = txt.read_text(encoding="utf-8").strip()
        if not text:
            msg = "empty txt"
            print(f"[{i}/{len(txt_files)}] {stem}: {msg}")
            report.append((stem, "error", msg))
            continue
        voice = pick_voice_from_name(stem)
        lang = voice[:1].lower() if voice[:1].lower() in "abefhijpz" else "a"
        try:
            pipe = KPipeline(lang_code=lang)
            parts = []
            for _gs, _ps, audio in pipe(text, voice=voice, speed=float(SPEED), split_pattern=r"\n+"):
                arr = np.asarray(audio, dtype=np.float32).reshape(-1)
                if arr.size:
                    parts.append(arr)
            if not parts:
                raise RuntimeError("empty audio")
            merged = np.concatenate(parts) if len(parts) > 1 else parts[0]
            wav_tmp = mp3_dir / f"{stem}.wav"
            sf.write(str(wav_tmp), merged, 24000)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_tmp), "-codec:a", "libmp3lame", "-qscale:a", "2", str(out_mp3)],
                capture_output=True,
                text=True,
            )
            wav_tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")
            msg = "ok"
            print(f"[{i}/{len(txt_files)}] {stem}: {msg}")
            report.append((stem, "ok", msg))
        except Exception as exc:
            msg = f"error: {exc}"
            print(f"[{i}/{len(txt_files)}] {stem}: {msg}")
            report.append((stem, "error", msg))

    ok = sum(1 for _, st, _ in report if st == "ok")
    err = sum(1 for _, st, _ in report if st == "error")
    skip = sum(1 for _, st, _ in report if st == "skipped")
    print(f"done ok={ok} skipped={skip} error={err}")


if __name__ == "__main__":
    main()
