from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from integrations.host.benchmark_plan import build_plan
from integrations.host.test_benchmark_manifest import write_benchmark


def write_coverage(root: Path, *, evaluated: int = 3, version: int = 2) -> Path:
    path = root / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fleet": [
                    {"model": "test/model", "reasoning_effort": "high", "tiers": ["core"]},
                    {"model": "new/model", "reasoning_effort": "high", "tiers": []},
                ],
                "scenarios": [
                    {
                        "scenario": "database-incident",
                        "scenario_version": version,
                        "boundary": {
                            "tier": "core",
                            "attempts": 3,
                            "agent_timeout_seconds": 600,
                            "harness_versions": [23],
                            "scenario_pack": {"id": "test/infra", "version": "1"},
                        },
                        "cells": [
                            {
                                "model": "test/model",
                                "reasoning_effort": "high",
                                "status": "covered",
                                "trials": 3,
                                "evaluated": evaluated,
                                "median_duration_seconds": 60,
                                "known_cost_usd": 0.30,
                                "cost_reported_trials": 3,
                            },
                            {
                                "model": "new/model",
                                "reasoning_effort": "high",
                                "status": "missing",
                            },
                        ],
                    }
                ],
            }
        )
    )
    return path


def args(benchmark: Path, coverage: Path, models: list[str] | None = None) -> Namespace:
    return Namespace(
        benchmark=benchmark,
        coverage=coverage,
        catalog=None,
        models=models,
        reasoning_efforts=None,
        results=[],
        concurrency=2,
        base_port=24000,
        format="text",
    )


def write_summary(root: Path, manifest_sha: str) -> Path:
    path = root / "summary.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite": "replaybook-host-matrix-v1",
                "harness_version": 23,
                "harness_versions": [23],
                "benchmark": {
                    "benchmark_manifest": {
                        "schema_version": 1,
                        "id": "test-infra",
                        "version": "20260810.0.0",
                        "tier": "core",
                        "sha256": manifest_sha,
                    },
                    "tier": "core",
                    "attempts": 3,
                    "agent_timeout_seconds": 600,
                    "scenario_packs": [{"id": "test/infra", "version": "1"}],
                    "scenarios": [{"id": "database-incident", "version": 2}],
                },
                "runs": [
                    {
                        "scenario": "database-incident",
                        "scenario_version": 2,
                        "model": "new/model",
                        "reasoning_effort": "high",
                        "trial_status": "evaluated",
                        "attempt": attempt,
                    }
                    for attempt in range(1, 4)
                ],
            }
        )
    )
    return path


class BenchmarkPlanTests(unittest.TestCase):
    def test_plans_only_missing_fleet_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = build_plan(args(write_benchmark(root), write_coverage(root)))

        self.assertEqual(plan["covered_cells"], 1)
        self.assertEqual(len(plan["gaps"]), 1)
        self.assertEqual(plan["gaps"][0]["model"], "new/model")
        self.assertEqual(plan["estimate"]["trials"], 3)
        self.assertEqual(len(plan["commands"]), 1)
        command = plan["commands"][0]["command"]
        self.assertIn("--scenario database-incident", command)
        self.assertIn("--models new/model", command)
        self.assertNotIn("test/model", command)

    def test_incomplete_cohort_is_a_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = build_plan(
                args(
                    write_benchmark(root),
                    write_coverage(root, evaluated=2),
                    ["test/model"],
                )
            )

        self.assertEqual(plan["gaps"][0]["reason"], "compatible cohort is incomplete")

    def test_scenario_version_change_is_a_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = build_plan(
                args(
                    write_benchmark(root),
                    write_coverage(root, version=1),
                    ["test/model"],
                )
            )

        self.assertEqual(plan["gaps"][0]["reason"], "scenario version changed")

    def test_complete_selected_lane_needs_no_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = build_plan(
                args(write_benchmark(root), write_coverage(root), ["test/model"])
            )

        self.assertEqual(plan["gaps"], [])
        self.assertEqual(plan["commands"], [])

    def test_local_complete_matrix_suppresses_unpublished_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            benchmark = write_benchmark(root)
            namespace = args(benchmark, write_coverage(root), ["new/model"])
            namespace.results = [write_summary(root, "unused")]
            plan = build_plan(namespace)

        self.assertEqual(plan["gaps"], [])
        self.assertEqual(plan["commands"], [])


if __name__ == "__main__":
    unittest.main()
