import unittest

from orchestrator.site_tts.colab_batch import _clean_text_for_drive_tts
from orchestrator.site_tts.info_parser import parse_voice_type_mfu
from orchestrator.site_tts.text_chunking import pack_paragraph_chunks


class TestInfoParser(unittest.TestCase):
    def test_parse_voice_type_last_wins(self) -> None:
        txt = "Тип голоса: M\nfoo\nТип голоса: F\n"
        self.assertEqual(parse_voice_type_mfu(txt), "F")

    def test_parse_voice_type_default_u(self) -> None:
        self.assertEqual(parse_voice_type_mfu("no marker"), "U")


class TestPackParagraphChunks(unittest.TestCase):
    def test_pack_paragraph_chunks(self) -> None:
        text = "a" * 100 + "\n\n" + "b" * 100 + "\n\n" + "c" * 400
        chunks = pack_paragraph_chunks(text, max_chars=250)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(c) <= 280 for c in chunks))


class TestDriveTtsClean(unittest.TestCase):
    def test_removes_ru_chapter_header(self) -> None:
        raw = "Глава 1\n\nThis is the real story text."
        out, *_, _lit = _clean_text_for_drive_tts(raw)
        self.assertNotIn("Глава", out)
        self.assertIn("This is the real story text.", out)

    def test_removes_en_chapter_header(self) -> None:
        raw = "Chapter 1\n\nThis is the real story text."
        out, *_, _lit = _clean_text_for_drive_tts(raw)
        self.assertNotIn("Chapter", out)
        self.assertIn("This is the real story text.", out)

    def test_removes_ru_source_line_with_url(self) -> None:
        raw = "Источник: https://example.com/forum/thread/123\n\nBody here."
        out, *_, _lit = _clean_text_for_drive_tts(raw)
        self.assertNotIn("Источник", out)
        self.assertNotIn("example.com", out)
        self.assertIn("Body here.", out)

    def test_removes_read_more_url_line(self) -> None:
        raw = "Read more at www.example.com\n\nBody."
        out, *_, _lit = _clean_text_for_drive_tts(raw)
        self.assertNotIn("example.com", out)
        self.assertIn("Body.", out)

    def test_keeps_chapter_in_prose(self) -> None:
        raw = "She remembered chapter 1 of her old life, but everything had changed."
        out, *_, _lit = _clean_text_for_drive_tts(raw)
        self.assertIn("chapter 1", out.lower())

    def test_splits_long_unbroken_paragraph(self) -> None:
        raw = "x" * 3200
        out, *_, _lit = _clean_text_for_drive_tts(raw)
        parts = out.split("\n\n")
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(len(p) <= 1400 for p in parts))

    def test_idempotent_second_pass(self) -> None:
        raw = "Глава 1\n\nИсточник: https://x.test\n\nOne. Two. Three."
        first, *_, _ = _clean_text_for_drive_tts(raw)
        second, *_, __ = _clean_text_for_drive_tts(first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
