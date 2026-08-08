#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$script_dir/run-host-native.sh"
bash -n "$script_dir/run-claux.sh"
bash -n "$script_dir/oracle.sh"
bash -n "$script_dir/classify-agent-exit.sh"
bash -n "$script_dir/ssh-probe.sh"
find "$script_dir/scenarios" -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
ruby -c "$script_dir/scenarios/013-sidekiq-wrong-redis/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/013-sidekiq-wrong-redis/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/014-missing-rails-migration/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/014-missing-rails-migration/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/015-sidekiq-poison-pill/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/015-sidekiq-poison-pill/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/016-rails-pool-exhaustion/app/app.ru" >/dev/null

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
grep -q 'SCENARIO_VERSION="2"' "$script_dir/scenarios/014-missing-rails-migration/scenario.conf"
grep -q 'SCENARIO_VERSION="1"' "$script_dir/scenarios/015-sidekiq-poison-pill/scenario.conf"
grep -q 'exit 22' "$script_dir/scenarios/015-sidekiq-poison-pill/verify.sh"
grep -q 'failure_category="poison_not_quarantined"' "$script_dir/run-host-native.sh"
grep -q 'SCENARIO_VERSION="1"' "$script_dir/scenarios/016-rails-pool-exhaustion/scenario.conf"
grep -q 'exit 23' "$script_dir/scenarios/016-rails-pool-exhaustion/verify.sh"
grep -q 'failure_category="database_pool_exhausted"' "$script_dir/run-host-native.sh"
grep -q '202608070001_add_delivery_state.sql' "$script_dir/scenarios/014-missing-rails-migration/oracle.sh"
grep -q 'deployment/migration' "$script_dir/scenarios/014-missing-rails-migration/verify.sh"
if grep -q 'ADD COLUMN IF NOT EXISTS' \
  "$script_dir/scenarios/014-missing-rails-migration/app/db/migrate/202608070001_add_delivery_state.sql"; then
  echo "migration scenario unexpectedly permits untracked manual schema repair" >&2
  exit 1
fi
grep -q 'scenario_version: $scenario_version' "$script_dir/run-host-native.sh"
grep -q 'HOST_HARNESS_VERSION=2' "$script_dir/run-host-native.sh"
grep -q 'harness_version: $harness_version' "$script_dir/run-host-native.sh"

oracle_copy_count="$(grep -c '\$ORACLE.*replaybook-eval/oracle\.sh' "$script_dir/run-host-native.sh")"
[[ "$oracle_copy_count" -eq 1 ]]
oracle_branch="$({
  sed -n '/if \[\[ "$RUN_ORACLE" == true \]\]; then/,/^else$/p' \
    "$script_dir/run-host-native.sh"
} || true)"
grep -q '\$ORACLE.*replaybook-eval/oracle\.sh' <<<"$oracle_branch"
grep -q 'test ! -e /root/replaybook-eval/oracle.sh' "$script_dir/run-host-native.sh"

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
grep -q 'capture_agent_results || true' "$script_dir/run-host-native.sh"
grep -q 'failure_category="host_reboot_failed"' "$script_dir/run-host-native.sh"
grep -q 'local deadline="\$((SECONDS + timeout_seconds))"' "$script_dir/run-host-native.sh"
[[ "$(grep -c 'ssh-probe.sh.*"\${SSH\[@\]}"' "$script_dir/run-host-native.sh")" -eq 3 ]]
grep -q 'failure_category="services_failed_after_reboot"' "$script_dir/run-host-native.sh"
grep -q 'failure_category="agent_timeout"' "$script_dir/run-host-native.sh"
grep -q 'agent_timeout_seconds: $agent_timeout_seconds' "$script_dir/run-host-native.sh"
grep -q 'v20260808.0.0' "$script_dir/run-host-native.sh"
grep -q 'v20260808.0.0' "$script_dir/run_host_matrix.py"
if grep -q 'timeout --foreground' "$script_dir/run-host-native.sh"; then
  echo "agent timeout unexpectedly leaves child processes outside its process group" >&2
  exit 1
fi
grep -q 'native_tool_filesystem_policy = "unrestricted"' "$script_dir/run-claux.sh"
grep -q 'bash_filesystem_policy = "unrestricted"' "$script_dir/run-claux.sh"
grep -q 'trap forward_termination TERM INT' "$script_dir/run-claux.sh"
grep -q 'result: null' "$script_dir/run-claux.sh"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/instruction.md"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/scenarios/013-sidekiq-wrong-redis/instruction.md"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/scenarios/014-missing-rails-migration/instruction.md"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/scenarios/015-sidekiq-poison-pill/instruction.md"
grep -q 'Do not reboot, shut down, or replace the host yourself' "$script_dir/scenarios/016-rails-pool-exhaustion/instruction.md"

