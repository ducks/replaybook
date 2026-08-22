#!/usr/bin/env python3
"""Export Replaybook host-agent runs as ATIF v1.7 trajectories."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ATIF_VERSION = "ATIF-v1.7"
INFRA_EVAL_VERSION = 1


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def transcript_path(trial: Path, result: dict[str, Any]) -> Path | None:
    for candidate in (
        trial / "results" / "transcript.json",
        trial / "results" / "claux-transcript.json",
    ):
        if candidate.is_file():
            return candidate
    supplied = result.get("transcript_file")
    if isinstance(supplied, str) and Path(supplied).is_file():
        return Path(supplied)
    return None


def resolve_trials(paths: Iterable[Path]) -> list[tuple[Path, Path]]:
    found: dict[Path, Path] = {}
    for supplied in paths:
        source = supplied.expanduser().resolve()
        if source.is_file() and source.name == "result.json":
            candidates = [source]
        elif source.is_dir() and (source / "result.json").is_file():
            candidates = [source / "result.json"]
        elif source.is_dir():
            candidates = sorted(source.rglob("result.json"))
        else:
            raise ValueError(f"run path does not exist: {source}")
        for result_path in candidates:
            trial = result_path.parent
            result = load_object(result_path)
            transcript = transcript_path(trial, result)
            if transcript is not None:
                found[result_path.resolve()] = transcript.resolve()
    if not found:
        raise ValueError("no Replaybook trials with structured transcripts found")
    return sorted(found.items())


def required_object(value: dict[str, Any], field: str, source: Path) -> dict[str, Any]:
    item = value.get(field)
    if not isinstance(item, dict):
        raise ValueError(f"{source}: {field} must be an object")
    return item


def trace_entries(transcript: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    trace = transcript.get("tool_trace")
    if not isinstance(trace, list):
        raise ValueError(f"{source}: tool_trace must be an array")
    entries = []
    for index, entry in enumerate(trace):
        if not isinstance(entry, dict):
            raise ValueError(f"{source}: tool_trace[{index}] must be an object")
        if not isinstance(entry.get("name"), str):
            raise ValueError(f"{source}: tool_trace[{index}].name must be a string")
        if not isinstance(entry.get("input"), dict):
            raise ValueError(f"{source}: tool_trace[{index}].input must be an object")
        output = entry.get("output")
        if output is not None and not isinstance(output, str):
            raise ValueError(f"{source}: tool_trace[{index}].output must be a string or null")
        entries.append(entry)
    return entries


def first_user_message(transcript: dict[str, Any]) -> str | None:
    messages = transcript.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "\n".join(
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
            if text:
                return text
    return None


def final_message(transcript: dict[str, Any]) -> tuple[str, str]:
    outcome = transcript.get("outcome")
    if not isinstance(outcome, dict):
        return "unknown", "Agent outcome was not recorded."
    status = outcome.get("status") if isinstance(outcome.get("status"), str) else "unknown"
    for field in ("result", "message"):
        if isinstance(outcome.get(field), str):
            return status, outcome[field]
    return status, f"Agent outcome: {status}"


def usage_metrics(transcript: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    usage = transcript.get("usage")
    if not isinstance(usage, dict):
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    cache_read = integer_or_none(usage.get("cache_read_tokens"))
    cache_creation = integer_or_none(usage.get("cache_creation_tokens"))
    cached = (
        cache_read + cache_creation
        if cache_read is not None and cache_creation is not None
        else None
    )
    cost = usage.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        cost = None
    return {
        "input": integer_or_none(usage.get("input_tokens")),
        "output": integer_or_none(usage.get("output_tokens")),
        "cached": cached,
        "cost": cost,
        "cache_read": cache_read,
        "cache_creation": cache_creation,
    }


def integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def infrastructure_metadata(result: dict[str, Any]) -> dict[str, Any]:
    scenario_pack = result.get("scenario_pack")
    if not isinstance(scenario_pack, dict):
        scenario_pack = None
    scenario_version = result.get("scenario_version")
    return {
        "schema_version": INFRA_EVAL_VERSION,
        "producer": {
            "name": "replaybook",
            "version": (
                str(result["harness_version"])
                if result.get("harness_version") is not None
                else None
            ),
            "revision": None,
            "result_schema_version": result.get("schema_version"),
        },
        "task": {
            "id": result.get("scenario"),
            "version": str(scenario_version) if scenario_version is not None else None,
            "pack": scenario_pack,
            "image_artifacts": result.get("image_artifacts", []),
        },
        "execution": {
            "agent": result.get("agent"),
            "model": result.get("model"),
            "reasoning_effort": result.get("reasoning_effort"),
            "timeout_seconds": result.get("agent_timeout_seconds"),
            "duration_seconds": result.get("agent_duration_seconds"),
            "suite": result.get("suite"),
        },
        "outcome": {
            "status": result.get("trial_status"),
            "reward": result.get("reward"),
            "failure_category": result.get("failure_category"),
            "verification": result.get("verification"),
        },
    }


def to_atif(
    result: dict[str, Any], transcript: dict[str, Any], source: Path
) -> dict[str, Any]:
    entries = trace_entries(transcript, source)
    model = result.get("model") or transcript.get("model") or "unknown"
    agent_name = result.get("agent") or "unknown"
    harness_version = result.get("harness_version")
    steps: list[dict[str, Any]] = []
    prompt = first_user_message(transcript)
    if prompt is not None:
        steps.append({"step_id": 1, "source": "user", "message": prompt})
    for index, entry in enumerate(entries):
        call_id = entry.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"tool-{index + 1}"
        timing = {
            "started_after_ms": integer_or_none(entry.get("started_after_ms")),
            "duration_ms": integer_or_none(entry.get("duration_ms")),
            "read_only": entry.get("read_only")
            if isinstance(entry.get("read_only"), bool)
            else None,
        }
        steps.append(
            {
                "step_id": len(steps) + 1,
                "source": "agent",
                "model_name": model,
                "message": "",
                "tool_calls": [
                    {
                        "tool_call_id": call_id,
                        "function_name": entry["name"],
                        "arguments": entry["input"],
                    }
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": call_id,
                            "content": entry.get("output") or "",
                            "extra": {"is_error": bool(entry.get("is_error"))},
                        }
                    ]
                },
                "extra": {key: value for key, value in timing.items() if value is not None},
            }
        )
    outcome_status, message = final_message(transcript)
    steps.append(
        {
            "step_id": len(steps) + 1,
            "source": "agent",
            "model_name": model,
            "message": message,
            "extra": {"outcome": outcome_status},
        }
    )
    usage = usage_metrics(transcript, result)
    timing = transcript.get("timing") if isinstance(transcript.get("timing"), dict) else {}
    metrics_extra: dict[str, Any] = {
        "cache_read_tokens": usage["cache_read"],
        "cache_creation_tokens": usage["cache_creation"],
        "model_rounds": timing.get("model_rounds"),
        "total_duration_ms": timing.get("total_duration_ms"),
        "infrastructure_evaluation": infrastructure_metadata(result),
    }
    return {
        "schema_version": ATIF_VERSION,
        "session_id": result.get("run_id") or source.parent.name,
        "agent": {
            "name": agent_name,
            "version": str(harness_version) if harness_version is not None else "unknown",
            "model_name": model,
            "extra": {"reasoning_effort": result.get("reasoning_effort")},
        },
        "steps": steps,
        "notes": (
            "Observable tool calls and exact outputs exported by Replaybook. "
            "Private assistant reasoning is intentionally excluded."
        ),
        "final_metrics": {
            "total_prompt_tokens": usage["input"],
            "total_completion_tokens": usage["output"],
            "total_cached_tokens": usage["cached"],
            "total_cost_usd": usage["cost"],
            "total_steps": len(steps),
            "extra": metrics_extra,
        },
    }


def safe_name(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return rendered or "trajectory"


def output_path(
    output: Path, result_path: Path, result: dict[str, Any], used: set[Path]
) -> Path:
    identity = str(result.get("run_id") or result_path.parent.name)
    candidate = output / f"{safe_name(identity)}.json"
    if candidate not in used:
        return candidate
    suffix = 2
    while output / f"{safe_name(identity)}-{suffix}.json" in used:
        suffix += 1
    return output / f"{safe_name(identity)}-{suffix}.json"


def export(paths: Iterable[Path], output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    written = []
    used: set[Path] = set()
    for result_path, transcript_path_value in resolve_trials(paths):
        result = load_object(result_path)
        result.setdefault("run_id", result_path.parent.name)
        transcript = load_object(transcript_path_value)
        target = output_path(output, result_path, result, used)
        trajectory = to_atif(result, transcript, transcript_path_value)
        target.write_text(json.dumps(trajectory, indent=2) + "\n")
        used.add(target)
        written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Replaybook host-agent runs as ATIF v1.7 trajectories."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="trial or matrix paths")
    parser.add_argument("--output", type=Path, default=Path("trajectories"))
    args = parser.parse_args()
    try:
        written = export(args.paths, args.output)
    except ValueError as error:
        parser.error(str(error))
    for path in written:
        print(path)
    print(f"exported {len(written)} ATIF trajectories", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
