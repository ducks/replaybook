#!/usr/bin/env python3
"""Import host-matrix summaries and build the public benchmark record."""

from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__:
    from integrations.host.benchmark_stats import wilson_interval
else:
    from benchmark_stats import wilson_interval


REPO_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path("benchmark-data")
INDEX_FILE = DATA_DIR / "index.json"
CATALOG_FILE = DATA_DIR / "catalog.json"
RELEASES_DIR = DATA_DIR / "releases"
DOCS_CURRENT = Path("docs/benchmarks.html")
DOCS_CATALOG = Path("docs/benchmark-catalog.json")
DOCS_EXPLORER = Path("docs/benchmark-explorer.html")
DOCS_HISTORY = Path("docs/benchmark-history.html")
MARKDOWN_RECORD = Path("benchmarks.md")
VERSION_PATTERN = re.compile(r"^[0-9]{8}\.[0-9]+\.[0-9]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
OPTIONAL_SNAPSHOT_HASHES = (
    "agent_adapter_sha256",
    "agent_payload_sha256",
    "agent_env_sha256",
    "claux_binary_sha256",
    "benchmark_manifest_sha256",
)
MARKDOWN_START = "<!-- replaybook:current-benchmark:start -->"
MARKDOWN_END = "<!-- replaybook:current-benchmark:end -->"
HISTORY_START = "<!-- replaybook:generated-history:start -->"
HISTORY_END = "<!-- replaybook:generated-history:end -->"


class PublishError(ValueError):
    """A benchmark cannot be imported or rendered safely."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PublishError(f"could not read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise PublishError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def required_object(value: dict[str, Any], key: str, source: Path) -> dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise PublishError(f"{source}: {key} must be an object")
    return child


def required_list(value: dict[str, Any], key: str, source: Path) -> list[Any]:
    child = value.get(key)
    if not isinstance(child, list):
        raise PublishError(f"{source}: {key} must be an array")
    return child


def compact_recording(
    recording: Any, run_id: str, source: Path
) -> dict[str, Any] | None:
    if recording is None:
        return None
    if not isinstance(recording, dict):
        raise PublishError(f"{source}: {run_id} recording must be an object or null")
    model_rounds = recording.get("model_rounds") or []
    tools = recording.get("tools") or []
    if not isinstance(model_rounds, list) or not isinstance(tools, list):
        raise PublishError(f"{source}: {run_id} recording arrays are invalid")
    model_duration = sum(
        float(round_.get("duration_ms") or 0)
        for round_ in model_rounds
        if isinstance(round_, dict)
    ) / 1000
    tool_duration = sum(
        float(tool.get("duration_ms") or 0)
        for tool in tools
        if isinstance(tool, dict)
    ) / 1000
    non_read_only = [
        float(tool.get("started_after_ms") or 0) / 1000
        for tool in tools
        if isinstance(tool, dict) and tool.get("read_only") is False
    ]
    first_non_read_only = min(non_read_only) if non_read_only else None
    total_duration = float(recording.get("total_duration_ms") or 0) / 1000
    return {
        "total_duration_seconds": total_duration,
        "model_rounds": len(model_rounds),
        "model_duration_seconds": model_duration,
        "tool_calls": len(tools),
        "tool_duration_seconds": tool_duration,
        "first_non_read_only_tool_seconds": first_non_read_only,
        "post_first_non_read_only_seconds": (
            max(0.0, total_duration - first_non_read_only)
            if first_non_read_only is not None
            else None
        ),
    }


def compact_run(run: dict[str, Any], source: Path) -> dict[str, Any]:
    required = {
        "run_id",
        "scenario",
        "scenario_version",
        "agent",
        "model",
        "attempt",
        "agent_duration_seconds",
        "agent_timeout_seconds",
        "reward",
        "trial_status",
    }
    missing = sorted(required - run.keys())
    if missing:
        raise PublishError(f"{source}: run missing fields: {', '.join(missing)}")
    if run["reward"] not in (0, 1):
        raise PublishError(f"{source}: {run['run_id']} has invalid reward")
    usage = run.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise PublishError(f"{source}: {run['run_id']} usage must be an object or null")
    verification = run.get("verification")
    if verification is not None and not isinstance(verification, dict):
        raise PublishError(
            f"{source}: {run['run_id']} verification must be an object or null"
        )
    compact = {
        "run_id": run["run_id"],
        "scenario": run["scenario"],
        "scenario_version": run["scenario_version"],
        "agent": run["agent"],
        "model": run["model"],
        "reasoning_effort": run.get("reasoning_effort"),
        "attempt": run["attempt"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "agent_duration_seconds": run["agent_duration_seconds"],
        "agent_timeout_seconds": run["agent_timeout_seconds"],
        "reward": run["reward"],
        "trial_status": run["trial_status"],
        "failure": run.get("failure"),
        "failure_category": run.get("failure_category"),
        "usage": usage,
        "verification": verification,
    }
    recording = compact_recording(run.get("recording"), run["run_id"], source)
    if recording is not None:
        compact["recording"] = recording
    return compact


def import_summary(path: Path) -> dict[str, Any]:
    summary = read_json(path)
    benchmark = required_object(summary, "benchmark", path)
    runs = [compact_run(run, path) for run in required_list(summary, "runs", path)]
    if not runs:
        raise PublishError(f"{path}: summary contains no runs")
    scenarios = benchmark.get("scenarios")
    scenario_packs = benchmark.get("scenario_packs", [])
    models = benchmark.get("models")
    reasoning_efforts = benchmark.get("reasoning_efforts", [])
    agent = benchmark.get("agent")
    if (
        not isinstance(scenarios, list)
        or not isinstance(scenario_packs, list)
        or not isinstance(models, list)
        or not isinstance(reasoning_efforts, list)
    ):
        raise PublishError(f"{path}: benchmark scenarios and models must be arrays")
    if not isinstance(agent, dict):
        raise PublishError(f"{path}: benchmark agent must be an object")
    execution_snapshot = benchmark.get("execution_snapshot")
    if isinstance(execution_snapshot, dict):
        execution_snapshot = dict(execution_snapshot)
        for key in OPTIONAL_SNAPSHOT_HASHES:
            execution_snapshot.setdefault(key, None)
    source = {
        "source": path.parent.name,
        "started_at": summary.get("started_at"),
        "finished_at": summary.get("finished_at"),
        "suite": summary.get("suite"),
        "harness_version": summary.get("harness_version"),
        "harness_versions": summary.get("harness_versions")
        or [summary.get("harness_version")],
        "replaybook_commit": benchmark.get("replaybook_commit"),
        "scenarios": scenarios,
        "scenario_packs": scenario_packs,
        "benchmark_manifest": benchmark.get("benchmark_manifest"),
        "execution_snapshot": execution_snapshot,
        "models": models,
        "reasoning_efforts": reasoning_efforts,
        "attempts": benchmark.get("attempts"),
        "concurrency": benchmark.get("concurrency"),
        "agent_timeout_seconds": benchmark.get("agent_timeout_seconds"),
        "agent": agent,
        "claux_release": benchmark.get("claux_release"),
        "runs": runs,
    }
    validate_source_matrix(source, path)
    return source


def validate_source_matrix(source: dict[str, Any], path: Path) -> None:
    models = source["models"]
    declared_reasoning_efforts = source["reasoning_efforts"]
    reasoning_efforts = declared_reasoning_efforts or [None]
    scenarios = source["scenarios"]
    scenario_packs = source["scenario_packs"]
    benchmark_manifest = source["benchmark_manifest"]
    execution_snapshot = source["execution_snapshot"]
    attempts = source["attempts"]
    harness_versions = source["harness_versions"]
    if (
        not isinstance(harness_versions, list)
        or not harness_versions
        or not all(
            isinstance(version, int) and not isinstance(version, bool)
            for version in harness_versions
        )
    ):
        raise PublishError(f"{path}: harness_versions must be an array of integers")
    if (
        not models
        or not all(isinstance(model, str) and model for model in models)
        or len(models) != len(set(models))
    ):
        raise PublishError(f"{path}: benchmark models must be unique names")
    if (
        not all(
            isinstance(effort, str) and effort in REASONING_EFFORTS
            for effort in declared_reasoning_efforts
        )
        or len(declared_reasoning_efforts) != len(set(declared_reasoning_efforts))
    ):
        raise PublishError(f"{path}: benchmark reasoning efforts are invalid")
    scenario_keys = []
    pack_keys = []
    for pack in scenario_packs:
        if (
            not isinstance(pack, dict)
            or not isinstance(pack.get("id"), str)
            or not isinstance(pack.get("version"), str)
        ):
            raise PublishError(f"{path}: benchmark scenario packs are invalid")
        pack_keys.append((pack["id"], pack["version"]))
    if len({pack_id for pack_id, _ in pack_keys}) != len(pack_keys):
        raise PublishError(f"{path}: benchmark scenario packs must be unique")
    if benchmark_manifest is not None and (
        not isinstance(benchmark_manifest, dict)
        or benchmark_manifest.get("schema_version") != 1
        or not isinstance(benchmark_manifest.get("id"), str)
        or not isinstance(benchmark_manifest.get("version"), str)
        or not isinstance(benchmark_manifest.get("sha256"), str)
        or not SHA256_PATTERN.fullmatch(benchmark_manifest["sha256"])
    ):
        raise PublishError(f"{path}: benchmark manifest metadata is invalid")
    declared_packs = {
        pack_id: version for pack_id, version in pack_keys
    }
    if execution_snapshot is not None:
        if (
            not isinstance(execution_snapshot, dict)
            or execution_snapshot.get("schema_version") != 1
            or not isinstance(execution_snapshot.get("host_harness_sha256"), str)
            or not SHA256_PATTERN.fullmatch(execution_snapshot["host_harness_sha256"])
            or not isinstance(execution_snapshot.get("scenario_packs"), list)
        ):
            raise PublishError(f"{path}: execution snapshot metadata is invalid")
        snapshot_packs = set()
        for pack in execution_snapshot["scenario_packs"]:
            if (
                not isinstance(pack, dict)
                or not isinstance(pack.get("id"), str)
                or not isinstance(pack.get("version"), str)
                or not isinstance(pack.get("sha256"), str)
                or not SHA256_PATTERN.fullmatch(pack["sha256"])
            ):
                raise PublishError(
                    f"{path}: execution snapshot scenario pack is invalid"
                )
            snapshot_packs.add((pack["id"], pack["version"]))
        if snapshot_packs != set(pack_keys):
            raise PublishError(
                f"{path}: execution snapshot does not match declared scenario packs"
            )
        for key in OPTIONAL_SNAPSHOT_HASHES:
            value = execution_snapshot.get(key)
            if value is not None and (
                not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value)
            ):
                raise PublishError(f"{path}: execution snapshot {key} is invalid")
        if benchmark_manifest is not None and (
            execution_snapshot.get("benchmark_manifest_sha256")
            != benchmark_manifest["sha256"]
        ):
            raise PublishError(
                f"{path}: execution snapshot does not match benchmark manifest"
            )
    for scenario in scenarios:
        if (
            not isinstance(scenario, dict)
            or not isinstance(scenario.get("id"), str)
            or not isinstance(scenario.get("version"), int)
        ):
            raise PublishError(f"{path}: benchmark scenarios are invalid")
        pack = scenario.get("pack")
        if pack is not None:
            if (
                not isinstance(pack, dict)
                or not isinstance(pack.get("id"), str)
                or not isinstance(pack.get("version"), str)
                or declared_packs.get(pack["id"]) != pack["version"]
            ):
                raise PublishError(
                    f"{path}: benchmark scenario references an undeclared pack"
                )
        scenario_keys.append((scenario["id"], scenario["version"]))
    if not scenario_keys or len(scenario_keys) != len(set(scenario_keys)):
        raise PublishError(f"{path}: benchmark scenarios must be unique")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts <= 0:
        raise PublishError(f"{path}: benchmark attempts must be a positive integer")

    expected = {
        (scenario, version, model, reasoning_effort, attempt)
        for scenario, version in scenario_keys
        for model in models
        for reasoning_effort in reasoning_efforts
        for attempt in range(1, attempts + 1)
    }
    actual = {
        (
            run["scenario"],
            run["scenario_version"],
            run["model"],
            run.get("reasoning_effort"),
            run["attempt"],
        )
        for run in source["runs"]
    }
    if len(actual) != len(source["runs"]):
        raise PublishError(f"{path}: summary contains duplicate trials")
    if actual != expected:
        missing = len(expected - actual)
        extra = len(actual - expected)
        raise PublishError(
            f"{path}: summary does not match its benchmark matrix "
            f"({missing} missing, {extra} unexpected trials)"
        )
    for run in source["runs"]:
        if run["agent"] != source["agent"].get("name"):
            raise PublishError(f"{path}: {run['run_id']} reports a different agent")
        if run["agent_timeout_seconds"] != source["agent_timeout_seconds"]:
            raise PublishError(f"{path}: {run['run_id']} reports a different timeout")


def compatibility_key(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "suite": source["suite"],
        "harness_version": source["harness_version"],
        "harness_versions": source["harness_versions"],
        "scenarios": source["scenarios"],
        "scenario_packs": source["scenario_packs"],
        "benchmark_manifest": source["benchmark_manifest"],
        "execution_snapshot": source["execution_snapshot"],
        "attempts": source["attempts"],
        "agent_timeout_seconds": source["agent_timeout_seconds"],
        "agent": source["agent"],
        "claux_release": source["claux_release"],
    }


def validate_compatible(sources: list[dict[str, Any]]) -> dict[str, Any]:
    expected = compatibility_key(sources[0])
    for source in sources[1:]:
        actual = compatibility_key(source)
        for key, expected_value in expected.items():
            if actual[key] != expected_value:
                raise PublishError(
                    f"incompatible summaries: {key} differs between "
                    f"{sources[0]['source']} and {source['source']}"
                )
    identities: set[tuple[str, str, str | None, int]] = set()
    for source in sources:
        for run in source["runs"]:
            identity = (
                run["scenario"],
                run["model"],
                run.get("reasoning_effort"),
                run["attempt"],
            )
            if identity in identities:
                raise PublishError(
                    "duplicate trial across summaries: " + "/".join(map(str, identity))
                )
            identities.add(identity)
    return expected


def apply_corrections(
    runs: list[dict[str, Any]], annotations: dict[str, Any]
) -> list[dict[str, Any]]:
    by_id = {run["run_id"]: run for run in runs}
    applied = []
    corrections = annotations.get("corrections", [])
    if not isinstance(corrections, list):
        raise PublishError("annotations corrections must be an array")
    for correction in corrections:
        if not isinstance(correction, dict):
            raise PublishError("every correction must be an object")
        run_id = correction.get("run_id")
        changes = correction.get("changes")
        reason = correction.get("reason")
        if run_id not in by_id:
            raise PublishError(f"correction references unknown run: {run_id}")
        if not isinstance(changes, dict) or not changes:
            raise PublishError(f"correction for {run_id} has no changes")
        if not isinstance(reason, str) or not reason.strip():
            raise PublishError(f"correction for {run_id} needs a reason")
        allowed = {"failure", "failure_category"}
        if not set(changes).issubset(allowed):
            raise PublishError(f"correction for {run_id} changes unsupported fields")
        original = {key: by_id[run_id].get(key) for key in changes}
        by_id[run_id].update(changes)
        applied.append(
            {"run_id": run_id, "original": original, "changes": changes, "reason": reason}
        )
    return applied


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [run for run in runs if run["trial_status"] == "evaluated"]
    passed = [run for run in evaluated if run["reward"] == 1]
    durations = [run["agent_duration_seconds"] for run in evaluated]
    usages = [run["usage"] for run in runs if isinstance(run.get("usage"), dict)]
    failure_categories = Counter(
        run["failure_category"]
        for run in evaluated
        if run["reward"] == 0 and run.get("failure_category")
    )
    unavailable_categories = Counter(
        run["failure_category"]
        for run in runs
        if run["trial_status"] != "evaluated" and run.get("failure_category")
    )
    durable_repairs_after_timeout = sum(
        1
        for run in evaluated
        if run["reward"] == 0
        and isinstance((run.get("verification") or {}).get("after_agent_timeout"), dict)
        and (run.get("verification") or {})["after_agent_timeout"].get(
            "durable_repair"
        )
        is True
    )
    result = {
        "trials": len(runs),
        "evaluated": len(evaluated),
        "unavailable": len(runs) - len(evaluated),
        "passed": len(passed),
        "failed": len(evaluated) - len(passed),
        "pass_rate": len(passed) / len(evaluated) if evaluated else None,
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "known_cost_usd": sum(float(usage.get("cost_usd", 0)) for usage in usages),
        "cost_reported_trials": len(usages),
        "input_tokens": sum(int(usage.get("input_tokens", 0)) for usage in usages),
        "output_tokens": sum(int(usage.get("output_tokens", 0)) for usage in usages),
        "cache_read_tokens": sum(
            int(usage.get("cache_read_tokens", 0)) for usage in usages
        ),
        "usage_reported_trials": len(usages),
        "failure_categories": dict(sorted(failure_categories.items())),
        "unavailable_categories": dict(sorted(unavailable_categories.items())),
        "durable_repairs_after_timeout": durable_repairs_after_timeout,
    }
    recordings = [
        run["recording"]
        for run in runs
        if isinstance(run.get("recording"), dict)
    ]
    if recordings:
        first_non_read_only = [
            recording["first_non_read_only_tool_seconds"]
            for recording in recordings
            if recording["first_non_read_only_tool_seconds"] is not None
        ]
        post_first_non_read_only = [
            recording["post_first_non_read_only_seconds"]
            for recording in recordings
            if recording["post_first_non_read_only_seconds"] is not None
        ]
        result.update(
            {
                "recording_reported_trials": len(recordings),
                "median_model_rounds": statistics.median(
                    recording["model_rounds"] for recording in recordings
                ),
                "median_model_duration_seconds": statistics.median(
                    recording["model_duration_seconds"] for recording in recordings
                ),
                "median_tool_calls": statistics.median(
                    recording["tool_calls"] for recording in recordings
                ),
                "median_tool_duration_seconds": statistics.median(
                    recording["tool_duration_seconds"] for recording in recordings
                ),
                "median_first_non_read_only_tool_seconds": (
                    statistics.median(first_non_read_only)
                    if first_non_read_only
                    else None
                ),
                "median_post_first_non_read_only_seconds": (
                    statistics.median(post_first_non_read_only)
                    if post_first_non_read_only
                    else None
                ),
            }
        )
    return result


def grouped_aggregates(
    runs: list[dict[str, Any]], key_fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[tuple(run[field] for field in key_fields)].append(run)
    values = []
    for key, group in sorted(grouped.items()):
        value = dict(zip(key_fields, key))
        value.update(aggregate_runs(group))
        values.append(value)
    return values


def model_group_fields(runs: list[dict[str, Any]]) -> tuple[str, ...]:
    if any(run.get("reasoning_effort") is not None for run in runs):
        return ("model", "reasoning_effort")
    return ("model",)


def scenario_model_group_fields(runs: list[dict[str, Any]]) -> tuple[str, ...]:
    if any(run.get("reasoning_effort") is not None for run in runs):
        return ("scenario", "scenario_version", "model", "reasoning_effort")
    return ("scenario", "scenario_version", "model")


def create_release(
    version: str, source_paths: list[Path], annotations: dict[str, Any]
) -> dict[str, Any]:
    if not VERSION_PATTERN.fullmatch(version):
        raise PublishError("benchmark version must use YYYYMMDD.MAJOR.PATCH DateVer")
    if not source_paths:
        raise PublishError("at least one summary is required")
    sources = [import_summary(path) for path in source_paths]
    compatibility = validate_compatible(sources)
    runs = [run for source in sources for run in source.pop("runs")]
    compatibility["reasoning_efforts"] = list(
        dict.fromkeys(
            run["reasoning_effort"]
            for run in runs
            if run.get("reasoning_effort") is not None
        )
    )
    corrections = apply_corrections(runs, annotations)
    return {
        "schema_version": 1,
        "version": version,
        "title": annotations.get("title", f"Benchmark {version}"),
        "description": annotations.get("description", ""),
        "model_labels": annotations.get("model_labels", {}),
        "model_order": annotations.get("model_order", []),
        "scenario_labels": annotations.get("scenario_labels", {}),
        "observations": annotations.get("observations", []),
        "notes": annotations.get("notes", []),
        "compatibility": compatibility,
        "sources": sources,
        "corrections": corrections,
        "totals": aggregate_runs(runs),
        "by_model": grouped_aggregates(runs, model_group_fields(runs)),
        "by_scenario_model": grouped_aggregates(runs, scenario_model_group_fields(runs)),
        "runs": runs,
    }


def validate_release(release: dict[str, Any], source: Path) -> None:
    runs = release.get("runs")
    if not isinstance(runs, list) or not runs:
        raise PublishError(f"{source}: release contains no normalized runs")
    expected = {
        "totals": aggregate_runs(runs),
        "by_model": grouped_aggregates(runs, model_group_fields(runs)),
        "by_scenario_model": grouped_aggregates(runs, scenario_model_group_fields(runs)),
    }
    for key, value in expected.items():
        if release.get(key) != value:
            raise PublishError(f"{source}: generated {key} does not match normalized runs")


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "n/a"
    rounded = round(float(seconds))
    return f"{rounded // 60}:{rounded % 60:02d}"


def format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{round(value * 100):d}%"


def money(value: float, incomplete: bool = False) -> str:
    suffix = "+" if incomplete else ""
    return f"${value:.4f}{suffix}"


def trial_noun(count: int) -> str:
    return "trial" if count == 1 else "trials"


def model_sort_key(
    release: dict[str, Any], model: str, reasoning_effort: str | None = None
) -> tuple[int, str, str]:
    order = release.get("model_order", [])
    try:
        return order.index(model), model, reasoning_effort or ""
    except ValueError:
        return len(order), model, reasoning_effort or ""


def label(release: dict[str, Any], kind: str, value: str) -> str:
    labels = release.get(f"{kind}_labels", {})
    return str(labels.get(value, value))


def model_rows(release: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        release["by_model"],
        key=lambda row: model_sort_key(
            release, row["model"], row.get("reasoning_effort")
        ),
    )


def model_variant_label(release: dict[str, Any], row: dict[str, Any]) -> str:
    model = label(release, "model", row["model"])
    effort = row.get("reasoning_effort")
    return f"{model} ({effort})" if effort is not None else model


def variant_key(row: dict[str, Any]) -> tuple[str, str | None]:
    return row["model"], row.get("reasoning_effort")


def cost_per_repair(row: dict[str, Any]) -> float | None:
    return row["known_cost_usd"] / row["passed"] if row["passed"] else None


def html_page(release: dict[str, Any]) -> str:
    totals = release["totals"]
    cost_incomplete = totals["cost_reported_trials"] < totals["trials"]
    rows = model_rows(release)
    model_cells = []
    for row in rows:
        incomplete = row["cost_reported_trials"] < row["trials"]
        repair_cost = cost_per_repair(row)
        model_cells.append(
            "          <tr>"
            f"<td>{html.escape(model_variant_label(release, row))}</td>"
            f"<td>{row['passed']}/{row['evaluated']}</td>"
            f"<td>{format_rate(row['pass_rate'])}</td>"
            f"<td>{format_duration(row['median_duration_seconds'])}</td>"
            f"<td>{row['input_tokens']:,}</td>"
            f"<td>{money(row['known_cost_usd'], incomplete)}</td>"
            f"<td>{money(repair_cost, incomplete) if repair_cost is not None else 'n/a'}</td>"
            "</tr>"
        )
    recording_cells = []
    for row in rows:
        if not row.get("recording_reported_trials"):
            continue
        recording_cells.append(
            "          <tr>"
            f"<td>{html.escape(model_variant_label(release, row))}</td>"
            f"<td>{row['recording_reported_trials']}/{row['trials']}</td>"
            f"<td>{row['median_model_rounds']:g}</td>"
            f"<td>{format_duration(row['median_model_duration_seconds'])}</td>"
            f"<td>{row['median_tool_calls']:g}</td>"
            f"<td>{format_duration(row['median_tool_duration_seconds'])}</td>"
            f"<td>{format_duration(row['median_first_non_read_only_tool_seconds'])}</td>"
            f"<td>{format_duration(row['median_post_first_non_read_only_seconds'])}</td>"
            "</tr>"
        )
    recording_section = ""
    if recording_cells:
        recording_section = f"""
    <h2>Execution recording</h2>
    <p class="small muted">Medians across trials with transcript schema v2 recording. First non-read is time before the first potentially mutating tool call; after non-read is the remaining agent time. Model and tool time can overlap.</p>
    <div class="table-scroll"><table><thead><tr><th>Model</th><th>Recorded</th><th>Rounds</th><th>Model time</th><th>Tools</th><th>Tool time</th><th>First non-read</th><th>After non-read</th></tr></thead><tbody>
{chr(10).join(recording_cells)}
    </tbody></table></div>
"""
    scenario_lookup = {
        (
            row["scenario"],
            row["scenario_version"],
            row["model"],
            row.get("reasoning_effort"),
        ): row
        for row in release["by_scenario_model"]
    }
    scenarios = [
        (item["id"], item["version"])
        for item in release["compatibility"]["scenarios"]
    ]
    scenario_lines = []
    for scenario, version in scenarios:
        cells = [
            f"<td>{html.escape(label(release, 'scenario', scenario))}</td>",
            f"<td>v{version}</td>",
        ]
        for row in rows:
            value = scenario_lookup[(scenario, version, *variant_key(row))]
            cells.append(
                f"<td>{value['passed']}/{value['evaluated']} &middot; "
                f"{format_duration(value['median_duration_seconds'])}</td>"
            )
        scenario_lines.append("          <tr>" + "".join(cells) + "</tr>")
    failure_items = "".join(
        f"<li><code>{html.escape(category)}</code>: {count}</li>"
        for category, count in totals["failure_categories"].items()
    ) or "<li>None</li>"
    unavailable_items = "".join(
        f"<li><code>{html.escape(category)}</code>: {count}</li>"
        for category, count in totals["unavailable_categories"].items()
    )
    unavailable_section = (
        f"""
    <h2>Unavailable trial categories</h2>
    <ul>{unavailable_items}</ul>"""
        if unavailable_items
        else ""
    )
    post_timeout = ""
    if totals["durable_repairs_after_timeout"]:
        post_timeout = f"""
    <div class="callout comparison-note">
      <strong>{totals['durable_repairs_after_timeout']} repairs became durable after the agent deadline.</strong>
      They remain scored as <code>agent_timeout</code>; post-timeout verification records the later operational outcome separately.
    </div>"""
    observations = "\n".join(
        f"      <p>{html.escape(str(item))}</p>"
        for item in release.get("observations", [])
    )
    corrections = ""
    if release["corrections"]:
        correction_items = "".join(
            f"<li><code>{html.escape(item['run_id'])}</code>: "
            f"{html.escape(item['reason'])}</li>"
            for item in release["corrections"]
        )
        corrections = f"""
    <div class="callout comparison-note">
      <strong>Documented post-run corrections</strong>
      <ul>{correction_items}</ul>
    </div>"""
    notes = ""
    if release.get("notes"):
        note_items = "".join(
            f"<li>{html.escape(str(item))}</li>" for item in release["notes"]
        )
        notes = f"""
    <div class="callout comparison-note">
      <strong>Run notes</strong>
      <ul>{note_items}</ul>
    </div>"""
    source_rows = "\n".join(
        "          <tr>"
        f"<td><code>{html.escape(source['source'])}</code></td>"
        f"<td>{html.escape(', '.join(label(release, 'model', model) for model in source['models']))}"
        f"{html.escape(' · reasoning ' + '/'.join(source.get('reasoning_efforts', []))) if source.get('reasoning_efforts') else ''}</td>"
        f"<td><code>{html.escape(str(source['replaybook_commit'])[:8])}</code></td>"
        "</tr>"
        for source in release["sources"]
    )
    model_headers = "".join(
        f"<th>{html.escape(model_variant_label(release, row))}</th>" for row in rows
    )
    attempts = release["compatibility"]["attempts"]
    command_models = (" " + "\\" + "\n    ").join(
        dict.fromkeys(row["model"] for row in rows)
    )
    reasoning_efforts = release["compatibility"].get("reasoning_efforts", [])
    command_reasoning = (
        " " + "\\" + "\n  --reasoning-efforts " + " ".join(reasoning_efforts)
        if reasoning_efforts
        else ""
    )
    command_scenarios = (" " + "\\" + "\n  --scenario ").join(
        scenario for scenario, _ in scenarios
    )
    scenario_packs = release["compatibility"].get("scenario_packs", [])
    pack_note = ""
    pack_compatibility = ""
    if scenario_packs:
        pack_compatibility = "scenario pack revisions, "
        pack_names = ", ".join(
            f"<code>{html.escape(pack['id'])}@{html.escape(pack['version'])}</code>"
            for pack in scenario_packs
        )
        pack_note = f"\n    <p class=\"small muted\">Scenario packs: {pack_names}.</p>"
    source_count = len(release["sources"])
    source_noun = "matrix" if source_count == 1 else "matrices"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Current benchmarks &middot; replaybook</title>
  <meta name="description" content="Current Replaybook infrastructure-agent benchmark results.">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div class="wrap wide">
    <header class="site">
      <a class="brand" href="index.html"><span class="prompt">$</span> replaybook</a>
      <nav class="site">
        <a href="index.html">Home</a>
        <a href="usage.html">Usage</a>
        <a href="scenarios.html">Scenarios</a>
        <a href="benchmarks.html" class="active">Benchmarks</a>
        <a href="https://github.com/ducks/replaybook">GitHub</a>
      </nav>
    </header>

    <nav class="benchmark-tabs" aria-label="Benchmark sections">
      <a href="benchmarks.html" class="active" aria-current="page">Current</a>
      <a href="benchmark-explorer.html">Explore</a>
      <a href="benchmark-history.html">History</a>
      <a href="benchmark-methodology.html">Methodology</a>
    </nav>

    <p class="eyebrow">Benchmark {html.escape(release['version'])}</p>
    <h1>{html.escape(release['title'])}</h1>
    <p class="tagline benchmark-tagline">{html.escape(release['description'])}</p>

    <div class="metric-grid" aria-label="Current benchmark results">
      <div class="metric"><span class="metric-value">{totals['passed']}/{totals['evaluated']}</span><span class="metric-label">durable repairs</span></div>
      <div class="metric"><span class="metric-value">{format_duration(totals['median_duration_seconds'])}</span><span class="metric-label">overall median</span></div>
      <div class="metric"><span class="metric-value">{money(totals['known_cost_usd'], cost_incomplete)}</span><span class="metric-label">known total cost</span></div>
    </div>

    <div class="callout benchmark-status verified-status">
      <strong>{totals['trials']} trials across {source_count} controlled {source_noun}.</strong>
      {totals['passed']} repairs passed durable verification. {totals['failed']} evaluated attempts failed and {totals['unavailable']} {trial_noun(totals['unavailable'])} {"was" if totals['unavailable'] == 1 else "were"} unavailable.
    </div>

    <h2>Model summary</h2>
    <div class="table-scroll"><table><thead><tr><th>Model</th><th>Repairs</th><th>Pass rate</th><th>Median</th><th>Input tokens</th><th>Known cost</th><th>Cost / repair</th></tr></thead><tbody>
{chr(10).join(model_cells)}
    </tbody></table></div>
{recording_section}

{observations}

    <h2>Scenario breakdown</h2>
    <div class="table-scroll"><table><thead><tr><th>Scenario</th><th>Version</th>{model_headers}</tr></thead><tbody>
{chr(10).join(scenario_lines)}
    </tbody></table></div>

    <h2>Failure categories</h2>
    <ul>{failure_items}</ul>
{unavailable_section}{post_timeout}{corrections}{notes}

    <h2>Constituent matrices</h2>
    <p>The publisher recorded harness provenance and validated matching {pack_compatibility}scenario versions, attempts, timeout, agent adapter, and Claux release before combining these summaries.</p>{pack_note}
    <div class="table-scroll"><table><thead><tr><th>Matrix</th><th>Models</th><th>Replaybook commit</th></tr></thead><tbody>
{source_rows}
    </tbody></table></div>

    <h2>Run the matrix</h2>
    <pre><code>python integrations/host/run_host_matrix.py \\
  --scenario {command_scenarios} \\
  --models \\
    {command_models}{command_reasoning} \\
  --attempts {attempts} \\
  --concurrency 2</code></pre>

    <p class="small muted">Host harness {html.escape('/'.join('v' + str(version) for version in release['compatibility']['harness_versions']))}, Claux <code>{html.escape(str(release['compatibility']['claux_release']))}</code>, {release['compatibility']['agent_timeout_seconds']}-second agent timeout. Usage was reported for {totals['usage_reported_trials']} of {totals['trials']} trials.</p>

    <p>Read the <a href="benchmark-methodology.html">methodology</a>, browse the <a href="benchmark-history.html">versioned history</a>, or inspect the <a href="https://github.com/ducks/replaybook/blob/main/benchmarks.md">complete benchmark record</a>.</p>

    <footer class="site"><a href="https://github.com/ducks/replaybook">github.com/ducks/replaybook</a> &middot; <a href="https://crates.io/crates/replaybook">crates.io</a></footer>
  </div>
</body>
</html>
"""


def markdown_section(release: dict[str, Any]) -> str:
    totals = release["totals"]
    incomplete = totals["cost_reported_trials"] < totals["trials"]
    rows = model_rows(release)
    lines = [
        MARKDOWN_START,
        f"## {release['title']}",
        "",
        release["description"],
        "",
        f"Benchmark release: `{release['version']}`",
        "",
        "| Model | Durable repairs | Pass rate | Median | Known cost | Cost per repair |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    scenario_packs = release["compatibility"].get("scenario_packs", [])
    if scenario_packs:
        pack_names = ", ".join(
            f"`{pack['id']}@{pack['version']}`" for pack in scenario_packs
        )
        lines[7:7] = [f"Scenario packs: {pack_names}", ""]
    for row in rows:
        row_incomplete = row["cost_reported_trials"] < row["trials"]
        repair_cost = cost_per_repair(row)
        lines.append(
            f"| {model_variant_label(release, row)} | {row['passed']}/{row['evaluated']} "
            f"| {format_rate(row['pass_rate'])} | {format_duration(row['median_duration_seconds'])} "
            f"| {money(row['known_cost_usd'], row_incomplete)} | "
            f"{money(repair_cost, row_incomplete) if repair_cost is not None else 'n/a'} |"
        )
    lines.extend(
        [
            f"| **Total** | **{totals['passed']}/{totals['evaluated']}** | "
            f"**{format_rate(totals['pass_rate'])}** | "
            f"**{format_duration(totals['median_duration_seconds'])}** | "
            f"**{money(totals['known_cost_usd'], incomplete)}** | "
            f"**{money(cost_per_repair(totals), incomplete)}** |",
            "",
        ]
    )
    if totals["unavailable_categories"]:
        lines.extend(
            [
                "### Unavailable trial categories",
                "",
                *[
                    f"- `{category}`: {count}"
                    for category, count in totals["unavailable_categories"].items()
                ],
                "",
            ]
        )
    if totals["durable_repairs_after_timeout"]:
        lines.extend(
            [
                "### Post-timeout verification",
                "",
                f"{totals['durable_repairs_after_timeout']} repairs became durable after the agent deadline. They remain scored as `agent_timeout`; post-timeout verification records the later operational outcome separately.",
                "",
            ]
        )
    recording_rows = [row for row in rows if row.get("recording_reported_trials")]
    if recording_rows:
        lines.extend(
            [
                "### Execution recording",
                "",
                "Medians across trials with transcript schema v2 recording. First non-read is time before the first potentially mutating tool call; after non-read is the remaining agent time. Model and tool time can overlap.",
                "",
                "| Model | Recorded | Rounds | Model time | Tools | Tool time | First non-read | After non-read |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in recording_rows:
            lines.append(
                f"| {model_variant_label(release, row)} | "
                f"{row['recording_reported_trials']}/{row['trials']} | "
                f"{row['median_model_rounds']:g} | "
                f"{format_duration(row['median_model_duration_seconds'])} | "
                f"{row['median_tool_calls']:g} | "
                f"{format_duration(row['median_tool_duration_seconds'])} | "
                f"{format_duration(row['median_first_non_read_only_tool_seconds'])} | "
                f"{format_duration(row['median_post_first_non_read_only_seconds'])} |"
            )
        lines.append("")
    lines.extend(f"{item}\n" for item in release.get("observations", []))
    scenario_lookup = {
        (
            row["scenario"],
            row["scenario_version"],
            row["model"],
            row.get("reasoning_effort"),
        ): row
        for row in release["by_scenario_model"]
    }
    lines.extend(
        [
            "### Scenario breakdown",
            "",
            "| Scenario | Version | "
            + " | ".join(model_variant_label(release, row) for row in rows)
            + " |",
            "|---|---:|" + "---:|" * len(rows),
        ]
    )
    for scenario in release["compatibility"]["scenarios"]:
        values = []
        for row in rows:
            aggregate = scenario_lookup[
                (scenario["id"], scenario["version"], *variant_key(row))
            ]
            values.append(
                f"{aggregate['passed']}/{aggregate['evaluated']}, "
                f"{format_duration(aggregate['median_duration_seconds'])}"
            )
        lines.append(
            f"| {label(release, 'scenario', scenario['id'])} | "
            f"v{scenario['version']} | " + " | ".join(values) + " |"
        )
    lines.extend(
        [
            "",
            "### Failure categories",
            "",
            *[
                f"- `{category}`: {count}"
                for category, count in totals["failure_categories"].items()
            ],
            "",
            "### Source matrices",
            "",
        ]
    )
    for source in release["sources"]:
        reasoning = (
            f"; reasoning {'/'.join(source.get('reasoning_efforts', []))}"
            if source.get("reasoning_efforts")
            else ""
        )
        lines.append(
            f"- `{source['source']}`: {', '.join(source['models'])}; "
            f"Replaybook `{str(source['replaybook_commit'])[:8]}`{reasoning}"
        )
    if release.get("notes"):
        lines.extend(["", "### Run notes", ""])
        lines.extend(f"- {item}" for item in release["notes"])
    if release["corrections"]:
        lines.extend(["", "### Post-run corrections", ""])
        for item in release["corrections"]:
            lines.append(f"- `{item['run_id']}`: {item['reason']}")
    lines.extend(["", MARKDOWN_END])
    return "\n".join(lines) + "\n"


def replace_managed(text: str, start: str, end: str, content: str) -> str:
    if start in text and end in text:
        before, remainder = text.split(start, 1)
        _, after = remainder.split(end, 1)
        return before + content.rstrip() + "\n\n" + after.lstrip("\n")
    raise PublishError(f"managed markers are missing: {start} / {end}")


def history_cards(index: dict[str, Any], root: Path) -> str:
    current = index["current_version"]
    cards = []
    for version in reversed(index["releases"]):
        if version == current:
            continue
        release = read_json(root / RELEASES_DIR / f"{version}.json")
        totals = release["totals"]
        cards.append(
            f"""    <div class="section-heading">
      <div><h3>{html.escape(release['title'])}</h3><p class="muted">Benchmark {html.escape(version)}</p></div>
      <span class="badge archived">Superseded</span>
    </div>
    <p>{totals['passed']}/{totals['evaluated']} durable repairs, {format_duration(totals['median_duration_seconds'])} median, {money(totals['known_cost_usd'], totals['cost_reported_trials'] < totals['trials'])} known cost.</p>"""
        )
    return HISTORY_START + "\n" + "\n".join(cards) + "\n    " + HISTORY_END


def public_catalog(index: dict[str, Any], root: Path) -> dict[str, Any]:
    """Build the path-free catalog consumed by the static benchmark explorer."""
    releases = []
    records = []
    for version in index["releases"]:
        path = root / RELEASES_DIR / f"{version}.json"
        release = read_json(path)
        if version == index["current_version"]:
            validate_release(release, path)
        compatibility = release["compatibility"]
        releases.append(
            {
                "version": version,
                "title": release["title"],
                "description": release["description"],
                "current": version == index["current_version"],
                "harness_versions": compatibility.get("harness_versions")
                or [compatibility["harness_version"]],
                "scenario_packs": compatibility.get("scenario_packs", []),
                "claux_release": compatibility["claux_release"],
                "agent_timeout_seconds": compatibility["agent_timeout_seconds"],
                "attempts": compatibility["attempts"],
                "totals": release["totals"],
            }
        )
        for aggregate in release["by_scenario_model"]:
            interval = wilson_interval(aggregate["passed"], aggregate["evaluated"])
            records.append(
                {
                    "release": version,
                    "scenario": aggregate["scenario"],
                    "scenario_label": label(release, "scenario", aggregate["scenario"]),
                    "scenario_version": aggregate["scenario_version"],
                    "model": aggregate["model"],
                    "model_label": label(release, "model", aggregate["model"]),
                    "reasoning_effort": aggregate.get("reasoning_effort"),
                    "trials": aggregate["trials"],
                    "evaluated": aggregate["evaluated"],
                    "unavailable": aggregate["unavailable"],
                    "passed": aggregate["passed"],
                    "failed": aggregate["failed"],
                    "pass_rate": aggregate["pass_rate"],
                    "pass_rate_95_low": interval[0] if interval else None,
                    "pass_rate_95_high": interval[1] if interval else None,
                    "median_duration_seconds": aggregate["median_duration_seconds"],
                    "input_tokens": aggregate["input_tokens"],
                    "output_tokens": aggregate["output_tokens"],
                    "known_cost_usd": aggregate["known_cost_usd"],
                    "cost_reported_trials": aggregate["cost_reported_trials"],
                    "cost_per_repair_usd": cost_per_repair(aggregate),
                    "failure_categories": aggregate["failure_categories"],
                    "unavailable_categories": aggregate.get(
                        "unavailable_categories", {}
                    ),
                }
            )
    return {
        "schema_version": 1,
        "current_version": index["current_version"],
        "releases": releases,
        "records": records,
    }


def explorer_page() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Benchmark explorer &middot; replaybook</title>
  <meta name="description" content="Explore versioned Replaybook infrastructure-agent benchmark evidence.">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div class="wrap wide">
    <header class="site">
      <a class="brand" href="index.html"><span class="prompt">$</span> replaybook</a>
      <nav class="site">
        <a href="index.html">Home</a>
        <a href="usage.html">Usage</a>
        <a href="scenarios.html">Scenarios</a>
        <a href="benchmarks.html" class="active">Benchmarks</a>
        <a href="https://github.com/ducks/replaybook">GitHub</a>
      </nav>
    </header>

    <nav class="benchmark-tabs" aria-label="Benchmark sections">
      <a href="benchmarks.html">Current</a>
      <a href="benchmark-explorer.html" class="active" aria-current="page">Explore</a>
      <a href="benchmark-history.html">History</a>
      <a href="benchmark-methodology.html">Methodology</a>
    </nav>

    <p class="eyebrow">Versioned evidence</p>
    <h1>Benchmark explorer</h1>
    <p class="tagline benchmark-tagline">Compare model behavior, cost per durable repair, and failure modes inside one compatible DateVer release at a time.</p>

    <div class="callout comparison-note">
      <strong>Release boundaries are comparison boundaries.</strong>
      The explorer never silently pools results from different harnesses, scenario packs, verifiers, adapters, or Claux releases.
    </div>

    <div class="catalog-controls" aria-label="Benchmark filters">
      <label>Release<select id="release-filter"></select></label>
      <label>Scenario<select id="scenario-filter"></select></label>
      <label>Model<select id="model-filter"></select></label>
    </div>

    <div id="release-context" class="small muted"></div>
    <div class="metric-grid" aria-label="Filtered benchmark results">
      <div class="metric"><span class="metric-value" id="metric-repairs">0/0</span><span class="metric-label">durable repairs</span></div>
      <div class="metric"><span class="metric-value" id="metric-cost">$0.0000</span><span class="metric-label">known spend</span></div>
      <div class="metric"><span class="metric-value" id="metric-repair-cost">n/a</span><span class="metric-label">cost / repair</span></div>
    </div>

    <h2>Results</h2>
    <div class="table-scroll"><table><thead><tr><th>Scenario</th><th>Model</th><th>Trials</th><th>Repairs</th><th>Pass rate</th><th>95% CI</th><th>Median</th><th>Known cost</th><th>Cost / repair</th></tr></thead><tbody id="catalog-results"></tbody></table></div>

    <h2>Cost per durable repair</h2>
    <div id="cost-chart" class="cost-chart"></div>

    <div class="catalog-breakdowns">
      <section><h2>Repair failures</h2><ul id="failure-categories"></ul></section>
      <section><h2>Unavailable trials</h2><ul id="unavailable-categories"></ul></section>
    </div>

    <p class="small muted">The path-free source data is available as <a href="benchmark-catalog.json"><code>benchmark-catalog.json</code></a>. Local exploratory runs remain in the rebuildable SQLite catalog and are not published automatically.</p>
    <footer class="site"><a href="https://github.com/ducks/replaybook">github.com/ducks/replaybook</a> &middot; <a href="https://crates.io/crates/replaybook">crates.io</a></footer>
  </div>

  <script>
  let catalog;
  const releaseFilter = document.querySelector("#release-filter");
  const scenarioFilter = document.querySelector("#scenario-filter");
  const modelFilter = document.querySelector("#model-filter");
  const params = new URLSearchParams(window.location.search);

  function option(value, text) {{
    const node = document.createElement("option");
    node.value = value;
    node.textContent = text;
    return node;
  }}

  function duration(seconds) {{
    if (seconds === null || seconds === undefined) return "n/a";
    const rounded = Math.round(seconds);
    return `${{Math.floor(rounded / 60)}}:${{String(rounded % 60).padStart(2, "0")}}`;
  }}

  function money(value, incomplete = false) {{
    if (value === null || value === undefined) return "n/a";
    return `$${{value.toFixed(4)}}${{incomplete ? "+" : ""}}`;
  }}

  function percent(value) {{
    return value === null || value === undefined ? "n/a" : `${{Math.round(value * 100)}}%`;
  }}

  function variant(record) {{
    return record.reasoning_effort ? `${{record.model_label}} (${{record.reasoning_effort}})` : record.model_label;
  }}

  function replaceOptions(select, entries, selected, allLabel) {{
    select.replaceChildren();
    if (allLabel) select.append(option("", allLabel));
    entries.forEach(([value, text]) => select.append(option(value, text)));
    if ([...select.options].some(item => item.value === selected)) select.value = selected;
  }}

  function selectedRecords() {{
    return catalog.records.filter(record =>
      record.release === releaseFilter.value &&
      (!scenarioFilter.value || record.scenario === scenarioFilter.value) &&
      (!modelFilter.value || record.model === modelFilter.value)
    );
  }}

  function categories(records, key) {{
    const counts = new Map();
    records.forEach(record => Object.entries(record[key]).forEach(([name, count]) =>
      counts.set(name, (counts.get(name) || 0) + count)
    ));
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }}

  function renderCategories(target, values, empty) {{
    target.replaceChildren();
    if (!values.length) {{
      const item = document.createElement("li");
      item.textContent = empty;
      target.append(item);
      return;
    }}
    values.forEach(([name, count]) => {{
      const item = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = name;
      item.append(code, `: ${{count}}`);
      target.append(item);
    }});
  }}

  function render() {{
    const records = selectedRecords();
    const release = catalog.releases.find(item => item.version === releaseFilter.value);
    document.querySelector("#release-context").textContent = `${{release.title}} · harness ${{release.harness_versions.map(value => `v${{value}}`).join("/")}} · Claux ${{release.claux_release}} · ${{release.agent_timeout_seconds}}s timeout`;
    const evaluated = records.reduce((sum, row) => sum + row.evaluated, 0);
    const passed = records.reduce((sum, row) => sum + row.passed, 0);
    const cost = records.reduce((sum, row) => sum + row.known_cost_usd, 0);
    const incomplete = records.some(row => row.cost_reported_trials < row.trials);
    document.querySelector("#metric-repairs").textContent = `${{passed}}/${{evaluated}}`;
    document.querySelector("#metric-cost").textContent = money(cost, incomplete);
    document.querySelector("#metric-repair-cost").textContent = passed ? money(cost / passed, incomplete) : "n/a";

    const body = document.querySelector("#catalog-results");
    body.replaceChildren();
    records.forEach(record => {{
      const row = document.createElement("tr");
      const values = [
        `${{record.scenario_label}} · v${{record.scenario_version}}`, variant(record),
        String(record.trials), `${{record.passed}}/${{record.evaluated}}`, percent(record.pass_rate),
        record.evaluated ? `${{percent(record.pass_rate_95_low)}}–${{percent(record.pass_rate_95_high)}}` : "n/a",
        duration(record.median_duration_seconds),
        money(record.known_cost_usd, record.cost_reported_trials < record.trials),
        money(record.cost_per_repair_usd, record.cost_reported_trials < record.trials)
      ];
      values.forEach(value => {{ const cell = document.createElement("td"); cell.textContent = value; row.append(cell); }});
      body.append(row);
    }});

    const chart = document.querySelector("#cost-chart");
    chart.replaceChildren();
    const priced = records.filter(record => record.cost_per_repair_usd !== null);
    const maximum = Math.max(...priced.map(record => record.cost_per_repair_usd), 0);
    priced.sort((a, b) => a.cost_per_repair_usd - b.cost_per_repair_usd).forEach(record => {{
      const row = document.createElement("div"); row.className = "cost-row";
      const name = document.createElement("span"); name.textContent = `${{variant(record)}} · ${{record.scenario_label}}`;
      const track = document.createElement("span"); track.className = "cost-track";
      const bar = document.createElement("span"); bar.className = "cost-bar";
      bar.style.width = `${{maximum ? Math.max(2, record.cost_per_repair_usd / maximum * 100) : 0}}%`;
      track.append(bar);
      const value = document.createElement("strong"); value.textContent = money(record.cost_per_repair_usd, record.cost_reported_trials < record.trials);
      row.append(name, track, value); chart.append(row);
    }});
    if (!priced.length) chart.textContent = "No durable repairs in this selection.";

    renderCategories(document.querySelector("#failure-categories"), categories(records, "failure_categories"), "None");
    renderCategories(document.querySelector("#unavailable-categories"), categories(records, "unavailable_categories"), "None");
    const next = new URLSearchParams();
    next.set("release", releaseFilter.value);
    if (scenarioFilter.value) next.set("scenario", scenarioFilter.value);
    if (modelFilter.value) next.set("model", modelFilter.value);
    history.replaceState(null, "", `?${{next}}`);
  }}

  function refreshDimensions() {{
    const records = catalog.records.filter(record => record.release === releaseFilter.value);
    const scenarios = [...new Map(records.map(record => [record.scenario, record.scenario_label])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
    const models = [...new Map(records.map(record => [record.model, record.model_label])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
    replaceOptions(scenarioFilter, scenarios, params.get("scenario") || scenarioFilter.value, "All scenarios");
    replaceOptions(modelFilter, models, params.get("model") || modelFilter.value, "All models");
    render();
  }}
  releaseFilter.addEventListener("change", () => {{ params.delete("scenario"); params.delete("model"); refreshDimensions(); }});
  scenarioFilter.addEventListener("change", render);
  modelFilter.addEventListener("change", render);

  async function initialize() {{
    const response = await fetch("benchmark-catalog.json");
    if (!response.ok) throw new Error(`catalog request failed: ${{response.status}}`);
    catalog = await response.json();
    catalog.releases.slice().reverse().forEach(release => releaseFilter.append(option(release.version, `${{release.version}}${{release.current ? " · current" : ""}}`)));
    releaseFilter.value = params.get("release") || catalog.current_version;
    if (!releaseFilter.value) releaseFilter.value = catalog.current_version;
    refreshDimensions();
  }}

  initialize().catch(error => {{
    document.querySelector("#release-context").textContent = `Could not load benchmark catalog: ${{error.message}}`;
  }});
  </script>
</body>
</html>
"""


def build_outputs(root: Path, *, check: bool = False) -> None:
    index = read_json(root / INDEX_FILE)
    current = index.get("current_version")
    releases = index.get("releases")
    if not isinstance(current, str) or not isinstance(releases, list) or current not in releases:
        raise PublishError("benchmark-data/index.json has an invalid current release")
    release_path = root / RELEASES_DIR / f"{current}.json"
    release = read_json(release_path)
    validate_release(release, release_path)
    catalog = public_catalog(index, root)
    outputs = {
        root / CATALOG_FILE: json.dumps(catalog, indent=2, sort_keys=True) + "\n",
        root / DOCS_CATALOG: json.dumps(catalog, indent=2, sort_keys=True) + "\n",
        root / DOCS_CURRENT: html_page(release),
        root / DOCS_EXPLORER: explorer_page(),
        root / MARKDOWN_RECORD: replace_managed(
            (root / MARKDOWN_RECORD).read_text(),
            MARKDOWN_START,
            MARKDOWN_END,
            markdown_section(release),
        ),
        root / DOCS_HISTORY: replace_managed(
            (root / DOCS_HISTORY).read_text(),
            HISTORY_START,
            HISTORY_END,
            history_cards(index, root),
        ),
    }
    stale = []
    for path, rendered in outputs.items():
        if check:
            if not path.is_file() or path.read_text() != rendered:
                stale.append(str(path.relative_to(root)))
        else:
            path.write_text(rendered)
    if stale:
        raise PublishError("generated benchmark files are stale: " + ", ".join(stale))


def import_release(args: argparse.Namespace, root: Path) -> None:
    annotations = read_json(args.annotations) if args.annotations else {}
    release = create_release(args.version, args.summaries, annotations)
    index_path = root / INDEX_FILE
    if index_path.is_file():
        index = read_json(index_path)
    else:
        index = {"schema_version": 1, "current_version": args.version, "releases": []}
    releases = index.setdefault("releases", [])
    if args.version not in releases:
        releases.append(args.version)
    index["current_version"] = args.version
    write_json(root / RELEASES_DIR / f"{args.version}.json", release)
    write_json(index_path, index)
    build_outputs(root)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repo-dir", type=Path, default=REPO_DIR)
    commands = value.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="import summaries and build the site")
    importer.add_argument("--version", required=True)
    importer.add_argument("--annotations", type=Path)
    importer.add_argument("summaries", nargs="+", type=Path)
    commands.add_parser("build", help="build pages from tracked benchmark data")
    commands.add_parser("check", help="fail when generated pages are stale")
    return value


def main() -> int:
    args = parser().parse_args()
    root = args.repo_dir.resolve()
    try:
        if args.command == "import":
            import_release(args, root)
        else:
            build_outputs(root, check=args.command == "check")
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
