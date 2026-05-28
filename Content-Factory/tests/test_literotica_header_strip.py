import unittest

from orchestrator.text_cleaning.literotica_header import (
    literotica_header_remnant_warning,
    strip_literotica_source_header,
)
from orchestrator.site_tts.colab_batch import _clean_text_for_drive_tts


class TestLiteroticaHeaderStrip(unittest.TestCase):
    def _strip(self, raw: str) -> str:
        out, _ = strip_literotica_source_header(raw)
        return out

    def test_plain_domain_and_pages(self) -> None:
        raw = "literotica.com/s/324a\nстраниц текста: 2\n\nFirst paragraph."
        self.assertEqual(self._strip(raw), "First paragraph.")

    def test_spaced_domain_after_punct_damage(self) -> None:
        raw = "literotica. com/s/324a\nстраниц текста: 2\n\nFirst paragraph."
        self.assertEqual(self._strip(raw), "First paragraph.")

    def test_spaced_domain_all_gaps(self) -> None:
        raw = "literotica . com/s/a-bit-more\nстраниц текста: 1\n\nFirst paragraph."
        self.assertEqual(self._strip(raw), "First paragraph.")

    def test_domain_and_pages_same_line(self) -> None:
        raw = "literotica.com/s/a-charity-case страниц текста: 4\n\nFirst paragraph."
        self.assertEqual(self._strip(raw), "First paragraph.")

    def test_same_line_header_and_body(self) -> None:
        raw = (
            "literotica. com/s/a-charity-case страниц текста: 4 "
            "Los Angeles: 2001 Two attractive women were there."
        )
        out = self._strip(raw)
        self.assertEqual(out, "Los Angeles: 2001 Two attractive women were there.")
        self.assertNotIn("literotica", out.lower())
        self.assertNotIn("страниц текста", out.lower())

    def test_separated_header(self) -> None:
        raw = "literotica. com/s/a-charity-case\nстраниц текста: 4\n\nLos Angeles: 2001"
        self.assertEqual(self._strip(raw), "Los Angeles: 2001")

    def test_body_literotica_not_removed(self) -> None:
        # Упоминание домена после 20-й строки — не шапка, не удаляем.
        tail = "She whispered literotica.com was not the point."
        raw = "First line.\n\n" + "\n".join(f"para {i}" for i in range(2, 22)) + f"\n\n{tail}\n"
        out = self._strip(raw)
        self.assertIn("literotica.com", out)
        self.assertIn("First line.", out)

    def test_diagnostics_count(self) -> None:
        raw = "literotica.com/x\nстраниц текста: 3\n\nBody."
        _, diag = strip_literotica_source_header(raw)
        self.assertEqual(diag["removed_literotica_header_lines_count"], 2)
        self.assertGreaterEqual(len(diag["removed_literotica_header_lines_sample"]), 1)

    def test_remnant_warning(self) -> None:
        bad = "literotica.com/foo\n\nBody"
        self.assertIsNotNone(literotica_header_remnant_warning(bad))

    def test_drive_cleaner_strips_before_and_after_punct(self) -> None:
        raw = "literotica.com/s/324a\nстраниц текста: 2\n\nFirst paragraph."
        out, *_ = _clean_text_for_drive_tts(raw)
        self.assertEqual(out, "First paragraph.")
        self.assertNotIn("literotica", out.lower())
        self.assertNotIn("страниц текста", out.lower())


if __name__ == "__main__":
    unittest.main()
