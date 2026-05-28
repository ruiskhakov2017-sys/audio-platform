from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from orchestrator.site_visual_validate import (
    TECHNICAL_PROMPT,
    StoryVisualRecord,
    run_validate_site_info_visuals,
)


@dataclass
class VisualStory:
    """Thin adapter over StoryVisualRecord for ComfyUI auto stage."""

    canonical: str
    story_dir: Path
    info_path: Path
    image_path: Path
    visual_prompt_full: str
    visual_prompt_preview: str
    visual_prompt_status: str
    failure_reason: str
    extraction_source: str
    final_prompt: str

    @property
    def visual_prompt(self) -> str:
        return self.visual_prompt_full

    @property
    def is_valid_for_generation(self) -> bool:
        return self.visual_prompt_status == "ok" and bool(self.visual_prompt_full.strip())

    @classmethod
    def from_record(cls, record: StoryVisualRecord) -> VisualStory:
        out_dir = record.output_story_dir
        return cls(
            canonical=record.canonical_basename,
            story_dir=out_dir if out_dir.is_dir() else record.story_workspace_path,
            info_path=(out_dir / "info.txt") if out_dir.is_dir() else record.story_workspace_path / "info.txt",
            image_path=(out_dir / f"{record.canonical_basename}.jpg") if out_dir.is_dir() else record.story_workspace_path / f"{record.canonical_basename}.jpg",
            visual_prompt_full=record.visual_prompt_full,
            visual_prompt_preview=record.visual_prompt_preview,
            visual_prompt_status=record.visual_prompt_status,
            failure_reason=record.failure_reason,
            extraction_source=record.extraction_source,
            final_prompt=record.final_prompt,
        )


@dataclass
class VisualRunResult:
    ok: bool
    mode: str
    generated_count: int
    failed_count: int
    csv_path: Path
    xlsx_path: Path | None
    invalid_csv_path: Path | None = None
    build_report_path: Path | None = None
    valid_row_count: int = 0
    invalid_row_count: int = 0
    errors: list[str] = field(default_factory=list)


def collect_visual_rows(
    output_dir: Path,
    *,
    runs_site_stories_dir: Path | None = None,
    export_dir: Path | None = None,
) -> list[VisualStory]:
    """Legacy name: delegates to validate_site_info_visuals collector."""
    runs_dir = runs_site_stories_dir or (output_dir.parent.parent / "runs" / "site")
    # Prefer explicit stories dir when provided
    if runs_site_stories_dir is None:
        runs_site_stories_dir = output_dir.parent / "runs" / "site"
        for candidate in output_dir.parent.parent.glob("runs/site/*-a/stories"):
            if candidate.is_dir():
                runs_site_stories_dir = candidate
                break

    from orchestrator.site_visual_validate import collect_story_visual_records

    records = collect_story_visual_records(
        runs_stories_dir=runs_site_stories_dir,
        output_site_dir=output_dir,
        export_dir=export_dir,
    )
    return [VisualStory.from_record(r) for r in records if r.is_valid]


def write_visual_exports(rows: list[VisualStory], export_dir: Path) -> tuple[Path, Path | None, Path, Path, dict[str, Any]]:
    """Deprecated: use run_validate_site_info_visuals. Kept for tests importing write_visual_exports."""
    from orchestrator.site_visual_validate import collect_story_visual_records, write_visual_prompt_tables

    # Re-build records from valid VisualStory only is insufficient; tests pass full collector path
    raise RuntimeError("write_visual_exports: use run_validate_site_info_visuals instead")


def _normalize_api_url(pod_url: str) -> str:
    base = pod_url.strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/api"):
        return base
    return base


def _load_workflow(workflow_path: Path) -> dict[str, Any]:
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
    return json.loads(workflow_path.read_text(encoding="utf-8"))


def _healthcheck_comfy(base_url: str) -> str:
    candidates = [base_url, f"{base_url}/api"]
    last_error = "unknown"
    for candidate in candidates:
        try:
            resp = requests.get(f"{candidate}/object_info", timeout=20)
            if resp.status_code == 200:
                return candidate
            last_error = f"{candidate} -> HTTP {resp.status_code}"
        except Exception as exc:
            last_error = f"{candidate} -> {exc}"
    raise RuntimeError(f"ComfyUI API unreachable: {last_error}")


def _render_workflow_prompt(workflow: dict[str, Any], prompt_text: str, text_node: str, seed_node: str) -> dict[str, Any]:
    payload = json.loads(json.dumps(workflow))
    if text_node not in payload:
        raise RuntimeError(f"Workflow text node missing: {text_node}")
    payload[text_node].setdefault("inputs", {})
    payload[text_node]["inputs"]["text"] = prompt_text
    if seed_node in payload:
        payload[seed_node].setdefault("inputs", {})
        inputs = payload[seed_node]["inputs"]
        seed = random.randint(0, 2**32 - 1)
        if "seed" in inputs:
            inputs["seed"] = seed
        elif "noise_seed" in inputs:
            inputs["noise_seed"] = seed
    return payload


