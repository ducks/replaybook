"""Frozen cohorts remain stable across development and model selections."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from integrations.host import benchmark_cohort as cohorts
from integrations.host import benchmark_session as sessions
from integrations.host.run_host_matrix import (
    apply_benchmark_manifest, git_commit, parse_args, stage_execution_snapshot,
)
from integrations.host.scenario_pack import discover
from integrations.host.test_benchmark_manifest import write_benchmark


class CohortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        pack = self.root / "pack"
        pack.mkdir()
        self.benchmark = write_benchmark(pack)
        self.binary = self.root / "development-claux"
        self.binary.write_text('binary-v1\n')
        self.binary.chmod(0o755)
        self.host = self.root / "host"
        self.host.mkdir()
        for name in cohorts.CONTROLLER_FILES:
            shutil.copy2(cohorts.SCRIPT_DIR / name, self.host / name)
        self.profile = {
            "args": ["--benchmark", str(self.benchmark), "--models", "test/seed",
                     "--agent-provider", "test-provider", "--claux-binary", str(self.binary)],
            "environment": {"REPLAYBOOK_CLAUX_PROVIDER_ROUTES": '{"*":"openrouter"}'},
        }

    def freeze(self):
        return cohorts.freeze_cohort("test-core", self.profile, self.root / "cohorts", host_dir=self.host)

    def test_live_source_updates_do_not_change_frozen_execution(self):
        root = self.freeze()
        original = cohorts.load_cohort(root)
        self.binary.write_text('binary-v2\n')
        (self.host / "run-claux.sh").write_text('# changed adapter\n')
        (self.benchmark.parent / "database-incident/scenario.toml").write_text('[scenario]\nversion=99\n')
        self.assertEqual(cohorts.load_cohort(root)["identity"], original["identity"])
        self.assertEqual((root / "agent/claux").read_text(), 'binary-v1\n')
        runner, args, _env = cohorts.execution({"cohort": str(root), "args": ["--models", "test/new"], "environment": {}})
        command = [sys.executable, str(runner), *args, "--check"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1 scenarios, 3 attempts', result.stdout)

    def test_models_and_efforts_share_exact_harness_identity(self):
        root = self.freeze()
        snapshots = []
        for index, (model, effort) in enumerate((("test/first", "low"), ("test/second", "high"))):
            runner, argv, environment = cohorts.execution({
                "cohort": str(root), "args": ["--models", model, "--reasoning-efforts", effort], "environment": {},
            })
            args = parse_args(argv)
            manifest = apply_benchmark_manifest(args)
            packs, scenarios = discover(args.scenario_packs)
            matrix = self.root / f"matrix-{index}"
            matrix.mkdir()
            snapshot = stage_execution_snapshot(
                matrix, packs=packs, selected_scenarios=list(scenarios.values()),
                agent_adapter=None, agent_payload=None, agent_env_file=None,
                claux_binary=args.claux_binary, benchmark_manifest=manifest.path,
                host_dir=runner.parent,
                claux_provider_routes=environment["REPLAYBOOK_CLAUX_PROVIDER_ROUTES"],
            )
            snapshots.append(snapshot.metadata)
        self.assertEqual(snapshots[0], snapshots[1])

    def test_frozen_artifacts_and_transport_cannot_be_modified(self):
        root = self.freeze()
        with self.assertRaisesRegex(ValueError, "override frozen"):
            cohorts.execution({"cohort": str(root), "args": ["--models", "test/new"],
                               "environment": {"REPLAYBOOK_OPENAI_UPSTREAM": "https://changed.invalid"}})
        with self.assertRaisesRegex(ValueError, "may select"):
            cohorts.execution({"cohort": str(root), "args": ["--claux-binary", str(self.binary)], "environment": {}})
        (root / "agent/claux").write_text('tampered\n')
        with self.assertRaisesRegex(ValueError, "cohort changed"):
            cohorts.load_cohort(root)

    def test_settings_are_hashed_and_expected_identity_is_checked(self):
        root = self.freeze()
        with self.assertRaisesRegex(ValueError, "identity changed"):
            cohorts.load_cohort(root, "wrong")
        record = json.loads((root / "cohort.json").read_text())
        record["environment"]["REPLAYBOOK_CLAUX_PROVIDER_ROUTES"] = '{}'
        (root / "cohort.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "cohort changed"):
            cohorts.load_cohort(root)

    def test_freeze_rejects_model_specific_protocol_map_and_overwrite(self):
        self.profile["environment"]["REPLAYBOOK_CLAUX_PROVIDER_ROUTES"] = '{"test/seed":"openrouter"}'
        with self.assertRaisesRegex(ValueError, "protocol must be shared"):
            self.freeze()
        self.profile["environment"]["REPLAYBOOK_CLAUX_PROVIDER_ROUTES"] = '{"*":"openrouter"}'
        self.freeze()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.freeze()

    def test_queued_session_rejects_changed_cohort_before_running(self):
        root = self.freeze()
        config = self.root / "sessions.json"
        config.write_text(json.dumps({"schema_version": 1, "profiles": {"new": {
            "cohort": str(root), "args": ["--models", "test/new"], "environment": {},
        }}}))
        with patch.object(sessions, "launch_worker"):
            session = sessions.start_session("new", config, self.root / "jobs")
        (root / "agent/claux").write_text('tampered\n')
        with patch.object(sessions.subprocess, "run") as run:
            self.assertEqual(sessions.run_session(session), 1)
        run.assert_not_called()
        self.assertEqual(sessions.read_object(session / "status.json")["state"], "failed")

    def test_frozen_source_commit_remains_available_without_git_metadata(self):
        root = self.freeze()
        source = root / "controller/.replaybook-source.json"
        expected = json.loads(source.read_text())["commit"]
        self.assertEqual(git_commit(root / "controller"), expected)


if __name__ == "__main__":
    unittest.main()
