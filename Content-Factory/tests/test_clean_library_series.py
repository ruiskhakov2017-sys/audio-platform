from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.clean_library_series import CleanLibrarySeriesOptions, run_clean_library_series
from orchestrator.config import load_config


class TestCleanLibrarySeries(unittest.TestCase):
    def test_dry_run_moves_only_serial(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = root / "sci-fi"
            g.mkdir(parents=True)
            (g / "Alpha Ch 01.txt").write_text("a", encoding="utf-8")
            (g / "Alpha Ch 02.txt").write_text("b", encoding="utf-8")
            (g / "Lonely Title.txt").write_text("c", encoding="utf-8")

            cfg = load_config()
            cfg.reports_dir = root / ".orch_reports"
            r = run_clean_library_series(
                config=cfg,
                options=CleanLibrarySeriesOptions(library_root=root, execute=False),
            )
            self.assertTrue(r.get("ok", False))
            s = r["summary"]
            self.assertGreaterEqual(s["serial_count"], 2)
            self.assertTrue((g / "Alpha Ch 01.txt").is_file())
            self.assertTrue((g / "Lonely Title.txt").is_file())

    def test_execute_and_duplicate_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = root / "fantasy"
            ser = g / "_series"
            g.mkdir(parents=True)
            ser.mkdir(parents=True)
            (g / "Beta Part 1.txt").write_text("x", encoding="utf-8")
            (ser / "Beta Part 1.txt").write_text("old", encoding="utf-8")

            cfg = load_config()
            cfg.reports_dir = root / ".orch_reports2"
            r = run_clean_library_series(
                config=cfg,
                options=CleanLibrarySeriesOptions(library_root=root, execute=True),
            )
            self.assertTrue(r.get("ok", False))
            self.assertGreaterEqual(r["summary"]["files_moved_to_series"], 1)
            self.assertGreaterEqual(r["summary"]["suffix_duplicate_resolved"], 1)
            self.assertFalse((g / "Beta Part 1.txt").exists())
            dup = list(ser.glob("Beta Part 1_duplicate_*.txt"))
            self.assertTrue(dup, "expected duplicate-suffixed file in _series")


if __name__ == "__main__":
    unittest.main()
