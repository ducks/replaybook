#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fixture='{
  "id": "job-1",
  "started_at": "2026-08-04T21:53:10.627187",
  "finished_at": "2026-08-04T21:56:22.243194",
  "stats": {
    "n_completed_trials": 1,
    "n_errored_trials": 0,
    "n_pending_trials": 0,
    "n_cancelled_trials": 0,
    "evals": {
      "claux__model__adhoc": {
        "metrics": [{"mean": 0.0}],
        "reward_stats": {"reward": {"0.0": ["001-nginx-502__trial"]}},
        "exception_stats": {}
      }
    }
  }
}'

summary="$({
  printf '%s\n' "$fixture"
} | jq \
  --slurp \
  --arg generated_at '2026-08-04T00:00:00Z' \
  --argjson expected_trials 1 \
  --argjson benchmark '{"suite":"replaybook-harbor-v1"}' \
  --argjson failure_details '{
    "001-nginx-502__trial": {
      "category": "repair_not_durable",
      "message": "health failed after restart"
    }
  }' \
  --from-file "$script_dir/matrix-summary.jq")"

jq --exit-status '
  .schema_version == 3
  and .benchmark.suite == "replaybook-harbor-v1"
  and .runs[0].failure_category == "repair_not_durable"
  and .runs[0].failure_message == "health failed after restart"
  and .failure_categories == [
    {"category": "repair_not_durable", "count": 1}
  ]
  and .by_agent[0].failure_categories == [
    {"category": "repair_not_durable", "count": 1}
  ]
' <<<"$summary" >/dev/null

uncategorized="$({
  printf '%s\n' "$fixture"
} | jq \
  --slurp \
  --arg generated_at '2026-08-04T00:00:00Z' \
  --argjson expected_trials 1 \
  --argjson benchmark null \
  --argjson failure_details '{}' \
  --from-file "$script_dir/matrix-summary.jq")"

jq --exit-status '
  .runs[0].failure_category == "uncategorized"
  and .failure_categories == [
    {"category": "uncategorized", "count": 1}
  ]
' <<<"$uncategorized" >/dev/null
