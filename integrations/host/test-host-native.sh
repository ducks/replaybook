#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$script_dir/run-host-native.sh"
bash -n "$script_dir/run-agent-adapter.sh"
bash -n "$script_dir/run-claux.sh"
python -m py_compile "$script_dir/openrouter_proxy.py"
python -m py_compile "$script_dir/guest_leak_audit.py"
bash -n "$script_dir/adapters/codex.sh"
bash -n "$script_dir/find-codex-binary.sh"
bash -n "$script_dir/prepare-codex-env.sh"
bash -n "$script_dir/oracle.sh"
bash -n "$script_dir/classify-agent-exit.sh"
bash -n "$script_dir/classify-agent-outcome.sh"
bash -n "$script_dir/ssh-probe.sh"
find "$script_dir/scenarios" -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
ruby -c "$script_dir/scenarios/013-sidekiq-wrong-redis/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/013-sidekiq-wrong-redis/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/014-missing-rails-migration/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/014-missing-rails-migration/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/015-sidekiq-poison-pill/app/jobs.rb" >/dev/null
ruby -c "$script_dir/scenarios/015-sidekiq-poison-pill/app/server.rb" >/dev/null
ruby -c "$script_dir/scenarios/016-rails-pool-exhaustion/app/app.ru" >/dev/null

grep -q 'systemd.services.checkout-backend' "$script_dir/scenarios/worker/nixos.nix"
grep -q 'systemd.services.incident-nginx' "$script_dir/scenarios/worker/nixos.nix"
grep -q 'guest.port = 80' "$script_dir/scenarios/worker/base.nix"
grep -q 'pid /run/incident-nginx/nginx.pid' "$script_dir/scenarios/worker/nixos.nix"
grep -q 'programs.nix-ld.enable = true' "$script_dir/scenarios/worker/base.nix"
grep -q 'systemd.services.checkout-sidekiq' "$script_dir/scenarios/013-sidekiq-wrong-redis/nixos.nix"
grep -q 'redis://127.0.0.1:6379/1' "$script_dir/scenarios/013-sidekiq-wrong-redis/nixos.nix"
grep -q 'redis://127.0.0.1:6379/0' "$script_dir/scenarios/013-sidekiq-wrong-redis/oracle.sh"
grep -q 'failure_category="backlog_not_recovered"' "$script_dir/run-host-native.sh"
grep -q 'failure_category="migration_not_applied"' "$script_dir/run-host-native.sh"
grep -q 'version = 1' "$script_dir/scenarios/001-nginx-502-host/scenario.toml"
grep -q 'version = 2' "$script_dir/scenarios/013-sidekiq-wrong-redis/scenario.toml"
grep -q 'failure_category = "backlog_not_recovered"' "$script_dir/scenarios/013-sidekiq-wrong-redis/scenario.toml"
grep -q 'version = 2' "$script_dir/scenarios/014-missing-rails-migration/scenario.toml"
grep -q 'failure_category = "migration_not_applied"' "$script_dir/scenarios/014-missing-rails-migration/scenario.toml"
grep -q 'failure_category = "backlog_not_recovered"' "$script_dir/scenarios/014-missing-rails-migration/scenario.toml"
grep -q 'version = 1' "$script_dir/scenarios/015-sidekiq-poison-pill/scenario.toml"
grep -q 'failure_category = "poison_not_quarantined"' "$script_dir/scenarios/015-sidekiq-poison-pill/scenario.toml"
grep -q 'failure_category="poison_not_quarantined"' "$script_dir/run-host-native.sh"
grep -q 'version = 1' "$script_dir/scenarios/016-rails-pool-exhaustion/scenario.toml"
grep -q 'failure_category = "database_pool_exhausted"' "$script_dir/scenarios/016-rails-pool-exhaustion/scenario.toml"
grep -q 'failure_category" == "database_pool_exhausted"' "$script_dir/run-host-native.sh"
grep -q '202608070001_add_delivery_state.sql' "$script_dir/scenarios/014-missing-rails-migration/oracle.sh"
grep -q 'path = "/deployment/migration"' "$script_dir/scenarios/014-missing-rails-migration/scenario.toml"
if grep -q 'ADD COLUMN IF NOT EXISTS' \
  "$script_dir/scenarios/014-missing-rails-migration/app/db/migrate/202608070001_add_delivery_state.sql"; then
  echo "migration scenario unexpectedly permits untracked manual schema repair" >&2
  exit 1
