#!/usr/bin/env bash
set -euo pipefail

eval_root="${REPLAYBOOK_EVAL_ROOT:-/root/replaybook-eval}"
model="${REPLAYBOOK_MODEL:-$(< "$eval_root/model")}"
instruction_file="${REPLAYBOOK_INSTRUCTION_FILE:-$eval_root/instruction.md}"
instruction="$(< "$instruction_file")"
claux="${REPLAYBOOK_AGENT_PAYLOAD:-$eval_root/claux}"
output="${REPLAYBOOK_RESULT_FILE:-$eval_root/results/agent.json}"
transcript="${REPLAYBOOK_TRANSCRIPT_FILE:-$eval_root/results/transcript.json}"
native_output="${output}.native"
reasoning_effort="${REPLAYBOOK_REASONING_EFFORT:-}"
base_url="${REPLAYBOOK_OPENAI_BASE_URL:-}"

"$claux" config init --provider openrouter --model "$model" >/dev/null
config="$HOME/.config/claux/config.toml"
if [[ -n "$base_url" ]]; then
  sed -i "s#^base_url = .*#base_url = \"${base_url}\"#" "$config"
  grep -qx "base_url = \"${base_url}\"" "$config"
fi
if [[ -n "$reasoning_effort" ]]; then
  profile="$(sed -n 's/^default_profile = "\([^"]*\)"/\1/p' "$config")"
  [[ -n "$profile" ]] || {
    echo "Claux config is missing default_profile" >&2
    exit 2
  }
  awk -v section="[model_profiles.${profile}]" -v effort="$reasoning_effort" '
    { print }
    $0 == section { print "reasoning_effort = \"" effort "\"" }
  ' "$config" >"${config}.partial"
  mv "${config}.partial" "$config"
fi
sed -i \
  -e 's/^native_tool_filesystem_policy = .*/native_tool_filesystem_policy = "unrestricted"/' \
  -e 's/^bash_filesystem_policy = .*/bash_filesystem_policy = "unrestricted"/' \
  "$config"
grep -qx 'native_tool_filesystem_policy = "unrestricted"' "$config"
grep -qx 'bash_filesystem_policy = "unrestricted"' "$config"

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
"$claux" --print "$instruction" \
  --permission-mode bypass \
  --output-format json \
  --transcript "$transcript" \
  >"$native_output" &
child_pid=$!
wait "$child_pid"
status=$?
if [[ "$termination_requested" == true ]] && kill -0 "$child_pid" 2>/dev/null; then
  wait "$child_pid"
  status=$?
fi
set -e

# One-shot JSON is normally emitted only for a completed response. Preserve
# usage and outcome metadata from the partial transcript when cancellation or
# another error ends the turn first.
if [[ -s "$transcript" && ! -s "$native_output" ]]; then
  jq '{schema_version, result: null, model, usage, outcome}' \
    "$transcript" >"${native_output}.partial"
  mv "${native_output}.partial" "$native_output"
fi

if [[ -s "$native_output" ]]; then
  jq --arg model "$model" --arg reasoning_effort "$reasoning_effort" \
    --slurpfile transcript "$transcript" \
    '. + {
      schema_version: 1,
      harness: "claux",
      model: $model,
      reasoning_effort: (if $reasoning_effort == "" then null else $reasoning_effort end),
      recording: (
        if ($transcript | length) == 0 or ($transcript[0].timing? == null) then null
        else {
          transcript_schema_version: $transcript[0].schema_version,
          total_duration_ms: $transcript[0].timing.total_duration_ms,
          model_rounds: ($transcript[0].timing.model_rounds // []),
          tools: (($transcript[0].tool_trace // []) | map({
            name,
            is_error,
            read_only,
            started_after_ms,
            duration_ms
          }))
        }
        end
      )
    }' \
    "$native_output" >"${output}.partial"
  mv "${output}.partial" "$output"
  rm -f -- "$native_output"
  cat "$output"
fi
exit "$status"
