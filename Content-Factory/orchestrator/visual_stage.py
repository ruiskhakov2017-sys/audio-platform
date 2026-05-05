from __future__ import annotations

import csv
import io
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

TECHNICAL_PROMPT = (
    "A photorealistic raw full-figure photograph. Natural, unposed lighting "
    "showing realistic skin texture. Shot on 35mm film, slight grain, high detail, 8k."
)

VISUAL_RE = re.compile(r"^\s*(?:визуал|визуальный\s+промпт)\s*:\s*(.+)\s*$", re.IGNORECASE)
TITLE_RE = re.compile(r"^\s*(?:заголовок|title)\s*:\s*(.+)\s*$", re.IGNORECASE)


@dataclass
class VisualStory:
    canonical: str
    story_dir: Path
    info_path: Path
    image_path: Path
    visual_prompt: str
    final_prompt: str


@dataclass
class VisualRunResult:
    ok: bool
    mode: str
    generated_count: int
    failed_count: int
    csv_path: Path
    xlsx_path: Path | None
    errors: list[str]


def _read_info_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_visual_prompt(info_text: str, fallback_title: str) -> str:
    if not info_text.strip():
        return fallback_title
    for line in info_text.splitlines():
        match = VISUAL_RE.match(line.strip())
        if match:
            value = match.group(1).strip()
            if value:
                return value
    for line in info_text.splitlines():
        match = TITLE_RE.match(line.strip())
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return fallback_title


def collect_visual_rows(output_dir: Path) -> list[VisualStory]:
    stories: list[VisualStory] = []
    if not output_dir.exists():
        return stories
    for story_dir in sorted([p for p in output_dir.iterdir() if p.is_dir()], key=lambda x: x.name.lower()):
        canonical = story_dir.name
        info_path = story_dir / "info.txt"
        info_text = _read_info_text(info_path)
        visual_prompt = _extract_visual_prompt(info_text, fallback_title=canonical.replace("_", " "))
        final_prompt = f"{TECHNICAL_PROMPT}; {visual_prompt}" if visual_prompt else TECHNICAL_PROMPT
        stories.append(
            VisualStory(
                canonical=canonical,
                story_dir=story_dir,
                info_path=info_path,
                image_path=story_dir / f"{canonical}.jpg",
                visual_prompt=visual_prompt,
                final_prompt=final_prompt,
            )
        )
    return stories


def write_visual_exports(rows: list[VisualStory], export_dir: Path) -> tuple[Path, Path | None]:
    export_dir.mkdir(parents=True, exist_ok=True)
    csv_path = export_dir / "visual_prompts.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["canonical_basename", "story_dir", "technical_prompt", "visual_prompt", "final_prompt"])
        for row in rows:
            writer.writerow(
                [
                    row.canonical,
                    str(row.story_dir),
                    TECHNICAL_PROMPT,
                    row.visual_prompt,
                    row.final_prompt,
                ]
            )

    xlsx_path: Path | None = None
    try:
        from openpyxl import Workbook  # type: ignore

        xlsx_path = export_dir / "visual_prompts.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "visual_prompts"
        ws.append(["canonical_basename", "story_dir", "technical_prompt", "visual_prompt", "final_prompt"])
        for row in rows:
            ws.append([row.canonical, str(row.story_dir), TECHNICAL_PROMPT, row.visual_prompt, row.final_prompt])
        wb.save(xlsx_path)
    except Exception:
        xlsx_path = None

    return csv_path, xlsx_path


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
) -> VisualRunResult:
    rows = collect_visual_rows(output_dir)
    csv_path, xlsx_path = write_visual_exports(rows, export_dir)
    if mode == "manual":
        return VisualRunResult(
            ok=True,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=csv_path,
            xlsx_path=xlsx_path,
            errors=[],
        )

    if mode != "auto":
        return VisualRunResult(
            ok=False,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=csv_path,
            xlsx_path=xlsx_path,
            errors=[f"Unsupported visual mode: {mode}"],
        )

    if not pod_url.strip():
        return VisualRunResult(
            ok=False,
            mode=mode,
            generated_count=0,
            failed_count=0,
            csv_path=csv_path,
            xlsx_path=xlsx_path,
            errors=["AUTO visual mode requires pod URL (--visual-pod-url)."],
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
            csv_path=csv_path,
            xlsx_path=xlsx_path,
            errors=[str(exc)],
        )

    ok_count = 0
    fail_count = 0
    errors: list[str] = []
    for row in rows:
        if row.image_path.exists():
            ok_count += 1
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
        csv_path=csv_path,
        xlsx_path=xlsx_path,
        errors=errors,
    )
