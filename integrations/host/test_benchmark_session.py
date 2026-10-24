"""Session lifecycle and reporting tests without model calls or VMs."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from integrations.host import benchmark_session as sessions


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def fixture(matrix, outcomes=(1, 0, None), finished=False):
    matrix.mkdir(parents=True, exist_ok=True)
    write(matrix / "benchmark.json", {
        "scenarios": [{"id": "incident"}], "models": ["test/model"],
        "attempts": len(outcomes), "base_port": 23000, "concurrency": 1,
        "agent": {"provider": "test-provider"}, "reasoning_efforts": [],
    })
    for index, reward in enumerate(outcomes, start=1):
        if reward is None:
            continue
        run = matrix / "runs" / f"incident-test-model-{index}"
        write(run / "result.json", {
            "harness_version": 23, "scenario": "incident", "scenario_version": 1,
            "model": "test/model", "provider": "test-provider", "reward": reward,
            "trial_status": "evaluated", "usage": {"cost_usd": 0.1},
            "failure_category": None if reward else "agent_timeout",
            # An agent's prose is deliberately inconsistent with its score.
            "result": "I successfully fixed everything",
        })
    if finished:
        write(matrix / "summary.json", {"infrastructure_errors": []})


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def profile(self, **updates):
        profile = {"args": ["--models", "test/model", "--agent-provider", "test-provider"],
                   "environment": {"REPLAYBOOK_CLAUX_PROVIDER_ROUTES": '{"test/model":"openrouter"}'}}
        profile.update(updates)
        path = self.root / "sessions.json"
        write(path, {"schema_version": 1, "profiles": {"test": profile}})
        return path

    def spec(self, session, **updates):
        value = {"profile": "test", "args": [], "environment": {}, "after": None}
        value.update(updates)
        write(session / "session.json", value)

    def test_profiles_reject_credentials_and_lifecycle_overrides(self):
        path = self.profile(environment={"OPENROUTER_API_KEY": "secret"})
        with self.assertRaisesRegex(ValueError, "credentials"):
            sessions.load_profile(path, "test")
        path = self.profile(args=["--models", "test/model", "--output-dir", "/tmp/output"])
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            sessions.load_profile(path, "test")

    def test_start_freezes_profile_and_after_target(self):
        prerequisite = self.root / "prerequisite"
        fixture(prerequisite)
        path = self.profile()
        with patch.object(sessions, "launch_worker") as launch:
            target = sessions.start_session("test", path, self.root, str(prerequisite))
        path.write_text('{}')
        spec = sessions.read_object(target / "session.json")
        self.assertEqual(spec["args"], ["--models", "test/model", "--agent-provider", "test-provider"])
        self.assertEqual(spec["after"], str(prerequisite))
        launch.assert_called_once_with(target)

    def test_live_session_cannot_start_a_second_worker(self):
        session = self.root / "session"
        self.spec(session)
        with patch.object(sessions, "worker_alive", return_value=True), \
             patch.object(sessions.os, "posix_spawn") as spawn:
            with self.assertRaisesRegex(ValueError, "live worker"):
                sessions.launch_worker(session)
        spawn.assert_not_called()

    def test_status_uses_validated_results_and_separates_unavailable(self):
        matrix = self.root / "matrix"
        fixture(matrix)
        unavailable = matrix / "runs/incident-test-model-3/result.json"
        write(unavailable, {
            "harness_version": 23, "scenario": "incident", "scenario_version": 1,
            "model": "test/model", "provider": "test-provider", "reward": 0,
            "trial_status": "unavailable", "failure_category": "authentication_failed",
        })
        report = sessions.matrix_report(matrix)
        self.assertEqual(report["completed"], 3)
        self.assertEqual(report["totals"]["passed"], 1)
        self.assertEqual(report["totals"]["failed"], 1)
        self.assertEqual(report["totals"]["unavailable"], 1)
        self.assertAlmostEqual(report["totals"]["known_cost_usd"], 0.2)
        data = sessions.read_object(unavailable)
        data["model"] = "wrong/model"
        write(unavailable, data)
        report = sessions.matrix_report(matrix)
        self.assertEqual(report["completed"], 2)
        self.assertEqual(len(report["invalid_results"]), 1)

    def test_live_resume_ignores_old_terminal_summary(self):
        session = self.root / "session"
        self.spec(session)
        fixture(session / "matrix", outcomes=(1,), finished=True)
        write(session / "status.json", {"state": "running"})
        with patch.object(sessions, "worker_alive", return_value=True):
            self.assertEqual(sessions.session_report(session)["state"], "running")
        with patch.object(sessions, "worker_alive", return_value=False):
            self.assertEqual(sessions.session_report(session)["state"], "completed")

    def test_watcher_waits_for_summary_write_to_finish(self):
        matrix = self.root / "matrix"
        fixture(matrix, outcomes=(1,))
        (matrix / "summary.json").write_text('{')
        self.assertEqual(sessions.matrix_report(matrix)["state"], "in_progress")
        write(matrix / "summary.json", {"infrastructure_errors": []})
        self.assertEqual(sessions.matrix_report(matrix)["state"], "completed")

    def test_dead_worker_is_interrupted_and_pid_reuse_is_rejected(self):
        session = self.root / "session"
        self.spec(session)
        write(session / "status.json", {"state": "running"})
        write(session / "worker.json", {"pid": 42, "identity": "old"})
        with patch.object(sessions, "worker_identity", return_value="new"):
            self.assertFalse(sessions.worker_alive(session))
            self.assertEqual(sessions.session_report(session)["state"], "interrupted")

    def test_queued_worker_waits_for_complete_dependency(self):
        prerequisite = self.root / "prerequisite"
        fixture(prerequisite, outcomes=(0,))
        session = self.root / "session"
        self.spec(session, after=str(prerequisite))

        def complete_dependency(_seconds):
            subprocess_run.assert_not_called()
            self.assertEqual(sessions.read_object(session / "status.json")["state"], "queued")
            write(prerequisite / "summary.json", {"infrastructure_errors": []})

        def run(command, **kwargs):
            self.assertIn("--output-dir", command)
            fixture(session / "matrix", outcomes=(1,), finished=True)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(sessions.time, "sleep", side_effect=complete_dependency), \
             patch.object(sessions.subprocess, "run", side_effect=run) as subprocess_run:
            self.assertEqual(sessions.run_session(session), 0)
        self.assertEqual(sessions.read_object(session / "status.json")["state"], "completed")

    def test_resume_preserves_concurrency_and_only_requests_pending_work(self):
        session = self.root / "session"
        self.spec(session, args=["--models", "should-not-be-reused"])
        fixture(session / "matrix")
        with patch.object(sessions.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            sessions.run_session(session, resume=True, retry_unavailable=True)
        command = run.call_args.args[0]
        self.assertEqual(command[2:], ["--resume", str(session / "matrix"), "--concurrency", "1", "--retry-unavailable"])
        self.assertNotIn("should-not-be-reused", command)

    def test_resume_can_supply_custom_adapter_credentials_without_saving_them(self):
        session = self.root / "session"
        self.spec(session, args=["--agent-env-file", "/old/credentials.env"])
        fixture(session / "matrix")
        credentials = self.root / "credentials.env"
        credentials.write_text('API_KEY=private-secret\n')
        with patch.object(sessions.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            sessions.run_session(session, resume=True, agent_env_file=credentials)
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--agent-env-file", str(credentials)])
        self.assertNotIn("private-secret", (session / "session.json").read_text())
        self.assertNotIn("private-secret", (session / "status.json").read_text())

    def test_watcher_interrupt_never_stops_worker(self):
        matrix = self.root / "matrix"
        fixture(matrix)
        with patch.object(sessions.time, "sleep", side_effect=KeyboardInterrupt), \
             patch.object(sessions.os, "posix_spawn") as spawn, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sessions.main(["watch", str(matrix)]), 0)
        spawn.assert_not_called()
        self.assertIn("workers continue", output.getvalue())

    def test_actual_detached_start_reports_setup_failure_without_inference(self):
        config = self.profile(args=["--benchmark", str(self.root / "missing.toml"),
                                    "--models", "test/model", "--agent-provider", "test-provider"])
        session = sessions.start_session("test", config, self.root)
        pid = sessions.read_object(session / "worker.json")["pid"]
        self.addCleanup(lambda: os.waitpid(pid, 0))
        self.assertEqual(os.getsid(pid), pid)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            report = sessions.session_report(session)
            if report["state"] == "failed":
                break
            time.sleep(0.05)
        else:
            self.fail((session / "controller.log").read_text())
        self.assertFalse((session / "matrix/benchmark.json").exists())
        result = subprocess.run([sys.executable, str(sessions.REPO_DIR / "replaybook-bench"),
                                 "status", str(session), "--json"],capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "failed")


if __name__ == "__main__":
    unittest.main()
