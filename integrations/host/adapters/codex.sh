#!/usr/bin/env bash
set -euo pipefail

: "${REPLAYBOOK_AGENT_PAYLOAD:?Codex adapter requires --agent-payload}"
: "${REPLAYBOOK_INSTRUCTION_FILE:?missing instruction path}"
: "${REPLAYBOOK_MODEL:?missing model identifier}"
: "${REPLAYBOOK_RESULT_FILE:?missing result path}"
: "${REPLAYBOOK_TRANSCRIPT_FILE:?missing transcript path}"
: "${REPLAYBOOK_WORKSPACE:?missing workspace path}"

codex="$REPLAYBOOK_AGENT_PAYLOAD"
eval_root="${REPLAYBOOK_EVAL_ROOT:-/root/replaybook-eval}"
codex_home="${eval_root}/codex-home"
events="${eval_root}/results/codex-events.jsonl"
final_message="${eval_root}/results/codex-final.txt"
mkdir -m 0700 -p "$codex_home"
export CODEX_HOME="$codex_home"
image_args=()
if [[ -n "${REPLAYBOOK_IMAGE_ARTIFACTS_FILE:-}" ]]; then
  jq -e 'type == "array" and all(.[]; type == "object" and (.path | type == "string"))' \
    "$REPLAYBOOK_IMAGE_ARTIFACTS_FILE" >/dev/null || {
    echo "invalid image artifact manifest" >&2
    exit 2
  }
  while IFS= read -r image_path; do
    [[ -f "$image_path" && ! -L "$image_path" ]] || {
      echo "image artifact is missing or unsafe: ${image_path}" >&2
      exit 2
    }
    image_args+=(--image "$image_path")
  done < <(jq -r '.[].path' \
    "$REPLAYBOOK_IMAGE_ARTIFACTS_FILE")
fi

if [[ -n "${CODEX_AUTH_JSON_B64:-}" ]]; then
  printf '%s' "$CODEX_AUTH_JSON_B64" | base64 --decode >"$codex_home/auth.json"
  chmod 0600 "$codex_home/auth.json"
elif [[ -n "${CODEX_ACCESS_TOKEN:-}" ]]; then
  printf '%s' "$CODEX_ACCESS_TOKEN" | "$codex" login --with-access-token >/dev/null
elif [[ -z "${CODEX_API_KEY:-}" ]]; then
  echo "Codex adapter requires CODEX_API_KEY, CODEX_ACCESS_TOKEN, or CODEX_AUTH_JSON_B64" >&2
  exit 2
fi

child_pid=""
termination_requested=false
forward_termination() {
  termination_requested=true
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
  fi
}
trap forward_termination TERM INT

set +e
"$codex" exec \
  "${image_args[@]}" \
  --model "$REPLAYBOOK_MODEL" \
  --cd "$REPLAYBOOK_WORKSPACE" \
  --dangerously-bypass-approvals-and-sandbox \
  --dangerously-bypass-hook-trust \
  --skip-git-repo-check \
  --ignore-user-config \
  --ignore-rules \
  --ephemeral \
  --json \
  --output-last-message "$final_message" \
  - <"$REPLAYBOOK_INSTRUCTION_FILE" >"$events" &
child_pid=$!
wait "$child_pid"
status=$?
if [[ "$termination_requested" == true ]] && kill -0 "$child_pid" 2>/dev/null; then
  wait "$child_pid"
  status=$?
fi
set -e

if [[ -s "$events" ]]; then
  jq -s . "$events" >"${REPLAYBOOK_TRANSCRIPT_FILE}.partial"
  mv "${REPLAYBOOK_TRANSCRIPT_FILE}.partial" "$REPLAYBOOK_TRANSCRIPT_FILE"
fi

usage="$(
  jq -sc '
    [.[] | select(.type == "turn.completed") | .usage] | last // null |
    if . == null then null else {
      input_tokens: (.input_tokens // 0),
      output_tokens: (.output_tokens // 0),
      cache_read_tokens: (.cached_input_tokens // 0),
      cache_creation_tokens: 0,
      cost_usd: null
    } end
  ' "$events" 2>/dev/null || printf 'null'
)"
[[ -n "$usage" ]] || usage=null
outcome_status="error"
(( status == 0 )) && outcome_status="success"

if [[ -f "$final_message" ]]; then
  jq -n \
    --arg harness "codex" \
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
    --arg harness "codex" \
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
rm -f -- "$events" "$final_message"
cat "$REPLAYBOOK_RESULT_FILE"
exit "$status"
