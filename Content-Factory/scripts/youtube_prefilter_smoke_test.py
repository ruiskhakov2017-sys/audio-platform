from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIX_ROOT = ROOT / "tests" / "fixtures" / "youtube_prefilter"
SITE_RUN_ID = "site-run-fixture-a"
YT_RUN_ID = "yt-fixture-a"


def _write_words(path: Path, count: int, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = " ".join([token] * count)
    path.write_text(text, encoding="utf-8")


def _build_fixture() -> Path:
    cleaned_dir = FIX_ROOT / "cleaned"
    short_path = cleaned_dir / "short_cleaned_story.txt"
    ok_path = cleaned_dir / "ok_cleaned_story.txt"
    long_path = cleaned_dir / "long_cleaned_story.txt"
    _write_words(short_path, 4300, "shortword")
    _write_words(ok_path, 5000, "okword")
    _write_words(long_path, 9100, "longword")

    fixture_deferred = {
        "items": [
            {
                "source_path": str(FIX_ROOT / "raw" / "short_source.txt"),
                "cleaned_path": str(short_path),
                "run_story_dir": str(FIX_ROOT / "run_story" / "short"),
                "canonical_basename": "fixture_short",
            },
            {
                "source_path": str(FIX_ROOT / "raw" / "ok_source.txt"),
                "cleaned_path": str(ok_path),
                "run_story_dir": str(FIX_ROOT / "run_story" / "ok"),
                "canonical_basename": "fixture_ok",
            },
            {
                "source_path": str(FIX_ROOT / "raw" / "long_source.txt"),
                "cleaned_path": str(long_path),
                "run_story_dir": str(FIX_ROOT / "run_story" / "long"),
                "canonical_basename": "fixture_long",
            },
        ]
    }

    deferred_target = ROOT / "runs" / "site" / SITE_RUN_ID / "_phase_a" / "ready_queues" / "deferred.json"
    deferred_target.parent.mkdir(parents=True, exist_ok=True)
    deferred_target.write_text(json.dumps(fixture_deferred, ensure_ascii=False, indent=2), encoding="utf-8")
    (FIX_ROOT / "deferred_fixture.json").write_text(json.dumps(fixture_deferred, ensure_ascii=False, indent=2), encoding="utf-8")
    return deferred_target


def main() -> int:
    deferred_target = _build_fixture()
    cmd = [
        sys.executable,
        "-m",
        "orchestrator",
        "youtube",
        "prefilter-from-site",
        "--site-run-id",
        SITE_RUN_ID,
        "--youtube-run-id",
        YT_RUN_ID,
        "--min-words",
        "4500",
        "--max-words",
        "9000",
        "--force",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)
        return proc.returncode

    result_path = ROOT / "runs" / "youtube" / YT_RUN_ID / "_selection" / "youtube_size_filter.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    by_name = {str(x.get("canonical_basename")): x for x in items}
    checks = [
        ("fixture_short", "no", "too_short"),
        ("fixture_ok", "yes", ""),
        ("fixture_long", "no", "too_long"),
    ]
    for name, expected_status, expected_reason in checks:
        row = by_name.get(name)
        if not row:
            print(f"[FAIL] missing row for {name}")
            return 2
        if row.get("youtube_size_status") != expected_status:
            print(f"[FAIL] {name} status={row.get('youtube_size_status')} expected={expected_status}")
            return 2
        if str(row.get("reject_reason", "")) != expected_reason:
            print(f"[FAIL] {name} reject_reason={row.get('reject_reason')} expected={expected_reason}")
            return 2
        if int(row.get("word_count", 0)) <= 0:
            print(f"[FAIL] {name} word_count not calculated")
            return 2
    print("[OK] youtube prefilter smoke-test passed")
    print(f"deferred_target={deferred_target}")
    print(f"result_json={result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
