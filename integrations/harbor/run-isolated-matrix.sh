#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run the Replaybook Harbor comparison with one disposable VM per attempt.

Usage:
  run-isolated-matrix.sh [options]

Options:
  --attempts N      Attempts per agent (default: 3).
  --concurrency N   Maximum simultaneous VMs (default: 2).
  --base-port PORT  First forwarded SSH port (default: 22300).
  --oracle          Run only isolated oracle attempts; useful as a pool smoke test.
  -h, --help        Show this help.

The model comparison uses Codex/gpt-5.6-sol, Claude Code/claude-sonnet-5,
and Claux/deepseek-v4-flash. CLAUDE_CODE_OAUTH_TOKEN and OPENROUTER_API_KEY
are read from the environment or prompted for privately.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUNNER="${SCRIPT_DIR}/run-isolated.sh"
ATTEMPTS=3
CONCURRENCY=2
BASE_PORT=22300
ORACLE_ONLY=false

while (( $# > 0 )); do
  case "$1" in
    --attempts)
      (( $# >= 2 )) || { echo "--attempts requires a value" >&2; exit 2; }
      ATTEMPTS="$2"
      shift 2
      ;;
    --concurrency)
      (( $# >= 2 )) || { echo "--concurrency requires a value" >&2; exit 2; }
      CONCURRENCY="$2"
      shift 2
      ;;
    --base-port)
      (( $# >= 2 )) || { echo "--base-port requires a value" >&2; exit 2; }
      BASE_PORT="$2"
      shift 2
      ;;
    --oracle)
      ORACLE_ONLY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! "${ATTEMPTS}" =~ ^[0-9]+$ ]] || (( ATTEMPTS <= 0 )); then
  echo "--attempts must be a positive integer" >&2
  exit 2
fi
if [[ ! "${CONCURRENCY}" =~ ^[0-9]+$ ]] || (( CONCURRENCY <= 0 )); then
  echo "--concurrency must be a positive integer" >&2
  exit 2
fi
if [[ ! "${BASE_PORT}" =~ ^[0-9]+$ ]] || (( BASE_PORT <= 0 || BASE_PORT > 65535 )); then
  echo "--base-port must be an integer from 1 to 65535" >&2
  exit 2
fi

if [[ "${ORACLE_ONLY}" == true ]]; then
  AGENTS=(oracle)
else
  AGENTS=(codex claude claux)
fi
EXPECTED_TRIALS=$(( ATTEMPTS * ${#AGENTS[@]} ))
LAST_PORT=$(( BASE_PORT + EXPECTED_TRIALS - 1 ))
(( LAST_PORT <= 65535 )) || {
  echo "worker port range ends above 65535" >&2
  exit 2
}

for port in $(seq "${BASE_PORT}" "${LAST_PORT}"); do
  if ss -ltn "sport = :${port}" | tail -n +2 | grep -q .; then
    echo "worker SSH port is already in use: ${port}" >&2
    exit 1
  fi
done

prompt_secret() {
  local name="$1"
  local prompt="$2"
  local value=""

  if [[ -v "${name}" && -n "${!name}" ]]; then
    export "${name?}"
    return
  fi
  [[ -t 0 ]] || {
    echo "${name} is not exported and input is not interactive" >&2
    exit 1
  }
  read -r -s -p "${prompt}: " value
  printf '\n' >&2
  [[ -n "${value}" ]] || {
    echo "${name} cannot be empty" >&2
    exit 1
  }
  printf -v "${name}" '%s' "${value}"
  export "${name?}"
}

if [[ "${ORACLE_ONLY}" == false ]]; then
  CODEX_AUTH_FILE="${REPLAYBOOK_CODEX_AUTH_FILE:-${HOME}/.codex/auth.json}"
  [[ -f "${CODEX_AUTH_FILE}" ]] || {
    echo "Codex auth is missing: ${CODEX_AUTH_FILE}" >&2
    exit 1
  }
  prompt_secret CLAUDE_CODE_OAUTH_TOKEN "Claude OAuth token"
  prompt_secret OPENROUTER_API_KEY "OpenRouter API key"
fi

mkdir -p "${REPO_DIR}/jobs"
MATRIX_DIR="$(mktemp -d "${REPO_DIR}/jobs/isolated-matrix-$(date -u +%Y-%m-%d__%H-%M-%S).XXXXXX")"
mkdir -p "${MATRIX_DIR}/logs" "${MATRIX_DIR}/runs"

PIDS=()
RUN_IDS=()
FAILURES=0
NEXT_PORT="${BASE_PORT}"

stop_workers() {
  local pid
  for pid in "${PIDS[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap 'stop_workers; exit 130' INT
trap 'stop_workers; exit 143' TERM

wait_for_oldest() {
  local pid="${PIDS[0]}"
  local run_id="${RUN_IDS[0]}"

  if wait "${pid}"; then
    echo "[matrix] passed ${run_id}"
  else
    echo "[matrix] failed ${run_id}; see ${MATRIX_DIR}/logs/${run_id}.log" >&2
    FAILURES=$(( FAILURES + 1 ))
  fi
  PIDS=("${PIDS[@]:1}")
  RUN_IDS=("${RUN_IDS[@]:1}")
}

launch_worker() {
  local agent="$1"
  local attempt="$2"
  local run_id="${agent}-${attempt}"
  local output_dir="${MATRIX_DIR}/runs/${run_id}"
  local log_file="${MATRIX_DIR}/logs/${run_id}.log"
  local config=""
  local worker_options=(--output-dir "${output_dir}")

  case "${agent}" in
    oracle)
      config="integrations/harbor/jobs/oracle-smoke.yaml"
      ;;
    codex)
      config="integrations/harbor/jobs/codex-single.yaml"
      worker_options+=(--codex-auth)
      ;;
    claude)
      config="integrations/harbor/jobs/claude-single.yaml"
      worker_options+=(--env CLAUDE_CODE_OAUTH_TOKEN)
      ;;
    claux)
      config="integrations/harbor/jobs/claux-single.yaml"
      worker_options+=(--env OPENROUTER_API_KEY)
      ;;
    *)
      echo "unknown matrix agent: ${agent}" >&2
      exit 2
      ;;
  esac

  echo "[matrix] starting ${run_id} on SSH port ${NEXT_PORT}"
  REPLAYBOOK_WORKER_SSH_PORT="${NEXT_PORT}" \
    bash "${RUNNER}" "${worker_options[@]}" -- --config "${config}" --yes \
    >"${log_file}" 2>&1 &
  PIDS+=("$!")
  RUN_IDS+=("${run_id}")
  NEXT_PORT=$(( NEXT_PORT + 1 ))
}

for agent in "${AGENTS[@]}"; do
  for attempt in $(seq 1 "${ATTEMPTS}"); do
    while (( ${#PIDS[@]} >= CONCURRENCY )); do
      wait_for_oldest
    done
    launch_worker "${agent}" "${attempt}"
  done
done
while (( ${#PIDS[@]} > 0 )); do
  wait_for_oldest
done

mapfile -t RESULT_FILES < <(
  find "${MATRIX_DIR}/runs" -mindepth 4 -maxdepth 4 -name result.json -print | sort
)

SUMMARY_FILE="${MATRIX_DIR}/summary.json"
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if (( ${#RESULT_FILES[@]} > 0 )); then
  jq -s \
    --arg generated_at "${GENERATED_AT}" \
    --argjson expected_trials "${EXPECTED_TRIALS}" \
    '
      {
        schema_version: 1,
        generated_at: $generated_at,
        expected_trials: $expected_trials,
        received_jobs: length,
        totals: {
          completed: ([.[].stats.n_completed_trials // 0] | add // 0),
          errored: ([.[].stats.n_errored_trials // 0] | add // 0),
          input_tokens: ([.[].stats.n_input_tokens // 0] | add // 0),
          cache_tokens: ([.[].stats.n_cache_tokens // 0] | add // 0),
          output_tokens: ([.[].stats.n_output_tokens // 0] | add // 0),
          known_cost_usd: ([.[].stats.cost_usd // 0] | add // 0),
          cost_reported_jobs: ([.[] | select(.stats.cost_usd != null)] | length)
        },
        runs: [
          .[] |
          (.stats.evals | to_entries[0]) as $eval |
          {
            job_id: .id,
            agent_model: $eval.key,
            trials: (.stats.n_completed_trials // 0),
            errors: (.stats.n_errored_trials // 0),
            mean: ($eval.value.metrics[0].mean // null),
            input_tokens: .stats.n_input_tokens,
            cache_tokens: .stats.n_cache_tokens,
            output_tokens: .stats.n_output_tokens,
            cost_usd: .stats.cost_usd
          }
        ]
      }
      | .by_agent = (
          .runs
          | group_by(.agent_model)
          | map({
              agent_model: .[0].agent_model,
              trials: ([.[].trials] | add // 0),
              errors: ([.[].errors] | add // 0),
              mean: (
                ([.[] | select(.mean != null) | (.mean * .trials)] | add // 0)
                / ([.[] | select(.mean != null) | .trials] | add // 1)
              ),
              input_tokens: ([.[].input_tokens // 0] | add // 0),
              cache_tokens: ([.[].cache_tokens // 0] | add // 0),
              output_tokens: ([.[].output_tokens // 0] | add // 0),
              known_cost_usd: ([.[].cost_usd // 0] | add // 0),
              cost_reported_jobs: ([.[] | select(.cost_usd != null)] | length)
            })
        )
    ' "${RESULT_FILES[@]}" >"${SUMMARY_FILE}"
else
  jq -n \
    --arg generated_at "${GENERATED_AT}" \
    --argjson expected_trials "${EXPECTED_TRIALS}" \
    '{schema_version: 1, generated_at: $generated_at, expected_trials: $expected_trials, received_jobs: 0, totals: {completed: 0, errored: 0}, runs: [], by_agent: []}' \
    >"${SUMMARY_FILE}"
fi

echo
printf '%-34s %8s %8s %8s %12s\n' "Agent/model" "Trials" "Errors" "Mean" "Known cost"
jq -r '.by_agent[] | [.agent_model, .trials, .errors, .mean, .known_cost_usd] | @tsv' \
  "${SUMMARY_FILE}" |
  while IFS=$'\t' read -r agent trials errors mean cost; do
    printf '%-34s %8s %8s %8.3f %12.4f\n' "${agent}" "${trials}" "${errors}" "${mean}" "${cost}"
  done

echo "[matrix] summary: ${SUMMARY_FILE}"

RECEIVED_JOBS="$(jq -r '.received_jobs' "${SUMMARY_FILE}")"
ERRORED_TRIALS="$(jq -r '.totals.errored' "${SUMMARY_FILE}")"
if (( FAILURES > 0 || RECEIVED_JOBS != EXPECTED_TRIALS || ERRORED_TRIALS > 0 )); then
  echo "[matrix] incomplete: ${FAILURES} workers failed, ${RECEIVED_JOBS}/${EXPECTED_TRIALS} results received, ${ERRORED_TRIALS} trials errored" >&2
  exit 1
fi

echo "[matrix] all ${EXPECTED_TRIALS} isolated trials completed"
