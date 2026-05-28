#!/usr/bin/env python3
"""
Read-only trace: legacy clean_text steps + drive _clean_text_for_drive_tts.
Does not modify production cleaners. Writes report to stdout and optional file.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orchestrator.text_cleaning.literotica_header import strip_literotica_source_header  # noqa: E402
from orchestrator.site_tts.colab_batch import _clean_text_for_drive_tts  # noqa: E402

import importlib.util

_cs_path = _REPO / "legacy" / "bulk-text-cleaner" / "clean_stories.py"
_spec = importlib.util.spec_from_file_location("clean_stories", _cs_path)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

TAIL_DEDUP_WINDOW = cs.TAIL_DEDUP_WINDOW
TAIL_DEDUP_MIN_RUN = cs.TAIL_DEDUP_MIN_RUN


@dataclass
class StepDiag:
    name: str
    text: str
    prev_text: str | None = None

    @property
    def changed(self) -> bool:
        if self.prev_text is None:
            return False
        return self.text != self.prev_text


def paragraph_metrics(text: str) -> dict[str, Any]:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = t.split("\n")
    paras = [p.strip() for p in re.split(r"\n\n+", t) if p.strip()]
    short80 = sum(1 for p in paras if len(p) < 80)
    short150 = sum(1 for p in paras if len(p) < 150)
    short300 = sum(1 for p in paras if len(p) < 300)
    max_consec = 0
    run = 0
    for p in paras:
        if len(p) < 80:
            run += 1
            max_consec = max(max_consec, run)
        else:
            run = 0
    double_nl = t.count("\n\n")
    one_sent_para = 0
    for p in paras:
        # heuristic: one sentence if single .!? ending and no internal sentence boundary
        parts = re.split(r"(?<=[.!?…])(?:\s+|$)", p.strip())
        parts = [x for x in parts if x.strip()]
        if len(parts) <= 1 and len(p) < 300:
            one_sent_para += 1
    return {
        "chars": len(t),
        "lines": len(lines),
        "paragraphs_count": len(paras),
        "short_paragraphs_lt80": short80,
        "short_paragraphs_lt150": short150,
        "short_paragraphs_lt300": short300,
        "max_consecutive_short_paragraphs": max_consec,
        "double_newline_count": double_nl,
        "one_sentence_paragraphs_heuristic": one_sent_para,
        "preview_30_lines": "\n".join(lines[:30]),
    }


def inspect_ngram_dedup(text: str) -> dict[str, Any]:
    """Mirror cut_duplicate_tail_by_ngrams detection; report match without changing cleaner."""
    words = text.split()
    n = TAIL_DEDUP_WINDOW
    info: dict[str, Any] = {
        "word_count": len(words),
        "would_run": len(words) >= n + TAIL_DEDUP_MIN_RUN - 1,
        "match_found": False,
    }
    if not info["would_run"]:
        return info

    norms = [cs._normalize_word_for_cmp(w) for w in words]
    seen: dict[tuple[str, ...], int] = {}

    for i in range(len(norms) - n + 1):
        ngram = tuple(norms[i : i + n])
        if not any(ngram):
            continue
        if ngram not in seen:
            seen[ngram] = i
            continue
        j = seen[ngram]
        if j + n > i:
            continue
        run = 1
        while run < TAIL_DEDUP_MIN_RUN and i + run + n <= len(norms) and j + run + n <= len(norms):
            ngram_i = tuple(norms[i + run : i + run + n])
            ngram_j = tuple(norms[j + run : j + run + n])
            if ngram_i != ngram_j or not any(ngram_i):
                break
            run += 1
        if run < TAIL_DEDUP_MIN_RUN:
            continue
        block_len = n + (run - 1)
        block_start = i
        for pos in range(i - 1, max(0, i - 300), -1):
            if cs._SENTENCE_END.match(words[pos].strip()):
                block_start = pos + 1
                break
        block_end = min(block_start + block_len, len(words))
        for pos in range(block_end, min(block_end + 100, len(words))):
            if cs._SENTENCE_END.match(words[pos].strip()):
                block_end = pos + 1
                break
        removed_words = words[block_start:block_end]
        info.update(
            {
                "match_found": True,
                "first_ngram_index_i": i,
                "first_ngram_index_j": j,
                "run": run,
                "block_start_word": block_start,
                "block_end_word": block_end,
                "removed_word_count": block_end - block_start,
                "ngram_sample_words": " ".join(words[i : i + min(12, n)]),
                "removed_sample_start": " ".join(removed_words[:20]),
                "removed_sample_end": " ".join(removed_words[-20:]) if len(removed_words) > 20 else "",
            }
        )
        return info
    return info


def run_legacy_trace(raw: str) -> list[StepDiag]:
    steps: list[StepDiag] = []
    prev: str | None = None

    def add(name: str, text: str) -> str:
        nonlocal prev
        steps.append(StepDiag(name=name, text=text, prev_text=prev))
        prev = text
        return text

    add("before_clean_text", raw)
    t, _ = strip_literotica_source_header(raw)
    add("after_strip_literotica_pre", t)
    t = cs.remove_technical_header(t)
    add("after_remove_technical_header", t)
    t = cs.remove_page_separators(t)
    add("after_remove_page_separators", t)
    t = cs.remove_bestweapon_links(t)
    add("after_remove_bestweapon_links", t)
    t = cs.remove_sentences_with_urls(t)
    add("after_remove_sentences_with_urls", t)
    t = cs.remove_spaces_before_dot_comma(t)
    add("after_remove_spaces_before_dot_comma", t)
    t = cs.fix_period_spacing(t)
    add("after_fix_period_spacing", t)
    add("before_cut_duplicate_tail_by_ngrams", t)
    ngram_info_holder: dict[str, Any] = {"inspect": inspect_ngram_dedup(t)}
    t_after = cs.cut_duplicate_tail_by_ngrams(t)
    steps.append(
        StepDiag(
            name="after_cut_duplicate_tail_by_ngrams",
            text=t_after,
            prev_text=t,
        )
    )
    steps[-1].ngram_inspect = ngram_info_holder["inspect"]  # type: ignore[attr-defined]
    t = t_after
    prev = t
    t = cs.remove_duplicate_paragraphs(t)
    add("after_remove_duplicate_paragraphs", t)
    t = cs.remove_duplicate_paragraph_sequences(t)
    add("after_remove_duplicate_paragraph_sequences", t)
    t = cs.collapse_blank_lines(t)
    add("after_collapse_blank_lines", t)
    t = t.strip()
    add("after_strip_whitespace", t)
    t, _ = strip_literotica_source_header(t)
    add("after_strip_literotica_post", t)
    add("after_clean_text_full", cs.clean_text(raw))
    out_drive, *_ = _clean_text_for_drive_tts(t)
    add("after_clean_text_for_drive_tts", out_drive)
    return steps


def micro_pattern_threshold(metrics: dict[str, Any], para_count: int) -> bool:
    """True if looks like one-short-phrase-per-paragraph pattern."""
    if para_count < 10:
        return False
    if metrics["short_paragraphs_lt80"] / max(para_count, 1) >= 0.55:
        return True
    if metrics["max_consecutive_short_paragraphs"] >= 8:
        return True
    return False


def format_row(step: StepDiag, metrics: dict[str, Any]) -> str:
    ch = "YES" if step.changed else "no"
    notes = ""
    if hasattr(step, "ngram_inspect"):
        ni = step.ngram_inspect  # type: ignore[attr-defined]
        if ni.get("match_found"):
            notes = (
                f"ngram_match i={ni.get('first_ngram_index_i')} j={ni.get('first_ngram_index_j')} "
                f"removed_words={ni.get('removed_word_count')}"
            )
        else:
            notes = "ngram: no match (no-op)"
    return (
        f"| {step.name} | {ch} | {metrics['paragraphs_count']} | {metrics['short_paragraphs_lt80']} | "
        f"{metrics['short_paragraphs_lt150']} | {metrics['max_consecutive_short_paragraphs']} | {notes} |"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Trace legacy + drive text cleaning steps (read-only).")
    ap.add_argument(
        "--input",
        type=Path,
        default=_REPO / "stories" / "input" / "A Charity Case.txt",
    )
    ap.add_argument(
        "--launch-source",
        type=Path,
        default=_REPO / "Запуски" / "SITE_FULL_20260513_1309" / "01_Общее" / "01_Исходные_рассказы" / "Вход" / "A Charity Case.txt",
    )
    ap.add_argument(
        "--cleaned",
        type=Path,
        default=_REPO
        / "Запуски"
        / "SITE_FULL_20260513_1309"
        / "05_Рассказы"
        / "A Charity Case"
        / "03_Сайт"
        / "01_Очищенный_текст"
        / "cleaned_story.txt",
    )
    ap.add_argument(
        "--drive-mirror",
        type=Path,
        default=_REPO
        / "Запуски"
        / "SITE_FULL_20260513_1309"
        / "02_Сайт"
        / "01_Очистка_текста"
        / "A Charity Case"
        / "A Charity Case__U.txt",
    )
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()

    lines_out: list[str] = []

    def emit(s: str = "") -> None:
        lines_out.append(s)
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))

    raw = args.input.read_text(encoding="utf-8")
    emit("# Text cleaning step trace — A Charity Case")
    emit()
    emit(f"Input: {args.input}")
    emit()

    steps = run_legacy_trace(raw)
    first_bad: str | None = None
    prev_metrics: dict[str, Any] | None = None

    emit("## Root cause trace")
    emit()
    emit("| step | changed? | paragraphs_count | short_lt80 | short_lt150 | max_consecutive_short | notes |")
    emit("|------|----------|------------------|------------|-------------|------------------------|-------|")

    for step in steps:
        m = paragraph_metrics(step.text)
        emit(format_row(step, m))
        if first_bad is None and prev_metrics is not None:
            was_bad = micro_pattern_threshold(prev_metrics, prev_metrics["paragraphs_count"])
            now_bad = micro_pattern_threshold(m, m["paragraphs_count"])
            if not was_bad and now_bad:
                first_bad = step.name
        prev_metrics = m

    emit()
    emit("## Step details")
    for step in steps:
        m = paragraph_metrics(step.text)
        emit(f"### {step.name}")
        emit(f"- changed: {step.changed}")
        emit(f"- chars: {m['chars']}, lines: {m['lines']}, double_newline_count: {m['double_newline_count']}")
        emit(
            f"- paragraphs: {m['paragraphs_count']}, short_lt80: {m['short_paragraphs_lt80']}, "
            f"short_lt150: {m['short_paragraphs_lt150']}, short_lt300: {m['short_paragraphs_lt300']}"
        )
        emit(
            f"- max_consecutive_short: {m['max_consecutive_short_paragraphs']}, "
            f"one_sentence_paragraphs_heuristic: {m['one_sentence_paragraphs_heuristic']}"
        )
        if step.name == "after_cut_duplicate_tail_by_ngrams" and hasattr(step, "ngram_inspect"):
            ni = step.ngram_inspect  # type: ignore[attr-defined]
            emit(f"- ngram_inspect: {ni}")
        if step.changed and step.prev_text is not None:
            emit("- preview (first 30 lines after step):")
            emit("```")
            emit(m["preview_30_lines"])
            emit("```")
        emit()

    # cut_duplicate verdict
    emit("## cut_duplicate_tail_by_ngrams verdict")
    before_step = next(s for s in steps if s.name == "before_cut_duplicate_tail_by_ngrams")
    after_step = next(s for s in steps if s.name == "after_cut_duplicate_tail_by_ngrams")
    changed = after_step.text != before_step.text
    emit(f"- changed text: **{'yes' if changed else 'no'}**")
    if changed:
        bm = paragraph_metrics(before_step.text)
        am = paragraph_metrics(after_step.text)
        emit(f"- chars before/after: {bm['chars']} -> {am['chars']}")
        emit(f"- words before/after: {len(before_step.text.split())} -> {len(after_step.text.split())}")
        emit(f"- paragraphs before/after: {bm['paragraphs_count']} -> {am['paragraphs_count']}")
        if hasattr(after_step, "ngram_inspect"):
            emit(f"- detection detail: {after_step.ngram_inspect}")  # type: ignore[attr-defined]
        emit("- preview before (lines 1-15):")
        emit("```")
        emit("\n".join(before_step.text.splitlines()[:15]))
        emit("```")
        emit("- preview after (lines 1-15):")
        emit("```")
        emit("\n".join(after_step.text.splitlines()[:15]))
        emit("```")
    else:
        emit("- **cut_duplicate_tail_by_ngrams did not change this story** (returned input unchanged).")
        if hasattr(after_step, "ngram_inspect"):
            emit(f"- inspect: {after_step.ngram_inspect}")  # type: ignore[attr-defined]

    emit()
    emit("## First bad step")
    if first_bad:
        emit(f"**{first_bad}** — first step where micro-paragraph pattern crosses threshold.")
    else:
        emit("No threshold crossing detected in trace; see file comparison below.")

    emit()
    emit("## File comparison (input vs artifacts)")
    for label, path in [
        ("stories/input", args.input),
        ("launch source", args.launch_source),
        ("cleaned_story.txt", args.cleaned),
        ("drive mirror", args.drive_mirror),
    ]:
        if path.is_file():
            m = paragraph_metrics(path.read_text(encoding="utf-8"))
            emit(
                f"- **{label}** (`{path.name}`): paragraphs={m['paragraphs_count']}, "
                f"short_lt80={m['short_paragraphs_lt80']}, max_consec_short={m['max_consecutive_short_paragraphs']}, "
                f"double_nl={m['double_newline_count']}"
            )
        else:
            emit(f"- **{label}**: missing `{path}`")

    emit()
    emit("## Recommended fix")
    emit("(Recommendations only — not applied.)")

    if changed:
        emit(
            "- In `legacy/bulk-text-cleaner/clean_stories.py`, `cut_duplicate_tail_by_ngrams`: "
            "remove or scope the global `re.sub(r\"([.!?])\\s+([А-ЯA-Z])\", r\"\\1\\n\\n\\2\", out)` "
            "so it does not reformat the entire story after a duplicate cut."
        )
        emit("- Tests: `A Charity Case.txt` full trace; assert paragraphs_count not >2x sentence count; "
              "assert short_lt80 ratio < 0.3 after clean.")
    else:
        emit("- Do not disable `cut_duplicate_tail_by_ngrams` for this story; investigate the step marked "
              "`First bad step` above or differences between launch source and stories/input.")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
        print(f"\nWrote report: {args.report}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
