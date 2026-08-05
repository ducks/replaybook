"""Tests for Harbor trajectory analysis."""

import json
import tempfile
import unittest
from pathlib import Path

from integrations.harbor.analyze_trajectory import (
    analyze,
    resolve_trajectories,
    text_report,
)


def trajectory():
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": "trial-agent",
        "agent": {
            "name": "claux",
            "version": "20260804.0.0",
            "model_name": "minimax/minimax-m3",
        },
        "steps": [
            {"step_id": 1, "source": "user", "message": "repair it"},
            {
                "step_id": 2,
                "source": "agent",
                "message": "",
                "tool_calls": [
                    {
                        "tool_call_id": "call-1",
                        "function_name": "Bash",
                        "arguments": {
                            "command": (
                                "docker stop app\n"
                                "docker rm app\n"
                                "docker run image"
                            )
                        },
                    }
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "call-1",
                            "content": "stopped\nremoved\nstarted\n",
                            "extra": {"is_error": False},
                        }
                    ]
                },
            },
            {"step_id": 3, "source": "agent", "message": "done"},
        ],
        "final_metrics": {
            "total_prompt_tokens": 100,
            "total_completion_tokens": 20,
            "total_cached_tokens": 80,
            "total_cost_usd": 0.01,
            "total_steps": 3,
        },
    }


class TrajectoryAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        trial = self.root / "jobs" / "trial"
        agent = trial / "agent"
        verifier = trial / "verifier"
        agent.mkdir(parents=True)
        verifier.mkdir()
        self.path = agent / "trajectory.json"
        self.path.write_text(json.dumps(trajectory()))
        (verifier / "failure-category.txt").write_text("topology_changed\n")
        (verifier / "test-stdout.txt").write_text(
            "FAIL[topology_changed]: app container not found\n"
        )
        (verifier / "reward.txt").write_text("0\n")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_finds_trajectories_recursively(self):
        self.assertEqual(resolve_trajectories([self.root]), [self.path])

    def test_summarizes_tools_mutations_usage_and_verifier(self):
        result = analyze(self.path)

        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["tool_errors"], 0)
        self.assertEqual(result["first_mutation"]["kind"], "container_stop")
        self.assertEqual(
            result["mutation_counts"],
            {"container_create": 1, "container_remove": 1, "container_stop": 1},
        )
        self.assertEqual(
            result["risk_signals"],
            ["container_replaced_outside_detected_orchestrator"],
        )
        self.assertEqual(result["failure_category"], "topology_changed")
        self.assertEqual(result["reward"], "0")
        self.assertEqual(result["usage"]["cost_usd"], 0.01)

    def test_text_report_highlights_consequential_behavior(self):
        output = text_report([analyze(self.path)])

        self.assertIn("Tools: 1 calls, 0 errors", output)
        self.assertIn("First mutation: step 2 container_stop", output)
        self.assertIn("container_replaced_outside_detected_orchestrator", output)
        self.assertIn("category=topology_changed", output)


if __name__ == "__main__":
    unittest.main()
