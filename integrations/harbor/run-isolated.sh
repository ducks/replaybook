#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run a Harbor evaluation inside a disposable NixOS VM.

Usage:
  run-isolated.sh [worker options] -- <harbor run arguments>

Worker options:
  --env NAME       Forward a set host environment variable into the worker.
                   Repeat for additional variables.
  --codex-auth     Copy ~/.codex/auth.json into the disposable worker.
  --output-dir DIR Store the retrieved worker job under DIR. DIR must not exist.
  -h, --help       Show this help.

Examples:
  bash integrations/harbor/run-isolated.sh -- \
    -p integrations/harbor/tasks/001-nginx-502 -a oracle

  bash integrations/harbor/run-isolated.sh \
    --codex-auth \
    --env CLAUDE_CODE_OAUTH_TOKEN \
    --env OPENROUTER_API_KEY \
    -- --config integrations/harbor/jobs/three-agent-smoke.yaml --yes

Environment:
  REPLAYBOOK_WORKER_SSH_KEY   SSH private key (default: ~/.ssh/id_ed25519)
  REPLAYBOOK_WORKER_SSH_PORT  Forwarded worker SSH port (default: 22222)
  REPLAYBOOK_WORKER_TMPDIR    Parent for ephemeral VM files (default: /var/tmp)
  REPLAYBOOK_CODEX_AUTH_FILE  Codex auth file (default: ~/.codex/auth.json)
EOF
}

