from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from orchestrator.site_tts.colab_batch import (
    _should_skip_redundant_drive_export,
    _site_has_final_mp3,
)


class TestDriveExportDedupe(unittest.TestCase):
    def test_skip_when_pending_job_and_txts_on_drive(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            texts = root / "texts"
            job = root / "job"
            site_root = root / "site"
            texts.mkdir(parents=True)
            job.mkdir(parents=True)
            site_root.mkdir(parents=True)
            (job / "EXPECTED_FILES.txt").write_text("MyStory__M.mp3\n", encoding="utf-8")
            (job / "LOCAL_STATUS.json").write_text(
                json.dumps({"state": "exported_waiting_mp3"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (texts / "MyStory__M.txt").write_text("body", encoding="utf-8")
            skip, reason = _should_skip_redundant_drive_export(texts_dir=texts, job_dir=job, site_root=site_root)
            self.assertTrue(skip)
            self.assertIn("pending", reason)

    def test_no_skip_when_user_removed_txt(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            texts = root / "texts"
            job = root / "job"
            site_root = root / "site"
            texts.mkdir(parents=True)
            job.mkdir(parents=True)
            site_root.mkdir(parents=True)
            (job / "EXPECTED_FILES.txt").write_text("MyStory__M.mp3\n", encoding="utf-8")
            (job / "LOCAL_STATUS.json").write_text(
                json.dumps({"state": "exported_waiting_mp3"}, ensure_ascii=False),
                encoding="utf-8",
            )
            skip, _ = _should_skip_redundant_drive_export(texts_dir=texts, job_dir=job, site_root=site_root)
            self.assertFalse(skip)

    def test_no_skip_when_job_imported(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            texts = root / "texts"
            job = root / "job"
            site_root = root / "site"
            texts.mkdir(parents=True)
            job.mkdir(parents=True)
            site_root.mkdir(parents=True)
            (job / "EXPECTED_FILES.txt").write_text("x__U.mp3\n", encoding="utf-8")
            (job / "LOCAL_STATUS.json").write_text(
                json.dumps({"state": "imported_success"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (texts / "x__U.txt").write_text("a", encoding="utf-8")
            skip, _ = _should_skip_redundant_drive_export(texts_dir=texts, job_dir=job, site_root=site_root)
            self.assertFalse(skip)


class TestSiteHasFinalMp3(unittest.TestCase):
    def test_voice_suffixed_mp3_in_folder(self) -> None:
        with TemporaryDirectory() as td:
            folder = Path(td) / "MyStory_M"
            folder.mkdir(parents=True)
            (folder / "MyStory_M__M.mp3").write_bytes(b"\x00\x01")
            self.assertTrue(_site_has_final_mp3(folder, "MyStory_M"))


if __name__ == "__main__":
    unittest.main()
