from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from orchestrator.site_tts.colab_batch import _colab_expected_resolution


class TestColabTerminalWait(unittest.TestCase):
    def test_unresolved_until_colab_done_without_mp3(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            job = root / "job"
            mp3 = root / "mp3"
            job.mkdir()
            mp3.mkdir()
            expected = {"Story__M.mp3"}
            (job / "COLAB_STATUS.json").write_text(
                json.dumps({"file_status": {"Story__M.mp3": "failed"}}),
                encoding="utf-8",
            )
            res = _colab_expected_resolution(expected_set=expected, mp3_dir=mp3, job_dir=job)
            self.assertEqual(res["unresolved_count"], 1)

    def test_failed_terminal_after_colab_done(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            job = root / "job"
            mp3 = root / "mp3"
            job.mkdir()
            mp3.mkdir()
            expected = {"Story__M.mp3"}
            (job / "COLAB_DONE.txt").write_text("{}", encoding="utf-8")
            (job / "COLAB_STATUS.json").write_text(
                json.dumps({"file_status": {"Story__M.mp3": "failed"}, "failed_count": 1}),
                encoding="utf-8",
            )
            res = _colab_expected_resolution(expected_set=expected, mp3_dir=mp3, job_dir=job)
            self.assertEqual(res["unresolved_count"], 0)
            self.assertEqual(res["failed_terminal"], ["Story__M.mp3"])

    def test_resolved_with_valid_mp3(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            job = root / "job"
            mp3 = root / "mp3"
            job.mkdir()
            mp3.mkdir()
            name = "Story__M.mp3"
            (mp3 / name).write_bytes(b"x" * 512)
            res = _colab_expected_resolution(expected_set={name}, mp3_dir=mp3, job_dir=job)
            self.assertEqual(res["unresolved_count"], 0)


if __name__ == "__main__":
    unittest.main()
