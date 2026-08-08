#!/usr/bin/env python3
"""Create a Replaybook declarative host scenario from the bundled templates."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


NAME_PATTERN = re.compile(r"^[0-9]{3}-[a-z0-9]+(?:-[a-z0-9]+)*$")
TEMPLATE_FILES = ("scenario.toml", "nixos.nix", "instruction.md", "oracle.sh")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="scenario name such as 017-expired-certificate")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Replaybook repository root (default: current directory)",
    )
    args = parser.parse_args()

    if not NAME_PATTERN.fullmatch(args.name):
        print("scenario name must match NNN-lowercase-hyphen-name", file=sys.stderr)
        return 2

    skill_dir = Path(__file__).resolve().parent.parent
    assets_dir = skill_dir / "assets"
    destination = args.repo.resolve() / "integrations" / "host" / "scenarios" / args.name
    if destination.exists():
        print(f"refusing to overwrite existing path: {destination}", file=sys.stderr)
        return 1
    missing = [name for name in TEMPLATE_FILES if not (assets_dir / name).is_file()]
    if missing:
        print(f"skill is missing templates: {', '.join(missing)}", file=sys.stderr)
        return 1

    destination.mkdir(parents=True)
    for name in TEMPLATE_FILES:
        shutil.copyfile(assets_dir / name, destination / name)
    (destination / "oracle.sh").chmod(0o755)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
