#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run a host-native infrastructure evaluation on a disposable NixOS VM.

Usage:
  run-host-native.sh [options]

Options:
  --oracle            Run the reference repair instead of Claux.
  --scenario ID       Host-native scenario (default: 001-nginx-502-host).
  --model MODEL       OpenRouter model for Claux.
  --ssh-port PORT     Forwarded SSH port (default: 22600).
  --http-port PORT    Forwarded HTTP port (default: 22601).
  --output-dir DIR    Result directory. It must not already exist.
  -h, --help          Show this help.

Environment:
  OPENROUTER_API_KEY             Required unless --oracle is used.
  REPLAYBOOK_HOST_SSH_KEY        SSH key (default: ~/.ssh/id_ed25519).
  REPLAYBOOK_HOST_TMPDIR         Temporary file parent (default: /var/tmp).
  REPLAYBOOK_HOST_CLAUX_BINARY   Existing Claux binary to copy into the VM.
  REPLAYBOOK_HOST_CLAUX_RELEASE  Release tag to download (default: v20260804.0.0).
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SSH_KEY="${REPLAYBOOK_HOST_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
WORK_PARENT="${REPLAYBOOK_HOST_TMPDIR:-/var/tmp}"
CLAUX_RELEASE="${REPLAYBOOK_HOST_CLAUX_RELEASE:-v20260804.0.0}"
CLAUX_BINARY="${REPLAYBOOK_HOST_CLAUX_BINARY:-}"
MODEL="deepseek/deepseek-v4-flash"
SCENARIO_ID="001-nginx-502-host"
SSH_PORT=22600
HTTP_PORT=22601
OUTPUT_DIR=""
RUN_ORACLE=false

