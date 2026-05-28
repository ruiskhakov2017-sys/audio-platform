from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

from orchestrator.site_tts.cleaned_text_guard import validate_cleaned_text_for_tts
from orchestrator.site_tts.config import SiteTtsSettings
from orchestrator.site_tts.contract import SiteTtsPaths, TTSSynthesisResult
from orchestrator.site_tts.info_parser import resolve_voice_letter_from_info_content
from orchestrator.site_tts.text_chunking import pack_paragraph_chunks


class KokoroSiteAdapter:
    engine = "kokoro"
    sample_rate = 24000

    voice_metadata_file = ".site_tts_voice.json"

    def _voice_meta_path(self, paths: SiteTtsPaths) -> Path:
        return paths.story_folder / self.voice_metadata_file

    def _load_existing_voice(self, paths: SiteTtsPaths) -> str | None:
        p = self._voice_meta_path(paths)
        if not p.is_file():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(obj, dict):
            return None
        v = str(obj.get("selected_voice", "") or "").strip()
        return v or None

    def _save_selected_voice(self, paths: SiteTtsPaths, *, voice_label: str, selected_voice: str, source: str) -> None:
        p = self._voice_meta_path(paths)
        payload = {
            "voice_label": str(voice_label or "U").strip().upper()[:1] or "U",
            "selected_voice": str(selected_voice or "").strip(),
            "source": str(source or ""),
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _pick_from_pool(self, *, story_id: str, voice_label: str, pool: list[str]) -> str:
        if not pool:
            return ""
        key = f"{story_id}|{voice_label}".encode("utf-8")
        h = hashlib.sha256(key).hexdigest()
        idx = int(h[:8], 16) % len(pool)
        return pool[idx]

    def _pick_voice(self, settings: SiteTtsSettings, voice_type: str) -> str:
        vt = (voice_type or "U").upper()[:1]
        if vt == "M":
            return settings.kokoro_voice_male
        if vt == "F":
            return settings.kokoro_voice_female
        return settings.kokoro_voice_neutral

    def _resolve_voice(
        self,
        *,
        settings: SiteTtsSettings,
        paths: SiteTtsPaths,
        voice_label: str,
        is_label_fallback: bool,
    ) -> tuple[str, str, str]:
        existing = self._load_existing_voice(paths)
        if existing:
            return voice_label, existing, "existing"

        strategy = (settings.voice_selection_strategy or "single").strip().lower()
        label = (voice_label or "").strip().upper()[:1]
        if label not in {"M", "F", "U"}:
            label = settings.voice_selection_fallback_label or "U"
        if label not in {"M", "F", "U"}:
            label = "U"
        if is_label_fallback:
            return label, (settings.voice_selection_fallback_voice or "af_bella"), "fallback"

        if strategy == "deterministic_pool":
            pool = list(settings.voice_pools.get(label, []))
            if not pool:
                fb_label = settings.voice_selection_fallback_label or "U"
                pool = list(settings.voice_pools.get(fb_label, []))
            if pool:
                selected = self._pick_from_pool(story_id=paths.story_folder.name, voice_label=label, pool=pool)
                if selected:
                    return label, selected, "new"

        selected = self._pick_voice(settings, label)
        if not selected:
            selected = settings.default_voice or settings.voice_selection_fallback_voice or "af_bella"
            return label, selected, "fallback"
        return label, selected, "fallback"

    def _lang_code(self, voice: str, settings: SiteTtsSettings) -> str:
        if settings.kokoro_lang_code:
            return settings.kokoro_lang_code.strip().lower()[:1]
        if voice:
            c = voice.strip().lower()[:1]
            if c in "abefhijpz":
                return c
        return "a"

    def _append_log(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _to_numpy_mono(self, audio: Any) -> Any:
        import numpy as np

        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return arr
        peak = float(np.max(np.abs(arr))) or 1.0
        if peak > 1.0:
            arr = arr / peak
        return arr

    def _synth_chunk(
        self,
        pipeline: Any,
        text: str,
        voice: str,
        speed: float,
        *,
        log_path: Path,
        chunk_index: int,
    ) -> Any:
        import numpy as np

        if not text.strip():
            return np.zeros(0, dtype=np.float32)
        acc: list[Any] = []
        t0 = time.perf_counter()
        gen = pipeline(text, voice=voice, speed=float(speed), split_pattern=r"\n+")
        inner_yields = 0
        for _gs, _ps, audio in gen:
            inner_yields += 1
            acc.append(self._to_numpy_mono(audio))
        synth_sec = time.perf_counter() - t0
        self._append_log(
            log_path,
            f"chunk {chunk_index:04d}: inner_kokoro_yields={inner_yields} synth_sec={synth_sec:.3f} "
            f"graphemes_len={len(text)}",
        )
        if not acc:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(acc) if len(acc) > 1 else acc[0]

    def _ffmpeg_mp3(self, wav_path: Path, mp3_path: Path, log_path: Path) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            str(mp3_path),
        ]
        self._append_log(log_path, "RUN " + " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"ffmpeg failed: {err[:800]}")

    def _ffprobe_duration(self, mp3_path: Path) -> float | None:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(mp3_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        try:
            return float((proc.stdout or "").strip())
        except ValueError:
            return None

    def _write_wav(self, path: Path, audio: Any, sr: int) -> None:
        import numpy as np

        path.parent.mkdir(parents=True, exist_ok=True)
        data_i16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(data_i16.tobytes())

    def synthesize(
        self,
        *,
        paths: SiteTtsPaths,
        settings: SiteTtsSettings,
        run_work_dir: Path,
        execute: bool,
        force: bool,
    ) -> TTSSynthesisResult:
        log_path = run_work_dir / "tts.log"
        run_work_dir.mkdir(parents=True, exist_ok=True)

        def fail(msg: str) -> TTSSynthesisResult:
            self._append_log(log_path, f"ERROR {msg}")
            return TTSSynthesisResult(
                status="error",
                output_path=None,
                duration_sec=None,
                logs_path=log_path,
                message=msg,
            )

        if not paths.cleaned_story_txt.is_file():
            return fail(f"missing cleaned_story.txt: {paths.cleaned_story_txt}")
        if not paths.info_txt.is_file():
            return fail(f"missing info.txt: {paths.info_txt}")

        story_name = paths.story_folder.name
        text = paths.cleaned_story_txt.read_text(encoding="utf-8")
        ok_txt, bad_reason = validate_cleaned_text_for_tts(text)
        if not ok_txt:
            return fail(f"TTS input rejected ({bad_reason}); source={paths.cleaned_story_txt}")

        info = paths.info_txt.read_text(encoding="utf-8")
        voice_type, _line, warn = resolve_voice_letter_from_info_content(info)
        selected_label, voice, voice_source = self._resolve_voice(
            settings=settings,
            paths=paths,
            voice_label=voice_type,
            is_label_fallback=bool(warn),
        )
        if warn:
            self._append_log(log_path, warn)
        if settings.voice_selection_save_selected_voice_to_story_metadata:
            try:
                self._save_selected_voice(paths, voice_label=selected_label, selected_voice=voice, source=voice_source)
            except OSError:
                pass
        lang = self._lang_code(voice, settings)

        if paths.output_mp3.is_file() and not force:
            msg = f"skip: {paths.output_mp3} exists (force=false)"
            self._append_log(log_path, msg)
            return TTSSynthesisResult(
                status="success",
                output_path=paths.output_mp3,
                duration_sec=self._ffprobe_duration(paths.output_mp3),
                logs_path=log_path,
                message=msg,
                details={"skipped": True},
            )

        if not execute:
            n_chunks = len(pack_paragraph_chunks(text, settings.kokoro_chunk_max_chars))
            plan = (
                f"dry-run: engine={self.engine} story={story_name} voice_label={selected_label} selected_voice={voice} source={voice_source} "
                f"lang={lang} speed={settings.kokoro_speed} chunks_est={n_chunks} "
                f"-> {paths.output_mp3}"
            )
            self._append_log(log_path, plan)
            return TTSSynthesisResult(
                status="success",
                output_path=paths.output_mp3,
                duration_sec=None,
                logs_path=log_path,
                message=plan,
                details={"dry_run": True},
            )

        try:
            from kokoro import KPipeline  # type: ignore
        except ImportError as exc:
            hint = (
                f" (Python {sys.version_info.major}.{sys.version_info.minor}: "
                "install Kokoro with a supported CPython, e.g. 3.10–3.12, then "
                "`pip install kokoro soundfile`)"
            )
            return fail(f"kokoro import failed: {exc}{hint}")

        chunks = pack_paragraph_chunks(text, settings.kokoro_chunk_max_chars)
        if not chunks:
            return fail("empty cleaned_story.txt")

        sample_cyr = text[:4000]
        looks_cyrillic = any("\u0400" <= c <= "\u04ff" for c in sample_cyr)
        if looks_cyrillic and lang == "a":
            self._append_log(
                log_path,
                "WARN text looks Cyrillic but Kokoro lang_code=a (American English G2P). "
                "Kokoro-82M has no dedicated Russian pipeline in this stack; quality/speed may suffer. "
                "See configs/site_tts.yaml (kokoro_lang_code / voices).",
            )

        self._append_log(
            log_path,
            f"execute story={story_name} start_ts={time.time():.3f} voice={voice} lang={lang} "
            f"voice_label={selected_label} source={voice_source} "
            f"paragraph_chunks={len(chunks)} chunk_max_chars={settings.kokoro_chunk_max_chars}",
        )

        import numpy as np
        import torch

        t_pipe = time.perf_counter()
        pipeline = KPipeline(lang_code=lang)
        pipe_sec = time.perf_counter() - t_pipe
        cuda_on = torch.cuda.is_available()
        dev_line = "model_device=none"
        if pipeline.model is not None:
            try:
                dev_line = f"model_device={next(pipeline.model.parameters()).device}"
            except (StopIteration, RuntimeError):
                dev_line = "model_device=unknown"
        self._append_log(
            log_path,
            f"KPipeline_ready: init_sec={pipe_sec:.3f} torch_cuda_available={cuda_on} {dev_line}",
        )

        pause_samples = int(max(0.0, settings.kokoro_pause_between_chunks_sec) * self.sample_rate)
        silence = np.zeros(pause_samples, dtype=np.float32) if pause_samples else np.zeros(0, dtype=np.float32)

        merged_parts: list[Any] = []
        for i, ch in enumerate(chunks):
            wav_i = run_work_dir / f"chunk_{i:04d}.wav"
            t_chunk = time.perf_counter()
            audio = self._synth_chunk(
                pipeline,
                ch,
                voice,
                settings.kokoro_speed,
                log_path=log_path,
                chunk_index=i,
            )
            if audio.size == 0:
                return fail(f"empty audio for chunk {i}")
            tw = time.perf_counter()
            self._write_wav(wav_i, audio, self.sample_rate)
            wav_sec = time.perf_counter() - tw
            self._append_log(
                log_path,
                f"chunk {i:04d}: write_wav_sec={wav_sec:.3f} path={wav_i.name} total_chunk_wall_sec={time.perf_counter() - t_chunk:.3f}",
            )
            merged_parts.append(audio)
            if i < len(chunks) - 1 and silence.size:
                merged_parts.append(silence)

        tm0 = time.perf_counter()
        merged = np.concatenate(merged_parts) if len(merged_parts) > 1 else merged_parts[0]
        merged_wav = run_work_dir / "merged.wav"
        self._write_wav(merged_wav, merged, self.sample_rate)
        self._append_log(log_path, f"merge_write_wav_sec={time.perf_counter() - tm0:.3f} merged_samples={int(merged.shape[0])}")

        tmp_mp3 = run_work_dir / "_out.mp3"
        try:
            tff = time.perf_counter()
            self._ffmpeg_mp3(merged_wav, tmp_mp3, log_path)
            self._append_log(log_path, f"ffmpeg_mp3_sec={time.perf_counter() - tff:.3f}")
        except RuntimeError as exc:
            return fail(str(exc))

        paths.output_mp3.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_mp3), str(paths.output_mp3))
        self._append_log(log_path, f"done story={story_name} mp3={paths.output_mp3}")

        if not settings.keep_tts_chunks:
            for p in run_work_dir.glob("chunk_*.wav"):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                merged_wav.unlink()
            except OSError:
                pass

        duration = self._ffprobe_duration(paths.output_mp3)
        ok_msg = f"written {paths.output_mp3}"
        self._append_log(log_path, ok_msg)
        return TTSSynthesisResult(
            status="success",
            output_path=paths.output_mp3,
            duration_sec=duration,
            logs_path=log_path,
            message=ok_msg,
            details={
                "voice": voice,
                "voice_label": selected_label,
                "voice_source": voice_source,
                "voice_type": voice_type,
                "chunks": len(chunks),
            },
        )
