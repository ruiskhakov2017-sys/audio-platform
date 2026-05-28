from __future__ import annotations



import re

from typing import Any



_LITEROTICA_PAGES_LINE_RE = re.compile(r"(?iu)\bстраниц\s+текста\s*:\s*\d+\b")

_LITEROTICA_PAGES_PREFIX_RE = re.compile(r"(?iu)^\s*страниц\s+текста\s*:\s*\d+\s*")

# literotica + optional spaces + . + com + optional /s/slug (flexible spacing)

_LITEROTICA_DOMAIN_PREFIX_RE = re.compile(

    r"(?iu)^\s*"

    r"(?:https?://)?(?:www\.)?"

    r"l\s*i\s*t\s*e\s*r\s*o\s*t\s*i\s*c\s*a\s*"

    r"\s*\.\s*"

    r"c\s*o\s*m\s*"

    r"(?:\s*/\s*s\s*/\s*[^\s]+)?"

    r"\s*"

)





def _line_has_literotica_domain(line: str) -> bool:

    normalized = re.sub(r"\s+", "", (line or "").lower())

    return "literotica.com" in normalized





def _line_has_pages_meta(line: str) -> bool:

    return bool(_LITEROTICA_PAGES_LINE_RE.search(line or ""))





def _line_is_header_only(line: str) -> bool:

    """True if line is only domain/path and/or pages meta (no story body)."""

    s = (line or "").strip()

    if not s:

        return False

    if not (_line_has_literotica_domain(s) or _line_has_pages_meta(s)):

        return False

    rest, _ = _strip_literotica_technical_prefix_from_line(s)

    return not rest.strip()





def _strip_literotica_technical_prefix_from_line(line: str) -> tuple[str, bool]:

    """

    Remove Literotica URL/domain and optional «страниц текста: N» from line start.

    Returns (remainder, removed_any). If remainder non-empty, keep it (same-line header+body).

    """

    s = line or ""

    if not (_line_has_literotica_domain(s) or _line_has_pages_meta(s)):

        return s, False



    rest = s

    removed = False



    dom = _LITEROTICA_DOMAIN_PREFIX_RE.match(rest)

    if dom:

        rest = rest[dom.end() :]

        removed = True



    pages = _LITEROTICA_PAGES_PREFIX_RE.match(rest)

    if pages:

        rest = rest[pages.end() :]

        removed = True

    elif _line_has_pages_meta(rest):

        # «страниц текста: N» not at column 0 but after domain fragment already stripped

        m = _LITEROTICA_PAGES_LINE_RE.search(rest)

        if m and m.start() == 0 or (m and rest[: m.start()].strip() == ""):

            rest = rest[m.end() :]

            removed = True



    if not removed and _line_has_pages_meta(s):

        m = _LITEROTICA_PAGES_LINE_RE.search(s)

        if m and s.strip() == m.group(0).strip():

            return "", True



    return rest.strip(), removed





def strip_literotica_source_header(

    text: str,

    max_header_lines: int = 20,

) -> tuple[str, dict[str, Any]]:

    """

    Удалить техническую шапку Literotica только в первых max_header_lines строках.

    Целые строки-шапку удаляем; same-line «шапка + тело» — только префикс.

    """

    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")

    lines = raw.split("\n")

    header_end = min(max(0, int(max_header_lines)), len(lines))

    removed_samples: list[str] = []

    removed_count = 0

    out_lines: list[str] = []



    for i, line in enumerate(lines):

        if i >= header_end:

            out_lines.append(line)

            continue

        if not (_line_has_literotica_domain(line) or _line_has_pages_meta(line)):

            out_lines.append(line)

            continue



        if _line_is_header_only(line):

            removed_count += 1

            if len(removed_samples) < 12:

                sample = line.strip()

                if sample:

                    removed_samples.append(sample[:240])

            continue



        remainder, stripped = _strip_literotica_technical_prefix_from_line(line)

        if stripped:

            removed_count += 1

            if len(removed_samples) < 12:

                sample = line.strip()

                if sample:

                    removed_samples.append(sample[:240])

        if remainder:

            out_lines.append(remainder)

        elif stripped:

            pass

        else:

            out_lines.append(line)



    start = 0

    while start < len(out_lines) and not out_lines[start].strip():

        start += 1

    result = "\n".join(out_lines[start:])



    diagnostics: dict[str, Any] = {

        "removed_literotica_header_lines_count": removed_count,

        "removed_literotica_header_lines_sample": removed_samples,

    }

    return result, diagnostics





def literotica_header_remnant_warning(text: str, max_header_lines: int = 20) -> str | None:

    """Предупреждение, если в начале файла остались literotica / страниц текста."""

    lines = (text or "").replace("\r\n", "\n").split("\n")

    for line in lines[: max(0, int(max_header_lines))]:

        s = line.strip()

        if not s:

            continue

        norm = re.sub(r"\s+", "", s.lower())

        if "literotica" in norm and _line_has_literotica_domain(s):

            return f"literotica still in header: {s[:160]!r}"

        if _line_has_pages_meta(s) and _line_is_header_only(s):

            return f"pages meta still in header: {s[:160]!r}"

    return None