while (( $# > 0 )); do
  case "$1" in
    --oracle)
      RUN_ORACLE=true
      shift
      ;;
    --model)
      (( $# >= 2 )) || { echo "--model requires a value" >&2; exit 2; }
      MODEL="$2"
      shift 2
      ;;
    --scenario)
      (( $# >= 2 )) || { echo "--scenario requires a value" >&2; exit 2; }
      SCENARIO_ID="$2"
      shift 2
      ;;
    --ssh-port)
      (( $# >= 2 )) || { echo "--ssh-port requires a value" >&2; exit 2; }
      SSH_PORT="$2"
      shift 2
      ;;
    --http-port)
      (( $# >= 2 )) || { echo "--http-port requires a value" >&2; exit 2; }
      HTTP_PORT="$2"
      shift 2
      ;;
    --output-dir)
      (( $# >= 2 )) || { echo "--output-dir requires a value" >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

[[ "$SCENARIO_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
  echo "scenario ID contains unsafe characters: ${SCENARIO_ID}" >&2
  exit 2
}
SCENARIO_DIR="${SCRIPT_DIR}/scenarios/${SCENARIO_ID}"
SCENARIO_MANIFEST="${SCENARIO_DIR}/scenario.conf"
[[ -f "$SCENARIO_MANIFEST" ]] || {
  echo "unknown host-native scenario: ${SCENARIO_ID}" >&2
  exit 2
}
# shellcheck source=/dev/null
source "$SCENARIO_MANIFEST"
for scenario_field in NIXOS_CONFIG INSTRUCTION ORACLE PREFLIGHT VERIFY REQUIRED_SERVICES RESTART_SERVICES; do
  [[ -n "${!scenario_field:-}" ]] || {
    echo "scenario ${SCENARIO_ID} is missing ${scenario_field}" >&2
    exit 2
  }
  if [[ "$scenario_field" != "REQUIRED_SERVICES" && "$scenario_field" != "RESTART_SERVICES" ]]; then
    scenario_path="${SCENARIO_DIR}/${!scenario_field}"
    [[ -f "$scenario_path" ]] || {
      echo "scenario ${SCENARIO_ID} file does not exist: ${scenario_path}" >&2
      exit 2
    }
    printf -v "$scenario_field" '%s' "$scenario_path"
  fi
done
service_list_pattern='^[a-zA-Z0-9@_. -]+$'
[[ "$REQUIRED_SERVICES" =~ $service_list_pattern ]] || {
  echo "scenario ${SCENARIO_ID} has unsafe required service names" >&2
  exit 2
}
[[ "$RESTART_SERVICES" =~ $service_list_pattern ]] || {
  echo "scenario ${SCENARIO_ID} has unsafe restart service names" >&2
  exit 2
}
VM_CONFIG="$NIXOS_CONFIG"

for port_name in SSH_PORT HTTP_PORT; do
  port="${!port_name}"
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port <= 0 || port > 65535 )); then
    echo "${port_name} must be an integer from 1 to 65535" >&2
    exit 2
  fi
done
[[ "$SSH_PORT" != "$HTTP_PORT" ]] || {
  echo "SSH and HTTP ports must differ" >&2
  exit 2
}

for port in "$SSH_PORT" "$HTTP_PORT"; do
  if ss -ltn "sport = :${port}" | tail -n +2 | grep -q .; then
    echo "host port is already in use: ${port}" >&2
    exit 1
  fi
done

[[ -f "$SSH_KEY" && -f "${SSH_KEY}.pub" ]] || {
  echo "missing SSH key pair: ${SSH_KEY}" >&2
  exit 1
}
[[ -d "$WORK_PARENT" ]] || {
  echo "temporary directory does not exist: ${WORK_PARENT}" >&2
  exit 1
}
if [[ "$RUN_ORACLE" == false && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is required unless --oracle is used" >&2
  exit 1
fi
if [[ -n "$CLAUX_BINARY" && ! -f "$CLAUX_BINARY" ]]; then
  echo "Claux binary does not exist: ${CLAUX_BINARY}" >&2
  exit 1
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  mkdir -p "${REPO_DIR}/jobs"
  OUTPUT_DIR="$(mktemp -d "${REPO_DIR}/jobs/host-native-$(date -u +%Y-%m-%d__%H-%M-%S).XXXXXX")"
else
  [[ "$OUTPUT_DIR" == /* ]] || OUTPUT_DIR="${REPO_DIR}/${OUTPUT_DIR}"
  [[ ! -e "$OUTPUT_DIR" ]] || {
    echo "output directory already exists: ${OUTPUT_DIR}" >&2
    exit 1
  }
  mkdir -p "$OUTPUT_DIR"
fi
SCENARIO_STATE_DIR="${OUTPUT_DIR}/scenario-state"
mkdir -m 0700 "$SCENARIO_STATE_DIR"

WORK_DIR="$(mktemp -d "${WORK_PARENT%/}/replaybook-host-eval.XXXXXX")"
VM_PID=""

cleanup() {
  if [[ -n "$VM_PID" ]] && kill -0 "$VM_PID" 2>/dev/null; then
    kill "$VM_PID" 2>/dev/null || true
    wait "$VM_PID" 2>/dev/null || true
  fi
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

SSH=(
  ssh
  -i "$SSH_KEY"
  -p "$SSH_PORT"
  -o BatchMode=yes
  -o ConnectTimeout=2
  -o LogLevel=ERROR
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  root@127.0.0.1
)
SCP=(
  scp
  -q
  -i "$SSH_KEY"
  -P "$SSH_PORT"
  -o BatchMode=yes
  -o ConnectTimeout=2
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)

wait_for_ssh() {
  local attempts="${1:-120}"
  for _ in $(seq 1 "$attempts"); do
    if [[ -n "$VM_PID" ]] && ! kill -0 "$VM_PID" 2>/dev/null; then
      return 1
    fi
    if "${SSH[@]}" true >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_ssh_down() {
  for _ in $(seq 1 30); do
    if ! "${SSH[@]}" true >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_services() {
  local attempts="${1:-60}"
  for _ in $(seq 1 "$attempts"); do
    if "${SSH[@]}" \
      "for service in $REQUIRED_SERVICES; do systemctl is-active \"\$service\" >/dev/null || exit 1; done" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

verify_repaired() {
  local phase="$1"
  "$VERIFY" "http://127.0.0.1:${HTTP_PORT}" "$phase" "$SCENARIO_STATE_DIR"
}

run_verification() {
  local phase="$1"
  local status=0

  verify_repaired "$phase" || status=$?
  if (( status == 20 )); then
    failure_category="backlog_not_recovered"
  fi
  return "$status"
}

echo "[host] building disposable NixOS incident host"
export REPLAYBOOK_HOST_PUBLIC_KEY_FILE="${SSH_KEY}.pub"
export REPLAYBOOK_HOST_SSH_PORT="$SSH_PORT"
export REPLAYBOOK_HOST_HTTP_PORT="$HTTP_PORT"
nix-shell -p nixos-generators --run \
  "nixos-generate -f vm-nogui -c '${VM_CONFIG}' -o '${WORK_DIR}/vm'"

VM_RUNNER="$(find -L "${WORK_DIR}/vm/bin" -maxdepth 1 -type f -name 'run-*-vm' -print -quit)"
[[ -n "$VM_RUNNER" ]] || {
  echo "generated VM runner was not found" >&2
  exit 1
}

echo "[host] booting VM on SSH ${SSH_PORT}, HTTP ${HTTP_PORT}"
NIX_DISK_IMAGE="${WORK_DIR}/disk.qcow2" \
  "$VM_RUNNER" >"${OUTPUT_DIR}/console.log" 2>&1 &
VM_PID=$!

wait_for_ssh || {
  echo "incident VM did not become reachable" >&2
  tail -100 "${OUTPUT_DIR}/console.log" >&2
  exit 1
}
if ! wait_for_services; then
  echo "incident services did not start" >&2
  "${SSH[@]}" \
    "systemctl --no-pager --full status $REQUIRED_SERVICES; journalctl --no-pager -n 100 $(
      for service in $REQUIRED_SERVICES; do printf -- '-u %q ' "$service"; done
    )" \
    >&2 || true
  exit 1
fi

if ! "$PREFLIGHT" "http://127.0.0.1:${HTTP_PORT}" "$SCENARIO_STATE_DIR"; then
  echo "incident preflight failed for ${SCENARIO_ID}" >&2
  exit 1
fi
echo "[host] preflight confirmed ${SCENARIO_ID}"

"${SSH[@]}" "install -d -m 700 /root/replaybook-eval/results"
"${SCP[@]}" "$INSTRUCTION" root@127.0.0.1:/root/replaybook-eval/instruction.md
"${SCP[@]}" "$ORACLE" root@127.0.0.1:/root/replaybook-eval/oracle.sh
"${SCP[@]}" "${SCRIPT_DIR}/run-claux.sh" root@127.0.0.1:/root/replaybook-eval/

agent="claux"
run_status=0
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
start_seconds="$(date +%s)"
if [[ "$RUN_ORACLE" == true ]]; then
  agent="oracle"
  echo "[host] running reference repair"
  "${SSH[@]}" "bash /root/replaybook-eval/oracle.sh" || run_status=$?
else
  if [[ -z "$CLAUX_BINARY" ]]; then
    CLAUX_BINARY="${WORK_DIR}/claux"
    echo "[host] downloading Claux ${CLAUX_RELEASE}"
    curl --fail --location --silent --show-error \
      "https://github.com/ducks/claux/releases/download/${CLAUX_RELEASE}/claux-linux-x86_64" \
      --output "$CLAUX_BINARY"
  fi
  "${SCP[@]}" "$CLAUX_BINARY" root@127.0.0.1:/root/replaybook-eval/claux
  "${SSH[@]}" "chmod 0755 /root/replaybook-eval/claux"
  printf '%s\n' "$MODEL" | "${SSH[@]}" "umask 077; cat > /root/replaybook-eval/model"
  printf 'export OPENROUTER_API_KEY=%q\n' "$OPENROUTER_API_KEY" \
    | "${SSH[@]}" "umask 077; cat > /root/replaybook-eval/runtime.env"
  echo "[host] running Claux directly on the incident host"
  "${SSH[@]}" "bash /root/replaybook-eval/run-claux.sh" || run_status=$?
fi
agent_seconds="$(( $(date +%s) - start_seconds ))"

reward=0
failure=""
failure_category=""
immediate_passed=false
restart_passed=false
reboot_passed=false
if (( run_status == 255 )); then
  for _ in $(seq 1 10); do
    failure_category="$(
      "$SCRIPT_DIR/classify-agent-exit.sh" "$run_status" "$OUTPUT_DIR/console.log"
    )"
    [[ -n "$failure_category" ]] && break
    sleep 1
  done
  if [[ "$failure_category" == "agent_rebooted_host" ]]; then
    echo "[host] detected an agent-initiated host reboot" >&2
    wait_for_ssh 120 || true
  fi
fi

if (( run_status != 0 )); then
  if [[ "$failure_category" == "agent_rebooted_host" ]]; then
    failure="agent rebooted the host during its session"
  else
    failure="agent exited with status $run_status"
  fi
elif ! run_verification immediate; then
  if [[ "$failure_category" == "backlog_not_recovered" ]]; then
    failure="repair did not recover the pre-existing backlog"
  else
    failure="HTTP repair did not recover"
  fi
else
  immediate_passed=true
  echo "[host] immediate verification passed"
  if ! "${SSH[@]}" "systemctl restart $RESTART_SERVICES"; then
    failure="service restart failed"
  elif ! run_verification service_restart; then
    if [[ "$failure_category" == "backlog_not_recovered" ]]; then
      failure="pre-existing backlog recovery did not survive service restarts"
    else
      failure="repair did not survive service restarts"
    fi
  else
    restart_passed=true
    echo "[host] service restart verification passed"
    set +e
    "${SSH[@]}" "systemctl reboot" >/dev/null 2>&1
    set -e
    if ! wait_for_ssh_down; then
      failure="VM did not shut down for reboot"
    elif ! wait_for_ssh 120; then
      failure="VM did not return after reboot"
    elif ! wait_for_services; then
      failure="required systemd services are not active after reboot"
    elif ! run_verification host_reboot; then
      if [[ "$failure_category" == "backlog_not_recovered" ]]; then
        failure="pre-existing backlog recovery did not survive host reboot"
      else
        failure="repair did not survive host reboot"
      fi
    else
      reboot_passed=true
      echo "[host] host reboot verification passed"
      reward=1
    fi
  fi
fi

"${SSH[@]}" "tar -C /root/replaybook-eval -czf - results" \
  | tar -xzf - -C "$OUTPUT_DIR" || true

usage='null'
if [[ -f "${OUTPUT_DIR}/results/claux.json" ]]; then
  usage_candidate="$(
    jq -c 'if type == "object" then (.usage // null) else null end' \
      "${OUTPUT_DIR}/results/claux.json" 2>/dev/null || true
  )"
  if [[ -n "$usage_candidate" ]] && jq -e . <<<"$usage_candidate" >/dev/null 2>&1; then
    usage="$usage_candidate"
  fi
fi
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -n \
  --arg suite "replaybook-host-v1" \
  --arg scenario "$SCENARIO_ID" \
  --arg agent "$agent" \
  --arg model "$(if [[ "$RUN_ORACLE" == true ]]; then printf 'oracle'; else printf '%s' "$MODEL"; fi)" \
  --arg started_at "$started_at" \
  --arg finished_at "$finished_at" \
  --arg failure "$failure" \
  --arg failure_category "$failure_category" \
  --argjson reward "$reward" \
  --argjson agent_seconds "$agent_seconds" \
  --argjson immediate_passed "$immediate_passed" \
  --argjson restart_passed "$restart_passed" \
  --argjson reboot_passed "$reboot_passed" \
  --argjson usage "$usage" \
  '{
    schema_version: 1,
    suite: $suite,
    scenario: $scenario,
    agent: $agent,
    model: $model,
    started_at: $started_at,
    finished_at: $finished_at,
    agent_duration_seconds: $agent_seconds,
    reward: $reward,
    failure: (if $failure == "" then null else $failure end),
    failure_category: (if $failure_category == "" then null else $failure_category end),
    usage: $usage,
    verification: {
      immediate_http: $immediate_passed,
      service_restart: $restart_passed,
      host_reboot: $reboot_passed
    }
  }' >"${OUTPUT_DIR}/result.json"

echo "[host] results: ${OUTPUT_DIR}"
if (( reward != 1 )); then
  echo "[host] failed: ${failure}" >&2
  exit 1
fi
echo "[host] host-native evaluation passed"
