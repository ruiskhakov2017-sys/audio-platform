from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventLogger:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        *,
        run_id: str,
        story_id: str,
        pipeline: str,
        stage: str,
        action: str,
        result: str,
        message: str,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        record = {
            "timestamp": _utc_now(),
            "run_id": run_id,
            "story_id": story_id,
            "pipeline": pipeline,
            "stage": stage,
            "action": action,
            "result": result,
            "message": message,
            "payload": payload or {},
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
