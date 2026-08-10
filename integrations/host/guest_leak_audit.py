#!/usr/bin/env python3
"""Scan controller-captured guest surfaces for scenario answer leaks."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path
from typing import BinaryIO

try:
    from .scenario_phase import leak_audit_config, load_manifest
except ImportError:
    from scenario_phase import leak_audit_config, load_manifest


def find_pattern(stream: BinaryIO, patterns: list[bytes]) -> int | None:
    overlap = max((len(pattern) for pattern in patterns), default=1) - 1
    previous = b""
    while chunk := stream.read(64 * 1024):
        lowered = previous + chunk.lower()
        for index, pattern in enumerate(patterns, start=1):
            if pattern in lowered:
                return index
        previous = lowered[-overlap:] if overlap else b""
    return None


def scan_file(path: Path, patterns: list[bytes]) -> int | None:
    with path.open("rb") as stream:
        return find_pattern(stream, patterns)


def scan_archive(path: Path, patterns: list[bytes]) -> tuple[int, str] | None:
    with tarfile.open(path, mode="r:*") as archive:
        for member in archive:
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            match = find_pattern(extracted, patterns)
            if match is not None:
                return match, member.name
    return None


def labeled_path(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label or not raw_path:
        raise argparse.ArgumentTypeError("expected LABEL=PATH")
    return label, Path(raw_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--surface", action="append", type=labeled_path, default=[])
    parser.add_argument("--archive", action="append", type=labeled_path, default=[])
    args = parser.parse_args()

    config = leak_audit_config(load_manifest(args.manifest))
    patterns = [value.casefold().encode() for value in config["forbidden_strings"]]
    if not patterns:
        return 0

    for label, path in args.surface:
        match = scan_file(path, patterns)
        if match is not None:
            print(
                f"guest image leak detected: forbidden string #{match} in {label}",
                flush=True,
            )
            return 1
    for label, path in args.archive:
        match = scan_archive(path, patterns)
        if match is not None:
            index, member = match
            print(
                f"guest image leak detected: forbidden string #{index} in {label}:{member}",
                flush=True,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
