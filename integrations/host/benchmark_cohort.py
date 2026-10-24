"""Freeze an operator-selected benchmark harness independently of development.

A cohort copies the controller closure, scenario pack, manifest, and agent
artifacts. Integrity checks protect them before each session starts. Model
selection and reasoning effort remain per-run configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from integrations.host.benchmark_manifest import load_benchmark_manifest
from integrations.host.run_host_matrix import (
    HOST_HARNESS_VERSION, HOST_RUNNER_FILES, REPO_DIR, SCRIPT_DIR,
    apply_benchmark_manifest, git_commit, parse_args, sha256_file,
    snapshot_ignore, utc_now, write_json_atomic,
)
from integrations.host.scenario_pack import discover

CONTROLLER_FILES = (*HOST_RUNNER_FILES, "run_host_matrix.py", "benchmark_manifest.py", "matrix_preflight.py")
TRANSPORT_DEFAULTS = {
    "REPLAYBOOK_CLAUX_PROVIDER_ROUTES": "{}",
    "REPLAYBOOK_OPENAI_UPSTREAM": "https://openrouter.ai",
    "REPLAYBOOK_OPENAI_PROXY_PATH": "/api/v1",
}
RUN_OPTIONS = {"--models", "--reasoning-efforts", "--base-port", "--concurrency",
               "--scenario", "--agent-env-file"}


def default_cohorts_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "replaybook/cohorts"


def file_manifest(root: Path) -> dict[str, dict[str, Any]]:
    files = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path == root / "cohort.json":
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"cohort artifact escapes its directory: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = {
                "sha256": sha256_file(path), "mode": path.stat().st_mode & 0o777,
            }
    return files


def identity(record: dict[str, Any]) -> str:
    content = {key: record[key] for key in ("files", "args", "environment")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def load_cohort(path: Path, expected_identity: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    try:
        record = json.loads((root / "cohort.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read frozen cohort {root}: {error}") from error
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise ValueError("unsupported frozen cohort schema")
    if not all(key in record for key in ("files", "args", "environment", "identity", "name")):
        raise ValueError("incomplete frozen cohort")
    if (not isinstance(record["args"], list)
        or not all(isinstance(value, str) for value in record["args"])
        or not isinstance(record["environment"], dict)
        or not all(isinstance(value, str) for value in record["environment"].values())):
        raise ValueError("invalid frozen cohort arguments or environment")
    if identity(record) != record["identity"] or file_manifest(root) != record["files"]:
        raise ValueError("frozen cohort changed; create a new cohort instead")
    if expected_identity is not None and record["identity"] != expected_identity:
        raise ValueError("queued session's frozen cohort identity changed")
    return record


def freeze_cohort(name: str, profile: dict[str, Any], destination: Path,
                  host_dir: Path = SCRIPT_DIR) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("cohort name must use letters, digits, dots, underscores, or hyphens")
    if profile.get("cohort"):
        raise ValueError("freeze a development profile, not an already-frozen cohort")
    args = parse_args(profile["args"])
    manifest = apply_benchmark_manifest(args)
    if manifest is None or args.oracle:
        raise ValueError("freezing requires an executable benchmark manifest and an agent")
    if args.scenarios != [item.id for item in manifest.scenarios]:
        raise ValueError("freeze the complete declared benchmark, not a scenario subset")
    packs, scenarios = discover([manifest.pack_path])
    manifest.validate_environment(packs, scenarios, HOST_HARNESS_VERSION)
    if args.agent_adapter is None and args.claux_binary is None:
        raise ValueError("freeze requires an explicit --claux-binary or custom adapter")
    if args.agent_adapter is not None and (args.claux_binary or args.claux_release):
        raise ValueError("custom adapters cannot use Claux binary/release options")
    if not args.agent_provider:
        raise ValueError("freeze requires --agent-provider")

    transport = {
        key: profile.get("environment", {}).get(key, os.environ.get(key, default))
        for key, default in TRANSPORT_DEFAULTS.items()
    }
    routes = json.loads(transport["REPLAYBOOK_CLAUX_PROVIDER_ROUTES"])
    if not isinstance(routes, dict) or set(routes) - {"*"}:
        raise ValueError('cohort protocol must be shared, e.g. {"*":"openrouter"}, rather than keyed by model')
    if any(value not in ("chat_completions", "openrouter", "responses", "anthropic") for value in routes.values()):
        raise ValueError("invalid shared cohort protocol")
    transport["REPLAYBOOK_CLAUX_PROVIDER_ROUTES"] = json.dumps(routes, sort_keys=True)
    destination = destination.expanduser().resolve()
    if destination.is_relative_to(manifest.pack_path):
        raise ValueError("cohort storage must be outside the source scenario pack")
    root = destination / name
    if root.exists():
        raise ValueError(f"cohort already exists; choose a new name: {root}")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=destination) as temporary:
        staged = Path(temporary) / "cohort"
        controller = staged / "controller/integrations/host"
        controller.mkdir(parents=True)
        for filename in CONTROLLER_FILES:
            shutil.copy2(host_dir / filename, controller / filename)
        write_json_atomic(staged / "controller/.replaybook-source.json", {"commit": git_commit(REPO_DIR)})
        pack_relative = manifest.pack_path.relative_to(manifest.path.parent)
        benchmark_dir = staged / "benchmark"
        pack_destination = benchmark_dir / pack_relative
        shutil.copytree(manifest.pack_path, pack_destination, ignore=snapshot_ignore)
        shutil.copy2(manifest.path, benchmark_dir / manifest.path.name)
        write_json_atomic(pack_destination / ".replaybook-source.json", {"commit": git_commit(manifest.pack_path)})
        frozen_args = ["--benchmark", "{cohort}/benchmark/" + manifest.path.name,
                       "--agent-provider", args.agent_provider]
        for flag, source, filename in (
            ("--claux-binary", args.claux_binary, "claux"),
            ("--agent-adapter", args.agent_adapter, "adapter"),
            ("--agent-payload", args.agent_payload, "payload"),
            ("--agent-config", args.agent_config, "config.json"),
        ):
            if source is not None:
                target = staged / "agent" / filename
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source.expanduser().resolve(), target)
                frozen_args.extend([flag, "{cohort}/agent/" + filename])
        if args.agent_name:
            frozen_args.extend(["--agent-name", args.agent_name])
        record = {
            "schema_version": 1, "name": name, "created_at": utc_now(),
            "args": frozen_args, "environment": transport,
            "files": file_manifest(staged),
            "benchmark": manifest.metadata(),
        }
        record["identity"] = identity(record)
        write_json_atomic(staged / "cohort.json", record)
        staged.rename(root)
    load_cohort(root)
    return root


def execution(profile: dict[str, Any], expected_identity: str | None = None) -> tuple[Path, list[str], dict[str, str]]:
    root = Path(profile["cohort"]).expanduser().resolve()
    cohort = load_cohort(root, expected_identity)
    options = {item.partition("=")[0] for item in profile["args"] if item.startswith("--")}
    if options - RUN_OPTIONS:
        raise ValueError("a frozen-cohort run may select models/effort, scenarios, ports, concurrency, and credentials only")
    for key, value in profile.get("environment", {}).items():
        if key in cohort["environment"] and value != cohort["environment"][key]:
            raise ValueError(f"profile cannot override frozen transport setting {key}")
    frozen_args = [value.replace("{cohort}", str(root)) for value in cohort["args"]]
    return (root / "controller/integrations/host/run_host_matrix.py",
            [*frozen_args, *profile["args"]],
            {**profile.get("environment", {}), **cohort["environment"]})