fi
grep -q 'scenario_version: $scenario_version' "$script_dir/run-host-native.sh"
grep -q 'HOST_HARNESS_VERSION=17' "$script_dir/run-host-native.sh"
grep -q 'export TMPDIR="${WORK_DIR}/tmp"' "$script_dir/run-host-native.sh"
grep -q 'USE_TMPDIR=1' "$script_dir/run-host-native.sh"
grep -q 'HOST_HARNESS_VERSION = 17' "$script_dir/run_host_matrix.py"
grep -q 'virtualisation.useNixStoreImage = true' "$script_dir/isolated-vm.nix"
grep -q 'virtualisation.mountHostNixStore = false' "$script_dir/isolated-vm.nix"
grep -q 'incident VM unexpectedly exposes the host Nix store' "$script_dir/run-host-native.sh"
grep -q 'audit_guest_image' "$script_dir/run-host-native.sh"
pack_description="$(
  python "$script_dir/scenario_pack.py" \
    --pack "$script_dir/scenarios" \
    --resolve 001-nginx-502-host
)"
[[ "$(jq -r '.pack.id' <<<"$pack_description")" == "ducks/replaybook-host-scenarios" ]]
[[ "$(jq -r '.version' <<<"$pack_description")" == "1" ]]
grep -q 'DECLARATIVE_SCENARIO=true' "$script_dir/run-host-native.sh"
grep -q 'harness_version: $harness_version' "$script_dir/run-host-native.sh"

oracle_copy_count="$(grep -c '\$ORACLE.*replaybook-eval/oracle\.sh' "$script_dir/run-host-native.sh")"
[[ "$oracle_copy_count" -eq 1 ]]
oracle_branch="$({
  sed -n '/if \[\[ "$RUN_ORACLE" == true \]\]; then/,/^else$/p' \
    "$script_dir/run-host-native.sh"
} || true)"
grep -q '\$ORACLE.*replaybook-eval/oracle\.sh' <<<"$oracle_branch"
grep -q 'test ! -e /root/replaybook-eval/oracle.sh' "$script_dir/run-host-native.sh"

if grep -qER 'docker\.enable|docker\.sock|docker-compose' "$script_dir/scenarios"; then
  echo "host-native worker unexpectedly enables Docker" >&2
  exit 1
fi

if grep -q 'harbor run' "$script_dir/run-host-native.sh"; then
  echo "host-native controller unexpectedly nests Harbor in the incident VM" >&2
  exit 1
fi

grep -q 'root@127.0.0.1:/root/replaybook-eval/adapter' "$script_dir/run-host-native.sh"
grep -q 'REPLAYBOOK_RESULT_FILE=' "$script_dir/run-agent-adapter.sh"
grep -q 'REPLAYBOOK_TRANSCRIPT_FILE=' "$script_dir/run-agent-adapter.sh"
grep -q 'REPLAYBOOK_AGENT_PAYLOAD=' "$script_dir/run-agent-adapter.sh"
grep -q '/run/current-system/sw/bin/claux' "$script_dir/run-claux.sh"
grep -q 'REPLAYBOOK_HOST_CLAUX_BINARY' "$script_dir/isolated-vm.nix"
grep -q 'environment.systemPackages' "$script_dir/isolated-vm.nix"
grep -q 'flock.*claux_lock_fd' "$script_dir/run-host-native.sh"
grep -q -- '--retry-all-errors' "$script_dir/run-host-native.sh"
grep -q 'REPLAYBOOK_HOST_VM_READY_TIMEOUT:-300' "$script_dir/run-host-native.sh"
grep -q 'REPLAYBOOK_HOST_PROXY_READY_TIMEOUT:-30' "$script_dir/run-host-native.sh"
grep -q 'proxy_deadline=' "$script_dir/run-host-native.sh"
grep -q 'tunnel_deadline=' "$script_dir/run-host-native.sh"
grep -q 'rm -f -- "$runtime_env"' "$script_dir/run-agent-adapter.sh"
grep -q 'OPENROUTER_API_KEY=replaybook-proxy' "$script_dir/run-host-native.sh"
grep -q 'REPLAYBOOK_OPENAI_BASE_URL=http://127.0.0.1:19091/api/v1' \
  "$script_dir/run-host-native.sh"
