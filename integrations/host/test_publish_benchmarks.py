from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from integrations.host.publish_benchmarks import (
    HISTORY_END,
    HISTORY_START,
    MARKDOWN_END,
    MARKDOWN_START,
    PublishError,
    build_outputs,
    cost_per_repair,
    create_release,
    html_page,
    normalized_agent_harness,
    validate_release,
    write_json,
)


def summary(
    model: str,
    *,
    scenario_version: int = 1,
    pack_version: str = "20260809.0.0",
    reward: int = 1,
    snapshot_hash: str | None = None,
    scenario_snapshot_hash: str | None = None,
    pack_snapshot_hash: str = "b" * 64,
    benchmark_hash: str | None = None,
    tier: str | None = None,
) -> dict:
    run_id = f"001-nginx-{model}-1"
    return {
        "schema_version": 1,
        "suite": "replaybook-host-matrix-v1",
        "harness_version": 5,
        "started_at": "2026-08-09T00:00:00Z",
        "finished_at": "2026-08-09T00:01:00Z",
        "benchmark": {
            "suite": "replaybook-host-matrix-v1",
            "replaybook_commit": "abcdef123456",
            "scenarios": [
                {
                    "id": "001-nginx",
                    "version": scenario_version,
                    "pack": {"id": "example/incidents", "version": pack_version},
                }
            ],
            "scenario_packs": [
                {"id": "example/incidents", "version": pack_version}
            ],
            "execution_snapshot": (
                {
                    "schema_version": 1,
                    "host_harness_sha256": snapshot_hash,
                    "scenario_packs": [
                        {
                            "id": "example/incidents",
                            "version": pack_version,
                            "sha256": pack_snapshot_hash,
                        }
                    ],
                    "selected_scenarios": (
                        [
                            {
                                "id": "001-nginx",
                                "version": scenario_version,
                                "pack_id": "example/incidents",
                                "sha256": scenario_snapshot_hash,
                            }
                        ]
                        if scenario_snapshot_hash is not None
                        else []
                    ),
                    "agent_adapter_sha256": None,
                    "agent_payload_sha256": None,
                    "agent_env_sha256": None,
                    "claux_binary_sha256": None,
                }
                if snapshot_hash is not None
                else None
            ),
            "benchmark_manifest": (
                {
                    "schema_version": 1,
                    "id": "replaybook-infra",
                    "version": "20260810.0.0",
                    "status": "preview",
                    "tier": tier,
                    "sha256": benchmark_hash,
                }
                if benchmark_hash is not None
                else None
            ),
            "tier": tier,
            "models": [model],
            "attempts": 1,
            "concurrency": 1,
            "agent_timeout_seconds": 900,
            "agent": {"name": "claux", "adapter": "builtin:claux", "payload": None},
            "claux_release": "v20260808.0.0",
        },
        "runs": [
            {
                "schema_version": 1,
                "suite": "replaybook-host-v1",
                "harness_version": 5,
                "scenario": "001-nginx",
                "scenario_version": scenario_version,
                "agent": "claux",
                "model": model,
                "attempt": 1,
                "run_id": run_id,
                "started_at": "2026-08-09T00:00:00Z",
                "finished_at": "2026-08-09T00:01:00Z",
                "agent_duration_seconds": 60,
                "agent_timeout_seconds": 900,
                "reward": reward,
                "trial_status": "evaluated",
                "failure": None if reward else "failed",
                "failure_category": None if reward else "repair_incomplete",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cache_read_tokens": 50,
                    "cache_creation_tokens": 0,
                    "cost_usd": 0.01,
                },
                "verification": {
                    "immediate_http": bool(reward),
                    "service_restart": bool(reward),
                    "host_reboot": bool(reward),
                },
                "result_file": "/tmp/private/result.json",
                "transcript_file": "/tmp/private/transcript.json",
            }
        ],
    }


