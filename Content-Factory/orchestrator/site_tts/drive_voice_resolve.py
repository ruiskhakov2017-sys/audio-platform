"""
Голоса Kokoro для Google Drive / Colab: тот же выбор, что у локального KokoroSiteAdapter (voice_pools + deterministic_pool).
Пишется kokoro_voices_job.json в job_dir при export_drive_texts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from orchestrator.site_tts.config import SiteTtsSettings
from orchestrator.site_tts.contract import SiteTtsPaths
from orchestrator.site_tts.info_parser import resolve_voice_letter_from_info_content
from orchestrator.site_tts.kokoro_adapter import KokoroSiteAdapter

VOICE_MANIFEST_FILENAME = "kokoro_voices_job.json"


def collapse_weighted_pool_pick(spec: str, *, story_id: str, voice_label: str) -> str:
    """
    Пул может вернуть строку вида 'af_bella:65,af_heart:35'. KPipeline в Colab ждёт один id —
    выбираем детерминированно по хэшу (как смена индекса в пуле).
    """
    s = (spec or "").strip()
    if not s:
        return ""
    if "," not in s:
        if ":" in s:
            return s.split(":", 1)[0].strip()
        return s
    parts: list[tuple[str, int]] = []
    for seg in s.split(","):
        seg = (seg or "").strip()
        if not seg:
            continue
        if ":" in seg:
            left, right = seg.rsplit(":", 1)
            try:
                w = max(1, int(float(right)))
            except ValueError:
                w = 1
            parts.append((left.strip(), w))
        else:
            parts.append((seg, 1))
    if not parts:
        return s
    if len(parts) == 1:
        return parts[0][0]
    key = f"{story_id}|{voice_label}|weighted_voice".encode("utf-8")
    h = int(hashlib.sha256(key).hexdigest()[:8], 16)
    total = sum(w for _, w in parts) or 1
    r = h % total
    acc = 0
    for vid, w in parts:
        acc += w
        if r < acc:
            return vid
    return parts[-1][0]


def resolve_colab_kokoro_voice_id(*, raw_voice: str, story_id: str, voice_label: str) -> str:
    raw = (raw_voice or "").strip()
    if not raw:
        return "af_bella"
    return collapse_weighted_pool_pick(raw, story_id=story_id, voice_label=voice_label)


def build_kokoro_drive_voice_item(
    *,
    project_root: Path,
    site_root: Path,
    story_folder: Path,
    txt_name: str,
    mp3_name: str,
    settings: SiteTtsSettings,
) -> dict[str, Any]:
    name = story_folder.name
    paths = SiteTtsPaths.for_site_output_folder(project_root, site_root, name)
    return build_kokoro_drive_voice_item_from_paths(
        paths=paths,
        txt_name=txt_name,
        mp3_name=mp3_name,
        story_id=name,
        settings=settings,
    )


def voice_ids_referenced_in_pool_entry(entry: str) -> set[str]:
    """Все Kokoro voice id, упомянутые в слоте пула (включая взвешенные)."""
    s = (entry or "").strip()
    if not s:
        return set()
    out: set[str] = set()
    if "," in s:
        for seg in s.split(","):
            seg = (seg or "").strip()
            if not seg:
                continue
            if ":" in seg:
                out.add(seg.rsplit(":", 1)[0].strip())
            else:
                out.add(seg)
    elif ":" in s:
        out.add(s.split(":", 1)[0].strip())
    else:
        out.add(s)
    return {x for x in out if x}


def collect_voice_ids_from_pools(settings: SiteTtsSettings) -> set[str]:
    out: set[str] = set()
    for _label, pool in (settings.voice_pools or {}).items():
        for entry in pool:
            out.update(voice_ids_referenced_in_pool_entry(entry))
    return out


def build_kokoro_drive_voice_item_from_paths(
    *,
    paths: SiteTtsPaths,
    txt_name: str,
    mp3_name: str,
    story_id: str,
    settings: SiteTtsSettings,
    filename_voice_label: str | None = None,
) -> dict[str, Any]:
    adapter = KokoroSiteAdapter()
    voice_label = (filename_voice_label or "U").strip().upper()[:1]
    if voice_label not in {"M", "F", "U"}:
        voice_label = "U"
    warn: str | None = None
    is_label_fallback = False
    if paths.info_txt.is_file():
        try:
            info = paths.info_txt.read_text(encoding="utf-8", errors="replace")
        except OSError:
            info = ""
        else:
            voice_type, _line, warn = resolve_voice_letter_from_info_content(info)
            voice_label = voice_type
            is_label_fallback = bool(warn)
    selected_label, voice_raw, voice_source = adapter._resolve_voice(
        settings=settings,
        paths=paths,
        voice_label=voice_label,
        is_label_fallback=is_label_fallback,
    )
    kokoro_id = resolve_colab_kokoro_voice_id(
        raw_voice=voice_raw,
        story_id=story_id,
        voice_label=selected_label,
    )
    lang_code = adapter._lang_code(kokoro_id, settings)
    return {
        "txt_name": txt_name,
        "mp3_name": mp3_name,
        "story_folder": story_id,
        "voice_label": selected_label,
        "voice_source": voice_source,
        "kokoro_voice": kokoro_id,
        "lang_code": lang_code,
        "speed": float(settings.kokoro_speed),
        "warn": warn or None,
    }


def build_kokoro_drive_voice_item_for_existing_txt(
    *,
    txt_name: str,
    settings: SiteTtsSettings,
    story_id: str,
    paths: SiteTtsPaths | None,
    filename_voice_label: str,
) -> dict[str, Any]:
    """
    Голос для TXT, уже лежащего на Drive: точное txt_name сохраняется; story_id — для deterministic_pool.
  """
    mp3_name = Path(txt_name).with_suffix(".mp3").name
    if paths is not None:
        return build_kokoro_drive_voice_item_from_paths(
            paths=paths,
            txt_name=txt_name,
            mp3_name=mp3_name,
            story_id=story_id,
            settings=settings,
            filename_voice_label=filename_voice_label,
        )
    adapter = KokoroSiteAdapter()
    pseudo = SiteTtsPaths(
        story_folder=Path(story_id),
        cleaned_story_txt=Path(story_id) / "cleaned_story.txt",
        info_txt=Path(story_id) / "info.txt",
        output_mp3=Path(story_id) / f"{story_id}.mp3",
    )
    label = (filename_voice_label or "U").strip().upper()[:1]
    if label not in {"M", "F", "U"}:
        label = "U"
    selected_label, voice_raw, voice_source = adapter._resolve_voice(
        settings=settings,
        paths=pseudo,
        voice_label=label,
        is_label_fallback=False,
    )
    kokoro_id = resolve_colab_kokoro_voice_id(
        raw_voice=voice_raw,
        story_id=story_id,
        voice_label=selected_label,
    )
    lang_code = adapter._lang_code(kokoro_id, settings)
    return {
        "txt_name": txt_name,
        "mp3_name": mp3_name,
        "story_folder": story_id,
        "voice_label": selected_label,
        "voice_source": voice_source,
        "kokoro_voice": kokoro_id,
        "lang_code": lang_code,
        "speed": float(settings.kokoro_speed),
        "warn": None,
    }


def write_kokoro_voices_job_json(
    *,
    project_root: Path,
    job_dir: Path,
    site_root: Path,
    exported_rows: list[list[str]],
    settings: SiteTtsSettings,
    human_launch: Path | None = None,
) -> Path | None:
    if not exported_rows:
        return None
    site_root = site_root.resolve()
    hl = human_launch.resolve() if human_launch is not None else None
    items: list[dict[str, Any]] = []
    for row in exported_rows:
        if len(row) < 6:
            continue
        _n, sid, txt_name, mp3_name, _fp, _vt = row[:6]
        if hl is not None:
            paths = SiteTtsPaths.from_human_launch_story(hl, str(sid), ensure_dirs=False)
            items.append(
                build_kokoro_drive_voice_item_from_paths(
                    paths=paths,
                    txt_name=str(txt_name),
                    mp3_name=str(mp3_name),
                    story_id=str(sid),
                    settings=settings,
                )
            )
        else:
            story_folder = (site_root / str(sid)).resolve()
            items.append(
                build_kokoro_drive_voice_item(
                    project_root=project_root.resolve(),
                    site_root=site_root,
                    story_folder=story_folder,
                    txt_name=str(txt_name),
                    mp3_name=str(mp3_name),
                    settings=settings,
                )
            )
    note = (
        "Сгенерировано Content-Factory export_drive_texts из configs/site_tts.yaml "
        "(voice_pools, deterministic_pool, .site_tts_voice.json при наличии)."
    )
    if hl is not None:
        note = (
            "Сгенерировано Content-Factory export_drive_texts (human-launch) из configs/site_tts.yaml "
            "(voice_pools, deterministic_pool, .site_tts_voice.json при наличии)."
        )
    payload: dict[str, Any] = {
        "version": 1,
        "default_speed": float(settings.kokoro_speed),
        "note": note,
        "items": items,
    }
    return write_kokoro_voices_job_payload(job_dir=job_dir, payload=payload)


def write_kokoro_voices_job_payload(*, job_dir: Path, payload: dict[str, Any]) -> Path:
    out = (job_dir / VOICE_MANIFEST_FILENAME).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
