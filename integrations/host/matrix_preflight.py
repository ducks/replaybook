#!/usr/bin/env python3
"""Host capacity and dependency checks for Replaybook VM matrices."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    import resource
except ImportError:  # pragma: no cover - the host runner rejects non-Linux hosts
    resource = None  # type: ignore[assignment]


GIB = 1024**3
REQUIRED_COMMANDS = (
    "bash",
    "curl",
    "flock",
    "jq",
    "nix-shell",
    "python",
    "realpath",
    "scp",
    "ss",
    "ssh",
    "tar",
    "timeout",
)


class Probe(Protocol):
    platform: str

    def executable(self, name: str) -> str | None: ...

    def path_read_write(self, path: Path) -> bool: ...

    def path_readable(self, path: Path) -> bool: ...

    def free_bytes(self, path: Path) -> int | None: ...

    def device_id(self, path: Path) -> int | None: ...

    def memory_available_bytes(self) -> int | None: ...

    def cpu_count(self) -> int | None: ...

    def file_descriptor_limit(self) -> int | None: ...

    def ports_available(self, ports: list[int]) -> str | None: ...

    def active_vm_processes(self) -> int: ...


def existing_parent(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


class SystemProbe:
    platform = sys.platform

    def executable(self, name: str) -> str | None:
        return shutil.which(name)

    def path_read_write(self, path: Path) -> bool:
        if path.is_dir():
            try:
                with tempfile.TemporaryFile(dir=path):
                    pass
            except OSError:
                return False
            return True
        return os.access(path, os.R_OK | os.W_OK)

    def path_readable(self, path: Path) -> bool:
        return path.exists() and os.access(path, os.R_OK)

    def free_bytes(self, path: Path) -> int | None:
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def device_id(self, path: Path) -> int | None:
        try:
            return path.stat().st_dev
        except OSError:
            return None

    def memory_available_bytes(self) -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    def cpu_count(self) -> int | None:
        return os.cpu_count()

    def file_descriptor_limit(self) -> int | None:
        if resource is None:
            return None
        try:
            soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        except (OSError, ValueError):
            return None
        return int(soft)

    def ports_available(self, ports: list[int]) -> str | None:
        listeners: list[socket.socket] = []
        try:
            for port in ports:
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError as error:
                    listener.close()
                    return f"127.0.0.1:{port}: {error}"
                listeners.append(listener)
        finally:
            for listener in listeners:
                listener.close()
        return None

    def active_vm_processes(self) -> int:
        count = 0
        proc = Path("/proc")
        try:
            candidates = proc.iterdir()
        except OSError:
            return 0
        for candidate in candidates:
            if not candidate.name.isdigit():
                continue
            try:
                command = (candidate / "cmdline").read_bytes().replace(b"\0", b" ")
            except (OSError, PermissionError):
                continue
            if b"replaybook-host-eval" in command or (
                b"qemu-system-" in command and b"nix-vm" in command
            ):
                count += 1
        return count


def check(
    name: str,
    status: str,
    detail: str,
    remedy: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"name": name, "status": status, "detail": detail}
    if remedy:
        value["remedy"] = remedy
    return value


def inspect_host(
    *,
    ports: list[int],
    concurrency: int,
    output_parent: Path,
    work_parent: Path,
    ssh_key: Path,
    probe: Probe | None = None,
) -> dict[str, Any]:
    active_probe = probe or SystemProbe()
    checks: list[dict[str, Any]] = []

    if active_probe.platform.startswith("linux"):
        checks.append(check("operating_system", "pass", active_probe.platform))
    else:
        checks.append(
            check(
                "operating_system",
                "fail",
                active_probe.platform,
                "Run host-native NixOS VM benchmarks from Linux.",
            )
        )

    missing = [name for name in REQUIRED_COMMANDS if not active_probe.executable(name)]
    checks.append(
        check(
            "host_commands",
            "fail" if missing else "pass",
            "missing: " + ", ".join(missing) if missing else "all required commands found",
            "Install the missing host benchmark dependencies." if missing else None,
        )
    )

    missing_keys = [path for path in (ssh_key, Path(f"{ssh_key}.pub")) if not path.is_file()]
    checks.append(
        check(
            "ssh_keypair",
            "fail" if missing_keys else "pass",
            "missing: " + ", ".join(str(path) for path in missing_keys)
            if missing_keys
            else str(ssh_key),
            "Create an Ed25519 key pair or set REPLAYBOOK_HOST_SSH_KEY."
            if missing_keys
            else None,
        )
    )

    kvm = Path("/dev/kvm")
    kvm_ready = active_probe.path_read_write(kvm)
    checks.append(
        check(
            "kvm",
            "fail" if not kvm_ready else "pass",
            "/dev/kvm is readable and writable" if kvm_ready else "/dev/kvm is unavailable",
            "Enable hardware virtualization and grant this user access to the kvm group."
            if not kvm_ready
            else None,
        )
    )

    port_error = active_probe.ports_available(ports)
    checks.append(
        check(
            "ports",
            "fail" if port_error else "pass",
            port_error or f"{len(ports)} scheduled ports are available",
            "Stop the listener or select a different --base-port." if port_error else None,
        )
    )

    resolved_paths = {
        "nix_store": Path("/nix"),
        "vm_work": existing_parent(work_parent),
        "matrix_output": existing_parent(output_parent),
    }
    nix_path = resolved_paths["nix_store"]
    nix_readable = active_probe.path_readable(nix_path)
    checks.append(
        check(
            "nix_store_accessible",
            "fail" if not nix_readable else "pass",
            str(nix_path),
            "Install Nix and ensure its store is readable."
            if not nix_readable
            else None,
        )
    )
    for name in ("vm_work", "matrix_output"):
        path = resolved_paths[name]
        writable = active_probe.path_read_write(path)
        checks.append(
            check(
                f"{name}_writable",
                "fail" if not writable else "pass",
                str(path),
                f"Make {path} writable or select another location."
                if not writable
                else None,
            )
        )

    # Sparse VM disks still consume real blocks while scenarios run. Reserve a
    # host floor plus capacity for every concurrently active VM, and aggregate
    # requirements when Nix, VM work, and results share a filesystem.
    reservations = {
        "nix_store": 8 * GIB,
        "vm_work": (2 + 2 * concurrency) * GIB,
        "matrix_output": 2 * GIB,
    }
    device_reservations: dict[int, int] = {}
    device_paths: dict[int, list[str]] = {}
    unknown_devices = []
    for name, path in resolved_paths.items():
        device = active_probe.device_id(path)
        if device is None:
            unknown_devices.append(name)
            continue
        device_reservations[device] = device_reservations.get(device, 0) + reservations[name]
        device_paths.setdefault(device, []).append(name)
    if unknown_devices:
        checks.append(
            check(
                "disk_capacity",
                "fail",
                "could not identify filesystems for " + ", ".join(unknown_devices),
                "Ensure the configured paths exist on readable filesystems.",
            )
        )
    for device, required in device_reservations.items():
        names = device_paths[device]
        path = resolved_paths[names[0]]
        available = active_probe.free_bytes(path)
        if available is None:
            checks.append(
                check(
                    "disk_capacity",
                    "fail",
                    f"could not read free space for {', '.join(names)}",
                    "Ensure POSIX filesystem capacity information is available.",
                )
            )
            continue
        recommended = required + 8 * GIB
        status = "pass" if available >= recommended else "warn" if available >= required else "fail"
        checks.append(
            check(
                "disk_capacity",
                status,
                f"{available / GIB:.1f} GiB free; {required / GIB:.1f} GiB required "
                f"for {', '.join(names)} at concurrency {concurrency}",
                "Free disk space, reduce concurrency, or move REPLAYBOOK_HOST_TMPDIR."
                if status != "pass"
                else None,
            )
        )

    available_memory = active_probe.memory_available_bytes()
    required_memory = (2 * concurrency + 2) * GIB
    recommended_memory = required_memory + 2 * GIB
    if available_memory is None:
        checks.append(
            check(
                "memory_capacity",
                "fail",
                "could not read MemAvailable",
                "Run on Linux with procfs mounted.",
            )
        )
    else:
        memory_status = (
            "pass"
            if available_memory >= recommended_memory
            else "warn"
            if available_memory >= required_memory
            else "fail"
        )
        checks.append(
            check(
                "memory_capacity",
                memory_status,
                f"{available_memory / GIB:.1f} GiB available; "
                f"{required_memory / GIB:.1f} GiB required at concurrency {concurrency}",
                "Free memory or reduce concurrency." if memory_status != "pass" else None,
            )
        )

    cpus = active_probe.cpu_count()
    recommended_cpus = 2 * concurrency
    checks.append(
        check(
            "cpu_capacity",
            "warn" if cpus is None or cpus < recommended_cpus else "pass",
            f"{cpus if cpus is not None else 'unknown'} logical CPUs; "
            f"{recommended_cpus} recommended at concurrency {concurrency}",
            "Reduce concurrency to avoid host contention."
            if cpus is None or cpus < recommended_cpus
            else None,
        )
    )

    descriptor_limit = active_probe.file_descriptor_limit()
    required_descriptors = max(256 + 64 * concurrency, len(ports) + 64)
    checks.append(
        check(
            "file_descriptors",
            "fail"
            if descriptor_limit is None or descriptor_limit < required_descriptors
            else "pass",
            f"soft limit {descriptor_limit if descriptor_limit is not None else 'unknown'}; "
            f"{required_descriptors} required",
            "Raise the open-file limit or reduce concurrency."
            if descriptor_limit is None or descriptor_limit < required_descriptors
            else None,
        )
    )

    active_vms = active_probe.active_vm_processes()
    checks.append(
        check(
            "active_vm_processes",
            "warn" if active_vms else "pass",
            f"{active_vms} existing Replaybook VM process{'es' if active_vms != 1 else ''}",
            "Confirm another matrix is not still running before launching."
            if active_vms
            else None,
        )
    )

    counts = Counter(item["status"] for item in checks)
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "healthy": counts["fail"] == 0,
        "active_concurrency": concurrency,
        "ports": {"count": len(ports), "first": min(ports), "last": max(ports)},
        "checks": checks,
        "summary": {
            "passed": counts["pass"],
            "warnings": counts["warn"],
            "failed": counts["fail"],
        },
    }


def print_report(report: dict[str, Any]) -> None:
    print("[matrix] host preflight")
    for item in report["checks"]:
        marker = item["status"].upper()
        print(f"  [{marker}] {item['name']}: {item['detail']}")
    summary = report["summary"]
    print(
        f"[matrix] preflight: {summary['passed']} passed, "
        f"{summary['warnings']} warnings, {summary['failed']} failed"
    )
