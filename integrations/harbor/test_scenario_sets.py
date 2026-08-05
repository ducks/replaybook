"""Tests for stable Replaybook evaluation scenario sets."""

import json
import unittest
from pathlib import Path


INTEGRATION_DIR = Path(__file__).parent
SETS_PATH = INTEGRATION_DIR / "scenario-sets.json"
TASKS_DIR = INTEGRATION_DIR / "tasks"


class ScenarioSetTests(unittest.TestCase):
    def setUp(self):
        self.sets = json.loads(SETS_PATH.read_text())
        self.available = {
            path.name for path in TASKS_DIR.iterdir() if (path / "task.toml").is_file()
        }

    def test_manifest_schema_is_supported(self):
        self.assertEqual(self.sets["schema_version"], 1)

    def test_existing_sets_reference_every_available_scenario(self):
        core_and_hard = set(self.sets["core"]) | set(self.sets["hard"])

        self.assertEqual(core_and_hard, self.available)
        self.assertFalse(set(self.sets["core"]) & set(self.sets["hard"]))

    def test_development_and_heldout_are_a_strict_partition(self):
        development = set(self.sets["development"])
        heldout = set(self.sets["heldout"])

        self.assertFalse(development & heldout)
        self.assertEqual(development | heldout, self.available)
        self.assertEqual(len(development), 6)
        self.assertEqual(len(heldout), 6)

    def test_sets_contain_no_duplicates(self):
        for name in ("core", "hard", "development", "heldout"):
            with self.subTest(name=name):
                values = self.sets[name]
                self.assertEqual(len(values), len(set(values)))


if __name__ == "__main__":
    unittest.main()
