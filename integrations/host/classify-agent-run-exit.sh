#!/usr/bin/env bash
set -euo pipefail

run_status="${1:?usage: classify-agent-run-exit.sh STATUS ELAPSED_SECONDS TIMEOUT_SECONDS}"
elapsed_seconds="${2:?usage: classify-agent-run-exit.sh STATUS ELAPSED_SECONDS TIMEOUT_SECONDS}"
timeout_seconds="${3:?usage: classify-agent-run-exit.sh STATUS ELAPSED_SECONDS TIMEOUT_SECONDS}"

for value in "$run_status" "$elapsed_seconds" "$timeout_seconds"; do
  [[ "$value" =~ ^[0-9]+$ ]] || {
    echo "status and durations must be non-negative integers" >&2
    exit 2
  }
done

if (( run_status == 124 )) \
  || (( elapsed_seconds >= timeout_seconds \
    && (run_status == 137 || run_status == 255) )); then
  printf '%s\n' agent_timeout
fi
