#!/usr/bin/env bash
set -euo pipefail

: "${REPLAYBOOK_AGENT_PAYLOAD:?OpenCode adapter requires --agent-payload}"
: "${REPLAYBOOK_INSTRUCTION_FILE:?missing instruction path}"
: "${REPLAYBOOK_MODEL:?missing model identifier}"
: "${REPLAYBOOK_RESULT_FILE:?missing result path}"
: "${REPLAYBOOK_TRANSCRIPT_FILE:?missing transcript path}"
: "${REPLAYBOOK_WORKSPACE:?missing workspace path}"
: "${OPENCODE_AUTH_JSON_B64:?OpenCode adapter requires OPENCODE_AUTH_JSON_B64}"

opencode="$REPLAYBOOK_AGENT_PAYLOAD"
eval_root="${REPLAYBOOK_EVAL_ROOT:-/root/replaybook-eval}"
config_home="${eval_root}/opencode-config"
data_home="${eval_root}/opencode-data"
cache_home="${eval_root}/opencode-cache"
events="${eval_root}/results/opencode-events.jsonl"
stderr_log="${eval_root}/results/opencode-stderr.log"
reasoning_effort="${REPLAYBOOK_REASONING_EFFORT:-}"
scheduled_model="$REPLAYBOOK_MODEL"
provider_model="$scheduled_model"
if [[ "$provider_model" != */* ]]; then
  provider_model="opencode-go/${provider_model}"
fi

mkdir -m 0700 -p "$config_home" "$data_home/opencode" "$cache_home"
printf '%s' "$OPENCODE_AUTH_JSON_B64" | base64 --decode \
  >"$data_home/opencode/auth.json"
chmod 0600 "$data_home/opencode/auth.json"
export XDG_CONFIG_HOME="$config_home"
export XDG_DATA_HOME="$data_home"
export XDG_CACHE_HOME="$cache_home"

args=(
  run
  --pure
  --print-logs
  --log-level ERROR
  --dir "$REPLAYBOOK_WORKSPACE"
  --model "$provider_model"
  --format json
  --auto
)
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
    args+=(--file "$image_path")
  done < <(jq -r '.[].path' \
    "$REPLAYBOOK_IMAGE_ARTIFACTS_FILE")
fi
if [[ -n "$reasoning_effort" ]]; then
  args+=(--variant "$reasoning_effort")
fi
args+=("$(< "$REPLAYBOOK_INSTRUCTION_FILE")")

child_pid=""
forward_termination() {
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
  fi
}
trap forward_termination TERM INT

set +e
"$opencode" "${args[@]}" >"$events" 2>"$stderr_log" &
child_pid=$!
wait "$child_pid"
status=$?
set -e
cat "$stderr_log" >&2

if [[ -s "$events" ]]; then
  jq -s . "$events" >"${REPLAYBOOK_TRANSCRIPT_FILE}.partial"
else
  printf '%s\n' '[]' >"${REPLAYBOOK_TRANSCRIPT_FILE}.partial"
fi
mv "${REPLAYBOOK_TRANSCRIPT_FILE}.partial" "$REPLAYBOOK_TRANSCRIPT_FILE"

usage="$(
  jq -sc '
    [.[] | select(.type == "step_finish") | .part] as $steps |
    if ($steps | length) == 0 then null else {
      input_tokens: ([$steps[].tokens.input // 0] | add),
      output_tokens: ([$steps[].tokens.output // 0] | add),
      reasoning_tokens: ([$steps[].tokens.reasoning // 0] | add),
      cache_read_tokens: ([$steps[].tokens.cache.read // 0] | add),
      cache_creation_tokens: ([$steps[].tokens.cache.write // 0] | add),
      cost_usd: null,
      subscription_usage_usd: ([$steps[].cost // 0] | add)
    } end
  ' "$events" 2>/dev/null || printf 'null'
)"
[[ -n "$usage" ]] || usage=null

result="$(
  jq -sc '[.[] | select(.type == "text") | .part.text] | last // null' \
    "$events" 2>/dev/null || printf 'null'
)"
[[ -n "$result" ]] || result=null

if ! recording="$(
  jq '
    def elapsed($start; $end):
      (($end - $start) | if . < 0 then 0 else . end);
    def read_only_tool($name):
      ($name | ascii_downcase) as $normalized |
      ["read", "grep", "glob", "list", "webfetch", "web_fetch"] |
      index($normalized) != null;

    if length == 0 then null else
      . as $events |
      ([$events[].timestamp? // empty] | min // 0) as $origin |
      (reduce $events[] as $event (
        {started_at: null, rounds: []};
        if $event.type == "step_start" then
          .started_at = ($event.timestamp // $origin)
        elif $event.type == "step_finish" then
          ($event.timestamp // $origin) as $finished_at |
          (.started_at // $finished_at) as $started_at |
          .rounds += [{
            index: ((.rounds | length) + 1),
            started_after_ms: elapsed($origin; $started_at),
            duration_ms: elapsed($started_at; $finished_at),
            status: "completed",
            finish_reason: ($event.part.reason // null),
            usage: {
              input_tokens: ($event.part.tokens.input // 0),
              output_tokens: ($event.part.tokens.output // 0),
              reasoning_tokens: ($event.part.tokens.reasoning // 0),
              cache_read_tokens: ($event.part.tokens.cache.read // 0),
              cache_creation_tokens: ($event.part.tokens.cache.write // 0),
              cost_usd: null,
              subscription_usage_usd: ($event.part.cost // 0)
            }
          }] |
          .started_at = null
        else . end
      )) as $recording |
      ([
        $events[] |
        select(.type == "tool_use") |
        . as $event |
        .part as $part |
        ($part.state.time.start // $event.timestamp // $origin) as $started_at |
        ($part.state.time.end // $event.timestamp // $started_at) as $finished_at |
        {
          name: ($part.tool // "unknown"),
          is_error: (
            ($part.state.status // "completed") != "completed" or
            (($part.state.metadata.exit? // 0) != 0)
          ),
          read_only: read_only_tool($part.tool // "unknown"),
          started_after_ms: elapsed($origin; $started_at),
          duration_ms: elapsed($started_at; $finished_at)
        }
      ]) as $tools |
      ([
        ($events[] | .timestamp? // empty),
        ($events[] | select(.type == "tool_use") | .part.state.time.end? // empty)
      ] | max // $origin) as $finished_at |
      {
        transcript_schema_version: 1,
        total_duration_ms: elapsed($origin; $finished_at),
        model_rounds: $recording.rounds,
        tools: $tools
      }
    end
  ' "$REPLAYBOOK_TRANSCRIPT_FILE"
)"; then
  printf '%s\n' 'warning: failed to normalize OpenCode execution recording' >&2
  recording=null
fi
[[ -n "$recording" ]] || recording=null

outcome_status="error"
(( status == 0 )) && outcome_status="success"
outcome_message="$(
  jq -sr '
    [.[] | select(.type == "error") |
      (.error.data.message // .error.message // "")] |
    last // ""
  ' "$events" 2>/dev/null || true
)"
provider_model_error="$(
  grep -aoE 'ProviderModelNotFoundError: Model not found: [^\"]+' \
    "$stderr_log" 2>/dev/null | tail -n 1 || true
)"
if [[ -n "$provider_model_error" ]]; then
  outcome_message="$provider_model_error"
fi
jq -n \
  --arg model "$scheduled_model" \
  --arg reasoning_effort "$reasoning_effort" \
  --arg outcome_status "$outcome_status" \
  --arg outcome_message "$outcome_message" \
  --argjson result "$result" \
  --argjson usage "$usage" \
  --argjson recording "$recording" \
  '{
    schema_version: 1,
    harness: "opencode",
    model: $model,
    reasoning_effort: (if $reasoning_effort == "" then null else $reasoning_effort end),
    result: $result,
    usage: $usage,
    recording: $recording,
    outcome: {
      status: $outcome_status,
      message: (if $outcome_message == "" then null else $outcome_message end)
    }
  }' >"${REPLAYBOOK_RESULT_FILE}.partial"
mv "${REPLAYBOOK_RESULT_FILE}.partial" "$REPLAYBOOK_RESULT_FILE"
rm -f -- "$events"
cat "$REPLAYBOOK_RESULT_FILE"
exit "$status"
