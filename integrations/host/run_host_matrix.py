#!/usr/bin/env python3
"""Run a versioned matrix of host-native Replaybook evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import signal
import socket
import statistics
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parents[1]
DEFAULT_SCENARIO = "013-sidekiq-wrong-redis"
DEFAULT_AGENT_TIMEOUT_SECONDS = 900
HOST_HARNESS_VERSION = 3
SCENARIO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SCENARIO_VERSION_PATTERN = re.compile(
    r'^SCENARIO_VERSION=["\']?([1-9][0-9]*)["\']?$', re.MULTILINE
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


@dataclass(frozen=True)
class WorkerResult:
    job: Job
    exit_code: int
    result: dict[str, Any] | None
    error: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "unnamed"


def discover_scenarios(scenarios_dir: Path = SCRIPT_DIR / "scenarios") -> dict[str, int]:
    discovered = {}
    if not scenarios_dir.is_dir():
        return discovered
    for scenario_dir in sorted(path for path in scenarios_dir.iterdir() if path.is_dir()):
        typed_manifest = scenario_dir / "scenario.toml"
        legacy_manifest = scenario_dir / "scenario.conf"
        if typed_manifest.is_file():
            try:
                scenario = tomllib.loads(typed_manifest.read_text()).get("scenario", {})
            except tomllib.TOMLDecodeError:
                continue
            version = scenario.get("version") if isinstance(scenario, dict) else None
            if isinstance(version, int) and not isinstance(version, bool) and version > 0:
                discovered[scenario_dir.name] = version
        elif legacy_manifest.is_file():
            match = SCENARIO_VERSION_PATTERN.search(legacy_manifest.read_text())
            if match:
                discovered[scenario_dir.name] = int(match.group(1))
    return discovered


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
) -> list[Job]:
    jobs = []
    index = 0
    for scenario in scenarios:
        for model in models:
            model_slug = "oracle" if model is None else slugify(model)
            for attempt in range(1, attempts + 1):
                run_id = f"{scenario}-{model_slug}-{attempt}"
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
    ):
        return None, f"result has invalid required fields: {path}"
    return result, None


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
    agent_timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
) -> WorkerResult:
    async with semaphore:
        print(
            f"[matrix] starting {job.run_id} on SSH {job.ssh_port}, "
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
        if job.model is None:
            command.append("--oracle")
        else:
            command.extend(["--model", job.model])

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
            result["scenario"] != job.scenario or result["model"] != expected_model
        ):
            error = f"result identity does not match scheduled job: {job.run_id}"
            result = None
        if result is None:
            print(
                f"[matrix] infrastructure failure {job.run_id}; see {job.log_file}",
                file=sys.stderr,
                flush=True,
            )
        elif int(result.get("reward", 0)) == 1:
            print(f"[matrix] passed {job.run_id}", flush=True)
        else:
            category = result.get("failure_category") or "uncategorized"
            print(f"[matrix] failed {job.run_id} ({category})", flush=True)
        return WorkerResult(job, exit_code, result, error)


async def run_jobs(
    jobs: list[Job],
    *,
    runner: Path,
    environment: dict[str, str],
    concurrency: int,
    agent_timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
) -> list[WorkerResult]:
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(
            run_worker(
                job,
                runner=runner,
                environment=environment,
                semaphore=semaphore,
                agent_timeout_seconds=agent_timeout_seconds,
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
    durations = [
        float(run["agent_duration_seconds"])
        for run in runs
        if run.get("agent_duration_seconds") is not None
    ]
    costs = [
        float(run["usage"]["cost_usd"])
        for run in runs
        if isinstance(run.get("usage"), dict)
        and run["usage"].get("cost_usd") is not None
    ]
    usages = [run["usage"] for run in runs if isinstance(run.get("usage"), dict)]
    passed = sum(int(run.get("reward", 0)) == 1 for run in runs)
    return {
        "trials": len(runs),
        "passed": passed,
        "failed": len(runs) - passed,
        "pass_rate": passed / len(runs) if runs else None,
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "known_cost_usd": sum(costs),
        "cost_reported_trials": len(costs),
        "input_tokens": sum(int(usage.get("input_tokens") or 0) for usage in usages),
        "output_tokens": sum(int(usage.get("output_tokens") or 0) for usage in usages),
        "cache_read_tokens": sum(
            int(usage.get("cache_read_tokens") or 0) for usage in usages
        ),
        "usage_reported_trials": len(usages),
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
        transcript = worker.job.output_dir / "results" / "claux-transcript.json"
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

    by_model_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_scenario_model_groups: dict[tuple[str, int, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for run in runs:
        model = str(run.get("model", "unknown"))
        scenario = str(run.get("scenario", "unknown"))
        version = int(run.get("scenario_version", 0))
        by_model_groups[model].append(run)
        by_scenario_model_groups[(scenario, version, model)].append(run)

    by_model = [
        {"model": model, **summarize_runs(group)}
        for model, group in sorted(by_model_groups.items())
    ]
    by_scenario_model = [
        {
            "scenario": scenario,
            "scenario_version": version,
            "model": model,
            **summarize_runs(group),
        }
        for (scenario, version, model), group in sorted(
            by_scenario_model_groups.items()
        )
    ]
    failure_categories = Counter(
        str(run["failure_category"])
        for run in runs
        if run.get("failure_category")
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
        "trials",
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
                str(row["trials"]),
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
        max(len(headers[index]), *(len(row[index]) for row in formatted))
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in formatted:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_scenario_table(rows: list[dict[str, Any]]) -> None:
    headers = ["scenario", "ver", "model", "trials", "passed", "failed", "median"]
    formatted = [
        [
            str(row["scenario"]),
            f"v{row['scenario_version']}",
            str(row["model"]),
            str(row["trials"]),
            str(row["passed"]),
            str(row["failed"]),
            display_duration(row["median_duration_seconds"]),
        ]
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in formatted))
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in formatted:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


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
        "--scenario",
        action="append",
        dest="scenarios",
        help=f"scenario ID; repeat for multiple scenarios (default: {DEFAULT_SCENARIO})",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        help="OpenRouter model IDs; omit only with --oracle",
    )
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=DEFAULT_AGENT_TIMEOUT_SECONDS,
        help=f"maximum Claux runtime per trial (default: {DEFAULT_AGENT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=22600,
        help="first port; each trial uses adjacent SSH and HTTP ports",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--claux-binary", type=Path)
    parser.add_argument("--claux-release")
    parser.add_argument("--oracle", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace, available: dict[str, int]) -> None:
    if args.attempts <= 0:
        raise ValueError("--attempts must be a positive integer")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be a positive integer")
    if args.agent_timeout_seconds <= 0:
        raise ValueError("--agent-timeout-seconds must be a positive integer")
    if args.oracle and args.models:
        raise ValueError("--oracle cannot be combined with --models")
    if not args.oracle and not args.models and not args.list_scenarios:
        raise ValueError("--models is required unless --oracle is used")
    for scenario in unique(args.scenarios or [DEFAULT_SCENARIO]):
        if not SCENARIO_ID_PATTERN.fullmatch(scenario) or scenario not in available:
            raise ValueError(f"unknown host-native scenario: {scenario}")


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    available = discover_scenarios()
    if args.list_scenarios:
        for scenario, version in available.items():
            print(f"{scenario}\tv{version}")
        return 0

    try:
        validate_args(args, available)
        scenarios = unique(args.scenarios or [DEFAULT_SCENARIO])
        models: list[str | None] = [None] if args.oracle else unique(args.models)
        matrix_dir = matrix_directory(args.output_dir)
        jobs = build_jobs(
            scenarios=scenarios,
            models=models,
            attempts=args.attempts,
            base_port=args.base_port,
            matrix_dir=matrix_dir,
        )
        if jobs[-1].http_port > 65535 or args.base_port <= 0:
            raise ValueError("matrix port range must stay between 1 and 65535")
        for job in jobs:
            check_port_available(job.ssh_port)
            check_port_available(job.http_port)
        if not args.oracle and not os.environ.get("OPENROUTER_API_KEY"):
            raise ValueError("OPENROUTER_API_KEY is required unless --oracle is used")
        if args.claux_binary and not args.claux_binary.expanduser().is_file():
            raise ValueError(f"Claux binary does not exist: {args.claux_binary}")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    (matrix_dir / "logs").mkdir(parents=True)
    (matrix_dir / "runs").mkdir()
    environment = dict(os.environ)
    if args.claux_binary:
        environment["REPLAYBOOK_HOST_CLAUX_BINARY"] = str(
            args.claux_binary.expanduser().resolve()
        )
    if args.claux_release:
        environment["REPLAYBOOK_HOST_CLAUX_RELEASE"] = args.claux_release

    benchmark = {
        "suite": "replaybook-host-matrix-v1",
        "replaybook_commit": current_commit(),
        "scenarios": [
            {"id": scenario, "version": available[scenario]}
            for scenario in scenarios
        ],
        "models": ["oracle" if model is None else model for model in models],
        "attempts": args.attempts,
        "concurrency": args.concurrency,
        "agent_timeout_seconds": args.agent_timeout_seconds,
        "claux_release": args.claux_release
        or environment.get("REPLAYBOOK_HOST_CLAUX_RELEASE", "v20260808.0.0"),
    }
    (matrix_dir / "benchmark.json").write_text(json.dumps(benchmark, indent=2) + "\n")
    started_at = utc_now()
    print(f"[matrix] results: {matrix_dir}")
    print(
        f"[matrix] launching {len(jobs)} trials with at most "
        f"{args.concurrency} VMs"
    )

    try:
        worker_results = asyncio.run(
            run_jobs(
                jobs,
                runner=SCRIPT_DIR / "run-host-native.sh",
                environment=environment,
                concurrency=args.concurrency,
                agent_timeout_seconds=args.agent_timeout_seconds,
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
    print()
    print_table(summary["by_model"])
    print()
    print_scenario_table(summary["by_scenario_model"])
    if summary["failure_categories"]:
        print("\nFailure categories:")
        for row in summary["failure_categories"]:
            print(f"  {row['category']}: {row['count']}")
    print(f"\n[matrix] summary: {summary_file}")

    infrastructure_errors = summary["infrastructure_errors"]
    if infrastructure_errors:
        print(
            f"[matrix] incomplete: {len(infrastructure_errors)} trials produced "
            "no valid result",
            file=sys.stderr,
        )
        return 1
    print(
        f"[matrix] all {len(jobs)} trials completed; "
        f"{summary['totals']['failed']} evaluation failures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
