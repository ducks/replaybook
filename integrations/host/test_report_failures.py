import json
import tempfile
import unittest
from pathlib import Path

from integrations.host.report_failures import (
    failed_runs,
    failure_report,
    markdown_report,
    resolve_matrix,
)


class FailureReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.matrix = Path(self.temporary.name)
        self.run_id = "013-test-model-1"
        run_dir = self.matrix / "runs" / self.run_id
        (run_dir / "results").mkdir(parents=True)
        (run_dir / "scenario-state").mkdir()
        (self.matrix / "logs").mkdir()
        self.failed = {
            "run_id": self.run_id,
            "scenario": "013-sidekiq-wrong-redis",
            "scenario_version": 2,
            "model": "test/model",
            "reasoning_effort": "high",
            "attempt": 1,
            "agent_duration_seconds": 125,
            "trial_status": "evaluated",
            "reward": 0,
            "failure_category": "backlog_not_recovered",
        }
        summary = {"runs": [self.failed, {"run_id": "passed", "reward": 1}]}
        (self.matrix / "summary.json").write_text(json.dumps(summary))
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "failure_category": "backlog_not_recovered",
                    "failure": "repair did not recover backlog",
                    "verification": {"immediate_http": False},
                }
            )
        )
        (run_dir / "scenario-state" / "phase-failure.json").write_text(
            json.dumps(
                {"phase": "immediate", "step": 1, "message": "three IDs missing"}
            )
        )
        (run_dir / "results" / "transcript.json").write_text(
            json.dumps(
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Bash",
                                    "input": {"command": "redis-cli flushdb"},
                                },
                                {"type": "text", "text": "Fixed Redis routing."},
                            ],
                        }
                    ],
                    "tool_trace": [{"is_error": True}],
                    "outcome": {"status": "completed", "result": "Fixed Redis routing."},
                }
            )
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_resolves_directory_and_selects_only_scored_failure(self):
        matrix, summary = resolve_matrix(self.matrix)
        self.assertEqual(matrix, self.matrix.resolve())
        self.assertEqual([run["run_id"] for run in failed_runs(summary)], [self.run_id])

    def test_report_combines_verifier_agent_and_commands(self):
        report = failure_report(self.matrix, self.failed, 8)
        self.assertEqual(report["category"], "backlog_not_recovered")
        self.assertEqual(report["phase_failure"]["message"], "three IDs missing")
        self.assertEqual(report["agent_final_report"], "Fixed Redis routing.")
        self.assertEqual(report["recent_commands"], ["redis-cli flushdb"])
        self.assertEqual(report["tool_errors"], 1)
        self.assertFalse(report["empty_completion"])
        rendered = markdown_report(self.matrix, [report])
        self.assertIn("Scored failures: **1**", rendered)
        self.assertIn("redis-cli flushdb", rendered)
        self.assertIn("three IDs missing", rendered)

    def test_marks_an_empty_completed_response(self):
        transcript_path = (
            self.matrix / "runs" / self.run_id / "results" / "transcript.json"
        )
        transcript = json.loads(transcript_path.read_text())
        transcript["messages"] = []
        transcript["outcome"] = {"status": "completed", "result": ""}
        transcript_path.write_text(json.dumps(transcript))
        report = failure_report(self.matrix, self.failed, 0)
        self.assertTrue(report["empty_completion"])
        self.assertIn("empty final response", markdown_report(self.matrix, [report]))


if __name__ == "__main__":
    unittest.main()