class PublisherTests(unittest.TestCase):
    def write_summary(self, root: Path, name: str, value: dict) -> Path:
        path = root / name / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
        return path

    def rename_scenario(self, value: dict, scenario_id: str) -> dict:
        value = deepcopy(value)
        value["benchmark"]["scenarios"][0]["id"] = scenario_id
        value["runs"][0]["scenario"] = scenario_id
        value["runs"][0]["run_id"] = (
            f"{scenario_id}-{value['runs'][0]['model']}-1"
        )
        return value

    def test_imports_compatible_summaries_without_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(root, "matrix-one", summary("model/a"))
            second = self.write_summary(root, "matrix-two", summary("model/b"))
            release = create_release("20260809.0.0", [first, second], {})

        self.assertEqual(release["totals"]["passed"], 2)
        self.assertEqual(len(release["sources"]), 2)
        self.assertNotIn("result_file", release["runs"][0])
        self.assertNotIn("transcript_file", release["runs"][0])

    def test_external_agent_identity_does_not_publish_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("model/a")
            value["benchmark"]["agent"] = {
                "name": "opencode",
                "adapter": "/home/user/replaybook/adapters/opencode.sh",
                "payload": "/home/user/.opencode/bin/opencode",
            }
            value["runs"][0]["agent"] = "opencode"
            path = self.write_summary(root, "matrix", value)
            release = create_release("20260820.0.0", [path], {})

        self.assertEqual(
            release["compatibility"]["agent"],
            {
                "name": "opencode",
                "adapter": "external:opencode.sh",
                "payload": "external:opencode",
            },
        )

    def test_preserves_usage_when_cost_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("subscription/model")
            value["runs"][0]["usage"]["cost_usd"] = None
            value["runs"][0]["usage"]["subscription_usage_usd"] = 0.25
            path = self.write_summary(root, "matrix", value)
            release = create_release("20260820.0.0", [path], {})

        self.assertEqual(release["totals"]["usage_reported_trials"], 1)
        self.assertEqual(release["totals"]["cost_reported_trials"], 0)
        self.assertEqual(release["totals"]["known_cost_usd"], 0)
        self.assertEqual(release["totals"]["subscription_usage_usd"], 0.25)
        self.assertEqual(
            release["totals"]["subscription_usage_reported_trials"], 1
        )
        self.assertEqual(release["totals"]["input_tokens"], 100)
        self.assertIsNone(cost_per_repair(release["totals"]))
        page = html_page(release)
        self.assertNotIn("$0.0000+", page)
        self.assertIn("$0.2500", page)
        self.assertIn("provider usage value", page)

    def test_propagates_tier_into_release_and_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary(
                "model/a",
                tier="core",
                benchmark_hash="d" * 64,
            )
            path = self.write_summary(root, "matrix", value)
            release = create_release("20260819.0.0", [path], {})

        self.assertEqual(release["tier"], "core")
        self.assertEqual(release["compatibility"]["benchmark_tier"], "core")
        self.assertIn("Core tier", html_page(release))

    def test_rejects_cross_tier_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(root, "smoke", summary("model/a", tier="smoke"))
            second = self.write_summary(root, "core", summary("model/b", tier="core"))
            with self.assertRaisesRegex(PublishError, "benchmark_tier differs"):
                create_release("20260819.0.0", [first, second], {})

    def test_imports_complete_model_cohort_split_across_scenario_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [
                self.write_summary(root, "a-one", summary("model/a")),
                self.write_summary(
                    root,
                    "a-two",
                    self.rename_scenario(summary("model/a"), "002-redis"),
                ),
                self.write_summary(root, "b-one", summary("model/b")),
                self.write_summary(
                    root,
                    "b-two",
                    self.rename_scenario(summary("model/b"), "002-redis"),
                ),
            ]

            release = create_release("20260809.0.0", paths, {})

        self.assertEqual(release["totals"]["passed"], 4)
        self.assertEqual(
            [scenario["id"] for scenario in release["compatibility"]["scenarios"]],
            ["001-nginx", "002-redis"],
        )

    def test_rejects_incomplete_model_cohort_across_scenario_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [
                self.write_summary(root, "a-one", summary("model/a")),
                self.write_summary(
                    root,
                    "a-two",
                    self.rename_scenario(summary("model/a"), "002-redis"),
                ),
                self.write_summary(root, "b-one", summary("model/b")),
            ]

            with self.assertRaisesRegex(PublishError, "cohort is incomplete"):
                create_release("20260809.0.0", paths, {})

    def test_reasoning_efforts_are_published_as_distinct_model_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("deepseek/model")
            value["benchmark"]["reasoning_efforts"] = ["low", "high"]
            low = value["runs"][0]
            low["reasoning_effort"] = "low"
            low["run_id"] = "001-nginx-deepseek-model-reasoning-low-1"
            high = deepcopy(low)
            high["reasoning_effort"] = "high"
            high["run_id"] = "001-nginx-deepseek-model-reasoning-high-1"
            value["runs"].append(high)
            path = self.write_summary(root, "reasoning-matrix", value)

            release = create_release("20260809.0.0", [path], {})

        self.assertEqual(len(release["by_model"]), 2)
        self.assertEqual(
            {row["reasoning_effort"] for row in release["by_model"]},
            {"low", "high"},
        )
        page = html_page(release)
        self.assertIn("deepseek/model (low)", page)
        self.assertIn("deepseek/model (high)", page)
        self.assertIn("--reasoning-efforts low high", page)
        self.assertIn("1 controlled matrix.", page)

    def test_annotation_can_override_the_reproduction_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a"))
            command = (
                "python integrations/host/run_host_matrix.py "
                "--benchmark ../incidents/benchmark-real.toml --models model/a"
            )
            release = create_release(
                "20260809.0.0",
                [path],
                {"reproduction_command": command},
            )

        page = html_page(release)
        self.assertIn(command, page)
        self.assertNotIn("--attempts 1", page)

    def test_rejects_an_empty_reproduction_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a"))
            with self.assertRaisesRegex(PublishError, "reproduction_command"):
                create_release(
                    "20260809.0.0",
                    [path],
                    {"reproduction_command": "  "},
                )

    def test_publishes_compact_execution_recording_medians(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("model/a")
            value["runs"][0]["recording"] = {
                "transcript_schema_version": 2,
                "total_duration_ms": 12000,
                "model_rounds": [
                    {"index": 1, "duration_ms": 4000},
                    {"index": 2, "duration_ms": 3000},
                ],
                "tools": [
                    {
                        "name": "Bash",
                        "read_only": True,
                        "started_after_ms": 1000,
                        "duration_ms": 500,
                    },
                    {
                        "name": "Bash",
                        "read_only": False,
                        "started_after_ms": 3000,
                        "duration_ms": 1000,
                    },
                ],
            }
            path = self.write_summary(root, "recorded-matrix", value)

            release = create_release("20260810.0.0", [path], {})

        run_recording = release["runs"][0]["recording"]
        self.assertEqual(run_recording["model_rounds"], 2)
        self.assertEqual(run_recording["model_duration_seconds"], 7)
        self.assertEqual(run_recording["first_non_read_only_tool_seconds"], 3)
        self.assertNotIsInstance(run_recording["model_rounds"], list)
        aggregate = release["by_model"][0]
        self.assertEqual(aggregate["recording_reported_trials"], 1)
        self.assertEqual(aggregate["median_post_first_non_read_only_seconds"], 9)
        page = html_page(release)
        self.assertIn("Execution recording", page)
        self.assertIn("First non-read", page)

    def test_publishes_resumed_harness_and_post_timeout_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("model/a", reward=0)
            value["harness_versions"] = [15, 17]
            run = value["runs"][0]
            run["failure_category"] = "agent_timeout"
            run["verification"]["after_agent_timeout"] = {
                "durable_repair": True,
            }
            path = self.write_summary(root, "resumed-matrix", value)

            release = create_release("20260812.0.0", [path], {})

        self.assertEqual(release["compatibility"]["harness_versions"], [15, 17])
        self.assertEqual(release["totals"]["durable_repairs_after_timeout"], 1)
        page = html_page(release)
        self.assertIn("Host harness v15/v17", page)
        self.assertIn("became durable after the agent deadline", page)

    def test_rejects_incompatible_scenario_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(root, "matrix-one", summary("model/a"))
            second = self.write_summary(
                root, "matrix-two", summary("model/b", scenario_version=2)
            )
            with self.assertRaisesRegex(PublishError, "scenario 001-nginx differs"):
                create_release("20260809.0.0", [first, second], {})

    def test_rejects_incompatible_scenario_pack_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(root, "matrix-one", summary("model/a"))
            second = self.write_summary(
                root,
                "matrix-two",
                summary("model/b", pack_version="20260809.0.1"),
            )
            with self.assertRaisesRegex(PublishError, "scenario_packs differs"):
                create_release("20260809.0.0", [first, second], {})

    def test_selected_scenario_compatibility_ignores_pack_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(
                root,
                "matrix-one",
                summary(
                    "model/a",
                    snapshot_hash="a" * 64,
                    scenario_snapshot_hash="e" * 64,
                    pack_snapshot_hash="b" * 64,
                    pack_version="20260817.0.0",
                ),
            )
            second = self.write_summary(
                root,
                "matrix-two",
                summary(
                    "model/b",
                    snapshot_hash="a" * 64,
                    scenario_snapshot_hash="e" * 64,
                    pack_snapshot_hash="c" * 64,
                    pack_version="20260817.0.1",
                ),
            )

            release = create_release("20260817.0.0", [first, second], {})

        self.assertEqual(release["totals"]["passed"], 2)
        self.assertEqual(
            [source["scenario_packs"][0]["version"] for source in release["sources"]],
            ["20260817.0.0", "20260817.0.1"],
        )
        self.assertEqual(
            release["compatibility"]["scenario_packs"],
            [{"id": "example/incidents", "version": "20260817.0.0"}],
        )
        snapshot = release["compatibility"]["execution_snapshot"]
        self.assertEqual(snapshot["scenario_packs"], [{"id": "example/incidents"}])
        self.assertEqual(snapshot["selected_scenarios"][0]["sha256"], "e" * 64)

    def test_selected_scenario_compatibility_rejects_selected_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(
                root,
                "matrix-one",
                summary(
                    "model/a",
                    snapshot_hash="a" * 64,
                    scenario_snapshot_hash="e" * 64,
                ),
            )
            second = self.write_summary(
                root,
                "matrix-two",
                summary(
                    "model/b",
                    snapshot_hash="a" * 64,
                    scenario_snapshot_hash="f" * 64,
                ),
            )

            with self.assertRaisesRegex(PublishError, "execution_snapshot differs"):
                create_release("20260817.0.0", [first, second], {})

    def test_rejects_incompatible_execution_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(
                root,
                "matrix-one",
                summary("model/a", snapshot_hash="a" * 64),
            )
            second = self.write_summary(
                root,
                "matrix-two",
                summary("model/b", snapshot_hash="c" * 64),
            )
            with self.assertRaisesRegex(PublishError, "execution_snapshot differs"):
                create_release("20260809.0.0", [first, second], {})

    def test_pack_commits_are_provenance_not_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_value = summary("model/a", snapshot_hash="a" * 64)
            second_value = summary("model/b", snapshot_hash="a" * 64)
            first_value["benchmark"]["execution_snapshot"]["scenario_packs"][0][
                "git_commit"
            ] = "1" * 40
            second_value["benchmark"]["execution_snapshot"]["scenario_packs"][0][
                "git_commit"
            ] = "2" * 40
            first = self.write_summary(root, "matrix-one", first_value)
            second = self.write_summary(root, "matrix-two", second_value)

            release = create_release("20260817.0.0", [first, second], {})

        self.assertEqual(release["totals"]["passed"], 2)
        self.assertEqual(
            release["sources"][0]["execution_snapshot"]["scenario_packs"][0][
                "git_commit"
            ],
            "1" * 40,
        )
        self.assertNotIn(
            "git_commit",
            release["compatibility"]["execution_snapshot"]["scenario_packs"][0],
        )

    def test_normalizes_missing_optional_execution_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_value = summary("model/a", snapshot_hash="a" * 64)
            second_value = summary("model/b", snapshot_hash="a" * 64)
            second_value["benchmark"]["execution_snapshot"][
                "benchmark_manifest_sha256"
            ] = None
            first = self.write_summary(root, "matrix-one", first_value)
            second = self.write_summary(root, "matrix-two", second_value)

            release = create_release("20260810.0.0", [first, second], {})

        self.assertEqual(release["totals"]["passed"], 2)

    def test_rejects_incompatible_benchmark_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(
                root,
                "matrix-one",
                summary("model/a", benchmark_hash="a" * 64),
            )
            second = self.write_summary(
                root,
                "matrix-two",
                summary("model/b", benchmark_hash="b" * 64),
            )
            with self.assertRaisesRegex(PublishError, "benchmark_manifest differs"):
                create_release("20260810.0.0", [first, second], {})

    def test_rejects_an_incomplete_source_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("model/a")
            value["benchmark"]["attempts"] = 2
            path = self.write_summary(root, "matrix", value)
            with self.assertRaisesRegex(PublishError, "1 missing"):
                create_release("20260809.0.0", [path], {})

    def test_applies_audited_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a", reward=0))
            annotations = {
                "corrections": [
                    {
                        "run_id": "001-nginx-model/a-1",
                        "changes": {"failure_category": "agent_timeout"},
                        "reason": "elapsed time exceeded the configured limit",
                    }
                ]
            }
            release = create_release("20260809.0.0", [path], annotations)

        self.assertEqual(release["totals"]["failure_categories"], {"agent_timeout": 1})
        self.assertEqual(
            release["corrections"][0]["original"],
            {"failure_category": "repair_incomplete"},
        )

    def test_build_and_check_generated_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a"))
            release = create_release(
                "20260809.0.0",
                [path],
                {
                    "title": "Current release",
                    "description": "A durable baseline.",
                    "notes": ["One worker was replaced after an infrastructure interruption."],
                },
            )
            write_json(
                root / "benchmark-data/index.json",
                {
                    "schema_version": 1,
                    "current_version": "20260809.0.0",
                    "coverage_fleet": [
                        {"model": "model/a", "reasoning_effort": None}
                    ],
                    "releases": ["20260809.0.0"],
                },
            )
            write_json(root / "benchmark-data/releases/20260809.0.0.json", release)
            (root / "docs").mkdir()
            (root / "docs/benchmark-history.html").write_text(
                f"before\n{HISTORY_START}\n{HISTORY_END}\nafter\n"
            )
            (root / "benchmarks.md").write_text(
                f"before\n{MARKDOWN_START}\n{MARKDOWN_END}\nafter\n"
            )

            build_outputs(root)
            build_outputs(root, check=True)
            current = (root / "docs/benchmarks.html").read_text()
            coverage = (root / "docs/benchmark-coverage.html").read_text()
            compare = (root / "docs/benchmark-compare.html").read_text()
            explorer = (root / "docs/benchmark-explorer.html").read_text()
            model_page = (root / "docs/benchmark-model.html").read_text()
            models_page = (root / "docs/benchmark-models.html").read_text()
            catalog = json.loads((root / "benchmark-data/catalog.json").read_text())
            coverage_data = json.loads(
                (root / "benchmark-data/coverage.json").read_text()
            )
            self.assertIn("Current release", current)
            self.assertIn("Recent scenario cohorts", current)
            self.assertIn("001-nginx", current)
            self.assertIn("example/incidents@20260809.0.0", current)
            self.assertIn("scenario pack revisions", current)
            self.assertIn("Run notes", current)
            self.assertIn("infrastructure interruption", current)
            self.assertIn("Compare this cohort", current)
            self.assertNotIn("\n+  --scenario", current)
            self.assertIn("Benchmark explorer", explorer)
            self.assertIn("cost / repair", explorer)
            self.assertIn("release-filter", explorer)
            self.assertIn("Benchmark coverage", coverage)
            self.assertIn("Rows are comparison boundaries", coverage)
            self.assertIn("coverage-cells", coverage)
            self.assertIn("benchmark-coverage.json", coverage)
            self.assertNotIn("\0", coverage)
            self.assertIn("Compare benchmark lanes", compare)
            self.assertIn("compatibility-status", compare)
            self.assertIn("Scenario head-to-head", compare)
            self.assertIn("benchmark-catalog.json", compare)
            self.assertIn("Model evidence", models_page)
            self.assertIn("not one pooled leaderboard", models_page)
            self.assertIn("model-grid", models_page)
            self.assertIn("Compare latest cohort", models_page)
            self.assertIn("Scenario evidence", model_page)
            self.assertIn("profile-results", model_page)
            self.assertIn("benchmark-coverage.json", model_page)
            self.assertIn("profile-compare", model_page)
            self.assertEqual(catalog["current_version"], "20260809.0.0")
            self.assertEqual(
                catalog["coverage_fleet"],
                [{"model": "model/a", "reasoning_effort": None}],
            )
            self.assertEqual(len(catalog["records"]), 1)
            self.assertEqual(catalog["records"][0]["model"], "model/a")
            self.assertEqual(len(catalog["lanes"]), 1)
            self.assertEqual(catalog["lanes"][0]["model"], "model/a")
            self.assertEqual(catalog["lanes"][0]["passed"], 1)
            self.assertEqual(
                catalog["releases"][0]["scenarios"][0]["id"], "001-nginx"
            )
            self.assertEqual(catalog["releases"][0]["scenarios"][0]["version"], 1)
            self.assertAlmostEqual(
                catalog["records"][0]["cost_per_repair_usd"], 0.01
            )
            self.assertEqual(coverage_data["schema_version"], 1)
            self.assertEqual(
                coverage_data["comparison_policy"],
                "newest_exact_scenario_cohort",
            )
            self.assertEqual(
                coverage_data["totals"],
                {
                    "scenarios": 1,
                    "model_lanes": 1,
                    "covered_cells": 1,
                    "possible_cells": 1,
                    "missing_cells": 0,
                },
            )
            self.assertEqual(
                coverage_data["scenarios"][0]["cells"][0]["status"],
                "covered",
            )
            self.assertEqual(
                coverage_data["scenarios"][0]["boundary"]["harness_versions"],
                [5],
            )
            self.assertTrue((root / "docs/benchmark-coverage.json").is_file())
            self.assertTrue((root / "docs/benchmark-catalog.json").is_file())

            (root / "docs/benchmarks.html").write_text("stale")
            with self.assertRaisesRegex(PublishError, "stale"):
                build_outputs(root, check=True)

    def test_companion_harness_is_visible_but_does_not_replace_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary_path = self.write_summary(root, "primary", summary("model/a"))
            companion_value = summary("subscription/model")
            companion_value["benchmark"]["agent"] = {
                "name": "other-harness",
                "adapter": "/tmp/other.sh",
                "payload": "/tmp/other",
            }
            companion_value["runs"][0]["agent"] = "other-harness"
            companion_path = self.write_summary(root, "companion", companion_value)
            primary = create_release(
                "20260819.0.0", [primary_path], {"title": "Primary cohort"}
            )
            companion = create_release(
                "20260820.0.0", [companion_path], {"title": "Other harness"}
            )
            self.assertIsNone(companion["compatibility"]["claux_release"])
            self.assertEqual(
                companion["compatibility"]["agent_harness"],
                {
                    "id": "other-harness",
                    "label": "Other Harness",
                    "version": None,
                    "provider": "subscription",
                    "billing": "unknown",
                },
            )
            write_json(
                root / "benchmark-data/index.json",
                {
                    "schema_version": 1,
                    "current_version": "20260819.0.0",
                    "companion_versions": ["20260820.0.0"],
                    "coverage_fleet": [
                        {"model": "model/a", "reasoning_effort": None}
                    ],
                    "releases": ["20260819.0.0", "20260820.0.0"],
                },
            )
            write_json(root / "benchmark-data/releases/20260819.0.0.json", primary)
            write_json(
                root / "benchmark-data/releases/20260820.0.0.json", companion
            )
            (root / "docs").mkdir()
            (root / "docs/benchmark-history.html").write_text(
                f"before\n{HISTORY_START}\n{HISTORY_END}\nafter\n"
            )
            (root / "benchmarks.md").write_text(
                f"before\n{MARKDOWN_START}\n{MARKDOWN_END}\nafter\n"
            )

            build_outputs(root)
            current = (root / "docs/benchmarks.html").read_text()
            coverage = json.loads(
                (root / "benchmark-data/coverage.json").read_text()
            )
            catalog = json.loads(
                (root / "benchmark-data/catalog.json").read_text()
            )

        self.assertIn("Primary cohort", current)
        self.assertIn("Companion harness cohorts", current)
        self.assertIn("Other harness", current)
        self.assertIn("Agent harness", current)
        self.assertEqual(catalog["releases"][1]["role"], "companion")
        self.assertEqual(
            catalog["releases"][1]["agent_harness"]["id"], "other-harness"
        )
        self.assertEqual(coverage["scenarios"][0]["release"], "20260819.0.0")
        self.assertEqual(coverage["scenarios"][0]["cells"][0]["model"], "model/a")

    def test_legacy_custom_adapter_does_not_render_as_claux(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = summary("opencode-go/glm-5.3")
            value["benchmark"]["agent"] = {
                "name": "opencode",
                "adapter": "/tmp/opencode.sh",
                "payload": "/tmp/opencode",
            }
            value["runs"][0]["agent"] = "opencode"
            path = self.write_summary(root, "legacy-opencode", value)
            release = create_release("20260820.0.0", [path], {})
            release["compatibility"].pop("agent_harness")
            release["compatibility"]["claux_release"] = "v20990101.0.0"

        harness = normalized_agent_harness(release)
        page = html_page(release)
        self.assertEqual(harness["label"], "OpenCode")
        self.assertEqual(harness["provider"], "OpenCode Go")
        self.assertEqual(harness["billing"], "subscription")
        self.assertIn("OpenCode agent harness", page)
        self.assertNotIn("Claux v20990101.0.0", page)

    def test_annotation_can_define_agent_harness_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("provider/model"))
            release = create_release(
                "20260820.0.0",
                [path],
                {
                    "agent_harness": {
                        "id": "custom",
                        "label": "Custom Harness",
                        "version": "v2",
                        "provider": "Example Provider",
                        "billing": "subscription",
                    }
                },
            )

        self.assertEqual(
            release["compatibility"]["agent_harness"]["label"],
            "Custom Harness",
        )
        self.assertIn("Custom Harness v2 agent harness", html_page(release))

    def test_rejects_invalid_agent_harness_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("provider/model"))
            with self.assertRaisesRegex(PublishError, "agent_harness"):
                create_release(
                    "20260820.0.0",
                    [path],
                    {"agent_harness": {"id": "custom"}},
                )

    def test_homepage_lists_recent_distinct_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nginx_path = self.write_summary(root, "nginx", summary("model/a"))
            redis_path = self.write_summary(
                root,
                "redis",
                self.rename_scenario(summary("model/a"), "002-redis"),
            )
            nginx = create_release(
                "20260809.0.0", [nginx_path], {"title": "Current Nginx"}
            )
            redis = create_release(
                "20260808.0.0", [redis_path], {"title": "Earlier Redis"}
            )
            write_json(
                root / "benchmark-data/index.json",
                {
                    "schema_version": 1,
                    "current_version": "20260809.0.0",
                    "releases": ["20260808.0.0", "20260809.0.0"],
                },
            )
            write_json(root / "benchmark-data/releases/20260808.0.0.json", redis)
            write_json(root / "benchmark-data/releases/20260809.0.0.json", nginx)
            (root / "docs").mkdir()
            (root / "docs/benchmark-history.html").write_text(
                f"{HISTORY_START}\n{HISTORY_END}\n"
            )
            (root / "benchmarks.md").write_text(
                f"{MARKDOWN_START}\n{MARKDOWN_END}\n"
            )

            build_outputs(root)

            current = (root / "docs/benchmarks.html").read_text()
            self.assertIn("001-nginx", current)
            self.assertIn("002-redis", current)
            self.assertIn(
                "release=20260808.0.0&amp;scenario=002-redis", current
            )

    def test_history_retains_superseded_generated_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a"))
            old = create_release(
                "20260808.0.0", [path], {"title": "Earlier generated release"}
            )
            current = deepcopy(old)
            current["version"] = "20260809.0.0"
            current["title"] = "Current generated release"
            write_json(
                root / "benchmark-data/index.json",
                {
                    "schema_version": 1,
                    "current_version": "20260809.0.0",
                    "releases": ["20260808.0.0", "20260809.0.0"],
                },
            )
            write_json(root / "benchmark-data/releases/20260808.0.0.json", old)
            write_json(root / "benchmark-data/releases/20260809.0.0.json", current)
            (root / "docs").mkdir()
            (root / "docs/benchmark-history.html").write_text(
                f"{HISTORY_START}\n{HISTORY_END}\n"
            )
            (root / "benchmarks.md").write_text(
                f"{MARKDOWN_START}\n{MARKDOWN_END}\n"
            )
            build_outputs(root)

            history = (root / "docs/benchmark-history.html").read_text()
            self.assertIn("Earlier generated release", history)
            self.assertNotIn("Current generated release", history)

    def test_html_page_has_no_patch_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a"))
            release = create_release("20260809.0.0", [path], {})
        self.assertNotIn("\n+", html_page(release))

    def test_rejects_stale_snapshot_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_summary(root, "matrix", summary("model/a"))
            release = create_release("20260809.0.0", [path], {})
            release["totals"]["passed"] = 0
            with self.assertRaisesRegex(PublishError, "totals"):
                validate_release(release, root / "release.json")


if __name__ == "__main__":
    unittest.main()
