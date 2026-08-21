#!/usr/bin/env python3
"""Render actionable reports for failed trials in a host matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def resolve_matrix(path: Path) -> tuple[Path, dict[str, Any]]:
    supplied = path.expanduser().resolve()
    summary_path = supplied / "summary.json" if supplied.is_dir() else supplied
    if summary_path.name != "summary.json":
        raise ValueError("expected a matrix directory or summary.json")
    summary = load_json(summary_path)
    if not isinstance(summary.get("runs"), list):
        raise ValueError(f"summary has no runs array: {summary_path}")
    return summary_path.parent, summary


def run_directory(matrix: Path, run: dict[str, Any]) -> Path:
    run_id = str(run.get("run_id") or "")
    if not run_id:
        raise ValueError("matrix run is missing run_id")
    return matrix / "runs" / run_id


def read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return load_json(path)
    except ValueError:
        return None


def failed_runs(summary: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []
    for run in summary["runs"]:
        if not isinstance(run, dict):
            continue
        reward = run.get("reward")
        category = run.get("failure_category")
        if reward == 0 or (category and run.get("trial_status") == "evaluated"):
            failures.append(run)
    return sorted(failures, key=lambda item: str(item.get("run_id") or ""))


def text_blocks(content: Any, kind: str) -> list[str]:
    if isinstance(content, str):
        return [content] if kind == "text" else []
    if not isinstance(content, list):
        return []
    return [
        str(block.get("text"))
        for block in content
        if isinstance(block, dict)
        and block.get("type") == kind
        and block.get("text")
    ]


def transcript_details(path: Path, command_limit: int) -> dict[str, Any]:
    transcript = read_optional_json(path)
    if transcript is None:
        return {
            "final_report": None,
            "commands": [],
            "tool_errors": 0,
            "outcome_status": None,
            "empty_completion": False,
        }

    final_report = None
    commands = []
    for message in transcript.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        rendered = text_blocks(message.get("content"), "text")
        if rendered:
            final_report = "\n".join(rendered).strip()
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            arguments = block.get("input") or {}
            if not isinstance(arguments, dict):
                continue
            command = next(
                (
                    arguments[field]
                    for field in ("command", "cmd", "script")
                    if isinstance(arguments.get(field), str)
                ),
                None,
            )
            if command:
                commands.append(command.strip())

    trace = transcript.get("tool_trace") or []
    outcome = transcript.get("outcome") or {}
    outcome_result = outcome.get("result")
    tool_errors = sum(
        bool(record.get("is_error")) for record in trace if isinstance(record, dict)
    )
    if command_limit >= 0:
        commands = commands[-command_limit:] if command_limit else []
    return {
        "final_report": final_report,
        "commands": commands,
        "tool_errors": tool_errors,
        "outcome_status": outcome.get("status"),
        "empty_completion": outcome.get("status") == "completed"
        and not str(outcome_result or "").strip(),
    }


def failure_report(matrix: Path, run: dict[str, Any], command_limit: int) -> dict[str, Any]:
    directory = run_directory(matrix, run)
    result = read_optional_json(directory / "result.json") or run
    phase = read_optional_json(directory / "scenario-state" / "phase-failure.json")
    transcript = transcript_details(
        directory / "results" / "transcript.json", command_limit
    )
    return {
        "run_id": run.get("run_id"),
        "scenario": run.get("scenario"),
        "scenario_version": run.get("scenario_version"),
        "model": run.get("model"),
        "reasoning_effort": run.get("reasoning_effort"),
        "attempt": run.get("attempt"),
        "duration_seconds": run.get("agent_duration_seconds"),
        "category": result.get("failure_category"),
        "failure": result.get("failure"),
        "verification": result.get("verification") or {},
        "phase_failure": phase,
        "agent_final_report": transcript["final_report"],
        "agent_outcome_status": transcript["outcome_status"],
        "empty_completion": transcript["empty_completion"],
        "recent_commands": transcript["commands"],
        "tool_errors": transcript["tool_errors"],
        "artifacts": {
            "result": str(directory / "result.json"),
            "transcript": str(directory / "results" / "transcript.json"),
            "log": str(matrix / "logs" / f"{run.get('run_id')}.log"),
        },
    }


def display_duration(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "unknown"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def markdown_report(matrix: Path, reports: list[dict[str, Any]]) -> str:
    lines = [
        "# Replaybook failure report",
        "",
        f"Matrix: `{matrix}`",
        "",
        f"Scored failures: **{len(reports)}**",
    ]
    for item in reports:
        lines.extend(
            [
                "",
                f"## {item['run_id']}",
                "",
                f"- Verdict: `{item['category'] or 'uncategorized'}` — "
                f"{item['failure'] or 'no failure message recorded'}",
                f"- Model: `{item['model']}` ({item['reasoning_effort'] or 'default'})",
                f"- Scenario: `{item['scenario']}` v{item['scenario_version']}, "
                f"attempt {item['attempt']}",
                f"- Agent duration: {display_duration(item['duration_seconds'])}; "
                f"tool errors: {item['tool_errors']}",
            ]
        )
        if item["empty_completion"]:
            lines.append(
                "- Transcript signal: **agent completed with an empty final response**"
            )
        phase = item["phase_failure"]
        if phase:
            lines.extend(
                [
                    "",
                    "### Verifier evidence",
                    "",
                    f"Phase `{phase.get('phase', 'unknown')}`, step "
                    f"`{phase.get('step', 'unknown')}`: {phase.get('message', 'unavailable')}",
                ]
            )
        verification = item["verification"]
        if verification:
            checks = ", ".join(
                f"{name}={'pass' if value is True else 'fail' if value is False else 'n/a'}"
                for name, value in verification.items()
            )
            lines.extend(["", f"Verification: {checks}"])
        if item["agent_final_report"]:
            lines.extend(
                ["", "### Agent’s final report", "", item["agent_final_report"]]
            )
        if item["recent_commands"]:
            lines.extend(["", "### Recent command trail", ""])
            for command in item["recent_commands"]:
                lines.extend(["```sh", command, "```", ""])
            if lines[-1] == "":
                lines.pop()
        lines.extend(
            [
                "",
                "### Artifacts",
                "",
                f"- Result: `{item['artifacts']['result']}`",
                f"- Transcript: `{item['artifacts']['transcript']}`",
                f"- Log: `{item['artifacts']['log']}`",
            ]
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path, help="matrix directory or summary.json")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--commands",
        type=int,
        default=8,
        help="recent commands per failure; use 0 to omit (default: 8)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.commands < 0:
        print("error: --commands must be zero or greater", file=sys.stderr)
        return 2
    try:
        matrix, summary = resolve_matrix(args.matrix)
        reports = [
            failure_report(matrix, run, args.commands)
            for run in failed_runs(summary)
        ]
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    output = (
        json.dumps(
            {
                "schema_version": 1,
                "matrix": str(matrix),
                "failures": reports,
            },
            indent=2,
        )
        + "\n"
        if args.format == "json"
        else markdown_report(matrix, reports)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
