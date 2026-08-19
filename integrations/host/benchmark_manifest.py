#!/usr/bin/env python3
"""Load executable Replaybook host benchmark manifests."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .scenario_pack import Scenario, ScenarioPack
except ImportError:
    from scenario_pack import Scenario, ScenarioPack


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
BENCHMARK_TIERS = {"smoke", "core", "full", "frontier"}


@dataclass(frozen=True)
class BenchmarkScenario:
    id: str
    version: int


@dataclass(frozen=True)
class BenchmarkManifest:
    path: Path
    id: str
    version: str
    status: str | None
    tier: str | None
    pack_path: Path
    pack_id: str
    pack_version: str
    scenarios: tuple[BenchmarkScenario, ...]
    attempts: int
    agent_timeout_seconds: int
    required_host_harness_version: int
    sha256: str

    def metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": self.id,
            "version": self.version,
            "status": self.status,
            "tier": self.tier,
            "sha256": self.sha256,
        }

    def validate_environment(
        self,
        packs: list[ScenarioPack],
        discovered: dict[str, Scenario],
        host_harness_version: int,
    ) -> None:
        if host_harness_version != self.required_host_harness_version:
            raise ValueError(
                f"benchmark requires host harness v{self.required_host_harness_version}, "
                f"found v{host_harness_version}"
            )
        if len(packs) != 1:
            raise ValueError("benchmark manifests must resolve exactly one scenario pack")
        pack = packs[0]
        if (pack.id, pack.version) != (self.pack_id, self.pack_version):
            raise ValueError(
                "benchmark pack identity mismatch: expected "
                f"{self.pack_id}@{self.pack_version}, found {pack.id}@{pack.version}"
            )
        for expected in self.scenarios:
            actual = discovered.get(expected.id)
            if actual is None:
                raise ValueError(f"benchmark scenario is missing: {expected.id}")
            if actual.version != expected.version:
                raise ValueError(
                    f"benchmark scenario {expected.id} requires v{expected.version}, "
                    f"found v{actual.version}"
                )


def table(document: dict[str, Any], key: str, source: Path) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{source}: missing [{key}] table")
    return value


def string_field(value: dict[str, Any], key: str, source: Path) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field.strip():
        raise ValueError(f"{source}: {key} must be a non-empty string")
    return field


def positive_int(value: dict[str, Any], key: str, source: Path) -> int:
    field = value.get(key)
    if not isinstance(field, int) or isinstance(field, bool) or field <= 0:
        raise ValueError(f"{source}: {key} must be a positive integer")
    return field


def load_benchmark_manifest(path: Path) -> BenchmarkManifest:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"benchmark manifest does not exist: {source}")
    try:
        raw = source.read_bytes()
        document = tomllib.loads(raw.decode())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"could not read benchmark manifest {source}: {error}") from error
    if document.get("schema_version") != 1:
        raise ValueError(f"{source}: unsupported benchmark schema version")

    benchmark = table(document, "benchmark", source)
    pack = table(document, "pack", source)
    verification = table(document, "verification", source)
    benchmark_id = string_field(benchmark, "id", source)
    pack_id = string_field(pack, "id", source)
    if not IDENTIFIER_PATTERN.fullmatch(benchmark_id):
        raise ValueError(f"{source}: benchmark.id contains unsafe characters")
    if not IDENTIFIER_PATTERN.fullmatch(pack_id):
        raise ValueError(f"{source}: pack.id contains unsafe characters")

    pack_path_value = string_field(pack, "path", source)
    pack_path = (source.parent / pack_path_value).resolve()
    if pack_path != source.parent and source.parent not in pack_path.parents:
        raise ValueError(f"{source}: pack.path must stay within the benchmark directory")
    scenarios_value = document.get("scenarios")
    if not isinstance(scenarios_value, list) or not scenarios_value:
        raise ValueError(f"{source}: [[scenarios]] must contain at least one scenario")
    scenarios = []
    scenario_ids = set()
    for index, item in enumerate(scenarios_value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{source}: scenarios entry {index} must be a table")
        scenario_id = string_field(item, "id", source)
        version = positive_int(item, "version", source)
        if not IDENTIFIER_PATTERN.fullmatch(scenario_id) or "/" in scenario_id:
            raise ValueError(f"{source}: scenario id contains unsafe characters")
        if scenario_id in scenario_ids:
            raise ValueError(f"{source}: duplicate benchmark scenario: {scenario_id}")
        scenario_ids.add(scenario_id)
        scenarios.append(BenchmarkScenario(scenario_id, version))

    required_checks = (
        "require_immediate_recovery",
        "require_service_restart",
        "require_host_reboot",
        "preserve_controller_state",
    )
    for key in required_checks:
        if verification.get(key) is not True:
            raise ValueError(f"{source}: verification.{key} must be true")

    status = benchmark.get("status")
    if status is not None and (not isinstance(status, str) or not status.strip()):
        raise ValueError(f"{source}: benchmark.status must be a non-empty string")
    tier = benchmark.get("tier")
    if tier is not None and tier not in BENCHMARK_TIERS:
        raise ValueError(
            f"{source}: benchmark.tier must be one of "
            + ", ".join(sorted(BENCHMARK_TIERS))
        )
    return BenchmarkManifest(
        path=source,
        id=benchmark_id,
        version=string_field(benchmark, "version", source),
        status=status,
        tier=tier,
        pack_path=pack_path,
        pack_id=pack_id,
        pack_version=string_field(pack, "version", source),
        scenarios=tuple(scenarios),
        attempts=positive_int(benchmark, "attempts", source),
        agent_timeout_seconds=positive_int(
            benchmark, "agent_timeout_seconds", source
        ),
        required_host_harness_version=positive_int(
            benchmark, "required_host_harness_version", source
        ),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
