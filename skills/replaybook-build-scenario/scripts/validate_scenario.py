#!/usr/bin/env python3
"""Statically validate a Replaybook declarative host scenario."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any


FILE_FIELDS = ("nixos_config", "instruction", "oracle")
SERVICE_FIELDS = ("required_services", "restart_services")
PHASES = ("preflight", "verify")
SUPPORTED_STEPS = {"wait_http", "concurrent_http", "replay_http"}
LEAK_MARKERS = (
    "oracle.sh",
    "phase-failure.json",
    "scenario-state.json",
    "run-host-native.sh",
)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_relative_path(value: Any) -> bool:
    if not nonempty_string(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def safe_guest_path(value: Any) -> bool:
    if not nonempty_string(value) or value == "/":
        return False
    path = PurePosixPath(value)
    return (
        path.is_absolute()
        and ".." not in path.parts
        and all(character.isalnum() or character in "/._-" for character in value)
    )


def validate(directory: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = directory / "scenario.toml"
    if not directory.is_dir():
        return [f"scenario directory does not exist: {directory}"]
    if not manifest_path.is_file():
        return [f"missing declarative manifest: {manifest_path}"]

    try:
        manifest = tomllib.loads(manifest_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [f"invalid scenario.toml: {error}"]

    scenario = manifest.get("scenario")
    if not isinstance(scenario, dict):
        return ["scenario.toml must define [scenario]"]
    version = scenario.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        fail(errors, "scenario.version must be a positive integer")

    resolved_files: dict[str, Path] = {}
    for field in FILE_FIELDS:
        value = scenario.get(field)
        if not safe_relative_path(value):
            fail(errors, f"scenario.{field} must be a safe relative path")
            continue
        path = directory / value
        resolved_files[field] = path
        if not path.is_file():
            fail(errors, f"scenario.{field} does not exist: {value}")
        elif path.stat().st_size == 0:
            fail(errors, f"scenario.{field} is empty: {value}")

    services: dict[str, list[str]] = {}
    for field in SERVICE_FIELDS:
        value = scenario.get(field)
        if not isinstance(value, list) or not value or not all(nonempty_string(item) for item in value):
            fail(errors, f"scenario.{field} must be a non-empty string array")
            continue
        services[field] = value
        if len(value) != len(set(value)):
            fail(errors, f"scenario.{field} contains duplicate services")
    if services.get("restart_services") and services.get("required_services"):
        unknown = set(services["restart_services"]) - set(services["required_services"])
        if unknown:
            fail(errors, "restart_services must also be required_services: " + ", ".join(sorted(unknown)))

    forbidden_strings: list[str] = []
    leak_audit = manifest.get("guest_leak_audit")
    if leak_audit is not None:
        if not isinstance(leak_audit, dict):
            fail(errors, "guest_leak_audit must be a table")
        else:
            forbidden = leak_audit.get("forbidden_strings")
            if not isinstance(forbidden, list) or not forbidden or not all(
                nonempty_string(value)
                and len(value.strip()) >= 4
                and not any(character in value for character in "\r\n\0")
                for value in forbidden
            ):
                fail(
                    errors,
                    "guest_leak_audit.forbidden_strings must be a non-empty string array with entries of at least four characters",
                )
            else:
                forbidden_strings = [value.strip() for value in forbidden]
                if len({value.casefold() for value in forbidden_strings}) != len(
                    forbidden_strings
                ):
                    fail(errors, "guest_leak_audit.forbidden_strings contains duplicates")
            scan_paths = leak_audit.get("scan_paths", [])
            if not isinstance(scan_paths, list) or not all(
                safe_guest_path(path) for path in scan_paths
            ):
                fail(
                    errors,
                    "guest_leak_audit.scan_paths must contain safe absolute guest paths",
                )
            elif len(scan_paths) != len(set(scan_paths)):
                fail(errors, "guest_leak_audit.scan_paths contains duplicates")

    recorded: set[str] = set()
    replayed: set[str] = set()
    for phase_name in PHASES:
        phase = manifest.get(phase_name)
        steps = phase.get("steps") if isinstance(phase, dict) else None
        if not isinstance(steps, list) or not steps:
            fail(errors, f"manifest must define at least one {phase_name}.step")
            continue
        for index, step in enumerate(steps, start=1):
            prefix = f"{phase_name} step {index}"
            if not isinstance(step, dict):
                fail(errors, f"{prefix} must be a table")
                continue
            step_type = step.get("type")
            if step_type not in SUPPORTED_STEPS:
                fail(errors, f"{prefix} has unsupported type: {step_type!r}")
            if not nonempty_string(step.get("failure_category")):
                fail(errors, f"{prefix} must define failure_category")
            for key in ("record_all_as", "record_failed_as"):
                value = step.get(key)
                if value is not None:
                    if not nonempty_string(value):
                        fail(errors, f"{prefix}.{key} must be a non-empty string")
                    else:
                        recorded.add(value)
            if step_type == "replay_http":
                value = step.get("ids_from")
                if not nonempty_string(value):
                    fail(errors, f"{prefix} must define ids_from")
                else:
                    replayed.add(value)

    missing_replays = recorded - replayed
    if missing_replays:
        fail(errors, "preflight records IDs that verify never replays: " + ", ".join(sorted(missing_replays)))
    unknown_replays = replayed - recorded
    if unknown_replays:
        fail(errors, "verify replays IDs not recorded by preflight: " + ", ".join(sorted(unknown_replays)))

    instruction = resolved_files.get("instruction")
    if instruction and instruction.is_file():
        text = instruction.read_text(errors="replace")
        if "Do not reboot, shut down, or replace the host yourself" not in text:
            fail(errors, "instruction must reserve reboot control for the external verifier")
        lowered = text.lower()
        for marker in LEAK_MARKERS:
            if marker.lower() in lowered:
                fail(errors, f"instruction exposes evaluation machinery: {marker}")

    nixos = resolved_files.get("nixos_config")
    if nixos and nixos.is_file():
        text = nixos.read_text(errors="replace").lower()
        for marker in LEAK_MARKERS:
            if marker.lower() in text:
                fail(errors, f"NixOS config exposes controller-only artifact: {marker}")

    if forbidden_strings:
        ignored = {manifest_path.resolve()}
        oracle_path = resolved_files.get("oracle")
        if oracle_path is not None:
            ignored.add(oracle_path.resolve())
        for path in directory.rglob("*"):
            if not path.is_file() or path.resolve() in ignored:
                continue
            text = path.read_text(errors="replace").casefold()
            for index, marker in enumerate(forbidden_strings, start=1):
                if marker.casefold() in text:
                    fail(
                        errors,
                        f"agent-visible source contains guest leak rule #{index}: {path.relative_to(directory)}",
                    )

    oracle = resolved_files.get("oracle")
    if oracle and oracle.is_file():
        result = subprocess.run(
            ["bash", "-n", str(oracle)], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            fail(errors, f"oracle shell syntax failed: {result.stderr.strip()}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="path containing scenario.toml")
    args = parser.parse_args()
    errors = validate(args.scenario.resolve())
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"valid Replaybook scenario: {args.scenario}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