SCRIPT_DIR="${REPLAYBOOK_HARBOR_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPO_DIR="${REPLAYBOOK_REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
VM_CONFIG="${SCRIPT_DIR}/worker/nixos.nix"
SSH_KEY="${REPLAYBOOK_WORKER_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
SSH_PORT="${REPLAYBOOK_WORKER_SSH_PORT:-22222}"
CODEX_AUTH_FILE="${REPLAYBOOK_CODEX_AUTH_FILE:-${HOME}/.codex/auth.json}"
WORK_PARENT="${REPLAYBOOK_WORKER_TMPDIR:-/var/tmp}"
FORWARDED_ENV=()
COPY_CODEX_AUTH=false
OUTPUT_DIR=""
HARBOR_ARGS=()

while (( $# > 0 )); do
  case "$1" in
    --env)
      (( $# >= 2 )) || {
        echo "--env requires a variable name" >&2
        exit 2
      }
      FORWARDED_ENV+=("$2")
      shift 2
      ;;
    --codex-auth)
      COPY_CODEX_AUTH=true
      shift
      ;;
    --output-dir)
      (( $# >= 2 )) || {
        echo "--output-dir requires a path" >&2
        exit 2
      }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      HARBOR_ARGS=("$@")
      break
      ;;
    *)
      echo "unknown worker option: $1" >&2
      echo "put Harbor arguments after --" >&2
      exit 2
      ;;
  esac
done

(( ${#HARBOR_ARGS[@]} > 0 )) || {
  echo "no Harbor arguments supplied; put them after --" >&2
  exit 2
}

for name in "${FORWARDED_ENV[@]}"; do
  [[ "${name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    echo "invalid environment variable name: ${name}" >&2
    exit 2
  }
  [[ -v "${name}" ]] || {
    echo "environment variable is not set: ${name}" >&2
    exit 2
  }
done

[[ -f "${SSH_KEY}" ]] || {
  echo "missing SSH private key: ${SSH_KEY}" >&2
  exit 1
}
[[ -f "${SSH_KEY}.pub" ]] || {
  echo "missing SSH public key: ${SSH_KEY}.pub" >&2
  exit 1
}
if [[ "${COPY_CODEX_AUTH}" == true && ! -f "${CODEX_AUTH_FILE}" ]]; then
  echo "missing Codex auth file: ${CODEX_AUTH_FILE}" >&2
  exit 1
fi
if [[ ! "${SSH_PORT}" =~ ^[0-9]+$ ]] || (( SSH_PORT <= 0 || SSH_PORT > 65535 )); then
  echo "REPLAYBOOK_WORKER_SSH_PORT must be an integer from 1 to 65535" >&2
  exit 1
fi
if ss -ltn "sport = :${SSH_PORT}" | tail -n +2 | grep -q .; then
  echo "SSH forwarding port ${SSH_PORT} is already in use" >&2
  exit 1
fi
[[ -d "${WORK_PARENT}" ]] || {
  echo "worker temporary directory does not exist: ${WORK_PARENT}" >&2
  exit 1
}
if [[ -n "${OUTPUT_DIR}" ]]; then
  if [[ "${OUTPUT_DIR}" != /* ]]; then
    OUTPUT_DIR="${REPO_DIR}/${OUTPUT_DIR}"
  fi
  [[ ! -e "${OUTPUT_DIR}" ]] || {
    echo "worker output directory already exists: ${OUTPUT_DIR}" >&2
    exit 1
  }
fi

WORK_DIR="$(mktemp -d "${WORK_PARENT%/}/replaybook-eval-worker.XXXXXX")"
VM_PID=""

cleanup() {
  if [[ -n "${VM_PID}" ]] && kill -0 "${VM_PID}" 2>/dev/null; then
    kill "${VM_PID}" 2>/dev/null || true
    wait "${VM_PID}" 2>/dev/null || true
  fi
  rm -rf -- "${WORK_DIR}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

{
  printf 'export CODEX_FORCE_AUTH_JSON=%q\n' "1"
  printf 'export CLAUDE_FORCE_OAUTH=%q\n' "1"
  printf 'export HARBOR_TELEMETRY=%q\n' "off"
  printf 'export PYTHONPATH=%q\n' "/root/replaybook"
  for name in "${FORWARDED_ENV[@]}"; do
    printf 'export %s=%q\n' "${name}" "${!name}"
  done
} >"${WORK_DIR}/runtime.env"

printf -v HARBOR_COMMAND ' %q' "${HARBOR_ARGS[@]}"

echo "[worker] building disposable NixOS VM"
export REPLAYBOOK_WORKER_PUBLIC_KEY_FILE="${SSH_KEY}.pub"
export REPLAYBOOK_WORKER_SSH_PORT="${SSH_PORT}"
nix-shell -p nixos-generators --run \
  "nixos-generate -f vm-nogui -c '${VM_CONFIG}' -o '${WORK_DIR}/vm'"

VM_RUNNER="$(find -L "${WORK_DIR}/vm/bin" -maxdepth 1 -type f -name 'run-*-vm' -print -quit)"
[[ -n "${VM_RUNNER}" ]] || {
  echo "generated VM runner was not found" >&2
  exit 1
}

echo "[worker] booting VM on SSH port ${SSH_PORT}"
NIX_DISK_IMAGE="${WORK_DIR}/disk.qcow2" \
  "${VM_RUNNER}" >"${WORK_DIR}/console.log" 2>&1 &
VM_PID=$!

SSH=(
  ssh
  -i "${SSH_KEY}"
  -p "${SSH_PORT}"
  -o BatchMode=yes
  -o ConnectTimeout=2
  -o LogLevel=ERROR
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  root@127.0.0.1
)

ready=false
for _ in $(seq 1 120); do
  if ! kill -0 "${VM_PID}" 2>/dev/null; then
    echo "worker VM exited before SSH became ready" >&2
    tail -100 "${WORK_DIR}/console.log" >&2
    exit 1
  fi
  if "${SSH[@]}" true >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
[[ "${ready}" == true ]] || {
  echo "timed out waiting for worker SSH" >&2
  tail -100 "${WORK_DIR}/console.log" >&2
  exit 1
}

echo "[worker] staging Harbor integration"
"${SSH[@]}" "mkdir -p /root/replaybook /root/worker"
tar -C "${REPO_DIR}" -czf - integrations/harbor \
  | "${SSH[@]}" "tar -xzf - -C /root/replaybook"
tar -C "${WORK_DIR}" -czf - runtime.env \
  | "${SSH[@]}" "tar -xzf - -C /root/worker && chmod 600 /root/worker/runtime.env"

if [[ "${COPY_CODEX_AUTH}" == true ]]; then
  echo "[worker] staging Codex authentication"
  "${SSH[@]}" "install -d -m 700 /root/.codex"
  "${SSH[@]}" "umask 077 && cat > /root/.codex/auth.json" <"${CODEX_AUTH_FILE}"
fi

echo "[worker] installing Harbor and running evaluation"
set +e
"${SSH[@]}" \
  "docker compose version && docker buildx version && uv tool install harbor && cd /root/replaybook && source /root/worker/runtime.env && /root/.local/bin/harbor run${HARBOR_COMMAND}"
RUN_STATUS=$?
set -e

if [[ -n "${OUTPUT_DIR}" ]]; then
  mkdir -p "$(dirname "${OUTPUT_DIR}")"
  mkdir "${OUTPUT_DIR}"
  RESULT_DIR="${OUTPUT_DIR}"
else
  mkdir -p "${REPO_DIR}/jobs"
  RESULT_DIR="$(mktemp -d "${REPO_DIR}/jobs/isolated-worker-$(date -u +%Y-%m-%d__%H-%M-%S).XXXXXX")"
fi
"${SSH[@]}" "if [[ -d /root/replaybook/jobs ]]; then tar -C /root/replaybook -czf - jobs; else exit 3; fi" \
  | tar -xzf - -C "${RESULT_DIR}"

mapfile -t RESULT_FILES < <(
  find "${RESULT_DIR}" -mindepth 3 -maxdepth 3 -name result.json -print
)
(( ${#RESULT_FILES[@]} > 0 )) || {
  echo "worker result was not retrieved" >&2
  exit 1
}

if (( RUN_STATUS != 0 )); then
  echo "Harbor exited with status ${RUN_STATUS}" >&2
  echo "[worker] results: ${RESULT_DIR}" >&2
  exit "${RUN_STATUS}"
fi

jq -e -s \
  'all(.[]; .stats.n_completed_trials > 0 and .stats.n_errored_trials == 0)' \
  "${RESULT_FILES[@]}" >/dev/null

echo "[worker] isolated evaluation passed"
echo "[worker] results: ${RESULT_DIR}"