if grep -q "printf .*OPENROUTER_API_KEY.*\$OPENROUTER_API_KEY" \
  "$script_dir/run-host-native.sh"; then
  echo "host-native runner writes the real OpenRouter key into the VM" >&2
  exit 1
fi
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
grep -q 'verifying host state after agent timeout' "$script_dir/run-host-native.sh"
grep -q 'You have a hard limit of.*seconds for this task' "$script_dir/run-host-native.sh"
grep -q 'after_agent_timeout:' "$script_dir/run-host-native.sh"
[[ "$("$script_dir/classify-agent-run-exit.sh" 124 1 900)" == "agent_timeout" ]]
[[ "$("$script_dir/classify-agent-run-exit.sh" 137 900 900)" == "agent_timeout" ]]
[[ "$("$script_dir/classify-agent-run-exit.sh" 255 934 900)" == "agent_timeout" ]]
[[ -z "$("$script_dir/classify-agent-run-exit.sh" 255 899 900)" ]]
[[ -z "$("$script_dir/classify-agent-run-exit.sh" 1 934 900)" ]]
grep -q 'read -r trial_status agent_outcome_category' "$script_dir/run-host-native.sh"
grep -q 'agent_timeout_seconds: $agent_timeout_seconds' "$script_dir/run-host-native.sh"
grep -q 'v20260810.0.1' "$script_dir/run-host-native.sh"
grep -q 'v20260810.0.1' "$script_dir/run_host_matrix.py"

agent_error="$(mktemp)"
trap 'rm -f -- "$agent_error"' EXIT
printf '%s\n' '{"outcome":{"status":"error","message":"openrouter API error (429 Too Many Requests): Provider returned error"}}' >"$agent_error"
[[ "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" == $'unavailable\tprovider_unavailable' ]]
printf '%s\n' '{"outcome":{"status":"error","message":"API error: response reached its output token limit"},"recording":{"model_rounds":[{"status":"completed"}],"tools":[]}}' >"$agent_error"
[[ "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" == $'evaluated\tagent_output_limit' ]]
printf '%s\n' '{"outcome":{"status":"error","message":"openrouter API error (502 Bad Gateway): upstream request failed"},"recording":{"model_rounds":[{"status":"completed"}],"tools":[{"name":"Bash"}]}}' >"$agent_error"
[[ "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" == $'evaluated\tprovider_interrupted' ]]
printf '%s\n' '{"outcome":{"status":"error","message":"authentication failed: invalid API key"}}' >"$agent_error"
[[ "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" == $'unavailable\tauthentication_failed' ]]
printf '%s\n' '{"outcome":{"status":"error","message":"adapter exited unexpectedly"}}' >"$agent_error"
[[ "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" == $'unavailable\tagent_runtime_error' ]]
printf '%s\n' '{"outcome":{"status":"error","message":"adapter exited unexpectedly"},"recording":{"model_rounds":[],"tools":[{"name":"Bash"}]}}' >"$agent_error"
[[ "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" == $'evaluated\tagent_runtime_error' ]]
printf '%s\n' '{"outcome":{"status":"cancelled","message":"interrupted"}}' >"$agent_error"
[[ -z "$("$script_dir/classify-agent-outcome.sh" "$agent_error")" ]]
rm -f -- "$agent_error"
if grep -q 'timeout --foreground' "$script_dir/run-host-native.sh"; then
  echo "agent timeout unexpectedly leaves child processes outside its process group" >&2
  exit 1
