from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StatusRecord:
    story_id: str
    pipeline: str
    stage: str
    state: str
    timestamp: str
    message: str


class StatusStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        story_id: str,
        pipeline: str,
        stage: str,
        state: str,
        message: str,
    ) -> StatusRecord:
        rec = StatusRecord(
            story_id=story_id,
            pipeline=pipeline,
            stage=stage,
            state=state,
            timestamp=utc_now(),
            message=message,
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        return rec

    def read_all(self) -> List[StatusRecord]:
        if not self.path.exists():
            return []
        out: List[StatusRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out.append(StatusRecord(**row))
        return out

    def latest(self, limit: int = 20) -> Iterable[StatusRecord]:
        return self.read_all()[-limit:]
