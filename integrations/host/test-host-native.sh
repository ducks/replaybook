#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$script_dir/run-host-native.sh"
bash -n "$script_dir/run-claux.sh"
bash -n "$script_dir/oracle.sh"

grep -q 'systemd.services.checkout-backend' "$script_dir/worker/nixos.nix"
grep -q 'systemd.services.incident-nginx' "$script_dir/worker/nixos.nix"
grep -q 'guest.port = 80' "$script_dir/worker/nixos.nix"
grep -q 'pid /run/incident-nginx/nginx.pid' "$script_dir/worker/nixos.nix"

if grep -qE 'docker\.enable|docker\.sock|docker-compose' "$script_dir/worker/nixos.nix"; then
  echo "host-native worker unexpectedly enables Docker" >&2
  exit 1
fi

if grep -q 'harbor run' "$script_dir/run-host-native.sh"; then
  echo "host-native controller unexpectedly nests Harbor in the incident VM" >&2
  exit 1
fi

output="$($script_dir/run-host-native.sh --help)"
grep -q 'host-native infrastructure evaluation' <<<"$output"

set +e
output="$($script_dir/run-host-native.sh --ssh-port 22600 --http-port 22600 --oracle 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -q 'SSH and HTTP ports must differ' <<<"$output"
