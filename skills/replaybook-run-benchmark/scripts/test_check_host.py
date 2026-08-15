#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from check_host import DEFAULT_VM_CORES, DEFAULT_VM_MEMORY_MIB, vm_allocation


class VmAllocationTest(unittest.TestCase):
    def test_reads_worker_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = root / "integrations/host/scenarios/worker/base.nix"
            worker.parent.mkdir(parents=True)
            worker.write_text(
                "virtualisation = { cores = 6; memorySize = 8192; };\n"
            )

            memory, cores, source = vm_allocation(root)

            self.assertEqual(memory, 8192)
            self.assertEqual(cores, 6)
            self.assertEqual(source, worker)

    def test_uses_defaults_without_worker_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory, cores, source = vm_allocation(Path(temporary))

            self.assertEqual(memory, DEFAULT_VM_MEMORY_MIB)
            self.assertEqual(cores, DEFAULT_VM_CORES)
            self.assertIsNone(source)


if __name__ == "__main__":
    unittest.main()
