from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.youtube_full_auto.layout import queue_paths, utc_now


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class StoryQueueItem:
    story_key: str
    canonical_basename: str
    cleaned_path: str
    word_count: int
    estimated_minutes: float
    status: str
    site_run_id: str
    mini_youtube_run_id: str = ""
    retry_count: int = 0
    last_error: str = ""
    last_worker: str = ""
    last_account_index: int = -1
    stages: dict[str, Any] = field(default_factory=dict)
    output_story_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_key": self.story_key,
            "canonical_basename": self.canonical_basename,
            "cleaned_path": self.cleaned_path,
            "word_count": self.word_count,
            "estimated_minutes": self.estimated_minutes,
            "status": self.status,
            "site_run_id": self.site_run_id,
            "mini_youtube_run_id": self.mini_youtube_run_id,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "last_worker": self.last_worker,
            "last_account_index": self.last_account_index,
            "stages": self.stages,
            "output_story_dir": self.output_story_dir,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> StoryQueueItem:
        return cls(
            story_key=str(row.get("story_key") or ""),
            canonical_basename=str(row.get("canonical_basename") or ""),
            cleaned_path=str(row.get("cleaned_path") or ""),
            word_count=int(row.get("word_count") or 0),
            estimated_minutes=float(row.get("estimated_minutes") or 0),
            status=str(row.get("status") or "discovered"),
            site_run_id=str(row.get("site_run_id") or ""),
            mini_youtube_run_id=str(row.get("mini_youtube_run_id") or ""),
            retry_count=int(row.get("retry_count") or 0),
            last_error=str(row.get("last_error") or ""),
            last_worker=str(row.get("last_worker") or ""),
            last_account_index=int(row.get("last_account_index", -1)),
            stages=dict(row.get("stages") or {}) if isinstance(row.get("stages"), dict) else {},
            output_story_dir=str(row.get("output_story_dir") or ""),
        )


class QueueStore:
    def __init__(
        self,
        batch_root: Path,
        *,
        config: OrchestratorConfig | None = None,
        youtube_run_id: str = "",
    ) -> None:
        self.batch_root = batch_root
        self.config = config
        self.youtube_run_id = youtube_run_id
        self.paths = queue_paths(
            batch_root,
            config=config,
            youtube_run_id=youtube_run_id,
        )
        self._items: dict[str, StoryQueueItem] = {}
        self._meta: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        payload = _read_json(self.paths["queue_json"])
        self._meta = {k: v for k, v in payload.items() if k != "items"}
        items = payload.get("items", [])
        self._items = {}
        if isinstance(items, list):
            for row in items:
                if isinstance(row, dict):
                    item = StoryQueueItem.from_dict(row)
                    if item.story_key:
                        self._items[item.story_key] = item

    def save(self) -> None:
        payload = {
            **self._meta,
            "updated_at": utc_now(),
            "items": [item.to_dict() for item in self._items.values()],
        }
        _write_json(self.paths["queue_json"], payload)
        self._write_csv()

    def _write_csv(self) -> None:
        rows = [item.to_dict() for item in self._items.values()]
        if not rows:
            return
        path = self.paths["queue_csv"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def set_meta(self, **kwargs: Any) -> None:
        self._meta.update(kwargs)

    def meta(self) -> dict[str, Any]:
        return dict(self._meta)

    def items(self) -> list[StoryQueueItem]:
        return list(self._items.values())

    def get(self, story_key: str) -> StoryQueueItem | None:
        return self._items.get(story_key)

    def upsert(self, item: StoryQueueItem) -> None:
        self._items[item.story_key] = item

    def replace_items(self, items: list[StoryQueueItem]) -> None:
        self._items = {item.story_key: item for item in items if item.story_key}

    def by_status(self, *statuses: str) -> list[StoryQueueItem]:
        wanted = {s.lower() for s in statuses}
        return [i for i in self._items.values() if i.status.lower() in wanted]

    def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self._items.values():
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def record_stage(
        self,
        item: StoryQueueItem,
        *,
        stage: str,
        status: str,
        worker: str = "",
        account_index: int = -1,
        input_path: str = "",
        output_path: str = "",
        error: str = "",
        reason_code: str = "",
        bridge_exit_code: int | None = None,
        chrome_profile: str = "",
        proxy_enabled: bool | None = None,
        finished: bool = False,
    ) -> None:
        now = utc_now()
        stage_row = dict(item.stages.get(stage) or {})
        if not stage_row.get("started_at"):
            stage_row["started_at"] = now
        if finished:
            stage_row["finished_at"] = now
        stage_row.update(
            {
                "status": status,
                "worker": worker,
                "worker_id": worker,
                "account_index": account_index,
                "input_path": input_path,
                "output_path": output_path,
                "error": error,
                "reason_code": reason_code,
                "bridge_exit_code": bridge_exit_code,
                "chrome_profile": chrome_profile,
                "proxy_enabled": proxy_enabled,
                "retry_count": item.retry_count,
            }
        )
        item.stages[stage] = stage_row
        item.status = status
        if worker:
            item.last_worker = worker
        if account_index >= 0:
            item.last_account_index = account_index
        if error:
            item.last_error = error
            _append_jsonl(
                self.paths["errors_jsonl"],
                {
                    "timestamp": now,
                    "story_key": item.story_key,
                    "canonical_basename": item.canonical_basename,
                    "stage": stage,
                    "status": status,
                    "error": error,
                    "reason_code": reason_code,
                    "worker": worker,
                    "worker_id": worker,
                    "account_index": account_index,
                    "bridge_exit_code": bridge_exit_code,
                    "chrome_profile": chrome_profile,
                    "proxy_enabled": proxy_enabled,
                },
            )
        self.upsert(item)

    def write_stage_status_snapshot(self) -> None:
        snapshot = {
            "updated_at": utc_now(),
            "counts": self.count_by_status(),
            "total": len(self._items),
        }
        _write_json(self.paths["stage_status"], snapshot)
