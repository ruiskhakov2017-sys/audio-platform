from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


TRACKED_KEYS = [
    "SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_ANON_KEY",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
    "R2_PUBLIC_URL",
    "NEXT_PUBLIC_SITE_URL",
    "NEXT_PUBLIC_APP_URL",
    "NEXT_PUBLIC_API_URL",
    "FRONTEND_URL",
    "BACKEND_URL",
    "API_URL",
]

REQUIRED_FOR_PUBLISH = [
    "SUPABASE_URL",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
    "R2_PUBLIC_URL",
]

TARGET_ENV_KEYS = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_SECRET_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
    "R2_PUBLIC_URL",
    "NEXT_PUBLIC_SITE_URL",
    "NEXT_PUBLIC_APP_URL",
    "NEXT_PUBLIC_API_URL",
    "FRONTEND_URL",
    "BACKEND_URL",
    "API_URL",
]


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = key.strip()
        v = value.strip()
        if not k:
            continue
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _mask_value(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return "<missing>"
    if _is_placeholder(v):
        return "<placeholder>"
    if len(v) <= 12:
        return "<present>"
    return f"{v[:6]}...{v[-6:]}"


def _project_ref_from_supabase_url(url: str) -> str:
    u = (url or "").strip().lower()
    m = re.match(r"^https?://([a-z0-9-]+)\.supabase\.co/?$", u)
    return m.group(1) if m else ""


def _collect_env_files(base: Path) -> list[Path]:
    must_check = [
        base / ".env",
        base / ".env.local",
        base / "backend" / ".env",
        base / "frontend" / ".env",
        base / "frontend" / ".env.local",
    ]
    found: list[Path] = []
    skip_dirs = {".git", "node_modules", ".next", ".venv", "venv", "__pycache__", "dist", "build"}
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.startswith(".env"):
                continue
            found.append((Path(root) / name).resolve())
    found = sorted(found, key=lambda p: str(p).lower())
    merged: list[Path] = []
    seen: set[str] = set()
    for p in [*must_check, *found]:
        s = str(p.resolve())
        if s in seen:
            continue
        seen.add(s)
        merged.append(p.resolve())
    return merged


def _resolve_key(values: dict[str, str], key: str) -> str:
    if key in values:
        raw = values[key].strip()
        if raw and not _is_placeholder(raw):
            return raw
    return ""


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return True
    markers = (
        "...",
        "xxxxx",
        "example",
        "change-me",
        "your-",
        "ваш_",
        "<",
        ">",
    )
    return any(m in v for m in markers)


def _effective_publish_values(values: dict[str, str]) -> dict[str, str]:
    supabase_url = _resolve_key(values, "SUPABASE_URL") or _resolve_key(values, "NEXT_PUBLIC_SUPABASE_URL")
    secret_key = _resolve_key(values, "SUPABASE_SECRET_KEY")
    service_role = _resolve_key(values, "SUPABASE_SERVICE_ROLE_KEY")
    server_side_key = secret_key or service_role
    anon = _resolve_key(values, "SUPABASE_ANON_KEY") or _resolve_key(values, "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    out = dict(values)
    out["SUPABASE_URL"] = supabase_url
    out["SUPABASE_SECRET_KEY"] = secret_key
    out["SUPABASE_SERVICE_ROLE_KEY"] = service_role
    out["SUPABASE_SERVER_SIDE_KEY"] = server_side_key
    out["SUPABASE_SERVER_SIDE_KEY_SOURCE"] = "SUPABASE_SECRET_KEY" if secret_key else ("SUPABASE_SERVICE_ROLE_KEY" if service_role else "")
    out["SUPABASE_ANON_EFFECTIVE"] = anon
    return out


def _aggregate_values(files: list[Path]) -> dict[str, str]:
    agg: dict[str, str] = {}
    for p in files:
        if not p.is_file():
            continue
        envs = _read_env_file(p)
        for k, v in envs.items():
            if k not in agg and v.strip():
                agg[k] = v.strip()
    return agg


def run_site_publish_env_doctor(
    *,
    content_factory_root: Path,
    dirtysecrets_root: Path | None = None,
    write_env_file: bool = True,
    report_path: Path | None = None,
) -> dict[str, Any]:
    cf_root = content_factory_root.resolve()
    ds_root = (dirtysecrets_root or (cf_root.parent / "Dirtysecrets")).resolve()
    report = (
        (report_path if report_path.is_absolute() else (cf_root / report_path)).resolve()
        if report_path
        else (cf_root / ".orchestrator" / "site_publish_env_report.json").resolve()
    )
    target_env_file = (cf_root / ".env.site_publish").resolve()

    ds_files = _collect_env_files(ds_root)
    cf_files = _collect_env_files(cf_root)

    ds_values_raw = _aggregate_values(ds_files)
    cf_values_raw = _aggregate_values(cf_files)
    ds_values = _effective_publish_values(ds_values_raw)
    cf_values = _effective_publish_values(cf_values_raw)

    ds_selected: dict[str, str] = {}
    for k in TARGET_ENV_KEYS:
        val = _resolve_key(ds_values, k)
        if k == "SUPABASE_URL":
            val = _resolve_key(ds_values, "SUPABASE_URL") or _resolve_key(ds_values, "NEXT_PUBLIC_SUPABASE_URL")
        ds_selected[k] = val
    # Legacy publisher expects SUPABASE_SERVICE_ROLE_KEY; mirror secret key when available.
    if ds_selected.get("SUPABASE_SECRET_KEY", "").strip() and not ds_selected.get("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        ds_selected["SUPABASE_SERVICE_ROLE_KEY"] = ds_selected["SUPABASE_SECRET_KEY"]

    missing_for_target = [k for k in REQUIRED_FOR_PUBLISH if not ds_selected.get(k, "").strip()]
    if not (ds_selected.get("SUPABASE_SECRET_KEY", "").strip() or ds_selected.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()):
        missing_for_target.append("SUPABASE_SERVER_SIDE_KEY")

    if write_env_file:
        lines = [
            "# Autogenerated by: python -m orchestrator site-publish env-doctor",
            "# Source of truth: Dirtysecrets env files",
            "# Do not commit secrets.",
            "",
        ]
        for k in TARGET_ENV_KEYS:
            v = ds_selected.get(k, "").strip()
            if v:
                lines.append(f"{k}={v}")
        target_env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    active_values = _effective_publish_values(_aggregate_values([target_env_file, *cf_files]))

    warnings: list[str] = []
    blockers: list[str] = []

    active_supabase = _resolve_key(active_values, "SUPABASE_URL")
    source_supabase = _resolve_key(ds_values, "SUPABASE_URL")
    if active_supabase and source_supabase and active_supabase.strip().lower() != source_supabase.strip().lower():
        blockers.append("supabase_url_mismatch_with_dirtysecrets")

    for req in REQUIRED_FOR_PUBLISH:
        if not _resolve_key(active_values, req):
            blockers.append(f"missing_{req}")

    anon_present = bool(_resolve_key(active_values, "SUPABASE_ANON_EFFECTIVE"))
    server_side_key = _resolve_key(active_values, "SUPABASE_SERVER_SIDE_KEY")
    server_side_key_source = _resolve_key(active_values, "SUPABASE_SERVER_SIDE_KEY_SOURCE")
    server_side_key_present = bool(server_side_key)
    if not server_side_key_present:
        blockers.append("missing SUPABASE_SERVER_SIDE_KEY")
    if anon_present and not server_side_key_present:
        warnings.append("anon_key_present_but_server_side_key_missing")

    anon_value = _resolve_key(active_values, "SUPABASE_ANON_EFFECTIVE")
    server_key_lower = server_side_key.lower()
    anon_lower = anon_value.lower()
    if server_side_key_present and (server_key_lower.startswith("sb_publishable_") or (anon_lower and server_key_lower == anon_lower)):
        blockers.append("server_side_key_is_publishable_or_anon")

    publisher_file = (cf_root / "legacy" / "autopublisher" / "publish_stories.py").resolve()
    publisher_text = publisher_file.read_text(encoding="utf-8", errors="replace") if publisher_file.is_file() else ""
    if 'or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")' in publisher_text:
        warnings.append("publisher_has_anon_fallback_in_code")

    if "localhost" in (active_values.get("NEXT_PUBLIC_SITE_URL", "") or "").lower():
        warnings.append("site_url_points_to_localhost")
    for key in ("NEXT_PUBLIC_APP_URL", "NEXT_PUBLIC_API_URL", "FRONTEND_URL", "BACKEND_URL", "API_URL"):
        v = (active_values.get(key, "") or "").strip().lower()
        if not v:
            continue
        if "localhost" in v or "audio-platform" in v or "vercel.app" in v:
            warnings.append(f"{key}_looks_non_production:{key}")

    masked_active_values = {
        "SUPABASE_URL": _mask_value(_resolve_key(active_values, "SUPABASE_URL")),
        "SUPABASE_PROJECT_REF": _project_ref_from_supabase_url(_resolve_key(active_values, "SUPABASE_URL")) or "<missing>",
        "SUPABASE_SERVICE_ROLE_KEY": _mask_value(_resolve_key(active_values, "SUPABASE_SERVICE_ROLE_KEY")),
        "SUPABASE_SECRET_KEY": _mask_value(_resolve_key(active_values, "SUPABASE_SECRET_KEY")),
        "SUPABASE_SERVER_SIDE_KEY": _mask_value(server_side_key),
        "SUPABASE_ANON_KEY": _mask_value(_resolve_key(active_values, "SUPABASE_ANON_EFFECTIVE")),
        "R2_ACCOUNT_ID": _mask_value(_resolve_key(active_values, "R2_ACCOUNT_ID")),
        "R2_ACCESS_KEY_ID": _mask_value(_resolve_key(active_values, "R2_ACCESS_KEY_ID")),
        "R2_SECRET_ACCESS_KEY": _mask_value(_resolve_key(active_values, "R2_SECRET_ACCESS_KEY")),
        "R2_BUCKET_NAME": _resolve_key(active_values, "R2_BUCKET_NAME") or "<missing>",
        "R2_PUBLIC_URL": _resolve_key(active_values, "R2_PUBLIC_URL") or "<missing>",
        "NEXT_PUBLIC_SITE_URL": _resolve_key(active_values, "NEXT_PUBLIC_SITE_URL") or "<missing>",
        "NEXT_PUBLIC_APP_URL": _resolve_key(active_values, "NEXT_PUBLIC_APP_URL") or "<missing>",
        "NEXT_PUBLIC_API_URL": _resolve_key(active_values, "NEXT_PUBLIC_API_URL") or "<missing>",
        "FRONTEND_URL": _resolve_key(active_values, "FRONTEND_URL") or "<missing>",
        "BACKEND_URL": _resolve_key(active_values, "BACKEND_URL") or "<missing>",
        "API_URL": _resolve_key(active_values, "API_URL") or "<missing>",
    }

    variables_present = sorted([k for k in TRACKED_KEYS if _resolve_key(active_values, k)])
    variables_missing = sorted([k for k in TRACKED_KEYS if not _resolve_key(active_values, k)])

    payload: dict[str, Any] = {
        "ok": len(blockers) == 0,
        "dirtysecrets_project_path": str(ds_root),
        "content_factory_path": str(cf_root),
        "env_files_checked": {
            "dirtysecrets": [str(p) for p in ds_files],
            "content_factory": [str(p) for p in cf_files],
            "generated": [str(target_env_file)] if write_env_file else [],
        },
        "variables_present": variables_present,
        "variables_missing": variables_missing,
        "masked_active_values": masked_active_values,
        "warnings": sorted(set(warnings)),
        "blockers": sorted(set(blockers)),
        "server_side_key_present": server_side_key_present,
        "server_side_key_source": server_side_key_source or "<missing>",
        "recommendation": (
            "ready_for_publish_execute" if len(blockers) == 0 else "fix_blockers_before_publish_execute"
        ),
        "target_env_file": str(target_env_file),
        "source_missing_for_target_env": missing_for_target,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["report_path"] = str(report)
    return payload

