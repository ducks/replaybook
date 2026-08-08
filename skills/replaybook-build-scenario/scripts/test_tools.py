#!/usr/bin/env python3
"""Tests for the Replaybook scenario-builder skill tools."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
SCAFFOLD = SCRIPTS / "scaffold_scenario.py"
VALIDATE = SCRIPTS / "validate_scenario.py"


class ScenarioToolsTest(unittest.TestCase):
    def scaffold(self, root: Path) -> Path:
        result = subprocess.run(
            [sys.executable, str(SCAFFOLD), "017-test-incident", "--repo", str(root)],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())

    def test_scaffolded_scenario_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = self.scaffold(Path(temporary))
            self.assertTrue((scenario / "oracle.sh").stat().st_mode & 0o100)
            result = subprocess.run(
                [sys.executable, str(VALIDATE), str(scenario)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_scaffold_refuses_to_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.scaffold(root)
            result = subprocess.run(
                [sys.executable, str(SCAFFOLD), "017-test-incident", "--repo", str(root)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("refusing to overwrite", result.stderr)

    def test_validator_rejects_unreplayed_controller_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = self.scaffold(Path(temporary))
            manifest = scenario / "scenario.toml"
            manifest.write_text(
                manifest.read_text().replace(
                    'failure_category = "incident_not_reproduced"',
                    'record_failed_as = "lost_jobs"\n'
                    'failure_category = "incident_not_reproduced"',
                    1,
                )
            )
            result = subprocess.run(
                [sys.executable, str(VALIDATE), str(scenario)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("verify never replays: lost_jobs", result.stderr)


if __name__ == "__main__":
    unittest.main()
