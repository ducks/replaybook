#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
phase="${2:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
state_dir="${3:?usage: verify.sh BASE_URL PHASE STATE_DIR}"
jobs_file="$state_dir/backlog-job-ids"

[[ -f "$jobs_file" ]] || {
  echo "$phase verification is missing the controller-owned backlog IDs" >&2
  exit 20
}
mapfile -t backlog_job_ids <"$jobs_file"
[[ "${#backlog_job_ids[@]}" -eq 3 ]] || {
  echo "$phase verification expected three controller-owned backlog IDs" >&2
  exit 20
}

migration=""
for _ in $(seq 1 30); do
  migration="$(curl --silent --fail --max-time 2 "$base_url/deployment/migration" || true)"
  [[ "$migration" == "applied" ]] && break
  sleep 1
done
[[ "$migration" == "applied" ]] || {
  echo "$phase verification found the deployed migration unapplied" >&2
  exit 21
}

pending_job_ids=("${backlog_job_ids[@]}")
for _ in $(seq 1 60); do
  pending_job_ids=()
  for backlog_job_id in "${backlog_job_ids[@]}"; do
    body="$(curl --silent --fail --max-time 2 "$base_url/jobs/$backlog_job_id" || true)"
    [[ "$body" == "completed" ]] || pending_job_ids+=("$backlog_job_id")
  done
  (( ${#pending_job_ids[@]} == 0 )) && break
  sleep 1
done
if (( ${#pending_job_ids[@]} != 0 )); then
  printf '%s verification did not recover pre-existing jobs: %s\n' \
    "$phase" "${pending_job_ids[*]}" >&2
  exit 20
fi

for backlog_job_id in "${backlog_job_ids[@]}"; do
  attempt_count="$(curl --silent --fail --max-time 2 "$base_url/jobs/$backlog_job_id/attempts" || true)"
  if [[ ! "$attempt_count" =~ ^[0-9]+$ ]] || (( attempt_count < 2 )); then
    echo "$phase verification found invalid retry history for $backlog_job_id: $attempt_count" >&2
    exit 20
  fi
done

job_id="verify-$phase-$(date +%s)-$$"
code="000"
for _ in $(seq 1 20); do
  code="$(curl --silent --output /dev/null --max-time 2 --request POST --write-out '%{http_code}' "$base_url/jobs/$job_id" || true)"
  [[ "$code" == "202" ]] && break
  sleep 1
done
[[ "$code" == "202" ]] || {
  echo "$phase verification could not enqueue a job: HTTP $code" >&2
  exit 1
}

for _ in $(seq 1 30); do
  body="$(curl --silent --fail --max-time 2 "$base_url/jobs/$job_id" || true)"
  [[ "$body" == "completed" ]] && break
  sleep 1
done
[[ "$body" == "completed" ]] || {
  echo "$phase verification timed out waiting for job $job_id" >&2
  exit 1
}

attempt_count="$(curl --silent --fail --max-time 2 "$base_url/jobs/$job_id/attempts" || true)"
[[ "$attempt_count" == "1" ]] || {
  echo "$phase verification expected one execution for $job_id, got $attempt_count" >&2
  exit 1
}
