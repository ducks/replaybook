#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run a host-native infrastructure evaluation on a disposable NixOS VM.

Usage:
  run-host-native.sh [options]

Options:
  --oracle            Run the reference repair instead of Claux.
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
VM_CONFIG="${SCRIPT_DIR}/worker/nixos.nix"
SSH_KEY="${REPLAYBOOK_HOST_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
WORK_PARENT="${REPLAYBOOK_HOST_TMPDIR:-/var/tmp}"
CLAUX_RELEASE="${REPLAYBOOK_HOST_CLAUX_RELEASE:-v20260804.0.0}"
CLAUX_BINARY="${REPLAYBOOK_HOST_CLAUX_BINARY:-}"
MODEL="deepseek/deepseek-v4-flash"
SSH_PORT=22600
HTTP_PORT=22601
OUTPUT_DIR=""
ORACLE=false

while (( $# > 0 )); do
  case "$1" in
    --oracle)
      ORACLE=true
      shift
      ;;
    --model)
      (( $# >= 2 )) || { echo "--model requires a value" >&2; exit 2; }
      MODEL="$2"
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
if [[ "$ORACLE" == false && -z "${OPENROUTER_API_KEY:-}" ]]; then
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

http_code() {
  curl --silent --output /dev/null --max-time 2 \
    --write-out '%{http_code}' "http://127.0.0.1:${HTTP_PORT}/health" || true
}

wait_for_http_code() {
  local expected="$1"
  local attempts="${2:-20}"
  local code
  for _ in $(seq 1 "$attempts"); do
    code="$(http_code)"
    if [[ "$code" == "$expected" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

verify_repaired() {
  wait_for_http_code 200 20 || return 1
  [[ "$(curl --silent --fail --max-time 2 "http://127.0.0.1:${HTTP_PORT}/health")" == "ok" ]]
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
if ! "${SSH[@]}" \
  "systemctl is-active checkout-backend.service >/dev/null && systemctl is-active incident-nginx.service >/dev/null"; then
  echo "incident services did not start" >&2
  "${SSH[@]}" \
    "systemctl --no-pager --full status checkout-backend.service incident-nginx.service; journalctl --no-pager -u checkout-backend.service -u incident-nginx.service -n 100" \
    >&2 || true
  exit 1
fi

if ! wait_for_http_code 502 20; then
  code="$(http_code)"
  echo "incident preflight failed: expected HTTP 502, got HTTP ${code}" >&2
  exit 1
fi
echo "[host] preflight confirmed HTTP 502"

"${SSH[@]}" "install -d -m 700 /root/replaybook-eval/results"
"${SCP[@]}" \
  "${SCRIPT_DIR}/instruction.md" \
  "${SCRIPT_DIR}/oracle.sh" \
  "${SCRIPT_DIR}/run-claux.sh" \
  root@127.0.0.1:/root/replaybook-eval/

agent="claux"
run_status=0
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
start_seconds="$(date +%s)"
if [[ "$ORACLE" == true ]]; then
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
immediate_passed=false
restart_passed=false
reboot_passed=false
if (( run_status != 0 )); then
  failure="agent exited with status ${run_status}"
elif ! verify_repaired; then
  failure="HTTP repair did not recover"
else
  immediate_passed=true
  echo "[host] immediate verification passed"
  if ! "${SSH[@]}" "systemctl restart checkout-backend.service incident-nginx.service"; then
    failure="service restart failed"
  elif ! verify_repaired; then
    failure="repair did not survive service restarts"
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
    elif ! "${SSH[@]}" \
      "systemctl is-active checkout-backend.service >/dev/null && systemctl is-active incident-nginx.service >/dev/null"; then
      failure="required systemd services are not active after reboot"
    elif ! verify_repaired; then
      failure="repair did not survive host reboot"
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
  --arg scenario "001-nginx-502-host" \
  --arg agent "$agent" \
  --arg model "$(if [[ "$ORACLE" == true ]]; then printf 'oracle'; else printf '%s' "$MODEL"; fi)" \
  --arg started_at "$started_at" \
  --arg finished_at "$finished_at" \
  --arg failure "$failure" \
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
