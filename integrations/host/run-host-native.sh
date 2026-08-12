#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run a host-native infrastructure evaluation on a disposable NixOS VM.

Usage:
  run-host-native.sh [options]

Options:
  --oracle            Run the reference repair instead of an agent harness.
  --scenario ID       Host-native scenario (default: 001-nginx-502-host).
  --scenario-pack DIR Select a versioned scenario pack. Repeat to combine packs.
                      Defaults to the pack bundled with Replaybook.
  --model MODEL       Model identifier passed to the agent adapter.
  --reasoning-effort EFFORT
                      Claux reasoning effort for this trial.
  --agent-adapter FILE
                      Adapter executable to run inside the VM.
  --agent-payload FILE
                      Optional harness binary or artifact for the adapter.
  --agent-env-file FILE
                      Optional environment file copied into the VM mode 0600.
  --agent-name NAME   Harness name recorded in results.
  --ssh-port PORT     Forwarded SSH port (default: 22600).
  --http-port PORT    Forwarded HTTP port (default: 22601).
  --agent-timeout-seconds SECONDS
                      Maximum agent runtime (default: 900).
  --output-dir DIR    Result directory. It must not already exist.
  -h, --help          Show this help.

Environment:
  OPENROUTER_API_KEY             Required by the default Claux adapter.
  REPLAYBOOK_HOST_SSH_KEY        SSH key (default: ~/.ssh/id_ed25519).
  REPLAYBOOK_HOST_TMPDIR         Temporary file parent (default: /var/tmp).
  REPLAYBOOK_HOST_CLAUX_BINARY   Existing Claux binary to bake into the VM.
  REPLAYBOOK_HOST_CLAUX_RELEASE  Release tag to cache and bake in (default: v20260810.0.1).

