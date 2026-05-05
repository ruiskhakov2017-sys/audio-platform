from orchestrator.site_tts.info_parser import parse_voice_type_mfu
from orchestrator.site_tts.text_chunking import pack_paragraph_chunks


def test_parse_voice_type_last_wins() -> None:
    txt = "Тип голоса: M\nfoo\nТип голоса: F\n"
    assert parse_voice_type_mfu(txt) == "F"


def test_parse_voice_type_default_u() -> None:
    assert parse_voice_type_mfu("no marker") == "U"


def test_pack_paragraph_chunks() -> None:
    text = "a" * 100 + "\n\n" + "b" * 100 + "\n\n" + "c" * 400
    chunks = pack_paragraph_chunks(text, max_chars=250)
    assert len(chunks) >= 2
    assert all(len(c) <= 280 for c in chunks)
