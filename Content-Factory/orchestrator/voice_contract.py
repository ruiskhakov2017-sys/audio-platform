"""Site → YouTube voice contract: single source of truth from site info.txt / site TTS resolver."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import OrchestratorConfig
from orchestrator.site_tts.config import DEFAULT_SITE_TTS_REL, SiteTtsSettings, load_site_tts_settings
from orchestrator.site_tts.contract import SiteTtsPaths
from orchestrator.site_tts.drive_voice_resolve import (
    build_kokoro_drive_voice_item_from_paths,
    collect_voice_ids_from_pools,
    resolve_colab_kokoro_voice_id,
    voice_ids_referenced_in_pool_entry,
)
from orchestrator.site_tts.info_parser import resolve_voice_letter_from_info_content

REASON_STALE_REPLACED = "STALE_YOUTUBE_VOICE_REPLACED_FROM_SITE_INFO"
REASON_VOICE_CONTRACT_MISSING = "VOICE_CONTRACT_MISSING"
REASON_VOICE_INFO_MISSING = "VOICE_INFO_MISSING"
REASON_VOICE_GENDER_INVALID = "VOICE_GENDER_INVALID"
REASON_VOICE_MAPPING_NOT_AVAILABLE = "VOICE_MAPPING_NOT_AVAILABLE"
REASON_YOUTUBE_TTS_VOICE_MISMATCH = "YOUTUBE_TTS_VOICE_MISMATCH"
REASON_VOICE_GUARD_NOT_ENABLED = "VOICE_GUARD_NOT_ENABLED"
REASON_EXISTING_BAD_AUDIO = "EXISTING_BAD_YOUTUBE_AUDIO_NOT_REJECTED"
REASON_JOB_VOICE_MISSING = "YOUTUBE_TTS_JOB_VOICE_MISSING"
REASON_U_VOICE_AF_BELLA_FORBIDDEN = "U_VOICE_AF_BELLA_FORBIDDEN"
REASON_U_VOICE_RANDOM_FORBIDDEN = "U_VOICE_RANDOM_FORBIDDEN"

VERDICT_OK = "VOICE_OK"
VERDICT_BLOCKED = "BLOCKED"

VOICE_CONTRACT_JSON = "voice_contract.json"
SITE_TTS_VOICE_JSON = ".site_tts_voice.json"
VOICE_MISMATCH_AUDIO_VIDEO_REJECTED_JSON = "VOICE_MISMATCH_AUDIO_VIDEO_REJECTED.json"

# U = third-person / neutral narrator — never default af_bella or random pick outside site resolver.
_U_FORBIDDEN_VOICES = frozenset({"af_bella"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def kokoro_voice_gender(kokoro_voice: str) -> str:
    v = str(kokoro_voice or "").strip().lower()
    if v.startswith(("am_", "bm_")):
        return "M"
    if v.startswith(("af_", "bf_")):
        return "F"
    return "U"


def genders_compatible(*, expected_gender: str, kokoro_voice: str) -> bool:
    exp = str(expected_gender or "U").strip().upper()[:1]
    got = kokoro_voice_gender(kokoro_voice)
    if exp == "M":
        return got == "M"
    if exp == "F":
        return got == "F"
    return True


def _site_tts_config_path(root: Path) -> Path:
    return (root / DEFAULT_SITE_TTS_REL).resolve()


@dataclass
class VoiceContractResult:
    ok: bool
    reason_code: str = ""
    source: str = ""
    expected_gender: str = ""
    resolved_gender: str = ""
    voice_label: str = ""
    kokoro_voice: str = ""
    site_voice_config: str = ""
    source_info_path: str = ""
    source_site_info_json_path: str = ""
    source_site_audio_path: str = ""
    source_site_tts_voice_json_path: str = ""
    site_actual_voice_id: str = ""
    voice_source: str = ""
    locked: bool = True
    exact_voice_match: bool = False
    u_voice_locked: bool = False
    warnings: list[str] = field(default_factory=list)
    message: str = ""

    def to_manifest_block(self) -> dict[str, Any]:
        block = {
            "source": self.source,
            "expected_gender": self.expected_gender,
            "resolved_gender": self.resolved_gender,
            "voice_type": self.voice_label,
            "kokoro_voice": self.kokoro_voice,
            "voice_label": self.voice_label,
            "site_voice_config": self.site_voice_config,
            "source_info_path": self.source_info_path,
            "source_site_info_json_path": self.source_site_info_json_path,
            "source_site_audio_path": self.source_site_audio_path,
            "source_site_tts_voice_json_path": self.source_site_tts_voice_json_path,
            "site_actual_voice_id": self.site_actual_voice_id,
            "youtube_voice_id": self.kokoro_voice,
            "voice_source": self.voice_source,
            "exact_voice_match": self.exact_voice_match,
            "locked": self.locked,
            "updated_at": _utc_now(),
        }
        if self.voice_label == "U":
            block["u_voice_locked"] = self.u_voice_locked
        return block

    def to_voice_contract_file(self) -> dict[str, Any]:
        return {
            **self.to_manifest_block(),
            "reason_code": self.reason_code,
            "ok": self.ok,
            "message": self.message,
        }

    def to_tts_voice_dict(self, settings: SiteTtsSettings) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "voice_label": self.voice_label,
            "kokoro_voice": self.kokoro_voice,
            "expected_gender": self.expected_gender,
            "speed": float(settings.kokoro_speed),
            "sample_rate": 24000,
            "reason_code": self.reason_code,
            "source_info_path": self.source_info_path,
        }


def _candidate_info_paths(
    *,
    root: Path,
    canonical_basename: str,
    manifest: dict[str, Any] | None,
) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()

    def add(p: Path | None) -> None:
        if p is None:
            return
        try:
            key = str(p.resolve()).casefold()
        except OSError:
            key = str(p).casefold()
        if key in seen:
            return
        seen.add(key)
        if p.is_file():
            out.append(p)

    name = str(canonical_basename or "").strip()
    if manifest:
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        site_dir = str(source.get("site_run_story_dir") or "").strip()
        if site_dir:
            base = Path(site_dir)
            add(base / "info.txt")
            add(base / "03_Сайт" / "02_Информация_для_сайта" / "info.txt")
            add(base / "03_Сайт" / "05_Пакет_к_публикации" / "info.txt")
        tg = manifest.get("telegram") if isinstance(manifest.get("telegram"), dict) else {}
        assets = tg.get("asset_sources") if isinstance(tg.get("asset_sources"), dict) else {}
        info_src = assets.get("info") if isinstance(assets.get("info"), dict) else {}
        add(Path(str(info_src.get("path") or "")))

    if name:
        launch_guess = root / "Запуски"
        if launch_guess.is_dir():
            for launch in launch_guess.iterdir():
                if not launch.is_dir():
                    continue
                story_base = launch / "05_Рассказы" / name
                add(story_base / "03_Сайт" / "02_Информация_для_сайта" / "info.txt")
                add(story_base / "03_Сайт" / "05_Пакет_к_публикации" / "info.txt")
        add(root / "output" / "site" / name / "info.txt")

    return out


def _candidate_site_info_json_paths(*, root: Path, canonical_basename: str, manifest: dict[str, Any] | None) -> list[Path]:
    out: list[Path] = []
    name = str(canonical_basename or "").strip()
    if manifest:
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        site_dir = str(source.get("site_run_story_dir") or "").strip()
        if site_dir:
            base = Path(site_dir)
            for rel in (
                "site_info.json",
                "03_Сайт/02_Информация_для_сайта/site_info.json",
            ):
                p = base / rel
                if p.is_file():
                    out.append(p)
    if name:
        p = root / "output" / "site" / name / "site_info.json"
        if p.is_file():
            out.append(p)
        launch_guess = root / "Запуски"
        if launch_guess.is_dir():
            for launch in launch_guess.iterdir():
                p2 = launch / "05_Рассказы" / name / "03_Сайт" / "02_Информация_для_сайта" / "site_info.json"
                if p2.is_file():
                    out.append(p2)
    return out


def _candidate_site_audio_paths(*, root: Path, canonical_basename: str, manifest: dict[str, Any] | None) -> list[Path]:
    out: list[Path] = []
    name = str(canonical_basename or "").strip()
    if manifest:
        tg = manifest.get("telegram") if isinstance(manifest.get("telegram"), dict) else {}
        assets = tg.get("asset_sources") if isinstance(tg.get("asset_sources"), dict) else {}
        audio_src = assets.get("audio") if isinstance(assets.get("audio"), dict) else {}
        p = Path(str(audio_src.get("path") or ""))
        if p.is_file():
            out.append(p)
    if name:
        folder = root / "output" / "site" / name
        if folder.is_dir():
            for mp3 in folder.glob("*.mp3"):
                out.append(mp3)
    return out


def _candidate_site_tts_voice_json_paths(
    *,
    root: Path,
    canonical_basename: str,
    manifest: dict[str, Any] | None,
) -> list[Path]:
    """Paths to .site_tts_voice.json written by site Kokoro TTS (exact site voice_id)."""
    out: list[Path] = []
    seen: set[str] = set()
    name = str(canonical_basename or "").strip()

    def add(p: Path | None) -> None:
        if p is None:
            return
        try:
            key = str(p.resolve()).casefold()
        except OSError:
            key = str(p).casefold()
        if key in seen:
            return
        seen.add(key)
        if p.is_file():
            out.append(p)

    if manifest:
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        site_dir = str(source.get("site_run_story_dir") or "").strip()
        if site_dir:
            base = Path(site_dir)
            add(base / SITE_TTS_VOICE_JSON)
            add(base / "03_Сайт" / "04_Озвучка" / SITE_TTS_VOICE_JSON)
            add(base / "03_Сайт" / "04_Озвучка" / ".site_tts_voice.json")

    if name:
        add(root / "output" / "site" / name / SITE_TTS_VOICE_JSON)
        launch_guess = root / "Запуски"
        if launch_guess.is_dir():
            for launch in launch_guess.iterdir():
                if not launch.is_dir():
                    continue
                story_base = launch / "05_Рассказы" / name
                add(story_base / "03_Сайт" / "04_Озвучка" / SITE_TTS_VOICE_JSON)
                add(story_base / SITE_TTS_VOICE_JSON)

    return out


def _read_site_actual_voice_id(voice_json_path: Path | None) -> tuple[str, str]:
    """Return (selected_voice_id, voice_label_from_meta)."""
    if voice_json_path is None or not voice_json_path.is_file():
        return "", ""
    data = _read_json(voice_json_path)
    voice = str(data.get("selected_voice") or "").strip()
    label = str(data.get("voice_label") or "").strip().upper()[:1]
    return voice, label


def _is_u_forbidden_voice(*, kokoro_voice: str, voice_source: str) -> bool:
    v = str(kokoro_voice or "").strip().lower()
    src = str(voice_source or "").strip().lower()
    if v in _U_FORBIDDEN_VOICES and src in {"fallback", "default", ""}:
        return True
    return v in _U_FORBIDDEN_VOICES and "random" in src


def _strip_forbidden_u_pool_entry(entry: str) -> str:
    s = str(entry or "").strip()
    if not s:
        return ""

    def keep_segment(segment: str) -> str:
        seg = str(segment or "").strip()
        if not seg:
            return ""
        voice_id = seg.rsplit(":", 1)[0].strip() if ":" in seg else seg
        return "" if voice_id.lower() in _U_FORBIDDEN_VOICES else seg

    if "," in s:
        return ",".join(seg for seg in (keep_segment(part) for part in s.split(",")) if seg)
    return keep_segment(s)


def _allowed_u_voice_pool(settings: SiteTtsSettings) -> list[str]:
    pool = list(settings.voice_pools.get("U") or [])
    if not pool:
        fb_label = settings.voice_selection_fallback_label or "U"
        pool = list(settings.voice_pools.get(fb_label, []) or [])
    return [entry for entry in (_strip_forbidden_u_pool_entry(raw) for raw in pool) if entry]


def _pick_allowed_u_pool_voice(*, settings: SiteTtsSettings, story_id: str) -> str:
    pool = _allowed_u_voice_pool(settings)
    if not pool:
        return ""
    from orchestrator.site_tts.kokoro_adapter import KokoroSiteAdapter

    adapter = KokoroSiteAdapter()
    raw = adapter._pick_from_pool(story_id=story_id, voice_label="U", pool=pool)
    resolved = resolve_colab_kokoro_voice_id(raw_voice=raw, story_id=story_id, voice_label="U")
    if resolved and resolved.lower() not in _U_FORBIDDEN_VOICES:
        return resolved

    for entry in pool:
        for voice_id in sorted(voice_ids_referenced_in_pool_entry(entry)):
            resolved = resolve_colab_kokoro_voice_id(raw_voice=voice_id, story_id=story_id, voice_label="U")
            if resolved and resolved.lower() not in _U_FORBIDDEN_VOICES:
                return resolved
    return ""


def _resolve_u_kokoro_voice(
    *,
    settings: SiteTtsSettings,
    paths: SiteTtsPaths,
    story_id: str,
    site_actual_voice_id: str,
    site_voice_meta_path: Path | None,
    item: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """
    U = third-person neutral narrator.
    1) exact site voice_id when site already synthesized
    2) else same deterministic U resolver as site TTS (never af_bella default / random)
    """
    warnings: list[str] = []
    if site_actual_voice_id:
        if site_actual_voice_id.strip().lower() not in _U_FORBIDDEN_VOICES:
            return site_actual_voice_id, "site_tts_voice_json", warnings
        warnings.append(REASON_U_VOICE_AF_BELLA_FORBIDDEN)

    kokoro_voice = str(item.get("kokoro_voice") or "").strip()
    voice_source = str(item.get("voice_source") or "").strip()

    if (
        not kokoro_voice
        or _is_u_forbidden_voice(kokoro_voice=kokoro_voice, voice_source=voice_source)
        or kokoro_voice.lower() in _U_FORBIDDEN_VOICES
    ):
        resolved = _pick_allowed_u_pool_voice(settings=settings, story_id=story_id)
        if resolved:
            return resolved, "site_u_deterministic_pool", warnings
        return (
            "",
            voice_source,
            warnings or [REASON_U_VOICE_AF_BELLA_FORBIDDEN],
        )

    if voice_source not in {"existing", "site_tts_voice_json", "new", "site_u_deterministic_pool"}:
        warnings.append(REASON_U_VOICE_RANDOM_FORBIDDEN)

    return kokoro_voice, voice_source or "site_resolver", warnings


def load_voice_contract_file(story_dir: Path) -> dict[str, Any]:
    return _read_json(story_dir / VOICE_CONTRACT_JSON)


def write_voice_contract_file(
    *,
    config: OrchestratorConfig | None = None,
    story_id: str = "",
    story_dir: Path,
    contract: VoiceContractResult,
) -> Path:
    from orchestrator.isolated_io import is_active_isolated, write_json as iso_write_json
    from orchestrator.isolated_site_paths import resolve_voice_contract_write_path

    sid = story_id or story_dir.name
    if config is not None and is_active_isolated(config):
        path = resolve_voice_contract_write_path(config, sid, story_dir)
        iso_write_json(
            config,
            path,
            contract.to_voice_contract_file(),
            module="orchestrator.voice_contract",
            function="write_voice_contract_file",
        )
        return path
    path = story_dir / VOICE_CONTRACT_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract.to_voice_contract_file(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _contract_from_locked_file(data: dict[str, Any]) -> VoiceContractResult | None:
    if not data.get("locked"):
        return None
    voice = str(data.get("kokoro_voice") or data.get("youtube_voice_id") or "").strip()
    label = str(data.get("voice_label") or data.get("voice_type") or "U").strip().upper()[:1]
    if not voice or label not in {"M", "F", "U"}:
        return None
    if label == "U" and voice.lower() in _U_FORBIDDEN_VOICES:
        return None
    site_actual = str(data.get("site_actual_voice_id") or "").strip()
    u_locked = bool(data.get("u_voice_locked")) if label == "U" else False
    return VoiceContractResult(
        ok=True,
        source=str(data.get("source") or "voice_contract_json"),
        expected_gender=str(data.get("expected_gender") or label),
        resolved_gender=str(data.get("resolved_gender") or label),
        voice_label=label,
        kokoro_voice=voice,
        site_voice_config=str(data.get("site_voice_config") or ""),
        source_info_path=str(data.get("source_info_path") or ""),
        source_site_info_json_path=str(data.get("source_site_info_json_path") or ""),
        source_site_audio_path=str(data.get("source_site_audio_path") or ""),
        source_site_tts_voice_json_path=str(data.get("source_site_tts_voice_json_path") or ""),
        site_actual_voice_id=site_actual,
        voice_source=str(data.get("voice_source") or "locked"),
        locked=True,
        exact_voice_match=bool(site_actual and site_actual == voice) if label == "U" else True,
        u_voice_locked=u_locked or (label == "U" and data.get("locked")),
    )


def _voice_type_from_site_info_json(path: Path) -> str:
    data = _read_json(path)
    vt = str(data.get("voice_type") or "").strip().upper()[:1]
    return vt if vt in {"M", "F", "U"} else ""


def _build_site_paths(*, root: Path, canonical_basename: str, info_path: Path) -> SiteTtsPaths:
    site_output = root / "output" / "site"
    return SiteTtsPaths.for_site_output_folder(root, site_output, canonical_basename)


def resolve_site_voice_contract(
    *,
    config: OrchestratorConfig,
    canonical_basename: str,
    manifest: dict[str, Any] | None = None,
    settings: SiteTtsSettings | None = None,
    story_dir: Path | None = None,
    respect_locked: bool = True,
) -> VoiceContractResult:
    root = config.root_dir.resolve()
    name = str(canonical_basename or "").strip()
    if not name:
        return VoiceContractResult(ok=False, reason_code=REASON_VOICE_INFO_MISSING, message="empty story id")

    if respect_locked and story_dir is not None:
        locked_file = load_voice_contract_file(story_dir)
        locked_contract = _contract_from_locked_file(locked_file)
        if locked_contract is not None:
            return locked_contract
        if manifest:
            vc = manifest.get("voice_contract") if isinstance(manifest.get("voice_contract"), dict) else {}
            locked_contract = _contract_from_locked_file(vc)
            if locked_contract is not None:
                return locked_contract

    try:
        site_settings = settings or load_site_tts_settings(root)
    except FileNotFoundError as exc:
        return VoiceContractResult(
            ok=False,
            reason_code=REASON_VOICE_MAPPING_NOT_AVAILABLE,
            message=str(exc),
            site_voice_config=str(_site_tts_config_path(root)),
        )

    info_paths = _candidate_info_paths(root=root, canonical_basename=name, manifest=manifest)
    info_json_paths = _candidate_site_info_json_paths(root=root, canonical_basename=name, manifest=manifest)
    audio_paths = _candidate_site_audio_paths(root=root, canonical_basename=name, manifest=manifest)
    voice_meta_paths = _candidate_site_tts_voice_json_paths(root=root, canonical_basename=name, manifest=manifest)

    info_path: Path | None = info_paths[0] if info_paths else None
    info_json_path: Path | None = info_json_paths[0] if info_json_paths else None
    audio_path: Path | None = audio_paths[0] if audio_paths else None
    voice_meta_path: Path | None = voice_meta_paths[0] if voice_meta_paths else None

    voice_label = ""
    source = ""
    warn = ""

    if info_path is not None:
        try:
            info_text = info_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            info_text = ""
        if info_text.strip():
            voice_label, _line, warn = resolve_voice_letter_from_info_content(info_text)
            source = "site_info_txt"

    if not voice_label and info_json_path is not None:
        vt = _voice_type_from_site_info_json(info_json_path)
        if vt:
            voice_label = vt
            source = "site_info_json"

    if voice_label not in {"M", "F", "U"}:
        return VoiceContractResult(
            ok=False,
            reason_code=REASON_VOICE_INFO_MISSING,
            message=f"site voice_type not found for {name!r}",
            source_info_path=str(info_path) if info_path else "",
            source_site_info_json_path=str(info_json_path) if info_json_path else "",
            site_voice_config=str(_site_tts_config_path(root)),
        )

    if warn and voice_label == "U" and "WARN" in warn:
        return VoiceContractResult(
            ok=False,
            reason_code=REASON_VOICE_GENDER_INVALID,
            message=warn,
            expected_gender="U",
            source_info_path=str(info_path) if info_path else "",
        )

    site_actual_voice_id, _meta_label = _read_site_actual_voice_id(voice_meta_path)

    paths = _build_site_paths(root=root, canonical_basename=name, info_path=info_path or Path(name))
    if info_path is not None:
        paths = SiteTtsPaths(
            story_folder=paths.story_folder,
            cleaned_story_txt=paths.cleaned_story_txt,
            info_txt=info_path,
            output_mp3=audio_path or paths.output_mp3,
        )

    try:
        item = build_kokoro_drive_voice_item_from_paths(
            paths=paths,
            txt_name=f"{name}.txt",
            mp3_name=f"{name}.mp3",
            story_id=name,
            settings=site_settings,
        )
    except Exception as exc:
        return VoiceContractResult(
            ok=False,
            reason_code=REASON_VOICE_MAPPING_NOT_AVAILABLE,
            message=repr(exc),
            expected_gender=voice_label,
            source=source,
            source_info_path=str(info_path) if info_path else "",
        )

    kokoro_voice = str(item.get("kokoro_voice") or "").strip()
    voice_source = str(item.get("voice_source") or "site_resolver").strip()
    resolved_label = str(item.get("voice_label") or voice_label).strip().upper()[:1]
    u_warnings: list[str] = []

    if voice_label == "U":
        kokoro_voice, voice_source, u_warnings = _resolve_u_kokoro_voice(
            settings=site_settings,
            paths=paths,
            story_id=name,
            site_actual_voice_id=site_actual_voice_id,
            site_voice_meta_path=voice_meta_path,
            item=item,
        )
        if not kokoro_voice:
            reason = u_warnings[0] if u_warnings else REASON_U_VOICE_AF_BELLA_FORBIDDEN
            return VoiceContractResult(
                ok=False,
                reason_code=reason,
                message="U voice: af_bella/default/random forbidden; use site voice_id or deterministic U pool",
                expected_gender="U",
                voice_label="U",
                site_actual_voice_id=site_actual_voice_id,
                source_site_tts_voice_json_path=str(voice_meta_path) if voice_meta_path else "",
                source=source,
            )
        resolved_label = "U"

    if not kokoro_voice:
        return VoiceContractResult(
            ok=False,
            reason_code=REASON_VOICE_MAPPING_NOT_AVAILABLE,
            message="kokoro voice empty after site resolver",
            expected_gender=voice_label,
            source=source,
        )

    if not genders_compatible(expected_gender=voice_label, kokoro_voice=kokoro_voice):
        return VoiceContractResult(
            ok=False,
            reason_code=REASON_VOICE_GENDER_INVALID,
            message=f"site gender {voice_label} incompatible with kokoro {kokoro_voice}",
            expected_gender=voice_label,
            kokoro_voice=kokoro_voice,
            source=source,
        )

    exact_match = bool(site_actual_voice_id and site_actual_voice_id == kokoro_voice)
    u_locked = voice_label == "U" and bool(kokoro_voice)

    return VoiceContractResult(
        ok=True,
        source=source,
        expected_gender=voice_label,
        resolved_gender=resolved_label,
        voice_label=resolved_label,
        kokoro_voice=kokoro_voice,
        site_voice_config=str(_site_tts_config_path(root)),
        source_info_path=str(info_path) if info_path else "",
        source_site_info_json_path=str(info_json_path) if info_json_path else "",
        source_site_audio_path=str(audio_path) if audio_path else "",
        source_site_tts_voice_json_path=str(voice_meta_path) if voice_meta_path else "",
        site_actual_voice_id=site_actual_voice_id,
        voice_source=voice_source,
        locked=True,
        exact_voice_match=exact_match,
        u_voice_locked=u_locked,
        warnings=[w for w in ([warn] if warn else []) + u_warnings if w],
    )


def _manifest_youtube_voice(manifest: dict[str, Any]) -> tuple[str, str]:
    tts = manifest.get("tts_kokoro_colab") if isinstance(manifest.get("tts_kokoro_colab"), dict) else {}
    vc = manifest.get("voice_contract") if isinstance(manifest.get("voice_contract"), dict) else {}
    label = str(tts.get("voice_label") or vc.get("voice_label") or "").strip().upper()[:1]
    voice = str(tts.get("kokoro_voice") or vc.get("kokoro_voice") or "").strip()
    return label, voice


def sync_voice_contract_in_manifest(
    *,
    config: OrchestratorConfig,
    manifest: dict[str, Any],
    manifest_path: Path | None = None,
    write: bool = False,
) -> tuple[dict[str, Any], VoiceContractResult, list[str]]:
    canonical = str(manifest.get("canonical_basename") or manifest.get("story_id") or "").strip()
    story_dir = manifest_path.parent if manifest_path is not None else None
    contract = resolve_site_voice_contract(
        config=config,
        canonical_basename=canonical,
        manifest=manifest,
        story_dir=story_dir,
        respect_locked=True,
    )
    events: list[str] = []

    if not contract.ok:
        return manifest, contract, events

    old_label, old_voice = _manifest_youtube_voice(manifest)
    stale = bool(old_voice) and (
        old_label != contract.voice_label
        or old_voice != contract.kokoro_voice
        or not genders_compatible(expected_gender=contract.expected_gender, kokoro_voice=old_voice)
    )
    if stale:
        events.append(REASON_STALE_REPLACED)
        contract.warnings.append(REASON_STALE_REPLACED)

    manifest["voice_contract"] = contract.to_manifest_block()
    tts = dict(manifest.get("tts_kokoro_colab") or {})
    tts["voice_label"] = contract.voice_label
    tts["kokoro_voice"] = contract.kokoro_voice
    manifest["tts_kokoro_colab"] = tts

    if write and manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        write_voice_contract_file(
            config=config,
            story_id=str(manifest.get("story_id") or manifest.get("canonical_basename") or manifest_path.parent.name),
            story_dir=manifest_path.parent,
            contract=contract,
        )

    return manifest, contract, events


def resolve_youtube_tts_voice(
    *,
    config: OrchestratorConfig,
    manifest: dict[str, Any],
    manifest_path: Path | None = None,
    write_manifest: bool = True,
) -> dict[str, Any]:
    manifest, contract, _events = sync_voice_contract_in_manifest(
        config=config,
        manifest=manifest,
        manifest_path=manifest_path,
        write=write_manifest and manifest_path is not None,
    )
    if not contract.ok:
        return {
            "ok": False,
            "reason_code": contract.reason_code or REASON_VOICE_CONTRACT_MISSING,
            "message": contract.message or REASON_VOICE_CONTRACT_MISSING,
        }
    try:
        settings = load_site_tts_settings(config.root_dir)
    except FileNotFoundError:
        settings = None
    speed = float(settings.kokoro_speed) if settings else 0.92
    return {
        "ok": True,
        "voice_label": contract.voice_label,
        "kokoro_voice": contract.kokoro_voice,
        "expected_gender": contract.expected_gender,
        "speed": speed,
        "sample_rate": 24000,
        "source_info_path": contract.source_info_path,
        "voice_contract": contract.to_manifest_block(),
        "warnings": contract.warnings,
    }


def voice_mapping_plan_lines(settings: SiteTtsSettings) -> list[str]:
    lines = [
        "VOICE MAPPING",
        "M = male voice",
        "F = female voice",
        "U = third person / neutral narrator (exact site voice_id or deterministic U pool; never af_bella default)",
    ]
    for label in ("M", "F", "U"):
        pool = settings.voice_pools.get(label) or []
        preview = ", ".join(pool[:3]) if pool else "n/a"
        lines.append(f"{label} -> {preview}")
    return lines


def _find_story_manifests_in_batch(batch_root: Path) -> list[Path]:
    yt = batch_root / "03_youtube"
    if not yt.is_dir():
        return []
    return sorted(p for p in yt.rglob("youtube_story_manifest.json") if p.is_file())


def _read_tts_job_voice(search_root: Path, canonical: str) -> tuple[str, str]:
    if not search_root.is_dir():
        return "", ""
    for pattern in ("**/full_tts_job.json", "**/sample_tts_job.json", "**/youtube_tts_job.json"):
        for job_path in search_root.glob(pattern):
            data = _read_json(job_path)
            sid = str(data.get("story_id") or data.get("canonical_basename") or "").strip()
            if sid and sid.casefold() != canonical.casefold():
                items = data.get("items")
                if isinstance(items, list):
                    matched = any(
                        str(it.get("canonical_basename") or it.get("story_id") or "").casefold() == canonical.casefold()
                        for it in items
                        if isinstance(it, dict)
                    )
                    if not matched:
                        continue
                else:
                    continue
            label = str(data.get("voice_label") or "").strip()
            voice = str(data.get("kokoro_voice") or "").strip()
            if not voice and isinstance(data.get("items"), list):
                for it in data["items"]:
                    if isinstance(it, dict):
                        voice = str(it.get("kokoro_voice") or voice)
                        label = str(it.get("voice_label") or label)
            if voice:
                return label, voice
    return "", ""


def audit_story_voice(
    *,
    config: OrchestratorConfig,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    canonical = str(manifest.get("canonical_basename") or manifest.get("story_id") or manifest_path.parent.name)
    search_root = manifest_path.parent
    launch_root = manifest.get("launch_root")
    if launch_root:
        search_root = Path(str(launch_root))
    contract = resolve_site_voice_contract(
        config=config,
        canonical_basename=canonical,
        manifest=manifest,
        story_dir=manifest_path.parent,
    )
    yt_label, yt_voice = _manifest_youtube_voice(manifest)
    job_label, job_voice = _read_tts_job_voice(search_root, canonical)

    site_selected = contract.kokoro_voice if contract.ok else ""
    expected = contract.expected_gender if contract.ok else ""
    site_actual = contract.site_actual_voice_id if contract.ok else ""
    exact_match = bool(site_actual and yt_voice and site_actual == yt_voice) if expected == "U" else None
    u_locked = contract.u_voice_locked if contract.ok and expected == "U" else False

    verdict = VERDICT_OK
    if not contract.ok:
        verdict = contract.reason_code or REASON_VOICE_INFO_MISSING
    elif expected == "U" and yt_voice and yt_voice.lower() in _U_FORBIDDEN_VOICES and yt_label == "U":
        verdict = "STALE_YOUTUBE_VOICE"
    elif yt_voice and not genders_compatible(expected_gender=expected, kokoro_voice=yt_voice):
        verdict = "STALE_YOUTUBE_VOICE" if yt_voice != contract.kokoro_voice else REASON_YOUTUBE_TTS_VOICE_MISMATCH
    elif job_voice and not genders_compatible(expected_gender=expected, kokoro_voice=job_voice):
        verdict = REASON_YOUTUBE_TTS_VOICE_MISMATCH
    elif yt_voice and contract.ok and yt_voice != contract.kokoro_voice:
        verdict = "STALE_YOUTUBE_VOICE"
    elif expected == "U" and site_actual and yt_voice and site_actual != yt_voice:
        verdict = REASON_YOUTUBE_TTS_VOICE_MISMATCH

    rejected = manifest_path.parent / "99_rejected" / "VOICE_MISMATCH_REJECTED.json"
    av_rejected = manifest_path.parent / "99_rejected" / VOICE_MISMATCH_AUDIO_VIDEO_REJECTED_JSON
    if rejected.is_file() or av_rejected.is_file():
        verdict = VERDICT_BLOCKED

    row = {
        "story_title": canonical,
        "voice_type": expected,
        "site_voice_type": expected,
        "resolved_gender": contract.resolved_gender if contract.ok else "",
        "site_actual_voice_id": site_actual,
        "site_selected_voice": site_selected,
        "youtube_voice_id": yt_voice,
        "youtube_manifest_voice_label": yt_label,
        "youtube_kokoro_voice": yt_voice,
        "exact_voice_match": exact_match if exact_match is not None else (site_actual == yt_voice if site_actual and yt_voice else ""),
        "u_voice_locked": u_locked,
        "tts_job_voice_label": job_label,
        "tts_job_kokoro_voice": job_voice,
        "source_info_path": contract.source_info_path,
        "source_site_tts_voice_json_path": contract.source_site_tts_voice_json_path if contract.ok else "",
        "verdict": verdict,
    }
    return row


def run_youtube_voice_audit(
    *,
    config: OrchestratorConfig,
    site_run_id: str = "",
    youtube_run_id: str,
    execute: bool = False,
) -> dict[str, Any]:
    from orchestrator.isolated_launch_context import isolated_launch_context
    from orchestrator.isolated_launch_mode import is_isolated_launch
    from orchestrator.reports_path_resolver import resolve_voice_audit_reports_dir
    from orchestrator.youtube_full_auto.layout import batch_launch_root

    if is_isolated_launch(config, launch_id=youtube_run_id):
        with isolated_launch_context(config, youtube_run_id):
            return _run_youtube_voice_audit_body(
                config=config,
                site_run_id=site_run_id,
                youtube_run_id=youtube_run_id,
                execute=execute,
            )
    return _run_youtube_voice_audit_body(
        config=config,
        site_run_id=site_run_id,
        youtube_run_id=youtube_run_id,
        execute=execute,
    )


def _run_youtube_voice_audit_body(
    *,
    config: OrchestratorConfig,
    site_run_id: str = "",
    youtube_run_id: str,
    execute: bool = False,
) -> dict[str, Any]:
    from orchestrator.isolated_io import is_active_isolated, write_json as iso_write_json, write_text as iso_write_text
    from orchestrator.reports_path_resolver import resolve_voice_audit_reports_dir
    from orchestrator.youtube_full_auto.layout import batch_launch_root

    batch_root = batch_launch_root(config, youtube_run_id)
    if not batch_root.is_dir():
        return {"ok": False, "message": f"batch not found: {batch_root}"}

    rows = [audit_story_voice(config=config, manifest_path=p) for p in _find_story_manifests_in_batch(batch_root)]
    reports_dir = resolve_voice_audit_reports_dir(config, launch_id=youtube_run_id)
    json_path = reports_dir / "voice_consistency_matrix.json"
    csv_path = reports_dir / "voice_consistency_matrix.csv"
    md_path = reports_dir / "voice_consistency_report.md"

    payload = {
        "generated_at": _utc_now(),
        "site_run_id": site_run_id,
        "youtube_run_id": youtube_run_id,
        "rows": rows,
        "counts": {},
    }
    for row in rows:
        v = str(row.get("verdict") or "")
        payload["counts"][v] = payload["counts"].get(v, 0) + 1

    if execute or True:
        if is_active_isolated(config):
            iso_write_json(
                config,
                json_path,
                payload,
                module="orchestrator.voice_contract",
                function="run_youtube_voice_audit",
            )
        else:
            reports_dir.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if rows:
            import io

            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
            csv_text = buffer.getvalue()
            if is_active_isolated(config):
                iso_write_text(
                    config,
                    csv_path,
                    csv_text,
                    module="orchestrator.voice_contract",
                    function="run_youtube_voice_audit",
                )
            else:
                with csv_path.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(csv_text)
        md_lines = [
            "# Voice consistency report",
            "",
            f"generated_at: {payload['generated_at']}",
            f"youtube_run_id: {youtube_run_id}",
            "",
            "| story | site | site voice | yt label | yt kokoro | job kokoro | verdict |",
            "|---|---:|---|---|---|---|---|",
        ]
        for row in rows:
            md_lines.append(
                f"| {row['story_title']} | {row['site_voice_type']} | {row['site_selected_voice']} | "
                f"{row['youtube_manifest_voice_label']} | {row['youtube_kokoro_voice']} | "
                f"{row['tts_job_kokoro_voice']} | {row['verdict']} |"
            )
        md_body = "\n".join(md_lines) + "\n"
        if is_active_isolated(config):
            iso_write_text(
                config,
                md_path,
                md_body,
                module="orchestrator.voice_contract",
                function="run_youtube_voice_audit",
            )
        else:
            md_path.write_text(md_body, encoding="utf-8")

    bad = sum(1 for r in rows if r.get("verdict") not in {VERDICT_OK, "", None})
    return {
        "ok": bad == 0,
        "youtube_run_id": youtube_run_id,
        "total": len(rows),
        "bad": bad,
        "reports": {"json": str(json_path), "csv": str(csv_path), "md": str(md_path)},
        "rows": rows,
    }


_SELECTIVE_AUDIO_VIDEO_GLOBS = (
    "04_audio/narration/**/*.mp3",
    "04_audio/narration.mp3",
    "04_audio/final_audio/**/*.mp3",
    "04_audio/samples/**/*.mp3",
    "07_runpod_package/**/*.mp3",
    "07_runpod_package/**/*.wav",
    "07_runpod_package/**/*.m4a",
    "08_video/final/**/*.mp4",
    "08_video/**/*final*.mp4",
    "08_video/rendered/**/*.mp4",
)

_BROAD_ARTIFACT_GLOBS = _SELECTIVE_AUDIO_VIDEO_GLOBS + (
    "04_audio/reports/AUDIO_*.json",
    "04_audio/reports/FULL_TTS_*.json",
    "04_audio/reports/audio_*.json",
)

_UNTOUCHED_VISUAL_GLOBS = (
    "**/frames/**",
    "**/images/**",
    "**/06_frames/**",
    "**/05_scenes/**",
    "**/04_characters/**",
    "**/03_visual/**",
    "**/02_safe_story/**",
    "**/promo/**",
    "**/telegram/**",
    "**/08_Telegram/**",
)

_PROTECTED_PATH_PARTS = frozenset(
    p.casefold()
    for p in (
        "frames",
        "images",
        "prompts",
        "visual",
        "characters",
        "character",
        "pictures",
        "metadata",
        "safe_story",
        "promo",
        "telegram",
        "site",
        "info.txt",
    )
)

_AUDIO_VIDEO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".mp4", ".aac", ".flac"})


def _path_has_protected_part(path: Path) -> bool:
    return any(part.casefold() in _PROTECTED_PATH_PARTS for part in path.parts)


def _is_audio_video_artifact(path: Path) -> bool:
    return path.suffix.casefold() in _AUDIO_VIDEO_EXTENSIONS


def _collect_selective_reject_paths(story_dir: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in _SELECTIVE_AUDIO_VIDEO_GLOBS:
        for p in story_dir.glob(pattern):
            if not p.is_file():
                continue
            if _path_has_protected_part(p):
                continue
            if not _is_audio_video_artifact(p):
                continue
            found.append(p.resolve())
    return sorted(set(found))


def _collect_broad_reject_paths(story_dir: Path) -> list[Path]:
    found = _collect_selective_reject_paths(story_dir)
    for pattern in _BROAD_ARTIFACT_GLOBS:
        if pattern in _SELECTIVE_AUDIO_VIDEO_GLOBS:
            continue
        for p in story_dir.glob(pattern):
            if p.is_file():
                found.append(p.resolve())
    for name in ("READY_FOR_RUNPOD.json", "PRODUCTION_PREFLIGHT.json"):
        for p in story_dir.rglob(name):
            if p.is_file():
                found.append(p.resolve())
    return sorted(set(found))


def _count_untouched_visual_paths(story_dir: Path) -> int:
    count = 0
    for pattern in _UNTOUCHED_VISUAL_GLOBS:
        count += sum(1 for p in story_dir.glob(pattern) if p.is_file())
    return count


def reject_bad_voice_artifacts(
    *,
    config: OrchestratorConfig,
    launch_id: str,
    story_id: str,
    reason_code: str = REASON_YOUTUBE_TTS_VOICE_MISMATCH,
    only_audio_video: bool = True,
    execute: bool = False,
) -> dict[str, Any]:
    from orchestrator.launch_contract import resolve_youtube_story_dir

    root = config.root_dir.resolve()
    story_dir, _ctx = resolve_youtube_story_dir(config, launch_id=launch_id, story_id=story_id)
    if not story_dir.is_dir():
        legacy = root / "output" / "youtube" / story_id
        story_dir = legacy if legacy.is_dir() else story_dir

    manifest_path = story_dir / "youtube_story_manifest.json"
    manifest = _read_json(manifest_path)
    contract = resolve_site_voice_contract(
        config=config,
        canonical_basename=str(manifest.get("canonical_basename") or story_id),
        manifest=manifest,
        story_dir=story_dir,
    )
    yt_label, yt_voice = _manifest_youtube_voice(manifest)
    expected = contract.expected_gender if contract.ok else ""
    actual_gender = kokoro_voice_gender(yt_voice) if yt_voice else ""

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reject_dir = story_dir / "99_rejected" / f"voice_mismatch_{ts}"
    moved_audio: list[str] = []
    moved_video: list[str] = []

    targets = _collect_selective_reject_paths(story_dir) if only_audio_video else _collect_broad_reject_paths(story_dir)
    untouched_visual_count = _count_untouched_visual_paths(story_dir)

    plan = {
        "reason_code": reason_code,
        "only_audio_video": only_audio_video,
        "expected_gender": expected,
        "voice_type": expected,
        "actual_voice": yt_voice,
        "actual_gender": actual_gender,
        "site_audio_untouched": True,
        "telegram_audio_untouched": True,
        "images_untouched": True,
        "frames_untouched": True,
        "untouched_visual_paths_count": untouched_visual_count,
        "reject_dir": str(reject_dir),
        "paths_to_move": [str(p) for p in targets],
        "moved_audio_paths": [],
        "moved_video_paths": [],
    }

    if not execute:
        return {"ok": True, "status": "dry_run", **plan}

    reject_dir.mkdir(parents=True, exist_ok=True)
    for src in targets:
        rel = src.relative_to(story_dir)
        dst = reject_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
            rel_s = str(rel)
            if src.suffix.casefold() in {".mp3", ".wav", ".m4a", ".aac", ".flac"}:
                moved_audio.append(rel_s)
            elif src.suffix.casefold() == ".mp4":
                moved_video.append(rel_s)
        except OSError:
            continue

    reject_report = {
        **plan,
        "rejected_at": _utc_now(),
        "moved_audio_paths": moved_audio,
        "moved_video_paths": moved_video,
        "paths_moved": moved_audio + moved_video,
    }
    marker_name = (
        VOICE_MISMATCH_AUDIO_VIDEO_REJECTED_JSON
        if only_audio_video
        else "VOICE_MISMATCH_REJECTED.json"
    )
    (story_dir / "99_rejected" / marker_name).write_text(
        json.dumps(reject_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest["audio_ready"] = False
    manifest["youtube_ready"] = False
    manifest["runpod_package_ready"] = False
    manifest["audio_rejected"] = True
    manifest["voice_rejected"] = True
    manifest["voice_reject_reason"] = reason_code
    status = dict(manifest.get("pipeline_stage_status") or {})
    status["audio"] = "rejected_voice_mismatch"
    status["tts_kokoro_colab"] = "rejected_voice_mismatch"
    manifest["pipeline_stage_status"] = status
    tts = dict(manifest.get("tts_kokoro_colab") or {})
    tts["status"] = "rejected_voice_mismatch"
    manifest["tts_kokoro_colab"] = tts
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "status": "rejected",
        "moved": len(moved_audio) + len(moved_video),
        **reject_report,
    }


def run_voice_preflight_guard(
    *,
    config: OrchestratorConfig,
    youtube_run_id: str = "",
    site_run_id: str = "",
) -> dict[str, Any]:
    root = config.root_dir.resolve()
    try:
        settings = load_site_tts_settings(root)
        pools_ok = bool(collect_voice_ids_from_pools(settings))
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "can_run_full_corpus": False,
            "reason_code": REASON_VOICE_GUARD_NOT_ENABLED,
            "message": str(exc),
        }

    if not youtube_run_id:
        return {
            "ok": pools_ok,
            "can_run_full_corpus": pools_ok,
            "voice_resolver_available": True,
            "site_voice_mapping_loaded": pools_ok,
            "reason_code": "" if pools_ok else REASON_VOICE_MAPPING_NOT_AVAILABLE,
        }

    audit = run_youtube_voice_audit(
        config=config,
        site_run_id=site_run_id,
        youtube_run_id=youtube_run_id,
        execute=True,
    )
    stale_m_afbella = 0
    unrejected = 0
    for row in audit.get("rows") or []:
        if row.get("verdict") in {"STALE_YOUTUBE_VOICE", REASON_YOUTUBE_TTS_VOICE_MISMATCH}:
            stale_m_afbella += 1
        if row.get("verdict") == VERDICT_BLOCKED:
            continue
        if row.get("verdict") not in {VERDICT_OK}:
            unrejected += 1

    can_run = pools_ok and stale_m_afbella == 0 and unrejected == 0
    reason = ""
    if not pools_ok:
        reason = REASON_VOICE_MAPPING_NOT_AVAILABLE
    elif unrejected > 0 or stale_m_afbella > 0:
        reason = REASON_EXISTING_BAD_AUDIO

    return {
        "ok": can_run,
        "can_run_full_corpus": can_run,
        "voice_resolver_available": True,
        "site_voice_mapping_loaded": pools_ok,
        "stale_voice_rows": stale_m_afbella,
        "unrejected_bad_rows": unrejected,
        "reason_code": reason,
        "audit": audit,
    }
