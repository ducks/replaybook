#!/usr/bin/env python3
"""Check whether a host has the basic capacity to run Replaybook VMs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path


GIB = 1024**3
DEFAULT_VM_MEMORY_MIB = 2048
DEFAULT_VM_CORES = 2


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, raw = line.split(":", 1)
        values[name] = int(raw.strip().split()[0]) * 1024
    return values


def vm_allocation(repo: Path) -> tuple[int, int, Path | None]:
    candidates = [
        repo / "integrations/host/scenarios/worker/base.nix",
        repo / "integrations/host/worker/base.nix",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        source = path.read_text()
        memory = re.search(r"\bmemorySize\s*=\s*(\d+)\s*;", source)
        cores = re.search(r"\bcores\s*=\s*(\d+)\s*;", source)
        return (
            int(memory.group(1)) if memory else DEFAULT_VM_MEMORY_MIB,
            int(cores.group(1)) if cores else DEFAULT_VM_CORES,
            path,
        )
    return DEFAULT_VM_MEMORY_MIB, DEFAULT_VM_CORES, None


def gib(value: int) -> float:
    return value / GIB


def inspect(repo: Path, reserve_gib: float, minimum_disk_gib: float) -> dict[str, object]:
    memory = meminfo()
    vm_memory_mib, vm_cores, allocation_source = vm_allocation(repo)
    available = memory.get("MemAvailable", memory.get("MemFree", 0))
    total = memory.get("MemTotal", available)
    vm_memory = vm_memory_mib * 1024**2
    current_memory_limit = available // vm_memory
    reserved_memory_limit = max(0, total - int(reserve_gib * GIB)) // vm_memory
    memory_limit = min(current_memory_limit, reserved_memory_limit)
    logical_cpus = os.cpu_count() or 1
    cpu_limit = max(1, logical_cpus // vm_cores)
    suggested = max(0, min(memory_limit, cpu_limit))
    disk = shutil.disk_usage(repo)
    commands = {
        command: shutil.which(command) is not None
        for command in ("nix", "ssh", "jq", "curl", "python")
    }
    kvm = Path("/dev/kvm")
    blockers: list[str] = []
    warnings: list[str] = []
    missing = [command for command, found in commands.items() if not found]
    if missing:
        blockers.append(f"missing commands: {', '.join(missing)}")
    if not kvm.exists():
        blockers.append("/dev/kvm is missing")
    elif not os.access(kvm, os.R_OK | os.W_OK):
        blockers.append("/dev/kvm is not readable and writable")
    if gib(disk.free) < minimum_disk_gib:
        blockers.append(
            f"only {gib(disk.free):.1f} GiB free on {repo}; "
            f"recommended minimum is {minimum_disk_gib:.1f} GiB"
        )
    if memory_limit < 1:
        blockers.append("available memory is below one VM plus the host reserve")
    swap_total = memory.get("SwapTotal", 0)
    swap_used = swap_total - memory.get("SwapFree", 0)
    if swap_total and swap_used > swap_total / 2:
        warnings.append("more than half of swap is occupied; check memory pressure")
    if suggested < 2:
        warnings.append("start with concurrency 1 and observe memory, load, and thermals")
    return {
        "repo": str(repo),
        "commands": commands,
        "kvm_available": kvm.exists() and os.access(kvm, os.R_OK | os.W_OK),
        "logical_cpus": logical_cpus,
        "memory_total_gib": round(gib(memory.get("MemTotal", 0)), 1),
        "memory_available_gib": round(gib(available), 1),
        "swap_used_gib": round(gib(swap_used), 1),
        "disk_free_gib": round(gib(disk.free), 1),
        "vm_memory_mib": vm_memory_mib,
        "vm_cores": vm_cores,
        "allocation_source": str(allocation_source) if allocation_source else None,
        "host_reserve_gib": reserve_gib,
        "memory_concurrency_limit": memory_limit,
        "cpu_concurrency_limit": cpu_limit,
        "suggested_starting_concurrency": suggested,
        "blockers": blockers,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--reserve-memory-gib", type=float, default=6.0)
    parser.add_argument("--minimum-free-disk-gib", type=float, default=40.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    report = inspect(repo, args.reserve_memory_gib, args.minimum_free_disk_gib)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Replaybook host: {repo}")
        print(
            f"CPU: {report['logical_cpus']} logical; VM allocation "
            f"{report['vm_cores']} cores"
        )
        print(
            f"RAM: {report['memory_available_gib']} GiB available of "
            f"{report['memory_total_gib']} GiB; VM allocation "
            f"{report['vm_memory_mib']} MiB"
        )
        print(
            f"Disk: {report['disk_free_gib']} GiB free; "
            f"swap used: {report['swap_used_gib']} GiB"
        )
        print(f"Suggested starting concurrency: {report['suggested_starting_concurrency']}")
        for blocker in report["blockers"]:
            print(f"BLOCKER: {blocker}")
        for warning in report["warnings"]:
            print(f"WARNING: {warning}")
    return 1 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
