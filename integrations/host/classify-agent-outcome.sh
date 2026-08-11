#!/usr/bin/env bash
set -euo pipefail

result_file="${1:?usage: classify-agent-outcome.sh RESULT.json}"
[[ -f "$result_file" ]] || exit 0

status="$(jq -r 'if type == "object" then (.outcome.status // "") else "" end' "$result_file" 2>/dev/null || true)"
[[ "$status" == "error" ]] || exit 0

message="$(jq -r 'if type == "object" then (.outcome.message // "") else "" end' "$result_file" 2>/dev/null || true)"
message="${message,,}"
made_progress="$(jq -r '
  if type != "object" then false
  else
    any(.recording.model_rounds[]?; .status == "completed") or
    ((.recording.tools // []) | length > 0)
  end
' "$result_file" 2>/dev/null || printf '%s\n' false)"

if [[ "$message" =~ 401|403|authentication|unauthorized|api[[:space:]_-]*key ]]; then
  printf '%s\t%s\n' unavailable authentication_failed
elif [[ "$message" =~ output[[:space:]_-]*token[[:space:]_-]*limit|maximum[[:space:]_-]*output[[:space:]_-]*tokens ]]; then
  printf '%s\t%s\n' evaluated agent_output_limit
elif [[ "$message" =~ 429|too[[:space:]]+many[[:space:]]+requests|provider[[:space:]]+returned[[:space:]]+error|provider[[:space:]]+unavailable|bad[[:space:]]+gateway|upstream[[:space:]]+request[[:space:]]+failed ]]; then
  if [[ "$made_progress" == true ]]; then
    printf '%s\t%s\n' evaluated provider_interrupted
  else
    printf '%s\t%s\n' unavailable provider_unavailable
  fi
elif [[ "$made_progress" == true ]]; then
  printf '%s\t%s\n' evaluated agent_runtime_error
else
  printf '%s\t%s\n' unavailable agent_runtime_error
fi
