#!/usr/bin/env bash
set -euo pipefail

eval_root="${REPLAYBOOK_EVAL_ROOT:-/root/replaybook-eval}"
model="${REPLAYBOOK_MODEL:-$(< "$eval_root/model")}"
instruction_file="${REPLAYBOOK_INSTRUCTION_FILE:-$eval_root/instruction.md}"
instruction="$(< "$instruction_file")"
claux="${REPLAYBOOK_AGENT_PAYLOAD:-/run/current-system/sw/bin/claux}"
output="${REPLAYBOOK_RESULT_FILE:-$eval_root/results/agent.json}"
transcript="${REPLAYBOOK_TRANSCRIPT_FILE:-$eval_root/results/transcript.json}"
native_output="${output}.native"
reasoning_effort="${REPLAYBOOK_REASONING_EFFORT:-}"
base_url="${REPLAYBOOK_OPENAI_BASE_URL:-}"
provider_routes="${REPLAYBOOK_CLAUX_PROVIDER_ROUTES:-}"
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
if [[ -z "$provider_routes" ]]; then
  provider_routes='{}'
fi

route="$(
  printf '%s' "$provider_routes" | jq -er --arg model "$model" '
    (.[$model] // "chat_completions") as $route
    | select($route == "chat_completions" or $route == "responses" or $route == "anthropic")
    | $route
  '
)" || {
  echo "REPLAYBOOK_CLAUX_PROVIDER_ROUTES must be a JSON object whose values are chat_completions, responses, or anthropic" >&2
  exit 2
}

"$claux" config init --provider openrouter --model "$model" >/dev/null
config="$HOME/.config/claux/config.toml"
if [[ -n "$base_url" ]]; then
  sed -i "s#^base_url = .*#base_url = \"${base_url}\"#" "$config"
  grep -qx "base_url = \"${base_url}\"" "$config"
fi
case "$route" in
  chat_completions)
    sed -i 's/^protocol = .*/protocol = "chat_completions"/' "$config"
    ;;
  responses)
    sed -i 's/^protocol = .*/protocol = "responses"/' "$config"
    ;;
  anthropic)
    sed -i 's/^type = "openai"/type = "anthropic"/' "$config"
    ;;
esac
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
  "${image_args[@]}" \
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
