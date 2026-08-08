#!/usr/bin/env bash
set -euo pipefail

: "${REPLAYBOOK_INSTRUCTION_FILE:?missing instruction path}"
: "${REPLAYBOOK_MODEL:?missing model identifier}"
: "${REPLAYBOOK_RESULT_FILE:?missing result path}"
: "${REPLAYBOOK_TRANSCRIPT_FILE:?missing transcript path}"
: "${REPLAYBOOK_WORKSPACE:?missing workspace path}"
: "${REPLAYBOOK_EVAL_ROOT:?missing evaluation root}"

harness="TODO-harness-name"
events="${REPLAYBOOK_EVAL_ROOT}/results/${harness}-events.jsonl"
final_message="${REPLAYBOOK_EVAL_ROOT}/results/${harness}-final.txt"

child_pid=""
forward_termination() {
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
  fi
}
trap forward_termination TERM INT

set +e
# TODO: Replace this invocation with the target harness's noninteractive CLI.
# It must receive the instruction, scheduled model, and workspace. Prefer a
# native event stream plus a separate final-message file.
"${REPLAYBOOK_AGENT_PAYLOAD:?missing harness payload}" \
  --model "$REPLAYBOOK_MODEL" \
  --workspace "$REPLAYBOOK_WORKSPACE" \
  --json \
  --output "$final_message" \
  <"$REPLAYBOOK_INSTRUCTION_FILE" >"$events" &
child_pid=$!
wait "$child_pid"
status=$?
set -e

# TODO: Convert the native event stream into valid JSON without dropping tool
# failures or cancellation events.
jq -s . "$events" >"${REPLAYBOOK_TRANSCRIPT_FILE}.partial"
mv "${REPLAYBOOK_TRANSCRIPT_FILE}.partial" "$REPLAYBOOK_TRANSCRIPT_FILE"

# TODO: Extract native usage. Leave unknown cost null.
usage='null'
outcome_status="error"
(( status == 0 )) && outcome_status="success"

if [[ -f "$final_message" ]]; then
  jq -n \
    --arg harness "$harness" \
    --arg model "$REPLAYBOOK_MODEL" \
    --arg outcome_status "$outcome_status" \
    --argjson usage "$usage" \
    --rawfile result "$final_message" \
    '{
      schema_version: 1,
      harness: $harness,
      model: $model,
      result: $result,
      usage: $usage,
      outcome: {status: $outcome_status}
    }' >"${REPLAYBOOK_RESULT_FILE}.partial"
else
  jq -n \
    --arg harness "$harness" \
    --arg model "$REPLAYBOOK_MODEL" \
    --arg outcome_status "$outcome_status" \
    --argjson usage "$usage" \
    '{
      schema_version: 1,
      harness: $harness,
      model: $model,
      result: null,
      usage: $usage,
      outcome: {status: $outcome_status}
    }' >"${REPLAYBOOK_RESULT_FILE}.partial"
fi
mv "${REPLAYBOOK_RESULT_FILE}.partial" "$REPLAYBOOK_RESULT_FILE"
exit "$status"
