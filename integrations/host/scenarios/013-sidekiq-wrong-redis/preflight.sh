#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: preflight.sh BASE_URL STATE_DIR}"
state_dir="${2:?usage: preflight.sh BASE_URL STATE_DIR}"
jobs_file="$state_dir/backlog-job-ids"

install -d -m 0700 "$state_dir"
: >"$jobs_file"
[[ "$(curl --silent --fail --max-time 2 "$base_url/health")" == "ok" ]]

for index in 1 2 3; do
  job_id="preflight-$(date +%s%N)-$$-$RANDOM-$index"
  code="$(curl --silent --output /dev/null --max-time 2 --request POST --write-out '%{http_code}' "$base_url/jobs/$job_id")"
  [[ "$code" == "202" ]]
  printf '%s\n' "$job_id" >>"$jobs_file"
done
chmod 0600 "$jobs_file"

sleep 4
while IFS= read -r job_id; do
  code="$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' "$base_url/jobs/$job_id")"
  if [[ "$code" != "404" ]]; then
    echo "expected queued job $job_id to remain pending, got HTTP $code" >&2
    exit 1
  fi
done <"$jobs_file"
