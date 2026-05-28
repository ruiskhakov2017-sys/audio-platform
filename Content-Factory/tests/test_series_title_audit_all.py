from __future__ import annotations

import unittest

from orchestrator.series_title_audit_all import extended_series_markers, extract_part_marker_number


class TestExtendedSeriesMarkers(unittest.TestCase):
    def test_day_marker(self) -> None:
        ok, tags = extended_series_markers("Kinktober Day 16")
        self.assertTrue(ok)
        self.assertTrue(any(t == "day" for t in tags))

    def test_page_marker(self) -> None:
        ok, tags = extended_series_markers("Story Page 12")
        self.assertTrue(ok)
        self.assertIn("page", tags)

    def test_season_episode(self) -> None:
        ok, tags = extended_series_markers("Show S01E03")
        self.assertTrue(ok)
        self.assertIn("season_episode", tags)

    def test_plain_title_not_serial(self) -> None:
        ok, _ = extended_series_markers("Plain Title")
        self.assertFalse(ok)

    def test_extract_part(self) -> None:
        m, n = extract_part_marker_number("Alpha Ch. 7.txt")
        self.assertIn("chapter", m)
        self.assertEqual(n, 7)

    def test_n_of_m_marker(self) -> None:
        ok, tags = extended_series_markers("Filmmaker (2 of 3)")
        self.assertTrue(ok)
        self.assertIn("n_of_m", tags)

    def test_slash_part_marker(self) -> None:
        ok, tags = extended_series_markers("Story 1/5")
        self.assertTrue(ok)
        self.assertIn("slash_part", tags)

    def test_season_word_marker(self) -> None:
        ok, tags = extended_series_markers("Show Season 2")
        self.assertTrue(ok)
        self.assertIn("season_word", tags)


if __name__ == "__main__":
    unittest.main()
