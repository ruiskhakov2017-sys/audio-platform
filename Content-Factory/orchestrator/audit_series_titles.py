"""
read-only: аудит «серийности» имён по тем же правилам, что return-series-from-input
(normalize_story_base_title / stem_has_explicit_serial_marker).

Два независимых охвата (без склейки между собой):
  - stories/input — только *.txt в каталоге;
  - gemini_input/.../uncategorized — только подпапки очереди.

Иначе одна и та же история в input и в очереди даёт ложный probable_serial (group_size=2).
"""
from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orchestrator.config import OrchestratorConfig
from orchestrator.stories_input_series_return import (
    _QUEUE_TAIL_RE,
    _format_matched_pattern,
    load_original_source_paths,
    normalize_story_base_title,
    stem_has_explicit_serial_marker,
)


SourceTag = Literal["stories_input", "gemini_queue"]
Bucket = Literal["serial", "probable_serial", "uncertain"]


@dataclass(frozen=True)
class AuditItem:
    stem: str
    path: str
    source: SourceTag


def _suggested_txt_name_for_queue_folder(folder_name: str) -> str:
    """Имя .txt для поиска в манифесте: срез хвоста _NNNNN + .txt."""
    base = _QUEUE_TAIL_RE.sub("", folder_name.strip()).strip()
    if base.lower().endswith(".txt"):
        return base
    return f"{base}.txt"