fi
grep -q 'native_tool_filesystem_policy = "unrestricted"' "$script_dir/run-claux.sh"
grep -q 'bash_filesystem_policy = "unrestricted"' "$script_dir/run-claux.sh"
grep -q 'trap forward_termination TERM INT' "$script_dir/run-claux.sh"
grep -q 'result: null' "$script_dir/run-claux.sh"
grep -q 'dangerously-bypass-approvals-and-sandbox' "$script_dir/adapters/codex.sh"
grep -q 'ignore-user-config' "$script_dir/adapters/codex.sh"
grep -q 'cached_input_tokens' "$script_dir/adapters/codex.sh"
grep -q 'volta.*which codex' "$script_dir/find-codex-binary.sh"
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
  cat >"$eval_root/payload" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "config" ]]; then
  mkdir -p "$HOME/.config/claux"
  printf '%s\n' \
    'native_tool_filesystem_policy = "workspace_only"' \
    'bash_filesystem_policy = "auto"' \
    'default_profile = "test-profile"' \
    '[model_profiles.test-profile]' \
    'provider = "openrouter"' \
    'model = "test/model"' \
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
  printf '%s\n' '{"schema_version":2,"model":"test/model","outcome":{"status":"error","message":"Interrupted by shutdown signal."},"usage":{"input_tokens":12,"output_tokens":3,"cache_read_tokens":4,"cache_creation_tokens":0,"cost_usd":0.001},"messages":[{"role":"user","content":"keep investigating"}],"tool_trace":[{"id":"call-1","name":"Bash","input":{"command":"sleep 30"},"output":"Interrupted by user.","is_error":true,"read_only":false,"started_after_ms":400,"duration_ms":600}],"timing":{"total_duration_ms":1000,"model_rounds":[{"index":1,"started_after_ms":0,"duration_ms":300,"status":"completed","usage":{"input_tokens":12,"output_tokens":3,"cache_read_tokens":4,"cache_creation_tokens":0,"cost_usd":0.001}}]}}' >"$transcript"
  exit 1
}
trap on_term TERM
sleep 30
EOF
  chmod 0755 "$eval_root/payload"

  set +e
  HOME="$home" REPLAYBOOK_EVAL_ROOT="$eval_root" \
    REPLAYBOOK_MODEL="test/model" \
    REPLAYBOOK_REASONING_EFFORT="low" \
    REPLAYBOOK_INSTRUCTION_FILE="$eval_root/instruction.md" \
    REPLAYBOOK_AGENT_PAYLOAD="$eval_root/payload" \
    REPLAYBOOK_RESULT_FILE="$eval_root/results/agent.json" \
    REPLAYBOOK_TRANSCRIPT_FILE="$eval_root/results/transcript.json" \
    timeout --signal=TERM --kill-after=5s 1s bash "$script_dir/run-claux.sh" \
    >"$smoke_root/stdout" 2>"$smoke_root/stderr"
  status=$?
  set -e

  [[ "$status" -eq 124 ]]
  jq -e '.outcome.status == "error" and (.tool_trace | length) == 1' \
    "$eval_root/results/transcript.json" >/dev/null
  grep -q 'reasoning_effort = "low"' "$home/.config/claux/config.toml"
  jq -e '.harness == "claux" and .model == "test/model" and .reasoning_effort == "low" and .result == null and .usage.input_tokens == 12 and .recording.transcript_schema_version == 2 and .recording.total_duration_ms == 1000 and .recording.model_rounds[0].duration_ms == 300 and .recording.tools[0].duration_ms == 600 and (.recording.tools[0] | has("input") | not) and (.recording.tools[0] | has("output") | not)' \
    "$eval_root/results/agent.json" >/dev/null
)

(
  smoke_root="$(mktemp -d)"
  trap 'rm -rf -- "$smoke_root"' EXIT
  eval_root="$smoke_root/eval"
  mkdir -p "$eval_root/results" "$smoke_root/workspace"
  printf '%s\n' 'repair the deployed service' >"$eval_root/instruction.md"
  cat >"$eval_root/payload" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "exec" ]]
shift
jq -e '.tokens.refresh_token == "replaybook-disabled"' "$CODEX_HOME/auth.json" >/dev/null
output=""
model=""
workspace=""
while (( $# > 0 )); do
  case "$1" in
    --model) model="$2"; shift 2 ;;
    --cd) workspace="$2"; shift 2 ;;
    --output-last-message) output="$2"; shift 2 ;;
    -) shift ;;
    *) shift ;;
  esac
