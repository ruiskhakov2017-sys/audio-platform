from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.config import load_config
from orchestrator.youtube_video_runpod_production import _discover_batch_stories


SSH_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ConnectionAttempts=1",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=4",
]

DOWNLOAD_CHUNK_TIMEOUT_SECONDS = 600
DOWNLOAD_CHUNK_STALL_SECONDS = 120
UPLOAD_CHUNK_TIMEOUT_SECONDS = 600


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Remote:
    def __init__(self, *, host: str, port: int, identity: Path) -> None:
        self.host = host
        self.port = int(port)
        self.identity = identity
        self.destination = f"root@{host}"

    def ssh_command(self, command: str) -> list[str]:
        return [
            "ssh",
            "-p",
            str(self.port),
            "-i",
            str(self.identity),
            *SSH_OPTIONS,
            self.destination,
            command,
        ]

    def scp_to_command(self, local: Path, remote: str) -> list[str]:
        return [
            "scp",
            "-P",
            str(self.port),
            "-i",
            str(self.identity),
            *SSH_OPTIONS,
            str(local),
            f"{self.destination}:{remote}",
        ]

    def scp_from_command(self, remote: str, local: Path) -> list[str]:
        return [
            "scp",
            "-P",
            str(self.port),
            "-i",
            str(self.identity),
            *SSH_OPTIONS,
            f"{self.destination}:{remote}",
            str(local),
        ]

    def run(
        self,
        command: str,
        *,
        check: bool = True,
        timeout: int = 120,
        attempts: int = 3,
    ) -> subprocess.CompletedProcess[str]:
        return run_with_retries(
            self.ssh_command(command),
            check=check,
            timeout=timeout,
            attempts=attempts,
        )

    def upload(self, local: Path, remote: str, *, timeout: int = 3600) -> None:
        run_with_retries(
            self.scp_to_command(local, remote),
            check=True,
            timeout=timeout,
            attempts=3,
        )

    def download(self, remote: str, local: Path, *, timeout: int = 7200) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        run_with_retries(
            self.scp_from_command(remote, local),
            check=True,
            timeout=timeout,
            attempts=3,
        )


def run_with_retries(
    command: list[str],
    *,
    check: bool,
    timeout: int,
    attempts: int,
) -> subprocess.CompletedProcess[str]:
    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            last = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            last = subprocess.CompletedProcess(
                command,
                124,
                exc.stdout if isinstance(exc.stdout, str) else "",
                exc.stderr if isinstance(exc.stderr, str) else "",
            )
        if last.returncode == 0:
            return last
        if attempt < attempts:
            print(
                f"TRANSPORT_RETRY attempt={attempt + 1}/{attempts} exit={last.returncode}",
                flush=True,
            )
            time.sleep(3)
    assert last is not None
    if check:
        raise RuntimeError(
            f"command failed exit={last.returncode}: {' '.join(command)}\n"
            f"{(last.stderr or last.stdout or '').strip()}"
        )
    return last


def resolve_launch_dir(launch_id: str) -> Path:
    candidate = PROJECT_ROOT / "Запуски" / launch_id
    if candidate.is_dir():
        return candidate
    for child in PROJECT_ROOT.iterdir():
        nested = child / launch_id
        if child.is_dir() and nested.is_dir():
            return nested
    raise FileNotFoundError(f"launch directory not found: {launch_id}")


def discover_stories(launch_id: str, limit: int) -> list[dict[str, str]]:
    rows = _discover_batch_stories(
        config=load_config(),
        launch_id=launch_id,
        launch_root=None,
        limit=max(0, limit),
    )
    if not rows:
        raise RuntimeError(f"no ready stories found for {launch_id}")
    return rows


def parse_story_filter(value: str) -> set[str]:
    return {item.strip().casefold() for item in value.split(",") if item.strip()}


def story_filter_keys(row: dict[str, str]) -> set[str]:
    return {
        str(row.get("story_id") or "").strip().casefold(),
        str(row.get("story_slug") or "").strip().casefold(),
    }


def filter_stories(rows: list[dict[str, str]], only_stories: str) -> list[dict[str, str]]:
    story_filter = parse_story_filter(only_stories)
    if not story_filter:
        return rows
    matched: list[dict[str, str]] = []
    missing = set(story_filter)
    for row in rows:
        keys = story_filter_keys(row)
        if keys & story_filter:
            matched.append(row)
            missing -= keys
    if missing:
        raise RuntimeError(f"--only-stories did not match: {', '.join(sorted(missing))}")
    return matched


def remote_layout(remote_workdir: str, launch_id: str) -> dict[str, str]:
    root = remote_workdir.rstrip("/")
    launch = f"{root}/launches/{launch_id}"
    return {
        "runtime_root": root,
        "runpod_dir": f"{root}/runpod",
        "launch_root": launch,
        "batch_dir": f"{launch}/batch",
        "manifest": f"{launch}/batch/manifest.json",
        "state": f"{launch}/batch/state.json",
        "stdout": f"{launch}/batch/stdout.log",
        "pid": f"{launch}/batch/batch.pid",
        "stop": f"{launch}/batch/STOP",
    }


def write_manifest(
    *,
    launch_dir: Path,
    launch_id: str,
    stories: list[dict[str, str]],
    layout: dict[str, str],
) -> Path:
    manifest = {
        "schema_version": 1,
        "launch_id": launch_id,
        "created_at": utc_now(),
        "runtime_root": layout["runtime_root"],
        "remote_launch_root": layout["launch_root"],
        "stories": [
            {
                "story_id": row["story_id"],
                "story_slug": row["story_slug"],
            }
            for row in stories
        ],
    }
    path = launch_dir / "07_reports" / "runpod_autonomous_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def deploy_runtime(remote: Remote, layout: dict[str, str], manifest_path: Path) -> None:
    remote.run(
        f"mkdir -p {shlex.quote(layout['runpod_dir'])} {shlex.quote(layout['batch_dir'])}",
        timeout=60,
    )
    files = [
        PROJECT_ROOT / "runpod" / "__init__.py",
        PROJECT_ROOT / "runpod" / "film_effects.py",
        PROJECT_ROOT / "runpod" / "youtube_video_final_render.py",
        PROJECT_ROOT / "runpod" / "youtube_video_batch_runner.py",
    ]
    for path in files:
        print(f"RUNTIME_UPLOAD file={path.name}", flush=True)
        remote.upload(path, f"{layout['runpod_dir']}/{path.name}", timeout=300)
    remote.upload(manifest_path, layout["manifest"], timeout=300)


def start_remote_batch(remote: Remote, layout: dict[str, str], *, workers: int) -> str:
    command = (
        f"if [ -f {shlex.quote(layout['pid'])} ] && "
        f"kill -0 \"$(cat {shlex.quote(layout['pid'])})\" 2>/dev/null; then "
        f"echo ALREADY_RUNNING pid=$(cat {shlex.quote(layout['pid'])}); "
        "else "
        f"rm -f {shlex.quote(layout['stop'])}; "
        f"cd {shlex.quote(layout['runtime_root'])}; "
        f"nohup python -m runpod.youtube_video_batch_runner "
        f"--manifest {shlex.quote(layout['manifest'])} "
        f"--workers {max(1, int(workers))} "
        f"> {shlex.quote(layout['stdout'])} 2>&1 < /dev/null & "
        "pid=$!; "
        f"echo \"$pid\" > {shlex.quote(layout['pid'])}; "
        "echo STARTED pid=$pid; "
        "fi"
    )
    proc = remote.run(command, timeout=60)
    output = (proc.stdout or "").strip()
    print(f"REMOTE_BATCH_{output}", flush=True)
    return output


