"""
Полный title-based аудит серийников по всем источникам, откуда материал может попасть в stories/input.

read-only + dry-run план (без перемещений). Отчёты:
  .orchestrator/reports/series_title_audit_all_sources.json
  .orchestrator/reports/series_title_audit_all_sources.csv

Детектор: normalize_story_base_title + группировка; маркеры — legacy (Ch/Ep/Part/…) плюс расширение Day/Page/Installment/S01E02 и т.д.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orchestrator.config import OrchestratorConfig
from orchestrator.stories_input_series_return import (
    _QUEUE_TAIL_RE,
    load_original_source_paths,
    normalize_story_base_title,
    stem_has_explicit_serial_marker,
)

Decision = Literal["standalone_ok", "serial", "probable_serial", "uncertain"]
Category = Literal["standalone_ok", "serial_confirmed", "probable_serial", "uncertain"]
SourceBucket = Literal["stories_input", "library_manifest", "stories_other", "gemini_queue", "archive", "other"]


# Расширение к stem_has_explicit_serial_marker (после среза queue tail), не заменяя legacy-логику.
_EXTRA_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("day", re.compile(r"(?i)(?<![\w])(?:day|days)\s*(?:#|№)?\s*\d{1,4}(?![\w0-9])")),
    ("page", re.compile(r"(?i)(?<![\w])(?:page|pg\.?)\s*\d{1,4}(?![\w0-9])")),
    ("installment", re.compile(r"(?i)(?<![\w])(?:installment|inst\.?)\s*\d{1,4}(?![\w0-9])")),
    ("segment", re.compile(r"(?i)(?<![\w])(?:segment|seg\.?)\s*\d{1,4}(?![\w0-9])")),
    ("scene", re.compile(r"(?i)(?<![\w])(?:scene|scn\.?)\s*\d{1,4}(?![\w0-9])")),
    ("act", re.compile(r"(?i)(?<![\w])(?:act)\s*\d{1,3}(?![\w0-9])")),
    ("season_episode", re.compile(r"(?i)(?<![\w])s\d{1,2}\s*e\d{1,3}(?![\w0-9])")),
    ("season_word", re.compile(r"(?i)(?<![\w])season\s*(?:#|№)?\s*\d{1,3}(?![\w0-9])")),
    ("n_of_m", re.compile(r"(?i)(?<![\w])(?:\(\s*)?\d{1,3}\s+of\s+\d{1,3}\s*\)?(?![\w0-9])")),
    ("slash_part", re.compile(r"(?<![\w/])(?<!\d)(\d{1,3})/(\d{1,3})(?![/\w0-9])")),
    ("continuation", re.compile(r"(?i)(?<![\w])(?:continued|continuation|sequel to|sequel)(?![\w])")),
    ("roman_part", re.compile(r"(?i)(?<![\w])(?:ii|iii|iv|v|vi|vii|viii|ix|x)\b(?![\w])")),
)


def _wo_queue_tail(stem: str) -> str:
    return _QUEUE_TAIL_RE.sub("", stem.strip()).strip()


def extended_series_markers(stem: str) -> tuple[bool, list[str]]:
    """True если есть legacy explicit ИЛИ любой из расширенных маркеров."""
    legacy_ok, legacy_tag = stem_has_explicit_serial_marker(stem)
    tags: list[str] = []
    if legacy_ok and legacy_tag:
        tags.append(f"legacy:{legacy_tag}")
    wo = _wo_queue_tail(stem)
    for label, rx in _EXTRA_MARKERS:
        if rx.search(wo):
            tags.append(label)
    return (legacy_ok or bool(tags)), tags


def _first_int_in_match(rx: re.Pattern[str], stem: str) -> tuple[str, int | None]:
    wo = _wo_queue_tail(stem)
    m = rx.search(wo)
    if not m:
        return "", None
    nums = re.findall(r"\d+", m.group(0))
    if not nums:
        return m.group(0).strip(), None
    return m.group(0).strip(), int(nums[0])


def extract_part_marker_number(stem: str) -> tuple[str, int | None]:
    """Грубое извлечение для отчёта (первая подходящая метка)."""
    wo = _wo_queue_tail(stem)
    checks: list[tuple[str, re.Pattern[str]]] = [
        ("chapter", re.compile(r"(?i)(?:ch\.?|chapter|chap\.?)\s*\d{1,4}")),
        ("episode", re.compile(r"(?i)(?:ep\.?|episode)\s*\d{1,4}")),
        ("part", re.compile(r"(?i)(?:part|pt\.?)\s*\d{1,4}")),
        ("book", re.compile(r"(?i)(?:book|bk\.?)\s*\d{1,4}")),
        ("volume", re.compile(r"(?i)(?:vol\.?|volume)\s*\d{1,4}")),
        ("hash_number", re.compile(r"(?i)\#\s*\d{1,4}")),
    ]
    for name, rx in checks:
        label, num = _first_int_in_match(rx, stem)
        if label:
            return f"{name}:{label}", num
    for label, rx in _EXTRA_MARKERS:
        m = rx.search(wo)
        if m:
            nums = re.findall(r"\d+", m.group(0))
            return f"{label}:{m.group(0).strip()}", int(nums[0]) if nums else None
    return "", None


def _confidence_for(decision: Decision, group_size: int, has_marker: bool) -> str:
    if decision == "serial":
        return "high" if (group_size > 1 and has_marker) or (group_size == 1 and has_marker) else "medium"
    if decision == "probable_serial":
        return "medium"
    if decision == "uncertain":
        return "low"
    return "high"


def _decision_to_category(decision: Decision) -> Category:
    if decision == "serial":
        return "serial_confirmed"
    if decision == "probable_serial":
        return "probable_serial"
    if decision == "uncertain":
        return "uncertain"
    return "standalone_ok"


def _classify_group(group_size: int, group_has_marker: bool, singleton_explicit: bool) -> Decision:
    if group_size > 1 and group_has_marker:
        return "serial"
    if group_size > 1:
        return "probable_serial"
    if group_size == 1 and singleton_explicit:
        return "serial"
    if group_size == 1:
        return "uncertain"
    return "standalone_ok"


def _finalize_singleton(decision: Decision, stem: str, strip_tags: list[str]) -> Decision:
    """Одиночный файл: uncertain только при слабом сигнале (очередь/диапазон без маркеров)."""
    if decision != "uncertain":
        return decision
    has_strip = bool(strip_tags)
    ext_ok, _tags = extended_series_markers(stem)
    if ext_ok:
        return "serial"
    if has_strip and not ext_ok:
        return "uncertain"
    return "standalone_ok"


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass
class _FileRec:
    path: Path
    stem: str
    source_bucket: SourceBucket
    root_label: str


def _normp(p: Path) -> str:
    return str(p).replace("\\", "/").lower()


def _bucket_for_path_fast(
    path: Path,
    *,
    input_s: str,
    gemini_s: str,
    archive_s: str,
    manifest_s: str,
    stories_short_s: str,
    stories_saved_s: str,
    stories_usaved_s: str,
    stories_lib_s: str,
    manifest_label: str,
) -> tuple[SourceBucket, str]:
    """Без resolve() на каждый файл (важно при 100k+ путях)."""
    ps = _normp(path)
    if ps.startswith(input_s) or ps.rstrip("/") == input_s.rstrip("/"):
        return "stories_input", "stories/input"
    if gemini_s and (ps.startswith(gemini_s) or ps.rstrip("/") == gemini_s.rstrip("/")):
        return "gemini_queue", "gemini_input"
    if archive_s and ps.startswith(archive_s):
        return "archive", "archive"
    if manifest_s and ps.startswith(manifest_s):
        return "library_manifest", manifest_label
    for pref, label in (
        (stories_short_s, "stories/short_under_15m"),
        (stories_saved_s, "stories/saved"),
        (stories_usaved_s, "stories/_saved"),
        (stories_lib_s, "stories/library"),
    ):
        if pref and ps.startswith(pref):
            return "stories_other", label
    if "/stories/" in ps:
        return "stories_other", "stories/*"
    return "other", "other"


def _library_category_dirs_from_batch_manifest(batch_manifest: Path, lib_root: Path) -> list[Path]:
    """Имена верхних папок-категорий из манифеста sample-library (без listdir всего output)."""
    if not batch_manifest.is_file():
        return []
    try:
        data = json.loads(batch_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    sel = data.get("selected_by_source_folder")
    if not isinstance(sel, dict):
        return []
    out: list[Path] = []
    for name in sorted(sel.keys(), key=lambda x: str(x).lower()):
        if not isinstance(name, str) or not name.strip():
            continue
        # Не вызываем is_dir() на каждую категорию (сетевой/медленный диск) — доверяем манифесту sample-library.
        out.append(lib_root / name.strip())
    return out


def _latest_library_manifest(manifests_dir: Path) -> dict[str, Any] | None:
    if not manifests_dir.is_dir():
        return None
    cands = sorted(manifests_dir.glob("library_sample_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands[:5]:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _discover_dirs(
    root: Path,
    *,
    manifests_dir: Path,
    input_dir: Path,
    archive_dir: Path,
) -> tuple[list[dict[str, Any]], Path | None]:
    """Кандидаты: фиксированные под stories/, archive, source_dir из манифестов."""
    rows: list[dict[str, Any]] = []
    manifest_source: Path | None = None

    def probe(path: Path, *, sampler_hint: str) -> None:
        if not path.exists():
            return
        n = 0
        samples: list[str] = []
        if path.is_file():
            return
        try:
            for i, p in enumerate(path.iterdir()):
                if i >= 60:
                    break
                if p.is_file() and p.suffix.lower() == ".txt":
                    n += 1
                    if len(samples) < 5:
                        samples.append(p.name)
                elif p.is_dir() and i < 25:
                    try:
                        for j, q in enumerate(p.iterdir()):
                            if j >= 15:
                                break
                            if q.is_file() and q.suffix.lower() == ".txt":
                                n += 1
                                if len(samples) < 5:
                                    samples.append(f"{p.name}/{q.name}")
                    except OSError:
                        pass
        except OSError:
            pass
        rows.append(
            {
                "path": str(path.resolve()),
                "txt_count_shallow_sample_max200": n,
                "sample_files": samples,
                "sampler_linked": sampler_hint,
            }
        )

    def register_dir_only(path: Path, *, sampler_hint: str) -> None:
        rows.append(
            {
                "path": str(path),
                "txt_count_shallow_sample_max200": -1,
                "sample_files": [],
                "sampler_linked": sampler_hint,
            }
        )

    stories = root / "stories"
    for rel, hint in (
        ("input", "target of sample-library"),
        ("short_under_15m", "repo stories"),
        ("saved", "candidate"),
        ("_saved", "candidate"),
        ("library", "candidate"),
        ("_series_return_unknown", "return-series unknown"),
    ):
        probe(stories / rel, sampler_hint="no" if rel != "input" else "target queue")

    if archive_dir.is_dir():
        register_dir_only(archive_dir, sampler_hint="archive (not probed)")

    bm = input_dir / "_batch_manifest.json"
    if bm.is_file():
        try:
            data = json.loads(bm.read_text(encoding="utf-8"))
            sd = data.get("source_dir")
            if isinstance(sd, str) and sd.strip():
                manifest_source = Path(sd.strip())
                if manifest_source.is_dir():
                    register_dir_only(manifest_source, sampler_hint="yes: batch_manifest.source_dir (not probed)")
        except (OSError, json.JSONDecodeError):
            pass

    lib = _latest_library_manifest(manifests_dir)
    if lib:
        sd = lib.get("source_dir")
        if isinstance(sd, str) and sd.strip():
            p = Path(sd.strip())
            if p.is_dir() and (manifest_source is None or str(p).strip() != str(manifest_source or "").strip()):
                register_dir_only(p, sampler_hint="yes: latest library_sample source_dir (not probed)")

    return rows, manifest_source


def _collect_txt_paths(
    roots: list[Path],
    *,
    cap: int,
    stories_input_dir: Path,
    manifest_library_root: Path | None,
    batch_manifest: Path,
) -> list[Path]:
    """
    Сбор .txt: для stories/input — только корень (как phase_a).
    Для корня библиотеки из манифеста — как sample-library: source_dir/<category>/*.txt (без глубокого rglob).
    Для прочих путей — rglob с лимитом per-root.
    """
    seen: set[str] = set()
    out: list[Path] = []
    inp = stories_input_dir.resolve()
    lib_root = manifest_library_root.resolve() if manifest_library_root and manifest_library_root.is_dir() else None

    def add_file(p: Path, *, ignore_cap: bool = False) -> bool:
        if p.suffix.lower() != ".txt":
            return False
        if p.name.startswith("_") and "input" in str(p).replace("\\", "/"):
            return False
        key = str(p).casefold()
        if key in seen:
            return False
        seen.add(key)
        out.append(p)
        if ignore_cap:
            return False
        return len(out) >= cap

    per_other_cap = min(2_000, max(400, cap // max(1, len(roots) * 4)))

    for root in roots:
        if not root.is_dir():
            continue
        rr = root.resolve()
        if rr == inp:
            for p in sorted(root.glob("*.txt"), key=lambda x: x.name.lower()):
                if not p.is_file():
                    continue
                add_file(p, ignore_cap=True)
            continue
        if lib_root is not None:
            try:
                if rr == lib_root:
                    cats = _library_category_dirs_from_batch_manifest(batch_manifest, lib_root)
                    if not cats:
                        cats = sorted(
                            [x for x in root.iterdir() if x.is_dir() and x.name != "_series"],
                            key=lambda x: x.name.lower(),
                        )[:40]
                    for cat in cats:
                        try:
                            cat_iter = cat.iterdir()
                        except OSError:
                            continue
                        n_cat = 0
                        for p in cat_iter:
                            if p.suffix.lower() != ".txt":
                                continue
                            if add_file(p):
                                return out
                            n_cat += 1
                            if n_cat >= 800:
                                break
                    continue
                if _is_under(rr, lib_root) and rr != lib_root:
                    n_here = 0
                    for p in root.iterdir():
                        if p.suffix.lower() != ".txt":
                            continue
                        if add_file(p):
                            return out
                        n_here += 1
                        if n_here >= 800:
                            break
                    continue
            except OSError:
                pass
        n = 0
        for p in root.rglob("*.txt"):
            if not p.is_file():
                continue
            if add_file(p):
                return out
            n += 1
            if n >= per_other_cap:
                break
    return out


def _gemini_folder_paths(queue_dir: Path | None, *, cap: int) -> list[Path]:
    if queue_dir is None or not queue_dir.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(queue_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir():
            out.append(p)
            if len(out) >= cap:
                break
    return out


def _recommended_action_and_plan(
    *,
    row: dict[str, Any],
    orig_map: dict[str, Path],
    root_dir: Path,
) -> tuple[str, str, str, str]:
    """recommended_action, planned_target, action (dry-run), notes."""
    path = Path(str(row["path"]))
    decision = str(row["decision"])
    bucket = str(row["source_bucket"])
    stem = str(row["original_stem"])
    manifest_key = Path(stem + ".txt").name.casefold()
    if bucket == "gemini_queue":
        manifest_key = _QUEUE_TAIL_RE.sub("", stem).strip()
        if not manifest_key.lower().endswith(".txt"):
            manifest_key = f"{manifest_key}.txt"
        manifest_key = manifest_key.casefold()

    unknown_dir = (root_dir / "stories" / "_series_return_unknown").resolve()

    if decision == "standalone_ok":
        return "keep", str(path), "none", ""
    if decision == "uncertain":
        return "manual_review_only", str(path), "none", "ambiguous singleton; do not auto-move"

    orig = orig_map.get(manifest_key)
    if bucket == "stories_input" and orig is not None and decision == "serial":
        return "return_to_manifest_original", str(orig), "move_return", str(orig)

    if bucket == "stories_input" and orig is None and decision == "serial":
        dest = unknown_dir / (stem + ".txt")
        return "move_to_series_return_unknown", str(dest), "move_unknown", "no manifest key"

    if bucket == "stories_input" and decision == "probable_serial":
        return "manual_review_input_probable", str(path), "none", "probable serial in queue; do not auto-return"

    if bucket in ("library_manifest", "stories_other") and decision in ("serial", "probable_serial"):
        parent = path.parent
        series_dir = parent / "_series_removed"
        dest = series_dir / path.name
        return "move_to_library__series_removed", str(dest), "move_library_series_removed", "same category folder"

    if bucket == "gemini_queue":
        return "pipeline_queue_review", str(path), "none", "queue folder; adjust upstream library"

    if bucket == "archive":
        return "archive_review", str(path), "none", "archived copy"

    return "review_other_bucket", str(path), "none", bucket


def run_series_title_audit_all_sources(
    *,
    config: OrchestratorConfig,
    stories_input_dir: Path,
    gemini_queue_dir: Path | None,
    max_txt_files: int,
) -> dict[str, Any]:
    root = config.root_dir.resolve()
    reports_dir = config.reports_dir.resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir = (config.service_dir / "manifests").resolve()
    input_dir = (root / stories_input_dir).resolve() if not stories_input_dir.is_absolute() else stories_input_dir.resolve()
    archive_dir = (root / Path(config.data_dirs.get("archive", "archive"))).resolve()
    gemini_dir = None
    if gemini_queue_dir:
        cand = (root / gemini_queue_dir).resolve() if not gemini_queue_dir.is_absolute() else gemini_queue_dir.resolve()
        gemini_dir = cand if cand.is_dir() else None

    t0 = time.perf_counter()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    print("audit-series-all-sources: discovering roots…", flush=True)
    discovered, manifest_source_root = _discover_dirs(root, manifests_dir=manifests_dir, input_dir=input_dir, archive_dir=archive_dir)

    scan_roots_set: set[str] = set()
    scan_roots: list[Path] = []
    for d in discovered:
        p = Path(str(d["path"]))
        if p.is_dir():
            k = str(p.resolve())
            if k not in scan_roots_set:
                scan_roots_set.add(k)
                scan_roots.append(p)

    if manifest_source_root and manifest_source_root.is_dir():
        lr = manifest_source_root.resolve()
        slim: list[Path] = []
        for p in scan_roots:
            pr = p.resolve()
            if pr == lr:
                slim.append(p)
                continue
            if _is_under(pr, lr):
                continue
            slim.append(p)
        scan_roots = slim
        if not any(p.resolve() == lr for p in scan_roots):
            scan_roots.insert(0, manifest_source_root)

    inp_res = input_dir.resolve()
    pref = manifest_source_root.resolve() if manifest_source_root else None

    def _scan_order_key(p: Path) -> tuple[int, str]:
        try:
            if p.resolve() == inp_res:
                return (0, str(p).lower())
        except OSError:
            pass
        if pref:
            try:
                if p.resolve() == pref:
                    return (1, str(p).lower())
            except OSError:
                pass
        return (2, str(p).lower())

    scan_roots.sort(key=_scan_order_key)

    print(f"audit-series-all-sources: {len(scan_roots)} scan roots, collecting .txt…", flush=True)
    txt_paths = _collect_txt_paths(
        scan_roots,
        cap=max_txt_files,
        stories_input_dir=input_dir,
        manifest_library_root=manifest_source_root,
        batch_manifest=input_dir / "_batch_manifest.json",
    )
    print(f"audit-series-all-sources: collected {len(txt_paths)} txt paths, grouping…", flush=True)

    orig_map = load_original_source_paths(input_dir / "_batch_manifest.json")

    stories_parent = input_dir.parent

    def _pfx(sub: str) -> str:
        d = stories_parent / sub
        return (_normp(d).rstrip("/") + "/") if d.is_dir() else ""

    input_s = _normp(input_dir).rstrip("/") + "/"
    gemini_s = (_normp(gemini_dir).rstrip("/") + "/") if gemini_dir else ""
    archive_s = (_normp(archive_dir).rstrip("/") + "/") if archive_dir.is_dir() else ""
    manifest_s = (_normp(manifest_source_root).rstrip("/") + "/") if manifest_source_root and manifest_source_root.is_dir() else ""
    manifest_label = str(manifest_source_root) if manifest_source_root else ""

    bucket_kw: dict[str, str] = {
        "input_s": input_s,
        "gemini_s": gemini_s,
        "archive_s": archive_s,
        "manifest_s": manifest_s,
        "stories_short_s": _pfx("short_under_15m"),
        "stories_saved_s": _pfx("saved"),
        "stories_usaved_s": _pfx("_saved"),
        "stories_lib_s": _pfx("library"),
        "manifest_label": manifest_label,
    }

    file_recs: list[_FileRec] = []
    for p in txt_paths:
        b, lab = _bucket_for_path_fast(p, **bucket_kw)
        file_recs.append(_FileRec(path=p, stem=p.stem, source_bucket=b, root_label=lab))

    gem_folders = _gemini_folder_paths(gemini_dir, cap=50000)
    for p in gem_folders:
        b, lab = _bucket_for_path_fast(p, **bucket_kw)
        file_recs.append(_FileRec(path=p, stem=p.name, source_bucket=b, root_label=lab))

    def _scope_key(fr: _FileRec) -> str:
        if fr.source_bucket == "stories_input":
            return "input"
        if fr.source_bucket == "gemini_queue":
            return "queue"
        return "library"

    def _build_rows_for_subset(subset: list[_FileRec]) -> list[dict[str, Any]]:
        part_cache: dict[str, tuple[str, str]] = {}

        def part_for(stem: str) -> tuple[str, str]:
            if stem not in part_cache:
                m, n = extract_part_marker_number(stem)
                part_cache[stem] = (m, "" if n is None else str(n))
            return part_cache[stem]

        groups: dict[str, list[_FileRec]] = {}
        meta_strips: dict[str, list[str]] = {}
        for fr in subset:
            norm, strip_tags = normalize_story_base_title(fr.stem)
            groups.setdefault(norm, []).append(fr)
            meta_strips[str(fr.path)] = strip_tags
        rows: list[dict[str, Any]] = []
        for norm, members in groups.items():
            stems = [m.stem for m in members]
            group_size = len(members)
            group_has_marker = any(extended_series_markers(s)[0] for s in stems)
            singleton_explicit = extended_series_markers(members[0].stem)[0] if group_size == 1 else False
            decision = _classify_group(group_size, group_has_marker, singleton_explicit)
            if group_size == 1:
                decision = _finalize_singleton(decision, members[0].stem, meta_strips[str(members[0].path)])

            serial_reasons: list[str] = []
            for s in stems:
                ok, tags = extended_series_markers(s)
                if ok:
                    serial_reasons.extend(tags)
            serial_reasons = list(dict.fromkeys(serial_reasons))[:24]

            for fr in members:
                if decision == "standalone_ok":
                    part_marker, part_num_s = "", ""
                else:
                    part_marker, part_num_s = part_for(fr.stem)
                conf = _confidence_for(decision, group_size, group_has_marker)
                cat = _decision_to_category(decision)
                p_res = str(fr.path)
                row = {
                    "path": p_res,
                    "original_path": p_res,
                    "current_location": p_res,
                    "source_bucket": fr.source_bucket,
                    "source_root_label": fr.root_label,
                    "original_stem": fr.stem,
                    "base_title": norm,
                    "part_marker": part_marker,
                    "part_number": part_num_s,
                    "group_key": norm,
                    "group_size": group_size,
                    "decision": decision,
                    "category": cat,
                    "confidence": conf,
                    "serial_reasons": "|".join(serial_reasons) if serial_reasons else "",
                    "recommended_action": "",
                    "planned_target": "",
                    "dry_run_action": "",
                    "plan_notes": "",
                }
                if decision == "standalone_ok":
                    row["recommended_action"] = "keep"
                    row["planned_target"] = p_res
                    row["dry_run_action"] = "none"
                    row["plan_notes"] = ""
                else:
                    rec, plan_tgt, act, notes = _recommended_action_and_plan(row=row, orig_map=orig_map, root_dir=root)
                    row["recommended_action"] = rec
                    row["planned_target"] = plan_tgt
                    row["dry_run_action"] = act
                    row["plan_notes"] = notes
                rows.append(row)
        return rows

    by_scope: dict[str, list[_FileRec]] = {"input": [], "library": [], "queue": []}
    for fr in file_recs:
        by_scope[_scope_key(fr)].append(fr)

    rows_out: list[dict[str, Any]] = []
    rows_out.extend(_build_rows_for_subset(by_scope["input"]))
    rows_out.extend(_build_rows_for_subset(by_scope["library"]))
    rows_out.extend(_build_rows_for_subset(by_scope["queue"]))

    def _summarize(pred: Any) -> dict[str, int]:
        def c(d: str) -> int:
            return sum(1 for r in rows_out if pred(r) and r["decision"] == d)

        return {
            "txt_total": sum(1 for r in rows_out if pred(r)),
            "serial": c("serial"),
            "probable_serial": c("probable_serial"),
            "uncertain": c("uncertain"),
            "standalone_ok": c("standalone_ok"),
        }

    pred_input = lambda r: r["source_bucket"] == "stories_input"
    pred_lib = lambda r: r["source_bucket"] in ("library_manifest", "stories_other", "archive", "other")
    pred_gem = lambda r: r["source_bucket"] == "gemini_queue"

    summary_a = _summarize(pred_input)
    summary_b = _summarize(pred_lib)
    summary_c = {
        "folders_total": sum(1 for r in rows_out if pred_gem(r)),
        "serial": sum(1 for r in rows_out if pred_gem(r) and r["decision"] == "serial"),
        "probable_serial": sum(1 for r in rows_out if pred_gem(r) and r["decision"] == "probable_serial"),
        "uncertain": sum(1 for r in rows_out if pred_gem(r) and r["decision"] == "uncertain"),
        "standalone_ok": sum(1 for r in rows_out if pred_gem(r) and r["decision"] == "standalone_ok"),
    }

    plan_rows = [r for r in rows_out if r["dry_run_action"] not in ("none", "")]

    dry_counts = {
        "move_library_series_removed": sum(1 for r in plan_rows if r["dry_run_action"] == "move_library_series_removed"),
        "move_return": sum(1 for r in plan_rows if r["dry_run_action"] == "move_return"),
        "move_unknown": sum(1 for r in plan_rows if r["dry_run_action"] == "move_unknown"),
    }
    lib_serial_move = sum(
        1
        for r in rows_out
        if pred_lib(r) and r["decision"] == "serial" and r["dry_run_action"] == "move_library_series_removed"
    )
    lib_prob_move = sum(
        1
        for r in rows_out
        if pred_lib(r) and r["decision"] == "probable_serial" and r["dry_run_action"] == "move_library_series_removed"
    )

    serial_lib = [r for r in rows_out if pred_lib(r) and r["decision"] == "serial"][:100]
    probable_lib = [r for r in rows_out if pred_lib(r) and r["decision"] == "probable_serial"][:100]

    groups_serial = {}
    for r in rows_out:
        if r["decision"] != "serial":
            continue
        groups_serial.setdefault(r["group_key"], []).append(r["path"])

    analysis = {
        "why_series_reappear": [
            "sample-library (orchestrator.library_sampler) читает только --source-dir: подпапки верхнего уровня (кроме _series), по N .txt из каждой; совпадение basename с очередью пропускается, но другой файл серии с другим именем снова попадёт в stories/input.",
            "return-series-from-input (stories_input_series_return) обрабатывает только каталог stories/input (по умолчанию), не библиотеку на диске D:…/output; серийники остаются в source pool.",
            "Расширенный детектор ловит Day/Page/S01E01/… в дополнение к Ch/Ep/Part; старый узкий сценарий без этих маркеров давал standalone.",
            "Если source_path_original в манифесте отсутствует или ключ basename не совпадает — перенос в исходник невозможен (см. plan move_unknown / manual_review).",
        ],
        "sampler_code_note": "library_sampler.py: top_folders = source_dir/*/ (skip _series), .txt только прямым детям категории.",
    }

    payload: dict[str, Any] = {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root_dir": str(root),
        "stories_input_dir": str(input_dir),
        "gemini_queue_dir": str(gemini_dir) if gemini_dir else "",
        "manifest_source_dir": str(manifest_source_root) if manifest_source_root else "",
        "discovered_scan_roots": discovered,
        "manifest_keys_loaded": len(orig_map),
        "max_txt_files_cap": max_txt_files,
        "library_scan_note": "Категории из _batch_manifest selected_by_source_folder; до 800 .txt на категорию (ускорение; полный pool шире). Полные строки — только CSV.",
        "txt_files_scanned": len(txt_paths),
        "rows_total": len(rows_out),
        "summary_stories_input": summary_a,
        "summary_saved_library": summary_b,
        "summary_gemini_queue": summary_c,
        "dry_run_plan_counts": dry_counts,
        "library_pool_cleanup_preview": {
            "serial_would_move_to__series_removed": lib_serial_move,
            "probable_serial_would_move_to__series_removed": lib_prob_move,
            "uncertain_left_for_manual": sum(1 for r in rows_out if pred_lib(r) and r["decision"] == "uncertain"),
            "standalone_ok_left_in_library_scan": sum(1 for r in rows_out if pred_lib(r) and r["decision"] == "standalone_ok"),
        },
        "examples_serial_library_first_100": serial_lib,
        "examples_probable_serial_library_first_100": probable_lib,
        "serial_groups_by_base_title_sample": dict(list(groups_serial.items())[:500]),
        "analysis": analysis,
        "full_rows_in_csv_only": True,
        "rows_sample": rows_out[:4000],
        "dry_run_plan": [
            {
                "original_path": r["original_path"],
                "current_location": r["current_location"],
                "planned_target": r["planned_target"],
                "action": r["dry_run_action"],
                "recommended_action": r["recommended_action"],
                "decision": r["decision"],
                "category": r["category"],
                "source_bucket": r["source_bucket"],
                "plan_notes": r["plan_notes"],
            }
            for r in rows_out
            if r["dry_run_action"] not in ("none", "")
        ][:8000],
        "report_answers": {
            "A_source_folders": "см. discovered_scan_roots в JSON (path, txt_count_shallow_sample_max200, sample_files, sampler_linked).",
            "B_where_serials": "см. rows / CSV: filter decision in serial|probable_serial по source_bucket.",
            "C_why_reappear": analysis["why_series_reappear"],
            "D_count_input": summary_a,
            "E_count_library": summary_b,
            "F_count_gemini": summary_c,
            "G_dry_run_plan": "dry_run_plan + dry_run_plan_counts; без execute.",
            "H_safe_execute_later": "только вручную после подтверждения: move_library_series_removed, move_return, move_unknown по списку dry_run_plan.",
        },
    }

    out_json = reports_dir / "series_title_audit_all_sources.json"
    out_csv = reports_dir / "series_title_audit_all_sources.csv"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = list(rows_out[0].keys()) if rows_out else []
    if rows_out:
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows_out)

    elapsed = round(time.perf_counter() - t0, 3)
    payload["elapsed_sec"] = elapsed

    print("=== audit-series-all-sources (read-only + dry-run plan) ===")
    print(f"elapsed_sec={elapsed}")
    print(f"report_json={out_json}")
    print(f"report_csv={out_csv}")
    print("")
    print("A) stories/input:", summary_a)
    print("B) saved/library (manifest+repo+stories/*):", summary_b)
    print("C) gemini_queue:", summary_c)
    print("")
    print("Discovered roots (shallow txt sample ≤200):")
    for d in discovered[:25]:
        print(f"  {d['path']}  txt≈{d['txt_count_shallow_sample_max200']}  sampler={d['sampler_linked']}")
    if len(discovered) > 25:
        print(f"  ... +{len(discovered) - 25} (json)")
    print("")
    print("Dry-run plan counts (no files touched):")
    print(json.dumps(dry_counts, ensure_ascii=False, indent=2))
    print("library_pool_cleanup_preview (dry-run, library buckets only):")
    print(json.dumps(payload.get("library_pool_cleanup_preview", {}), ensure_ascii=False, indent=2))
    print("")
    print("Execute later (only after your confirmation): moves from dry_run_plan JSON; no auto-execute in this command.")
    payload["report_json"] = str(out_json)
    payload["report_csv"] = str(out_csv)
    return payload
