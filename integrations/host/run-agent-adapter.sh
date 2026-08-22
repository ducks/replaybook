#!/usr/bin/env bash
set -euo pipefail

eval_root="${REPLAYBOOK_EVAL_ROOT:-/root/replaybook-eval}"
runtime_env="${eval_root}/runtime.env"
adapter="${eval_root}/adapter"

[[ -x "$adapter" ]] || {
  echo "agent adapter is missing or not executable: ${adapter}" >&2
  exit 2
}
[[ -f "${eval_root}/instruction.md" ]] || {
  echo "agent instruction is missing" >&2
  exit 2
}
[[ -f "${eval_root}/model" ]] || {
  echo "agent model is missing" >&2
  exit 2
}

if [[ -f "$runtime_env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$runtime_env"
  set +a
  rm -f -- "$runtime_env"
fi

export REPLAYBOOK_EVAL_ROOT="$eval_root"
export REPLAYBOOK_INSTRUCTION_FILE="${eval_root}/instruction.md"
export REPLAYBOOK_MODEL="$(< "${eval_root}/model")"
export REPLAYBOOK_WORKSPACE="${REPLAYBOOK_WORKSPACE:-/root}"
export REPLAYBOOK_RESULT_FILE="${eval_root}/results/agent.json"
export REPLAYBOOK_TRANSCRIPT_FILE="${eval_root}/results/transcript.json"
if [[ -f "${eval_root}/image-artifacts.json" ]]; then
  export REPLAYBOOK_IMAGE_ARTIFACTS_FILE="${eval_root}/image-artifacts.json"
fi
if [[ -e "${eval_root}/payload" ]]; then
  export REPLAYBOOK_AGENT_PAYLOAD="${eval_root}/payload"
fi

cd "$REPLAYBOOK_WORKSPACE"
exec "$adapter"
