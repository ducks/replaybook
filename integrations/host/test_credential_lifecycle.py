from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from integrations.host.credential_lifecycle import scan, scrub


class CredentialLifecycleTests(unittest.TestCase):
    def test_scan_reports_counts_without_secret_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            jobs.mkdir()
            runtime = root / "runtime"
            runtime.mkdir()
            (jobs / "summary.json").write_text('{"key":"sk-or-v1-secret-value"}')
            (runtime / "replaybook-opencode-env.abc123").write_text("SECRET=hidden\n")
            report = scan(jobs, runtime)

        encoded = json.dumps(report)
        self.assertEqual(report["openrouter_key_file_count"], 1)
        self.assertEqual(report["disposable_env_file_count"], 1)
        self.assertNotIn("sk-or-v1-secret-value", encoded)
        self.assertNotIn("hidden", encoded)

    def test_scrub_requires_confirmation_for_env_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            jobs.mkdir()
            runtime = root / "runtime"
            runtime.mkdir()
            (jobs / "summary.json").write_text('{"key":"sk-or-v1-secret-value"}')
            (runtime / "replaybook-codex-env.abc123").write_text("SECRET=hidden\n")
            with self.assertRaises(SystemExit):
                scrub(jobs, runtime, remove_env_files=True, yes=False)
            result = scrub(jobs, runtime, remove_env_files=True, yes=True)
            self.assertEqual(result["redacted_openrouter_keys"], 1)
            self.assertEqual(result["removed_disposable_env_files"], 1)
            self.assertNotIn("sk-or-v1-secret-value", (jobs / "summary.json").read_text())
            self.assertFalse((runtime / "replaybook-codex-env.abc123").exists())


if __name__ == "__main__":
    unittest.main()
