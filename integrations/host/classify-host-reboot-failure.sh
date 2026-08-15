#!/usr/bin/env bash
set -euo pipefail

console_log="${1:?usage: classify-host-reboot-failure.sh CONSOLE_LOG}"
[[ -f "$console_log" ]] || exit 0

# The VM's dedicated Nix store image is supplied by the host harness. If that
# device disappears during a verifier-controlled reboot, the guest cannot boot
# far enough to evaluate the agent's repair. Keep this signature deliberately
# narrow so an agent-induced service or boot failure remains an evaluated miss.
if grep -aEq \
  'Timed out waiting for device .*/dev/disk/by-label/nix-store|Dependency failed for .*/nix/\.ro-store' \
  "$console_log"; then
  printf '%s\n' "guest_boot_infrastructure_failed"
fi
