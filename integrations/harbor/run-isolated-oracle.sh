#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VM_CONFIG="${SCRIPT_DIR}/worker/nixos.nix"
SSH_KEY="${REPLAYBOOK_WORKER_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
SSH_PORT="${REPLAYBOOK_WORKER_SSH_PORT:-22222}"
WORK_DIR="$(mktemp -d /tmp/replaybook-eval-worker.XXXXXX)"
VM_PID=""

cleanup() {
  if [[ -n "${VM_PID}" ]] && kill -0 "${VM_PID}" 2>/dev/null; then
    kill "${VM_PID}" 2>/dev/null || true
    wait "${VM_PID}" 2>/dev/null || true
  fi
  rm -rf -- "${WORK_DIR}"
}
trap cleanup EXIT INT TERM

[[ -f "${SSH_KEY}" ]] || {
  echo "missing SSH private key: ${SSH_KEY}" >&2
  exit 1
}
[[ -f "${SSH_KEY}.pub" ]] || {
  echo "missing SSH public key: ${SSH_KEY}.pub" >&2
  exit 1
}
[[ "${SSH_PORT}" =~ ^[0-9]+$ ]] && (( SSH_PORT > 0 && SSH_PORT <= 65535 )) || {
  echo "REPLAYBOOK_WORKER_SSH_PORT must be an integer from 1 to 65535" >&2
  exit 1
}
if ss -ltn "sport = :${SSH_PORT}" | tail -n +2 | grep -q .; then
  echo "SSH forwarding port ${SSH_PORT} is already in use" >&2
  exit 1
fi

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
"${SSH[@]}" "mkdir -p /root/replaybook"
tar -C "${REPO_DIR}" -czf - integrations/harbor \
  | "${SSH[@]}" "tar -xzf - -C /root/replaybook"

echo "[worker] installing Harbor and running the oracle"
"${SSH[@]}" \
  "docker compose version && docker buildx version && uv tool install harbor && cd /root/replaybook && HARBOR_TELEMETRY=off /root/.local/bin/harbor run -p integrations/harbor/tasks/001-nginx-502 -a oracle"

RESULT_DIR="${REPO_DIR}/jobs/isolated-worker-$(date -u +%Y-%m-%d__%H-%M-%S)"
mkdir -p "${RESULT_DIR}"
"${SSH[@]}" "tar -C /root/replaybook -czf - jobs" \
  | tar -xzf - -C "${RESULT_DIR}"

RESULT_FILE="$(find "${RESULT_DIR}" -mindepth 3 -maxdepth 3 -name result.json -print -quit)"
[[ -n "${RESULT_FILE}" ]] || {
  echo "worker result was not retrieved" >&2
  exit 1
}
jq -e '.stats.n_completed_trials == 1 and .stats.n_errored_trials == 0' \
  "${RESULT_FILE}" >/dev/null

echo "[worker] isolated oracle passed"
echo "[worker] results: ${RESULT_DIR}"
