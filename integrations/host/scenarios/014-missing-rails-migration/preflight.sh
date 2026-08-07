#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: preflight.sh BASE_URL STATE_DIR}"
state_dir="${2:?usage: preflight.sh BASE_URL STATE_DIR}"
jobs_file="$state_dir/backlog-job-ids"
pending_jobs_file="${jobs_file}.pending"

install -d -m 0700 "$state_dir"
rm -f -- "$pending_jobs_file"
trap 'rm -f -- "$pending_jobs_file"' EXIT

service_ready=false
for _ in $(seq 1 60); do
  if [[ "$(curl --silent --fail --max-time 2 "$base_url/health" 2>/dev/null || true)" == "ok" ]]; then
    service_ready=true
    break
  fi
  sleep 1
done
[[ "$service_ready" == true ]] || {
  echo "checkout web service did not become ready for preflight" >&2
  exit 1
}

if [[ "$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' "$base_url/deployment/migration")" != "404" ]]; then
  echo "expected the deployed migration to be missing before the repair" >&2
  exit 1
fi

: >"$pending_jobs_file"
for index in 1 2 3; do
  job_id="preflight-$(date +%s%N)-$$-$RANDOM-$index"
  code="$(curl --silent --output /dev/null --max-time 2 --request POST --write-out '%{http_code}' "$base_url/jobs/$job_id")"
  [[ "$code" == "202" ]]
  printf '%s\n' "$job_id" >>"$pending_jobs_file"
done

while IFS= read -r job_id; do
  attempted=false
  for _ in $(seq 1 30); do
    attempt_count="$(curl --silent --fail --max-time 2 "$base_url/jobs/$job_id/attempts" || true)"
    if [[ "$attempt_count" =~ ^[1-9][0-9]*$ ]]; then
      attempted=true
      break
    fi
    sleep 1
  done
  [[ "$attempted" == true ]] || {
    echo "expected queued job $job_id to be attempted by Sidekiq" >&2
    exit 1
  }

  code="$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' "$base_url/jobs/$job_id")"
  [[ "$code" == "404" ]] || {
    echo "expected attempted job $job_id to remain pending, got HTTP $code" >&2
    exit 1
  }
done <"$pending_jobs_file"

chmod 0600 "$pending_jobs_file"
mv "$pending_jobs_file" "$jobs_file"
trap - EXIT