done
[[ "$model" == "openai/test-model" ]]
[[ -d "$workspace" ]]
[[ "$(cat)" == "repair the deployed service" ]]
printf '%s\n' '{"type":"thread.started","thread_id":"test"}'
printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":120,"cached_input_tokens":80,"output_tokens":15,"reasoning_output_tokens":4}}'
printf '%s\n' 'repair complete' >"$output"
EOF
  chmod 0755 "$eval_root/payload"
  codex_auth_b64="$(
    printf '%s\n' '{"tokens":{"access_token":"test","refresh_token":"replaybook-disabled"}}' \
      | base64 --wrap=0
  )"

  REPLAYBOOK_EVAL_ROOT="$eval_root" \
    REPLAYBOOK_AGENT_PAYLOAD="$eval_root/payload" \
    CODEX_AUTH_JSON_B64="$codex_auth_b64" \
    REPLAYBOOK_INSTRUCTION_FILE="$eval_root/instruction.md" \
    REPLAYBOOK_MODEL="openai/test-model" \
    REPLAYBOOK_RESULT_FILE="$eval_root/results/agent.json" \
    REPLAYBOOK_TRANSCRIPT_FILE="$eval_root/results/transcript.json" \
    REPLAYBOOK_WORKSPACE="$smoke_root/workspace" \
    bash "$script_dir/adapters/codex.sh" >"$smoke_root/stdout"

  jq -e '
    .harness == "codex" and
    .model == "openai/test-model" and
    .result == "repair complete\n" and
    .usage.input_tokens == 120 and
    .usage.cache_read_tokens == 80
  ' "$eval_root/results/agent.json" >/dev/null
  jq -e 'length == 2 and .[1].type == "turn.completed"' \
    "$eval_root/results/transcript.json" >/dev/null
)

(
  smoke_root="$(mktemp -d)"
  trap 'rm -rf -- "$smoke_root"' EXIT
  mkdir -p "$smoke_root/codex-home"
  printf '%s\n' \
    '{"auth_mode":"chatgpt","tokens":{"id_token":"id","access_token":"access","refresh_token":"local-refresh"}}' \
    >"$smoke_root/codex-home/auth.json"
  CODEX_HOME="$smoke_root/codex-home" \
    "$script_dir/prepare-codex-env.sh" "$smoke_root/codex.env" \
    >"$smoke_root/output"
  [[ "$(< "$smoke_root/output")" == "$smoke_root/codex.env" ]]
  [[ "$(stat -c '%a' "$smoke_root/codex.env")" == "600" ]]
  # shellcheck source=/dev/null
  source "$smoke_root/codex.env"
  printf '%s' "$CODEX_AUTH_JSON_B64" | base64 --decode \
    | jq -e '
        .tokens.access_token == "access" and
        .tokens.refresh_token == "replaybook-disabled"
      ' >/dev/null
  ! grep -q 'local-refresh' "$smoke_root/codex.env"
)

(
  smoke_root="$(mktemp -d)"
  trap 'rm -rf -- "$smoke_root"' EXIT
  eval_root="$smoke_root/eval"
  mkdir -p "$eval_root/results"
  printf '%s\n' 'repair the service' >"$eval_root/instruction.md"
  printf '%s\n' 'vendor/model' >"$eval_root/model"
  printf '%s\n' 'export CUSTOM_SECRET=present' >"$eval_root/runtime.env"
  printf '%s\n' 'payload-data' >"$eval_root/payload"
  cat >"$eval_root/adapter" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$CUSTOM_SECRET" == "present" ]]
[[ ! -e "$REPLAYBOOK_EVAL_ROOT/runtime.env" ]]
[[ "$(< "$REPLAYBOOK_INSTRUCTION_FILE")" == "repair the service" ]]
[[ "$(< "$REPLAYBOOK_AGENT_PAYLOAD")" == "payload-data" ]]
jq -n \
  --arg model "$REPLAYBOOK_MODEL" \
  '{schema_version: 1, harness: "custom-agent", model: $model, result: "ok", usage: null}' \
  >"$REPLAYBOOK_RESULT_FILE"
printf '%s\n' '{"events":[]}' >"$REPLAYBOOK_TRANSCRIPT_FILE"
EOF
  chmod 0755 "$eval_root/adapter"

  REPLAYBOOK_EVAL_ROOT="$eval_root" REPLAYBOOK_WORKSPACE="$smoke_root" \
    bash "$script_dir/run-agent-adapter.sh"
  jq -e '.harness == "custom-agent" and .model == "vendor/model"' \
    "$eval_root/results/agent.json" >/dev/null
  jq -e '.events == []' "$eval_root/results/transcript.json" >/dev/null
)
