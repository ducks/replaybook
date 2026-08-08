#!/usr/bin/env bash
set -euo pipefail

eval_root="${REPLAYBOOK_EVAL_ROOT:-/root/replaybook-eval}"
source "$eval_root/runtime.env"
model="$(< "$eval_root/model")"
instruction="$(< "$eval_root/instruction.md")"
output="$eval_root/results/claux.json"
transcript="$eval_root/results/claux-transcript.json"

"$eval_root/claux" config init --provider openrouter --model "$model" >/dev/null
config="$HOME/.config/claux/config.toml"
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
"$eval_root/claux" --print "$instruction" \
  --permission-mode bypass \
  --output-format json \
  --transcript "$transcript" \
  >"$output" &
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
if [[ -s "$transcript" && ! -s "$output" ]]; then
  jq '{schema_version, result: null, model, usage, outcome}' \
    "$transcript" >"${output}.partial"
  mv "${output}.partial" "$output"
fi

if [[ -s "$output" ]]; then
  cat "$output"
fi
exit "$status"
