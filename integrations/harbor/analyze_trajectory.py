#!/usr/bin/env python3
"""Summarize consequential behavior from Harbor ATIF trajectories."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


MUTATION_PATTERNS = [
    ("compose_lifecycle", re.compile(r"\bdocker\s+compose\s+(?:up|down|restart|rm)\b")),
    ("container_remove", re.compile(r"\bdocker\s+(?:container\s+)?rm\b")),
    ("container_stop", re.compile(r"\bdocker\s+(?:container\s+)?(?:stop|kill)\b")),
    ("container_create", re.compile(r"\bdocker\s+(?:container\s+)?(?:run|create)\b")),
    ("image_build", re.compile(r"\bdocker\s+(?:image\s+)?build\b")),
    (
        "service_lifecycle",
        re.compile(
            r"\b(?:systemctl|service)\s+\S+\s+(?:start|stop|restart|reload)\b"
            r"|\bsystemctl\s+(?:start|stop|restart|reload)\b"
        ),
    ),
    (
        "package_change",
        re.compile(
            r"\b(?:apt(?:-get)?|apk|dnf|yum|pacman)\s+"
            r"(?:install|remove|upgrade|add|del)\b"
        ),
    ),
    ("file_remove", re.compile(r"(?:^|[;&|]\s*)rm\s+(?:-[^ ]+\s+)*[^ ]")),
    ("file_move", re.compile(r"(?:^|[;&|]\s*)mv\s+[^ ]")),
    ("in_place_edit", re.compile(r"\bsed\s+(?:-[^ ]+\s+)*-i\b")),
]


def resolve_trajectories(paths: Iterable[Path]) -> list[Path]:
    """Resolve files and directories into a stable, unique trajectory list."""
    found: set[Path] = set()
    for supplied in paths:
        path = supplied.expanduser().resolve()
        if path.is_file():
            found.add(path)
        elif path.is_dir():
            found.update(path.rglob("trajectory.json"))
        else:
            raise ValueError(f"path does not exist: {path}")
    if not found:
        raise ValueError("no trajectory.json files found")
    return sorted(found)


def load_trajectory(path: Path) -> dict[str, Any]:
    try:
        trajectory = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read trajectory {path}: {error}") from error
    if not isinstance(trajectory, dict) or not str(
        trajectory.get("schema_version", "")
    ).startswith("ATIF-v"):
        raise ValueError(f"unsupported trajectory format: {path}")
    if not isinstance(trajectory.get("steps"), list):
        raise ValueError(f"trajectory has no steps array: {path}")
    return trajectory


def tool_records(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ATIF tool calls while retaining their matching observations."""
    records = []
    for step in trajectory["steps"]:
        if not isinstance(step, dict):
            continue
        results = {}
        observation = step.get("observation")
        if isinstance(observation, dict):
            for result in observation.get("results") or []:
                if isinstance(result, dict) and result.get("source_call_id"):
                    results[result["source_call_id"]] = result
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            result = results.get(call.get("tool_call_id"), {})
            content = result.get("content")
            if not isinstance(content, str):
                content = json.dumps(content) if content is not None else ""
            records.append(
                {
                    "step_id": step.get("step_id"),
                    "id": call.get("tool_call_id"),
                    "name": call.get("function_name", "unknown"),
                    "arguments": call.get("arguments") or {},
                    "output": content,
                    "output_bytes": len(content.encode()),
                    "is_error": bool((result.get("extra") or {}).get("is_error")),
                }
            )
    return records


def command_for(record: dict[str, Any]) -> str:
    arguments = record["arguments"]
    if not isinstance(arguments, dict):
        return ""
    for field in ("command", "cmd", "script"):
        value = arguments.get(field)
        if isinstance(value, str):
            return value
    return ""


def mutations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detected = []
    for record in records:
        command = command_for(record)
        if not command:
            continue
        for line in command.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("echo "):
                continue
            for kind, pattern in MUTATION_PATTERNS:
                if pattern.search(stripped):
                    detected.append(
                        {
                            "step_id": record["step_id"],
                            "tool_call_id": record["id"],
                            "kind": kind,
                            "command": stripped,
                        }
                    )
                    break
    return detected


