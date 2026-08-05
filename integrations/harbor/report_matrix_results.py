#!/usr/bin/env python3
"""Aggregate Replaybook Harbor matrix summaries into JSON or Markdown."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CORE_SCENARIOS = [
    "001-nginx-502",
    "002-postgres-rejecting-connections",
    "003-missing-env-var",
    "004-disk-full",
    "005-oom-kill",
    "006-sidekiq-cant-connect",
    "007-packet-loss",
    "008-connection-pool-exhaustion",
    "009-phantom-backend",
]
HARD_SCENARIOS = [
    "010-stale-auth-secret",
    "011-partial-rollout",
    "012-retry-storm",
]


def resolve_summary(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_dir():
        path = path / "summary.json"
    if not path.is_file():
        raise ValueError(f"summary does not exist: {path}")
    return path


def load_summaries(paths: Iterable[Path]) -> list[tuple[Path, dict[str, Any]]]:
    loaded = []
    for supplied in paths:
        path = resolve_summary(supplied)
        try:
            summary = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read summary {path}: {error}") from error
        if not isinstance(summary.get("runs"), list):
            raise ValueError(f"summary has no runs array: {path}")
        loaded.append((path, summary))
    if not loaded:
        raise ValueError("at least one summary path is required")
    return loaded


def inferred_scenario_set(scenarios: list[str]) -> str:
    selected = set(scenarios)
    if selected == set(CORE_SCENARIOS):
        return "core"
    if selected == set(HARD_SCENARIOS):
        return "hard"
    if selected == set(CORE_SCENARIOS + HARD_SCENARIOS):
        return "all"
    return "custom"


def current_commit(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def category_counts(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        run.get("failure_category")
        for run in runs
        if run.get("failure_category")
    )
    return [
        {"category": category, "count": count}
        for category, count in sorted(counts.items())
    ]


def summarize_group(runs: list[dict[str, Any]]) -> dict[str, Any]:
    trials = sum(int(run.get("trials") or 0) for run in runs)
    scored_trials = sum(
        int(run.get("trials") or 0)
        for run in runs
        if run.get("mean") is not None
    )
    durable_repairs = sum(
        float(run["mean"]) * int(run.get("trials") or 0)
        for run in runs
        if run.get("mean") is not None
    )
    durations = sorted(
        float(run["duration_seconds"])
        for run in runs
        if run.get("duration_seconds") is not None
    )
    known_cost = sum(float(run.get("cost_usd") or 0) for run in runs)
    cost_reported_trials = sum(
        int(run.get("trials") or 0)
        for run in runs
        if run.get("cost_usd") is not None
    )
    return {
        "trials": trials,
        "scored_trials": scored_trials,
        "durable_repairs": durable_repairs,
        "pass_rate": durable_repairs / scored_trials if scored_trials else None,
        "errors": sum(int(run.get("errors") or 0) for run in runs),
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "known_cost_usd": known_cost,
        "cost_reported_trials": cost_reported_trials,
        "failure_categories": category_counts(runs),
    }


def build_report(
    loaded: list[tuple[Path, dict[str, Any]]], repo: Path
) -> dict[str, Any]:
    runs_by_id: dict[str, dict[str, Any]] = {}
    anonymous = 0
    benchmarks = []
    for path, summary in loaded:
        benchmarks.append({"source": str(path), "metadata": summary.get("benchmark")})
        for run in summary["runs"]:
            job_id = run.get("job_id")
            if not job_id:
                anonymous += 1
                job_id = f"anonymous:{path}:{anonymous}"
            if job_id in runs_by_id and runs_by_id[job_id] != run:
                raise ValueError(f"conflicting duplicate job_id: {job_id}")
            runs_by_id[job_id] = run

    runs = list(runs_by_id.values())
    scenarios = sorted({str(run.get("scenario", "unknown")) for run in runs})
    models = sorted({str(run.get("agent_model", "unknown")) for run in runs})

    grouped_models: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_scenarios: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        model = str(run.get("agent_model", "unknown"))
        scenario = str(run.get("scenario", "unknown"))
        grouped_models[model].append(run)
        grouped_scenarios[(scenario, model)].append(run)

    by_model = []
    for model in models:
        by_model.append({"agent_model": model, **summarize_group(grouped_models[model])})

    by_scenario_model = []
    for scenario, model in sorted(grouped_scenarios):
        by_scenario_model.append(
            {
                "scenario": scenario,
                "agent_model": model,
                **summarize_group(grouped_scenarios[(scenario, model)]),
            }
        )

    suites = sorted(
        {
            metadata["metadata"].get("suite")
            for metadata in benchmarks
            if isinstance(metadata["metadata"], dict)
            and metadata["metadata"].get("suite")
        }
    )
    commits = sorted(
        {
            metadata["metadata"].get("replaybook_commit")
            for metadata in benchmarks
            if isinstance(metadata["metadata"], dict)
            and metadata["metadata"].get("replaybook_commit")
        }
    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reporting_commit": current_commit(repo),
        "source_summaries": [str(path) for path, _ in loaded],
        "benchmark": {
            "suites": suites,
            "replaybook_commits": commits,
            "scenario_set": inferred_scenario_set(scenarios),
            "scenarios": scenarios,
        },
        "totals": summarize_group(runs),
        "by_model": by_model,
        "by_scenario_model": by_scenario_model,
        "benchmarks": benchmarks,
    }


def display_number(value: float) -> str:
    if math.isclose(value, round(value)):
        return str(round(value))
    return f"{value:.2f}"


def display_duration(value: float | None) -> str:
    if value is None:
        return "unavailable"
    minutes, seconds = divmod(round(value), 60)
    return f"{minutes}:{seconds:02d}"


def display_cost(row: dict[str, Any]) -> str:
    coverage = row["cost_reported_trials"]
    trials = row["trials"]
    if coverage == 0:
        return "unavailable"
    cost = f"${row['known_cost_usd']:.2f}"
    return cost if coverage == trials else f"{cost} known ({coverage}/{trials})"


def escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Replaybook evaluation report",
        "",
        f"Scenario set: `{report['benchmark']['scenario_set']}`  ",
        f"Scenarios: {len(report['benchmark']['scenarios'])}  ",
        f"Source summaries: {len(report['source_summaries'])}",
        "",
        "## Model summary",
        "",
        "| Model | Durable repairs | Pass rate | Agent errors | Median trial time | Reported cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["by_model"]:
        pass_rate = "unavailable" if row["pass_rate"] is None else f"{row['pass_rate']:.1%}"
        lines.append(
            "| "
            + " | ".join(
                escape_cell(value)
                for value in (
                    row["agent_model"],
                    f"{display_number(row['durable_repairs'])}/{row['scored_trials']}",
                    pass_rate,
                    row["errors"],
                    display_duration(row["median_duration_seconds"]),
                    display_cost(row),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Scenario results",
            "",
            "| Scenario | Model | Durable repairs | Median trial time | Reported cost |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in report["by_scenario_model"]:
        lines.append(
            "| "
            + " | ".join(
                escape_cell(value)
                for value in (
                    row["scenario"],
                    row["agent_model"],
                    f"{display_number(row['durable_repairs'])}/{row['scored_trials']}",
                    display_duration(row["median_duration_seconds"]),
                    display_cost(row),
                )
            )
            + " |"
        )

    failures = [
        (row["agent_model"], failure)
        for row in report["by_model"]
        for failure in row["failure_categories"]
    ]
    if failures:
        lines.extend(
            [
                "",
                "## Failure categories",
                "",
                "| Model | Category | Count |",
                "|---|---|---:|",
            ]
        )
        for model, failure in failures:
            lines.append(
                f"| {escape_cell(model)} | {escape_cell(failure['category'])} | {failure['count']} |"
            )

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "summaries",
        nargs="+",
        type=Path,
        help="summary.json files or matrix directories",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument(
        "--output",
        type=Path,
        help="write the report to this file instead of stdout",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        loaded = load_summaries(args.summaries)
        repo = Path(__file__).resolve().parents[2]
        report = build_report(loaded, repo)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    rendered = (
        json.dumps(report, indent=2) + "\n"
        if args.format == "json"
        else markdown_report(report)
    )
    if args.output:
        args.output.expanduser().write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
