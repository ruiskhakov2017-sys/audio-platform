"""Read-only audit for YouTube visual character anchors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig


FORBIDDEN_ANCHOR_RE = re.compile(
    r"\b(handsome|hot|beautiful|sexy|seductive|very attractive|attractive face|perfect face|model-like|smooth perfect skin)\b",
    re.IGNORECASE,
)


@dataclass
class YoutubeCharactersAnchorAuditOptions:
    story_id: str


def _story_dir(config: OrchestratorConfig, story_id: str) -> Path:
    return (config.root_dir / "output" / "youtube" / story_id).resolve()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def run_youtube_characters_anchor_audit(
    *,
    config: OrchestratorConfig,
    options: YoutubeCharactersAnchorAuditOptions,
) -> dict[str, Any]:
    story_id = str(options.story_id).strip()
    story_dir = _story_dir(config, story_id)
    path = story_dir / "05_characters" / "characters.txt"
    data = _read_json(path)
    missing = [] if path.is_file() else [str(path)]
    characters = data.get("characters") if isinstance(data, dict) else []
    findings: list[dict[str, Any]] = []
    if isinstance(characters, list):
        for idx, item in enumerate(characters, start=1):
            if not isinstance(item, dict):
                continue
            anchor = str(item.get("anchor", "") or "")
            terms = sorted({match.group(0).lower() for match in FORBIDDEN_ANCHOR_RE.finditer(anchor)})
            if terms:
                findings.append(
                    {
                        "id": str(item.get("id", f"CHAR_{idx}") or f"CHAR_{idx}"),
                        "role": str(item.get("role", "") or ""),
                        "forbidden_terms": terms,
                        "anchor": anchor,
                    }
                )
    return {
        "ok": not missing and not findings,
        "status": "missing_characters" if missing else ("invalid" if findings else "ok"),
        "story_id": story_id,
        "characters_path": str(path),
        "style_name": data.get("style_name") if isinstance(data, dict) else "",
        "characters_count": len(characters) if isinstance(characters, list) else 0,
        "forbidden_terms_total": sum(len(item["forbidden_terms"]) for item in findings),
        "invalid_anchors_count": len(findings),
        "findings": findings,
        "missing": missing,
    }