console_log="$(mktemp)"
trap 'rm -f -- "$console_log"' EXIT
printf '%s\n' '[   63.187500] reboot: Restarting system' >"$console_log"
[[ "$("$script_dir/classify-agent-exit.sh" 255 "$console_log")" == "agent_rebooted_host" ]]
[[ -z "$("$script_dir/classify-agent-exit.sh" 1 "$console_log")" ]]
printf '%s\n' 'Connection to host closed.' >"$console_log"
[[ -z "$("$script_dir/classify-agent-exit.sh" 255 "$console_log")" ]]

probe_dir="$(mktemp -d)"
probe_server_pid=""
cleanup_probe() {
  [[ -z "$probe_server_pid" ]] || kill "$probe_server_pid" 2>/dev/null || true
  [[ -z "$probe_server_pid" ]] || wait "$probe_server_pid" 2>/dev/null || true
  rm -rf -- "$probe_dir"
}
trap cleanup_probe EXIT
python -c 'import socket,time; server=socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(); print(server.getsockname()[1], flush=True); connection,_=server.accept(); time.sleep(30)' \
  >"$probe_dir/port" &
probe_server_pid=$!
for _ in $(seq 1 50); do
  [[ -s "$probe_dir/port" ]] && break
  sleep 0.1
done
[[ -s "$probe_dir/port" ]]
probe_port="$(<"$probe_dir/port")"
probe_started="$SECONDS"
set +e
"$script_dir/ssh-probe.sh" 2 ssh \
  -p "$probe_port" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  root@127.0.0.1 true >/dev/null 2>&1
probe_status=$?
set -e
probe_elapsed="$((SECONDS - probe_started))"
[[ "$probe_status" -eq 124 ]]
(( probe_elapsed < 6 ))
cleanup_probe
trap 'rm -f -- "$console_log"' EXIT

output="$($script_dir/run-host-native.sh --help)"
grep -q 'host-native infrastructure evaluation' <<<"$output"

set +e
output="$($script_dir/run-host-native.sh --ssh-port 22600 --http-port 22600 --oracle 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -q 'SSH and HTTP ports must differ' <<<"$output"

set +e
output="$("$script_dir/run-host-native.sh" --agent-timeout-seconds 0 --oracle 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -q -- '--agent-timeout-seconds must be a positive integer' <<<"$output"

set +e
output="$("$script_dir/run-host-native.sh" --scenario missing --oracle 2>&1)"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -q 'unknown host-native scenario: missing' <<<"$output"

(
  smoke_root="$(mktemp -d)"
  trap 'rm -rf -- "$smoke_root"' EXIT
  eval_root="$smoke_root/eval"
  home="$smoke_root/home"
  mkdir -p "$eval_root/results" "$home"
  printf '%s\n' 'export OPENROUTER_API_KEY=test' >"$eval_root/runtime.env"
  printf '%s\n' 'test/model' >"$eval_root/model"
  printf '%s\n' 'keep investigating' >"$eval_root/instruction.md"
  cat >"$eval_root/claux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "config" ]]; then
  mkdir -p "$HOME/.config/claux"
  printf '%s\n' \
    'native_tool_filesystem_policy = "workspace_only"' \
    'bash_filesystem_policy = "auto"' \
    >"$HOME/.config/claux/config.toml"
  exit 0
fi
transcript=""
while (( $# > 0 )); do
  case "$1" in
    --transcript) transcript="$2"; shift 2 ;;
    *) shift ;;
  esac
done
on_term() {
  printf '%s\n' '{"schema_version":1,"model":"test/model","outcome":{"status":"error","message":"Interrupted by shutdown signal."},"usage":{"input_tokens":12,"output_tokens":3,"cache_read_tokens":4,"cache_creation_tokens":0,"cost_usd":0.001},"messages":[{"role":"user","content":"keep investigating"}],"tool_trace":[{"id":"call-1","name":"Bash","input":{"command":"sleep 30"},"output":"Interrupted by user.","is_error":true}]}' >"$transcript"
  exit 1
}
trap on_term TERM
sleep 30
EOF
  chmod 0755 "$eval_root/claux"

  set +e
  HOME="$home" REPLAYBOOK_EVAL_ROOT="$eval_root" \
    timeout --signal=TERM --kill-after=5s 1s bash "$script_dir/run-claux.sh" \
    >"$smoke_root/stdout" 2>"$smoke_root/stderr"
  status=$?
  set -e

  [[ "$status" -eq 124 ]]
  jq -e '.outcome.status == "error" and (.tool_trace | length) == 1' \
    "$eval_root/results/claux-transcript.json" >/dev/null
  jq -e '.result == null and .usage.input_tokens == 12' \
    "$eval_root/results/claux.json" >/dev/null
)