def _poll_history(api_url: str, prompt_id: str, timeout_sec: int = 900) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        resp = requests.get(f"{api_url}/history/{prompt_id}", timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            if prompt_id in data:
                return data[prompt_id]
        time.sleep(2)
    raise TimeoutError(f"ComfyUI timeout for prompt_id={prompt_id}")


def _extract_first_image(api_url: str, history_node: dict[str, Any]) -> bytes:
    outputs = history_node.get("outputs", {})
    for node_data in outputs.values():
        images = node_data.get("images", [])
        if not images:
            continue
        first = images[0]
        params = {
            "filename": first["filename"],
            "subfolder": first.get("subfolder", ""),
            "type": first.get("type", "output"),
        }
        resp = requests.get(f"{api_url}/view", params=params, timeout=120)
        resp.raise_for_status()
        return resp.content
    raise RuntimeError("ComfyUI returned no images in history payload")


def run_visual_stage(
    output_dir: Path,
    export_dir: Path,
    mode: str,
    pod_url: str,
    workflow_path: Path,
    text_node: str = "93",
    seed_node: str = "31",
    *,
    runs_site_stories_dir: Path | None = None,
) -> VisualRunResult:
    validate_result = run_validate_site_info_visuals(
        runs_stories_dir=runs_site_stories_dir or (export_dir.parent / "stories"),
        output_site_dir=output_dir,
        export_dir=export_dir,
    )
    if not validate_result.ok:
        return VisualRunResult(
            ok=False,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=validate_result.valid_csv_path,
            xlsx_path=validate_result.xlsx_path,
            invalid_csv_path=validate_result.invalid_csv_path,
            build_report_path=validate_result.report_path,
            errors=[validate_result.message],
        )

    report = validate_result.report
    valid_rows = [VisualStory.from_record(r) for r in validate_result.records if r.is_valid]
    invalid_n = int(report.get("invalid_prompts", report.get("invalid_rows", 0)))

    from orchestrator.site_visual_validate import print_visual_gate_summary

    print_visual_gate_summary(export_dir)

    if mode == "manual":
        return VisualRunResult(
            ok=True,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=validate_result.valid_csv_path,
            xlsx_path=validate_result.xlsx_path,
            invalid_csv_path=validate_result.invalid_csv_path,
            build_report_path=validate_result.report_path,
            valid_row_count=int(report.get("valid_prompts", 0)),
            invalid_row_count=invalid_n,
            errors=[],
        )

    if mode != "auto":
        return VisualRunResult(
            ok=False,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=validate_result.valid_csv_path,
            xlsx_path=validate_result.xlsx_path,
            invalid_csv_path=validate_result.invalid_csv_path,
            build_report_path=validate_result.report_path,
            valid_row_count=int(report.get("valid_prompts", 0)),
            invalid_row_count=invalid_n,
            errors=[f"Unsupported visual mode: {mode}"],
        )

    if not pod_url.strip():
        return VisualRunResult(
            ok=False,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=validate_result.valid_csv_path,
            xlsx_path=validate_result.xlsx_path,
            invalid_csv_path=validate_result.invalid_csv_path,
            build_report_path=validate_result.report_path,
            valid_row_count=int(report.get("valid_prompts", 0)),
            invalid_row_count=invalid_n,
            errors=["AUTO visual mode requires pod URL (--visual-pod-url)."],
        )

    if invalid_n > 0:
        print(
            f"[visual-gate] auto ComfyUI: generating for {len(valid_rows)} valid stories only; "
            f"{invalid_n} invalid excluded (see {validate_result.invalid_csv_path})",
            flush=True,
        )

    try:
        api_url = _healthcheck_comfy(_normalize_api_url(pod_url))
        workflow = _load_workflow(workflow_path)
    except Exception as exc:
        return VisualRunResult(
            ok=False,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=validate_result.valid_csv_path,
            xlsx_path=validate_result.xlsx_path,
            invalid_csv_path=validate_result.invalid_csv_path,
            build_report_path=validate_result.report_path,
            valid_row_count=int(report.get("valid_prompts", 0)),
            invalid_row_count=invalid_n,
            errors=[str(exc)],
        )

    ok_count = 0
    fail_count = 0
    errors: list[str] = []
    for row in valid_rows:
        if row.image_path.exists():
            ok_count += 1
            continue
        if not row.final_prompt.strip():
            fail_count += 1
            errors.append(f"{row.canonical}: empty final_prompt")
            continue
        try:
            payload = _render_workflow_prompt(workflow, row.final_prompt, text_node=text_node, seed_node=seed_node)
            queued = requests.post(f"{api_url}/prompt", json={"prompt": payload}, timeout=120)
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            hist = _poll_history(api_url, prompt_id)
            image_bytes = _extract_first_image(api_url, hist)
            row.image_path.write_bytes(image_bytes)
            ok_count += 1
        except Exception as exc:
            fail_count += 1
            errors.append(f"{row.canonical}: {exc}")

    return VisualRunResult(
        ok=fail_count == 0,
        mode=mode,
        generated_count=ok_count,
        failed_count=fail_count,
        csv_path=validate_result.valid_csv_path,
        xlsx_path=validate_result.xlsx_path,
        invalid_csv_path=validate_result.invalid_csv_path,
        build_report_path=validate_result.report_path,
        valid_row_count=int(report.get("valid_prompts", 0)),
        invalid_row_count=invalid_n,
        errors=errors,
    )
