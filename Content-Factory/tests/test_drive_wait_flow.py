from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from orchestrator.site_tts.colab_batch import drive_kokoro_job_pending_on_drive


class TestDriveKokoroJobPending(unittest.TestCase):
    def test_pending_when_local_status_waiting(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            job = root / "job"
            texts = root / "texts"
            job.mkdir()
            texts.mkdir()
            (job / "EXPECTED_FILES.txt").write_text("Story__M.mp3\n", encoding="utf-8")
            (job / "LOCAL_STATUS.json").write_text(
                json.dumps({"state": "exported_waiting_mp3", "expected_count": 1}),
                encoding="utf-8",
            )
            (texts / "Story__M.txt").write_text("body", encoding="utf-8")
            cfg = root / "configs"
            cfg.mkdir()
            (cfg / "site_tts.yaml").write_text(
                f"""
google_drive_tts:
  texts_dir: "{texts}"
  job_dir: "{job}"
  mp3_dir: "{root / 'mp3'}"
""".strip(),
                encoding="utf-8",
            )
            pending, info = drive_kokoro_job_pending_on_drive(root)
            self.assertTrue(pending)
            self.assertEqual(info.get("reason"), "local_status_exported_waiting_mp3")

    def test_not_pending_after_imported(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            job = root / "job"
            job.mkdir()
            (job / "EXPECTED_FILES.txt").write_text("Story__M.mp3\n", encoding="utf-8")
            (job / "LOCAL_STATUS.json").write_text(
                json.dumps({"state": "imported_success"}),
                encoding="utf-8",
            )
            cfg = root / "configs"
            cfg.mkdir()
            (cfg / "site_tts.yaml").write_text("google_drive_tts:\n  root_dir: \"\"\n", encoding="utf-8")
            pending, _ = drive_kokoro_job_pending_on_drive(root)
            self.assertFalse(pending)


if __name__ == "__main__":
    unittest.main()