Without --agent-adapter, Replaybook uses its bundled Claux adapter. Custom
adapters receive the paths and model through REPLAYBOOK_* environment variables
and must write normalized JSON to REPLAYBOOK_RESULT_FILE.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SSH_KEY="${REPLAYBOOK_HOST_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
WORK_PARENT="${REPLAYBOOK_HOST_TMPDIR:-/var/tmp}"
CLAUX_RELEASE="${REPLAYBOOK_HOST_CLAUX_RELEASE:-v20260810.0.1}"
CLAUX_BINARY="${REPLAYBOOK_HOST_CLAUX_BINARY:-}"
HOST_HARNESS_VERSION=16
MODEL="deepseek/deepseek-v4-flash"
REASONING_EFFORT=""
SCENARIO_ID="001-nginx-502-host"
SSH_PORT=22600
HTTP_PORT=22601
AGENT_TIMEOUT_SECONDS=900
OUTPUT_DIR=""
RUN_ORACLE=false
AGENT_ADAPTER=""
AGENT_PAYLOAD=""
AGENT_ENV_FILE=""
AGENT_NAME=""
CUSTOM_AGENT_ADAPTER=false
SCENARIO_PACK_DIRS=()

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
    --reasoning-effort)
      (( $# >= 2 )) || { echo "--reasoning-effort requires a value" >&2; exit 2; }
      REASONING_EFFORT="$2"
      shift 2
      ;;
    --agent-adapter)
      (( $# >= 2 )) || { echo "--agent-adapter requires a value" >&2; exit 2; }
      AGENT_ADAPTER="$2"
      shift 2
      ;;
    --agent-payload)
      (( $# >= 2 )) || { echo "--agent-payload requires a value" >&2; exit 2; }
      AGENT_PAYLOAD="$2"
      shift 2
      ;;
    --agent-env-file)
      (( $# >= 2 )) || { echo "--agent-env-file requires a value" >&2; exit 2; }
      AGENT_ENV_FILE="$2"
      shift 2
      ;;
    --agent-name)
      (( $# >= 2 )) || { echo "--agent-name requires a value" >&2; exit 2; }
      AGENT_NAME="$2"
      shift 2
      ;;
    --scenario)
      (( $# >= 2 )) || { echo "--scenario requires a value" >&2; exit 2; }
      SCENARIO_ID="$2"
      shift 2
      ;;
    --scenario-pack)
      (( $# >= 2 )) || { echo "--scenario-pack requires a value" >&2; exit 2; }
      SCENARIO_PACK_DIRS+=("$2")
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
    --agent-timeout-seconds)
      (( $# >= 2 )) || { echo "--agent-timeout-seconds requires a value" >&2; exit 2; }
      AGENT_TIMEOUT_SECONDS="$2"
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

if [[ "$RUN_ORACLE" == true \
  && ( -n "$AGENT_ADAPTER" || -n "$AGENT_PAYLOAD" \
    || -n "$AGENT_ENV_FILE" || -n "$AGENT_NAME" \
    || -n "$REASONING_EFFORT" ) ]]; then
  echo "--oracle cannot be combined with agent adapter options" >&2
  exit 2
fi
if [[ -n "$REASONING_EFFORT" \
  && ! "$REASONING_EFFORT" =~ ^(none|minimal|low|medium|high|xhigh|max)$ ]]; then
  echo "unsupported reasoning effort: ${REASONING_EFFORT}" >&2
  exit 2
fi
if [[ -n "$AGENT_ADAPTER" ]]; then
  CUSTOM_AGENT_ADAPTER=true
  if [[ -n "$REASONING_EFFORT" ]]; then
    echo "--reasoning-effort is supported only by the built-in Claux adapter" >&2
    exit 2
  fi
  [[ -f "$AGENT_ADAPTER" ]] || {
    echo "agent adapter does not exist: ${AGENT_ADAPTER}" >&2
    exit 2
  }
  if [[ -z "$AGENT_NAME" ]]; then
    AGENT_NAME="$(basename "$AGENT_ADAPTER")"
    AGENT_NAME="${AGENT_NAME%.*}"
  fi
else
  if [[ -n "$AGENT_PAYLOAD" || -n "$AGENT_ENV_FILE" || -n "$AGENT_NAME" ]]; then
    echo "custom agent options require --agent-adapter" >&2
    exit 2
  fi
  AGENT_ADAPTER="${SCRIPT_DIR}/run-claux.sh"
  [[ -n "$AGENT_NAME" ]] || AGENT_NAME="claux"
fi
[[ "$AGENT_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
  echo "agent name contains unsafe characters: ${AGENT_NAME}" >&2
  exit 2
}
if [[ -n "$AGENT_PAYLOAD" && ! -f "$AGENT_PAYLOAD" ]]; then
  echo "agent payload does not exist: ${AGENT_PAYLOAD}" >&2
  exit 2
fi
if [[ -n "$AGENT_ENV_FILE" && ! -f "$AGENT_ENV_FILE" ]]; then
  echo "agent environment file does not exist: ${AGENT_ENV_FILE}" >&2
  exit 2
fi

acquire_claux() {
  [[ "$RUN_ORACLE" == false && "$CUSTOM_AGENT_ADAPTER" == false ]] || return 0
  if [[ -n "$CLAUX_BINARY" ]]; then
    CLAUX_BINARY="$(realpath "$CLAUX_BINARY")"
    return 0
  fi

  local cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/replaybook/claux/${CLAUX_RELEASE}"
  local cached_binary="${cache_root}/claux-linux-x86_64"
  local lock_file="${cache_root}.lock"
  mkdir -p "$(dirname "$cache_root")"
  exec {claux_lock_fd}>"$lock_file"
  flock "$claux_lock_fd"
  if [[ ! -x "$cached_binary" ]]; then
    mkdir -p "$cache_root"
    local partial="${cached_binary}.partial.$$"
    echo "[host] caching Claux ${CLAUX_RELEASE}"
    curl --fail --location --silent --show-error \
      --retry 5 --retry-all-errors --retry-delay 2 \
      "https://github.com/ducks/claux/releases/download/${CLAUX_RELEASE}/claux-linux-x86_64" \
      --output "$partial"
    chmod 0755 "$partial"
    mv "$partial" "$cached_binary"
  fi
  CLAUX_BINARY="$cached_binary"
  flock -u "$claux_lock_fd"
  exec {claux_lock_fd}>&-
}

acquire_claux

[[ "$SCENARIO_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
  echo "scenario ID contains unsafe characters: ${SCENARIO_ID}" >&2
  exit 2
}
if (( ${#SCENARIO_PACK_DIRS[@]} == 0 )); then
  SCENARIO_PACK_DIRS=("${SCRIPT_DIR}/scenarios")
fi
pack_command=(python "${SCRIPT_DIR}/scenario_pack.py" --resolve "$SCENARIO_ID")
for scenario_pack_dir in "${SCENARIO_PACK_DIRS[@]}"; do
  pack_command+=(--pack "$scenario_pack_dir")
done
scenario_location="$("${pack_command[@]}")" || exit 2
SCENARIO_DIR="$(jq -r '.path' <<<"$scenario_location")"
SCENARIO_PACK_ID="$(jq -r '.pack.id' <<<"$scenario_location")"
SCENARIO_PACK_VERSION="$(jq -r '.pack.version' <<<"$scenario_location")"
TYPED_SCENARIO_MANIFEST="${SCENARIO_DIR}/scenario.toml"
LEGACY_SCENARIO_MANIFEST="${SCENARIO_DIR}/scenario.conf"
DECLARATIVE_SCENARIO=false
GUEST_LEAK_AUDIT_ENABLED=false
GUEST_LEAK_SCAN_PATHS=()
if [[ -f "$TYPED_SCENARIO_MANIFEST" ]]; then
  DECLARATIVE_SCENARIO=true
  SCENARIO_MANIFEST="$TYPED_SCENARIO_MANIFEST"
  scenario_description="$(python "$SCRIPT_DIR/scenario_phase.py" --describe "$SCENARIO_MANIFEST")" || exit 2
  SCENARIO_VERSION="$(jq -r '.version' <<<"$scenario_description")"
  NIXOS_CONFIG="$(jq -r '.nixos_config' <<<"$scenario_description")"
  INSTRUCTION="$(jq -r '.instruction' <<<"$scenario_description")"
  ORACLE="$(jq -r '.oracle' <<<"$scenario_description")"
  REQUIRED_SERVICES="$(jq -r '.required_services | join(" ")' <<<"$scenario_description")"
  RESTART_SERVICES="$(jq -r '.restart_services | join(" ")' <<<"$scenario_description")"
  if (( $(jq -r '.guest_leak_audit.forbidden_strings | length' <<<"$scenario_description") > 0 )); then
    GUEST_LEAK_AUDIT_ENABLED=true
    mapfile -t GUEST_LEAK_SCAN_PATHS < <(
      jq -r '.guest_leak_audit.scan_paths[]' <<<"$scenario_description"
    )
  fi
else
  SCENARIO_MANIFEST="$LEGACY_SCENARIO_MANIFEST"
fi
[[ -f "$SCENARIO_MANIFEST" ]] || {
  echo "unknown host-native scenario: ${SCENARIO_ID}" >&2
  exit 2
}
if [[ "$DECLARATIVE_SCENARIO" == false ]]; then
  # shellcheck source=/dev/null
  source "$SCENARIO_MANIFEST"
fi
[[ "${SCENARIO_VERSION:-}" =~ ^[1-9][0-9]*$ ]] || {
  echo "scenario ${SCENARIO_ID} has an invalid SCENARIO_VERSION" >&2
  exit 2
}
scenario_fields=(NIXOS_CONFIG INSTRUCTION ORACLE REQUIRED_SERVICES RESTART_SERVICES)
if [[ "$DECLARATIVE_SCENARIO" == false ]]; then
  scenario_fields+=(PREFLIGHT VERIFY)
fi
for scenario_field in "${scenario_fields[@]}"; do
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
SCENARIO_VM_CONFIG="$NIXOS_CONFIG"
VM_CONFIG="${SCRIPT_DIR}/isolated-vm.nix"

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
if [[ ! "$AGENT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || (( AGENT_TIMEOUT_SECONDS <= 0 )); then
  echo "--agent-timeout-seconds must be a positive integer" >&2
  exit 2
fi

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
if [[ "$RUN_ORACLE" == false && "$CUSTOM_AGENT_ADAPTER" == false \
  && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is required by the default Claux adapter" >&2
  exit 1
fi
if [[ "$CUSTOM_AGENT_ADAPTER" == false \
  && -n "$CLAUX_BINARY" && ! -f "$CLAUX_BINARY" ]]; then
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
export TMPDIR="${WORK_DIR}/tmp"
mkdir -p "$TMPDIR"
VM_PID=""
PROXY_PID=""
TUNNEL_PID=""

cleanup() {
  if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    wait "$TUNNEL_PID" 2>/dev/null || true
  fi
  if [[ -n "$PROXY_PID" ]] && kill -0 "$PROXY_PID" 2>/dev/null; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
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
  local timeout_seconds="${1:-120}"
  local deadline="$((SECONDS + timeout_seconds))"
  while (( SECONDS < deadline )); do
    if [[ -n "$VM_PID" ]] && ! kill -0 "$VM_PID" 2>/dev/null; then
      return 1
    fi
    if "$SCRIPT_DIR/ssh-probe.sh" 5 "${SSH[@]}" true >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_ssh_down() {
  local timeout_seconds="${1:-30}"
  local deadline="$((SECONDS + timeout_seconds))"
  while (( SECONDS < deadline )); do
    if ! "$SCRIPT_DIR/ssh-probe.sh" 5 "${SSH[@]}" true >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_services() {
  local timeout_seconds="${1:-60}"
  local deadline="$((SECONDS + timeout_seconds))"
  while (( SECONDS < deadline )); do
    if "$SCRIPT_DIR/ssh-probe.sh" 5 "${SSH[@]}" \
      "for service in $REQUIRED_SERVICES; do systemctl is-active \"\$service\" >/dev/null || exit 1; done" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

audit_guest_image() {
  [[ "$GUEST_LEAK_AUDIT_ENABLED" == true ]] || return 0

  local audit_dir="${WORK_DIR}/guest-leak-audit"
  local content_archive="${audit_dir}/configured-paths.tar.gz"
  mkdir -m 0700 "$audit_dir"

  "${SSH[@]}" \
    "systemctl list-units --all --plain --no-pager --no-legend; systemctl list-unit-files --no-pager --no-legend; systemctl list-units --all --plain --no-pager --no-legend | while read -r unit rest; do systemctl show --all --no-pager -- \"\$unit\"; done" \
    >"${audit_dir}/systemd.txt"
  "${SSH[@]}" \
    "nix-store --query --requisites /run/current-system; find /etc /root /var/lib -xdev -printf '%p -> %l\\n'" \
    >"${audit_dir}/paths.txt"

  if (( ${#GUEST_LEAK_SCAN_PATHS[@]} > 0 )); then
    "${SSH[@]}" bash -s -- "${GUEST_LEAK_SCAN_PATHS[@]}" >"$content_archive" <<'EOF'
set -euo pipefail
existing=()
for path in "$@"; do
  [[ ! -e "$path" ]] || existing+=("${path#/}")
done
if (( ${#existing[@]} == 0 )); then
  tar -czf - --files-from /dev/null
else
  tar -C / -czf - -- "${existing[@]}"
fi
EOF
  else
    tar -czf "$content_archive" --files-from /dev/null
  fi

  if ! python "$SCRIPT_DIR/guest_leak_audit.py" "$SCENARIO_MANIFEST" \
    --surface "systemd=${audit_dir}/systemd.txt" \
    --surface "paths=${audit_dir}/paths.txt" \
    --archive "configured-paths=${content_archive}"; then
    echo "guest image leak audit failed for ${SCENARIO_ID}" >&2
    return 1
  fi
  rm -rf -- "$audit_dir"
}

verify_repaired() {
  local phase="$1"
  if [[ "$DECLARATIVE_SCENARIO" == true ]]; then
    python "$SCRIPT_DIR/scenario_phase.py" \
      "$SCENARIO_MANIFEST" "$phase" "http://127.0.0.1:${HTTP_PORT}" "$SCENARIO_STATE_DIR"
  else
    "$VERIFY" "http://127.0.0.1:${HTTP_PORT}" "$phase" "$SCENARIO_STATE_DIR"
  fi
}

run_preflight() {
  if [[ "$DECLARATIVE_SCENARIO" == true ]]; then
    python "$SCRIPT_DIR/scenario_phase.py" \
      "$SCENARIO_MANIFEST" preflight "http://127.0.0.1:${HTTP_PORT}" "$SCENARIO_STATE_DIR"
  else
    "$PREFLIGHT" "http://127.0.0.1:${HTTP_PORT}" "$SCENARIO_STATE_DIR"
  fi
}

capture_agent_results() {
  local archive="${WORK_DIR}/agent-results.tar.gz"

  [[ "$RUN_ORACLE" == false ]] || return 0
  if ! "${SSH[@]}" "tar -C /root/replaybook-eval -czf - results" >"$archive"; then
    rm -f -- "$archive"
    return 1
  fi
  if ! tar -tzf "$archive" >/dev/null 2>&1; then
    rm -f -- "$archive"
    return 1
  fi
  tar -xzf "$archive" -C "$OUTPUT_DIR"
}

run_verification() {
  local phase="$1"
  local status=0

  verify_repaired "$phase" || status=$?
  if [[ "$DECLARATIVE_SCENARIO" == true ]] && (( status != 0 )); then
    failure_category="$(jq -r '.category // empty' "$SCENARIO_STATE_DIR/phase-failure.json" 2>/dev/null || true)"
    [[ -n "$failure_category" ]] || failure_category="verification_failed"
  elif (( status == 20 )); then
    failure_category="backlog_not_recovered"
  elif (( status == 21 )); then
    failure_category="migration_not_applied"
  elif (( status == 22 )); then
    failure_category="poison_not_quarantined"
  fi
  return "$status"
}

verify_repair_lifecycle() {
  if ! run_verification immediate; then
    if [[ "$failure_category" == "backlog_not_recovered" ]]; then
      failure="repair did not recover the pre-existing backlog"
    elif [[ "$failure_category" == "migration_not_applied" ]]; then
      failure="repair did not apply the deployed migration"
    elif [[ "$failure_category" == "poison_not_quarantined" ]]; then
      failure="repair did not quarantine poison work safely"
    elif [[ "$failure_category" == "database_pool_exhausted" ]]; then
      failure="repair did not restore database connection capacity"
    else
      failure="HTTP repair did not recover"
    fi
    return 1
  fi

  immediate_passed=true
  echo "[host] immediate verification passed"
  if ! "${SSH[@]}" "systemctl restart $RESTART_SERVICES"; then
    failure="service restart failed"
    return 1
  elif ! run_verification service_restart; then
    if [[ "$failure_category" == "backlog_not_recovered" ]]; then
      failure="pre-existing backlog recovery did not survive service restarts"
    elif [[ "$failure_category" == "migration_not_applied" ]]; then
      failure="deployed migration was not applied after service restarts"
    elif [[ "$failure_category" == "poison_not_quarantined" ]]; then
      failure="poison handling did not survive service restarts"
    elif [[ "$failure_category" == "database_pool_exhausted" ]]; then
      failure="database pool repair did not survive service restarts"
    else
      failure="repair did not survive service restarts"
    fi
    return 1
  fi

  restart_passed=true
  echo "[host] service restart verification passed"
  set +e
  "${SSH[@]}" "systemctl reboot" >/dev/null 2>&1
  set -e
  if ! wait_for_ssh_down; then
    failure_category="host_reboot_failed"
    failure="VM did not shut down for reboot"
    return 1
  elif ! wait_for_ssh 120; then
    failure_category="host_reboot_failed"
    failure="VM did not return after reboot"
    return 1
  elif ! wait_for_services; then
    failure_category="services_failed_after_reboot"
    failure="required systemd services are not active after reboot"
    return 1
  elif ! run_verification host_reboot; then
    if [[ "$failure_category" == "backlog_not_recovered" ]]; then
      failure="pre-existing backlog recovery did not survive host reboot"
    elif [[ "$failure_category" == "migration_not_applied" ]]; then
      failure="deployed migration was not applied after host reboot"
    elif [[ "$failure_category" == "poison_not_quarantined" ]]; then
      failure="poison handling did not survive host reboot"
    elif [[ "$failure_category" == "database_pool_exhausted" ]]; then
      failure="database pool repair did not survive host reboot"
    else
      failure="repair did not survive host reboot"
    fi
    return 1
  fi

  reboot_passed=true
  echo "[host] host reboot verification passed"
  return 0
}

echo "[host] building disposable NixOS incident host"
export REPLAYBOOK_HOST_PUBLIC_KEY_FILE="${SSH_KEY}.pub"
export REPLAYBOOK_HOST_SSH_PORT="$SSH_PORT"
export REPLAYBOOK_HOST_HTTP_PORT="$HTTP_PORT"
export REPLAYBOOK_HOST_SCENARIO_CONFIG="$SCENARIO_VM_CONFIG"
export REPLAYBOOK_HOST_CLAUX_BINARY="$CLAUX_BINARY"
nix-shell -p nixos-generators --run \
  "nixos-generate -f vm-nogui -c '${VM_CONFIG}' -o '${WORK_DIR}/vm'"

VM_RUNNER="$(find -L "${WORK_DIR}/vm/bin" -maxdepth 1 -type f -name 'run-*-vm' -print -quit)"
[[ -n "$VM_RUNNER" ]] || {
  echo "generated VM runner was not found" >&2
  exit 1
}

echo "[host] booting VM on SSH ${SSH_PORT}, HTTP ${HTTP_PORT}"
NIX_DISK_IMAGE="${WORK_DIR}/disk.qcow2" \
  USE_TMPDIR=1 \
  "$VM_RUNNER" >"${OUTPUT_DIR}/console.log" 2>&1 &
VM_PID=$!

wait_for_ssh || {
  echo "incident VM did not become reachable" >&2
  tail -100 "${OUTPUT_DIR}/console.log" >&2
  exit 1
}

if [[ "$RUN_ORACLE" == false && "$CUSTOM_AGENT_ADAPTER" == false ]]; then
  proxy_ready="${WORK_DIR}/openrouter-proxy.port"
  OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
    python "${SCRIPT_DIR}/openrouter_proxy.py" \
      --port 0 \
      --ready-file "$proxy_ready" \
      >"${WORK_DIR}/openrouter-proxy.log" 2>&1 &
  PROXY_PID=$!
  for _ in $(seq 1 100); do
    [[ -s "$proxy_ready" ]] && break
    kill -0 "$PROXY_PID" 2>/dev/null || {
      cat "${WORK_DIR}/openrouter-proxy.log" >&2
      echo "OpenRouter credential proxy exited before becoming ready" >&2
      exit 1
    }
    sleep 0.05
  done
  [[ -s "$proxy_ready" ]] || {
    echo "OpenRouter credential proxy did not become ready" >&2
    exit 1
  }
  proxy_host_port="$(<"$proxy_ready")"
  proxy_vm_port=19091
  ssh \
    -i "$SSH_KEY" \
    -p "$SSH_PORT" \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o LogLevel=ERROR \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -N \
    -R "127.0.0.1:${proxy_vm_port}:127.0.0.1:${proxy_host_port}" \
    root@127.0.0.1 &
  TUNNEL_PID=$!
  for _ in $(seq 1 100); do
    if "${SSH[@]}" "timeout 1 bash -c '</dev/tcp/127.0.0.1/${proxy_vm_port}'" \
      >/dev/null 2>&1; then
      break
    fi
    kill -0 "$TUNNEL_PID" 2>/dev/null || {
      echo "OpenRouter credential tunnel exited before becoming ready" >&2
      exit 1
    }
    sleep 0.05
  done
  "${SSH[@]}" "timeout 1 bash -c '</dev/tcp/127.0.0.1/${proxy_vm_port}'" \
    >/dev/null 2>&1 || {
      echo "OpenRouter credential tunnel did not become ready" >&2
      exit 1
    }
fi
if ! wait_for_services; then
  echo "incident services did not start" >&2
  "${SSH[@]}" \
    "systemctl --no-pager --full status $REQUIRED_SERVICES; journalctl --no-pager -n 100 $(
      for service in $REQUIRED_SERVICES; do printf -- '-u %q ' "$service"; done
    )" \
    >&2 || true
  exit 1
fi

if "${SSH[@]}" "grep -q '^nix-store /nix/.ro-store 9p ' /proc/mounts"; then
  echo "incident VM unexpectedly exposes the host Nix store" >&2
  exit 1
fi

if ! audit_guest_image; then
  exit 1
fi

if ! run_preflight; then
  echo "incident preflight failed for ${SCENARIO_ID}" >&2
  exit 1
fi
echo "[host] preflight confirmed ${SCENARIO_ID}"

"${SSH[@]}" "rm -rf -- /root/replaybook-eval; install -d -m 700 /root/replaybook-eval/results"
RUNTIME_INSTRUCTION="${WORK_DIR}/instruction.md"
{
  cat "$INSTRUCTION"
  printf '\n%s\n' \
    "You have a hard limit of ${AGENT_TIMEOUT_SECONDS} seconds for this task." \
    "Budget the investigation accordingly. Once you have applied and verified a durable repair, stop investigating and return your final report before the deadline."
} >"$RUNTIME_INSTRUCTION"
"${SCP[@]}" "$RUNTIME_INSTRUCTION" root@127.0.0.1:/root/replaybook-eval/instruction.md
"${SCP[@]}" "${SCRIPT_DIR}/run-agent-adapter.sh" root@127.0.0.1:/root/replaybook-eval/launcher

agent="$AGENT_NAME"
run_status=0
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
start_seconds="$(date +%s)"
if [[ "$RUN_ORACLE" == true ]]; then
  agent="oracle"
  echo "[host] running reference repair"
  "${SCP[@]}" "$ORACLE" root@127.0.0.1:/root/replaybook-eval/oracle.sh
  "${SSH[@]}" "bash /root/replaybook-eval/oracle.sh" || run_status=$?
else
  # The oracle is the benchmark answer key. Keep this runtime assertion next
  # to the model path so a future staging change cannot silently expose it.
  "${SSH[@]}" "test ! -e /root/replaybook-eval/oracle.sh"
  if [[ "$CUSTOM_AGENT_ADAPTER" == false ]]; then
    if [[ -z "$AGENT_ENV_FILE" ]]; then
      AGENT_ENV_FILE="${WORK_DIR}/runtime.env"
      printf '%s\n' \
        'export OPENROUTER_API_KEY=replaybook-proxy' \
        'export REPLAYBOOK_OPENAI_BASE_URL=http://127.0.0.1:19091/api/v1' \
        >"$AGENT_ENV_FILE"
      chmod 0600 "$AGENT_ENV_FILE"
    fi
  fi
  "${SCP[@]}" "$AGENT_ADAPTER" root@127.0.0.1:/root/replaybook-eval/adapter
  if [[ -n "$AGENT_PAYLOAD" ]]; then
    "${SCP[@]}" "$AGENT_PAYLOAD" root@127.0.0.1:/root/replaybook-eval/payload
  fi
  if [[ -n "$AGENT_ENV_FILE" ]]; then
    "${SCP[@]}" "$AGENT_ENV_FILE" root@127.0.0.1:/root/replaybook-eval/runtime.env
  else
    "${SSH[@]}" "umask 077; : > /root/replaybook-eval/runtime.env"
  fi
  "${SSH[@]}" \
    "chmod 0755 /root/replaybook-eval/launcher /root/replaybook-eval/adapter; \
     chmod 0600 /root/replaybook-eval/runtime.env"
  printf '%s\n' "$MODEL" | "${SSH[@]}" "umask 077; cat > /root/replaybook-eval/model"
  echo "[host] running ${AGENT_NAME} directly on the incident host"
  "${SSH[@]}" \
    "REPLAYBOOK_REASONING_EFFORT=$(printf '%q' "$REASONING_EFFORT") timeout --signal=TERM --kill-after=30s ${AGENT_TIMEOUT_SECONDS}s /root/replaybook-eval/launcher" \
    || run_status=$?
fi
agent_seconds="$(( $(date +%s) - start_seconds ))"
agent_timed_out=false
if [[ "$(
  "$SCRIPT_DIR/classify-agent-run-exit.sh" \
    "$run_status" "$agent_seconds" "$AGENT_TIMEOUT_SECONDS"
)" == "agent_timeout" ]]; then
  agent_timed_out=true
fi

# Preserve usage and transcript data while the repaired host is still
# reachable. Verification deliberately restarts and reboots that host, and a
# broken repair may prevent it from ever returning.
capture_agent_results || true

agent_result="${OUTPUT_DIR}/results/agent.json"
agent_result_invalid=false
if [[ "$RUN_ORACLE" == false && -f "$agent_result" ]]; then
  reported_agent="$(jq -r --arg model "$MODEL" --arg agent "$AGENT_NAME" 'select(
      type == "object" and
      .schema_version == 1 and
      .harness == $agent and
      .model == $model
    ) | .harness' "$agent_result" 2>/dev/null || true)"
  if [[ -n "$reported_agent" ]]; then
    agent="$reported_agent"
  elif (( run_status == 0 )); then
    agent_result_invalid=true
    run_status=65
  fi
elif [[ "$RUN_ORACLE" == false && $run_status -eq 0 ]]; then
  agent_result_invalid=true
  run_status=65
fi

reward=0
failure=""
failure_category=""
trial_status="evaluated"
immediate_passed=false
restart_passed=false
reboot_passed=false
post_timeout_verification_attempted=false
post_timeout_durable_repair=false
post_timeout_failure=""
post_timeout_failure_category=""
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
  if [[ "$agent_result_invalid" == true ]]; then
    failure_category="agent_result_invalid"
    failure="agent did not write a valid normalized result"
  elif [[ "$agent_timed_out" == true ]]; then
    failure_category="agent_timeout"
    failure="agent exceeded ${AGENT_TIMEOUT_SECONDS} second timeout"
  elif [[ "$failure_category" == "agent_rebooted_host" ]]; then
    failure="agent rebooted the host during its session"
  elif [[ -f "$agent_result" ]] \
    && agent_outcome_classification="$("$SCRIPT_DIR/classify-agent-outcome.sh" "$agent_result")" \
    && [[ -n "$agent_outcome_classification" ]]; then
    read -r trial_status agent_outcome_category <<<"$agent_outcome_classification"
    failure_category="$agent_outcome_category"
    failure="$(jq -r '.outcome.message // "agent harness could not complete the trial"' "$agent_result")"
  else
    failure="agent exited with status $run_status"
  fi
  if [[ "$agent_timed_out" == true ]]; then
    primary_failure="$failure"
    primary_failure_category="$failure_category"
    failure=""
    failure_category=""
    post_timeout_verification_attempted=true
    echo "[host] verifying host state after agent timeout"
    if verify_repair_lifecycle; then
      post_timeout_durable_repair=true
      echo "[host] timed-out agent left a durable repair"
    else
      post_timeout_failure="$failure"
      post_timeout_failure_category="$failure_category"
    fi
    failure="$primary_failure"
    failure_category="$primary_failure_category"
  fi
elif verify_repair_lifecycle; then
  reward=1
fi

if [[ "$RUN_ORACLE" == false && ! -f "$agent_result" ]]; then
  capture_agent_results || true
fi

usage='null'
recording='null'
if [[ -f "$agent_result" ]]; then
  usage_candidate="$(
    jq -c 'if type == "object" then (.usage // null) else null end' \
      "$agent_result" 2>/dev/null || true
  )"
  if [[ -n "$usage_candidate" ]] && jq -e . <<<"$usage_candidate" >/dev/null 2>&1; then
    usage="$usage_candidate"
  fi
  recording_candidate="$(
    jq -c 'if type == "object" then (.recording // null) else null end' \
      "$agent_result" 2>/dev/null || true
  )"
  if [[ -n "$recording_candidate" ]] && jq -e . <<<"$recording_candidate" >/dev/null 2>&1; then
    recording="$recording_candidate"
  fi
fi
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -n \
  --arg suite "replaybook-host-v1" \
  --argjson harness_version "$HOST_HARNESS_VERSION" \
  --arg scenario "$SCENARIO_ID" \
  --argjson scenario_version "$SCENARIO_VERSION" \
  --arg scenario_pack_id "$SCENARIO_PACK_ID" \
  --arg scenario_pack_version "$SCENARIO_PACK_VERSION" \
  --arg agent "$agent" \
  --arg model "$(if [[ "$RUN_ORACLE" == true ]]; then printf 'oracle'; else printf '%s' "$MODEL"; fi)" \
  --arg reasoning_effort "$REASONING_EFFORT" \
  --arg started_at "$started_at" \
  --arg finished_at "$finished_at" \
  --arg failure "$failure" \
  --arg failure_category "$failure_category" \
  --arg trial_status "$trial_status" \
  --argjson reward "$reward" \
  --argjson agent_seconds "$agent_seconds" \
  --argjson agent_timeout_seconds "$AGENT_TIMEOUT_SECONDS" \
  --argjson immediate_passed "$immediate_passed" \
  --argjson restart_passed "$restart_passed" \
  --argjson reboot_passed "$reboot_passed" \
  --argjson post_timeout_verification_attempted "$post_timeout_verification_attempted" \
  --argjson post_timeout_durable_repair "$post_timeout_durable_repair" \
  --arg post_timeout_failure "$post_timeout_failure" \
  --arg post_timeout_failure_category "$post_timeout_failure_category" \
  --argjson usage "$usage" \
  --argjson recording "$recording" \
  '{
    schema_version: 1,
    suite: $suite,
    harness_version: $harness_version,
    scenario: $scenario,
    scenario_version: $scenario_version,
    scenario_pack: {
      id: $scenario_pack_id,
      version: $scenario_pack_version
    },
    agent: $agent,
    model: $model,
    reasoning_effort: (if $reasoning_effort == "" then null else $reasoning_effort end),
    started_at: $started_at,
    finished_at: $finished_at,
    agent_duration_seconds: $agent_seconds,
    agent_timeout_seconds: $agent_timeout_seconds,
    reward: $reward,
    trial_status: $trial_status,
    failure: (if $failure == "" then null else $failure end),
    failure_category: (if $failure_category == "" then null else $failure_category end),
    usage: $usage,
    recording: $recording,
    verification: {
      immediate_http: $immediate_passed,
      service_restart: $restart_passed,
      host_reboot: $reboot_passed,
      after_agent_timeout: (
        if $post_timeout_verification_attempted then {
          durable_repair: $post_timeout_durable_repair,
          failure: (if $post_timeout_failure == "" then null else $post_timeout_failure end),
          failure_category: (if $post_timeout_failure_category == "" then null else $post_timeout_failure_category end)
        } else null end
      )
    }
  }' >"${OUTPUT_DIR}/result.json"

echo "[host] results: ${OUTPUT_DIR}"
if (( reward != 1 )); then
  echo "[host] failed: ${failure}" >&2
  exit 1
fi
echo "[host] host-native evaluation passed"
