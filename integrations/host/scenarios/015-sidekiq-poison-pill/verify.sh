#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
phase="${2:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
state_dir="${3:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
jobs_file="$state_dir/job-ids"
[[ -f "$jobs_file" ]] || { echo "$phase verification is missing job IDs" >&2; exit 22; }

poison_id="$(awk -F= '$1 == "poison" { print $2 }' "$jobs_file")"
mapfile -t valid_ids < <(awk -F= '$1 == "valid" { print $2 }' "$jobs_file")
[[ -n "$poison_id" && "${#valid_ids[@]}" -eq 3 ]] || { echo "$phase verification found invalid controller state" >&2; exit 22; }

for _ in $(seq 1 60); do
  pending=0
  for job_id in "${valid_ids[@]}"; do
    [[ "$(curl --silent --fail --max-time 2 "$base_url/jobs/$job_id" || true)" == "completed" ]] || pending=$((pending + 1))
  done
  poison_state="$(curl --silent --fail --max-time 2 "$base_url/jobs/$poison_id" || true)"
  [[ "$pending" -eq 0 && "$poison_state" == "quarantined" ]] && break
  sleep 1
done
[[ "$pending" -eq 0 ]] || { echo "$phase verification did not recover valid backlog" >&2; exit 20; }
[[ "$poison_state" == "quarantined" ]] || { echo "$phase verification did not quarantine poison job" >&2; exit 22; }

future_poison="verify-poison-$phase-$(date +%s%N)-$$"
future_valid="verify-valid-$phase-$(date +%s%N)-$$"
curl --silent --fail --request POST "$base_url/jobs/$future_poison/poison" >/dev/null
curl --silent --fail --request POST "$base_url/jobs/$future_valid" >/dev/null
for _ in $(seq 1 30); do
  poison_state="$(curl --silent --fail --max-time 2 "$base_url/jobs/$future_poison" || true)"
  valid_state="$(curl --silent --fail --max-time 2 "$base_url/jobs/$future_valid" || true)"
  [[ "$poison_state" == "quarantined" && "$valid_state" == "completed" ]] && exit 0
  sleep 1
done
echo "$phase verification found poison handling still blocks valid work" >&2
exit 22
