#!/usr/bin/env bash
set -euo pipefail

base_url="${1:?usage: preflight.sh BASE_URL STATE_DIR}"
state_dir="${2:?usage: preflight.sh BASE_URL STATE_DIR}"
jobs_file="$state_dir/job-ids"
pending_file="${jobs_file}.pending"

install -d -m 0700 "$state_dir"
rm -f -- "$pending_file"
trap 'rm -f -- "$pending_file"' EXIT

for _ in $(seq 1 60); do
  [[ "$(curl --silent --fail --max-time 2 "$base_url/health" 2>/dev/null || true)" == "ok" ]] && break
  sleep 1
done
[[ "$(curl --silent --fail --max-time 2 "$base_url/health" 2>/dev/null || true)" == "ok" ]]

poison_id="poison-$(date +%s%N)-$$-$RANDOM"
printf 'poison=%s\n' "$poison_id" >"$pending_file"
code="$(curl --silent --output /dev/null --request POST --write-out '%{http_code}' "$base_url/jobs/$poison_id/poison")"
[[ "$code" == "202" ]]

for _ in $(seq 1 30); do
  attempted="$(curl --silent --fail --max-time 2 "$base_url/jobs/$poison_id/attempts" || true)"
  [[ "$attempted" =~ ^[1-9][0-9]*$ ]] && break
  sleep 1
done
[[ "$attempted" =~ ^[1-9][0-9]*$ ]] || { echo "poison job did not begin executing" >&2; exit 1; }

for index in 1 2 3; do
  job_id="valid-$(date +%s%N)-$$-$RANDOM-$index"
  printf 'valid=%s\n' "$job_id" >>"$pending_file"
  code="$(curl --silent --output /dev/null --request POST --write-out '%{http_code}' "$base_url/jobs/$job_id")"
  [[ "$code" == "202" ]]
done

sleep 3
while IFS='=' read -r kind job_id; do
  [[ "$kind" == "valid" ]] || continue
  code="$(curl --silent --output /dev/null --write-out '%{http_code}' "$base_url/jobs/$job_id")"
  [[ "$code" == "404" ]] || { echo "expected valid job $job_id to be blocked" >&2; exit 1; }
done <"$pending_file"

chmod 0600 "$pending_file"
mv "$pending_file" "$jobs_file"
trap - EXIT
