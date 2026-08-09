#!/usr/bin/env python3
"""Execute declarative preflight and verification phases for host scenarios."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATE_FILE = "scenario-state.json"
FAILURE_FILE = "phase-failure.json"
SUPPORTED_STEPS = {"wait_http", "concurrent_http", "replay_http"}
SCENARIO_FILE_FIELDS = ("nixos_config", "instruction", "oracle")
SCENARIO_SERVICE_FIELDS = ("required_services", "restart_services")


@dataclass(frozen=True)
class Response:
    status: int
    body: str


class PhaseFailure(RuntimeError):
    def __init__(self, message: str, category: str = "verification_failed") -> None:
        super().__init__(message)
        self.category = category


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = tomllib.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a TOML table")
    return manifest


def describe_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    scenario = manifest.get("scenario")
    if not isinstance(scenario, dict):
        raise ValueError("manifest does not define [scenario]")
    version = scenario.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise ValueError("scenario.version must be a positive integer")
    description: dict[str, Any] = {"version": version}
    for field in SCENARIO_FILE_FIELDS:
        description[field] = string_value(scenario, field)
    for field in SCENARIO_SERVICE_FIELDS:
        services = scenario.get(field)
        if not isinstance(services, list) or not services or not all(
            isinstance(service, str) and service for service in services
        ):
            raise ValueError(f"scenario.{field} must be a non-empty string array")
        description[field] = services
    for phase in ("preflight", "verify"):
        phase_config = manifest.get(phase)
        if not isinstance(phase_config, dict) or not isinstance(phase_config.get("steps"), list):
            raise ValueError(f"manifest does not define {phase}.steps")
        description[f"{phase}_steps"] = len(phase_config["steps"])
    return description


def request(base_url: str, step: dict[str, Any], path: str) -> Response:
    method = string_value(step, "method", "GET")
    timeout = number_value(step, "request_timeout_seconds", 5.0)
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request_object = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request_object, timeout=timeout) as response:
            return Response(response.status, response.read().decode(errors="replace"))
    except urllib.error.HTTPError as error:
        with error:
            return Response(error.code, error.read().decode(errors="replace"))
    except (OSError, urllib.error.URLError):
        return Response(0, "")


def string_value(step: dict[str, Any], key: str, default: str | None = None) -> str:
    value = step.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def integer_value(step: dict[str, Any], key: str, default: int | None = None) -> int:
    value = step.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def number_value(step: dict[str, Any], key: str, default: float) -> float:
    value = step.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return float(value)


def format_value(value: str, *, phase: str, identifier: str = "") -> str:
    return value.format(phase=phase, id=identifier)


def matches(response: Response, step: dict[str, Any]) -> bool:
    expected_status = integer_value(step, "expected_status", 200)
    if response.status != expected_status:
        return False
    expected_body = step.get("expected_body")
    if expected_body is not None and response.body != expected_body:
        return False
    minimum = step.get("body_integer_min")
    if minimum is not None:
        if not isinstance(minimum, int) or isinstance(minimum, bool):
            raise ValueError("body_integer_min must be an integer")
        try:
            return int(response.body) >= minimum
        except ValueError:
            return False
    return True


def failure(step: dict[str, Any], message: str) -> PhaseFailure:
    return PhaseFailure(message, string_value(step, "failure_category", "verification_failed"))


def run_wait_http(base_url: str, phase: str, step: dict[str, Any]) -> None:
    path = format_value(string_value(step, "path"), phase=phase)
    deadline = time.monotonic() + number_value(step, "timeout_seconds", 30.0)
    interval = number_value(step, "interval_seconds", 1.0)
    last = Response(0, "")
    while time.monotonic() < deadline:
        last = request(base_url, step, path)
        if matches(last, step):
            return
        time.sleep(interval)
    raise failure(
        step,
        f"{phase} HTTP assertion failed for {path}: status={last.status} body={last.body!r}",
    )


def generated_id(prefix: str, index: int) -> str:
    return f"{prefix}-{time.time_ns()}-{os.getpid()}-{secrets.token_hex(3)}-{index}"


def concurrent_requests(
    base_url: str,
    phase: str,
    step: dict[str, Any],
    identifiers: list[str],
) -> list[tuple[str, Response]]:
    template = string_value(step, "path")

    def run(identifier: str) -> tuple[str, Response]:
        path = format_value(template, phase=phase, identifier=identifier)
        return identifier, request(base_url, step, path)

    concurrency = integer_value(step, "concurrency", len(identifiers) or 1)
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(run, identifiers))


def run_concurrent_http(
    base_url: str,
    phase: str,
    step: dict[str, Any],
    state: dict[str, Any],
) -> None:
    count = integer_value(step, "count")
    if count <= 0:
        raise ValueError("count must be positive")
    prefix = format_value(string_value(step, "id_prefix", phase), phase=phase)
    identifiers = [generated_id(prefix, index) for index in range(1, count + 1)]
    results = concurrent_requests(base_url, phase, step, identifiers)
    successes = [identifier for identifier, response in results if matches(response, step)]
    failed = [identifier for identifier, response in results if not matches(response, step)]
    minimum = integer_value(step, "min_success", count)
    maximum = integer_value(step, "max_success", count)
    if minimum < 0 or maximum < minimum or maximum > count:
        raise ValueError("success bounds must satisfy 0 <= min_success <= max_success <= count")

    if step.get("record_all_as") is not None:
        state[string_value(step, "record_all_as")] = identifiers
    if step.get("record_failed_as") is not None:
        state[string_value(step, "record_failed_as")] = failed
    if not minimum <= len(successes) <= maximum:
        raise failure(
            step,
            f"{phase} concurrent HTTP assertion got {len(successes)}/{count} successes; expected {minimum}..{maximum}",
        )


def run_replay_http(
    base_url: str,
    phase: str,
    step: dict[str, Any],
    state: dict[str, Any],
) -> None:
    state_key = string_value(step, "ids_from")
    identifiers = state.get(state_key)
    if not isinstance(identifiers, list) or not identifiers or not all(isinstance(item, str) for item in identifiers):
        raise failure(step, f"{phase} phase is missing controller-owned IDs in {state_key}")
    pending = identifiers
    timeout = step.get("timeout_seconds")
    if timeout is not None:
        deadline = time.monotonic() + number_value(step, "timeout_seconds", 30.0)
        interval = number_value(step, "interval_seconds", 1.0)
    else:
        deadline = time.monotonic()
        interval = 0.0

    while pending:
        results = concurrent_requests(base_url, phase, step, pending)
        pending = [identifier for identifier, response in results if not matches(response, step)]
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(interval)
    if pending:
        raise failure(step, f"{phase} could not recover controller-owned IDs: {', '.join(pending)}")


def load_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / STATE_FILE
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("scenario state must be a JSON object")
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_phase(manifest_path: Path, phase: str, base_url: str, state_dir: Path) -> None:
    manifest = load_manifest(manifest_path)
    plan_name = "preflight" if phase == "preflight" else "verify"
    phase_config = manifest.get(plan_name)
    if not isinstance(phase_config, dict) or not isinstance(phase_config.get("steps"), list):
        raise ValueError(f"manifest does not define {plan_name}.steps")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = load_state(state_dir)
    failure_path = state_dir / FAILURE_FILE
    failure_path.unlink(missing_ok=True)

    for index, step in enumerate(phase_config["steps"], start=1):
        if not isinstance(step, dict):
            raise ValueError(f"{phase} step {index} must be a table")
        step_type = string_value(step, "type")
        if step_type not in SUPPORTED_STEPS:
            raise ValueError(f"unsupported step type: {step_type}")
        try:
            if step_type == "wait_http":
                run_wait_http(base_url, phase, step)
            elif step_type == "concurrent_http":
                run_concurrent_http(base_url, phase, step, state)
            else:
                run_replay_http(base_url, phase, step, state)
        except PhaseFailure as error:
            save_json(
                failure_path,
                {"category": error.category, "message": str(error), "phase": phase, "step": index},
            )
            save_json(state_dir / STATE_FILE, state)
            raise
        save_json(state_dir / STATE_FILE, state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("phase", nargs="?")
    parser.add_argument("base_url", nargs="?")
    parser.add_argument("state_dir", nargs="?", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.describe:
            print(json.dumps(describe_manifest(args.manifest), sort_keys=True))
        else:
            if not args.phase or not args.base_url or args.state_dir is None:
                raise ValueError("phase, base URL, and state directory are required")
            run_phase(args.manifest, args.phase, args.base_url, args.state_dir)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"invalid declarative scenario: {error}", file=os.sys.stderr)
        return 2
    except PhaseFailure as error:
        print(error, file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
