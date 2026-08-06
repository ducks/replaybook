#!/usr/bin/env bash
set -euo pipefail

source /root/replaybook-eval/runtime.env
model="$(< /root/replaybook-eval/model)"
instruction="$(< /root/replaybook-eval/instruction.md)"

/root/replaybook-eval/claux config init --provider openrouter --model "$model" >/dev/null
config=/root/.config/claux/config.toml
sed -i \
  -e 's/^native_tool_filesystem_policy = .*/native_tool_filesystem_policy = "unrestricted"/' \
  -e 's/^bash_filesystem_policy = .*/bash_filesystem_policy = "unrestricted"/' \
  "$config"
grep -qx 'native_tool_filesystem_policy = "unrestricted"' "$config"
grep -qx 'bash_filesystem_policy = "unrestricted"' "$config"

set -o pipefail
/root/replaybook-eval/claux --print "$instruction" \
  --permission-mode bypass \
  --output-format json \
  --transcript /root/replaybook-eval/results/claux-transcript.json \
  | tee /root/replaybook-eval/results/claux.json
