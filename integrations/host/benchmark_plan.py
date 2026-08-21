#!/usr/bin/env python3
"""Plan the smallest honest set of host matrices needed for benchmark coverage."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from .benchmark_manifest import BenchmarkManifest, load_benchmark_manifest
except ImportError:
    from benchmark_manifest import BenchmarkManifest, load_benchmark_manifest


REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_COVERAGE = REPO_DIR / "benchmark-data" / "coverage.json"
DEFAULT_CATALOG = REPO_DIR / "benchmark-data" / "catalog.json"


class PlanError(ValueError):
    """Coverage evidence cannot be turned into a safe execution plan."""


@dataclass(frozen=True)
class Lane:
    model: str
    reasoning_effort: str | None


@dataclass(frozen=True)
class Gap:
    scenario: str
    scenario_version: int
    model: str
    reasoning_effort: str | None
    reason: str


@dataclass(frozen=True)
class CommandPlan:
    scenarios: tuple[str, ...]
    models: tuple[str, ...]
    reasoning_effort: str | None
    trials: int
    base_port: int
    command: tuple[str, ...]


def read_json(path: Path) -> dict[str, Any]:
    source = path.expanduser().resolve()
    try:
        value = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError(f"could not read coverage evidence {source}: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise PlanError(f"{source}: unsupported coverage schema")
    if not isinstance(value.get("fleet"), list) or not isinstance(
        value.get("scenarios"), list
    ):
        raise PlanError(f"{source}: malformed coverage evidence")
    return value


def read_catalog(path: Path) -> dict[str, Any]:
    source = path.expanduser().resolve()
    try:
        value = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError(f"could not read benchmark catalog {source}: {error}") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("lanes"), list)
    ):
        raise PlanError(f"{source}: unsupported benchmark catalog schema")
    return value


def coverage_lanes(coverage: dict[str, Any]) -> tuple[Lane, ...]:
    lanes = []
    for item in coverage["fleet"]:
        if not isinstance(item, dict) or not isinstance(item.get("model"), str):
            raise PlanError("coverage fleet contains an invalid lane")
        effort = item.get("reasoning_effort")
        if effort is not None and not isinstance(effort, str):
            raise PlanError("coverage fleet contains an invalid reasoning effort")
        lanes.append(Lane(item["model"], effort))
    if not lanes:
        raise PlanError("coverage fleet is empty; pass --models explicitly")
    return tuple(lanes)


def selected_lanes(
    coverage: dict[str, Any],
    models: Sequence[str] | None,
    reasoning_efforts: Sequence[str] | None,
) -> tuple[Lane, ...]:
    def unique_lanes(items: Iterable[Lane]) -> tuple[Lane, ...]:
        return tuple(dict.fromkeys(items))

    if not models:
        lanes = coverage_lanes(coverage)
        if reasoning_efforts:
            requested = set(reasoning_efforts)
            lanes = tuple(lane for lane in lanes if lane.reasoning_effort in requested)
            if not lanes:
                raise PlanError("no configured coverage lanes use the requested effort")
        return unique_lanes(lanes)
    efforts: Sequence[str | None] = reasoning_efforts or ("high",)
    return unique_lanes(
        Lane(model, effort) for effort in efforts for model in models
    )


def result_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    found = set()
    for supplied in paths:
        path = supplied.expanduser().resolve()
        if path.is_file():
            found.add(path)
        elif path.is_dir():
            found.update(item.resolve() for item in path.rglob("summary.json"))
        else:
            raise PlanError(f"local result path does not exist: {path}")
    return tuple(sorted(found))


def local_sources(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError(f"could not read local evidence {path}: {error}") from error
    if not isinstance(value, dict):
        raise PlanError(f"{path}: local evidence must be a JSON object")
    if value.get("kind") == "replaybook-benchmark-submission":
        sources = (value.get("evidence") or {}).get("sources")
        if not isinstance(sources, list) or any(
            not isinstance(source, dict) for source in sources
        ):
            raise PlanError(f"{path}: malformed benchmark submission")
        return tuple(sources)
    if value.get("suite") == "replaybook-host-matrix-v1":
        benchmark = value.get("benchmark") or {}
        return (
            {
                "suite": value.get("suite"),
                "harness_versions": value.get("harness_versions")
                or [value.get("harness_version")],
                "benchmark_manifest": benchmark.get("benchmark_manifest"),
                "benchmark_tier": benchmark.get("tier"),
                "attempts": benchmark.get("attempts"),
                "agent_timeout_seconds": benchmark.get("agent_timeout_seconds"),
                "scenario_packs": benchmark.get("scenario_packs"),
                "scenarios": benchmark.get("scenarios"),
                "runs": value.get("runs"),
            },
        )
    if path.name == "summary.json":
        return ()
    raise PlanError(f"{path}: expected a host matrix summary or submission bundle")


def source_matches(manifest: BenchmarkManifest, source: dict[str, Any]) -> bool:
    benchmark = source.get("benchmark_manifest") or {}
    if benchmark.get("id") != manifest.id:
        return False
    if source.get("benchmark_tier") != manifest.tier:
        return False
    if source.get("attempts") != manifest.attempts:
        return False
    if source.get("agent_timeout_seconds") != manifest.agent_timeout_seconds:
        return False
    versions = source.get("harness_versions") or []
    if manifest.required_host_harness_version not in versions:
        return False
    packs = source.get("scenario_packs") or []
    if not any(
        isinstance(pack, dict) and pack.get("id") == manifest.pack_id for pack in packs
    ):
        return False
    expected = {(scenario.id, scenario.version) for scenario in manifest.scenarios}
    actual = {
        (scenario.get("id"), scenario.get("version"))
        for scenario in source.get("scenarios") or []
        if isinstance(scenario, dict)
    }
    return bool(expected & actual)


def local_coverage(
    manifest: BenchmarkManifest, paths: Iterable[Path]
) -> dict[tuple[str, int, str, str | None], int]:
    counts: dict[tuple[str, int, str, str | None], int] = defaultdict(int)
    for path in result_files(paths):
        for source in local_sources(path):
            if not source_matches(manifest, source):
                continue
            source_counts: dict[tuple[str, int, str, str | None], int] = defaultdict(int)
            runs = source.get("runs")
            if not isinstance(runs, list):
                raise PlanError(f"{path}: local evidence has no runs array")
            for run in runs:
                if not isinstance(run, dict) or run.get("trial_status") != "evaluated":
                    continue
                key = (
                    str(run.get("scenario")),
                    int(run.get("scenario_version", 0)),
                    str(run.get("model")),
                    run.get("reasoning_effort"),
                )
                source_counts[key] += 1
            for key, evaluated in source_counts.items():
                counts[key] = max(counts[key], evaluated)
    return counts


def boundary_reason(
    manifest: BenchmarkManifest, scenario: dict[str, Any]
) -> str | None:
    boundary = scenario.get("boundary")
    if not isinstance(boundary, dict):
        return "missing comparison boundary"
    if boundary.get("attempts") != manifest.attempts:
        return "attempt count changed"
    if boundary.get("agent_timeout_seconds") != manifest.agent_timeout_seconds:
        return "agent timeout changed"
    if manifest.tier is not None and boundary.get("tier") != manifest.tier:
        return "benchmark tier changed"
    versions = boundary.get("harness_versions")
    if not isinstance(versions, list) or manifest.required_host_harness_version not in versions:
        return "host harness changed"
    pack = boundary.get("scenario_pack")
    if not isinstance(pack, dict) or pack.get("id") != manifest.pack_id:
        return "scenario pack changed"
    return None


def plan_gaps(
    manifest: BenchmarkManifest,
    coverage: dict[str, Any],
    lanes: Sequence[Lane],
    local: dict[tuple[str, int, str, str | None], int] | None = None,
) -> tuple[Gap, ...]:
    local = local or {}
    published = {
        (item.get("scenario"), item.get("scenario_version")): item
        for item in coverage["scenarios"]
        if isinstance(item, dict)
    }
    gaps = []
    for expected in manifest.scenarios:
        uncovered_lanes = [
            lane
            for lane in lanes
            if local.get(
                (expected.id, expected.version, lane.model, lane.reasoning_effort), 0
            )
            < manifest.attempts
        ]
        if not uncovered_lanes:
            continue
        scenario = published.get((expected.id, expected.version))
        if scenario is None:
            stale_versions = [
                version
                for scenario_id, version in published
                if scenario_id == expected.id
            ]
            reason = (
                "scenario version changed"
                if stale_versions
                else "scenario has no published evidence"
            )
            gaps.extend(
                Gap(expected.id, expected.version, lane.model, lane.reasoning_effort, reason)
                for lane in uncovered_lanes
            )
            continue
        incompatible = boundary_reason(manifest, scenario)
        cells = {
            (cell.get("model"), cell.get("reasoning_effort")): cell
            for cell in scenario.get("cells", [])
            if isinstance(cell, dict)
        }
        for lane in uncovered_lanes:
            cell = cells.get((lane.model, lane.reasoning_effort))
            reason = incompatible
            if reason is None and (cell is None or cell.get("status") != "covered"):
                reason = "model/scenario cell has no current evidence"
            if reason is None and int(cell.get("evaluated", 0)) < manifest.attempts:
                reason = "compatible cohort is incomplete"
            if reason is not None:
                gaps.append(
                    Gap(
                        expected.id,
                        expected.version,
                        lane.model,
                        lane.reasoning_effort,
                        reason,
                    )
                )
    return tuple(gaps)


def build_commands(
    manifest: BenchmarkManifest,
    gaps: Sequence[Gap],
    *,
    concurrency: int,
    base_port: int,
) -> tuple[CommandPlan, ...]:
    manifest_order = {item.id: index for index, item in enumerate(manifest.scenarios)}
    missing_by_lane: dict[Lane, set[str]] = defaultdict(set)
    for gap in gaps:
        missing_by_lane[Lane(gap.model, gap.reasoning_effort)].add(gap.scenario)
    grouped: dict[tuple[str | None, tuple[str, ...]], list[str]] = defaultdict(list)
    for lane, scenarios in missing_by_lane.items():
        ordered = tuple(sorted(scenarios, key=manifest_order.__getitem__))
        grouped[(lane.reasoning_effort, ordered)].append(lane.model)

    plans = []
    next_port = base_port
    for (effort, scenarios), models in sorted(
        grouped.items(), key=lambda item: (manifest_order[item[0][1][0]], item[0][0] or "")
    ):
        ordered_models = tuple(sorted(models))
        command = [
            "python",
            "integrations/host/run_host_matrix.py",
            "--benchmark",
            str(manifest.path),
        ]
        for scenario in scenarios:
            command.extend(("--scenario", scenario))
        command.append("--models")
        command.extend(ordered_models)
        if effort is not None:
            command.extend(("--reasoning-efforts", effort))
        command.extend(("--concurrency", str(concurrency), "--base-port", str(next_port)))
        trials = len(scenarios) * len(ordered_models) * manifest.attempts
        if next_port + trials * 2 - 1 > 65535:
            raise PlanError("planned matrices exceed the available host port range")
        plans.append(
            CommandPlan(
                scenarios=scenarios,
                models=ordered_models,
                reasoning_effort=effort,
                trials=trials,
                base_port=next_port,
                command=tuple(command),
            )
        )
        next_port += trials * 2
    return tuple(plans)


def estimate(
    manifest: BenchmarkManifest,
    coverage: dict[str, Any],
    lanes: Sequence[Lane],
    gaps: Sequence[Gap],
    concurrency: int,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lane_keys = {(lane.model, lane.reasoning_effort) for lane in lanes}
    durations: dict[tuple[str, str | None], list[float]] = defaultdict(list)
    costs: dict[tuple[str, str | None], list[float]] = defaultdict(list)
    for scenario in coverage["scenarios"]:
        for cell in scenario.get("cells", []):
            key = (cell.get("model"), cell.get("reasoning_effort"))
            if key not in lane_keys or cell.get("status") != "covered":
                continue
            trials = int(cell.get("trials", 0))
            duration = cell.get("median_duration_seconds")
            if isinstance(duration, (int, float)):
                durations[key].append(float(duration))
            cost = cell.get("known_cost_usd")
            reported = int(cell.get("cost_reported_trials", 0))
            if isinstance(cost, (int, float)) and reported:
                costs[key].append(float(cost) / reported)
    if catalog is not None:
        for lane in catalog.get("lanes", []):
            if not isinstance(lane, dict):
                continue
            key = (lane.get("model"), lane.get("reasoning_effort"))
            if key not in lane_keys:
                continue
            if not durations[key] and isinstance(
                lane.get("median_duration_seconds"), (int, float)
            ):
                durations[key].append(float(lane["median_duration_seconds"]))
            reported = int(lane.get("cost_reported_trials", 0))
            if not costs[key] and reported and isinstance(
                lane.get("known_cost_usd"), (int, float)
            ):
                costs[key].append(float(lane["known_cost_usd"]) / reported)

    duration_total = 0.0
    duration_known = True
    cost_total = 0.0
    cost_known = True
    for gap in gaps:
        key = (gap.model, gap.reasoning_effort)
        if durations[key]:
            duration_total += sorted(durations[key])[len(durations[key]) // 2] * manifest.attempts
        else:
            duration_known = False
        if costs[key]:
            cost_total += (sum(costs[key]) / len(costs[key])) * manifest.attempts
        else:
            cost_known = False
    return {
        "trials": len(gaps) * manifest.attempts,
        "wall_seconds": math.ceil(duration_total / concurrency) if duration_known else None,
        "known_cost_usd": round(cost_total, 6) if cost_known else None,
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    if args.concurrency <= 0:
        raise PlanError("--concurrency must be a positive integer")
    if args.base_port <= 0 or args.base_port > 65535:
        raise PlanError("--base-port must be between 1 and 65535")
    manifest = load_benchmark_manifest(args.benchmark)
    coverage = read_json(args.coverage)
    catalog = None
    if args.catalog is not None:
        catalog = read_catalog(args.catalog)
    lanes = selected_lanes(coverage, args.models, args.reasoning_efforts)
    local = local_coverage(manifest, args.results)
    gaps = plan_gaps(manifest, coverage, lanes, local)
    commands = build_commands(
        manifest, gaps, concurrency=args.concurrency, base_port=args.base_port
    )
    return {
        "schema_version": 1,
        "benchmark": manifest.metadata(),
        "coverage_source": str(args.coverage.expanduser().resolve()),
        "local_sources": [str(path.expanduser().resolve()) for path in args.results],
        "target_attempts": manifest.attempts,
        "lanes": [asdict(lane) for lane in lanes],
        "covered_cells": len(manifest.scenarios) * len(lanes) - len(gaps),
        "possible_cells": len(manifest.scenarios) * len(lanes),
        "gaps": [asdict(gap) for gap in gaps],
        "estimate": estimate(
            manifest, coverage, lanes, gaps, args.concurrency, catalog
        ),
        "commands": [
            {
                **{key: value for key, value in asdict(command).items() if key != "command"},
                "command": shlex.join(command.command),
            }
            for command in commands
        ],
    }


def duration(value: int | None) -> str:
    if value is None:
        return "n/a"
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"


def print_plan(plan: dict[str, Any], *, shell_only: bool = False) -> None:
    if shell_only:
        for command in plan["commands"]:
            print(command["command"])
        return
    benchmark = plan["benchmark"]
    estimate_data = plan["estimate"]
    print(f"BENCHMARK PLAN  {benchmark['id']}@{benchmark['version']}")
    print(
        f"coverage: {plan['covered_cells']}/{plan['possible_cells']} cells current; "
        f"{len(plan['gaps'])} gaps"
    )
    if not plan["gaps"]:
        print("plan: no runs needed")
        return
    print(
        f"work: {estimate_data['trials']} trials in {len(plan['commands'])} matrices; "
        f"estimated wall time {duration(estimate_data['wall_seconds'])}; "
        + (
            f"estimated cost ${estimate_data['known_cost_usd']:.4f}"
            if estimate_data["known_cost_usd"] is not None
            else "estimated cost n/a"
        )
    )
    reasons: dict[str, int] = defaultdict(int)
    for gap in plan["gaps"]:
        reasons[gap["reason"]] += 1
    print("gaps:")
    for reason, count in sorted(reasons.items()):
        print(f"  {count:>3}  {reason}")
    print("commands (run sequentially):")
    for index, command in enumerate(plan["commands"], start=1):
        print(f"  # {index}: {command['trials']} trials")
        print(f"  {command['command']}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan missing benchmark coverage without rerunning current cells."
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="published catalog used for historical duration and cost estimates",
    )
    parser.add_argument(
        "--results",
        action="append",
        type=Path,
        default=[],
        help="local summary, submission bundle, or jobs directory; repeat as needed",
    )
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--reasoning-efforts", nargs="+")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--base-port", type=int, default=22600)
    parser.add_argument("--format", choices=("text", "json", "shell"), default="text")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(args)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print_plan(plan, shell_only=args.format == "shell")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
