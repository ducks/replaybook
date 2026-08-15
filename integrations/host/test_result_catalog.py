from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from integrations.host.result_catalog import (
    compare,
    compatibility_cohorts,
    connect,
    import_paths,
)


def matrix_summary(
    model: str = "model/cheap",
    *,
    reward: int = 1,
    trial_status: str = "evaluated",
    cost: float | None = 0.01,
    run_id: str = "024-deploy-model-cheap-1",
    started_at: str = "2026-08-15T00:00:00Z",
    host_harness_sha256: str = "a" * 64,
    claux_release: str = "v20260815.0.0",
) -> dict:
    failure_category = None
    if trial_status != "evaluated":
        failure_category = "provider_unavailable"
    elif reward == 0:
        failure_category = "release_not_converged"
    return {
        "schema_version": 1,
        "suite": "replaybook-host-matrix-v1",
        "harness_version": 17,
        "started_at": started_at,
        "finished_at": "2026-08-15T00:02:00Z",
        "expected_trials": 1,
        "received_results": 1,
        "benchmark": {
            "replaybook_commit": "abcdef123456",
            "started_at": started_at,
            "scenarios": [
                {
                    "id": "024-discourse-interrupted-deploy",
                    "version": 1,
                    "pack": {"id": "ducks/replaybook-infra", "version": "1.0.0"},
                }
            ],
            "scenario_packs": [
                {"id": "ducks/replaybook-infra", "version": "1.0.0"}
            ],
            "execution_snapshot": {
                "host_harness_sha256": host_harness_sha256,
                "agent_adapter_sha256": "b" * 64,
                "agent_payload_sha256": "c" * 64,
            },
            "benchmark_manifest": {
                "id": "infra-core",
                "version": "20260815.0.0",
                "sha256": "d" * 64,
            },
            "models": [model],
            "reasoning_efforts": ["high"],
            "attempts": 1,
            "agent_timeout_seconds": 900,
            "agent": {"name": "claux", "adapter": "builtin:claux"},
            "claux_release": claux_release,
        },
        "runs": [
            {
                "run_id": run_id,
                "scenario": "024-discourse-interrupted-deploy",
                "scenario_version": 1,
                "harness_version": 17,
                "agent": "claux",
                "model": model,
                "reasoning_effort": "high",
                "attempt": 1,
                "started_at": started_at,
                "finished_at": "2026-08-15T00:02:00Z",
                "agent_duration_seconds": 120,
                "agent_timeout_seconds": 900,
                "trial_status": trial_status,
                "reward": reward,
                "failure": failure_category,
                "failure_category": failure_category,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 50,
                    "cache_creation_tokens": 0,
                    "cost_usd": cost,
                },
                "verification": {
                    "immediate_http": reward == 1,
                    "service_restart": reward == 1,
                    "host_reboot": reward == 1,
                },
                "recording": {
                    "total_duration_ms": 120_000,
                    "model_rounds": [{"duration_ms": 1_000}],
                    "tools": [
                        {
                            "duration_ms": 200,
                            "started_after_ms": 5_000,
                            "read_only": False,
                        }
                    ],
                },
            }
        ],
        "infrastructure_errors": [],
    }


class ResultCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.connection = connect(self.root / "catalog.sqlite3")

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def write_summary(self, name: str, value: dict) -> Path:
        path = self.root / name / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
        return path

    def count(self, table: str) -> int:
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def test_reimport_replaces_one_matrix_instead_of_duplicating_trials(self) -> None:
        path = self.write_summary("matrix", matrix_summary())
        import_paths(self.connection, [path])
        import_paths(self.connection, [path])

        self.assertEqual(self.count("matrices"), 1)
        self.assertEqual(self.count("trials"), 1)

        updated = matrix_summary()
        updated["runs"].append(
            deepcopy(updated["runs"][0])
            | {"run_id": "024-deploy-model-cheap-2", "attempt": 2}
        )
        updated["received_results"] = 2
        path.write_text(json.dumps(updated))
        import_paths(self.connection, [path])

        self.assertEqual(self.count("matrices"), 1)
        self.assertEqual(self.count("trials"), 2)

    def test_harness_and_claux_changes_create_separate_cohorts(self) -> None:
        first = self.write_summary("first", matrix_summary())
        changed = matrix_summary(
            started_at="2026-08-15T01:00:00Z",
            host_harness_sha256="e" * 64,
            claux_release="v20260815.0.1",
        )
        second = self.write_summary("second", changed)
        import_paths(self.connection, [first, second])

        cohorts = compatibility_cohorts(
            self.connection, "024-discourse-interrupted-deploy"
        )
        self.assertEqual(len(cohorts), 2)
        output = compare(self.connection, "024-discourse-interrupted-deploy")
        self.assertIn("cohorts: using newest of 2", output)
        self.assertIn("v20260815.0.1", output)

    def test_compare_separates_unavailable_and_prices_reliability(self) -> None:
        passed = self.write_summary("passed", matrix_summary(cost=0.03))
        failed_value = matrix_summary(
            reward=0,
            cost=0.01,
            run_id="024-deploy-model-cheap-2",
            started_at="2026-08-15T00:01:00Z",
        )
        failed_value["benchmark"]["models"] = ["model/cheap"]
        failed = self.write_summary("failed", failed_value)
        unavailable_value = matrix_summary(
            model="model/provider",
            reward=0,
            trial_status="unavailable",
            cost=0.005,
            run_id="024-deploy-model-provider-1",
            started_at="2026-08-15T00:02:00Z",
        )
        unavailable = self.write_summary("unavailable", unavailable_value)
        import_paths(self.connection, [passed, failed, unavailable])

        output = compare(self.connection, "024-discourse-interrupted-deploy")
        cheap_line = next(line for line in output.splitlines() if line.startswith("model/cheap"))
        provider_line = next(
            line for line in output.splitlines() if line.startswith("model/provider")
        )
        self.assertIn("2       2     0        1       1", cheap_line)
        self.assertIn("$0.0400", cheap_line)
        self.assertIn("1       0     1        0       0", provider_line)

    def test_unsupported_summaries_are_reported_as_skipped(self) -> None:
        path = self.write_summary("old", {"runs": []})
        stats = import_paths(self.connection, [path])
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(self.count("matrices"), 0)

    def test_pre_status_host_runs_are_normalized_as_evaluated(self) -> None:
        value = matrix_summary()
        del value["runs"][0]["trial_status"]
        path = self.write_summary("legacy-host", value)
        import_paths(self.connection, [path])

        status = self.connection.execute("SELECT trial_status FROM trials").fetchone()[0]
        self.assertEqual(status, "evaluated")


if __name__ == "__main__":
    unittest.main()
