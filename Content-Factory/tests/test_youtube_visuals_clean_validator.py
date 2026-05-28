from __future__ import annotations

import json
from pathlib import Path

from orchestrator.youtube_visuals_clean import validate_visual_characters_file


def _characters_file(tmp_path: Path, anchor: str) -> Path:
    path = tmp_path / "characters.txt"
    path.write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "id": "CHAR_1",
                        "role": "test",
                        "anchor": anchor,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_accepts_hyphenated_adult_age_range(tmp_path: Path) -> None:
    result = validate_visual_characters_file(_characters_file(tmp_path, "A 28-to-32-year-old man in side profile."))

    assert result["ok"]
    assert result["adult_band_findings"] == []


def test_accepts_aged_adult_range(tmp_path: Path) -> None:
    result = validate_visual_characters_file(_characters_file(tmp_path, "A woman aged 28 to 32 with stable clothing."))

    assert result["ok"]
    assert result["adult_band_findings"] == []


def test_accepts_decade_adult_range(tmp_path: Path) -> None:
    result = validate_visual_characters_file(_characters_file(tmp_path, "A man in his late 20s to early 30s in a wide shot."))

    assert result["ok"]
    assert result["adult_band_findings"] == []


def test_rejects_youthful_anchor_even_with_adult_number(tmp_path: Path) -> None:
    result = validate_visual_characters_file(_characters_file(tmp_path, "A youthful man in his 30s with stable clothing."))

    assert not result["ok"]
    assert result["adult_band_findings"]


def test_rejects_young_looking_anchor(tmp_path: Path) -> None:
    result = validate_visual_characters_file(_characters_file(tmp_path, "A young-looking woman with stable clothing."))

    assert not result["ok"]
    assert result["adult_band_findings"]


def test_rejects_anchor_without_age(tmp_path: Path) -> None:
    result = validate_visual_characters_file(_characters_file(tmp_path, "A man with stable clothing and side profile framing."))

    assert not result["ok"]
    assert result["adult_band_findings"]
