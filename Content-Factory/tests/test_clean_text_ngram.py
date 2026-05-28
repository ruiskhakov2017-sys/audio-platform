from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

from orchestrator.text_cleaning.literotica_header import strip_literotica_source_header

_REPO = Path(__file__).resolve().parents[1]
_cs_path = _REPO / "legacy" / "bulk-text-cleaner" / "clean_stories.py"
_spec = importlib.util.spec_from_file_location("clean_stories", _cs_path)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

_CHARITY = _REPO / "stories" / "input" / "A Charity Case.txt"
_BIT_MORE = _REPO / "stories" / "input" / "A Bit More.txt"
_324A = _REPO / "stories" / "input" / "324A.txt"


def _metrics(text: str) -> dict[str, int]:
    paras = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    short80 = sum(1 for p in paras if len(p) < 80)
    run = 0
    max_consec = 0
    for p in paras:
        if len(p) < 80:
            run += 1
            max_consec = max(max_consec, run)
        else:
            run = 0
    return {
        "paragraphs": len(paras),
        "short_lt80": short80,
        "max_consecutive_short": max_consec,
    }


class TestCleanTextNgramDisabled(unittest.TestCase):
    def test_charity_case_default_clean_no_microparagraphs(self) -> None:
        raw = _CHARITY.read_text(encoding="utf-8")
        out = cs.clean_text(raw)
        m = _metrics(out)
        self.assertLess(m["paragraphs"], 250, m)
        self.assertLess(m["short_lt80"], 80, m)
        self.assertLess(m["max_consecutive_short"], 12, m)
        self.assertNotIn("literotica", out.lower()[:500])
        self.assertIn("Los Angeles: 2001", out)
        self.assertIn("Chapter 1: Paint and Water", out)

    def test_bit_more_and_324a_stable(self) -> None:
        for path in (_BIT_MORE, _324A):
            raw = path.read_text(encoding="utf-8")
            out = cs.clean_text(raw)
            mr, mc = _metrics(raw), _metrics(out)
            self.assertLess(mc["paragraphs"], mr["paragraphs"] * 2 + 5, f"{path.name}: {mc}")
            self.assertLess(abs(mc["paragraphs"] - mr["paragraphs"]), 15, f"{path.name}: {mc}")
            self.assertLess(mc["max_consecutive_short"], mr["max_consecutive_short"] + 8, f"{path.name}: {mc}")

    def test_ngram_enabled_legacy_no_sentence_paragraph_explosion(self) -> None:
        raw = _CHARITY.read_text(encoding="utf-8")
        before = cs.remove_spaces_before_dot_comma(
            cs.remove_sentences_with_urls(
                cs.remove_bestweapon_links(
                    cs.remove_page_separators(
                        cs.remove_technical_header(strip_literotica_source_header(raw)[0])
                    )
                )
            )
        )
        before = cs.fix_period_spacing(before)
        after = cs.cut_duplicate_tail_by_ngrams(before)
        if after != before:
            m = _metrics(after)
            self.assertLess(m["paragraphs"], 400, m)
            ratio = m["short_lt80"] / max(m["paragraphs"], 1)
            self.assertLess(ratio, 0.5, m)


if __name__ == "__main__":
    unittest.main()
