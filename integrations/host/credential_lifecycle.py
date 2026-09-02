#!/usr/bin/env python3
"""Scan and explicitly scrub disposable Replaybook credential artifacts.

The command never prints credential contents. Cleanup is opt-in and requires
``scrub --yes`` so a routine audit cannot accidentally remove a user's login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


OPENROUTER_KEY = re.compile(r"sk-or-v1-[A-Za-z0-9_-]+")
OPENROUTER_KEY_BYTES = re.compile(rb"sk-or-v1-[A-Za-z0-9_-]+")
ENV_FILE = re.compile(r"replaybook-(?:opencode|codex|openrouter)-env\.[A-Za-z0-9]+$")


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/var/tmp")


def scan(jobs_root: Path, runtime: Path) -> dict[str, object]:
    key_files: list[str] = []
    if jobs_root.is_dir():
        for path in sorted(jobs_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            if OPENROUTER_KEY.search(text):
                key_files.append(str(path))
    env_files = sorted(
        str(path)
        for path in runtime.iterdir()
        if path.is_file() and not path.is_symlink() and ENV_FILE.fullmatch(path.name)
    ) if runtime.is_dir() else []
    return {
        "jobs_root": str(jobs_root),
        "runtime_dir": str(runtime),
        "openrouter_key_files": key_files,
        "openrouter_key_file_count": len(key_files),
        "disposable_env_files": env_files,
        "disposable_env_file_count": len(env_files),
    }


def scrub(jobs_root: Path, runtime: Path, remove_env_files: bool, yes: bool) -> dict[str, object]:
    if remove_env_files and not yes:
        raise SystemExit("refusing to remove env files without --yes")
    redacted = 0
    if jobs_root.is_dir():
        for path in sorted(jobs_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            updated, count = OPENROUTER_KEY_BYTES.subn(
                b"[REDACTED_OPENROUTER_KEY]", data
            )
            if count:
                path.write_bytes(updated)
                redacted += count
    removed = 0
    if remove_env_files and runtime.is_dir():
        for path in list(runtime.iterdir()):
            if path.is_file() and not path.is_symlink() and ENV_FILE.fullmatch(path.name):
                path.unlink()
                removed += 1
    return {"redacted_openrouter_keys": redacted, "removed_disposable_env_files": removed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "scrub"), nargs="?", default="scan")
    parser.add_argument("--jobs-root", type=Path, default=Path(__file__).resolve().parents[2] / "jobs")
    parser.add_argument("--runtime-dir", type=Path, default=runtime_dir())
    parser.add_argument("--remove-env-files", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    result = (
        scan(args.jobs_root, args.runtime_dir)
        if args.command == "scan"
        else scrub(args.jobs_root, args.runtime_dir, args.remove_env_files, args.yes)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
