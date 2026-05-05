"""Google Drive Kokoro Colab runner (job-aware, wait-for-texts)."""

from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


# Set True if you want to regenerate existing mp3 files.
FORCE = False

ROOT_DIR = "/content/drive/MyDrive/ContentFactory_TTS"
TEXTS_DIR = f"{ROOT_DIR}/texts"
MP3_DIR = f"{ROOT_DIR}/mp3"
LOGS_DIR = f"{ROOT_DIR}/logs"
JOB_DIR = f"{ROOT_DIR}/job"
REPORT_CSV = f"{LOGS_DIR}/results_report.csv"
EXPECTED_COUNT_TXT = f"{JOB_DIR}/EXPECTED_COUNT.txt"
EXPECTED_FILES_TXT = f"{JOB_DIR}/EXPECTED_FILES.txt"
COLAB_STATUS_JSON = f"{JOB_DIR}/COLAB_STATUS.json"

# Kokoro voices for M/F/U suffix
VOICE_M = "am_adam"
VOICE_F = "af_heart"
VOICE_U = "af_heart"
SPEED = 1.0
MAX_WORKERS = 1
WAIT_FOR_TEXTS = True
WAIT_TEXTS_INTERVAL_SECONDS = 60
WAIT_TEXTS_MAX_MINUTES = 240
REQUIRE_STABLE_FILES = True
STABLE_CHECKS = 2


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_expected() -> list[str]:
    p = Path(EXPECTED_FILES_TXT)
    if not p.is_file():
        return []
    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if name and name.lower().endswith(".mp3"):
            out.append(Path(name).name)
    return out


def _read_expected_count(expected_files: list[str]) -> int:
    p = Path(EXPECTED_COUNT_TXT)
    if p.is_file():
        try:
            return int(p.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            return len(expected_files)
    return len(expected_files)


def _write_status(**data: object) -> None:
    p = Path(COLAB_STATUS_JSON)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": _now(), **data}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _wait_for_expected_texts(texts_dir: Path, expected_mp3: list[str], expected_count: int) -> list[Path]:
    expected_txt = [Path(x).with_suffix(".txt").name for x in expected_mp3]
    expected_set = set(expected_txt)
    if not WAIT_FOR_TEXTS:
        return sorted([p for p in texts_dir.glob("*.txt") if p.is_file() and p.name in expected_set])
    deadline = time.time() + WAIT_TEXTS_MAX_MINUTES * 60
    stable_hits = 0
    prev_sizes: dict[str, int] = {}
    while True:
        files = {p.name: p for p in texts_dir.glob("*.txt") if p.is_file()}
        present = sorted(expected_set.intersection(files.keys()))
        zero_size = [n for n in present if files[n].stat().st_size <= 0]
        current_sizes = {n: files[n].stat().st_size for n in present}
        stable_now = (current_sizes == prev_sizes) if REQUIRE_STABLE_FILES else True
        stable_hits = stable_hits + 1 if stable_now else 0
        _write_status(
            state="waiting_texts",
            expected_count=expected_count,
            expected_files=len(expected_set),
            found_texts=len(present),
            zero_size_texts=len(zero_size),
            stable_hits=stable_hits,
            stable_required=STABLE_CHECKS,
        )
        print(
            f"[WAIT_TEXTS] expected={expected_count} found={len(present)} zero_size={len(zero_size)} "
            f"stable_hits={stable_hits}/{STABLE_CHECKS}",
            flush=True,
        )
        if len(present) == expected_count and not zero_size and stable_hits >= STABLE_CHECKS:
            return [files[n] for n in present]
        if time.time() >= deadline:
            raise RuntimeError("Timed out waiting for expected txt files")
        prev_sizes = current_sizes
        time.sleep(WAIT_TEXTS_INTERVAL_SECONDS)


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
    logs_dir = Path(LOGS_DIR)
    report_csv = Path(REPORT_CSV)
    job_dir = Path(JOB_DIR)
    colab_status = Path(COLAB_STATUS_JSON)
    texts_dir.mkdir(parents=True, exist_ok=True)
    mp3_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    colab_status.parent.mkdir(parents=True, exist_ok=True)

    expected_mp3 = _read_expected()
    expected_count = _read_expected_count(expected_mp3)
    if expected_count <= 0:
        _write_status(state="error", message="expected_count is empty/zero")
        raise RuntimeError(f"Invalid expected count in {EXPECTED_COUNT_TXT}")
    if not expected_mp3:
        _write_status(state="error", message="EXPECTED_FILES is empty")
        raise RuntimeError(f"EXPECTED_FILES is empty: {EXPECTED_FILES_TXT}")

    txt_files = _wait_for_expected_texts(texts_dir, expected_mp3, expected_count)
    if not txt_files:
        _write_status(state="done", done_count=0, error_count=0, missing_count=expected_count, finished_at=_now())
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
    started_at = _now()
    ok = 0
    err = 0
    total = len(txt_files)

    _write_status(state="running", expected_count=expected_count, total_txt=total, done_count=0, error_count=0)

    def _render_one(txt: Path) -> tuple[str, str, str]:
        mp3_out = mp3_dir / f"{txt.stem}.mp3"
        if mp3_out.is_file() and not FORCE:
            return (txt.name, "skipped_existing", "mp3 already exists")
        try:
            text = txt.read_text(encoding="utf-8").strip()
            if not text:
                return (txt.name, "error", "empty txt")
            voice = _pick_voice_from_stem(txt.stem)
            lang = voice.strip().lower()[:1] if voice and voice.strip().lower()[:1] in "abefhijpz" else "a"
            pipe = KPipeline(lang_code=lang)
            parts = []
            for _gs, _ps, audio in pipe(text, voice=voice, speed=float(SPEED), split_pattern=r"\n+"):
                arr = np.asarray(audio, dtype=np.float32).reshape(-1)
                if arr.size:
                    parts.append(arr)
            if not parts:
                return (txt.name, "error", "kokoro returned empty audio")
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
                return (txt.name, "error", f"ffmpeg failed: {(proc.stderr or proc.stdout or '')[:300]}")
            return (txt.name, "ok", f"written {mp3_out.name}")
        except Exception as exc:  # pragma: no cover
            return (txt.name, "error", str(exc))

    max_workers = max(1, int(MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_render_one, txt): txt.name for txt in txt_files}
        done_idx = 0
        for fut in as_completed(futures):
            done_idx += 1
            file_name, status, message = fut.result()
            rows.append([file_name, status, message])
            if status == "ok":
                ok += 1
            elif status == "error":
                err += 1
            print(f"[{done_idx}/{total}] {file_name}: {status}", flush=True)
            _write_status(
                state="running",
                started_at=started_at,
                expected_count=expected_count,
                done_count=ok,
                error_count=err,
                processed=done_idx,
                total=total,
            )

    with report_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "message"])
        w.writerows(rows)

    done_mp3 = {p.name for p in mp3_dir.glob("*.mp3") if p.is_file() and p.stat().st_size > 0}
    expected_set = set(expected_mp3)
    missing_count = len(expected_set - done_mp3)
    _write_status(
        state="finished",
        started_at=started_at,
        done_count=ok,
        error_count=err,
        missing_count=missing_count,
        finished_at=_now(),
    )
    print(f"[DONE] ok={ok} error={err} missing={missing_count}")
    print(f"[DONE] mp3_dir={mp3_dir}")
    print(f"[DONE] report_csv={report_csv}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
