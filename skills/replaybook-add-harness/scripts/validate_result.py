#!/usr/bin/env python3
"""Validate a normalized Replaybook harness result."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


HARNESS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


def nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def validate(result: Any, expected_harness: str, expected_model: str) -> list[str]:
    errors = []
    if not isinstance(result, dict):
        return ["result must be a JSON object"]
    if result.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    harness = result.get("harness")
    if harness != expected_harness:
        errors.append(f"harness must equal {expected_harness!r}")
    if not isinstance(harness, str) or not HARNESS_PATTERN.fullmatch(harness):
        errors.append("harness contains unsafe characters")
    if result.get("model") != expected_model:
        errors.append(f"model must equal {expected_model!r}")

    usage = result.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            errors.append("usage must be an object or null")
        else:
            for field in TOKEN_FIELDS:
                value = usage.get(field)
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    errors.append(f"usage.{field} must be a nonnegative integer")
            cost = usage.get("cost_usd")
            if cost is not None and not nonnegative_number(cost):
                errors.append("usage.cost_usd must be a nonnegative number or null")

    outcome = result.get("outcome")
    if outcome is not None and not isinstance(outcome, dict):
        errors.append("outcome must be an object or null")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--harness", required=True)
    parser.add_argument("--model", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = json.loads(args.result.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"invalid result file: {error}", file=sys.stderr)
        return 1
    errors = validate(result, args.harness, args.model)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"valid Replaybook result: {args.result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
