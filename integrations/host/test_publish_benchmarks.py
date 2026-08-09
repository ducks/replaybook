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
    create_release,
    html_page,
    validate_release,
    write_json,
)


def summary(
    model: str,
    *,
    scenario_version: int = 1,
    pack_version: str = "20260809.0.0",
    reward: int = 1,
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

    def test_rejects_incompatible_scenario_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_summary(root, "matrix-one", summary("model/a"))
            second = self.write_summary(
                root, "matrix-two", summary("model/b", scenario_version=2)
            )
            with self.assertRaisesRegex(PublishError, "scenarios differs"):
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
            with self.assertRaisesRegex(PublishError, "scenarios differs"):
                create_release("20260809.0.0", [first, second], {})

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
            self.assertIn("Current release", current)
            self.assertIn("example/incidents@20260809.0.0", current)
            self.assertIn("scenario pack revisions", current)
            self.assertIn("Run notes", current)
            self.assertIn("infrastructure interruption", current)
            self.assertNotIn("\n+  --scenario", current)

            (root / "docs/benchmarks.html").write_text("stale")
            with self.assertRaisesRegex(PublishError, "stale"):
                build_outputs(root, check=True)

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
