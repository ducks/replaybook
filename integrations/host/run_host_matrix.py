#!/usr/bin/env python3
"""Run a versioned matrix of host-native Replaybook evaluations."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from .benchmark_manifest import BenchmarkManifest, load_benchmark_manifest
    from .scenario_pack import ScenarioPack, discover
except ImportError:
    from benchmark_manifest import BenchmarkManifest, load_benchmark_manifest
    from scenario_pack import ScenarioPack, discover


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parents[1]
DEFAULT_SCENARIO = "013-sidekiq-wrong-redis"
DEFAULT_SCENARIO_PACK = SCRIPT_DIR / "scenarios"
DEFAULT_AGENT_TIMEOUT_SECONDS = 900
HOST_HARNESS_VERSION = 15
TRIAL_STATUSES = {"evaluated", "unavailable"}
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
SCENARIO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HOST_RUNNER_FILES = (
    "classify-agent-exit.sh",
    "classify-agent-outcome.sh",
    "classify-agent-run-exit.sh",
    "guest_leak_audit.py",
    "isolated-vm.nix",
    "openrouter_proxy.py",
    "run-agent-adapter.sh",
    "run-claux.sh",
    "run-host-native.sh",
    "scenario_pack.py",
    "scenario_phase.py",
    "ssh-probe.sh",
)


@dataclass(frozen=True)
class Job:
    run_id: str
    scenario: str
    model: str | None
    attempt: int
    ssh_port: int
    http_port: int
    output_dir: Path
    log_file: Path
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class WorkerResult:
    job: Job
    exit_code: int
    result: dict[str, Any] | None
    error: str | None


@dataclass
class Progress:
    total: int
    started: int = 0
    completed: int = 0

    def start(self) -> int:
        self.started += 1
        return self.started

    def complete(self) -> int:
        self.completed += 1
        return self.completed


@dataclass(frozen=True)
class ExecutionSnapshot:
    runner: Path
    scenario_pack_dirs: list[Path]
    agent_adapter: Path | None
    agent_payload: Path | None
    claux_binary: Path | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ResumePlan:
    matrix_dir: Path
    benchmark: dict[str, Any]
    snapshot: ExecutionSnapshot
    all_jobs: list[Job]
    completed: list[WorkerResult]
    pending: list[Job]
    started_at: str
    agent_timeout_seconds: int
    agent_name: str | None
    claux_release: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "unnamed"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and "__pycache__" not in candidate.parts
        and candidate.suffix != ".pyc"
    ):
        relative = item.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update((item.stat().st_mode & 0o777).to_bytes(2, "big"))
        file_digest = bytes.fromhex(sha256_file(item))
        digest.update(file_digest)
    return digest.hexdigest()


def copy_artifact(source: Path | None, destination: Path) -> tuple[Path | None, str | None]:
    if source is None:
        return None, None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination, sha256_file(destination)


def stage_execution_snapshot(
    matrix_dir: Path,
    *,
    packs: list[ScenarioPack],
    agent_adapter: Path | None,
    agent_payload: Path | None,
    agent_env_file: Path | None,
    claux_binary: Path | None,
    benchmark_manifest: Path | None = None,
    host_dir: Path = SCRIPT_DIR,
) -> ExecutionSnapshot:
    snapshot_dir = matrix_dir / "execution-snapshot"
    harness_dir = snapshot_dir / "host-harness"
    harness_dir.mkdir(parents=True)
    for name in HOST_RUNNER_FILES:
        source = host_dir / name
        if not source.is_file():
            raise ValueError(f"host runner snapshot file does not exist: {source}")
        shutil.copy2(source, harness_dir / name)

    pack_dirs = []
    pack_metadata = []
    packs_dir = snapshot_dir / "scenario-packs"
    packs_dir.mkdir()
    for index, pack in enumerate(packs, start=1):
        destination = packs_dir / f"{index:02d}-{slugify(pack.id)}"
        shutil.copytree(pack.path, destination, symlinks=False)
        pack_dirs.append(destination)
        pack_metadata.append(
            {
                **pack.metadata(),
                "sha256": sha256_tree(destination),
            }
        )

    adapter, adapter_hash = copy_artifact(
        agent_adapter,
        snapshot_dir / "agent" / "adapter",
    )
    payload, payload_hash = copy_artifact(
        agent_payload,
        snapshot_dir / "agent" / "payload",
    )
    binary, binary_hash = copy_artifact(
        claux_binary,
        snapshot_dir / "agent" / "claux",
    )
    _, benchmark_manifest_hash = copy_artifact(
        benchmark_manifest,
        snapshot_dir / "benchmark.toml",
    )
    metadata = {
        "schema_version": 1,
        "host_harness_sha256": sha256_tree(harness_dir),
        "scenario_packs": pack_metadata,
        "agent_adapter_sha256": adapter_hash,
        "agent_payload_sha256": payload_hash,
        "agent_env_sha256": sha256_file(agent_env_file)
        if agent_env_file is not None
        else None,
        "claux_binary_sha256": binary_hash,
        "benchmark_manifest_sha256": benchmark_manifest_hash,
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )
    return ExecutionSnapshot(
        runner=harness_dir / "run-host-native.sh",
        scenario_pack_dirs=pack_dirs,
        agent_adapter=adapter,
        agent_payload=payload,
        claux_binary=binary,
        metadata=metadata,
    )


def load_execution_snapshot(matrix_dir: Path) -> ExecutionSnapshot:
    snapshot_dir = matrix_dir / "execution-snapshot"
    manifest_path = snapshot_dir / "manifest.json"
    benchmark_path = matrix_dir / "benchmark.json"
    if not manifest_path.is_file() or not benchmark_path.is_file():
        raise ValueError("resume directory is missing its execution snapshot or benchmark.json")
    metadata = json.loads(manifest_path.read_text())
    harness_dir = snapshot_dir / "host-harness"
    if sha256_tree(harness_dir) != metadata.get("host_harness_sha256"):
        raise ValueError("saved host harness does not match its recorded hash")
    pack_dirs = sorted((snapshot_dir / "scenario-packs").iterdir())
    recorded_packs = metadata.get("scenario_packs") or []
    if len(pack_dirs) != len(recorded_packs):
        raise ValueError("saved scenario pack count does not match the snapshot manifest")
    for path, recorded in zip(pack_dirs, recorded_packs, strict=True):
        if sha256_tree(path) != recorded.get("sha256"):
            raise ValueError(f"saved scenario pack changed: {path}")

    def optional_artifact(name: str, hash_name: str) -> Path | None:
        path = snapshot_dir / "agent" / name
        expected = metadata.get(hash_name)
        if expected is None:
            return None
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"saved agent {name} does not match its recorded hash")
        return path

    return ExecutionSnapshot(
        runner=harness_dir / "run-host-native.sh",
        scenario_pack_dirs=pack_dirs,
        agent_adapter=optional_artifact("adapter", "agent_adapter_sha256"),
        agent_payload=optional_artifact("payload", "agent_payload_sha256"),
        claux_binary=optional_artifact("claux", "claux_binary_sha256"),
        metadata=metadata,
    )


@contextlib.contextmanager
def stage_runtime_env(source: Path | None) -> Iterable[Path | None]:
    if source is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="replaybook-matrix-secrets.") as temporary:
        destination = Path(temporary) / "runtime.env"
        shutil.copy2(source, destination)
        destination.chmod(0o600)
        yield destination


def discover_scenarios(
    scenarios_dir: Path = DEFAULT_SCENARIO_PACK,
) -> dict[str, int]:
    _, scenarios = discover([scenarios_dir])
    return {scenario_id: scenario.version for scenario_id, scenario in scenarios.items()}


def check_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise ValueError(f"host port is already in use: {port}") from error


def build_jobs(
    *,
    scenarios: list[str],
    models: list[str | None],
    attempts: int,
    base_port: int,
    matrix_dir: Path,
    reasoning_efforts: list[str | None] | None = None,
) -> list[Job]:
    jobs = []
    index = 0
    for scenario in scenarios:
        for model in models:
            model_slug = "oracle" if model is None else slugify(model)
            efforts = reasoning_efforts or [None]
            for reasoning_effort in efforts:
                effort_slug = (
                    f"-reasoning-{slugify(reasoning_effort)}"
                    if reasoning_effort is not None
                    else ""
                )
                for attempt in range(1, attempts + 1):
                    run_id = f"{scenario}-{model_slug}{effort_slug}-{attempt}"
                    jobs.append(
                        Job(
                            run_id=run_id,
                            scenario=scenario,
                            model=model,
                            attempt=attempt,
                            ssh_port=base_port + index * 2,
                            http_port=base_port + index * 2 + 1,
                            output_dir=matrix_dir / "runs" / run_id,
                            log_file=matrix_dir / "logs" / f"{run_id}.log",
                            reasoning_effort=reasoning_effort,
                        )
                    )
                    index += 1
    run_ids = [job.run_id for job in jobs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("model names produce colliding run IDs")
    return jobs


def load_result(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"result was not written: {path}"
    try:
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return None, f"could not read result {path}: {error}"
    required = {
        "harness_version",
        "scenario",
        "scenario_version",
        "model",
        "reward",
    }
    if not isinstance(result, dict) or not required.issubset(result):
        return None, f"result is missing required fields: {path}"
    if (
        not isinstance(result["harness_version"], int)
        or result["harness_version"] <= 0
        or not isinstance(result["scenario"], str)
        or not isinstance(result["scenario_version"], int)
        or result["scenario_version"] <= 0
        or not isinstance(result["model"], str)
        or result["reward"] not in (0, 1)
        or result.get("trial_status", "evaluated") not in TRIAL_STATUSES
    ):
        return None, f"result has invalid required fields: {path}"
    return result, None


def completed_worker(job: Job) -> WorkerResult | None:
    result, error = load_result(job.output_dir / "result.json")
    expected_model = "oracle" if job.model is None else job.model
    if result is None or (
        result["scenario"] != job.scenario
        or result["model"] != expected_model
        or result.get("reasoning_effort") != job.reasoning_effort
    ):
        return None
    return WorkerResult(
        job=job,
        exit_code=0 if result["reward"] == 1 else 1,
        result=result,
        error=error,
    )


def prepare_pending_job(job: Job) -> None:
    if job.output_dir.exists():
        shutil.rmtree(job.output_dir)


def verify_saved_result_versions(
    completed: list[WorkerResult], benchmark: dict[str, Any]
) -> None:
    expected = {
        str(item["id"]): int(item["version"])
        for item in benchmark.get("scenarios") or []
    }
    for worker in completed:
        actual = int(worker.result["scenario_version"])
        if actual != expected.get(worker.job.scenario):
            raise ValueError(
                f"saved result has wrong scenario version: {worker.job.run_id}"
            )


def build_resume_plan(matrix_dir: Path) -> ResumePlan:
    matrix_dir = matrix_dir.expanduser().resolve()
    benchmark_path = matrix_dir / "benchmark.json"
    if not matrix_dir.is_dir() or not benchmark_path.is_file():
        raise ValueError(f"resume directory is not a host matrix: {matrix_dir}")
    benchmark = json.loads(benchmark_path.read_text())
    scenarios = [str(item["id"]) for item in benchmark.get("scenarios") or []]
    models = [None if model == "oracle" else str(model) for model in benchmark.get("models") or []]
    efforts = [str(value) for value in benchmark.get("reasoning_efforts") or []] or [None]
    attempts = int(benchmark.get("attempts") or 0)
    base_port = int(benchmark.get("base_port") or 22600)
    if not scenarios or not models or attempts <= 0:
        raise ValueError("saved benchmark does not contain a complete job plan")
    jobs = build_jobs(
        scenarios=scenarios,
        models=models,
        attempts=attempts,
        base_port=base_port,
        matrix_dir=matrix_dir,
        reasoning_efforts=efforts,
    )
    completed = []
    pending = []
    for job in jobs:
        worker = completed_worker(job)
        if worker is None:
            pending.append(job)
        else:
            completed.append(worker)
    snapshot = load_execution_snapshot(matrix_dir)
    verify_saved_result_versions(completed, benchmark)
    started_at = str(benchmark.get("started_at") or utc_now())
    agent = benchmark.get("agent") or {}
    return ResumePlan(
        matrix_dir=matrix_dir,
        benchmark=benchmark,
        snapshot=snapshot,
        all_jobs=jobs,
        completed=completed,
        pending=pending,
        started_at=started_at,
        agent_timeout_seconds=int(
            benchmark.get("agent_timeout_seconds") or DEFAULT_AGENT_TIMEOUT_SECONDS
        ),
        agent_name=agent.get("name") if agent.get("adapter") != "builtin:claux" else None,
        claux_release=benchmark.get("claux_release"),
    )


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await process.wait()


async def run_worker(
    job: Job,
    *,
    runner: Path,
    environment: dict[str, str],
    semaphore: asyncio.Semaphore,
    progress: Progress,
    agent_timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
    agent_adapter: Path | None = None,
    agent_payload: Path | None = None,
    agent_env_file: Path | None = None,
    agent_name: str | None = None,
    scenario_pack_dirs: list[Path] | None = None,
) -> WorkerResult:
    async with semaphore:
        started = progress.start()
        print(
            f"[matrix] starting {started} of {progress.total}: {job.run_id} "
            f"on SSH {job.ssh_port}, "
            f"HTTP {job.http_port}",
            flush=True,
        )
        command = [
            "bash",
            str(runner),
            "--scenario",
            job.scenario,
            "--ssh-port",
            str(job.ssh_port),
            "--http-port",
            str(job.http_port),
            "--output-dir",
            str(job.output_dir),
            "--agent-timeout-seconds",
            str(agent_timeout_seconds),
        ]
        for scenario_pack_dir in scenario_pack_dirs or []:
            command.extend(["--scenario-pack", str(scenario_pack_dir)])
        if job.model is None:
            command.append("--oracle")
        else:
            command.extend(["--model", job.model])
            if job.reasoning_effort is not None:
                command.extend(["--reasoning-effort", job.reasoning_effort])
            if agent_adapter is not None:
                command.extend(["--agent-adapter", str(agent_adapter)])
            if agent_payload is not None:
                command.extend(["--agent-payload", str(agent_payload)])
            if agent_env_file is not None:
                command.extend(["--agent-env-file", str(agent_env_file)])
            if agent_name is not None:
                command.extend(["--agent-name", agent_name])

        job.log_file.parent.mkdir(parents=True, exist_ok=True)
        with job.log_file.open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=REPO_DIR,
                env=environment,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                exit_code = await process.wait()
            except asyncio.CancelledError:
                await terminate_process(process)
                raise

        result, error = load_result(job.output_dir / "result.json")
        expected_model = "oracle" if job.model is None else job.model
        if result is not None and (
            result["scenario"] != job.scenario
            or result["model"] != expected_model
            or result.get("reasoning_effort") != job.reasoning_effort
        ):
            error = f"result identity does not match scheduled job: {job.run_id}"
            result = None
        output = sys.stdout
        if result is None:
            outcome = f"infrastructure failure {job.run_id}; see {job.log_file}"
            output = sys.stderr
        elif result.get("trial_status") == "unavailable":
            category = result.get("failure_category") or "unavailable"
            outcome = f"unavailable {job.run_id} ({category})"
        elif int(result.get("reward", 0)) == 1:
            outcome = f"passed {job.run_id}"
        else:
            category = result.get("failure_category") or "uncategorized"
            outcome = f"failed {job.run_id} ({category})"
        completed = progress.complete()
        print(
            f"[matrix] completed {completed} of {progress.total}: {outcome}",
            file=output,
            flush=True,
        )
        return WorkerResult(job, exit_code, result, error)


async def run_jobs(
    jobs: list[Job],
    *,
    runner: Path,
    environment: dict[str, str],
    concurrency: int,
    agent_timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
    agent_adapter: Path | None = None,
    agent_payload: Path | None = None,
    agent_env_file: Path | None = None,
    agent_name: str | None = None,
    scenario_pack_dirs: list[Path] | None = None,
) -> list[WorkerResult]:
    semaphore = asyncio.Semaphore(concurrency)
    progress = Progress(total=len(jobs))
    tasks = [
        asyncio.create_task(
            run_worker(
                job,
                runner=runner,
                environment=environment,
                semaphore=semaphore,
                progress=progress,
                agent_timeout_seconds=agent_timeout_seconds,
                agent_adapter=agent_adapter,
                agent_payload=agent_payload,
                agent_env_file=agent_env_file,
                agent_name=agent_name,
                scenario_pack_dirs=scenario_pack_dirs,
            )
        )
        for job in jobs
    ]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [
        run
        for run in runs
        if run.get("trial_status", "evaluated") == "evaluated"
    ]
    durations = [
        float(run["agent_duration_seconds"])
        for run in evaluated
        if run.get("agent_duration_seconds") is not None
    ]
    costs = [
        float(run["usage"]["cost_usd"])
        for run in runs
        if isinstance(run.get("usage"), dict)
        and run["usage"].get("cost_usd") is not None
    ]
    usages = [run["usage"] for run in runs if isinstance(run.get("usage"), dict)]
    recordings = [
        run["recording"]
        for run in runs
        if isinstance(run.get("recording"), dict)
    ]
    model_round_counts = [
        len(recording.get("model_rounds") or []) for recording in recordings
    ]
    model_durations = [
        sum(
            float(round_.get("duration_ms") or 0)
            for round_ in recording.get("model_rounds") or []
        )
        / 1000
        for recording in recordings
    ]
    tool_counts = [len(recording.get("tools") or []) for recording in recordings]
    tool_durations = [
        sum(
            float(tool.get("duration_ms") or 0)
            for tool in recording.get("tools") or []
        )
        / 1000
        for recording in recordings
    ]
    first_non_read_only_seconds = []
    post_non_read_only_seconds = []
    for recording in recordings:
        non_read_only_tools = [
            float(tool.get("started_after_ms") or 0) / 1000
            for tool in recording.get("tools") or []
            if tool.get("read_only") is False
        ]
        if non_read_only_tools:
            first_non_read_only = min(non_read_only_tools)
            first_non_read_only_seconds.append(first_non_read_only)
            post_non_read_only_seconds.append(
                max(
                    0.0,
                    float(recording.get("total_duration_ms") or 0) / 1000
                    - first_non_read_only,
                )
            )
    passed = sum(int(run.get("reward", 0)) == 1 for run in evaluated)
    durable_repairs_after_timeout = sum(
        run.get("failure_category") == "agent_timeout"
        and isinstance(run.get("verification"), dict)
        and isinstance(run["verification"].get("after_agent_timeout"), dict)
        and run["verification"]["after_agent_timeout"].get("durable_repair") is True
        for run in evaluated
    )
    return {
        "trials": len(runs),
        "evaluated": len(evaluated),
        "unavailable": len(runs) - len(evaluated),
        "passed": passed,
        "failed": len(evaluated) - passed,
        "durable_repairs_after_timeout": durable_repairs_after_timeout,
        "pass_rate": passed / len(evaluated) if evaluated else None,
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "known_cost_usd": sum(costs),
        "cost_reported_trials": len(costs),
        "input_tokens": sum(int(usage.get("input_tokens") or 0) for usage in usages),
        "output_tokens": sum(int(usage.get("output_tokens") or 0) for usage in usages),
        "cache_read_tokens": sum(
            int(usage.get("cache_read_tokens") or 0) for usage in usages
        ),
        "usage_reported_trials": len(usages),
        "recording_reported_trials": len(recordings),
        "median_model_rounds": (
            statistics.median(model_round_counts) if model_round_counts else None
        ),
        "median_model_duration_seconds": (
            statistics.median(model_durations) if model_durations else None
        ),
        "median_tool_calls": statistics.median(tool_counts) if tool_counts else None,
        "median_tool_duration_seconds": (
            statistics.median(tool_durations) if tool_durations else None
        ),
        "median_first_non_read_only_tool_seconds": (
            statistics.median(first_non_read_only_seconds)
            if first_non_read_only_seconds
            else None
        ),
        "median_post_first_non_read_only_seconds": (
            statistics.median(post_non_read_only_seconds)
            if post_non_read_only_seconds
            else None
        ),
    }


def build_summary(
    worker_results: list[WorkerResult],
    *,
    started_at: str,
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    runs = []
    infrastructure_errors = []
    for worker in worker_results:
        if worker.result is None:
            infrastructure_errors.append(
                {
                    "run_id": worker.job.run_id,
                    "exit_code": worker.exit_code,
                    "error": worker.error,
                    "log_file": str(worker.job.log_file),
                }
            )
            continue
        run = dict(worker.result)
        transcript = worker.job.output_dir / "results" / "transcript.json"
        run.update(
            {
                "run_id": worker.job.run_id,
                "attempt": worker.job.attempt,
                "worker_exit_code": worker.exit_code,
                "result_file": str(worker.job.output_dir / "result.json"),
                "log_file": str(worker.job.log_file),
                "transcript_file": str(transcript) if transcript.is_file() else None,
            }
        )
        runs.append(run)

    harness_versions = {int(run["harness_version"]) for run in runs}
    if len(harness_versions) > 1:
        raise ValueError("matrix results contain mixed host harness versions")
    harness_version = next(iter(harness_versions), HOST_HARNESS_VERSION)

    by_model_groups: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    by_scenario_model_groups: dict[
        tuple[str, int, str, str | None], list[dict[str, Any]]
    ] = (
        defaultdict(list)
    )
    for run in runs:
        model = str(run.get("model", "unknown"))
        scenario = str(run.get("scenario", "unknown"))
        version = int(run.get("scenario_version", 0))
        reasoning_effort = run.get("reasoning_effort")
        by_model_groups[(model, reasoning_effort)].append(run)
        by_scenario_model_groups[(scenario, version, model, reasoning_effort)].append(run)

    by_model = [
        {
            "model": model,
            "reasoning_effort": reasoning_effort,
            **summarize_runs(group),
        }
        for (model, reasoning_effort), group in sorted(
            by_model_groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
        )
    ]
    by_scenario_model = [
        {
            "scenario": scenario,
            "scenario_version": version,
            "model": model,
            "reasoning_effort": reasoning_effort,
            **summarize_runs(group),
        }
        for (scenario, version, model, reasoning_effort), group in sorted(
            by_scenario_model_groups.items(),
            key=lambda item: (*item[0][:3], item[0][3] or ""),
        )
    ]
    failure_categories = Counter(
        str(run["failure_category"])
        for run in runs
        if run.get("trial_status", "evaluated") == "evaluated"
        and run.get("failure_category")
    )
    unavailable_categories = Counter(
        str(run["failure_category"])
        for run in runs
        if run.get("trial_status") == "unavailable" and run.get("failure_category")
    )

    return {
        "schema_version": 1,
        "suite": "replaybook-host-matrix-v1",
        "harness_version": harness_version,
        "started_at": started_at,
        "finished_at": utc_now(),
        "benchmark": benchmark,
        "expected_trials": len(worker_results),
        "received_results": len(runs),
        "totals": summarize_runs(runs),
        "infrastructure_errors": infrastructure_errors,
        "failure_categories": [
            {"category": category, "count": count}
            for category, count in sorted(failure_categories.items())
        ],
        "unavailable_categories": [
            {"category": category, "count": count}
            for category, count in sorted(unavailable_categories.items())
        ],
        "by_model": by_model,
        "by_scenario_model": by_scenario_model,
        "runs": runs,
    }


def display_duration(value: float | None) -> str:
    if value is None:
        return "n/a"
    minutes, seconds = divmod(round(value), 60)
    return f"{minutes}:{seconds:02d}"


def display_cost(row: dict[str, Any]) -> str:
    if row["cost_reported_trials"] == 0:
        return "n/a"
    suffix = "" if row["cost_reported_trials"] == row["trials"] else "+"
    return f"${row['known_cost_usd']:.4f}{suffix}"


def display_tokens(value: int, coverage: int) -> str:
    return "n/a" if coverage == 0 else f"{value:,}"


def print_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "model",
        "reasoning",
        "trials",
        "eval",
        "unavail",
        "passed",
        "failed",
        "pass",
        "median",
        "input",
        "output",
        "cost",
    ]
    formatted = []
    for row in rows:
        rate = row["pass_rate"]
        formatted.append(
            [
                str(row["model"]),
                str(row.get("reasoning_effort") or "default"),
                str(row["trials"]),
                str(row["evaluated"]),
                str(row["unavailable"]),
                str(row["passed"]),
                str(row["failed"]),
                "n/a" if rate is None else f"{rate:.0%}",
                display_duration(row["median_duration_seconds"]),
                display_tokens(row["input_tokens"], row["usage_reported_trials"]),
                display_tokens(row["output_tokens"], row["usage_reported_trials"]),
                display_cost(row),
            ]
        )
    widths = [
        max([len(headers[index]), *(len(row[index]) for row in formatted)])
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in formatted:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_scenario_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "scenario",
        "ver",
        "model",
        "reasoning",
        "trials",
        "eval",
        "unavail",
        "passed",
        "failed",
        "median",
    ]
    formatted = [
        [
            str(row["scenario"]),
            f"v{row['scenario_version']}",
            str(row["model"]),
            str(row.get("reasoning_effort") or "default"),
            str(row["trials"]),
            str(row["evaluated"]),
            str(row["unavailable"]),
            str(row["passed"]),
            str(row["failed"]),
            display_duration(row["median_duration_seconds"]),
        ]
        for row in rows
    ]
    widths = [
        max([len(headers[index]), *(len(row[index]) for row in formatted)])
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in formatted:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_recording_table(rows: list[dict[str, Any]]) -> None:
    rows = [row for row in rows if row["recording_reported_trials"]]
    if not rows:
        return
    headers = [
        "model",
        "reasoning",
        "recorded",
        "rounds",
        "model time",
        "tools",
        "tool time",
        "first non-read",
        "after non-read",
    ]
    formatted = [
        [
            str(row["model"]),
            str(row.get("reasoning_effort") or "default"),
            f"{row['recording_reported_trials']}/{row['trials']}",
            f"{row['median_model_rounds']:g}",
            display_duration(row["median_model_duration_seconds"]),
            f"{row['median_tool_calls']:g}",
            display_duration(row["median_tool_duration_seconds"]),
            display_duration(row["median_first_non_read_only_tool_seconds"]),
            display_duration(row["median_post_first_non_read_only_seconds"]),
        ]
        for row in rows
    ]
    widths = [
        max([len(headers[index]), *(len(row[index]) for row in formatted)])
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in formatted:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_summary(summary: dict[str, Any], summary_file: Path) -> None:
    print()
    print_table(summary["by_model"])
    print()
    print_scenario_table(summary["by_scenario_model"])
    if summary["totals"]["recording_reported_trials"]:
        print("\nExecution recording (medians):")
        print_recording_table(summary["by_model"])
    if summary["failure_categories"]:
        print("\nFailure categories:")
        for row in summary["failure_categories"]:
            print(f"  {row['category']}: {row['count']}")
    if summary["totals"]["durable_repairs_after_timeout"]:
        print(
            "\nPost-timeout verification:\n"
            f"  durable repairs: {summary['totals']['durable_repairs_after_timeout']} "
            "(still scored as agent_timeout)"
        )
    if summary["unavailable_categories"]:
        print("\nUnavailable trial categories:")
        for row in summary["unavailable_categories"]:
            print(f"  {row['category']}: {row['count']}")
    print(f"\n[matrix] summary: {summary_file}")


def summary_exit_status(summary: dict[str, Any], expected_trials: int) -> int:
    infrastructure_errors = summary["infrastructure_errors"]
    if infrastructure_errors:
        print(
            f"[matrix] incomplete: {len(infrastructure_errors)} trials produced "
            "no valid result",
            file=sys.stderr,
        )
        return 1
    if summary["totals"]["unavailable"]:
        print(
            f"[matrix] incomplete: {summary['totals']['unavailable']} trials "
            "were unavailable and excluded from pass rates",
            file=sys.stderr,
        )
        return 1
    print(
        f"[matrix] all {expected_trials} trials completed; "
        f"{summary['totals']['failed']} evaluation failures"
    )
    return 0


def current_commit() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run host-native Replaybook scenarios across models and attempts."
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        help="executable benchmark.toml defining the pack, scenarios, attempts, "
        "timeout, and required host harness",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help=f"scenario ID; repeat for multiple scenarios (default: {DEFAULT_SCENARIO})",
    )
    parser.add_argument(
        "--scenario-pack",
        action="append",
        type=Path,
        dest="scenario_packs",
        help="versioned host scenario pack; repeat to combine packs "
        "(default: bundled pack)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        help="OpenRouter model IDs; omit only with --oracle",
    )
    parser.add_argument(
        "--reasoning-efforts",
        nargs="+",
        help="Claux reasoning efforts to compare, such as low high",
    )
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=None,
        help=f"maximum agent runtime per trial (default: {DEFAULT_AGENT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=22600,
        help="first port; each trial uses adjacent SSH and HTTP ports",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--resume",
        type=Path,
        metavar="MATRIX_DIR",
        help="resume an interrupted matrix from its immutable execution snapshot",
    )
    parser.add_argument("--claux-binary", type=Path)
    parser.add_argument("--claux-release")
    parser.add_argument("--agent-adapter", type=Path)
    parser.add_argument("--agent-payload", type=Path)
    parser.add_argument("--agent-env-file", type=Path)
    parser.add_argument("--agent-name")
    parser.add_argument("--oracle", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the benchmark and selected pack without starting VMs",
    )
    parser.add_argument("--list-scenarios", action="store_true")
    return parser.parse_args(argv)


def apply_benchmark_manifest(args: argparse.Namespace) -> BenchmarkManifest | None:
    if args.benchmark is None:
        args.attempts = 1 if args.attempts is None else args.attempts
        args.agent_timeout_seconds = (
            DEFAULT_AGENT_TIMEOUT_SECONDS
            if args.agent_timeout_seconds is None
            else args.agent_timeout_seconds
        )
        return None
    manifest = load_benchmark_manifest(args.benchmark)
    conflicts = []
    if args.scenarios:
        conflicts.append("--scenario")
    if args.scenario_packs:
        conflicts.append("--scenario-pack")
    if args.attempts is not None:
        conflicts.append("--attempts")
    if args.agent_timeout_seconds is not None:
        conflicts.append("--agent-timeout-seconds")
    if conflicts:
        raise ValueError(
            "--benchmark defines scenarios, packs, attempts, and timeout; remove "
            + ", ".join(conflicts)
        )
    args.scenario_packs = [manifest.pack_path]
    args.scenarios = [scenario.id for scenario in manifest.scenarios]
    args.attempts = 1 if args.oracle else manifest.attempts
    args.agent_timeout_seconds = manifest.agent_timeout_seconds
    return manifest


def validate_args(args: argparse.Namespace, available: dict[str, int]) -> None:
    if args.attempts <= 0:
        raise ValueError("--attempts must be a positive integer")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be a positive integer")
    if args.agent_timeout_seconds <= 0:
        raise ValueError("--agent-timeout-seconds must be a positive integer")
    if args.oracle and args.models:
        raise ValueError("--oracle cannot be combined with --models")
    if args.oracle and args.check:
        raise ValueError("--oracle cannot be combined with --check")
    if args.oracle and args.reasoning_efforts:
        raise ValueError("--oracle cannot be combined with --reasoning-efforts")
    if args.oracle and any(
        (
            args.agent_adapter,
            args.agent_payload,
            args.agent_env_file,
            args.agent_name,
        )
    ):
        raise ValueError("--oracle cannot be combined with agent adapter options")
    if args.agent_adapter is None and any(
        (args.agent_payload, args.agent_env_file, args.agent_name)
    ):
        raise ValueError("custom agent options require --agent-adapter")
    if args.agent_adapter is not None and args.reasoning_efforts:
        raise ValueError("--reasoning-efforts is supported only by the built-in Claux adapter")
    if args.reasoning_efforts:
        invalid = set(args.reasoning_efforts) - REASONING_EFFORTS
        if invalid:
            raise ValueError(
                "unsupported reasoning effort: " + ", ".join(sorted(invalid))
            )
    if args.agent_name and not SCENARIO_ID_PATTERN.fullmatch(args.agent_name):
        raise ValueError("--agent-name contains unsafe characters")
    if args.agent_adapter is not None and args.claux_binary:
        raise ValueError("--agent-adapter cannot be combined with --claux-binary")
    if args.agent_adapter is not None and args.claux_release:
        raise ValueError("--agent-adapter cannot be combined with --claux-release")
    if not args.oracle and not args.models and not args.list_scenarios and not args.check:
        raise ValueError("--models is required unless --oracle is used")
    for scenario in unique(args.scenarios or [DEFAULT_SCENARIO]):
        if not SCENARIO_ID_PATTERN.fullmatch(scenario) or scenario not in available:
            raise ValueError(f"unknown host-native scenario: {scenario}")


def validate_resume_args(args: argparse.Namespace) -> None:
    conflicts = []
    for flag, value in (
        ("--benchmark", args.benchmark),
        ("--scenario", args.scenarios),
        ("--scenario-pack", args.scenario_packs),
        ("--models", args.models),
        ("--reasoning-efforts", args.reasoning_efforts),
        ("--attempts", args.attempts),
        ("--agent-timeout-seconds", args.agent_timeout_seconds),
        ("--base-port", args.base_port if args.base_port != 22600 else None),
        ("--output-dir", args.output_dir),
        ("--claux-binary", args.claux_binary),
        ("--claux-release", args.claux_release),
        ("--agent-adapter", args.agent_adapter),
        ("--agent-payload", args.agent_payload),
        ("--agent-name", args.agent_name),
        ("--oracle", args.oracle),
        ("--check", args.check),
        ("--list-scenarios", args.list_scenarios),
    ):
        if value:
            conflicts.append(flag)
    if conflicts:
        raise ValueError("--resume cannot be combined with " + ", ".join(conflicts))
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be a positive integer")


def matrix_directory(supplied: Path | None) -> Path:
    if supplied is not None:
        path = supplied.expanduser()
        if not path.is_absolute():
            path = REPO_DIR / path
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d__%H-%M-%S")
        path = REPO_DIR / "jobs" / f"host-matrix-{timestamp}.{secrets.token_hex(3)}"
    if path.exists():
        raise ValueError(f"output directory already exists: {path}")
    return path


def resume_matrix(args: argparse.Namespace) -> int:
    try:
        validate_resume_args(args)
        plan = build_resume_plan(args.resume)
        if args.agent_env_file is not None:
            agent_env_file = args.agent_env_file.expanduser().resolve()
            if not agent_env_file.is_file():
                raise ValueError(f"--agent-env-file does not exist: {agent_env_file}")
            expected_hash = plan.snapshot.metadata.get("agent_env_sha256")
            if expected_hash is None or sha256_file(agent_env_file) != expected_hash:
                raise ValueError("--agent-env-file does not match the original matrix")
        else:
            agent_env_file = None
        for job in plan.pending:
            check_port_available(job.ssh_port)
            check_port_available(job.http_port)
        if plan.pending and not any(job.model is None for job in plan.pending):
            if plan.snapshot.agent_adapter is None and not os.environ.get("OPENROUTER_API_KEY"):
                raise ValueError("OPENROUTER_API_KEY is required by the saved Claux adapter")
        for job in plan.pending:
            prepare_pending_job(job)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: could not resume matrix: {error}", file=sys.stderr)
        return 2

    environment = dict(os.environ)
    if plan.snapshot.claux_binary:
        environment["REPLAYBOOK_HOST_CLAUX_BINARY"] = str(plan.snapshot.claux_binary)
    if plan.claux_release:
        environment["REPLAYBOOK_HOST_CLAUX_RELEASE"] = plan.claux_release

    print(f"[matrix] resuming: {plan.matrix_dir}")
    print(
        "[matrix] execution snapshot: "
        f"{plan.snapshot.metadata['host_harness_sha256'][:12]} "
        f"({plan.matrix_dir / 'execution-snapshot'})"
    )
    print(
        f"[matrix] recovered {len(plan.completed)} of {len(plan.all_jobs)} valid results; "
        f"running {len(plan.pending)} remaining trials with at most {args.concurrency} VMs"
    )
    try:
        with stage_runtime_env(agent_env_file) as runtime_env:
            resumed = asyncio.run(
                run_jobs(
                    plan.pending,
                    runner=plan.snapshot.runner,
                    environment=environment,
                    concurrency=args.concurrency,
                    agent_timeout_seconds=plan.agent_timeout_seconds,
                    agent_adapter=plan.snapshot.agent_adapter,
                    agent_payload=plan.snapshot.agent_payload,
                    agent_env_file=runtime_env,
                    agent_name=plan.agent_name,
                    scenario_pack_dirs=plan.snapshot.scenario_pack_dirs,
                )
            )
    except KeyboardInterrupt:
        print("\n[matrix] interrupted; active workers terminated", file=sys.stderr)
        return 130

    worker_results = [*plan.completed, *resumed]
    summary = build_summary(
        worker_results,
        started_at=plan.started_at,
        benchmark=plan.benchmark,
    )
    summary_file = plan.matrix_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2) + "\n")
    print_summary(summary, summary_file)
    return summary_exit_status(summary, len(plan.all_jobs))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.resume is not None:
        return resume_matrix(args)
    try:
        manifest = apply_benchmark_manifest(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    scenario_pack_dirs = [
        path.expanduser().resolve()
        for path in (args.scenario_packs or [DEFAULT_SCENARIO_PACK])
    ]
    try:
        packs, discovered = discover(scenario_pack_dirs)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    available = {
        scenario_id: scenario.version
        for scenario_id, scenario in discovered.items()
    }
    try:
        if manifest is not None:
            manifest.validate_environment(packs, discovered, HOST_HARNESS_VERSION)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.list_scenarios:
        selected = args.scenarios or list(available)
        for scenario in selected:
            version = available[scenario]
            print(f"{scenario}\tv{version}")
        return 0

    try:
        validate_args(args, available)
        scenarios = unique(args.scenarios or [DEFAULT_SCENARIO])
        if args.check:
            if manifest is None:
                raise ValueError("--check requires --benchmark")
            print(f"[benchmark] {manifest.id}@{manifest.version} is valid")
            print(f"[benchmark] pack: {manifest.pack_id}@{manifest.pack_version}")
            print(
                f"[benchmark] {len(scenarios)} scenarios, "
                f"{manifest.attempts} attempts per model, "
                f"{manifest.agent_timeout_seconds}-second timeout"
            )
            return 0
        models: list[str | None] = [None] if args.oracle else unique(args.models)
        reasoning_efforts: list[str | None] = (
            unique(args.reasoning_efforts) if args.reasoning_efforts else [None]
        )
        matrix_dir = matrix_directory(args.output_dir)
        jobs = build_jobs(
            scenarios=scenarios,
            models=models,
            attempts=args.attempts,
            base_port=args.base_port,
            matrix_dir=matrix_dir,
            reasoning_efforts=reasoning_efforts,
        )
        if jobs[-1].http_port > 65535 or args.base_port <= 0:
            raise ValueError("matrix port range must stay between 1 and 65535")
        for job in jobs:
            check_port_available(job.ssh_port)
            check_port_available(job.http_port)
        if (
            not args.oracle
            and args.agent_adapter is None
            and not os.environ.get("OPENROUTER_API_KEY")
        ):
            raise ValueError("OPENROUTER_API_KEY is required by the default Claux adapter")
        if args.claux_binary and not args.claux_binary.expanduser().is_file():
            raise ValueError(f"Claux binary does not exist: {args.claux_binary}")
        for option, supplied in (
            ("--agent-adapter", args.agent_adapter),
            ("--agent-payload", args.agent_payload),
            ("--agent-env-file", args.agent_env_file),
        ):
            if supplied is not None and not supplied.expanduser().is_file():
                raise ValueError(f"{option} file does not exist: {supplied}")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    (matrix_dir / "logs").mkdir(parents=True)
    (matrix_dir / "runs").mkdir()
    try:
        snapshot = stage_execution_snapshot(
            matrix_dir,
            packs=packs,
            agent_adapter=args.agent_adapter.expanduser().resolve()
            if args.agent_adapter
            else None,
            agent_payload=args.agent_payload.expanduser().resolve()
            if args.agent_payload
            else None,
            agent_env_file=args.agent_env_file.expanduser().resolve()
            if args.agent_env_file
            else None,
            claux_binary=args.claux_binary.expanduser().resolve()
            if args.claux_binary
            else None,
            benchmark_manifest=manifest.path if manifest else None,
        )
        if (
            manifest is not None
            and snapshot.metadata["benchmark_manifest_sha256"] != manifest.sha256
        ):
            raise ValueError("benchmark manifest changed while staging the matrix")
    except (OSError, ValueError) as error:
        print(f"error: could not stage execution snapshot: {error}", file=sys.stderr)
        return 2
    environment = dict(os.environ)
    if snapshot.claux_binary:
        environment["REPLAYBOOK_HOST_CLAUX_BINARY"] = str(snapshot.claux_binary)
    if args.claux_release:
        environment["REPLAYBOOK_HOST_CLAUX_RELEASE"] = args.claux_release

    benchmark = {
        "suite": "replaybook-host-matrix-v1",
        "replaybook_commit": current_commit(),
        "benchmark_manifest": manifest.metadata() if manifest else None,
        "scenarios": [
            discovered[scenario].metadata()
            for scenario in scenarios
        ],
        "scenario_packs": [pack.metadata() for pack in packs],
        "execution_snapshot": snapshot.metadata,
        "models": ["oracle" if model is None else model for model in models],
        "reasoning_efforts": args.reasoning_efforts or [],
        "attempts": args.attempts,
        "base_port": args.base_port,
        "concurrency": args.concurrency,
        "agent_timeout_seconds": args.agent_timeout_seconds,
        "agent": {
            "name": args.agent_name
            or (args.agent_adapter.stem if args.agent_adapter else "claux"),
            "adapter": str(args.agent_adapter.expanduser().resolve())
            if args.agent_adapter
            else "builtin:claux",
            "payload": str(args.agent_payload.expanduser().resolve())
            if args.agent_payload
            else None,
        },
        "claux_release": args.claux_release
        or environment.get("REPLAYBOOK_HOST_CLAUX_RELEASE", "v20260810.0.1"),
    }
    benchmark["started_at"] = utc_now()
    (matrix_dir / "benchmark.json").write_text(json.dumps(benchmark, indent=2) + "\n")
    started_at = benchmark["started_at"]
    print(f"[matrix] results: {matrix_dir}")
    print(
        "[matrix] execution snapshot: "
        f"{snapshot.metadata['host_harness_sha256'][:12]} "
        f"({matrix_dir / 'execution-snapshot'})"
    )
    print(
        f"[matrix] launching {len(jobs)} trials with at most "
        f"{args.concurrency} VMs"
    )

    try:
        with stage_runtime_env(
            args.agent_env_file.expanduser().resolve()
            if args.agent_env_file
            else None
        ) as runtime_env:
            worker_results = asyncio.run(
                run_jobs(
                    jobs,
                    runner=snapshot.runner,
                    environment=environment,
                    concurrency=args.concurrency,
                    agent_timeout_seconds=args.agent_timeout_seconds,
                    agent_adapter=snapshot.agent_adapter,
                    agent_payload=snapshot.agent_payload,
                    agent_env_file=runtime_env,
                    agent_name=args.agent_name,
                    scenario_pack_dirs=snapshot.scenario_pack_dirs,
                )
            )
    except KeyboardInterrupt:
        print("\n[matrix] interrupted; active workers terminated", file=sys.stderr)
        return 130

    summary = build_summary(
        worker_results, started_at=started_at, benchmark=benchmark
    )
    summary_file = matrix_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2) + "\n")
    print_summary(summary, summary_file)
    return summary_exit_status(summary, len(jobs))


if __name__ == "__main__":
    raise SystemExit(main())