def nearby_text(path: Path, relative: str) -> str | None:
    target = path.parent.parent / relative
    try:
        value = target.read_text().strip()
    except OSError:
        return None
    return value or None


def analyze(path: Path) -> dict[str, Any]:
    trajectory = load_trajectory(path)
    records = tool_records(trajectory)
    detected = mutations(records)
    mutation_counts = Counter(item["kind"] for item in detected)
    signals = []
    kinds = set(mutation_counts)
    if "container_remove" in kinds and "container_create" in kinds:
        signals.append("container_replaced_outside_detected_orchestrator")
    if "image_build" in kinds:
        signals.append("image_rebuilt")

    metrics = trajectory.get("final_metrics") or {}
    agent = trajectory.get("agent") or {}
    largest = sorted(records, key=lambda record: record["output_bytes"], reverse=True)
    return {
        "trajectory": str(path),
        "session_id": trajectory.get("session_id"),
        "agent": agent.get("name"),
        "agent_version": agent.get("version"),
        "model": agent.get("model_name"),
        "tool_calls": len(records),
        "tool_errors": sum(record["is_error"] for record in records),
        "tool_output_bytes": sum(record["output_bytes"] for record in records),
        "largest_outputs": [
            {
                "step_id": record["step_id"],
                "name": record["name"],
                "output_bytes": record["output_bytes"],
            }
            for record in largest[:5]
        ],
        "first_mutation": detected[0] if detected else None,
        "mutation_counts": dict(sorted(mutation_counts.items())),
        "risk_signals": signals,
        "failure_category": nearby_text(path, "verifier/failure-category.txt"),
        "verifier_output": nearby_text(path, "verifier/test-stdout.txt"),
        "reward": nearby_text(path, "verifier/reward.txt"),
        "usage": {
            "input_tokens": metrics.get("total_prompt_tokens"),
            "output_tokens": metrics.get("total_completion_tokens"),
            "cached_tokens": metrics.get("total_cached_tokens"),
            "cost_usd": metrics.get("total_cost_usd"),
        },
    }


def display_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def text_report(analyses: list[dict[str, Any]]) -> str:
    sections = []
    for item in analyses:
        usage = item["usage"]
        cost = "unknown" if usage["cost_usd"] is None else f"${usage['cost_usd']:.4f}"
        lines = [
            str(item["trajectory"]),
            f"Agent: {item['agent']} {item['agent_version']} | Model: {item['model']}",
            f"Tools: {item['tool_calls']} calls, {item['tool_errors']} errors, "
            f"{display_bytes(item['tool_output_bytes'])} output",
            f"Usage: {usage['input_tokens']} input, {usage['output_tokens']} output, "
            f"{usage['cached_tokens']} cached, {cost}",
        ]
        if item["first_mutation"]:
            mutation = item["first_mutation"]
            lines.append(
                f"First mutation: step {mutation['step_id']} {mutation['kind']} | "
                f"{mutation['command']}"
            )
        else:
            lines.append("First mutation: none detected")
        if item["mutation_counts"]:
            rendered = ", ".join(
                f"{kind}={count}" for kind, count in item["mutation_counts"].items()
            )
            lines.append(f"Mutations: {rendered}")
        if item["risk_signals"]:
            lines.append(f"Risk signals: {', '.join(item['risk_signals'])}")
        if item["largest_outputs"]:
            rendered = ", ".join(
                f"step {entry['step_id']} {entry['name']} "
                f"{display_bytes(entry['output_bytes'])}"
                for entry in item["largest_outputs"][:3]
            )
            lines.append(f"Largest outputs: {rendered}")
        verifier = item["verifier_output"] or "unavailable"
        lines.append(
            f"Verifier: reward={item['reward'] or 'unavailable'} "
            f"category={item['failure_category'] or 'none'} | {verifier}"
        )
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize tools, mutations, usage, and verifier outcomes from "
            "ATIF trajectories."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        analyses = [analyze(path) for path in resolve_trajectories(args.paths)]
    except ValueError as error:
        parser.error(str(error))

    output = (
        json.dumps({"schema_version": 1, "trajectories": analyses}, indent=2) + "\n"
        if args.format == "json"
        else text_report(analyses)
    )
    if args.output:
        args.output.write_text(output)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
