from __future__ import annotations


def pack_paragraph_chunks(text: str, max_chars: int) -> list[str]:
    """
    Разбивает текст по абзацам (пустые строки), упаковывая в чанки <= max_chars.
    Слишком длинный абзац режется по предложениям / жёстко по max_chars.
    """
    if max_chars < 200:
        max_chars = 200
    raw_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not raw_paras:
        t = text.strip()
        return _split_oversized_paragraph(t, max_chars) if t else []

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0

    for para in raw_paras:
        if len(para) > max_chars:
            flush()
            chunks.extend(_split_oversized_paragraph(para, max_chars))
            continue
        add_len = len(para) + (2 if buf else 0)
        if buf_len + add_len > max_chars:
            flush()
        buf.append(para)
        buf_len += add_len
    flush()
    return chunks


def _split_oversized_paragraph(para: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(para):
        end = min(start + max_chars, len(para))
        slice_ = para[start:end]
        if end < len(para):
            cut = slice_.rfind(". ")
            if cut > max_chars // 2:
                slice_ = slice_[: cut + 1]
                end = start + len(slice_)
        parts.append(slice_.strip())
        start = end
    return [p for p in parts if p]
