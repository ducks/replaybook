#!/usr/bin/env bash
set -euo pipefail

result_file="${1:?usage: classify-agent-outcome.sh RESULT.json}"
[[ -f "$result_file" ]] || exit 0

status="$(jq -r 'if type == "object" then (.outcome.status // "") else "" end' "$result_file" 2>/dev/null || true)"
[[ "$status" == "error" ]] || exit 0

message="$(jq -r 'if type == "object" then (.outcome.message // "") else "" end' "$result_file" 2>/dev/null || true)"
message="${message,,}"

if [[ "$message" =~ 401|403|authentication|unauthorized|api[[:space:]_-]*key ]]; then
  printf '%s\n' authentication_failed
elif [[ "$message" =~ 429|too[[:space:]]+many[[:space:]]+requests|provider[[:space:]]+returned[[:space:]]+error|provider[[:space:]]+unavailable|api[[:space:]]+error ]]; then
  printf '%s\n' provider_unavailable
else
  printf '%s\n' agent_runtime_error
fi
