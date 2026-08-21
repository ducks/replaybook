from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from integrations.host.benchmark_manifest import load_benchmark_manifest
from integrations.host.run_host_matrix import (
    HOST_HARNESS_VERSION,
    apply_benchmark_manifest,
    main,
    parse_args,
)
from integrations.host.scenario_pack import discover


def write_benchmark(root: Path, *, scenario_version: int = 2) -> Path:
    (root / "replaybook-pack.toml").write_text(
        '[pack]\nid = "test/infra"\nversion = "20260810.0.0"\n'
    )
    scenario = root / "database-incident"
    scenario.mkdir()
    (scenario / "scenario.toml").write_text(
        f"[scenario]\nversion = {scenario_version}\n"
    )
    manifest = root / "benchmark.toml"
    manifest.write_text(
        f'''schema_version = 1

[benchmark]
id = "test-infra"
version = "20260810.0.0"
status = "preview"
tier = "core"
attempts = 3
agent_timeout_seconds = 600
required_host_harness_version = {HOST_HARNESS_VERSION}

[pack]
path = "."
id = "test/infra"
version = "20260810.0.0"

[verification]
require_immediate_recovery = true
require_service_restart = true
require_host_reboot = true
preserve_controller_state = true

[[scenarios]]
id = "database-incident"
version = 2
'''
    )
    return manifest


class BenchmarkManifestTests(unittest.TestCase):
    def test_loads_and_validates_a_pinned_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_benchmark(root)
            manifest = load_benchmark_manifest(path)
            packs, scenarios = discover([root])
            manifest.validate_environment(packs, scenarios, HOST_HARNESS_VERSION)

        self.assertEqual(manifest.id, "test-infra")
        self.assertEqual(manifest.tier, "core")
        self.assertEqual(manifest.metadata()["tier"], "core")
        self.assertEqual(manifest.attempts, 3)
        self.assertEqual(manifest.agent_timeout_seconds, 600)
        self.assertEqual(manifest.scenarios[0].id, "database-incident")
        self.assertEqual(len(manifest.sha256), 64)

    def test_rejects_scenario_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = load_benchmark_manifest(
                write_benchmark(root, scenario_version=3)
            )
            packs, scenarios = discover([root])
            with self.assertRaisesRegex(ValueError, "requires v2, found v3"):
                manifest.validate_environment(
                    packs, scenarios, HOST_HARNESS_VERSION
                )

    def test_rejects_unknown_benchmark_tier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            path.write_text(path.read_text().replace('tier = "core"', 'tier = "huge"'))
            with self.assertRaisesRegex(ValueError, "benchmark.tier"):
                load_benchmark_manifest(path)

    def test_benchmark_supplies_matrix_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            args = parse_args(["--benchmark", str(path), "--models", "test/model"])
            manifest = apply_benchmark_manifest(args)

        self.assertIsNotNone(manifest)
        self.assertEqual(args.scenarios, ["database-incident"])
        self.assertEqual(args.attempts, 3)
        self.assertEqual(args.agent_timeout_seconds, 600)

    def test_oracle_uses_one_attempt_per_manifest_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            args = parse_args(["--benchmark", str(path), "--oracle"])
            apply_benchmark_manifest(args)

        self.assertEqual(args.attempts, 1)

    def test_rejects_benchmark_dimension_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            args = parse_args(
                ["--benchmark", str(path), "--models", "test/model", "--attempts", "1"]
            )
            with self.assertRaisesRegex(ValueError, "remove --attempts"):
                apply_benchmark_manifest(args)

    def test_benchmark_allows_declared_scenario_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            args = parse_args(
                [
                    "--benchmark",
                    str(path),
                    "--scenario",
                    "database-incident",
                    "--models",
                    "test/model",
                ]
            )
            apply_benchmark_manifest(args)

        self.assertEqual(args.scenarios, ["database-incident"])
        self.assertEqual(args.attempts, 3)

    def test_benchmark_rejects_undeclared_scenario_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            args = parse_args(
                [
                    "--benchmark",
                    str(path),
                    "--scenario",
                    "not-in-benchmark",
                    "--models",
                    "test/model",
                ]
            )
            with self.assertRaisesRegex(ValueError, "not declared"):
                apply_benchmark_manifest(args)

    def test_check_validates_without_model_credentials_or_vms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_benchmark(Path(temporary))
            status = main(["--benchmark", str(path), "--check"])

        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
