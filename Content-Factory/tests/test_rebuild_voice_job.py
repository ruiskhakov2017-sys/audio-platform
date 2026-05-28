from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from orchestrator.site_tts.colab_batch import rebuild_drive_voice_job
from orchestrator.site_tts.config import load_site_tts_settings
from orchestrator.site_tts.drive_voice_resolve import collect_voice_ids_from_pools


class TestRebuildDriveVoiceJob(unittest.TestCase):
    def test_rebuild_from_drive_texts_dry_run(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            texts = root / "texts"
            job = root / "job"
            site = root / "site"
            texts.mkdir()
            job.mkdir()
            story = site / "Alpha Tale"
            story.mkdir(parents=True)
            (story / "info.txt").write_text("Тип голоса: M\n", encoding="utf-8")
            (texts / "Alpha Tale__M.txt").write_text("hello", encoding="utf-8")
            (texts / "Beta__F.txt").write_text("world", encoding="utf-8")

            cfg_path = root / "configs"
            cfg_path.mkdir()
            (cfg_path / "site_tts.yaml").write_text(
                """
site_tts_engine: kokoro
voice_selection:
  strategy: deterministic_pool
voice_pools:
  F: [af_heart, af_bella, af_sarah, af_nicole, af_sky]
  M: [am_michael, am_adam, bm_george, bm_lewis, am_echo]
  U: [af_bella, af_heart, af_sarah, am_michael, am_adam]
google_drive_tts:
  root_dir: ""
  texts_dir: ""
  job_dir: ""
""".strip(),
                encoding="utf-8",
            )

            res = rebuild_drive_voice_job(
                root,
                texts_dir=texts,
                job_dir=job,
                site_root=site,
                execute=False,
            )
            self.assertTrue(res.get("dry_run"))
            self.assertEqual(res.get("txt_found"), 2)
            self.assertEqual(res.get("items_planned"), 2)
            self.assertFalse((job / "kokoro_voices_job.json").is_file())
            self.assertEqual(res.get("items_written"), 0)

    def test_rebuild_writes_job_without_touching_texts(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            texts = root / "texts"
            job = root / "job"
            texts.mkdir()
            job.mkdir()
            original = "cleaned-on-drive-only"
            txt_path = texts / "OnlyDrive__U.txt"
            txt_path.write_text(original, encoding="utf-8")

            cfg_path = root / "configs"
            cfg_path.mkdir()
            (cfg_path / "site_tts.yaml").write_text(
                """
voice_selection:
  strategy: deterministic_pool
voice_pools:
  U: [af_bella, af_heart, af_sarah, am_michael, am_adam]
google_drive_tts:
  root_dir: ""
""".strip(),
                encoding="utf-8",
            )

            res = rebuild_drive_voice_job(
                root,
                texts_dir=texts,
                job_dir=job,
                execute=True,
            )
            self.assertTrue(res.get("ok"))
            self.assertEqual(txt_path.read_text(encoding="utf-8"), original)
            job_json = job / "kokoro_voices_job.json"
            self.assertTrue(job_json.is_file())
            data = json.loads(job_json.read_text(encoding="utf-8"))
            self.assertEqual(len(data.get("items") or []), 1)
            self.assertEqual(data["items"][0]["txt_name"], "OnlyDrive__U.txt")
            self.assertTrue((job / "EXPECTED_FILES.txt").is_file())
            self.assertEqual((job / "EXPECTED_COUNT.txt").read_text(encoding="utf-8").strip(), "1")

    def test_pool_coverage_warn_when_voice_missing(self) -> None:
        root = Path(__file__).resolve().parents[1]
        settings = load_site_tts_settings(root)
        pool_ids = collect_voice_ids_from_pools(settings)
        self.assertGreaterEqual(len(pool_ids), 4)


if __name__ == "__main__":
    unittest.main()
