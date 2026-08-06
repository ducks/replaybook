#!/usr/bin/env bash
set -euo pipefail

source /root/replaybook-eval/runtime.env
model="$(< /root/replaybook-eval/model)"
instruction="$(< /root/replaybook-eval/instruction.md)"

/usr/local/bin/claux config init --provider openrouter --model "$model" >/dev/null
set -o pipefail
/usr/local/bin/claux --print "$instruction" \
  --permission-mode bypass \
  --output-format json \
  --transcript /root/replaybook-eval/results/claux-transcript.json \
  | tee /root/replaybook-eval/results/claux.json
