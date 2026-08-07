#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$script_dir/run-host-native.sh"
bash -n "$script_dir/run-claux.sh"
bash -n "$script_dir/oracle.sh"
bash -n "$script_dir/classify-agent-exit.sh"
find "$script_dir/scenarios" -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
ruby -c "$script_dir/scenarios/013-sidekiq-wrong-redis/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/013-sidekiq-wrong-redis/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/014-missing-rails-migration/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/014-missing-rails-migration/app/server.rb" >/dev/null

grep -q 'systemd.services.checkout-backend' "$script_dir/worker/nixos.nix"
grep -q 'systemd.services.incident-nginx' "$script_dir/worker/nixos.nix"
grep -q 'guest.port = 80' "$script_dir/worker/base.nix"
grep -q 'pid /run/incident-nginx/nginx.pid' "$script_dir/worker/nixos.nix"
grep -q 'programs.nix-ld.enable = true' "$script_dir/worker/base.nix"
grep -q 'systemd.services.checkout-sidekiq' "$script_dir/scenarios/013-sidekiq-wrong-redis/nixos.nix"
grep -q 'redis://127.0.0.1:6379/1' "$script_dir/scenarios/013-sidekiq-wrong-redis/nixos.nix"
grep -q 'redis://127.0.0.1:6379/0' "$script_dir/scenarios/013-sidekiq-wrong-redis/oracle.sh"
grep -q 'backlog-job-ids' "$script_dir/scenarios/013-sidekiq-wrong-redis/preflight.sh"
grep -q 'checkout web service did not become ready for preflight' "$script_dir/scenarios/013-sidekiq-wrong-redis/preflight.sh"
grep -q 'exit 20' "$script_dir/scenarios/013-sidekiq-wrong-redis/verify.sh"
grep -q 'failure_category="backlog_not_recovered"' "$script_dir/run-host-native.sh"
grep -q 'failure_category="migration_not_applied"' "$script_dir/run-host-native.sh"
grep -q 'SCENARIO_VERSION="1"' "$script_dir/scenarios/001-nginx-502-host/scenario.conf"
grep -q 'SCENARIO_VERSION="2"' "$script_dir/scenarios/013-sidekiq-wrong-redis/scenario.conf"
grep -q 'SCENARIO_VERSION="1"' "$script_dir/scenarios/014-missing-rails-migration/scenario.conf"
grep -q '202608070001_add_delivery_state.sql' "$script_dir/scenarios/014-missing-rails-migration/oracle.sh"
grep -q 'deployment/migration' "$script_dir/scenarios/014-missing-rails-migration/verify.sh"
grep -q 'scenario_version: $scenario_version' "$script_dir/run-host-native.sh"

if grep -qER 'docker\.enable|docker\.sock|docker-compose' "$script_dir/worker" "$script_dir/scenarios"; then
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
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/scenarios/013-sidekiq-wrong-redis/instruction.md"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/scenarios/014-missing-rails-migration/instruction.md"

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

set +e
output="$("$script_dir/run-host-native.sh" --scenario missing --oracle 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -q 'unknown host-native scenario: missing' <<<"$output"
