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

if [[ "$message" =~ content_policy_violation|prohibited_content|content[[:space:]_-]*policy|safety[[:space:]_-]*(filter|policy)|blocked[[:space:]]+by[[:space:]]+(content|safety) ]]; then
  if [[ "$made_progress" == true ]]; then
    printf '%s\t%s\n' evaluated provider_policy_rejection
  else
    printf '%s\t%s\n' unavailable provider_policy_rejection
  fi
elif [[ "$message" =~ corrupted[[:space:]]+thought[[:space:]]+signature|thought[[:space:]_-]*signature|malformed[[:space:]]+(response|tool|message)|provider[[:space:]_-]*protocol[[:space:]_-]*error ]]; then
  if [[ "$made_progress" == true ]]; then
    printf '%s\t%s\n' evaluated provider_protocol_error
  else
    printf '%s\t%s\n' unavailable provider_protocol_error
  fi
elif [[ "$message" =~ 401|authentication[[:space:]_-]*failed|unauthorized|invalid[[:space:]_-]*api[[:space:]_-]*key|api[[:space:]_-]*key.*(missing|invalid|expired) ]]; then
  printf '%s\t%s\n' unavailable authentication_failed
elif [[ "$message" =~ output[[:space:]_-]*token[[:space:]_-]*limit|maximum[[:space:]_-]*output[[:space:]_-]*tokens ]]; then
  printf '%s\t%s\n' evaluated agent_output_limit
elif [[ "$message" =~ 429|too[[:space:]]+many[[:space:]]+requests|provider[[:space:]]+returned[[:space:]]+error|provider[[:space:]]+unavailable|service[[:space:]]+unavailable|endpoint[[:space:]]+is[[:space:]]+unavailable|provider_model_not_found|model[[:space:]_-]*not[[:space:]_-]*found|bad[[:space:]]+gateway|upstream[[:space:]]+request[[:space:]]+failed ]]; then
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