def remote_package_valid(remote: Remote, story_root: str) -> bool:
    input_dir = f"{story_root}/runpod_input"
    command = (
        f"test -s {shlex.quote(input_dir + '/READY_FOR_RUNPOD.json')} && "
        f"test -s {shlex.quote(input_dir + '/audio/narration.mp3')} && "
        f"test -s {shlex.quote(input_dir + '/timeline.json')} && "
        f"test -s {shlex.quote(input_dir + '/segment_plan.json')} && "
        f"test -s {shlex.quote(input_dir + '/render_settings.json')}"
    )
    return remote.run(command, check=False, timeout=45, attempts=2).returncode == 0


def create_package_tar(package_dir: Path, output: Path) -> tuple[int, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    files = [path for path in package_dir.rglob("*") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    with tarfile.open(output, "w") as archive:
        archive.add(package_dir, arcname="runpod_input")
    return len(files), total_bytes


def _remote_size_matches(remote: Remote, remote_path: str, expected_bytes: int) -> bool:
    proc = remote.run(
        (
            f"test -f {shlex.quote(remote_path)} && "
            f"test \"$(stat -c %s {shlex.quote(remote_path)} 2>/dev/null)\" = {int(expected_bytes)}"
        ),
        check=False,
        timeout=45,
        attempts=1,
    )
    return proc.returncode == 0


def _write_local_part(source: Path, part: Path, *, offset: int, size: int) -> None:
    if part.is_file() and part.stat().st_size == size:
        return
    part.parent.mkdir(parents=True, exist_ok=True)
    temp = part.with_name(part.name + ".tmp")
    with source.open("rb") as handle, temp.open("wb") as output:
        handle.seek(offset)
        remaining = size
        while remaining > 0:
            block = handle.read(min(8 * 1024 * 1024, remaining))
            if not block:
                break
            output.write(block)
            remaining -= len(block)
    if temp.stat().st_size != size:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"local part size mismatch: {part.name}")
    temp.replace(part)


def upload_file_chunked(
    *,
    remote: Remote,
    local_path: Path,
    remote_path: str,
    local_parts_dir: Path,
    remote_parts_dir: str,
    story_slug: str,
    chunk_size_mb: int,
) -> None:
    size_bytes = local_path.stat().st_size
    chunk_bytes = max(8, int(chunk_size_mb)) * 1048576
    total_parts = max(1, math.ceil(size_bytes / chunk_bytes))
    remote.run(
        f"mkdir -p {shlex.quote(remote_parts_dir)}",
        timeout=60,
    )
    print(
        f"UPLOAD_CHUNKED story={story_slug} size_mb={size_bytes / 1048576:.1f} "
        f"chunks={total_parts} chunk_mb={max(8, int(chunk_size_mb))}",
        flush=True,
    )
    for index in range(total_parts):
        offset = index * chunk_bytes
        expected = min(chunk_bytes, size_bytes - offset)
        part = local_parts_dir / f"chunk_{index:05d}.part"
        remote_part = f"{remote_parts_dir}/chunk_{index:05d}.part"
        if _remote_size_matches(remote, remote_part, expected):
            print(f"UPLOAD_CHUNK_SKIPPED story={story_slug} chunk={index + 1}/{total_parts}", flush=True)
            continue
        _write_local_part(local_path, part, offset=offset, size=expected)
        chunk_ok = False
        last_error = ""
        for attempt in range(1, 4):
            try:
                upload_timeout = max(120, min(UPLOAD_CHUNK_TIMEOUT_SECONDS, int(chunk_size_mb) * 8))
                remote.upload(part, remote_part, timeout=upload_timeout)
                if _remote_size_matches(remote, remote_part, expected):
                    chunk_ok = True
                    break
                last_error = "remote chunk size mismatch"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                remote.run(f"rm -f {shlex.quote(remote_part)}", check=False, timeout=60, attempts=1)
                time.sleep(3)
        if not chunk_ok:
            raise RuntimeError(
                f"{last_error}: {story_slug} chunk {index + 1}/{total_parts}"
            )
        print(f"UPLOAD_CHUNK_DONE story={story_slug} chunk={index + 1}/{total_parts}", flush=True)

    temp_remote = f"{remote_path}.tmp"
    assemble = (
        f"rm -f {shlex.quote(temp_remote)}; "
        f"cat {shlex.quote(remote_parts_dir)}/chunk_*.part > {shlex.quote(temp_remote)}; "
        f"test \"$(stat -c %s {shlex.quote(temp_remote)} 2>/dev/null)\" = {int(size_bytes)}; "
        f"mv {shlex.quote(temp_remote)} {shlex.quote(remote_path)}; "
        f"rm -rf {shlex.quote(remote_parts_dir)}"
    )
    remote.run(assemble, timeout=900)


def mark_upload_failed(remote: Remote, story_root: str, error: str) -> None:
    payload = json.dumps({"error": error, "at": utc_now()}, ensure_ascii=True)
    command = (
        f"mkdir -p {shlex.quote(story_root)}; "
        f"printf %s {shlex.quote(payload)} > {shlex.quote(story_root + '/UPLOAD_FAILED.json')}"
    )
    remote.run(command, check=False, timeout=60, attempts=2)


def clean_upload_staging(remote: Remote, story_root: str) -> None:
    command = (
        f"rm -rf {shlex.quote(story_root + '/.upload_parts')} "
        f"{shlex.quote(story_root + '/.incoming')} "
        f"{shlex.quote(story_root + '/runpod_input.upload.tar')} "
        f"{shlex.quote(story_root + '/runpod_input.upload.tar.tmp')} "
        f"{shlex.quote(story_root + '/UPLOAD_FAILED.json')}"
    )
    remote.run(command, check=False, timeout=120, attempts=2)


def upload_story(
    *,
    remote: Remote,
    layout: dict[str, str],
    row: dict[str, str],
    temp_root: Path,
    index: int,
    total: int,
    upload_chunk_size_mb: int,
) -> tuple[str, str]:
    story_slug = row["story_slug"]
    story_root = f"{layout['launch_root']}/{story_slug}"
    if remote_package_valid(remote, story_root):
        remote.run(
            f"rm -f {shlex.quote(story_root + '/UPLOAD_FAILED.json')}",
            check=False,
            timeout=60,
            attempts=1,
        )
        print(f"UPLOAD_SKIPPED_REMOTE_OK story={story_slug} index={index}/{total}", flush=True)
        return story_slug, "skipped"

    package_dir = Path(row["package_dir"]) / "07_runpod_package" / "runpod_input"
    archive_path = temp_root / f"{story_slug}.tar"
    local_parts_dir = temp_root / f"{story_slug}.upload_parts"
    print(f"UPLOAD_PACKAGING story={story_slug} index={index}/{total}", flush=True)
    file_count, total_bytes = create_package_tar(package_dir, archive_path)
    print(
        f"UPLOAD_STARTED story={story_slug} files={file_count} "
        f"size_mb={total_bytes / 1048576:.1f} index={index}/{total}",
        flush=True,
    )
    remote.run(f"mkdir -p {shlex.quote(story_root)}", timeout=60)
    clean_upload_staging(remote, story_root)
    remote_archive = f"{story_root}/runpod_input.upload.tar"
    try:
        upload_file_chunked(
            remote=remote,
            local_path=archive_path,
            remote_path=remote_archive,
            local_parts_dir=local_parts_dir,
            remote_parts_dir=f"{story_root}/.upload_parts",
            story_slug=story_slug,
            chunk_size_mb=upload_chunk_size_mb,
        )
        incoming = f"{story_root}/.incoming"
        extract = (
            f"rm -rf {shlex.quote(incoming)}; "
            f"mkdir -p {shlex.quote(incoming)}; "
            f"tar -xf {shlex.quote(remote_archive)} -C {shlex.quote(incoming)}; "
            f"test -s {shlex.quote(incoming + '/runpod_input/READY_FOR_RUNPOD.json')}; "
            f"rm -rf {shlex.quote(story_root + '/runpod_input')}; "
            f"mv {shlex.quote(incoming + '/runpod_input')} {shlex.quote(story_root + '/runpod_input')}; "
            f"rm -rf {shlex.quote(incoming)} {shlex.quote(remote_archive)} "
            f"{shlex.quote(story_root + '/UPLOAD_FAILED.json')}"
        )
        remote.run(extract, timeout=900)
    finally:
        archive_path.unlink(missing_ok=True)
        if local_parts_dir.is_dir():
            for part in local_parts_dir.glob("chunk_*.part"):
                part.unlink(missing_ok=True)
            local_parts_dir.rmdir()
    print(f"UPLOAD_DONE story={story_slug} index={index}/{total}", flush=True)
    return story_slug, "uploaded"


def action_start(args: argparse.Namespace, remote: Remote) -> int:
    launch_dir = resolve_launch_dir(args.launch_id)
    discovered = filter_stories(discover_stories(args.launch_id, args.limit), str(args.only_stories or ""))
    stories: list[dict[str, str]] = []
    for row in discovered:
        local_final = Path(row["package_dir"]) / "08_video" / "final" / "final.mp4"
        ok, _ = validate_download(local_final)
        if ok:
            print(f"START_SKIP_LOCAL_FINAL story={row['story_slug']} path={local_final}", flush=True)
            continue
        stories.append(row)
    if not stories:
        print("AUTONOMOUS_BATCH_NO_STORIES all local finals already valid", flush=True)
        return 0
    layout = remote_layout(args.remote_workdir, args.launch_id)
    manifest_path = write_manifest(
        launch_dir=launch_dir,
        launch_id=args.launch_id,
        stories=stories,
        layout=layout,
    )
    print(f"AUTONOMOUS_BATCH_PREPARED stories={len(stories)}", flush=True)
    deploy_runtime(remote, layout, manifest_path)
    start_remote_batch(remote, layout, workers=args.workers)
    remote.run(
        f"rm -f {shlex.quote(layout['batch_dir'] + '/UPLOADS_COMPLETE')}",
        check=False,
        timeout=60,
        attempts=2,
    )

    temp_root = launch_dir / "10_Временные_файлы" / "runpod_autonomous_upload"
    temp_root.mkdir(parents=True, exist_ok=True)
    uploaded = 0
    skipped = 0
    failed = 0
    for index, row in enumerate(stories, start=1):
        try:
            _, status = upload_story(
                remote=remote,
                layout=layout,
                row=row,
                temp_root=temp_root,
                index=index,
                total=len(stories),
                upload_chunk_size_mb=max(8, int(args.upload_chunk_size_mb)),
            )
            if status == "uploaded":
                uploaded += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            error = f"{type(exc).__name__}: {exc}"
            print(f"UPLOAD_FAILED story={row['story_slug']} error={error}", flush=True)
            mark_upload_failed(remote, f"{layout['launch_root']}/{row['story_slug']}", error)
    remote.run(f"touch {shlex.quote(layout['batch_dir'] + '/UPLOADS_COMPLETE')}", check=False, timeout=60)
    print(
        f"AUTONOMOUS_BATCH_DEPLOYED total={len(stories)} uploaded={uploaded} "
        f"skipped={skipped} upload_failed={failed}",
        flush=True,
    )
    print("PowerShell can now be closed; rendering continues on RunPod.", flush=True)
    return 0 if failed == 0 else 1


def action_upload_only(args: argparse.Namespace, remote: Remote) -> int:
    launch_dir = resolve_launch_dir(args.launch_id)
    stories = filter_stories(discover_stories(args.launch_id, args.limit), str(args.only_stories or ""))
    if not stories:
        print("UPLOAD_ONLY_NO_STORIES", flush=True)
        return 0
    layout = remote_layout(args.remote_workdir, args.launch_id)
    remote.run(f"mkdir -p {shlex.quote(layout['launch_root'])}", timeout=60)
    temp_root = launch_dir / "10_Временные_файлы" / "runpod_autonomous_upload"
    temp_root.mkdir(parents=True, exist_ok=True)
    uploaded = 0
    skipped = 0
    failed = 0
    for index, row in enumerate(stories, start=1):
        try:
            _, status = upload_story(
                remote=remote,
                layout=layout,
                row=row,
                temp_root=temp_root,
                index=index,
                total=len(stories),
                upload_chunk_size_mb=max(8, int(args.upload_chunk_size_mb)),
            )
            if status == "uploaded":
                uploaded += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            error = f"{type(exc).__name__}: {exc}"
            print(f"UPLOAD_ONLY_FAILED story={row['story_slug']} error={error}", flush=True)
            mark_upload_failed(remote, f"{layout['launch_root']}/{row['story_slug']}", error)
    print(
        f"UPLOAD_ONLY_DONE total={len(stories)} uploaded={uploaded} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 0 if failed == 0 else 1


def action_status(args: argparse.Namespace, remote: Remote) -> int:
    layout = remote_layout(args.remote_workdir, args.launch_id)
    command = (
        f"echo '=== STATE ==='; "
        f"cat {shlex.quote(layout['state'])} 2>/dev/null || echo STATE_NOT_READY; "
        f"echo '=== LAST LOG ==='; "
        f"tail -n {max(10, args.tail)} {shlex.quote(layout['stdout'])} 2>/dev/null || true"
    )
    proc = remote.run(command, check=False, timeout=60, attempts=3)
    print(proc.stdout or proc.stderr or "No remote status.", flush=True)
    return 0 if proc.returncode == 0 else 1


def action_stop(args: argparse.Namespace, remote: Remote) -> int:
    layout = remote_layout(args.remote_workdir, args.launch_id)
    command = (
        f"touch {shlex.quote(layout['stop'])}; "
        f"if [ -f {shlex.quote(layout['pid'])} ]; then "
        f"pid=$(cat {shlex.quote(layout['pid'])}); "
        "pkill -TERM -P \"$pid\" 2>/dev/null || true; "
        "kill -TERM \"$pid\" 2>/dev/null || true; "
        "for i in $(seq 1 15); do kill -0 \"$pid\" 2>/dev/null || break; sleep 1; done; "
        "if kill -0 \"$pid\" 2>/dev/null; then "
        "pkill -KILL -P \"$pid\" 2>/dev/null || true; kill -KILL \"$pid\" 2>/dev/null || true; "
        "fi; "
        "echo STOPPED pid=$pid; "
        "else echo NOT_RUNNING; fi"
    )
    proc = remote.run(command, check=False, timeout=45, attempts=2)
    print((proc.stdout or proc.stderr or "").strip(), flush=True)
    return 0


def load_remote_state(remote: Remote, layout: dict[str, str]) -> dict[str, Any]:
    proc = remote.run(f"cat {shlex.quote(layout['state'])}", timeout=60)
    payload = json.loads(proc.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("invalid remote batch state")
    return payload


def remote_batch_running(remote: Remote, layout: dict[str, str]) -> bool:
    command = (
        f"if [ -f {shlex.quote(layout['pid'])} ]; then "
        f"pid=$(cat {shlex.quote(layout['pid'])}); "
        "kill -0 \"$pid\" 2>/dev/null; "
        "else exit 1; fi"
    )
    return remote.run(command, check=False, timeout=30, attempts=1).returncode == 0


def list_remote_completed_slugs(remote: Remote, layout: dict[str, str]) -> list[str]:
    command = (
        f"for d in {shlex.quote(layout['launch_root'])}/*; do "
        "[ -d \"$d\" ] || continue; "
        "[ -s \"$d/runpod_output/final/final.mp4\" ] || continue; "
        "[ -s \"$d/runpod_output/status/RUNPOD_RENDER_DONE.json\" ] || continue; "
        "basename \"$d\"; "
        "done"
    )
    proc = remote.run(command, check=False, timeout=60, attempts=2)
    rows = []
    for line in (proc.stdout or "").splitlines():
        slug = line.strip()
        if slug and slug not in rows:
            rows.append(slug)
    return rows


def cleanup_remote_story_after_download(remote: Remote, layout: dict[str, str], story_slug: str) -> None:
    story_root = f"{layout['launch_root']}/{story_slug}"
    command = (
        f"rm -rf {shlex.quote(story_root + '/runpod_input')} "
        f"{shlex.quote(story_root + '/runpod_output')} "
        f"{shlex.quote(story_root + '/UPLOAD_FAILED.json')} "
        f"{shlex.quote(story_root + '/.upload_parts')} "
        f"{shlex.quote(story_root + '/.incoming')} "
        f"{shlex.quote(story_root + '/runpod_input.upload.tar')} "
        f"{shlex.quote(story_root + '/runpod_input.upload.tar.tmp')}"
    )
    remote.run(command, check=False, timeout=120, attempts=2)


def upload_rclone_config(remote: Remote, layout: dict[str, str], config_path: Path) -> str:
    if not config_path.is_file():
        raise FileNotFoundError(f"rclone config missing: {config_path}")
    remote_dir = f"{layout['runtime_root']}/rclone"
    remote_config = f"{remote_dir}/rclone.conf"
    remote.run(f"mkdir -p {shlex.quote(remote_dir)} && chmod 700 {shlex.quote(remote_dir)}", timeout=60)
    remote.upload(config_path, remote_config, timeout=300)
    remote.run(f"chmod 600 {shlex.quote(remote_config)}", check=False, timeout=60, attempts=1)
    return remote_config


def ensure_remote_rclone(remote: Remote) -> None:
    command = (
        "if command -v rclone >/dev/null 2>&1; then "
        "rclone version | head -1; "
        "else "
        "echo RCLONE_INSTALL_STARTED; "
        "if ! command -v curl >/dev/null 2>&1; then "
        "apt-get update -y && apt-get install -y --no-install-recommends curl ca-certificates unzip; "
        "fi; "
        "curl -fsSL https://rclone.org/install.sh | bash; "
        "rclone version | head -1; "
        "fi"
    )
    proc = remote.run(command, timeout=900, attempts=1)
    print((proc.stdout or proc.stderr or "").strip(), flush=True)


def write_drive_uploader_script(
    *,
    remote: Remote,
    layout: dict[str, str],
    remote_config: str,
    drive_remote: str,
    drive_path: str,
    delete_remote_after_upload: bool,
    clean_intermediates_after_upload: bool,
    watch: bool,
    poll_seconds: int,
    drive_chunk_size: str,
    drive_parallel_uploads: int,
) -> str:
    script_path = f"{layout['batch_dir']}/drive_upload.sh"
    drive_path = drive_path.strip("/\\")
    delete_flag = "1" if delete_remote_after_upload else "0"
    clean_intermediates_flag = "1" if clean_intermediates_after_upload else "0"
    watch_flag = "1" if watch else "0"
    parallel_uploads = max(1, int(drive_parallel_uploads))
    drive_chunk_size = drive_chunk_size.strip() or "256M"
    script = f"""#!/usr/bin/env bash
set -u

export RCLONE_CONFIG={shlex.quote(remote_config)}
LAUNCH_ROOT={shlex.quote(layout['launch_root'])}
BATCH_PID={shlex.quote(layout['pid'])}
DRIVE_REMOTE={shlex.quote(drive_remote)}
DRIVE_PATH={shlex.quote(drive_path)}
DELETE_AFTER={delete_flag}
CLEAN_INTERMEDIATES_AFTER={clean_intermediates_flag}
WATCH={watch_flag}
POLL_SECONDS={max(10, int(poll_seconds))}
DRIVE_CHUNK_SIZE={shlex.quote(drive_chunk_size)}
PARALLEL_UPLOADS={parallel_uploads}

json_size() {{
  python3 -c 'import json,sys; data=json.load(sys.stdin); item=data[0] if isinstance(data,list) and data else data; print(int(item.get("Size", 0)) if isinstance(item, dict) else 0)' 2>/dev/null || echo 0
}}

drive_dest() {{
  slug="$1"
  printf '%s:%s/%s.mp4' "$DRIVE_REMOTE" "$DRIVE_PATH" "$slug"
}}

prepare_drive_root() {{
  rclone mkdir "${{DRIVE_REMOTE}}:${{DRIVE_PATH}}" \\
    --retries 8 \\
    --low-level-retries 20 >/dev/null
}}

clean_intermediates_keep_final() {{
  story_root="$1"
  rm -rf "$story_root/runpod_input" \\
    "$story_root/.upload_parts" \\
    "$story_root/.incoming" \\
    "$story_root/runpod_input.upload.tar" \\
    "$story_root/runpod_input.upload.tar.tmp" \\
    "$story_root/UPLOAD_FAILED.json"

  if [ -d "$story_root/runpod_output" ]; then
    find "$story_root/runpod_output" -mindepth 1 -maxdepth 1 \\
      ! -name final \\
      ! -name status \\
      -exec rm -rf {{}} + 2>/dev/null || true
  fi
  if [ -d "$story_root/runpod_output/final" ]; then
    find "$story_root/runpod_output/final" -mindepth 1 -maxdepth 1 \\
      ! -name final.mp4 \\
      -exec rm -rf {{}} + 2>/dev/null || true
  fi
}}

upload_one() {{
  story_root="$1"
  [ -d "$story_root" ] || return 0
  slug="$(basename "$story_root")"
  final="$story_root/runpod_output/final/final.mp4"
  done_marker="$story_root/DRIVE_UPLOAD_DONE.json"
  fail_marker="$story_root/DRIVE_UPLOAD_FAILED.txt"
  [ -s "$final" ] || return 0

  size="$(stat -c %s "$final" 2>/dev/null || echo 0)"
  [ "$size" -gt 10485760 ] || return 0
  dest="$(drive_dest "$slug")"

  if [ -s "$done_marker" ] && grep -F '"drive_path":"'"$dest"'"' "$done_marker" >/dev/null 2>&1; then
    if [ "$DELETE_AFTER" = "1" ]; then
      rm -rf "$story_root/runpod_input" "$story_root/runpod_output" "$story_root/.upload_parts" "$story_root/.incoming" "$story_root/runpod_input.upload.tar" "$story_root/runpod_input.upload.tar.tmp" "$story_root/UPLOAD_FAILED.json"
      echo "REMOTE_CLEANED_AFTER_DRIVE story=${{slug}}" >&1
    elif [ "$CLEAN_INTERMEDIATES_AFTER" = "1" ]; then
      clean_intermediates_keep_final "$story_root"
      echo "REMOTE_INTERMEDIATES_CLEANED_AFTER_DRIVE story=${{slug}} kept=final.mp4" >&1
    fi
    return 0
  fi

  remote_size="$(rclone lsjson "$dest" 2>/dev/null | json_size)"
  if [ "$remote_size" = "$size" ]; then
    rm -f "$fail_marker"
    printf '{{"story_slug":"%s","drive_path":"%s","size_bytes":%s,"uploaded_at":"%s"}}\\n' "$slug" "$dest" "$size" "$(date -Iseconds)" > "$done_marker"
    echo "DRIVE_UPLOAD_ALREADY_ON_DRIVE story=${{slug}} size_bytes=${{size}}" >&1
    if [ "$DELETE_AFTER" = "1" ]; then
      rm -rf "$story_root/runpod_input" "$story_root/runpod_output" "$story_root/.upload_parts" "$story_root/.incoming" "$story_root/runpod_input.upload.tar" "$story_root/runpod_input.upload.tar.tmp" "$story_root/UPLOAD_FAILED.json"
      echo "REMOTE_CLEANED_AFTER_DRIVE story=${{slug}}" >&1
    elif [ "$CLEAN_INTERMEDIATES_AFTER" = "1" ]; then
      clean_intermediates_keep_final "$story_root"
      echo "REMOTE_INTERMEDIATES_CLEANED_AFTER_DRIVE story=${{slug}} kept=final.mp4" >&1
    fi
    return 0
  fi

  echo "DRIVE_UPLOAD_STARTED story=${{slug}} size_mb=$((size / 1048576)) dest=${{dest}}" >&1

  if rclone copyto "$final" "$dest" \\
      --drive-chunk-size "$DRIVE_CHUNK_SIZE" \\
      --transfers 1 \\
      --checkers 4 \\
      --retries 8 \\
      --low-level-retries 20 \\
      --stats 30s \\
      --stats-one-line; then
    remote_size="$(rclone lsjson "$dest" 2>/dev/null | json_size)"
    if [ "$remote_size" = "$size" ]; then
      rm -f "$fail_marker"
      printf '{{"story_slug":"%s","drive_path":"%s","size_bytes":%s,"uploaded_at":"%s"}}\\n' "$slug" "$dest" "$size" "$(date -Iseconds)" > "$done_marker"
      echo "DRIVE_UPLOAD_DONE story=${{slug}} size_bytes=${{size}}" >&1
      if [ "$DELETE_AFTER" = "1" ]; then
        rm -rf "$story_root/runpod_input" "$story_root/runpod_output" "$story_root/.upload_parts" "$story_root/.incoming" "$story_root/runpod_input.upload.tar" "$story_root/runpod_input.upload.tar.tmp" "$story_root/UPLOAD_FAILED.json"
        echo "REMOTE_CLEANED_AFTER_DRIVE story=${{slug}}" >&1
      elif [ "$CLEAN_INTERMEDIATES_AFTER" = "1" ]; then
        clean_intermediates_keep_final "$story_root"
        echo "REMOTE_INTERMEDIATES_CLEANED_AFTER_DRIVE story=${{slug}} kept=final.mp4" >&1
      fi
    else
      echo "drive size mismatch local=$size remote=$remote_size dest=$dest" > "$fail_marker"
      echo "DRIVE_UPLOAD_FAILED story=${{slug}} error=size_mismatch local=${{size}} remote=${{remote_size}}" >&1
    fi
  else
    code="$?"
    echo "rclone copy failed exit=$code dest=$dest" > "$fail_marker"
    echo "DRIVE_UPLOAD_FAILED story=${{slug}} exit=${{code}}" >&1
  fi
}}

launch_name="$(basename "$LAUNCH_ROOT")"
echo "DRIVE_UPLOAD_LOOP_STARTED launch=${{launch_name}} watch=$WATCH delete_after=$DELETE_AFTER clean_intermediates_after=$CLEAN_INTERMEDIATES_AFTER drive=${{DRIVE_REMOTE}}:${{DRIVE_PATH}}" >&1
if prepare_drive_root; then
  echo "DRIVE_UPLOAD_ROOT_READY drive=${{DRIVE_REMOTE}}:${{DRIVE_PATH}}" >&1
else
  echo "DRIVE_UPLOAD_ROOT_FAILED drive=${{DRIVE_REMOTE}}:${{DRIVE_PATH}}" >&1
  exit 1
fi
while true; do
  active=0
  for story_root in "$LAUNCH_ROOT"/*; do
    upload_one "$story_root" &
    active=$((active + 1))
    if [ "$active" -ge "$PARALLEL_UPLOADS" ]; then
      wait -n 2>/dev/null || true
      active=$((active - 1))
    fi
  done
  wait 2>/dev/null || true

  if [ "$WATCH" != "1" ]; then
    break
  fi
  if [ -f "$BATCH_PID" ] && kill -0 "$(cat "$BATCH_PID" 2>/dev/null)" 2>/dev/null; then
    echo "DRIVE_UPLOAD_SLEEP seconds=$POLL_SECONDS running=true" >&1
    sleep "$POLL_SECONDS"
  else
    echo "DRIVE_UPLOAD_FINAL_PASS running=false" >&1
    WATCH=0
  fi
done
echo "DRIVE_UPLOAD_LOOP_FINISHED uploaded=$(find "$LAUNCH_ROOT" -name DRIVE_UPLOAD_DONE.json -type f 2>/dev/null | wc -l)" >&1
"""
    temp_root = PROJECT_ROOT / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        suffix=".sh",
        dir=temp_root,
    ) as handle:
        local_script = Path(handle.name)
        handle.write(script)
    try:
        remote.run(f"mkdir -p {shlex.quote(layout['batch_dir'])}", timeout=60)
        remote.upload(local_script, script_path, timeout=300)
        remote.run(f"chmod +x {shlex.quote(script_path)}", check=False, timeout=60, attempts=1)
    finally:
        local_script.unlink(missing_ok=True)
    return script_path


def action_drive_upload(args: argparse.Namespace, remote: Remote, *, watch: bool) -> int:
    layout = remote_layout(args.remote_workdir, args.launch_id)
    log_path = f"{layout['batch_dir']}/drive_upload.log"
    pid_path = f"{layout['batch_dir']}/drive_upload.pid"
    check_command = (
        f"if [ -s {shlex.quote(pid_path)} ]; then "
        f"pid=$(cat {shlex.quote(pid_path)} 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
        f"echo DRIVE_UPLOAD_ALREADY_RUNNING pid=$pid log={shlex.quote(log_path)}; "
        "exit 0; "
        "fi; "
        "fi; "
        "exit 1"
    )
    existing = remote.run(check_command, check=False, timeout=20, attempts=1)
    if existing.returncode == 0:
        print((existing.stdout or "").strip(), flush=True)
        return 0
    if not args.drive_remote:
        raise RuntimeError("--drive-remote is required")
    if not args.drive_path:
        raise RuntimeError("--drive-path is required")
    if not args.rclone_config:
        raise RuntimeError("--rclone-config is required for starting Drive upload")
    remote_config = upload_rclone_config(remote, layout, Path(args.rclone_config).expanduser().resolve())
    ensure_remote_rclone(remote)
    script_path = write_drive_uploader_script(
        remote=remote,
        layout=layout,
        remote_config=remote_config,
        drive_remote=args.drive_remote,
        drive_path=args.drive_path,
        delete_remote_after_upload=bool(args.delete_remote_after_drive_upload),
        clean_intermediates_after_upload=bool(args.clean_remote_intermediates_after_drive_upload),
        watch=watch,
        poll_seconds=int(args.poll_seconds),
        drive_chunk_size=str(args.drive_chunk_size),
        drive_parallel_uploads=int(args.drive_parallel_uploads),
    )
    command = (
        f"nohup bash {shlex.quote(script_path)} >> {shlex.quote(log_path)} 2>&1 < /dev/null & "
        f"echo $! > {shlex.quote(pid_path)}; "
        f"echo DRIVE_UPLOAD_REMOTE_STARTED pid=$(cat {shlex.quote(pid_path)}) log={shlex.quote(log_path)}"
    )
    proc = remote.run(command, timeout=60, attempts=1)
    print((proc.stdout or "").strip(), flush=True)
    return 0


def action_drive_stop(args: argparse.Namespace, remote: Remote) -> int:
    layout = remote_layout(args.remote_workdir, args.launch_id)
    pid_path = f"{layout['batch_dir']}/drive_upload.pid"
    command = (
        f"if [ -s {shlex.quote(pid_path)} ]; then "
        f"pid=$(cat {shlex.quote(pid_path)} 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
        "kill \"$pid\" 2>/dev/null || true; "
        "echo DRIVE_UPLOAD_STOPPED pid=$pid; "
        "fi; "
        "fi; "
        "pkill -TERM -f '[d]rive_upload.sh' 2>/dev/null || true; "
        f"pkill -TERM -f '[r]clone .*{shlex.quote(args.launch_id)}' 2>/dev/null || true; "
        f"rm -f {shlex.quote(pid_path)}; "
        "echo DRIVE_UPLOAD_STOP_DONE"
    )
    proc = remote.run(command, check=False, timeout=60, attempts=2)
    print(proc.stdout or proc.stderr or "DRIVE_UPLOAD_STOP_DONE", flush=True)
    return 0 if proc.returncode == 0 else 1


def action_drive_status(args: argparse.Namespace, remote: Remote) -> int:
    layout = remote_layout(args.remote_workdir, args.launch_id)
    manifest_py = json.dumps(layout["manifest"])
    state_py = json.dumps(layout["state"])
    command = (
        f"echo '=== DRIVE UPLOAD STATUS ==='; "
        f"if [ -f {shlex.quote(layout['batch_dir'] + '/drive_upload.pid')} ]; then "
        f"pid=$(cat {shlex.quote(layout['batch_dir'] + '/drive_upload.pid')}); "
        "if kill -0 \"$pid\" 2>/dev/null; then echo running pid=$pid; else echo stopped pid=$pid; fi; "
        "else echo pid_missing; fi; "
        f"echo manifest_total=$(python3 -c 'import json; print(len(json.load(open({manifest_py}, encoding=\"utf-8\")).get(\"stories\", [])))' 2>/dev/null || echo 0); "
        f"echo final_ready=$(find {shlex.quote(layout['launch_root'])} -path '*/runpod_output/final/final.mp4' -type f -size +10M 2>/dev/null | wc -l); "
        f"echo done=$(find {shlex.quote(layout['launch_root'])} -name DRIVE_UPLOAD_DONE.json -type f 2>/dev/null | wc -l); "
        f"echo failed=$(find {shlex.quote(layout['launch_root'])} -name DRIVE_UPLOAD_FAILED.txt -type f 2>/dev/null | wc -l); "
        f"python3 -c 'import json, pathlib; p=pathlib.Path({state_py}); "
        "d=json.load(open(p, encoding=\"utf-8\")) if p.is_file() else {}; "
        "print(\"batch_status=%s batch_completed=%s batch_failed=%s batch_skipped=%s batch_remaining=%s\" % "
        "(d.get(\"status\", \"missing\"), d.get(\"completed\", 0), d.get(\"failed\", 0), d.get(\"skipped\", 0), d.get(\"remaining\", 0)))' 2>/dev/null || true; "
        f"echo '=== LAST DRIVE LOG ==='; "
        f"tail -n {max(20, int(args.tail))} {shlex.quote(layout['batch_dir'] + '/drive_upload.log')} 2>/dev/null || true"
    )
    proc = remote.run(command, check=False, timeout=60, attempts=2)
    print(proc.stdout or proc.stderr or "No Drive upload status.", flush=True)
    return 0 if proc.returncode == 0 else 1


def action_drive_clean_uploaded(args: argparse.Namespace, remote: Remote) -> int:
    layout = remote_layout(args.remote_workdir, args.launch_id)
    if bool(args.clean_remote_intermediates_after_drive_upload):
        command = (
            "clean_keep_final() { "
            "story_root=\"$1\"; "
            "rm -rf \"$story_root/runpod_input\" \"$story_root/.upload_parts\" \"$story_root/.incoming\" "
            "\"$story_root/runpod_input.upload.tar\" \"$story_root/runpod_input.upload.tar.tmp\" \"$story_root/UPLOAD_FAILED.json\"; "
            "if [ -d \"$story_root/runpod_output\" ]; then "
            "find \"$story_root/runpod_output\" -mindepth 1 -maxdepth 1 ! -name final ! -name status -exec rm -rf {} + 2>/dev/null || true; "
            "fi; "
            "if [ -d \"$story_root/runpod_output/final\" ]; then "
            "find \"$story_root/runpod_output/final\" -mindepth 1 -maxdepth 1 ! -name final.mp4 -exec rm -rf {} + 2>/dev/null || true; "
            "fi; "
            "}; "
            f"count=0; "
            f"for marker in $(find {shlex.quote(layout['launch_root'])} -name DRIVE_UPLOAD_DONE.json -type f 2>/dev/null); do "
            "story_root=$(dirname \"$marker\"); slug=$(basename \"$story_root\"); "
            "clean_keep_final \"$story_root\"; "
            "count=$((count + 1)); echo REMOTE_INTERMEDIATES_CLEANED_AFTER_DRIVE story=$slug kept=final.mp4; "
            "done; "
            "echo DRIVE_CLEAN_UPLOADED_DONE count=$count mode=keep_final"
        )
    else:
        command = (
            f"count=0; "
            f"for marker in $(find {shlex.quote(layout['launch_root'])} -name DRIVE_UPLOAD_DONE.json -type f 2>/dev/null); do "
            "story_root=$(dirname \"$marker\"); slug=$(basename \"$story_root\"); "
            "rm -rf \"$story_root/runpod_input\" \"$story_root/runpod_output\" \"$story_root/.upload_parts\" \"$story_root/.incoming\" "
            "\"$story_root/runpod_input.upload.tar\" \"$story_root/runpod_input.upload.tar.tmp\" \"$story_root/UPLOAD_FAILED.json\"; "
            "count=$((count + 1)); echo REMOTE_CLEANED_AFTER_DRIVE story=$slug; "
            "done; "
            "echo DRIVE_CLEAN_UPLOADED_DONE count=$count mode=delete_all"
        )
    proc = remote.run(command, check=False, timeout=600, attempts=1)
    print(proc.stdout or proc.stderr or "No uploaded Drive files to clean.", flush=True)
    return 0 if proc.returncode == 0 else 1


def validate_download(path: Path) -> tuple[bool, str]:
    if not path.is_file() or path.stat().st_size < 10 * 1024 * 1024:
        return False, "missing_or_too_small"
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0 and "1920x1080" in (proc.stdout or ""), (proc.stderr or proc.stdout or "").strip()


def remote_file_size(remote: Remote, remote_path: str) -> int:
    proc = remote.run(
        f"stat -c %s {shlex.quote(remote_path)}",
        timeout=45,
        attempts=2,
    )
    text = (proc.stdout or "").strip().splitlines()
    if not text:
        raise RuntimeError(f"remote file size unavailable: {remote_path}")
    return int(text[-1].strip())


def _download_remote_chunk(
    *,
    remote: Remote,
    remote_path: str,
    part_path: Path,
    story_slug: str,
    start_mb: int,
    count_mb: int,
    expected_bytes: int,
    timeout: int,
    stall_timeout: int,
) -> None:
    if part_path.is_file() and part_path.stat().st_size == expected_bytes:
        return
    part_path.parent.mkdir(parents=True, exist_ok=True)
    temp = part_path.with_name(part_path.name + ".tmp")
    command = remote.ssh_command(
        "dd "
        f"if={shlex.quote(remote_path)} "
        f"bs=1048576 skip={int(start_mb)} count={int(count_mb)} "
        "status=none"
    )
    last: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(1, 4):
        with temp.open("wb") as handle:
            proc = subprocess.Popen(command, stdout=handle, stderr=subprocess.PIPE)
            started = time.monotonic()
            last_change = started
            last_size = 0
            while True:
                returncode = proc.poll()
                if returncode is not None:
                    _, stderr = proc.communicate()
                    last = subprocess.CompletedProcess(command, returncode, b"", stderr or b"")
                    break
                current_size = temp.stat().st_size if temp.exists() else 0
                now = time.monotonic()
                if current_size != last_size:
                    last_size = current_size
                    last_change = now
                if now - last_change > stall_timeout:
                    proc.kill()
                    _, stderr = proc.communicate()
                    last = subprocess.CompletedProcess(command, 124, b"", stderr or b"")
                    print(
                        f"DOWNLOAD_CHUNK_STALL story={story_slug} start_mb={start_mb} "
                        f"attempt={attempt}/3 bytes={last_size}",
                        flush=True,
                    )
                    break
                if now - started > timeout:
                    proc.kill()
                    _, stderr = proc.communicate()
                    last = subprocess.CompletedProcess(command, 124, b"", stderr or b"")
                    print(
                        f"DOWNLOAD_CHUNK_TIMEOUT story={story_slug} start_mb={start_mb} "
                        f"attempt={attempt}/3 bytes={last_size}",
                        flush=True,
                    )
                    break
                time.sleep(2)
        if last.returncode == 0 and temp.is_file() and temp.stat().st_size == expected_bytes:
            temp.replace(part_path)
            return
        temp.unlink(missing_ok=True)
        if attempt < 3:
            time.sleep(3)
    stderr = ""
    if last is not None and last.stderr:
        stderr = last.stderr.decode("utf-8", errors="replace").strip()
    raise RuntimeError(
        f"chunk download failed start_mb={start_mb} count_mb={count_mb} "
        f"expected={expected_bytes} {stderr}"
    )


def download_chunked(
    *,
    remote: Remote,
    remote_path: str,
    local_final: Path,
    story_slug: str,
    streams: int,
    chunk_size_mb: int,
) -> tuple[bool, str]:
    size_bytes = remote_file_size(remote, remote_path)
    if size_bytes < 10 * 1024 * 1024:
        return False, "remote_final_too_small"

    local_final.parent.mkdir(parents=True, exist_ok=True)
    temp = local_final.with_suffix(".mp4.download")
    parts_dir = local_final.parent / (local_final.name + ".parts")
    chunk_size_mb = max(8, int(chunk_size_mb))
    chunk_bytes = chunk_size_mb * 1048576
    chunks: list[tuple[int, int, int, int, Path]] = []
    offset = 0
    index = 0
    while offset < size_bytes:
        expected = min(chunk_bytes, size_bytes - offset)
        start_mb = offset // 1048576
        count_mb = math.ceil(expected / 1048576)
        part = parts_dir / f"chunk_{index:05d}.bin"
        chunks.append((index, start_mb, count_mb, expected, part))
        offset += expected
        index += 1

    print(
        f"DOWNLOAD_CHUNKED story={story_slug} size_mb={size_bytes / 1048576:.1f} "
        f"chunks={len(chunks)} streams={max(1, int(streams))} chunk_mb={chunk_size_mb}",
        flush=True,
    )
    completed = 0
    timeout = max(120, min(DOWNLOAD_CHUNK_TIMEOUT_SECONDS, chunk_size_mb * 8))
    stall_timeout = max(45, min(DOWNLOAD_CHUNK_STALL_SECONDS, chunk_size_mb * 2))
    with ThreadPoolExecutor(max_workers=max(1, int(streams))) as executor:
        futures = [
            executor.submit(
                _download_remote_chunk,
                remote=remote,
                remote_path=remote_path,
                part_path=part,
                story_slug=story_slug,
                start_mb=start_mb,
                count_mb=count_mb,
                expected_bytes=expected,
                timeout=timeout,
                stall_timeout=stall_timeout,
            )
            for _, start_mb, count_mb, expected, part in chunks
        ]
        for future in as_completed(futures):
            future.result()
            completed += 1
            print(
                f"DOWNLOAD_CHUNK_DONE story={story_slug} chunks={completed}/{len(chunks)}",
                flush=True,
            )

    temp.unlink(missing_ok=True)
    with temp.open("wb") as output:
        for _, _, _, expected, part in chunks:
            if not part.is_file() or part.stat().st_size != expected:
                return False, f"part_missing_or_wrong_size:{part.name}"
            with part.open("rb") as handle:
                while True:
                    block = handle.read(8 * 1024 * 1024)
                    if not block:
                        break
                    output.write(block)
    if temp.stat().st_size != size_bytes:
        return False, f"merged_size_mismatch:{temp.stat().st_size}!={size_bytes}"

    ok, detail = validate_download(temp)
    if not ok:
        return False, detail
    temp.replace(local_final)
    for _, _, _, _, part in chunks:
        part.unlink(missing_ok=True)
    parts_dir.rmdir()
    return True, str(local_final)


def _download_remote_slug(
    *,
    remote: Remote,
    layout: dict[str, str],
    by_slug: dict[str, dict[str, str]],
    slug: str,
    args: argparse.Namespace,
    delete_remote: bool,
) -> tuple[str, bool, str]:
    local_row = by_slug.get(slug)
    if not local_row:
        return slug, False, "local_story_not_found"
    local_final = Path(local_row["package_dir"]) / "08_video" / "final" / "final.mp4"
    remote_final = f"{layout['launch_root']}/{slug}/runpod_output/final/final.mp4"
    ok, detail = validate_download(local_final)
    if not ok:
        try:
            ok, detail = download_chunked(
                remote=remote,
                remote_path=remote_final,
                local_final=local_final,
                story_slug=slug,
                streams=max(1, int(args.parallel_downloads)),
                chunk_size_mb=max(8, int(args.chunk_size_mb)),
            )
        except Exception as exc:
            return slug, False, f"{type(exc).__name__}: {exc}"
    else:
        detail = str(local_final)
    if ok and delete_remote:
        try:
            cleanup_remote_story_after_download(remote, layout, slug)
            print(f"REMOTE_CLEANED story={slug}", flush=True)
        except Exception as exc:
            return slug, False, f"cleanup_failed:{type(exc).__name__}: {exc}"
    return slug, ok, detail


def action_download(args: argparse.Namespace, remote: Remote) -> int:
    launch_dir = resolve_launch_dir(args.launch_id)
    stories = discover_stories(args.launch_id, args.limit)
    by_slug = {row["story_slug"]: row for row in stories}
    layout = remote_layout(args.remote_workdir, args.launch_id)
    ready_slugs = list_remote_completed_slugs(remote, layout)
    if not ready_slugs:
        try:
            state = load_remote_state(remote, layout)
        except Exception:
            state = {}
        ready_slugs = [
            str(row.get("story_slug") or "")
            for row in state.get("results", [])
            if isinstance(row, dict) and row.get("status") in {"done", "skipped_existing"}
        ]
        ready_slugs = [slug for slug in ready_slugs if slug]
    if not ready_slugs:
        print("No completed remote videos to download.", flush=True)
        return 1

    completed = 0
    failed = 0
    for slug in ready_slugs:
        slug, ok, detail = _download_remote_slug(
            remote=remote,
            layout=layout,
            by_slug=by_slug,
            slug=slug,
            args=args,
            delete_remote=bool(args.delete_remote_after_download),
        )
        if ok:
            completed += 1
            print(f"DOWNLOAD_DONE story={slug} path={detail}", flush=True)
        else:
            failed += 1
            print(f"DOWNLOAD_FAILED story={slug} error={detail}", flush=True)
    print(f"DOWNLOAD_SUMMARY done={completed} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


def action_watch_download(args: argparse.Namespace, remote: Remote) -> int:
    stories = discover_stories(args.launch_id, args.limit)
    by_slug = {row["story_slug"]: row for row in stories}
    layout = remote_layout(args.remote_workdir, args.launch_id)
    seen: set[str] = set()
    failed_slugs: set[str] = set()
    total_done = 0
    while True:
        try:
            ready_slugs = list_remote_completed_slugs(remote, layout)
        except Exception as exc:
            ready_slugs = []
            print(f"WATCH_LIST_FAILED error={type(exc).__name__}: {exc}", flush=True)
        pending = [slug for slug in ready_slugs if slug not in seen]
        if pending:
            print(f"WATCH_READY count={len(pending)} stories={','.join(pending)}", flush=True)
        for slug in pending:
            slug, ok, detail = _download_remote_slug(
                remote=remote,
                layout=layout,
                by_slug=by_slug,
                slug=slug,
                args=args,
                delete_remote=True,
            )
            if ok:
                total_done += 1
                failed_slugs.discard(slug)
                seen.add(slug)
                print(f"WATCH_DOWNLOAD_DONE story={slug} path={detail}", flush=True)
            else:
                failed_slugs.add(slug)
                print(f"WATCH_DOWNLOAD_FAILED story={slug} error={detail}", flush=True)
        try:
            running = remote_batch_running(remote, layout)
        except Exception as exc:
            running = True
            print(f"WATCH_RUNNING_CHECK_FAILED error={type(exc).__name__}: {exc}", flush=True)
        if not running and not pending:
            print(
                f"WATCH_DOWNLOAD_FINISHED downloaded={total_done} failed={len(failed_slugs)}",
                flush=True,
            )
            return 0 if not failed_slugs else 1
        print(
            f"WATCH_SLEEP seconds={max(10, int(args.poll_seconds))} "
            f"running={str(running).lower()} downloaded={total_done} failed={len(failed_slugs)}",
            flush=True,
        )
        time.sleep(max(10, int(args.poll_seconds)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy and control autonomous RunPod video batches.")
    parser.add_argument(
        "action",
        choices={
            "start",
            "upload-only",
            "status",
            "stop",
            "download",
            "watch-download",
            "drive-upload",
            "watch-drive-upload",
            "drive-stop",
            "drive-status",
            "drive-clean-uploaded",
        },
    )
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--pod-host", required=True)
    parser.add_argument("--pod-port", required=True, type=int)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--remote-workdir", default="/workspace/content_factory_youtube")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-stories", default="")
    parser.add_argument("--tail", type=int, default=50)
    parser.add_argument("--parallel-downloads", type=int, default=8)
    parser.add_argument("--chunk-size-mb", type=int, default=128)
    parser.add_argument("--upload-chunk-size-mb", type=int, default=32)
    parser.add_argument("--poll-seconds", type=int, default=120)
    parser.add_argument("--delete-remote-after-download", action="store_true")
    parser.add_argument("--rclone-config", default="")
    parser.add_argument("--drive-remote", default="gdrive")
    parser.add_argument("--drive-path", default="")
    parser.add_argument("--drive-chunk-size", default="256M")
    parser.add_argument("--drive-parallel-uploads", type=int, default=2)
    parser.add_argument("--delete-remote-after-drive-upload", action="store_true")
    parser.add_argument("--clean-remote-intermediates-after-drive-upload", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    identity = Path(args.identity).expanduser().resolve()
    if not identity.is_file():
        raise FileNotFoundError(f"SSH identity missing: {identity}")
    remote = Remote(host=args.pod_host, port=args.pod_port, identity=identity)
    if args.action == "start":
        return action_start(args, remote)
    if args.action == "upload-only":
        return action_upload_only(args, remote)
    if args.action == "status":
        return action_status(args, remote)
    if args.action == "stop":
        return action_stop(args, remote)
    if args.action == "watch-download":
        return action_watch_download(args, remote)
    if args.action == "drive-upload":
        return action_drive_upload(args, remote, watch=False)
    if args.action == "watch-drive-upload":
        return action_drive_upload(args, remote, watch=True)
    if args.action == "drive-stop":
        return action_drive_stop(args, remote)
    if args.action == "drive-status":
        return action_drive_status(args, remote)
    if args.action == "drive-clean-uploaded":
        return action_drive_clean_uploaded(args, remote)
    return action_download(args, remote)


if __name__ == "__main__":
    raise SystemExit(main())
