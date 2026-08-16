from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from integrations.trajectory.export_atif import export, resolve_trials, to_atif


def fixture() -> tuple[dict, dict]:
    result = {
        "schema_version": 7,
        "run_id": "nginx-model-1",
        "scenario": "001-nginx-502-host",
        "scenario_version": 1,
        "scenario_pack": {"id": "ducks/replaybook-infra", "version": "20260816.0.0"},
        "agent": "claux",
        "harness_version": 20,
        "model": "test/model",
        "reasoning_effort": "high",
        "agent_timeout_seconds": 900,
        "agent_duration_seconds": 42.5,
        "trial_status": "evaluated",
        "failure_category": None,
        "reward": 1,
        "verification": {
            "immediate_http": True,
            "service_restart": True,
            "host_reboot": True,
        },
    }
    transcript = {
        "schema_version": 2,
        "model": "test/model",
        "messages": [
            {"role": "user", "content": "repair the service"},
            {"role": "assistant", "content": "private reasoning must not export"},
        ],
        "tool_trace": [
            {
                "id": "call-1",
                "name": "Bash",
                "input": {"command": "systemctl status app"},
                "output": "inactive\n",
                "is_error": False,
                "read_only": True,
                "started_after_ms": 100,
                "duration_ms": 20,
            },
            {
                "id": "call-2",
                "name": "Edit",
                "input": {"file_path": "/etc/app.conf"},
                "output": "done",
                "is_error": False,
                "read_only": False,
                "started_after_ms": 200,
                "duration_ms": 10,
            },
        ],
        "outcome": {"status": "completed", "result": "service repaired"},
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 50,
            "cache_creation_tokens": 5,
            "cost_usd": 0.01,
        },
        "timing": {
            "total_duration_ms": 42_500,
            "model_rounds": [{"index": 1, "duration_ms": 80}],
        },
    }
    return result, transcript


class ExportAtifTests(unittest.TestCase):
    def test_exports_observable_trace_and_verifier_metadata(self) -> None:
        result, transcript = fixture()
        trajectory = to_atif(result, transcript, Path("transcript.json"))

        self.assertEqual(trajectory["schema_version"], "ATIF-v1.7")
        self.assertEqual(trajectory["agent"]["name"], "claux")
        self.assertEqual(trajectory["agent"]["model_name"], "test/model")
        self.assertEqual(trajectory["steps"][0]["source"], "user")
        self.assertEqual(
            trajectory["steps"][1]["tool_calls"][0]["arguments"]["command"],
            "systemctl status app",
        )
        self.assertEqual(
            trajectory["steps"][1]["observation"]["results"][0]["content"],
            "inactive\n",
        )
        self.assertNotIn("private reasoning", json.dumps(trajectory))
        metrics = trajectory["final_metrics"]
        self.assertEqual(metrics["total_cached_tokens"], 55)
        evaluation = metrics["extra"]["infrastructure_evaluation"]
        self.assertEqual(evaluation["schema_version"], 1)
        self.assertEqual(evaluation["producer"]["name"], "replaybook")
        self.assertEqual(evaluation["producer"]["version"], "20")
        self.assertIsNone(evaluation["producer"]["revision"])
        self.assertEqual(evaluation["task"]["version"], "1")
        self.assertTrue(evaluation["outcome"]["verification"]["host_reboot"])

    def test_discovers_and_exports_a_matrix_trial(self) -> None:
        result, transcript = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trial = root / "runs" / "trial-one"
            (trial / "results").mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps(result))
            (trial / "results" / "transcript.json").write_text(json.dumps(transcript))

            resolved = resolve_trials([root])
            self.assertEqual(len(resolved), 1)
            written = export([root], root / "export")

            self.assertEqual(len(written), 1)
            value = json.loads(written[0].read_text())
            self.assertEqual(value["session_id"], "nginx-model-1")


if __name__ == "__main__":
    unittest.main()
