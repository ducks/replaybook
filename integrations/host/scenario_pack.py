#!/usr/bin/env python3
"""Discover versioned host-native scenario packs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PACK_MANIFEST = "replaybook-pack.toml"
PACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SCENARIO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SCENARIO_VERSION_PATTERN = re.compile(
    r'^SCENARIO_VERSION=["\']?([1-9][0-9]*)["\']?$', re.MULTILINE
)


@dataclass(frozen=True)
class ScenarioPack:
    id: str
    version: str
    path: Path

    def metadata(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version}


@dataclass(frozen=True)
class Scenario:
    id: str
    version: int
    path: Path
    pack: ScenarioPack

    def metadata(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "pack": self.pack.metadata(),
        }


def string_field(table: dict[str, object], key: str, source: Path) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: pack.{key} must be a non-empty string")
    return value


def load_pack(path: Path) -> ScenarioPack:
    resolved = path.expanduser().resolve()
    manifest = resolved / PACK_MANIFEST
    if not resolved.is_dir():
        raise ValueError(f"scenario pack directory does not exist: {resolved}")
    if not manifest.is_file():
        raise ValueError(f"scenario pack is missing {PACK_MANIFEST}: {resolved}")
    try:
        document = tomllib.loads(manifest.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"could not read scenario pack manifest {manifest}: {error}") from error
    table = document.get("pack")
    if not isinstance(table, dict):
        raise ValueError(f"{manifest}: missing [pack] table")
    pack_id = string_field(table, "id", manifest)
    version = string_field(table, "version", manifest)
    if not PACK_ID_PATTERN.fullmatch(pack_id) or any(
        part in {"", ".", ".."} for part in pack_id.split("/")
    ):
        raise ValueError(f"{manifest}: pack.id contains unsafe characters")
    return ScenarioPack(id=pack_id, version=version, path=resolved)


def scenario_version(path: Path) -> int | None:
    typed = path / "scenario.toml"
    legacy = path / "scenario.conf"
    if typed.is_file():
        try:
            document = tomllib.loads(typed.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"could not read scenario manifest {typed}: {error}") from error
        table = document.get("scenario")
        version = table.get("version") if isinstance(table, dict) else None
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            raise ValueError(f"{typed}: scenario.version must be a positive integer")
        return version
    if legacy.is_file():
        match = SCENARIO_VERSION_PATTERN.search(legacy.read_text())
        if not match:
            raise ValueError(f"{legacy}: SCENARIO_VERSION must be a positive integer")
        return int(match.group(1))
    return None


def discover(pack_paths: Iterable[Path]) -> tuple[list[ScenarioPack], dict[str, Scenario]]:
    packs: list[ScenarioPack] = []
    scenarios: dict[str, Scenario] = {}
    pack_ids: set[str] = set()
    for path in pack_paths:
        pack = load_pack(path)
        if pack.id in pack_ids:
            raise ValueError(f"duplicate scenario pack ID: {pack.id}")
        pack_ids.add(pack.id)
        packs.append(pack)
        for scenario_dir in sorted(item for item in pack.path.iterdir() if item.is_dir()):
            version = scenario_version(scenario_dir)
            if version is None:
                continue
            scenario_id = scenario_dir.name
            if not SCENARIO_ID_PATTERN.fullmatch(scenario_id):
                raise ValueError(f"scenario ID contains unsafe characters: {scenario_id}")
            if scenario_id in scenarios:
                previous = scenarios[scenario_id]
                raise ValueError(
                    f"duplicate host-native scenario {scenario_id}: "
                    f"{previous.pack.id} and {pack.id}"
                )
            scenarios[scenario_id] = Scenario(
                id=scenario_id,
                version=version,
                path=scenario_dir,
                pack=pack,
            )
    return packs, scenarios


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="append", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true")
    action.add_argument("--resolve", metavar="SCENARIO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        packs, scenarios = discover(args.pack)
        if args.resolve:
            scenario = scenarios.get(args.resolve)
            if scenario is None:
                raise ValueError(f"unknown host-native scenario: {args.resolve}")
            output: object = {
                **scenario.metadata(),
                "path": str(scenario.path),
            }
        else:
            output = {
                "packs": [pack.metadata() for pack in packs],
                "scenarios": [scenario.metadata() for scenario in scenarios.values()],
            }
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
