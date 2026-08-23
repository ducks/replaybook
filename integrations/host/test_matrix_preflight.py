from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from integrations.host.matrix_preflight import GIB, REQUIRED_COMMANDS, inspect_host


class FakeProbe:
    platform = "linux"

    def __init__(self) -> None:
        self.missing_commands: set[str] = set()
        self.writable = True
        self.available = 64 * GIB
        self.memory = 16 * GIB
        self.cpus = 8
        self.descriptors = 1024
        self.port_error: str | None = None
        self.active_vms = 0

    def executable(self, name: str) -> str | None:
        return None if name in self.missing_commands else f"/bin/{name}"

    def path_read_write(self, path: Path) -> bool:
        return self.writable

    def path_readable(self, path: Path) -> bool:
        return self.writable

    def free_bytes(self, path: Path) -> int | None:
        return self.available

    def device_id(self, path: Path) -> int | None:
        return 1

    def memory_available_bytes(self) -> int | None:
        return self.memory

    def cpu_count(self) -> int | None:
        return self.cpus

    def file_descriptor_limit(self) -> int | None:
        return self.descriptors

    def ports_available(self, ports: list[int]) -> str | None:
        return self.port_error

    def active_vm_processes(self) -> int:
        return self.active_vms


class MatrixPreflightTests(unittest.TestCase):
    def inspect(self, probe: FakeProbe) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "id_ed25519"
            key.write_text("private")
            Path(f"{key}.pub").write_text("public")
            return inspect_host(
                ports=[26000, 26001, 26002, 26003],
                concurrency=2,
                output_parent=root,
                work_parent=root,
                ssh_key=key,
                probe=probe,
            )

    def test_healthy_host_passes_and_aggregates_shared_disk_reservations(self) -> None:
        report = self.inspect(FakeProbe())

        self.assertTrue(report["healthy"])
        self.assertEqual(report["summary"], {"passed": 13, "warnings": 0, "failed": 0})
        disk = next(item for item in report["checks"] if item["name"] == "disk_capacity")
        self.assertIn("16.0 GiB required", disk["detail"])
        self.assertEqual(report["ports"], {"count": 4, "first": 26000, "last": 26003})

    def test_capacity_dependencies_and_ports_block_launch(self) -> None:
        probe = FakeProbe()
        probe.missing_commands = {REQUIRED_COMMANDS[0]}
        probe.writable = False
        probe.available = 10 * GIB
        probe.memory = 5 * GIB
        probe.cpus = 2
        probe.descriptors = 128
        probe.port_error = "127.0.0.1:26002: address in use"
        probe.active_vms = 2

        report = self.inspect(probe)
        statuses = {item["name"]: item["status"] for item in report["checks"]}

        self.assertFalse(report["healthy"])
        self.assertEqual(statuses["host_commands"], "fail")
        self.assertEqual(statuses["kvm"], "fail")
        self.assertEqual(statuses["ports"], "fail")
        self.assertEqual(statuses["disk_capacity"], "fail")
        self.assertEqual(statuses["memory_capacity"], "fail")
        self.assertEqual(statuses["cpu_capacity"], "warn")
        self.assertEqual(statuses["file_descriptors"], "fail")
        self.assertEqual(statuses["active_vm_processes"], "warn")


if __name__ == "__main__":
    unittest.main()
