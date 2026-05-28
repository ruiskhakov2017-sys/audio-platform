from __future__ import annotations

import unittest

from orchestrator.audit_series_titles import _classify_bucket, _suggested_txt_name_for_queue_folder


class TestAuditSeriesTitles(unittest.TestCase):
    def test_classify_serial_multi_marker(self) -> None:
        self.assertEqual(
            _classify_bucket(group_size=3, group_has_marker=True, explicit=False),
            "serial",
        )

    def test_classify_probable_multi_no_marker(self) -> None:
        self.assertEqual(
            _classify_bucket(group_size=2, group_has_marker=False, explicit=False),
            "probable_serial",
        )

    def test_classify_singleton_explicit(self) -> None:
        self.assertEqual(
            _classify_bucket(group_size=1, group_has_marker=False, explicit=True),
            "serial",
        )

    def test_classify_singleton_plain(self) -> None:
        self.assertEqual(
            _classify_bucket(group_size=1, group_has_marker=False, explicit=False),
            "uncertain",
        )

    def test_suggested_txt_from_folder(self) -> None:
        self.assertEqual(_suggested_txt_name_for_queue_folder("My Tale Ch 2_000339"), "My Tale Ch 2.txt")


if __name__ == "__main__":
    unittest.main()
