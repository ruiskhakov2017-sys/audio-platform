from __future__ import annotations

import json
import unittest
from pathlib import Path

from orchestrator.stories_input_series_return import (
    load_original_source_paths,
    normalize_story_base_title,
    stem_has_explicit_serial_marker,
    stem_serial_signal,
)


class TestNormalizeStoryBaseTitle(unittest.TestCase):
    def test_chapter_episode_part_merge(self) -> None:
        a, ta = normalize_story_base_title("My Tale Ch. 02")
        b, tb = normalize_story_base_title("My Tale Episode 7")
        c, _ = normalize_story_base_title("My Tale Part 3")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(a, "my tale")
        self.assertIn("chapter", ta)
        self.assertIn("episode", tb)

    def test_queue_tail_strips_but_groups_only_with_explicit(self) -> None:
        n1, t1 = normalize_story_base_title("SameTitle_000339")
        n2, t2 = normalize_story_base_title("SameTitle_000401")
        self.assertEqual(n1, n2)
        self.assertIn("queue_tail", t1)
        self.assertFalse(stem_has_explicit_serial_marker("SameTitle_000339")[0])

    def test_plain_numbers_do_not_merge(self) -> None:
        n1, _ = normalize_story_base_title("Alpha 12")
        n2, _ = normalize_story_base_title("Alpha 99")
        self.assertNotEqual(n1, n2)

    def test_range_stripped(self) -> None:
        n, tags = normalize_story_base_title("Batch 016-020 remainder")
        self.assertEqual(n, "batch remainder")
        self.assertIn("numeric_range", tags)

    def test_hash_stripped(self) -> None:
        n, tags = normalize_story_base_title("Story #003 end")
        self.assertEqual(n, "story end")
        self.assertIn("hash_number", tags)


class TestExplicitSerial(unittest.TestCase):
    def test_explicit_chapter_after_tail_strip(self) -> None:
        ok, tag = stem_has_explicit_serial_marker("X_000339 Ch 2")
        self.assertTrue(ok)
        self.assertEqual(tag, "chapter")

    def test_tail_only_not_explicit(self) -> None:
        self.assertFalse(stem_has_explicit_serial_marker("Lonely_000339")[0])

    def test_extended_day_marker_via_stem_serial_signal(self) -> None:
        ok, tag = stem_serial_signal("My Story Day 3")
        self.assertTrue(ok)
        self.assertEqual(tag, "day")


class TestManifestMap(unittest.TestCase):
    def test_load_keys(self) -> None:
        root = Path(__file__).resolve().parent
        mf = root / "_tmp_manifest_series.json"
        mf.write_text(
            json.dumps(
                {
                    "files": [
                        {
                            "source_path_original": "D:/lib/Foo/Story Ch 1.txt",
                            "selected_filename": "Story Ch 1.txt",
                            "target_path": "D:/q/Story Ch 1.txt",
                        },
                        {
                            "original_source_path": "D:/lib/Bar/Other.txt",
                            "target_path": "D:/q/Renamed.txt",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            m = load_original_source_paths(mf)
            self.assertEqual(m["story ch 1.txt"], Path("D:/lib/Foo/Story Ch 1.txt"))
            self.assertEqual(m["renamed.txt"], Path("D:/lib/Bar/Other.txt"))
        finally:
            mf.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
