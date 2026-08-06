#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$script_dir/run-host-native.sh"
bash -n "$script_dir/run-claux.sh"
bash -n "$script_dir/oracle.sh"
bash -n "$script_dir/classify-agent-exit.sh"

grep -q 'systemd.services.checkout-backend' "$script_dir/worker/nixos.nix"
grep -q 'systemd.services.incident-nginx' "$script_dir/worker/nixos.nix"
grep -q 'guest.port = 80' "$script_dir/worker/nixos.nix"
grep -q 'pid /run/incident-nginx/nginx.pid' "$script_dir/worker/nixos.nix"
grep -q 'programs.nix-ld.enable = true' "$script_dir/worker/nixos.nix"

if grep -qE 'docker\.enable|docker\.sock|docker-compose' "$script_dir/worker/nixos.nix"; then
  echo "host-native worker unexpectedly enables Docker" >&2
  exit 1
fi

if grep -q 'harbor run' "$script_dir/run-host-native.sh"; then
  echo "host-native controller unexpectedly nests Harbor in the incident VM" >&2
  exit 1
fi

grep -q 'root@127.0.0.1:/root/replaybook-eval/claux' "$script_dir/run-host-native.sh"
if grep -q '/usr/local/bin/claux' \
  "$script_dir/run-host-native.sh" "$script_dir/run-claux.sh"; then
  echo "host-native runner assumes /usr/local/bin exists" >&2
  exit 1
fi

grep -q 'usage_candidate=' "$script_dir/run-host-native.sh"
grep -q 'native_tool_filesystem_policy = "unrestricted"' "$script_dir/run-claux.sh"
grep -q 'bash_filesystem_policy = "unrestricted"' "$script_dir/run-claux.sh"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/instruction.md"

console_log="$(mktemp)"
trap 'rm -f -- "$console_log"' EXIT
printf '%s\n' '[   63.187500] reboot: Restarting system' >"$console_log"
[[ "$("$script_dir/classify-agent-exit.sh" 255 "$console_log")" == "agent_rebooted_host" ]]
[[ -z "$("$script_dir/classify-agent-exit.sh" 1 "$console_log")" ]]
printf '%s\n' 'Connection to host closed.' >"$console_log"
[[ -z "$("$script_dir/classify-agent-exit.sh" 255 "$console_log")" ]]

output="$($script_dir/run-host-native.sh --help)"
grep -q 'host-native infrastructure evaluation' <<<"$output"

set +e
output="$($script_dir/run-host-native.sh --ssh-port 22600 --http-port 22600 --oracle 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -q 'SSH and HTTP ports must differ' <<<"$output"