def _collect_stories_input_txt(input_dir: Path) -> list[AuditItem]:
    if not input_dir.is_dir():
        return []
    out: list[AuditItem] = []
    for p in sorted(input_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.suffix.lower() != ".txt":
            continue
        if p.name.startswith("_"):
            continue
        out.append(AuditItem(stem=p.stem, path=str(p.resolve()), source="stories_input"))
    return out


def _collect_gemini_queue_folders(queue_dir: Path) -> list[AuditItem]:
    if not queue_dir.is_dir():
        return []
    out: list[AuditItem] = []
    for p in sorted(queue_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir():
            continue
        out.append(AuditItem(stem=p.name, path=str(p.resolve()), source="gemini_queue"))
    return out


def _classify_bucket(*, group_size: int, group_has_marker: bool, explicit: bool) -> Bucket:
    is_serial = (group_size > 1 and group_has_marker) or (group_size == 1 and explicit)
    if is_serial:
        return "serial"
    if group_size > 1:
        return "probable_serial"
    return "uncertain"


def _manifest_key_for_item(m: AuditItem) -> str:
    if m.source == "stories_input":
        return Path(m.stem + ".txt").name.casefold()
    return _suggested_txt_name_for_queue_folder(m.stem).casefold()


def _audit_items_scope(
    *,
    scope_name: str,
    items: list[AuditItem],
    orig_map: dict[str, Path],
) -> dict[str, Any]:
    meta: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[AuditItem]] = {}
    for it in items:
        norm, strip_tags = normalize_story_base_title(it.stem)
        explicit, explicit_tag = stem_has_explicit_serial_marker(it.stem)
        meta[str(it.path)] = {
            "normalized_base_title": norm,
            "strip_tags": strip_tags,
            "explicit": explicit,
            "explicit_tag": explicit_tag,
        }
        groups.setdefault(norm, []).append(it)

    rows: list[dict[str, Any]] = []
    group_summaries: list[dict[str, Any]] = []

    for norm, members in sorted(groups.items(), key=lambda x: x[0]):
        stems = [m.stem for m in members]
        group_size = len(members)
        group_has_marker = any(stem_has_explicit_serial_marker(s)[0] for s in stems)
        group_serial_flag = bool(group_size > 1 and group_has_marker)
        first_explicit_tag = ""
        for s in stems:
            ex, tag = stem_has_explicit_serial_marker(s)
            if ex:
                first_explicit_tag = tag
                break
        strip_tags0 = meta[str(members[0].path)]["strip_tags"]
        explicit_one, tag_one = stem_has_explicit_serial_marker(members[0].stem) if group_size == 1 else (False, "")
        if group_size == 1:
            matched_pattern = _format_matched_pattern(list(strip_tags0), tag_one if explicit_one else "", False)
            bucket = _classify_bucket(group_size=1, group_has_marker=False, explicit=explicit_one)
        else:
            matched_pattern = _format_matched_pattern(
                list(strip_tags0),
                first_explicit_tag if group_has_marker else "",
                group_serial_flag,
            )
            bucket = _classify_bucket(group_size=group_size, group_has_marker=group_has_marker, explicit=False)

        group_summaries.append(
            {
                "scope": scope_name,
                "normalized_base_title": norm,
                "bucket": bucket,
                "group_size": group_size,
                "group_has_marker": group_has_marker,
                "matched_pattern": matched_pattern,
                "members": [{"stem": m.stem, "path": m.path, "source": m.source} for m in members],
            }
        )

        for m in members:
            manifest_key = _manifest_key_for_item(m)
            has_manifest = manifest_key in orig_map
            return_safe = bool(bucket == "serial" and has_manifest)
            needs_review = not return_safe and (
                bucket in ("probable_serial", "uncertain") or (bucket == "serial" and not has_manifest)
            )
            review_priority = "low"
            if return_safe:
                review_priority = "none"
            elif bucket == "probable_serial" or (bucket == "serial" and not has_manifest):
                review_priority = "high"
            elif bucket == "uncertain" and not has_manifest:
                review_priority = "high"
            elif needs_review:
                review_priority = "low"

            rows.append(
                {
                    "scope": scope_name,
                    "stem": m.stem,
                    "path": m.path,
                    "source": m.source,
                    "normalized_base_title": norm,
                    "bucket": bucket,
                    "group_size": group_size,
                    "group_has_marker": group_has_marker,
                    "matched_pattern": matched_pattern,
                    "manifest_key": manifest_key,
                    "manifest_hit": has_manifest,
                    "return_safe": return_safe,
                    "needs_review": needs_review,
                    "review_priority": review_priority,
                }
            )

    totals = {
        "serial": sum(1 for r in rows if r["bucket"] == "serial"),
        "probable_serial": sum(1 for r in rows if r["bucket"] == "probable_serial"),
        "uncertain": sum(1 for r in rows if r["bucket"] == "uncertain"),
    }
    return_safe_rows = [r for r in rows if r["return_safe"]]
    needs_review_rows = [r for r in rows if r["needs_review"]]
    serial_groups_multi = [g for g in group_summaries if g["bucket"] == "serial" and g["group_size"] > 1]
    probable_groups = [g for g in group_summaries if g["bucket"] == "probable_serial"]

    return {
        "scope": scope_name,
        "items_count": len(items),
        "totals": totals,
        "rows": rows,
        "group_summaries": group_summaries,
        "serial_groups_multi": serial_groups_multi,
        "probable_serial_groups": probable_groups,
        "return_safe": return_safe_rows,
        "needs_review": needs_review_rows,
    }


def run_audit_series_titles(
    *,
    config: OrchestratorConfig,
    stories_input_dir: Path,
    gemini_queue_dir: Path,
    batch_manifest: Path,
    max_examples: int,
) -> dict[str, Any]:
    reports_dir = config.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    input_dir = stories_input_dir.resolve()
    queue_dir = gemini_queue_dir.resolve()
    manifest_path = batch_manifest.resolve()

    input_items = _collect_stories_input_txt(input_dir)
    queue_items = _collect_gemini_queue_folders(queue_dir)
    orig_map = load_original_source_paths(manifest_path)

    input_block = _audit_items_scope(scope_name="stories_input", items=input_items, orig_map=orig_map)
    queue_block = _audit_items_scope(scope_name="gemini_queue", items=queue_items, orig_map=orig_map)

    rows_all = list(input_block["rows"]) + list(queue_block["rows"])

    def _examples_for_scope(scope: str, bucket: Bucket) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rows_all:
            if r["scope"] != scope or r["bucket"] != bucket:
                continue
            out.append({"stem": r["stem"], "path": r["path"], "group_size": r["group_size"]})
            if len(out) >= max_examples:
                break
        return out

    elapsed = max(0.0001, time.perf_counter() - t0)
    report_json = reports_dir / "audit_series_titles.json"
    report_csv = reports_dir / "audit_series_titles.csv"

    payload: dict[str, Any] = {
        "ok": True,
        "elapsed_sec": round(elapsed, 4),
        "stories_input_dir": str(input_dir),
        "gemini_queue_dir": str(queue_dir),
        "batch_manifest": str(manifest_path),
        "manifest_loaded": bool(orig_map),
        "manifest_keys": len(orig_map),
        "scopes": {
            "stories_input": {
                "items_count": input_block["items_count"],
                "totals": input_block["totals"],
                "serial_groups_multi": input_block["serial_groups_multi"],
                "probable_serial_groups": input_block["probable_serial_groups"],
                "return_safe_count": len(input_block["return_safe"]),
                "needs_review_count": len(input_block["needs_review"]),
                "examples": {
                    "serial": _examples_for_scope("stories_input", "serial"),
                    "probable_serial": _examples_for_scope("stories_input", "probable_serial"),
                    "uncertain": _examples_for_scope("stories_input", "uncertain"),
                },
                "return_safe": input_block["return_safe"][:500],
                "needs_review_sample": input_block["needs_review"][:500],
            },
            "gemini_queue": {
                "items_count": queue_block["items_count"],
                "totals": queue_block["totals"],
                "serial_groups_multi": queue_block["serial_groups_multi"],
                "probable_serial_groups": queue_block["probable_serial_groups"],
                "return_safe_count": len(queue_block["return_safe"]),
                "needs_review_count": len(queue_block["needs_review"]),
                "examples": {
                    "serial": _examples_for_scope("gemini_queue", "serial"),
                    "probable_serial": _examples_for_scope("gemini_queue", "probable_serial"),
                    "uncertain": _examples_for_scope("gemini_queue", "uncertain"),
                },
                "return_safe": queue_block["return_safe"][:500],
                "needs_review_sample": queue_block["needs_review"][:500],
            },
        },
        "rows": rows_all,
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "scope",
        "stem",
        "source",
        "bucket",
        "group_size",
        "normalized_base_title",
        "matched_pattern",
        "manifest_hit",
        "return_safe",
        "needs_review",
        "review_priority",
        "path",
    ]
    with report_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows_all:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    payload["report_json"] = str(report_json)
    payload["report_csv"] = str(report_csv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    print("=== audit-series-titles (read-only) ===")
    print(f"batch_manifest: {manifest_path} (keys: {len(orig_map)})")
    print("")
    for label, block, path in (
        ("1) stories/input", input_block, input_dir),
        ("2) gemini_queue (uncategorized folders)", queue_block, queue_dir),
    ):
        t = block["totals"]
        print(f"{label}  path={path}")
        print(f"  items: {block['items_count']}")
        print(f"  serial: {t['serial']}  probable_serial: {t['probable_serial']}  uncertain: {t['uncertain']}")
        print(f"  serial multi-member groups: {len(block['serial_groups_multi'])}")
        print(f"  probable_serial groups: {len(block['probable_serial_groups'])}")
        print(f"  return_safe (serial + manifest): {len(block['return_safe'])}")
        print(f"  needs_review (все не return_safe): {len(block['needs_review'])}")
        hi = sum(1 for r in block["rows"] if r.get("review_priority") == "high")
        lo = sum(1 for r in block["rows"] if r.get("review_priority") == "low")
        print(f"  review_priority high: {hi}  low: {lo}")
        sc = block["scope"]
        exb = payload["scopes"][sc]["examples"]
        print("  examples [serial]:")
        for ex in exb["serial"][:max_examples]:
            print(f"    - {ex['stem']}  (group_size={ex['group_size']})")
        print("  examples [probable_serial]:")
        for ex in exb["probable_serial"][:max_examples]:
            print(f"    - {ex['stem']}  (group_size={ex['group_size']})")
        print("  examples [uncertain]:")
        for ex in exb["uncertain"][:max_examples]:
            print(f"    - {ex['stem']}  (group_size={ex['group_size']})")
        print("  return_safe (точно к исходнику по манифесту, как move_serial_to_original):")
        for r in block["return_safe"][:10]:
            print(f"    - {r['stem']} -> {orig_map.get(r['manifest_key'], '')}")
        if len(block["return_safe"]) > 10:
            print(f"    ... +{len(block['return_safe']) - 10} (json)")
        print("  needs_review (проверить отдельно), первые строки:")
        for r in block["needs_review"][:8]:
            print(
                f"    - {r['stem']}  bucket={r['bucket']} manifest_hit={r['manifest_hit']}"
            )
        if len(block["needs_review"]) > 8:
            print(f"    ... +{len(block['needs_review']) - 8} (json/csv)")
        print("")

    print("series groups (serial, multi-member) — stories_input:")
    for g in input_block["serial_groups_multi"][:6]:
        print(f"  norm={g['normalized_base_title']!r} size={g['group_size']} pattern={g['matched_pattern']}")
        for mm in g["members"][:5]:
            print(f"    - {mm['stem']}")
    if len(input_block["serial_groups_multi"]) > 6:
        print(f"  ... +{len(input_block['serial_groups_multi']) - 6} groups in json")
    print("")
    print("series groups (serial, multi-member) — gemini_queue:")
    for g in queue_block["serial_groups_multi"][:6]:
        print(f"  norm={g['normalized_base_title']!r} size={g['group_size']} pattern={g['matched_pattern']}")
        for mm in g["members"][:5]:
            print(f"    - {mm['stem']}")
    if len(queue_block["serial_groups_multi"]) > 6:
        print(f"  ... +{len(queue_block['serial_groups_multi']) - 6} groups in json")
    print("")
    print("probable_serial groups (фрагмент) — stories_input:")
    for g in input_block["probable_serial_groups"][:4]:
        print(f"  norm={g['normalized_base_title']!r} size={g['group_size']}")
        for mm in g["members"][:4]:
            print(f"    - {mm['stem']}")
    print("")
    print("probable_serial groups (фрагмент) — gemini_queue:")
    for g in queue_block["probable_serial_groups"][:4]:
        print(f"  norm={g['normalized_base_title']!r} size={g['group_size']}")
        for mm in g["members"][:4]:
            print(f"    - {mm['stem']}")
    print("")
    print(f"report_json={report_json}")
    print(f"report_csv={report_csv}")

    return payload

