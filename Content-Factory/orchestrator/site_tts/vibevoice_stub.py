from __future__ import annotations

from pathlib import Path

from orchestrator.site_tts.contract import SiteTtsPaths, TTSSynthesisResult


class VibeVoiceSiteAdapterStub:
    engine = "vibevoice"
    status = "experimental"

    def synthesize(
        self,
        *,
        paths: SiteTtsPaths,
        settings: object,
        run_work_dir: Path,
        execute: bool,
        force: bool,
    ) -> TTSSynthesisResult:
        log = run_work_dir / "vibevoice_stub.log"
        run_work_dir.mkdir(parents=True, exist_ok=True)
        msg = (
            "VibeVoice: адаптер experimental/disabled. Production batch site TTS на VibeVoice не выполняется. "
            "Используйте Kokoro (site_tts_engine: kokoro) для массовой озвучки."
        )
        log.write_text(msg + "\n", encoding="utf-8")
        return TTSSynthesisResult(
            status="error",
            output_path=None,
            duration_sec=None,
            logs_path=log,
            message=msg,
            details={"engine": self.engine, "adapter_status": self.status},
        )
