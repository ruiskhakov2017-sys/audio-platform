from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

STORY_QUEUE_DIR_RE = re.compile(r".+_\d{6}$")

INFO_SKIP_NAMES = {"info.txt", "result_report.txt"}


def _is_nonempty_info_txt(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def parse_gemini_folder_story_prefix(folder_name: str) -> tuple[str, int | None]:
    """
    Восстановить префикс рассказа из имени папки orchestrator: ``<story>_<NNNNNN>``.
    Если хвост не похож на наш суффикс из 6 цифр — вернуть (folder_name, None).
    """
    m = re.fullmatch(r"(.+)_(\d{6})", folder_name)
    if not m:
        return folder_name, None
    return m.group(1), int(m.group(2))


def intake_source_key(path: Path, stories_dir: Path) -> str:
    """Стабильный ключ исходника относительно каталога intake (корень .txt)."""
    try:
        rel = path.resolve().relative_to(stories_dir.resolve())
    except ValueError:
        return path.resolve().as_posix().lower()
    return rel.as_posix().lower()


def resolve_phase_a_root(run_path: Path) -> Path:
    """
    Нормализовать путь: допускается корень run (родитель _phase_a) или сам _phase_a.
    """
    p = run_path.resolve()
    if (p / "gemini_input" / "stories").is_dir():
        return p
    cand = p / "_phase_a"
    if (cand / "gemini_input" / "stories").is_dir():
        return cand
    if p.name == "_phase_a" and (p / "gemini_input" / "stories").is_dir():
        return p
    return p


def gemini_selection_stories_dir(phase_a_root: Path) -> Path:
    primary = phase_a_root / "gemini_input" / "stories"
    if primary.is_dir():
        return primary
    legacy = phase_a_root / "_phase_a" / "gemini_input" / "stories"
    if legacy.is_dir():
        return legacy
    return primary


def length_filter_kept_count(phase_a_root: Path) -> int | None:
    for name in ("length_filter_manifest.json",):
        for base in (phase_a_root, phase_a_root.parent):
            mp = base / name
            if mp.is_file():
                try:
                    data = json.loads(mp.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("kept_count") is not None:
                        return int(data["kept_count"])
                except Exception:
                    pass
    return None


def _scan_txt_root(stories_dir: Path, extensions: list[str]) -> list[Path]:
    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    out: list[Path] = []
    if not stories_dir.is_dir():
        return out
    for p in sorted(stories_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ext_set:
            continue
        out.append(p)
    return out


def _primary_story_txt_in_gemini_dir(story_dir: Path) -> Path | None:
    txts: list[Path] = []
    for p in story_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() != ".txt":
            continue
        if p.name.lower() in INFO_SKIP_NAMES:
            continue
        txts.append(p)
    if not txts:
        return None
    txts.sort(key=lambda x: x.name.lower())
    return txts[0]


def iter_gemini_story_queue_dirs(gemini_stories_root: Path) -> list[tuple[str, Path]]:
    """(category_relative, story_dir) только для папок вида ``*_NNNNNN`` с очередным .txt."""
    root = gemini_stories_root.resolve()
    if not root.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for txt in root.rglob("*.txt"):
        if txt.name.lower() == "info.txt":
            continue
        if txt.name.lower() in INFO_SKIP_NAMES:
            continue
        parent = txt.parent
        if parent == root:
            continue
        if not STORY_QUEUE_DIR_RE.match(parent.name):
            continue
        try:
            rel_cat = parent.parent.relative_to(root)
        except ValueError:
            continue
        cat = rel_cat.as_posix() if str(rel_cat) != "." else "."
        out.append((cat, parent))
    # уникальные story_dir
    seen: set[Path] = set()
    uniq: list[tuple[str, Path]] = []
    for cat, sd in sorted(out, key=lambda t: str(t[1])):
        rp = sd.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append((cat, sd))
    return uniq


def _intake_by_basename_lower(stories_dir: Path, extensions: list[str]) -> dict[str, list[Path]]:
    m: dict[str, list[Path]] = defaultdict(list)
    for p in _scan_txt_root(stories_dir, extensions):
        m[p.name.lower()].append(p)
    return dict(m)


def build_gemini_resume_audit(
    *,
    stories_dir: Path,
    phase_a_root: Path,
    extensions: list[str],
) -> dict[str, Any]:
    stories_dir = stories_dir.resolve()
    phase_a_root = phase_a_root.resolve()
    gem_root = gemini_selection_stories_dir(phase_a_root)

    intake_paths = _scan_txt_root(stories_dir, extensions)
    intake_txt_total = len(intake_paths)
    intake_keys = [intake_source_key(p, stories_dir) for p in intake_paths]
    intake_unique_total = len(set(intake_keys))

    intake_by_bn = _intake_by_basename_lower(stories_dir, extensions)

    rows = iter_gemini_story_queue_dirs(gem_root)
    gemini_folder_total = len(rows)

    folder_to_bn: dict[str, str] = {}
    folder_has_nonempty_info: dict[str, bool] = {}
    bn_to_folders: dict[str, list[Path]] = defaultdict(list)

    for _cat, sd in rows:
        sid = str(sd.resolve())
        pt = _primary_story_txt_in_gemini_dir(sd)
        bn = (pt.name.lower() if pt is not None else "")
        folder_to_bn[sid] = bn
        folder_has_nonempty_info[sid] = _is_nonempty_info_txt(sd / "info.txt")
        if bn:
            bn_to_folders[bn].append(sd)

    gemini_folders_with_nonempty_info = sum(1 for v in folder_has_nonempty_info.values() if v)
    gemini_folders_without_info = gemini_folder_total - gemini_folders_with_nonempty_info

    # Ключ для группировки дублей: basename нижний регистр (intake корень).
    duplicate_keys = {k for k, lst in bn_to_folders.items() if k and len(lst) > 1}
    duplicate_source_key_count = len(duplicate_keys)
    duplicate_extra_folder_count = sum(len(bn_to_folders[k]) - 1 for k in duplicate_keys)

    dup_without = 0
    dup_with = 0
    for k in duplicate_keys:
        for sd in bn_to_folders[k]:
            if _is_nonempty_info_txt(sd / "info.txt"):
                dup_with += 1
            else:
                dup_without += 1

    intake_basenames = {p.name.lower() for p in intake_paths}

    def folders_for_intake_key(ik: str) -> list[Path]:
        # ik — posix lower rel; для плоского intake это имя файла lower
        name = Path(ik).name.lower()
        return list(bn_to_folders.get(name, []))

    intake_unique_with_gemini_folder = 0
    intake_unique_done_by_any_nonempty_info = 0
    intake_unique_remaining_without_info = 0
    for ik in set(intake_keys):
        fds = folders_for_intake_key(ik)
        if fds:
            intake_unique_with_gemini_folder += 1
            if any(_is_nonempty_info_txt(d / "info.txt") for d in fds):
                intake_unique_done_by_any_nonempty_info += 1
            else:
                intake_unique_remaining_without_info += 1
        else:
            intake_unique_remaining_without_info += 1

    gemini_folders_without_matching_input_txt = 0
    orphan_examples: list[str] = []
    for _cat, sd in rows:
        sid = str(sd.resolve())
        bn = folder_to_bn.get(sid, "")
        if not bn or bn not in intake_basenames:
            gemini_folders_without_matching_input_txt += 1
            if len(orphan_examples) < 15:
                orphan_examples.append(str(sd))

    input_txt_without_gemini_folder = 0
    missing_examples: list[str] = []
    for p in intake_paths:
        if not bn_to_folders.get(p.name.lower()):
            input_txt_without_gemini_folder += 1
            if len(missing_examples) < 15:
                missing_examples.append(str(p))

    duplicate_examples: list[dict[str, Any]] = []
    for k in sorted(duplicate_keys)[:20]:
        lst = sorted(bn_to_folders[k], key=lambda x: x.name.lower())
        duplicate_examples.append(
            {
                "source_basename_lower": k,
                "folders": [str(x) for x in lst],
                "nonempty_info": [_is_nonempty_info_txt(x / "info.txt") for x in lst],
            }
        )

    lf_kept = length_filter_kept_count(phase_a_root)
    hypothesis_1275_plus_371 = None
    if lf_kept is not None and gemini_folder_total > 0:
        hypothesis_1275_plus_371 = {
            "length_filter_kept": lf_kept,
            "gemini_folder_total": gemini_folder_total,
            "delta_folders_minus_kept": gemini_folder_total - lf_kept,
            "duplicate_extra_folder_count": duplicate_extra_folder_count,
            "note": "Если delta ≈ duplicate_extra, гипотеза «лишние папки = дубли по одному intake» подтверждается.",
        }

    section_a = {
        "intake_txt_total": intake_txt_total,
        "intake_unique_total": intake_unique_total,
        "intake_unique_with_gemini_folder": intake_unique_with_gemini_folder,
        "intake_unique_done_by_any_nonempty_info": intake_unique_done_by_any_nonempty_info,
        "intake_unique_remaining_without_info": intake_unique_remaining_without_info,
    }
    section_b = {
        "gemini_folder_total": gemini_folder_total,
        "gemini_folders_with_nonempty_info": gemini_folders_with_nonempty_info,
        "gemini_folders_without_info": gemini_folders_without_info,
    }
    section_c = {
        "duplicate_source_key_count": duplicate_source_key_count,
        "duplicate_extra_folder_count": duplicate_extra_folder_count,
        "duplicate_examples": duplicate_examples,
        "duplicate_without_info_count": dup_without,
        "duplicate_with_info_count": dup_with,
    }
    section_d = {
        "gemini_folders_without_matching_input_txt": gemini_folders_without_matching_input_txt,
        "orphan_folder_examples": orphan_examples,
        "input_txt_without_gemini_folder": input_txt_without_gemini_folder,
        "input_missing_examples": missing_examples,
    }

    return {
        "meta": {
            "stories_dir": str(stories_dir),
            "phase_a_root": str(phase_a_root),
            "gemini_stories_root": str(gem_root),
            "extensions": extensions,
        },
        "A_intake": section_a,
        "B_gemini_folders": section_b,
        "C_duplicates": section_c,
        "D_orphans": section_d,
        "hypothesis_check": hypothesis_1275_plus_371,
    }


def render_gemini_resume_audit_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Gemini resume / duplicate audit",
        "",
        "## Meta",
        f"- stories_dir: `{payload.get('meta', {}).get('stories_dir', '')}`",
        f"- phase_a_root: `{payload.get('meta', {}).get('phase_a_root', '')}`",
        f"- gemini_stories_root: `{payload.get('meta', {}).get('gemini_stories_root', '')}`",
        "",
        "## A. Intake",
    ]
    a = payload.get("A_intake", {})
    for k in (
        "intake_txt_total",
        "intake_unique_total",
        "intake_unique_with_gemini_folder",
        "intake_unique_done_by_any_nonempty_info",
        "intake_unique_remaining_without_info",
    ):
        lines.append(f"- {k}: {a.get(k)}")
    lines += ["", "## B. Gemini folders"]
    b = payload.get("B_gemini_folders", {})
    for k in ("gemini_folder_total", "gemini_folders_with_nonempty_info", "gemini_folders_without_info"):
        lines.append(f"- {k}: {b.get(k)}")
    lines += ["", "## C. Duplicates"]
    c = payload.get("C_duplicates", {})
    for k in (
        "duplicate_source_key_count",
        "duplicate_extra_folder_count",
        "duplicate_without_info_count",
        "duplicate_with_info_count",
    ):
        lines.append(f"- {k}: {c.get(k)}")
    lines.append("")
    lines.append("### Examples")
    for ex in c.get("duplicate_examples") or []:
        lines.append(f"- **{ex.get('source_basename_lower')}**: {len(ex.get('folders') or [])} folders")
        for fp, ok in zip(ex.get("folders") or [], ex.get("nonempty_info") or []):
            lines.append(f"  - `{fp}` nonempty_info={ok}")
    lines += ["", "## D. Orphans"]
    d = payload.get("D_orphans", {})
    lines.append(f"- gemini_folders_without_matching_input_txt: {d.get('gemini_folders_without_matching_input_txt')}")
    lines.append(f"- input_txt_without_gemini_folder: {d.get('input_txt_without_gemini_folder')}")
    if payload.get("hypothesis_check"):
        lines += ["", "## Hypothesis"]
        lines.append("```json")
        lines.append(json.dumps(payload["hypothesis_check"], ensure_ascii=False, indent=2))
        lines.append("```")
    return "\n".join(lines) + "\n"


def write_gemini_resume_audit_reports(
    *,
    logs_dir: Path,
    payload: dict[str, Any],
) -> tuple[Path, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    jp = logs_dir / "gemini_resume_audit.json"
    mp = logs_dir / "gemini_resume_audit.md"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_gemini_resume_audit_md(payload), encoding="utf-8")
    return jp, mp


def dedupe_plan_payload(audit: dict[str, Any]) -> dict[str, Any]:
    """Только план (read-only): canonical folder и extra для каждого дублирующегося basename."""
    meta = audit.get("meta", {})
    stories_dir = Path(str(meta.get("stories_dir", ".")))
    gem_root = Path(str(meta.get("gemini_stories_root", ".")))
    extensions = meta.get("extensions")
    if not isinstance(extensions, list) or not extensions:
        extensions = [".txt"]

    rows = iter_gemini_story_queue_dirs(gem_root)
    bn_to_folders: dict[str, list[Path]] = defaultdict(list)
    for _c, sd in rows:
        pt = _primary_story_txt_in_gemini_dir(sd)
        if pt is None:
            continue
        bn_to_folders[pt.name.lower()].append(sd)

    intake_by_bn = _intake_by_basename_lower(stories_dir, list(extensions)) if stories_dir.is_dir() else {}

    plans: list[dict[str, Any]] = []
    for bn, lst in sorted(bn_to_folders.items(), key=lambda x: x[0]):
        if len(lst) < 2:
            continue
        lst_sorted = sorted(lst, key=lambda p: p.name.lower())
        scored: list[tuple[tuple[int, float, str], Path]] = []
        for d in lst_sorted:
            has = _is_nonempty_info_txt(d / "info.txt")
            mtime = (d / "info.txt").stat().st_mtime if (d / "info.txt").is_file() else 0.0
            # canonical: сначала с непустым info (новее), иначе лексикографически минимальное имя папки
            key = (0 if has else 1, -mtime if has else 0.0, d.name.lower())
            scored.append((key, d))
        scored.sort(key=lambda t: t[0])
        canonical = scored[0][1]
        extras = [d for d in lst_sorted if d.resolve() != canonical.resolve()]
        intake_candidates = intake_by_bn.get(bn, [])
        intake_path = str(intake_candidates[0]) if intake_candidates else ""
        plans.append(
            {
                "source_basename_lower": bn,
                "intake_path": intake_path,
                "canonical_folder": str(canonical),
                "extra_folders": [str(x) for x in extras],
                "canonical_nonempty_info": _is_nonempty_info_txt(canonical / "info.txt"),
                "extra_nonempty_info": [_is_nonempty_info_txt(x / "info.txt") for x in extras],
            }
        )

    return {
        "dry_run": True,
        "meta": meta,
        "duplicate_groups": len(plans),
        "plans": plans,
        "recovery_note": (
            "Не удалять и не перемещать папки автоматически. "
            "После ручного dry-run: quarantine только лишние extra_folders без непустого info.txt, "
            "если canonical уже содержит готовый результат; иначе оставить воркеру одну canonical "
            "неполную папку (оркестратор теперь переиспользует её при resume)."
        ),
    }


def selection_progress_snapshot(
    *,
    stories_dir: Path,
    phase_a_root: Path,
    extensions: list[str],
) -> dict[str, Any]:
    """Сводка для терминала: раздельно intake-unique и уровень папок Gemini + дубли."""
    audit = build_gemini_resume_audit(stories_dir=stories_dir, phase_a_root=phase_a_root, extensions=extensions)
    a = audit["A_intake"]
    b = audit["B_gemini_folders"]
    c = audit["C_duplicates"]
    return {
        "progress_scope": "split",
        "intake_unique_total": a["intake_unique_total"],
        "intake_unique_done": a["intake_unique_done_by_any_nonempty_info"],
        "intake_unique_remaining": a["intake_unique_remaining_without_info"],
        "gemini_folder_total": b["gemini_folder_total"],
        "gemini_folder_done": b["gemini_folders_with_nonempty_info"],
        "gemini_folder_remaining": b["gemini_folders_without_info"],
        "duplicate_source_keys": c["duplicate_source_key_count"],
        "duplicate_extra_folders": c["duplicate_extra_folder_count"],
    }


def run_gemini_audit_cli(
    *,
    mode: str,
    run_path: Path,
    stories_dir: Path | None,
    extensions: list[str],
    logs_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    phase_a = resolve_phase_a_root(run_path)
    if stories_dir is None:
        im = phase_a / "intake_manifest.json"
        if im.is_file():
            try:
                raw = json.loads(im.read_text(encoding="utf-8"))
                sd = raw.get("stories_dir")
                if isinstance(sd, str) and sd.strip():
                    stories_dir = Path(sd)
            except Exception:
                stories_dir = None
    if stories_dir is None:
        raise SystemExit("Укажите --stories-dir или положите intake_manifest.json в phase_a с полем stories_dir.")
    stories_dir = stories_dir.resolve()
    audit = build_gemini_resume_audit(stories_dir=stories_dir, phase_a_root=phase_a, extensions=extensions)
    jp, mp = write_gemini_resume_audit_reports(logs_dir=logs_dir, payload=audit)
    out: dict[str, Any] = {"audit_json": str(jp), "audit_md": str(mp), "audit": audit}
    if mode == "dedupe-plan":
        if not dry_run:
            raise SystemExit("dedupe-plan поддерживает только --dry-run (план без изменений).")
        plan = dedupe_plan_payload(audit)
        plan_path = logs_dir / "gemini_dedupe_plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        out["dedupe_plan_json"] = str(plan_path)
        out["dedupe_plan"] = plan
    return out
