from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from integrations.host.scenario_pack import discover


class ScenarioPackTests(unittest.TestCase):
    def make_pack(
        self,
        root: Path,
        directory: str,
        pack_id: str,
        scenario_id: str,
        scenario_version: int = 1,
    ) -> Path:
        pack = root / directory
        pack.mkdir()
        (pack / "replaybook-pack.toml").write_text(
            f'[pack]\nid = "{pack_id}"\nversion = "20260809.0.0"\n'
        )
        scenario = pack / scenario_id
        scenario.mkdir()
        (scenario / "scenario.toml").write_text(
            f"[scenario]\nversion = {scenario_version}\n"
        )
        return pack

    def test_discovers_and_combines_versioned_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_pack(root, "first", "example/first", "one", 1)
            second = self.make_pack(root, "second", "example/second", "two", 2)
            packs, scenarios = discover([first, second])

        self.assertEqual([pack.id for pack in packs], ["example/first", "example/second"])
        self.assertEqual(scenarios["one"].version, 1)
        self.assertEqual(scenarios["two"].version, 2)
        self.assertEqual(scenarios["two"].pack.id, "example/second")

    def test_rejects_duplicate_scenario_ids_across_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_pack(root, "first", "example/first", "same")
            second = self.make_pack(root, "second", "example/second", "same")
            with self.assertRaisesRegex(ValueError, "duplicate host-native scenario same"):
                discover([first, second])

    def test_requires_a_pack_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "missing replaybook-pack.toml"):
                discover([Path(temporary)])


if __name__ == "__main__":
    unittest.main()
