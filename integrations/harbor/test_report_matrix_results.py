"""Tests for aggregating Harbor matrix summaries."""

import json
import tempfile
import unittest
from pathlib import Path

from integrations.harbor.report_matrix_results import (
    build_report,
    load_summaries,
    markdown_report,
)


def run(job_id, model, scenario, mean, *, category=None, cost=None, duration=60):
    return {
        "job_id": job_id,
        "scenario": scenario,
        "agent_model": model,
        "trials": 1,
        "errors": 0,
        "mean": mean,
        "failure_category": category,
        "failure_message": None,
        "cost_usd": cost,
        "duration_seconds": duration,
    }


class MatrixReportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_summary(self, name, runs, benchmark=None):
        directory = self.root / name
        directory.mkdir()
        path = directory / "summary.json"
        path.write_text(json.dumps({"runs": runs, "benchmark": benchmark}))
        return directory

    def test_aggregates_models_costs_durations_and_failures(self):
        first = self.write_summary(
            "first",
            [
                run("a", "model-a", "010-stale-auth-secret", 1, cost=0.01, duration=50),
                run("b", "model-a", "011-partial-rollout", 0, category="regression", cost=0.02, duration=70),
            ],
            {"suite": "replaybook-harbor-v1", "replaybook_commit": "abc"},
        )
        second = self.write_summary(
            "second",
            [run("c", "model-b", "012-retry-storm", 1, duration=40)],
        )

        report = build_report(load_summaries([first, second]), self.root)

        self.assertEqual(report["benchmark"]["scenario_set"], "hard")
        self.assertEqual(report["benchmark"]["suites"], ["replaybook-harbor-v1"])
        self.assertEqual(report["benchmark"]["replaybook_commits"], ["abc"])
        model_a = report["by_model"][0]
        self.assertEqual(model_a["durable_repairs"], 1)
        self.assertEqual(model_a["pass_rate"], 0.5)
        self.assertEqual(model_a["median_duration_seconds"], 60)
        self.assertAlmostEqual(model_a["known_cost_usd"], 0.03)
        self.assertEqual(
            model_a["failure_categories"],
            [{"category": "regression", "count": 1}],
        )

    def test_deduplicates_identical_jobs(self):
        shared = run("same", "model", "010-stale-auth-secret", 1)
        first = self.write_summary("first", [shared])
        second = self.write_summary("second", [shared])

        report = build_report(load_summaries([first, second]), self.root)

        self.assertEqual(report["totals"]["trials"], 1)

    def test_rejects_conflicting_duplicate_jobs(self):
        first = self.write_summary(
            "first", [run("same", "model", "010-stale-auth-secret", 1)]
        )
        second = self.write_summary(
            "second", [run("same", "model", "010-stale-auth-secret", 0)]
        )

        with self.assertRaisesRegex(ValueError, "conflicting duplicate job_id"):
            build_report(load_summaries([first, second]), self.root)

    def test_markdown_marks_partial_and_unknown_cost(self):
        summary = self.write_summary(
            "summary",
            [
                run("a", "model-a", "010-stale-auth-secret", 1, cost=0.01),
                run("b", "model-a", "010-stale-auth-secret", 0, category="regression"),
                run("c", "model-b", "010-stale-auth-secret", 1),
            ],
        )

        markdown = markdown_report(build_report(load_summaries([summary]), self.root))

        self.assertIn("$0.01 known (1/2)", markdown)
        self.assertIn("unavailable", markdown)
        self.assertIn("| model-a | regression | 1 |", markdown)


if __name__ == "__main__":
    unittest.main()
